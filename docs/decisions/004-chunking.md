# ADR-004: Document Chunking Strategy

## Status
Accepted

## Context
Long documents reduce search precision.

## Decision
Split at sentence/paragraph boundaries with 20% overlap.

## Parameters
- CHUNK_SIZE = 1000 chars
- CHUNK_OVERLAP = 200 chars (20%)

## Consequences
- Better search precision for long documents
- Overlap prevents losing context at chunk boundaries
- Fixed size is simple and predictable
