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
        # All content should be covered
        combined = " ".join(chunks)
        assert "word" in combined

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
        assert boost > 0

    def test_unrelated_query_no_boost(self):
        result = {"title": "Random doc", "content": "Some content"}
        boost = type_boost("random query", result)
        assert boost == 0


# ── Storage ─────────────────────────────────────────────────────────────────


class TestStorage:
    def test_init_creates_db(self, tmp_storage):
        assert tmp_storage.db_path.exists()

    def test_stats_empty(self, tmp_storage):
        stats = tmp_storage.stats()
        assert stats["total_documents"] == 0
        assert stats["collections"] == 0

    def test_list_collections_empty(self, tmp_storage):
        collections = tmp_storage.list_collections()
        assert collections == []

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

        collections = tmp_storage.list_collections()
        by_name = {c["name"]: c for c in collections}
        assert by_name["core__fastapi"]["domain"] == "core"
        assert by_name["misc"]["domain"] is None

    def test_get_nonexistent(self, tmp_storage):
        result = tmp_storage.get("nonexistent.md")
        assert result is None

    def test_search_empty(self, tmp_storage):
        results = tmp_storage.search("test query")
        assert results == []


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

        results = tmp_storage.search("FastAPI")
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

        results = tmp_storage.search("tutorial", collections=["fastapi"])
        assert all(r["collection"] == "fastapi" for r in results)

    def test_get_document(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "README.md", "# Test Repo\nContent here", "Test Repo"),
        )
        conn.commit()

        doc = tmp_storage.get("README.md")
        assert doc is not None
        assert "Test Repo" in doc["content"]

    def test_search_hybrid_strategy(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "How to use FastAPI dependency injection", "FastAPI DI Guide"),
        )
        conn.commit()

        results = tmp_storage.search("FastAPI dependency injection", strategy="hybrid")
        assert len(results) >= 1
        assert results[0]["score"] > 0

    def test_search_explain(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "guide.md", "FastAPI dependency injection tutorial", "FastAPI Guide"),
        )
        conn.commit()

        results = tmp_storage.search("FastAPI", explain=True)
        assert len(results) > 0
        r = results[0]
        assert "explain" in r
        assert "base_score" in r["explain"]
        assert "type_boost" in r["explain"]
        assert "final_score" in r["explain"]
        assert "source" in r["explain"]

    def test_search_no_explain_by_default(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Test content", "Test"),
        )
        conn.commit()

        results = tmp_storage.search("Test")
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

        stale = tmp_storage.check_stale("test")
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

        stale = tmp_storage.check_stale("test")
        assert len(stale) == 1
        assert stale[0]["reason"] == "symlink_skipped"

    def test_search_empty_query(self, tmp_storage):
        results = tmp_storage.search("")
        assert results == []

    def test_search_sql_injection(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Normal content", "Normal"),
        )
        conn.commit()
        results = tmp_storage.search('"; DROP TABLE documents; --')
        assert isinstance(results, list)

    def test_search_limit_zero(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Content", "Title"),
        )
        conn.commit()
        results = tmp_storage.search("Content", limit=0)
        assert results == []

    def test_auto_strategy_boundary(self):
        from storage import auto_strategy
        assert auto_strategy("a") == "fts"
        assert auto_strategy("a b") == "fts"
        assert auto_strategy("a b c") == "hybrid"
