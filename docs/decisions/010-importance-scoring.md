# ADR-010: Importance Scoring

## Status
Accepted

## Context

BM25 search returns results by text relevance only. Important documents (recent, frequently accessed) should rank higher regardless of text match.

## Decision

Add multi-signal importance scoring combining:
- **Recency**: exponential decay with 90-day half-life (configurable via `AGE_HALF_LIFE_DAYS`)
- **Frequency**: log-scale retrieval count tracked per document

Weights: RECENCY_WEIGHT=0.1, FREQUENCY_WEIGHT=0.05 (max 0.15 total boost)

## Consequences

+ Better search results for time-sensitive knowledge bases
+ Retrieval tracking enables learning from usage
- Extra DB column (retrieval_count) adds storage overhead
- Search now has side effects (increments retrieval count)

## Alternatives Considered

1. **Pure recency** — rejected: ignores document importance
2. **PageRank-style** — rejected: too complex for local KB
3. **User-defined weights** — deferred: premature optimization
