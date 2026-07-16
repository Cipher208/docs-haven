# ADR-006: Type-Aware Boost

## Status
Accepted

## Context
Different query types should boost matching document types.

## Decision
Add 0.15 boost when query keywords match document type keywords.

## Types
- api: endpoint, route, handler, request, response
- tutorial: tutorial, guide, howto, step, example
- reference: reference, docs, documentation, spec
- config: config, configuration, setup, install

## Consequences
- API queries boost API docs
- Tutorial queries boost tutorials
- Boost capped at 1.0 to prevent score inflation
