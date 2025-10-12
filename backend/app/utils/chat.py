"""Utility helpers that support chat session workflows."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.core.settings import get_settings


def ensure_aware(value: datetime) -> datetime:
    """Return a timezone-aware datetime in UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def default_session_ttl() -> timedelta:
    """Default TTL for chat sessions derived from settings."""

    settings = get_settings()
    return timedelta(seconds=settings.CHAT_SESSION_TTL_SECONDS)


def compute_session_expiration(now: datetime, ttl: timedelta | None = None) -> datetime:
    """Compute the expiry timestamp for a chat session."""

    base = ensure_aware(now)
    active_ttl = ttl or default_session_ttl()
    return base + active_ttl


def is_session_expired(expires_at: datetime | None, now: datetime | None = None) -> bool:
    """Check whether a session has expired by comparing to ``now``."""

    if expires_at is None:
        return False
    current = ensure_aware(now or datetime.now(timezone.utc))
    expiry = ensure_aware(expires_at)
    return expiry <= current


def generate_session_token(*, nbytes: int | None = None, max_length: int | None = None) -> str:
    """Generate a secure random session token respecting project limits."""

    settings = get_settings()
    size = nbytes if nbytes is not None else settings.CHAT_SESSION_TOKEN_BYTES
    token = secrets.token_urlsafe(size)
    limit = max_length if max_length is not None else settings.CHAT_SESSION_TOKEN_MAX_LENGTH
    if limit and limit > 0:
        return token[:limit]
    return token


def resolve_welcome_template(
    requested_key: str | None,
    *,
    default_key: str | None = None,
    available_keys: Iterable[str] | None = None,
) -> str | None:
    """Resolve the welcome template key honoring availability and defaults."""

    available = set(available_keys) if available_keys is not None else None
    if requested_key:
        if available is None or requested_key in available:
            return requested_key
    if default_key:
        if available is None or default_key in available:
            return default_key
    return None


__all__ = [
    "compute_session_expiration",
    "default_session_ttl",
    "ensure_aware",
    "generate_session_token",
    "is_session_expired",
    "resolve_welcome_template",
]
