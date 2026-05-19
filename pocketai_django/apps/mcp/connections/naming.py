from __future__ import annotations

import hashlib
import re


_TOOL_SAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_]+")


def _normalize_tool_fragment(value: str) -> str:
    normalized = _TOOL_SAFE_PATTERN.sub("_", (value or "").strip())
    normalized = normalized.strip("_") or "tool"
    return normalized[:32]


def build_remote_tool_name(connection_id: str, remote_tool_name: str) -> str:
    conn_token = str(connection_id).split("-")[0]
    digest = hashlib.sha256((remote_tool_name or "").encode("utf-8")).hexdigest()[:8]
    fragment = _normalize_tool_fragment(remote_tool_name)
    return f"mcp_{conn_token}__{fragment}__{digest}"
