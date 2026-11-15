"""Input validation for DocsHaven."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

_MAX_QUERY_LENGTH = 10000
_MAX_CONTENT_LENGTH = 10_000_000  # the repo's content-size precedent: kb_update caps content at 10MB (server.py)
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


def validate_text_content(content: str) -> str | None:
    """Validate raw text content for note indexing (Storage.add_text).

    Size cap reuses the repo's content-size precedent: kb_update caps its
    content argument at 10MB (server.py) — the same 10_000_000 chars applies.
    """
    if not content:
        return "Text content cannot be empty"
    if len(content) > _MAX_CONTENT_LENGTH:
        return f"Text content too long (max {_MAX_CONTENT_LENGTH} chars)"
    return None


def text_slug(title: str) -> str:
    """Slug for text:// note file paths: lowercase, non-alnum -> '-', trimmed.

    An empty result falls back to "note" so file paths stay non-empty.
    """
    slug = "".join(c if c.isalnum() else "-" for c in title.lower())
    return slug.strip("-") or "note"


def validate_http_url(url: str) -> str | None:
    """Validate a bookmark URL — web links are http(s) only.

    Based on validate_url's checks (scheme allowlist, 2048-char cap, dangerous
    characters) minus the git-specific parts: no git@ scheme and no git-host
    domain allowlist — bookmarks point at arbitrary web pages, not clone
    targets. Adds a host requirement: a bare scheme is not a link. The input
    is stripped and the scheme is matched case-insensitively (RFC 3986:
    schemes are case-insensitive), so " https://ok.com " and
    "HTTPS://EXAMPLE.COM" validate while dangerous schemes are rejected even
    when spelled in a different case (JavaScript://...).
    """
    url = url.strip()
    if not url:
        return "URL cannot be empty"
    if not url.lower().startswith(("http://", "https://")):
        return f"Invalid URL scheme (http/https only): {url}"
    if len(url) > 2048:
        return "URL too long (max 2048 chars)"
    # validate_url's dangerous set minus shell-quoting metacharacters: bookmarks
    # are stored, not executed — and '&' is a legitimate query-string separator.
    dangerous_chars = set("\n\r\t<>")
    if any(c in url for c in dangerous_chars):
        return "URL contains dangerous characters"
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname
    except ValueError:
        return f"Invalid URL: {url}"
    if not host:
        return "URL must include a host"
    return None


def normalize_http_url(url: str) -> str:
    """Fold a bookmark URL to its canonical case form (H-5, RFC 3986).

    Scheme and host are case-insensitive and fold to lowercase; userinfo,
    path, query and fragment keep their case. The UNIQUE(url) bookmark key
    must not depend on case — https://dup.com/a and HTTPS://DUP.COM/a are one
    link. Strips surrounding whitespace (the F-6 normalization step). Port
    digits and IPv6 literals pass through untouched.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    userinfo, at, hostport = parts.netloc.rpartition("@")
    host, colon, port = hostport.rpartition(":")
    if colon and port.isdigit():
        hostport = host.lower() + colon + port
    else:
        hostport = hostport.lower()
    netloc = userinfo + at + hostport if at else hostport
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment))


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


def validate_question(question: str) -> str | None:
    if not question or not question.strip():
        return "Question cannot be empty"
    if len(question) > _MAX_QUERY_LENGTH:
        return f"Question too long (max {_MAX_QUERY_LENGTH} chars)"
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


def validate_local_dir(path: str) -> str | None:
    """Validate a local directory for indexing. Returns error or None.

    The path must exist, be a directory, and be readable.
    """
    if not path or not path.strip():
        return "Local path cannot be empty"
    if "\x00" in path:
        return "Local path contains NUL byte"
    p = Path(path)
    if not p.exists():
        return f"Local path does not exist: {path}"
    if not p.is_dir():
        return f"Local path is not a directory: {path}"
    if not os.access(p, os.R_OK):
        return f"Local path is not readable: {path}"
    return None


def validate_topic(topic: str) -> str | None:
    if not topic or not topic.strip():
        return "Topic cannot be empty"
    if len(topic) > _MAX_QUERY_LENGTH:
        return f"Topic too long (max {_MAX_QUERY_LENGTH} chars)"
    return None


def validate_limit(limit: int) -> str | None:
    if not isinstance(limit, int) or isinstance(limit, bool):
        return "Limit must be an integer"
    if limit < 1:
        return "Limit must be >= 1"
    return None


def parse_since(since: str) -> datetime | None:
    """Parse an ISO date or datetime as naive UTC; None when unparseable.

    A trailing 'Z'/'z' is stripped before parsing so Python 3.10's
    fromisoformat (which rejects 'Z') accepts ISO datetimes with UTC designator.
    """
    text = since[:-1] if isinstance(since, str) and since.endswith(("Z", "z")) else since
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def validate_since(since: str) -> str | None:
    if parse_since(since) is None:
        return "Invalid since timestamp (expected ISO date or datetime)"
    return None
