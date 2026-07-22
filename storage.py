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

from result import Err, Ok

logger = logging.getLogger(__name__)

# Valid URI domains for collection naming (matches uri.py VALID_DOMAINS)
_VALID_DOMAINS = {"core", "ref", "guide", "lib", "src", "test", "note"}

# ── Validation ──────────────────────────────────────────────────────────────

_MAX_QUERY_LENGTH = 10000
_MAX_FTS5_TOKENS = 100
_MAX_SEARCH_LIMIT = 1000
_VALID_URL_SCHEMES = ("https://", "http://", "git@")
_COLLECTION_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_url(url: str) -> str | None:
    if not url.startswith(_VALID_URL_SCHEMES):
        return f"Invalid URL scheme: {url}"
    if len(url) > 2048:
        return "URL too long (max 2048 chars)"
    # Block shell metacharacters and newlines that could exploit git
    dangerous_chars = set("\n\r\t`$&|;<>\\")
    if any(c in url for c in dangerous_chars):
        return "URL contains dangerous characters"
    # Block URL-encoded traversal
    if "%2e" in url.lower() or "%2f" in url.lower():
        return "URL contains encoded path traversal"
    return None


def validate_collection(name: str) -> str | None:
    if not name:
        return "Collection name cannot be empty"
    if len(name) > 255:
        return "Collection name too long (max 255 chars)"
    if not _COLLECTION_PATTERN.match(name):
        return f"Invalid collection name: {name}"
    return None


def validate_query(query: str) -> str | None:
    if len(query) > _MAX_QUERY_LENGTH:
        return f"Query too long (max {_MAX_QUERY_LENGTH} chars)"
    return None


def validate_file_mask(mask: str) -> str | None:
    if ".." in mask:
        return "File mask must not contain '..' (path traversal)"
    if mask.startswith("/"):
        return "File mask must not start with '/'"
    return None


# ── Chunking ────────────────────────────────────────────────────────────────

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    # Clamp overlap to prevent infinite loop
    overlap = min(overlap, chunk_size - 1)

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        if end < len(text):
            last_period = chunk.rfind(". ")
            last_newline = chunk.rfind("\n\n")
            break_at = max(last_period, last_newline)
            if break_at > chunk_size // 2:
                chunk = text[start : start + break_at + 1]
                end = start + break_at + 1

        chunks.append(chunk.strip())
        start = end - overlap

    return [c for c in chunks if c]


def chunk_code(text: str) -> list[str]:
    """Chunk code files by function/class boundaries."""
    if not text:
        return []
    # Split on function/class definitions or blank lines
    chunks = re.split(r"\n(?=(?:def |class |async def |# ---|## ))", text)
    return [c.strip() for c in chunks if c.strip()]


# File extensions that should use code chunking
_CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php"}


def auto_chunk(text: str, file_path: str | None = None) -> list[str]:
    """Auto-select chunking strategy based on file type."""
    if file_path:
        ext = Path(file_path).suffix.lower()
        if ext in _CODE_EXTENSIONS:
            chunks = chunk_code(text)
            if chunks:
                return chunks
    return chunk_text(text)


# ── Auto Strategy ───────────────────────────────────────────────────────────


def auto_strategy(query: str) -> str:
    if len(query.split()) <= 2:
        return "fts"
    return "hybrid"


# ── Type Boost ──────────────────────────────────────────────────────────────

TYPE_KEYWORDS = {
    "api": ["api", "endpoint", "route", "handler", "request", "response"],
    "tutorial": ["tutorial", "guide", "howto", "how to", "step", "example"],
    "reference": ["reference", "docs", "documentation", "spec", "specification"],
    "config": ["config", "configuration", "setup", "install", "environment"],
}


def type_boost(query: str, result: dict) -> float:
    query_lower = query.lower()
    title_lower = result.get("title", "").lower()
    content_lower = result.get("content", "").lower()[:200]

    boost = 0.0
    for keywords in TYPE_KEYWORDS.values():
        for kw in keywords:
            if kw in query_lower:
                if any(k in title_lower or k in content_lower for k in keywords):
                    boost = max(boost, 0.15)
                    break
    return boost


# ── Storage ─────────────────────────────────────────────────────────────────


