"""Anchors for Card P2-2: text (note) indexing — `Storage.add_text`.

MUST FAIL at the pre-card HEAD (fce5ac4) and PASS after the card lands:

- Storage.add_text does not exist (AttributeError)
"""

from __future__ import annotations

import hashlib

import pytest

from result import Err
from storage import Storage

NOTE_BODY = "FastAPI dependency injection with Depends and parameters"
LONG_BODY = "\n\n".join(f"Paragraph {i} about distributed systems " + "x" * 200 for i in range(20))


def _hash16(content: str) -> str:
    """The card's content-address suffix: the content_hash — sha256, first 16 hex."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# A constructed sha1[:8] collision (found offline). Under the old sha1[:8]
# suffix these two different contents minted ONE file_path and the second add
# silently deleted the first note (F-2). Different sha256[:16] -> different
# file_paths, both notes survive.
_SHA1_COLLIDING_A = "Ai\n9SIJfgG1pytRICg\ns\ncque"
_SHA1_COLLIDING_B = "AtHibu-dqGcFrl4SOnXbT4P3v"


@pytest.fixture
def kb_storage(tmp_path):
    """Storage on tmp_path (not TemporaryDirectory): matches test_kb_index —
    Windows cannot unlink files still open at fixture teardown."""
    storage = Storage(tmp_path / "kb-data")
    yield storage
    storage.close()


class TestAddTextBasics:
    def test_content_searchable_result_shape(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text(NOTE_BODY, title="Deps")
        assert result.is_ok, result
        assert set(result.value.keys()) == {"collection", "file_path", "title", "status", "chunks"}
        assert result.value["collection"] == "notes"
        assert result.value["title"] == "Deps"
        assert result.value["file_path"].startswith("text://")
        assert result.value["status"] == "added"
        assert result.value["chunks"] == 1

        found = kb_storage.search("dependency injection")
        assert found.is_ok, found
        assert any(r["file_path"] == result.value["file_path"] for r in found.value), found.value

    def test_get_returns_full_content(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text(LONG_BODY, title="Long").unwrap()
        assert result["chunks"] > 1
        fetched = kb_storage.get(result["file_path"])
        assert fetched.is_ok, fetched
        assert fetched.value["chunks"] == result["chunks"]
        assert "Paragraph 0" in fetched.value["content"]
        assert "Paragraph 19" in fetched.value["content"]

    def test_custom_collection(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text(NOTE_BODY, title="Deps", collection="ideas").unwrap()
        assert result["collection"] == "ideas"
        found = kb_storage.search("dependency injection", collections=["ideas"])
        assert any(r["file_path"] == result["file_path"] for r in found.value), found.value

    def test_description_stored_as_context(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text(NOTE_BODY, title="Deps", description="my context").unwrap()
        row = kb_storage._get_conn().execute("SELECT context FROM documents WHERE file_path = ?", (result["file_path"],)).fetchone()
        assert row["context"] == "my context"

    def test_tags_accepted(self, kb_storage: Storage) -> None:
        # tags have no schema home yet (documents have no tag column — P3-15);
        # add_text accepts them for add_local/add_repo signature parity.
        result = kb_storage.add_text(NOTE_BODY, title="Deps", tags=["python", "docs"])
        assert result.is_ok, result


class TestAddTextFileAddress:
    def test_idempotent_re_add_same_content(self, kb_storage: Storage) -> None:
        first = kb_storage.add_text(NOTE_BODY, title="Deps").unwrap()
        second = kb_storage.add_text(NOTE_BODY, title="Deps renamed").unwrap()
        assert second["file_path"] == first["file_path"]
        assert second["status"] == "replaced"
        count = kb_storage._get_conn().execute("SELECT COUNT(*) FROM documents WHERE collection = 'notes'").fetchone()[0]
        assert count == 1
        assert kb_storage.get(first["file_path"]).value["title"] == "Deps renamed"

    def test_idempotent_re_add_multi_chunk_no_duplicate_rows(self, kb_storage: Storage) -> None:
        first = kb_storage.add_text(LONG_BODY, title="Long").unwrap()
        assert first["chunks"] > 1
        second = kb_storage.add_text(LONG_BODY, title="Long").unwrap()
        assert second["file_path"] == first["file_path"]
        count = kb_storage._get_conn().execute("SELECT COUNT(*) FROM documents WHERE collection = 'notes'").fetchone()[0]
        assert count == first["chunks"]

    def test_same_title_different_content_distinct_paths(self, kb_storage: Storage) -> None:
        a = kb_storage.add_text("first body content", title="Same Title").unwrap()
        b = kb_storage.add_text("second body content", title="Same Title").unwrap()
        assert a["file_path"] != b["file_path"]
        count = kb_storage._get_conn().execute("SELECT COUNT(*) FROM documents WHERE collection = 'notes'").fetchone()[0]
        assert count == 2

    def test_file_path_layout(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text("body", title="My Note!").unwrap()
        assert result["file_path"] == f"text://my-note-{_hash16('body')}"

    def test_constructed_suffix_collision_no_silent_loss(self, kb_storage: Storage) -> None:
        # F-2: the sha1[:8] suffix collided for this pair (verified below), so
        # the second add used to delete the first note silently.
        assert hashlib.sha1(_SHA1_COLLIDING_A.encode()).hexdigest()[:8] == hashlib.sha1(_SHA1_COLLIDING_B.encode()).hexdigest()[:8]
        a = kb_storage.add_text(_SHA1_COLLIDING_A, title="Same Title").unwrap()
        b = kb_storage.add_text(_SHA1_COLLIDING_B, title="Same Title").unwrap()
        assert a["file_path"] != b["file_path"], "different content must mint different file_paths"
        assert kb_storage.get(a["file_path"]).value["content"] == _SHA1_COLLIDING_A
        assert kb_storage.get(b["file_path"]).value["content"] == _SHA1_COLLIDING_B
        count = kb_storage._get_conn().execute("SELECT COUNT(*) FROM documents WHERE collection = 'notes'").fetchone()[0]
        assert count == 2

    def test_replace_delete_requires_content_hash_match(self, kb_storage: Storage) -> None:
        # F-2: the replace DELETE is filtered by content_hash, so a residual
        # file_path collision (same path, different content — e.g. after a
        # kb_update rewrote the note in place) surfaces as a visible UNIQUE
        # error instead of silently deleting the other note.
        first = kb_storage.add_text("original note body", title="Note").unwrap()
        kb_storage.update_document(first["file_path"], "rewritten body").unwrap()
        result = kb_storage.add_text("original note body", title="Note")
        assert isinstance(result, Err), result
        assert "unique" in result.error.lower(), result.error
        assert kb_storage.get(first["file_path"]).value["content"] == "rewritten body"

    def test_replace_preserves_created_at_and_retrieval_count(self, kb_storage: Storage) -> None:
        # F-4: an idempotent replace carries created_at and retrieval_count
        # over from the rows it replaces (update_document's behavior).
        first = kb_storage.add_text(NOTE_BODY, title="Deps").unwrap()
        conn = kb_storage._get_conn()
        conn.execute("UPDATE documents SET retrieval_count = 1, created_at = '2026-01-01 00:00:00' WHERE file_path = ?", (first["file_path"],))
        conn.commit()
        second = kb_storage.add_text(NOTE_BODY, title="Deps renamed").unwrap()
        assert second["status"] == "replaced"
        row = conn.execute("SELECT created_at, retrieval_count FROM documents WHERE file_path = ?", (first["file_path"],)).fetchone()
        assert row["created_at"] == "2026-01-01 00:00:00"
        assert row["retrieval_count"] == 1


class TestTitleRules:
    def test_explicit_title_wins(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text("First line body\nmore text", title="Explicit").unwrap()
        assert result["title"] == "Explicit"

    def test_title_from_first_non_empty_line(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text("\n   My First Line   \nbody text").unwrap()
        assert result["title"] == "My First Line"

    def test_derived_title_truncated_to_80(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text("L" * 120 + "\nbody").unwrap()
        assert result["title"] == "L" * 80

    def test_empty_title_falls_back_to_derivation(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text("Derived wins here", title="").unwrap()
        assert result["title"] == "Derived wins here"

    def test_hash_fallback_title(self, kb_storage: Storage) -> None:
        # whitespace-only content has no non-empty line -> title from hash
        content = "   \n  \n"
        result = kb_storage.add_text(content).unwrap()
        assert result["title"] == f"note-{_hash16(content)[:8]}"
        assert result["file_path"].startswith("text://note-")


class TestValidation:
    def test_empty_content_is_err(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text("")
        assert isinstance(result, Err), result
        assert "empty" in result.error.lower()

    def test_content_too_large_is_err(self, kb_storage: Storage) -> None:
        # reuses the repo's 10MB content cap (kb_update precedent, server.py)
        result = kb_storage.add_text("a" * 10_000_001)
        assert isinstance(result, Err), result
        assert "too long" in result.error.lower()

    def test_invalid_collection_is_err(self, kb_storage: Storage) -> None:
        result = kb_storage.add_text("body", collection="bad name!")
        assert isinstance(result, Err), result


class TestCorpusCacheDiscipline:
    def test_add_text_invalidates_corpus_cache(self, kb_storage: Storage, monkeypatch) -> None:
        calls: list = []
        original = kb_storage._invalidate_corpus_cache

        def spy() -> None:
            calls.append(1)
            original()

        monkeypatch.setattr(kb_storage, "_invalidate_corpus_cache", spy)
        kb_storage.add_text(NOTE_BODY, title="Deps").unwrap()
        assert calls, "add_text must invalidate the corpus cache (BH-4)"

    def test_add_text_visible_in_warmed_cache(self, kb_storage: Storage) -> None:
        kb_storage.bulk_insert_raw("guide", "src.md", "FastAPI dependency injection with Depends", "Source").unwrap()
        first = kb_storage.find_related("guide", "src.md").unwrap()
        assert first["related"] == []  # only the source exists

        kb_storage.add_text("More on dependency injection with Depends", title="Deps notes").unwrap()
        second = kb_storage.find_related("guide", "src.md").unwrap()
        assert any(r["file_path"].startswith("text://") for r in second["related"]), second
