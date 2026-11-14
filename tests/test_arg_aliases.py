"""Anchors for native pydantic argument aliases on hot MCP tools (Card BH-2b).

These tests MUST FAIL on current code (alias.py is dead code; the tools accept
only canonical parameter names, so e.g. kb_ask called with {"query": ...}
raises ToolError "... query Field required") and PASS after the hot parameters
declare validation_alias=AliasChoices(...) natively:

- kb_ask {"query"/"q"/"userQuery"/"prompt"} resolve to question
- kb_search {"q"/"text"/"search_query"} resolve to query
- kb_get {"filePath"/"documentId"/"doc_path"} resolve to file_path
- tools/list schema purity: aliases never leak into the LLM-visible schema
- canonical parameter names keep working unchanged
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from storage import Storage

ANSWERABLE_QUESTION = "how to use Depends with parameters in FastAPI?"


async def _call(tool: str, args: dict) -> list:
    """Call a tool through the real MCP layer and return parsed JSON payloads.

    FastMCP may return the result as a dict, a list of text content blocks
    (one JSON value per block), or a (content, structured) tuple.
    """
    from server import mcp

    result = await mcp.call_tool(tool, args)
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return [result]
    return [json.loads(item.text) for item in result]


async def _tool_schema(name: str) -> dict:
    from server import mcp

    tools = await mcp.list_tools()
    return next(t.inputSchema for t in tools if t.name == name)


@pytest.fixture
def hot_storage(tmp_storage: Storage):
    """Two FastAPI docs, enough for kb_search / kb_ask / kb_get anchors.

    Content mirrors the proven test_kb_ask fixture: the question's keywords
    match both docs directly, so kb_ask's chunk loop never sees expansion-only
    rows (those hit a pre-existing KeyError unrelated to argument aliases).
    """
    tmp_storage.bulk_insert_raw("fastapi", "deps.md", "FastAPI dependency injection with Depends and parameters", "Dependencies")
    tmp_storage.bulk_insert_raw("fastapi", "params.md", "Path and query parameters in FastAPI", "Parameters")
    with patch("server._get_storage", return_value=tmp_storage):
        yield tmp_storage


# ── alias resolution (MUST FAIL before, PASS after) ─────────────────────────


@pytest.mark.parametrize("alias", ["query", "q", "userQuery", "prompt"])
async def test_kb_ask_alias_resolves_to_question(hot_storage: Storage, alias: str) -> None:
    answers = await _call("kb_ask", {alias: ANSWERABLE_QUESTION})
    assert len(answers) == 1
    assert answers[0]["chunks"], answers[0]
    assert answers[0]["citations"], answers[0]


@pytest.mark.parametrize("alias", ["q", "text", "search_query"])
async def test_kb_search_alias_resolves_to_query(hot_storage: Storage, alias: str) -> None:
    results = await _call("kb_search", {alias: "dependency injection"})
    assert results
    assert any(r["file_path"] == "deps.md" for r in results), results


@pytest.mark.parametrize("alias", ["filePath", "documentId", "doc_path"])
async def test_kb_get_alias_resolves_to_file_path(hot_storage: Storage, alias: str) -> None:
    docs = await _call("kb_get", {alias: "fastapi/deps.md"})
    assert len(docs) == 1
    assert docs[0]["content"] == "FastAPI dependency injection with Depends and parameters"


# ── schema purity: aliases must not leak into tools/list ────────────────────


async def test_kb_ask_schema_exposes_question_only() -> None:
    schema = await _tool_schema("kb_ask")
    assert "question" in schema["properties"]
    assert not {"query", "q", "userQuery", "prompt"} & schema["properties"].keys()
    assert schema["required"] == ["question"]


async def test_kb_search_schema_exposes_query_only() -> None:
    schema = await _tool_schema("kb_search")
    assert "query" in schema["properties"]
    assert not {"q", "text", "search_query"} & schema["properties"].keys()
    assert schema["required"] == ["query"]


async def test_kb_get_schema_exposes_file_path_only() -> None:
    schema = await _tool_schema("kb_get")
    assert "file_path" in schema["properties"]
    assert not {"filePath", "documentId", "doc_path"} & schema["properties"].keys()
    assert schema["required"] == ["file_path"]


# ── canonical names must keep working unchanged ─────────────────────────────


async def test_canonical_names_still_work(hot_storage: Storage) -> None:
    results = await _call("kb_search", {"query": "dependency injection"})
    assert any(r["file_path"] == "deps.md" for r in results), results

    docs = await _call("kb_get", {"file_path": "fastapi/deps.md"})
    assert docs[0]["content"] == "FastAPI dependency injection with Depends and parameters"

    answers = await _call("kb_ask", {"question": ANSWERABLE_QUESTION})
    assert answers[0]["chunks"], answers[0]
