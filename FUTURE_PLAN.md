# FUTURE PLAN — docs-haven

> Roadmap based on Vision v1.0 + spec + community feedback.
>
> **A developer's personal library.** MCP server for agents: not just "searching repos", but living memory — indexed libraries, documentation, links, notes — with answers in natural language.

---

## Implemented

### Core (v0.1.0 – v0.5.0)
- ✅ SQLite FTS5 search engine (BM25 + LIKE fallback)
- ✅ Auto strategy selection (fts/hybrid by query length)
- ✅ Document chunking (text + code-aware)
- ✅ Type-aware result boosting
- ✅ URI routing (core://, ref://, guide://, lib://, src://, test://, note://)
- ✅ Git sync with compressed chunks
- ✅ MCP server with tools
- ✅ Result type (Ok/Err) with Pydantic v2
- ✅ Input validation (URL, collection names, query length, file masks)
- ✅ E2E + chaos test suite
- ✅ ADRs (8 architectural decisions)
- ✅ CLI interface (11 commands)

### Search & Scoring (v0.6.0 – v0.9.0)
- ✅ Vector search — TF-IDF based, no external dependencies
- ✅ Score explanation — `explain=True` returns per-signal breakdown
- ✅ Importance scoring — recency (exponential decay, 90-day half-life) + retrieval frequency
- ✅ LLM argument aliasing — 70+ aliases for hallucinated parameter names
- ✅ Conflict detection + resolution CLI

### Infrastructure (v0.7.0 – v0.9.0)
- ✅ Context attachments — human-written summaries for collections
- ✅ Export/Import CLI — `export --format json/csv/md`, `import backup.json`
- ✅ Incremental reindexing — `find_changed_docs()` + `reindex_collection()`
- ✅ Collection rename — `collection rename old new` (CLI + MCP)
- ✅ Benchmark automation — CI regression detection, PR comments
- ✅ PyPI publish automation — trusted publishing on v* tag push
- ✅ Security hardening — decompression bomb, connection leak, thread-local connections, SHA pinning
- ✅ Code quality — 59/59 review issues, storage.py split into validation/chunking/scoring

---

## P1: Smart Search — kb_ask + Agent Tools

> **Goal:** Agent asks questions in natural language, gets answers with citations. Not "formulate a search query" — "answer the question".

### 1. kb_ask() — NL Query Engine

Natural language → search → LLM summary → `{answer, chunks, citations}`.

```
kb_ask("how to use Depends with parameters in FastAPI?")
  → parsing: extract keywords (FastAPI, Depends, parameters)
  → FTS5 with query expansion (Depends, dependency injection, dependencies)
  → ranking with context awareness (what was viewed recently)
  → LLM summary: "Here's how. Three approaches: ..."
  → return: {answer, chunks[], citations[]}
```

**Why:** Key feature from Vision v1.0. Agent shouldn't formulate queries — it should ask questions.

**How:**
- LLM called via MCP host (not inside DocsHaven — zero deps principle)
- Query expansion: "async generator" → async AND generator, async_generator, yield from
- Context-aware: prioritizes same collection as previous query
- Answer mode: LLM summary from top chunks with inline citations

**Files:** server.py (add kb_ask tool), storage.py (query expansion), new: llm.py (LLM integration via MCP host)

### 2. Query Expansion

Automatic expansion of search queries.

**How:**
- Synonym dictionary: Depends → dependency injection, dependencies
- Stemming: generator → generate, generating, generated
- Standard patterns: "how to" → tutorial, guide, example
- Configurable expansion dictionary

**Files:** storage.py (add _expand_query method)

### 3. Context-Aware Search

Search knows what the agent viewed before.

**How:**
- Track last N searched collections in session
- Boost results from recently accessed collections
- `context` parameter in kb_ask: `["fastapi", "sqlalchemy"]`
- Weight: recent collection +0.1 boost

**Files:** storage.py (add context tracking)

### 4. Answer Mode (LLM Summary)

LLM summary from found chunks with citations.

**How:**
- After FTS5 search, send top chunks to LLM
- LLM generates summary with inline citations
- Return: `{answer: str, chunks: list, citations: list}`
- Citations link back to source: `{collection, file_path, chunk_index}`

**Files:** server.py (kb_ask returns answer mode)

### 5. kb_related() — Related Documents

"What else is related to this?"

**How:**
- Find documents with same tags, same collection, overlapping content
- Score: tag overlap + collection proximity + content similarity
- Return top 5 related documents

**Files:** storage.py (add find_related method), server.py (add tool)

### 6. kb_recent() — Recent Activity

"What did I look at yesterday?"

**How:**
- Use retrieval_count tracking (already implemented)
- Return most recently accessed documents
- Filter by collection, time range

**Files:** storage.py (add get_recent method), server.py (add tool)

### 7. kb_learn() — Topic Exploration

"I want to learn about this topic."

**How:**
- Given a topic, find related collections and documents
- Return structured overview: collections, key documents, learning path
- Group by complexity (beginner → advanced)

**Files:** storage.py (add learn_topic method), server.py (add tool)

---

## P2: Indexation Sources — Expanding Sources

> **Goal:** Index not just GitHub repos — anything: local folders, URLs, PyPI, plain text.

### 8. Local Folder Indexing

`kb_index("path:///usr/lib/python3.14/asyncio")`

**How:**
- Add `source_type: "local"` to repo metadata
- Index files matching mask from local directory
- No git — direct file read
- Validate path exists and is readable

**Files:** storage.py (add add_local method), server.py (update kb_add_repo)

### 9. URL/Web Page Indexing

`kb_index("https://docs.python.org/3/library/asyncio.html")`

**How:**
- Fetch URL content (requests + BeautifulSoup or similar)
- Extract main content (strip nav, footer, ads)
- Index as single document or split by headings
- Store URL as file_path, domain as collection

**Files:** storage.py (add add_url method), server.py (add tool), new: fetcher.py

### 10. Plain Text Indexing

`kb_index("text", content="...")` or paste in CLI

**How:**
- Accept text content directly
- Generate title from first line or hash
- Index as single document
- CLI: `echo "content" | docs-haven index --text`

**Files:** storage.py (add add_text method), cli.py (add --text flag)

### 11. PyPI Package Indexing

`kb_index("pypi://fastapi")`

**How:**
- Download package via `pip download --no-deps --dest /tmp`
- Extract documentation (README, docs/ if present)
- Index markdown/rst files
- Store as collection with package name

**Files:** storage.py (add add_pypi method), server.py (add tool)

### 12. Bookmark / Fav List

"Save this link, I'll come back to it"

**How:**
- `kb_bookmark(url, title?, note?)` — save URL with metadata
- `kb_bookmarks()` — list saved bookmarks
- Bookmarks stored in separate table
- Lightweight — no content indexing, just URL + metadata

**Files:** storage.py (add bookmarks table), server.py (add tools)

---

## P3: Database Depth — Hierarchy and Structure

> **Goal:** Database with hierarchy, tags, versions — not a flat documents table.

### 13. Repos Table (separate from documents)

Separate table for repositories with metadata.

```sql
CREATE TABLE repos (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT,
    source_type TEXT,  -- github, local, url, pypi, text
    source_meta TEXT,  -- JSON metadata
    tags TEXT,         -- comma-separated
    description TEXT,
    update_policy TEXT DEFAULT 'manual',  -- once, auto, manual
    last_indexed TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**Why:** Repos currently stored in config.json. Separate table enables SQL queries, filtering, statistics.

**Files:** storage.py (migration + new table)

### 14. Collections with Parent_id (Hierarchy)

Hierarchical collections: `python → stdlib → asyncio`

**How:**
- Add `parent_id INTEGER REFERENCES collections(id)` to collections
- `kb_ask("show me everything about async in python")` → hierarchy: python → stdlib → asyncio
- Tree navigation: `kb_tree("python")` returns child collections

**Files:** storage.py (migration + hierarchy methods)

### 15. Tags M2M Table

Many-to-many relationship between documents and tags.

```sql
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    category TEXT  -- language, topic, framework, etc.
);

