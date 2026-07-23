"""Tests for LLM Argument Aliasing."""

from __future__ import annotations

from alias import alias_args


class TestAliasArgs:
    def test_no_aliases(self):
        args = {"query": "test", "limit": 5}
        result = alias_args("kb_search", args)
        assert result == {"query": "test", "limit": 5}

    def test_single_alias(self):
        args = {"userQuery": "test"}
        result = alias_args("kb_search", args)
        assert result == {"query": "test"}

    def test_multiple_aliases(self):
        args = {"userQuery": "test", "maxResults": 10, "verbose": True}
        result = alias_args("kb_search", args)
        assert result == {"query": "test", "limit": 10, "explain": True}

    def test_unknown_params_preserved(self):
        args = {"query": "test", "custom_param": "value"}
        result = alias_args("kb_search", args)
        assert result == {"query": "test", "custom_param": "value"}

    def test_mixed_aliases_and_normal(self):
        args = {"userQuery": "test", "collections": ["core"], "limit": 5}
        result = alias_args("kb_search", args)
        assert result == {"query": "test", "collections": ["core"], "limit": 5}

    def test_kb_add_repo_aliases(self):
        args = {"repoUrl": "https://github.com/test/repo", "desc": "Test repo"}
        result = alias_args("kb_add_repo", args)
        assert result == {"url": "https://github.com/test/repo", "description": "Test repo"}

    def test_kb_context_aliases(self):
        args = {"note": "Test summary", "contextPath": "overview"}
        result = alias_args("kb_context_add", args)
        assert result == {"summary": "Test summary", "path": "overview"}
