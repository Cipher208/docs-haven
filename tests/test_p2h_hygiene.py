"""Anchors for Card P2-H: hygiene wave — six known holes (review ledgers P2-1 + P2-2),
plus the fix-round findings F-a/F-b that the wave itself introduced.

Wave anchors (22): fail-before at the pre-card HEAD (334f2a1) for 16; the
remaining 6 are guards incapable of failing before (the bug masked the
outcome or the behavior was already correct at HEAD):
- H-3 test_modified_file_is_content_changed (github + local kinds) — the
  64-vs-16 over-report made "modified" pass at HEAD by accident
- H-3 test_deleted_file_is_file_deleted (github + local kinds) — the hash
  comparison never ran for missing files (same over-report)
- H-4 test_changed_file_under_mask_still_detected — pre-card detection
  globbed **/* and saw the in-mask change anyway
- H-5 test_path_and_query_case_preserved — no folding at HEAD and none
  wanted for path/query (asserts the fix stays RFC 3986-scoped)

The 16 wave repros:

- H-1: a simulated sqlite3.Error mid-write leaves pending rows that a later
  commit on the same connection persists (reproduced on update_document —
  the F-3/Qwen repro; and on bulk paths)
- H-2: parallel first writes on a fresh Storage surface
  sqlite3.OperationalError('database is locked') from the PRAGMA race
- H-3: check_stale reports content_changed for ANY existing file
  (64-char sha256 vs 16-char stored content_hash)
- H-4: find_changed_docs ignores the collection mask and sweeps dot-dirs
  (.git/config gets indexed on reindex); config entries carry no mask
- H-5: bookmark URLs are not case-folded — the two case variants of one
  link become two rows
- H-6: a whitespace-only note mints text://note-<hash8>-<hash16>
  (hash doubled between title fallback and file_path slug)

Fix-round anchors (2, both fail before the fix):

- F-a: a missing "mask" config key (every pre-card entry) defaulted to
  **/*.md — legacy non-.md collections reported "deleted" wholesale and
  reindex wiped them (also red at the pre-card HEAD via its mask-leak guard)
- F-b: hidden stored rows were invisible on the stored side — pre-card
  .git rows froze as orphans while mask-mismatched rows were purged
  (a wave-introduced regression; green at the pre-card HEAD)
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from result import Err
from storage import Storage

LONG_BODY = "\n\n".join(f"Paragraph {i} about distributed systems " + "x" * 200 for i in range(20))
_LATER_COLL = "zzz-later"  # sorts after every seeded collection in snapshot order


def _hash16(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _seed_doc(storage: Storage, collection: str, path: str, content: str, content_hash: str, title: str = "Doc") -> None:
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO documents (collection, file_path, content, content_hash, title) VALUES (?, ?, ?, ?, ?)",
        (collection, path, content, content_hash, title),
    )
    conn.commit()


class _FlakyConn:
    """Proxy over a real sqlite3 connection that raises sqlite3.OperationalError
    exactly on the Nth write statement (INSERT/UPDATE/DELETE/REPLACE) — the H-1
    simulated mid-write failure. Reads and later writes pass through."""

    def __init__(self, conn: sqlite3.Connection, fail_on_write: int):
        self._conn = conn
        self._fail_on_write = fail_on_write
        self._writes = 0

    def execute(self, sql: str, *args):
        if sql.split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            self._writes += 1
            if self._writes == self._fail_on_write:
                raise sqlite3.OperationalError("simulated mid-write failure (P2-H)")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _snapshot(storage: Storage) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Byte-identity snapshot of every DB table the write paths touch:
    (documents, context_attachments, conflict_judgments)."""
    conn = storage._get_conn()
    docs = conn.execute(
        "SELECT collection, file_path, chunk_index, total_chunks, content, content_hash, extension, title, context, "
        "retrieval_count, created_at FROM documents ORDER BY collection, file_path, chunk_index"
    ).fetchall()
    ctx = conn.execute("SELECT collection, path, summary FROM context_attachments ORDER BY collection, path").fetchall()
    judge = conn.execute("SELECT new_id, candidate_id, judgment FROM conflict_judgments ORDER BY id").fetchall()
    return ([tuple(r) for r in docs], [tuple(r) for r in ctx], [tuple(r) for r in judge])


