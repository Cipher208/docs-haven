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
import sqlite3
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Chunking ────────────────────────────────────────────────────────────────

CHUNK_SIZE = 1000
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
    for _doc_type, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in query_lower:
                if any(k in title_lower or k in content_lower for k in keywords):
                    boost = max(boost, 0.15)
                    break
    return boost


# ── Storage ─────────────────────────────────────────────────────────────────


class Storage:
    """SQLite FTS5-backed document storage with smart search strategies."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.repos_dir = data_dir / "repos"
        self.config_path = data_dir / "config.json"
        self.db_path = data_dir / "docshaven.db"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self._conn = None
        self._conn_lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a persistent connection with WAL mode and performance PRAGMAs."""
        if self._conn is None:
            self._conn_lock.acquire()
            try:
                if self._conn is None:
                    self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                    self._conn.row_factory = sqlite3.Row
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA busy_timeout=5000")
                    self._conn.execute("PRAGMA synchronous=NORMAL")
                    self._conn.execute("PRAGMA cache_size=-64000")
                    self._conn.execute("PRAGMA temp_store=MEMORY")
                    self._conn.execute("PRAGMA mmap_size=268435456")
            finally:
                self._conn_lock.release()
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

        conn.commit()

    def add_repo(
        self,
        url: str,
        tags: list[str] | None = None,
        description: str | None = None,
        mask: str | None = None,
    ) -> dict:
        """Clone repo and index documents into FTS5 with chunking."""
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
                return {"error": f"Clone failed: {result.stderr}"}

        file_mask = mask or "**/*.md"
        files = list(repo_dir.glob(file_mask))
        indexed = 0
        total_chunks = 0

        conn = self._get_conn()
        for f in files:
            if f.is_file() and f.stat().st_size < 500_000:
                try:
                    content = f.read_text(errors="ignore")
                    rel_path = str(f.relative_to(repo_dir))
                    title = f.stem.replace("-", " ").replace("_", " ")

                    chunks = chunk_text(content)
                    import hashlib

                    content_hash = hashlib.sha256(content.encode()).hexdigest()
                    for i, chunk in enumerate(chunks):
                        conn.execute(
                            """INSERT OR REPLACE INTO documents
                            (collection, file_path, content, content_hash, extension, title, context, chunk_index, total_chunks)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (name, rel_path, chunk, content_hash, f.suffix, title, description or "", i, len(chunks)),
                        )
                        total_chunks += 1
                    indexed += 1
                except Exception as e:
                    logger.debug("Skipping %s: %s", f, e)
                    continue
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

        return {"name": name, "status": "added", "files_indexed": indexed, "chunks": total_chunks}

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        strategy: str | None = None,
    ) -> list[dict]:
        """Search with auto strategy selection."""
        if strategy is None:
            strategy = auto_strategy(query)

        results = self._search_fts5(query, collections, limit * 2)

        if strategy == "hybrid" and len(results) < limit:
            like_results = self._search_like(query, collections, limit)
            seen = {r["path"] for r in results}
            for r in like_results:
                if r["path"] not in seen:
                    results.append(r)
                    seen.add(r["path"])

        for r in results:
            boost = type_boost(query, r)
            if boost > 0:
                r["score"] = min(1.0, r.get("score", 0) + boost)
                r["boost"] = boost

        results.sort(key=lambda x: -x.get("score", 0))
        return results[:limit]

    def _search_fts5(self, query: str, collections: list[str] | None, limit: int) -> list[dict]:
        """FTS5 search with BM25 ranking."""
        conn = self._get_conn()
        try:
            fts_query = f'"{query.replace(chr(34), chr(34) + chr(34))}"'

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
            return [
                {
                    "path": f"{r['collection']}/{r['file_path']}",
                    "content": r["content"][:500],
                    "highlighted": r["highlighted"] if r["highlighted"] else r["content"][:200],
                    "collection": r["collection"],
                    "title": r["title"],
                    "chunk": r["chunk_index"],
                    "total_chunks": r["total_chunks"],
                    "score": round(-r["rank"], 3) if r["rank"] else 0,
                    "source": "fts5",
                }
                for r in rows
            ]
        except Exception as e:
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
                    WHERE (title LIKE ? OR content LIKE ?)
                    AND collection IN ({placeholders})
                    LIMIT ?
                """
                params = [f"%{escaped}%", f"%{escaped}%"] + collections + [limit]
            else:
                sql = """
                    SELECT file_path, content, collection, title,
                           chunk_index, total_chunks
                    FROM documents
                    WHERE title LIKE ? OR content LIKE ?
                    LIMIT ?
                """
                params = [f"%{escaped}%", f"%{escaped}%", limit]

            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "path": f"{r['collection']}/{r['file_path']}",
                    "content": r["content"][:500],
                    "collection": r["collection"],
                    "title": r["title"],
                    "chunk": r["chunk_index"],
                    "total_chunks": r["total_chunks"],
                    "score": 0.5,
                    "source": "like",
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug("LIKE search failed: %s", e)
            return []

    def get(self, file_path: str, chunk: int | None = None) -> dict | None:
        """Get a document by path, optionally a specific chunk."""
        conn = self._get_conn()
        try:
            if chunk is not None:
                row = conn.execute(
                    "SELECT * FROM documents WHERE file_path = ? AND chunk_index = ?",
                    (file_path, chunk),
                ).fetchone()
            else:
                rows = conn.execute(
                    "SELECT * FROM documents WHERE file_path = ? ORDER BY chunk_index",
                    (file_path,),
                ).fetchall()
                if not rows:
                    return None
                content = "\n".join(r["content"] for r in rows)
                return {
                    "file_path": rows[0]["file_path"],
                    "content": content,
                    "collection": rows[0]["collection"],
                    "title": rows[0]["title"],
                    "chunks": len(rows),
                }
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.debug("Get failed: %s", e)
            return None

    def list_collections(self) -> list[dict]:
        """List all collections with document counts."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT collection, COUNT(DISTINCT file_path) as docs,
                   COUNT(*) as chunks,
                   GROUP_CONCAT(context, '|') as contexts
                   FROM documents GROUP BY collection"""
            ).fetchall()
            return [
                {
                    "name": r["collection"],
                    "count": r["docs"],
                    "chunks": r["chunks"],
                    "contexts": r["contexts"].split("|") if r["contexts"] else [],
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug("List collections failed: %s", e)
            return []

    def stats(self) -> dict:
        """Get database statistics."""
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            docs = conn.execute("SELECT COUNT(DISTINCT file_path) FROM documents").fetchone()[0]
            collections = conn.execute("SELECT COUNT(DISTINCT collection) FROM documents").fetchone()[0]
            config = self._load_config()
            return {
                "total_chunks": total,
                "total_documents": docs,
                "collections": collections,
                "repos": len(config.get("repos", {})),
                "db_path": str(self.db_path),
                "db_size_kb": round(self.db_path.stat().st_size / 1024) if self.db_path.exists() else 0,
            }
        except Exception as e:
            logger.debug("Stats failed: %s", e)
            return {"total_chunks": 0, "total_documents": 0, "collections": 0, "repos": 0, "db_path": "", "db_size_kb": 0}

    def _load_config(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"repos": {}}

    def _save_config(self, config: dict):
        """Atomic write via temp file + rename."""
        tmp_path = self.config_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(config, indent=2))
            tmp_path.replace(self.config_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def check_stale(self, collection: str) -> list[dict]:
        """Check for stale documents by comparing content hashes."""
        conn = self._get_conn()
        try:
            repo_dir = self.repos_dir / collection
            if not repo_dir.exists():
                return []

            stale = []
            rows = conn.execute(
                "SELECT file_path, content_hash FROM documents WHERE collection = ? AND chunk_index = 0",
                (collection,),
            ).fetchall()

            for row in rows:
                file_path = repo_dir / row["file_path"]
                if file_path.exists():
                    current_hash = hashlib.sha256(file_path.read_text(errors="ignore").encode()).hexdigest()
                    if current_hash != row["content_hash"]:
                        stale.append({"file_path": row["file_path"], "reason": "content_changed"})
                else:
                    stale.append({"file_path": row["file_path"], "reason": "file_deleted"})

            return stale
        except Exception as e:
            logger.debug("Stale check failed: %s", e)
            return []
