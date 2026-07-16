"""Result type for error handling — replaces error dict anti-pattern."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class Ok(Generic[T]):
    """Success result."""
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value


@dataclass
class Err:
    """Error result."""
    error: str
    code: str = "error"

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self):
        raise RuntimeError(self.error)


Result = Ok | Err


def ok(value: T) -> Ok[T]:
    """Create a success result."""
    return Ok(value)


def err(error: str, code: str = "error") -> Err:
    """Create an error result."""
    return Err(error=error, code=code)
