from __future__ import annotations

from apps.mcp.connections.approval import (
    _TOOL_READ_HINTS,
    _TOOL_WRITE_HINTS,
    _infer_operation_type_from_tool_name,
    get_tool_approval_requirement,
)
from apps.mcp.connections.auth import (
    list_enabled_mcp_connections_for_agent,
    mcp_connection_auth_headers,
)
from apps.mcp.connections.cache import _is_cache_expired
from apps.mcp.connections.descriptors import (
    RemoteToolDescriptor,
    build_remote_tool_definitions,
    build_remote_tool_definitions_from_descriptors,
    list_remote_tool_descriptors,
)
from apps.mcp.connections.naming import (
    _TOOL_SAFE_PATTERN,
    _normalize_tool_fragment,
    build_remote_tool_name,
)
from apps.mcp.connections.schema import (
    _REMOTE_TOOL_DESCRIPTION_MAX_CHARS_DEFAULT,
    _REMOTE_TOOL_MAX_PROPERTIES_DEFAULT,
    _REMOTE_TOOL_SCHEMA_MAX_DEPTH_DEFAULT,
    _clip_text,
    _safe_int_setting,
    _slim_json_schema_node,
    _slim_remote_input_schema,
)
