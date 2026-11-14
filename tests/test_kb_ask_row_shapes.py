"""Anchor tests for BH-5: kb_ask row-shape resolution.

THE BUG (pre-existing since DH-2): kb_ask builds chunk dicts with r["file_path"]
and r["chunk"] directly. A multi-token question flips storage.search into hybrid
mode (auto_strategy: >2 words -> hybrid); when FTS is sparse the vector backend
appends rows whose shape carries only path="collection/file_path" (no
file_path, no chunk key) -> KeyError('file_path').

Anchors below MUST FAIL on pre-fix code (KeyError in kb_ask) and PASS after
storage.resolve_chunk is wired into the kb_ask chunk loop.

Fail-before evidence (HEAD 6ccc68a): the repro script below against the
mixed_storage corpus -> KeyError: 'file_path' at server.py:149 (kb_ask chunk loop).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from storage import Storage, resolve_chunk

# 5 words -> auto_strategy -> hybrid; keywords [auth, autoscaling, kubernetes].
QUESTION = "how to use auth autoscaling kubernetes?"


# ── corpus ───────────────────────────────────────────────────────────────────


def _insert_two_chunks(storage: Storage) -> None:
    """One document, two chunks: only chunk 1 shares tokens with the BH-7 query."""
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title, chunk_index, total_chunks) VALUES (?, ?, ?, ?, ?, ?)",
        ("sec", "big.md", "plain alpha bravo common", "Big", 0, 2),
    )
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title, chunk_index, total_chunks) VALUES (?, ?, ?, ?, ?, ?)",
        ("sec", "big.md", "quixotic zeppelin xylophone tune", "Big", 1, 2),
    )
    conn.commit()


@pytest.fixture
def mixed_storage(tmp_storage: Storage):
    """Doc guide.md matches 'auth' ONLY through the expansion dictionary
    (synonym 'authentication'); plants.md shares no token with the query at
    all, so it is reachable only as a zero-cosine vector row (path-only shape)
    once hybrid mode merges it in (3 FTS hits < limit=5)."""
    tmp_storage.bulk_insert_raw("sec", "guide.md", "Guide to authentication basics", "Security")
    tmp_storage.bulk_insert_raw("ops", "k8s.md", "Autoscaling guide", "Ops")
    tmp_storage.bulk_insert_raw("ops", "kube.md", "Kubernetes deployment notes", "Kube")
    tmp_storage.bulk_insert_raw("life", "plants.md", "Gardening tips for balcony plants", "Plants")
    with patch("server._get_storage", return_value=tmp_storage):
        yield tmp_storage


# ── fail-before: kb_ask over an expansion + vector-row corpus ────────────────


async def test_kb_ask_expansion_only_match_then_vector_row(mixed_storage: Storage) -> None:
    """The only FTS match comes via expansion terms; hybrid mode then merges
    vector rows (path-only shape). kb_ask must return normally, citing the
    expansion match — never raise KeyError."""
    from server import kb_ask

    class _NoHost:
        async def create_message(self, *args, **kwargs):  # pragma: no cover - host degraded
            raise AssertionError("host not expected in this anchor")

    result = await kb_ask(QUESTION, ctx=_NoHost())
    assert "error" not in result, result
    assert result["answer_mode"] == "host_unavailable", result
    assert any(c["file_path"] == "guide.md" for c in result["chunks"]), result["chunks"]


# ── resolver unit anchors ────────────────────────────────────────────────────


def test_resolve_chunk_vector_shape_row() -> None:
    """Vector row (path without file_path/chunk) resolves to the canonical chunk."""
    chunk = resolve_chunk(
        {"path": "sec/guide.md", "collection": "sec", "title": "Security", "content": "auth notes", "score": 0.42, "source": "vector"}
    )
    assert chunk == {
        "collection": "sec",
        "file_path": "guide.md",
        "chunk_index": 0,
        "title": "Security",
        "content": "auth notes",
        "score": 0.42,
    }


def test_resolve_chunk_garbage_row_skipped() -> None:
    """A row with no path info at all resolves to None (caller skips, never crashes)."""
    assert resolve_chunk({"title": "orphan", "content": "no identity"}) is None
    assert resolve_chunk("not-a-dict") is None
    assert resolve_chunk({"path": "no-slash"}) is None  # unsplit path


def test_resolve_chunk_well_formed_row_unchanged() -> None:
    """Well-formed fts/like rows resolve identically to the current kb_ask dict."""
    row = {
        "path": "sec/guide.md",
        "file_path": "guide.md",
        "collection": "sec",
        "chunk": 2,
        "title": "Security",
        "content": "auth notes",
        "score": 0.9,
    }
    assert resolve_chunk(row) == {
        "collection": "sec",
        "file_path": "guide.md",
        "chunk_index": 2,
        "title": "Security",
        "content": "auth notes",
        "score": 0.9,
    }


# ── BH-7: shared resolver, per-caller chunk tolerance ─────────────────────────


def test_resolve_chunk_non_int_chunk_maps_to_zero() -> None:
    """BH-7 strict mode: resolve_chunk maps a malformed chunk to 0 (kb_ask chunk
    dicts always carry an int index; the missing/malformed case degrades to 0)."""
    for bad in ("not-int", None, 1.5, True):
        row = {"path": "sec/guide.md", "chunk": bad, "title": "T", "content": "C", "score": 0.5}
        chunk = resolve_chunk(row)
        assert chunk is not None
        assert chunk["chunk_index"] == 0, f"chunk={bad!r} must degrade to 0"
    # absent chunk falls back to the default 0
    no_chunk = resolve_chunk({"path": "sec/guide.md"})
    assert no_chunk is not None
    assert no_chunk["chunk_index"] == 0


def test_resolve_chunk_chunk_index_fallback_unchanged() -> None:
    """BH-7: a row with only a chunk_index key still resolves to that index
    (the pre-BH-7 fallback chain is preserved by the shared resolver)."""
    row = {"path": "sec/guide.md", "chunk_index": 2}
    chunk = resolve_chunk(row)
    assert chunk is not None
    assert chunk["chunk_index"] == 2


def test_vector_hit_reports_chunk_index_through_resolve_chunk(tmp_storage: Storage) -> None:
    """BH-7: a vector hit on chunk 1 must report chunk_index 1 through the same
    resolver kb_ask uses. Pre-BH-7 vector rows dropped the chunk index, so every
    content-similarity citation pointed at chunk 0."""
    from vector import VectorIndex

    _insert_two_chunks(tmp_storage)
    rows = VectorIndex(tmp_storage).search("quixotic zeppelin xylophone", limit=5)
    hit = next((r for r in rows if "quixotic" in r.get("content", "")), None)
    assert hit is not None, f"vector search did not return the chunk-1 row: {rows}"
    chunk = resolve_chunk(hit)
    assert chunk is not None
    assert chunk["chunk_index"] == 1, f"vector hit on chunk 1 must resolve to 1: {chunk}"


async def test_kb_ask_vector_hit_cites_chunk_index(tmp_storage: Storage) -> None:
    """BH-7 end-to-end: kb_ask citations carry the real chunk index of a vector hit."""
    from unittest.mock import patch

    from server import kb_ask

    class _NoHost:
        async def create_message(self, *args, **kwargs):  # pragma: no cover - host degraded
            raise AssertionError("host not expected in this anchor")

    _insert_two_chunks(tmp_storage)
    with patch("server._get_storage", return_value=tmp_storage), patch("storage.auto_strategy", return_value="vector"):
        result = await kb_ask("explain quixotic zeppelin xylophone", ctx=_NoHost())
    assert "error" not in result, result
    cited = [c for c in result["citations"] if c["file_path"] == "big.md"]
    assert cited, result["citations"]
    assert max(c["chunk_index"] for c in cited) == 1, f"vector hit on chunk 1 must cite chunk 1: {result['citations']}"


# ── kb_ask regression guard: canonical shape unchanged ───────────────────────


async def test_kb_ask_well_formed_rows_shape_unchanged(tmp_storage: Storage) -> None:
    """For fts rows (the only rows kb_ask saw before hybrid/vector merging), the
    chunk dict keeps the exact same shape and values as the pre-BH-5 builder."""
    tmp_storage.bulk_insert_raw("fastapi", "deps.md", "FastAPI dependency injection with Depends", "Dependencies")
    with patch("server._get_storage", return_value=tmp_storage):

        class _NoHost:
            async def create_message(self, *args, **kwargs):  # pragma: no cover - host degraded
                raise AssertionError("host not expected in this anchor")

        from server import kb_ask

        result = await kb_ask("how to use Depends?", ctx=_NoHost())
    for chunk in result["chunks"]:
        assert set(chunk) == {"collection", "file_path", "chunk_index", "title", "content", "score"}, chunk
    assert result["chunks"] == [
        {
            "collection": "fastapi",
            "file_path": "deps.md",
            "chunk_index": 0,
            "title": "Dependencies",
            "content": "FastAPI dependency injection with Depends",
            "score": result["chunks"][0]["score"],
        }
    ], result["chunks"]
    assert result["citations"] == [{"collection": "fastapi", "file_path": "deps.md", "chunk_index": 0}], result["citations"]
