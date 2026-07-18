# CLI Reference

DocsHaven provides a command-line interface for managing your knowledge base.

## Installation

```bash
pip install docs-haven
```

The `docs-haven` command becomes available globally.

## Commands

### search

Search the knowledge base.

```bash
docs-haven search "dependency injection"
docs-haven search "fastapi" --limit 5
docs-haven search "async" --explain
```

**Options:**
- `query` (required): Search query
- `-l, --limit`: Max results (default: 10)
- `-e, --explain`: Show scoring breakdown (base_score, type_boost, source)

### add

Clone and index a GitHub repository.

```bash
docs-haven add https://github.com/fastapi/fastapi
docs-haven add https://github.com/pallets/flask --description "Flask web framework"
```

**Options:**
- `url` (required): GitHub repo URL
- `-d, --description`: Repository description

### stats

Show knowledge base statistics.

```bash
docs-haven stats
```

**Output:**
```
Collections: 5
Documents: 120
Chunks: 350
DB size: 1024KB
```

### list

List all collections with document counts.

```bash
docs-haven list
```

**Output:**
```
  core__fastapi: 45 docs, 120 chunks
  guide__pytest: 12 docs, 35 chunks
```

### delete

Delete a document from the knowledge base.

```bash
docs-haven delete fastapi/README.md
```

### uri

URI routing operations.

```bash
# Resolve a URI to its collection
docs-haven uri resolve core://fastapi/dependencies

# List all URIs in a domain
docs-haven uri list core

# List all domains with collection counts
docs-haven uri domains
```

### serve

Start the MCP server as an HTTP service.

```bash
docs-haven serve
docs-haven serve --port 9000
```

**Options:**
- `-p, --port`: Port number (default: 8000)

## Global Options

- `-h, --help`: Show help message
- `--version`: Show version

## Examples

```bash
# Add FastAPI docs and search them
docs-haven add https://github.com/fastapi/fastapi
docs-haven search "dependency injection"

# Check what's in your knowledge base
docs-haven stats
docs-haven list

# Start MCP server for remote access
docs-haven serve --port 8080
```
