"""Base utilities for repository implementations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ContextManager, Iterator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.repositories.errors import DbTimeoutError, RepositoryError

STATEMENT_TIMEOUT_MS = 2000


def utc_now() -> datetime:
    """Return current UTC time with timezone info."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class StatementTimeoutSpec:
    """Configuration for statement timeout enforcement."""

    milliseconds: int = STATEMENT_TIMEOUT_MS


@contextmanager
def statement_timeout(session: Session, spec: StatementTimeoutSpec | None = None) -> Iterator[None]:
    """Apply a transactional statement timeout for PostgreSQL dialects.

    Other dialects ignore the timeout request. Any SQLAlchemy error emitted while
    attempting to set the timeout is wrapped into a ``DbTimeoutError`` to maintain
    the repository error contract.
    """

    active_spec = spec or StatementTimeoutSpec()
    bind = session.get_bind() if session is not None else None
    if bind is None:
        yield
        return

    if bind.dialect.name != "postgresql":
        yield
        return

    try:
        # Postgres does not allow bind params in SET/SET LOCAL; use a literal.
        # exec_driver_sql avoids SQLAlchemy parameter binding here.
        session.connection().exec_driver_sql(
            f"SET LOCAL statement_timeout = '{active_spec.milliseconds}ms'"
        )
    except SQLAlchemyError as exc:  # pragma: no cover - defensive guard
        raise DbTimeoutError("Failed to set statement timeout", details={"cause": str(exc)}) from exc

    try:
        yield
    except RepositoryError:
        raise
    except SQLAlchemyError as exc:  # pragma: no cover - defensive guard
        raise RepositoryError("Database error during repository call", details={"cause": str(exc)}) from exc


class BaseRepository:
    """Base class providing shared repository helpers."""

    def __init__(self, session: Session, *, timeout: StatementTimeoutSpec | None = None) -> None:
        self._session = session
        self._timeout_spec = timeout or StatementTimeoutSpec()

    @property
    def session(self) -> Session:
        return self._session

    @property
    def timeout_spec(self) -> StatementTimeoutSpec:
        return self._timeout_spec

    def _with_timeout(self) -> ContextManager[None]:
        return statement_timeout(self._session, self._timeout_spec)
