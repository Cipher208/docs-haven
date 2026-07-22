"""Tests for context attachments."""

from __future__ import annotations

from pathlib import Path

from storage import Storage


class TestContextAttachments:
    def test_add_context(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.add_context("test", "overview", "This is an overview")
        assert result.is_ok
        assert result.value["status"] == "added"

    def test_get_context(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.add_context("test", "overview", "Summary 1")
        storage.add_context("test", "quickstart", "Summary 2")
        result = storage.get_context("test")
        assert result.is_ok
        assert len(result.value) == 2

    def test_get_context_by_path(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.add_context("test", "overview", "Summary 1")
        result = storage.get_context("test", "overview")
        assert result.is_ok
        assert len(result.value) == 1
        assert result.value[0]["summary"] == "Summary 1"

    def test_list_contexts(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.add_context("test1", "a", "Summary A")
        storage.add_context("test2", "b", "Summary B")
        result = storage.list_contexts()
        assert result.is_ok
        assert len(result.value) == 2

    def test_remove_context(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.add_context("test", "overview", "Summary")
        result = storage.remove_context("test", "overview")
        assert result.is_ok
        remaining = storage.get_context("test")
        assert remaining.is_ok
        assert len(remaining.value) == 0

    def test_remove_all_in_collection(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.add_context("test", "a", "Summary A")
        storage.add_context("test", "b", "Summary B")
        result = storage.remove_context("test")
        assert result.is_ok
        remaining = storage.get_context("test")
        assert remaining.is_ok
        assert len(remaining.value) == 0

    def test_add_empty_path_fails(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.add_context("test", "", "Summary")
        assert result.is_err

    def test_add_empty_summary_fails(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.add_context("test", "path", "")
        assert result.is_err

    def test_add_invalid_collection_fails(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.add_context("../../../etc", "path", "Summary")
        assert result.is_err
