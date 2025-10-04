"""Domain-level errors surfaced by service methods."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ServiceError(Exception):
    """Base service error aligned with API error payload."""

    code: str
    message: str
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ServiceValidationError(ServiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="validation", message=message, details=details)


class ServiceConflictError(ServiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="conflict", message=message, details=details)


class ServiceNotFoundError(ServiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="not_found", message=message, details=details)


class ServiceExpiredError(ServiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="expired", message=message, details=details)


class ServicePermissionError(ServiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="forbidden", message=message, details=details)


class ServiceTimeoutError(ServiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="db_timeout", message=message, details=details)


__all__ = [
    "ServiceConflictError",
    "ServiceError",
    "ServiceExpiredError",
    "ServiceNotFoundError",
    "ServicePermissionError",
    "ServiceTimeoutError",
    "ServiceValidationError",
]

