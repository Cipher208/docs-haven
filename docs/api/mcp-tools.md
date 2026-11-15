# MCP Tools Reference

DocsHaven exposes 32 MCP tools for knowledge base operations.

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

### kb_index

Index any source — a local folder, a GitHub repository, a web page or plain text. The source kind is auto-detected; an explicit `type`
overrides detection.

```json
{
  "source": "path:///home/me/projects/notes"
}
```

```json
{
  "source": "https://github.com/fastapi/fastapi",
  "description": "FastAPI web framework"
}
```

```json
{
  "source": "text://Meeting Notes",
  "content": "Attendees: ...\nDecisions: ..."
}
```

```json
{
  "source": "https://docs.python.org/3/library/asyncio.html",
  "description": "asyncio stdlib docs"
}
```

**Parameters:**
- `source` (string, required): Local folder (`path://<dir>` or a plain existing directory), GitHub repo (`github://<owner>/<repo>` or a repo-shaped git URL), web page (any other http(s) URL), or note title (`text://<title>`, percent-decoded). Also accepted as `url`, `path` or `folder`.
- `type` (string, optional): Force the source kind: `"local"`, `"github"`, `"url"` (alias `"web"`) or `"text"` (auto-detected otherwise)
- `content` (string, optional): Note text — required for text sources, rejected for other sources
- `collection` (string, optional): Collection name override (local, web and text sources only; GitHub names come from the URL; text defaults to `"notes"`, web pages to the slugified domain)
- `tags` (list, optional): Optional tags
- `description` (string, optional): Description
- `mask` (string, default "**/*.md"): File glob pattern. Not for text or web sources.

**Behavior:**
- `local` — files are indexed straight from disk (no git). The collection is registered in config with `source_type: "local"` and `url: "path://<absolute path>"`; incremental reindexing resolves the folder from config.
- `github` — the same flow as `kb_add_repo` (shallow clone, then index). Repo-shaped means: `github://<owner>/<repo>`, `git@host:owner/repo.git`, or an http(s) URL on an allowed git host with at most 2 path segments (`owner/<repo>`, optional `.git`). A path ending in `.git` is repo-shaped at ANY depth (GitLab subgroup repos like `gitlab.com/group/subgroup/proj.git` clone instead of fetching the HTML page).
- `url` (alias `web`) — any other http(s) URL is fetched and indexed as a web page (card P2-3). The visible text is extracted with the stdlib `html.parser` (title from `<title>`, falling back to the first `<h1>`; `<script>`/`<style>`/`<noscript>`/`<nav>`/`<footer>`/`<header>`/`<aside>` subtrees are stripped entirely; block-level newlines, `<pre>` preserved). Deeper git-host URLs (e.g. `github.com/owner/repo/blob/main/README.md`) name pages inside a repo and route here too. Identity is the URL itself: the file path is the final normalized post-redirect URL (max 3 redirect hops, every hop re-validated as http(s) — no scheme downgrades), so re-indexing the same URL replaces all its chunks — even when the new page has fewer — while preserving `created_at` and `retrieval_count`. URLs differing only in `#fragment` are distinct identities (fragments are kept in the normalized URL) — known behavior. The collection defaults to the slugified domain (`docs.python.org` → `docs-python-org`; the port stays in the slug), `collection=` overrides. The response must be HTML (`text/html` / `application/xhtml+xml`, charset from the header, utf-8 fallback) and at most 10MB; the timeout is 10s and the fetch runs off the event loop. `robots.txt` is deliberately NOT consulted. Re-indexing a page is a full replace — it does not go through the file reindex flow.
- `text` — `content=` is indexed as a note (`Storage.add_text`): chunked with the shared chunker and stored in the documents projection, so it is fully searchable. The file path is content-addressed (`text://<slug from title>-<sha256[:16] of content>`), so re-adding identical content is an idempotent replace. The title is the `text://` remainder (or `type="text"` source); an empty remainder derives the title from the first non-empty content line (hash fallback for whitespace-only content). Text sources require `content=`; passing `content=` with a file source is an error.
- Incremental reindex is mask-aware (the collection's stored mask — new entries always persist it; a legacy entry without the key keeps the pre-card `**/*` detection): changed files are rewritten, deleted files drop their documents, and mask-mismatched or dot-path rows are purged on reindex. The dot-path rule is broad by design: it excludes dot-FILES as well as dot-directories (`.readme.md`; even an explicit `.docs/*.md` mask indexes 0 files). Re-running `kb_index` over an already-indexed folder refreshes matching files but does not remove documents of deleted files until a reindex (parity with `kb_add_repo`).
- unknown sources return an error listing the supported kinds

**Security note (residual SSRF surface, by design):** git-clone URLs are gated
by `validate_url`'s domain allowlist (the 59-issue review's SSRF fix — clones
can only target the public git hosts). A web-page fetch is only validated as
http(s): `kb_index(url)` fetches anything the host machine can reach, including
loopback and internal addresses. This is accepted for a local-first tool run
by its owner — arbitrary web pages are the feature, and a host allowlist would
defeat it. Mitigations that do apply: per-hop redirect scheme re-validation,
the 10MB response cap, HTML-only content, and a fixed timeout.

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

## Bookmarks

Bookmarks are web links with optional metadata. They are deliberately outside
the documents projection: no content indexing, nothing enters search, and
bookmark writes never touch the corpus cache. There is no delete or edit tool —
the upsert IS the edit.

### kb_bookmark

Save or update a bookmark, keyed by URL.

```json
{
  "url": "https://docs.python.org/3/library/asyncio.html",
  "title": "asyncio — async I/O",
  "note": "come back for the TaskGroup section"
}
```

**Parameters:**
- `url` (string, required): Web link (http/https only). Also accepted as `link` or `href`.
- `title` (string, optional): Display title (default: empty)
- `note` (string, optional): Free-text note (default: empty)

**Behavior:**
- Re-saving an existing URL updates `title`/`note`/`updated_at` and preserves `created_at`.
- `title`/`note` omitted on a re-save keep the stored value; pass `""` to clear a field.
- Non-http(s) schemes, empty values and URLs without a host return an error.

**Returns:** `{"url": "...", "created": true}` — `created` is false when an existing bookmark was updated.

### kb_bookmarks

List saved bookmarks, most recent first.

**Returns:** `[{"url", "title", "note", "created_at", "updated_at"}, ...]` — ordered by `updated_at` descending (exact write order within one clock tick).

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
