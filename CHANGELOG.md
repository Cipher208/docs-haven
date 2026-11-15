# Changelog

All notable changes to DocsHaven will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **URL/web page indexing (P2-3)** — `kb_index` gains a URL mode: any http(s) URL that is not a repo-shaped git URL is fetched and indexed as a web page (`type="url"`, alias `"web"`, forces it). New stdlib-only `fetcher.py` (`urllib.request` + `html.parser`, zero new dependencies by design) extracts the visible text — title from `<title>` else first `<h1>`, `<script>`/`<style>`/`<noscript>`/`<nav>`/`<footer>`/`<header>`/`<aside>` stripped entirely, block-level newlines, `<pre>` preserved — with custom redirect handling (max 3 hops, every hop's scheme re-validated: no downgrade to file://), HTML-only content types, charset from header (utf-8 fallback), a 10MB cap (existing precedent) and a 10s timeout. `Storage.add_url` indexes the extracted text through the shared chunker with URL-keyed identity: the file path is the final normalized post-redirect URL, so re-indexing the same URL replaces ALL its chunks (no stale rows when a page shrinks) while preserving created_at and retrieval_count (P2-2 F-4 semantics), on the corpus-cache-hooked write path (BH-4). The collection defaults to the slugified domain (`docs.python.org` → `docs-python-org`), `collection=` overrides. MCP tools stay 32 (URL mode is a kb_index mode, no new tool); tests 532 → 571 (+39 anchors). Residual SSRF surface documented honestly in docs/api/mcp-tools.md (a web fetch reaches anything the host can reach — local-first tool by design; the git-clone domain allowlist from the 59-issue review still gates clones); robots.txt is deliberately not consulted.

- **Plain text note indexing (P2-2)** — `Storage.add_text` indexes pasted text as a searchable document (shared chunker, corpus-cache-hooked write path — BH-4); `kb_index` gains a text mode (`text://<title>` or `type="text"`, `content=` required; `content=` on file sources errors). The file path is content-addressed (`text://<slug>-<sha256[:16] of content>`), so identical content re-added is an idempotent replace (created_at / retrieval_count preserved) and different content gets a different path unless two contents collide on sha256[:16] (the replace delete is content_hash-scoped, so a residual collision surfaces as a visible UNIQUE error). Title: explicit → first content line (80 chars) → `note-<hash>`.
- **Bookmarks (P2-2)** — new owner-approved `bookmarks` table (outside the documents projection: no content indexing, no corpus-cache hooks) with MCP tools `kb_bookmark` (upsert keyed by URL — the upsert IS the edit; `created_at` preserved) and `kb_bookmarks` (list, most recent first). http(s)-only URLs validated by `validate_http_url`. MCP tools 30 → 32; tests 449 → 508 (+59 anchors).
- **Local folder indexing + `kb_index` unified entry (P2-1)** — `Storage.add_local` indexes a local folder without git (default mask `**/*.md`), registering `source_type: "local"` and `url: "path://<absolute path>"` in config; incremental reindex and stale checks resolve local dirs from config. New MCP tool `kb_index` auto-detects local vs GitHub sources (`type` overrides detection) and delegates GitHub sources to the existing `kb_add_repo` flow. MCP tools 29 → 30; tests 424 → 449 (+25 anchors).

### Changed
- **kb_index routing** — auto-detection now routes a plain http(s) URL to web fetch (was: "Unsupported source") and deeper git-host URLs (e.g. `github.com/owner/repo/blob/main/README.md`) to web fetch instead of clone; repo-shaped URLs (`github.com/<owner>/<repo>` with at most 2 path segments, `github://` scheme, `git@host:owner/repo.git`) keep the P2-1 clone route unchanged.
- **Docs** — documentation synced with measured reality: MCP reference covered all 29 tools of that sync (32 as of the P2-1/P2-2 entries above), CLI reference matches all 13 commands, and stale counts (tests, coverage, benchmarks) were re-measured and corrected.

### Removed
- **alias.py** — never wired, dead since v0.8.0; hot MCP parameters now accept common argument aliases natively via pydantic AliasChoices.

### Fixed
- **Hygiene wave (P2-H)** — six holes from the P2-1/P2-2 blind-review ledgers closed. H-1: transactional rollback for every multi-statement write — a failed update/bulk/remove/rename/reindex/index batch now persists nothing (`conn.rollback()` in `except`; `reindex_collection` lost its two-commit window and `_index_files` no longer skips DB errors into a half-written file), so a later commit on the same connection can no longer persist a failed call's partial rows. H-2: cold-start concurrency — `busy_timeout` is set before any other PRAGMA and setup PRAGMAs retry once on transient lock, so parallel first writes on a fresh `Storage` no longer surface `database is locked`. H-3: `check_stale` compares the 16-char hash representation on both sides (was full-sha256 vs stored prefix → `content_changed` for any file). H-4: `find_changed_docs` is mask-aware — new config entries persist their `mask`, legacy entries without the key keep the pre-card `**/*` detection (no migration: nothing changes for them until re-added), and a mask-mismatched row is deleted on reindex. The dot-path exclusion reaches existing indexes too — stored dot-path rows are purged on reindex, the same treatment as mask mismatches — and it is broad by design: it excludes dot-FILES as well as dot-directories (`.readme.md`; even an explicit `.docs/*.md` mask indexes 0 files). H-5: bookmark URLs fold scheme+host case per RFC 3986 — `https://dup.com/a` and `HTTPS://DUP.COM/a` upsert into one row. H-6: a whitespace-only note's `file_path` no longer doubles the hash (`text://note-<hash16>`; display title stays `note-<hash8>`). Tests 508 → 532 (+24 anchors — 22 wave anchors, fail-before at the pre-card HEAD for 16 and guards for 6 that cannot fail before: H-3 `test_modified_file_is_content_changed` and `test_deleted_file_is_file_deleted` (×2 kinds each — the over-report masked both outcomes), H-4 `test_changed_file_under_mask_still_detected`, H-5 `test_path_and_query_case_preserved`; plus 2 fix-round regression anchors F-a/F-b, fail-before at the wave state; 3 existing assertions updated to follow the spec — recorded in FUTURE_PLAN).

## [0.9.0] - 2026-07-23

### Added
- **Importance scoring** — multi-signal search ranking with recency (exponential decay, 90-day half-life) and retrieval frequency (log-scale). New `retrieval_count` column tracks how often documents are retrieved.
- **PyPI publish workflow** — GitHub Actions auto-publishes to PyPI on `v*` tag push (trusted publishing).

### Changed
- **Search scoring** — now combines BM25 rank + type_boost + importance_boost (recency + frequency)
- **Explain output** — includes `importance_boost` breakdown alongside `type_boost`
- **Tests** — 275 → 279 (+4 importance scoring tests)

## [0.8.0] - 2026-07-23

### Fixed
- **chunk_text guard** — chunk_size=0 no longer causes infinite loop (M1)
- **config save** — atomic write failure logged instead of raised (M2)
- **hybrid search cap** — intermediate results properly sorted and capped (M6)
- **list_contexts** — long SQL query broken into multi-line (M10)
- **_init_db** — return type annotation added (M12)
- **FTS5 sanitization** — query tokens stripped of special characters (L7)
- **file mask validation** — dangerous characters blocked (L12)
- **config size limit** — 1MB max prevents memory exhaustion (L17)
- **_unwrap** — non-Result objects handled gracefully (M4)
- **kb_check_imports** — repo_root path validated (L8)
- **kb_update** — 10MB content limit enforced (L16)
- **CLI delete** — path traversal rejected (L14)
- **CLI import** — file existence and .json suffix validated (L9)
- **CLI search** — limit clamped to [1, 1000] (L15)
- **sync import** — collection names validated against injection (L11)
- **sync import** — 50MB uncompressed size guard (L10)
- **sync import** — deprecated time.gmtime replaced (L22)
- **vector search** — batch processing for large collections (M5)
- **vector search** — 50k doc auto-build guard (L23)
- **apply_template** — add_context errors now logged (M3)
- **_unwrap type** — Any annotation added (M11)
- **benchmark** — Path.stem instead of string split (M14)
- **benchmark** — misleading query count fixed (L3)
- **alias** — fragile "id" → "file_path" removed (L1)
- **import_guard** — stdlib modules no longer flagged as phantom (L5)
- **URI parse** — path traversal rejected (L13)

### Changed
- **Coverage** — 88% → 91%
- **Tests** — 275 (existing suite updated)

## [0.7.0] - 2026-07-22

### Added
- **Context attachments** — human-written summaries for collections (table + CRUD)
- **MCP tools** — `kb_context_add`, `kb_context_list`, `kb_context_rm`
- **CLI context management** — `context add/list/rm`
- **Export/Import CLI** — `export --format json/csv/md`, `import backup.json`
- **Storage.list_documents()** — list all documents (chunk_index=0)
- **Collection rename** — `collection rename old new` (CLI + MCP)
- **Auto chunking** — detects file type, uses code chunking for .py/.js/.ts etc
- **Incremental embedding** — `find_changed_docs()`, `reindex_collection()`
- **17 new tests** — context, export/import, rename, auto-chunk, error paths

### Changed
- **Storage refactor** — 945 lines, extracted helpers, reduced nesting
- **CLI** — full parity with QMD: list/show/remove/rename
- **Coverage** — 84% → 88%
- **Tests** — 138 → 191 (+53)

### Fixed
- **mypy errors** — union-attr and arg-type errors in cli.py
- **ruff format** — all files formatted

## [0.6.0] - 2026-07-22

### Added
- **Vector search** — optional TF-IDF based vector search (vector.py, no external deps)
- **CLI collection management** — `collection list`, `collection show`, `collection remove`
- **Path traversal prevention** — Storage._index_file now validates file paths
- **v0.6.0 milestone** — GitHub milestone created for triage

### Changed
- **Storage refactor** — extracted _build_fts_sql, _build_like_sql, _build_get_sql helpers
- **Storage refactor** — extracted _check_file_stale from check_stale (reduces nesting)
- **Storage refactor** — FTS triggers extracted to module-level constant
- **CLI** — list command preserved as alias for collection list

### Fixed
- **QMD references removed** — all "QMD" mentions cleaned from uri.py, conflicts.py
- **benchmark.py** — excluded from wheel package, docstring explains corpus
- **ruff format** — 4 files reformatted for CI compliance
- **mypy errors** — union-attr and arg-type errors fixed in cli.py

### Security
- **Path traversal** — _index_file validates resolved path stays within repo_dir
- **Symlink check** — _check_file_stale skips symlinks

## [0.5.0] - 2026-07-18

### Added
- **Pydantic v2 models** — Ok, Err, ConflictResult, ChunkEntry, Manifest converted to BaseModel
- **Input validation** — validate_url, validate_collection, validate_query, validate_file_mask
- **E2E tests** — 12 end-to-end tests covering search, CRUD, conflicts, sync, URI, config
- **Chaos fixtures** — 9 tests for database locked, timeout, disk full, corrupt data
- **Codecov integration** — coverage reporting in CI
- **uv migration** — CI migrated from pip to uv for faster installs
- **CLI reference** — `docs/cli.md` with all 7 commands documented
- **Troubleshooting guide** — `docs/troubleshooting.md` with common issues
- **Configuration guide** — `docs/configuration.md` with data directory and HTTP server docs
- **CodeRabbit config** — `.coderabbit.yaml` for AI code review
- **Greptile config** — `greptile.yaml` for code analysis
- **CodeFactor config** — `.codefactor.yml` for code quality metrics

### Changed
- **Result type** — Ok/Err are now Pydantic BaseModel with `is_ok`/`is_err` as properties
- **CI** — migrated from pip to astral-sh/setup-uv
- **Coverage** — added pytest-cov and Codecov upload
- **Architecture docs** — updated with result.py, cli.py, benchmark.py
- **Examples** — updated to use Result type pattern

### Fixed
- **CI lint errors** — fixed ruff formatting issues
- **CI audit** — pip-audit installed via uv tool

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
