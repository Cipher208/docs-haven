"""Anchor tests for context-aware search (FUTURE_PLAN P1 #3, DH-2 item 2).

These tests MUST FAIL on current code (search() takes no context kwarg, Storage
has no recent_collections()) and PASS after the context-aware implementation:
- `context` parameter boosts results from named collections by +0.1
- recently accessed collections are tracked per session
- recently accessed collections get the same +0.1 boost
- the boost is opt-in via the context parameter (plain kb_search is unchanged)
"""

from __future__ import annotations

import pytest

from storage import Storage

# ── context parameter boost ──────────────────────────────────────────────────


def test_context_parameter_boosts_collection(tmp_storage: Storage) -> None:
    """Results from collections named in `context` get +0.1 and rank above ties."""
    for coll in ("alpha", "beta"):
        tmp_storage.bulk_insert_raw(coll, "doc.md", "shared_keyword content here", "Doc")

    res = tmp_storage.search("shared_keyword", limit=10, explain=True, context=["beta"])
    assert res.is_ok, res
    docs = res.value
    assert len(docs) == 2, [d["path"] for d in docs]

    by_coll = {d["collection"]: d for d in docs}
    assert by_coll["beta"]["explain"]["context_boost"] == pytest.approx(0.1), by_coll["beta"]["explain"]
    assert by_coll["alpha"]["explain"]["context_boost"] == 0.0, by_coll["alpha"]["explain"]
    assert docs[0]["collection"] == "beta", f"context boost must break the tie: {[d['path'] for d in docs]}"
    assert by_coll["beta"]["score"] == pytest.approx(min(1.0, by_coll["alpha"]["score"] + 0.1), abs=1e-3)


def test_context_boost_clamped_to_one(tmp_storage: Storage) -> None:
    """Public score stays clamped to 0..1 even with the context boost applied."""
    tmp_storage.bulk_insert_raw("alpha", "doc.md", "shared_keyword content here", "Doc")
    res = tmp_storage.search("shared_keyword", limit=10, context=["alpha"])
    assert res.is_ok, res
    assert res.value[0]["score"] <= 1.0, res.value[0]["score"]


# ── session tracking of recently accessed collections ────────────────────────


def test_recent_collections_tracked_on_search(tmp_storage: Storage) -> None:
    """Collections of search results are recorded in the session recents."""
    tmp_storage.bulk_insert_raw("seen", "doc.md", "shared_keyword content", "Doc")
    tmp_storage.search("shared_keyword", limit=10)
    assert "seen" in tmp_storage.recent_collections(), tmp_storage.recent_collections()


def test_recent_collections_tracked_on_get(tmp_storage: Storage) -> None:
    """Viewing a document via get() also records its collection as recently accessed."""
    tmp_storage.bulk_insert_raw("viewed", "doc.md", "content", "Doc")
    tmp_storage.get("doc.md", collection="viewed")
    assert "viewed" in tmp_storage.recent_collections(), tmp_storage.recent_collections()


# ── recent collections get the boost in later searches ───────────────────────


def test_recently_viewed_collections_boosted(tmp_storage: Storage) -> None:
    """A later search (context=[] = recents-only mode) boosts recently accessed collections by +0.1."""
    tmp_storage.bulk_insert_raw("recentcoll", "doc.md", "shared_keyword content", "Doc")
    tmp_storage.bulk_insert_raw("other", "doc2.md", "shared_keyword content", "Doc2")

    # First search is scoped to recentcoll, so only it enters the session recents.
    first = tmp_storage.search("shared_keyword", collections=["recentcoll"], limit=10)
    assert first.is_ok and first.value, first

    second = tmp_storage.search("shared_keyword", limit=10, explain=True, context=[])
    assert second.is_ok, second
    by_coll = {d["collection"]: d for d in second.value}
    assert by_coll["recentcoll"]["explain"]["context_boost"] == pytest.approx(0.1), by_coll["recentcoll"]["explain"]
    assert by_coll["other"]["explain"]["context_boost"] == 0.0, by_coll["other"]["explain"]


def test_context_boost_requires_context_param(tmp_storage: Storage) -> None:
    """Without the context parameter, plain search() stays unboosted (kb_search compat)."""
    tmp_storage.bulk_insert_raw("alpha", "x.md", "shared_keyword text", "X")
    tmp_storage.search("shared_keyword", limit=10)  # populate recents

    res = tmp_storage.search("shared_keyword", limit=10, explain=True)
    assert res.is_ok, res
    assert res.value[0]["explain"]["context_boost"] == 0.0, res.value[0]["explain"]


def test_context_boost_absent_from_plain_explain_defaults(tmp_storage: Storage) -> None:
    """explain output still sums up: final_score includes context_boost when applied."""
    tmp_storage.bulk_insert_raw("alpha", "x.md", "shared_keyword text", "X")
    res = tmp_storage.search("shared_keyword", limit=10, explain=True, context=["alpha"])
    explain = res.value[0]["explain"]
    total = explain["base_score"] + explain["type_boost"] + explain["importance_boost"] + explain["context_boost"]
    assert total == pytest.approx(explain["final_score"], abs=0.01), explain
