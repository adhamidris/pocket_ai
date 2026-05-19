from __future__ import annotations

from typing import Mapping

from .base import _function_schema


GATEWAY_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    _function_schema(
        name="mcp_search_tools",
        description=(
            "Search available external MCP tools and return a small list of candidates. "
            "Results include tool_id (stable identifier), connection_name, remote_tool, description, "
            "and required_args (names + types only)."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "Natural-language description of what you want to do (e.g. 'list GitHub repos').",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of tools to return (1-10).",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
            "connection_id": {
                "type": "string",
                "description": "Optional: restrict search to a specific MCP connection id.",
            },
        },
        required=("query",),
    ),
    _function_schema(
        name="mcp_call_tool",
        description=(
            "Call an external MCP tool by tool_id. "
            "Provide arguments as an object. Returns status + output and may include approval metadata."
        ),
        properties={
            "tool_id": {
                "type": "string",
                "description": "Tool identifier from mcp_search_tools results[].tool_id.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "arguments": {
                "type": "object",
                "description": "Arguments for the selected tool.",
                "additionalProperties": True,
            },
        },
        required=("tool_id", "arguments"),
    ),
)
