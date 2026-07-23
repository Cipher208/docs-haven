"""LLM Argument Aliasing — intercept and rewrite hallucinated parameter names.

LLMs often invent parameter names that look plausible but fail validation.
This module maps common aliases to the correct parameter names.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Common LLM aliases → correct parameter names
# Format: alias → correct_name
ALIASES: dict[str, str] = {
    # kb_search
    "userQuery": "query",
    "searchQuery": "query",
    "search_query": "query",
    "q": "query",
    "text": "query",
    "filter": "collections",
    "collection_filter": "collections",
    "maxResults": "limit",
    "max_results": "limit",
    "top_k": "limit",
    "topK": "limit",
    "verbose": "explain",
    "debug": "explain",
    "showExplanation": "explain",
    "show_explanation": "explain",
    # kb_add_repo
    "repoUrl": "url",
    "repo_url": "url",
    "repository": "url",
    "repo": "url",
    "tags": "tags",
    "label": "description",
    "desc": "description",
    "fileMask": "mask",
    "file_mask": "mask",
    "pattern": "mask",
    "glob": "mask",
    # kb_get
    "documentId": "file_path",
    "document_id": "file_path",
    "docId": "file_path",
    "doc_id": "file_path",
    "path": "file_path",
    "filePath": "file_path",
    # kb_update
    "newContent": "content",
    "new_content": "content",
    "body": "content",
    "docTitle": "title",
    "doc_title": "title",
    # kb_delete
    "documentPath": "file_path",
    "document_path": "file_path",
    # kb_context_add
    "note": "summary",
    "contextPath": "path",
    "context_path": "path",
    # kb_collection_rename
    "from": "old_name",
    "to": "new_name",
    "newName": "new_name",
}


def alias_args(tool_name: str, args: dict) -> dict:
    """Rewrite hallucinated parameter names to correct names.

    Args:
        tool_name: Name of the MCP tool being called
        args: Original arguments from LLM

    Returns:
        Rewritten arguments with correct parameter names
    """
    rewritten = {}
    for key, value in args.items():
        if key in ALIASES:
            correct_name = ALIASES[key]
            if correct_name != key:
                logger.debug("Aliasing %s.%s → %s", tool_name, key, correct_name)
            rewritten[correct_name] = value
        else:
            rewritten[key] = value
    return rewritten
