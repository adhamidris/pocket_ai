from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    AgentMcpToolSetting,
    McpConnection,
    McpConnectionApprovalMode,
    McpConnectionAuthType,
    McpConnectionStatus,
    McpConnectionToolSetting,
    McpToolOperationType,
)


logger = logging.getLogger(__name__)


_TOOL_SAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_]+")

_TOOL_READ_HINTS = {
    "get",
    "list",
    "search",
    "read",
    "fetch",
    "retrieve",
    "query",
    "describe",
    "view",
    "show",
    "lookup",
    "find",
}

_TOOL_WRITE_HINTS = {
    "create",
    "update",
    "delete",
    "remove",
    "add",
    "set",
    "patch",
    "put",
    "post",
    "write",
    "edit",
    "modify",
    "upload",
    "send",
    "execute",
    "run",
    "trigger",
    "approve",
    "deny",
    "grant",
    "revoke",
    "sync",
    "import",
}


def _infer_operation_type_from_tool_name(remote_tool_name: str) -> McpToolOperationType:
    """
    Best-effort operation type inference for tools without an explicit setting.

    This is used to make APPROVE_WRITES mode usable out-of-the-box when the
    remote MCP server does not supply explicit read/write metadata.
    """

    raw = str(remote_tool_name or "").strip()
    if not raw:
        return McpToolOperationType.UNKNOWN

    # Normalize camelCase + separators into tokens.
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", raw)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).lower()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if not tokens:
        return McpToolOperationType.UNKNOWN

    if any(token in _TOOL_WRITE_HINTS for token in tokens):
        return McpToolOperationType.WRITE
    if any(token in _TOOL_READ_HINTS for token in tokens):
        return McpToolOperationType.READ
    return McpToolOperationType.UNKNOWN


def _is_cache_expired(tool_cache: Mapping[str, Any]) -> bool:
    """Check if a tool cache has expired based on its expires_at timestamp."""
    expires_at = tool_cache.get("expires_at")
    if not expires_at:
        # Legacy caches without expiration are considered valid (backwards compatibility)
        return False
    try:
        if isinstance(expires_at, str):
            # Parse ISO format timestamp
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        elif isinstance(expires_at, datetime):
            expiry = expires_at
        else:
            return False
        # Ensure timezone-aware comparison
        now = datetime.now(timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return now > expiry
    except (ValueError, TypeError):
        return False


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
    Expired caches (past their TTL) are skipped with a warning log.
    """

    tool_defs: list[dict[str, Any]] = []
    registry: dict[str, tuple[McpConnection, str]] = {}

    for connection in connections:
        metadata = connection.metadata or {}
        tool_cache = metadata.get("tool_cache") if isinstance(metadata.get("tool_cache"), Mapping) else {}
        tools = tool_cache.get("tools")
        if not isinstance(tools, list) or not tools:
            continue

        # Skip connections with expired tool caches
        if _is_cache_expired(tool_cache):
            logger.info(
                "mcp_tool_cache_expired connection_id=%s connection_name=%s expires_at=%s",
                connection.id,
                connection.name,
                tool_cache.get("expires_at"),
            )
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


def get_tool_approval_requirement(
    connection: McpConnection,
    remote_tool_name: str,
    *,
    agent: AgentProfile | None = None,
) -> dict[str, Any]:
    """
    Determine if a tool call requires user approval.

    Returns a dict with:
    - requires_approval: bool
    - approval_mode: str (the effective mode)
    - operation_type: str (read/write/unknown)
    - reason: str (human-readable explanation)
    """
    business_id = getattr(connection, "business_profile_id", None) or getattr(
        getattr(connection, "business_profile", None),
        "id",
        None,
    )
    agent_value = agent if agent and getattr(agent, "business_profile_id", None) == business_id else None
    agent_default_mode = (getattr(agent_value, "mcp_default_approval_mode", None) or "").strip() if agent_value else ""

    explicit_setting = False

    # Get per-tool setting if exists
    with tenant_context(business_id):
        try:
            approval_mode = ""
            operation_type = McpToolOperationType.UNKNOWN
            if agent_value:
                try:
                    agent_tool_setting = AgentMcpToolSetting.objects.get(
                        agent_profile=agent_value,
                        connection=connection,
                        tool_name=remote_tool_name,
                    )
                    explicit_setting = True
                    approval_mode = (agent_tool_setting.approval_mode or "").strip()
                    operation_type = agent_tool_setting.operation_type
                except AgentMcpToolSetting.DoesNotExist:
                    agent_tool_setting = None

            if not explicit_setting:
                try:
                    tool_setting = McpConnectionToolSetting.objects.get(
                        connection=connection,
                        tool_name=remote_tool_name,
                    )
                    explicit_setting = True
                    approval_mode = (tool_setting.approval_mode or "").strip()
                    operation_type = tool_setting.operation_type
                except McpConnectionToolSetting.DoesNotExist:
                    tool_setting = None

            if not approval_mode:
                approval_mode = agent_default_mode or connection.default_approval_mode
        except Exception:
            approval_mode = agent_default_mode or connection.default_approval_mode
            operation_type = McpToolOperationType.UNKNOWN

    inferred_operation_type = operation_type
    inferred_by_heuristic = False
    if not explicit_setting and inferred_operation_type == McpToolOperationType.UNKNOWN:
        inferred_candidate = _infer_operation_type_from_tool_name(remote_tool_name)
        if inferred_candidate != McpToolOperationType.UNKNOWN:
            inferred_operation_type = inferred_candidate
            inferred_by_heuristic = True

    # Determine if approval is required based on mode
    if approval_mode == McpConnectionApprovalMode.AUTO:
        return {
            "requires_approval": False,
            "approval_mode": approval_mode,
            "operation_type": inferred_operation_type,
            "reason": "Auto-approve mode enabled",
        }

    if approval_mode == McpConnectionApprovalMode.APPROVE_ALL:
        return {
            "requires_approval": True,
            "approval_mode": approval_mode,
            "operation_type": inferred_operation_type,
            "reason": "All operations require approval",
        }

    # APPROVE_WRITES mode - check operation type
    if approval_mode == McpConnectionApprovalMode.APPROVE_WRITES:
        if inferred_operation_type == McpToolOperationType.READ:
            return {
                "requires_approval": False,
                "approval_mode": approval_mode,
                "operation_type": inferred_operation_type,
                "reason": (
                    "Read operation - auto-approved"
                    if not inferred_by_heuristic
                    else "Read operation (inferred) - auto-approved"
                ),
            }
        else:
            # WRITE or UNKNOWN - require approval
            return {
                "requires_approval": True,
                "approval_mode": approval_mode,
                "operation_type": inferred_operation_type,
                "reason": (
                    "Write operation requires approval"
                    if inferred_operation_type == McpToolOperationType.WRITE
                    else "Unknown operation type - treating as write"
                ),
            }

    # Fallback - require approval for safety
    return {
        "requires_approval": True,
        "approval_mode": approval_mode,
        "operation_type": operation_type,
        "reason": "Unknown approval mode - requiring approval for safety",
    }
