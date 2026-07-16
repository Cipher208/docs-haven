"""Tests for config."""

import tempfile
from pathlib import Path

from config import Config


def test_config_defaults():
    with tempfile.TemporaryDirectory() as d:
        config = Config(Path(d))
        assert config.get("default_limit") == 10
        assert config.get("auto_chunk") is True


def test_config_set_get():
    with tempfile.TemporaryDirectory() as d:
        config = Config(Path(d))
        config.set("custom_key", "custom_value")
        assert config.get("custom_key") == "custom_value"


def test_config_persistence():
    with tempfile.TemporaryDirectory() as d:
        config = Config(Path(d))
        config.set("persist_key", "persist_value")

        config2 = Config(Path(d))
        assert config2.get("persist_key") == "persist_value"


def test_config_data_dir():
    with tempfile.TemporaryDirectory() as d:
        config = Config(Path(d))
        assert config.data_dir == Path(d)


def test_config_sync_dir():
    with tempfile.TemporaryDirectory() as d:
        config = Config(Path(d))
        assert config.sync_dir == Path(d) / "sync"
