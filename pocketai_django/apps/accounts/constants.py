from __future__ import annotations

from typing import Mapping

FEATURE_FLAG_METADATA_KEY = "features"
FEATURE_FLAG_DEFAULTS: dict[str, bool] = {
    "alias_lookup": True,
    "entity_chunking": True,
    "hybrid_search": True,
    "rag_chunk_quality_filter": False,
    "rag_chunk_dedupe": False,
    "rag_alias_hygiene": False,
    "rag_text_chunk_penalty": True,
    "rag_shadow_ingestion": False,
    "rag_shadow_retrieval": False,
    "rag_eval_logging": False,
    "rag_agentic_mode": True,  # 2-tool retrieval: search (metadata) → read (content)
    # When enabled, expose a small "gateway" tool surface for external MCP tools
    # instead of inlining every remote tool schema into the LLM prompt.
    "mcp_gateway_mode": True,
    # Enable background sub-agents (agent runs, automations, watchers, inbox) for this business.
    "sub_agents_v1": False,
    # Enable the new standalone CRM runtime.
    "crm_v1": False,
}

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}


def coerce_feature_value(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        trimmed = value.strip().lower()
        if trimmed in _TRUE_VALUES:
            return True
        if trimmed in _FALSE_VALUES:
            return False
    return bool(default)


def sanitize_feature_payload(payload: Mapping[str, object] | None) -> dict[str, bool]:
    sanitized: dict[str, bool] = {}
    if isinstance(payload, Mapping):
        for key, raw in payload.items():
            if key in FEATURE_FLAG_DEFAULTS:
                sanitized[key] = coerce_feature_value(raw, FEATURE_FLAG_DEFAULTS[key])
    for key, default in FEATURE_FLAG_DEFAULTS.items():
        sanitized.setdefault(key, bool(default))
    return sanitized


__all__ = [
    "FEATURE_FLAG_DEFAULTS",
    "FEATURE_FLAG_METADATA_KEY",
    "coerce_feature_value",
    "sanitize_feature_payload",
]
