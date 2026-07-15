# Search Examples

## Basic Search

```python
from storage import Storage
from pathlib import Path

storage = Storage(Path.home() / ".docshaven")

# Search across all collections
results = storage.search("dependency injection")
for r in results:
    print(f"{r['score']:.2f} {r['title']}")
```

## Filtered Search

```python
# Search within specific collections
results = storage.search(
    "async",
    collections=["fastapi", "sqlalchemy"],
    limit=5,
)
```

## Strategy Selection

```python
# Auto strategy (recommended)
results = storage.search("fastapi middleware", strategy="auto")

# Force FTS-only (faster, good for short queries)
results = storage.search("fastapi", strategy="fts")

# Force hybrid (better recall, slower)
results = storage.search("how to use dependency injection", strategy="hybrid")
```

## URI Search

```python
from uri import URIRouter

router = URIRouter(storage)

# Search within a URI scope
results = router.search_by_uri("core://fastapi", limit=5)
```

## Conflict Detection

```python
from conflicts import ConflictDetector

detector = ConflictDetector(storage)
result = detector.detect(
    title="FastAPI dependency injection",
    content="How to use Depends()...",
)

if result.has_conflicts:
    print(f"Found {len(result.candidates)} similar documents:")
    for c in result.candidates:
        print(f"  - {c['title']} (score: {c['score']})")
```
