"""Anchors for P1-7: update_document metadata preservation + collection scoping.

Semantics under test (``storage.update_document``):

- a content-only update preserves every metadata field the caller does not
  pass (title, context, created_at, retrieval_count, extension);
- passing ``title`` (or ``context``) overrides just that field;
- an update is scoped to one collection: with the same ``file_path`` in two
  collections, updating one leaves the other completely untouched;
- an update of a missing (collection, path) is Err and creates nothing
  (the old code silently created a document with an empty collection).

These tests MUST FAIL on the previous code (update_document re-inserted rows
losing created_at/retrieval_count/context, deleted by file_path across ALL
collections, and created a document when none existed) and PASS after the fix.
"""

from __future__ import annotations

from storage import Storage


def _insert(
    storage: Storage,
    collection: str,
    path: str,
    content: str,
    title: str = "",
    context: str = "",
    created_at: str = "2026-01-01 00:00:00",
    retrieval_count: int = 0,
) -> None:
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title, context, created_at, retrieval_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (collection, path, content, title, context, created_at, retrieval_count),
    )
    conn.commit()


def _row(storage: Storage, collection: str, path: str) -> dict | None:
    row = (
        storage._get_conn()
        .execute(
            "SELECT * FROM documents WHERE collection = ? AND file_path = ?",
            (collection, path),
        )
        .fetchone()
    )
    return dict(row) if row else None


class TestUpdatePreservesMetadata:
    def test_update_content_only_preserves_metadata(self, tmp_storage: Storage):
        """Content-only update must keep title, context, created_at, retrieval_count."""
        _insert(
            tmp_storage,
            "test",
            "doc.md",
            "old content",
            title="Old Title",
            context="repo description",
            created_at="2026-01-01 00:00:00",
            retrieval_count=7,
        )

        result = tmp_storage.update_document("doc.md", "new content")
        assert result.is_ok

        row = _row(tmp_storage, "test", "doc.md")
        assert row is not None
        assert row["content"] == "new content"
        assert row["title"] == "Old Title", "title must survive a content-only update"
        assert row["context"] == "repo description", "context must survive a content-only update"
        assert row["created_at"] == "2026-01-01 00:00:00", "created_at must survive an update"
        assert row["retrieval_count"] == 7, "retrieval_count must survive an update"

    def test_update_with_new_title_updates_title_only(self, tmp_storage: Storage):
        """Passing title overrides the title; other metadata still survives."""
        _insert(
            tmp_storage,
            "test",
            "doc.md",
            "old content",
            title="Old Title",
            context="repo description",
            created_at="2026-01-01 00:00:00",
            retrieval_count=3,
        )

        result = tmp_storage.update_document("doc.md", "new content", title="New Title")
        assert result.is_ok

        row = _row(tmp_storage, "test", "doc.md")
        assert row is not None
        assert row["title"] == "New Title"
        assert row["context"] == "repo description", "context must survive a title update"
        assert row["created_at"] == "2026-01-01 00:00:00"
        assert row["retrieval_count"] == 3


class TestUpdateScoping:
    def test_update_scoped_leaves_other_collection_untouched(self, tmp_storage: Storage):
        """same file_path in collections a and b: updating a/b leaves b/a untouched."""
        _insert(tmp_storage, "a", "same.md", "in A", title="A Title", context="A ctx")
        _insert(tmp_storage, "b", "same.md", "in B", title="B Title", context="B ctx")

        result = tmp_storage.update_document("same.md", "A updated", collection="a")
        assert result.is_ok

        row_a = _row(tmp_storage, "a", "same.md")
        row_b = _row(tmp_storage, "b", "same.md")
        assert row_a is not None and row_a["content"] == "A updated"
        assert row_b is not None, "document in b must survive the update of a"
        assert row_b["content"] == "in B", "content in b must be untouched"
        assert row_b["title"] == "B Title", "metadata in b must be untouched"
        assert row_b["context"] == "B ctx"

    def test_update_without_collection_ambiguous_is_err(self, tmp_storage: Storage):
        """Bare-path update when the path exists in 2 collections is Err (no guessing)."""
        _insert(tmp_storage, "a", "same.md", "in A")
        _insert(tmp_storage, "b", "same.md", "in B")

        result = tmp_storage.update_document("same.md", "clobbered")
        assert result.is_err
        assert "collection required" in result.error

        assert _row(tmp_storage, "a", "same.md")["content"] == "in A", "nothing may change on ambiguity"
        assert _row(tmp_storage, "b", "same.md")["content"] == "in B", "nothing may change on ambiguity"

    def test_update_missing_returns_err_and_creates_nothing(self, tmp_storage: Storage):
        """Updating a nonexistent (collection, path) is Err and inserts no rows."""
        result = tmp_storage.update_document("ghost.md", "new content", collection="nope")
        assert result.is_err

        count = tmp_storage._get_conn().execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 0, "an update of a missing document must not create anything"

    def test_update_missing_unscoped_returns_err_and_creates_nothing(self, tmp_storage: Storage):
        """Bare-path update of a nonexistent document is Err and inserts no rows."""
        result = tmp_storage.update_document("ghost.md", "new content")
        assert result.is_err

        count = tmp_storage._get_conn().execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 0, "an update of a missing document must not create anything"
