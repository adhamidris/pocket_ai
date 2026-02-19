from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TypeVar

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CacheUnavailableError(RuntimeError):
    """Raised when strict cache operations cannot be completed safely."""


@dataclass(frozen=True)
class RedisCircuitStatus:
    enabled: bool
    using_redis_cache: bool
    is_open: bool
    failure_count: int
    opened_until_monotonic: float
    last_error: str


class _RedisCircuitBreaker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failure_count = 0
        self._opened_until = 0.0
        self._last_error = ""
        self._last_log_ts = 0.0

    @staticmethod
    def _enabled() -> bool:
        return bool(getattr(settings, "REDIS_CIRCUIT_BREAKER_ENABLED", True))

    @staticmethod
    def _failure_threshold() -> int:
        try:
            value = int(getattr(settings, "REDIS_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3) or 3)
        except (TypeError, ValueError):
            value = 3
        return max(1, value)

    @staticmethod
    def _recovery_seconds() -> float:
        try:
            value = float(getattr(settings, "REDIS_CIRCUIT_BREAKER_RECOVERY_SECONDS", 30) or 30)
        except (TypeError, ValueError):
            value = 30.0
        return max(1.0, value)

    @staticmethod
    def _log_cooldown_seconds() -> float:
        try:
            value = float(getattr(settings, "REDIS_CIRCUIT_BREAKER_LOG_COOLDOWN_SECONDS", 10) or 10)
        except (TypeError, ValueError):
            value = 10.0
        return max(1.0, value)

    def _should_log(self, now: float) -> bool:
        cooldown = self._log_cooldown_seconds()
        if (now - self._last_log_ts) >= cooldown:
            self._last_log_ts = now
            return True
        return False

    def allow_request(self, operation: str) -> bool:
        if not self._enabled():
            return True
        now = time.monotonic()
        with self._lock:
            if self._opened_until > now:
                if self._should_log(now):
                    logger.warning(
                        "redis.circuit.open operation=%s opened_until=%.3f failures=%s",
                        operation,
                        self._opened_until,
                        self._failure_count,
                    )
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            was_open = self._opened_until > 0.0
            had_failures = self._failure_count > 0
            self._failure_count = 0
            self._opened_until = 0.0
            self._last_error = ""
            if was_open or had_failures:
                logger.info("redis.circuit.recovered")

    def record_failure(self, operation: str, exc: Exception) -> None:
        now = time.monotonic()
        with self._lock:
            self._failure_count += 1
            self._last_error = str(exc)[:240]
            threshold = self._failure_threshold()
            if self._failure_count >= threshold:
                self._opened_until = max(self._opened_until, now + self._recovery_seconds())
                logger.error(
                    "redis.circuit.opened operation=%s failures=%s threshold=%s recovery_s=%.1f error=%s",
                    operation,
                    self._failure_count,
                    threshold,
                    self._recovery_seconds(),
                    self._last_error,
                )
            elif self._should_log(now):
                logger.warning(
                    "redis.circuit.failure operation=%s failures=%s threshold=%s error=%s",
                    operation,
                    self._failure_count,
                    threshold,
                    self._last_error,
                )

    def snapshot(self) -> RedisCircuitStatus:
        now = time.monotonic()
        with self._lock:
            return RedisCircuitStatus(
                enabled=self._enabled(),
                using_redis_cache=using_redis_cache(),
                is_open=bool(self._opened_until > now),
                failure_count=int(self._failure_count),
                opened_until_monotonic=float(self._opened_until),
                last_error=str(self._last_error or ""),
            )


_breaker = _RedisCircuitBreaker()


def using_redis_cache() -> bool:
    caches = getattr(settings, "CACHES", {}) if hasattr(settings, "CACHES") else {}
    default_cfg = caches.get("default") if isinstance(caches, Mapping) else {}
    backend = str((default_cfg or {}).get("BACKEND") or "").strip().lower()
    if not backend:
        return False
    return "django_redis" in backend or backend.endswith(".rediscache")


def _run_strict_redis(operation: str, callback: Callable[[Any], T]) -> T:
    if not using_redis_cache():
        raise CacheUnavailableError("Redis cache backend is not active.")
    if not _breaker.allow_request(operation):
        raise CacheUnavailableError(f"Redis temporarily unavailable (circuit open) during {operation}.")

    try:
        from django_redis import get_redis_connection

        conn = get_redis_connection("default")
        result = callback(conn)
    except Exception as exc:  # pragma: no cover - depends on runtime transport failures
        _breaker.record_failure(operation, exc)
        raise CacheUnavailableError(f"Redis operation failed during {operation}.") from exc

    _breaker.record_success()
    return result


def circuit_status() -> RedisCircuitStatus:
    return _breaker.snapshot()


def reserve_counter(
    *,
    key: str,
    window_seconds: int,
    amount: int = 1,
    operation: str,
) -> int:
    ttl = max(1, int(window_seconds or 1))
    increment = max(1, int(amount or 1))

    if using_redis_cache():
        def _op(conn):
            pipe = conn.pipeline()
            pipe.incrby(key, increment)
            pipe.ttl(key)
            new_total, key_ttl = pipe.execute()
            try:
                ttl_int = int(key_ttl)
            except (TypeError, ValueError):
                ttl_int = -1
            if ttl_int < 0:
                conn.expire(key, ttl)
            return int(new_total)

        return _run_strict_redis(operation, _op)

    # Non-Redis local fallback (tests/dev).
    try:
        current = cache.get(key)
        if current is None:
            cache.set(key, increment, timeout=ttl)
            return increment
        try:
            new_total = cache.incr(key, increment)
        except TypeError:
            base = int(cache.incr(key))
            new_total = base + (increment - 1)
            cache.set(key, int(new_total), timeout=ttl)
        except Exception:
            new_total = int(current) + increment
            cache.set(key, int(new_total), timeout=ttl)
        return int(new_total)
    except Exception as exc:
        raise CacheUnavailableError(f"Cache operation failed during {operation}.") from exc


def reserve_cooldown_key(*, key: str, ttl_seconds: int, operation: str) -> bool:
    ttl = max(1, int(ttl_seconds or 1))

    if using_redis_cache():
        def _op(conn):
            created = conn.set(key, b"1", ex=ttl, nx=True)
            return bool(created)

        return bool(_run_strict_redis(operation, _op))

    try:
        created = cache.add(key, True, timeout=ttl)
        if created is None:
            return bool(cache.get(key))
        return bool(created)
    except Exception as exc:
        raise CacheUnavailableError(f"Cache operation failed during {operation}.") from exc


def store_json_state(*, key: str, payload: Mapping[str, Any], ttl_seconds: int, operation: str) -> None:
    ttl = max(1, int(ttl_seconds or 1))

    if using_redis_cache():
        encoded = json.dumps(dict(payload or {}), separators=(",", ":"), ensure_ascii=True)

        def _op(conn):
            conn.set(key, encoded, ex=ttl)
            return True

        _run_strict_redis(operation, _op)
        return

    try:
        cache.set(key, dict(payload or {}), timeout=ttl)
    except Exception as exc:
        raise CacheUnavailableError(f"Cache operation failed during {operation}.") from exc


def load_json_state(*, key: str, operation: str) -> Mapping[str, Any] | None:
    if using_redis_cache():
        def _op(conn):
            return conn.get(key)

        raw = _run_strict_redis(operation, _op)
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("redis.cache_state.invalid_json key=%s operation=%s", key, operation)
            return None
        if isinstance(value, Mapping):
            return dict(value)
        return None

    try:
        value = cache.get(key)
    except Exception as exc:
        raise CacheUnavailableError(f"Cache operation failed during {operation}.") from exc
    if isinstance(value, Mapping):
        return dict(value)
    return None


def delete_state_key(*, key: str, operation: str) -> None:
    if using_redis_cache():
        def _op(conn):
            conn.delete(key)
            return True

        _run_strict_redis(operation, _op)
        return

    try:
        cache.delete(key)
    except Exception as exc:
        raise CacheUnavailableError(f"Cache operation failed during {operation}.") from exc
