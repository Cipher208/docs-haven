# FIXER C — Identity + search + rename + FTS snippet (P0-2/4/5)

## Goal
Unify path semantics (search ↔ get ↔ delete ↔ rename), fix FTS snippet column (2→1), and make rename persist.

## Tests (new file `tests/test_search_identity_and_snippet.py`)
1. kb_search path is fetchable by kb_get.
2. FTS highlight shows content, not collection.
3. CLI get/delete by prefixed path.
4. kb_collection_rename persists and search reflects it.
5. No cross-collection false match.

## Commands
- New: `uv run --frozen pytest tests/test_search_identity_and_snippet.py -q -p no:cacheprovider -x`
- Regression: `uv run --frozen pytest tests/ -q -p no:cacheprovider`

## Out of scope
Do NOT touch sync, scoring, vector, conflicts, alias, templates.