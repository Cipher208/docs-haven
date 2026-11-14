"""Anchors for P1-8: delete_document must be scoped, never mass-delete.

Scoping policy under test (``storage.delete_document``):

- a delete WITHOUT a collection must never remove the path from more than one
  collection: when the path exists in 2+ collections the call is Err
  ("collection required") and NOTHING is deleted;
- when the path exists in exactly one collection the unambiguous delete is
  allowed (documented backward-compat policy);
- a delete WITH a collection removes only that collection's document, and an
  identically-named file in another collection survives;
- the MCP tool kb_delete passes the collection through to the scoped delete
  and never triggers a cross-collection delete.

These tests MUST FAIL on the previous code (delete_document deleted matching
file_paths across ALL collections) and PASS after the fix.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import server
from result import Ok
from storage import Storage


def _insert(storage: Storage, collection: str, path: str, content: str, title: str = "") -> None:
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
        (collection, path, content, title),
    )
    conn.commit()


def _count(storage: Storage, collection: str, path: str) -> int:
    return (
        storage._get_conn()
        .execute(
            "SELECT COUNT(*) FROM documents WHERE collection = ? AND file_path = ?",
            (collection, path),
        )
        .fetchone()[0]
    )


class TestDeleteWithoutCollection:
    def test_multi_collection_path_err_nothing_deleted(self, tmp_storage: Storage):
        """Bare-path delete of a path in 2 collections: Err, both documents survive."""
        _insert(tmp_storage, "a", "same.md", "in A")
        _insert(tmp_storage, "b", "same.md", "in B")

        result = tmp_storage.delete_document("same.md")
        assert result.is_err, "ambiguous delete must be refused"
        assert "collection required" in result.error

        assert _count(tmp_storage, "a", "same.md") == 1, "nothing may be deleted on ambiguity"
        assert _count(tmp_storage, "b", "same.md") == 1, "nothing may be deleted on ambiguity"

    def test_single_collection_path_deletes(self, tmp_storage: Storage):
        """Bare-path delete of a path held by exactly one collection is allowed
        (unambiguous delete — documented backward-compat policy)."""
        _insert(tmp_storage, "a", "only.md", "in A")

        result = tmp_storage.delete_document("only.md")
        assert result.is_ok

        assert _count(tmp_storage, "a", "only.md") == 0


class TestDeleteWithCollection:
    def test_scoped_delete_removes_only_that_collection(self, tmp_storage: Storage):
        """delete_document(path, collection=...) removes one collection's doc only."""
        _insert(tmp_storage, "a", "same.md", "in A")
        _insert(tmp_storage, "b", "same.md", "in B")

        result = tmp_storage.delete_document("same.md", collection="a")
        assert result.is_ok

        assert _count(tmp_storage, "a", "same.md") == 0
        assert _count(tmp_storage, "b", "same.md") == 1, "same path in the other collection must survive"

    def test_delete_documents_scoped_is_scoped(self, tmp_storage: Storage):
        """The scoped variant (used by CLI/MCP) behaves the same way."""
        _insert(tmp_storage, "a", "same.md", "in A")
        _insert(tmp_storage, "b", "same.md", "in B")

        result = tmp_storage.delete_documents_scoped("same.md", "a")
        assert result.is_ok

        assert _count(tmp_storage, "a", "same.md") == 0
        assert _count(tmp_storage, "b", "same.md") == 1


class TestKbDeletePassesCollection:
    async def test_kb_delete_passes_collection_through(self):
        """kb_delete(file_path, collection) routes to the scoped delete only."""
        with patch("server._get_storage") as mock:
            storage = MagicMock()
            storage.delete_documents_scoped.return_value = Ok(value={"status": "deleted", "file_path": "same.md", "collection": "a"})
            mock.return_value = storage

            result = await server.kb_delete("same.md", collection="a")

        storage.delete_documents_scoped.assert_called_once_with("same.md", "a")
        storage.delete_document.assert_not_called()
        assert result["status"] == "deleted"

    async def test_kb_delete_with_collection_never_mass_deletes(self, tmp_storage: Storage, monkeypatch):
        """Real storage: kb_delete with a collection removes only that document."""
        _insert(tmp_storage, "a", "same.md", "in A")
        _insert(tmp_storage, "b", "same.md", "in B")
        monkeypatch.setattr(server, "_get_storage", lambda: tmp_storage)

        result = await server.kb_delete("same.md", collection="a")
        assert result["status"] == "deleted"

        assert _count(tmp_storage, "a", "same.md") == 0
        assert _count(tmp_storage, "b", "same.md") == 1, "kb_delete must never touch other collections"

    async def test_kb_delete_without_collection_refuses_ambiguous(self, tmp_storage: Storage, monkeypatch):
        """Real storage: bare kb_delete of a cross-collection path errors, deletes nothing."""
        _insert(tmp_storage, "a", "same.md", "in A")
        _insert(tmp_storage, "b", "same.md", "in B")
        monkeypatch.setattr(server, "_get_storage", lambda: tmp_storage)

        result = await server.kb_delete("same.md")
        assert "error" in result
        assert "collection required" in result["error"]

        assert _count(tmp_storage, "a", "same.md") == 1
        assert _count(tmp_storage, "b", "same.md") == 1
