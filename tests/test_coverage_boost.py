"""Tests to boost coverage to 90%+."""

from __future__ import annotations

from pathlib import Path

from storage import Storage, validate_collection, validate_file_mask, validate_query, validate_url


class TestValidation:
    def test_validate_url_valid(self):
        assert validate_url("https://github.com/user/repo.git") is None

    def test_validate_url_invalid_scheme(self):
        assert validate_url("ftp://invalid.com/repo") is not None

    def test_validate_url_too_long(self):
        assert validate_url("https://example.com/" + "a" * 2050) is not None

    def test_validate_url_dangerous_chars(self):
        assert validate_url("https://evil.com/repo\n--upload-pack=x") is not None

    def test_validate_url_encoded_traversal(self):
        assert validate_url("https://evil.com/%2e%2e/etc/passwd") is not None

    def test_validate_collection_valid(self):
        assert validate_collection("test") is None

    def test_validate_collection_empty(self):
        assert validate_collection("") is not None

    def test_validate_collection_too_long(self):
        assert validate_collection("a" * 256) is not None

    def test_validate_collection_invalid_chars(self):
        assert validate_collection("../../../etc") is not None

    def test_validate_query_valid(self):
        assert validate_query("test") is None

    def test_validate_query_too_long(self):
        assert validate_query("a" * 10001) is not None

    def test_validate_file_mask_valid(self):
        assert validate_file_mask("**/*.md") is None

    def test_validate_file_mask_traversal(self):
        assert validate_file_mask("../../../etc") is not None

    def test_validate_file_mask_slash(self):
        assert validate_file_mask("/etc/passwd") is not None


class TestStorageEdgeCases:
    def test_close(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.close()
        assert storage._conn is None

    def test_get_conn_reconnect(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.close()
        conn = storage._get_conn()
        assert conn is not None

    def test_list_documents_empty(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.list_documents()
        assert result.is_ok
        assert result.value == []

    def test_list_documents_by_collection(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert([
            {"collection": "a", "path": "a.md", "content": "A", "title": "A"},
            {"collection": "b", "path": "b.md", "content": "B", "title": "B"},
        ])
        result = storage.list_documents("a")
        assert result.is_ok
        assert len(result.value) == 1

    def test_stats_empty(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.stats()
        assert result.is_ok
        assert result.value["total_chunks"] == 0

    def test_stats_with_data(self, tmp_path: Path):
        storage = Storage(tmp_path)
        storage.bulk_insert([
            {"collection": "test", "path": "doc.md", "content": "content", "title": "Title"},
        ])
        result = storage.stats()
        assert result.is_ok
        assert result.value["total_documents"] == 1

    def test_delete_nonexistent(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.delete_document("nonexistent.md")
        assert result.is_ok

    def test_bulk_insert_empty(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.bulk_insert([])
        assert result.is_ok
        assert result.value == 0

    def test_config_broken_json(self, tmp_path: Path):
        (tmp_path / "config.json").write_text("not json {{{")
        storage = Storage(tmp_path)
        config = storage._load_config()
        assert "repos" in config

    def test_rename_invalid_names(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.rename_collection("../../../etc", "new")
        assert result.is_err
        result = storage.rename_collection("old", "../../../etc")
        assert result.is_err

    def test_remove_invalid_name(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.remove_collection("../../../etc")
        assert result.is_err

    def test_find_changed_docs_empty(self, tmp_path: Path):
        storage = Storage(tmp_path)
        changed = storage.find_changed_docs("nonexistent")
        assert changed == []

    def test_reindex_invalid_collection(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.reindex_collection("../../../etc")
        assert result.is_err

    def test_reindex_missing_repo(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = storage.reindex_collection("nonexistent")
        assert result.is_err
