"""Git Sync for DocsHaven — compressed chunks for PC <-> VPS sync.

Pattern from engram: each sync creates a NEW chunk (never modifies old ones).
No merge conflicts. Manifest tracks all chunks.
"""

import gzip
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from storage import Storage

CHUNKS_DIR = "chunks"
MANIFEST_FILE = "manifest.json"
WORK_DB = "docshaven.db"  # gitignored


class ChunkEntry(BaseModel):
    """Single chunk entry in manifest."""

    id: str  # SHA-256 prefix (16 hex chars)
    created_by: str
    created_at: str
    collections: int
    documents: int


class Manifest(BaseModel):
    """Index of all synced chunks."""

    version: int = 1
    chunks: list[ChunkEntry] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {"version": self.version, "chunks": [c.model_dump() for c in self.chunks]}

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        m = cls(version=d.get("version", 1))
        m.chunks = [ChunkEntry(**c) for c in d.get("chunks", [])]
        return m

    @classmethod
    def from_file(cls, path: Path) -> "Manifest":
        """Load manifest from disk, return empty if missing."""
        if not path.exists():
            return cls()
        with open(path) as f:
            return cls.from_dict(json.load(f))


class Syncer:
    """Handle compressed chunk sync between PC and VPS."""

    def __init__(self, sync_dir: Path):
        self.sync_dir = sync_dir
        self.chunks_dir = sync_dir / CHUNKS_DIR
        self.manifest_path = sync_dir / MANIFEST_FILE
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def export(self, collections_data: dict, created_by: str = "unknown") -> dict:
        """Export collections data as a compressed chunk.

        Args:
            collections_data: {collection_name: [documents]}
            created_by: Username or machine identifier

        Returns:
            {chunk_id, collections, documents, isEmpty}
        """
        manifest = Manifest.from_file(self.manifest_path)
        created_by = created_by or "unknown"

        # Build chunk content
        chunk: dict = {
            "collections": {},
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        total_docs = 0
        for name, docs in collections_data.items():
            chunk["collections"][name] = docs
            total_docs += len(docs)

        if total_docs == 0:
            return {"isEmpty": True}

        # Serialize and compress
        chunk_json = json.dumps(chunk, ensure_ascii=False).encode()
        chunk_id = hashlib.sha256(chunk_json).hexdigest()[:16]

        # Check if already exists
        known = {c.id for c in manifest.chunks}
        if chunk_id in known:
            return {"isEmpty": True, "duplicate": True}

        # Write compressed chunk
        chunk_path = self.chunks_dir / f"{chunk_id}.json.gz"
        with gzip.open(chunk_path, "wb") as f:
            f.write(chunk_json)

        # Update manifest
        entry = ChunkEntry(
            id=chunk_id,
            created_by=created_by,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            collections=len(collections_data),
            documents=total_docs,
        )
        manifest.chunks.append(entry)
        self._write_manifest(manifest)

        return {
            "chunk_id": chunk_id,
            "collections": len(collections_data),
            "documents": total_docs,
            "isEmpty": False,
        }

    def _import_chunk_data(self, chunk_data: dict, storage) -> tuple[int, int]:
        """Import a single chunk's data into storage. Returns (collections, docs) counts."""
        collections = chunk_data.get("collections", {})
        if storage is None:
            return len(collections), sum(len(d) for d in collections.values())

        documents = []
        for collection_name, docs in collections.items():
            for doc in docs:
                if isinstance(doc, dict):
                    documents.append(
                        {
                            "collection": collection_name,
                            "path": doc.get("path", ""),
                            "content": doc.get("content", ""),
                            "title": doc.get("title", ""),
                        }
                    )
        storage.bulk_insert(documents)
        return len(collections), sum(len(d) for d in collections.values())

    def import_chunks(self, storage: "Storage | None" = None) -> dict:
        """Import all chunks not yet applied.

        Args:
            storage: Optional Storage instance to import documents into.

        Returns:
            {chunks_imported, collections_imported, documents_imported, chunks_skipped}
        """
        manifest = Manifest.from_file(self.manifest_path)
        if not manifest.chunks:
            return {"chunks_imported": 0}

        result = {
            "chunks_imported": 0,
            "collections_imported": 0,
            "documents_imported": 0,
            "chunks_skipped": 0,
        }

        for entry in manifest.chunks:
            chunk_path = self.chunks_dir / f"{entry.id}.json.gz"
            if not chunk_path.exists():
                result["chunks_skipped"] += 1
                continue

            # Guard against oversized chunks (max 10MB uncompressed)
            if chunk_path.stat().st_size > 10 * 1024 * 1024:
                result["chunks_skipped"] += 1
                continue

            # Check if this chunk was already imported (by chunk_id in collection name)
            if storage is not None:
                conn = storage._get_conn()
                try:
                    existing = conn.execute(
                        "SELECT COUNT(*) FROM documents WHERE collection = ?",
                        (f"sync_{entry.id}",),
                    ).fetchone()[0]
                    if existing > 0:
                        result["chunks_skipped"] += 1
                        continue
                except sqlite3.Error:
                    pass

            with gzip.open(chunk_path, "rb") as f:
                chunk_data = json.loads(f.read())

            n_collections, n_docs = self._import_chunk_data(chunk_data, storage)
            result["chunks_imported"] += 1
            result["collections_imported"] += n_collections
            result["documents_imported"] += n_docs

        return result

    def status(self) -> dict:
        """Get sync status."""
        manifest = Manifest.from_file(self.manifest_path)
        local_chunks = len(manifest.chunks)

        # Count actual chunk files
        actual_files = len(list(self.chunks_dir.glob("*.json.gz")))

        return {
            "local_chunks": local_chunks,
            "chunk_files": actual_files,
            "manifest_size": self.manifest_path.stat().st_size if self.manifest_path.exists() else 0,
        }

    def _write_manifest(self, manifest: Manifest) -> None:
        with open(self.manifest_path, "w") as f:
            json.dump(manifest.to_dict(), f, indent=2)


def get_username() -> str:
    """Get current username for chunk attribution."""
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
