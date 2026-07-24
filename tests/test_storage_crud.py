"""Tests for Storage CRUD operations, repos, stale detection, config, reindex."""

import tempfile
from pathlib import Path

import pytest

from storage import Storage, importance_boost


@pytest.fixture
def tmp_storage():
    """Provide a clean temporary storage."""
    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))
        yield storage
        storage.close()


# ── CRUD ────────────────────────────────────────────────────────────────────


class TestUpdateDelete:
    def test_update_document_with_title(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "old content", "Old Title"),
        )
        conn.commit()

        result = tmp_storage.update_document("doc.md", "new content", title="New Title")
        assert result.is_ok

        doc = tmp_storage.get("doc.md")
        assert doc.is_ok
        assert doc.value["content"] == "new content"
        assert doc.value["title"] == "New Title"

    def test_update_document_without_title(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "old content", "Title"),
        )
        conn.commit()

        result = tmp_storage.update_document("doc.md", "new content")
        assert result.is_ok

        doc = tmp_storage.get("doc.md")
        assert doc.is_ok
        assert doc.value["content"] == "new content"
        assert doc.value["title"] == "Title"

    def test_delete_document(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "content", "Title"),
        )
        conn.commit()

        result = tmp_storage.delete_document("doc.md")
        assert result.is_ok

        doc = tmp_storage.get("doc.md")
        assert doc.is_err

    def test_get_with_chunk(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title, chunk_index, total_chunks) VALUES (?, ?, ?, ?, ?, ?)",
            ("test", "doc.md", "Chunk 0 content", "Title", 0, 2),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title, chunk_index, total_chunks) VALUES (?, ?, ?, ?, ?, ?)",
            ("test", "doc.md", "Chunk 1 content", "Title", 1, 2),
        )
        conn.commit()

        result = tmp_storage.get("doc.md", chunk=0)
        assert result.is_ok
        assert result.value["content"] == "Chunk 0 content"
        assert result.value["chunk_index"] == 0

    def test_get_with_collection(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("core__fastapi", "guide.md", "FastAPI guide", "Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("guide__pytest", "guide.md", "Pytest guide", "Guide"),
        )
        conn.commit()

        result = tmp_storage.get("guide.md", collection="core__fastapi")
        assert result.is_ok
        assert result.value["collection"] == "core__fastapi"

    def test_get_multi_chunk_assembly(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title, chunk_index, total_chunks) VALUES (?, ?, ?, ?, ?, ?)",
            ("test", "doc.md", "First part.", "Title", 0, 2),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title, chunk_index, total_chunks) VALUES (?, ?, ?, ?, ?, ?)",
            ("test", "doc.md", "Second part.", "Title", 1, 2),
        )
        conn.commit()

        result = tmp_storage.get("doc.md")
        assert result.is_ok
        assert "First part." in result.value["content"]
        assert "Second part." in result.value["content"]
        assert result.value["chunks"] == 2

    def test_list_documents_all(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title, chunk_index) VALUES (?, ?, ?, ?, ?)", ("a", "a.md", "A", "A", 0))
        conn.execute("INSERT INTO documents (collection, file_path, content, title, chunk_index) VALUES (?, ?, ?, ?, ?)", ("b", "b.md", "B", "B", 0))
        conn.commit()
        result = tmp_storage.list_documents()
        assert result.is_ok
        assert len(result.value) == 2

    def test_list_documents_by_collection(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title, chunk_index) VALUES (?, ?, ?, ?, ?)", ("a", "a.md", "A", "A", 0))
        conn.execute("INSERT INTO documents (collection, file_path, content, title, chunk_index) VALUES (?, ?, ?, ?, ?)", ("b", "b.md", "B", "B", 0))
        conn.commit()
        result = tmp_storage.list_documents("a")
        assert result.is_ok
        assert len(result.value) == 1

    def test_update_document_with_title_v2(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", "doc.md", "old", "Old"))
        conn.commit()
        result = tmp_storage.update_document("doc.md", "new content", title="New")
        assert result.is_ok
        assert result.value["status"] == "updated"

    def test_delete_nonexistent(self, tmp_storage):
        result = tmp_storage.delete_document("nonexistent.md")
        assert result.is_ok  # Delete is idempotent
        assert result.value["status"] == "deleted"

    def test_record_judgment(self, tmp_storage):
        result = tmp_storage.record_judgment("new_id", "old_id", "supersedes")
        assert result.is_ok
        assert result.value["judgment"] == "supersedes"

    def test_bulk_insert_empty(self, tmp_storage):
        result = tmp_storage.bulk_insert([])
        assert result.is_ok
        assert result.value == 0

    def test_get_nonexistent(self, tmp_storage):
        result = tmp_storage.get("nonexistent.md")
        assert result.is_err

    def test_list_documents_empty(self, tmp_storage):
        result = tmp_storage.list_documents()
        assert result.is_ok
        assert result.value == []

    def test_close(self, tmp_storage):
        tmp_storage.close()
        assert getattr(tmp_storage._local, "conn", None) is None

    def test_get_conn_reconnect(self, tmp_storage):
        tmp_storage.close()
        conn = tmp_storage._get_conn()
        assert conn is not None


# ── Config Persistence ──────────────────────────────────────────────────────


class TestConfigPersistence:
    def test_config_save_load(self, tmp_storage):
        config = tmp_storage._load_config()
        config["repos"]["test"] = {"url": "https://example.com/repo"}
        tmp_storage._save_config(config)
        loaded = tmp_storage._load_config()
        assert loaded["repos"]["test"]["url"] == "https://example.com/repo"

    def test_config_broken_json(self, tmp_path: Path):
        tmp_path / "config.json"
        (tmp_path / "config.json").write_text("not json {{{")
        storage = Storage(tmp_path)
        config = storage._load_config()
        assert "repos" in config


# ── Repo Operations ─────────────────────────────────────────────────────────


class TestRepoOperations:
    def test_add_repo_invalid_url(self, tmp_storage):
        result = tmp_storage.add_repo("ftp://invalid.com/repo")
        assert result.is_err
        assert "Invalid URL scheme" in result.error

    def test_add_repo_path_traversal_mask(self, tmp_storage):
        # Create a fake repo dir so clone is skipped
        repo_dir = tmp_storage.repos_dir / "repo"
        repo_dir.mkdir()

        result = tmp_storage.add_repo("https://github.com/test/repo", mask="../../etc/passwd")
        assert result.is_err
        assert "path traversal" in result.error.lower()

    def test_add_repo_with_mocked_clone(self, tmp_storage):
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("storage.subprocess.run", return_value=mock_result):
            # Create repo dir after "clone"
            repo_dir = tmp_storage.repos_dir / "test-repo"
            repo_dir.mkdir(exist_ok=True)
            (repo_dir / "README.md").write_text("# Test\nContent here")

            result = tmp_storage.add_repo("https://github.com/test/test-repo", description="Test repo")
            assert result.is_ok
            assert result.value["name"] == "test-repo"
            assert result.value["files_indexed"] == 1

    def test_add_repo_clone_failure(self, tmp_storage):
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "fatal: repository not found"

        with patch("storage.subprocess.run", return_value=mock_result):
            result = tmp_storage.add_repo("https://github.com/nonexistent/repo")
            assert result.is_err
            assert "Clone failed" in result.error

    def test_add_repo_invalid_url_v2(self, tmp_storage):
        result = tmp_storage.add_repo("ftp://invalid.com/repo")
        assert result.is_err

    def test_add_repo_long_url(self, tmp_storage):
        result = tmp_storage.add_repo("https://example.com/" + "a" * 2050)
        assert result.is_err
        assert result.error

    def test_add_repo_invalid_mask(self, tmp_storage):
        result = tmp_storage.add_repo("https://example.com/repo", mask="../../../etc")
        assert result.is_err
        assert result.error


# ── Stale Detection ─────────────────────────────────────────────────────────


class TestStaleDetection:
    def test_check_stale_content_changed(self, tmp_storage):
        import hashlib

        repo_dir = tmp_storage.repos_dir / "test"
        repo_dir.mkdir()
        (repo_dir / "doc.md").write_text("original content")

        original_hash = hashlib.sha256(b"original content").hexdigest()
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, content_hash, title) VALUES (?, ?, ?, ?, ?)",
            ("test", "doc.md", "original content", original_hash, "Doc"),
        )
        conn.commit()

        # Modify the file
        (repo_dir / "doc.md").write_text("modified content")

        result = tmp_storage.check_stale("test")
        assert result.is_ok
        stale = result.value
        assert len(stale) == 1
        assert stale[0]["reason"] == "content_changed"

    def test_check_stale_nonexistent_collection(self, tmp_storage):
        result = tmp_storage.check_stale("nonexistent")
        assert result.is_ok
        assert result.value == []


