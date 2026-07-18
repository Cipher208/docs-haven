"""Result type for error handling — replaces error dict anti-pattern.

Uses Pydantic v2 for validation and serialization.
"""

from __future__ import annotations

from typing import Any, Generic, NoReturn, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Ok(BaseModel, Generic[T]):
    """Success result."""

    value: T
    is_ok: bool = Field(default=True, init=False)
    is_err: bool = Field(default=False, init=False)

    def unwrap(self) -> T:
        return self.value

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Override to return just the value for MCP compatibility."""
        if isinstance(self.value, BaseModel):
            return self.value.model_dump(**kwargs)
        if isinstance(self.value, dict):
            return self.value
        return {"value": self.value}


class Err(BaseModel):
    """Error result."""

    error: str
    code: str = "error"
    is_ok: bool = Field(default=False, init=False)
    is_err: bool = Field(default=True, init=False)

    def unwrap(self) -> NoReturn:
        raise RuntimeError(self.error)


Result = Ok[Any] | Err


def ok(value: T) -> Ok[T]:
    """Create a success result."""
    return Ok(value=value)


def err(error: str, code: str = "error") -> Err:
    """Create an error result."""
    return Err(error=error, code=code)
