"""URI Routing for DocsHaven — organize knowledge by domain://path."""

import re
from dataclasses import dataclass

# Valid domains for the knowledge base
VALID_DOMAINS = {"core", "ref", "guide", "lib", "src", "test", "note"}


@dataclass
class URI:
    """Parsed knowledge URI: domain://path/to/doc"""

    domain: str
    path: str
    raw: str

    @classmethod
    def parse(cls, uri: str) -> "URI":
        """Parse 'core://fastapi/deps' into URI(domain='core', path='fastapi/deps')"""
        m = re.match(r"^([a-zA-Z0-9_-]+)://(.+)$", uri)
        if not m:
            raise ValueError(f"Invalid URI format: {uri} (expected domain://path)")
        domain = m.group(1).lower()
        # Collapse multiple slashes and strip leading/trailing slashes
        path = re.sub(r"/+", "/", m.group(2)).strip("/")
        if not path:
            raise ValueError(f"Empty path in URI: {uri} (expected domain://path)")
        if domain not in VALID_DOMAINS:
            raise ValueError(f"Unknown domain '{domain}'. Valid: {sorted(VALID_DOMAINS)}")
        return cls(domain=domain, path=path, raw=uri)

    @classmethod
    def create(cls, domain: str, path: str) -> "URI":
        """Create URI from domain and path components."""
        domain = domain.lower().strip()
        path = path.strip("/")
        if domain not in VALID_DOMAINS:
            raise ValueError(f"Unknown domain '{domain}'. Valid: {sorted(VALID_DOMAINS)}")
        return cls(domain=domain, path=path, raw=f"{domain}://{path}")

    def to_collection(self) -> str:
        """Map URI to collection name: 'core://fastapi/deps' -> 'core__fastapi'"""
        parts = self.path.split("/")
        if len(parts) >= 1 and parts[0]:
            return f"{self.domain}__{parts[0]}"
        return f"{self.domain}__root"

    def to_file_pattern(self) -> str:
        """Map URI to file glob pattern for search."""
        return f"**/{self.path}*"

    def __str__(self) -> str:
        return self.raw

    def __eq__(self, other):
        return isinstance(other, URI) and self.domain == other.domain and self.path == other.path

    def __hash__(self):
        return hash((self.domain, self.path))


class URIRouter:
    """Maps URIs to collections and provides structured access."""

    DOMAIN_DOCS = {
        "core": "Core documentation - primary source of truth",
        "ref": "Reference materials - API docs, specs",
        "guide": "Guides and tutorials",
        "lib": "Library documentation",
        "src": "Source code annotations",
        "test": "Test documentation",
        "note": "Personal notes and annotations",
    }

    def __init__(self, storage):
        self.storage = storage

    def resolve(self, uri_str: str) -> dict:
        """Resolve a URI to its collection and metadata."""
        uri = URI.parse(uri_str)
        collection = uri.to_collection()
        return {
            "uri": str(uri),
            "domain": uri.domain,
            "path": uri.path,
            "collection": collection,
            "domain_doc": self.DOMAIN_DOCS.get(uri.domain, ""),
        }

    def search_by_uri(self, uri_str: str, limit: int = 10) -> list[dict]:
        """Search within a URI scope. Supports wildcards: core://fastapi/*"""
        uri = URI.parse(uri_str)
        collection = uri.to_collection()

        # Check for wildcard pattern (core://fastapi/* or core://*)
        if uri.path.endswith("/*") or uri.path == "*":
            # Wildcard: search all collections in domain
            prefix = f"{uri.domain}__"
            result = self.storage.list_collections()
            if result.is_err:
                return []
            collections = [c["name"] for c in result.value if c.get("name", "").startswith(prefix)]
            if not collections:
                return []
            # Extract search term from path (e.g., core://fastapi/* → "fastapi")
            search_term = uri.path.rstrip("/*").split("/")[-1] if "/" in uri.path else ""
            if not search_term:
                # No search term — return all docs in domain via broad query
                search_term = " OR ".join(collections)
            result = self.storage.search(
                query=search_term,
                collections=collections,
                limit=limit,
            )
            return result.value if result.is_ok else []

        result = self.storage.search(
            query=uri.path.split("/")[-1],
            collections=[collection],
            limit=limit,
        )
        return result.value if result.is_ok else []

    def list_by_domain(self, domain: str) -> list[dict]:
        """List all URIs in a domain."""
        if domain not in VALID_DOMAINS:
            return [{"error": f"Unknown domain: {domain}"}]
        result = self.storage.list_collections()
        if result.is_err:
            return []
        collections = result.value
        prefix = f"{domain}__"
        return [
            {"collection": c["name"], "uri": f"{domain}://{c['name'].replace(prefix, '')}"}
            for c in collections
            if c.get("name", "").startswith(prefix)
        ]

    def list_all_domains(self) -> dict:
        """List all domains with their collection counts."""
        result = self.storage.list_collections()
        if result.is_err:
            return {}
        collections = result.value
        domains: dict[str, int] = {}
        for c in collections:
            name = c.get("name", "")
            if "__" in name:
                domain = name.split("__")[0]
                if domain in VALID_DOMAINS:
                    domains[domain] = domains.get(domain, 0) + 1
        return {d: {"count": domains.get(d, 0), "doc": self.DOMAIN_DOCS.get(d, "")} for d in VALID_DOMAINS}
