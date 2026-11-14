"""Anchor tests for kb_learn (FUTURE_PLAN P1 #7, DH-3 item 7).

These tests MUST FAIL on current code (no Storage.learn_topic / server.kb_learn)
and PASS after the implementation. Covered:

- result shape {topic, collections, key_documents, learning_path} with
  learning_path steps {step, document, level, why} numbered sequentially
- learning path grouped beginner -> advanced (deterministic, no LLM calls)
- GROUNDING: complexity level is defined from real features only —
  title markers ("introduction/basics/..." vs "internals/advanced/...") win;
  otherwise a code-chunk ratio + document length score picks the band.
  The rule is documented in the storage docstrings and asserted here.
- topic gating: unrelated documents never appear (lexical_relevance == 0)
- collection filter, empty results, bad input validation
- C6: a topic query is not a viewing — kb_learn records no accesses
- DB errors (sqlite3.Error) surface as Err / error dicts, never raise
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from storage import Storage


def _raising_conn() -> None:
    raise sqlite3.OperationalError("disk I/O error")


INTRO_CONTENT = "FastAPI basics: install the framework and create your first application step by step."
QUICK_CONTENT = "Short note about FastAPI routing."
ASYNC_CONTENT = (
    "FastAPI runs async endpoints on the event loop without blocking. " * 18
    + "```python\nasync def handler():\n    return await db.fetch()\n```\n"
    + "Extend this guide with practical patterns for testing and deployment. " * 8
)
INTERNALS_CONTENT = "FastAPI dependency injection internals and routing layers explained.\n```python\nfrom fastapi import Depends\n\nasync def get_db():\n    ...\n```"
ROUTING_CONTENT = "FastAPI routing notes. \n```python\n" + "x = 1\n" * 300 + "```"


@pytest.fixture
def learn_storage(tmp_storage: Storage):
    """Topic 'FastAPI' with docs at every complexity band plus one off-topic doc."""
    tmp_storage.bulk_insert_raw("guide", "intro-fastapi.md", INTRO_CONTENT, "Introduction to FastAPI")
    tmp_storage.bulk_insert_raw("guide", "fastapi-quick.md", QUICK_CONTENT, "FastAPI Notes")
    tmp_storage.bulk_insert_raw("guide", "async-in-fastapi.md", ASYNC_CONTENT, "Async in FastAPI")
    tmp_storage.bulk_insert_raw("guide", "fastapi-internals.md", INTERNALS_CONTENT, "FastAPI Internals")
    tmp_storage.bulk_insert_raw("guide", "routing-notes.md", ROUTING_CONTENT, "Routing Notes")
    tmp_storage.bulk_insert_raw("guide", "gardening.md", "Tomatoes need sun and water", "Tomatoes and Sun")
    tmp_storage.bulk_insert_raw("other", "fastapi-ref.md", "Complete FastAPI reference for routes and handlers", "FastAPI Reference")
    with patch("server._get_storage", return_value=tmp_storage):
        yield tmp_storage


# ── result shape: {topic, collections, key_documents, learning_path} ────────


async def test_learn_result_shape(learn_storage: Storage) -> None:
    from server import kb_learn

    result = await kb_learn("FastAPI")

    assert set(result.keys()) == {"topic", "collections", "key_documents", "learning_path"}
    assert result["topic"] == "FastAPI"

    by_coll = {c["collection"]: c for c in result["collections"]}
    for entry in result["collections"]:
        assert {"collection", "documents", "matched_documents", "score"} <= set(entry.keys()), entry.keys()
    assert by_coll["guide"]["documents"] == 6  # including the off-topic doc
    assert by_coll["guide"]["matched_documents"] == 5

    for doc in result["key_documents"]:
        assert {"collection", "file_path", "title", "level", "score"} <= set(doc.keys()), doc.keys()
        assert doc["level"] in ("beginner", "intermediate", "advanced")

    for i, step in enumerate(result["learning_path"], start=1):
        assert {"step", "document", "level", "why"} <= set(step.keys()), step.keys()
        assert step["step"] == i
        assert step["why"]


# ── learning path grouped beginner -> advanced ──────────────────────────────


async def test_learn_learning_path_beginner_to_advanced(learn_storage: Storage) -> None:
    from server import kb_learn

    result = await kb_learn("FastAPI", limit=20)
    path = result["learning_path"]

    # deterministic, documented order: intro -> quick note -> medium guide -> advanced docs
    assert [s["document"] for s in path] == [
        "guide/intro-fastapi.md",
        "guide/fastapi-quick.md",
        "guide/async-in-fastapi.md",
        "guide/fastapi-internals.md",
        "other/fastapi-ref.md",
        "guide/routing-notes.md",
    ]

    rank = {"beginner": 0, "intermediate": 1, "advanced": 2}
    assert [rank[s["level"]] for s in path] == sorted(rank[s["level"]] for s in path)
    assert [s["document"] for s in path if s["level"] == "intermediate"] == ["guide/async-in-fastapi.md"]

    assert path[0]["why"].lower().startswith("start here")
    assert any("deep dive" in s["why"].lower() for s in path if s["level"] == "advanced")


# ── documented complexity rule: title markers, then code ratio + length ─────


async def test_learn_level_rule(learn_storage: Storage) -> None:
    from server import kb_learn

    result = await kb_learn("FastAPI", limit=20)
    levels = {d["file_path"]: d["level"] for d in result["key_documents"]}

    assert levels["intro-fastapi.md"] == "beginner"  # title marker "introduction"
    assert levels["fastapi-internals.md"] == "advanced"  # title marker "internals"
    assert levels["fastapi-quick.md"] == "beginner"  # fallback: short prose, no code
    assert levels["async-in-fastapi.md"] == "intermediate"  # fallback: long prose, little code
    assert levels["routing-notes.md"] == "advanced"  # fallback: high code-chunk ratio


async def test_learn_level_fallback_ignores_markers_absent(tmp_storage: Storage) -> None:
    """A doc with no title markers is banded purely by code ratio + length."""
    tmp_storage.bulk_insert_raw("guide", "tiny.md", "A tiny note about FastAPI setup.", "Setup Note")
    heavy_code = "FastAPI routing internals by example.\n```python\n" + "x = compute()\n" * 200 + "```"
    tmp_storage.bulk_insert_raw("guide", "code-heavy.md", heavy_code, "Routing Examples")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_learn

        result = await kb_learn("FastAPI", limit=20)
    levels = {d["file_path"]: d["level"] for d in result["key_documents"]}
    assert levels["tiny.md"] == "beginner"
    assert levels["code-heavy.md"] == "advanced"


# ── topic gating: unrelated documents never appear ──────────────────────────


async def test_learn_topic_gating(learn_storage: Storage) -> None:
    from server import kb_learn

    result = await kb_learn("FastAPI", limit=20)

    files = {d["file_path"] for d in result["key_documents"]}
    assert "gardening.md" not in files
    path_docs = {s["document"] for s in result["learning_path"]}
    assert "guide/gardening.md" not in path_docs
    assert set(c["collection"] for c in result["collections"]) == {"guide", "other"}


async def test_learn_collection_filter(learn_storage: Storage) -> None:
    from server import kb_learn

    result = await kb_learn("FastAPI", collection="guide", limit=20)

    assert [c["collection"] for c in result["collections"]] == ["guide"]
    assert "other/fastapi-ref.md" not in {s["document"] for s in result["learning_path"]}


# ── empty results + bad input validation ────────────────────────────────────


async def test_learn_no_results(tmp_storage: Storage) -> None:
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_learn

        result = await kb_learn("xyzzy nonexistent topic")
    assert result == {"topic": "xyzzy nonexistent topic", "collections": [], "key_documents": [], "learning_path": []}


async def test_learn_records_no_access(tmp_storage: Storage) -> None:
    """C6: a topic query is not a viewing — kb_learn must not pollute the access log."""
    tmp_storage.bulk_insert_raw("guide", "a.md", "FastAPI basics intro", "Introduction to FastAPI")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_learn, kb_recent

        result = await kb_learn("FastAPI")
        recent = await kb_recent()

    assert result["key_documents"]  # the topic matched documents
    assert recent["documents"] == []  # exploring recorded nothing


# ── DB errors surface as Err / error dicts, never raise ─────────────────────


async def test_learn_db_error_is_err_not_raise(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "FastAPI basics", "A")
    with patch.object(tmp_storage, "_get_conn", _raising_conn), patch("server._get_storage", return_value=tmp_storage):
        result = tmp_storage.learn_topic("FastAPI")
        assert result.is_err
        assert "disk I/O error" in str(getattr(result, "error", ""))

        from server import kb_learn

        tool = await kb_learn("FastAPI")
    assert "error" in tool and "disk I/O error" in tool["error"]


async def test_learn_bad_input_validation(learn_storage: Storage) -> None:
    from server import kb_learn

    empty = await kb_learn("   ")
    assert "error" in empty

    no_keywords = await kb_learn("!!!")
    assert "error" in no_keywords

    bad_collection = await kb_learn("FastAPI", collection="bad name!")
    assert "error" in bad_collection

    bad_limit = await kb_learn("FastAPI", limit=0)
    assert "error" in bad_limit
