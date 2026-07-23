"""Storage layer — SQLite FTS5 with search strategies from mcp-ariel-memory.

Features:
- FTS5 full-text search with LIKE fallback
- Auto strategy selection (fts/hybrid by query length)
- Document chunking for better search precision
- Type-aware result boosting
- WAL mode + performance PRAGMAs
- Persistent connection with pooling
"""

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path

from chunking import _BINARY_SUFFIXES, auto_chunk, chunk_code, chunk_text  # noqa: F401
from result import Err, Ok
from scoring import importance_boost, type_boost  # noqa: F401
from uri import VALID_DOMAINS as _VALID_DOMAINS
from validation import (
    _MAX_SEARCH_LIMIT,
    validate_collection,
    validate_file_mask,
    validate_query,
    validate_url,
)

logger = logging.getLogger(__name__)

# SQLite PRAGMA constants
_CACHE_SIZE_KB = 64000
_MMAP_SIZE = 256 * 1024 * 1024  # 256 MiB

# Scoring constants
_HYBRID_MULTIPLIER = 3

# Field name constants
_FIELD_SCORE = "score"

# Action/status constants
_ACTION_ADDED = "added"
_ACTION_DELETED = "deleted"

# Default value constants
_UNKNOWN = "unknown"

# Error message constants
_ERR_COLLECTION_NOT_FOUND = "Collection not found"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_explain(base_score: float, boost: float, imp_boost: float, score: float, source: str) -> dict:
    return {
        "base_score": round(base_score, 3),
        "type_boost": round(boost, 3),
        "importance_boost": round(imp_boost, 3),
        "final_score": round(score, 3),
        "source": source,
    }


# ── Auto Strategy ───────────────────────────────────────────────────────────


def _sanitize_fts5_token(token: str) -> str:
    return re.sub(r"[^\w]", "", token)


def auto_strategy(query: str) -> str:
    if len(query.split()) <= 2:
        return "fts"
    return "hybrid"


# ── Storage ─────────────────────────────────────────────────────────────────


def _collection_row_to_dict(row: sqlite3.Row, ctx_counts: dict) -> dict:
    coll = row["collection"]
    parts = coll.split("__")
    return {
        "name": coll,
        "count": row["docs"],
        "chunks": row["chunks"],
        "contexts": row["contexts"].split("|") if row["contexts"] else [],
        "context_count": ctx_counts.get(coll, 0),
        "domain": parts[0] if len(parts) > 1 and parts[0] in _VALID_DOMAINS else None,
    }


