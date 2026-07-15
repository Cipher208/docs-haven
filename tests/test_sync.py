"""Tests for Git Sync — compressed chunks."""

import tempfile
from pathlib import Path

import pytest

from sync import ChunkEntry, Manifest, Syncer


@pytest.fixture
def tmp_syncer():
    """Provide a clean temporary syncer."""
    with tempfile.TemporaryDirectory() as d:
        yield Syncer(Path(d))


class TestSyncer:
    def test_export_creates_chunk(self, tmp_syncer):
        result = tmp_syncer.export({"test": [{"name": "doc1"}]}, "testuser")
        assert result["isEmpty"] is False
        assert result["chunk_id"]
        assert result["collections"] == 1
        assert result["documents"] == 1

    def test_export_empty_no_chunk(self, tmp_syncer):
        result = tmp_syncer.export({}, "testuser")
        assert result["isEmpty"] is True

    def test_export_deduplicates(self, tmp_syncer):
        data = {"test": [{"name": "doc1"}]}
        r1 = tmp_syncer.export(data, "user")
        r2 = tmp_syncer.export(data, "user")
        assert r1["isEmpty"] is False
        assert r2["isEmpty"] is True  # Same content = same chunk ID

    def test_status(self, tmp_syncer):
        tmp_syncer.export({"test": [{"name": "doc1"}]}, "user")
        status = tmp_syncer.status()
        assert status["local_chunks"] == 1
        assert status["chunk_files"] == 1

    def test_import_chunks(self, tmp_syncer):
        tmp_syncer.export({"test": [{"name": "doc1"}, {"name": "doc2"}]}, "user")
        result = tmp_syncer.import_chunks()
        assert result["chunks_imported"] == 1
        assert result["documents_imported"] == 2


class TestManifest:
    def test_empty_manifest(self):
        m = Manifest()
        assert m.version == 1
        assert m.chunks == []

    def test_roundtrip(self):
        m = Manifest()
        m.chunks.append(ChunkEntry(
            id="abc12345",
            created_by="test",
            created_at="2026-01-01T00:00:00Z",
            collections=1,
            documents=5,
        ))
        d = m.to_dict()
        m2 = Manifest.from_dict(d)
        assert len(m2.chunks) == 1
        assert m2.chunks[0].id == "abc12345"