def _later_rows(conn: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT collection, file_path, chunk_index, total_chunks, content, content_hash, extension, title, context, "
            "retrieval_count, created_at FROM documents WHERE collection = ?",
            (_LATER_COLL,),
        ).fetchall()
    ]


def _assert_intact_after_later_write(storage: Storage, conn: sqlite3.Connection, before: tuple) -> None:
    """The Qwen repro step: a later successful write on the same connection must
    commit ONLY itself — never whatever the failed call left pending."""
    assert _snapshot(storage) == before, "mid-write failure left pending rows visible on the connection"
    storage.bulk_insert_raw(_LATER_COLL, "later.md", "later write body", "Later").unwrap()
    docs, ctx, judge = _snapshot(storage)
    assert docs == before[0] + _later_rows(conn), "a later write resurrected the failed call's partial rows"
    assert ctx == before[1] and judge == before[2], "a later write resurrected partial non-document rows"


# ── H-1 — transactional rollback for multi-statement writes ─────────────────


class TestH1Rollback:
    def test_update_document_failure_leaves_doc_intact(self, tmp_path: Path, monkeypatch) -> None:
        """F-3/Qwen repro: DELETE ran, INSERT failed — HEAD leaves the DELETE
        pending and a later commit persists it (half-deleted doc)."""
        storage = Storage(tmp_path / "data")
        _seed_doc(storage, "test", "doc.md", "original content", "seedhash00000000")
        conn = storage._get_conn()
        before = _snapshot(storage)

        monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=2))
        result = storage.update_document("doc.md", "new content", collection="test")
        assert isinstance(result, Err), result

        monkeypatch.setattr(storage, "_get_conn", lambda: conn)
        _assert_intact_after_later_write(storage, conn, before)
        storage.close()

    def test_update_document_multichunk_failure_no_partial_chunks(self, tmp_path: Path, monkeypatch) -> None:
        """Multi-chunk replace: DELETE + chunk 0 INSERT succeed, chunk 1 fails —
        HEAD commits a half-replaced document (partial chunks)."""
        storage = Storage(tmp_path / "data")
        _seed_doc(storage, "test", "doc.md", "original content", "seedhash00000000")
        conn = storage._get_conn()
        before = _snapshot(storage)

        monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=3))
        result = storage.update_document("doc.md", LONG_BODY, collection="test")
        assert isinstance(result, Err), result

        monkeypatch.setattr(storage, "_get_conn", lambda: conn)
        _assert_intact_after_later_write(storage, conn, before)
        storage.close()

    def test_bulk_insert_failure_persists_nothing(self, tmp_path: Path, monkeypatch) -> None:
        storage = Storage(tmp_path / "data")
        _seed_doc(storage, "bulk", "a.md", "content a", "hasha00000000000", title="A")
        _seed_doc(storage, "bulk", "b.md", "content b", "hashb00000000000", title="B")
        conn = storage._get_conn()
        before = _snapshot(storage)

        # writes: DELETE a (1), INSERT a (2), DELETE b (3) -> fail on 3
        monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=3))
        result = storage.bulk_insert(
            [
                {"collection": "bulk", "path": "a.md", "content": "replaced a", "title": "A2"},
                {"collection": "bulk", "path": "b.md", "content": "replaced b", "title": "B2"},
            ]
        )
        assert isinstance(result, Err), result

        monkeypatch.setattr(storage, "_get_conn", lambda: conn)
        _assert_intact_after_later_write(storage, conn, before)
        storage.close()

    def test_remove_collection_failure_persists_nothing(self, tmp_path: Path, monkeypatch) -> None:
        storage = Storage(tmp_path / "data")
        _seed_doc(storage, "gone", "doc.md", "content", "hashg00000000000")
        storage.add_context("gone", "doc.md", "summary").unwrap()
        storage.record_judgment("gone/doc.md", "gone/other.md", "keep").unwrap()
        conn = storage._get_conn()
        before = _snapshot(storage)

        # writes: DELETE judgments (1), DELETE contexts (2), DELETE docs (3) -> fail on 3
        monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=3))
        result = storage.remove_collection("gone")
        assert isinstance(result, Err), result

        monkeypatch.setattr(storage, "_get_conn", lambda: conn)
        _assert_intact_after_later_write(storage, conn, before)
        storage.close()

    def test_rename_collection_failure_persists_nothing(self, tmp_path: Path, monkeypatch) -> None:
        storage = Storage(tmp_path / "data")
        _seed_doc(storage, "alpha", "doc.md", "content", "hasha00000000000")
        storage.add_context("alpha", "doc.md", "summary").unwrap()
        storage.record_judgment("alpha/doc.md", "alpha/other.md", "keep").unwrap()
        conn = storage._get_conn()
        before = _snapshot(storage)

        # writes: UPDATE docs (1), UPDATE contexts (2), UPDATE judgments new_id (3),
        # UPDATE judgments candidate_id (4) -> fail on 4
        monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=4))
        result = storage.rename_collection("alpha", "beta")
        assert isinstance(result, Err), result

        monkeypatch.setattr(storage, "_get_conn", lambda: conn)
        _assert_intact_after_later_write(storage, conn, before)
        storage.close()

    def test_reindex_failure_leaves_no_half_deleted_doc(self, tmp_path: Path, monkeypatch) -> None:
        """The two-commit window: HEAD commits the delete phase first, then a
        failed re-insert leaves the document half-deleted (old chunks gone,
        new never added)."""
        storage = Storage(tmp_path / "data")
        repo_dir = storage.repos_dir / "test"
        repo_dir.mkdir()
        (repo_dir / "doc.md").write_text("changed content")
        _seed_doc(storage, "test", "doc.md", "stale content", "stalehash0000000")
        conn = storage._get_conn()
        before = _snapshot(storage)

        # writes: DELETE doc chunks (1), INSERT re-index chunk (2) -> fail on 2
        monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=2))
        result = storage.reindex_collection("test")
        assert result.is_err, result  # the failed call must not report success

        monkeypatch.setattr(storage, "_get_conn", lambda: conn)
        _assert_intact_after_later_write(storage, conn, before)
        storage.close()

    def test_index_files_failure_persists_nothing(self, tmp_path: Path, monkeypatch) -> None:
        """add-index batch: file 1's INSERT succeeds, file 2's INSERT fails —
        HEAD's per-file skip then commits file 1 (partial progress persisted)."""
        storage = Storage(tmp_path / "data")
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "a.md").write_text("alpha content")
        (repo_dir / "b.md").write_text("beta content")
        conn = storage._get_conn()
        before = _snapshot(storage)

        monkeypatch.setattr(storage, "_get_conn", lambda: _FlakyConn(conn, fail_on_write=2))
        with pytest.raises(sqlite3.Error):
            storage._index_files("docs", [repo_dir / "a.md", repo_dir / "b.md"], repo_dir, None)

        monkeypatch.setattr(storage, "_get_conn", lambda: conn)
        _assert_intact_after_later_write(storage, conn, before)
        storage.close()


