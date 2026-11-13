"""Anchor tests for kb_ask (FUTURE_PLAN P1 #1+#4, DH-2 items 3+4).

These tests MUST FAIL on current code (server has no kb_ask tool) and PASS after
the implementation:
- NL question -> keyword extraction -> FTS5 search with expansion -> ranking
- LLM summary via the MCP host (sampling/createMessage) with inline citations
- result shape {answer, chunks, citations} + answer_mode flag
- citations {collection, file_path, chunk_index} link back to sources
- graceful degradation when the host cannot provide completions
  (answer="" + answer_mode="host_unavailable", never crashes)
- explain-style breakdown reuses scoring explain
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from result import Ok
from storage import Storage

CANNED_ANSWER = "Use Depends for dependency injection [1]. Parameters go in the signature [2]."


class _FakeSession:
    """Simulates an MCP host that can (or cannot) provide sampling completions."""

    def __init__(self, text: str | None = None, error: Exception | None = None):
        self.text = text
        self.error = error
        self.prompts: list[str] = []

    async def create_message(self, messages, *, max_tokens, system_prompt=None, **kwargs):
        self.prompts.append("\n".join(getattr(m.content, "text", "") for m in messages))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=SimpleNamespace(type="text", text=self.text), model="fake-model", role="assistant")


class _FakeCtx:
    def __init__(self, session):
        self.session = session


@pytest.fixture
def ask_storage(tmp_storage: Storage):
    """Small fixture: two FastAPI docs in one collection."""
    tmp_storage.bulk_insert_raw("fastapi", "dependencies.md", "FastAPI dependency injection with Depends and parameters", "Dependencies")
    tmp_storage.bulk_insert_raw("fastapi", "params.md", "Path and query parameters in FastAPI", "Parameters")
    with patch("server._get_storage", return_value=tmp_storage):
        yield tmp_storage


# ── result shape: {answer, chunks, citations} + answer_mode ──────────────────


async def test_kb_ask_answer_chunks_citations(ask_storage: Storage) -> None:
    from server import kb_ask

    session = _FakeSession(text=CANNED_ANSWER)
    result = await kb_ask("how to use Depends with parameters in FastAPI?", ctx=_FakeCtx(session))

    assert result["answer"] == CANNED_ANSWER
    assert result["answer_mode"] == "llm"
    assert len(result["chunks"]) == 2, [c["file_path"] for c in result["chunks"]]
    assert len(result["citations"]) == len(result["chunks"])

    for chunk in result["chunks"]:
        for key in ("collection", "file_path", "chunk_index", "title", "content", "score"):
            assert key in chunk, f"chunk missing {key}: {chunk.keys()}"

    # Prompt sent to the host must carry the question and numbered sources.
    assert len(session.prompts) == 1
    assert "how to use Depends" in session.prompts[0]
    assert "[1]" in session.prompts[0] and "[2]" in session.prompts[0]


async def test_kb_ask_citations_are_fetchable(ask_storage: Storage) -> None:
    from server import kb_ask

    result = await kb_ask("how to use Depends with parameters in FastAPI?", ctx=_FakeCtx(_FakeSession(text=CANNED_ANSWER)))
    for citation in result["citations"]:
        assert set(citation.keys()) == {"collection", "file_path", "chunk_index"}, citation
        got = ask_storage.get(citation["file_path"], chunk=citation["chunk_index"], collection=citation["collection"])
        assert isinstance(got, Ok), f"citation not fetchable: {citation} -> {got}"


# ── graceful degradation when the host cannot complete ───────────────────────


async def test_kb_ask_degrades_without_host(ask_storage: Storage) -> None:
    """No ctx at all: answer slot stays empty, flag says host_unavailable, chunks intact."""
    from server import kb_ask

    result = await kb_ask("how to use Depends with parameters in FastAPI?")

    assert result["answer"] == ""
    assert result["answer_mode"] == "host_unavailable"
    assert len(result["chunks"]) == 2
    assert len(result["citations"]) == 2
    # Prompt-ready slot: the assembled prompt is handed back so the caller can answer.
    assert "Sources" in result["prompt"]
    assert "[1]" in result["prompt"]


async def test_kb_ask_degrades_when_host_sampling_fails(ask_storage: Storage) -> None:
    """Host present but sampling unsupported/failed: never crash, same degraded shape."""
    from server import kb_ask

    ctx = _FakeCtx(_FakeSession(error=RuntimeError("sampling not supported")))
    result = await kb_ask("how to use Depends with parameters in FastAPI?", ctx=ctx)

    assert result["answer"] == ""
    assert result["answer_mode"] == "host_unavailable"
    assert len(result["chunks"]) == 2
    assert "prompt" in result


async def test_kb_ask_no_results(ask_storage: Storage) -> None:
    from server import kb_ask

    result = await kb_ask("xyzzy nonexistent topic", ctx=_FakeCtx(_FakeSession(text="unused")))
    assert result["answer"] == ""
    assert result["answer_mode"] == "no_results"
    assert result["chunks"] == []
    assert result["citations"] == []


# ── expansion + validation through the kb_ask path ───────────────────────────


async def test_kb_ask_uses_query_expansion(tmp_storage: Storage) -> None:
    """The doc contains only expansion terms ('authentication'), never 'auth'
    (porter-unrelated pair, so only the synonym expansion can bridge them)."""
    tmp_storage.bulk_insert_raw("sec", "guide.md", "Guide to authentication and authorization", "Security")
    with patch("server._get_storage", return_value=tmp_storage):
        from server import kb_ask

        result = await kb_ask("how to use auth?", ctx=_FakeCtx(_FakeSession(text="Answer [1].")))
    assert any(c["file_path"] == "guide.md" for c in result["citations"]), result["citations"]


async def test_kb_ask_empty_question_returns_error() -> None:
    from server import kb_ask

    result = await kb_ask("   ")
    assert "error" in result


# ── explain-style breakdown reuses scoring explain ───────────────────────────


async def test_kb_ask_explain_breakdown(ask_storage: Storage) -> None:
    from server import kb_ask

    result = await kb_ask("how to use Depends with parameters in FastAPI?", explain=True, ctx=_FakeCtx(_FakeSession(text=CANNED_ANSWER)))
    for chunk in result["chunks"]:
        explain = chunk.get("explain")
        assert explain is not None, f"no explain in chunk: {chunk}"
        assert "base_score" in explain and "final_score" in explain, explain


async def test_kb_ask_context_parameter_boosts(ask_storage: Storage) -> None:
    """The kb_ask search path exposes the context parameter (DH-2 item 2 wiring)."""
    ask_storage.bulk_insert_raw("other", "other.md", "Depends and parameters in FastAPI", "Other")
    with patch("server._get_storage", return_value=ask_storage):
        from server import kb_ask

        result = await kb_ask("how to use Depends with parameters in FastAPI?", context=["other"], explain=True, ctx=_FakeCtx(_FakeSession(text=CANNED_ANSWER)))
    by_coll = {c["collection"]: c for c in result["chunks"]}
    assert by_coll["other"]["explain"]["context_boost"] == pytest.approx(0.1), by_coll["other"]["explain"]