CREATE TABLE document_tags (
    document_id INTEGER REFERENCES documents(id),
    tag_id INTEGER REFERENCES tags(id),
    PRIMARY KEY (document_id, tag_id)
);
```

**Why:** Tags stored as comma-separated in config.json. M2M table enables SQL filtering, aggregations, tag navigation.

**Files:** storage.py (migration + tag methods)

### 16. Freshness Score

Automatic calculation of document relevance over time.

**How:**
- `freshness_score = f(age, last_updated, retrieval_count)`
- Age: exponential decay from `created_at`
- Last updated: boost if recently changed
- Retrieval: boost if frequently accessed
- Display in search results: "freshness: 0.85"

**Files:** storage.py (add freshness calculation)

### 17. Version Tracking

Document versioning.

**How:**
- Store `version_tag` in documents table (e.g., "fastapi 0.95")
- `kb_version("fastapi 0.95")` — search within specific version
- Compare versions: `kb_diff("fastapi", "0.94", "0.95")`

**Files:** storage.py (add version methods)

### 18. Auto-Tags from Content

Automatic tag detection from document content.

**How:**
- During indexing, detect: language, framework, topic
- Language: file extension mapping
- Framework: keyword detection (import fastapi → "fastapi")
- Topic: title/content keyword extraction
- Store as tags in document_tags

**Files:** storage.py (add auto_tag method)

### 19. Cross-References Between Documents

Links between documents through references and mentions.

**How:**
- Detect `[[wikilinks]]`, `[text](url)`, `import X` in content
- Build cross-reference graph
- `kb_related()` uses cross-refs for recommendations

**Files:** storage.py (add cross-ref extraction)

---

## P4: Primitive API — Fewer Tools, More Layers

> **Goal:** 5 core primitives for agents. The rest — layered for power users.

### 20. Primitive Tools (Agent-Facing)

Agent sees only 5 tools:

```
kb_ask(query, context?)     → {answer, chunks, citations}
kb_index(source, type?)     → {collection, docs_indexed}
kb_search(query, filters?)  → [{chunk, score, source}]
kb_get(file_path)           → {content, metadata}
kb_stats()                  → {collections, docs, languages, tags}
```

**Why:** From vision: "Agent needs 5. The rest — technical tools for administration."

**How:**
- `kb_ask` — NL query → search → answer (P1 #1)
- `kb_index` — smart add with auto-type detection (P2 #8-11)
- `kb_search` — current search with filters (tags, language, complexity)
- `kb_get` — current kb_get
- `kb_stats` — enhanced stats (languages, tags, freshness)

### 21. Layered Tools (Technical/Power User)

Other tools available but not in main API:

```
# Layer 1: Agent-facing (5 tools)
kb_ask, kb_index, kb_search, kb_get, kb_stats

