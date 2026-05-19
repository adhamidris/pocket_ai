"""
Handlers and helpers for the external MCP gateway tools.
"""

from __future__ import annotations

import re
from typing import Mapping

from apps.conversations.models import Conversation

from ..types import ToolExecutionContext


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)

def _mcp_gateway_mode_enabled(conversation: Conversation) -> bool:
    # Gateway mode is permanently enabled.
    del conversation
    return True


def _gateway_clip_text(value: object, limit: int) -> str:
    text = _coerce_str(value).strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _gateway_schema_type_hint(schema: object) -> str:
    if not isinstance(schema, Mapping):
        return "any"

    raw_type = schema.get("type")
    types: list[str] = []
    if isinstance(raw_type, str) and raw_type.strip():
        types = [raw_type.strip()]
    elif isinstance(raw_type, list):
        types = [str(entry).strip() for entry in raw_type if str(entry).strip()]

    if not types:
        for key in ("anyOf", "oneOf", "allOf"):
            options = schema.get(key)
            if not isinstance(options, list):
                continue
            option_types: list[str] = []
            for option in options[:8]:
                hint = _gateway_schema_type_hint(option)
                if hint and hint != "any":
                    option_types.append(hint)
            if option_types:
                types = option_types
                break

    if not types:
        if isinstance(schema.get("properties"), Mapping):
            return "object"
        if schema.get("items") is not None:
            return "array"
        return "any"

    if "array" in types:
        items = schema.get("items")
        item_hint = _gateway_schema_type_hint(items)
        return f"array<{item_hint}>"

    if len(types) == 1:
        return types[0]
    return "|".join(types[:4])


def _gateway_required_args(
    schema: object,
    *,
    satisfied_keys: set[str] | None = None,
    limit: int = 12,
) -> list[dict[str, str]]:
    if not isinstance(schema, Mapping):
        return []
    required = schema.get("required")
    if not isinstance(required, list) or not required:
        return []
    properties = schema.get("properties")
    props = properties if isinstance(properties, Mapping) else {}
    out: list[dict[str, str]] = []
    for key in required:
        name = str(key).strip()
        if not name:
            continue
        if satisfied_keys and name in satisfied_keys:
            continue
        hint = _gateway_schema_type_hint(props.get(name))
        out.append({"name": name, "type": hint})
        if len(out) >= limit:
            break
    return out


_GATEWAY_TOOL_VERB_HINTS: set[str] = {
    "add",
    "create",
    "delete",
    "describe",
    "edit",
    "fetch",
    "find",
    "get",
    "list",
    "lookup",
    "modify",
    "patch",
    "post",
    "put",
    "query",
    "read",
    "remove",
    "retrieve",
    "search",
    "send",
    "set",
    "show",
    "update",
    "upload",
    "view",
    "write",
}

_GATEWAY_VENDOR_TOKENS: set[str] = {
    "github",
    "gitlab",
    "jira",
    "slack",
    "notion",
    "linear",
    "google",
    "drive",
    "gmail",
}


def _gateway_tokenize(text: str) -> set[str]:
    tokens = {token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if token}
    expanded = set(tokens)

    for token in list(tokens):
        if len(token) < 4:
            continue
        if token.endswith("ies") and len(token) > 4:
            expanded.add(token[:-3] + "y")  # repositories -> repository
        if token.endswith("es") and not token.endswith("ies") and len(token) > 4:
            expanded.add(token[:-2])  # branches -> branch (best-effort)
        if token.endswith("s") and not token.endswith("ss") and len(token) > 4:
            expanded.add(token[:-1])  # issues -> issue

    if "repos" in expanded or "repo" in expanded:
        expanded.update({"repository", "repositories"})
    if "pr" in expanded:
        expanded.update({"pull", "request", "requests"})
    if "my" in expanded:
        expanded.update({"me", "user", "account", "profile"})

    return {token for token in expanded if token}


def _gateway_token_weight(token: str) -> int:
    normalized = str(token or "").strip().lower()
    if not normalized:
        return 0
    if normalized in _GATEWAY_TOOL_VERB_HINTS:
        return 1
    if normalized in _GATEWAY_VENDOR_TOKENS:
        return 2
    return 4


