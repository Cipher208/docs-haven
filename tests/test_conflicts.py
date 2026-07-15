"""Tests for Conflict Surfacing."""

import tempfile
from pathlib import Path

import pytest

from conflicts import ConflictDetector, ConflictResult
from storage import Storage


@pytest.fixture
def storage_with_docs():
    """Provide storage with sample documents."""
    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))

        # Add some test documents directly
        conn = storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "guide.md", "FastAPI dependency injection tutorial", "FastAPI Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "api.md", "FastAPI REST API endpoints", "FastAPI API"),
        )
        conn.commit()
        conn.close()

        yield storage


class TestConflictDetector:
    def test_no_conflicts_empty_db(self):
        with tempfile.TemporaryDirectory() as d:
            storage = Storage(Path(d))
            detector = ConflictDetector(storage)
            result = detector.detect("New Document", "Some content")
            assert result.has_conflicts is False
            assert result.judgment_required is False

    def test_detects_similar_title(self, storage_with_docs):
        detector = ConflictDetector(storage_with_docs)
        result = detector.detect("FastAPI tutorial", "How to use FastAPI")
        # Should find something since we have FastAPI docs
        assert isinstance(result, ConflictResult)
        assert result.new_title == "FastAPI tutorial"

    def test_no_match_unrelated(self, storage_with_docs):
        detector = ConflictDetector(storage_with_docs)
        result = detector.detect("XYZZY completely unrelated", "xyzzy content xyzzy")
        assert isinstance(result, ConflictResult)

    def test_to_dict(self, storage_with_docs):
        detector = ConflictDetector(storage_with_docs)
        result = detector.detect("FastAPI", "content")
        d = result.to_dict()
        assert "new_title" in d
        assert "candidates" in d
        assert "has_conflicts" in d

    def test_judge_valid(self):
        detector = ConflictDetector()
        result = detector.judge("doc1", "doc2", "supersedes")
        assert result["status"] == "recorded"
        assert result["judgment"] == "supersedes"

    def test_judge_invalid(self):
        detector = ConflictDetector()
        result = detector.judge("doc1", "doc2", "invalid_judgment")
        assert "error" in result
