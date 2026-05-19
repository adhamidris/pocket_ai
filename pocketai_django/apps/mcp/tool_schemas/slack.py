from __future__ import annotations

from typing import Mapping

from .base import _function_schema


SLACK_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — Slack
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="slack_list_channels",
        description="List Slack channels the user can access.",
        properties={
            "max_results": {"type": "integer", "description": "Maximum channels to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    _function_schema(
        name="slack_read_channel",
        description="Read recent messages from a Slack channel.",
        properties={
            "channel_id": {"type": "string", "description": "Slack channel ID."},
            "limit": {"type": "integer", "description": "Maximum messages to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("channel_id",),
    ),
    _function_schema(
        name="slack_send_message",
        description="Send a message to a Slack channel.",
        properties={
            "channel_id": {"type": "string", "description": "Slack channel ID."},
            "text": {"type": "string", "description": "Message text to send."},
            "thread_ts": {"type": "string", "description": "Thread timestamp to reply in thread (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("channel_id", "text"),
    ),
    _function_schema(
        name="slack_search_messages",
        description="Search messages across the connected Slack workspace.",
        properties={
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Maximum results (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
)
