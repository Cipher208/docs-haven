"""Tests for Result type (Ok/Err) — Pydantic v2 models."""

import pytest

from result import Err, Ok, err, ok


class TestOk:
    def test_is_ok(self):
        assert Ok(value=42).is_ok is True

    def test_is_err(self):
        assert Ok(value=42).is_err is False

    def test_unwrap(self):
        assert Ok(value=42).unwrap() == 42

    def test_value(self):
        assert Ok(value="hello").value == "hello"


class TestErr:
    def test_is_ok(self):
        assert Err(error="fail").is_ok is False

    def test_is_err(self):
        assert Err(error="fail").is_err is True

    def test_unwrap_raises(self):
        with pytest.raises(RuntimeError, match="fail"):
            Err(error="fail").unwrap()

    def test_error(self):
        assert Err(error="fail").error == "fail"

    def test_code_default(self):
        assert Err(error="fail").code == "error"

    def test_code_custom(self):
        assert Err(error="fail", code="not_found").code == "not_found"


class TestConstructors:
    def test_ok_constructor(self):
        r = ok(10)
        assert isinstance(r, Ok)
        assert r.value == 10

    def test_err_constructor(self):
        r = err("oops")
        assert isinstance(r, Err)
        assert r.error == "oops"

    def test_err_with_code(self):
        r = err("oops", code="bad")
        assert r.code == "bad"


class TestSerialization:
    def test_ok_model_dump(self):
        r = Ok(value={"key": "val"})
        assert r.model_dump() == {"key": "val"}

    def test_err_model_dump(self):
        r = Err(error="fail", code="not_found")
        d = r.model_dump()
        assert d["error"] == "fail"
        assert d["code"] == "not_found"
