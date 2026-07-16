"""Conflict Surfacing for DocsHaven — detect contradictions in knowledge base.

Pattern from engram: after adding a document, search for similar titles.
If similar docs exist, flag potential conflicts for human review.

Uses Storage.search() instead of external QMD CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage import Storage


@dataclass
class ConflictCandidate:
    """A potential conflict with an existing document."""

    title: str
    collection: str
    score: float
    path: str
    snippet: str = ""
    judgment: str = "pending"  # pending, supersedes, conflicts_with, unrelated


@dataclass
class ConflictResult:
    """Result of conflict detection for a new document."""

    new_title: str
    candidates: list
    has_conflicts: bool
    judgment_required: bool

    def to_dict(self) -> dict:
        return {
            "new_title": self.new_title,
            "candidates": [vars(c) if hasattr(c, "__dataclass_fields__") else c for c in self.candidates],
            "has_conflicts": self.has_conflicts,
            "judgment_required": self.judgment_required,
        }


class ConflictDetector:
    """Detect potential conflicts when adding new documents."""

    SCORE_THRESHOLD = 0.3  # Minimum score to consider a candidate
    MAX_CANDIDATES = 3

    def __init__(self, storage: Storage | None = None):
        self._storage = storage

    def _get_storage(self) -> Storage:
        if self._storage is None:
            from pathlib import Path

            from storage import Storage

            self._storage = Storage(Path.home() / ".docshaven")
        return self._storage

    def detect(self, title: str, content: str, collections: list[str] | None = None) -> ConflictResult:
        """Detect conflicts for a new document.

        Searches for similar documents using the Storage search engine.

        Args:
            title: Document title
            content: Document content (for context)
            collections: Optional collection filter

        Returns:
            ConflictResult with candidates and judgment status
        """
        candidates = self._find_similar(title, collections)

        return ConflictResult(
            new_title=title,
            candidates=candidates,
            has_conflicts=len(candidates) > 0,
            judgment_required=len(candidates) > 0,
        )

    def _find_similar(self, title: str, collections: list[str] | None = None) -> list[dict]:
        """Find documents with similar titles using Storage search."""
        storage = self._get_storage()

        # Search with the title as query
        results = storage.search(
            query=title,
            collections=collections,
            limit=self.MAX_CANDIDATES + 2,
            strategy="fts",
        )

        # Filter by score threshold and exclude exact matches
        candidates = []
        for r in results:
            score = r.get("score", 0)
            if score >= self.SCORE_THRESHOLD:
                candidates.append(
                    {
                        "title": r.get("title", ""),
                        "collection": r.get("collection", "unknown"),
                        "score": round(score, 3),
                        "path": r.get("path", ""),
                        "snippet": r.get("content", "")[:200],
                    }
                )

        return candidates[: self.MAX_CANDIDATES]

    def judge(self, new_id: str, candidate_id: str, judgment: str) -> dict:
        """Record a human judgment on a conflict.

        Args:
            new_id: ID of the new document
            candidate_id: ID of the candidate document
            judgment: One of 'supersedes', 'conflicts_with', 'unrelated'

        Returns:
            {status: 'recorded', judgment: str}
        """
        valid_judgments = {"supersedes", "conflicts_with", "unrelated"}
        if judgment not in valid_judgments:
            return {"error": f"Invalid judgment. Must be one of: {sorted(valid_judgments)}"}

        return {
            "status": "recorded",
            "new_id": new_id,
            "candidate_id": candidate_id,
            "judgment": judgment,
        }
