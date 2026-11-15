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
- ✅ ADRs (10 architectural decisions)
- ✅ CLI interface (13 commands)

### Search & Scoring (v0.6.0 – v0.9.0)
- ✅ Vector search — TF-IDF based, no external dependencies
- ✅ Score explanation — `explain=True` returns per-signal breakdown
- ✅ Importance scoring — recency (exponential decay, 90-day half-life) + retrieval frequency
- ✅ Conflict detection + resolution CLI

### Smart Search (P1)
- ✅ kb_ask — natural-language answers with inline citations via the MCP host LLM (answer modes: llm / host_unavailable / no_results)
- ✅ Query expansion + context-aware ranking (session-recency and `context` boosts)
- ✅ kb_related / kb_recent / kb_learn — related documents, recent activity, topic learning paths (see P1 below; KNOWN LIMITATIONS apply)

### Infrastructure (v0.7.0 – v0.9.0)
- ✅ Context attachments — human-written summaries for collections
- ✅ Export/Import CLI — `export --format json/csv/md`, `import backup.json`
- ✅ Incremental reindexing — `find_changed_docs()` + `reindex_collection()`
- ✅ Collection rename — `collection rename old new` (CLI + MCP)
- ✅ Benchmark automation — CI regression detection, PR comments
- ✅ PyPI publish automation — trusted publishing on v* tag push
- ✅ Security hardening — decompression bomb, connection leak, thread-local connections, SHA pinning
- ✅ Code quality — 59/59 review issues, storage.py split into validation/chunking/scoring

### Maintenance & Quality Sprint (2026-11-12 – 11-15)

- ✅ Scoped update/delete semantics — kb_update/kb_delete hit exact rows
  (ghost-row bug fixed: update no longer creates rows with an empty
  collection); delete is idempotent-Ok on zero matches
- ✅ Native argument aliases — dead `alias.py` deleted, hot tools use
  pydantic `AliasChoices` (see Rejected)
- ✅ Row-shape resilience — `resolve_chunk()` so kb_ask survives
  vector/expansion-only rows (was `KeyError: 'file_path'`)
- ✅ Corpus cache + thread lock — per-call corpus load replaced by
  mutation-invalidated cache (N4 below); rebuilds lock-guarded (BH-9)
- ✅ Vector citations fixed — search rows carry `chunk_index` (citations
  pointed at chunk 0)
- ✅ Lint/format baseline zero + CI tool pins (ruff==0.17.5, mypy==2.4.1;
  unpinned CI tools had drifted red on unchanged code)
- ✅ Stale CODE_METRICS.md removed (unregenerable without its tool)
- ✅ Test suite 330 → 424 green; 20+ blind review rounds
- ⏳ mcp 2.x migration — deliberately deferred (2.x renames
  `mcp.server.fastmcp`; pinned `<2` until its own card)

---

## P1: Smart Search — kb_ask + Agent Tools

> **Goal:** Agent asks questions in natural language, gets answers with citations. Not "formulate a search query" — "answer the question".

> **Status: ✅ delivered** — kb_ask, kb_related, kb_recent and kb_learn are live in
> server.py (with llm.py for the host-LLM handoff). The sections below are the
> design record; the KNOWN LIMITATIONS block still applies.

### 1. ✅ kb_ask() — NL Query Engine

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

### 2. ✅ Query Expansion

Automatic expansion of search queries.

**How:**
- Synonym dictionary: Depends → dependency injection, dependencies
- Stemming: generator → generate, generating, generated
- Standard patterns: "how to" → tutorial, guide, example
- Configurable expansion dictionary

**Files:** storage.py (add _expand_query method)

### 3. ✅ Context-Aware Search

Search knows what the agent viewed before.

**How:**
- Track last N searched collections in session
- Boost results from recently accessed collections
- `context` parameter in kb_ask: `["fastapi", "sqlalchemy"]`
- Weight: recent collection +0.1 boost

**Files:** storage.py (add context tracking)

### 4. ✅ Answer Mode (LLM Summary)

LLM summary from found chunks with citations.

