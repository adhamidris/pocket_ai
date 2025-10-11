"""Database engine and session management helpers."""

from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _initialise() -> None:
    global _engine, _SessionFactory
    if _engine is not None and _SessionFactory is not None:
        return

    settings = get_settings()
    engine = create_engine(
        settings.DATABASE_URL,
        future=True,
        pool_pre_ping=True,
    )
    _engine = engine
    _SessionFactory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def get_engine() -> Engine:
    """Return the shared SQLAlchemy engine instance."""

    if _engine is None:
        _initialise()
    assert _engine is not None  # for type checkers
    return _engine


def get_session() -> Session:
    """Create a new database session bound to the shared engine."""

    if _SessionFactory is None:
        _initialise()
    assert _SessionFactory is not None
    return _SessionFactory()


def session_scope() -> Iterator[Session]:
    """Context manager yielding a session with guaranteed cleanup."""

    session = get_session()
    try:
        yield session
    finally:
        session.close()


__all__ = ["get_engine", "get_session", "session_scope"]
