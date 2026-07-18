# Cursor Integration

## Setup

1. Install docs-haven:
```bash
pip install docs-haven
```

2. Add to Cursor MCP settings:

**Project-level:** `.cursor/mcp.json`
**Global:** `~/.cursor/mcp.json`

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

Cursor's AI chat will automatically have access to all 16 DocsHaven tools.

Try:
- "Search my knowledge base for async patterns"
- "Add the fastapi docs to my knowledge base"
- "Show me all collections in my knowledge base"
