"""Extra pinning for the dedup guard in import_chunks (P0-1, requirement 3 refined).

The existing guard in import_chunks is:

    existing = storage.count_documents_by_path(f"%{entry.id}%")
    if existing > 0:
        chunks_skipped += 1
        continue

That is FALSE dedup: it checks whether ANY document's file_path contains the
chunk id as a substring, not whether THIS chunk was already imported. This is
both (a) ineffective as dedup and (b) risky: a document whose file_path
coincidentally contains a 16-hex substring that matches another chunk's id will
cause that other chunk to be skipped.

These tests MUST FAIL on current master (the guard can skip an unrelated chunk
when a file_path contains the same hex substring) and PASS after the fix, which
should track imported chunk ids rather than scanning file_path.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from sync import Syncer
from storage import Storage


def _write_manifest(syncer: Syncer, entries: list[dict]) -> None:
    syncer._ensure_dirs()
    syncer.manifest_path.write_text(
        json.dumps({"version": 1, "chunks": entries})
    )


def _make_chunk(syncer: Syncer, rec: dict) -> dict:
    chunk = {
        "collections": {rec["collection"]: [rec]},
        "exported_at": "2026-01-01T00:00:00Z",
    }
    chunk_json = json.dumps(chunk, ensure_ascii=False).encode()
    chunk_id = hashlib.sha256(chunk_json).hexdigest()[:16]
    (syncer.chunks_dir / f"{chunk_id}.json.gz").write_bytes(
        gzip.compress(chunk_json)
    )
    return {
        "id": chunk_id,
        "created_by": "test",
        "created_at": "2026-01-01T00:00:00Z",
        "collections": 1,
        "documents": 1,
    }


# ── Guard corner case: file_path collision must NOT skip the wrong chunk ─────

def test_path_collision_does_not_skip_needed_chunk(tmp_storage: Storage) -> None:
    """A file_path that coincidentally contains a chunk id as substring must not
    cause a DIFFERENT, needed chunk to be skipped on import.

    current guard: count_documents_by_path(f"%{entry.id}%") -> can be fooled.
    fixed: track imported chunk ids by their manifest entry id.
    """
    # Build records so that one file_path contains hex that equals another
    # chunk's id. We force this by choosing inputs whose SHA-256 prefix we
    # control indirectly: we first import a "decoy" document whose file_path is
    # a 16-hex string, then create a chunk whose id equals that string.
    import random

    hex16 = "".join(random.choice("0123456789abcdef") for _ in range(16))
    decoy = {
        "collection": "c",
        "file_path": f"/tmp/{hex16}/note.md",  # file_path CONTAINS hex16
        "title": "Decoy",
        "content": "decoy body",
    }
    # The "real" chunk record we actually want to import must have an id
    # EQUAL to hex16. We construct it by brute-forcing content until the
    # SHA-256[:16] of its chunk payload equals hex16 (rare, but we only need
    # to demonstrate the guard's logic failure, so we instead directly test
    # the guard by patching the scenario: we write a manifest entry whose id
    # equals hex16 AND a chunk file for it, and pre-populate the storage with
    # the decoy doc whose file_path contains hex16).
    #
    # Simpler robust approach: we IMITATE the guard's own logic in the test by
    # manually constructing the conditions it relies on, without needing
    # hash-collision luck. We write one chunk (id = hex16) and a decoy doc
    # whose file_path contains hex16.

    syncer = Syncer(tmp_storage.data_dir / ".docshaven-sync")

    # 1. Pre-populate storage with a doc whose file_path contains hex16.
    tmp_storage.bulk_insert_raw("c", f"/tmp/{hex16}/note.md", "real body", "Real")

    # 2. Create the chunk file whose manifest id == hex16.
    real_rec = {
        "collection": "c",
        "file_path": "important.md",
        "title": "Important",
        "content": "important content here",
    }
    chunk = {
        "collections": {"c": [real_rec]},
        "exported_at": "2026-01-01T00:00:00Z",
    }
    chunk_json = json.dumps(chunk, ensure_ascii=False).encode()
    # Use hex16 as the chunk id directly (we control it here for the test).
    (syncer.chunks_dir / f"{hex16}.json.gz").write_bytes(gzip.compress(chunk_json))

    real_entry = {
        "id": hex16,
        "created_by": "test",
        "created_at": "2026-01-01T00:00:00Z",
        "collections": 1,
        "documents": 1,
    }
    _write_manifest(syncer, [real_entry])

    # 3. IMPORT — the current guard checks count_documents_by_path(f"%{hex16}%")
    #    which matches the decoy doc (file_path contains hex16), so it SKIPS the
    #    real chunk. After import, "important.md" must still be missing.
    before = tmp_storage._get_conn().execute(
        "SELECT COUNT(*) FROM documents WHERE collection = ? AND file_path = ?",
        ("c", "important.md"),
    ).fetchone()[0]

    res = syncer.import_chunks(tmp_storage)

    after = tmp_storage._get_conn().execute(
        "SELECT COUNT(*) FROM documents WHERE collection = ? AND file_path = ?",
        ("c", "important.md"),
    ).fetchone()[0]

    # Correct behavior: the needed chunk must be imported despite the collision.
    assert after > before, (
        "needed chunk was skipped due to file_path collision with guard; "
        f"before={before} after={after}; guard result={res}"
    )
