from __future__ import annotations

import logging
import os
import random
import socket
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib import error as urllib_error

from apps.rag.rag_logging import structured_log

logger = logging.getLogger(__name__)

DEFAULT_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _parse_retryable_statuses(raw: str | None) -> set[int]:
    if not raw:
        return set(DEFAULT_RETRYABLE_STATUS_CODES)
    values: set[int] = set()
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            values.add(int(token))
        except (TypeError, ValueError):
            continue
    return values or set(DEFAULT_RETRYABLE_STATUS_CODES)


LLM_RETRY_MAX_ATTEMPTS = max(1, min(8, _env_int("LLM_RETRY_MAX_ATTEMPTS", 3)))
LLM_RETRY_BASE_DELAY_SECONDS = max(0.0, _env_float("LLM_RETRY_BASE_DELAY_SECONDS", 1.0))
LLM_RETRY_MAX_DELAY_SECONDS = max(0.1, _env_float("LLM_RETRY_MAX_DELAY_SECONDS", 8.0))
LLM_RETRY_JITTER_SECONDS = max(0.0, _env_float("LLM_RETRY_JITTER_SECONDS", 0.25))
LLM_RETRYABLE_STATUSES = _parse_retryable_statuses(os.getenv("LLM_RETRYABLE_STATUSES"))


def _status_is_retryable(status_code: int | None) -> bool:
    return status_code is not None and int(status_code) in LLM_RETRYABLE_STATUSES


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        delay = float(text)
        if delay <= 0:
            return None
        return delay
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delay = parsed.timestamp() - time.time()
    if delay <= 0:
        return None
    return delay


def _extract_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return int(value)
        if value is not None:
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                continue
    return None


def _extract_retry_after(exc: Exception) -> str | None:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        return str(retry_after)

    headers = getattr(exc, "headers", None)
    if hasattr(headers, "get"):
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value:
            return str(value)

    response = getattr(exc, "response", None)
    response_headers = getattr(response, "headers", None)
    if hasattr(response_headers, "get"):
        value = response_headers.get("Retry-After") or response_headers.get("retry-after")
        if value:
            return str(value)
    return None


def _is_retryable_transport_error(exc: Exception) -> bool:
    if isinstance(exc, urllib_error.URLError):
        return True
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    retry_markers = (
        "timeout",
        "timed out",
        "tempor",
        "connection",
        "network",
        "reset",
        "rate limit",
        "too many requests",
        "service unavailable",
        "unavailable",
        "overloaded",
    )
    return any(marker in text for marker in retry_markers)


class _ProviderRequestError(RuntimeError):
    """Internal transport/status failure with retry metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.retry_after_seconds = _parse_retry_after_seconds(retry_after)
        self.retryable = bool(retryable)


def _provider_request_error_from_exception(prefix: str, exc: Exception) -> _ProviderRequestError:
    status_code = _extract_status_code(exc)
    retry_after = _extract_retry_after(exc)
    message = f"{prefix}: {exc}"
    return _ProviderRequestError(
        message,
        status_code=status_code,
        retry_after=retry_after,
        retryable=_status_is_retryable(status_code) or _is_retryable_transport_error(exc),
    )


class PromptGenerationError(RuntimeError):
    """Raised when the LLM provider fails to respond."""


def _retry_delay_seconds(attempt: int, retry_after_seconds: float | None) -> float:
    exponent = max(0, int(attempt) - 1)
    backoff = LLM_RETRY_BASE_DELAY_SECONDS * (2 ** exponent)
    backoff = min(LLM_RETRY_MAX_DELAY_SECONDS, backoff)
    jitter = random.uniform(0.0, LLM_RETRY_JITTER_SECONDS) if LLM_RETRY_JITTER_SECONDS > 0 else 0.0
    delay = backoff + jitter
    if retry_after_seconds and retry_after_seconds > 0:
        delay = max(delay, min(LLM_RETRY_MAX_DELAY_SECONDS, retry_after_seconds))
    return min(LLM_RETRY_MAX_DELAY_SECONDS, delay)


def _call_with_retry(
    call: Callable[[], Any],
    *,
    provider: str,
    model: str | None,
    operation: str,
    can_retry: Callable[[], bool] | None = None,
) -> Any:
    attempts = max(1, int(LLM_RETRY_MAX_ATTEMPTS))
    attempt = 0
    while True:
        attempt += 1
        try:
            return call()
        except _ProviderRequestError as exc:
            retry_allowed = exc.retryable and attempt < attempts
            if retry_allowed and can_retry is not None:
                try:
                    retry_allowed = bool(can_retry())
                except Exception:
                    retry_allowed = False
            if not retry_allowed:
                raise PromptGenerationError(str(exc)) from exc
            delay_s = _retry_delay_seconds(attempt, exc.retry_after_seconds)
            structured_log(
                "llm",
                "retry",
                {
                    "provider": provider,
                    "model": model,
                    "operation": operation,
                    "attempt": attempt,
                    "max_attempts": attempts,
                    "status_code": exc.status_code,
                    "retry_after": exc.retry_after,
                    "delay_s": round(delay_s, 3),
                },
                logger_obj=logger,
                level=logging.WARNING,
            )
            time.sleep(delay_s)
