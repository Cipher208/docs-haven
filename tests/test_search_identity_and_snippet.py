"""Pinning tests for search identity + FTS snippet (P0-2/4/5).

Checks:
1. kb_search path is fetchable by kb_get (search result path ↔ get).
2. FTS highlight shows content, not collection (snippet column 2 -> 1).
3. CLI get/delete by prefixed path (storage-level delete_doc scoped).
4. kb_collection_rename persists and search reflects it.
5. No cross-collection false match.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from result import Err, Ok
from storage import Storage


def _insert(storage: Storage, collection: str, file_path: str, content: str, title: str = "") -> None:
    storage.bulk_insert_raw(collection, file_path, content, title)


def _search(storage: Storage, query: str, collection: str | None = None) -> list[dict]:
    res = storage.search(query, collections=[collection] if collection else None, limit=20)
    return res.value if isinstance(res, Ok) else []


# ── 1. search result path is fetchable by get ────────────────────────────────

def test_search_result_fetchable_by_get(tmp_storage: Storage) -> None:
    """A document returned by search must be retrievable by get using its
    collection + file_path (the two fields a search result carries)."""
    _insert(tmp_storage, "proj", "notes/alpha.md", "alpha body text here", "Alpha")

    results = _search(tmp_storage, "alpha", collection="proj")
    assert results, "document not found by search"

    row = results[0]
    got = tmp_storage.get(row["file_path"], collection=row["collection"])
    assert isinstance(got, Ok), got
    assert got.value["content"] == "alpha body text here", got.value


# ── 2. FTS highlight shows content, not collection ───────────────────────────

def test_fts_highlight_shows_content_not_collection(tmp_storage: Storage) -> None:
    """FTS snippet must come from the content column, not the collection column.

    On the buggy code snippet(documents_fts, 2, ...) reads column index 2,
    which is `collection` (columns: 0=title, 1=content, 2=collection).
    After the fix it must read column 1 (content), so the highlighted span
    contains a word from the document body rather than the collection name.
    """
    _insert(
        tmp_storage,
        "unique_coll_xyz",
        "guide.md",
        "the quick brown fox jumps",
        "Guide Title",
    )

    results = _search(tmp_storage, "fox", collection="unique_coll_xyz")
    assert results, "document not found by search"

    highlighted = results[0].get("highlighted", "")
    # The unique body word must appear in the highlight (snippet from content).
    assert "fox" in highlighted.lower(), (
        f"snippet should contain body word 'fox'; highlighted={highlighted!r}; "
        "collection={}".format(results[0].get("collection"))
    )
    # The collection name must NOT dominate the highlight on a content search.
    # (It may appear incidentally if the body repeats it, which is fine; the key
    # assertion is that the body word is present.)
    assert results[0].get("collection") == "unique_coll_xyz"


# ── 3. delete scoped to one collection does not touch another ────────────────

def test_delete_scoped_to_collection(tmp_storage: Storage) -> None:
    """Deleting a document by (collection, file_path) must remove it only from
    that collection, leaving an identically-named file in another collection intact.

    This guards the previously-unscoped ``delete_document(file_path)`` that
    removed matching file_paths across ALL collections.
    """
    _insert(tmp_storage, "a", "same.md", "in A", "A")
    _insert(tmp_storage, "b", "same.md", "in B", "B")

    # scoped delete from collection a only
    r = tmp_storage.delete_documents_scoped("same.md", "a")
    assert isinstance(r, Ok), r

    in_a = _search(tmp_storage, "in A", collection="a")
    in_b = _search(tmp_storage, "in B", collection="b")

    assert not in_a, "document in a should have been removed"
    assert in_b, "document in b must remain untouched"


# ── 4. rename persists and search reflects it ────────────────────────────────

def test_rename_reflects_in_search(tmp_storage: Storage) -> None:
    """After renaming a collection, document search scoped to the new name must
    still return the collection's documents (rename must propagate to FTS via
    the documents_au trigger)."""
    _insert(tmp_storage, "old_coll", "doc.md", "rename me", "Doc")

    r = tmp_storage.rename_collection("old_coll", "new_coll")
    assert isinstance(r, Ok), r
    assert r.value["from"] == "old_coll"
    assert r.value["to"] == "new_coll"

    before = _search(tmp_storage, "rename", collection="old_coll")
    after = _search(tmp_storage, "rename", collection="new_coll")

    assert not before, "old collection should no longer have documents"
    assert after, "new collection should still have the document"


# ── 5. no cross-collection false match ──────────────────────────────────────

def test_search_scoped_excludes_other_collections(tmp_storage: Storage) -> None:
    """A search scoped to one collection must not return documents from another
    collection, even when they share the same file_path or content."""
    _insert(tmp_storage, "c1", "shared.md", "shared body", "C1")
    _insert(tmp_storage, "c2", "shared.md", "shared body", "C2")

    results = _search(tmp_storage, "shared", collection="c1")
    collections_seen = {r["collection"] for r in results}

    assert collections_seen == {"c1"}, (
        f"search scoped to c1 returned docs from {collections_seen}"
    )