class Storage:
    """SQLite FTS5-backed document storage with smart search strategies.

    # ponytail: 55 methods is high but inherent to the domain —
    # each is a thin DB operation. Splitting into multiple classes
    # would add indirection without reducing complexity.
    """

    @classmethod
    def default(cls) -> "Storage":
        return cls(Path.home() / ".docshaven")

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.repos_dir = data_dir / "repos"
        self.config_path = data_dir / "config.json"
        self.db_path = data_dir / "docshaven.db"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._db_initialized = False

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.ProgrammingError:
                pass
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA cache_size=-{_CACHE_SIZE_KB}")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(f"PRAGMA mmap_size={_MMAP_SIZE}")
        self._local.conn = conn
        if not self._db_initialized:
            with self._init_lock:
                if not self._db_initialized:
                    self._conn = conn  # temp for _init_db
                    try:
                        self._init_db()
                        self._db_initialized = True
                    except sqlite3.Error:
                        self._conn = None  # type: ignore[assignment]
                        raise
                    finally:
                        self._conn = None  # type: ignore[assignment]
        return conn

    def _create_documents_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT DEFAULT '',
                extension TEXT DEFAULT '',
                title TEXT DEFAULT '',
                context TEXT DEFAULT '',
                chunk_index INTEGER DEFAULT 0,
                total_chunks INTEGER DEFAULT 1,
                retrieval_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(collection, file_path, chunk_index)
            )
        """)

    def _create_fts_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title, content, collection,
                content='documents',
                content_rowid='id',
                tokenize='porter unicode61'
            )
        """)
        for trigger_sql in _FTS_TRIGGERS:
            conn.execute(trigger_sql)

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_filepath ON documents(file_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection_filepath ON documents(collection, file_path)")

    def _create_judgments_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conflict_judgments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                new_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                judgment TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

    def _create_context_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS context_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection TEXT NOT NULL,
                path TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(collection, path)
            )
        """)

    def _init_db(self) -> None:
        conn = self._conn
        assert conn is not None, "_init_db called before connection established"
        self._create_documents_table(conn)
        self._create_fts_table(conn)
        self._create_indexes(conn)
        self._create_judgments_table(conn)
        self._create_context_table(conn)
        try:
            conn.execute("ALTER TABLE documents ADD COLUMN retrieval_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        conn.commit()

    @staticmethod
    def _compute_file_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @staticmethod
    def _should_skip_file(file_path: Path) -> bool:
        if file_path.is_symlink():
            return True
        if not file_path.exists():
            return True
        if file_path.suffix.lower() in _BINARY_SUFFIXES:
            return True
        return False

    @staticmethod
    def _validate_index_path(file_path: Path, repo_dir: Path) -> str | None:
        """Validate file path is within repo and not dangerous. Returns error or None."""
        if file_path.is_symlink():
            return "skipped_symlink"
        try:
            file_path.relative_to(repo_dir)
        except ValueError:
            return "path_outside_repo"
        return None

    def _index_file(self, conn: sqlite3.Connection, f: Path, repo_dir: Path, name: str, description: str | None) -> int:
        if self._should_skip_file(f):
            return 0
        if self._validate_index_path(f, repo_dir) is not None:
            return 0
        # Size guard — skip files over 500KB
        try:
            if f.stat().st_size > 500_000:
                return 0
        except OSError:
            return 0
        content = f.read_text(errors="ignore")
        rel_path = str(f.relative_to(repo_dir))
        title = f.stem.replace("-", " ").replace("_", " ")
        chunks = auto_chunk(content, str(f))
        content_hash = self._compute_file_hash(content)
        for i, chunk in enumerate(chunks):
            conn.execute(
                """INSERT OR REPLACE INTO documents
                (collection, file_path, content, content_hash, extension, title, context, chunk_index, total_chunks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, rel_path, chunk, content_hash, f.suffix, title, description or "", i, len(chunks)),
            )
        return len(chunks)

    def _clone_repo(self, url: str, repo_dir: Path) -> Err | None:
        """Clone repo into repo_dir. Returns Err on failure, None on success."""
        if repo_dir.exists():
            return None
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(repo_dir)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)
            return Err(error=f"Clone failed: {result.stderr}")
        return None

    def _collect_indexable_files(self, repo_dir: Path, mask: str) -> list[Path]:
        """Collect files matching mask, excluding symlinks and out-of-repo paths."""
        resolved_root = repo_dir.resolve()
        files = []
        for f in repo_dir.glob(mask):
            if not f.is_file() or f.is_symlink():
                continue
            try:
                resolved = f.resolve()
                if resolved != resolved_root and resolved_root not in resolved.parents:
                    continue
                if f.stat().st_size < 500_000:
                    files.append(f)
            except OSError:
                continue
        return files

    def _index_files(self, collection: str, files: list[Path], repo_dir: Path, description: str | None) -> tuple[int, int]:
        """Index files into DB. Returns (indexed_count, total_chunks)."""
        conn = self._get_conn()
        indexed = 0
        total_chunks = 0
        for f in files:
            try:
                total_chunks += self._index_file(conn, f, repo_dir, collection, description)
                indexed += 1
            except (OSError, sqlite3.Error) as e:
                logger.debug("Skipping %s: %s", f, e)
        conn.commit()
        return indexed, total_chunks

    def add_repo(
        self,
        url: str,
        tags: list[str] | None = None,
        description: str | None = None,
        mask: str | None = None,
    ) -> Ok[dict] | Err:
        url_error = validate_url(url)
        if url_error:
            return Err(error=url_error)

        name = url.rstrip("/").split("/")[-1].replace(".git", "")
        name_error = validate_collection(name)
        if name_error:
            return Err(error=f"Invalid collection name from URL: {name_error}")
        repo_dir = self.repos_dir / name

        clone_err = self._clone_repo(url, repo_dir)
        if clone_err:
            return clone_err

        file_mask = mask or "**/*.md"
        mask_error = validate_file_mask(file_mask)
        if mask_error:
            return Err(error=mask_error)

        files = self._collect_indexable_files(repo_dir, file_mask)
        indexed, total_chunks = self._index_files(name, files, repo_dir, description)

        config = self._load_config()
        config["repos"][name] = {
            "url": url,
            "tags": tags or [],
            "description": description or "",
            "files_indexed": indexed,
            "total_chunks": total_chunks,
        }
        self._save_config(config)

        return Ok(value={"name": name, "status": _ACTION_ADDED, "files_indexed": indexed, "chunks": total_chunks})

    def _merge_hybrid(self, results: list[dict], query: str, collections: list[str] | None, limit: int) -> list[dict]:
        like_results = self._search_like(query, collections, limit)
        seen = {r["path"] for r in results}
        for r in like_results:
            if r["path"] not in seen:
                results.append(r)
                seen.add(r["path"])
        return results

    def _apply_boosts(self, results: list[dict], query: str, explain: bool) -> list[dict]:
        for r in results:
            base_score = r.get(_FIELD_SCORE, 0)
            boost = type_boost(query, r)
            imp_boost = importance_boost(r)
            total_boost = boost + imp_boost
            if total_boost > 0:
                r[_FIELD_SCORE] = min(1.0, base_score + total_boost)
                r["boost"] = total_boost
            if explain:
                r["explain"] = _build_explain(base_score, boost, imp_boost, r.get(_FIELD_SCORE, 0), r.get("source", _UNKNOWN))
        return results

    def _run_hybrid_search(self, query: str, collections: list[str] | None, limit: int, min_score: float) -> list[dict]:
        max_intermediate = limit * _HYBRID_MULTIPLIER
        results = self._search_fts5(query, collections, limit * 2)
        # Try vector if FTS results are sparse
        if len(results) < limit:
            try:
                from vector import VectorIndex

                vi = VectorIndex(self)
                vec_results = vi.search(query, limit=limit, min_score=min_score)
                seen = {r["path"] for r in results}
                for r in vec_results:
                    if r["path"] not in seen and len(results) < max_intermediate:
                        results.append(r)
                        seen.add(r["path"])
            except ImportError:
                pass
        # Fallback to LIKE
        if len(results) < limit:
            results = self._merge_hybrid(results, query, collections, limit)
        results.sort(key=lambda r: r.get(_FIELD_SCORE, 0), reverse=True)
        return results[:max_intermediate]

    def _search_vector(self, query: str, limit: int, min_score: float) -> list[dict] | None:
        """Attempt vector search. Returns results or None if unavailable."""
        try:
            from vector import VectorIndex

            vi = VectorIndex(self)
            return vi.search(query, limit=limit, min_score=min_score)
        except ImportError:
            return None

    def _search_strategy(self, query: str, collections: list[str] | None, limit: int, strategy: str, min_score: float) -> list[dict]:
        """Dispatch to the right search backend based on strategy."""
        if strategy == "vector":
            results = self._search_vector(query, limit, min_score)
            if results is not None:
                return results
            strategy = "fts"

        if strategy == "hybrid":
            return self._run_hybrid_search(query, collections, limit, min_score)

        return self._search_fts5(query, collections, limit)

    def _track_retrieval(self, results: list[dict], limit: int) -> None:
        """Increment retrieval counts for returned results."""
        try:
            self._increment_retrieval([r["path"] for r in results[:limit]])
        except (sqlite3.Error, KeyError) as e:
            logger.debug("Retrieval count update failed: %s", e)

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        strategy: str | None = None,
        *,
        explain: bool = False,
        min_score: float = 0.0,
    ) -> Ok[list[dict]] | Err:
        query_error = validate_query(query)
        if query_error:
            return Err(error=query_error)
        if limit > _MAX_SEARCH_LIMIT:
            limit = _MAX_SEARCH_LIMIT
        if strategy is None:
            strategy = auto_strategy(query)

        results = self._search_strategy(query, collections, limit, strategy, min_score)

        results = self._apply_boosts(results, query, explain)
        results.sort(key=lambda x: -x.get(_FIELD_SCORE, 0))
        if min_score > 0:
            results = [r for r in results if r.get(_FIELD_SCORE, 0) >= min_score]

        self._track_retrieval(results, limit)

        return Ok(value=results[:limit])

    def _row_to_result(self, row: sqlite3.Row, source: str, highlighted: str | None = None, score: float | None = None) -> dict:
        # Handle rank column gracefully (may not exist in LIKE/get queries)
        rank = row["rank"] if "rank" in row.keys() else None
        return {
            "path": f"{row['collection']}/{row['file_path']}",
            "content": row["content"][:500],
            "highlighted": highlighted if highlighted else row["content"][:200],
            "collection": row["collection"],
            "title": row["title"],
            "chunk": row["chunk_index"],
            "total_chunks": row["total_chunks"],
            _FIELD_SCORE: score if score is not None else (round(-rank, 3) if rank is not None else 0),
            "source": source,
            "created_at": row["created_at"] if "created_at" in row.keys() else None,
            "retrieval_count": row["retrieval_count"] if "retrieval_count" in row.keys() else 0,
        }

    def _increment_retrieval(self, paths: list[str]) -> None:
        if not paths:
            return
        conn = self._get_conn()
        try:
            for path in paths:
                parts = path.split("/", 1)
                if len(parts) == 2:
                    collection, file_path = parts
                    conn.execute(
                        "UPDATE documents SET retrieval_count = retrieval_count + 1 WHERE collection = ? AND file_path = ?",
                        (collection, file_path),
                    )
            conn.commit()
        except sqlite3.Error as e:
            logger.debug("Failed to update retrieval counts: %s", e)

    def _build_fts_sql(self, collections: list[str] | None) -> tuple[str, list]:
        """Build FTS5 search SQL with optional collection filter."""
        base = """
            SELECT d.file_path, d.content, d.collection, d.title,
                   d.chunk_index, d.total_chunks, rank,
                   snippet(documents_fts, 2, '<b>', '</b>', '...', 20) as highlighted
            FROM documents_fts fts
            JOIN documents d ON fts.rowid = d.id
            WHERE documents_fts MATCH ?
        """
        if collections:
            placeholders = ",".join("?" * len(collections))
            return base + f" AND d.collection IN ({placeholders}) ORDER BY rank LIMIT ?", collections
        return base + " ORDER BY rank LIMIT ?", []

    def _search_fts5(self, query: str, collections: list[str] | None, limit: int) -> list[dict]:
        conn = self._get_conn()
        try:
            # Sanitize: strip everything except alphanumeric, spaces, and hyphens
            sanitized = []
            for t in query.split():
                t = _sanitize_fts5_token(t)
                t = t.strip()
                if t:
                    sanitized.append(f'"{t}"')
            fts_query = " ".join(sanitized) if sanitized else '""'
            sql_suffix, extra_params = self._build_fts_sql(collections)
            params = [fts_query] + extra_params + [limit]
            rows = conn.execute(sql_suffix, params).fetchall()
            return [self._row_to_result(r, "fts5", r["highlighted"]) for r in rows]
        except sqlite3.Error as e:
            logger.debug("FTS5 search failed: %s", e)
            return []

    def _build_like_sql(self, collections: list[str] | None) -> tuple[str, list]:
        """Build LIKE search SQL with optional collection filter."""
        base = """
            SELECT file_path, content, collection, title,
                   chunk_index, total_chunks
            FROM documents
            WHERE (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
        """
        if collections:
            placeholders = ",".join("?" * len(collections))
            return base + f" AND collection IN ({placeholders}) LIMIT ?", collections
        return base + " LIMIT ?", []

    def _search_like(self, query: str, collections: list[str] | None, limit: int) -> list[dict]:
        conn = self._get_conn()
        try:
            escaped = query.replace("%", "\\%").replace("_", "\\_")
            sql_suffix, extra_params = self._build_like_sql(collections)
            params = [f"%{escaped}%", f"%{escaped}%"] + extra_params + [limit]
            rows = conn.execute(sql_suffix, params).fetchall()
            return [self._row_to_result(r, "like", None, score=0.5) for r in rows]
        except sqlite3.Error as e:
            logger.debug("LIKE search failed: %s", e)
            return []

    def _build_get_sql(self, chunk: int | None, collection: str | None) -> tuple[str, list]:
        """Build GET SQL with optional chunk and collection filters."""
        conditions = ["file_path = ?"]
        params: list = []

        if chunk is not None:
            conditions.append("chunk_index = ?")
            params.append(chunk)
        if collection:
            conditions.append("collection = ?")
            params.append(collection)

        where = " AND ".join(conditions)
        return f"SELECT * FROM documents WHERE {where}", params

    def get(self, file_path: str, chunk: int | None = None, collection: str | None = None) -> Ok[dict] | Err:
        conn = self._get_conn()
        try:
            sql, params = self._build_get_sql(chunk, collection)
            params = [file_path] + params

            if chunk is not None:
                row = conn.execute(sql, params).fetchone()
                if row:
                    return Ok(value=dict(row))
                return Err(error=f"Document not found: {file_path}")

            rows = conn.execute(sql + " ORDER BY chunk_index", params).fetchall()
            if not rows:
                return Err(error=f"Document not found: {file_path}")
            content = "\n".join(r["content"] for r in rows)
            return Ok(
                value={
                    "file_path": rows[0]["file_path"],
                    "content": content,
                    "collection": rows[0]["collection"],
                    "title": rows[0]["title"],
                    "chunks": len(rows),
                }
            )
        except sqlite3.Error as e:
            logger.debug("Get failed: %s", e)
            return Err(error=str(e))

    def update_document(self, file_path: str, content: str, title: str | None = None) -> Ok[dict] | Err:
        conn = self._get_conn()
        try:
            # Read existing data before deletion
            existing = conn.execute(
                "SELECT collection, title, extension FROM documents WHERE file_path = ? AND chunk_index = 0",
                (file_path,),
            ).fetchone()
            collection = existing["collection"] if existing else ""
            original_title = existing["title"] if existing else ""
            extension = existing["extension"] if existing else Path(file_path).suffix

            # Delete all existing chunks
            conn.execute("DELETE FROM documents WHERE file_path = ?", (file_path,))

            # Re-chunk the content
            chunks = auto_chunk(content)
            content_hash = self._compute_file_hash(content)
            final_title = title if title is not None else original_title

            # Re-insert chunks
            for i, chunk in enumerate(chunks):
                conn.execute(
                    """INSERT OR REPLACE INTO documents
                    (collection, file_path, content, content_hash, extension, title, chunk_index, total_chunks)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (collection, file_path, chunk, content_hash, extension, final_title, i, len(chunks)),
                )
            conn.commit()
            return Ok(value={"status": "updated", "file_path": file_path, "chunks": len(chunks)})
        except sqlite3.Error as e:
            logger.debug("Update failed: %s", e)
            return Err(error=str(e))

    def delete_document(self, file_path: str) -> Ok[dict] | Err:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM documents WHERE file_path = ?", (file_path,))
            conn.commit()
            return Ok(value={"status": _ACTION_DELETED, "file_path": file_path})
        except sqlite3.Error as e:
            logger.debug("Delete failed: %s", e)
            return Err(error=str(e))

    def record_judgment(self, new_id: str, candidate_id: str, judgment: str) -> Ok[dict] | Err:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO conflict_judgments (new_id, candidate_id, judgment) VALUES (?, ?, ?)",
                (new_id, candidate_id, judgment),
            )
            conn.commit()
            return Ok(value={"status": "recorded", "new_id": new_id, "candidate_id": candidate_id, "judgment": judgment})
        except sqlite3.Error as e:
            logger.warning("Failed to record judgment: %s", e)
            return Err(error=str(e))

    def get_judgments(self, new_id: str) -> Ok[list[dict]] | Err:
        """Get conflict judgments for a document."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM conflict_judgments WHERE new_id = ?",
                (new_id,),
            ).fetchall()
            return Ok(value=[dict(r) for r in rows])
        except sqlite3.Error as e:
            return Err(error=str(e))

    def get_all_documents(self) -> Ok[list[dict]] | Err:
        """Get all documents for vector indexing."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT id, collection, file_path, content, title, chunk_index FROM documents").fetchall()
            return Ok(value=[dict(r) for r in rows])
        except sqlite3.Error as e:
            return Err(error=str(e))

    def count_documents_by_path(self, pattern: str) -> int:
        """Count documents matching a file_path pattern."""
        conn = self._get_conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM documents WHERE file_path LIKE ?",
                (pattern,),
            ).fetchone()[0]
        except sqlite3.Error:
            return 0

    def bulk_insert_raw(self, collection: str, file_path: str, content: str, title: str) -> Ok[dict] | Err:
        """Insert a single document row directly (for benchmark use)."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
                (collection, file_path, content, title),
            )
            conn.commit()
            return Ok(value={"status": "inserted", "file_path": file_path})
        except sqlite3.Error as e:
            return Err(error=str(e))

    def delete_documents_scoped(self, file_path: str, collection: str) -> Ok[dict] | Err:
        """Delete a document scoped to a specific collection."""
        conn = self._get_conn()
        try:
            conn.execute(
                "DELETE FROM documents WHERE file_path = ? AND collection = ?",
                (file_path, collection),
            )
            conn.commit()
            return Ok(value={"status": _ACTION_DELETED, "file_path": file_path, "collection": collection})
        except sqlite3.Error as e:
            return Err(error=str(e))

    def bulk_insert(self, documents: list[dict]) -> Ok[int] | Err:
        conn = self._get_conn()
        try:
            for doc in documents:
                # Delete existing chunks for this file before insert
                coll = doc.get("collection", "")
                path = doc.get("path", "")
                if coll and path:
                    conn.execute(
                        "DELETE FROM documents WHERE collection = ? AND file_path = ?",
                        (coll, path),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
                    (doc.get("collection", ""), doc.get("path", ""), doc.get("content", ""), doc.get("title", "")),
                )
            conn.commit()
            return Ok(value=len(documents))
        except sqlite3.Error as e:
            logger.debug("Bulk insert failed: %s", e)
            return Err(error=str(e))

    def remove_collection(self, name: str) -> Ok[dict] | Err:
        """Remove a collection and all associated data."""
        coll_error = validate_collection(name)
        if coll_error:
            return Err(error=coll_error)

        conn = self._get_conn()
        try:
            # Count documents before deletion
            count = conn.execute("SELECT COUNT(*) FROM documents WHERE collection = ?", (name,)).fetchone()[0]

            # Delete from all tables (order matters: judgments first, then docs)
            conn.execute(
                """DELETE FROM conflict_judgments
                WHERE new_id IN (SELECT file_path FROM documents WHERE collection = ?)
                OR candidate_id IN (SELECT file_path FROM documents WHERE collection = ?)""",
                (name, name),
            )
            conn.execute("DELETE FROM context_attachments WHERE collection = ?", (name,))
            conn.execute("DELETE FROM documents WHERE collection = ?", (name,))
            conn.commit()

            # Remove from config
            config = self._load_config()
            if "repos" in config and name in config["repos"]:
                config["repos"].pop(name)
                self._save_config(config)

            return Ok(value={"status": "removed", "collection": name, "documents": count})
        except sqlite3.Error as e:
            logger.debug("Remove collection failed: %s", e)
            return Err(error=str(e))

    def _rename_documents(self, conn: sqlite3.Connection, old: str, new: str) -> int:
        cursor = conn.execute("UPDATE documents SET collection = ? WHERE collection = ?", (new, old))
        return cursor.rowcount

    def _rename_fts(self, conn: sqlite3.Connection, old: str, new: str) -> None:
        # FTS5 doesn't support UPDATE on content tables — triggers handle it
        pass

    def _rename_contexts(self, conn: sqlite3.Connection, old: str, new: str) -> int:
        cursor = conn.execute("UPDATE context_attachments SET collection = ? WHERE collection = ?", (new, old))
        return cursor.rowcount

    def _rename_judgments(self, conn: sqlite3.Connection, old: str, new: str) -> None:
        conn.execute("UPDATE conflict_judgments SET new_id = ? WHERE new_id = ?", (new, old))
        conn.execute("UPDATE conflict_judgments SET candidate_id = ? WHERE candidate_id = ?", (new, old))

    def rename_collection(self, old_name: str, new_name: str) -> Ok[dict] | Err:
        """Rename a collection across all documents and config."""
        old_err = validate_collection(old_name)
        if old_err:
            return Err(error=f"Invalid old name: {old_err}")
        new_err = validate_collection(new_name)
        if new_err:
            return Err(error=f"Invalid new name: {new_err}")

        conn = self._get_conn()
        try:
            # Check if old collection exists
            count = conn.execute("SELECT COUNT(*) FROM documents WHERE collection = ?", (old_name,)).fetchone()[0]
            if count == 0:
                return Err(error=f"{_ERR_COLLECTION_NOT_FOUND}: {old_name}")

            # Check if new name already exists
            existing = conn.execute("SELECT COUNT(*) FROM documents WHERE collection = ?", (new_name,)).fetchone()[0]
            if existing > 0:
                return Err(error=f"Collection already exists: {new_name}")

            self._rename_documents(conn, old_name, new_name)
            self._rename_contexts(conn, old_name, new_name)
            self._rename_fts(conn, old_name, new_name)
            self._rename_judgments(conn, old_name, new_name)
            conn.commit()

            # Update config
            config = self._load_config()
            if "repos" in config and old_name in config["repos"]:
                config["repos"][new_name] = config["repos"].pop(old_name)
                self._save_config(config)

            return Ok(value={"status": "renamed", "from": old_name, "to": new_name, "documents": count})
        except sqlite3.Error as e:
            logger.debug("Rename collection failed: %s", e)
            return Err(error=str(e))

    def list_collections(self) -> Ok[list[dict]] | Err:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT collection, COUNT(DISTINCT file_path) as docs,
                   COUNT(*) as chunks,
                   GROUP_CONCAT(context, '|') as contexts
                   FROM documents GROUP BY collection"""
            ).fetchall()

            # Get context counts from context_attachments
            ctx_rows = conn.execute(
                """SELECT collection, COUNT(*) as ctx_count
                   FROM context_attachments GROUP BY collection"""
            ).fetchall()
            ctx_counts = {r["collection"]: r["ctx_count"] for r in ctx_rows}

            return Ok(value=[_collection_row_to_dict(r, ctx_counts) for r in rows])
        except sqlite3.Error as e:
            logger.debug("List collections failed: %s", e)
            return Err(error=str(e))

    def list_documents(self, collection: str | None = None) -> Ok[list[dict]] | Err:
        """List all documents (chunk_index=0) optionally filtered by collection."""
        conn = self._get_conn()
        try:
            if collection:
                rows = conn.execute(
                    "SELECT collection, file_path, title, content FROM documents WHERE collection = ? AND chunk_index = 0",
                    (collection,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT collection, file_path, title, content FROM documents WHERE chunk_index = 0").fetchall()
            return Ok(
                value=[
                    {
                        "collection": r["collection"],
                        "path": f"{r['collection']}/{r['file_path']}",
                        "title": r["title"],
                        "content": r["content"],
                    }
                    for r in rows
                ]
            )
        except sqlite3.Error as e:
            logger.debug("List documents failed: %s", e)
            return Err(error=str(e))

    def stats(self) -> Ok[dict] | Err:
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            docs = conn.execute("SELECT COUNT(DISTINCT file_path) FROM documents").fetchone()[0]
            collections = conn.execute("SELECT COUNT(DISTINCT collection) FROM documents").fetchone()[0]
            config = self._load_config()
            return Ok(
                value={
                    "total_chunks": total,
                    "total_documents": docs,
                    "collections": collections,
                    "repos": len(config.get("repos", {})),
                    "db_path": str(self.db_path),
                    "db_size_kb": round(self.db_path.stat().st_size / 1024) if self.db_path.exists() else 0,
                }
            )
        except (sqlite3.Error, OSError) as e:
            logger.debug("Stats failed: %s", e)
            return Err(error=str(e))

    def _load_config(self) -> dict:
        # Config read/write is protected by self._config_lock (threading.Lock)
        # initialized in __init__. Lock prevents concurrent config corruption.
        with self._config_lock:
            if self.config_path.exists():
                try:
                    if self.config_path.stat().st_size > 1_000_000:  # 1MB limit
                        logger.warning("Config file too large, using defaults")
                        return {"repos": {}}
                    return json.loads(self.config_path.read_text())
                except json.JSONDecodeError:
                    logger.warning("Broken config.json, using defaults")
            return {"repos": {}}

    def _save_config(self, config: dict):
        with self._config_lock:
            tmp_path = self.config_path.with_suffix(".tmp")
            try:
                tmp_path.write_text(json.dumps(config, indent=2))
                tmp_path.replace(self.config_path)
            except OSError as e:
                logger.warning("Failed to save config: %s", e)
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

    def _check_file_stale(self, file_path: Path, repo_dir: Path, stored_hash: str) -> dict | None:
        """Check if a single file is stale. Returns finding or None."""
        if file_path.is_symlink():
            return {"file_path": str(file_path.relative_to(repo_dir)), "reason": "symlink_skipped"}
        if not file_path.exists():
            return {"file_path": str(file_path.relative_to(repo_dir)), "reason": "file_deleted"}
        try:
            current_hash = hashlib.sha256(file_path.read_text(errors="ignore").encode()).hexdigest()
            if current_hash != stored_hash:
                return {"file_path": str(file_path.relative_to(repo_dir)), "reason": "content_changed"}
        except OSError:
            return None
        return None

    def check_stale(self, collection: str) -> Ok[list[dict]] | Err:
        coll_error = validate_collection(collection)
        if coll_error:
            return Err(error=coll_error)
        conn = self._get_conn()
        try:
            repo_dir = self.repos_dir / collection
            if not repo_dir.exists():
                return Ok(value=[])

            rows = conn.execute(
                "SELECT file_path, content_hash FROM documents WHERE collection = ? AND chunk_index = 0",
                (collection,),
            ).fetchall()

            stale = []
            for row in rows:
                finding = self._check_file_stale(repo_dir / row["file_path"], repo_dir, row["content_hash"])
                if finding:
                    stale.append(finding)

            return Ok(value=stale)
        except (sqlite3.Error, OSError) as e:
            logger.debug("Stale check failed: %s", e)
            return Err(error=str(e))

    # ── Context Attachments ──────────────────────────────────────────────────

    def add_context(self, collection: str, path: str, summary: str) -> Ok[dict] | Err:
        """Add a context attachment (human-written summary) to a collection."""
        coll_error = validate_collection(collection)
        if coll_error:
            return Err(error=coll_error)
        if not path.strip():
            return Err(error="Context path cannot be empty")
        if not summary.strip():
            return Err(error="Context summary cannot be empty")
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO context_attachments (collection, path, summary) VALUES (?, ?, ?)",
                (collection, path.strip(), summary.strip()),
            )
            conn.commit()
            return Ok(value={"status": _ACTION_ADDED, "collection": collection, "path": path.strip()})
        except sqlite3.Error as e:
            logger.debug("Add context failed: %s", e)
            return Err(error=str(e))

    def get_context(self, collection: str, path: str | None = None) -> Ok[list[dict]] | Err:
        """Get context attachments for a collection."""
        conn = self._get_conn()
        try:
            if path:
                rows = conn.execute(
                    "SELECT * FROM context_attachments WHERE collection = ? AND path = ?",
                    (collection, path.strip()),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM context_attachments WHERE collection = ?",
                    (collection,),
                ).fetchall()
            return Ok(value=[dict(r) for r in rows])
        except sqlite3.Error as e:
            logger.debug("Get context failed: %s", e)
            return Err(error=str(e))

    def list_contexts(self) -> Ok[list[dict]] | Err:
        """List all context attachments."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT collection, path, summary, created_at FROM context_attachments ORDER BY collection, path").fetchall()
            return Ok(value=[dict(r) for r in rows])
        except sqlite3.Error as e:
            logger.debug("List contexts failed: %s", e)
            return Err(error=str(e))

    def remove_context(self, collection: str, path: str | None = None) -> Ok[dict] | Err:
        """Remove context attachment(s)."""
        conn = self._get_conn()
        try:
            if path:
                conn.execute(
                    "DELETE FROM context_attachments WHERE collection = ? AND path = ?",
                    (collection, path.strip()),
                )
            else:
                conn.execute(
                    "DELETE FROM context_attachments WHERE collection = ?",
                    (collection,),
                )
            conn.commit()
            return Ok(value={"status": "removed", "collection": collection})
        except sqlite3.Error as e:
            logger.debug("Remove context failed: %s", e)
            return Err(error=str(e))

    # ── Incremental Embedding ────────────────────────────────────────────────

    def find_changed_docs(self, collection: str, repo_dir: Path | None = None) -> list[dict]:
        """Find documents whose content_hash differs from the repo file.

        Returns list of {file_path, old_hash, new_hash, status} dicts.
        Status: 'changed', 'deleted', 'added'.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT file_path, content_hash FROM documents WHERE collection = ? AND chunk_index = 0",
                (collection,),
            ).fetchall()

            if repo_dir is None:
                repo_dir = self.repos_dir / collection

            stored = {r["file_path"]: r["content_hash"] for r in rows}
            current = (
                {
                    str(f.relative_to(repo_dir)): self._compute_file_hash(f.read_text(errors="ignore"))
                    for f in repo_dir.glob("**/*")
                    if f.is_file() and not f.is_symlink()
                }
                if repo_dir.exists()
                else {}
            )

            changed = []
            for fp, old_hash in stored.items():
                if fp in current:
                    if current[fp] != old_hash:
                        changed.append({"file_path": fp, "old_hash": old_hash, "new_hash": current[fp], "status": "changed"})
                else:
                    changed.append({"file_path": fp, "old_hash": old_hash, "new_hash": None, "status": _ACTION_DELETED})
            for fp in current:
                if fp not in stored:
                    changed.append({"file_path": fp, "old_hash": None, "new_hash": current[fp], "status": _ACTION_ADDED})

            return changed
        except (sqlite3.Error, OSError) as e:
            logger.debug("Find changed docs failed: %s", e)
            return []

    def reindex_collection(self, collection: str) -> Ok[int] | Err:
        """Re-index a collection, only updating changed documents.

        Returns count of documents updated.
        """
        coll_error = validate_collection(collection)
        if coll_error:
            return Err(error=coll_error)

        repo_dir = self.repos_dir / collection
        if not repo_dir.exists():
            return Err(error=f"Repository not found: {collection}")

        changed = self.find_changed_docs(collection, repo_dir)
        if not changed:
            return Ok(value=0)

        conn = self._get_conn()

        # Delete old chunks for changed files
        for item in changed:
            conn.execute("DELETE FROM documents WHERE collection = ? AND file_path = ?", (collection, item["file_path"]))
        conn.commit()

        # Re-index changed files
        updated = 0
        for item in changed:
            fp = item["file_path"]
            if item["status"] == _ACTION_DELETED:
                updated += 1
                continue
            full = repo_dir / fp
            if not full.exists() or full.is_symlink():
                continue
            try:
                self._index_file(conn, full, repo_dir, collection, None)
                updated += 1
            except (OSError, sqlite3.Error) as e:
                logger.debug("Reindexing %s failed: %s", fp, e)
        conn.commit()
        return Ok(value=updated)


# FTS5 triggers (extracted to reduce nesting in _init_db)
_FTS_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
        INSERT INTO documents_fts(rowid, title, content, collection)
        VALUES (new.id, new.title, new.content, new.collection);
    END""",
    """CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, content, collection)
        VALUES ('delete', old.id, old.title, old.content, old.collection);
    END""",
    """CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, content, collection)
        VALUES ('delete', old.id, old.title, old.content, old.collection);
        INSERT INTO documents_fts(rowid, title, content, collection)
        VALUES (new.id, new.title, new.content, new.collection);
    END""",
]

# Re-exports for backward compatibility
__all__ = [
    "Storage",
    "validate_url",
    "validate_collection",
    "validate_query",
    "validate_file_mask",
    "chunk_text",
    "chunk_code",
    "auto_chunk",
    "type_boost",
    "importance_boost",
    "auto_strategy",
]
