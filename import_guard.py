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
            local_path = root / f"{module_name}.py"
            local_pkg = root / module_name / "__init__.py"
            src_path = root / "src" / f"{module_name}.py"
            src_pkg = root / "src" / module_name / "__init__.py"
            if not (local_path.exists() or local_pkg.exists() or src_path.exists() or src_pkg.exists()):
                phantom.append(module)
    return imports, phantom


def _check_js_imports(content: str, root: Path) -> tuple[list[str], list[str]]:
    imports = []
    phantom = []
    for match in _JS_IMPORT.finditer(content):
        spec = match.group(1) or match.group(2) or match.group(3)
        if spec:
            imports.append(spec)
            if not spec.startswith(".") and not spec.startswith("/"):
                pkg = spec.split("/")[0] if not spec.startswith("@") else "/".join(spec.split("/")[:2])
                nm_path = root / "node_modules" / pkg
                if not nm_path.exists():
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
