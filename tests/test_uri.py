"""Tests for URI Routing."""

import pytest

from uri import URI, VALID_DOMAINS


class TestURI:
    def test_parse_valid(self):
        u = URI.parse("core://fastapi/dependencies")
        assert u.domain == "core"
        assert u.path == "fastapi/dependencies"
        assert str(u) == "core://fastapi/dependencies"

    def test_parse_with_slashes(self):
        u = URI.parse("guide://testing//pytest///")
        assert u.domain == "guide"
        assert u.path == "testing/pytest"

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            URI.parse("not-a-uri")

    def test_parse_unknown_domain(self):
        with pytest.raises(ValueError):
            URI.parse("unknown://path")

    def test_create(self):
        u = URI.create("ref", "sqlalchemy/orm")
        assert u.domain == "ref"
        assert u.path == "sqlalchemy/orm"

    def test_to_collection(self):
        u = URI.parse("core://fastapi/deps")
        assert u.to_collection() == "core__fastapi"

    def test_to_collection_single(self):
        u = URI.parse("core://fastapi")
        assert u.to_collection() == "core__fastapi"

    def test_equality(self):
        u1 = URI.parse("core://fastapi/deps")
        u2 = URI.parse("core://fastapi/deps")
        assert u1 == u2

    def test_hash(self):
        u1 = URI.parse("core://fastapi/deps")
        u2 = URI.parse("core://fastapi/deps")
        assert hash(u1) == hash(u2)

    def test_all_domains_valid(self):
        for domain in VALID_DOMAINS:
            u = URI.create(domain, "test")
            assert u.domain == domain
