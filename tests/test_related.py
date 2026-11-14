"""Anchor tests for kb_related (FUTURE_PLAN P1 #5, DH-3 items 5).

These tests MUST FAIL on current code (no Storage.find_related / server.kb_related)
and PASS after the implementation. Covered:

- result shape {source, related[]} with relation {tag_overlap, same_collection,
  content_similarity} and score in [0, 1], sorted descending
- scoring formula: 0.4*tag_overlap + 0.2*same_collection + 0.4*content_similarity
- GROUNDING: the schema has no tag column, so the "tag" signal is title-term
  overlap (share of the source title's terms found in the candidate title).
  The substitution is documented and marked in explain.tag_overlap_source.
- content similarity reuses scoring.lexical_relevance over the source doc's
  content (no embeddings, no new dependencies)
- per-document aggregation: multi-chunk candidates appear exactly once
- chunk_index selects the source chunk (missing chunk errors gracefully)
- empty results, bad input validation (unknown source, bad collection, bad limit)
- explain-style breakdown with weights + the grounding marker
- zero-signal candidates (tag_overlap == 0 AND content_similarity == 0) are
  dropped: same_collection alone is not a relation
- a long source document (> 24 filler tokens) still matches on its subject when
  the subject appears at the END of the content (word-window sampling)
- ROUND 3 anchors:
  - source terms are sampled on WORD boundaries (no mid-word cuts like
    "depen"+"dency") so short docs keep matching their subject wording
  - a subject placed MID-document (outside every char-third sampling window
    the old implementation used) still matches via the word-stream windows
  - the zero-signal gate does NOT drop cross-collection documents with a
    different title but shared subject wording (content-only relations)
  - find_related logs the source access with the caller's chunk_index
- DB errors (sqlite3.Error) surface as Err / error dicts, never raise
- ROUND 4 anchors:
  - a subject placed at 20% and at 70% of a 200-word document is visible in
    BOTH positions (content_similarity > 0) — the old head/middle/tail 48-term
    positional sampling left both positions in invisible windows
  - the full position sweep 0%..100% (step 10) of a 200-word document has ZERO
    holes: every position matches (fails on any windowed sampler)
  - a documents row with chunk_index = NULL (reachable only via raw SQL)
    degrades gracefully in find_related and learn_topic — skip/None, never a
    TypeError
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from conftest import insert_doc

from result import Ok
from storage import Storage


def _raising_conn() -> None:
    raise sqlite3.OperationalError("disk I/O error")


SOURCE_TITLE = "FastAPI Dependencies"
SOURCE_CONTENT = "FastAPI dependency injection with Depends and parameters"


@pytest.fixture
def related_storage(tmp_storage: Storage):
    """Small fixture: source doc + same-collection, cross-collection and zero-overlap neighbours."""
    tmp_storage.bulk_insert_raw("guide", "fastapi-deps.md", SOURCE_CONTENT, SOURCE_TITLE)
    tmp_storage.bulk_insert_raw("guide", "fastapi-params.md", "Path and query parameters in FastAPI", "FastAPI Parameters")
    tmp_storage.bulk_insert_raw("guide", "gardening.md", "Tomatoes need sun and water", "Tomatoes and Sun")
    tmp_storage.bulk_insert_raw("notes", "deps-notes.md", "Notes on dependency injection in FastAPI with Depends", "FastAPI Dependencies Notes")
    with patch("server._get_storage", return_value=tmp_storage):
        yield tmp_storage


# ── result shape: {source, related[]} + relation breakdown ──────────────────


async def test_related_result_shape(related_storage: Storage) -> None:
    from server import kb_related

    result = await kb_related("guide", "fastapi-deps.md")

    assert set(result.keys()) == {"source", "related"}
    src = result["source"]
    assert src["collection"] == "guide"
    assert src["file_path"] == "fastapi-deps.md"
    assert src["title"] == SOURCE_TITLE

    for item in result["related"]:
        assert {"collection", "file_path", "chunk_index", "title", "score", "relation"} <= set(item.keys()), item.keys()
        rel = item["relation"]
        assert set(rel.keys()) == {"tag_overlap", "same_collection", "content_similarity"}, rel
        assert 0.0 <= item["score"] <= 1.0
        assert isinstance(rel["same_collection"], bool)

    scores = [i["score"] for i in result["related"]]
    assert scores == sorted(scores, reverse=True)


# ── scoring signals + documented formula ────────────────────────────────────


async def test_related_scoring_signals(related_storage: Storage) -> None:
    from server import kb_related

    result = await kb_related("guide", "fastapi-deps.md", limit=10)
    by_path = {i["file_path"]: i for i in result["related"]}
    assert set(by_path) == {"fastapi-params.md", "deps-notes.md"}

    notes = by_path["deps-notes.md"]
    params = by_path["fastapi-params.md"]

    # GROUNDING: no tag column in the schema -> tag_overlap is title-term overlap.
    # Source title "FastAPI Dependencies" (2 terms):
    #   deps-notes title shares both -> 1.0, params shares one -> 0.5
    assert notes["relation"]["tag_overlap"] == pytest.approx(1.0)
    assert params["relation"]["tag_overlap"] == pytest.approx(0.5)

    # collection proximity
    assert notes["relation"]["same_collection"] is False
    assert params["relation"]["same_collection"] is True

    # content similarity reuses lexical_relevance over the source content
    assert notes["relation"]["content_similarity"] > params["relation"]["content_similarity"] > 0.0

    # documented formula: score = 0.4*tag_overlap + 0.2*same_collection + 0.4*content_similarity
    for item in result["related"]:
        rel = item["relation"]
        expected = 0.4 * rel["tag_overlap"] + 0.2 * (1.0 if rel["same_collection"] else 0.0) + 0.4 * rel["content_similarity"]
        assert item["score"] == pytest.approx(expected, abs=1e-3), item

    # cross-collection title match wins over the bare same-collection proximity point
    assert [i["file_path"] for i in result["related"]] == ["deps-notes.md", "fastapi-params.md"]


# ── per-document aggregation (multi-chunk candidates appear once) ───────────


async def test_related_aggregates_multichunk_candidates(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "src.md", SOURCE_CONTENT, SOURCE_TITLE)
    insert_doc(
        tmp_storage, "notes", "multi.md", "Notes on dependency injection in FastAPI with Depends", "Multi Notes", chunk_index=0, total_chunks=2
    )
    insert_doc(tmp_storage, "notes", "multi.md", "Second chunk about dependency injection", "Multi Notes", chunk_index=1, total_chunks=2)
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_related

        result = await kb_related("guide", "src.md")

    paths = [i["file_path"] for i in result["related"]]
    assert paths.count("multi.md") == 1, paths
    multi = next(i for i in result["related"] if i["file_path"] == "multi.md")
    assert multi["chunk_index"] == 0  # first chunk of the aggregated candidate document


# ── chunk_index selects the source chunk ────────────────────────────────────


async def test_related_source_chunk_selection(tmp_storage: Storage) -> None:
    insert_doc(tmp_storage, "guide", "multi.md", SOURCE_CONTENT, "Multi", chunk_index=0, total_chunks=2)
    insert_doc(tmp_storage, "guide", "multi.md", "Totally different subject: tomatoes and sun", "Multi", chunk_index=1, total_chunks=2)
    tmp_storage.bulk_insert_raw("guide", "deps.md", "More about dependency injection and Depends", "Dependency Notes")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_related

        whole = await kb_related("guide", "multi.md")
        chunk1 = await kb_related("guide", "multi.md", chunk_index=1)
        missing = await kb_related("guide", "multi.md", chunk_index=9)

    assert chunk1["source"]["chunk_index"] == 1
    assert chunk1["source"]["chunks"] == 2

    # chunk 1 is about tomatoes -> deps.md has no lexical signal at all and must be dropped
    # (same_collection alone is not a relation)
    assert chunk1["related"] == []

    # whole-document comparison includes chunk 0 -> content similarity kicks in
    dep_whole = next(i for i in whole["related"] if i["file_path"] == "deps.md")
    assert dep_whole["relation"]["content_similarity"] > 0.0

    assert "error" in missing, missing


# ── zero-signal candidates are dropped (same_collection alone is not enough) ─


async def test_related_drops_zero_signal_junk(related_storage: Storage) -> None:
    from server import kb_related

    result = await kb_related("guide", "fastapi-deps.md")

    # gardening.md shares the collection and nothing else: tag_overlap == 0,
    # content_similarity == 0 -> not a relation, must not be returned
    assert "gardening.md" not in {i["file_path"] for i in result["related"]}


# ── long sources still match on their subject (whole-document sampling) ─────


async def test_related_long_source_matches_subject_at_end(tmp_storage: Storage) -> None:
    # Short filler words (5 chars): under the old char-offset sampling the subject
    # could slip into a sampling window by accident — with short filler it cannot.
    filler = " ".join(f"w{i:04d}" for i in range(100))
    assert len(set(filler.split())) > 24  # more than _query_terms' default cap
    subject = "Our caching strategy keeps hot keys in Redis with a layered eviction policy."
    tmp_storage.bulk_insert_raw("guide", "long.md", filler + "\n" + subject, "Long Document")
    tmp_storage.bulk_insert_raw("guide", "caching.md", "Caching strategy notes for Redis keys", "Caching Notes")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_related

        result = await kb_related("guide", "long.md")

    caching = next(i for i in result["related"] if i["file_path"] == "caching.md")
    # the subject lives at the END of the content, far past any head/middle window:
    # word-stream sampling must still see it via the tail window
    assert caching["relation"]["content_similarity"] > 0
    assert caching["relation"]["tag_overlap"] == 0.0  # isolated content signal


# ── round 3 anchor: word-boundary sampling (no mid-word cuts) ───────────────


def test_related_terms_are_whole_words() -> None:
    """Regression: the old implementation sliced content BY CHARACTERS and cut words
    mid-word ("dependency" -> "depen"+"dency"), polluting terms and dropping the
    match even on short documents."""
    from scoring import lexical_relevance
    from storage import _related_source_terms

    terms = _related_source_terms("", "FastAPI dependency injection with Depends")

    # whole words only — none of the old mid-word fragments survive
    assert terms == ["fastapi", "dependency", "injection", "with", "depends"]

    candidate_title, candidate_content = "Notes about dependency injection", "Notes about dependency injection"
    assert lexical_relevance(terms, candidate_title, candidate_content) > 0.0


# ── round 3 anchor: mid-document subject still matches ──────────────────────


async def test_related_mid_document_subject_matches(tmp_storage: Storage) -> None:
    # Subject placed in the MIDDLE of the word stream, past the 12-token cap the old
    # per-char-segment sampler took from its middle third.
    filler_before = " ".join(f"w{i:04d}" for i in range(60))
    filler_after = " ".join(f"w{i:04d}" for i in range(60, 120))
    subject = "Our caching strategy keeps hot keys in Redis with a layered eviction policy."
    tmp_storage.bulk_insert_raw("guide", "long.md", filler_before + "\n" + subject + "\n" + filler_after, "Long Document")
    tmp_storage.bulk_insert_raw("guide", "caching.md", "Caching strategy notes for Redis keys", "Caching Notes")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_related

        result = await kb_related("guide", "long.md")

    caching = next(i for i in result["related"] if i["file_path"] == "caching.md")
    assert caching["relation"]["content_similarity"] > 0


# ── round 3 anchor: the zero-signal gate keeps content-only relations ────────


async def test_related_keeps_content_only_cross_collection_match(related_storage: Storage) -> None:
    """The M3 gate (skip when tag_overlap == 0 AND content_similarity == 0) must not
    drop cross-collection documents with a different title but shared subject wording."""
    related_storage.bulk_insert_raw("guide", "standup.md", "FastAPI dependency injection with Depends", "Team Standup Minutes")
    related_storage.bulk_insert_raw("notes", "retro.md", "Notes about dependency injection", "Sprint Retrospective")
    with patch("server._get_storage", return_value=related_storage):
        from server import kb_related

        result = await kb_related("guide", "standup.md")

    retro = next((i for i in result["related"] if i["file_path"] == "retro.md"), None)
    assert retro is not None, result
    assert retro["relation"]["tag_overlap"] == 0.0  # titles share nothing
    assert retro["relation"]["content_similarity"] > 0.0  # shared subject wording


# ── round 3 anchor: access log keeps the caller's chunk_index (N6) ───────────


async def test_related_access_log_chunk_index(tmp_storage: Storage) -> None:
    insert_doc(tmp_storage, "guide", "multi.md", SOURCE_CONTENT, "Multi", chunk_index=0, total_chunks=2)
    insert_doc(tmp_storage, "guide", "multi.md", "Second chunk about dependency injection", "Multi", chunk_index=1, total_chunks=2)
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_related

        await kb_related("guide", "multi.md", chunk_index=1)
        assert tmp_storage._recent_docs[0]["chunk_index"] == 1

        await kb_related("guide", "multi.md")
        assert tmp_storage._recent_docs[0]["chunk_index"] is None


# ── DB errors surface as Err / error dicts, never raise ─────────────────────


async def test_related_db_error_is_err_not_raise(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "src.md", SOURCE_CONTENT, SOURCE_TITLE)
    with patch.object(tmp_storage, "_get_conn", _raising_conn), patch("server._get_storage", return_value=tmp_storage):
        result = tmp_storage.find_related("guide", "src.md")
        assert result.is_err
        assert "disk I/O error" in str(getattr(result, "error", ""))

        from server import kb_related

        tool = await kb_related("guide", "src.md")
    assert "error" in tool and "disk I/O error" in tool["error"]


# ── empty results ───────────────────────────────────────────────────────────


async def test_related_no_neighbours_is_empty(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "alone.md", SOURCE_CONTENT, SOURCE_TITLE)
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_related

        result = await kb_related("guide", "alone.md")

    assert result["related"] == []
    assert result["source"]["file_path"] == "alone.md"


# ── bad input validation ────────────────────────────────────────────────────


async def test_related_bad_input_validation(related_storage: Storage) -> None:
    from server import kb_related

    unknown = await kb_related("guide", "nope.md")
    assert "error" in unknown and "not found" in unknown["error"].lower()

    bad_collection = await kb_related("bad name!", "fastapi-deps.md")
    assert "error" in bad_collection

    bad_limit = await kb_related("guide", "fastapi-deps.md", limit=0)
    assert "error" in bad_limit


# ── explain breakdown + grounding marker ────────────────────────────────────


async def test_related_explain_breakdown(related_storage: Storage) -> None:
    from server import kb_related

    result = await kb_related("guide", "fastapi-deps.md", explain=True)

    for item in result["related"]:
        ex = item.get("explain")
        assert ex is not None, item
        assert {"tag_overlap", "same_collection", "content_similarity", "final_score", "tag_overlap_source", "weights"} <= set(ex.keys()), ex
        # documented substitution: no tag column -> title terms stand in for tags
        assert ex["tag_overlap_source"] == "title_terms"
        assert ex["weights"] == {"tag_overlap": 0.4, "same_collection": 0.2, "content_similarity": 0.4}
        assert ex["final_score"] == pytest.approx(item["score"], abs=1e-3)


# ── ROUND 4 anchor (M7): subject visible at ANY document position ────────────

SUBJECT_SENTENCE = "Our caching strategy keeps hot keys in Redis with a layered eviction policy."
CAND_TITLE, CAND_CONTENT = "Caching Notes", "Caching strategy notes for Redis keys"


def _doc_with_subject_at(total_words: int, pct: int) -> str:
    """A `total_words`-word document with the subject sentence starting at `pct`% of the word stream."""
    filler = [f"w{i:04d}" for i in range(total_words)]
    at = total_words * pct // 100
    return " ".join(filler[:at] + SUBJECT_SENTENCE.split() + filler[at:])


@pytest.mark.parametrize("pct", [20, 70])
async def test_related_subject_visible_at_20_and_70_percent(tmp_storage: Storage, pct: int) -> None:
    """M7 blocker: the old 48-term head/middle/tail positional sampling left both
    positions inside invisible windows (~60% of positions had no hole-free coverage)."""
    tmp_storage.bulk_insert_raw("guide", "long.md", _doc_with_subject_at(200, pct), "Long Document")
    tmp_storage.bulk_insert_raw("guide", "caching.md", CAND_CONTENT, CAND_TITLE)

    result = tmp_storage.find_related("guide", "long.md").unwrap()
    caching = next(i for i in result["related"] if i["file_path"] == "caching.md")
    assert caching["relation"]["content_similarity"] > 0


@pytest.mark.parametrize("pct", list(range(0, 101, 10)))
async def test_related_position_sweep_zero_holes(tmp_storage: Storage, pct: int) -> None:
    """Full sweep of a 200-word document: subject at 0/10/.../100% must ALL match.
    Codifies the verifier's position table with zero holes; fails on any positional sampler."""
    tmp_storage.bulk_insert_raw("guide", "long.md", _doc_with_subject_at(200, pct), "Long Document")
    tmp_storage.bulk_insert_raw("guide", "caching.md", CAND_CONTENT, CAND_TITLE)

    result = tmp_storage.find_related("guide", "long.md").unwrap()
    caching = next(i for i in result["related"] if i["file_path"] == "caching.md")
    assert caching["relation"]["content_similarity"] > 0


# ── ROUND 4 anchor (M9): chunk_index = NULL degrades gracefully ──────────────


async def test_related_null_chunk_index_row_never_raises(tmp_storage: Storage) -> None:
    """A documents row with chunk_index = NULL is reachable only via raw SQL, but
    find_related / learn_topic must skip or tolerate it — never raise TypeError."""
    conn = tmp_storage._get_conn()
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title, chunk_index) VALUES ('guide', 'src.md', ?, 'Src', NULL)",
        (SOURCE_CONTENT,),
    )
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title, chunk_index) VALUES ('guide', 'rel.md', 'More about dependency injection and Depends', 'Dependency Notes', NULL)"
    )
    conn.commit()

    related = tmp_storage.find_related("guide", "src.md")
    assert not related.is_err or "chunk" not in str(getattr(related, "error", "")).lower()
    assert isinstance(related, Ok) and isinstance(related.value["related"], list)

    learned = tmp_storage.learn_topic("dependency injection")
    assert not learned.is_err or "chunk" not in str(getattr(learned, "error", "")).lower()
    assert isinstance(learned, Ok) and isinstance(learned.value["key_documents"], list)
