"""Tests for Storage — SQLite FTS5 backend."""

import tempfile
from pathlib import Path

import pytest

from storage import Storage, auto_strategy, chunk_text, type_boost


@pytest.fixture
def tmp_storage():
    """Provide a clean temporary storage."""
    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))
        yield storage
        storage.close()


@pytest.fixture
def sample_repo(tmp_storage):
    """Create a sample repo with test documents."""
    repo_dir = tmp_storage.repos_dir / "test-repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Test Repo\n\nThis is a test repository.")
    (repo_dir / "guide.md").write_text("# Guide\n\nStep by step tutorial.")
    (repo_dir / "api.md").write_text("# API Reference\n\nEndpoint: GET /users")
    return repo_dir


# ── Chunking ────────────────────────────────────────────────────────────────


class TestChunking:
    def test_short_text_no_chunks(self):
        text = "Short text"
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_chunked(self):
        text = "word " * 500  # ~2500 chars
        chunks = chunk_text(text, chunk_size=500, overlap=100)
        assert len(chunks) > 1
        # All content should be preserved across chunks
        combined = " ".join(chunks)
        assert "word" in combined
        assert len(combined) >= len(text) - 100  # Allow for overlap trimming

    def test_empty_text(self):
        chunks = chunk_text("")
        assert chunks == []


# ── Auto Strategy ───────────────────────────────────────────────────────────


class TestAutoStrategy:
    def test_short_query(self):
        assert auto_strategy("fastapi") == "fts"
        assert auto_strategy("python async") == "fts"

    def test_long_query(self):
        assert auto_strategy("how to use dependency injection") == "hybrid"
        assert auto_strategy("fastapi middleware authentication") == "hybrid"


# ── Type Boost ──────────────────────────────────────────────────────────────


class TestTypeBoost:
    def test_api_query_boosts_api_doc(self):
        result = {"title": "API endpoints", "content": "GET /users"}
        boost = type_boost("api endpoint", result)
        assert boost == 0.15

    def test_unrelated_query_no_boost(self):
        result = {"title": "Random doc", "content": "Some content"}
        boost = type_boost("random query", result)
        assert boost == 0


# ── Storage ─────────────────────────────────────────────────────────────────


