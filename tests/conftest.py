"""Shared test fixtures for docs-haven."""

import tempfile
from pathlib import Path

import pytest

from storage import Storage


@pytest.fixture
def tmp_storage():
    """Provide a clean temporary storage."""
    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d))
        yield storage
        storage.close()


def insert_doc(storage: Storage, collection: str, path: str, content: str, title: str) -> None:
    """Insert a test document into storage."""
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, title) VALUES (?, ?, ?, ?)",
        (collection, path, content, title),
    )
    conn.commit()
