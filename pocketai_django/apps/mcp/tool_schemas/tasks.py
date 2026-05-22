from __future__ import annotations

from typing import Mapping

from .base import _function_schema


TASK_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="list_tasks",
        description="List saved Agentic Tasks for the current business, optionally filtered by owning agent or status.",
        properties={
            "agent_id": {"type": "string", "description": "Optional agent UUID."},
            "status": {"type": "string", "enum": ["draft", "active", "paused", "all"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        required=(),
    ),
    _function_schema(
        name="draft_agentic_task",
        description=(
            "Create an inactive Agentic Task draft. Use for persistent manual or scheduled task agents. "
            "Drafts must be approved by the user before activation. Only store fields visible in the task UI."
        ),
        properties={
            "agent_id": {"type": "string", "description": "Optional owning agent UUID. Defaults to the current agent."},
            "name": {"type": "string", "description": "Short task name."},
            "goal": {"type": "string", "description": "What the task should accomplish."},
            "schedule_enabled": {"type": "boolean", "description": "Enable cron runs for this task."},
            "schedule_config": {"type": "object", "additionalProperties": True},
            "visibility": {"type": "string", "enum": ["initiator", "managers", "workspace"]},
        },
        required=("name", "goal"),
    ),
    _function_schema(
        name="update_agentic_task",
        description="Update an existing saved Agentic Task draft or paused Agentic Task.",
        properties={
            "agentic_task_id": {"type": "string", "description": "Agentic Task UUID."},
            "name": {"type": "string"},
            "goal": {"type": "string"},
            "schedule_enabled": {"type": "boolean"},
            "schedule_config": {"type": "object", "additionalProperties": True},
            "visibility": {"type": "string", "enum": ["initiator", "managers", "workspace"]},
        },
        required=("agentic_task_id",),
    ),
    _function_schema(
        name="request_agentic_task_activation",
        description=(
            "Activate a saved Agentic Task only after explicit user approval. "
            "If approved is false or omitted, returns an approval-needed payload instead of activating."
        ),
        properties={
            "agentic_task_id": {"type": "string", "description": "Agentic Task UUID."},
            "approved": {"type": "boolean", "description": "Set true only after the user explicitly approves activation."},
        },
        required=("agentic_task_id",),
    ),
    _function_schema(
        name="pause_agentic_task",
        description="Pause an active saved Agentic Task.",
        properties={
            "agentic_task_id": {"type": "string", "description": "Agentic Task UUID."},
            "reason": {"type": "string"},
        },
        required=("agentic_task_id",),
    ),
)
