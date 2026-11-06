"""Pinning tests for P0-5 MCP get accepts collection/file_path.

These tests MUST FAIL before the fix (kb_get only accepts bare file_path and will
look up the bare path in every collection) and PASS after kb_get is changed to
accept the 'collection/file_path' form like the CLI does.

The MCP tool signature is not changed for testing: the test patches the server's
storage singleton to a temporary Storage so kb_get runs against an isolated DB.
"""

from __future__ import annotations

import pytest

from server import _get_storage, kb_get
from storage import Storage


@pytest.mark.asyncio
async def test_mcp_get_with_collection(tmp_path: pytest.Path) -> None:
    """kb_get accepts collection/file_path and returns the scoped document."""

    tmp_dir = tmp_path
    storage = Storage(tmp_dir)
    storage.bulk_insert(
        [
            {
                "collection": "c",
                "path": "a.md",
                "content": "Collection C document A",
                "title": "A",
            },
        ]
    )

    original = _get_storage()
    try:
        # Patch the server singleton to use our isolated storage.
        import server as _server

        _server._storage = storage
        result = await kb_get("c/a.md")
    finally:
        _server._storage = original
        storage.close()

    assert "error" not in result, result
    assert result.get("title") == "A", result
    assert result.get("content") == "Collection C document A", result


@pytest.mark.asyncio
async def test_mcp_get_without_collection(tmp_path: pytest.Path) -> None:
    """kb_get still works for a bare file_path that is unique across collections."""

    tmp_dir = tmp_path
    storage = Storage(tmp_dir)
    storage.bulk_insert(
        [
            {
                "collection": "c",
                "path": "b.md",
                "content": "Bare path document",
                "title": "B",
            },
        ]
    )

    original = _get_storage()
    try:
        import server as _server

        _server._storage = storage
        result = await kb_get("b.md")
    finally:
        _server._storage = original
        storage.close()

    assert "error" not in result, result
    assert result.get("title") == "B", result
