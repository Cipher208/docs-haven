"""DocsHaven — local knowledge base for AI agents with SQLite FTS5 search."""

import logging
import threading
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import AliasChoices, Field

from conflicts import ConflictDetector
from import_guard import check_imports
from llm import build_answer_prompt, extract_keywords, host_complete
from result import Err
from storage import Storage
from sync import Syncer, get_username
from uri import URIRouter
from validation import validate_question

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docs-haven")

_ERR_INVALID_PATH = "Invalid file path"

mcp = FastMCP("docs-haven")


def _unwrap(result: Any) -> dict:
    """Unwrap a Result at the MCP boundary. Returns value or error dict."""
    if not hasattr(result, "is_err"):
        return result if isinstance(result, dict) else {"value": result}
    if result.is_err:  # type: ignore[union-attr]
        return {"error": result.error, "code": getattr(result, "code", None)}  # type: ignore[union-attr]
    return result.value  # type: ignore[union-attr]


def _is_unsafe_path(path: str) -> bool:
    """Check if a path contains traversal or injection attempts."""
    return ".." in path or path.startswith("/") or "\x00" in path or "\\" in path or "%2e" in path.lower() or "%2f" in path.lower()


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
    query: Annotated[str, Field(validation_alias=AliasChoices("query", "q", "text", "search_query"))],
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
    if collections and len(collections) > 100:
        collections = collections[:100]
    result = storage.search(query, collections, limit, explain=explain, min_score=min_score)
    return _unwrap(result)


@mcp.tool()
async def kb_ask(
    question: Annotated[str, Field(validation_alias=AliasChoices("question", "query", "q", "userQuery", "prompt"))],
    collections: list[str] | None = None,
    context: list[str] | None = None,
    limit: int = 5,
    *,
    explain: bool = False,
    ctx: Context | None = None,
) -> dict:
    """Ask a question in natural language and get an answer with citations.

    Extracts keywords from the question, runs an expanded full-text search
    (synonyms, stems, patterns), ranks the top chunks, and asks the MCP host LLM
    to summarize them with inline [n] citations. If the host cannot provide a
    completion, returns the assembled chunks and the prompt with
    answer_mode="host_unavailable" instead of failing.

    Args:
        question: Natural-language question (e.g. "how to use Depends with parameters in FastAPI?")
        collections: Filter to specific collections (optional)
        context: Collections to prioritize (+0.1 boost); recently viewed collections are boosted too (optional)
        limit: Max source chunks (default: 5)
        explain: Include scoring breakdown per chunk (default: false)
    """
    validation_error = validate_question(question)
    if validation_error:
        return _unwrap(Err(error=validation_error))
    keywords = extract_keywords(question)
    if not keywords:
        return _unwrap(Err(error="Question has no searchable keywords"))

    storage = _get_storage()
    if collections and len(collections) > 100:
        collections = collections[:100]
    result = storage.search(
        " ".join(keywords),
        collections=collections,
        limit=limit,
        explain=explain,
        context=context if context is not None else [],
        expand=True,
    )
    if result.is_err:
        return _unwrap(result)

    chunks = []
    for r in result.value:
        chunk = {
            "collection": r["collection"],
            "file_path": r["file_path"],
            "chunk_index": r["chunk"],
            "title": r["title"],
            "content": r["content"],
            "score": r["score"],
        }
        if explain:
            chunk["explain"] = r.get("explain")
        chunks.append(chunk)
    citations = [{"collection": c["collection"], "file_path": c["file_path"], "chunk_index": c["chunk_index"]} for c in chunks]

    if not chunks:
        return {"answer": "", "answer_mode": "no_results", "chunks": [], "citations": []}

    prompt = build_answer_prompt(question, chunks)
    answer = await host_complete(ctx, prompt)
    if answer is None:
        return {"answer": "", "answer_mode": "host_unavailable", "chunks": chunks, "citations": citations, "prompt": prompt}
    return {"answer": answer, "answer_mode": "llm", "chunks": chunks, "citations": citations}


