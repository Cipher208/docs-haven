# Claude Desktop Integration

## Setup

1. Install docs-haven:
```bash
pip install docs-haven
```

2. Add to Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

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

3. Restart Claude Desktop.

## Usage

Ask Claude:
- "Search for FastAPI middleware examples"
- "Add the flask repository to my knowledge base"
- "What documentation do we have about SQLAlchemy?"
- "Check if this new doc conflicts with existing ones"

## Troubleshooting

If Claude doesn't see the tools:
1. Check that `python` is in your PATH
2. Try full path: `"command": "/usr/bin/python3"`
3. Check Claude Desktop logs for MCP errors