**How:**
- After FTS5 search, send top chunks to LLM
- LLM generates summary with inline citations
- Return: `{answer: str, chunks: list, citations: list}`
- Citations link back to source: `{collection, file_path, chunk_index}`

**Files:** server.py (kb_ask returns answer mode)

### 5. ✅ kb_related() — Related Documents

"What else is related to this?"

**How:**
- Find documents with same tags, same collection, overlapping content
- Score: tag overlap + collection proximity + content similarity
- Return top 5 related documents

**Files:** storage.py (add find_related method), server.py (add tool)

### 6. ✅ kb_recent() — Recent Activity

"What did I look at yesterday?"

**How:**
- Use retrieval_count tracking (already implemented)
- Return most recently accessed documents
- Filter by collection, time range

**Files:** storage.py (add get_recent method), server.py (add tool)

### 7. ✅ kb_learn() — Topic Exploration

"I want to learn about this topic."

**How:**
- Given a topic, find related collections and documents
- Return structured overview: collections, key documents, learning path
- Group by complexity (beginner → advanced)

**Files:** storage.py (add learn_topic method), server.py (add tool)

#### KNOWN LIMITATIONS (P1 search tools, review round 4)

- **N3 — related-doc scoring weights.** `kb_related`'s formula
  (0.4\*tag_overlap + 0.2\*same_collection + 0.4\*content_similarity) is
  spec-derived but lets same-collection proximity dominate realistic lexical
  signals; recalibrating the weights is a future decision.
- **N4 — corpus load per call: fixed (BH-4); memory limit remains.**
  `find_related`/`learn_topic` now read the corpus through a per-Storage
  invalidated in-memory cache (`_cached_documents`): one `_load_documents`
  load per mutation epoch instead of per call. The cache is dropped on every
  document-mutating path (insert/index, add_text, update, delete, bulk insert,
  remove/rename collection, reindex, sync import); bookmarks are deliberately
  outside this list — their table is not part of the documents projection.
  SQLite stays the single source of truth. Resident memory is still O(corpus) — fine for local
  libraries — and invalidation is instance-scoped (a second Storage over the
  same data dir serves its own cache until its own rebuild). Rebuilds are
  lock-guarded (BH-9): the lazy rebuild region and invalidation share one
  lock, and an invalidation racing a rebuild wins (the in-flight snapshot is
  discarded), so no stale epoch is served. Indexing at scale remains the
  future step.
- **N5 — hard term cap in related lookup.** `_related_source_terms` keeps at
  most 512 unique terms (title first); in documents with more than ~511 unique
  word forms (~2000+ words) a subject living only in the body can fall outside
  the cap and drop the candidate. Frequency-based term selection instead of
  first-512 is the future fix.

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

**Status:** ✅ DONE (card P2-1) — `kb_index` shell + `Storage.add_local` landed: local folders index without git (`path://<dir>` or a plain directory), config entries carry `source_type: "local"` + `url: "path://<absolute path>"`, and reindex resolves local dirs from config. URL/text/PyPI/bookmarks stay later cards.

**Review findings (P2-1 blind review) — resolved (P2-H hygiene wave, 2026-11-15):** the `check_stale` hash bug (fixed in P2-H, H-3: both sides compare the 16-char hash prefix) and the `find_changed_docs` mask ignorance + `.git` sweep (fixed in P2-H, H-4: stored mask + dot-path exclusion) are closed; the moved-folder / `kb_index` re-add docs line landed in `docs/api/mcp-tools.md` (P2-H). History: all three were pre-existing at the P2-1 HEAD and stayed open through P2-2.

### 9. URL/Web Page Indexing

`kb_index("https://docs.python.org/3/library/asyncio.html")`

**How:**
- Fetch URL content (requests + BeautifulSoup or similar)
- Extract main content (strip nav, footer, ads)
- Index as single document or split by headings
- Store URL as file_path, domain as collection

**Files:** storage.py (add add_url method), server.py (add tool), new: fetcher.py

