# FIXER A — Sync round-trip fix (P0-1)

## Goal
Make `kb_sync_export` serialize **real documents** and `kb_sync_import` reconstruct them faithfully and idempotently.

## What must change
- `server.py`: `kb_sync_export` must produce documents/rows, not `{c["name"]: [c]}`.
- `sync.py`: `import_chunks` / `_import_chunk_data` must parse real doc rows, store real id + content, dedupe by id, reject empty rows.

## Tests to add: `tests/test_sync_export_import_roundtrip.py`
1. Export contains N real documents with content/file_path/title.
2. Round-trip: after clear+import, get(path) content == original, total_documents == N.
3. Idempotency: double import keeps count at N.
4. Junk row (file_path='', content='', title='') does not become a searchable empty doc.

## Commands
- Create: `uv run --frozen pytest tests/test_sync_export_import_roundtrip.py -q -p no:cacheprovider -x`
- Regression: `uv run --frozen pytest tests/ -q -p no:cacheprovider`

## Out of scope
Do NOT touch scoring, vector, conflicts, cli, alias, templates.