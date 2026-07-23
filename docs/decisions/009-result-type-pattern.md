# ADR-009: Result Type Pattern (Ok/Err)

## Status
Accepted

## Context

The codebase needs a consistent error-handling pattern. Exceptions are not suitable for expected errors (validation, not-found) that callers should handle. Raw error dicts lose type safety.

## Decision

Use a Result type (Ok[T] | Err) based on Pydantic BaseModel. All public Storage methods return Result types. The MCP server boundary uses `_unwrap()` to convert to dicts.

## Consequences

+ Consistent error handling across all modules
+ Type-safe: mypy can verify error paths
+ Pydantic integration: serialization for MCP
- Every caller must check `is_ok`/`is_err`
- `# type: ignore[union-attr]` needed for property access (until union narrowing improves)

## Alternatives Considered

1. **Exceptions** — rejected: expected errors (not-found, validation) shouldn't be exceptional
2. **Error dicts** — rejected: no type safety, easy to forget checking
3. **Status codes** — rejected: lose error message context
