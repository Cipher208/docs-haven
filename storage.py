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
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from chunking import _BINARY_SUFFIXES, auto_chunk, chunk_code, chunk_text  # noqa: F401
from result import Err, Ok
from scoring import _query_terms, _token_set, importance_boost, lexical_relevance, type_boost  # noqa: F401
from uri import VALID_DOMAINS as _VALID_DOMAINS
from validation import (
    _MAX_SEARCH_LIMIT,
    domain_slug,
    normalize_http_url,
    parse_since,
    text_slug,
    validate_collection,
    validate_file_mask,
    validate_http_url,
    validate_limit,
    validate_local_dir,
    validate_query,
    validate_since,
    validate_text_content,
    validate_topic,
    validate_url,
)

logger = logging.getLogger(__name__)

# SQLite PRAGMA constants
_CACHE_SIZE_KB = 64000
_MMAP_SIZE = 256 * 1024 * 1024  # 256 MiB

# Scoring constants
_HYBRID_MULTIPLIER = 3

# Context-aware search constants
CONTEXT_BOOST = 0.1
_RECENT_COLLECTIONS_MAX = 10

# DH-3 session document-access log (kb_recent): in-memory, per Storage,
# no schema migration — see FUTURE_PLAN P1 #6.
_RECENT_DOCS_MAX = 50

# DH-3 related-document scoring (kb_related): tag overlap + collection
# proximity + content similarity. The schema has no tag column, so the "tag"
# signal is title-term overlap (documented substitution).
_RELATED_W_TAG = 0.4
_RELATED_W_COLLECTION = 0.2
_RELATED_W_CONTENT = 0.4
_TAG_OVERLAP_SOURCE = "title_terms"

# Query expansion constants
_MAX_EXPANSION_TERMS = 24
_STEM_SUFFIXES = ("ions", "ion", "ing", "ed", "es", "er", "or", "ly", "ment", "s")
_STEM_FORMS = ("e", "s", "es", "ing", "ed", "er", "or", "ion")

# Configurable expansion dictionary: single-word keys are synonyms, multi-word
# keys are standard pattern mappings ("how to" -> tutorial/guide/example).
# Instances copy this into Storage.expansion_dictionary; callers may extend or
# override per call via the `expansion` argument of _expand_query.
DEFAULT_EXPANSIONS: dict[str, list[str]] = {
    "depends": ["dependency injection", "dependencies"],
    "dependency injection": ["depends", "dependencies"],
    "config": ["configuration", "setup"],
    "auth": ["authentication", "authorization"],
    "docs": ["documentation"],
    "how to": ["tutorial", "guide", "example"],
    "async generator": ["async_generator", "yield from"],
}

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


def _build_explain(base_score: float, boost: float, imp_boost: float, ctx_boost: float, score: float, source: str) -> dict:
    return {
        "base_score": round(base_score, 3),
        "type_boost": round(boost, 3),
        "importance_boost": round(imp_boost, 3),
        "context_boost": round(ctx_boost, 3),
        "final_score": round(score, 3),
        "source": source,
    }


# ── Auto Strategy ───────────────────────────────────────────────────────────


def _sanitize_fts5_token(token: str) -> str:
    return re.sub(r"[^\w]", "", token)


def _fts_match_term(term: str) -> str | None:
    """Build one FTS5 MATCH atom; multi-word terms become quoted phrases."""
    words = [_sanitize_fts5_token(w) for w in term.split()]
    words = [w for w in words if w]
    if not words:
        return None
    return '"' + " ".join(words) + '"'


def _stem_variants(token: str) -> set[str]:
    """Light rule-based stemming: generator -> generate, generating, generated, ..."""
    t = token.casefold()
    base = None
    for suf in _STEM_SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            base = t[: -len(suf)]
            break
    if base is None:
        base = t[:-1] if t.endswith("e") and len(t) > 3 else t
    elif base.endswith("e"):
        base = base[:-1]
    variants = {base + form for form in _STEM_FORMS}
    variants.add(base)
    return {v for v in variants if v and v != t}


def auto_strategy(query: str) -> str:
    if len(query.split()) <= 2:
        return "fts"
    return "hybrid"


def split_collection_path(path: str) -> tuple[str | None, str]:
    """Split a "collection/file_path" reference into (collection, file_path).

    The first "/"-segment is taken as a collection only when it contains no
    ":" (collection names match ^[a-zA-Z0-9_-]+$ per validate_collection, so
    a colon can never appear in one). Scheme-bearing paths like
    "text://<slug>-<hash>" therefore stay whole file_paths instead of being
    mis-split into collection "text:" + path "/x". Plain
    "collection/relative/path.md" splits exactly as before; a path with no
    "/" returns (None, path).
    """
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[0] and ":" not in parts[0]:
        return parts[0], parts[1]
    return None, path


def _is_hidden_relpath(rel: str) -> bool:
    """True when any path segment starts with '.' (H-4: dot-FILES as well as
    dot-dirs — VCS/venv metadata like .git, but also .readme.md, is not
    documentation). Filters the DISK side only: a stored hidden row is
    reported deleted and purged on reindex like any mask mismatch (F-b)."""
    return any(seg.startswith(".") for seg in rel.replace("\\", "/").split("/") if seg)


def _resolve_row(row: object, default_chunk: int | None) -> tuple[str, str, int | None] | None:
    """Resolve (collection, file_path, chunk_index) from a search-style result row.

    One shared resolver for resolve_chunk and _track_doc_accesses (BH-7: their
    row parsing was near-duplicate). Rows may carry file_path (fts/like rows)
    or only a "collection/file_path" path key (vector rows). Returns None when
    no path info can be resolved; callers skip such rows.

    default_chunk makes the callers' deliberate chunk tolerances explicit
    (anchored in tests/test_kb_ask_row_shapes.py and tests/test_recent.py):
    resolve_chunk passes 0 (kb_ask chunk dicts always carry an int index; an
    absent or malformed chunk degrades to chunk 0), _track_doc_accesses passes
    None (the access log records a malformed chunk as "no chunk", never
    invents an index).
    """
    if not isinstance(row, dict):
        return None
    collection = row.get("collection") if isinstance(row.get("collection"), str) else None
    file_path = row.get("file_path") if isinstance(row.get("file_path"), str) else None
    if not file_path and isinstance(row.get("path"), str):
        path_collection, path_file = split_collection_path(row["path"])
        if path_collection is not None:
            collection, file_path = path_collection, path_file
        elif "/" in row["path"]:
            # scheme-bearing whole path (text://...) — no collection segment in
            # the path; keep the row's collection and take the path as file_path
            file_path = path_file
        # else: unsplit path carries no identity — unchanged, resolve to None
    if not collection or not file_path:
        return None
    chunk = row.get("chunk", row.get("chunk_index", default_chunk))
    if not isinstance(chunk, int) or isinstance(chunk, bool):
        chunk = default_chunk
    return collection, file_path, chunk


def resolve_chunk(row: object) -> dict | None:
    """Resolve a search result row into the canonical chunk dict kb_* tools expose.

    Rows may carry file_path (fts/like rows) or only a "collection/file_path"
    path key (vector rows) — the same resolution _track_doc_accesses applies
    (BH-5: kb_ask crashed with KeyError on expansion/vector rows). Returns
    None when no path info can be resolved; callers skip such rows and log.
    Strict mode (default_chunk=0): an absent or malformed chunk degrades to
    chunk 0 so the dict always carries an int index.
    """
    resolved = _resolve_row(row, default_chunk=0)
    if resolved is None or not isinstance(row, dict):
        return None
    collection, file_path, chunk = resolved
    return {
        "collection": collection,
        "file_path": file_path,
        "chunk_index": chunk,
        "title": row.get("title", ""),
        "content": row.get("content", ""),
        "score": row.get("score", 0.0),
    }


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


# DH-3 complexity rule (kb_learn): title markers win, else code-chunk ratio +
# document length pick the band. Deterministic, no LLM calls.
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_LEVEL_RANK = {"beginner": 0, "intermediate": 1, "advanced": 2}
_BEGINNER_MARKERS = (
    "intro",
    "basics",
    "beginner",
    "getting started",
    "quickstart",
    "quick start",
    "first steps",
    "overview",
    "primer",
    "fundamentals",
)
_ADVANCED_MARKERS = ("advanced", "internals", "deep dive", "deep-dive", "architecture", "optimization", "optimisation", "performance", "reference")


