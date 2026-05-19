from __future__ import annotations

import re
from typing import Any

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    McpConnectionApprovalMode,
    McpToolOperationType,
)
from apps.mcp.models import (
    AgentMcpToolSetting,
    McpConnection,
    McpConnectionToolSetting,
)

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
