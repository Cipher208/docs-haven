# Contributing to DocsHaven

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/Cipher208/docs-haven.git
cd docs-haven
pip install -e ".[test]"
```

## Running Tests

```bash
pytest tests/ -v
```

## Code Quality

```bash
ruff check .
ruff format --check .
mypy uri.py storage.py sync.py conflicts.py server.py --ignore-missing-imports
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`pytest tests/ -v`)
5. Run linting (`ruff check .`)
6. Commit your changes (`git commit -m 'Add amazing feature'`)
7. Push to the branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

## Code Style

- Use ruff for linting and formatting
- Follow PEP 8
- Add docstrings to public functions
- Keep functions focused and small

## Reporting Issues

Use the [GitHub issue tracker](https://github.com/Cipher208/docs-haven/issues) to report bugs or request features.
