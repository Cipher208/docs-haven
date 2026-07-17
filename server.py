"""DocsHaven — local knowledge base for AI agents with SQLite FTS5 search."""

import logging
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from conflicts import ConflictDetector
from storage import Storage
from sync import Syncer, get_username
from uri import URIRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docs-haven")

mcp = FastMCP("docs-haven")

# Thread-safe singleton: double-checked locking pattern.
# First check avoids lock contention on hot path.
# Second check inside lock prevents double-creation.
_storage: Storage | None = None
_storage_lock = threading.Lock()
_sync_dir = Path.home() / ".docshaven-sync"


def _get_storage() -> Storage:
    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                _storage = Storage.default()
    return _storage


def _get_syncer() -> Syncer:
    return Syncer(_sync_dir)


def _get_router() -> URIRouter:
    return URIRouter(_get_storage())


def _get_detector() -> ConflictDetector:
    return ConflictDetector(_get_storage())


# ── Core Search & Retrieval ────────────────────────────────────────────────


@mcp.tool()
async def kb_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_score: float = 0.0,
    *,
    explain: bool = False,
) -> list[dict] | dict:
    """Search knowledge base using BM25 full-text search.

    Args:
        query: Search query (keywords, phrase, or natural language)
        collections: Filter to specific collections (optional)
        limit: Max results (default: 10)
        min_score: Minimum relevance score (default: 0)
        explain: Include scoring breakdown in results (default: false)
    """
    storage = _get_storage()
    result = storage.search(query, collections, limit, explain=explain, min_score=min_score)
    if result.is_err():  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


@mcp.tool()
async def kb_add_repo(
    url: str,
    tags: list[str] | None = None,
    description: str | None = None,
    mask: str | None = None,
) -> dict:
    """Add a GitHub repository to the knowledge base.

    Clones the repo (shallow), indexes documents into FTS5.

    Args:
        url: GitHub repo URL
        tags: Optional tags
        description: Optional description
        mask: File pattern (default: **/*.md). Use **/*.rst for Sphinx, **/*.py for Python.
    """
    import asyncio
    storage = _get_storage()
    # Run blocking git clone in executor to avoid blocking event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, storage.add_repo, url, tags, description, mask)
    if result.is_err():  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


@mcp.tool()
async def kb_get(file_path: str) -> dict:
    """Get full content of a document.

    Args:
        file_path: Document path (e.g., 'repo/README.md')
    """
    # Path traversal protection
    if ".." in file_path or file_path.startswith("/"):
        return {"error": "Invalid file path"}
    result = _get_storage().get(file_path)
    if result.is_err():  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


@mcp.tool()
async def kb_update(file_path: str, content: str, title: str | None = None) -> dict:
    """Update an existing document's content.

    Args:
        file_path: Document path to update
        content: New content for the document
        title: Optional new title
    """
    storage = _get_storage()
    result = storage.update_document(file_path, content, title)
    if result.is_err():  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


@mcp.tool()
async def kb_delete(file_path: str, collection: str | None = None) -> dict:
    """Delete a document from the knowledge base.

    Args:
        file_path: Document path to delete
        collection: Optional collection scope (prevents cross-collection deletes)
    """
    storage = _get_storage()
    result = storage.delete_document(file_path)
    if result.is_err():  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


@mcp.tool()
async def kb_list_collections() -> list[dict]:
    """List all knowledge base collections with document counts."""
    result = _get_storage().list_collections()
    if result.is_err():  # type: ignore[union-attr]
        return [{"error": result.error}]  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


@mcp.tool()
async def kb_stats() -> dict:
    """Get knowledge base statistics."""
    result = _get_storage().stats()
    if result.is_err():  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


# ── URI Routing Tools ──────────────────────────────────────────────────────


@mcp.tool()
async def kb_uri_resolve(uri: str) -> dict:
    """Resolve a URI to its collection and metadata.

    URI format: domain://path/to/doc
    Domains: core, ref, guide, lib, src, test, note

    Example: kb_uri_resolve("core://fastapi/dependencies")
    """
    return _get_router().resolve(uri)


@mcp.tool()
async def kb_uri_search(uri: str, limit: int = 10) -> list[dict]:
    """Search within a URI scope.

    Example: kb_uri_search("core://fastapi")
    """
    return _get_router().search_by_uri(uri, limit)


@mcp.tool()
async def kb_uri_list(domain: str) -> list[dict]:
    """List all URIs in a domain.

    Example: kb_uri_list("core")
    """
    return _get_router().list_by_domain(domain)


@mcp.tool()
async def kb_uri_domains() -> dict:
    """List all domains with their collection counts."""
    return _get_router().list_all_domains()


# ── Git Sync Tools ─────────────────────────────────────────────────────────


@mcp.tool()
async def kb_sync_export(created_by: str | None = None) -> dict:
    """Export knowledge base as compressed chunk for sync.

    Creates a compressed JSONL chunk in .docshaven-sync/chunks/.
    Each export is a NEW chunk (no merge conflicts).
    """
    syncer = _get_syncer()
    storage = _get_storage()

    result = storage.list_collections()
    if result.is_err():  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    collections_data = {c["name"]: [c] for c in result.value if c.get("name")}  # type: ignore[union-attr]

    return syncer.export(collections_data, created_by or get_username())


@mcp.tool()
async def kb_sync_import() -> dict:
    """Import compressed chunks from sync directory."""
    return _get_syncer().import_chunks()


@mcp.tool()
async def kb_sync_status() -> dict:
    """Get sync status - chunks, manifest size, pending imports."""
    return _get_syncer().status()


# ── Conflict Surfacing Tools ───────────────────────────────────────────────


@mcp.tool()
async def kb_conflict_check(
    title: str,
    content: str,
    collections: list[str] | None = None,
) -> dict:
    """Check for conflicts before adding a new document.

    Searches for similar documents and returns candidates for review.

    Args:
        title: Document title
        content: Document content
        collections: Optional collection filter
    """
    detector = _get_detector()
    result = detector.detect(title, content, collections)
    return result.to_dict()


@mcp.tool()
async def kb_conflict_judge(
    new_id: str,
    candidate_id: str,
    judgment: str,
) -> dict:
    """Record a judgment on a conflict candidate.

    Args:
        new_id: ID of the new document
        candidate_id: ID of the conflicting document
        judgment: 'supersedes', 'conflicts_with', or 'unrelated'
    """
    return _get_detector().judge(new_id, candidate_id, judgment)


if __name__ == "__main__":
    import atexit
    import sys

    def _shutdown():
        s = _get_storage()
        if s is not None:
            s.close()
            logger.info("Storage connection closed.")

    atexit.register(_shutdown)

    _HTTP_FLAG = "--http"
    if _HTTP_FLAG in sys.argv:
        import uvicorn

        idx = sys.argv.index(_HTTP_FLAG)
        try:
            port = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 8000
        except (ValueError, IndexError):
            port = 8000
        logger.info("Starting HTTP server on port %d...", port)
        uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=port)
    else:
        mcp.run()
