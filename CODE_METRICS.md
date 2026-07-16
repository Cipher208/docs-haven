# DocsHaven Code Metrics Report

## File Summary

| File | LOC | Functions | Avg CCN | Max CCN |
|------|-----|-----------|---------|---------|
| storage.py | 506 | 18 | 3.8 | 7 |
| server.py | 286 | 12 | 2.6 | 3 |
| sync.py | 203 | 10 | 3.7 | 5 |
| conflicts.py | 137 | 8 | 2.0 | 3 |
| uri.py | 138 | 12 | 2.3 | 4 |
| benchmark.py | 123 | 4 | 3.3 | 4 |
| cli.py | 111 | 6 | 1.0 | 1 |
| config.py | 47 | 7 | 1.0 | 1 |
| result.py | 54 | 8 | 1.0 | 1 |
| **Total** | **1605** | **85** | **2.6** | **7** |

## Cyclomatic Complexity by Function (CCN)

### storage.py

| Function | CCN | LOC |
|----------|-----|-----|
| `chunk_text` | 7 | 25 |
| `auto_strategy` | 2 | 5 |
| `type_boost` | 6 | 13 |
| `Storage.__init__` | 1 | 9 |
| `Storage.close` | 2 | 5 |
| `Storage._get_conn` | 2 | 12 |
| `Storage._init_db` | 1 | 57 |
| `Storage._index_file` | 2 | 17 |
| `Storage.add_repo` | 6 | 38 |
| `Storage._merge_hybrid` | 3 | 9 |
| `Storage.search` | 7 | 35 |
| `Storage._row_to_result` | 1 | 13 |
| `Storage._search_fts5` | 3 | 35 |
| `Storage._search_like` | 3 | 32 |
| `Storage.get` | 3 | 30 |
| `Storage.list_collections` | 3 | 24 |
| `Storage.stats` | 4 | 19 |
| `Storage._load_config` | 2 | 4 |
| `Storage._save_config` | 2 | 9 |
| `Storage.check_stale` | 4 | 29 |

### server.py

| Function | CCN | LOC |
|----------|-----|-----|
| `_get_storage` | 1 | 6 |
| `_get_syncer` | 1 | 2 |
| `_get_router` | 1 | 2 |
| `_get_detector` | 1 | 2 |
| `kb_search` | 2 | 16 |
| `kb_add_repo` | 2 | 15 |
| `kb_get` | 1 | 8 |
| `kb_update` | 3 | 16 |
| `kb_delete` | 2 | 14 |
| `kb_list_collections` | 1 | 3 |
| `kb_stats` | 1 | 3 |
| `kb_uri_resolve` | 1 | 9 |
| `kb_uri_search` | 1 | 6 |
| `kb_uri_list` | 1 | 6 |
| `kb_uri_domains` | 1 | 3 |
| `kb_sync_export` | 2 | 13 |
| `kb_sync_import` | 1 | 3 |
| `kb_sync_status` | 1 | 3 |
| `kb_conflict_check` | 2 | 19 |
| `kb_conflict_judge` | 2 | 13 |
| `__main__` block | 3 | 12 |

### sync.py

| Function | CCN | LOC |
|----------|-----|-----|
| `Manifest.to_dict` | 1 | 2 |
| `Manifest.from_dict` | 1 | 4 |
| `Manifest.from_file` | 2 | 6 |
| `Syncer.__init__` | 1 | 5 |
| `Syncer._ensure_dirs` | 1 | 3 |
| `Syncer.export` | 5 | 38 |
| `Syncer._import_chunk_data` | 3 | 19 |
| `Syncer.import_chunks` | 4 | 35 |
| `Syncer.status` | 2 | 13 |
| `Syncer._write_manifest` | 1 | 3 |
| `get_username` | 1 | 2 |

### uri.py

| Function | CCN | LOC |
|----------|-----|-----|
| `URI.parse` | 3 | 11 |
| `URI.create` | 3 | 7 |
| `URI.to_collection` | 2 | 4 |
| `URI.to_file_pattern` | 2 | 5 |
| `URI.__str__` | 1 | 2 |
| `URI.__eq__` | 1 | 2 |
| `URI.__hash__` | 1 | 2 |
| `URIRouter.resolve` | 1 | 9 |
| `URIRouter.search_by_uri` | 4 | 22 |
| `URIRouter.list_by_domain` | 2 | 11 |
| `URIRouter.list_all_domains` | 3 | 11 |

### conflicts.py

| Function | CCN | LOC |
|----------|-----|-----|
| `ConflictResult.to_dict` | 1 | 7 |
| `ConflictDetector.__init__` | 1 | 2 |
| `ConflictDetector._get_storage` | 2 | 7 |
| `ConflictDetector.detect` | 2 | 18 |
| `ConflictDetector._find_similar` | 3 | 27 |
| `ConflictDetector.judge` | 2 | 16 |

### benchmark.py

| Function | CCN | LOC |
|----------|-----|-----|
| `generate_docs` | 2 | 23 |
| `benchmark_indexing` | 1 | 16 |
| `benchmark_search` | 1 | 32 |
| `run_benchmark` | 1 | 30 |

### cli.py

