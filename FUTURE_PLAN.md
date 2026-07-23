# FUTURE PLAN — docs-haven

> Roadmap of potential improvements and features.

---

## Implemented

### 1. Vector Search ✅ (v0.6.0)
TF-IDF based vector search in `vector.py`. No external dependencies.

### 2. Context Hierarchy ✅ (v0.7.0)
`context_attachments` table with CRUD operations. MCP tools: `kb_context_add`, `kb_context_list`, `kb_context_rm`.

### 3. Score Explanation ✅ (v0.4.0)
`explain=True` parameter in search returns per-signal breakdown.

### 4. Dual Mode (MCP + CLI) ✅ (v0.7.0)
Full CLI with 11 commands. MCP server with 20 tools.

### 5. Export/Import CLI ✅ (v0.7.0)
`export --format json/csv/md`, `import backup.json`.

### 6. Incremental Embedding ✅ (v0.7.0)
`find_changed_docs()` and `reindex_collection()` for selective re-indexing.

### 7. Collection Rename ✅ (v0.7.0)
`collection rename old new` (CLI + MCP).

### 8. LLM Argument Aliasing ✅ (v0.8.0)
70+ aliases in `alias.py`. Intercepts hallucinated parameter names at MCP boundary.

### 9. Importance Scoring ✅ (v0.9.0)
Multi-signal search ranking: recency (exponential decay, 90-day half-life) + retrieval frequency (log-scale).

### 10. Conflict Resolution CLI ✅ (v0.9.0)
`docs-haven conflicts check|resolve|suggest|list` — interactive wizard with diff and judgment recording.

### 11. Benchmark Automation ✅ (v0.9.0)
CI workflow with regression detection (10ms threshold), PR comments, artifact tracking.

### 12. PyPI Publish Automation ✅ (v0.9.0)
GitHub Actions trusted publishing on `v*` tag push.

### 13. Code Quality Cleanup ✅ (v0.9.0)
59/59 code review issues resolved. storage.py split into validation/chunking/scoring modules. All god methods refactored.

### 14. Security Hardening ✅ (v0.9.0)
Decompression bomb streaming, connection leak fix, chunk ID validation, thread-local connections, GitHub Actions SHA pinning.

---

## High Priority

### 1. kb_get_repo_structure
Show file tree of an indexed repository.

**Why:** Users need to navigate repository structure without downloading. Essential for "show me the project layout" queries.

**How:**
- MCP tool: `kb_get_repo_structure(repo_name, pattern?)`
- Read file list from DB (stored during indexing)
- Return tree structure with file types and sizes
- Optional glob filter (`**/*.py`)

**Files:** storage.py (add method), server.py (add tool), cli.py (add command)

### 2. Tag Filter
Filter search results by tags stored in collection metadata.

**Why:** Tags are stored during `kb_add_repo` but never used for filtering. Users tag repos for a reason.

**How:**
- Add `tags` column to collections config (already exists in config.json)
- MCP tool parameter: `tags: list[str]` on `kb_search`
- CLI: `docs-haven search "query" --tags python,fastapi`
- Filter at SQL level: `WHERE collection IN (SELECT name FROM config WHERE tags LIKE ?)`

**Files:** storage.py (add filter), server.py (update kb_search), cli.py (add --tags flag)

### 3. Language Filter
Filter by file extension / programming language.

**Why:** Multi-language repos mix Python, JS, YAML. Users often want "show me only Python docs".

**How:**
- Store `extension` during indexing (already done)
- MCP tool parameter: `language: str` on `kb_search`
- CLI: `docs-haven search "query" --language py`
- Filter: `WHERE extension IN ('.py', '.pyx', '.pyi')` for python

**Files:** storage.py (add filter), server.py (update kb_search), cli.py (add --language flag)

### 4. Update Policies (once/auto/manual)
Control how repos are updated after initial indexing.

**Why:** Currently only manual reindex. Users want repos to stay fresh automatically.

**How:**
- Config field: `update_policy: "once" | "auto" | "manual"` per repo
- `kb_update_repo(repo_name)` — pull + reindex changed files
- `kb_update_all()` — update all repos with policy `auto`
- CLI: `docs-haven repo update <name>`, `docs-haven repo update --all`
- Scheduled via cron or manual trigger

**Files:** storage.py (add update methods), server.py (add tools), cli.py (add commands)

### 5. RRF (Reciprocal Rank Fusion)
Better hybrid search combining FTS5 and vector results.

**Why:** Current merge is simple append+dedup. RRF produces better rankings by considering rank position.

**How:**
```python
def rrf_score(fts_rank: int, vec_rank: int, k: int = 60) -> float:
    return 1 / (k + fts_rank) + 1 / (k + vec_rank)
```
- Apply after merging FTS5 and vector results
- Sort by RRF score instead of raw relevance
- Configurable `k` parameter (default 60)

