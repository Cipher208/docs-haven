# Git Sync Examples

## Export (Machine A)

```python
from sync import Syncer
from pathlib import Path

syncer = Syncer(Path.home() / ".docshaven-sync")

# Export all collections as a compressed chunk
result = syncer.export(
    collections_data={"fastapi": docs, "sqlalchemy": docs},
    created_by="alice",
)
print(f"Exported {result['documents']} documents in chunk {result['chunk_id']}")
```

## Import (Machine B)

```python
# Import chunks from sync directory
result = syncer.import_chunks()
print(f"Imported {result['chunks_imported']} chunks, {result['documents_imported']} documents")
```

## Status

```python
status = syncer.status()
print(f"Chunks: {status['local_chunks']}, Size: {status['manifest_size']} bytes")
```

## How It Works

1. Each export creates a NEW `.jsonl.gz` chunk (never modifies old ones)
2. Manifest tracks all chunks (small, merge-friendly)
3. Import reads chunks and applies new data
4. No merge conflicts — each machine creates independent chunks

## Setup for Multi-Machine

1. Create a shared git repo:
   ```bash
   mkdir docshaven-sync && cd docshaven-sync
   git init
   ```

2. Configure syncer to use this repo:
   ```python
   syncer = Syncer(Path("/path/to/docshaven-sync"))
   ```

3. On each machine, run `git pull` before import and `git push` after export.
