# MCP Tools Reference

DocsHaven exposes 18 MCP tools for knowledge base operations.

## Search & Retrieval

### kb_search

Search the knowledge base using BM25 full-text search with highlighted excerpts.

```json
{
  "query": "fastapi dependency injection",
  "collections": ["fastapi"],
  "limit": 10,
  "min_score": 0.3
}
```

**Parameters:**
- `query` (string, required): Search query
- `collections` (list, optional): Filter to specific collections
- `limit` (int, default 10): Max results
- `min_score` (float, default 0): Minimum relevance score

**Returns:** Results with `highlighted` field containing FTS5 snippet with `<b>` tags around matched terms.

### kb_get

Get full content of a document.

```json
{
  "file_path": "fastapi/README.md"
}
```

### kb_update

Update an existing document's content.

```json
{
  "file_path": "fastapi/README.md",
  "content": "Updated content here...",
  "title": "Optional new title"
}
```

### kb_delete

Delete a document from the knowledge base.

```json
{
  "file_path": "fastapi/README.md"
}
```

### kb_list_collections

List all collections with document counts.

```json
{}
```

### kb_stats

Get database statistics.

```json
{}
```

## Repository Management

### kb_add_repo

Clone and index a GitHub repository.

```json
{
  "url": "https://github.com/fastapi/fastapi",
  "description": "FastAPI web framework",
  "mask": "**/*.md"
}
```

## URI Routing

### kb_uri_resolve

Resolve a URI to its collection.

```json
{
  "uri": "core://fastapi/dependencies"
}
```

### kb_uri_search

Search within a URI scope. Supports wildcards: `core://fastapi/*`

```json
{
  "uri": "core://fastapi",
  "limit": 10
}
```

### kb_uri_list

List all URIs in a domain.

```json
{
  "domain": "core"
}
```

### kb_uri_domains

List all domains with collection counts.

```json
{}
```

## Git Sync

### kb_sync_export

Export knowledge base as compressed chunk.

```json
{
  "created_by": "alice"
}
```

### kb_sync_import

Import compressed chunks from sync directory.

```json
{}
```

### kb_sync_status

Get sync status.

```json
{}
```

## Conflict Detection

### kb_conflict_check

Check for conflicts before adding a document.

```json
{
  "title": "FastAPI dependency injection",
  "content": "How to use Depends()...",
  "collections": ["fastapi"]
}
```

### kb_conflict_judge

Record a judgment on a conflict candidate.

```json
{
  "new_id": "doc1",
  "candidate_id": "doc2",
  "judgment": "supersedes"
}
```