@mcp.tool()
async def kb_related(
    collection: str,
    file_path: str,
    chunk_index: int | None = None,
    limit: int = 5,
    *,
    explain: bool = False,
) -> dict:
    """Find documents related to a stored document.

    Scores candidates by title-term overlap (the "tag" signal — the schema has
    no tag column), same-collection proximity, and content similarity (lexical
    relevance over the source document's content): score = 0.4*tag +
    0.2*same_collection + 0.4*content. Multi-chunk candidates appear once.
    Candidates with no tag or content overlap are dropped.

    Args:
        collection: Collection of the source document
        file_path: File path of the source document
        chunk_index: Compare from this chunk only (optional; whole document otherwise)
        limit: Max related documents (default: 5)
        explain: Include the scoring breakdown per document (default: false)
    """
    if _is_unsafe_path(file_path):
        return {"error": _ERR_INVALID_PATH}
    return _unwrap(_get_storage().find_related(collection, file_path, chunk_index=chunk_index, limit=limit, explain=explain))


@mcp.tool()
async def kb_recent(collection: str | None = None, since: str | None = None, limit: int = 10) -> dict:
    """List recently accessed documents, most recent first.

    Access recency comes from a session access log (searches, gets, related
    lookups); documents not seen this session are filled from retrieval_count
    plus updated_at ordering. Querying recent activity does not record an
    access — searches and document fetches do.

    Args:
        collection: Filter to one collection (optional)
        since: ISO date/datetime lower bound on access/update time (optional)
        limit: Max documents (default: 10)
    """
    result = _get_storage().get_recent(collection=collection, since=since, limit=limit)
    if result.is_err:
        return _unwrap(result)
    return {"documents": result.unwrap()}


@mcp.tool()
async def kb_learn(topic: str, collection: str | None = None, limit: int = 10) -> dict:
    """Explore a topic: related collections, key documents and a beginner ->
    advanced learning path.

    Pure retrieval with deterministic complexity grouping (title markers, else
    code-chunk ratio + document length); no LLM calls. Use kb_ask to summarize
    the returned documents afterwards. A topic query is not a viewing:
    kb_learn records no accesses — only searches, document fetches and related
    lookups log accesses.

    Args:
        topic: Topic to explore (e.g. "dependency injection in FastAPI")
        collection: Restrict to one collection (optional)
        limit: Max key documents / learning steps (default: 10)
    """
    return _unwrap(_get_storage().learn_topic(topic, collection=collection, limit=limit))


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
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, storage.add_repo, url, tags, description, mask)
    return _unwrap(result)


@mcp.tool()
async def kb_get(file_path: Annotated[str, Field(validation_alias=AliasChoices("file_path", "filePath", "documentId", "doc_path"))]) -> dict:
    """Get full content of a document.

    Args:
        file_path: Document path, either 'file_path' or 'collection/file_path' (e.g., 'repo/README.md' or 'core/repo/README.md').
    """
    # Path traversal protection
    if _is_unsafe_path(file_path):
        return {"error": _ERR_INVALID_PATH}

    # Accept collection/file_path form (consistent with CLI get/delete).
    parts = file_path.split("/", 1)
    if len(parts) == 2 and parts[0]:
        collection, path = parts
        result = _get_storage().get(path, collection=collection)
    else:
        result = _get_storage().get(file_path)
    return _unwrap(result)


@mcp.tool()
async def kb_update(file_path: str, content: str, title: str | None = None, collection: str | None = None) -> dict:
    """Update an existing document's content.

    Metadata not passed (title, context, created_at, retrieval_count) is preserved.
    Updates are scoped: when collection is given only that document is updated;
    when omitted the path must exist in exactly one collection, otherwise an
    error is returned. An update never creates a document and never touches a
    same-named file in another collection.

    Args:
        file_path: Document path to update
        content: New content for the document
        title: Optional new title (otherwise the existing title is preserved)
        collection: Optional collection scope (required when the path is ambiguous)
    """
    if _is_unsafe_path(file_path):
        return {"error": _ERR_INVALID_PATH}
    if len(content) > 10_000_000:  # 10MB limit
        return {"error": "Content too large (max 10MB)"}
    storage = _get_storage()
    result = storage.update_document(file_path, content, title, collection=collection)
    return _unwrap(result)


@mcp.tool()
async def kb_delete(file_path: str, collection: str | None = None) -> dict:
    """Delete a document from the knowledge base.

    Deletion is scoped and never mass-deletes: with collection only that
    document is removed. Without it, the delete proceeds only when the path
    exists in exactly one collection (unambiguous delete); a path shared by
    several collections returns an error and deletes nothing.

    Args:
        file_path: Document path to delete
        collection: Optional collection scope (prevents cross-collection deletes)
    """
    if _is_unsafe_path(file_path):
        return {"error": _ERR_INVALID_PATH}
    storage = _get_storage()
    if collection:
        return _unwrap(storage.delete_documents_scoped(file_path, collection))
    result = storage.delete_document(file_path)
    return _unwrap(result)


