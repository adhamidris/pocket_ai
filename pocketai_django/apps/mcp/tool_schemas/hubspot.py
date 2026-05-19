from __future__ import annotations

from typing import Mapping

from .base import _function_schema


HUBSPOT_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — HubSpot
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="hubspot_search_contacts",
        description="Search contacts in the connected HubSpot CRM.",
        properties={
            "query": {"type": "string", "description": "Search query (name, email, company, etc.)."},
            "max_results": {"type": "integer", "description": "Maximum results (1-100).", "minimum": 1, "maximum": 100, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="hubspot_get_contact",
        description="Get details of a specific HubSpot contact by ID.",
        properties={
            "contact_id": {"type": "string", "description": "HubSpot contact ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("contact_id",),
    ),
    _function_schema(
        name="hubspot_create_contact",
        description="Create a new contact in HubSpot CRM.",
        properties={
            "email": {"type": "string", "description": "Contact email address (required)."},
            "first_name": {"type": "string", "description": "First name (optional)."},
            "last_name": {"type": "string", "description": "Last name (optional)."},
            "phone": {"type": "string", "description": "Phone number (optional)."},
            "company": {"type": "string", "description": "Company name (optional)."},
            "job_title": {"type": "string", "description": "Job title (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("email",),
    ),
    _function_schema(
        name="hubspot_search_deals",
        description="Search deals in the connected HubSpot CRM.",
        properties={
            "query": {"type": "string", "description": "Search query (deal name, etc.)."},
            "max_results": {"type": "integer", "description": "Maximum results (1-100).", "minimum": 1, "maximum": 100, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
)
