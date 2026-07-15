# Changelog

All notable changes to DocsHaven will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-07-16

### Added
- SQLite FTS5 search engine (replaces QMD dependency)
- BM25 full-text search with LIKE fallback
- Auto strategy selection (fts/hybrid by query length)
- Document chunking for better search precision
- Type-aware result boosting
- URI routing (core://, ref://, guide://, lib://, src://, test://, note://)
- Git sync with compressed chunks (no merge conflicts)
- Conflict detection when adding documents
- MCP server with 15 tools
- MIT license
- pytest test suite

### Changed
- Storage backend: QMD → SQLite FTS5 (zero external dependencies)
- Search: FTS5 + LIKE fallback (pattern from mcp-ariel-memory)
- Conflicts: now uses Storage.search() instead of QMD CLI
