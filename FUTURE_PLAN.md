# FUTURE PLAN — docs-haven

> Roadmap of potential improvements and features.

---

## High Priority

### 1. Vector Search
Add semantic search alongside BM25 using sqlite-vec or sentence-transformers.

**Why:** BM25 handles keywords well but misses semantic similarity. "How to handle errors" won't match "exception handling patterns".

**How:**
- Optional dependency: `sentence-transformers` or `sqlite-vec`
- Store embeddings alongside chunks in SQLite
- Hybrid scoring: blend BM25 + cosine similarity
- Auto-detect: use vector when available, fallback to FTS5

### 2. Context Hierarchy
Organize knowledge with path-to-description mapping.

**Why:** Raw collection names don't convey meaning. Users need to understand what each collection contains.

**How:**
```
core://fastapi/dependencies → "FastAPI dependency injection patterns"
guide://testing/pytest → "Testing guide with pytest fixtures"
```
- Add `context` field to collections
- Return context alongside search results
- Feed context to LLM during reranking (if added)

### 3. Score Explanation
Show how each search result was scored.

**Why:** Users don't know why results rank the way they do. Transparency builds trust.

**How:**
```python
{
    "title": "FastAPI Guide",
    "score": 0.85,
    "explain": {
        "bm25": 0.7,
        "type_boost": 0.15,
        "collection_match": 0.0
    }
}
```
- Add `explain=True` parameter to search
- Return per-signal breakdown

---

## Medium Priority

### 4. LLM Argument Aliasing
Intercept and rewrite hallucinated parameter names.

**Why:** LLMs invent parameter names that look plausible but fail validation. This causes confusing errors.

**How:**
- Map common aliases: `userQuery` → `query`, `libraryID` → `libraryId`
- Apply at MCP transport level before tool execution
- Log aliased calls for debugging

### 5. Importance Scoring
Multi-signal scoring for document relevance.

**Why:** Not all documents are equally important. Recency, frequency, type should affect ranking.

**How:**
- Signals: base relevance, length, keywords, recency, retrieval frequency
- Type boost: tutorials weighted higher than raw docs
- Configurable weights per collection

### 6. Emotion Detection
Detect emotionally charged content and prioritize it.

**Why:** Important decisions, urgent requests, and critical errors should rank higher.

**How:**
- Pattern matching for emotional markers (Russian + English)
- Priority boost for urgency indicators
- Configurable sensitivity

### 7. Dual Mode (MCP + CLI)
Already have CLI. Enhance with MCP-aware features.

**Why:** CLI users shouldn't need to start an MCP server for simple operations.

**How:**
- `docs-haven search "query"` — direct FTS5 search
- `docs-haven add <url>` — add repository
- `docs-haven serve` — start MCP server
- Shared database between CLI and MCP

---

## Low Priority

### 8. Incremental Embedding
Only re-embed changed documents, not entire collection.

**Why:** Re-embedding 1000 docs when 1 changed is wasteful.

**How:**
- Track document hashes
- On update, only embed changed chunks
- Background worker for bulk re-embedding

### 9. Conflict Resolution UI
Interactive conflict resolution instead of just detection.

**Why:** Current system detects conflicts but resolution is manual.

**How:**
- CLI wizard: `docs-haven conflicts resolve`
- Show diff between conflicting docs
- Suggest merge strategies
- Auto-resolve low-confidence conflicts

### 10. Collection Templates
Pre-built templates for common documentation types.

**Why:** Users shouldn't have to configure everything from scratch.

**How:**
```
docs-haven template python-docs  # auto-configure for Python docs
docs-haven template api-docs     # auto-configure for REST API
docs-haven template wiki         # auto-configure for knowledge base
```

### 11. Web Dashboard
Visual interface for browsing and managing knowledge base.

**Why:** Some users prefer visual tools over CLI/MCP.

**How:**
- Flask/FastAPI web UI
- Browse collections, search, view documents
- Conflict resolution interface
- Sync status dashboard

### 12. Plugin System
Extensible architecture for custom features.

**Why:** Users may need custom search strategies, storage backends, or integrations.

**How:**
- Plugin API: `docs-haven plugin install <name>`
- Custom search strategies
- Custom storage backends
- Webhook support

### 13. Benchmark Automation
Continuous performance tracking.

**Why:** Performance regressions should be caught before release.

**How:**
- CI benchmark job
- Track indexing speed, search latency, throughput
- Compare against baseline
- Alert on degradation

### 14. Multi-User Support
Shared knowledge base with access control.

**Why:** Teams need shared knowledge bases with permissions.

**How:**
- User authentication
- Per-user collections
- Role-based access (admin, editor, viewer)
- Shared vs private collections

### 15. Export/Import CLI
Bulk data operations.

**Why:** Users need to backup, migrate, or share knowledge bases.

**How:**
```
docs-haven export --format json > backup.json
docs-haven import backup.json
docs-haven export --collection fastapi --format markdown
```

---

## Implementation Order

| Phase | Features | Effort |
|-------|----------|--------|
| Phase 1 | Vector Search, Context Hierarchy, Score Explain | High |
| Phase 2 | LLM Aliasing, Importance Scoring, Emotion Detection | Medium |
| Phase 3 | Incremental Embedding, Conflict UI, Templates | Medium |
| Phase 4 | Web Dashboard, Plugin System, Benchmark | Low |
| Phase 5 | Multi-User, Export/Import, Advanced | Low |

---

## Technical Notes

### SQLite FTS5 Limitations
- No native vector search (need sqlite-vec extension)
- BM25 scoring is good but not semantic
- LIKE fallback is slow for large datasets

### Recommended Architecture Evolution
```
Current:  FTS5 only
Phase 1:  FTS5 + sqlite-vec (hybrid)
Phase 2:  FTS5 + sqlite-vec + LLM reranking
Phase 3:  FTS5 + sqlite-vec + reranking + importance scoring
```

### Dependencies to Consider
- `sentence-transformers` — Python embeddings (heavy, ~500MB)
- `sqlite-vec` — SQLite vector extension (lightweight)
- `hnswlib` — Approximate nearest neighbors (for large datasets)
- `flask`/`fastapi` — Web dashboard

---

*Last updated: 2026-07-16*
