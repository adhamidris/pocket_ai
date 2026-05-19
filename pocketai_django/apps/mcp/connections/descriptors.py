from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from django.conf import settings

from apps.mcp.connections.cache import _is_cache_expired
from apps.mcp.connections.naming import build_remote_tool_name
from apps.mcp.connections.schema import (
    _REMOTE_TOOL_DESCRIPTION_MAX_CHARS_DEFAULT,
    _clip_text,
    _safe_int_setting,
    _slim_remote_input_schema,
)
from apps.mcp.models import McpConnection


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteToolDescriptor:
    safe_name: str
    remote_name: str
    description: str
    input_schema: Mapping[str, Any] | None
    connection: McpConnection


def list_remote_tool_descriptors(connections: Sequence[McpConnection]) -> list[RemoteToolDescriptor]:
    """
    Build normalized remote-tool metadata for selection and tool-schema generation.

    This is intentionally lightweight so callers can select a small subset of
    tools before paying the cost to serialize full JSON Schemas into the LLM
    prompt.
    """

    descriptors: list[RemoteToolDescriptor] = []
    seen: set[str] = set()

    description_limit = max(
        80,
        _safe_int_setting(
            getattr(settings, "MCP_REMOTE_TOOL_DESCRIPTION_MAX_CHARS", None),
            _REMOTE_TOOL_DESCRIPTION_MAX_CHARS_DEFAULT,
        ),
    )

    for connection in connections:
        metadata = connection.metadata or {}
        tool_cache = metadata.get("tool_cache") if isinstance(metadata.get("tool_cache"), Mapping) else {}
        tools = tool_cache.get("tools")
        if not isinstance(tools, list) or not tools:
            continue

        if _is_cache_expired(tool_cache):
            logger.info(
                "mcp_tool_cache_expired_using_stale_schema connection_id=%s connection_name=%s expires_at=%s",
                connection.id,
                connection.name,
                tool_cache.get("expires_at"),
            )

        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            remote_name = str(tool.get("name") or "").strip()
            if not remote_name:
                continue
            safe_name = build_remote_tool_name(str(connection.id), remote_name)
            if safe_name in seen:
                continue
            seen.add(safe_name)

            description = str(tool.get("description") or "").strip()
            if connection.name:
                prefix = f"[MCP: {connection.name}]"
                description = f"{prefix} {description}".strip() if description else prefix
            if description:
                description = _clip_text(description, description_limit)

            input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), Mapping) else None
            descriptors.append(
                RemoteToolDescriptor(
                    safe_name=safe_name,
                    remote_name=remote_name,
                    description=description,
                    input_schema=dict(input_schema) if isinstance(input_schema, dict) else None,
                    connection=connection,
                )
            )

    return descriptors


def build_remote_tool_definitions_from_descriptors(
    descriptors: Sequence[RemoteToolDescriptor],
    *,
    allowed_tools: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, tuple[McpConnection, str]]]:
    """
    Convert RemoteToolDescriptor entries to OpenAI tool schemas + registry.
    """

    tool_defs: list[dict[str, Any]] = []
    registry: dict[str, tuple[McpConnection, str]] = {}

    for desc in descriptors:
        if allowed_tools is not None and desc.safe_name not in allowed_tools:
            continue
        if desc.safe_name in registry:
            continue
        registry[desc.safe_name] = (desc.connection, desc.remote_name)
        parameters = (
            _slim_remote_input_schema(desc.input_schema)
            if isinstance(desc.input_schema, Mapping)
            else {"type": "object", "additionalProperties": True}
        )
        tool_defs.append(
            {
                "type": "function",
                "function": {
                    "name": desc.safe_name,
                    "description": desc.description,
                    "parameters": parameters,
                },
            }
        )

    return tool_defs, registry


def build_remote_tool_definitions(
    connections: list[McpConnection],
    *,
    allowed_tools: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, tuple[McpConnection, str]]]:
    """
    Build OpenAI tool schemas + a registry mapping tool name -> (connection, remote_tool_name).

    Tool schemas are sourced from connection.metadata.tool_cache.tools when present.
    Expired caches (past their TTL) are treated as stale but still usable. We keep
    exposing the last known tool schema to avoid "tools disappear" behavior in
    the chat experience. The admin UI can still surface the stale status and
    prompt a refresh via "Test connection".
    """

    descriptors = list_remote_tool_descriptors(connections)
    return build_remote_tool_definitions_from_descriptors(descriptors, allowed_tools=allowed_tools)
