"""Tests for MCP server tools."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_storage():
    """Create a mock storage for server tests."""
    with patch("server._get_storage") as mock:
        storage = MagicMock()
        storage.search.return_value = [{"score": 0.9, "collection": "test", "title": "Test", "content": "Content...", "path": "test/doc.md"}]
        storage.stats.return_value = {"total_documents": 10, "collections": 2, "repos": 1, "total_chunks": 50, "db_path": "", "db_size_kb": 1024}
        storage.list_collections.return_value = [{"name": "test", "count": 10}]
        mock.return_value = storage
        yield storage


@pytest.mark.asyncio
async def test_kb_search(mock_storage):
    """Test search tool."""
    from server import kb_search

    result = await kb_search("test query")
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_kb_stats(mock_storage):
    """Test stats tool."""
    from server import kb_stats

    result = await kb_stats()
    assert "total_documents" in result


@pytest.mark.asyncio
async def test_kb_list_collections(mock_storage):
    """Test list collections tool."""
    from server import kb_list_collections

    result = await kb_list_collections()
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_kb_uri_resolve():
    """Test URI resolve tool."""
    from server import kb_uri_resolve

    result = await kb_uri_resolve("core://fastapi/deps")
    assert result["domain"] == "core"
    assert result["collection"] == "core__fastapi"


@pytest.mark.asyncio
async def test_kb_uri_domains():
    """Test URI domains tool."""
    from server import kb_uri_domains

    result = await kb_uri_domains()
    assert isinstance(result, dict)
    assert "core" in result