class TestStorage:
    def test_init_creates_db(self, tmp_storage):
        assert tmp_storage.db_path.exists()

    def test_stats_empty(self, tmp_storage):
        result = tmp_storage.stats()
        assert result.is_ok
        stats = result.value
        assert stats["total_documents"] == 0
        assert stats["collections"] == 0

    def test_list_collections_empty(self, tmp_storage):
        result = tmp_storage.list_collections()
        assert result.is_ok
        assert result.value == []

    def test_list_collections_domain(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("core__fastapi", "guide.md", "FastAPI guide", "FastAPI Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("misc", "notes.md", "Notes", "Notes"),
        )
        conn.commit()

        result = tmp_storage.list_collections()
        assert result.is_ok
        collections = result.value
        by_name = {c["name"]: c for c in collections}
        assert by_name["core__fastapi"]["domain"] == "core"
        assert by_name["misc"]["domain"] is None

    def test_get_nonexistent(self, tmp_storage):
        result = tmp_storage.get("nonexistent.md")
        assert result.is_err

    def test_search_empty(self, tmp_storage):
        result = tmp_storage.search("test query")
        assert result.is_ok
        assert result.value == []


class TestStorageSearch:
    def test_add_documents_and_search(self, tmp_storage):
        # Add documents directly to DB
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "guide.md", "FastAPI dependency injection tutorial", "FastAPI Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "api.md", "FastAPI REST API endpoints", "FastAPI API"),
        )
        conn.commit()

        result = tmp_storage.search("FastAPI")
        assert result.is_ok
        results = result.value
        assert len(results) >= 1
        assert results[0]["title"] == "FastAPI Guide"
        assert results[0]["score"] > 0

    def test_search_by_collection(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "guide.md", "FastAPI tutorial", "Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("sqlalchemy", "orm.md", "SQLAlchemy ORM guide", "ORM Guide"),
        )
        conn.commit()

        result = tmp_storage.search("tutorial", collections=["fastapi"])
        assert result.is_ok
        results = result.value
        assert all(r["collection"] == "fastapi" for r in results)

    def test_get_document(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "README.md", "# Test Repo\nContent here", "Test Repo"),
        )
        conn.commit()

        result = tmp_storage.get("README.md")
        assert result.is_ok
        doc = result.value
        assert "Test Repo" in doc["content"]

    def test_search_hybrid_strategy(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "How to use FastAPI dependency injection", "FastAPI DI Guide"),
        )
        conn.commit()

        result = tmp_storage.search("FastAPI dependency injection", strategy="hybrid")
        assert result.is_ok
        results = result.value
        assert len(results) >= 1
        assert results[0]["score"] > 0

    def test_search_explain(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "guide.md", "FastAPI dependency injection tutorial", "FastAPI Guide"),
        )
        conn.commit()

        result = tmp_storage.search("FastAPI", explain=True)
        assert result.is_ok
        results = result.value
        assert len(results) > 0
        r = results[0]
        assert "explain" in r
        e = r["explain"]
        assert isinstance(e["base_score"], (int, float))
        assert isinstance(e["type_boost"], (int, float))
        assert isinstance(e["final_score"], (int, float))
        assert e["source"] in ("fts5", "like")
        assert e["final_score"] == e["base_score"] + e["type_boost"]

    def test_search_no_explain_by_default(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Test content", "Test"),
        )
        conn.commit()

        result = tmp_storage.search("Test")
        assert result.is_ok
        results = result.value
        assert len(results) > 0
        assert "explain" not in results[0]

    def test_check_stale_deleted_file(self, tmp_storage):
        repo_dir = tmp_storage.repos_dir / "test"
        repo_dir.mkdir()

        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, content_hash, title) VALUES (?, ?, ?, ?, ?)",
            ("test", "deleted.md", "content", "hash123", "Deleted"),
        )
        conn.commit()

        result = tmp_storage.check_stale("test")
        assert result.is_ok
        stale = result.value
        assert len(stale) == 1
        assert stale[0]["reason"] == "file_deleted"

    def test_check_stale_symlink(self, tmp_storage):
        conn = tmp_storage._get_conn()
        repo_dir = tmp_storage.repos_dir / "test"
        repo_dir.mkdir()
        symlink = repo_dir / "link.md"
        symlink.symlink_to(repo_dir / "nonexistent.md")

        conn.execute(
            "INSERT INTO documents (collection, file_path, content, content_hash, title) VALUES (?, ?, ?, ?, ?)",
            ("test", "link.md", "content", "hash123", "Link"),
        )
        conn.commit()

        result = tmp_storage.check_stale("test")
        assert result.is_ok
        stale = result.value
        assert len(stale) == 1
        assert stale[0]["reason"] == "symlink_skipped"

    def test_search_empty_query(self, tmp_storage):
        result = tmp_storage.search("")
        assert result.is_ok
        assert result.value == []

    def test_search_sql_injection(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Normal content", "Normal"),
        )
        conn.commit()
        result = tmp_storage.search('"; DROP TABLE documents; --')
        assert result.is_ok
        assert isinstance(result.value, list)

    def test_search_limit_zero(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Content", "Title"),
        )
        conn.commit()
        result = tmp_storage.search("Content", limit=0)
        assert result.is_ok
        assert result.value == []

    def test_auto_strategy_boundary(self):
        from storage import auto_strategy

        assert auto_strategy("a") == "fts"
        assert auto_strategy("a b") == "fts"
        assert auto_strategy("a b c") == "hybrid"


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

    def test_stats_with_data(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "content", "Title"),
        )
        conn.commit()

        result = tmp_storage.stats()
        assert result.is_ok
        stats = result.value
        assert stats["total_documents"] == 1
        assert stats["total_chunks"] == 1
        assert stats["collections"] == 1
        assert stats["db_size_kb"] > 0

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

    def test_delete_nonexistent(self, tmp_storage):
        result = tmp_storage.delete_document("nonexistent.md")
        assert result.is_ok  # Delete is idempotent

    def test_record_judgment(self, tmp_storage):
        result = tmp_storage.record_judgment("new_id", "old_id", "supersedes")
        assert result.is_ok
        assert result.value["judgment"] == "supersedes"

    def test_bulk_insert_empty(self, tmp_storage):
        result = tmp_storage.bulk_insert([])
        assert result.is_ok
        assert result.value == 0

    def test_stats_with_data_v2(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", "doc.md", "content", "Title"))
        conn.commit()
        result = tmp_storage.stats()
        assert result.is_ok
        assert result.value["total_documents"] == 1

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

    def test_search_with_min_score(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", "doc.md", "FastAPI tutorial guide", "FastAPI"))
        conn.commit()
        result = tmp_storage.search("FastAPI", min_score=0.9)
        assert result.is_ok
        # May or may not find results depending on score threshold

    def test_search_with_limit(self, tmp_storage):
        conn = tmp_storage._get_conn()
        for i in range(5):
            conn.execute("INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", f"doc{i}.md", f"Content {i}", f"Doc {i}"))
        conn.commit()
        result = tmp_storage.search("content", limit=2)
        assert result.is_ok
        assert len(result.value) <= 2

    def test_search_hybrid_strategy(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", "doc.md", "FastAPI middleware authentication", "Auth"))
        conn.commit()
        result = tmp_storage.search("FastAPI middleware", strategy="hybrid")
        assert result.is_ok

    def test_search_vector_strategy(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", "doc.md", "FastAPI middleware", "Auth"))
        conn.commit()
        result = tmp_storage.search("middleware", strategy="vector")
        assert result.is_ok

    def test_add_repo_invalid_url_v2(self, tmp_storage):
        result = tmp_storage.add_repo("ftp://invalid.com/repo")
        assert result.is_err

    def test_add_repo_long_url(self, tmp_storage):
        result = tmp_storage.add_repo("https://example.com/" + "a" * 2050)
        assert result.is_err

    def test_add_repo_invalid_mask(self, tmp_storage):
        result = tmp_storage.add_repo("https://example.com/repo", mask="../../../etc")
        assert result.is_err

    def test_get_nonexistent(self, tmp_storage):
        result = tmp_storage.get("nonexistent.md")
        assert result.is_err

    def test_context_empty_path(self, tmp_storage):
        result = tmp_storage.add_context("test", "", "summary")
        assert result.is_err

    def test_context_empty_summary(self, tmp_storage):
        result = tmp_storage.add_context("test", "path", "")
        assert result.is_err

    def test_rename_invalid_names(self, tmp_storage):
        result = tmp_storage.rename_collection("../../../etc", "new")
        assert result.is_err
        result = tmp_storage.rename_collection("old", "../../../etc")
        assert result.is_err

    def test_find_changed_docs_collection(self, tmp_storage):
        repo_dir = tmp_storage.repos_dir / "test"
        repo_dir.mkdir()
        changed = tmp_storage.find_changed_docs("test", repo_dir)
        assert changed == []

    def test_reindex_invalid_collection(self, tmp_storage):
        result = tmp_storage.reindex_collection("../../../etc")
        assert result.is_err

    def test_reindex_missing_repo(self, tmp_storage):
        result = tmp_storage.reindex_collection("nonexistent")
        assert result.is_err

    def test_list_documents_empty(self, tmp_storage):
        result = tmp_storage.list_documents()
        assert result.is_ok
        assert result.value == []

    def test_stats_empty(self, tmp_storage):
        result = tmp_storage.stats()
        assert result.is_ok
        assert result.value["total_chunks"] == 0

    def test_close(self, tmp_storage):
        tmp_storage.close()
        assert tmp_storage._conn is None

    def test_get_conn_reconnect(self, tmp_storage):
        tmp_storage.close()
        conn = tmp_storage._get_conn()
        assert conn is not None
