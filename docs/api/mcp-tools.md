# MCP Tools Reference

DocsHaven exposes 29 MCP tools for knowledge base operations.

## Search & Retrieval

### kb_search

Search the knowledge base using BM25 full-text search with highlighted excerpts.

```json
{
  "query": "fastapi dependency injection",
  "collections": ["fastapi"],
  "limit": 10,
  "min_score": 0.3,
  "explain": true
}
```

**Parameters:**
- `query` (string, required): Search query. Accepts the argument aliases `q`, `text`, `search_query` natively
- `collections` (list, optional): Filter to specific collections
- `limit` (int, default 10): Max results
- `min_score` (float, default 0): Minimum relevance score
- `explain` (bool, default false): Include scoring breakdown

**Returns:** Results with `highlighted` field containing FTS5 snippet with `<b>` tags around matched terms. When `explain=true`, includes `explain` object with `base_score`, `type_boost`, `importance_boost`, `context_boost`, `final_score`, `source`.

### kb_ask

Ask a question in natural language and get an answer with inline `[n]` citations.

Extracts keywords from the question, runs an expanded full-text search
(synonyms, stems, patterns), ranks the top chunks, and asks the MCP host LLM to
summarize them. If the host cannot provide a completion, the assembled chunks
and the prompt are returned instead of failing.

```json
{
  "question": "how to use Depends with parameters in FastAPI?",
  "collections": ["fastapi"],
  "context": ["fastapi", "sqlalchemy"],
  "limit": 5,
  "explain": false
}
```

**Parameters:**
- `question` (string, required): Natural-language question. Accepts the argument aliases `query`, `q`, `userQuery`, `prompt` natively
- `collections` (list, optional): Filter to specific collections
- `context` (list, optional): Collections to prioritize (+0.1 boost); recently viewed collections are boosted too
- `limit` (int, default 5): Max source chunks
- `explain` (bool, default false): Include scoring breakdown per chunk

**Returns:**
- `{"answer": str, "answer_mode": "llm", "chunks": [...], "citations": [...]}` — host LLM summarized the chunks
- `{"answer": "", "answer_mode": "host_unavailable", "chunks": [...], "citations": [...], "prompt": str}` — no host completion available; the caller can summarize manually
- `{"answer": "", "answer_mode": "no_results", "chunks": [], "citations": []}` — nothing matched

Each citation is `{collection, file_path, chunk_index}`.

### kb_related

Find documents related to a stored document.

Scores candidates by title-term overlap (the "tag" signal — the schema has no
tag column), same-collection proximity, and content similarity:
`score = 0.4*tag_overlap + 0.2*same_collection + 0.4*content_similarity`.
Multi-chunk candidates appear once; candidates with no title or content overlap
are dropped. A same-collection candidate can fill all slots — the result does
not guarantee cross-collection visibility.

```json
{
  "collection": "fastapi",
  "file_path": "dependencies.md",
  "chunk_index": null,
  "limit": 5,
  "explain": false
}
```

**Parameters:**
- `collection` (string, required): Collection of the source document
- `file_path` (string, required): File path of the source document
- `chunk_index` (int, optional): Compare from this chunk only (whole document otherwise)
- `limit` (int, default 5): Max related documents
- `explain` (bool, default false): Include the scoring breakdown per document

**Returns:**
```json
{
  "source": {"collection": "...", "file_path": "...", "chunk_index": 0, "title": "...", "chunks": 3},
  "related": [
    {
      "collection": "...", "file_path": "...", "chunk_index": 0, "title": "...", "score": 0.52,
      "relation": {"tag_overlap": 0.25, "same_collection": true, "content_similarity": 0.63}
    }
  ]
}
```
When `explain=true`, each related document also carries an `explain` object with
the three signals, `final_score`, `tag_overlap_source`, and the weights used.

### kb_recent

List recently accessed documents, most recent first.

Access recency comes from a session access log (see below); documents not seen
this session are filled from retrieval_count plus updated_at ordering and report
`accessed_at: null`. For log entries `since` compares against the session access
time; for fallback rows against `updated_at`.

```json
{
  "collection": "fastapi",
  "since": "2026-11-01",
  "limit": 10
}
```

**Parameters:**
- `collection` (string, optional): Filter to one collection
- `since` (string, optional): ISO date/datetime lower bound on access/update time
- `limit` (int, default 10): Max documents

**Returns:**
```json
{
  "documents": [
    {
      "collection": "...", "file_path": "...", "chunk_index": 0, "title": "...",
      "accessed_at": "2026-11-14 12:00:00", "retrieval_count": 3, "updated_at": "..."
    }
  ]
}
```

### kb_learn

Explore a topic: related collections, key documents and a beginner → advanced
learning path.

Pure retrieval with deterministic complexity grouping (title markers, else
code-chunk ratio + document length); no LLM calls. Use `kb_ask` to summarize the
returned documents afterwards.

```json
{
  "topic": "dependency injection in FastAPI",
  "collection": "fastapi",
  "limit": 10
}
```

**Parameters:**
- `topic` (string, required): Topic to explore
- `collection` (string, optional): Restrict to one collection
- `limit` (int, default 10): Max key documents / learning steps

**Returns:**
```json
{
  "topic": "...",
  "collections": [{"collection": "...", "documents": 42, "matched_documents": 7, "score": 1.23}],
  "key_documents": [{"collection": "...", "file_path": "...", "title": "...", "level": "beginner", "score": 0.61}],
  "learning_path": [{"step": 1, "document": "collection/file_path", "level": "beginner", "why": "Start here: covers the basics of ..."}]
}
```

### Access log semantics

