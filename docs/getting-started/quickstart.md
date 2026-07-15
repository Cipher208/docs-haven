# Quick Start

## 1. Add a Repository

```python
from storage import Storage
from pathlib import Path

storage = Storage(Path.home() / ".docshaven")

result = storage.add_repo(
    url="https://github.com/fastapi/fastapi",
    description="FastAPI web framework",
    mask="**/*.md",  # Index only markdown files
)
print(f"Indexed {result['files_indexed']} files")
```

## 2. Search

```python
results = storage.search("dependency injection", limit=5)
for r in results:
    print(f"{r['score']:.2f} [{r['collection']}] {r['title']}")
```

## 3. Use URI Routing

```python
from uri import URI

# Parse a URI
uri = URI.parse("core://fastapi/dependencies")
print(uri.to_collection())  # "core__fastapi"

# Search within a URI scope
results = storage.search("deps", collections=["core__fastapi"])
```

## 4. As MCP Server

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

Then use any MCP-compatible agent to search your knowledge base.
