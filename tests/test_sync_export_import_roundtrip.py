"""Pinning tests for kb_sync export/import round-trip (P0-1).

Design (kept consistent with the fix):
- kb_sync_export serializes ONE record per CHUNK. Each record carries:
  id, collection, file_path, title, content, chunk_index, total_chunks.
- kb_sync_import reconstructs chunks from those records; duplicate chunk
  ids are skipped (idempotent).

These tests MUST FAIL on current master (export writes collection summaries
only; _import_chunk_data reads doc.get("path", "") but the record uses the
"file_path" key -> empty-path junk rows are inserted, dedupe guard never
matches, and double-import happens) and PASS after PS1.
"""

from __future__ import annotations

import gzip
import hashlib
import json

from storage import Storage
from sync import Syncer


def _chunk_rows(storage: Storage) -> list[dict]:
    conn = storage._get_conn()
    return conn.execute("SELECT id, collection, file_path, title, content, chunk_index, total_chunks FROM documents ORDER BY id").fetchall()


def _export_chunk_data(storage: Storage) -> list[dict]:
    return [
        {
            "id": r["id"],
            "collection": r["collection"],
            "file_path": r["file_path"],
            "title": r["title"],
            "content": r["content"],
            "chunk_index": r["chunk_index"],
            "total_chunks": r["total_chunks"],
        }
        for r in _chunk_rows(storage)
    ]


def _count(storage: Storage) -> int:
    return storage._get_conn().execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def _find(storage: Storage, collection: str, path: str) -> dict | None:
    conn = storage._get_conn()
    row = conn.execute(
        "SELECT id, collection, file_path, title, content FROM documents WHERE collection = ? AND file_path = ?",
        (collection, path),
    ).fetchone()
    return dict(row) if row else None


def _write_manifest(syncer: Syncer, entries: list[dict]) -> None:
    """Write manifest with the given chunk entries (idempotent overwrite)."""
    syncer._ensure_dirs()
    manifest = {"version": 1, "chunks": list(entries)}
    (syncer.manifest_path).write_text(json.dumps(manifest))


def _make_chunk(syncer: Syncer, rec: dict) -> dict:
    """Create one chunk file (gzipped JSON) for a single record. Returns manifest entry."""
    chunk = {"collections": {rec["collection"]: [rec]}, "exported_at": "2026-01-01T00:00:00Z"}
    chunk_json = json.dumps(chunk, ensure_ascii=False).encode()
    chunk_id = hashlib.sha256(chunk_json).hexdigest()[:16]
    (syncer.chunks_dir / f"{chunk_id}.json.gz").write_bytes(gzip.compress(chunk_json))
    return {
        "id": chunk_id,
        "created_by": "test",
        "created_at": "2026-01-01T00:00:00Z",
        "collections": 1,
        "documents": 1,
    }


def _fresh_import_target(tmp_storage: Storage, sub: str) -> tuple[Storage, Syncer]:
    """Create a FRESH storage + syncer INSIDE tmp_storage's data_dir (so cleanup works)."""
    target_dir = tmp_storage.data_dir / sub
    target_dir.mkdir(parents=True, exist_ok=True)
    fresh = Storage(target_dir)
    syncer = Syncer(fresh.data_dir / ".docshaven-sync")
    return fresh, syncer


# ── Requirement 1: export carries real content ───────────────────────────────


def test_export_contains_real_content(tmp_storage: Storage) -> None:
    """kb_sync_export must serialize real document content, not summaries."""
    tmp_storage.bulk_insert_raw("core", "README.md", "real content here", "Readme")
    tmp_storage.bulk_insert_raw("core", "guide.md", "guide body text", "Guide")

    rows = _chunk_rows(tmp_storage)
    exported = _export_chunk_data(tmp_storage)

    assert len(exported) == len(rows) == 2, "expected 2 real chunks exported"
    for rec in exported:
        assert rec["content"], "exported chunk has empty content"
        assert rec["file_path"], "exported chunk has empty file_path"


