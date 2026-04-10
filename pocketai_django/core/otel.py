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

import os
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

        def get_current_span(self) -> Span:
            return Span()

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
    "current_log_record_otel_fields",
]


def _format_trace_id(value: int) -> str:
    return f"{int(value or 0):032x}"


def _format_span_id(value: int) -> str:
    return f"{int(value or 0):016x}"


def _trace_sampled_from_context(span_context: Any) -> bool:
    trace_flags = getattr(span_context, "trace_flags", None)
    sampled = getattr(trace_flags, "sampled", None)
    if sampled is not None:
        return bool(sampled)
    try:
        return bool(int(trace_flags) & 0x01)
    except Exception:
        return False


def current_log_record_otel_fields() -> dict[str, object]:
    """
    Return OpenTelemetry-compatible logging fields for manually emitted LogRecords.

    LoggingInstrumentor enriches records created through the normal logger path.
    Our structured loggers emit raw LogRecords directly to handlers, so we need
    to attach the same fields ourselves to satisfy formatters that expect them.
    """

    trace_id = 0
    span_id = 0
    sampled = False

    try:
        span = otel_trace.get_current_span()
        span_context = span.get_span_context() if span else None
        if span_context is not None:
            trace_id = int(getattr(span_context, "trace_id", 0) or 0)
            span_id = int(getattr(span_context, "span_id", 0) or 0)
            sampled = _trace_sampled_from_context(span_context)
    except Exception:
        trace_id = 0
        span_id = 0
        sampled = False

    return {
        "otelTraceID": _format_trace_id(trace_id),
        "otelSpanID": _format_span_id(span_id),
        "otelTraceSampled": sampled,
        "otelServiceName": os.getenv("OTEL_SERVICE_NAME", "pocketai-django"),
    }
