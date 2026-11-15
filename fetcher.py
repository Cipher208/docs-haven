"""Web page fetching and text extraction for URL indexing (card P2-3).

Stdlib only by design (the card's zero-dependency rule overrides the plan
text's "requests + BeautifulSoup or similar"): urllib.request for HTTP with
manual redirect handling, html.parser for extraction. Deliberately NOT a
readability engine — it strips the known chrome subtrees and keeps the
visible text (YAGNI).

SECURITY — residual SSRF surface, documented honestly: git-clone URLs are
gated by validation._ALLOWED_GIT_DOMAINS (the 59-issue review's SSRF fix,
commit 0762f2f — a domain allowlist preventing clones from internal hosts),
and that gate still holds because repo-shaped URLs route to add_repo. A FETCH
URL is only validated as http(s) (validation.validate_http_url): kb_index(url)
fetches whatever the host machine can reach, including loopback and internal
addresses. DocsHaven is a local-first tool run by its owner and arbitrary web
pages are the feature — a host allowlist would defeat it. Mitigations that
DO apply: every redirect hop is re-validated as http(s) (no downgrade to
file:// or other schemes), the response is size-capped, and only HTML is
accepted. robots.txt is deliberately NOT consulted (card non-goal).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

from result import Err, Ok
from validation import _MAX_CONTENT_LENGTH, normalize_http_url, validate_http_url

_MAX_REDIRECTS = 3
_TIMEOUT = 10  # fetch timeout in seconds — generous on purpose: a docs page can be slow to assemble and this is a background indexing task, not an interactive request
_USER_AGENT = "DocsHaven"  # some doc sites 403 requests without a User-Agent
_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_REDIRECT_CODES = {301, 302, 303, 307, 308}

_WS = re.compile(r"\s+")

# Chrome subtrees stripped entirely, text and all (card spec).
_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside"}
# Line breaks at block boundaries: the card's p/h1-h6/li/pre/br/tr plus the
# containers whose text would otherwise glue together ("foo" + "bar") because
# whitespace is collapsed per text run. Not a readability engine — just
# correct word separation.
_BREAK_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "pre",
    "br",
    "tr",
    "div",
    "section",
    "article",
    "main",
    "head",
    "body",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "blockquote",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "figure",
    "figcaption",
    "hr",
}
# Table cells separate with a space, rows with a line break (tr in _BREAK_TAGS).
_SPACE_TAGS = {"td", "th"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Disable urllib's built-in redirect following — fetch_url re-validates
    every hop's scheme itself (SECURITY note in the module docstring)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201 - urllib API
        return None


class _PageTextParser(HTMLParser):
    """Extract title + visible text from an HTML page (markdown-ish plain text).

    Title: <title> if present and non-empty, else the first <h1>. Text:
    visible content of everything outside the stripped chrome subtrees, with
    block-level newlines and collapsed whitespace runs; <pre> is preserved
    as-is.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._pre = 0
        self._in_title = 0
        self._h1_open = 0
        self._h1_done = False
        self._cur = ""
        self._lines: list[str] = []
        self._pre_parts: list[str] = []
        self._title_parts: list[str] = []
        self._h1_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title += 1
            return
        if tag == "pre":
            self._flush()
            self._pre += 1
            return
        if tag == "h1" and not self._h1_done and not self._h1_open:
            self._h1_open += 1
        if tag in _BREAK_TAGS:
            self._flush()
        elif tag in _SPACE_TAGS:
            self._cur += " "

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = max(0, self._in_title - 1)
            return
        if tag == "pre":
            self._flush_pre()
            self._pre = max(0, self._pre - 1)
            return
        if tag == "h1" and self._h1_open:
            self._h1_open -= 1
            self._h1_done = True
        if tag in _BREAK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._pre:
            self._pre_parts.append(data)
            return
        if self._h1_open:
            self._h1_parts.append(data)  # h1 is visible text too
        self._cur += data

    def _flush(self) -> None:
        line = _WS.sub(" ", self._cur).strip()
        if line:
            self._lines.append(line)
        self._cur = ""

    def _flush_pre(self) -> None:
        raw = "".join(self._pre_parts)
        self._pre_parts = []
        if raw.strip():
            self._lines.append(raw.strip("\n"))  # interior preserved as-is

    def result(self) -> tuple[str, str]:
        self._flush()
        self._flush_pre()  # unclosed <pre>: still emit what we have
        title = _WS.sub(" ", "".join(self._title_parts)).strip()
        if not title:
            title = _WS.sub(" ", "".join(self._h1_parts)).strip()
        return title, "\n".join(self._lines)


def _extract(html: str) -> tuple[str, str]:
    parser = _PageTextParser()
    parser.feed(html)
    parser.close()
    return parser.result()


def fetch_url(url: str) -> Ok[dict] | Err:
    """Fetch a web page and extract its title and visible text.

    Returns Ok({"url": final_url, "title": ..., "text": ...}) where final_url
    is the post-redirect, normalized URL (the document's identity). Errors:
    invalid/non-http URL, HTTP failures (incl. 404), redirects to a non-http
    scheme or past the max-3-hops budget, non-HTML content types ("not an
    HTML page"), and bodies over validation._MAX_CONTENT_LENGTH ("too large" —
    the repo's 10MB content-size precedent, not a new number).
    """
    error = validate_http_url(url)
    if error:
        return Err(error=error)
    current = normalize_http_url(url)
    opener = urllib.request.build_opener(_NoRedirect)
    for _hop in range(_MAX_REDIRECTS + 1):
        request = urllib.request.Request(current, headers={"User-Agent": _USER_AGENT})
        try:
            response = opener.open(request, timeout=_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in _REDIRECT_CODES:
                location = e.headers.get("Location") if e.headers else None
                if not location:
                    return Err(error=f"Redirect without Location from {current}")
                from urllib.parse import urljoin

                target = urljoin(current, location)
                hop_error = validate_http_url(target)  # EVERY hop re-validated: no scheme downgrade (file:// etc.)
                if hop_error:
                    return Err(error=f"Unsafe redirect target: {hop_error}")
                current = normalize_http_url(target)
                continue
            return Err(error=f"HTTP {e.code} fetching {current}")
        except (urllib.error.URLError, OSError) as e:
            return Err(error=f"Fetch failed for {current}: {e}")
        with response:
            content_type = response.headers.get("Content-Type", "")
            main_type = content_type.split(";")[0].strip().lower()
            if main_type not in _CONTENT_TYPES:
                return Err(error=f"not an HTML page (Content-Type: {content_type or 'missing'})")
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read(_MAX_CONTENT_LENGTH + 1)
        if len(body) > _MAX_CONTENT_LENGTH:
            return Err(error=f"too large (max {_MAX_CONTENT_LENGTH} bytes)")
        try:
            decoded = body.decode(charset, errors="replace")
        except LookupError:
            # Unknown encoding name from Content-Type must honor the Ok/Err
            # contract instead of raising through the tool layer.
            return Err(error=f"Unknown charset in Content-Type: {charset}")
        title, text = _extract(decoded)
        return Ok(value={"url": current, "title": title, "text": text})
    return Err(error=f"Too many redirects (max {_MAX_REDIRECTS})")
