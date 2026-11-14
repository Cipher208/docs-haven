"""Anchor tests for the invalidated in-memory corpus cache (BH-4, FUTURE_PLAN N4).

These tests MUST FAIL on current code (every find_related / learn_topic call
loads the whole corpus via _load_documents) and PASS after the implementation.
Covered:

- fail-before: two find_related calls load the corpus twice via _load_documents
  (counted by monkeypatch) -> after the fix exactly ONE load total; the second
  call is served from the cache
- update_document is visible immediately in find_related results
- delete_document / delete_documents_scoped are visible immediately
- insert paths (bulk_insert_raw, bulk_insert, _index_files) are visible
  immediately in learn_topic
- remove_collection / rename_collection are visible immediately
- the sync import path (_import_chunk_data -> bulk_insert) is visible immediately
- two Storage instances do NOT share cache state (instance-scoped cache and
  invalidation — cross-instance staleness on a shared data dir is the
  documented limitation, FUTURE_PLAN N4)
- cache rebuilt after invalidation returns fresh data: a content edit is
  reflected in similarity scores
"""

from __future__ import annotations

from storage import Storage
from sync import Syncer

SOURCE_TITLE = "FastAPI Dependencies"
SOURCE_CONTENT = "FastAPI dependency injection with Depends and parameters"
RELATED_CONTENT = "Notes on dependency injection in FastAPI with Depends"
UNRELATED_CONTENT = "Tomatoes need sun and water"


def _count_loads(storage: Storage, monkeypatch) -> list:
    """Count _load_documents invocations on *storage* (the cache's load path)."""
    calls: list = []
    original = storage._load_documents

    def counting(collection: str | None = None) -> list[dict]:
        calls.append(collection)
        return original(collection)

    monkeypatch.setattr(storage, "_load_documents", counting)
    return calls


def _seed_source_and_related(storage: Storage) -> None:
    storage.bulk_insert_raw("guide", "src.md", SOURCE_CONTENT, SOURCE_TITLE).unwrap()
    storage.bulk_insert_raw("notes", "rel.md", RELATED_CONTENT, "FastAPI Dependencies Notes").unwrap()


# ── fail-before: the double-load case ───────────────────────────────────────


def test_double_find_related_loads_corpus_once(tmp_storage: Storage, monkeypatch) -> None:
    _seed_source_and_related(tmp_storage)
    calls = _count_loads(tmp_storage, monkeypatch)

    first = tmp_storage.find_related("guide", "src.md").unwrap()
    second = tmp_storage.find_related("guide", "src.md").unwrap()

    assert len(calls) == 1, f"expected exactly one corpus load across two calls, got {len(calls)}: {calls}"
    assert first == second
    assert [r["file_path"] for r in second["related"]] == ["rel.md"]


# ── mutation paths are visible immediately ──────────────────────────────────


def test_update_document_visible_immediately_in_find_related(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "src.md", SOURCE_CONTENT, SOURCE_TITLE).unwrap()
    tmp_storage.bulk_insert_raw("guide", "cand.md", UNRELATED_CONTENT, "Tomatoes and Sun").unwrap()

    first = tmp_storage.find_related("guide", "src.md").unwrap()
    assert first["related"] == []  # tomatoes share no lexical signal

    tmp_storage.update_document("cand.md", RELATED_CONTENT, title="FastAPI Dependencies Notes", collection="guide").unwrap()
    second = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [r["file_path"] for r in second["related"]] == ["cand.md"]


def test_delete_document_visible_immediately(tmp_storage: Storage) -> None:
    _seed_source_and_related(tmp_storage)
    first = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [r["file_path"] for r in first["related"]] == ["rel.md"]

    tmp_storage.delete_document("rel.md", collection="notes").unwrap()
    second = tmp_storage.find_related("guide", "src.md").unwrap()
    assert second["related"] == []


def test_delete_documents_scoped_visible_immediately(tmp_storage: Storage) -> None:
    _seed_source_and_related(tmp_storage)
    first = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [r["file_path"] for r in first["related"]] == ["rel.md"]

    tmp_storage.delete_documents_scoped("rel.md", "notes").unwrap()
    second = tmp_storage.find_related("guide", "src.md").unwrap()
    assert second["related"] == []


def test_bulk_insert_visible_immediately_in_learn_topic(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "FastAPI basics intro", "Introduction to FastAPI").unwrap()
    first = tmp_storage.learn_topic("FastAPI", collection="guide").unwrap()
    assert [d["file_path"] for d in first["key_documents"]] == ["a.md"]

    tmp_storage.bulk_insert([{"collection": "guide", "path": "b.md", "content": "FastAPI routing and handlers", "title": "Routing"}]).unwrap()
    second = tmp_storage.learn_topic("FastAPI", collection="guide").unwrap()
    assert sorted(d["file_path"] for d in second["key_documents"]) == ["a.md", "b.md"]


