from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from django.conf import settings

logger = logging.getLogger(__name__)


def _timestamp() -> str:
    tz_name = getattr(settings, "PORTAL_TRACE_TIMEZONE", "Africa/Cairo")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover - fallback when TZ database unavailable
        tz = ZoneInfo("UTC")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_detail(detail: Any | None) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, Mapping):
        parts: list[str] = []
        for key in sorted(detail.keys()):
            value = detail[key]
            if value is None or value == "":
                continue
            parts.append(f"{key}={value}")
        return " ".join(parts)
    try:
        return json.dumps(detail, ensure_ascii=False)
    except Exception:
        return str(detail)


def structured_log(
    namespace: str,
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    timestamp = _timestamp()
    header_parts: list[str] = [f"[{timestamp}]", f"stage={stage}"]
    if context:
        for key in sorted(context.keys()):
            value = context[key]
            if value is None or value == "":
                continue
            header_parts.append(f"{key}={value}")
    header = f"{namespace}.trace " + " ".join(header_parts)
    indent_prefix = "    " * max(indent, 0)
    body_text = _format_detail(detail)
    lines: list[str] = []
    if body_text:
        for payload_line in body_text.splitlines():
            lines.append(f"{indent_prefix}• {payload_line}")
    else:
        lines.append(f"{indent_prefix}• (no detail)")
    logger.log(level, "%s\n%s", header.strip(), "\n".join(lines))


def rag_log(
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
) -> None:
    structured_log("rag", stage, detail, indent=indent, context=context)
