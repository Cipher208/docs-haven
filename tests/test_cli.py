"""Tests for CLI."""

from unittest.mock import patch

import pytest

from cli import main
from result import Ok


def test_cli_search(monkeypatch, capsys):
    """Test search command outputs formatted results."""
    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = Ok(value=[{"score": 0.9, "collection": "test", "title": "Test Doc", "content": "Content here..."}])
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
        mock.return_value.search.return_value = Ok(value=[])
        with patch("sys.argv", ["cli", "search", "nothing"]):
            main()
    captured = capsys.readouterr()
    assert "No results found" in captured.out


def test_cli_search_explain(capsys):
    """Test search with explain flag."""
    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = Ok(
            value=[
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
            value={
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
        mock.return_value.list_collections.return_value = Ok(value=[{"name": "core__fastapi", "count": 10, "chunks": 25}])
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
        mock.return_value.search.return_value = Err(error="Database locked")
        with patch("sys.argv", ["cli", "search", "test"]):
            with pytest.raises(SystemExit):
                main()


def test_cli_collection_list(capsys):
    """Test collection list command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok(value=[{"name": "core__fastapi", "count": 10, "chunks": 25, "domain": "core"}])
        with patch("sys.argv", ["cli", "collection", "list"]):
            main()
    captured = capsys.readouterr()
    assert "core__fastapi" in captured.out
    assert "[core]" in captured.out
    assert "10 docs" in captured.out


def test_cli_collection_show(capsys):
    """Test collection show command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok(
            value=[{"name": "core__fastapi", "count": 10, "chunks": 25, "domain": "core", "contexts": ["ctx1", "ctx2"]}]
        )
        with patch("sys.argv", ["cli", "collection", "show", "core__fastapi"]):
            main()
    captured = capsys.readouterr()
    assert "Collection: core__fastapi" in captured.out
    assert "Documents: 10" in captured.out
    assert "Domain: core" in captured.out


def test_cli_collection_show_not_found(capsys):
    """Test collection show with non-existent collection."""
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok(value=[])
        with patch("sys.argv", ["cli", "collection", "show", "nonexistent"]):
            main()
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_cli_collection_remove(capsys):
    """Test collection remove command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.remove_collection.return_value = Ok(value={"status": "removed", "collection": "test_coll", "documents": 5})
        with patch("sys.argv", ["cli", "collection", "remove", "test_coll"]):
            main()
    captured = capsys.readouterr()
    assert "Removed collection: test_coll" in captured.out


def test_cli_add(capsys):
    """Test add command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.add_repo.return_value = Ok(value={"name": "test-repo", "status": "added", "files_indexed": 5, "chunks": 10})
        with patch("sys.argv", ["cli", "add", "https://github.com/test/repo"]):
            main()
    captured = capsys.readouterr()
    assert "Added test-repo" in captured.out
    assert "5 files" in captured.out


def test_cli_add_error():
    """Test add error handling."""
    from result import Err

    with patch("cli.get_storage") as mock:
        mock.return_value.add_repo.return_value = Err(error="Clone failed")
        with patch("sys.argv", ["cli", "add", "https://github.com/test/repo"]):
            with pytest.raises(SystemExit):
                main()


def test_cli_delete(capsys):
    """Test delete command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.delete_document.return_value = Ok(value={"status": "deleted", "file_path": "test.md"})
        with patch("sys.argv", ["cli", "delete", "test.md"]):
            main()
    captured = capsys.readouterr()
    assert "Deleted: test.md" in captured.out


def test_cli_uri_resolve(capsys):
    """Test uri resolve command."""
    with patch("cli.get_storage"):
        with patch("sys.argv", ["cli", "uri", "resolve", "core://fastapi/deps"]):
            main()
    captured = capsys.readouterr()
    assert "domain: core" in captured.out
    assert "path: fastapi/deps" in captured.out


def test_cli_conflicts_list(capsys):
    """Test conflicts list command."""
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok(
            value=[{"name": "core__fastapi", "count": 10, "chunks": 25}]
        )
        with patch("sys.argv", ["cli", "conflicts", "list"]):
            main()
    captured = capsys.readouterr()
    assert "Collections" in captured.out
    assert "core__fastapi" in captured.out


def test_cli_conflicts_check_no_conflicts(capsys):
    """Test conflicts check with no conflicts."""
    from conflicts import ConflictDetector

    with patch("cli.get_storage") as mock:
        with patch.object(ConflictDetector, "detect") as mock_detect:
            mock_detect.return_value = type("R", (), {"has_conflicts": False, "candidates": []})()
            with patch("sys.argv", ["cli", "conflicts", "check", "test title", "test content"]):
                main()
    captured = capsys.readouterr()
    assert "No conflicts found" in captured.out


def test_cli_conflicts_check_with_conflicts(capsys):
    """Test conflicts check with conflicts found."""
    from conflicts import ConflictDetector

    with patch("cli.get_storage") as mock:
        with patch.object(ConflictDetector, "detect") as mock_detect:
            mock_result = type("R", (), {
                "has_conflicts": True,
                "candidates": [
                    {"title": "Existing Doc", "collection": "test", "score": 0.85, "path": "test.md", "snippet": "Some content..."}
                ]
            })()
            mock_detect.return_value = mock_result
            with patch("sys.argv", ["cli", "conflicts", "check", "test title", "test content"]):
                main()
    captured = capsys.readouterr()
    assert "potential conflict" in captured.out
    assert "Existing Doc" in captured.out


def test_cli_conflicts_suggest_no_conflicts(capsys):
    """Test conflicts suggest with no conflicts."""
    from conflicts import ConflictDetector

    with patch("cli.get_storage") as mock:
        with patch.object(ConflictDetector, "suggest_resolution") as mock_suggest:
            mock_suggest.return_value = {"suggestion": "no_action", "confidence": 1.0, "reason": "No conflicts detected"}
            with patch("sys.argv", ["cli", "conflicts", "suggest", "nonexistent"]):
                main()
    captured = capsys.readouterr()
    assert "no_action" in captured.out
    assert "100%" in captured.out


def test_cli_conflicts_suggest_error():
    """Test conflicts suggest with error."""
    from conflicts import ConflictDetector

    with patch("cli.get_storage") as mock:
        with patch.object(ConflictDetector, "suggest_resolution") as mock_suggest:
            mock_suggest.return_value = {"error": "Document not found: bad_id"}
            with patch("sys.argv", ["cli", "conflicts", "suggest", "bad_id"]):
                with pytest.raises(SystemExit):
                    main()
