from __future__ import annotations

from typing import Mapping

from .base import _function_schema


PHONE_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="initiate_phone_call",
        description=(
            "Initiate a single outbound phone call. "
            "Creates a queued CallSession that will be executed by the voice_call_worker."
        ),
        properties={
            "phone_number": {
                "type": "string",
                "description": "Destination number in E.164 format (e.g., +201234567890).",
            },
            "objective": {
                "type": "string",
                "description": "Short, concrete purpose for the call (what the agent must accomplish).",
            },
            "call_type": {
                "type": "string",
                "description": "Type of call (service or marketing). Marketing calls may be blocked by policy.",
                "enum": ["service", "marketing"],
                "default": "service",
            },
            "language": {
                "type": "string",
                "description": "Call language (en or ar).",
                "enum": ["en", "ar"],
                "default": "en",
            },
            "max_duration_minutes": {
                "type": "integer",
                "description": "Upper bound for call duration (enforced by policy).",
                "minimum": 1,
                "maximum": 60,
                "default": 10,
            },
            "context_items": {
                "type": "array",
                "description": "Optional structured notes to attach to the call session.",
                "items": {"type": "object", "additionalProperties": True},
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("phone_number", "objective"),
    ),
)
