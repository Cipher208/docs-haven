# ADR-002: URI Routing System with 7 Domains

## Status
Accepted

## Context
Organize knowledge by domain for AI agents.

## Decision
Custom URI system: `domain://path/to/doc` maps to `domain__collection`.

## Domains
- `core`: Primary source of truth
- `ref`: Reference materials
- `guide`: Guides and tutorials
- `lib`: Library documentation
- `src`: Source code annotations
- `test`: Test documentation
- `note`: Personal notes

## Consequences
- Intuitive organization for AI agents
- URI-to-collection mapping is deterministic
- Easy to extend with new domains
