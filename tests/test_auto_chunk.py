"""Tests for auto chunking and incremental embedding."""

from __future__ import annotations

import hashlib
from pathlib import Path

from storage import Storage, auto_chunk, chunk_code


class TestAutoChunk:
    def test_python_code(self):
        code = """
def foo():
    pass

class Bar:
    def method(self):
        pass
"""
        chunks = auto_chunk(code, "test.py")
        assert len(chunks) >= 2
        combined = "\n".join(chunks)
        assert "def foo" in combined
        assert "class Bar" in combined

    def test_markdown(self):
        md = "# Title\n\nContent here.\n\n## Section\n\nMore content."
        chunks = auto_chunk(md, "test.md")
        assert len(chunks) >= 1
        combined = "\n".join(chunks)
        assert "Content here" in combined

    def test_no_file_path(self):
        text = "Simple text content"
        chunks = auto_chunk(text)
        assert len(chunks) == 1

    def test_empty(self):
        assert auto_chunk("", "test.py") == []


class TestChunkCode:
    def test_functions(self):
        code = "def foo():\n    pass\n\ndef bar():\n    pass"
        chunks = chunk_code(code)
        assert len(chunks) == 2

    def test_empty(self):
        assert chunk_code("") == []


class TestReindex:
    def test_reindex_unchanged(self, tmp_path: Path):
        storage = Storage(tmp_path)
        repo_dir = storage.repos_dir / "test"
        repo_dir.mkdir()
        content = "content"
        (repo_dir / "doc.md").write_text(content)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        # Manually add document with correct hash
        conn = storage._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO documents (collection, file_path, content, content_hash, chunk_index) VALUES (?, ?, ?, ?, ?)",
            ("test", "doc.md", content, content_hash, 0),
        )
        conn.commit()
        result = storage.reindex_collection("test")
        assert result.is_ok
        assert result.value == 0  # No changes

    def test_find_changed_empty(self, tmp_path: Path):
        storage = Storage(tmp_path)
        changed = storage.find_changed_docs("nonexistent")
        assert changed == []
