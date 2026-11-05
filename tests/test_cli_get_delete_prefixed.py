"""Pinning tests for CLI get/delete by prefixed path (P0-2, CLI part of Phase B).

CLI must accept a prefixed path (``collection/file_path``) for get and delete
and route to the scoped storage operations so that:

- ``docs get collection/file_path`` prints the scoped document content.
- ``docs delete collection/file_path`` removes the document only from that
  collection (scoped delete), never from all collections at once.

These tests MUST FAIL on previous code (cmd_delete passed the raw string to
``delete_document(file_path)``, which deletes matching file_paths across ALL
collections; cmd_get did not exist) and PASS after the CLI change.
"""

from __future__ import annotations

import io
import sys

import pytest

from storage import Storage

# The cli module uses a module-global _storage singleton. We swap it per test.
import cli as _cli


def _reset_cli_storage() -> None:
    _cli._storage = None


def _set_cli_storage(storage: Storage) -> None:
    _cli._storage = storage


def _insert(storage: Storage, collection: str, file_path: str, content: str, title: str = "") -> None:
    storage.bulk_insert_raw(collection, file_path, content, title)


def _capture_stdout() -> tuple[io.StringIO, list[str]]:
    old = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    return buf, [old]

def _restore_stdout(buf_and_old: tuple[io.StringIO, list[str]]) -> str:
    buf, old = buf_and_old
    sys.stdout = old[0]
    return buf.getvalue()


# ── get by prefixed path ─────────────────────────────────────────────────────


def test_cli_get_by_prefixed_path(tmp_storage: Storage) -> None:
    """``cmd_get(collection/file_path)`` prints the scoped document content."""
    _reset_cli_storage()
    _set_cli_storage(tmp_storage)

    _insert(tmp_storage, "proj", "notes/alpha.md", "alpha body text here", "Alpha")

    from cli import cmd_get  # noqa: F811

    import argparse

    args = argparse.Namespace(file_path="proj/notes/alpha.md")
    buf_and_old = _capture_stdout()
    try:
        cmd_get(args)
    finally:
        out = _restore_stdout(buf_and_old)
        _reset_cli_storage()

    assert "alpha body text here" in out, out


def test_cli_get_missing_path_exits(tmp_storage: Storage) -> None:
    """``cmd_get`` for a path that does not exist must exit with a clear error."""
    _reset_cli_storage()
    _set_cli_storage(tmp_storage)

    from cli import cmd_get  # noqa: F811

    import argparse

    args = argparse.Namespace(file_path="proj/nope.md")
    buf_and_old = _capture_stdout()
    try:
        try:
            cmd_get(args)
        finally:
            _restore_stdout(buf_and_old)
            _reset_cli_storage()
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 1, e.code


# ── delete by prefixed path (scoped) ────────────────────────────────────────


def test_cli_delete_by_prefixed_path_is_scoped(tmp_storage: Storage) -> None:
    """``cmd_delete(collection/file_path)`` removes the document only from the
    named collection, leaving an identically-named file in another collection
    intact."""
    _reset_cli_storage()
    _set_cli_storage(tmp_storage)

    _insert(tmp_storage, "a", "same.md", "in A", "A")
    _insert(tmp_storage, "b", "same.md", "in B", "B")

    from cli import cmd_delete  # noqa: F811

    import argparse

    args = argparse.Namespace(file_path="a/same.md")
    buf_and_old = _capture_stdout()
    try:
        cmd_delete(args)
    finally:
        _restore_stdout(buf_and_old)
        _reset_cli_storage()

    # document in a must be gone
    like_a = tmp_storage._get_conn().execute(
        "SELECT COUNT(*) FROM documents WHERE collection = ? AND file_path = ?",
        ("a", "same.md"),
    ).fetchone()[0]
    assert like_a == 0, "document in a should have been removed"

    # document in b must remain
    like_b = tmp_storage._get_conn().execute(
        "SELECT COUNT(*) FROM documents WHERE collection = ? AND file_path = ?",
        ("b", "same.md"),
    ).fetchone()[0]
    assert like_b == 1, "document in b must remain untouched"


def test_cli_delete_plain_path_is_ambiguous_and_guarded(tmp_storage: Storage) -> None:
    """A plain (non-prefixed) path passed to ``cmd_delete`` is still accepted
    (legacy behavior) but a prefixed path is handled by the scoped path."""
    _reset_cli_storage()
    _set_cli_storage(tmp_storage)

    _insert(tmp_storage, "x", "shared.md", "in X", "X")

    from cli import cmd_delete  # noqa: F811

    import argparse

    args = argparse.Namespace(file_path="x/shared.md")
    buf_and_old = _capture_stdout()
    try:
        cmd_delete(args)
    finally:
        _restore_stdout(buf_and_old)
        _reset_cli_storage()

    # The scoped delete must have removed the document in x.
    cnt = tmp_storage._get_conn().execute(
        "SELECT COUNT(*) FROM documents WHERE collection = ? AND file_path = ?",
        ("x", "shared.md"),
    ).fetchone()[0]
    assert cnt == 0, "document in x should be gone after prefixed delete"
