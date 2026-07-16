# DocsHaven

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Cipher208/docs-haven)](https://github.com/Cipher208/docs-haven/stargazers)
[![GitHub last commit](https://img.shields.io/github/last-commit/Cipher208/docs-haven)](https://github.com/Cipher208/docs-haven/commits/main)
[![CI](https://github.com/Cipher208/docs-haven/actions/workflows/ci.yml/badge.svg)](https://github.com/Cipher208/docs-haven/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

> **Your AI agent keeps forgetting what it learned last session. DocsHaven fixes that.**

**Add your repos, and your agent always has them at hand.** DocsHaven is a local knowledge base that lets you index any GitHub repository, search it instantly, and keep your agent informed across sessions. Zero dependencies, works with any MCP-compatible agent — Claude, Cursor, Gemini, Codex.

**Why DocsHaven?**
- 🧠 **Add repos once, search forever** — your agent always has the knowledge it needs
- ⚡ **Instant search** — SQLite FTS5 finds relevant docs in <100ms
- 🔌 **Works with any AI agent** — Claude, Cursor, Gemini, Codex via MCP
- 🏠 **100% local** — no cloud, no API keys, no data leaves your machine
- 📦 **Zero dependencies** — built into Python, no Docker or external services

## Features

- **SQLite FTS5 search** — BM25 ranking with LIKE fallback
- **URI routing** — organize knowledge by domain: `core://`, `ref://`, `guide://`
- **Git sync** — compressed chunks for multi-machine sync (no merge conflicts)
- **Conflict detection** — flag contradictions when adding documents
- **MCP server** — 14 tools for any MCP-compatible agent
- **Document chunking** — split long documents for better search precision

## Installation

```bash
git clone https://github.com/Cipher208/docs-haven.git
cd docs-haven
pip install -e .
```

<details>
<summary><b>With test dependencies</b></summary>

```bash
pip install -e ".[test]"
```

</details>

## How to Use

<details open>
<summary><b>1. Add Repositories to Your Knowledge Base</b></summary>

```python
from storage import Storage
from pathlib import Path

storage = Storage(Path.home() / ".docshaven")

# Add a GitHub repo (clones and indexes markdown files)
result = storage.add_repo(
    url="https://github.com/fastapi/fastapi",
    description="FastAPI web framework",
)
print(f"Indexed {result['files_indexed']} files in {result['chunks']} chunks")

# Add with custom file mask (index Python files)
result = storage.add_repo(
    url="https://github.com/pallets/flask",
    mask="**/*.py",
)
```

</details>

<details open>
<summary><b>2. Search Your Knowledge Base</b></summary>

```python
# Basic search
results = storage.search("dependency injection")
for r in results:
    print(f"{r['score']:.2f} [{r['collection']}] {r['title']}")
    print(f"  {r['content'][:100]}...")
    print()

# Search with filters
results = storage.search(
    "async middleware",
    collections=["fastapi"],
    limit=5,
    min_score=0.3,
)

# Use auto strategy (fts for short queries, hybrid for long)
results = storage.search("how to use Depends()", strategy="auto")
```

</details>

<details open>
<summary><b>3. Organize with URI Routing</b></summary>

```python
from uri import URI, URIRouter

# Parse URIs
uri = URI.parse("core://fastapi/dependencies")
print(uri.domain)      # "core"
print(uri.path)        # "fastapi/dependencies"
print(uri.to_collection())  # "core__fastapi"

# Search within a URI scope
router = URIRouter(storage)
results = router.search_by_uri("core://fastapi", limit=5)

# List all domains
domains = router.list_all_domains()
# {'core': {'count': 3, 'doc': 'Core documentation'}, 'ref': {'count': 2, ...}}
```

**Available domains:** `core`, `ref`, `guide`, `lib`, `src`, `test`, `note`

</details>

<details>
<summary><b>4. Detect Conflicts</b></summary>

```python
from conflicts import ConflictDetector

detector = ConflictDetector(storage)
result = detector.detect(
    title="FastAPI dependency injection",
    content="How to use Depends()...",
)

if result.has_conflicts:
    print(f"Found {len(result.candidates)} similar documents:")
    for c in result.candidates:
        print(f"  - {c['title']} (score: {c['score']})")
        print(f"    {c['snippet'][:80]}...")

    # Record judgment
    detector.judge("new_doc_id", "existing_doc_id", "supersedes")
```

</details>

<details>
<summary><b>5. Sync Between Machines</b></summary>

```python
from sync import Syncer
from pathlib import Path

syncer = Syncer(Path.home() / ".docshaven-sync")

# On machine A: export
result = syncer.export(
    {"fastapi": docs, "sqlalchemy": docs},
    created_by="alice",
)
print(f"Exported chunk {result['chunk_id']}")

# On machine B: import
result = syncer.import_chunks()
print(f"Imported {result['chunks_imported']} chunks")

# Check status
status = syncer.status()
print(f"Chunks: {status['local_chunks']}")
```

</details>

<details>
<summary><b>6. Use as MCP Server</b></summary>

Add to your MCP client config (Claude Desktop, Cursor, etc.):

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

Then ask your agent:

> "Search for FastAPI middleware examples"
> "What documentation do we have about SQLAlchemy?"
> "Check if this new doc conflicts with existing ones"

</details>

## MCP Tools

| Tool | Description |
|------|-------------|
| `kb_search` | Search with BM25 ranking |
| `kb_add_repo` | Clone and index a GitHub repo |
| `kb_get` | Get document content |
| `kb_list_collections` | List all collections |
| `kb_stats` | Database statistics |
| `kb_uri_resolve` | URI to collection mapping |
| `kb_uri_search` | Search within URI scope |
| `kb_uri_list` | List URIs in domain |
| `kb_uri_domains` | All domains with counts |
| `kb_sync_export` | Export compressed chunk |
| `kb_sync_import` | Import chunks |
| `kb_sync_status` | Sync status |
| `kb_conflict_check` | Detect conflicts |
| `kb_conflict_judge` | Record judgment |

## Comparison

| Feature | DocsHaven | QMD | Elasticsearch | Context7 |
|---------|-----------|-----|---------------|----------|
| Dependencies | 0 (stdlib) | 1 (npm) | JVM + plugins | External service |
| Setup time | 10 seconds | 5 minutes | 30+ minutes | API key needed |
| MCP server | Built-in | No | No | Yes |
| URI routing | Yes | No | No | No |
| Conflict detection | Yes | No | No | No |
| Git sync | Compressed chunks | No | No | No |
| Cost | Free | Free | Free (self-hosted) | Paid tiers |

## FAQ

<details>
<summary><b>What is DocsHaven?</b></summary>

DocsHaven is a local knowledge base designed for AI agents. It provides full-text search via SQLite FTS5, organizes knowledge by URI domains (core://, ref://, guide://), and detects contradictions when adding new documents. It runs as an MCP server with 14 tools.

</details>

<details>
<summary><b>How is this different from just using SQLite?</b></summary>

DocsHaven adds a complete knowledge management layer on top of SQLite: automatic document chunking, BM25 ranking with LIKE fallback, URI-based organization, conflict detection, and compressed multi-machine sync — all exposed via MCP tools.

</details>

<details>
<summary><b>Can I use this with Claude Desktop / Cursor / other AI agents?</b></summary>

Yes. DocsHaven runs as an MCP server. Add it to your MCP client config and all 14 tools become available to your agent.

</details>

<details>
<summary><b>How fast is search?</b></summary>

SQLite FTS5 with BM25 ranking handles 1,000+ documents in under 100ms on modern hardware. No network latency since everything is local.

</details>

<details>
<summary><b>Is my data sent anywhere?</b></summary>

No. DocsHaven is fully local. The only network operation is cloning GitHub repositories (which you initiate). All search and storage happens on your machine.

</details>

## Architecture

```
docs-haven/
├── server.py        # MCP server (14 tools)
├── storage.py       # SQLite FTS5 backend
├── uri.py           # URI routing
├── sync.py          # Git sync (compressed chunks)
├── conflicts.py     # Conflict detection
├── tests/           # pytest test suite (39 tests)
├── docs/            # Documentation
└── pyproject.toml   # Package config
```

## Development

```bash
# Install with test dependencies
pip install -e ".[test]"

# Run tests
pytest tests/ -v

# Run linting
ruff check .

# Run formatting
ruff format .

# Run type checking
mypy . --ignore-missing-imports
```

## Security

- SQLite FTS5 with parameterized queries (no SQL injection)
- Input validation on all MCP tool parameters
- No external network dependencies (local-only operation)
- Secret scanning via GitHub Actions (gitleaks)

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Author

Built with ❤️ by [Cipher208](https://github.com/Cipher208)

## License

MIT
