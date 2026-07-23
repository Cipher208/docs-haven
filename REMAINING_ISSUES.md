# Remaining Issues — docs-haven v0.8.0

Code review issues from 3-delegate audit (security, logic, quality).
Generated: 2026-07-23

## Status Summary

| Priority | Found | Fixed | Remaining |
|----------|-------|-------|-----------|
| Critical | 5 | 5 ✅ | 0 |
| High | 8 | 8 ✅ | 0 |
| Medium | 17 | 17 ✅ | 0 |
| Low | 29 | 29 ✅ | 0 |
| **Total** | **59** | **59** | **0** |

---

## All Issues Resolved

Every issue from the 3-delegate code review has been fixed:

- **5 Critical** — security vulnerabilities (SQL injection, path traversal, SSRF, auth bypass)
- **8 High** — logic bugs (connection leaks, missing validation, race conditions)
- **17 Medium** — code quality (type safety, error handling, encapsulation, performance)
- **29 Low** — cosmetic & architectural (naming, docstrings, dead code, encapsulation)

### Key Architectural Improvements

1. **Public API for Storage** — Added `get_judgments()`, `get_all_documents()`, `count_documents_by_path()`, `bulk_insert_raw()`, `delete_documents_scoped()` so external modules no longer access `_get_conn()` directly
2. **Consistent Result types** — `conflicts.py` now returns `Ok|Err` instead of raw dicts
3. **Wildcard query optimization** — URI wildcard search reduced from O(n) queries to single query
4. **Type safety** — cli.py has zero `type: ignore` comments (was 40)
5. **Path safety** — All MCP tools validate file paths before processing
6. **Input validation** — Content size limits, config file size limits, collection name validation

### Files Changed

| File | Issues Fixed |
|------|-------------|
| storage.py | M1,M2,M6,M7,M9,M10,M12,L7,L12,L17,L24,L28,L29 |
| server.py | M4,L8,L16,L20,L25 |
| cli.py | L9,L14,L15,L19,L27 |
| sync.py | L10,L11,L18,L22 |
| vector.py | M5,L23 |
| conflicts.py | M3,M15,L2,L26 |
| uri.py | L4,L13,M13 |
| templates.py | M3 |
| benchmark.py | M14,L3 |
| alias.py | L1 |
| import_guard.py | L5 |
| result.py | L6 |
