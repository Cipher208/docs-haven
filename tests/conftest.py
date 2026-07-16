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
