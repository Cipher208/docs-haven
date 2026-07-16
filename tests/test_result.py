"""Tests for Result type (Ok/Err)."""

import pytest

from result import Err, Ok, err, ok


class TestOk:
    def test_is_ok(self):
        assert Ok(42).is_ok() is True

    def test_is_err(self):
        assert Ok(42).is_err() is False

    def test_unwrap(self):
        assert Ok(42).unwrap() == 42

    def test_value(self):
        assert Ok("hello").value == "hello"


class TestErr:
    def test_is_ok(self):
        assert Err("fail").is_ok() is False

    def test_is_err(self):
        assert Err("fail").is_err() is True

    def test_unwrap_raises(self):
        with pytest.raises(RuntimeError, match="fail"):
            Err("fail").unwrap()

    def test_error(self):
        assert Err("fail").error == "fail"

    def test_code_default(self):
        assert Err("fail").code == "error"

    def test_code_custom(self):
        assert Err("fail", code="not_found").code == "not_found"


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
