"""
OpenTelemetry compatibility layer.

This project uses OpenTelemetry for tracing in production, but some local/dev or
CI environments may not have the opentelemetry packages installed. Importing
OpenTelemetry directly in many modules can then break management commands and
tests at import-time.

This module centralizes the import and provides no-op fallbacks that preserve
the small subset of APIs we use (get_tracer/start_as_current_span and basic
context attach/detach).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


try:  # pragma: no cover - exercised in environments with opentelemetry installed
    from opentelemetry import context as otel_context  # type: ignore
    from opentelemetry import trace as otel_trace  # type: ignore
    from opentelemetry.trace import Span, Status, StatusCode  # type: ignore
except Exception:  # pragma: no cover - optional dependency

    class _NoopSpanContext:
        trace_id: int = 0
        span_id: int = 0

    class Span:  # type: ignore[override]
        def get_span_context(self) -> _NoopSpanContext:
            return _NoopSpanContext()

        def is_recording(self) -> bool:
            return False

        def set_attribute(self, *args: Any, **kwargs: Any) -> None:
            return None

        def set_attributes(self, *args: Any, **kwargs: Any) -> None:
            return None

        def add_event(self, *args: Any, **kwargs: Any) -> None:
            return None

        def record_exception(self, *args: Any, **kwargs: Any) -> None:
            return None

        def set_status(self, *args: Any, **kwargs: Any) -> None:
            return None

    class _NoopTracer:
        @contextmanager
        def start_as_current_span(self, *args: Any, **kwargs: Any) -> Iterator[Span]:
            yield Span()

    class _NoopTraceModule:
        def get_tracer(self, *args: Any, **kwargs: Any) -> _NoopTracer:
            return _NoopTracer()

    class StatusCode:  # type: ignore[override]
        OK = "OK"
        ERROR = "ERROR"

    @dataclass(frozen=True)
    class Status:  # type: ignore[override]
        status_code: Any
        description: str | None = None

    class _NoopContextToken:
        pass

    class _NoopContextModule:
        def get_current(self) -> object | None:
            return None

        def attach(self, _ctx: object) -> _NoopContextToken:
            return _NoopContextToken()

        def detach(self, _token: object) -> None:
            return None

    otel_trace = _NoopTraceModule()
    otel_context = _NoopContextModule()


__all__ = [
    "otel_context",
    "otel_trace",
    "Span",
    "Status",
    "StatusCode",
]

