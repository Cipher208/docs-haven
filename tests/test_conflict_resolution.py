"""Tests for Conflict Resolution UI."""

from __future__ import annotations

from pathlib import Path

from conflicts import ConflictDetector
from storage import Storage


class TestConflictResolution:
    def test_get_conflict_details(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert([
            {"collection": "test", "path": "doc1.md", "content": "FastAPI tutorial", "title": "FastAPI Tutorial"},
        ])
        detector = ConflictDetector(storage)
        details = detector.get_conflict_details("doc1.md")
        assert details["new_id"] == "doc1.md"
        assert "candidates" in details
        assert "judgments" in details

    def test_get_conflict_details_not_found(self, tmp_path: Path):
        storage = Storage(tmp_path)
        detector = ConflictDetector(storage)
        details = detector.get_conflict_details("nonexistent")
        assert "error" in details

    def test_suggest_resolution_no_conflict(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert([
            {"collection": "test", "path": "doc1.md", "content": "Unique content", "title": "Unique"},
        ])
        detector = ConflictDetector(storage)
        suggestion = detector.suggest_resolution("doc1.md")
        assert suggestion["suggestion"] == "no_action"

    def test_suggest_resolution_not_found(self, tmp_path: Path):
        storage = Storage(tmp_path)
        detector = ConflictDetector(storage)
        suggestion = detector.suggest_resolution("nonexistent")
        assert "error" in suggestion

    def test_judge_validates_inputs(self, tmp_path: Path):
        storage = Storage(tmp_path)
        detector = ConflictDetector(storage)
        result = detector.judge("", "candidate", "supersedes")
        assert result.is_err
        result = detector.judge("new", "", "supersedes")
        assert result.is_err
        result = detector.judge("new", "candidate", "invalid")
        assert result.is_err
