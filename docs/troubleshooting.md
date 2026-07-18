# Troubleshooting

## Common Issues

### "Database is locked" error

SQLite allows only one writer at a time. If you see this error:

1. Make sure no other DocsHaven process is running
2. Check if the MCP server is running while you're using CLI
3. Wait a few seconds and retry — the busy timeout (5s) should handle transient locks

### Git clone fails

```
Error: Clone failed: fatal: repository not found
```

**Causes:**
- Invalid URL (check for typos)
- Repository is private (need authentication)
- Network connectivity issues

**Fix:**
```bash
# Test the URL first
git ls-remote https://github.com/user/repo

# For private repos, configure git credentials
git config --global credential.helper store
```

### Search returns no results

1. Check if documents are indexed: `docs-haven stats`
2. Try a broader query
3. Check collection filter: `docs-haven list`
4. Try different strategy: `strategy="hybrid"` for longer queries

### FTS5 tokenization issues

FTS5 splits on whitespace and punctuation. Special characters in queries may cause issues:

```python
# Bad: special characters
storage.search('user@email.com')

# Good: escape or simplify
storage.search('email address')
```

### Path traversal errors

DocsHaven validates paths to prevent directory traversal:

```
Error: Invalid file path
```

**Fix:** Use relative paths without `..` or leading `/`:
```python
# Bad
storage.get("../../etc/passwd")
storage.get("/etc/passwd")

# Good
storage.get("repo/README.md")
```

### Config file corrupted

If `~/.docshaven/config.json` is corrupted, DocsHaven will use default settings and recreate the file on next `add_repo` call.

To manually fix:
```bash
rm ~/.docshaven/config.json
docs-haven add https://github.com/user/repo  # Recreates config
```

### Stale documents

After modifying files in a cloned repo, use `check_stale()` to find outdated documents:

```python
stale = storage.check_stale("fastapi")
for s in stale:
    print(f"{s['file_path']}: {s['reason']}")
```

Reasons:
- `file_deleted`: File no longer exists in the repo
- `content_changed`: File content differs from indexed version
- `symlink_skipped`: File is a symlink (skipped for safety)

## Performance Tips

1. **Use `strategy="fts"` for short queries** — faster than hybrid
2. **Limit results** — `limit=10` is usually enough
3. **Use collections filter** — narrow search scope when possible
4. **Run `docs-haven stats`** — check if database is growing too large

## Getting Help

- GitHub Issues: https://github.com/Cipher208/docs-haven/issues
- Documentation: https://github.com/Cipher208/docs-haven/tree/main/docs
