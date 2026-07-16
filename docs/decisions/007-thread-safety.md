# ADR-007: Thread-Safe Storage

## Status
Accepted

## Context
MCP server handles concurrent requests.

## Decision
Single persistent connection with threading.Lock.

## Consequences
- Simple connection management
- WAL mode allows concurrent reads
- Single writer at a time (acceptable for local use)
- check_same_thread=False for cross-thread access
