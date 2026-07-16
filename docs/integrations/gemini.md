# Gemini CLI Integration

## Setup

1. Install docs-haven:
```bash
pip install docs-haven
```

2. Add MCP server to Gemini CLI:

```bash
gemini mcp add docs-haven -- python -m docs_haven.server
```

Or manually add to `~/.gemini/settings.json`:

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

3. Restart Gemini CLI.

## Usage

Ask Gemini:
- "Search my knowledge base for async patterns"
- "Index the fastapi documentation"
- "What do we know about SQLAlchemy?"