**Status:** ✅ DONE (card P2-3) — delivered with **stdlib only** (`urllib.request` + `html.parser`, ZERO new dependencies BY DESIGN — the card's zero-deps rule overrides the plan text's "requests + BeautifulSoup or similar"). `fetcher.fetch_url` returns {final url, title, text}: http(s)-only with custom redirect handling (max 3 hops, every hop's scheme re-validated — no downgrade to file://), 10s timeout, User-Agent header, HTML-only content types, charset from header (utf-8 fallback, errors="replace"), 10MB cap (the existing `_MAX_CONTENT_LENGTH` precedent). Extraction strips script/style/noscript/nav/footer/header/aside subtrees entirely, title from `<title>` else first `<h1>`, block-level newlines, `<pre>` preserved — deliberately not a readability engine. `Storage.add_url` indexes the extracted text through the shared chunker with URL-keyed identity (file_path = final normalized URL; re-index replaces ALL chunks — no stale rows when a page shrinks — and preserves created_at/retrieval_count per the P2-2 F-4 lesson; corpus-cache-hooked write path, BH-4). `kb_index` routing: an http(s) URL that is NOT repo-shaped (github.com/<owner>/<repo> ≤2 path segments, github:// scheme, git@host:owner/repo.git — P2-1 behavior unchanged) routes to fetch; deeper git-host URLs (e.g. `.../blob/main/README.md`) are pages; `type="url"`/`"web"` forces fetch, `type="github"` forces repo. Collection = slugified domain (`docs.python.org` → `docs-python-org`), `collection=` overrides. No new MCP tool — URL mode is a kb_index mode (tool count stays 32). Tests 532 → 571 (+39 anchors).

**Security — residual SSRF surface (documented honestly in docs/api/mcp-tools.md):** the 59-issue review's SSRF fix (commit 0762f2f: `validate_url`'s git-host domain allowlist) still gates clone targets and is reused by the repo-shaped check. A web-page fetch is only validated as http(s): `kb_index(url)` fetches anything the host machine can reach (loopback, internal networks). Accepted for a local-first tool run by its owner — arbitrary web pages are the feature. Mitigations: per-hop redirect scheme re-validation, 10MB cap, HTML-only, fixed timeout. robots.txt is deliberately NOT consulted (non-goal).

### 10. Plain Text Indexing

`kb_index("text", content="...")` or paste in CLI

**How:**
- Accept text content directly
- Generate title from first line or hash
- Index as single document
- CLI: `echo "content" | docs-haven index --text`

**Files:** storage.py (add add_text method), cli.py (add --text flag)

**Status:** ✅ DONE (card P2-2) — `Storage.add_text` + `kb_index` text mode (`text://<title>` or `type="text"`, `content=` required) landed: notes chunk with the shared chunker into the documents projection (fully searchable, corpus-cache-hooked writes), file paths are content-addressed (`text://<slug>-<sha256[:16] of content>` — identical content re-added is an idempotent replace, different content gets a different path unless two contents collide on sha256[:16]), and the title comes from the first line or hash exactly as specced. The CLI `--text` flag is an explicit non-goal of P2-2 and stays on the CLI card.

### 11. PyPI Package Indexing

`kb_index("pypi://fastapi")`

**How:**
- Download package via `pip download --no-deps --dest /tmp`
- Extract documentation (README, docs/ if present)
- Index markdown/rst files
- Store as collection with package name

**Files:** storage.py (add add_pypi method), pypi.py (new, download/extract layer), server.py (kb_index pypi mode)

**Status:** ✅ DONE (card P2-4) — `Storage.add_pypi` + `kb_index` pypi mode (`pypi://<name>` or `type="pypi"` with a bare package name) landed as decided: **latest release only** (`pypi://pkg==1.2` is out); **stdlib unpack** (urllib + `tarfile`/`zipfile`) — the plan text's `pip download` is superseded because **pip is deliberately not used** (uv venvs ship no pip); README*/docs* extracted nearly raw (no rst rendering). Identity `pypi://<name>/<relpath>`, collection = slugified package name; re-index replaces the collection's `pypi://` rows in one transaction (created_at/retrieval_count preserved per surviving path, P2-H rollback). Unpack safety: tar `filter="data"` (tar-slip), zip member validation. Artifact cap 40MB — derived ×4 of the 10MB content precedent (documented as derived in CHANGELOG). No new tool (kb_index mode; tools stay 32).

