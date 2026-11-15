"""Anchors for Card P2-4: PyPI package indexing (FUTURE_PLAN item 11).

MUST FAIL at the pre-card HEAD (f52bcb3) and PASS after the card lands:

- pypi.fetch_pypi_docs / Storage.add_pypi do not exist (ModuleNotFoundError /
  AttributeError)
- kb_index rejects pypi://test-pkg ("Unsupported source")

Guards that cannot fail before (they pin behavior the card must not break):
the repo-shaped GitHub routing still clones (P2-1 parity), a bare name with
no type= is NOT auto-detected as a package, the unknown-source error keeps
listing the other kinds, and the MCP tool count stays 32 (pypi is a kb_index
mode, not a new tool).

All network in these tests is mocked at urllib.request.urlopen — NO external
network; sdists are REAL tarballs/zips built in memory with stdlib
tarfile/zipfile.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import tempfile
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from result import Ok
from storage import Storage
from validation import _MAX_CONTENT_LENGTH

PKG = "test-pkg"
JSON_URL = f"https://pypi.org/pypi/{PKG}/json"
ARTIFACT_URL = f"https://files.pythonhosted.org/packages/sdist/{PKG}-1.0.tar.gz"

README_MD = "# Test Package\n\nHello docs body about widgets."
GUIDE_RST = "Guide\n=====\n\nGuide body about gadget tuning."


# ── fixtures & sdist builders ───────────────────────────────────────────────


@pytest.fixture
def kb_storage(tmp_path):
    """Storage on tmp_path (not TemporaryDirectory): kb_index opens SQLite
    connections in executor threads that outlive the test, and Windows cannot
    unlink files that are still open at fixture teardown."""
    storage = Storage(tmp_path / "kb-data")
    yield storage
    storage.close()


def _sdist(files: dict[str, str], prefix: str = f"{PKG}-1.0/") -> bytes:
    """A REAL sdist tarball in memory (stdlib tarfile)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(prefix + rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip_sdist(files: dict[str, str], prefix: str = f"{PKG}-1.0/") -> bytes:
    """A REAL sdist zip in memory (stdlib zipfile)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, text in files.items():
            zf.writestr(prefix + rel, text)
    return buf.getvalue()


class _Response:
    """Minimal urlopen response: read(n) + context manager."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def pypi_http():
    """Mock urllib.request.urlopen with the two PyPI calls (NO network):
    the JSON metadata GET and the sdist artifact download. Per-URL errors
    can be injected via state.errors."""
    state = SimpleNamespace(
        json_body={"urls": [{"packagetype": "sdist", "url": ARTIFACT_URL}]},
        artifact=b"",
        errors={},
    )

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - urllib API
        url = request.full_url if hasattr(request, "full_url") else request
        if url in state.errors:
            raise state.errors[url]
        if url == JSON_URL:
            return _Response(json.dumps(state.json_body).encode())
        if url == ARTIFACT_URL:
            return _Response(state.artifact)
        raise AssertionError(f"unexpected URL fetched: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        yield state


# ── pypi.fetch_pypi_docs: download + unpack + extraction ────────────────────


class TestFetchPypiDocs:
    async def test_extracts_readme_and_docs_with_heading_titles(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.artifact = _sdist({"README.md": README_MD, "docs/guide.rst": GUIDE_RST})
        result = fetch_pypi_docs(PKG)
        assert result.is_ok, result
        docs = {d["path"]: d for d in result.value}
        assert set(docs) == {"README.md", "docs/guide.rst"}
        assert docs["README.md"]["title"] == "Test Package"  # markdown '#'-heading
        assert docs["docs/guide.rst"]["title"] == "Guide"  # rst underline heading
        assert "Hello docs body about widgets." in docs["README.md"]["text"]  # nearly raw
        assert "Guide body about gadget tuning." in docs["docs/guide.rst"]["text"]

    async def test_title_falls_back_to_file_name(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.artifact = _sdist({"README.md": "no heading here, just prose", "docs/note.md": "also plain prose"})
        docs = {d["path"]: d for d in fetch_pypi_docs(PKG).unwrap()}
        assert docs["README.md"]["title"] == "README.md"
        assert docs["docs/note.md"]["title"] == "note.md"

    async def test_collection_rules_readme_any_case_top_level_md_rst(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.artifact = _sdist(
            {
                "README.RST": "readme body text",
                "CHANGELOG.md": "# Changelog\n\nhistory text",
                "notes.txt": "top-level txt is not documentation",
                "requirements.txt": "also excluded",
                "docs/tips.txt": "tips under docs are collected",
                "src/mod.py": "code is not collected",
            }
        )
        docs = {d["path"] for d in fetch_pypi_docs(PKG).unwrap()}
        assert "README.RST" in docs  # README* any case, .rst ok
        assert "CHANGELOG.md" in docs  # top-level *.md
        assert "docs/tips.txt" in docs  # docs/**/*.{md,rst,txt}
        assert "notes.txt" not in docs  # top-level *.txt (non-README) is out
        assert "requirements.txt" not in docs
        assert "src/mod.py" not in docs

    async def test_zip_sdist_supported(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.artifact = _zip_sdist({"README.md": README_MD, "docs/guide.rst": GUIDE_RST})
        result = fetch_pypi_docs(PKG)
        assert result.is_ok, result
        assert {d["path"] for d in result.value} == {"README.md", "docs/guide.rst"}

    async def test_invalid_name_is_err(self) -> None:
        from pypi import fetch_pypi_docs

        for bad in ("../../etc", "", "has space", "name/with/slash"):
            result = fetch_pypi_docs(bad)
            assert result.is_err, f"{bad!r} must be rejected"
            assert "package name" in result.error.lower(), result.error

    async def test_404_package_is_err_not_found(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.errors[JSON_URL] = urllib.error.HTTPError(JSON_URL, 404, "Not Found", None, None)
        result = fetch_pypi_docs(PKG)
        assert result.is_err, result
        assert "not found" in result.error.lower(), result.error
        assert PKG in result.error  # the error names the package

    async def test_no_sdist_in_release_is_err(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.json_body = {"urls": [{"packagetype": "bdist_wheel", "url": "https://files.pythonhosted.org/x.whl"}]}
        result = fetch_pypi_docs(PKG)
        assert result.is_err, result
        assert "sdist" in result.error.lower(), result.error

    async def test_oversized_artifact_is_err(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.artifact = b"a" * (4 * _MAX_CONTENT_LENGTH + 1)  # 40MB + 1
        result = fetch_pypi_docs(PKG)
        assert result.is_err, result
        assert "too large" in result.error.lower(), result.error

    async def test_sdist_cap_is_derived_from_content_precedent(self) -> None:
        # derived (not measured) limit — must stay 4 x the 10MB content precedent
        from pypi import _MAX_SDIST_BYTES

        assert _MAX_SDIST_BYTES == 4 * _MAX_CONTENT_LENGTH

    async def test_bad_archive_is_err(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.artifact = b"this is definitely not an archive"
        result = fetch_pypi_docs(PKG)
        assert result.is_err, result
        assert "archive" in result.error.lower(), result.error

    async def test_tar_slip_member_is_err_no_file_written_outside(self, pypi_http) -> None:
        """A crafted malicious tarball (../ member) must be rejected and must
        not write outside the scratch dir (tar-slip anchor)."""
        from pypi import fetch_pypi_docs

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"evil"
            for name in (f"{PKG}-1.0/README.md", "../evil-p24-slip.txt"):
                payload = README_MD.encode() if "README" in name else data
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        pypi_http.artifact = buf.getvalue()

        result = fetch_pypi_docs(PKG)
        assert result.is_err, result
        assert "archive" in result.error.lower() or "unsafe" in result.error.lower(), result.error
        assert not (Path(tempfile.gettempdir()) / "evil-p24-slip.txt").exists(), "tar-slip wrote outside the scratch dir"

    async def test_zip_slip_member_is_err(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs

        pypi_http.artifact = _zip_sdist({"README.md": README_MD, "../evil-p24-zipslip.txt": "evil"})
        result = fetch_pypi_docs(PKG)
        assert result.is_err, result
        assert "unsafe" in result.error.lower() or "archive" in result.error.lower(), result.error
        assert not (Path(tempfile.gettempdir()) / "evil-p24-zipslip.txt").exists(), "zip-slip wrote outside the scratch dir"


# ── Storage.add_pypi: chunking, identity, replace semantics ─────────────────


def _docs(*pairs: tuple[str, str]) -> list[dict]:
    return [{"path": rel, "title": rel, "text": text} for rel, text in pairs]


def _snapshot(storage: Storage) -> list[tuple]:
    conn = storage._get_conn()
    rows = conn.execute(
        "SELECT collection, file_path, chunk_index, total_chunks, content, content_hash, title, context, "
        "retrieval_count, created_at FROM documents ORDER BY collection, file_path, chunk_index"
    ).fetchall()
    return [tuple(r) for r in rows]


class _FlakyConn:
    """Proxy over a real sqlite3 connection that raises sqlite3.OperationalError
    exactly on the Nth write statement — the P2-H simulated mid-write failure
    (tests/test_p2h_hygiene.py pattern). Reads and later writes pass through."""

    def __init__(self, conn: sqlite3.Connection, fail_on_write: int):
        self._conn = conn
        self._fail_on_write = fail_on_write
        self._writes = 0

    def execute(self, sql: str, *args):
        if sql.split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            self._writes += 1
            if self._writes == self._fail_on_write:
                raise sqlite3.OperationalError("simulated mid-write failure (P2-4)")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class TestAddPypi:
    def test_result_shape_and_searchable(self, tmp_storage: Storage) -> None:
        from pypi import fetch_pypi_docs  # noqa: F401 - documents the seam pair

        result = tmp_storage.add_pypi(PKG, _docs(("README.md", README_MD), ("docs/guide.rst", GUIDE_RST))).unwrap()
        assert set(result.keys()) == {"collection", "docs_indexed", "chunks", "status"}
        assert result == {"collection": PKG, "docs_indexed": 2, "chunks": 2, "status": "added"}

        found = tmp_storage.search("gadget tuning")
        assert found.is_ok, found
        assert any(r["file_path"] == "pypi://test-pkg/docs/guide.rst" for r in found.value), found.value

    def test_collection_slug_from_package_name(self, tmp_storage: Storage) -> None:
        result = tmp_storage.add_pypi("Test-Pkg", _docs(("README.md", README_MD))).unwrap()
        assert result["collection"] == "test-pkg"  # lowercase, non-alnum -> '-'

    def test_reindex_removes_dropped_docs_and_replaces(self, tmp_storage: Storage) -> None:
        tmp_storage.add_pypi(PKG, _docs(("README.md", "old readme marker text"), ("docs/guide.rst", "guide marker text"))).unwrap()

        second = tmp_storage.add_pypi(PKG, _docs(("README.md", "new readme marker text"))).unwrap()
        assert second["status"] == "replaced", second

        rows = tmp_storage._get_conn().execute("SELECT file_path, content FROM documents WHERE collection = ?", (PKG,)).fetchall()
        assert all(r["file_path"] == "pypi://test-pkg/README.md" for r in rows), "guide rows must be gone"
        assert all("new readme marker" in r["content"] for r in rows), "README must be replaced"
        gone = tmp_storage.search("guide marker")
        assert gone.is_ok
        assert not any(r["collection"] == PKG for r in gone.value), "dropped doc still searchable"

    def test_reindex_preserves_created_at_and_retrieval_count(self, tmp_storage: Storage) -> None:
        tmp_storage.add_pypi(PKG, _docs(("README.md", README_MD), ("docs/guide.rst", GUIDE_RST))).unwrap()
        conn = tmp_storage._get_conn()
        conn.execute("UPDATE documents SET retrieval_count = 7, created_at = '2020-01-01 00:00:00' WHERE file_path = 'pypi://test-pkg/README.md'")
        conn.commit()

        second = tmp_storage.add_pypi(PKG, _docs(("README.md", "changed readme body"), ("docs/guide.rst", GUIDE_RST))).unwrap()
        assert second["status"] == "replaced", second

        readme = conn.execute(
            "SELECT created_at, retrieval_count FROM documents WHERE file_path = 'pypi://test-pkg/README.md' AND chunk_index = 0"
        ).fetchone()
        assert readme["created_at"] == "2020-01-01 00:00:00", readme  # F-4: preserved on surviving paths
        assert readme["retrieval_count"] == 7, readme
        guide = conn.execute("SELECT created_at FROM documents WHERE file_path = 'pypi://test-pkg/docs/guide.rst' AND chunk_index = 0").fetchone()
        assert guide["created_at"] != "2020-01-01 00:00:00", "guide never had a preserved created_at"

    def test_mid_failure_leaves_nothing(self, tmp_path: Path, monkeypatch) -> None:
        """P2-H discipline: ONE transaction — a failed replace rolls back and a
        later commit on the same connection persists nothing."""
        storage = Storage(tmp_path / "kb-data")
        try:
            storage.add_pypi(PKG, _docs(("README.md", "first readme body"), ("docs/guide.rst", "first guide body"))).unwrap()
            before = _snapshot(storage)

            conn = storage._get_conn()
            monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=3))  # DELETE + INSERT + INSERT(fails)
            result = storage.add_pypi(PKG, _docs(("README.md", "second readme body"), ("docs/guide.rst", "second guide body")))
            assert result.is_err, result

            conn.commit()  # a later commit on the same connection must resurrect nothing
            assert _snapshot(storage) == before, "mid-failure left pending rows"
        finally:
            storage.close()

    def test_add_pypi_invalidates_corpus_cache(self, kb_storage: Storage, monkeypatch) -> None:
        original = kb_storage._invalidate_corpus_cache
        calls: list = []

        def spy() -> None:
            calls.append(1)
            original()

        monkeypatch.setattr(kb_storage, "_invalidate_corpus_cache", spy)
        kb_storage.add_pypi(PKG, _docs(("README.md", README_MD))).unwrap()
        assert calls, "add_pypi must invalidate the corpus cache (BH-4)"

    def test_invalid_package_name_is_err(self, tmp_storage: Storage) -> None:
        result = tmp_storage.add_pypi("../../etc", _docs(("README.md", README_MD)))
        assert result.is_err, result
        assert "package name" in result.error.lower(), result.error

    def test_unsafe_doc_path_is_err(self, tmp_storage: Storage) -> None:
        result = tmp_storage.add_pypi(PKG, _docs(("../evil.md", README_MD)))
        assert result.is_err, result

    def test_empty_doc_text_is_err(self, tmp_storage: Storage) -> None:
        result = tmp_storage.add_pypi(PKG, _docs(("README.md", "")))
        assert result.is_err, result


# ── kb_index routing: pypi mode ─────────────────────────────────────────────


def _mocked_clone():
    return patch("storage.subprocess.run", return_value=MagicMock(returncode=0, stderr=""))


def _prepare_github_repo(storage: Storage, name: str = "test-repo") -> None:
    repo_dir = storage.repos_dir / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text("# Test\nContent here")


class TestKbIndexPypiMode:
    async def test_pypi_scheme_end_to_end_searchable(self, kb_storage: Storage, pypi_http) -> None:
        from server import kb_index

        pypi_http.artifact = _sdist({"README.md": README_MD, "docs/guide.rst": GUIDE_RST})
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"pypi://{PKG}")
        assert result["status"] == "added", result
        assert result["collection"] == PKG
        assert result["docs_indexed"] == 2

        found = kb_storage.search("gadget tuning")
        assert found.is_ok, found
        assert any(r["file_path"] == "pypi://test-pkg/docs/guide.rst" for r in found.value), found.value

    async def test_type_pypi_with_bare_name(self, kb_storage: Storage) -> None:
        from server import kb_index

        canned = [{"path": "README.md", "title": "T", "text": "package readme body text"}]
        with patch("server._get_storage", return_value=kb_storage), patch("server.fetch_pypi_docs", return_value=Ok(value=canned)) as fetch_mock:
            result = await kb_index(PKG, type="pypi")
        fetch_mock.assert_called_once_with(PKG)
        assert result["status"] == "added", result
        assert result["collection"] == PKG

    async def test_collection_override_applies(self, kb_storage: Storage) -> None:
        from server import kb_index

        canned = [{"path": "README.md", "title": "T", "text": "package readme body text"}]
        with patch("server._get_storage", return_value=kb_storage), patch("server.fetch_pypi_docs", return_value=Ok(value=canned)):
            result = await kb_index(f"pypi://{PKG}", collection="custom-name")
        assert result["collection"] == "custom-name", result

    async def test_mask_rejected_for_pypi(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"pypi://{PKG}", mask="**/*.md")
        assert "error" in result, result
        assert "mask" in result["error"].lower(), result

    async def test_unknown_type_lists_pypi_kind(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("x", type="bogus")
        assert "error" in result, result
        assert "pypi" in result["error"].lower(), result

    async def test_fetch_err_surfaces_through_kb_index(self, kb_storage: Storage, pypi_http) -> None:
        from server import kb_index

        pypi_http.errors[JSON_URL] = urllib.error.HTTPError(JSON_URL, 404, "Not Found", None, None)
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"pypi://{PKG}")
        assert "error" in result, result
        assert "not found" in result["error"].lower(), result

    async def test_kb_get_round_trip_on_pypi_file_path(self, kb_storage: Storage, pypi_http) -> None:
        # P2-2 lesson: every new file_path identity must survive kb_get
        from server import kb_get, kb_index

        pypi_http.artifact = _sdist({"README.md": README_MD})
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"pypi://{PKG}")
        assert result["status"] == "added", result
        with patch("server._get_storage", return_value=kb_storage):
            got = await kb_get("pypi://test-pkg/README.md")
        assert got.get("file_path") == "pypi://test-pkg/README.md", got
        assert "Hello docs body about widgets." in got["content"]


# ── routing guards (behavior the card must not break) ───────────────────────


class TestRoutingGuards:
    async def test_unknown_source_still_err(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("completely-unknown-source")
        assert "error" in result, result

    async def test_bare_name_without_type_is_not_pypi(self, kb_storage: Storage) -> None:
        # guard: auto-detection must NOT swallow bare names as packages
        from server import kb_index

        with (
            patch("server._get_storage", return_value=kb_storage),
            patch("server.fetch_pypi_docs", create=True) as fetch_mock,
        ):
            result = await kb_index("some-package")
        fetch_mock.assert_not_called()
        assert "error" in result, result

    async def test_github_repo_url_routes_to_add_repo(self, kb_storage: Storage) -> None:
        # guard (P2-1 parity): repo-shaped URLs keep cloning
        from server import kb_index

        _prepare_github_repo(kb_storage)
        with patch("server._get_storage", return_value=kb_storage), _mocked_clone():
            result = await kb_index("https://github.com/test/test-repo")
        assert result.get("name") == "test-repo", result


# ── tool-count guard (pypi is a mode, not a tool) ───────────────────────────


class TestToolCount:
    async def test_tool_count_stays_32(self) -> None:
        from server import mcp

        tools = await mcp.list_tools()
        assert len(tools) == 32, f"pypi mode is a kb_index mode — expected 32 tools, got {len(tools)}"


# ── lead fix: corrupted archives honor the Ok/Err contract (blind review B1) ─


class TestCorruptedArchiveContract:
    def test_truncated_targz_is_err(self, tmp_path) -> None:
        from pypi import _unpack
        from result import Err

        blob = _sdist({"README.md": "# T\n" + "body " * 200})
        err = _unpack(blob[: len(blob) // 2], tmp_path / "d")
        assert isinstance(err, Err)

    def test_crc_corrupt_zip_is_err(self, tmp_path) -> None:
        from pypi import _unpack
        from result import Err

        blob = _zip_sdist({"README.md": "B" * 64})
        corrupt = blob.replace(b"BBBBBBBBBB", b"BBBBABBBBB", 1)
        assert corrupt != blob  # the flip actually happened
        err = _unpack(corrupt, tmp_path / "d")
        assert isinstance(err, Err)

    def test_zip_name_collision_is_err(self, tmp_path) -> None:
        from pypi import _unpack
        from result import Err

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("escape", "x")  # file named like the dir below
            zf.writestr("escape/evil.md", "y")
        err = _unpack(buf.getvalue(), tmp_path / "d")
        assert isinstance(err, Err)

    def test_truncated_artifact_err_surfaces_through_fetch(self, pypi_http) -> None:
        from pypi import fetch_pypi_docs
        from result import Err

        blob = _sdist({"README.md": "# T\n" + "body " * 200})
        pypi_http.artifact = blob[: len(blob) // 2]
        result = fetch_pypi_docs(PKG)
        assert isinstance(result, Err)
        assert "archive" in result.error.lower() or "unrecognized" in result.error.lower()
