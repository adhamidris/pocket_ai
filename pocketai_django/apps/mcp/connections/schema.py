from __future__ import annotations

from typing import Any, Mapping

from django.conf import settings

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