# ── Reindex ─────────────────────────────────────────────────────────────────


class TestReindex:
    def test_find_changed_docs_collection(self, tmp_storage):
        repo_dir = tmp_storage.repos_dir / "test"
        repo_dir.mkdir()
        changed = tmp_storage.find_changed_docs("test", repo_dir)
        assert changed == []

    def test_reindex_invalid_collection(self, tmp_storage):
        result = tmp_storage.reindex_collection("../../../etc")
        assert result.is_err
        assert result.error

    def test_reindex_missing_repo(self, tmp_storage):
        result = tmp_storage.reindex_collection("nonexistent")
        assert result.is_err
        assert "not found" in result.error.lower()


# ── Importance Scoring ────────────────────────────────────────────────────


def test_importance_boost_recency():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).timestamp()
    new_doc = {"created_at": datetime.now(timezone.utc).isoformat(), "retrieval_count": 0}
    old_doc = {"created_at": "2020-01-01T00:00:00+00:00", "retrieval_count": 0}

    new_boost = importance_boost(new_doc, now=now)
    old_boost = importance_boost(old_doc, now=now)

    assert new_boost > old_boost
    assert new_boost > 0
    assert old_boost < 0.01


def test_importance_boost_frequency():
    no_retrievals = {"retrieval_count": 0}
    few_retrievals = {"retrieval_count": 5}
    many_retrievals = {"retrieval_count": 100}

    assert importance_boost(no_retrievals) == 0
    assert importance_boost(few_retrievals) > importance_boost(no_retrievals)
    assert importance_boost(many_retrievals) > importance_boost(few_retrievals)


def test_search_explain_includes_importance():
    storage = Storage(Path(tempfile.mkdtemp()))
    storage.bulk_insert([{"collection": "test", "path": "a.md", "content": "hello world", "title": "Test"}])
    result = storage.search("hello", explain=True)
    assert result.is_ok
    assert len(result.value) > 0
    assert "importance_boost" in result.value[0].get("explain", {})
    storage.close()


def test_retrieval_count_increments():
    storage = Storage(Path(tempfile.mkdtemp()))
    storage.bulk_insert([{"collection": "test", "path": "a.md", "content": "hello world", "title": "Test"}])
    # First search
    storage.search("hello")
    conn = storage._get_conn()
    row = conn.execute("SELECT retrieval_count FROM documents WHERE file_path = 'a.md'").fetchone()
    assert row["retrieval_count"] == 1
    # Second search
    storage.search("hello")
    row = conn.execute("SELECT retrieval_count FROM documents WHERE file_path = 'a.md'").fetchone()
    assert row["retrieval_count"] == 2
    storage.close()
