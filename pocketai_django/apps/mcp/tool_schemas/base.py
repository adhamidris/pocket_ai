from __future__ import annotations

from typing import Mapping

from django.conf import settings


DEFAULT_MAX_SEARCH_QUERY_VARIANTS = 1


def _search_query_variant_limit() -> int:
    try:
        value = int(
            getattr(
                settings,
                "MCP_SEARCH_MAX_QUERY_VARIANTS",
                DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
            )
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive
        value = DEFAULT_MAX_SEARCH_QUERY_VARIANTS
    return max(1, value)


def _search_queries_schema_description() -> str:
    limit = _search_query_variant_limit()
    if limit == 1:
        return "List of search queries. Use up to 1 short, specific variant."
    return f"List of search queries. Use up to {limit} short, specific variants."


try:
    _PROMPT_TOOL_OUTPUT_MAX_CHARS = int(getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", 12000) or 12000)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _PROMPT_TOOL_OUTPUT_MAX_CHARS = 12000
try:
    # Prefer the read_knowledge-specific knob, but retain the historical read_document name
    # as a backwards-compatible alias.
    _READ_KNOWLEDGE_MAX_CHARS_MARGIN = int(
        getattr(settings, "MCP_READ_KNOWLEDGE_MAX_CHARS_MARGIN", None)
        or getattr(settings, "MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", 800)
        or 800
    )
except (TypeError, ValueError):  # pragma: no cover - defensive
    _READ_KNOWLEDGE_MAX_CHARS_MARGIN = 800
_READ_KNOWLEDGE_SAFE_PROMPT_MAX_CHARS = max(500, _PROMPT_TOOL_OUTPUT_MAX_CHARS - max(0, _READ_KNOWLEDGE_MAX_CHARS_MARGIN))
READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX = max(500, min(20000, _READ_KNOWLEDGE_SAFE_PROMPT_MAX_CHARS))
READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT = max(500, min(8000, READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX))

try:
    _MCP_PROMPT_MAX_SNIPPETS = int(getattr(settings, "MCP_PROMPT_MAX_SNIPPETS", 4) or 4)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _MCP_PROMPT_MAX_SNIPPETS = 4

# Single source of truth: how many snippet items the LLM is allowed to see per tool call.
MCP_PROMPT_MAX_SNIPPETS_CAP = max(1, _MCP_PROMPT_MAX_SNIPPETS)

# Keep the tool schema aligned with the runtime cap so the LLM can request up to the true limit.
SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX = MCP_PROMPT_MAX_SNIPPETS_CAP
try:
    _SEARCH_KNOWLEDGE_DEFAULT_LIMIT = int(getattr(settings, "MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT", 10) or 10)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _SEARCH_KNOWLEDGE_DEFAULT_LIMIT = 10
SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT = max(
    1,
    min(int(_SEARCH_KNOWLEDGE_DEFAULT_LIMIT), SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX),
)

# Prefetch cap: allows cursor caching to store more candidates than the per-page limit.
# This is intentionally higher than MCP_PROMPT_MAX_SNIPPETS_CAP so pagination has results to page through.
SEARCH_PREFETCH_ABSOLUTE_CAP = 200


def _function_schema(
    *,
    name: str,
    description: str,
    properties: Mapping[str, Mapping[str, object]],
    required: tuple[str, ...] = (),
) -> Mapping[str, object]:
    """Helper to keep tool definitions concise and consistent."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        },
    }
