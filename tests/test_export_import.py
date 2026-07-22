"""Tests for export/import CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from cli import main
from result import Ok
from storage import Storage


class TestExportImport:
    def test_export_json(self, tmp_path: Path, capsys):
        """Test export in JSON format."""
        storage = Storage(tmp_path)
        storage.bulk_insert(
            [
                {"collection": "test", "path": "doc1.md", "content": "Content 1", "title": "Doc 1"},
            ]
        )
        with patch("cli.get_storage", return_value=storage):
            with patch("sys.argv", ["cli", "export", "--format", "json"]):
                main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["title"] == "Doc 1"

    def test_export_csv(self, tmp_path: Path, capsys):
        """Test export in CSV format."""
        storage = Storage(tmp_path)
        storage.bulk_insert(
            [
                {"collection": "test", "path": "doc1.md", "content": "Content 1", "title": "Doc 1"},
            ]
        )
        with patch("cli.get_storage", return_value=storage):
            with patch("sys.argv", ["cli", "export", "--format", "csv"]):
                main()
        captured = capsys.readouterr()
        assert "collection" in captured.out
        assert "Doc 1" in captured.out

    def test_import_json(self, tmp_path: Path, capsys):
        """Test import from JSON file."""
        export_file = tmp_path / "backup.json"
        export_file.write_text(
            json.dumps(
                [
                    {"collection": "imported", "path": "doc.md", "content": "Imported content", "title": "Imported"},
                ]
            )
        )
        with patch("cli.get_storage") as mock:
            mock.return_value.bulk_insert.return_value = Ok(value=1)
            with patch("sys.argv", ["cli", "import", str(export_file)]):
                main()
        captured = capsys.readouterr()
        assert "Imported 1 documents" in captured.out


class TestContextCLI:
    def test_context_add(self, tmp_path: Path, capsys):
        """Test context add command."""
        with patch("cli.get_storage") as mock:
            mock.return_value.add_context.return_value = Ok(value={"status": "added"})
            with patch("sys.argv", ["cli", "context", "add", "test", "overview", "Summary text"]):
                main()
        captured = capsys.readouterr()
        assert "Added context: test/overview" in captured.out

    def test_context_list(self, tmp_path: Path, capsys):
        """Test context list command."""
        with patch("cli.get_storage") as mock:
            mock.return_value.list_contexts.return_value = Ok(
                value=[{"collection": "test", "path": "overview", "summary": "Test summary", "created_at": "2026-01-01"}]
            )
            with patch("sys.argv", ["cli", "context", "list"]):
                main()
        captured = capsys.readouterr()
        assert "[test]" in captured.out
        assert "overview" in captured.out

    def test_context_rm(self, tmp_path: Path, capsys):
        """Test context rm command."""
        with patch("cli.get_storage") as mock:
            mock.return_value.remove_context.return_value = Ok(value={"status": "removed"})
            with patch("sys.argv", ["cli", "context", "rm", "test", "--path", "overview"]):
                main()
        captured = capsys.readouterr()
        assert "Removed context from: test" in captured.out
