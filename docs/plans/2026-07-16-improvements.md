# docs-haven Improvements Plan

> **For agentic workers:** Execute tasks sequentially. Each task is self-contained.

**Goal:** Improve docs-haven with benchmark, CLI, config, integration examples, and fixes.

**Architecture:** Add CLI layer on top of existing Storage/URI/Sync/Conflict modules.

**Tech Stack:** Python 3.10+, click/typer for CLI, existing SQLite FTS5 backend.

---

## Task 1: Benchmark Script

**Files:**
- Create: `benchmark.py`
- Modify: `README.md` (add Performance section)

**Status:** ✅ DONE — benchmark.py created, results: 3.3ms avg search, 13k docs/sec indexing.

---

## Task 2: Fix mypy warning in conflicts.py

**Files:**
- Modify: `conflicts.py`

**Steps:**

- [ ] **Step 1: Fix no_implicit_optional**

```python
# In conflicts.py, the issue is in detect() signature
# Current:
def detect(self, title: str, content: str, collections: list[str] = None) -> ConflictResult:

# Fix to:
def detect(self, title: str, content: str, collections: Optional[list[str]] = None) -> ConflictResult:
```

- [ ] **Step 2: Run mypy to verify**

Run: `mypy conflicts.py --ignore-missing-imports`
Expected: Success with no errors

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_conflicts.py -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add conflicts.py
git commit -m "fix: resolve mypy no_implicit_optional warning in conflicts.py"
```

---

## Task 3: Integration Examples

**Files:**
- Create: `docs/integrations/claude-desktop.md`
- Create: `docs/integrations/cursor.md`
- Create: `docs/integrations/gemini.md`
- Modify: `README.md` (add Integrations section)

**Steps:**

- [ ] **Step 1: Create Claude Desktop integration guide**

Create `docs/integrations/claude-desktop.md`:

```markdown
# Claude Desktop Integration

## Setup

1. Install docs-haven:
```bash
pip install docs-haven
```

2. Find the server path:
```bash
python -c "import docs_haven; print(docs_haven.__file__)"
# Or: which docs-haven-server
```

3. Add to Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "docs-haven": {
      "command": "python",
      "args": ["-m", "docs_haven.server"]
    }
  }
}
```

4. Restart Claude Desktop.

## Usage

Ask Claude:
- "Search for FastAPI middleware examples"
- "Add the flask repository to my knowledge base"
- "What documentation do we have about SQLAlchemy?"
```

- [ ] **Step 2: Create Cursor integration guide**

Create `docs/integrations/cursor.md`:

```markdown
# Cursor Integration

## Setup

1. Install docs-haven:
```bash
pip install docs-haven
```

2. Add to Cursor MCP settings (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "docs-haven": {
      "command": "python",
      "args": ["-m", "docs_haven.server"]
    }
  }
}
```

3. Restart Cursor.

## Usage

Use Cursor's AI chat with MCP tools available.
```

- [ ] **Step 3: Create Gemini integration guide**

Create `docs/integrations/gemini.md`:

```markdown
# Gemini CLI Integration

## Setup

1. Install docs-haven:
```bash
pip install docs-haven
```

2. Configure Gemini CLI MCP:

```bash
gemini mcp add docs-haven -- python -m docs_haven.server
```

## Usage

Ask Gemini:
- "Search my knowledge base for async patterns"
- "Index the fastapi documentation"
```

- [ ] **Step 4: Add Integrations section to README**

Add after "Use as MCP Server" section:

```markdown
## Integrations

| Client | Config Location | Status |
|--------|----------------|--------|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | ✅ |
| Cursor | `.cursor/mcp.json` | ✅ |
| Gemini CLI | `gemini mcp add` | ✅ |
| VS Code (Copilot) | `.vscode/mcp.json` | ✅ |
| Codex | `.codex/config.toml` | ✅ |

See [docs/integrations/](docs/integrations/) for detailed guides.
```

- [ ] **Step 5: Commit**

```bash
git add docs/integrations/ README.md
git commit -m "docs: add integration guides for Claude Desktop, Cursor, Gemini"
```

---

## Task 4: CLI Entry Point

**Files:**
- Create: `cli.py`
- Modify: `pyproject.toml` (add scripts section)
- Create: `tests/test_cli.py`

**Steps:**

- [ ] **Step 1: Create CLI module**

Create `cli.py`:

```python
"""CLI for DocsHaven — search, add repos, manage knowledge base."""

import sys
from pathlib import Path

from storage import Storage
from uri import URI, URIRouter


def get_storage() -> Storage:
    return Storage(Path.home() / ".docshaven")


def cmd_search(args):
    """Search the knowledge base."""
    storage = get_storage()
    results = storage.search(args.query, limit=args.limit)
    for r in results:
        print(f"{r['score']:.2f} [{r['collection']}] {r['title']}")
        print(f"  {r['content'][:100]}...")
        print()


def cmd_add(args):
    """Add a repository."""
    storage = get_storage()
    result = storage.add_repo(args.url, description=args.description)
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Added {result['name']}: {result['files_indexed']} files, {result.get('chunks', 0)} chunks")


