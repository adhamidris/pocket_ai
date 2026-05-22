from __future__ import annotations

from typing import Mapping

from .base import _function_schema


MEMORY_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="search_memory",
        description="Search scoped long-term memory for relevant facts, preferences, decisions, or agentic_task state.",
        properties={
            "query": {"type": "string", "description": "Search text."},
            "scope": {"type": "string", "enum": ["workspace", "agent", "agentic_task", "run", "conversation", "crm_contact", "crm_company"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        required=("query",),
    ),
    _function_schema(
        name="save_memory",
        description="Save a scoped memory item. Sensitive or behavior-changing memories are routed to review.",
        properties={
            "content": {"type": "string", "description": "Memory content."},
            "kind": {"type": "string", "enum": ["fact", "preference", "policy", "decision", "instruction", "relationship", "state_note", "artifact_ref", "extracted_data"]},
            "scope": {"type": "string", "enum": ["workspace", "agent", "agentic_task", "run", "conversation"]},
            "key": {"type": "string"},
            "sensitivity": {"type": "string", "enum": ["normal", "sensitive", "secret"]},
            "visibility": {"type": "string", "enum": ["private", "shared"]},
        },
        required=("content",),
    ),
    _function_schema(
        name="forget_memory",
        description="Archive a memory item that is stale, wrong, or no longer needed.",
        properties={"memory_id": {"type": "string", "description": "UUID of the memory item to archive."}},
        required=("memory_id",),
    ),
)