class Storage:
    """SQLite FTS5-backed document storage with smart search strategies."""

    @classmethod
    def default(cls) -> "Storage":
        return cls(Path.home() / ".docshaven")

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.repos_dir = data_dir / "repos"
        self.config_path = data_dir / "config.json"
        self.db_path = data_dir / "docshaven.db"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        self._db_initialized = False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        with self._conn_lock:
            if self._conn is not None:
                try:
                    self._conn.execute("SELECT 1")
                    return self._conn
                except sqlite3.ProgrammingError:
                    self._conn = None
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")
            self._conn = conn
            # Lazy init: create tables on first connection
            if not self._db_initialized:
                self._init_db()
                self._db_initialized = True
            return self._conn

    def _init_db(self):
        conn = self._conn
        assert conn is not None, "_init_db called before connection established"
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
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(collection, file_path, chunk_index)
            )
        """)
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_filepath ON documents(file_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection_filepath ON documents(collection, file_path)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conflict_judgments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                new_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                judgment TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Context attachments — human-written summaries for collections
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
        conn.commit()

    def _index_file(self, conn: sqlite3.Connection, f: Path, repo_dir: Path, name: str, description: str | None) -> int:
        if f.is_symlink():
            return 0
        # Path traversal check
        try:
            f.resolve().relative_to(repo_dir.resolve())
        except ValueError:
            return 0
        # Skip binary files
        _BINARY_SUFFIXES = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".pyc",
            ".pyo",
            ".so",
            ".dll",
            ".exe",
            ".bin",
            ".whl",
            ".zip",
            ".tar",
            ".gz",
        }
        if f.suffix.lower() in _BINARY_SUFFIXES:
            return 0
        content = f.read_text(errors="ignore")
        rel_path = str(f.relative_to(repo_dir))
        title = f.stem.replace("-", " ").replace("_", " ")
        chunks = auto_chunk(content, str(f))
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        for i, chunk in enumerate(chunks):
            conn.execute(
                """INSERT OR REPLACE INTO documents
                (collection, file_path, content, content_hash, extension, title, context, chunk_index, total_chunks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, rel_path, chunk, content_hash, f.suffix, title, description or "", i, len(chunks)),
            )
        return len(chunks)

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
        # Validate derived collection name
        name_error = validate_collection(name)
        if name_error:
            return Err(error=f"Invalid collection name from URL: {name_error}")
        repo_dir = self.repos_dir / name

        if not repo_dir.exists():
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

        file_mask = mask or "**/*.md"
        mask_error = validate_file_mask(file_mask)
        if mask_error:
            return Err(error=mask_error)
        # Filter out symlinks and paths that resolve outside repo_dir
        resolved_root = repo_dir.resolve()
        files = []
        for f in repo_dir.glob(file_mask):
            if not f.is_file():
                continue
            if f.is_symlink():
                continue
            try:
                resolved = f.resolve()
                # Use Path.parents for proper path containment check
                if resolved != resolved_root and resolved_root not in resolved.parents:
                    continue
                if f.stat().st_size < 500_000:
                    files.append(f)
            except OSError:
                continue
        indexed = 0
        total_chunks = 0

        conn = self._get_conn()
        for f in files:
            try:
                total_chunks += self._index_file(conn, f, repo_dir, name, description)
                indexed += 1
            except (OSError, sqlite3.Error) as e:
                logger.debug("Skipping %s: %s", f, e)
        conn.commit()

        config = self._load_config()
        config["repos"][name] = {
            "url": url,
            "tags": tags or [],
            "description": description or "",
            "files_indexed": indexed,
            "total_chunks": total_chunks,
        }
        self._save_config(config)

        return Ok(value={"name": name, "status": "added", "files_indexed": indexed, "chunks": total_chunks})

    def _merge_hybrid(self, results: list[dict], query: str, collections: list[str] | None, limit: int) -> list[dict]:
        like_results = self._search_like(query, collections, limit)
        seen = {r["path"] for r in results}
        for r in like_results:
            if r["path"] not in seen:
                results.append(r)
                seen.add(r["path"])
        return results

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

        # Vector search (optional — requires VectorIndex)
        if strategy == "vector":
            try:
                from vector import VectorIndex

                vi = VectorIndex(self)
                results = vi.search(query, limit=limit, min_score=min_score)
                return Ok(value=results)
            except ImportError:
                logger.debug("VectorIndex not available, falling back to FTS5")
                strategy = "fts"

        results = self._search_fts5(query, collections, limit * 2)

        if strategy == "hybrid" and len(results) < limit:
            # Try vector search in hybrid mode
            try:
                from vector import VectorIndex

                vi = VectorIndex(self)
                vec_results = vi.search(query, limit=limit, min_score=min_score)
                seen = {r["path"] for r in results}
                for r in vec_results:
                    if r["path"] not in seen:
                        results.append(r)
                        seen.add(r["path"])
            except ImportError:
                pass
            # Also try LIKE fallback
            if len(results) < limit:
                results = self._merge_hybrid(results, query, collections, limit)

        for r in results:
            base_score = r.get("score", 0)
            boost = type_boost(query, r)
            if boost > 0:
                r["score"] = min(1.0, base_score + boost)
                r["boost"] = boost
            if explain:
                r["explain"] = {
                    "base_score": round(base_score, 3),
                    "type_boost": round(boost, 3),
                    "final_score": round(r.get("score", 0), 3),
                    "source": r.get("source", "unknown"),
                }

        results.sort(key=lambda x: -x.get("score", 0))
        if min_score > 0:
            results = [r for r in results if r.get("score", 0) >= min_score]
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
            "score": score if score is not None else (round(-rank, 3) if rank is not None else 0),
            "source": source,
        }

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
            # Sanitize tokens: escape FTS5 special characters
            tokens = query.split()
            sanitized = []
            for t in tokens:
                # Escape double quotes and strip FTS5 operators
                t = t.replace('"', '""')
                t = re.sub(r"[*+^~:{}]|\b(OR|AND|NEAR|NOT)\b", "", t, flags=re.IGNORECASE)
                if t.strip():
                    sanitized.append(f'"{t.strip()}"')
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
            if title:
                conn.execute(
                    "UPDATE documents SET content = ?, title = ?, updated_at = datetime('now') WHERE file_path = ?",
                    (content, title, file_path),
                )
            else:
                conn.execute(
                    "UPDATE documents SET content = ?, updated_at = datetime('now') WHERE file_path = ?",
                    (content, file_path),
                )
            conn.commit()
            return Ok(value={"status": "updated", "file_path": file_path})
        except sqlite3.Error as e:
            logger.debug("Update failed: %s", e)
            return Err(error=str(e))

    def delete_document(self, file_path: str) -> Ok[dict] | Err:
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM documents WHERE file_path = ?", (file_path,))
            conn.commit()
            return Ok(value={"status": "deleted", "file_path": file_path})
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
            count = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE collection = ?", (name,)
            ).fetchone()[0]

            # Delete from all tables
            conn.execute("DELETE FROM documents WHERE collection = ?", (name,))
            conn.execute("DELETE FROM context_attachments WHERE collection = ?", (name,))
            conn.execute(
                "DELETE FROM conflict_judgments WHERE new_id = ? OR candidate_id = ?",
                (name, name),
            )
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
                return Err(error=f"Collection not found: {old_name}")

            # Check if new name already exists
            existing = conn.execute("SELECT COUNT(*) FROM documents WHERE collection = ?", (new_name,)).fetchone()[0]
            if existing > 0:
                return Err(error=f"Collection already exists: {new_name}")

            # Rename all documents
            conn.execute(
                "UPDATE documents SET collection = ? WHERE collection = ?",
                (new_name, old_name),
            )
            # Rename context attachments
            conn.execute(
                "UPDATE context_attachments SET collection = ? WHERE collection = ?",
                (new_name, old_name),
            )
            # Rename conflict judgments
            conn.execute(
                "UPDATE conflict_judgments SET new_id = ? WHERE new_id = ?",
                (new_name, old_name),
            )
            conn.execute(
                "UPDATE conflict_judgments SET candidate_id = ? WHERE candidate_id = ?",
                (new_name, old_name),
            )
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
            return Ok(
                value=[
                    {
                        "name": r["collection"],
                        "count": r["docs"],
                        "chunks": r["chunks"],
                        "contexts": r["contexts"].split("|") if r["contexts"] else [],
                        "domain": r["collection"].split("__")[0]
                        if "__" in r["collection"] and r["collection"].split("__")[0] in _VALID_DOMAINS
                        else None,
                    }
                    for r in rows
                ]
            )
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
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text())
            except json.JSONDecodeError:
                logger.warning("Broken config.json, using defaults")
        return {"repos": {}}

    def _save_config(self, config: dict):
        tmp_path = self.config_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(config, indent=2))
            tmp_path.replace(self.config_path)
        except OSError:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

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
            return Ok(value={"status": "added", "collection": collection, "path": path.strip()})
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
            changed = []

            if repo_dir.exists():
                current_files = {}
                for f in repo_dir.glob("**/*"):
                    if f.is_file() and not f.is_symlink():
                        rel = str(f.relative_to(repo_dir))
                        h = hashlib.sha256(f.read_text(errors="ignore").encode()).hexdigest()
                        current_files[rel] = h

                # Check for changed or deleted files
                for fp, old_hash in stored.items():
                    if fp in current_files:
                        if current_files[fp] != old_hash:
                            changed.append({"file_path": fp, "old_hash": old_hash, "new_hash": current_files[fp], "status": "changed"})
                    else:
                        changed.append({"file_path": fp, "old_hash": old_hash, "new_hash": None, "status": "deleted"})

                # Check for added files
                for fp in current_files:
                    if fp not in stored:
                        changed.append({"file_path": fp, "old_hash": None, "new_hash": current_files[fp], "status": "added"})

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
        updated = 0

        for item in changed:
            fp = item["file_path"]
            full_path = repo_dir / fp

            if item["status"] in ("deleted",):
                conn.execute("DELETE FROM documents WHERE collection = ? AND file_path = ?", (collection, fp))
                updated += 1
            elif item["status"] in ("changed", "added") and full_path.exists():
                # Delete old chunks and re-index
                conn.execute("DELETE FROM documents WHERE collection = ? AND file_path = ?", (collection, fp))
                try:
                    self._index_file(conn, full_path, repo_dir, collection, None)
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
