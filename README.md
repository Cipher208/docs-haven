# DocsHaven

Local knowledge base for AI agents with SQLite FTS5 search, URI routing, and conflict detection.

**Zero external dependencies** — uses built-in Python `sqlite3` with FTS5. No QMD, no Docker, no external search engine.

## Features

- **SQLite FTS5 search** — BM25 ranking with LIKE fallback
- **URI routing** — organize knowledge by domain: `core://`, `ref://`, `guide://`
- **Git sync** — compressed chunks for multi-machine sync (no merge conflicts)
- **Conflict detection** — flag contradictions when adding documents
- **MCP server** — 15 tools for any MCP-compatible agent
- **Document chunking** — split long documents for better search precision

## Installation

```bash
pip install -e .
```

Or from source:

```bash
git clone https://github.com/youruser/docs-haven.git
cd docs-haven
pip install -e ".[test]"
```

## Quick Start

### As MCP Server

Add to your MCP client config:

```json
{
  "mcpServers": {
    "docs-haven": {
      "command": "python",
      "args": ["/path/to/docs-haven/server.py"]
    }
  }
}
```

### Add a Repository

```python
from storage import Storage
from pathlib import Path

storage = Storage(Path.home() / ".docshaven")
result = storage.add_repo(
    url="https://github.com/fastapi/fastapi",
    description="FastAPI web framework",
)
print(f"Indexed {result['files_indexed']} files")
```

### Search

```python
results = storage.search("dependency injection", limit=5)
for r in results:
    print(f"{r['score']:.2f} {r['title']}")
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `kb_search` | Search with BM25 ranking |
| `kb_add_repo` | Clone and index a GitHub repo |
| `kb_get` | Get document content |
| `kb_list_collections` | List all collections |
| `kb_stats` | Database statistics |
| `kb_uri_resolve` | URI → collection mapping |
| `kb_uri_search` | Search within URI scope |
| `kb_uri_list` | List URIs in domain |
| `kb_uri_domains` | All domains with counts |
| `kb_sync_export` | Export compressed chunk |
| `kb_sync_import` | Import chunks |
| `kb_sync_status` | Sync status |
| `kb_conflict_check` | Detect conflicts |
| `kb_conflict_judge` | Record judgment |

## URI Routing

Organize knowledge by domain:

```
core://fastapi/dependencies    → core__fastapi collection
guide://testing/pytest         → guide__testing collection
ref://sqlalchemy/orm           → ref__sqlalchemy collection
```

**Domains:** `core`, `ref`, `guide`, `lib`, `src`, `test`, `note`

## Git Sync

Sync knowledge between machines without merge conflicts:

```python
from sync import Syncer
from pathlib import Path

syncer = Syncer(Path.home() / ".docshaven-sync")

# On machine A: export
syncer.export({"fastapi": docs}, "alice")

# On machine B: import
syncer.import_chunks()
```

Each sync creates a NEW compressed chunk. No files are modified, so git never conflicts.

## Conflict Detection

When adding a document, check for similar existing docs:

```python
from conflicts import ConflictDetector

detector = ConflictDetector(storage)
result = detector.detect(
    title="FastAPI dependency injection",
    content="How to use Depends()...",
)
if result.has_conflicts:
    for c in result.candidates:
        print(f"Similar: {c['title']} (score: {c['score']})")
```

## Development

```bash
# Install with test dependencies
pip install -e ".[test]"

# Run tests
pytest tests/ -v

# Run linting
ruff check .

# Run type checking
mypy . --ignore-missing-imports
```

## Architecture

```
docs-haven/
├── server.py        # MCP server (15 tools)
├── storage.py       # SQLite FTS5 backend
├── uri.py           # URI routing
├── sync.py          # Git sync (compressed chunks)
├── conflicts.py     # Conflict detection
├── tests/           # pytest test suite
└── pyproject.toml   # Package config
```

## License

MIT
