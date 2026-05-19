from __future__ import annotations

from typing import Mapping

from .base import (
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
    _function_schema,
    _search_queries_schema_description,
    _search_query_variant_limit,
)


KNOWLEDGE_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="search_knowledge",
        description="Search the knowledge base using a natural-language query.",
        properties={
            "cursor": {
                "type": "string",
                "description": "Opaque cursor from a prior search_knowledge response to fetch the next page.",
            },
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": _search_query_variant_limit(),
                "description": _search_queries_schema_description(),
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
        },
        required=(),
    ),
    _function_schema(
        name="read_knowledge",
        description=(
            "Read canonical evidence from the knowledge base (agentic contract). "
            "Provide refs from search_knowledge; include cursors only when continuing a partial read."
        ),
        properties={
            "refs": {
                "type": "array",
                "minItems": 1,
                "description": "List of refs to read. Each item is {id} or {id,cursor}.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Ref id from search_knowledge refs[].id.",
                        },
                        "cursor": {
                            "type": "string",
                            "description": "Opaque continuation cursor from a previous read_knowledge response.",
                        },
                        "row_start": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "For table refs only: 0-based row offset within the returned table body "
                                "(excluding header/separator rows)."
                            ),
                        },
                        "row_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": "For table refs only: maximum number of rows to return for this read.",
                        },
                    },
                    "required": ["id"],
                },
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
            "max_chars": {
                "type": "integer",
                "description": "Maximum total characters to return across all refs (bounded by server caps).",
                "minimum": 500,
                "maximum": READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
                "default": READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
            },
        },
        required=("refs", "max_chars"),
    ),
)
