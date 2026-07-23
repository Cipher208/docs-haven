# FUTURE PLAN — docs-haven

> Roadmap based on Vision v1.0 + spec + community feedback.
> 
> **Личная библиотека разработчика.** MCP-сервер для агента: не просто «поиск по репам», а живая память — индексированные библиотеки, документация, ссылки, заметки — с ответами на естественном языке.

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

> **Цель:** Агент задаёт вопросы на естественном языке, получает ответы с цитатами. Не "сформулируй поисковый запрос", а "ответь на вопрос".

### 1. kb_ask() — NL Query Engine

Natural language → search → LLM summary → `{answer, chunks, citations}`.

```
kb_ask("как в FastAPI сделать Depends с параметрами?")
  → парсинг: извлечь ключевые слова (FastAPI, Depends, параметры)
  → FTS5 с query expansion (Depends, dependency injection, dependencies)
  → ранжирование с учётом контекста (что сейчас смотрел)
  → LLM-резюме: "Вот как. Три подхода: ..."
  → возврат: {answer, chunks[], citations[]}
```

**Why:** Ключевая фича Vision v1.0. Агент не должен сам формулировать запросы — он должен задавать вопросы.

**How:**
- LLM вызывается через MCP host (не внутри DocsHaven — zero deps)
- Query expansion: "async generator" → async AND generator, async_generator, yield from
- Context-aware: приоритезирует ту же коллекцию что и предыдущий запрос
- Answer mode: LLM-резюме по найденным чанкам с цитатами

**Files:** server.py (add kb_ask tool), storage.py (query expansion), new: llm.py (LLM integration via MCP host)

### 2. Query Expansion

Автоматическое расширение поисковых запросов.

**How:**
- Словарь синонимов: Depends → dependency injection, dependencies
- Стемминг: generator → generate, generating, generated
- Стандартные паттерны: "how to" → tutorial, guide, example
- Конфигурируемый expansion dictionary

**Files:** storage.py (add _expand_query method)

### 3. Context-Aware Search

Поиск знает что агент смотрел раньше.

**How:**
- Track last N searched collections in session
- Boost results from recently accessed collections
- `context` parameter in kb_ask: `["fastapi", "sqlalchemy"]`
- Weight: recent collection +0.1 boost

**Files:** storage.py (add context tracking)

### 4. Answer Mode (LLM Summary)

LLM-резюме по найденным чанкам с цитатами.

**How:**
- After FTS5 search, send top chunks to LLM
- LLM generates summary with inline citations
- Return: `{answer: str, chunks: list, citations: list}`
- Citations link back to source: `{collection, file_path, chunk_index}`

**Files:** server.py (kb_ask returns answer mode)

### 5. kb_related() — Related Documents

"Что ещё связано с этим?"

**How:**
- Find documents with same tags, same collection, overlapping content
- Score: tag overlap + collection proximity + content similarity
- Return top 5 related documents

**Files:** storage.py (add find_related method), server.py (add tool)

### 6. kb_recent() — Recent Activity

"Что я смотрела вчера?"

**How:**
- Use retrieval_count tracking (already implemented)
- Return most recently accessed documents
- Filter by collection, time range

**Files:** storage.py (add get_recent method), server.py (add tool)

### 7. kb_learn() — Topic Exploration

"Хочу изучить тему."

**How:**
- Given a topic, find related collections and documents
- Return structured overview: collections, key documents, learning path
- Group by complexity (beginner → advanced)

**Files:** storage.py (add learn_topic method), server.py (add tool)

---

## P2: Indexation Sources — Расширение источников

> **Цель:** Индексировать не только GitHub repos, а всё: локальные папки, URL, PyPI, plain text.

### 8. Local Folder Indexing

`kb_add "path:///usr/lib/python3.14/asyncio"`

**How:**
- Add `source_type: "local"` to repo metadata
- Index files matching mask from local directory
- No git — direct file read
- Validate path exists and is readable

**Files:** storage.py (add add_local method), server.py (update kb_add_repo)

### 9. URL/Web Page Indexing

