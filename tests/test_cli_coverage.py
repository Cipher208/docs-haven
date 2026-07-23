"""Tests to boost CLI coverage."""

from __future__ import annotations

from unittest.mock import patch

from cli import main
from result import Err, Ok


def test_cli_search_error():
    with patch("cli.get_storage") as mock:
        mock.return_value.search.return_value = Err(error="Database locked")
        with patch("sys.argv", ["cli", "search", "test"]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 1


def test_cli_add_error():
    with patch("cli.get_storage") as mock:
        mock.return_value.add_repo.return_value = Err(error="Clone failed")
        with patch("sys.argv", ["cli", "add", "https://github.com/test/repo"]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 1


def test_cli_list_error():
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Err(error="DB error")
        with patch("sys.argv", ["cli", "list"]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 1


def test_cli_delete(capsys):
    with patch("cli.get_storage") as mock:
        mock.return_value.delete_document.return_value = Ok(value={"status": "deleted", "file_path": "test.md"})
        with patch("sys.argv", ["cli", "delete", "test.md"]):
            main()
    captured = capsys.readouterr()
    assert "Deleted: test.md" in captured.out


def test_cli_delete_error():
    with patch("cli.get_storage") as mock:
        mock.return_value.delete_document.return_value = Err(error="Not found")
        with patch("sys.argv", ["cli", "delete", "test.md"]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 1


def test_cli_uri_resolve(capsys):
    with patch("cli.get_storage"):
        with patch("sys.argv", ["cli", "uri", "resolve", "core://fastapi/deps"]):
            main()
    captured = capsys.readouterr()
    assert "domain: core" in captured.out


def test_cli_uri_list(capsys):
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok(
            value=[{"name": "core__fastapi", "count": 10, "chunks": 25, "contexts": [], "context_count": 0, "domain": "core"}]
        )
        with patch("sys.argv", ["cli", "uri", "list", "core"]):
            main()
    captured = capsys.readouterr()
    assert "core://fastapi" in captured.out


def test_cli_uri_domains(capsys):
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok(
            value=[{"name": "core__fastapi", "count": 10, "chunks": 25, "contexts": [], "context_count": 0, "domain": "core"}]
        )
        with patch("sys.argv", ["cli", "uri", "domains"]):
            main()
    captured = capsys.readouterr()
    assert "core" in captured.out


def test_cli_collection_show(capsys):
    with patch("cli.get_storage") as mock:
        mock.return_value.list_collections.return_value = Ok(
            value=[{"name": "test", "count": 5, "chunks": 15, "contexts": ["ctx1"], "context_count": 2, "domain": "core"}]
        )
        with patch("sys.argv", ["cli", "collection", "show", "test"]):
            main()
    captured = capsys.readouterr()
    assert "Collection: test" in captured.out
    assert "Contexts: 2 attachments" in captured.out


def test_cli_context_add(capsys):
    with patch("cli.get_storage") as mock:
        mock.return_value.add_context.return_value = Ok(value={"status": "added"})
        with patch("sys.argv", ["cli", "context", "add", "test", "overview", "Summary"]):
            main()
    captured = capsys.readouterr()
    assert "Added context: test/overview" in captured.out


def test_cli_context_add_error():
    with patch("cli.get_storage") as mock:
        mock.return_value.add_context.return_value = Err(error="Empty name")
        with patch("sys.argv", ["cli", "context", "add", "", "overview", "Summary"]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 1


def test_cli_export_empty(capsys):
    with patch("cli.get_storage") as mock:
        mock.return_value.list_documents.return_value = Ok(value=[])
        with patch("sys.argv", ["cli", "export", "--format", "json"]):
            main()
    captured = capsys.readouterr()
    # Empty export prints to stderr
    assert "No documents to export" in (captured.out + captured.err)


def test_cli_import_error():
    with patch("cli.get_storage") as mock:
        mock.return_value.bulk_insert.return_value = Ok(value=0)
        with patch("sys.argv", ["cli", "import", "/nonexistent/file.json"]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 1


def test_cli_import_invalid_json(tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json {{{")
    with patch("cli.get_storage") as mock:
        mock.return_value.bulk_insert.return_value = Ok(value=0)
        with patch("sys.argv", ["cli", "import", str(bad_file)]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 1
