# ADR-005: Auto Strategy Selection

## Status
Accepted

## Context
Short queries work fine with FTS5; longer queries benefit from LIKE fallback.

## Decision
Use FTS for ≤2 words, hybrid for longer queries.

## Consequences
- Short queries: fast, precise FTS5 search
- Long queries: LIKE fallback catches partial matches
- Hybrid merges results, deduplicates by path
