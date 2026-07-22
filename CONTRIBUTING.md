# Contributing to DocsHaven

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/Cipher208/docs-haven.git
cd docs-haven
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

Or with uv (recommended):
```bash
uv sync --extra dev
```

## Running Tests

```bash
pytest tests/ -v                    # run all tests
pytest tests/ --cov=. --cov-report=term-missing  # with coverage
```

## Code Quality

```bash
ruff check src/ tests/              # lint
ruff format src/ tests/             # format
mypy src/ --ignore-missing-imports  # type check
```

## Branch Naming

- `feature/description` — new features
- `fix/description` — bug fixes
- `docs/description` — documentation changes
- `refactor/description` — code refactoring

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new tool for X
fix: resolve Y bug in Z
docs: update README with examples
refactor: extract helper function
test: add tests for new feature
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`pytest tests/ -v`)
5. Run linting (`ruff check src/ tests/`)
6. Run type checking (`mypy src/ --ignore-missing-imports`)
7. Commit your changes with conventional commit message
8. Push to the branch (`git push origin feature/amazing-feature`)
9. Open a Pull Request with a clear description

## Code Style

- Use ruff for linting and formatting (line-length: 150)
- Follow PEP 8
- Add type hints to all public functions
- Add docstrings to public functions
- Keep functions focused and small
- Use Pydantic models for data structures

## Testing Guidelines

- Write tests for all new functionality
- Aim for >80% coverage on new code
- Use `pytest` fixtures for common test data
- Use `pytest-asyncio` for async tests
- Test both success and error paths

## Reporting Issues

Use the [GitHub issue tracker](https://github.com/Cipher208/docs-haven/issues) to report bugs or request features.

### Security Issues

For security vulnerabilities, please use [GitHub Private Vulnerability Reporting](https://github.com/Cipher208/docs-haven/security) instead of opening a public issue.
