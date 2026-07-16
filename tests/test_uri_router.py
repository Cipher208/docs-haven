"""Tests for URIRouter."""

import tempfile
from pathlib import Path

import pytest

from storage import Storage
from uri import URIRouter


@pytest.fixture
def storage_with_collections():
    """Provide storage with sample collections."""
    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))
        conn = storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("core__fastapi", "guide.md", "FastAPI guide", "FastAPI Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("core__fastapi", "api.md", "FastAPI API", "FastAPI API"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("guide__pytest", "testing.md", "Pytest guide", "Pytest Guide"),
        )
        conn.commit()
        yield storage
        storage.close()


class TestURIRouter:
    def test_resolve(self, storage_with_collections):
        router = URIRouter(storage_with_collections)
        result = router.resolve("core://fastapi/guide")
        assert result["domain"] == "core"
        assert result["path"] == "fastapi/guide"
        assert result["collection"] == "core__fastapi"

    def test_search_by_uri(self, storage_with_collections):
        router = URIRouter(storage_with_collections)
        results = router.search_by_uri("core://fastapi")
        assert len(results) > 0

    def test_search_by_uri_wildcard(self, storage_with_collections):
        router = URIRouter(storage_with_collections)
        results = router.search_by_uri("core://fastapi/*")
        assert len(results) > 0

    def test_list_by_domain(self, storage_with_collections):
        router = URIRouter(storage_with_collections)
        results = router.list_by_domain("core")
        assert len(results) == 1
        assert results[0]["collection"] == "core__fastapi"

    def test_list_by_domain_unknown(self, storage_with_collections):
        router = URIRouter(storage_with_collections)
        results = router.list_by_domain("unknown")
        assert len(results) == 1
        assert "error" in results[0]

    def test_list_all_domains(self, storage_with_collections):
        router = URIRouter(storage_with_collections)
        result = router.list_all_domains()
        assert "core" in result
        assert result["core"]["count"] == 1
        assert "guide" in result
        assert result["guide"]["count"] == 1
