"""Simple in-memory rate limiting utilities."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class RateLimitConfig:
    """Parsed rate-limit configuration."""

    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitState:
    """Outcome of a rate-limit check."""

    remaining: int
    reset_in: float


class RateLimitExceededError(RuntimeError):
    """Raised when a rate limit is exceeded."""

    def __init__(self, *, retry_after: float) -> None:
        super().__init__("Rate limit exceeded")
        self.retry_after = retry_after


_WINDOW_FACTORS: dict[str, int] = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hour": 3600,
    "hours": 3600,
}

_ENTRIES: dict[str, deque[float]] = {}
_LOCK = Lock()


def parse_rate_limit(value: str) -> RateLimitConfig:
    """Parse a rate limit definition like ``'5/min'`` or ``'100/hour'``."""

    if not value or "/" not in value:
        raise ValueError("Rate limit must be in the format '<count>/<window>'")
    count_part, window_part = value.split("/", 1)
    try:
        limit = int(count_part.strip())
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise ValueError("Rate limit count must be an integer") from exc
    if limit <= 0:
        raise ValueError("Rate limit count must be positive")
    unit = window_part.strip().lower()
    factor = _WINDOW_FACTORS.get(unit)
    if factor is None:
        raise ValueError("Unsupported rate limit window; use sec/min/hour")
    return RateLimitConfig(limit=limit, window_seconds=factor)


def enforce_rate_limit(key: str, config: RateLimitConfig) -> RateLimitState:
    """Enforce the configured rate limit for a unique key."""

    now = monotonic()
    cutoff = now - config.window_seconds
    with _LOCK:
        bucket = _ENTRIES.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= config.limit:
            retry_after = max(0.0, bucket[0] + config.window_seconds - now)
            raise RateLimitExceededError(retry_after=retry_after)
        bucket.append(now)
        remaining = config.limit - len(bucket)
        reset_in = bucket[0] + config.window_seconds - now if bucket else float(config.window_seconds)
    return RateLimitState(remaining=remaining, reset_in=max(0.0, reset_in))


__all__ = [
    "RateLimitConfig",
    "RateLimitExceededError",
    "RateLimitState",
    "enforce_rate_limit",
    "parse_rate_limit",
]

