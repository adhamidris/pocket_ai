from __future__ import annotations

from typing import Mapping

from .base import _function_schema


AGENT_RUN_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="start_agent_run",
        description=(
            "Create a background AgentRun (background agent) anchored to this conversation. "
            "Use this when the visitor asks for a long-running or multi-step task so the chat can continue "
            "while the work happens in the Activity panel."
        ),
        properties={
            "goal": {
                "type": "string",
                "description": "Clear task goal for the background run.",
            },
            "title": {
                "type": "string",
                "description": "Optional short title shown in the Activity panel.",
            },
            "followup_mode": {
                "type": "string",
                "enum": ["handoff", "supervisor"],
                "description": (
                    "Optional: how results should be reported back into chat. "
                    "`handoff` posts the run output directly; `supervisor` is reserved for manager-style synthesis."
                ),
            },
            "success_criteria": {
                "type": "array",
                "description": "Optional list of success criteria (1-10).",
                "items": {"type": "string"},
            },
            "constraints": {
                "type": "object",
                "description": "Optional execution constraints (timeouts, max steps, max tool calls).",
                "additionalProperties": True,
            },
            "output_schema": {
                "type": "object",
                "description": "Optional expected output schema (JSON Schema-like).",
                "additionalProperties": True,
            },
            "approval": {
                "type": "object",
                "description": "Optional approval policy metadata for downstream tools.",
                "additionalProperties": True,
            },
            "visibility": {
                "type": "string",
                "enum": ["initiator", "managers", "workspace"],
                "description": "Who can view this run (teams/roles are pending).",
            },
            "plan": {
                "type": "object",
                "description": "Optional planner output to display in the Activity panel.",
                "additionalProperties": True,
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata for routing/output destinations.",
                "additionalProperties": True,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "additionalProperties": True,
            },
        },
        required=("goal",),
    ),
    _function_schema(
        name="list_agent_runs",
        description=(
            "List background runs (agent workforce) for this conversation. "
            "Returns status, title, and summary for each run so you can track progress and results."
        ),
        properties={
            "status_filter": {
                "type": "string",
                "enum": ["all", "active", "completed", "waiting"],
                "description": "Filter runs by status. 'active' = queued/running, 'waiting' = needs user/approval, 'completed' = finished/failed/cancelled.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Max runs to return (default 10).",
            },
            "refresh": {
                "type": "boolean",
                "description": "Bypass cache and fetch latest runs (default false).",
            },
        },
        required=(),
    ),
    _function_schema(
        name="get_agent_run",
        description=(
            "Get detailed status and result of a specific background run. "
            "Use this after list_agent_runs to check on a particular task."
        ),
        properties={
            "run_id": {
                "type": "string",
                "description": "UUID of the agent run to retrieve.",
            },
            "include_events": {
                "type": "boolean",
                "description": "Include recent execution events (default false).",
            },
        },
        required=("run_id",),
    ),
    _function_schema(
        name="continue_agent_run",
        description=(
            "Continue an existing background run (background agent) with a follow-up message. "
            "Use this to send additional instructions to a completed or waiting run instead of creating a new one. "
            "The background agent will resume with its full conversation history."
        ),
        properties={
            "run_id": {
                "type": "string",
                "description": "UUID of the agent run to continue.",
            },
            "message": {
                "type": "string",
                "description": "Follow-up instruction or message for the background agent.",
            },
        },
        required=("run_id", "message"),
    ),
)
