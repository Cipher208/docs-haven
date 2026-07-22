"""Tests for optional vector search."""

from __future__ import annotations

from pathlib import Path

from storage import Storage
from vector import VectorIndex, _cosine_similarity, _tokenize


class TestTokenize:
    def test_simple(self):
        assert _tokenize("hello world") == ["hello", "world"]

    def test_punctuation(self):
        assert _tokenize("hello, world!") == ["hello", "world"]

    def test_empty(self):
        assert _tokenize("") == []


class TestCosine:
    def test_identical(self):
        assert _cosine_similarity({"a": 1.0}, {"a": 1.0}) == 1.0

    def test_orthogonal(self):
        assert _cosine_similarity({"a": 1.0}, {"b": 1.0}) == 0.0

    def test_empty(self):
        assert _cosine_similarity({}, {"a": 1.0}) == 0.0


class TestVectorIndex:
    def test_build_and_search(self, tmp_path: Path):
        storage = Storage(tmp_path)
        # Add test documents
        storage.bulk_insert([
            {"collection": "test", "path": "doc1.md", "content": "FastAPI dependency injection tutorial", "title": "FastAPI Tutorial"},
            {"collection": "test", "path": "doc2.md", "content": "SQLAlchemy async database patterns", "title": "SQLAlchemy Guide"},
            {"collection": "test", "path": "doc3.md", "content": "FastAPI middleware authentication", "title": "FastAPI Auth"},
        ])

        index = VectorIndex(storage)
        index.build()
        results = index.search("FastAPI tutorial")
        assert len(results) > 0
        assert any("FastAPI" in r["title"] for r in results)

    def test_empty_index(self, tmp_path: Path):
        storage = Storage(tmp_path)
        index = VectorIndex(storage)
        index.build()
        results = index.search("anything")
        assert results == []

    def test_rebuild(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert([
            {"collection": "test", "path": "doc1.md", "content": "test content", "title": "Test"},
        ])
        index = VectorIndex(storage)
        index.build()
        index.rebuild()
        assert index._built

    def test_search_after_build(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert([
            {"collection": "test", "path": "doc1.md", "content": "async middleware pattern", "title": "Middleware"},
        ])
        index = VectorIndex(storage)
        results = index.search("middleware", limit=1)
        assert len(results) == 1
        assert results[0]["source"] == "vector"