# Layer 2: Navigation (power users / CLI)
kb_related, kb_recent, kb_learn, kb_tree, kb_version

# Layer 3: Administration
kb_add_repo, kb_update_repo, kb_delete, kb_list_collections
kb_context_add, kb_context_list, kb_context_rm
kb_collection_rename, kb_sync_export, kb_sync_import
kb_conflict_check, kb_conflict_judge

# Layer 4: System
kb_config, kb_backup, kb_restore
```

**Why:** Don't show agents 20+ tools. Give 5 key ones, the rest — on request.

### 22. Tool Consolidation

Merge duplicate tools.

**How:**
- `kb_add_repo` + `kb_add_local` + `kb_add_url` + `kb_add_text` → `kb_index(source, type)`
- `kb_conflict_check` + `kb_conflict_suggest` → `kb_conflict_check` (includes suggestions)
- `kb_context_add` + `kb_context_list` + `kb_context_rm` → `kb_context(action, ...)`

**Files:** server.py (consolidate tools)

---

## P5: Standalone Version — Autonomous Version for Users

> **Goal:** EXE/distribution for non-agent users. With GUI.

### 23. Standalone Desktop App (EXE)

Autonomous docs-haven without MCP, with GUI.

**How:**
- PyInstaller / cx_Freeze → single EXE
- Built-in FastAPI server + web interface
- Auto-start server on launch
- System tray icon
- No MCP dependency — standalone

**Files:** new: desktop.py, new: web/ directory

### 24. Web Dashboard (for standalone)

Visual interface for standalone version.

**How:**
- Browse collections, search, view documents
- Conflict resolution interface
- Sync status dashboard
- Settings page (update policies, tags)
- **Defer until:** standalone version ready

**Files:** new: web/templates/, new: web/static/

### 25. Auto-Update for standalone

Automatic updates for standalone version.

**How:**
- Check GitHub releases for new versions
- Download and replace EXE
- User notification + restart

**Files:** new: updater.py

---

## Deferred

### 1. Epistemic Graph
Knowledge graph with automatic relationships.

**Why:** Vision: "deferred until 100+ collections. Document relationships are obvious now."

**When:** 100+ collections, cross-collection search becomes bottleneck.

### 2. RAG (Full Semantic Search)
Embedding-based semantic search with local models.

**How:** MiniLM, sentence-transformers, Qdrant/ChromaDB.
**When:** TF-IDF stops finding what's needed.
**Cost:** +200MB deps, +100MB RAM per 1000 docs.

### 3. Plugin System
Extensible architecture.

**How:** Plugin API, custom strategies, custom backends.
**When:** Community requests appear.

### 4. Multi-User Support
Authentication, roles, per-user collections.

**When:** Team use cases appear.

### 5. Backup System
Automated index backups.

**When:** Git sync stops ensuring data safety.

---

## Roadmap Timeline

| Phase | Scope | Estimate |
|-------|-------|----------|
| **P1** | kb_ask + Query Expansion + Context-aware + kb_related + kb_recent | 1-2 weeks |
| **P2** | Local/URL/Text/PyPI indexing + Bookmarks | 1-2 weeks |
| **P3** | repos table + hierarchy + tags M2M + freshness + versions + auto-tags + cross-refs | 2-3 weeks |
| **P4** | Primitive API + layered tools + consolidation | 3-5 days |
| **P5** | Standalone EXE + Web Dashboard + auto-update | 2-3 weeks |
| **Deferred** | Graph, RAG, Plugins, Multi-User, Backup | When needed |

---

*Last updated: 2026-07-23*
