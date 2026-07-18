# Installation

## From Source

```bash
git clone https://github.com/Cipher208/docs-haven.git
cd docs-haven
pip install -e .
```

## With Test Dependencies

```bash
pip install -e ".[test]"
```

## Requirements

- Python 3.10+
- Requires `mcp[cli]` (installed automatically with pip)

## Verify Installation

```python
from storage import Storage
from pathlib import Path

storage = Storage(Path.home() / ".docshaven")
print(storage.stats())
```
