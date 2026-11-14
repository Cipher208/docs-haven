"""Anchor tests for kb_recent (FUTURE_PLAN P1 #6, DH-3 item 6).

These tests MUST FAIL on current code (no Storage.get_recent / server.kb_recent)
and PASS after the implementation. Covered:

- GROUNDING: no schema migration. The schema has created_at/updated_at/
  retrieval_count but no last_accessed column, so access recency comes from a
  per-Storage in-memory session access log (populated by search/get/find_related,
  the same mechanism as DH-2's session recents). Documents missing from the log
  fall back to retrieval_count DESC, updated_at DESC (retrieval_count > 0 only)
  and report accessed_at=None.
- filters: optional collection, optional since= (ISO date/datetime lower bound;
  compares against the session access time for log entries, updated_at for
  fallback rows)
- result shape {"documents": [...]} with collection/file_path/chunk_index/title/
  accessed_at/retrieval_count/updated_at per document (document-level, deduped)
- empty results, bad input validation (bad collection, bad since, bad limit)
- kb_recent itself records no access (no self-pollution)
- since= accepts a trailing 'Z' (stripped before fromisoformat for Python 3.10)
- DB errors (sqlite3.Error) surface as Err / error dicts, never raise
- malformed search rows cannot break the access-log hot path
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from unittest.mock import patch

from storage import Storage


def _raising_conn() -> None:
    raise sqlite3.OperationalError("disk I/O error")


# ── access recency from the session log (in-memory, no schema change) ───────


async def test_recent_access_recency_from_get(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    tmp_storage.bulk_insert_raw("guide", "b.md", "Doc B about FastAPI", "B")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_get, kb_recent

        await kb_get("guide/a.md")
        result = await kb_recent()

    assert set(result.keys()) == {"documents"}
    docs = result["documents"]
    assert [d["file_path"] for d in docs] == ["a.md"], docs  # b.md was never accessed
    for key in ("collection", "file_path", "chunk_index", "title", "accessed_at", "retrieval_count", "updated_at"):
        assert key in docs[0], f"missing {key}: {docs[0].keys()}"
    assert docs[0]["collection"] == "guide"
    assert docs[0]["accessed_at"] is not None


async def test_recent_access_recency_is_most_recent_first(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    tmp_storage.bulk_insert_raw("guide", "b.md", "Doc B about FastAPI", "B")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_get, kb_recent

        await kb_get("guide/a.md")
        await kb_get("guide/b.md")
        result = await kb_recent()

    assert [d["file_path"] for d in result["documents"]] == ["b.md", "a.md"]


async def test_recent_tracks_search_results(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    tmp_storage.bulk_insert_raw("guide", "z.md", "Nothing relevant here about tomatoes", "Z")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_recent, kb_search

        await kb_search("FastAPI")
        result = await kb_recent()

    assert [d["file_path"] for d in result["documents"]] == ["a.md"], result["documents"]


async def test_recent_records_no_access_for_itself(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_recent

        first = await kb_recent()
        second = await kb_recent()

    assert first["documents"] == []
    assert second["documents"] == []  # asking twice does not fabricate activity


# ── malformed rows must not break the access-log hot path (search/get) ──────


async def test_track_doc_accesses_ignores_malformed_rows(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_recent

        # wrong types everywhere: must neither raise nor crash the caller
        rows: list[Any] = [
            "junk",
            7,
            {"collection": 5, "file_path": None, "path": 123, "chunk": "x"},
            {"collection": "guide", "file_path": "a.md", "chunk": "not-int"},
        ]
        tmp_storage._track_doc_accesses(rows)
        result = await kb_recent()

    assert [d["file_path"] for d in result["documents"]] == ["a.md"]
    assert result["documents"][0]["chunk_index"] == 0  # real DB row value


def test_track_doc_accesses_non_int_chunk_logged_as_none(tmp_storage: Storage) -> None:
    """BH-7 lenient mode: the access-log resolver records a malformed chunk as
    'no chunk' (None) — it never coerces it to 0 like resolve_chunk does."""
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    tmp_storage._track_doc_accesses(
        [
            {"collection": "guide", "file_path": "a.md", "chunk": "not-int"},
            {"collection": "guide", "file_path": "a.md", "chunk": 3},
        ]
    )
    assert tmp_storage._recent_docs[0]["chunk_index"] == 3  # int chunk passes through
    assert tmp_storage._recent_docs[1]["chunk_index"] is None  # non-int -> None, not 0


# ── documented fallback: retrieval_count DESC, updated_at DESC ──────────────


async def test_recent_fallback_retrieval_count_updated_at(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "c.md", "Doc C", "C")
    tmp_storage.bulk_insert_raw("guide", "d.md", "Doc D", "D")
    tmp_storage.bulk_insert_raw("guide", "e.md", "Doc E", "E")
    tmp_storage.bulk_insert_raw("guide", "never.md", "Never retrieved", "N")
    conn = tmp_storage._get_conn()
    conn.execute("UPDATE documents SET retrieval_count = 5, updated_at = '2026-01-01 00:00:00' WHERE file_path = 'c.md'")
    conn.execute("UPDATE documents SET retrieval_count = 2, updated_at = '2026-02-01 00:00:00' WHERE file_path = 'd.md'")
    conn.execute("UPDATE documents SET retrieval_count = 2, updated_at = '2026-01-15 00:00:00' WHERE file_path = 'e.md'")
    conn.commit()

    # Fresh Storage over the same data dir: empty in-memory access log -> documented fallback.
    fresh = Storage(tmp_storage.data_dir)
    try:
        with patch("server._get_storage", return_value=fresh):
            from server import kb_recent

            result = await kb_recent()
    finally:
        fresh.close()

    docs = result["documents"]
    assert [d["file_path"] for d in docs] == ["c.md", "d.md", "e.md"], docs
    # retrieval_count DESC first, then updated_at DESC for ties
    assert docs[0]["retrieval_count"] == 5
    assert all(d["accessed_at"] is None for d in docs)  # no session access recorded
    assert "never.md" not in {d["file_path"] for d in docs}  # never retrieved -> not recent


# ── filters: since= time range, collection ─────────────────────────────────


async def test_recent_since_filter(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_get, kb_recent

        await kb_get("guide/a.md")
        included = await kb_recent(since="2000-01-01")
        excluded = await kb_recent(since="2999-01-01")

    assert [d["file_path"] for d in included["documents"]] == ["a.md"]
    assert excluded["documents"] == []


async def test_recent_since_filter_fallback_rows(tmp_storage: Storage) -> None:
    """For fallback rows since= compares against updated_at (documented choice)."""
    tmp_storage.bulk_insert_raw("guide", "c.md", "Doc C", "C")
    conn = tmp_storage._get_conn()
    conn.execute("UPDATE documents SET retrieval_count = 3, updated_at = '2026-01-02 00:00:00' WHERE file_path = 'c.md'")
    conn.commit()

    fresh = Storage(tmp_storage.data_dir)
    try:
        with patch("server._get_storage", return_value=fresh):
            from server import kb_recent

            included = await kb_recent(since="2026-01-01 00:00:00")
            excluded = await kb_recent(since="2026-02-01")
    finally:
        fresh.close()

    assert [d["file_path"] for d in included["documents"]] == ["c.md"]
    assert excluded["documents"] == []


async def test_recent_collection_filter(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    tmp_storage.bulk_insert_raw("notes", "n.md", "Doc N about FastAPI", "N")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_get, kb_recent

        await kb_get("guide/a.md")
        await kb_get("notes/n.md")
        result = await kb_recent(collection="guide")

    assert [d["collection"] for d in result["documents"]] == ["guide"]


# ── empty results + bad input validation ────────────────────────────────────


async def test_recent_empty_storage_is_empty(tmp_storage: Storage) -> None:
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_recent

        result = await kb_recent()
    assert result == {"documents": []}


async def test_recent_since_accepts_trailing_z(tmp_storage: Storage) -> None:
    """parse_since strips a trailing 'Z'/'z' so Python 3.10's fromisoformat accepts it."""
    from validation import parse_since, validate_since

    assert parse_since("2026-01-01T00:00:00Z") == datetime(2026, 1, 1)
    assert parse_since("2026-01-01T00:00:00z") == datetime(2026, 1, 1)
    assert validate_since("2026-01-01T00:00:00Z") is None
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_get, kb_recent

        await kb_get("guide/a.md")
        included = await kb_recent(since="2026-01-01T00:00:00Z")

    assert [d["file_path"] for d in included["documents"]] == ["a.md"]


# ── DB errors surface as Err / error dicts, never raise ─────────────────────


async def test_recent_db_error_is_err_not_raise(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "Doc A about FastAPI", "A")
    with patch.object(tmp_storage, "_get_conn", _raising_conn), patch("server._get_storage", return_value=tmp_storage):
        result = tmp_storage.get_recent()
        assert result.is_err
        assert "disk I/O error" in str(getattr(result, "error", ""))

        from server import kb_recent

        tool = await kb_recent()
    assert "error" in tool and "disk I/O error" in tool["error"]


async def test_recent_bad_input_validation(tmp_storage: Storage) -> None:
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_recent

        bad_collection = await kb_recent(collection="bad name!")
        assert "error" in bad_collection

        bad_since = await kb_recent(since="not-a-date")
        assert "error" in bad_since

        bad_limit = await kb_recent(limit=0)
        assert "error" in bad_limit
