"""Tests for Collection Templates."""

from __future__ import annotations

from pathlib import Path

from templates import list_templates, get_template, apply_template
from storage import Storage


class TestTemplates:
    def test_list_templates(self):
        templates = list_templates()
        assert len(templates) > 0
        names = [t["name"] for t in templates]
        assert "python-docs" in names
        assert "api-docs" in names
        assert "wiki" in names

    def test_get_template(self):
        t = get_template("python-docs")
        assert t is not None
        assert t.name == "python-docs"
        assert t.domain == "guide"

    def test_get_template_not_found(self):
        t = get_template("nonexistent")
        assert t is None

    def test_apply_template(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = apply_template(storage, "python-docs")
        assert result["status"] == "applied"
        assert result["collection"] == "python-docs"

    def test_apply_template_not_found(self, tmp_path: Path):
        storage = Storage(tmp_path)
        result = apply_template(storage, "nonexistent")
        assert "error" in result
