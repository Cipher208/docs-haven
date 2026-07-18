"""End-to-end tests — full workflow for each MCP tool."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from storage import Storage
from conflicts import ConflictDetector
from sync import Syncer
from uri import URIRouter


@pytest.fixture
def e2e_storage():
    """Provide a real storage instance for E2E testing."""
    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))
        yield storage
        storage.close()


class TestE2ESearch:
    """Full workflow: add docs → search → verify results."""

    def test_search_after_indexing(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "guide.md", "FastAPI dependency injection tutorial", "FastAPI Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "api.md", "FastAPI REST API endpoints", "FastAPI API"),
        )
        conn.commit()

        result = e2e_storage.search("FastAPI")
        assert result.is_ok()
        assert len(result.value) >= 1
        assert any("FastAPI" in r["title"] for r in result.value)

    def test_search_with_collection_filter(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("fastapi", "guide.md", "FastAPI guide", "Guide"),
        )
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("sqlalchemy", "orm.md", "SQLAlchemy ORM", "ORM"),
        )
        conn.commit()

        result = e2e_storage.search("guide", collections=["fastapi"])
        assert result.is_ok()
        assert all(r["collection"] == "fastapi" for r in result.value)

    def test_search_hybrid_strategy(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "How to use FastAPI dependency injection", "FastAPI DI"),
        )
        conn.commit()

        result = e2e_storage.search("FastAPI dependency injection", strategy="hybrid")
        assert result.is_ok()
        assert len(result.value) >= 1

    def test_search_with_explain(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "FastAPI guide", "Guide"),
        )
        conn.commit()

        result = e2e_storage.search("FastAPI", explain=True)
        assert result.is_ok()
        r = result.value[0]
        assert "explain" in r
        assert "base_score" in r["explain"]
        assert "source" in r["explain"]

    def test_search_with_min_score(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "FastAPI guide", "Guide"),
        )
        conn.commit()

        result = e2e_storage.search("FastAPI", min_score=0.5)
        assert result.is_ok()
        # With high min_score, some results may be filtered out
        for r in result.value:
            assert r["score"] >= 0.5


class TestE2ECRUD:
    """Full workflow: create → read → update → delete."""

    def test_full_crud_cycle(self, e2e_storage):
        # Create
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Original content", "Original Title"),
        )
        conn.commit()

        # Read
        result = e2e_storage.get("doc.md")
        assert result.is_ok()
        assert result.value["content"] == "Original content"

        # Update
        update_result = e2e_storage.update_document("doc.md", "Updated content", title="Updated Title")
        assert update_result.is_ok()

        # Verify update
        result = e2e_storage.get("doc.md")
        assert result.is_ok()
        assert result.value["content"] == "Updated content"
        assert result.value["title"] == "Updated Title"

        # Delete
        delete_result = e2e_storage.delete_document("doc.md")
        assert delete_result.is_ok()

        # Verify deletion
        result = e2e_storage.get("doc.md")
        assert result.is_err()


class TestE2EConflictDetection:
    """Full workflow: add docs → detect conflicts → judge."""

    def test_conflict_workflow(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "guide.md", "FastAPI tutorial", "FastAPI Guide"),
        )
        conn.commit()

        detector = ConflictDetector(e2e_storage)
        result = detector.detect("FastAPI Guide", "")
        assert isinstance(result, dict) or hasattr(result, "has_conflicts")

    def test_judge_persists(self, e2e_storage):
        detector = ConflictDetector(e2e_storage)
        result = detector.judge("doc1", "doc2", "supersedes")
        assert result.get("status") == "recorded"


class TestE2ESync:
    """Full workflow: export → import → verify."""

    def test_sync_roundtrip(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("test", "doc.md", "Content", "Title"),
        )
        conn.commit()

        collections = e2e_storage.list_collections()
        assert collections.is_ok()

        with tempfile.TemporaryDirectory() as sync_dir:
            syncer = Syncer(Path(sync_dir))
            export_result = syncer.export(
                {c["name"]: [] for c in collections.value},
                created_by="test",
            )
            assert export_result.get("isEmpty") is True or "chunk_id" in export_result


class TestE2EURI:
    """Full workflow: resolve → search → list."""

    def test_uri_workflow(self, e2e_storage):
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
            ("core__fastapi", "guide.md", "FastAPI guide", "Guide"),
        )
        conn.commit()

        router = URIRouter(e2e_storage)

        # Resolve
        resolved = router.resolve("core://fastapi/guide")
        assert resolved["domain"] == "core"
        assert resolved["collection"] == "core__fastapi"

        # Search
        results = router.search_by_uri("core://fastapi")
        assert len(results) >= 1

        # List domains
        domains = router.list_all_domains()
        assert "core" in domains


class TestE2EConfig:
    """Full workflow: add repo → check config → verify persistence."""

    def test_config_persistence(self, e2e_storage):
        from unittest.mock import patch, MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("storage.subprocess.run", return_value=mock_result):
            repo_dir = e2e_storage.repos_dir / "test-repo"
            repo_dir.mkdir(exist_ok=True)
            (repo_dir / "README.md").write_text("# Test")

            result = e2e_storage.add_repo("https://github.com/test/test-repo")
            assert result.is_ok()

            # Verify config was saved
            config = e2e_storage._load_config()
            assert "test-repo" in config.get("repos", {})


class TestE2ECheckStale:
    """Full workflow: index → modify file → check stale."""

    def test_stale_detection_workflow(self, e2e_storage):
        import hashlib

        repo_dir = e2e_storage.repos_dir / "test"
        repo_dir.mkdir()
        (repo_dir / "doc.md").write_text("original content")

        original_hash = hashlib.sha256(b"original content").hexdigest()
        conn = e2e_storage._get_conn()
        conn.execute(
            "INSERT INTO documents (collection, file_path, content, content_hash, title) VALUES (?, ?, ?, ?, ?)",
            ("test", "doc.md", "original content", original_hash, "Doc"),
        )
        conn.commit()

        # Modify file
        (repo_dir / "doc.md").write_text("modified content")

        # Check stale
        result = e2e_storage.check_stale("test")
        assert result.is_ok()
        assert len(result.value) == 1
        assert result.value[0]["reason"] == "content_changed"