def cmd_stats(args):
    """Show knowledge base statistics."""
    storage = get_storage()
    stats = storage.stats()
    print(f"Collections: {stats['collections']}")
    print(f"Documents: {stats['total_documents']}")
    print(f"Chunks: {stats['total_chunks']}")
    print(f"DB size: {stats['db_size_kb']}KB")


def cmd_uri(args):
    """URI operations."""
    storage = get_storage()
    router = URIRouter(storage)

    if args.subcmd == "resolve":
        result = router.resolve(args.uri)
        for k, v in result.items():
            print(f"{k}: {v}")
    elif args.subcmd == "list":
        results = router.list_by_domain(args.domain)
        for r in results:
            print(f"  {r['uri']}")
    elif args.subcmd == "domains":
        domains = router.list_all_domains()
        for d, info in domains.items():
            print(f"  {d}: {info['count']} collections")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="DocsHaven CLI")
    subparsers = parser.add_subparsers(dest="command")

    # search
    sp = subparsers.add_parser("search", help="Search knowledge base")
    sp.add_argument("query", help="Search query")
    sp.add_argument("-l", "--limit", type=int, default=10)
    sp.set_defaults(func=cmd_search)

    # add
    sp = subparsers.add_parser("add", help="Add a repository")
    sp.add_argument("url", help="GitHub repo URL")
    sp.add_argument("-d", "--description", help="Description")
    sp.set_defaults(func=cmd_add)

    # stats
    sp = subparsers.add_parser("stats", help="Show statistics")
    sp.set_defaults(func=cmd_stats)

    # uri
    sp = subparsers.add_parser("uri", help="URI operations")
    uri_sub = sp.add_subparsers(dest="subcmd")
    rp = uri_sub.add_parser("resolve", help="Resolve URI")
    rp.add_argument("uri", help="URI to resolve")
    lp = uri_sub.add_parser("list", help="List URIs in domain")
    lp.add_argument("domain", help="Domain to list")
    uri_sub.add_parser("domains", help="List all domains")
    sp.set_defaults(func=cmd_uri)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add CLI entry point to pyproject.toml**

Add after `[project.urls]`:

```toml
[project.scripts]
docs-haven = "cli:main"
```

- [ ] **Step 3: Write tests**

Create `tests/test_cli.py`:

```python
"""Tests for CLI."""

import sys
from unittest.mock import patch

import pytest

from cli import main, get_storage


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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add cli.py tests/test_cli.py pyproject.toml
git commit -m "feat: add CLI entry point — docs-haven search/add/stats/uri"
```

---

## Task 5: Config File

**Files:**
- Create: `config.py`
- Modify: `storage.py` (use config)
- Modify: `cli.py` (use config)
- Create: `tests/test_config.py`

**Steps:**

- [ ] **Step 1: Create config module**

Create `config.py`:

```python
"""Configuration for DocsHaven."""

import json
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_DIR = Path.home() / ".docshaven"
CONFIG_FILE = "config.json"


class Config:
    """DocsHaven configuration."""

    def __init__(self, config_dir: Optional[Path] = None):
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
        self.config_path.write_text(json.dumps(self._data, indent=2))

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    @property
    def data_dir(self) -> Path:
        return Path(self.get("data_dir", str(DEFAULT_CONFIG_DIR)))

    @property
    def sync_dir(self) -> Path:
        return Path(self.get("sync_dir", str(DEFAULT_CONFIG_DIR / "sync")))
```

- [ ] **Step 2: Update storage.py to use config**

Modify `Storage.__init__`:

```python
def __init__(self, data_dir: Path = None):
    if data_dir is None:
        from config import Config
        config = Config()
        data_dir = config.data_dir
    self.data_dir = data_dir
    # ... rest of init
```

- [ ] **Step 3: Write tests**

Create `tests/test_config.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_config.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py storage.py
git commit -m "feat: add config file support — ~/.docshaven/config.json"
```

---

## Task 6: CHANGELOG automation

**Files:**
- Modify: `.github/workflows/release-drafter.yml` (verify config)
- Modify: `CHANGELOG.md` (add v0.1.0 entry)

**Steps:**

- [ ] **Step 1: Verify release-drafter config**

The `.github/release-drafter.yml` is already configured. Verify it works by checking GitHub.

- [ ] **Step 2: Update CHANGELOG.md**

Add proper v0.1.0 entry at the top.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG with v0.1.0 release notes"
```

---

## Task 7: Add benchmark results and integration section to README

**Files:**
- Modify: `README.md`

**Steps:**

- [ ] **Step 1: Add Performance section**

Add after Architecture section:

```markdown
## Performance

Benchmarked on Linux (Python 3.14, SQLite FTS5):

| Operation | Time |
|-----------|------|
| Index 1,000 docs | 0.076s (13,219 docs/sec) |
| Search (avg) | 3.3ms |
| Search (P95) | 4.3ms |
| Throughput | 299 queries/sec |
```

- [ ] **Step 2: Add Integrations section**

Add after Performance section.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add performance benchmarks and integration section to README"
```

---

## Execution Order

1. Task 2: Fix mypy warning (quick)
2. Task 3: Integration examples
3. Task 4: CLI entry point
4. Task 5: Config file
5. Task 6: CHANGELOG automation
6. Task 7: README updates

Total: 7 tasks, ~30 minutes of work.