# ── H-2 — cold-start concurrent 'database is locked' (F-5) ──────────────────


class TestH2ColdStartConcurrency:
    def test_parallel_first_writes_all_succeed(self, tmp_path: Path) -> None:
        """Barrier-synchronized first writes on a FRESH Storage (BH-9 smoke
        style, bounded 10s). On HEAD the PRAGMA setup races before the
        busy_timeout is set and surfaces OperationalError('database is locked').
        Run 3x for stability."""
        for run in range(3):
            storage = Storage(tmp_path / f"run{run}")
            n = 8
            barrier = threading.Barrier(n, timeout=10)
            errors: list[BaseException] = []
            results: list = []

            def worker(i: int) -> None:
                try:
                    barrier.wait()
                    results.append(storage.add_text(f"cold start note body {i}", title=f"note-{i}"))
                except BaseException as e:  # the bug surfaces as a raise from _get_conn PRAGMA setup
                    errors.append(e)
                finally:
                    storage.close()  # closes this thread's thread-local connection (Windows file lock)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert all(not t.is_alive() for t in threads), f"run {run}: worker threads must finish (bounded)"
            assert not errors, f"run {run}: cold-start PRAGMA race surfaced: {errors!r}"
            assert len(results) == n, f"run {run}: {len(results)}/{n} writes returned"
            assert all(r.is_ok for r in results), f"run {run}: first writes must all succeed: {[str(r) for r in results if r.is_err]}"
            storage.close()


