"""Anchors for Card P2-3: URL/web page indexing (FUTURE_PLAN item 9).

MUST FAIL at the pre-card HEAD (a8eb393) and PASS after the card lands:

- fetcher.fetch_url and Storage.add_url do not exist (AttributeError /
  ModuleNotFoundError)
- kb_index rejects a plain http(s) URL ("Unsupported source")

Guards that cannot fail before (they pin behavior the card must not break or
change the wrong way): repo-shaped github.com/<owner>/<repo> still routes to
add_repo (P2-1 parity), type="github" still forces the repo route, and the MCP
tool count stays 32 (URL mode is a kb_index mode, not a new tool).

All network in these tests is loopback (http.server in a thread) or mocked —
NO external network.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from result import Ok
from storage import Storage
from validation import _MAX_CONTENT_LENGTH

DOC_URL = "https://docs.python.org/3/library/asyncio.html"


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def kb_storage(tmp_path):
    """Storage on tmp_path (not TemporaryDirectory): kb_index opens SQLite
    connections in executor threads that outlive the test, and Windows cannot
    unlink files that are still open at fixture teardown."""
    storage = Storage(tmp_path / "kb-data")
    yield storage
    storage.close()


@pytest.fixture
def page_server():
    """Loopback HTTP server with mutable per-path routes (no external network).

    routes[path] = {"body": str|bytes, "status": int, "content_type": str,
                    "headers": {name: value}}. A missing path answers 404.
    """
    routes: dict[str, dict] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            entry = routes.get(self.path)
            if entry is None:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"not found")
                return
            self.send_response(entry.get("status", 200))
            headers = entry.get("headers", {})
            for key, value in headers.items():
                self.send_header(key, value)
            if not any(k.lower() == "content-type" for k in headers):
                self.send_header("Content-Type", entry.get("content_type", "text/html; charset=utf-8"))
            self.end_headers()
            body = entry.get("body", "")
            self.wfile.write(body.encode("utf-8") if isinstance(body, str) else body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.handle_error = lambda *args: None  # keep client-closes-early noise out of test output
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(routes=routes, base=f"http://127.0.0.1:{server.server_port}")
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _html(title: str | None, body: str) -> str:
    head = f"<head><title>{title}</title></head>" if title else "<head></head>"
    return f"<html>{head}<body>{body}</body></html>"


# ── fetcher.fetch_url + extraction ──────────────────────────────────────────


class TestFetchUrl:
    async def test_title_from_title_tag_and_final_url(self, page_server) -> None:
        from fetcher import fetch_url

        page_server.routes["/page"] = {"body": _html("Asyncio — Python docs", "<p>Event loop body text.</p>")}
        result = fetch_url(f"{page_server.base}/page")
        assert result.is_ok, result
        assert result.value["url"] == f"{page_server.base}/page"
        assert result.value["title"] == "Asyncio — Python docs"
        assert "Event loop body text." in result.value["text"]

    async def test_title_falls_back_to_first_h1(self, page_server) -> None:
        from fetcher import fetch_url

        page_server.routes["/page"] = {"body": _html(None, "<h1>Fallback Heading</h1><p>Body text.</p>")}
        result = fetch_url(f"{page_server.base}/page")
        assert result.is_ok, result
        assert result.value["title"] == "Fallback Heading"
        assert "Fallback Heading" in result.value["text"]  # h1 is visible text too

    async def test_chrome_subtrees_stripped(self, page_server) -> None:
        from fetcher import fetch_url

        chrome = (
            "<nav>Advertisement</nav>"
            "<header>Header noise</header>"
            "<footer>Footer noise</footer>"
            "<aside>Sidebar promo</aside>"
            "<script>var x = 1;</script>"
            "<style>body { color: red; }</style>"
            "<noscript>Enable JavaScript</noscript>"
        )
        page_server.routes["/page"] = {"body": _html("T", f"{chrome}<p>Real content stays.</p>")}
        text = fetch_url(f"{page_server.base}/page").unwrap()["text"]
        assert "Real content stays." in text
        for noise in ("Advertisement", "Header noise", "Footer noise", "Sidebar promo", "var x = 1", "color: red", "Enable JavaScript"):
            assert noise not in text, f"chrome leaked into extracted text: {noise}"

    async def test_block_newlines_and_whitespace_collapse(self, page_server) -> None:
        from fetcher import fetch_url

        body = "<p>first   block</p><p>second block</p><ul><li>item one</li><li>item two</li></ul>inline <b>bold</b> glue"
        page_server.routes["/page"] = {"body": _html("T", body)}
        text = fetch_url(f"{page_server.base}/page").unwrap()["text"]
        lines = text.split("\n")
        assert "first block" in lines  # whitespace runs collapsed
        assert "second block" in lines  # block-level newline
        assert "item one" in lines and "item two" in lines  # li as line breaks
        assert "inline bold glue" in text  # inline markup keeps word boundaries

    async def test_pre_preserved_as_is(self, page_server) -> None:
        from fetcher import fetch_url

        body = "<p>before</p><pre>def f():\n    return  1</pre><p>after</p>"
        page_server.routes["/page"] = {"body": _html("T", body)}
        text = fetch_url(f"{page_server.base}/page").unwrap()["text"]
        assert "def f():\n    return  1" in text  # interior whitespace untouched

    async def test_charset_from_content_type(self, page_server) -> None:
        from fetcher import fetch_url

        page_server.routes["/page"] = {
            "body": b"caf\xe9 latte body",
            "content_type": "text/html; charset=iso-8859-1",
        }
        text = fetch_url(f"{page_server.base}/page").unwrap()["text"]
        assert "café latte body" in text

    async def test_404_is_err(self, page_server) -> None:
        from fetcher import fetch_url

        result = fetch_url(f"{page_server.base}/missing")
        assert result.is_err, result
        assert "404" in result.error

    async def test_non_html_content_type_is_err(self, page_server) -> None:
        from fetcher import fetch_url

        page_server.routes["/page"] = {"body": "just plain text", "content_type": "text/plain"}
        result = fetch_url(f"{page_server.base}/page")
        assert result.is_err, result
        assert "not an HTML page" in result.error

    async def test_oversized_body_is_err(self, page_server) -> None:
        from fetcher import fetch_url

        page_server.routes["/page"] = {"body": b"a" * (_MAX_CONTENT_LENGTH + 1)}
        result = fetch_url(f"{page_server.base}/page")
        assert result.is_err, result
        assert "too large" in result.error

    async def test_redirect_followed_final_url_returned(self, page_server) -> None:
        from fetcher import fetch_url

        page_server.routes["/old"] = {"status": 302, "headers": {"Location": "/final"}, "body": ""}
        page_server.routes["/final"] = {"body": _html("Final Page", "<p>Redirected content.</p>")}
        result = fetch_url(f"{page_server.base}/old")
        assert result.is_ok, result
        assert result.value["url"] == f"{page_server.base}/final"
        assert result.value["title"] == "Final Page"

    async def test_redirect_to_non_http_scheme_is_err(self, page_server) -> None:
        from fetcher import fetch_url

        page_server.routes["/evil"] = {"status": 302, "headers": {"Location": "file:///C:/Windows/win.ini"}, "body": ""}
        result = fetch_url(f"{page_server.base}/evil")
        assert result.is_err, result
        assert "scheme" in result.error.lower()

    async def test_too_many_redirects_is_err(self, page_server) -> None:
        from fetcher import fetch_url

        # 4 redirects exceed the max-3-hops budget
        for i in range(4):
            page_server.routes[f"/hop{i}"] = {"status": 302, "headers": {"Location": f"/hop{i + 1}"}, "body": ""}
        page_server.routes["/hop4"] = {"body": _html("T", "<p>content</p>")}
        result = fetch_url(f"{page_server.base}/hop0")
        assert result.is_err, result
        assert "redirect" in result.error.lower()


# ── kb_index URL mode: fetch + index end to end ─────────────────────────────


class TestKbIndexUrlMode:
    async def test_fetch_and_index_searchable(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": _html("Asyncio — Python docs", "<p>Event loop scheduling internals.</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        assert result["title"] == "Asyncio — Python docs", result
        assert result["status"] == "added"
        assert result["file_path"] == f"{page_server.base}/page"

        found = kb_storage.search("event loop scheduling")
        assert found.is_ok, found
        assert any(r["file_path"] == result["file_path"] for r in found.value), found.value

    async def test_h1_fallback_title_through_kb_index(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": _html(None, "<h1>Fallback Heading</h1><p>Body text.</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        assert result["title"] == "Fallback Heading", result

    async def test_chrome_text_not_searchable(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        body = (
            "<nav>Advertisement</nav><script>var x = 1;</script><style>body{color:red}</style><footer>Footer noise</footer><p>Real content stays.</p>"
        )
        page_server.routes["/page"] = {"body": _html("T", body)}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        assert result["status"] == "added"

        for noise in ("Advertisement", "var x = 1", "color:red", "Footer noise"):
            found = kb_storage.search(noise)
            assert found.is_ok
            assert not any(r["file_path"] == result["file_path"] for r in found.value), f"chrome text searchable: {noise}"

    async def test_collection_default_is_slugified_domain(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": _html("T", "<p>Body text.</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        port = page_server.base.rsplit(":", 1)[1]
        assert result["collection"] == f"127-0-0-1-{port}", result

    async def test_collection_override_applies(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": _html("T", "<p>Body text.</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page", collection="custom-name")
        assert result["collection"] == "custom-name", result

    async def test_redirect_indexes_under_final_url(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/old"] = {"status": 302, "headers": {"Location": "/final"}, "body": ""}
        page_server.routes["/final"] = {"body": _html("Final Page", "<p>Redirected content body.</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/old")
        assert result["file_path"] == f"{page_server.base}/final", result

        found = kb_storage.search("redirected content")
        assert found.is_ok
        assert any(r["file_path"] == result["file_path"] for r in found.value), found.value

    async def test_404_surfaces_err(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/missing")
        assert "error" in result, result
        assert "404" in result["error"], result

    async def test_non_html_content_type_surfaces_err(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": "plain", "content_type": "text/plain"}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        assert "error" in result, result
        assert "not an HTML page" in result["error"]

    async def test_oversized_body_surfaces_err(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": b"a" * (_MAX_CONTENT_LENGTH + 1)}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        assert "error" in result, result
        assert "too large" in result["error"]

    async def test_redirect_to_non_http_scheme_surfaces_err(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/evil"] = {"status": 302, "headers": {"Location": "ftp://example.com/x"}, "body": ""}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/evil")
        assert "error" in result, result
        assert "scheme" in result["error"].lower(), result


# ── routing matrix ──────────────────────────────────────────────────────────


def _mocked_clone():
    return patch("storage.subprocess.run", return_value=MagicMock(returncode=0, stderr=""))


def _prepare_github_repo(storage: Storage, name: str = "test-repo", content: str = "# Test\nContent here") -> None:
    """Pre-create the clone target so add_repo skips the network entirely."""
    repo_dir = storage.repos_dir / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text(content)


class TestRoutingMatrix:
    async def test_plain_http_url_routes_to_fetch(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": _html("T", "<p>Fetched body text.</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        assert result.get("status") == "added", result

    async def test_github_repo_url_routes_to_add_repo(self, kb_storage: Storage) -> None:
        # guard (P2-1 parity): repo-shaped URLs keep cloning
        from server import kb_index

        _prepare_github_repo(kb_storage)
        with patch("server._get_storage", return_value=kb_storage), _mocked_clone():
            result = await kb_index("https://github.com/test/test-repo")
        assert result.get("name") == "test-repo", result
        entry = kb_storage._load_config()["repos"]["test-repo"]
        assert entry["url"] == "https://github.com/test/test-repo"

    async def test_deep_github_url_routes_to_fetch(self, kb_storage: Storage) -> None:
        from server import kb_index

        deep = "https://github.com/owner/repo/blob/main/README.md"
        canned = {"url": deep, "title": "README", "text": "Deep page extracted body text."}
        with (
            patch("server._get_storage", return_value=kb_storage),
            patch("server.fetch_url", return_value=Ok(value=canned)) as fetch_mock,
            patch("storage.subprocess.run") as clone_mock,
        ):
            result = await kb_index(deep)
        fetch_mock.assert_called_once_with(deep)
        clone_mock.assert_not_called()
        assert result["file_path"] == deep, result

    async def test_type_url_overrides_repo_shaped(self, kb_storage: Storage) -> None:
        from server import kb_index

        shallow = "https://github.com/test/test-repo"
        canned = {"url": shallow, "title": "Repo page", "text": "Forced fetch body text."}
        with patch("server._get_storage", return_value=kb_storage), patch("server.fetch_url", return_value=Ok(value=canned)) as fetch_mock:
            result = await kb_index(shallow, type="url")
        fetch_mock.assert_called_once_with(shallow)
        assert result["file_path"] == shallow, result
        assert "test-repo" not in kb_storage._load_config().get("repos", {})

    async def test_type_web_alias_forces_fetch(self, kb_storage: Storage) -> None:
        from server import kb_index

        shallow = "https://github.com/test/test-repo"
        canned = {"url": shallow, "title": "Repo page", "text": "Forced fetch body text."}
        with patch("server._get_storage", return_value=kb_storage), patch("server.fetch_url", return_value=Ok(value=canned)) as fetch_mock:
            result = await kb_index(shallow, type="web")
        fetch_mock.assert_called_once_with(shallow)
        assert result["file_path"] == shallow, result

    async def test_type_github_forces_repo_on_deep_url(self, kb_storage: Storage) -> None:
        # guard: the forced repo route wins even for page-shaped URLs
        from server import kb_index

        deep = "https://github.com/owner/repo/tree/main"
        with (
            patch("server._get_storage", return_value=kb_storage),
            patch("server.fetch_url") as fetch_mock,
            patch("storage.subprocess.run", return_value=MagicMock(returncode=1, stderr="boom")) as clone_mock,
        ):
            result = await kb_index(deep, type="github")
        fetch_mock.assert_not_called()
        clone_mock.assert_called_once()
        assert "Clone failed" in result.get("error", ""), result

    async def test_unknown_type_lists_web_kind(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("x", type="bogus")
        assert "error" in result, result
        assert "web" in result["error"].lower()

    async def test_unknown_source_still_err(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("completely-unknown-source")
        assert "error" in result, result


# ── identity: URL-keyed replace semantics ──────────────────────────────────


class TestUrlIdentity:
    async def test_reindex_same_url_replaces_content(self, kb_storage: Storage, page_server) -> None:
        from server import kb_index

        page_server.routes["/page"] = {"body": _html("P", "<p>alpha version body text</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            first = await kb_index(f"{page_server.base}/page")
        assert first["status"] == "added", first

        page_server.routes["/page"] = {"body": _html("P", "<p>beta version body text</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            second = await kb_index(f"{page_server.base}/page")
        assert second["status"] == "replaced", second
        assert second["file_path"] == first["file_path"]

        old = kb_storage.search("alpha version")
        assert old.is_ok
        assert not any(r["file_path"] == first["file_path"] for r in old.value), "old content must be gone"
        new = kb_storage.search("beta version")
        assert new.is_ok
        assert any(r["file_path"] == first["file_path"] for r in new.value), new.value

    def test_reindex_preserves_created_at_and_retrieval_count(self, tmp_storage: Storage) -> None:
        tmp_storage.add_url(DOC_URL, "asyncio first version body text", title="asyncio").unwrap()
        conn = tmp_storage._get_conn()
        conn.execute("UPDATE documents SET retrieval_count = 7, created_at = '2020-01-01 00:00:00' WHERE file_path = ?", (DOC_URL,))
        conn.commit()

        second = tmp_storage.add_url(DOC_URL, "asyncio second version body text", title="asyncio").unwrap()
        assert second["status"] == "replaced", second

        row = conn.execute("SELECT created_at, retrieval_count FROM documents WHERE file_path = ?", (DOC_URL,)).fetchone()
        assert row["created_at"] == "2020-01-01 00:00:00", row
        assert row["retrieval_count"] == 7, row

    def test_shrinking_page_leaves_no_stale_chunks(self, tmp_storage: Storage) -> None:
        url = "https://example.com/long"
        long_text = "filler words " * 300 + "stalechunkmarker"
        added = tmp_storage.add_url(url, long_text).unwrap()
        assert added["chunks"] >= 2, added

        tmp_storage.add_url(url, "short new body").unwrap()

        rows = tmp_storage._get_conn().execute("SELECT chunk_index, content FROM documents WHERE file_path = ?", (url,)).fetchall()
        assert len(rows) == 1, f"stale chunks left behind: {len(rows)}"
        assert "stalechunkmarker" not in rows[0]["content"]
        found = tmp_storage.search("stalechunkmarker")
        assert found.is_ok
        assert not any(r["file_path"] == url for r in found.value)

    async def test_kb_get_round_trip_on_url_file_path(self, kb_storage: Storage, page_server) -> None:
        # P2-2 lesson: every new file_path identity must survive kb_get
        from server import kb_get, kb_index

        page_server.routes["/page"] = {"body": _html("Round Trip", "<p>Round trip body text.</p>")}
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"{page_server.base}/page")
        with patch("server._get_storage", return_value=kb_storage):
            got = await kb_get(result["file_path"])
        assert got.get("file_path") == result["file_path"], got
        assert "Round trip body text." in got["content"]

    def test_add_url_result_shape_and_title_fallback(self, tmp_storage: Storage) -> None:
        result = tmp_storage.add_url(DOC_URL, "body text").unwrap()
        assert set(result.keys()) == {"collection", "file_path", "title", "status", "chunks"}
        assert result["collection"] == "docs-python-org"
        assert result["file_path"] == DOC_URL
        assert result["title"] == DOC_URL  # no extracted title -> the URL itself

    def test_add_url_normalizes_identity(self, tmp_storage: Storage) -> None:
        first = tmp_storage.add_url("HTTPS://Docs.Python.ORG/3/", "body one").unwrap()
        second = tmp_storage.add_url("https://docs.python.org/3/", "body two").unwrap()
        assert first["file_path"] == second["file_path"] == "https://docs.python.org/3/"
        assert second["status"] == "replaced"

    def test_domain_slug_shape(self) -> None:
        from validation import domain_slug

        assert domain_slug("https://docs.python.org/3/library/asyncio.html") == "docs-python-org"
        assert domain_slug("https://Example.COM:8443/x") == "example-com-8443"


# ── corpus-cache hook (BH-4 discipline) ────────────────────────────────────


class TestCorpusCacheHook:
    def test_add_url_invalidates_corpus_cache(self, kb_storage: Storage, monkeypatch) -> None:
        original = kb_storage._invalidate_corpus_cache
        calls: list = []

        def spy() -> None:
            calls.append(1)
            original()

        monkeypatch.setattr(kb_storage, "_invalidate_corpus_cache", spy)
        kb_storage.add_url(DOC_URL, "body text here").unwrap()
        assert calls, "add_url must invalidate the corpus cache (BH-4)"


# ── tool-count guard (URL mode is a mode, not a tool) ──────────────────────


class TestToolCount:
    async def test_tool_count_stays_32(self) -> None:
        from server import mcp

        tools = await mcp.list_tools()
        assert len(tools) == 32, f"URL mode is a kb_index mode — expected 32 tools, got {len(tools)}"


# ── lead round-2 fixes (blind review follow-ups) ────────────────────────────


class TestLeadRound2Fixes:
    async def test_unknown_charset_is_err_not_exception(self, page_server) -> None:
        """An unknown encoding name must return Err, not raise LookupError
        through the tool layer (blind-review defect)."""
        from fetcher import fetch_url

        page_server.routes["/weird"] = {
            "body": "<html><body>x</body></html>",
            "content_type": "text/html; charset=x-not-a-charset",
        }
        result = fetch_url(f"{page_server.base}/weird")  # must not raise
        assert result.is_err
        assert "charset" in result.error.lower()

    def test_git_suffix_repo_shaped_at_any_depth(self) -> None:
        """.git-marked URLs are repos at any depth; unmarked 3-segment URLs
        are pages (the documented boundary)."""
        from server import _is_repo_shaped

        assert _is_repo_shaped("https://gitlab.com/group/subgroup/proj.git")
        assert _is_repo_shaped("https://gitlab.com/group/subgroup/proj.git/")
        assert not _is_repo_shaped("https://gitlab.com/group/subgroup/proj")
        assert not _is_repo_shaped("https://github.com/owner/repo/blob/main/README.md")
        assert _is_repo_shaped("https://github.com/owner/repo")
