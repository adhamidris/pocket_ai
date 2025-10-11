"""Error types for repository interactions."""

from __future__ import annotations

from typing import Any


class RepositoryError(Exception):
    """Base error carrying API-aligned payload."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "repository_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class NotFoundError(RepositoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="not_found", details=details)


class ExpiredError(RepositoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="expired", details=details)


class ConflictError(RepositoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="conflict", details=details)


class ValidationError(RepositoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="validation", details=details)


class DbTimeoutError(RepositoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="db_timeout", details=details)


__all__ = [
    "ConflictError",
    "DbTimeoutError",
    "ExpiredError",
    "NotFoundError",
    "RepositoryError",
    "ValidationError",
]
