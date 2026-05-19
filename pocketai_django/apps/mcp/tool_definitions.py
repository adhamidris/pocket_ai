"""
LLM-facing MCP tool schemas.

This module keeps declarative tool schemas separate from the runtime handlers
in apps.mcp.tools.
"""

from __future__ import annotations

import copy
from typing import Mapping

from .tool_schemas.agent_runs import AGENT_RUN_TOOL_DEFINITIONS
from .tool_schemas.base import (
    DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
    MCP_PROMPT_MAX_SNIPPETS_CAP,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
    SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT,
    SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX,
    SEARCH_PREFETCH_ABSOLUTE_CAP,
    _function_schema,
    _search_query_variant_limit,
    _search_queries_schema_description,
)
from .tool_schemas.calendar import CALENDAR_TOOL_DEFINITIONS
from .tool_schemas.cloud_files import CLOUD_FILE_TOOL_DEFINITIONS
from .tool_schemas.conversation_files import CONVERSATION_FILE_TOOL_DEFINITIONS
from .tool_schemas.core import CORE_TOOL_DEFINITIONS
from .tool_schemas.email import EMAIL_TOOL_DEFINITIONS
from .tool_schemas.gateway import GATEWAY_TOOL_DEFINITIONS
from .tool_schemas.hubspot import HUBSPOT_TOOL_DEFINITIONS
from .tool_schemas.knowledge import KNOWLEDGE_TOOL_DEFINITIONS
from .tool_schemas.memory import MEMORY_TOOL_DEFINITIONS
from .tool_schemas.phone import PHONE_TOOL_DEFINITIONS
from .tool_schemas.slack import SLACK_TOOL_DEFINITIONS
from .tool_schemas.tasks import TASK_TOOL_DEFINITIONS




TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    *CORE_TOOL_DEFINITIONS,
    *AGENT_RUN_TOOL_DEFINITIONS,
    *TASK_TOOL_DEFINITIONS,
    *MEMORY_TOOL_DEFINITIONS,
    *KNOWLEDGE_TOOL_DEFINITIONS[:1],
    *CONVERSATION_FILE_TOOL_DEFINITIONS,
    *KNOWLEDGE_TOOL_DEFINITIONS[1:],
    *EMAIL_TOOL_DEFINITIONS,
    *PHONE_TOOL_DEFINITIONS,
    *CALENDAR_TOOL_DEFINITIONS,
    *CLOUD_FILE_TOOL_DEFINITIONS,
    *SLACK_TOOL_DEFINITIONS,
    *HUBSPOT_TOOL_DEFINITIONS,
)


def get_tool_definitions() -> tuple[Mapping[str, object], ...]:
    """
    Return tool schemas with runtime-tuned descriptions.

    Some guidance text depends on current settings (for example
    MCP_SEARCH_MAX_QUERY_VARIANTS), so we patch those fields per request.
    """

    definitions: list[Mapping[str, object]] = copy.deepcopy(list(TOOL_DEFINITIONS))
    queries_description = _search_queries_schema_description()

    for tool_def in definitions:
        function_block = tool_def.get("function")
        if not isinstance(function_block, Mapping):
            continue
        if str(function_block.get("name") or "").strip() != "search_knowledge":
            continue
        parameters = function_block.get("parameters")
        if not isinstance(parameters, Mapping):
            break
        properties = parameters.get("properties")
        if not isinstance(properties, Mapping):
            break
        queries_schema = properties.get("queries")
        if isinstance(queries_schema, dict):
            queries_schema["description"] = queries_description
        break
    return tuple(definitions)
