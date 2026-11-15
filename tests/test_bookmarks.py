"""Anchors for Card P2-2: bookmarks (table + upsert/list + MCP tools).

MUST FAIL at the pre-card HEAD (fce5ac4) and PASS after the card lands:

- the bookmarks table does not exist (OperationalError: no such table)
- Storage.bookmark_upsert / Storage.bookmark_list do not exist
- kb_bookmark / kb_bookmarks are not registered MCP tools
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from result import Err
from storage import Storage

URL_A = "https://example.com/a"
URL_B = "https://example.com/b"


@pytest.fixture
def kb_storage(tmp_path):
    storage = Storage(tmp_path / "kb-data")
    yield storage
    storage.close()


async def _mcp_call(tool: str, args: dict) -> list:
    """Call a tool through the real MCP layer; flatten list payloads."""
    from server import mcp

    result = await mcp.call_tool(tool, args)
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return [result]
    out: list = []
    for item in result:
        data = json.loads(item.text)
        out.extend(data if isinstance(data, list) else [data])
    return out


class TestBookmarksTable:
    def test_fresh_db_has_bookmarks_table(self, kb_storage: Storage) -> None:
        row = kb_storage._get_conn().execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bookmarks'").fetchone()
        assert row is not None


class TestBookmarkUpsert:
    def test_create_then_update_no_duplicate(self, kb_storage: Storage) -> None:
        first = kb_storage.bookmark_upsert(URL_A, title="A", note="first")
        assert first.is_ok, first
        assert first.value == {"url": URL_A, "created": True}
        created_at = kb_storage._get_conn().execute("SELECT created_at FROM bookmarks WHERE url = ?", (URL_A,)).fetchone()[0]

        second = kb_storage.bookmark_upsert(URL_A, title="A renamed")
        assert second.is_ok, second
        assert second.value == {"url": URL_A, "created": False}

        rows = kb_storage.bookmark_list().unwrap()
        assert len(rows) == 1  # second save does not duplicate
        row = rows[0]
        assert set(row.keys()) == {"url", "title", "note", "created_at", "updated_at"}
        assert row["title"] == "A renamed"
        assert row["note"] == "first"  # None leaves the stored value
        assert row["created_at"] == created_at  # created_at preserved

    def test_explicit_empty_clears_field(self, kb_storage: Storage) -> None:
        kb_storage.bookmark_upsert(URL_A, title="A", note="keep me").unwrap()
        kb_storage.bookmark_upsert(URL_A, title="").unwrap()
        row = kb_storage.bookmark_list().unwrap()[0]
        assert row["title"] == ""
        assert row["note"] == "keep me"

    def test_list_order_most_recent_first(self, kb_storage: Storage) -> None:
        kb_storage.bookmark_upsert(URL_A, title="A").unwrap()
        kb_storage.bookmark_upsert(URL_B, title="B").unwrap()
        assert [r["url"] for r in kb_storage.bookmark_list().unwrap()] == [URL_B, URL_A]

        kb_storage.bookmark_upsert(URL_A, title="A2").unwrap()
        assert [r["url"] for r in kb_storage.bookmark_list().unwrap()] == [URL_A, URL_B]


class TestBookmarkUrlValidation:
    @pytest.mark.parametrize("bad", ["", "   ", "example.com", "ftp://example.com", "git@github.com:x/y.git", "http://"])
    def test_bad_urls_are_err(self, kb_storage: Storage, bad: str) -> None:
        result = kb_storage.bookmark_upsert(bad)
        assert isinstance(result, Err), bad
        assert "url" in result.error.lower(), result.error

    def test_dangerous_chars_are_err(self, kb_storage: Storage) -> None:
        result = kb_storage.bookmark_upsert("https://example.com/a\nb")
        assert isinstance(result, Err), result
        assert "dangerous" in result.error.lower()

    def test_too_long_url_is_err(self, kb_storage: Storage) -> None:
        result = kb_storage.bookmark_upsert("https://example.com/" + "a" * 2048)
        assert isinstance(result, Err), result
        assert "too long" in result.error.lower()

    @pytest.mark.parametrize("good", ["http://example.com", "https://example.com/x?y=1&z=2"])
    def test_http_and_https_ok(self, kb_storage: Storage, good: str) -> None:
        result = kb_storage.bookmark_upsert(good)
        assert result.is_ok, result
        assert result.value["created"] is True


class TestHttpUrlNormalization:
    """F-6 fix anchors: validate_http_url strips its input and compares the
    scheme case-insensitively (RFC 3986 schemes are case-insensitive).

    MUST FAIL before the fix (both padded and uppercase-scheme URLs error with
    "Invalid URL scheme") and PASS after; dangerous schemes stay rejected.
    """

    def test_surrounding_whitespace_accepted(self, kb_storage: Storage) -> None:
        result = kb_storage.bookmark_upsert(" https://ok.com ")
        assert result.is_ok, result
        assert result.value["url"] == "https://ok.com"  # stored normalized

    def test_uppercase_scheme_accepted(self, kb_storage: Storage) -> None:
        result = kb_storage.bookmark_upsert("HTTPS://EXAMPLE.COM")
        assert result.is_ok, result
        assert result.value["url"] == "HTTPS://EXAMPLE.COM"

    def test_dangerous_scheme_rejected_after_lowercasing(self, kb_storage: Storage) -> None:
        result = kb_storage.bookmark_upsert("JavaScript://evil.example/x")
        assert isinstance(result, Err), result
        assert "scheme" in result.error.lower(), result.error


class TestBookmarksOutsideCorpusCache:
    def test_bookmark_writes_do_not_invalidate_corpus_cache(self, kb_storage: Storage, monkeypatch) -> None:
        kb_storage.bulk_insert_raw("guide", "src.md", "some content", "T").unwrap()
        kb_storage.find_related("guide", "src.md").unwrap()  # warm the cache
        calls: list = []
        original = kb_storage._invalidate_corpus_cache

        def spy() -> None:
            calls.append(1)
            original()

        monkeypatch.setattr(kb_storage, "_invalidate_corpus_cache", spy)
        kb_storage.bookmark_upsert(URL_A, title="A").unwrap()
        kb_storage.bookmark_list().unwrap()
        assert calls == [], "bookmarks are outside the documents projection — no cache hooks"
        assert kb_storage._corpus_cache is not None  # warmed cache survives


class TestBookmarkMcpLayer:
    async def test_kb_bookmark_and_list_via_mcp(self, kb_storage: Storage) -> None:
        with patch("server._get_storage", return_value=kb_storage):
            payloads = await _mcp_call("kb_bookmark", {"link": URL_A, "title": "A", "note": "n"})
            assert payloads == [{"url": URL_A, "created": True}], payloads
            payloads = await _mcp_call("kb_bookmarks", {})
        assert [p["url"] for p in payloads] == [URL_A], payloads

    async def test_schema_exposes_url_only(self) -> None:
        from server import mcp

        tools = await mcp.list_tools()
        schema = next(t.inputSchema for t in tools if t.name == "kb_bookmark")
        assert "url" in schema["properties"]
        assert not {"link", "href"} & schema["properties"].keys()

    async def test_kb_bookmarks_schema_has_no_params(self) -> None:
        from server import mcp

        tools = await mcp.list_tools()
        schema = next(t.inputSchema for t in tools if t.name == "kb_bookmarks")
        assert not schema.get("properties")