# ── H-3 — check_stale hash-length bug (P2-1 ledger) ─────────────────────────


def _setup_stale_collection(storage: Storage, tmp_path: Path, kind: str) -> Path:
    """A collection whose source dir holds doc.md, registered as github-style
    (repos_dir/<name>) or local (config entry) — the P2-1 resolver path."""
    if kind == "local":
        folder = tmp_path / "local-src"
        folder.mkdir()
        (folder / "doc.md").write_text("original content")
        storage.add_local(str(folder), collection="docs").unwrap()
        return folder
    repo_dir = storage.repos_dir / "docs"
    repo_dir.mkdir()
    (repo_dir / "doc.md").write_text("original content")
    _seed_doc(storage, "docs", "doc.md", "original content", _hash16("original content"))
    return repo_dir


class TestH3CheckStale:
    @pytest.mark.parametrize("kind", ["github", "local"])
    def test_clean_file_is_clean(self, tmp_path: Path, kind: str) -> None:
        """FAIL-BEFORE: the 64-vs-16 hash comparison reports content_changed
        for a file that exists and is unchanged."""
        storage = Storage(tmp_path / "data")
        _setup_stale_collection(storage, tmp_path, kind)
        result = storage.check_stale("docs")
        assert result.is_ok, result
        assert result.value == [], f"unmodified file must be clean, got {result.value}"
        storage.close()

    @pytest.mark.parametrize("kind", ["github", "local"])
    def test_modified_file_is_content_changed(self, tmp_path: Path, kind: str) -> None:
        storage = Storage(tmp_path / "data")
        folder = _setup_stale_collection(storage, tmp_path, kind)
        (folder / "doc.md").write_text("modified content")
        result = storage.check_stale("docs")
        assert result.is_ok, result
        assert [f["reason"] for f in result.value] == ["content_changed"], result.value
        storage.close()

    @pytest.mark.parametrize("kind", ["github", "local"])
    def test_deleted_file_is_file_deleted(self, tmp_path: Path, kind: str) -> None:
        storage = Storage(tmp_path / "data")
        folder = _setup_stale_collection(storage, tmp_path, kind)
        (folder / "doc.md").unlink()
        result = storage.check_stale("docs")
        assert result.is_ok, result
        assert [f["reason"] for f in result.value] == ["file_deleted"], result.value
        storage.close()


# ── H-4 — find_changed_docs: mask-aware + dot-dir exclusion (P2-1 ledger) ────


