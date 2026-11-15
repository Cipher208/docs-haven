"""Anchors for Card P2-1: `storage.add_local` + `kb_index` unified entry.

MUST FAIL at the pre-card HEAD (61a4af8) and PASS after the card lands:

- add_local does not exist (AttributeError / collection errors)
- kb_index does not exist
- reindex of a local collection resolves repos_dir/<name> which never
  exists for local folders -> "Repository not found" instead of reindex
- find_changed_docs on a local collection reports files as "deleted"
  because it scans repos_dir/<name> instead of the local folder
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from result import Err
from storage import Storage

MD_INTRO = "# Intro\nFastAPI dependency injection with Depends and parameters"
MD_PARAMS = "# Params\nPath and query parameters in FastAPI"
MD_QUANTUM = "# Quantum\nQuantum entanglement for distributed consensus"


@pytest.fixture
def kb_storage(tmp_path):
    """Storage on tmp_path (not TemporaryDirectory): kb_index opens SQLite
    connections in executor threads that outlive the test, and Windows cannot
    unlink files that are still open at fixture teardown."""
    storage = Storage(tmp_path / "kb-data")
    yield storage
    storage.close()


def _make_folder(tmp_path, name: str = "notes", files: dict[str, str] | None = None):
    """Create a local folder with known markdown content."""
    folder = tmp_path / name
    folder.mkdir()
    for fname, content in (files if files is not None else {"intro.md": MD_INTRO, "params.md": MD_PARAMS}).items():
        (folder / fname).write_text(content)
    return folder


def _mocked_clone():
    return patch("storage.subprocess.run", return_value=MagicMock(returncode=0, stderr=""))


def _prepare_github_repo(storage: Storage, name: str = "test-repo", content: str = "# Test\nContent here") -> None:
    """Pre-create the clone target so add_repo skips the network entirely."""
    repo_dir = storage.repos_dir / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text(content)


async def _mcp_call(tool: str, args: dict) -> list:
    """Call a tool through the real MCP layer and return parsed JSON payloads."""
    from server import mcp

    result = await mcp.call_tool(tool, args)
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return [result]
    return [json.loads(item.text) for item in result]


# ── storage.add_local ───────────────────────────────────────────────────────


class TestAddLocal:
    def test_add_local_indexes_folder_searchable(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path)
        result = kb_storage.add_local(str(folder), description="local notes")
        assert result.is_ok, result

        # result shape mirrors add_repo exactly
        assert set(result.value.keys()) == {"name", "status", "files_indexed", "chunks"}
        assert result.value["name"] == "notes"
        assert result.value["status"] == "added"
        assert result.value["files_indexed"] == 2

        found = kb_storage.search("dependency injection")
        assert found.is_ok, found
        assert any(r["file_path"] == "intro.md" and r["collection"] == "notes" for r in found.value), found.value

    def test_add_local_default_mask_is_markdown(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path, files={"intro.md": MD_INTRO, "notes.txt": "not markdown"})
        result = kb_storage.add_local(str(folder))
        assert result.is_ok
        assert result.value["files_indexed"] == 1

    def test_add_local_custom_collection_override(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path)
        result = kb_storage.add_local(str(folder), collection="my-notes")
        assert result.is_ok
        assert result.value["name"] == "my-notes"

    def test_add_local_config_shape(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path)
        result = kb_storage.add_local(str(folder), tags=["docs"], description="local notes")
        assert result.is_ok

        config = kb_storage._load_config()
        entry = config["repos"]["notes"]
        assert entry["source_type"] == "local"
        assert entry["url"] == f"path://{folder.resolve()}"
        # the rest mirrors add_repo entries; "mask" added by P2-H (H-4) — was
        # pinned without it, which precluded mask-aware reindex (find_changed_docs)
        assert set(entry.keys()) == {"url", "source_type", "tags", "description", "mask", "files_indexed", "total_chunks"}
        assert entry["mask"] == "**/*.md"
        assert entry["tags"] == ["docs"]
        assert entry["description"] == "local notes"

    def test_add_local_missing_dir_is_err(self, kb_storage: Storage, tmp_path) -> None:
        result = kb_storage.add_local(str(tmp_path / "does-not-exist"))
        assert isinstance(result, Err), result
        assert "exist" in result.error.lower()

    def test_add_local_file_not_dir_is_err(self, kb_storage: Storage, tmp_path) -> None:
        target = tmp_path / "a-file.md"
        target.write_text("x")
        result = kb_storage.add_local(str(target))
        assert isinstance(result, Err), result
        assert "directory" in result.error.lower()

    def test_add_local_invalid_mask_is_err(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path)
        result = kb_storage.add_local(str(folder), mask="../../etc")
        assert isinstance(result, Err), result
        assert "mask" in result.error.lower()

    def test_add_local_invalid_collection_is_err(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path)
        result = kb_storage.add_local(str(folder), collection="bad name!")
        assert isinstance(result, Err), result
        assert "collection" in result.error.lower()

    def test_add_local_default_name_validated_overridable(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path, name="my notes")  # space -> invalid collection name
        bad = kb_storage.add_local(str(folder))
        assert isinstance(bad, Err), bad
        assert "collection" in bad.error.lower()

        good = kb_storage.add_local(str(folder), collection="my-notes")
        assert good.is_ok


# ── incremental reindex for local collections ──────────────────────────────


class TestLocalIncremental:
    def test_local_reindex_resolves_dir_from_config(self, kb_storage: Storage, tmp_path) -> None:
        folder = _make_folder(tmp_path, files={"intro.md": MD_INTRO})
        added = kb_storage.add_local(str(folder))
        assert added.is_ok

        # the collection lives OUTSIDE repos_dir — resolution must come from config
        assert not (kb_storage.repos_dir / "notes").exists()

        (folder / "intro.md").write_text(MD_QUANTUM)
        changed = kb_storage.find_changed_docs("notes")
        assert changed, "modified file must be reported"
        assert changed[0]["file_path"] == "intro.md"
        assert changed[0]["status"] == "changed"

        reindexed = kb_storage.reindex_collection("notes")
        assert reindexed.is_ok, reindexed
        assert reindexed.value >= 1

        found = kb_storage.search("quantum entanglement")
        assert found.is_ok
        assert any(r["file_path"] == "intro.md" for r in found.value), found.value


# ── kb_index routing ────────────────────────────────────────────────────────


class TestKbIndexRouting:
    async def test_path_scheme_routes_local(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        folder = _make_folder(tmp_path)
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(f"path://{folder}")
        assert result.get("name") == "notes", result
        assert result["files_indexed"] == 2

        config = kb_storage._load_config()
        assert config["repos"]["notes"]["source_type"] == "local"

    async def test_plain_existing_dir_routes_local(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        folder = _make_folder(tmp_path)
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(str(folder))
        assert result.get("name") == "notes", result

    async def test_github_url_parity_with_kb_add_repo(self, tmp_path) -> None:
        from server import kb_add_repo, kb_index

        url = "https://github.com/test/test-repo"

        storage_a = Storage(tmp_path / "a")
        _prepare_github_repo(storage_a)
        with patch("server._get_storage", return_value=storage_a), _mocked_clone():
            via_add_repo = await kb_add_repo(url, description="Test repo")

        storage_b = Storage(tmp_path / "b")
        _prepare_github_repo(storage_b)
        with patch("server._get_storage", return_value=storage_b), _mocked_clone():
            via_index = await kb_index(url, description="Test repo")

        assert via_add_repo.get("name") == "test-repo", via_add_repo
        assert via_index == via_add_repo

    async def test_github_scheme_maps_to_github_url(self, tmp_path) -> None:
        from server import kb_index

        storage = Storage(tmp_path / "a")
        _prepare_github_repo(storage)
        with patch("server._get_storage", return_value=storage), _mocked_clone():
            result = await kb_index("github://test/test-repo")
        assert result.get("name") == "test-repo", result

    async def test_type_override_local_on_git_url(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("https://github.com/test/test-repo", type="local")
        assert "error" in result, result
        # local path validation ran instead of the github route
        assert "path" in result["error"].lower()
        assert "test-repo" not in kb_storage._load_config().get("repos", {})

    async def test_type_override_github_on_existing_dir(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        folder = _make_folder(tmp_path)
        with patch("server._get_storage", return_value=kb_storage), _mocked_clone():
            result = await kb_index(str(folder), type="github")
        assert "error" in result, result
        # github URL validation ran instead of local indexing
        assert "url" in result["error"].lower()
        assert "notes" not in kb_storage._load_config().get("repos", {})

    async def test_unknown_type_is_err_listing_kinds(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        folder = _make_folder(tmp_path)
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(str(folder), type="bogus")
        assert "error" in result, result
        assert "local" in result["error"].lower()
        assert "github" in result["error"].lower()

    async def test_unknown_source_is_err_listing_kinds(self, kb_storage: Storage) -> None:
        from server import kb_index

        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index("completely-unknown-source")
        assert "error" in result, result
        assert "local" in result["error"].lower()
        assert "github" in result["error"].lower()

    async def test_collection_override_local(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        folder = _make_folder(tmp_path)
        with patch("server._get_storage", return_value=kb_storage):
            result = await kb_index(str(folder), collection="custom-name")
        assert result.get("name") == "custom-name", result

    async def test_collection_override_rejected_for_github(self, kb_storage: Storage, tmp_path) -> None:
        from server import kb_index

        storage = kb_storage
        _prepare_github_repo(storage)
        with patch("server._get_storage", return_value=storage), _mocked_clone():
            result = await kb_index("https://github.com/test/test-repo", collection="custom")
        assert "error" in result, result
        assert "local" in result["error"].lower()


# ── source argument aliases (MCP layer) ────────────────────────────────────


class TestKbIndexAliases:
    @pytest.mark.parametrize("alias", ["url", "path", "folder"])
    async def test_source_alias_resolves(self, kb_storage: Storage, tmp_path, alias: str) -> None:
        folder = _make_folder(tmp_path)
        with patch("server._get_storage", return_value=kb_storage):
            payloads = await _mcp_call("kb_index", {alias: str(folder)})
        assert payloads[0].get("name") == "notes", payloads

    async def test_schema_exposes_source_only(self) -> None:
        from server import mcp

        tools = await mcp.list_tools()
        schema = next(t.inputSchema for t in tools if t.name == "kb_index")
        assert "source" in schema["properties"]
        assert not {"url", "path", "folder"} & schema["properties"].keys()


# ── config shape: github entries unchanged ─────────────────────────────────


class TestGithubConfigShape:
    def test_github_entry_has_no_source_type(self, kb_storage: Storage) -> None:
        _prepare_github_repo(kb_storage)
        with _mocked_clone():
            result = kb_storage.add_repo("https://github.com/test/test-repo")
        assert result.is_ok

        entry = kb_storage._load_config()["repos"]["test-repo"]
        # "mask" added by P2-H (H-4) — was pinned without it, which precluded
        # mask-aware reindex (find_changed_docs); github entries stay
        # source_type-less
        assert set(entry.keys()) == {"url", "tags", "description", "mask", "files_indexed", "total_chunks"}
        assert entry["mask"] == "**/*.md"
        assert "source_type" not in entry
        assert entry["url"] == "https://github.com/test/test-repo"