@mcp.tool()
async def kb_collection_rename(old_name: str, new_name: str) -> dict:
    """Rename a collection across all documents.

    Args:
        old_name: Current collection name
        new_name: New collection name
    """
    return _unwrap(_get_storage().rename_collection(old_name, new_name))


@mcp.tool()
async def kb_list_collections() -> list[dict] | dict:
    """List all knowledge base collections with document counts."""
    result = _get_storage().list_collections()
    return _unwrap(result)


@mcp.tool()
async def kb_check_imports(file_path: str, repo_root: str = ".") -> dict:
    """Validate that imports in a file reference real modules/packages.

    Args:
        file_path: Path to the file to check (e.g., 'src/app.py')
        repo_root: Repository root directory (default: current dir)
    """
    if _is_unsafe_path(file_path):
        return {"error": _ERR_INVALID_PATH}
    if _is_unsafe_path(repo_root):
        return {"error": "Invalid repo root path"}
    return check_imports(file_path, repo_root, _get_storage())


@mcp.tool()
async def kb_stats() -> dict:
    """Get knowledge base statistics."""
    result = _get_storage().stats()
    return _unwrap(result)


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
    if result.is_err:  # type: ignore[union-attr]
        return {"error": result.error}  # type: ignore[union-attr]
    collections_data = {c["name"]: [c] for c in result.value if c.get("name")}  # type: ignore[union-attr]

    return syncer.export(collections_data, created_by or get_username())


@mcp.tool()
async def kb_sync_import() -> dict:
    """Import compressed chunks from sync directory."""
    return _get_syncer().import_chunks(_get_storage())


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
    if len(content) > 1_000_000:  # 1MB — conflict check only needs preview
        content = content[:1_000_000]
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
    return _unwrap(_get_detector().judge(new_id, candidate_id, judgment))


@mcp.tool()
async def kb_conflict_details(new_id: str) -> dict:
    """Get details about a conflict for resolution.

    Args:
        new_id: ID of the new document
    """
    return _get_detector().get_conflict_details(new_id)


@mcp.tool()
async def kb_conflict_suggest(new_id: str) -> dict:
    """Suggest a resolution strategy for a conflict.

    Args:
        new_id: ID of the new document
    """
    return _get_detector().suggest_resolution(new_id)


@mcp.tool()
async def kb_template_list() -> list[dict]:
    """List all available collection templates."""
    from templates import list_templates

    return list_templates()


@mcp.tool()
async def kb_template_apply(
    template_name: str,
    repo_url: str | None = None,
) -> dict:
    """Apply a collection template.

    Args:
        template_name: Template name (e.g., 'python-docs', 'api-docs', 'wiki')
        repo_url: Optional repository URL to clone and index
    """
    from templates import apply_template

    return apply_template(_get_storage(), template_name, repo_url)


# ── Context Attachments ────────────────────────────────────────────────────


@mcp.tool()
async def kb_context_add(
    collection: str,
    path: str,
    summary: str,
) -> dict:
    """Add a context attachment (human-written summary) to a collection.

    Args:
        collection: Collection name
        path: Context path (e.g., 'overview', 'quickstart')
        summary: Human-written summary text
    """
    return _unwrap(_get_storage().add_context(collection, path, summary))


@mcp.tool()
async def kb_context_list(
    collection: str | None = None,
) -> list[dict] | dict:
    """List context attachments.

    Args:
        collection: Filter by collection (optional, lists all if omitted)
    """
    storage = _get_storage()
    if collection:
        return _unwrap(storage.get_context(collection))
    return _unwrap(storage.list_contexts())


@mcp.tool()
async def kb_context_rm(
    collection: str,
    path: str | None = None,
) -> dict:
    """Remove context attachment(s).

    Args:
        collection: Collection name
        path: Specific path to remove (optional, removes all in collection if omitted)
    """
    return _unwrap(_get_storage().remove_context(collection, path))


def _run_server() -> None:
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


if __name__ == "__main__":
    _run_server()
