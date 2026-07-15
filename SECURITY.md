# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, use **GitHub Private Vulnerability Reporting**:

1. Go to the [Security tab](https://github.com/Cipher208/docs-haven/security) of the repository
2. Click "Report a vulnerability"
3. Fill in the form with details about the vulnerability

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgment**: within 48 hours
- **Assessment**: within 1 week
- **Fix or mitigation**: depends on severity

## Security Measures

- SQLite FTS5 with parameterized queries (no SQL injection)
- Input validation on all MCP tool parameters
- No external network dependencies (local-only operation)
- WAL mode with busy_timeout for safe concurrent access