`kb_add "https://docs.python.org/3/library/asyncio.html"`

**How:**
- Fetch URL content (requests + BeautifulSoup or similar)
- Extract main content (strip nav, footer, ads)
- Index as single document or split by headings
- Store URL as file_path, domain as collection

**Files:** storage.py (add add_url method), server.py (add tool), new: fetcher.py

### 10. Plain Text Indexing

`kb_add --text "..."` или paste в CLI

**How:**
- Accept text content directly
- Generate title from first line or hash
- Index as single document
- CLI: `echo "content" | docs-haven add --text`

**Files:** storage.py (add add_text method), cli.py (add --text flag)

### 11. PyPI Package Indexing

`kb_add "pypi://fastapi"`

**How:**
- Download package via `pip download --no-deps --dest /tmp`
- Extract documentation (README, docs/ if present)
- Index markdown/rst files
- Store as collection with package name

**Files:** storage.py (add add_pypi method), server.py (add tool)

### 12. Bookmark / Fav List

"Сохрани эту ссылку, я к ней вернусь"

**How:**
- `kb_bookmark(url, title?, note?)` — save URL with metadata
- `kb_bookmarks()` — list saved bookmarks
- Bookmarks stored in separate table
- Lightweight — no content indexing, just URL + metadata

**Files:** storage.py (add bookmarks table), server.py (add tools)

---

## P3: Database Depth — Иерархия и структура

> **Цель:** БД с иерархией, тегами, версиями — не плоская таблица documents.

### 13. Repos Table (separate from documents)

Отдельная таблица для репозиториев с метаданными.

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

**Why:** Сейчас repos хранятся в config.json. Отдельная таблица позволяет SQL-запросы, фильтрацию, статистику.

**Files:** storage.py (migration + new table)

### 14. Collections with Parent_id (Hierarchy)

Иерархические коллекции: `python → stdlib → asyncio`

**How:**
- Add `parent_id INTEGER REFERENCES collections(id)` to collections
- `kb_ask("покажи всё про async в python")` → hierarchy: python → stdlib → asyncio
- Tree navigation: `kb_tree("python")` returns child collections

**Files:** storage.py (migration + hierarchy methods)

### 15. Tags M2M Table

Many-to-many связь документов и тегов.

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

**Why:** Теги хранятся в config.json как comma-separated. M2M таблица позволяет SQL-фильтрацию, агрегации, навигацию по тегам.

**Files:** storage.py (migration + tag methods)

### 16. Freshness Score

Автоматический расчёт актуальности документа.

**How:**
- `freshness_score = f(age, last_updated, retrieval_count)`
- Age: exponential decay from `created_at`
- Last updated: boost if recently changed
- Retrieval: boost if frequently accessed
- Display in search results: "freshness: 0.85"

**Files:** storage.py (add freshness calculation)

### 17. Version Tracking

Версионирование документов.

**How:**
- Store `version_tag` in documents table (e.g., "fastapi 0.95")
- `kb_version("fastapi 0.95")` — search within specific version
- Compare versions: `kb_diff("fastapi", "0.94", "0.95")`

**Files:** storage.py (add version methods)

### 18. Auto-Tags from Content

Автоматическое определение тегов по содержимому.

**How:**
- During indexing, detect: language, framework, topic
- Language: file extension mapping
- Framework: keyword detection (import fastapi → "fastapi")
- Topic: title/content keyword extraction
- Store as tags in document_tags

**Files:** storage.py (add auto_tag method)

### 19. Cross-References Between Documents

Связи между документами через ссылки и упоминания.

**How:**
- Detect `[[wikilinks]]`, `[text](url)`, `import X` in content
- Build cross-reference graph
- `kb_related()` uses cross-refs for recommendations

**Files:** storage.py (add cross-ref extraction)

---

## P4: Primitive API — Мало инструментов, много слоёв

> **Цель:** 5 базовых примитивов для агента. Остальное — layered для power users.

### 20. Primitive Tools (Agent-Facing)

Агент видит только 5 инструментов:

