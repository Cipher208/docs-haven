"""Tests for MCP server tools."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_storage():
    """Create a mock storage for server tests."""
    from result import Ok

    with patch("server._get_storage") as mock:
        storage = MagicMock()
        storage.search.return_value = Ok(
            value=[{"score": 0.9, "collection": "test", "title": "Test", "content": "Content...", "path": "test/doc.md"}]
        )
        storage.stats.return_value = Ok(
            value={"total_documents": 10, "collections": 2, "repos": 1, "total_chunks": 50, "db_path": "", "db_size_kb": 1024}
        )
        storage.list_collections.return_value = Ok(value=[{"name": "test", "count": 10}])
        mock.return_value = storage
        yield storage


@pytest.mark.asyncio
async def test_kb_search(mock_storage):
    """Test search tool."""
    from server import kb_search

    result = await kb_search("test query")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["title"] == "Test"
    assert result[0]["score"] == 0.9


@pytest.mark.asyncio
async def test_kb_stats(mock_storage):
    """Test stats tool."""
    from server import kb_stats

    result = await kb_stats()
    assert result["total_documents"] == 10
    assert result["collections"] == 2
    assert result["repos"] == 1


@pytest.mark.asyncio
async def test_kb_list_collections(mock_storage):
    """Test list collections tool."""
    from server import kb_list_collections

    result = await kb_list_collections()
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "test"


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


@pytest.mark.asyncio
async def test_kb_update(mock_storage):
    """Test update tool."""
    from result import Ok
    from server import kb_update

    mock_storage.update_document.return_value = Ok(value={"status": "updated", "file_path": "test/doc.md"})
    result = await kb_update("test/doc.md", "new content", title="New Title")
    assert result["status"] == "updated"
    assert result["file_path"] == "test/doc.md"


@pytest.mark.asyncio
async def test_kb_delete(mock_storage):
    """Test delete tool."""
    from result import Ok
    from server import kb_delete

    mock_storage.delete_document.return_value = Ok(value={"status": "deleted", "file_path": "test/doc.md"})
    result = await kb_delete("test/doc.md")
    assert result["status"] == "deleted"
    assert result["file_path"] == "test/doc.md"


@pytest.mark.asyncio
async def test_kb_conflict_check():
    """Test conflict check tool."""
    from server import kb_conflict_check

    result = await kb_conflict_check("FastAPI Guide", "How to use FastAPI")
    assert isinstance(result, dict)
    assert "has_conflicts" in result


@pytest.mark.asyncio
async def test_kb_get_path_traversal():
    """Test kb_get rejects path traversal."""
    from server import kb_get

    result = await kb_get("../../../etc/passwd")
    assert "error" in result

    result = await kb_get("/etc/passwd")
    assert "error" in result


@pytest.mark.asyncio
async def test_kb_get_nonexistent(mock_storage):
    """Test kb_get returns error for nonexistent document."""
    from result import Err
    from server import kb_get

    mock_storage.get.return_value = Err(error="Document not found")
    result = await kb_get("nonexistent.md")
    assert "error" in result
