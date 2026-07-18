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
import sqlite3
import subprocess
import threading
from pathlib import Path

from result import Err, Ok

logger = logging.getLogger(__name__)

# Valid URI domains for collection naming (matches uri.py VALID_DOMAINS)
_VALID_DOMAINS = {"core", "ref", "guide", "lib", "src", "test", "note"}

# ── Validation ──────────────────────────────────────────────────────────────

# Max query length for FTS5 (prevents memory exhaustion)
_MAX_QUERY_LENGTH = 10000
# Max tokens in FTS5 query
_MAX_FTS5_TOKENS = 100
# Max search result limit
_MAX_SEARCH_LIMIT = 1000
# Valid URL schemes for git clone
_VALID_URL_SCHEMES = ("https://", "http://", "git@")
# Pattern for safe collection names (alphanumeric + underscore + hyphen)
_COLLECTION_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_url(url: str) -> str | None:
    """Validate URL for git clone. Returns error message or None."""
    if not url.startswith(_VALID_URL_SCHEMES):
        return f"Invalid URL scheme: {url}"
    if len(url) > 2048:
        return "URL too long (max 2048 chars)"
    return None


def validate_collection(name: str) -> str | None:
    """Validate collection name. Returns error message or None."""
    if not name:
        return "Collection name cannot be empty"
    if len(name) > 255:
        return "Collection name too long (max 255 chars)"
    if not _COLLECTION_PATTERN.match(name):
        return f"Invalid collection name: {name}"
    return None


def validate_query(query: str) -> str | None:
    """Validate search query. Returns error message or None."""
    if len(query) > _MAX_QUERY_LENGTH:
        return f"Query too long (max {_MAX_QUERY_LENGTH} chars)"
    return None


def validate_file_mask(mask: str) -> str | None:
    """Validate file mask for path traversal. Returns error message or None."""
    if ".." in mask:
        return "File mask must not contain '..' (path traversal)"
    if mask.startswith("/"):
        return "File mask must not start with '/'"
    return None


# ── Chunking ────────────────────────────────────────────────────────────────