def _document_level(title: str, content: str) -> str:
    """Deterministic complexity level for kb_learn: beginner / intermediate / advanced.

    Grounding (the schema has no difficulty column):
    1. Title markers win — "introduction/basics/..." -> beginner,
       "internals/advanced/..." -> advanced.
    2. Otherwise score = 0.6*min(1, code_ratio/0.3) + 0.4*min(1, length/1500)
       where code_ratio is the share of the content inside ``` fences:
       score < 0.35 beginner, < 0.7 intermediate, else advanced.
    """
    t = (title or "").casefold()
    if any(m in t for m in _BEGINNER_MARKERS):
        return "beginner"
    if any(m in t for m in _ADVANCED_MARKERS):
        return "advanced"
    text = content or ""
    code_len = sum(len(m.group(0)) for m in _CODE_FENCE_RE.finditer(text))
    code_ratio = code_len / max(1, len(text))
    score = 0.6 * min(1.0, code_ratio / 0.3) + 0.4 * min(1.0, len(text) / 1500)
    if score < 0.35:
        return "beginner"
    if score < 0.7:
        return "intermediate"
    return "advanced"


def _has_level_marker(title: str, level: str) -> bool:
    """True when the title explicitly names the level (within-level ordering hint)."""
    t = (title or "").casefold()
    markers = _BEGINNER_MARKERS if level == "beginner" else _ADVANCED_MARKERS if level == "advanced" else ()
    return any(m in t for m in markers)


# kb_related compares whole documents, so the source term set is ALL unique
# document terms, capped at this many unique terms (title terms preferred first).
_RELATED_MAX_TERMS = 512


