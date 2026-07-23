"""Collection Templates — pre-built templates for common documentation types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CollectionTemplate:
    """Template for a knowledge base collection."""

    name: str
    description: str
    file_mask: str = "**/*.md"
    domain: str = "note"
    tags: list[str] = field(default_factory=list)
    context_notes: list[dict[str, str]] = field(default_factory=list)


# Pre-built templates
TEMPLATES: dict[str, CollectionTemplate] = {
    "python-docs": CollectionTemplate(
        name="python-docs",
        description="Python project documentation (README, API docs, guides)",
        file_mask="**/*.md",
        domain="guide",
        tags=["python", "docs"],
        context_notes=[
            {"path": "overview", "summary": "Python project documentation"},
        ],
    ),
    "api-docs": CollectionTemplate(
        name="api-docs",
        description="REST API documentation (OpenAPI, Swagger)",
        file_mask="**/*.md",
        domain="ref",
        tags=["api", "rest", "openapi"],
        context_notes=[
            {"path": "overview", "summary": "REST API reference documentation"},
        ],
    ),
    "wiki": CollectionTemplate(
        name="wiki",
        description="Knowledge base wiki with general articles",
        file_mask="**/*.md",
        domain="note",
        tags=["wiki", "knowledge"],
        context_notes=[
            {"path": "overview", "summary": "General knowledge base articles"},
        ],
    ),
    "tutorial": CollectionTemplate(
        name="tutorial",
        description="Step-by-step tutorials and guides",
        file_mask="**/*.md",
        domain="guide",
        tags=["tutorial", "guide", "howto"],
        context_notes=[
            {"path": "overview", "summary": "Step-by-step tutorials and guides"},
            {"path": "getting-started", "summary": "Quick start guide for beginners"},
        ],
    ),
    "source-code": CollectionTemplate(
        name="source-code",
        description="Source code with inline documentation",
        file_mask="**/*.py",
        domain="src",
        tags=["source", "code"],
        context_notes=[
            {"path": "overview", "summary": "Source code with inline documentation"},
        ],
    ),
    "test-docs": CollectionTemplate(
        name="test-docs",
        description="Test documentation and fixtures",
        file_mask="**/*.md",
        domain="test",
        tags=["test", "testing"],
        context_notes=[
            {"path": "overview", "summary": "Test documentation and fixtures"},
        ],
    ),
}


def list_templates() -> list[dict]:
    """List all available templates."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "domain": t.domain,
            "tags": t.tags,
            "file_mask": t.file_mask,
        }
        for t in TEMPLATES.values()
    ]


def get_template(name: str) -> CollectionTemplate | None:
    """Get a template by name."""
    return TEMPLATES.get(name)


def apply_template(
    storage,
    template_name: str,
    repo_url: str | None = None,
) -> dict:
    """Apply a template to create a configured collection.

    Args:
        storage: Storage instance
        template_name: Name of the template to apply
        repo_url: Optional repository URL to clone and index

    Returns:
        {status, collection, template, files_indexed}
    """
    template = get_template(template_name)
    if template is None:
        return {"error": f"Template not found: {template_name}"}

    # Add repo if URL provided
    files_indexed = 0
    if repo_url:
        result = storage.add_repo(
            url=repo_url,
            description=template.description,
            mask=template.file_mask,
            tags=template.tags,
        )
        if result.is_err:
            return {"error": result.error}
        files_indexed = result.value.get("files_indexed", 0)

    # Add context notes
    collection_name = template.name
    import logging

    logger = logging.getLogger(__name__)
    for note in template.context_notes:
        ctx_result = storage.add_context(collection_name, note["path"], note["summary"])
        if hasattr(ctx_result, "is_err") and ctx_result.is_err:
            logger.warning("Failed to add context %s: %s", note["path"], ctx_result.error)

    return {
        "status": "applied",
        "collection": collection_name,
        "template": template_name,
        "domain": template.domain,
        "tags": template.tags,
        "files_indexed": files_indexed,
    }