def test_index_files_visible_immediately_in_learn_topic(tmp_storage: Storage) -> None:
    first = tmp_storage.learn_topic("FastAPI").unwrap()
    assert first["key_documents"] == []

    repo_dir = tmp_storage.repos_dir / "guide"
    repo_dir.mkdir(parents=True, exist_ok=True)
    notes = repo_dir / "notes.md"
    notes.write_text("FastAPI routing and handlers overview", encoding="utf-8")
    tmp_storage._index_files("guide", [notes], repo_dir, None)

    second = tmp_storage.learn_topic("FastAPI").unwrap()
    assert [d["file_path"] for d in second["key_documents"]] == ["notes.md"]


def test_remove_collection_visible_immediately(tmp_storage: Storage) -> None:
    _seed_source_and_related(tmp_storage)
    first = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [r["file_path"] for r in first["related"]] == ["rel.md"]

    tmp_storage.remove_collection("notes").unwrap()
    second = tmp_storage.find_related("guide", "src.md").unwrap()
    assert second["related"] == []


def test_rename_collection_visible_immediately(tmp_storage: Storage) -> None:
    _seed_source_and_related(tmp_storage)
    first = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [r["collection"] for r in first["related"]] == ["notes"]

    tmp_storage.rename_collection("notes", "renamed").unwrap()
    second = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [(r["collection"], r["file_path"]) for r in second["related"]] == [("renamed", "rel.md")]


def test_import_path_visible_immediately_in_learn_topic(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "a.md", "FastAPI basics intro", "Introduction to FastAPI").unwrap()
    first = tmp_storage.learn_topic("FastAPI").unwrap()
    assert [d["file_path"] for d in first["key_documents"]] == ["a.md"]

    syncer = Syncer(tmp_storage.data_dir / ".docshaven-sync")
    chunk_data = {
        "collections": {
            "guide": [{"file_path": "imported.md", "content": "FastAPI routing and handlers", "title": "Routing"}],
        }
    }
    syncer._import_chunk_data(chunk_data, tmp_storage)

    second = tmp_storage.learn_topic("FastAPI").unwrap()
    assert sorted(d["file_path"] for d in second["key_documents"]) == ["a.md", "imported.md"]


# ── instance-scoped cache state ─────────────────────────────────────────────


def test_two_instances_do_not_share_cache_state(tmp_storage: Storage, monkeypatch) -> None:
    tmp_storage.bulk_insert_raw("guide", "src.md", SOURCE_CONTENT, SOURCE_TITLE).unwrap()
    other = Storage(tmp_storage.data_dir)
    try:
        calls_first = _count_loads(tmp_storage, monkeypatch)
        calls_other = _count_loads(other, monkeypatch)

        tmp_storage.find_related("guide", "src.md").unwrap()
        other.find_related("guide", "src.md").unwrap()

        # Each instance built its own cache — one corpus load per instance.
        assert len(calls_first) == 1
        assert len(calls_other) == 1
        assert tmp_storage._corpus_cache is not other._corpus_cache

        # Invalidation is instance-scoped: a mutation on one instance does not
        # clear the other's cache (documented limitation, FUTURE_PLAN N4).
        other.bulk_insert_raw("guide", "rel.md", RELATED_CONTENT, "FastAPI Dependencies Notes").unwrap()
        assert other._corpus_cache is None
        assert tmp_storage._corpus_cache is not None
    finally:
        other.close()


# ── rebuilt cache returns fresh data ────────────────────────────────────────


def test_cache_rebuild_reflects_content_edit_in_scores(tmp_storage: Storage) -> None:
    tmp_storage.bulk_insert_raw("guide", "src.md", SOURCE_CONTENT, SOURCE_TITLE).unwrap()
    tmp_storage.bulk_insert_raw("guide", "cand.md", "Notes about deployment and servers", "FastAPI Dependencies Notes").unwrap()

    first = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [r["file_path"] for r in first["related"]] == ["cand.md"]
    baseline_similarity = first["related"][0]["relation"]["content_similarity"]

    tmp_storage.update_document("cand.md", RELATED_CONTENT, collection="guide").unwrap()
    second = tmp_storage.find_related("guide", "src.md").unwrap()
    assert [r["file_path"] for r in second["related"]] == ["cand.md"]
    fresh_similarity = second["related"][0]["relation"]["content_similarity"]
    assert fresh_similarity > baseline_similarity
    assert second["related"][0]["score"] > first["related"][0]["score"]
