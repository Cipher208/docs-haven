# Search Examples

## Basic Search

```python
from storage import Storage
from pathlib import Path

storage = Storage(Path.home() / ".docshaven")

# Search across all collections — returns Ok[list[dict]] | Err
result = storage.search("dependency injection")
if result.is_ok():
    for r in result.value:
        print(f"{r['score']:.2f} {r['title']}")
else:
    print(f"Error: {result.error}")
```

## Filtered Search

```python
# Search within specific collections
result = storage.search(
    "async",
    collections=["fastapi", "sqlalchemy"],
    limit=5,
)
if result.is_ok():
    for r in result.value:
        print(f"{r['score']:.2f} {r['title']}")
```

## With Score Explanation

```python
# Get scoring breakdown for each result
result = storage.search("fastapi", explain=True)
if result.is_ok():
    for r in result.value:
        print(f"{r['title']}: base={r['explain']['base_score']}, boost={r['explain']['type_boost']}")
```

## Strategy Selection

```python
# Auto strategy (recommended)
result = storage.search("fastapi middleware", strategy="auto")

# Force FTS-only (faster, good for short queries)
result = storage.search("fastapi", strategy="fts")

# Force hybrid (better recall, slower)
result = storage.search("how to use dependency injection", strategy="hybrid")
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
