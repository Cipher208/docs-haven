# Changelog

All notable changes to DocsHaven will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-07-17

### Added
- **Result type (Ok/Err)** — all Storage methods return `Ok[dict] | Err` or `Ok[list] | Err`
- **Score explanation** — `search(explain=True)` returns scoring breakdown (base_score, type_boost, source)
- **Domain field** — `list_collections` returns URI domain for each collection
- **8 ADRs** — architectural decision records in `docs/decisions/`
- **CLI commands** — `list`, `delete`, `serve` added to CLI
- **Graceful shutdown** — `atexit` handler closes Storage connection on exit
- **Connection liveness** — `_get_conn` checks connection health before returning
- **Edge case tests** — empty query, SQL injection, limit=0, auto_strategy boundary
- **URIRouter tests** — 6 tests for resolve, search, wildcard, list_by_domain, list_all_domains
- **conftest.py** — shared test fixtures and `insert_doc` helper

### Fixed
- **FTS5 operator injection** — query tokenization prevents FTS5 special char exploitation
- **URL validation** — rejects `file://`, `ext::`, `git://` schemes before `subprocess.run`
- **HTTP server default bind** — changed from `0.0.0.0` to `127.0.0.1` (security)
- **Atomic config write** — `tmp+rename` pattern prevents corruption on crash
- **Benchmark connection leak** — removed `conn.close()` that killed Storage persistent connection
- **ConflictDetector second Storage** — now receives Storage from server.py
- **conflict_judgments table** — `judge()` now persists to SQLite
- **FTS5 wildcard** — `core://*` works via OR of collection names
- **--http parsing** — `ValueError`/`IndexError` protection, defaults to port 8000
- **Sync import dedup** — chunk ID tracking prevents re-importing duplicates
- **Path traversal** — `add_repo` rejects `..` in file_mask, `kb_get` rejects `..` and absolute paths
- **LIKE ESCAPE** — `_search_like` uses `ESCAPE '\'` for underscore wildcard safety
- **_load_config** — handles broken JSON gracefully (returns defaults)
- **empty URI path** — `core:///` raises ValueError
- **created_by empty string** — defaults to "unknown"
- **CLI KeyError** — `r.get('content', '')` prevents missing key error
- **Git clone cleanup** — `add_repo` removes partial clone on failure
- **SHA-256 prefix** — increased from 8 to 16 chars (64 bits collision safety)
- **README** — corrected tool count (16), dependency claim ("minimal"), comparison table
- **GitHub Actions** — pinned all actions to SHA, added timeouts, added `persist-credentials: false`

### Removed
- `config.py` — dead code (Config class unused by Storage/Server)
- `AUDIT_2026-07-16.md` — outdated report
- `FIX_REPORT_2026-07-16.md` — outdated report

## [0.3.0] - 2026-07-16

### Added
- 18 MCP tools (added `kb_update`, `kb_delete`)
- Conflict detection when adding documents
- Git sync with compressed chunks

## [0.1.0] - 2026-07-16

### Added
- SQLite FTS5 search engine (replaces QMD dependency)
- BM25 full-text search with LIKE fallback
- Auto strategy selection (fts/hybrid by query length)
- Document chunking for better search precision
- Type-aware result boosting
- URI routing (core://, ref://, guide://, lib://, src://, test://, note://)
- Git sync with compressed chunks (no merge conflicts)
- MCP server with 15 tools
- MIT license
- pytest test suite

### Changed
- Storage backend: QMD → SQLite FTS5 (zero external dependencies)
- Search: FTS5 + LIKE fallback (pattern from mcp-ariel-memory)
- Conflicts: now uses Storage.search() instead of QMD CLI