def _gateway_search_score(
    query_tokens: set[str],
    *,
    remote_tokens: set[str],
    description_tokens: set[str],
    connection_tokens: set[str],
    required_count: int,
) -> int:
    if not query_tokens:
        return 0

    score = 0

    overlap_remote = query_tokens.intersection(remote_tokens)
    overlap_description = query_tokens.intersection(description_tokens)
    overlap_connection = query_tokens.intersection(connection_tokens)

    score += sum(_gateway_token_weight(token) for token in overlap_remote) * 6
    score += sum(_gateway_token_weight(token) for token in overlap_description) * 2
    score += sum(_gateway_token_weight(token) for token in overlap_connection) * 1

    if required_count <= 0:
        score += 3
    elif required_count == 1:
        score += 2
    elif required_count >= 5:
        score -= 2

    return score


def _mcp_search_tools_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del conversation

    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return {
            "tool": "mcp_search_tools",
            "status": "error",
            "error": "missing_query",
            "error_code": "missing_query",
            "results": [],
            "hint": "Provide a natural-language query describing the tool you want (e.g. 'list GitHub repos').",
        }

    try:
        limit = int(arguments.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(10, limit))

    connection_filter = _coerce_str(arguments.get("connection_id")).strip()

    catalog = getattr(context, "mcp_gateway_catalog", None)
    if not isinstance(catalog, Mapping) or not catalog:
        return {
            "tool": "mcp_search_tools",
            "status": "ok",
            "results": [],
            "hint": "No external MCP tools are available for this agent.",
        }

    query_norm = query.lower()
    query_tokens = _gateway_tokenize(query_norm)

    scored: list[tuple[int, str, dict[str, object]]] = []
    for tool_id, meta in catalog.items():
        if not isinstance(meta, Mapping):
            continue
        conn_id = str(meta.get("connection_id") or "").strip()
        if connection_filter and conn_id != connection_filter:
            continue
        connection_name = _coerce_str(meta.get("connection_name")).strip()
        remote_tool = _coerce_str(meta.get("remote_tool")).strip()
        description = _coerce_str(meta.get("description")).strip()
        schema = meta.get("input_schema")
        satisfied_raw = meta.get("default_arg_keys")
        satisfied_keys = (
            {str(value).strip() for value in satisfied_raw if str(value).strip()}
            if isinstance(satisfied_raw, (list, tuple, set))
            else None
        )
        required_args = _gateway_required_args(schema, satisfied_keys=satisfied_keys)
        remote_tokens = _gateway_tokenize(remote_tool)
        description_tokens = _gateway_tokenize(description)
        connection_tokens = _gateway_tokenize(connection_name)
        score = _gateway_search_score(
            query_tokens,
            remote_tokens=remote_tokens,
            description_tokens=description_tokens,
            connection_tokens=connection_tokens,
            required_count=len(required_args),
        )
        if score <= 0:
            continue
        scored.append((score, tool_id, dict(meta)))

    scored.sort(key=lambda entry: (-entry[0], entry[1]))

    results: list[dict[str, object]] = []
    for _, tool_id, meta in scored[:limit]:
        schema = meta.get("input_schema")
        satisfied_raw = meta.get("default_arg_keys")
        satisfied_keys = (
            {str(value).strip() for value in satisfied_raw if str(value).strip()}
            if isinstance(satisfied_raw, (list, tuple, set))
            else None
        )
        results.append(
            {
                "tool_id": str(tool_id),
                "connection_name": _gateway_clip_text(meta.get("connection_name"), 80),
                "remote_tool": _gateway_clip_text(meta.get("remote_tool"), 80),
                "description": _gateway_clip_text(meta.get("description"), 240),
                "required_args": _gateway_required_args(schema, satisfied_keys=satisfied_keys),
            }
        )

    if not results:
        hint = "No tools matched that query."
        if connection_filter:
            hint = "No tools matched that query for the requested connection_id."
        return {
            "tool": "mcp_search_tools",
            "status": "ok",
            "results": [],
            "hint": hint,
        }

    return {
        "tool": "mcp_search_tools",
        "status": "ok",
        "results": results,
    }


def _mcp_call_tool_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del arguments, conversation, context
    return {
        "tool": "mcp_call_tool",
        "status": "error",
        "error": "handled_by_orchestrator",
        "error_code": "handled_by_orchestrator",
        "output": None,
        "hint": "mcp_call_tool is executed by the MCP orchestrator tool loop.",
    }