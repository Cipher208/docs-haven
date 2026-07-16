# ADR-001: Use SQLite FTS5 Instead of Elasticsearch/PostgreSQL

## Status
Accepted

## Context
Need full-text search for local knowledge base with zero dependencies.

## Decision
SQLite FTS5 with BM25 ranking.

## Alternatives Considered
- PostgreSQL: Requires running server, not portable
- Elasticsearch: Heavyweight, JVM dependency
- Whoosh: Python-specific, less performant

## Consequences
- Zero external dependencies (built into Python)
- Local-only operation, no network latency
- Limited to single-writer concurrency (WAL mode mitigates)
- BM25 ranking is good but not semantic
