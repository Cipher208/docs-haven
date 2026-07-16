# ADR-003: Git Sync via Compressed Chunks

## Status
Accepted

## Context
Multi-machine sync without merge conflicts.

## Decision
Each sync creates a NEW compressed chunk (never modifies old ones).

## Consequences
- No merge conflicts (append-only)
- Chunks are immutable once created
- Manifest tracks all chunks for import
- Trade-off: storage grows over time (acceptable for knowledge bases)
