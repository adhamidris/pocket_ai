from __future__ import annotations

from typing import Mapping

from .base import _function_schema


EMAIL_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="email_search",
        description="Search the connected email mailbox (Google/Microsoft). Results are bounded and text-only.",
        properties={
            "query": {"type": "string", "description": "Search query (provider syntax may vary)."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
            "limit": {
                "type": "integer",
                "description": "Maximum number of results (1-25).",
                "minimum": 1,
                "maximum": 25,
                "default": 5,
            },
            "after": {"type": "string", "description": "Optional: ISO date/time lower bound."},
            "before": {"type": "string", "description": "Optional: ISO date/time upper bound."},
            "from": {"type": "string", "description": "Optional: filter sender email address."},
            "to": {"type": "string", "description": "Optional: filter recipient email address."},
            "subject": {"type": "string", "description": "Optional: filter subject contains."},
        },
        required=("query",),
    ),
    _function_schema(
        name="email_get_message",
        description="Fetch a specific email message by id (text-only).",
        properties={
            "message_id": {"type": "string", "description": "Provider message id."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
        },
        required=("message_id",),
    ),
    _function_schema(
        name="email_get_thread",
        description="Fetch a specific email thread by id (text-only).",
        properties={
            "thread_id": {"type": "string", "description": "Provider thread id."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
        },
        required=("thread_id",),
    ),
    _function_schema(
        name="email_create_draft",
        description="Create an email draft (text-only body).",
        properties={
            "to": {
                "type": "array",
                "description": "Primary recipients (email addresses).",
                "items": {"type": "string"},
            },
            "cc": {"type": "array", "items": {"type": "string"}},
            "bcc": {"type": "array", "items": {"type": "string"}},
            "subject": {"type": "string"},
            "body_text": {"type": "string", "description": "Plain text email body."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
        },
        required=("to", "subject", "body_text"),
    ),
    _function_schema(
        name="email_send_draft",
        description="Send a previously created draft (may require approval depending on policy).",
        properties={
            "draft_id": {
                "type": "string",
                "description": "Optional: provider draft id returned by email_create_draft. If omitted, the system will try to send the most recent pending draft in this conversation.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: UUID of a specific connected mailbox id (usually omit)."},
        },
        required=(),
    ),
)