**Files:** storage.py (update _run_hybrid_search)

---

## Medium Priority

### 1. Metadata: Language & Frequency
Track and expose document language and access frequency.

**Why:** Useful for analytics ("what languages do I use most?") and better search ranking.

**How:**
- Detect language during indexing (simple heuristic: file extension + keyword frequency)
- Store in documents table: `language TEXT DEFAULT ''`
- `kb_stats` returns language distribution
- Frequency already tracked via `retrieval_count`

**Files:** storage.py (add language detection), server.py (update kb_stats)

### 2. Collection Statistics Enhancement
Richer stats with language distribution, top tags, storage breakdown.

**Why:** Current `kb_stats` only shows counts. Users want to understand their knowledge base composition.

**How:**
- Language distribution: `{"python": 45, "javascript": 30, "markdown": 25}`
- Top tags: `{"fastapi": 5, "react": 3}`
- Storage per collection
- Last indexed date per collection

**Files:** storage.py (enhance stats method)

---

## Low Priority

### 1. Curated Starter Collection
Pre-loaded repos as demo and quick-start reference.

**Why:** New users don't know what to index. A curated collection shows docs-haven's value immediately. From kb-mcp concept: "10-20 шаблонов Лили".

**How:**
- `docs-haven init --demo` — adds 10-20 curated repos
- Curated list: fastapi, flask, sqlalchemy, 30-seconds-of-code, developer-roadmap, etc.
- Stored in `~/.docshaven/starter/` (separate from user repos)
- Can be updated via `docs-haven init --demo --update`

**Files:** cli.py (add init command), curated list in a JSON/YAML config

### 2. Content Freshness Tracking
Detect and surface stale documents.

**Why:** From kb-mcp risk: "Контент устаревает — высокая вероятность". Repos change over time, indexed content may be outdated.

**How:**
- Track `last_indexed_at` per collection
- `kb_stats` shows "last updated X days ago" per collection
- `docs-haven check-stale` — lists collections not updated in N days
- MCP tool: `kb_check_stale(days=30)` returns stale collections
- Visual indicator in search results: "indexed 45 days ago"

**Files:** storage.py (add staleness tracking), server.py (add tool), cli.py (add command)

### 3. Complexity Filter
Filter by estimated code complexity.

**Why:** From kb-mcp concept: `complexity: "beginner" | "intermediate" | "advanced"`. Useful for learning-oriented searches.

**How:**
- Estimate during indexing: file size + nesting depth + import count
- Store as `complexity TEXT DEFAULT ''` in documents
- MCP tool parameter: `complexity: str` on `kb_search`
- CLI: `docs-haven search "query" --complexity beginner`

**Files:** storage.py (add complexity estimation), server.py (update kb_search)

### 4. Plugin System
Extensible architecture for custom features.

**Why:** Users may need custom search strategies, storage backends, or integrations.

**How:**
- Plugin API: `docs-haven plugin install <name>`
- Custom search strategies
- Custom storage backends
- Webhook support

### 5. Multi-User Support
Shared knowledge base with access control.

**Why:** Teams need shared knowledge bases with permissions.

**How:**
- User authentication
- Per-user collections
- Role-based access (admin, editor, viewer)
- Shared vs private collections

---

## Future (Deferred)

### 1. Web Dashboard
Visual interface for browsing and managing knowledge base.

**Why:** Some users prefer visual tools over CLI/MCP. Not critical for agent workflows.

**How:**
- FastAPI web UI
- Browse collections, search, view documents
- Conflict resolution interface
- Sync status dashboard
- **Defer until:** CLI + MCP proven insufficient for user base

### 2. RAG (Full Semantic Search)
Embedding-based semantic search with local models.

**Why:** TF-IDF is keyword-based. RAG would enable semantic understanding ("how to handle errors" → finds error handling patterns even without exact keywords).

**How:**
- Local embeddings: MiniLM, all-MiniLM-L6-v2 via sentence-transformers
- Vector DB: Qdrant or ChromaDB (embedded mode)
- Hybrid: RRF combining FTS5 + semantic search
- **Defer until:** TF-IDF proven insufficient for real usage patterns
- **Cost:** +200MB dependencies, +100MB RAM per 1000 docs

### 3. Backup System
Automated backups of index and metadata.

**Why:** Git sync handles multi-machine sync, but doesn't protect against accidental deletion.

**How:**
- Periodic snapshots of index.db
- Configurable retention (keep last N backups)
- Restore command
- **Defer until:** Git sync proves insufficient for data safety

---

*Last updated: 2026-07-23*
