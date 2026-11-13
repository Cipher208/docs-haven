"""Pinning tests for PS4 conflict detection: detector finds real candidates.

These tests MUST FAIL before the PS4 fix (detector never finds candidates
because the FTS query is AND-quoted / threshold unreachable) and PASS after.
"""

from __future__ import annotations

import pytest

from conflicts import ConflictDetector
from storage import Storage


def test_conflict_finds_candidates(tmp_path: pytest.Path) -> None:
    """Similar title/content triggers conflict candidates."""

    storage = Storage(tmp_path)

    storage.bulk_insert(
        [
            {
                "collection": "docs",
                "path": "ref.md",
                "content": "FastAPI tutorial covering async endpoints",
                "title": "FastAPI Tutorial",
            },
        ]
    )

    detector = ConflictDetector(storage)
    result = detector.detect("FastAPI overview of async endpoints", "FastAPI async endpoints guide")

    assert result.has_conflicts, f"expected conflicts, got {result.to_dict()}"
    assert len(result.candidates) >= 1, f"expected >=1 candidate, got {result.candidates}"


def test_conflict_no_candidates_for_unique(tmp_path: pytest.Path) -> None:
    """A lone unique document yields no conflict candidates."""

    storage = Storage(tmp_path)

    storage.bulk_insert(
        [
            {
                "collection": "docs",
                "path": "unique.md",
                "content": "Unique content that no other document resembles",
                "title": "Unique",
            },
        ]
    )

    detector = ConflictDetector(storage)
    result = detector.detect("Completely unrelated title", "Unrelated body text here")

    assert not result.has_conflicts, f"expected no conflicts, got {result.to_dict()}"
    assert result.candidates == []