class TestH4ChangedDocsMaskAndDotDirs:
    def test_add_local_config_carries_mask(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path / "data")
        folder = tmp_path / "src"
        folder.mkdir()
        (folder / "a.rst").write_text("rst content")
        storage.add_local(str(folder), mask="**/*.rst").unwrap()
        entry = storage._load_config()["repos"]["src"]
        assert entry["mask"] == "**/*.rst", entry
        storage.close()

    def test_add_repo_config_carries_mask(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        storage = Storage(tmp_path / "data")
        repo_dir = storage.repos_dir / "test-repo"
        repo_dir.mkdir()
        (repo_dir / "README.rst").write_text("rst content")
        with patch("storage.subprocess.run", return_value=MagicMock(returncode=0, stderr="")):
            storage.add_repo("https://github.com/test/test-repo", mask="**/*.rst").unwrap()
        entry = storage._load_config()["repos"]["test-repo"]
        assert entry["mask"] == "**/*.rst", entry
        storage.close()

    def test_changed_docs_ignores_files_outside_mask(self, tmp_path: Path) -> None:
        """FAIL-BEFORE: find_changed_docs globs **/* and reports the new .md
        file as added even though the collection mask is **/*.rst."""
        storage = Storage(tmp_path / "data")
        folder = tmp_path / "src"
        folder.mkdir()
        (folder / "a.rst").write_text("rst content")
        storage.add_local(str(folder), mask="**/*.rst").unwrap()
        (folder / "b.md").write_text("markdown that must not join the collection")

        changed = storage.find_changed_docs("src")
        assert all(item["file_path"] != "b.md" for item in changed), changed

        reindexed = storage.reindex_collection("src")
        assert reindexed.is_ok, reindexed
        assert not any(r["path"] == "src/b.md" for r in storage.list_documents("src").unwrap()), "non-mask file indexed on reindex"
        storage.close()

    def test_dot_dir_files_never_reported_nor_indexed(self, tmp_path: Path) -> None:
        """FAIL-BEFORE: .git/config is reported as added by find_changed_docs
        and swept into the index on reindex (VCS metadata is not docs)."""
        storage = Storage(tmp_path / "data")
        folder = tmp_path / "src"
        folder.mkdir()
        (folder / "a.md").write_text("real doc")
        (folder / ".git").mkdir()
        (folder / ".git" / "config").write_text("[core]\nrepositoryformatversion = 0")
        (folder / ".git" / "hooks.md").write_text("# hooks note\nVCS metadata")

        added = storage.add_local(str(folder))
        assert added.is_ok, added
        assert added.value["files_indexed"] == 1, f"dot-dir files must not be indexed: {added.value}"

        (folder / ".git" / "second.md").write_text("more VCS metadata")
        changed = storage.find_changed_docs("src")
        assert all(not any(seg.startswith(".") for seg in item["file_path"].replace("\\", "/").split("/")) for item in changed), changed

        reindexed = storage.reindex_collection("src")
        assert reindexed.is_ok, reindexed
        paths = [r["path"] for r in storage.list_documents("src").unwrap()]
        assert paths == ["src/a.md"], f"VCS metadata indexed: {paths}"
        storage.close()

    def test_changed_file_under_mask_still_detected(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path / "data")
        folder = tmp_path / "src"
        folder.mkdir()
        (folder / "a.rst").write_text("rst content")
        storage.add_local(str(folder), mask="**/*.rst").unwrap()
        (folder / "a.rst").write_text("rst content changed")

        changed = storage.find_changed_docs("src")
        assert [(item["file_path"], item["status"]) for item in changed] == [("a.rst", "changed")], changed
        storage.close()


# ── H-5 — bookmark URL case-folding (P2-2 ledger) ───────────────────────────


class TestH5BookmarkCaseFolding:
    def test_case_variants_upsert_into_one_row(self, tmp_path: Path) -> None:
        """FAIL-BEFORE: https://dup.com/a and HTTPS://DUP.COM/a mint two rows."""
        storage = Storage(tmp_path / "data")
        first = storage.bookmark_upsert("https://dup.com/a", title="first")
        second = storage.bookmark_upsert("HTTPS://DUP.COM/a", title="second")
        assert first.is_ok and second.is_ok, (first, second)
        assert first.value["created"] is True
        assert second.value["created"] is False, "case variants must fold into one row"
        rows = storage.bookmark_list().unwrap()
        assert len(rows) == 1, rows
        assert rows[0]["url"] == "https://dup.com/a", "stored key is the normalized URL"
        assert rows[0]["title"] == "second", "second save updates the same row"
        storage.close()

    def test_path_and_query_case_preserved(self, tmp_path: Path) -> None:
        """RFC 3986: only scheme+host fold — /A and /a are different links."""
        storage = Storage(tmp_path / "data")
        storage.bookmark_upsert("https://dup.com/A?x=1", title="upper path").unwrap()
        storage.bookmark_upsert("https://dup.com/a?x=1", title="lower path").unwrap()
        urls = sorted(r["url"] for r in storage.bookmark_list().unwrap())
        assert urls == ["https://dup.com/A?x=1", "https://dup.com/a?x=1"], urls
        storage.close()


# ── H-6 — whitespace-note path hash doubling (cosmetic) ─────────────────────


class TestH6NotePathHashDoubling:
    def test_whitespace_note_path_is_not_hash_doubled(self, tmp_path: Path) -> None:
        """FAIL-BEFORE: the file_path is text://note-<hash8>-<hash16> — the
        fallback title's hash is embedded again in the slug."""
        storage = Storage(tmp_path / "data")
        content = "   \n  \n"
        result = storage.add_text(content).unwrap()
        assert result["title"] == f"note-{_hash16(content)[:8]}", "display title keeps the short hash"
        assert result["file_path"] == f"text://note-{_hash16(content)}", result["file_path"]
        storage.close()


# ── Fix round — blind-review findings F-a/F-b (regressions of the wave) ─────


class TestFixRoundMaskFallbackAndHiddenRows:
    def test_legacy_entry_without_mask_key_keeps_pre_card_detection(self, tmp_path: Path) -> None:
        """F-a FAIL-BEFORE: find_changed_docs defaults a MISSING "mask" config
        key to **/*.md — but every pre-P2-H entry lacks the key (masks were
        never persisted), so a legacy a.rst+b.md collection reports a.rst
        deleted and reindex wipes it (an all-.rst collection ends at 0 docs).
        A missing key must mean the pre-card **/* glob; new entries always
        persist their mask (add_local/add_repo write it unconditionally)."""
        storage = Storage(tmp_path / "data")
        folder = tmp_path / "src"
        folder.mkdir()
        (folder / "a.rst").write_text("rst content")
        (folder / "b.md").write_text("markdown content")
        _seed_doc(storage, "src", "a.rst", "rst content", _hash16("rst content"))
        _seed_doc(storage, "src", "b.md", "markdown content", _hash16("markdown content"))
        config = storage._load_config()
        config["repos"]["src"] = {  # pre-card shape: masks were never persisted
            "url": f"path://{folder}",
            "source_type": "local",
            "tags": [],
            "description": "",
            "files_indexed": 2,
            "total_chunks": 2,
        }
        storage._save_config(config)

        changed = storage.find_changed_docs("src")
        assert not any(item["status"] == "deleted" for item in changed), f"legacy docs reported deleted: {changed}"
        reindexed = storage.reindex_collection("src")
        assert reindexed.is_ok, reindexed
        paths = sorted(r["path"] for r in storage.list_documents("src").unwrap())
        assert paths == ["src/a.rst", "src/b.md"], f"legacy docs wiped by reindex: {paths}"

        # H-4 stays: a NEW entry's explicit mask still wins — b.md never joins
        added = storage.add_local(str(folder), collection="rst-only", mask="**/*.rst")
        assert added.is_ok, added
        assert added.value["files_indexed"] == 1, f"non-mask file joined the new entry: {added.value}"
        changed_new = storage.find_changed_docs("rst-only")
        assert all(item["file_path"] != "b.md" for item in changed_new), changed_new
        storage.close()

    def test_hidden_stored_rows_reported_deleted_and_purged(self, tmp_path: Path) -> None:
        """F-b FAIL-BEFORE: hidden stored rows were dropped from the `stored`
        side (reindex never removed pre-card .git junk — frozen orphans with
        permanently stale content) while mask-mismatched rows WERE deleted.
        Symmetric now: a hidden stored row behaves exactly like a
        mask-mismatched row — reported deleted, purged on reindex."""
        storage = Storage(tmp_path / "data")
        folder = tmp_path / "src"
        folder.mkdir()
        (folder / "a.md").write_text("real doc")
        storage.add_local(str(folder)).unwrap()
        _seed_doc(storage, "src", ".git/config", "[core]\nrepositoryformatversion = 0", _hash16("junk"))

        changed = storage.find_changed_docs("src")
        assert (".git/config", "deleted") in [(item["file_path"], item["status"]) for item in changed], changed
        reindexed = storage.reindex_collection("src")
        assert reindexed.is_ok, reindexed
        paths = [r["path"] for r in storage.list_documents("src").unwrap()]
        assert paths == ["src/a.md"], f"hidden orphan not purged / normal row lost: {paths}"
        storage.close()
