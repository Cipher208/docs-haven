# Configuration

DocsHaven stores configuration in `~/.docshaven/config.json`.

## Data Directory

Default location: `~/.docshaven/`

```
~/.docshaven/
├── config.json           # Configuration (repos, settings)
├── docshaven.db          # SQLite database
└── repos/                # Cloned repositories
    ├── fastapi/
    └── flask/
```

## Configuration File

`config.json` tracks added repositories:

```json
{
  "repos": {
    "fastapi": {
      "url": "https://github.com/fastapi/fastapi",
      "description": "FastAPI web framework",
      "files_indexed": 45,
      "total_chunks": 120
    }
  }
}
```

The configuration is automatically managed when you add repos via CLI or MCP tools.

## HTTP Server Mode

DocsHaven can run as an HTTP server for remote access.

### CLI

```bash
docs-haven serve --port 8080
```

### Python

```python
from server import mcp
import uvicorn

uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=8000)
```

### Command Line Flag

```bash
python server.py --http 8080
```

The server binds to `127.0.0.1` by default (localhost only). For network access, you would need to configure a reverse proxy.

## Environment Variables

DocsHaven does not use environment variables for configuration. All settings are stored in `config.json`.
