from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    McpConnection,
    McpConnectionAuthType,
    McpConnectionStatus,
)


_TOOL_SAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_]+")


def list_enabled_mcp_connections_for_agent(agent: AgentProfile) -> list[McpConnection]:
    business = agent.business_profile
    if not business:
        return []
    with tenant_context(business.id):
        return list(
            McpConnection.objects.filter(
                business_profile=business,
                status=McpConnectionStatus.ENABLED,
            )
            .exclude(agent_opt_outs__agent_profile=agent)
            .order_by("name")
        )


def mcp_connection_auth_headers(connection: McpConnection) -> dict[str, str]:
    if connection.auth_type == McpConnectionAuthType.NONE:
        return {}
    credentials = connection.credentials or {}
    if connection.auth_type == McpConnectionAuthType.BEARER:
        token = str(credentials.get("token") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}
    if connection.auth_type == McpConnectionAuthType.HEADER:
        header_name = str(credentials.get("header_name") or "").strip()
        header_value = str(credentials.get("header_value") or "").strip()
        if header_name and header_value:
            return {header_name: header_value}
    return {}


def _normalize_tool_fragment(value: str) -> str:
    normalized = _TOOL_SAFE_PATTERN.sub("_", (value or "").strip())
    normalized = normalized.strip("_") or "tool"
    return normalized[:32]


def build_remote_tool_name(connection_id: str, remote_tool_name: str) -> str:
    conn_token = str(connection_id).split("-")[0]
    digest = hashlib.sha256((remote_tool_name or "").encode("utf-8")).hexdigest()[:8]
    fragment = _normalize_tool_fragment(remote_tool_name)
    return f"mcp_{conn_token}__{fragment}__{digest}"


def build_remote_tool_definitions(
    connections: list[McpConnection],
) -> tuple[list[dict[str, Any]], dict[str, tuple[McpConnection, str]]]:
    """
    Build OpenAI tool schemas + a registry mapping tool name -> (connection, remote_tool_name).

    Tool schemas are sourced from connection.metadata.tool_cache.tools when present.
    """

    tool_defs: list[dict[str, Any]] = []
    registry: dict[str, tuple[McpConnection, str]] = {}

    for connection in connections:
        metadata = connection.metadata or {}
        tool_cache = metadata.get("tool_cache") if isinstance(metadata.get("tool_cache"), Mapping) else {}
        tools = tool_cache.get("tools")
        if not isinstance(tools, list) or not tools:
            continue
        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            remote_name = str(tool.get("name") or "").strip()
            if not remote_name:
                continue
            safe_name = build_remote_tool_name(str(connection.id), remote_name)
            if safe_name in registry:
                continue
            description = str(tool.get("description") or "").strip()
            if connection.name:
                prefix = f"[MCP: {connection.name}]"
                description = f"{prefix} {description}".strip() if description else prefix
            input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), Mapping) else None
            parameters = dict(input_schema) if isinstance(input_schema, dict) else {"type": "object", "additionalProperties": True}
            tool_defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": safe_name,
                        "description": description,
                        "parameters": parameters,
                    },
                }
            )
            registry[safe_name] = (connection, remote_name)

    return tool_defs, registry

