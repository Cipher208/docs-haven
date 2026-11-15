"""Anchors for Card P2-2: `kb_index` text mode routing.

MUST FAIL at the pre-card HEAD (fce5ac4) and PASS after the card lands:

- a "text://<title>" source is rejected (unsupported source kind)
- type="text" is rejected (unknown source type)
- nothing routes to Storage.add_text (which does not exist yet)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from storage import Storage

NOTE_BODY = "Dependency injection notes for FastAPI with Depends"


@pytest.fixture
def kb_storage(tmp_path):
    storage = Storage(tmp_path / "kb-data")
    yield storage
    storage.close()


async def _mcp_call(tool: str, args: dict) -> list:
    """Call a tool through the real MCP layer and return parsed JSON payloads."""
    from server import mcp

    result = await mcp.call_tool(tool, args)
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return [result]
    return [json.loads(item.text) for item in result]


class TestTextRouting:
    async def test_type_text_with_content_ok(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("My Note", type="text", content=NOTE_BODY)
        assert result.get("title") == "My Note", result
        assert result["collection"] == "notes"
        assert result["file_path"].startswith("text://"), result

        found = kb_storage.search("dependency injection")
        assert any(r["file_path"] == result["file_path"] for r in found.value), found.value

    async def test_text_scheme_title_honored(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("text://Quick%20Tip", content=NOTE_BODY)
        assert result.get("title") == "Quick Tip", result

    async def test_text_scheme_empty_remainder_derives_title(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("text://", content="Derived title line\nbody text")
        assert result.get("title") == "Derived title line", result

    async def test_missing_content_is_err(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("text://No Content")
        assert "error" in result, result
        assert "text sources require content" in result["error"]

    async def test_missing_content_on_type_text_is_err(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("No Content", type="text")
        assert "error" in result, result
        assert "text sources require content" in result["error"]

    async def test_content_with_path_source_is_err(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        folder = tmp_path / "notes"
        folder.mkdir()
        (folder / "a.md").write_text("doc body")
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(str(folder), content="some text")
        assert "error" in result, result
        assert "content" in result["error"].lower()
        assert "notes" not in kb_storage._load_config().get("repos", {})

    async def test_content_with_github_source_is_err(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("https://github.com/test/repo", content="some text")
        assert "error" in result, result
        assert "content" in result["error"].lower()

    async def test_collection_override_applies_to_text(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("text://Tip", content=NOTE_BODY, collection="inbox")
        assert result.get("collection") == "inbox", result

    async def test_unknown_type_error_lists_text_too(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("x", type="bogus")
        assert "error" in result, result
        assert "text" in result["error"].lower()


class TestTextMcpLayer:
    async def test_kb_index_text_via_mcp(self, kb_storage: Storage) -> None:
        with patch("server._get_storage", return_value=kb_storage):
            payloads = await _mcp_call("kb_index", {"source": "text://Via MCP", "content": NOTE_BODY})
        assert payloads[0].get("title") == "Via MCP", payloads


class TestTextPathRouting:
    """F-1 fix anchors: the returned `text://` file_path round-trips through
    every lookup heuristic (MCP kb_get, CLI get/delete, retrieval counting)
    instead of being split into collection "text:" + path "/x".

    MUST FAIL before the fix ("Document not found: /x" / silent no-op) and
    PASS after. The plain collection/relative/path.md anchor is a preservation
    guard: it passes before AND after (the existing heuristic is kept).
    """

    async def test_add_text_roundtrip_kb_get_via_mcp(self, kb_storage: Storage) -> None:
        added = kb_storage.add_text(NOTE_BODY, title="Round Trip").unwrap()
        with patch("server._get_storage", return_value=kb_storage):
            payloads = await _mcp_call("kb_get", {"file_path": added["file_path"]})
        assert payloads[0].get("content") == NOTE_BODY, payloads

    def test_add_text_roundtrip_cli_get(self, kb_storage: Storage, capsys) -> None:
        import argparse

        import cli as _cli

        added = kb_storage.add_text(NOTE_BODY, title="Round Trip").unwrap()
        _cli._storage = kb_storage
        try:
            _cli.cmd_get(argparse.Namespace(file_path=added["file_path"]))
        finally:
            _cli._storage = None
        assert NOTE_BODY in capsys.readouterr().out

    def test_add_text_roundtrip_cli_delete(self, kb_storage: Storage, capsys) -> None:
        import argparse

        import cli as _cli

        added = kb_storage.add_text(NOTE_BODY, title="Round Trip").unwrap()
        _cli._storage = kb_storage
        try:
            _cli.cmd_delete(argparse.Namespace(file_path=added["file_path"]))
        finally:
            _cli._storage = None
        assert "Deleted" in capsys.readouterr().out
        assert kb_storage.get(added["file_path"]).is_err  # note is gone

    def test_increment_retrieval_bare_text_path(self, kb_storage: Storage) -> None:
        added = kb_storage.add_text(NOTE_BODY, title="Round Trip").unwrap()
        kb_storage._increment_retrieval([added["file_path"]])
        row = kb_storage._get_conn().execute("SELECT retrieval_count FROM documents WHERE file_path = ?", (added["file_path"],)).fetchone()
        assert row["retrieval_count"] == 1

    async def test_plain_collection_relative_lookup_still_works(self, kb_storage: Storage) -> None:
        # existing heuristic preserved: first segment without ':' is a collection
        kb_storage.bulk_insert_raw("collection", "relative/path.md", "plain body text", "Plain").unwrap()
        with patch("server._get_storage", return_value=kb_storage):
            payloads = await _mcp_call("kb_get", {"file_path": "collection/relative/path.md"})
        assert payloads[0].get("content") == "plain body text", payloads
        # prefixed text lookups split the same way
        added = kb_storage.add_text(NOTE_BODY, title="Round Trip").unwrap()
        with patch("server._get_storage", return_value=kb_storage):
            payloads = await _mcp_call("kb_get", {"file_path": f"notes/{added['file_path']}"})
        assert payloads[0].get("content") == NOTE_BODY, payloads

    def test_resolve_row_bare_text_path_keeps_identity(self) -> None:
        # F-1, storage._resolve_row call site: a vector/fts row whose path key
        # is a bare scheme-bearing file_path must not be split into collection
        # "text:" + file_path "/x" — the row's own collection stays the scope.
        from storage import resolve_chunk

        chunk = resolve_chunk({"path": "text://my-note-abc123", "collection": "notes", "title": "T", "content": "C", "chunk": 2})
        assert chunk is not None
        assert chunk["collection"] == "notes", chunk
        assert chunk["file_path"] == "text://my-note-abc123", chunk
        assert chunk["chunk_index"] == 2, chunk