`kb_search` (its top results) and `kb_ask` (its internal search) record
accesses, as do `kb_get` fetches and successful `kb_related` lookups.
`kb_recent` and `kb_learn` record nothing — querying recent activity or
exploring a topic is not a viewing. The log is in-memory per server session
(it is not persisted).

### kb_get

Get full content of a document.

```json
{
  "file_path": "fastapi/README.md"
}
```

**Parameters:**
- `file_path` (string, required): Document path, either `file_path` or `collection/file_path`. Accepts the argument aliases `filePath`, `documentId`, `doc_path` natively

### kb_update

Update an existing document's content.

Metadata the caller does not pass — title, context, created_at,
retrieval_count, extension — is preserved; `content_hash`, chunking and
`total_chunks` are recomputed from the new content and `updated_at` is
refreshed. An update never creates a document and never touches a same-named
file in another collection.

```json
{
  "file_path": "fastapi/README.md",
  "content": "Updated content here...",
  "title": "Optional new title",
  "collection": "fastapi"
}
```

**Parameters:**
- `file_path` (string, required): Document path to update
- `content` (string, required): New content for the document (max 10MB)
- `title` (string, optional): New title (otherwise the existing title is preserved)
- `collection` (string, optional): Collection scope (required when the path is ambiguous)

**Returns:** `{"status": "updated", "file_path": "...", "collection": "...", "chunks": 2}`

### kb_delete

Delete a document from the knowledge base.

```json
{
  "file_path": "fastapi/README.md",
  "collection": "fastapi"
}
```

**Parameters:**
- `file_path` (string, required): Document path to delete
- `collection` (string, optional): Collection scope (prevents cross-collection deletes)

**Returns:** `{"status": "deleted", "file_path": "...", "collection": "..."}`

### Scoped mutation semantics (kb_update / kb_delete)

`kb_update` takes a `collection` and preserves unpassed metadata; `kb_delete`
never mass-deletes. Both follow the same scoping policy:

| Case | Behavior |
|------|----------|
| `collection` given | Only that document is updated/removed (delete is idempotent when nothing matched) |
| `collection` omitted, path in exactly one collection | That document is updated/removed |
| `collection` omitted, path in 2+ collections | Error — `collection required: ...` — and nothing is changed |
| `collection` omitted, path matches nothing | `kb_delete`: idempotent success (`status: deleted`, `collection: null`). `kb_update`: `Document not found` error (an update never creates) |

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

**Parameters:**
- `url` (string, required): GitHub repo URL (must start with https://, http://, or git@)
- `tags` (list, optional): Optional tags
- `description` (string, optional): Description
- `mask` (string, default "**/*.md"): File glob pattern

### kb_collection_rename

Rename a collection across all documents.

```json
{
  "old_name": "fastapi",
  "new_name": "fastapi-docs"
}
```

**Parameters:**
- `old_name` (string, required): Current collection name
- `new_name` (string, required): New collection name

### kb_check_imports

Validate that imports in a file reference real modules/packages (Python and
JS/TS files are checked; other extensions report no imports).

```json
{
  "file_path": "src/app.py",
  "repo_root": "."
}
```

**Parameters:**
- `file_path` (string, required): Path to the file to check
- `repo_root` (string, default "."): Repository root directory

**Returns:** `{"imports": [...], "phantom_imports": [...], "status": "ok", "total_imports": 5, "phantom_count": 1}` (with `status` `file_not_found` / `read_error` on failure).

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

**Parameters:**
- `new_id` (string, required): ID of the new document
- `candidate_id` (string, required): ID of the conflicting document
- `judgment` (string, required): One of "supersedes", "conflicts_with", "unrelated"

**Note:** Judgments are persisted to the `conflict_judgments` table.

### kb_conflict_details

Get details about a conflict for resolution.

```json
{
  "new_id": "doc1"
}
```

**Returns:** `{"new_id": "...", "new_title": "...", "candidates": [...], "has_conflicts": true, "judgments": [...]}` (or `{"error": ...}` when the document is unknown).

### kb_conflict_suggest

Suggest a resolution strategy for a conflict.

```json
{
  "new_id": "doc1"
}
```

**Returns:** `{"suggestion": "supersedes" | "conflicts_with" | "unrelated" | "no_action", "confidence": 0.8, "reason": "..."}` — deterministic thresholds over the top candidate's score.

## Templates

### kb_template_list

List all available collection templates.

```json
{}
```

**Returns:** Templates with `name`, `description`, `domain`, `tags`, `file_mask` — the built-ins are `python-docs`, `api-docs`, `wiki`, `tutorial`, `source-code`, `test-docs`.

### kb_template_apply

Apply a collection template.

```json
{
  "template_name": "python-docs",
  "repo_url": "https://github.com/fastapi/fastapi"
}
```

**Parameters:**
- `template_name` (string, required): Template name (see `kb_template_list`)
- `repo_url` (string, optional): Repository URL to clone and index

## Context Attachments

### kb_context_add

Add a context attachment (human-written summary) to a collection.

```json
{
  "collection": "fastapi",
  "path": "overview",
  "summary": "FastAPI is a modern web framework"
}
```

**Parameters:**
- `collection` (string, required): Collection name
- `path` (string, required): Context path (e.g., 'overview', 'quickstart')
- `summary` (string, required): Human-written summary text

### kb_context_list

List context attachments.

```json
{
  "collection": "fastapi"
}
```

**Parameters:**
- `collection` (string, optional): Filter by collection (lists all if omitted)

### kb_context_rm

Remove context attachment(s).

```json
{
  "collection": "fastapi",
  "path": "overview"
}
```

**Parameters:**
- `collection` (string, required): Collection name
- `path` (string, optional): Specific path to remove (removes all in collection if omitted)
