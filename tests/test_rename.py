"""Tests for collection rename."""

from __future__ import annotations

from pathlib import Path

from storage import Storage


class TestRenameCollection:
    def test_rename_basic(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert(
            [
                {"collection": "old", "path": "doc.md", "content": "test", "title": "Test"},
            ]
        )
        result = storage.rename_collection("old", "new")
        assert result.is_ok
        assert result.value["from"] == "old"
        assert result.value["to"] == "new"

        # Verify data moved
        docs = storage.list_documents("new")
        assert docs.is_ok
        assert len(docs.value) == 1

        # Verify old is gone
        docs_old = storage.list_documents("old")
        assert docs_old.is_ok
        assert len(docs_old.value) == 0

    def test_rename_nonexistent(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.rename_collection("nonexistent", "new")
        assert result.is_err
        assert "not found" in result.error

    def test_rename_to_existing(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert(
            [
                {"collection": "a", "path": "doc.md", "content": "test", "title": "A"},
                {"collection": "b", "path": "doc.md", "content": "test", "title": "B"},
            ]
        )
        result = storage.rename_collection("a", "b")
        assert result.is_err
        assert "already exists" in result.error

    def test_rename_invalid_old_name(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.rename_collection("../../../etc", "new")
        assert result.is_err
        assert result.error

    def test_rename_invalid_new_name(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.rename_collection("old", "../../../etc")
        assert result.is_err
        assert result.error

    def test_rename_preserves_context(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert(
            [
                {"collection": "old", "path": "doc.md", "content": "test", "title": "Test"},
            ]
        )
        storage.add_context("old", "overview", "Summary")
        result = storage.rename_collection("old", "new")
        assert result.is_ok
        ctx = storage.get_context("new")
        assert ctx.is_ok
        assert len(ctx.value) == 1
