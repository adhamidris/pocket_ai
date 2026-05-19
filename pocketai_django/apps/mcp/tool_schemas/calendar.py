from __future__ import annotations

from typing import Mapping

from .base import _function_schema


CALENDAR_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — Google Calendar
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="calendar_list_events",
        description="List upcoming events from the connected Google Calendar.",
        properties={
            "time_min": {"type": "string", "description": "Start of time range (ISO 8601 datetime). Defaults to now."},
            "time_max": {"type": "string", "description": "End of time range (ISO 8601 datetime)."},
            "query": {"type": "string", "description": "Free-text search query."},
            "max_results": {"type": "integer", "description": "Maximum events to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    _function_schema(
        name="calendar_get_event",
        description="Get details of a specific Google Calendar event by ID.",
        properties={
            "event_id": {"type": "string", "description": "Google Calendar event ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("event_id",),
    ),
    _function_schema(
        name="calendar_create_event",
        description="Create a new event on Google Calendar.",
        properties={
            "summary": {"type": "string", "description": "Event title."},
            "start_time": {"type": "string", "description": "Start datetime (ISO 8601, e.g. 2025-01-15T09:00:00-05:00)."},
            "end_time": {"type": "string", "description": "End datetime (ISO 8601)."},
            "description": {"type": "string", "description": "Event description (optional)."},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "Email addresses of attendees."},
            "location": {"type": "string", "description": "Event location (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("summary", "start_time", "end_time"),
    ),
    _function_schema(
        name="calendar_update_event",
        description="Update an existing Google Calendar event.",
        properties={
            "event_id": {"type": "string", "description": "Google Calendar event ID to update."},
            "summary": {"type": "string", "description": "New event title (optional)."},
            "start_time": {"type": "string", "description": "New start datetime (optional)."},
            "end_time": {"type": "string", "description": "New end datetime (optional)."},
            "description": {"type": "string", "description": "New description (optional)."},
            "location": {"type": "string", "description": "New location (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("event_id",),
    ),
)
