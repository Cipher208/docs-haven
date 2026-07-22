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

---

## High Priority

### 1. LLM Argument Aliasing
Intercept and rewrite hallucinated parameter names.

**Why:** LLMs invent parameter names that look plausible but fail validation. This causes confusing errors.

**How:**
- Map common aliases: `userQuery` → `query`, `libraryID` → `libraryId`
- Apply at MCP transport level before tool execution
- Log aliased calls for debugging

### 2. Importance Scoring
Multi-signal scoring for document relevance.

**Why:** Not all documents are equally important. Recency, frequency, type should affect ranking.

**How:**
- Signals: base relevance, length, keywords, recency, retrieval frequency
- Type boost: tutorials weighted higher than raw docs
- Configurable weights per collection

---

## Medium Priority

### 1. Conflict Resolution UI
Interactive conflict resolution instead of just detection.

**Why:** Current system detects conflicts but resolution is manual.

**How:**
- CLI wizard: `docs-haven conflicts resolve`
- Show diff between conflicting docs
- Suggest merge strategies
- Auto-resolve low-confidence conflicts

### 2. Web Dashboard
Visual interface for browsing and managing knowledge base.

**Why:** Some users prefer visual tools over CLI/MCP.

**How:**
- Flask/FastAPI web UI
- Browse collections, search, view documents
- Conflict resolution interface
- Sync status dashboard

---

## Low Priority

### 1. Plugin System
Extensible architecture for custom features.

**Why:** Users may need custom search strategies, storage backends, or integrations.

**How:**
- Plugin API: `docs-haven plugin install <name>`
- Custom search strategies
- Custom storage backends
- Webhook support

### 2. Benchmark Automation
Continuous performance tracking.

**Why:** Performance regressions should be caught before release.

**How:**
- CI benchmark job
- Track indexing speed, search latency, throughput
- Compare against baseline
- Alert on degradation

### 3. Multi-User Support
Shared knowledge base with access control.

**Why:** Teams need shared knowledge bases with permissions.

**How:**
- User authentication
- Per-user collections
- Role-based access (admin, editor, viewer)
- Shared vs private collections

---

*Last updated: 2026-07-22*
