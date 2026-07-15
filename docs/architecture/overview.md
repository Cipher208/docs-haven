# Architecture Overview

## Components

```
docs-haven/
├── server.py        # MCP server — tool routing and lifecycle
├── storage.py       # SQLite FTS5 backend — search and indexing
├── uri.py           # URI routing — domain://path organization
├── sync.py          # Git sync — compressed chunks for multi-machine
├── conflicts.py     # Conflict detection — find contradictions
└── tests/           # pytest test suite
```

## Data Flow

```
User/Agent → MCP Tools → server.py → storage.py → SQLite FTS5
                                    → uri.py → collection mapping
                                    → sync.py → compressed chunks
                                    → conflicts.py → contradiction detection
```

## Storage Layer

SQLite with FTS5 full-text search. No external dependencies.

- **documents** table: stores document chunks with metadata
- **documents_fts** virtual table: FTS5 index for BM25 search
- **triggers**: keep FTS in sync with document changes

### Search Strategies

| Strategy | When | How |
|----------|------|-----|
| `fts` | Short queries (<=2 words) | FTS5 BM25 only |
| `hybrid` | Longer queries | FTS5 + LIKE fallback |
| `auto` | Default | Picks by query length |

Pattern from mcp-ariel-memory.

## URI Routing

Organizes knowledge by domain:

```
core://fastapi/dependencies  →  core__fastapi collection
guide://testing/pytest       →  guide__testing collection
ref://sqlalchemy/orm         →  ref__sqlalchemy collection
```

7 domains: `core`, `ref`, `guide`, `lib`, `src`, `test`, `note`

Pattern from nocturne_memory.

## Git Sync

Each sync creates a NEW compressed chunk. Never modifies old files.

```
.docshaven-sync/
├── manifest.json          # Index (small, merge-friendly)
└── chunks/
    ├── a3f8c1d2.jsonl.gz  # Chunk 1
    └── b7d2e4f1.jsonl.gz  # Chunk 2
```

Pattern from engram.

## Conflict Surfacing

When adding a document, searches for similar titles. If found, flags potential conflicts.

```
New doc: "FastAPI dependency injection"
→ FTS5 search: "FastAPI Guide" (score: 0.85)
→ Conflict detected, judgment required
```

Pattern from engram.