def _related_source_terms(title: str, content: str) -> list[str]:
    """Source term set for kb_related: title terms first, then ALL unique content terms.

    Earlier revisions sampled 48 terms from head/middle/tail word-stream windows,
    which left ~60% of document positions invisible (a subject at 20% or 70% of a
    200-word document did not match) and was worse than the char-offset sampler
    in the 33-40% and 66-75% bands. Positional sampling is gone: the budget is
    the full unique term set — all unique content terms (whole words,
    deduped by _query_terms) capped at _RELATED_MAX_TERMS, plus the title terms
    which always come first. A document with more than 512 unique terms keeps
    its first-512 in document order (title terms still included); 512 is the
    spec for now — frequency-based selection was deliberately not added.

    Cost: coverage/title scoring is O(terms) per candidate and negligible; the
    phrase substring scan in lexical_relevance is skipped for term sets above
    scoring._PHRASE_SCAN_MAX_TERMS, so the dominant per-call cost remains the
    full corpus scan — served from the invalidated in-memory cache, so the
    dominant remaining per-call cost is lexical scoring over the cached corpus
    (FUTURE_PLAN P1 known limitation N4).
    """
    seen: set[str] = set()
    out: list[str] = []
    for term in _query_terms(title or "", limit=_RELATED_MAX_TERMS) + _query_terms(content or "", limit=_RELATED_MAX_TERMS):
        if term not in seen:
            seen.add(term)
            out.append(term)
            if len(out) >= _RELATED_MAX_TERMS:
                break
    return out


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
        # Query expansion dictionary (configurable per instance / per call)
        self.expansion_dictionary: dict[str, list[str]] = dict(DEFAULT_EXPANSIONS)
        # Session-scoped recency of accessed collections (most recent first)
        self._recent_collections: deque[str] = deque(maxlen=_RECENT_COLLECTIONS_MAX)
        # Session-scoped document-access log for kb_recent (newest first)
        self._recent_docs: deque[dict] = deque(maxlen=_RECENT_DOCS_MAX)
        # Invalidated in-memory corpus cache for find_related/learn_topic (N4):
        # populated lazily on first use, dropped by _invalidate_corpus_cache on
        # every document-mutating path. SQLite stays the single source of truth.
        # BH-9: the rebuild region and invalidations share self._corpus_lock;
        # _corpus_epoch counts invalidations so an in-flight rebuild that raced
        # one discards its snapshot instead of caching a stale epoch. The lock
        # is reentrant so an invalidation may land mid-load on the same thread.
        self._corpus_cache: list[dict] | None = None
        self._corpus_lock = threading.RLock()
        self._corpus_epoch = 0

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @staticmethod
    def _pragma_with_retry(conn: sqlite3.Connection, pragma: str) -> None:
        """Run one connection-setup PRAGMA with a single bounded retry on a
        transient 'database is locked'/'busy' (H-2: the busy handler does not
        cover every setup statement, notably the fresh-DB WAL switch)."""
        try:
            conn.execute(pragma)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            time.sleep(0.05)
            conn.execute(pragma)

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
        # H-2 (F-5): busy_timeout FIRST — the fresh-DB WAL switch below takes an
        # exclusive lock, and parallel first-contact threads used to race it
        # before any timeout was set, surfacing spurious 'database is locked'.
        conn.execute("PRAGMA busy_timeout=5000")
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            f"PRAGMA cache_size=-{_CACHE_SIZE_KB}",
            "PRAGMA temp_store=MEMORY",
            f"PRAGMA mmap_size={_MMAP_SIZE}",
        ):
            self._pragma_with_retry(conn, pragma)
        self._local.conn = conn
        if not self._db_initialized:
            with self._init_lock:
                if not self._db_initialized:
                    self._conn = conn  # temp for _init_db
                    try:
                        self._init_db()
                        self._db_initialized = True
                    except sqlite3.Error:
                        # Nothing pending to roll back: Python's implicit
                        # transactions never wrap DDL — each CREATE/ALTER
                        # autocommits — and the DDL is idempotent (CREATE ...
                        # IF NOT EXISTS; the ALTER tolerates a duplicate
                        # column), so retrying _init_db is the safety net.
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

    def _create_imported_chunks_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS imported_chunks (
                chunk_id TEXT PRIMARY KEY,
                imported_at TEXT DEFAULT (datetime('now'))
            )
        """)

    def _create_bookmarks_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
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
        self._create_imported_chunks_table(conn)
        self._create_bookmarks_table(conn)
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
            if _is_hidden_relpath(str(f.relative_to(repo_dir))):  # H-4: no dot-paths (dirs AND files)
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
        try:
            indexed = 0
            total_chunks = 0
            for f in files:
                try:
                    total_chunks += self._index_file(conn, f, repo_dir, collection, description)
                    indexed += 1
                except OSError as e:
                    # H-1: OSError is file-specific and hits before any INSERT
                    # (read/stat), so skipping is safe — no partial rows. A
                    # sqlite3.Error is a batch-level failure and must persist
                    # nothing: it escapes to the rollback below instead of being
                    # skipped with a half-written file left behind.
                    logger.debug("Skipping %s: %s", f, e)
            conn.commit()
            return indexed, total_chunks
        except sqlite3.Error:
            conn.rollback()  # H-1: a failed index batch persists nothing
            raise
        finally:
            self._invalidate_corpus_cache()

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
            "mask": file_mask,
            "files_indexed": indexed,
            "total_chunks": total_chunks,
        }
        self._save_config(config)

        return Ok(value={"name": name, "status": _ACTION_ADDED, "files_indexed": indexed, "chunks": total_chunks})

    def add_local(
        self,
        path: str,
        collection: str | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
        mask: str | None = None,
    ) -> Ok[dict] | Err:
        """Index a local folder directly (no git) and register it in config.

        Mirrors add_repo's result shape and config entry; the entry adds
        source_type="local" and url="path://<absolute path>" so the shape
        stays uniform with github entries.
        """
        path_error = validate_local_dir(path)
        if path_error:
            return Err(error=path_error)

        local_dir = Path(path).resolve()
        name = collection or local_dir.name
        name_error = validate_collection(name)
        if name_error:
            return Err(error=f"Invalid collection name: {name_error}")

        file_mask = mask or "**/*.md"
        mask_error = validate_file_mask(file_mask)
        if mask_error:
            return Err(error=mask_error)

        files = self._collect_indexable_files(local_dir, file_mask)
        indexed, total_chunks = self._index_files(name, files, local_dir, description)

        config = self._load_config()
        config["repos"][name] = {
            "url": f"path://{local_dir}",
            "source_type": "local",
            "tags": tags or [],
            "description": description or "",
            "mask": file_mask,
            "files_indexed": indexed,
            "total_chunks": total_chunks,
        }
        self._save_config(config)

        return Ok(value={"name": name, "status": _ACTION_ADDED, "files_indexed": indexed, "chunks": total_chunks})

    def add_text(
        self,
        content: str,
        title: str | None = None,
        collection: str = "notes",
        tags: list[str] | None = None,
        description: str | None = None,
    ) -> Ok[dict] | Err:
        """Index a plain-text note directly (card P2-2) — no source folder.

        The note is chunked with the shared chunker (auto_chunk, same as
        _index_file) and lands in the documents projection, so it is searchable
        and corpus-cache tracked like any indexed file (BH-4: this write path
        invalidates the corpus cache on every exit). file_path is
        content-addressed — text://<slug from title>-<content_hash (sha256,
        first 16 hex of content)> — so re-adding identical content is an
        idempotent replace of the same document (same file_path even when the
        title differs) and different content gets a different file_path unless
        two contents collide on sha256[:16]. The replace DELETE is additionally
        scoped by content_hash, so any residual file_path collision surfaces as
        a visible UNIQUE error instead of silent loss; a replace also preserves
        created_at and retrieval_count like update_document.

        Title: an explicit title wins; otherwise the first non-empty line of
        the content (trimmed, max 80 chars); content with no non-empty line
        falls back to "note-<hash>" (FUTURE_PLAN item 10: title from first line
        or hash; the display title keeps the short hash but the file_path slug
        is then plain "note" — H-6: no hash doubling). description is stored in the documents context column, the
        same mapping _index_file uses for a source's description. tags are
        accepted for add_local/add_repo signature parity but not persisted: the
        documents schema has no tag column (tags live in source config entries;
        P3-15 Tags M2M is their future home).
        """
        content_error = validate_text_content(content)
        if content_error:
            return Err(error=content_error)
        coll_error = validate_collection(collection)
        if coll_error:
            return Err(error=f"Invalid collection name: {coll_error}")

        content_hash = self._compute_file_hash(content)
        final_title = (title or "").strip()
        hash_fallback = False
        if not final_title:
            for line in content.splitlines():
                stripped = line.strip()
                if stripped:
                    final_title = stripped[:80]
                    break
            else:
                final_title = f"note-{content_hash[:8]}"
                hash_fallback = True  # H-6: the title IS a hash — the slug must not embed it again
        slug_source = "note" if hash_fallback else final_title
        file_path = f"text://{text_slug(slug_source)}-{content_hash}"

        chunks = auto_chunk(content, file_path) or [content]
        conn = self._get_conn()
        try:
            # Content identity: a note with identical content already in this
            # collection keeps its file_path, so re-adding is a replace even
            # when the caller's title changed.
            prior = conn.execute(
                "SELECT file_path FROM documents WHERE collection = ? AND content_hash = ? "
                "AND file_path LIKE 'text://%' ORDER BY chunk_index LIMIT 1",
                (collection, content_hash),
            ).fetchone()
            if prior is not None:
                file_path = prior["file_path"]
            # F-4: a replace preserves created_at / retrieval_count across the
            # rewrite (min/max over the replaced rows, like update_document).
            old_rows = conn.execute(
                "SELECT created_at, retrieval_count FROM documents WHERE collection = ? AND file_path = ? AND content_hash = ?",
                (collection, file_path, content_hash),
            ).fetchall()
            created_at = min((r["created_at"] for r in old_rows if r["created_at"]), default=None)
            retrieval_count = max((r["retrieval_count"] or 0 for r in old_rows), default=0)
            # F-2: the delete is scoped by content_hash too — a same-path row
            # with different content is NOT replaced; the INSERT below then
            # hits the UNIQUE(collection, file_path, chunk_index) constraint and
            # surfaces it as Err instead of silently dropping the other note.
            existed = (
                conn.execute(
                    "DELETE FROM documents WHERE collection = ? AND file_path = ? AND content_hash = ?",
                    (collection, file_path, content_hash),
                ).rowcount
                > 0
            )
            for i, chunk in enumerate(chunks):
                conn.execute(
                    """INSERT INTO documents
                    (collection, file_path, content, content_hash, title, context,
                     chunk_index, total_chunks, retrieval_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                    (collection, file_path, chunk, content_hash, final_title, description or "", i, len(chunks), retrieval_count, created_at),
                )
            conn.commit()
            return Ok(
                value={
                    "collection": collection,
                    "file_path": file_path,
                    "title": final_title,
                    "status": "replaced" if existed else "added",
                    "chunks": len(chunks),
                }
            )
        except sqlite3.Error as e:
            conn.rollback()  # F-2: a failed multi-chunk replace leaves no pending rows
            logger.debug("Add text failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()

    def add_url(
        self,
        url: str,
        text: str,
        title: str | None = None,
        collection: str | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
    ) -> Ok[dict] | Err:
        """Index an extracted web page as a URL-identified document (card P2-3).

        Seam decision: this method takes ALREADY-EXTRACTED text —
        fetcher.fetch_url owns all network I/O (and its extraction), keeping
        the storage layer hermetic and the fetch policy in one module. The
        text is chunked with the shared chunker (auto_chunk in text mode —
        extension-based code chunking makes no sense for a URL identity) and
        lands in the documents projection, so it is searchable and
        corpus-cache tracked like any indexed file (BH-4: this write path
        invalidates the corpus cache on every exit).

        Identity is the URL itself (NOT content hash): file_path is the final
        normalized URL (normalize_http_url), so re-indexing the same URL
        REPLACES all its chunks even when the new content has fewer — the
        delete is scoped by (collection, file_path) only, unlike add_text's
        content_hash-scoped delete (there the hash is the identity; here the
        URL is). A replace preserves created_at and retrieval_count like
        update_document (the P2-2 F-4 lesson). Collection defaults to the
        slugified netloc (domain_slug: docs.python.org -> "docs-python-org");
        collection= overrides. Result shape matches add_text.

        Title: the extracted page title (<title> else first <h1>, produced by
        fetcher.fetch_url); without one the URL itself is the title. tags are
        accepted for add_local/add_repo/add_text signature parity but not
        persisted (the documents schema has no tag column; P3-15). description
        is stored in the documents context column like the other add_* paths.
        """
        url_error = validate_http_url(url)
        if url_error:
            return Err(error=url_error)
        text_error = validate_text_content(text)
        if text_error:
            return Err(error=text_error)
        final_url = normalize_http_url(url)
        name = collection or domain_slug(final_url)
        name_error = validate_collection(name)
        if name_error:
            return Err(error=f"Invalid collection name: {name_error}")
        final_title = (title or "").strip() or final_url
        content_hash = self._compute_file_hash(text)
        chunks = auto_chunk(text) or [text]
        conn = self._get_conn()
        try:
            # F-4: a replace preserves created_at / retrieval_count across the
            # rewrite (min/max over the replaced rows, like update_document).
            old_rows = conn.execute(
                "SELECT created_at, retrieval_count FROM documents WHERE collection = ? AND file_path = ?",
                (name, final_url),
            ).fetchall()
            created_at = min((r["created_at"] for r in old_rows if r["created_at"]), default=None)
            retrieval_count = max((r["retrieval_count"] or 0 for r in old_rows), default=0)
            # URL identity: the delete is NOT content_hash-scoped — every old
            # chunk goes, so a shrinking page leaves no stale rows.
            existed = (
                conn.execute(
                    "DELETE FROM documents WHERE collection = ? AND file_path = ?",
                    (name, final_url),
                ).rowcount
                > 0
            )
            for i, chunk in enumerate(chunks):
                conn.execute(
                    """INSERT INTO documents
                    (collection, file_path, content, content_hash, title, context,
                     chunk_index, total_chunks, retrieval_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                    (name, final_url, chunk, content_hash, final_title, description or "", i, len(chunks), retrieval_count, created_at),
                )
            conn.commit()
            return Ok(
                value={
                    "collection": name,
                    "file_path": final_url,
                    "title": final_title,
                    "status": "replaced" if existed else "added",
                    "chunks": len(chunks),
                }
            )
        except sqlite3.Error as e:
            conn.rollback()  # H-1: a failed multi-chunk replace leaves no pending rows
            logger.debug("Add url failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()

    def _resolve_collection_dir(self, collection: str) -> Path:
        """Resolve the source directory for a collection.

        Local collections (source_type == "local") resolve from their config
        entry; everything else resolves from the default clone location in
        repos_dir.
        """
        config = self._load_config()
        entry = config.get("repos", {}).get(collection)
        if entry and entry.get("source_type") == "local":
            url = entry.get("url", "")
            if url.startswith("path://"):
                return Path(url[len("path://") :])
        return self.repos_dir / collection

    def _merge_hybrid(self, results: list[dict], query: str, collections: list[str] | None, limit: int) -> list[dict]:
        like_results = self._search_like(query, collections, limit)
        seen = {r["path"] for r in results}
        for r in like_results:
            if r["path"] not in seen:
                results.append(r)
                seen.add(r["path"])
        return results

    def _apply_boosts(
        self, results: list[dict], query: str, query_terms: list[str], explain: bool, context_collections: set[str] | None = None
    ) -> list[dict]:
        """Apply lexical relevance + type/importance/context boosts; public score clamped 0..1.

        base_score is a corpus-independent lexical relevance [0,1] so the 0.3 threshold used by
        the conflict detector stays meaningful on small local indexes (where FTS5 bm25 floors at
        ~1e-6). context_collections names collections receiving the +0.1 context boost.
        """
        for r in results:
            base_score = lexical_relevance(query_terms, r.get("title", ""), r.get("content", ""), r.get("_bm25_score", None))
            boost = type_boost(query, r)
            imp_boost = importance_boost(r)
            ctx_boost = CONTEXT_BOOST if context_collections and r.get("collection") in context_collections else 0.0
            raw_score = base_score + boost + imp_boost + ctx_boost
            # Public score is clamped to 0..1 for callers; explain reports the unclamped raw score
            # so component scores still sum to final_score in explain output.
            r["_raw_score"] = raw_score
            if boost + imp_boost > 0:
                r["boost"] = boost + imp_boost
            public_score = min(1.0, raw_score)
            r[_FIELD_SCORE] = public_score
            if explain:
                r["explain"] = _build_explain(base_score, boost, imp_boost, ctx_boost, raw_score, r.get("source", _UNKNOWN))
        return results

    def _expand_query(self, query: str, expansion: dict[str, list[str]] | None = None) -> list[str]:
        """Expand a query with synonyms, stem variants, and pattern-mapped terms.

        Returns the original query terms followed by expansion additions (deduped, additions
        capped at _MAX_EXPANSION_TERMS). `expansion` extends/overrides the configurable
        instance dictionary (self.expansion_dictionary, seeded from DEFAULT_EXPANSIONS).
        """
        dictionary = {**self.expansion_dictionary, **(expansion or {})}
        q = (query or "").casefold()
        out: list[str] = []
        seen: set[str] = set()
        additions = 0

        def _add(term: str) -> None:
            nonlocal additions
            key = term.casefold()
            if not key or key in seen or additions >= _MAX_EXPANSION_TERMS:
                return
            seen.add(key)
            out.append(term)
            additions += 1

        for token in query.split():
            key = token.casefold()
            if key not in seen:
                seen.add(key)
                out.append(token)

        # Dictionary terms first (synonyms + pattern mapping), then stem variants.
        for key, terms in dictionary.items():
            if re.search(rf"(?<![\w]){re.escape(key.casefold())}(?![\w])", q):
                for term in terms:
                    _add(term)
        for token in query.split():
            for variant in sorted(_stem_variants(token)):
                _add(variant)
        return out

    def _recent_boost(self, context: list[str] | None) -> set[str] | None:
        """Collections to boost for a search: explicit context plus session recents.

        Returns None when no context parameter was given (boost opt-out for plain search).
        """
        if context is None:
            return None
        return set(context) | set(self.recent_collections())

    def _track_recent(self, results: list[dict]) -> None:
        """Record accessed collections in the session recents (most recent first)."""
        for r in results:
            coll = r.get("collection")
            if coll and (not self._recent_collections or self._recent_collections[0] != coll):
                self._recent_collections.appendleft(coll)

    def recent_collections(self) -> list[str]:
        """Collections accessed this session, most recent first."""
        return list(self._recent_collections)

    def _run_hybrid_search(
        self, query: str, collections: list[str] | None, limit: int, min_score: float, match_any: bool = False, terms: list[str] | None = None
    ) -> list[dict]:
        max_intermediate = limit * _HYBRID_MULTIPLIER
        results = self._search_fts5(query, collections, limit * 2, match_any=match_any, terms=terms)
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

    def _search_strategy(
        self,
        query: str,
        collections: list[str] | None,
        limit: int,
        strategy: str,
        min_score: float,
        match_any: bool = False,
        terms: list[str] | None = None,
    ) -> list[dict]:
        """Dispatch to the right search backend based on strategy."""
        if strategy == "vector":
            results = self._search_vector(query, limit, min_score)
            if results is not None:
                return results
            strategy = "fts"

        if strategy == "hybrid":
            return self._run_hybrid_search(query, collections, limit, min_score, match_any=match_any, terms=terms)

        return self._search_fts5(query, collections, limit, match_any=match_any, terms=terms)

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
        match_any: bool = False,
        context: list[str] | None = None,
        expand: bool = False,
    ) -> Ok[list[dict]] | Err:
        """Full-text search.

        match_any=True relaxes the default implicit-AND FTS5 semantics to an OR of query tokens.
        It is intended for internal similarity scans (e.g. conflict detection) where recall matters
        more than strict multi-term matching.

        expand=True widens recall through _expand_query (synonyms, stems, pattern mapping) with OR
        semantics over the expanded terms; scoring still ranks by the original query terms.

        context names collections to boost by CONTEXT_BOOST (+0.1). When it is given — even as an
        empty list — recently accessed collections (session recents) get the same boost. When it
        is omitted, no context boost applies (plain kb_search behaviour is unchanged).
        """
        query_error = validate_query(query)
        if query_error:
            return Err(error=query_error)
        if limit > _MAX_SEARCH_LIMIT:
            limit = _MAX_SEARCH_LIMIT
        if strategy is None:
            strategy = auto_strategy(query)

        terms = self._expand_query(query) if expand else None
        results = self._search_strategy(query, collections, limit, strategy, min_score, match_any=match_any or expand, terms=terms)

        # Pre-tokenize once; lexical_relevance uses it as the canonical query signal so that all
        # strategies (fts/like/vector) produce corpus-independent scores in [0,1].
        query_terms = _query_terms(query)
        results = self._apply_boosts(results, query, query_terms, explain, self._recent_boost(context))
        # Rank by the raw (unclamped) score so differences added by boosts are preserved,
        # while the public score field stays clamped to 0..1 for callers.
        results.sort(key=lambda x: -x.get("_raw_score", x.get(_FIELD_SCORE, 0.0)))
        if min_score > 0:
            results = [r for r in results if r.get(_FIELD_SCORE, 0) >= min_score]

        self._track_retrieval(results, limit)
        self._track_recent(results[:limit])
        self._track_doc_accesses(results[:limit])

        return Ok(value=results[:limit])

    def _row_to_result(self, row: sqlite3.Row, source: str, highlighted: str | None = None, score: float | None = None) -> dict:
        # _FIELD_SCORE is the public (clamped) score set later by _apply_boosts; here we only
        # stash a raw -rank tie-break for FTS rows (absent on LIKE/get rows).
        rank = row["rank"] if "rank" in row.keys() else None
        return {
            "path": f"{row['collection']}/{row['file_path']}",
            "file_path": row["file_path"],
            "content": row["content"][:500],
            "highlighted": highlighted if highlighted else row["content"][:200],
            "collection": row["collection"],
            "title": row["title"],
            "chunk": row["chunk_index"],
            "total_chunks": row["total_chunks"],
            _FIELD_SCORE: score if score is not None else 0.0,
            "source": source,
            "created_at": row["created_at"] if "created_at" in row.keys() else None,
            "retrieval_count": row["retrieval_count"] if "retrieval_count" in row.keys() else 0,
            "_bm25_score": (-rank if rank is not None else None),
        }

    def _increment_retrieval(self, paths: list[str]) -> None:
        if not paths:
            return
        conn = self._get_conn()
        try:
            for path in paths:
                collection, file_path = split_collection_path(path)
                if collection is not None:
                    conn.execute(
                        "UPDATE documents SET retrieval_count = retrieval_count + 1 WHERE collection = ? AND file_path = ?",
                        (collection, file_path),
                    )
                elif "/" in path:
                    # scheme-bearing whole path (e.g. text://...) — there is no
                    # collection segment to scope to; count by file_path alone
                    # (unchanged: a bare name without "/" still counts nothing)
                    conn.execute(
                        "UPDATE documents SET retrieval_count = retrieval_count + 1 WHERE file_path = ?",
                        (file_path,),
                    )
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()  # H-1: a failed retrieval-count batch persists nothing
            logger.debug("Failed to update retrieval counts: %s", e)

    def _build_fts_sql(self, collections: list[str] | None) -> tuple[str, list]:
        """Build FTS5 search SQL with optional collection filter."""
        base = """
            SELECT d.file_path, d.content, d.collection, d.title,
                   d.chunk_index, d.total_chunks, rank,
                   d.retrieval_count, d.created_at,
                   snippet(documents_fts, 1, '<b>', '</b>', '...', 20) as highlighted
            FROM documents_fts fts
            JOIN documents d ON fts.rowid = d.id
            WHERE documents_fts MATCH ?
        """
        if collections:
            placeholders = ",".join("?" * len(collections))
            return base + f" AND d.collection IN ({placeholders}) ORDER BY rank LIMIT ?", collections
        return base + " ORDER BY rank LIMIT ?", []

    def _search_fts5(
        self, query: str, collections: list[str] | None, limit: int, match_any: bool = False, terms: list[str] | None = None
    ) -> list[dict]:
        """FTS5 search.

        match_any=True builds an OR of the query tokens (broad recall — used by conflict detection);
        match_any=False (default) keeps FTS5's implicit AND across matched tokens so multi-term
        queries still require each salient term.

        terms, when given, replaces the tokenization of `query` (used by query expansion;
        multi-word terms match as quoted phrases).
        """
        conn = self._get_conn()
        try:
            matches: list[str] = []
            seen: set[str] = set()
            for raw in terms if terms is not None else query.split():
                key = raw.casefold()
                if key in seen:
                    continue
                seen.add(key)
                match = _fts_match_term(raw)
                if match:
                    matches.append(match)
            if not matches:
                return []
            joiner = " OR " if match_any else " "
            fts_query = joiner.join(matches)
            sql_suffix, extra_params = self._build_fts_sql(collections)
            params = [fts_query] + extra_params + [limit]
            rows = conn.execute(sql_suffix, params).fetchall()
            results = [self._row_to_result(r, "fts5", r["highlighted"]) for r in rows]

            # Carry bm25 as a tie-break only (raw -rank, no min-max normalization). On tiny local
            # corpora bm25 floors near 1e-6 and is not a meaningful primary signal; lexical relevance
            # computed in _apply_boosts is what makes the 0.3 threshold reachable.
            for r in results:
                rank = r.pop(_FIELD_SCORE, None)
                if rank is None:
                    r["_bm25_score"] = None
                else:
                    r["_bm25_score"] = float(-rank)

            return results
        except sqlite3.Error as e:
            logger.debug("FTS5 search failed: %s", e)
            return []

    def _build_like_sql(self, collections: list[str] | None) -> tuple[str, list]:
        """Build LIKE search SQL with optional collection filter."""
        base = """
            SELECT file_path, content, collection, title,
                   chunk_index, total_chunks,
                   retrieval_count, created_at
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
            return [self._row_to_result(r, "like", None, score=0.0) for r in rows]
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
                    self._track_recent([{"collection": row["collection"]}])
                    self._track_doc_access(row["collection"], row["file_path"], row["chunk_index"])
                    return Ok(value=dict(row))
                return Err(error=f"Document not found: {file_path}")

            rows = conn.execute(sql + " ORDER BY chunk_index", params).fetchall()
            if not rows:
                return Err(error=f"Document not found: {file_path}")
            content = "\n".join(r["content"] for r in rows)
            self._track_recent([{"collection": rows[0]["collection"]}])
            self._track_doc_access(rows[0]["collection"], rows[0]["file_path"])
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

    def _collections_for_path(self, conn: sqlite3.Connection, file_path: str) -> list[str]:
        """Collections holding file_path, for scope resolution of unscoped calls."""
        rows = conn.execute(
            "SELECT DISTINCT collection FROM documents WHERE file_path = ? ORDER BY collection",
            (file_path,),
        ).fetchall()
        return [r["collection"] for r in rows]

    def update_document(
        self,
        file_path: str,
        content: str,
        title: str | None = None,
        collection: str | None = None,
        context: str | None = None,
    ) -> Ok[dict] | Err:
        """Replace a document's content in place, scoped to a single collection.

        Scoping: when *collection* is given, only that (collection, file_path)
        document is updated. When it is omitted, the call is resolved against the
        collections holding *file_path*: exactly one match is updated, and Err is
        returned when the path is ambiguous (exists in 2+ collections) or missing.
        An update never creates a document and never touches a same-named file in
        another collection.

        Metadata preservation: fields the caller does not pass survive the update —
        title, context, created_at, retrieval_count and extension are carried over
        from the existing rows. content_hash, chunking and total_chunks are
        recomputed from the new content, and updated_at is refreshed. Passing
        *title* or *context* overrides just that field.
        """
        conn = self._get_conn()
        try:
            if collection is None:
                collections = self._collections_for_path(conn, file_path)
                if len(collections) > 1:
                    return Err(
                        error=(
                            f"collection required: '{file_path}' exists in {len(collections)} collections "
                            f"({', '.join(collections)}); pass collection to scope the update"
                        )
                    )
                if not collections:
                    return Err(error=f"Document not found: {file_path}")
                collection = collections[0]

            rows = conn.execute(
                "SELECT title, context, extension, created_at, retrieval_count FROM documents "
                "WHERE collection = ? AND file_path = ? ORDER BY chunk_index",
                (collection, file_path),
            ).fetchall()
            if not rows:
                return Err(error=f"Document not found: {collection}/{file_path}")
            first = rows[0]
            final_title = title if title is not None else first["title"]
            final_context = context if context is not None else first["context"]
            extension = first["extension"] or Path(file_path).suffix
            created_at = min((r["created_at"] for r in rows if r["created_at"]), default=None)
            retrieval_count = max(r["retrieval_count"] or 0 for r in rows)

            # Re-chunk the content and replace the scoped rows only
            chunks = auto_chunk(content)
            content_hash = self._compute_file_hash(content)

            conn.execute("DELETE FROM documents WHERE collection = ? AND file_path = ?", (collection, file_path))
            for i, chunk in enumerate(chunks):
                conn.execute(
                    """INSERT INTO documents
                    (collection, file_path, content, content_hash, extension, title, context,
                     chunk_index, total_chunks, retrieval_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (
                        collection,
                        file_path,
                        chunk,
                        content_hash,
                        extension,
                        final_title,
                        final_context,
                        i,
                        len(chunks),
                        retrieval_count,
                        created_at,
                    ),
                )
            conn.commit()
            return Ok(value={"status": "updated", "file_path": file_path, "collection": collection, "chunks": len(chunks)})
        except sqlite3.Error as e:
            conn.rollback()  # H-1: a failed replace persists nothing (F-3 repro)
            logger.debug("Update failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()

    def delete_document(self, file_path: str, collection: str | None = None) -> Ok[dict] | Err:
        """Delete a document, scoped to a single collection — never mass-deletes.

        Scoping policy (backward-compat decision): deletion is scoped — it never
        removes *file_path* from more than one collection. When *collection* is
        given, only that collection's document is removed (idempotent Ok when
        nothing matched). When it is omitted, the call is resolved against the
        collections holding *file_path*: exactly one match is deleted
        (unambiguous delete allowed), 2+ matches return Err("collection required")
        with NOTHING deleted, and a path matching nothing is an idempotent Ok
        no-op. Callers that can pass a collection (MCP kb_delete, CLI prefixed
        path) should always do so.
        """
        conn = self._get_conn()
        try:
            if collection is None:
                collections = self._collections_for_path(conn, file_path)
                if len(collections) > 1:
                    return Err(
                        error=(
                            f"collection required: '{file_path}' exists in {len(collections)} collections "
                            f"({', '.join(collections)}); pass collection to scope the delete"
                        )
                    )
                if not collections:
                    return Ok(value={"status": _ACTION_DELETED, "file_path": file_path, "collection": None})
                collection = collections[0]
            conn.execute("DELETE FROM documents WHERE file_path = ? AND collection = ?", (file_path, collection))
            conn.commit()
            return Ok(value={"status": _ACTION_DELETED, "file_path": file_path, "collection": collection})
        except sqlite3.Error as e:
            logger.debug("Delete failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()

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
        finally:
            self._invalidate_corpus_cache()

    def delete_documents_scoped(self, file_path: str, collection: str) -> Ok[dict] | Err:
        """Delete a document scoped to a specific collection."""
        return self.delete_document(file_path, collection=collection)

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
            conn.rollback()  # H-1: a failed bulk batch persists nothing
            logger.debug("Bulk insert failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()

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
            conn.rollback()  # H-1: a failed removal persists nothing (no half-deleted collection)
            logger.debug("Remove collection failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()

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
            conn.rollback()  # H-1: a failed rename persists nothing (no half-renamed collection)
            logger.debug("Rename collection failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()

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

    # ── DH-3: related / recent / learn (FUTURE_PLAN P1 #5-7) ───────────────────

    def _track_doc_access(self, collection: str, file_path: str, chunk_index: int | None = None) -> None:
        """Record a document access in the session log (newest first, no schema change)."""
        self._recent_docs.appendleft(
            {
                "collection": collection,
                "file_path": file_path,
                "chunk_index": chunk_index,
                "accessed_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }
        )

    def _track_doc_accesses(self, results: list[dict]) -> None:
        """Record accessed documents from search-style result rows.

        Rows may carry file_path (fts/like rows) or only a "collection/file_path"
        path key (vector rows) — both resolve to the same access record through
        the shared _resolve_row (BH-7) in lenient mode (default_chunk=None: a
        malformed chunk is recorded as 'no chunk'). Malformed rows (wrong
        types) are skipped so the search() hot path never raises on logging.
        """
        for r in results:
            resolved = _resolve_row(r, default_chunk=None)
            if resolved is None:
                continue
            collection, file_path, chunk = resolved
            self._track_doc_access(collection, file_path, chunk)

    def _load_documents(self, collection: str | None = None) -> list[dict]:
        """All documents aggregated per (collection, file_path) with full content.

        chunk_index is the lowest chunk of the document; chunk count is kept in
        `chunks`. Ordering follows first insertion so results stay deterministic.
        A row with chunk_index NULL (reachable only via raw SQL) is tolerated:
        the aggregate stays None until an integer chunk is seen (M9).

        find_related/learn_topic consume this via _cached_documents, not
        directly: a per-call load here is O(corpus) time and allocation (N4).
        """
        conn = self._get_conn()
        sql = "SELECT collection, file_path, title, content, chunk_index FROM documents"
        params: list = []
        if collection is not None:
            sql += " WHERE collection = ?"
            params.append(collection)
        sql += " ORDER BY id"
        docs: dict[tuple[str, str], dict] = {}
        for r in conn.execute(sql, params).fetchall():
            key = (r["collection"], r["file_path"])
            # Malformed rows (chunk_index NULL via raw SQL) degrade gracefully: a
            # NULL chunk stays None until an integer chunk is seen — never raises.
            chunk_index = r["chunk_index"] if isinstance(r["chunk_index"], int) else None
            entry = docs.setdefault(
                key,
                {
                    "collection": r["collection"],
                    "file_path": r["file_path"],
                    "title": r["title"] or "",
                    "contents": [],
                    "chunk_index": chunk_index,
                },
            )
            entry["contents"].append(r["content"])
            if chunk_index is not None and (entry["chunk_index"] is None or chunk_index < entry["chunk_index"]):
                entry["chunk_index"] = chunk_index
        out = []
        for entry in docs.values():
            entry["content"] = "\n".join(entry["contents"])
            del entry["contents"]
            out.append(entry)
        return out

    def _invalidate_corpus_cache(self) -> None:
        """Drop the in-memory corpus cache; the next read rebuilds from SQLite.

        Called from every document-mutating method (insert/index, add_text,
        add_url, update, delete, bulk insert, remove/rename collection,
        reindex) on all
        exits — success, error and early return alike — so a partial mutation
        can never be served stale. Deliberately NOT called for retrieval_count
        updates, judgments, contexts, import markers and bookmarks: they do not
        change the _load_documents projection (bookmarks live in their own
        table and carry no content), and retrieval counting runs inside
        search (_track_retrieval). Invalidation is instance-scoped: a second Storage
        over the same data dir does not see this instance's invalidations
        (documented limitation, FUTURE_PLAN N4). Rebuilds are lock-guarded
        (BH-9): invalidation and the lazy rebuild region share _corpus_lock and
        every invalidation bumps _corpus_epoch, so an invalidation racing a
        rebuild always wins — the in-flight snapshot is discarded, never
        cached as a stale epoch.
        """
        with self._corpus_lock:
            self._corpus_epoch += 1
            self._corpus_cache = None

    def _cached_documents(self, collection: str | None = None) -> list[dict]:
        """Corpus for find_related/learn_topic via the invalidated in-memory cache.

        Same data shape as _load_documents: the full corpus is loaded once and
        cached; a collection argument filters the cached copy in memory. The
        cache is dropped on every document-mutating path, so SQLite remains the
        single source of truth and this is a pure read accelerator. Cached
        entries are shared across calls — callers must treat them as read-only.
        Resident memory is O(corpus), accepted for local libraries (N4).
        Rebuilds are lock-guarded (BH-9): the rebuild region runs under
        _corpus_lock and is retried while invalidations keep landing — a
        snapshot loaded across an invalidation is dropped, so readers never
        observe a stale epoch.
        """
        cache = self._corpus_cache
        if cache is None:
            with self._corpus_lock:
                cache = self._corpus_cache
                while cache is None:
                    epoch = self._corpus_epoch
                    loaded = self._load_documents()
                    if self._corpus_epoch == epoch:
                        self._corpus_cache = loaded
                        cache = loaded
        if collection is not None:
            return [doc for doc in cache if doc["collection"] == collection]
        return cache

    def find_related(
        self, collection: str, file_path: str, chunk_index: int | None = None, limit: int = 5, *, explain: bool = False
    ) -> Ok[dict] | Err:
        """Documents related to a stored document (FUTURE_PLAN P1 #5).

        Score = 0.4*tag_overlap + 0.2*same_collection + 0.4*content_similarity.

        Grounding (the schema has no tag column): "tag overlap" is title-term
        overlap — the share of the source title's terms found in the candidate
        title — and is marked tag_overlap_source="title_terms" in explain.
        Content similarity reuses scoring.lexical_relevance over the source
        document's full unique term set (see _related_source_terms); the phrase
        substring scan inside lexical_relevance is skipped for that large term
        set (scoring._PHRASE_SCAN_MAX_TERMS). No embeddings, no new
        dependencies. Candidates are aggregated per document; their reported
        chunk_index is the first chunk. Related is the top `limit` (default 5)
        candidates by score — the result does NOT guarantee cross-collection
        visibility: a same-collection candidate can fill all slots. A
        successful lookup records a session access for kb_recent.
        """
        collection_error = validate_collection(collection)
        if collection_error:
            return Err(error=collection_error)
        limit_error = validate_limit(limit)
        if limit_error:
            return Err(error=limit_error)
        if not file_path:
            return Err(error="File path cannot be empty")
        limit = min(limit, _MAX_SEARCH_LIMIT)
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT title, content, chunk_index FROM documents WHERE collection = ? AND file_path = ? ORDER BY chunk_index",
                (collection, file_path),
            ).fetchall()
            if not rows:
                return Err(error=f"Document not found: {collection}/{file_path}")
            if chunk_index is not None:
                matched = [r for r in rows if r["chunk_index"] == chunk_index]
                if not matched:
                    return Err(error=f"Chunk not found: {collection}/{file_path}#{chunk_index}")
                source_content = matched[0]["content"]
            else:
                source_content = "\n".join(r["content"] for r in rows)
            source_title = rows[0]["title"] or ""

            source_terms = _related_source_terms(source_title, source_content)
            source_title_terms = _token_set(source_title)
            related = []
            for cand in self._cached_documents():
                if cand["collection"] == collection and cand["file_path"] == file_path:
                    continue
                cand_title_terms = _token_set(cand["title"])
                tag_overlap = len(source_title_terms & cand_title_terms) / len(source_title_terms) if source_title_terms else 0.0
                same_collection = cand["collection"] == collection
                content_similarity = lexical_relevance(source_terms, cand["title"], cand["content"])
                if tag_overlap == 0.0 and content_similarity == 0.0:
                    continue  # no lexical signal: same_collection alone is not a relation
                score = (
                    _RELATED_W_TAG * tag_overlap + _RELATED_W_COLLECTION * (1.0 if same_collection else 0.0) + _RELATED_W_CONTENT * content_similarity
                )
                item = {
                    "collection": cand["collection"],
                    "file_path": cand["file_path"],
                    "chunk_index": cand["chunk_index"],
                    "title": cand["title"],
                    "score": round(score, 4),
                    "relation": {
                        "tag_overlap": round(tag_overlap, 4),
                        "same_collection": same_collection,
                        "content_similarity": round(content_similarity, 4),
                    },
                }
                if explain:
                    item["explain"] = {
                        "tag_overlap": round(tag_overlap, 4),
                        "same_collection": same_collection,
                        "content_similarity": round(content_similarity, 4),
                        "final_score": round(score, 4),
                        "tag_overlap_source": _TAG_OVERLAP_SOURCE,
                        "weights": {
                            "tag_overlap": _RELATED_W_TAG,
                            "same_collection": _RELATED_W_COLLECTION,
                            "content_similarity": _RELATED_W_CONTENT,
                        },
                    }
                related.append(item)
            related.sort(key=lambda i: (-i["score"], i["collection"], i["file_path"]))
        except sqlite3.Error as e:
            logger.debug("Find related failed: %s", e)
            return Err(error=str(e))

        self._track_doc_access(collection, file_path, chunk_index)
        source = {
            "collection": collection,
            "file_path": file_path,
            "chunk_index": chunk_index if chunk_index is not None else rows[0]["chunk_index"],
            "title": source_title,
            "chunks": len(rows),
        }
        return Ok(value={"source": source, "related": related[:limit]})

    def get_recent(self, collection: str | None = None, since: str | None = None, limit: int = 10) -> Ok[list[dict]] | Err:
        """Recently accessed documents, most recent first (FUTURE_PLAN P1 #6).

        Grounding (no schema migration): access recency comes from the
        per-Storage in-memory session access log (populated by search/get/
        find_related, like DH-2's session recents but per document). Documents
        absent from the log fill remaining slots from persistent signals —
        retrieval_count DESC, updated_at DESC (retrieval_count > 0 only) — and
        report accessed_at=None. For log entries `since` compares against the
        session access time; for fallback rows against updated_at. This method
        records nothing: querying recent activity is not an access, and a
        kb_learn topic query is not a viewing either — only searches, document
        fetches and related lookups log accesses.
        """
        if collection is not None:
            collection_error = validate_collection(collection)
            if collection_error:
                return Err(error=collection_error)
        since_dt = None
        if since is not None:
            since_error = validate_since(since)
            if since_error:
                return Err(error=since_error)
            since_dt = parse_since(since)
        limit_error = validate_limit(limit)
        if limit_error:
            return Err(error=limit_error)
        limit = min(limit, _MAX_SEARCH_LIMIT)
        try:
            conn = self._get_conn()
            out: list[dict] = []
            seen: set[tuple[str, str]] = set()

            def _row_dict(row: sqlite3.Row, accessed_at: str | None, chunk_index: int | None) -> dict:
                return {
                    "collection": row["collection"],
                    "file_path": row["file_path"],
                    "chunk_index": chunk_index if chunk_index is not None else row["chunk_index"],
                    "title": row["title"],
                    "accessed_at": accessed_at,
                    "retrieval_count": row["retrieval_count"] or 0,
                    "updated_at": row["updated_at"],
                }

            def _doc_row(coll: str, path: str) -> sqlite3.Row | None:
                return conn.execute(
                    "SELECT collection, file_path, title, chunk_index, retrieval_count, updated_at "
                    "FROM documents WHERE collection = ? AND file_path = ? ORDER BY chunk_index LIMIT 1",
                    (coll, path),
                ).fetchone()

            # 1) Session access log (newest first) — the primary signal.
            for rec in self._recent_docs:
                key = (rec["collection"], rec["file_path"])
                if key in seen or (collection is not None and rec["collection"] != collection):
                    continue
                if since_dt is not None and rec["accessed_at"] < since_dt:
                    continue
                row = _doc_row(*key)
                if row is None:
                    continue  # deleted since access
                seen.add(key)
                out.append(_row_dict(row, rec["accessed_at"].strftime("%Y-%m-%d %H:%M:%S"), rec["chunk_index"]))
                if len(out) >= limit:
                    return Ok(value=out)

            # 2) Documented fallback: retrieval_count DESC, updated_at DESC.
            sql = (
                "SELECT collection, file_path, MAX(title) AS title, MIN(chunk_index) AS chunk_index, "
                "MAX(retrieval_count) AS retrieval_count, MAX(updated_at) AS updated_at "
                "FROM documents WHERE retrieval_count > 0"
            )
            params: list = []
            if collection is not None:
                sql += " AND collection = ?"
                params.append(collection)
            sql += " GROUP BY collection, file_path ORDER BY retrieval_count DESC, updated_at DESC, collection, file_path"
            for fallback_row in conn.execute(sql, params).fetchall():
                if len(out) >= limit:
                    break
                key = (fallback_row["collection"], fallback_row["file_path"])
                if key in seen:
                    continue
                updated = parse_since(fallback_row["updated_at"] or "")
                if since_dt is not None and (updated is None or updated < since_dt):
                    continue
                seen.add(key)
                out.append(_row_dict(fallback_row, None, fallback_row["chunk_index"]))
        except sqlite3.Error as e:
            logger.debug("Get recent failed: %s", e)
            return Err(error=str(e))
        return Ok(value=out)

    def learn_topic(self, topic: str, collection: str | None = None, limit: int = 10) -> Ok[dict] | Err:
        """Structured topic exploration (FUTURE_PLAN P1 #7).

        Pure retrieval + deterministic grouping (no LLM calls — the host can
        summarize the result later via kb_ask). Documents are gated by lexical
        relevance to the topic terms (score > 0), then grouped into collections,
        key documents and a beginner -> advanced learning path. The complexity
        band comes from _document_level (title markers, else code-chunk ratio +
        length). Within a level: higher relevance first, then documents whose
        title names the level, then file_path for determinism. This method
        records nothing: a topic query is not a viewing — only searches,
        document fetches and related lookups log accesses.
        """
        if collection is not None:
            collection_error = validate_collection(collection)
            if collection_error:
                return Err(error=collection_error)
        topic_error = validate_topic(topic)
        if topic_error:
            return Err(error=topic_error)
        limit_error = validate_limit(limit)
        if limit_error:
            return Err(error=limit_error)
        terms = _query_terms(topic)
        if not terms:
            return Err(error="Topic has no searchable keywords")
        limit = min(limit, _MAX_SEARCH_LIMIT)
        try:
            docs = self._cached_documents(collection)
            coll_totals: dict[str, int] = {}
            for doc in docs:
                coll_totals[doc["collection"]] = coll_totals.get(doc["collection"], 0) + 1

            matched: list[dict] = []
            coll_stats: dict[str, dict] = {}
            for doc in docs:
                score = lexical_relevance(terms, doc["title"], doc["content"])
                if score <= 0:
                    continue
                level = _document_level(doc["title"], doc["content"])
                matched.append(
                    {
                        "collection": doc["collection"],
                        "file_path": doc["file_path"],
                        "title": doc["title"],
                        "level": level,
                        "score": round(score, 4),
                    }
                )
                stats = coll_stats.setdefault(
                    doc["collection"], {"collection": doc["collection"], "documents": 0, "matched_documents": 0, "score": 0.0}
                )
                stats["matched_documents"] += 1
                stats["score"] += score
            for stats in coll_stats.values():
                stats["documents"] = coll_totals[stats["collection"]]
                stats["score"] = round(stats["score"], 4)

            matched.sort(key=lambda d: (_LEVEL_RANK[d["level"]], 0 if _has_level_marker(d["title"], d["level"]) else 1, -d["score"], d["file_path"]))
            key_documents = matched[:limit]

            why_templates = {
                "beginner": "Start here: covers the basics of {topic}",
                "intermediate": "Builds on the fundamentals of {topic}",
                "advanced": "Deep dive into {topic}",
            }
            learning_path = [
                {
                    "step": i,
                    "document": f"{d['collection']}/{d['file_path']}",
                    "level": d["level"],
                    "why": why_templates[d["level"]].format(topic=topic),
                }
                for i, d in enumerate(key_documents, start=1)
            ]
        except sqlite3.Error as e:
            logger.debug("Learn topic failed: %s", e)
            return Err(error=str(e))

        return Ok(
            value={
                "topic": topic,
                "collections": sorted(coll_stats.values(), key=lambda c: (-c["score"], c["collection"])),
                "key_documents": key_documents,
                "learning_path": learning_path,
            }
        )

    def chunk_imported(self, chunk_id: str) -> bool:
        """Return True if chunk_id was already imported into this storage."""
        conn = self._get_conn()
        try:
            return (
                conn.execute(
                    "SELECT 1 FROM imported_chunks WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchone()
                is not None
            )
        except sqlite3.Error:
            return False

    def mark_chunk_imported(self, chunk_id: str) -> None:
        """Record that chunk_id was imported into this storage."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO imported_chunks (chunk_id) VALUES (?)",
                (chunk_id,),
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.debug("Failed to mark chunk imported: %s", e)

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
            # H-3: compare the same representation both sides — the stored
            # content_hash is the 16-char sha256 prefix (_compute_file_hash),
            # so the current hash is truncated too. Comparing a full 64-char
            # digest against the stored prefix reported content_changed for
            # ANY file that existed.
            current_hash = hashlib.sha256(file_path.read_text(errors="ignore").encode()).hexdigest()[:16]
            if current_hash != stored_hash[:16]:
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
            repo_dir = self._resolve_collection_dir(collection)
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

    # ── Bookmarks (card P2-2) ────────────────────────────────────────────────

    def bookmark_upsert(self, url: str, title: str | None = None, note: str | None = None) -> Ok[dict] | Err:
        """Create or update a bookmark keyed by UNIQUE(url) (card P2-2).

        A repeat save updates title/note/updated_at and preserves created_at;
        title=None / note=None leave the stored value untouched (the upsert IS
        the edit — pass "" to clear a field). The id doubles as a recency
        sequence: every save allocates the next id (the update bumps it), so
        bookmark_list's "most recent first" order is exact even within one wall
        clock tick (~16ms granularity on Windows); the column defaults match
        the approved DDL.

        Deliberately OUTSIDE the documents projection: no corpus-cache
        invalidation here — bookmarks carry no content and never enter search.
        """
        url = url.strip()  # normalize the UNIQUE(url) key (F-6)
        # validate the raw form FIRST: validate_http_url rejects dangerous
        # characters before any URL parsing (urllib silently strips newlines,
        # so normalizing first would launder them past the check)
        url_error = validate_http_url(url)
        if url_error:
            return Err(error=url_error)
        url = normalize_http_url(url)  # H-5: fold scheme+host case (RFC 3986) — one link, one row
        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            existing = conn.execute("SELECT id FROM bookmarks WHERE url = ?", (url,)).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO bookmarks (url, title, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (url, title or "", note or "", now, now),
                )
                created = True
            else:
                # Bump id to the next sequence value: id carries write
                # recency so "most recent first" breaks timestamp ties exactly.
                conn.execute(
                    "UPDATE bookmarks SET title = COALESCE(?, title), note = COALESCE(?, note), updated_at = ?, "
                    "id = (SELECT MAX(id) FROM bookmarks) + 1 WHERE url = ?",
                    (title, note, now, url),
                )
                created = False
            conn.commit()
            return Ok(value={"url": url, "created": created})
        except sqlite3.Error as e:
            logger.debug("Bookmark upsert failed: %s", e)
            return Err(error=str(e))

    def bookmark_list(self) -> Ok[list[dict]] | Err:
        """All bookmarks, most recent first (updated_at DESC).

        id DESC breaks ties exactly — id is a recency sequence (every save
        allocates the next id). Returns dicts shaped
        {url, title, note, created_at, updated_at}. No corpus-cache hooks —
        bookmarks are outside the documents projection.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT url, title, note, created_at, updated_at FROM bookmarks ORDER BY updated_at DESC, id DESC").fetchall()
            return Ok(value=[dict(r) for r in rows])
        except sqlite3.Error as e:
            logger.debug("Bookmark list failed: %s", e)
            return Err(error=str(e))

    # ── Incremental Embedding ────────────────────────────────────────────────

    def find_changed_docs(self, collection: str, repo_dir: Path | None = None) -> list[dict]:
        """Find documents whose content_hash differs from the repo file.

        Returns list of {file_path, old_hash, new_hash, status} dicts.
        Status: 'changed', 'deleted', 'added'.

        H-4: the file collection is the stored mask of the collection's config
        entry; a missing "mask" key is a legacy (pre-H-4) entry and keeps the
        pre-card **/* detection — new entries always persist their mask
        (add_repo/add_local write the key unconditionally). Dot-path files are
        never collected from disk: the rule covers dot-FILES as well as
        dot-dirs (any segment starting with '.', e.g. .readme.md, or a .docs/
        dir — even an explicit .docs/*.md mask indexes 0 files). F-b: a stored
        dot-path row is reported deleted and purged on reindex — the same
        treatment as a mask-mismatched row (symmetric, no frozen orphans).
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT file_path, content_hash FROM documents WHERE collection = ? AND chunk_index = 0",
                (collection,),
            ).fetchall()

            if repo_dir is None:
                repo_dir = self._resolve_collection_dir(collection)

            entry = self._load_config().get("repos", {}).get(collection) or {}
            mask = entry.get("mask") or "**/*"  # missing key = legacy entry = pre-card glob
            # F-b: hidden rows stay in `stored` — a stored dot-path row is a
            # pre-card leftover and must surface as deleted (then get purged),
            # exactly like a mask-mismatched row. The hidden filter lives on
            # the disk side only.
            stored = {r["file_path"]: r["content_hash"] for r in rows}
            current = (
                {
                    str(f.relative_to(repo_dir)): self._compute_file_hash(f.read_text(errors="ignore"))
                    for f in repo_dir.glob(mask)
                    if f.is_file() and not f.is_symlink() and not _is_hidden_relpath(str(f.relative_to(repo_dir)))
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

        repo_dir = self._resolve_collection_dir(collection)
        if not repo_dir.exists():
            return Err(error=f"Repository not found: {collection}")

        # No hidden filter here (F-b): find_changed_docs reports hidden stored
        # rows as deleted on purpose, so pre-card .git junk gets purged.
        changed = self.find_changed_docs(collection, repo_dir)
        if not changed:
            return Ok(value=0)

        conn = self._get_conn()
        try:
            # Delete old chunks for changed files
            for item in changed:
                conn.execute("DELETE FROM documents WHERE collection = ? AND file_path = ?", (collection, item["file_path"]))

            # Re-index changed files — ONE transaction with the deletes (H-1):
            # the old two-commit window persisted the delete phase first, so a
            # failed re-insert left the document half-deleted. Any failure now
            # rolls back both phases; only a vanished source keeps its delete
            # (the file is gone — that IS the deletion).
            updated = 0
            for item in changed:
                fp = item["file_path"]
                if item["status"] == _ACTION_DELETED:
                    updated += 1
                    continue
                full = repo_dir / fp
                if not full.exists() or full.is_symlink():
                    continue
                self._index_file(conn, full, repo_dir, collection, None)
                updated += 1
            conn.commit()
            return Ok(value=updated)
        except (sqlite3.Error, OSError) as e:
            conn.rollback()  # H-1: a failed reindex persists nothing — no half-deleted docs
            logger.debug("Reindex failed: %s", e)
            return Err(error=str(e))
        finally:
            self._invalidate_corpus_cache()


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
