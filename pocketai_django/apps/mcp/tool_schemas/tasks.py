from __future__ import annotations

from typing import Mapping

from .base import _function_schema


TASK_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="list_tasks",
        description="List saved automations for the current business, optionally filtered by owning assistant or status.",
        properties={
            "agent_id": {"type": "string", "description": "Optional agent UUID."},
            "status": {"type": "string", "enum": ["draft", "active", "paused", "all"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        required=(),
    ),
    _function_schema(
        name="draft_task",
        description=(
            "Create an inactive automation draft. Use for persistent scheduled, webhook, or email inbox tasks. "
            "Drafts must be approved by the user before activation. Only store fields visible in the task UI."
        ),
        properties={
            "agent_id": {"type": "string", "description": "Optional owning agent UUID. Defaults to the current agent."},
            "name": {"type": "string", "description": "Short task name."},
            "goal": {"type": "string", "description": "What the task should accomplish."},
            "trigger_type": {"type": "string", "enum": ["schedule", "webhook", "email_inbox"]},
            "trigger_config": {"type": "object", "additionalProperties": True},
            "source_config": {"type": "object", "additionalProperties": True},
            "visibility": {"type": "string", "enum": ["initiator", "managers", "workspace"]},
        },
        required=("name", "goal"),
    ),
    _function_schema(
        name="update_task",
        description="Update an existing saved automation draft or paused automation.",
        properties={
            "task_id": {"type": "string", "description": "Automation/task UUID."},
            "name": {"type": "string"},
            "goal": {"type": "string"},
            "trigger_type": {"type": "string", "enum": ["schedule", "webhook", "email_inbox"]},
            "trigger_config": {"type": "object", "additionalProperties": True},
            "source_config": {"type": "object", "additionalProperties": True},
            "visibility": {"type": "string", "enum": ["initiator", "managers", "workspace"]},
        },
        required=("task_id",),
    ),
    _function_schema(
        name="request_task_activation",
        description=(
            "Activate a saved task only after explicit user approval. "
            "If approved is false or omitted, returns an approval-needed payload instead of activating."
        ),
        properties={
            "task_id": {"type": "string", "description": "Automation/task UUID."},
            "approved": {"type": "boolean", "description": "Set true only after the user explicitly approves activation."},
        },
        required=("task_id",),
    ),
    _function_schema(
        name="pause_task",
        description="Pause an active saved automation.",
        properties={
            "task_id": {"type": "string", "description": "Automation/task UUID."},
            "reason": {"type": "string"},
        },
        required=("task_id",),
    ),
)