# ADR-004: 1000 chars balances search precision vs context retention
CHUNK_SIZE = 1000
# 20% overlap prevents losing context at chunk boundaries
CHUNK_OVERLAP = 200


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks for better search precision."""
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

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


# ── Auto Strategy ───────────────────────────────────────────────────────────


# ADR-005: ≤2 words = fast FTS5; longer queries benefit from LIKE fallback
def auto_strategy(query: str) -> str:
    """Pick search strategy based on query complexity."""
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
    """Calculate type-aware boost for a search result."""
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
        """Create Storage with default ~/.docshaven path."""
        return cls(Path.home() / ".docshaven")

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.repos_dir = data_dir / "repos"
        self.config_path = data_dir / "config.json"
        self.db_path = data_dir / "docshaven.db"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        # ADR-007: Single persistent connection for MCP server concurrency.
        # check_same_thread=False allows cross-thread access from async handlers.
        # _conn_lock protects _get_conn from double-creation under concurrent requests.
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        self._init_db()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a persistent connection with WAL mode and performance PRAGMAs."""
        if self._conn is not None:
            try:
                self._conn.execute("SELECT 1")
                return self._conn
            except sqlite3.ProgrammingError:
                self._conn = None
        with self._conn_lock:
            if self._conn is None:
                conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-64000")
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA mmap_size=268435456")
                self._conn = conn
        return self._conn

    def _init_db(self):
        """Initialize SQLite database with FTS5 tables and chunk support."""
        conn = self._get_conn()

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

        # Triggers to keep FTS in sync
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, title, content, collection)
                VALUES (new.id, new.title, new.content, new.collection);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, content, collection)
                VALUES ('delete', old.id, old.title, old.content, old.collection);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, content, collection)
                VALUES ('delete', old.id, old.title, old.content, old.collection);
                INSERT INTO documents_fts(rowid, title, content, collection)
                VALUES (new.id, new.title, new.content, new.collection);
            END
        """)

        # Indexes for filtered queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_filepath ON documents(file_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection_filepath ON documents(collection, file_path)")

        # Conflict judgments table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conflict_judgments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                new_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                judgment TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        conn.commit()

    def _index_file(self, conn: sqlite3.Connection, f: Path, repo_dir: Path, name: str, description: str | None) -> int:
        """Index a single file into FTS5. Returns number of chunks inserted."""
        if f.is_symlink():
            return 0
        content = f.read_text(errors="ignore")
        rel_path = str(f.relative_to(repo_dir))
        title = f.stem.replace("-", " ").replace("_", " ")
        chunks = chunk_text(content)
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
        """Clone repo and index documents into FTS5 with chunking."""
        url_error = validate_url(url)
        if url_error:
            return Err(error=url_error)

        name = url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_dir = self.repos_dir / name

        if not repo_dir.exists():
            result = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(repo_dir)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                # Clean up partial clone
                if repo_dir.exists():
                    import shutil

                    shutil.rmtree(repo_dir, ignore_errors=True)
                return Err(error=f"Clone failed: {result.stderr}")

        file_mask = mask or "**/*.md"
        mask_error = validate_file_mask(file_mask)
        if mask_error:
            return Err(error=mask_error)
        files = [f for f in repo_dir.glob(file_mask) if f.is_file() and f.stat().st_size < 500_000]
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
        """Merge FTS5 results with LIKE fallback for hybrid search."""
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
        """Search with auto strategy selection."""
        query_error = validate_query(query)
        if query_error:
            return Err(error=query_error)
        if limit > _MAX_SEARCH_LIMIT:
            limit = _MAX_SEARCH_LIMIT
        if strategy is None:
            strategy = auto_strategy(query)

        results = self._search_fts5(query, collections, limit * 2)

        if strategy == "hybrid" and len(results) < limit:
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
        """Convert a database row to a search result dict."""
        return {
            "path": f"{row['collection']}/{row['file_path']}",
            "content": row["content"][:500],
            "highlighted": highlighted if highlighted else row["content"][:200],
            "collection": row["collection"],
            "title": row["title"],
            "chunk": row["chunk_index"],
            "total_chunks": row["total_chunks"],
            "score": score if score is not None else (round(-row["rank"], 3) if row["rank"] else 0),
            "source": source,
        }

    def _search_fts5(self, query: str, collections: list[str] | None, limit: int) -> list[dict]:
        """FTS5 search with BM25 ranking."""
        conn = self._get_conn()
        try:
            # Escape each token and wrap in quotes to prevent FTS5 operator injection
            tokens = query.split()
            fts_query = " ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens) if tokens else '""'

            if collections:
                placeholders = ",".join("?" * len(collections))
                sql = f"""
                    SELECT d.file_path, d.content, d.collection, d.title,
                           d.chunk_index, d.total_chunks, rank,
                           snippet(documents_fts, 2, '<b>', '</b>', '...', 20) as highlighted
                    FROM documents_fts fts
                    JOIN documents d ON fts.rowid = d.id
                    WHERE documents_fts MATCH ?
                    AND d.collection IN ({placeholders})
                    ORDER BY rank
                    LIMIT ?
                """
                params = [fts_query] + collections + [limit]
            else:
                sql = """
                    SELECT d.file_path, d.content, d.collection, d.title,
                           d.chunk_index, d.total_chunks, rank,
                           snippet(documents_fts, 2, '<b>', '</b>', '...', 20) as highlighted
                    FROM documents_fts fts
                    JOIN documents d ON fts.rowid = d.id
                    WHERE documents_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                params = [fts_query, limit]

            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_result(r, "fts5", r["highlighted"]) for r in rows]
        except sqlite3.Error as e:
            logger.debug("FTS5 search failed: %s", e)
            return []

    def _search_like(self, query: str, collections: list[str] | None, limit: int) -> list[dict]:
        """LIKE fallback for when FTS5 fails or for hybrid search."""
        conn = self._get_conn()
        try:
            escaped = query.replace("%", "\\%").replace("_", "\\_")

            if collections:
                placeholders = ",".join("?" * len(collections))
                sql = f"""
                    SELECT file_path, content, collection, title,
                           chunk_index, total_chunks
                    FROM documents
                    WHERE (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
                    AND collection IN ({placeholders})
                    LIMIT ?
                """
                params = [f"%{escaped}%", f"%{escaped}%"] + collections + [limit]
            else:
                sql = """
                    SELECT file_path, content, collection, title,
                           chunk_index, total_chunks
                    FROM documents
                    WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'
                    LIMIT ?
                """
                params = [f"%{escaped}%", f"%{escaped}%", limit]

            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_result(r, "like", None, score=0.5) for r in rows]
        except sqlite3.Error as e:
            logger.debug("LIKE search failed: %s", e)
            return []

    def get(self, file_path: str, chunk: int | None = None, collection: str | None = None) -> Ok[dict] | Err:
        """Get a document by path, optionally a specific chunk and collection."""
        conn = self._get_conn()
        try:
            if chunk is not None:
                if collection:
                    row = conn.execute(
                        "SELECT * FROM documents WHERE file_path = ? AND chunk_index = ? AND collection = ?",
                        (file_path, chunk, collection),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT * FROM documents WHERE file_path = ? AND chunk_index = ?",
                        (file_path, chunk),
                    ).fetchone()
                if row:
                    return Ok(value=dict(row))
                return Err(error=f"Document not found: {file_path}")
            else:
                if collection:
                    rows = conn.execute(
                        "SELECT * FROM documents WHERE file_path = ? AND collection = ? ORDER BY chunk_index",
                        (file_path, collection),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM documents WHERE file_path = ? ORDER BY chunk_index",
                        (file_path,),
                    ).fetchall()
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
        """Update a document's content."""
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
        """Delete a document by path."""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM documents WHERE file_path = ?", (file_path,))
            conn.commit()
            return Ok(value={"status": "deleted", "file_path": file_path})
        except sqlite3.Error as e:
            logger.debug("Delete failed: %s", e)
            return Err(error=str(e))

    def record_judgment(self, new_id: str, candidate_id: str, judgment: str) -> Ok[dict] | Err:
        """Record a conflict judgment."""
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
        """Bulk insert documents. Returns count of inserted documents."""
        conn = self._get_conn()
        try:
            for doc in documents:
                conn.execute(
                    "INSERT OR REPLACE INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
                    (doc.get("collection", ""), doc.get("path", ""), doc.get("content", ""), doc.get("title", "")),
                )
            conn.commit()
            return Ok(value=len(documents))
        except sqlite3.Error as e:
            logger.debug("Bulk insert failed: %s", e)
            return Err(error=str(e))

    def list_collections(self) -> Ok[list[dict]] | Err:
        """List all collections with document counts."""
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

    def stats(self) -> Ok[dict] | Err:
        """Get database statistics."""
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
        """Atomic write via temp file + rename."""
        tmp_path = self.config_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(config, indent=2))
            tmp_path.replace(self.config_path)
        except OSError:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def check_stale(self, collection: str) -> Ok[list[dict]] | Err:
        """Check for stale documents by comparing content hashes."""
        coll_error = validate_collection(collection)
        if coll_error:
            return Err(error=coll_error)
        conn = self._get_conn()
        try:
            repo_dir = self.repos_dir / collection
            if not repo_dir.exists():
                return Ok(value=[])

            stale = []
            rows = conn.execute(
                "SELECT file_path, content_hash FROM documents WHERE collection = ? AND chunk_index = 0",
                (collection,),
            ).fetchall()

            for row in rows:
                file_path = repo_dir / row["file_path"]
                if file_path.is_symlink():
                    stale.append({"file_path": row["file_path"], "reason": "symlink_skipped"})
                elif file_path.exists():
                    current_hash = hashlib.sha256(file_path.read_text(errors="ignore").encode()).hexdigest()
                    if current_hash != row["content_hash"]:
                        stale.append({"file_path": row["file_path"], "reason": "content_changed"})
                else:
                    stale.append({"file_path": row["file_path"], "reason": "file_deleted"})

            return Ok(value=stale)
        except (sqlite3.Error, OSError) as e:
            logger.debug("Stale check failed: %s", e)
            return Err(error=str(e))