### 12. Bookmark / Fav List

"Save this link, I'll come back to it"

**How:**
- `kb_bookmark(url, title?, note?)` — save URL with metadata
- `kb_bookmarks()` — list saved bookmarks
- Bookmarks stored in separate table
- Lightweight — no content indexing, just URL + metadata

**Files:** storage.py (add bookmarks table), server.py (add tools)

**Status:** ✅ DONE (card P2-2) — owner-approved `bookmarks` table (separate from documents, no content indexing), `kb_bookmark` upsert keyed by UNIQUE(url) — the upsert IS the edit (repeat saves update title/note/updated_at, `created_at` preserved) — and `kb_bookmarks` listing most recent first. http(s)-only URLs validated by `validate_http_url`. Deliberately outside the documents projection: bookmark writes carry no corpus-cache hooks, and searching bookmarks through `kb_search` is out of scope (they carry no content).

**Review findings (P2-2 blind review, 2 rounds) — resolved (P2-H hygiene wave, 2026-11-15):** missing transactional rollback (fixed in P2-H, H-1: every multi-statement write rolls back in `except`), cold-start `database is locked` (fixed in P2-H, H-2: `busy_timeout` first + bounded PRAGMA retry), bookmark URL case-folding (fixed in P2-H, H-5: scheme+host folded per RFC 3986) and the whitespace-note hash doubling (fixed in P2-H, H-6: `text://note-<hash16>`). History: all four were open at the P2-2 HEAD; `add_text` had already gotten its rollback in P2-2's fix round.

**Deferred residuals (P2-H fix-round blind review; recorded, not fixed — each its own card):**
- single-write paths call `conn.commit()` without a rollback guard — a failed commit leaves the statement pending and a later write on the same connection persists it (`delete_document` repro);
- `find_changed_docs` swallows `OSError` and the whole collection silently reports `Ok(0)` "no changes" (which also makes `reindex_collection`'s `except OSError` nearly unreachable);
- `mask="**"` indexes nothing (`Path.glob("**")` matches directories only; pre-existing);
- `test_sync.py::test_export_deduplicates` is flaky at a UTC second
  boundary: `sync.py` hashes `exported_at` (second granularity) into the
  chunk payload, so two exports straddling the boundary miss dedup;
- a `text://` note stored in a *file* collection is reported `deleted`
  and purged by reindex (file collections have no notes on disk to match;
  same class as the items above);
- P2-4 replace is collection-scoped: indexing pkg-b into a shared
  `collection=` silently destroys pkg-a's `pypi://` rows (the TZ said
  "all pypi:// rows of the collection" — spec-compliant but sharp);
  identity also keeps raw package case, so `Test-Pkg` re-index loses the
  F-4 preservation. Follow-up: scope delete to `pypi://<package>/%` and
  case-normalize per PEP 503;
- P2-4 fetch-side empty-text docs are skipped without an anchor
  (storage-side Err is anchored).

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

## Rejected / not pursued

- **LLM argument aliasing (alias.py, 70+ aliases)** — replaced by native per-parameter `validation_alias=AliasChoices(...)` on the hot MCP tools. The old module was never wired and its alias map contradicted itself ("path" was both alias and target); pydantic resolves hallucinated parameter names natively without polluting the tools/list schema.

---

## Roadmap Timeline

| Phase | Scope | Estimate |
|-------|-------|----------|
| **P1** | kb_ask + Query Expansion + Context-aware + kb_related + kb_recent + kb_learn | ✅ Delivered |
| **P2** | Local/URL/Text/PyPI indexing + Bookmarks | 1-2 weeks |
| **P3** | repos table + hierarchy + tags M2M + freshness + versions + auto-tags + cross-refs | 2-3 weeks |
| **P4** | Primitive API + layered tools + consolidation | 3-5 days |
| **P5** | Standalone EXE + Web Dashboard + auto-update | 2-3 weeks |
| **Deferred** | Graph, RAG, Plugins, Multi-User, Backup | When needed |

---

*Last updated: 2026-11-15*
