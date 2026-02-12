from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from django.conf import settings

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    McpConnectionApprovalMode,
    McpConnectionAuthType,
    McpConnectionStatus,
    McpToolOperationType,
)
from apps.mcp.models import (
    AgentMcpToolSetting,
    McpConnection,
    McpConnectionToolSetting,
)
from apps.accounts.oauth_helpers import OAuthFlowError, ensure_fresh_oauth_credentials


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


_REMOTE_TOOL_DESCRIPTION_MAX_CHARS_DEFAULT = 240
_REMOTE_TOOL_MAX_PROPERTIES_DEFAULT = 40
_REMOTE_TOOL_SCHEMA_MAX_DEPTH_DEFAULT = 3


def _safe_int_setting(value: object, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _clip_text(value: str, limit: int) -> str:
    text = str(value or "")
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _slim_json_schema_node(
    value: object,
    *,
    depth: int,
    max_depth: int,
    max_properties: int,
) -> object:
    if depth >= max_depth:
        if isinstance(value, Mapping):
            schema_type = value.get("type")
            if isinstance(schema_type, str) and schema_type.strip():
                return {"type": schema_type.strip()}
        return {"type": "object", "additionalProperties": True}

    if isinstance(value, list):
        return [
            _slim_json_schema_node(item, depth=depth + 1, max_depth=max_depth, max_properties=max_properties)
            for item in value[:4]
        ]

    if not isinstance(value, Mapping):
        return value

    schema_type = value.get("type")
    base_type = schema_type.strip() if isinstance(schema_type, str) and schema_type.strip() else ""
    properties = value.get("properties")
    if not base_type and isinstance(properties, Mapping):
        base_type = "object"

    # If we see refs/defs, collapse to a permissive object to avoid shipping large definition graphs.
    if any(key in value for key in ("$ref", "$defs", "definitions", "$schema")):
        return {"type": base_type or "object", "additionalProperties": True}

    out: dict[str, Any] = {}
    if base_type:
        out["type"] = base_type

    required = value.get("required")
    required_list: list[str] = []
    if isinstance(required, list):
        for item in required:
            if isinstance(item, str) and item.strip():
                required_list.append(item.strip())
    if required_list:
        out["required"] = required_list[:max_properties]

    if isinstance(properties, Mapping):
        selected_names: list[str] = []
        seen: set[str] = set()
        for name in required_list:
            if name and name not in seen and name in properties:
                selected_names.append(name)
                seen.add(name)
        for name in sorted(str(k) for k in properties.keys()):
            if len(selected_names) >= max_properties:
                break
            if not name or name in seen:
                continue
            selected_names.append(name)
            seen.add(name)
        slim_props: dict[str, Any] = {}
        for name in selected_names:
            schema = properties.get(name)
            slim_props[name] = _slim_json_schema_node(schema, depth=depth + 1, max_depth=max_depth, max_properties=max_properties)
        if slim_props:
            out["properties"] = slim_props

    items = value.get("items")
    if isinstance(items, (Mapping, list)):
        out["items"] = _slim_json_schema_node(items, depth=depth + 1, max_depth=max_depth, max_properties=max_properties)

    for key in ("enum", "oneOf", "anyOf", "allOf"):
        if key not in value:
            continue
        payload = value.get(key)
        if payload is None:
            continue
        out[key] = _slim_json_schema_node(payload, depth=depth + 1, max_depth=max_depth, max_properties=max_properties)

    if value.get("additionalProperties") is not None:
        out["additionalProperties"] = bool(value.get("additionalProperties"))

    # Keep only structure-relevant keys (drop descriptions/examples/metadata).
    allowed_keys = {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "oneOf",
        "anyOf",
        "allOf",
        "additionalProperties",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "pattern",
    }
    return {k: v for k, v in out.items() if k in allowed_keys and v is not None}


def _slim_remote_input_schema(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    Remote MCP servers sometimes publish very large JSON Schemas (OpenAPI-derived).
    Those count toward prompt tokens on every LLM call. We trim aggressively while
    keeping required + basic types so the model can still call tools correctly.
    """

    if not isinstance(schema, Mapping):
        return {"type": "object", "additionalProperties": True}

    max_properties = max(
        4,
        _safe_int_setting(
            getattr(settings, "MCP_REMOTE_TOOL_MAX_PROPERTIES", None),
            _REMOTE_TOOL_MAX_PROPERTIES_DEFAULT,
        ),
    )
    max_depth = max(
        2,
        _safe_int_setting(
            getattr(settings, "MCP_REMOTE_TOOL_SCHEMA_MAX_DEPTH", None),
            _REMOTE_TOOL_SCHEMA_MAX_DEPTH_DEFAULT,
        ),
    )

    slimmed = _slim_json_schema_node(schema, depth=0, max_depth=max_depth, max_properties=max_properties)
    if isinstance(slimmed, Mapping) and str(slimmed.get("type") or "").strip().lower() == "object":
        return dict(slimmed)
    return {"type": "object", "additionalProperties": True}


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
    if connection.auth_type == McpConnectionAuthType.BEARER:
        try:
            ensure_fresh_oauth_credentials(connection)
        except OAuthFlowError as exc:
            logger.warning("mcp_oauth_refresh_failed connection_id=%s error=%s", connection.id, str(exc)[:200])
        credentials = connection.credentials or {}
        token = str(credentials.get("token") or credentials.get("access_token") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}
    credentials = connection.credentials or {}
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
