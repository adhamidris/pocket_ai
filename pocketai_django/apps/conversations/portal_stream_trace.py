from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from django.conf import settings


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def portal_stream_trace_enabled() -> bool:
    return bool(getattr(settings, "PORTAL_STREAM_TRACE", False))


def portal_stream_trace_dir() -> str:
    directory = str(getattr(settings, "PORTAL_STREAM_TRACE_DIR", "") or "").strip()
    return directory or "/tmp/pocketai/portal_stream_traces"


def portal_stream_trace_include_text() -> bool:
    return bool(getattr(settings, "PORTAL_STREAM_TRACE_INCLUDE_TEXT", False))


def portal_stream_trace_max_buffer_lines() -> int:
    try:
        value = int(getattr(settings, "PORTAL_STREAM_TRACE_MAX_BUFFER_LINES", 500) or 500)
    except (TypeError, ValueError):
        value = 500
    return max(10, min(10_000, value))


def portal_stream_trace_max_text_preview_chars() -> int:
    try:
        value = int(getattr(settings, "PORTAL_STREAM_TRACE_TEXT_PREVIEW_CHARS", 120) or 120)
    except (TypeError, ValueError):
        value = 120
    return max(0, min(1000, value))


@dataclass
class PortalStreamTrace:
    """
    Lightweight JSONL trace writer for debugging portal streaming behavior.

    Intended usage:
      - worker: record incoming deltas, emitted events, final persisted blocks
      - web/sse: record Redis/Postgres batch sizes + yield cadence

    This intentionally defaults to *no output* unless PORTAL_STREAM_TRACE is enabled.
    """

    turn_id: uuid.UUID
    component: str
    enabled: bool = field(default_factory=portal_stream_trace_enabled)
    started_at_perf: float = field(default_factory=time.perf_counter)
    pid: int = field(default_factory=os.getpid)
    _buffer: list[str] = field(default_factory=list, init=False)
    _buffer_limit: int = field(default_factory=portal_stream_trace_max_buffer_lines, init=False)

    def _path(self) -> str:
        directory = portal_stream_trace_dir()
        safe_component = "".join(ch for ch in (self.component or "trace") if ch.isalnum() or ch in {"-", "_"}).strip()
        safe_component = safe_component or "trace"
        return os.path.join(directory, f"{self.turn_id}.{safe_component}.{self.pid}.jsonl")

    def record(self, event: str, data: Mapping[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        now_perf = time.perf_counter()
        record: dict[str, Any] = {
            "turn_id": str(self.turn_id),
            "component": str(self.component or "trace"),
            "pid": int(self.pid),
            "t_ms": int(max(0.0, (now_perf - self.started_at_perf) * 1000.0)),
            "t_epoch_ms": int(time.time() * 1000.0),
            "event": str(event or "event"),
        }
        if data:
            record.update(dict(data))
        self._buffer.append(json.dumps(record, separators=(",", ":"), ensure_ascii=True))
        if len(self._buffer) >= self._buffer_limit:
            self.flush()

    def record_text(self, event: str, text: str, extra: Mapping[str, Any] | None = None) -> None:
        metrics: dict[str, Any] = {"len": int(len(text or ""))}
        if extra:
            metrics.update(dict(extra))
        if portal_stream_trace_include_text():
            limit = portal_stream_trace_max_text_preview_chars()
            if limit > 0 and text:
                preview = text[:limit]
                metrics["preview"] = preview
        self.record(event, metrics)

    def flush(self) -> None:
        if not self.enabled or not self._buffer:
            return
        path = self._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(self._buffer))
            handle.write("\n")
        self._buffer.clear()

    def close(self) -> None:
        self.flush()

