from __future__ import annotations

from typing import Mapping

from .base import _function_schema


CORE_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    _function_schema(
        name="retrieve_earlier_context",
        description=(
            "Retrieve detailed context from earlier conversation history that was compacted. "
            "Use this when you need specific facts or messages from earlier turns."
        ),
        properties={
            "query": {"type": "string", "description": "What to search for (keywords or a short question)."},
            "segment_id": {
                "type": "string",
                "description": "Optional: exact compacted segment id returned by a previous retrieve_earlier_context call.",
            },
            "timeframe": {
                "type": "string",
                "description": "Which part of history to search.",
                "enum": ["first_10_turns", "turns_10_to_20", "recent", "oldest", "all"],
            },
            "include_full_segment": {
                "type": "boolean",
                "description": "When true, return the full compacted segment messages (may be large).",
            },
            "max_messages": {
                "type": "integer",
                "description": "Maximum messages to return (applies to both matched and full segment output).",
                "minimum": 1,
                "maximum": 50,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=(),
    ),
    _function_schema(
        name="portal_emit_blocks",
        description=(
            "Emit structured block events for the portal UI. Use this to stream the final visitor-facing answer "
            "instead of plain text. Send incremental block_start/block_delta/block_end events."
        ),
        properties={
            "events": {
                "type": "array",
                "description": "Ordered list of block events to apply.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Event type: block_start, block_delta, or block_end.",
                        },
                        "block": {
                            "type": "object",
                            "description": "Block payload for block_start (block_id, type, payload, parent_block_id).",
                        },
                        "block_id": {"type": "string", "description": "Target block id for block_delta/block_end."},
                        "ops": {
                            "type": "array",
                            "description": "Operations for block_delta (append_inline or append_code).",
                            "items": {"type": "object"},
                        },
                    },
                    "required": ["type"],
                },
            }
        },
        required=("events",),
    ),
    _function_schema(
        name="request_user_input",
        description=(
            "Request missing information from the end user. "
            "Use this when running background tasks that must pause until the user responds."
        ),
        properties={
            "prompt": {
                "type": "string",
                "description": "Primary question/prompt for the user (freeform).",
            },
            "questions": {
                "type": "array",
                "description": "Optional list of crisp questions to ask the user.",
                "items": {"type": "string"},
            },
            "schema": {
                "type": "object",
                "description": "Optional structured schema for the user's response (UI hint only).",
                "additionalProperties": True,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "additionalProperties": True,
            },
        },
        required=(),
    ),
)
