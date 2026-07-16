"""Tests for CLI."""

from unittest.mock import patch

import pytest

from cli import main


def test_cli_search(monkeypatch):
    """Test search command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = [
            {"score": 0.9, "collection": "test", "title": "Test Doc", "content": "Content..."}
        ]
        with patch("sys.argv", ["cli", "search", "test query"]):
            main()


def test_cli_stats(monkeypatch):
    """Test stats command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.stats.return_value = {
            "collections": 2,
            "total_documents": 100,
            "total_chunks": 500,
            "db_size_kb": 1024,
        }
        with patch("sys.argv", ["cli", "stats"]):
            main()


def test_cli_no_command():
    """Test no command shows help."""
    with patch("sys.argv", ["cli"]):
        with pytest.raises(SystemExit):
            main()
