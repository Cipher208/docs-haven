"""Tests for Import Guard."""

from __future__ import annotations

from pathlib import Path

from import_guard import check_imports


class TestImportGuard:
    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = check_imports(str(f), str(tmp_path))
        assert result["status"] == "ok"
        assert result["total_imports"] == 0

    def test_python_imports(self, tmp_path: Path):
        f = tmp_path / "app.py"
        f.write_text("import os\nfrom pathlib import Path")
        result = check_imports(str(f), str(tmp_path))
        assert result["status"] == "ok"
        assert "os" in result["imports"]

    def test_phantom_import(self, tmp_path: Path):
        f = tmp_path / "app.py"
        f.write_text("import nonexistent_module_xyz")
        result = check_imports(str(f), str(tmp_path))
        assert "nonexistent_module_xyz" in result["phantom_imports"]

    def test_local_module_not_phantom(self, tmp_path: Path):
        (tmp_path / "mymodule.py").write_text("x = 1")
        f = tmp_path / "app.py"
        f.write_text("import mymodule")
        result = check_imports(str(f), str(tmp_path))
        assert "mymodule" not in result["phantom_imports"]

    def test_missing_file(self, tmp_path: Path):
        result = check_imports(str(tmp_path / "nonexistent.py"), str(tmp_path))
        assert result["status"] == "file_not_found"

    def test_js_imports(self, tmp_path: Path):
        f = tmp_path / "app.js"
        f.write_text('import React from "react"')
        result = check_imports(str(f), str(tmp_path))
        assert "react" in result["imports"]

    def test_counts(self, tmp_path: Path):
        f = tmp_path / "app.py"
        f.write_text("import os\nimport sys\nimport nonexistent")
        result = check_imports(str(f), str(tmp_path))
        assert result["total_imports"] == 3
        # os, sys, nonexistent are all phantom (no local .py file)
        assert result["phantom_count"] == 3
