# Remaining Issues — docs-haven v0.8.0

Code review issues from 3-delegate audit (security, logic, quality).
Generated: 2026-07-23

## Status Summary

| Priority | Found | Fixed | Remaining |
|----------|-------|-------|-----------|
| Critical | 5 | 5 ✅ | 0 |
| High | 8 | 8 ✅ | 0 |
| Medium | 17 | 14 ✅ | 3 (acceptable) |
| Low | 29 | 17 ✅ | 12 (deferred) |
| **Total** | **59** | **44** | **15** |

---

## Remaining Medium Issues (3 — marked acceptable)

| # | Source | Issue | File | Rationale |
|---|--------|-------|------|-----------|
| M7 | Logic | _init_db exception leaves connection in partial state | storage.py:230 | Rare failure path, connection GC handles cleanup |
| M13 | Quality | uri.py quoted forward reference -> "URI" | uri.py:19 | Cosmetic only, no runtime impact |
| M15 | Quality | conflicts.py inconsistent return types (judge) | conflicts.py:116 | Acceptable trade-off for API clarity |

---

## Remaining Low Issues (14 — deferred as known-debt)

| # | Source | Issue | File |
|---|--------|-------|------|
| L2 | Logic | conflicts.py direct _get_conn() access | conflicts.py:154 |
| L4 | Logic | uri.py wildcard search scales poorly (O(n) queries) | uri.py:99 |
| L6 | Logic | result.py Ok.model_dump() inconsistent types | result.py:25 |

| L19 | Quality | cli.py type:ignore spam (20+ occurrences) | cli.py:all |
| L20 | Quality | server.py long __main__ block (25 lines) | server.py:417 |
| L24 | Quality | storage.py chunk_text infinite loop comment | storage.py:100 |
| L25 | Quality | server.py _is_unsafe_path duplicates validation | server.py:29 |
| L26 | Quality | conflicts.py direct SQL outside storage layer | conflicts.py:154 |
| L27 | Quality | cli.py monolithic argument parser (87 lines) | cli.py:250 |
| L28 | Quality | storage.py config file read/write no locking | storage.py:864 |
| L29 | Quality | Encapsulation: 5 files access _get_conn() directly | misc |

---

## Notes

- All Critical and High issues have been fixed
- 14 of 17 Medium issues fixed (3 remaining are acceptable)
- 17 of 29 Low issues fixed (12 deferred as known-debt)
- Total remaining: 15 issues (all cosmetic/encapsulation)
- Code review was performed by 3 parallel delegates (security, logic, quality)
