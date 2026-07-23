"""Import Guard — detect phantom imports in knowledge base documents.

Checks if import statements in indexed documents reference real files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from storage import Storage

_STDLIB_MODULES = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()

# Python import patterns
_PYTHON_IMPORT = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
    re.MULTILINE,
)

# JS/TS import patterns
_JS_IMPORT = re.compile(
    r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|"""
    r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)|"""
    r"""import\s+['"]([^'"]+)['"])""",
    re.MULTILINE,
)


def _module_exists_locally(module_name: str, root: Path) -> bool:
    return (
        (root / f"{module_name}.py").exists()
        or (root / module_name / "__init__.py").exists()
        or (root / "src" / f"{module_name}.py").exists()
        or (root / "src" / module_name / "__init__.py").exists()
    )


def _check_python_imports(content: str, root: Path) -> tuple[list[str], list[str]]:
    imports = []
    phantom = []
    for match in _PYTHON_IMPORT.finditer(content):
        module = match.group(1) or match.group(2)
        if module:
            imports.append(module)
            parts = module.split(".")
            module_name = parts[0]
            if module_name in _STDLIB_MODULES:
                continue
            if not _module_exists_locally(module_name, root):
                phantom.append(module)
    return imports, phantom


def _resolve_js_package(spec: str) -> str:
    """Resolve JS package name from import spec."""
    if spec.startswith(".") or spec.startswith("/"):
        return ""  # relative/local — skip
    if spec.startswith("@"):
        return "/".join(spec.split("/")[:2])
    return spec.split("/")[0]


def _check_js_imports(content: str, root: Path) -> tuple[list[str], list[str]]:
    imports = []
    phantom = []
    for match in _JS_IMPORT.finditer(content):
        spec = match.group(1) or match.group(2) or match.group(3)
        if not spec:
            continue
        imports.append(spec)
        pkg = _resolve_js_package(spec)
        if pkg and not (root / "node_modules" / pkg).exists():
            phantom.append(spec)
    return imports, phantom


def check_imports(file_path: str, repo_root: str = ".", storage: Storage | None = None) -> dict:
    """Check if imports in a file reference real modules.

    Args:
        file_path: Path to the file to check
        repo_root: Repository root directory
        storage: Optional Storage instance for indexed docs

    Returns:
        {imports: list, phantom_imports: list, status: str}
    """
    root = Path(repo_root)
    full_path = Path(file_path)

    if not full_path.exists():
        return {"imports": [], "phantom_imports": [], "status": "file_not_found"}

    try:
        content = full_path.read_text(errors="ignore")
    except OSError:
        return {"imports": [], "phantom_imports": [], "status": "read_error"}

    if full_path.suffix in (".py",):
        imports, phantom = _check_python_imports(content, root)
    elif full_path.suffix in (".js", ".ts", ".jsx", ".tsx"):
        imports, phantom = _check_js_imports(content, root)
    else:
        imports, phantom = [], []

    return {
        "imports": imports,
        "phantom_imports": phantom,
        "status": "ok",
        "total_imports": len(imports),
        "phantom_count": len(phantom),
    }
