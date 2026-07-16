"""Configuration for DocsHaven."""

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / ".docshaven"
CONFIG_FILE = "config.json"


class Config:
    """DocsHaven configuration."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.config_path = self.config_dir / CONFIG_FILE
        self._data = self._load()

    def _load(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {
            "data_dir": str(self.config_dir),
            "sync_dir": str(self.config_dir / "sync"),
            "default_limit": 10,
            "auto_chunk": True,
            "chunk_size": 1000,
        }

    def save(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self._data, indent=2))

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    @property
    def data_dir(self) -> Path:
        return Path(self.get("data_dir", str(DEFAULT_CONFIG_DIR)))

    @property
    def sync_dir(self) -> Path:
        return Path(self.get("sync_dir", str(DEFAULT_CONFIG_DIR / "sync")))
