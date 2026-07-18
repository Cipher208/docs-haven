"""Tests for CLI."""

from unittest.mock import patch

import pytest

from cli import main
from result import Ok


def test_cli_search(monkeypatch, capsys):
    """Test search command outputs formatted results."""
    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = Ok([{"score": 0.9, "collection": "test", "title": "Test Doc", "content": "Content here..."}])
        with patch("sys.argv", ["cli", "search", "test query"]):
            main()
    captured = capsys.readouterr()
    assert "0.90" in captured.out
    assert "[test]" in captured.out
    assert "Test Doc" in captured.out
    assert "Content here..." in captured.out


def test_cli_search_empty(capsys):
    """Test search with no results."""
    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = Ok([])
        with patch("sys.argv", ["cli", "search", "nothing"]):
            main()
    captured = capsys.readouterr()
    assert "No results found" in captured.out


def test_cli_search_explain(capsys):
    """Test search with explain flag."""
    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = Ok(
            [
                {
                    "score": 0.85,
                    "collection": "test",
                    "title": "Test",
                    "content": "Content",
                    "explain": {"base_score": 0.7, "type_boost": 0.15, "source": "fts5"},
                }
            ]
        )
        with patch("sys.argv", ["cli", "search", "test", "--explain"]):
            main()
    captured = capsys.readouterr()
    assert "base=0.7" in captured.out
    assert "boost=0.15" in captured.out


def test_cli_stats(capsys):
    """Test stats command outputs statistics."""
    with patch("cli.get_storage") as mock:
        mock.return_value.stats.return_value = Ok(
            {
                "collections": 2,
                "total_documents": 100,
                "total_chunks": 500,
                "db_size_kb": 1024,
            }
        )
        with patch("sys.argv", ["cli", "stats"]):
            main()
    captured = capsys.readouterr()
    assert "Collections: 2" in captured.out
    assert "Documents: 100" in captured.out
    assert "Chunks: 500" in captured.out
    assert "DB size: 1024KB" in captured.out


def test_cli_list(capsys):
    """Test list command outputs collections."""
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok([{"name": "core__fastapi", "count": 10, "chunks": 25}])
        with patch("sys.argv", ["cli", "list"]):
            main()
    captured = capsys.readouterr()
    assert "core__fastapi" in captured.out
    assert "10 docs" in captured.out


def test_cli_no_command():
    """Test no command shows help."""
    with patch("sys.argv", ["cli"]):
        with pytest.raises(SystemExit):
            main()


def test_cli_search_error():
    """Test search error handling."""
    from result import Err

    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = Err("Database locked")
        with patch("sys.argv", ["cli", "search", "test"]):
            with pytest.raises(SystemExit):
                main()
