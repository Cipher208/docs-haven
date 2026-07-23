"""Shared test fixtures for docs-haven."""

import tempfile
from pathlib import Path

import pytest

from storage import Storage

# ── Shared test constants ────────────────────────────────────────────────────
TEST_COLLECTION = "test"
TEST_FILE = "doc.md"
TEST_CONTENT = "# Test Repo\nContent here"
TEST_TITLE = "Test Repo"


@pytest.fixture
def tmp_storage():
    """Provide a clean temporary storage."""
    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))
        yield storage
        storage.close()


def insert_doc(
    storage: Storage,
    collection: str,
    path: str,
    content: str,
    title: str = "",
    chunk_index: int = 0,
    total_chunks: int = 1,
) -> None:
    """Insert a test document into storage.

    Supports optional chunk_index/total_chunks for multi-chunk documents.
    Usable both as a plain function (``from conftest import insert_doc``)
    and as a pytest fixture (``def test_foo(insert_doc):``).
    """
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title, chunk_index, total_chunks) VALUES (?, ?, ?, ?, ?, ?)",
        (collection, path, content, title, chunk_index, total_chunks),
    )
    conn.commit()


@pytest.fixture
def insert_doc_fixture(insert_doc):
    """Fixture wrapper around insert_doc for tests that prefer fixture injection."""
    return insert_doc
