# ADR-008: WAL Mode and Performance PRAGMAs

## Status
Accepted

## Context
Need concurrent reads/writes for MCP server.

## Decision
WAL mode with performance-tuned PRAGMAs.

## PRAGMAs
- journal_mode=WAL: concurrent reads during writes
- busy_timeout=5000: wait 5s on lock before failing
- synchronous=NORMAL: good durability, better performance
- cache_size=-64000: 64MB cache
- temp_store=MEMORY: temp tables in RAM
- mmap_size=268435456: 256MB memory-mapped I/O

## Consequences
- Good read performance for concurrent requests
- Acceptable write durability (NORMAL vs FULL)
- Busy timeout prevents immediate failures under load