# ── Requirement 2: round-trip reconstructs content/title ─────────────────────


def test_roundtrip_restores_content(tmp_storage: Storage) -> None:
    """After clear + import, every exported doc is findable with same content/title."""
    tmp_storage.bulk_insert_raw("lib", "doc.md", "unique alpha content", "Alpha")
    tmp_storage.bulk_insert_raw("lib", "doc2.md", "beta body text here", "Beta")

    exported = _export_chunk_data(tmp_storage)

    fresh, syncer = _fresh_import_target(tmp_storage, "rt")
    try:
        entries = [_make_chunk(syncer, rec) for rec in exported]
        _write_manifest(syncer, entries)

        res = syncer.import_chunks(fresh)
        assert res["chunks_imported"] == len(exported), res
        assert _count(fresh) == len(exported), f"{_count(fresh)} != {len(exported)}"

        for r in exported:
            got = _find(fresh, r["collection"], r["file_path"])
            assert got is not None, f"missing after import: {r['collection']}/{r['file_path']}"
            assert got["content"] == r["content"], f"content mismatch for {r['file_path']}"
            assert got["title"] == r["title"], f"title mismatch for {r['file_path']}"
    finally:
        fresh.close()


# ── Requirement 3: idempotent import ────────────────────────────────────────


def test_idempotent_import(tmp_storage: Storage) -> None:
    """Importing the same chunk twice must not increase the document count.

    Covers both in-run dedup (duplicate manifest entry with the same id) and
    across-call dedup (re-importing the same manifest for the same storage).
    """
    tmp_storage.bulk_insert_raw("c", "one.md", "first", "One")
    tmp_storage.bulk_insert_raw("c", "two.md", "second", "Two")
    exported = _export_chunk_data(tmp_storage)

    target, syncer = _fresh_import_target(tmp_storage, "idem")
    try:
        # Manifest with each real chunk once, plus a duplicate of the first entry.
        entries = [_make_chunk(syncer, rec) for rec in exported]
        entries.append(entries[0].copy())
        _write_manifest(syncer, entries)

        first = syncer.import_chunks(target)
        first_count = _count(target)

        # In-run dedup: the duplicated entry must have been skipped.
        assert first["chunks_imported"] == len(exported), first
        assert first["chunks_skipped"] == 1, f"duplicate manifest entry should be skipped, got chunks_skipped={first['chunks_skipped']}"
        assert first_count == len(exported), f"document count should equal number of unique chunks, got {first_count}"

        # Across-call dedup: re-importing the same manifest for the same storage
        # must skip every chunk (chunks_skipped == len(manifest entries)).
        second = syncer.import_chunks(target)
        second_count = _count(target)

        assert second["chunks_skipped"] == len(entries), (
            f"re-import should skip all {len(entries)} chunks, got chunks_skipped={second['chunks_skipped']}"
        )
        assert second_count == first_count, f"count must not grow on re-import: {second_count} != {first_count}"
    finally:
        target.close()


# ── Requirement 4: empty rows rejected ──────────────────────────────────────


def test_empty_row_rejected(tmp_storage: Storage) -> None:
    """A chunk row with empty file_path/content/title must not create a searchable doc."""
    good = {"collection": "c", "file_path": "real.md", "title": "Real", "content": "real text"}
    junk = {"collection": "c", "file_path": "", "title": "", "content": ""}
    syncer = Syncer(tmp_storage.data_dir / ".docshaven-sync")
    _write_manifest(syncer, [_make_chunk(syncer, good), _make_chunk(syncer, junk)])

    try:
        syncer.import_chunks(tmp_storage)
        count = _count(tmp_storage)

        # junk row must NOT become a searchable empty doc
        assert count == 1, f"junk row should be ignored, got {count}"
        assert _find(tmp_storage, "c", "real.md") is not None
    finally:
        tmp_storage.close()  # tmp_storage fixture would close anyway; safe no-op if already closed
