"""Input validation for DocsHaven."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

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
