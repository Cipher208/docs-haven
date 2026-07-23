"""Conflict Surfacing for DocsHaven — detect contradictions in knowledge base.

Pattern from engram: after adding a document, search for similar titles.
If similar docs exist, flag potential conflicts for human review.

Uses Storage.search() for internal full-text search.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from result import Err, Ok

if TYPE_CHECKING:
    from storage import Storage

logger = logging.getLogger(__name__)


class ConflictResult(BaseModel):
    """Result of conflict detection for a new document."""

    new_title: str
    candidates: list[dict] = Field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return len(self.candidates) > 0

    @property
    def judgment_required(self) -> bool:
        return self.has_conflicts

    def to_dict(self) -> dict:
        return {
            "new_title": self.new_title,
            "candidates": self.candidates,
            "has_conflicts": self.has_conflicts,
            "judgment_required": self.judgment_required,
        }


class ConflictDetector:
    """Detect potential conflicts when adding new documents."""

    # 0.3 catches moderately similar docs without too many false positives
    SCORE_THRESHOLD = 0.3
    # 3 candidates keeps review manageable — more is noise
    MAX_CANDIDATES = 3

    def __init__(self, storage: Storage | None = None):
        self._storage = storage

    def _get_storage(self) -> Storage:
        if self._storage is None:
            from storage import Storage

            self._storage = Storage.default()
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
        candidates = self._find_similar(title, content, collections)

        return ConflictResult(
            new_title=title,
            candidates=candidates,
        )

    def _find_similar(self, title: str, content: str = "", collections: list[str] | None = None) -> list[dict]:
        """Find documents with similar titles using Storage search."""
        storage = self._get_storage()

        # Search with title + content keywords for better coverage
        query = f"{title} {content[:200]}" if content else title
        result = storage.search(
            query=query,
            collections=collections,
            limit=self.MAX_CANDIDATES + 2,
            strategy="fts",
        )

        if result.is_err:
            return []

        # Filter by score threshold and exclude exact matches
        candidates = []
        for r in result.value:  # type: ignore[union-attr]
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

    def judge(self, new_id: str, candidate_id: str, judgment: str) -> Ok[dict] | Err:
        """Record a human judgment on a conflict.

        Args:
            new_id: ID of the new document
            candidate_id: ID of the candidate document
            judgment: One of 'supersedes', 'conflicts_with', 'unrelated'

        Returns:
            Ok({status: 'recorded', ...}) or Err
        """
        if not new_id or not new_id.strip():
            return Err(error="new_id cannot be empty")
        if not candidate_id or not candidate_id.strip():
            return Err(error="candidate_id cannot be empty")
        valid_judgments = {"supersedes", "conflicts_with", "unrelated"}
        if judgment not in valid_judgments:
            return Err(error=f"Invalid judgment. Must be one of: {sorted(valid_judgments)}")

        storage = self._get_storage()
        return storage.record_judgment(new_id, candidate_id, judgment)

    def get_conflict_details(self, new_id: str) -> dict:
        """Get details about a conflict for resolution."""
        storage = self._get_storage()

        new_doc = storage.get(new_id)
        if new_doc.is_err:
            return {"error": f"Document not found: {new_id}"}

        title = new_doc.value.get("title", "")  # type: ignore[union-attr]
        content = new_doc.value.get("content", "")  # type: ignore[union-attr]
        candidates = self.detect(title, content)

        judgments_result = storage.get_judgments(new_id)
        judgments = judgments_result.value if judgments_result.is_ok else []

        return {
            "new_id": new_id,
            "new_title": title,
            "candidates": candidates.candidates,
            "has_conflicts": candidates.has_conflicts,
            "judgments": judgments,
        }

    def suggest_resolution(self, new_id: str) -> dict:
        """Suggest a resolution strategy for a conflict."""
        details = self.get_conflict_details(new_id)
        if "error" in details:
            return {"error": details["error"]}

        if not details["has_conflicts"]:
            return {"suggestion": "no_action", "confidence": 1.0, "reason": "No conflicts detected"}

        if not details["candidates"]:
            return {"suggestion": "no_action", "confidence": 1.0, "reason": "No similar documents found"}

        top_score = details["candidates"][0].get("score", 0)
        if top_score > 0.7:
            return {
                "suggestion": "supersedes",
                "confidence": 0.8,
                "reason": f"High similarity ({top_score:.2f}) — new doc likely supersedes",
            }
        elif top_score > 0.4:
            return {
                "suggestion": "conflicts_with",
                "confidence": 0.6,
                "reason": f"Moderate similarity ({top_score:.2f}) — review manually",
            }
        else:
            return {
                "suggestion": "unrelated",
                "confidence": 0.7,
                "reason": "Low similarity — likely unrelated",
            }