```
kb_ask(query, context?)     → {answer, chunks, citations}
kb_index(source, type?)     → {collection, docs_indexed}
kb_search(query, filters?)  → [{chunk, score, source}]
kb_get(file_path)           → {content, metadata}
kb_stats()                  → {collections, docs, languages, tags}
```

**Why:** Из vision: "Агенту нужно 5. Остальное — technical tools для администрирования."

**How:**
- `kb_ask` — NL query → search → answer (P1 #1)
- `kb_index` — smart add with auto-type detection (P2 #8-11)
- `kb_search` — current search with filters (tags, language, complexity)
- `kb_get` — current kb_get
- `kb_stats` — enhanced stats (languages, tags, freshness)

### 21. Layered Tools (Technical/Power User)

Остальные инструменты доступны, но не в основном API:

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

**Why:** Не показывать агенту 20+ инструментов. Дать 5 ключевых, остальное — по запросу.

### 22. Tool Consolidation

Объединение дублирующих инструментов.

**How:**
- `kb_add_repo` + `kb_add_local` + `kb_add_url` + `kb_add_text` → `kb_index(source, type)`
- `kb_conflict_check` + `kb_conflict_suggest` → `kb_conflict_check` (включает suggestions)
- `kb_context_add` + `kb_context_list` + `kb_context_rm` → `kb_context(action, ...)`

**Files:** server.py (consolidate tools)

---

## P5: Standalone Version — Автономная версия для пользователя

> **Цель:** EXE/дистрибутив для non-агент пользователей. С веб-интерфейсом.

### 23. Standalone Desktop App (EXE)

Автономная версия.docs-haven без MCP, с GUI.

**How:**
- PyInstaller / cx_Freeze → single EXE
- Встроенный FastAPI server + веб-интерфейс
- Auto-start server on launch
- System tray icon
- No MCP dependency — standalone

**Files:** new: desktop.py, new: web/ directory

### 24. Web Dashboard (для standalone)

Визуальный интерфейс для standalone версии.

**How:**
- Browse collections, search, view documents
- Conflict resolution interface
- Sync status dashboard
- Settings page (update policies, tags)
- **Defer until:** standalone version ready

**Files:** new: web/templates/, new: web/static/

### 25. Auto-Update для standalone

Автоматическое обновление standalone версии.

**How:**
- Check GitHub releases for new versions
- Download and replace EXE
- User notification + restart

**Files:** new: updater.py

---

## Deferred

### 1. Epistemic Graph
Граф знаний с автоматическими связями.

**Why:** Vision: "отложено до 100+ коллекций. Связи между документами сейчас очевидны."

**When:** 100+ коллекций, cross-collection search becomes bottleneck.

### 2. RAG (Full Semantic Search)
Embedding-based semantic search с локальными моделями.

**How:** MiniLM, sentence-transformers, Qdrant/ChromaDB.
**When:** TF-IDF перестанет находить нужное.
**Cost:** +200MB deps, +100MB RAM per 1000 docs.

### 3. Plugin System
Расширяемая архитектура.

**How:** Plugin API, custom strategies, custom backends.
**When:** Появятся запросы от сообщества.

### 4. Multi-User Support
Аутентификация, роли, per-user collections.

**When:** Появятся командные use cases.

### 5. Backup System
Автоматические бэкапы индекса.

**When:** Git sync перестанет обеспечивать безопасность данных.

---

## Roadmap Timeline

| Phase | Scope | Estimate |
|-------|-------|----------|
| **P1** | kb_ask + Query Expansion + Context-aware + kb_related + kb_recent | 1-2 недели |
| **P2** | Local/URL/Text/PyPI indexing + Bookmarks | 1-2 недели |
| **P3** | repos table + hierarchy + tags M2M + freshness + versions + auto-tags + cross-refs | 2-3 недели |
| **P4** | Primitive API + layered tools + consolidation | 3-5 дней |
| **P5** | Standalone EXE + Web Dashboard + auto-update | 2-3 недели |
| **Deferred** | Graph, RAG, Plugins, Multi-User, Backup | Когда понадобится |

---

*Last updated: 2026-07-23*
