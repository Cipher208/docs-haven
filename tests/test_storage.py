"""Tests for Storage — SQLite FTS5 backend."""

import tempfile
from pathlib import Path

import pytest
from conftest import insert_doc

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
        # Lazy init: DB created on first operation
        tmp_storage.list_documents()
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
        insert_doc(tmp_storage, "core__fastapi", "guide.md", "FastAPI guide", "FastAPI Guide")
        insert_doc(tmp_storage, "misc", "notes.md", "Notes", "Notes")

        result = tmp_storage.list_collections()
        assert result.is_ok
        collections = result.value
        by_name = {c["name"]: c for c in collections}
        assert by_name["core__fastapi"]["domain"] == "core"
        assert by_name["misc"]["domain"] is None

    def test_search_empty(self, tmp_storage):
        result = tmp_storage.search("test query")
        assert result.is_ok
        assert result.value == []


class TestStorageSearch:
    def test_add_documents_and_search(self, tmp_storage):
        insert_doc(tmp_storage, "fastapi", "guide.md", "FastAPI dependency injection tutorial", "FastAPI Guide")
        insert_doc(tmp_storage, "fastapi", "api.md", "FastAPI REST API endpoints", "FastAPI API")

        result = tmp_storage.search("FastAPI")
        assert result.is_ok
        results = result.value
        assert len(results) >= 1
        assert results[0]["title"] == "FastAPI Guide"
        assert results[0]["score"] > 0

    def test_search_by_collection(self, tmp_storage):
        insert_doc(tmp_storage, "fastapi", "guide.md", "FastAPI tutorial", "Guide")
        insert_doc(tmp_storage, "sqlalchemy", "orm.md", "SQLAlchemy ORM guide", "ORM Guide")

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
        insert_doc(tmp_storage, "fastapi", "guide.md", "FastAPI dependency injection tutorial", "FastAPI Guide")

        result = tmp_storage.search("FastAPI", explain=True)
        assert result.is_ok
        results = result.value
        assert len(results) > 0
        r = results[0]
        assert "explain" in r
        e = r["explain"]
        assert isinstance(e["base_score"], (int, float))
        assert isinstance(e["type_boost"], (int, float))
        assert isinstance(e["importance_boost"], (int, float))
        assert isinstance(e["final_score"], (int, float))
        assert e["source"] in ("fts5", "like")
        assert e["final_score"] == e["base_score"] + e["type_boost"] + e["importance_boost"]

    def test_search_no_explain_by_default(self, tmp_storage):
        insert_doc(tmp_storage, "test", "doc.md", "Test content", "Test")

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


# ── Search Strategies ───────────────────────────────────────────────────────


class TestSearchStrategies:
    def test_search_with_min_score(self, tmp_storage):
        # Insert docs with varying relevance to FastAPI
        insert_doc(tmp_storage, "test", "fastapi.md", "FastAPI is a Python web framework for building APIs", "FastAPI")
        insert_doc(tmp_storage, "test", "unrelated.md", "The weather is nice today and birds are singing", "Weather")

        result = tmp_storage.search("FastAPI", min_score=0.9)
        assert result.is_ok
        # Every returned result must meet the threshold
        for r in result.value:
            assert r["score"] >= 0.9, f"Result {r['title']} has score {r['score']} below min_score 0.9"
        # The unrelated doc should not appear
        titles = [r["title"] for r in result.value]
        assert "Weather" not in titles

    def test_search_with_limit(self, tmp_storage):
        conn = tmp_storage._get_conn()
        for i in range(5):
            conn.execute(
                "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
                ("test", f"doc{i}.md", f"Content {i}", f"Doc {i}"),
            )
        conn.commit()
        result = tmp_storage.search("content", limit=2)
        assert result.is_ok
        assert len(result.value) <= 2

    def test_search_vector_strategy(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", "doc.md", "FastAPI middleware", "Auth")
        )
        conn.commit()
        result = tmp_storage.search("middleware", strategy="vector")
        assert result.is_ok
        assert isinstance(result.value, list)


# ── Context Validation ──────────────────────────────────────────────────────


class TestContext:
    def test_context_empty_path(self, tmp_storage):
        result = tmp_storage.add_context("test", "", "summary")
        assert result.is_err

    def test_context_empty_summary(self, tmp_storage):
        result = tmp_storage.add_context("test", "path", "")
        assert result.is_err


# ── Rename Validation ───────────────────────────────────────────────────────


class TestRename:
    def test_rename_invalid_names(self, tmp_storage):
        result = tmp_storage.rename_collection("../../../etc", "new")
        assert result.is_err
        assert result.error
        result = tmp_storage.rename_collection("old", "../../../etc")
        assert result.is_err
        assert result.error


# ── Stats ───────────────────────────────────────────────────────────────────


class TestStats:
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

    def test_stats_with_data_v2(self, tmp_storage):
        conn = tmp_storage._get_conn()
        conn.execute("INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)", ("test", "doc.md", "content", "Title"))
        conn.commit()
        result = tmp_storage.stats()
        assert result.is_ok
        assert result.value["total_documents"] == 1

    def test_stats_empty_chunks(self, tmp_storage):
        result = tmp_storage.stats()
        assert result.is_ok
        assert result.value["total_chunks"] == 0



