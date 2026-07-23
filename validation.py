"""Input validation for DocsHaven."""

import re

_MAX_QUERY_LENGTH = 10000
_MAX_FTS5_TOKENS = 100
_MAX_SEARCH_LIMIT = 1000
_VALID_URL_SCHEMES = ("https://", "http://", "git@")
_ALLOWED_GIT_DOMAINS = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
_COLLECTION_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_url(url: str) -> str | None:
    if not url.startswith(_VALID_URL_SCHEMES):
        return f"Invalid URL scheme: {url}"
    if len(url) > 2048:
        return "URL too long (max 2048 chars)"
    dangerous_chars = set("\n\r\t`$&|;<>\\")
    if any(c in url for c in dangerous_chars):
        return "URL contains dangerous characters"
    if "%2e" in url.lower() or "%2f" in url.lower():
        return "URL contains encoded path traversal"
    return _validate_url_domain(url)


def _validate_url_domain(url: str) -> str | None:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if domain and domain not in _ALLOWED_GIT_DOMAINS:
            if url.startswith("git@"):
                git_host = url.split("@")[1].split(":")[0] if "@" in url else ""
                if git_host not in _ALLOWED_GIT_DOMAINS:
                    return f"Domain not allowed: {git_host}"
            else:
                return f"Domain not allowed: {domain}"
    except (ValueError, AttributeError):
        pass
    return None


def validate_collection(name: str) -> str | None:
    if not name:
        return "Collection name cannot be empty"
    if len(name) > 255:
        return "Collection name too long (max 255 chars)"
    if not _COLLECTION_PATTERN.match(name):
        return f"Invalid collection name: {name}"
    return None


def validate_query(query: str) -> str | None:
    if len(query) > _MAX_QUERY_LENGTH:
        return f"Query too long (max {_MAX_QUERY_LENGTH} chars)"
    return None


def validate_file_mask(mask: str) -> str | None:
    if ".." in mask:
        return "File mask must not contain '..' (path traversal)"
    if mask.startswith("/") or mask.startswith("\\\\"):
        return "File mask must not be absolute"
    dangerous = set("|;&$`\x00")
    if any(c in mask for c in dangerous):
        return "File mask contains dangerous characters"
    return None