| Function | CCN | LOC |
|----------|-----|-----|
| `get_storage` | 1 | 2 |
| `cmd_search` | 1 | 13 |
| `cmd_add` | 1 | 9 |
| `cmd_stats` | 1 | 8 |
| `cmd_uri` | 2 | 17 |
| `main` | 1 | 37 |

### config.py

| Function | CCN | LOC |
|----------|-----|-----|
| `Config.__init__` | 1 | 4 |
| `Config._load` | 2 | 10 |
| `Config.save` | 1 | 3 |
| `Config.get` | 1 | 2 |
| `Config.set` | 1 | 3 |
| `Config.data_dir` | 1 | 2 |
| `Config.sync_dir` | 1 | 2 |

### result.py

| Function | CCN | LOC |
|----------|-----|-----|
| `Ok.is_ok` | 1 | 2 |
| `Ok.is_err` | 1 | 2 |
| `Ok.unwrap` | 1 | 2 |
| `Err.is_ok` | 1 | 2 |
| `Err.is_err` | 1 | 2 |
| `Err.unwrap` | 1 | 2 |
| `ok` | 1 | 2 |
| `err` | 1 | 2 |

## Test Coverage Gaps

| Source Module | Functions | Tested | Untested Functions |
|---------------|-----------|--------|--------------------|
| storage.py | 18 | 12 | `_get_conn`, `_init_db`, `_index_file`, `_merge_hybrid`, `_search_fts5`, `_search_like` (private, tested indirectly) |
| server.py | 13 | 5 | `kb_add_repo`, `kb_update`, `kb_delete`, `kb_sync_export`, `kb_sync_import`, `kb_sync_status`, `kb_conflict_check`, `kb_conflict_judge` |
| sync.py | 7 | 3 | `_import_chunk_data` (tested indirectly via import_chunks) |
| conflicts.py | 5 | 3 | `_get_storage`, `_find_similar` (private, tested indirectly) |
| uri.py | 7 | 7 | **Full coverage** |
| cli.py | 6 | 3 | `cmd_add` (untested) |
| config.py | 7 | 5 | `data_dir`, `sync_dir` properties (tested indirectly) |
| result.py | 8 | 8 | **Full coverage** |
| benchmark.py | 4 | 0 | **No tests** (benchmark script) |

**Highest priority untested:**
1. `server.py` — 8 MCP tools completely untested (kb_add_repo, kb_update, kb_delete, sync tools, conflict tools)
2. `cli.py:cmd_add` — add repo path untested
3. `benchmark.py` — no tests (acceptable for benchmark script)

## Code Duplication

### 1. `storage._get_conn()` pattern (3 files)
- `server.py:118` — `storage._get_conn()`
- `sync.py:132` — `storage._get_conn()`
- `benchmark.py:42` — `storage._get_conn()`

All three access the private `_get_conn()` method directly from outside the class. Should be exposed via a public method or use `Storage`'s public API.

### 2. `subprocess.run(["git", "clone", ...])` pattern
- `storage.py:220-226` — git clone in `add_repo`
- `server.py:91` — delegates to `add_repo`

Not true duplication (server delegates), but the subprocess call in `storage.py` could be extracted.

### 3. `from pathlib import Path` + `Storage(Path.home() / ".docshaven")` pattern
- `server.py:30` — `Storage(Path.home() / ".docshaven")`
- `cli.py:12` — `Storage(Path.home() / ".docshaven")`
- `conflicts.py:62` — `Storage(Path.home() / ".docshaven")`

Three files independently create Storage instances with the same default path. Should be centralized in `Config` or a factory.

### 4. `conn.execute("INSERT OR REPLACE INTO documents ...")` pattern
- `storage.py:200-205` — in `_index_file`
- `sync.py:137-141` — in `_import_chunk_data`

Both insert documents with the same SQL structure but different field sets.

### 5. `logger.debug("... failed: %s", e)` pattern
- `storage.py:350,383,414,439,460,505` — 6 occurrences
- `server.py:132,149` — 2 occurrences

Consistent error-logging pattern (not harmful duplication, but could use a decorator).

## Top 3 Improvement Targets

### 1. storage.py — `chunk_text` (CCN: 7) + `search` (CCN: 7)

**Why:** These are the highest-complexity functions. `chunk_text` has nested conditionals for break-point logic. `search` combines auto-strategy, hybrid merging, type boosting, and explain mode in one function.

**Fix:**
- `chunk_text`: Extract break-point finding into `_find_break_point(text, chunk_size)` helper
- `search`: Extract explain-mode scoring into a separate `_apply_explain(results, query)` method

### 2. server.py — Missing tests for 8 MCP tools

**Why:** `kb_add_repo`, `kb_update`, `kb_delete`, `kb_sync_export`, `kb_sync_import`, `kb_sync_status`, `kb_conflict_check`, `kb_conflict_judge` have zero test coverage. These are user-facing MCP tools — a regression would silently break agent integrations.

**Fix:** Add mock-based tests for each untested tool, following the pattern in `test_server.py` (use `MagicMock` + `patch`).

### 3. Duplicated Storage initialization across 3 files

**Why:** `Storage(Path.home() / ".docshaven")` appears in `server.py:30`, `cli.py:12`, and `conflicts.py:62`. Changing the default path requires editing 3 files.

**Fix:** Add a `Storage.default()` classmethod or use `Config.data_dir` as the single source of truth.

---

Generated: 2026-07-16
