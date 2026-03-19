"""
Tool registry and dispatcher for MCP orchestration.

This module will eventually expose two public artifacts:
1. TOOL_DEFINITIONS – JSON schemas advertised to the LLM provider.
2. execute_tool(...) – server-side implementation of each tool call.

For phase one we only anchor the structure so future phases can iterate without
touching unrelated parts of the codebase.
"""

from __future__ import annotations

import copy
import csv
import gzip
import base64
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from django.db import models
from django.db.models import Prefetch
from django.db.models.functions import Length
from django.core.cache import cache
from django.core import signing
from django.conf import settings

from apps.accounts.models import (
    AgentProfile,
    EmailAccountProvider,
    EmailAccountStatus,
    IntegrationAccountStatus,
    IntegrationType,
    McpToolOperationType,
    KnowledgeAuditAction,
    KnowledgeVisibility,
    KnowledgeStatus,
)
from apps.knowledge.models import (
    KnowledgeAuditEvent,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    KnowledgeUploadTableCell,
)
from apps.integrations.models import (
    EmailAccount,
    IntegrationAccount,
)
from apps.conversations.models import (
    AgentRun,
    AgentRunEvent,
    AgentRunStatus,
    Conversation,
    ConversationFile,
    ConversationFileChunk,
)
from apps.rag.ai_orchestrator import (
    ActionType,
    AiOrchestratorService,
    KnowledgeSearchService,
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
)
from apps.knowledge.knowledge_access import apply_customer_visible_chunks, apply_customer_visible_uploads
from apps.knowledge.privacy import sha256_hex
from apps.rag.rag_logging import structured_log
from apps.core.logging_utils import log_start, log_success, log_warning, log_performance, LogEmoji
from apps.rag.tabular_limits import ToolRateLimit, enforce_tool_rate_limit
from core.metrics import latency_monitor
from core.tenancy import tenant_context
from .types import (
    ChunkPageBudgetExceeded,
    ChunkReadBudgetExceeded,
    SearchBudgetExceeded,
    ToolConstraintError,
    ToolExecutionContext,
    ToolRateLimitExceeded,
    CharacterBudgetExceeded,
)
from .tool_artifacts import store_local_tool_output_artifact
from .models import McpToolOutputArtifact
from apps.accounts.feature_flags import FeatureFlagService
from apps.integrations.email_accounts import ensure_fresh_email_credentials
from apps.integrations.gmail import (
    GmailApiError,
    build_gmail_query,
    gmail_create_draft,
    gmail_get_message,
    gmail_get_thread,
    gmail_search_messages,
    gmail_send_draft,
)
from apps.integrations.microsoft_graph import (
    GraphApiError,
    graph_create_draft,
    graph_get_message,
    graph_get_thread,
    graph_search_messages,
    graph_send_draft,
)
from apps.integrations.integration_accounts import ensure_fresh_integration_credentials
from apps.integrations.google_calendar_api import (
    CalendarApiError,
    calendar_list_events,
    calendar_get_event,
    calendar_create_event,
    calendar_update_event,
)
from apps.integrations.google_drive_native_api import (
    DriveApiError,
    drive_search_files,
    drive_get_file_content,
    drive_list_files,
)
from apps.integrations.microsoft_onedrive_api import (
    OneDriveApiError,
    onedrive_search_files,
    onedrive_get_file_content,
    onedrive_list_files,
)
from apps.integrations.slack_api import (
    SlackApiError,
    slack_list_channels,
    slack_read_channel,
    slack_send_message,
    slack_search_messages,
)
from apps.integrations.hubspot_api import (
    HubSpotApiError,
    hubspot_search_contacts,
    hubspot_get_contact,
    hubspot_create_contact,
    hubspot_search_deals,
)

try:
    import duckdb  # type: ignore
except Exception:  # pragma: no cover
    duckdb = None  # type: ignore


logger = logging.getLogger(__name__)
# Default to one query per user turn for latency predictability.
# Additional query variants (fanout) can be enabled via `MCP_SEARCH_MAX_QUERY_VARIANTS`.
DEFAULT_MAX_SEARCH_QUERY_VARIANTS = 1
MCP_LOG_PII_DEFAULT = False
MCP_LOG_SNIPPET_PREVIEWS_DEFAULT = False
MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT = False


def _search_query_variant_limit() -> int:
    try:
        value = int(
            getattr(
                settings,
                "MCP_SEARCH_MAX_QUERY_VARIANTS",
                DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
            )
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive
        value = DEFAULT_MAX_SEARCH_QUERY_VARIANTS
    return max(1, value)


def _search_queries_schema_description() -> str:
    limit = _search_query_variant_limit()
    if limit == 1:
        return "List of search queries. Use up to 1 short, specific variant."
    return f"List of search queries. Use up to {limit} short, specific variants."


try:
    _PROMPT_TOOL_OUTPUT_MAX_CHARS = int(getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", 12000) or 12000)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _PROMPT_TOOL_OUTPUT_MAX_CHARS = 12000
try:
    # Prefer the read_knowledge-specific knob, but retain the historical read_document name
    # as a backwards-compatible alias.
    _READ_KNOWLEDGE_MAX_CHARS_MARGIN = int(
        getattr(settings, "MCP_READ_KNOWLEDGE_MAX_CHARS_MARGIN", None)
        or getattr(settings, "MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", 800)
        or 800
    )
except (TypeError, ValueError):  # pragma: no cover - defensive
    _READ_KNOWLEDGE_MAX_CHARS_MARGIN = 800
_READ_KNOWLEDGE_SAFE_PROMPT_MAX_CHARS = max(500, _PROMPT_TOOL_OUTPUT_MAX_CHARS - max(0, _READ_KNOWLEDGE_MAX_CHARS_MARGIN))
READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX = max(500, min(20000, _READ_KNOWLEDGE_SAFE_PROMPT_MAX_CHARS))
READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT = max(500, min(8000, READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX))

try:
    _MCP_PROMPT_MAX_SNIPPETS = int(getattr(settings, "MCP_PROMPT_MAX_SNIPPETS", 4) or 4)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _MCP_PROMPT_MAX_SNIPPETS = 4

# Single source of truth: how many snippet items the LLM is allowed to see per tool call.
MCP_PROMPT_MAX_SNIPPETS_CAP = max(1, _MCP_PROMPT_MAX_SNIPPETS)

# Keep the tool schema aligned with the runtime cap so the LLM can request up to the true limit.
SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX = MCP_PROMPT_MAX_SNIPPETS_CAP
try:
    _SEARCH_KNOWLEDGE_DEFAULT_LIMIT = int(getattr(settings, "MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT", 10) or 10)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _SEARCH_KNOWLEDGE_DEFAULT_LIMIT = 10
SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT = max(
    1,
    min(int(_SEARCH_KNOWLEDGE_DEFAULT_LIMIT), SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX),
)

# Prefetch cap: allows cursor caching to store more candidates than the per-page limit.
# This is intentionally higher than MCP_PROMPT_MAX_SNIPPETS_CAP so pagination has results to page through.
SEARCH_PREFETCH_ABSOLUTE_CAP = 200

# search_knowledge pagination (cursor) helpers
SEARCH_KNOWLEDGE_CURSOR_SALT = "mcp.search_knowledge.cursor.v1"
SEARCH_KNOWLEDGE_CURSOR_CACHE_PREFIX = "mcp:search_knowledge:cursor:v1"
def _search_cursor_cache_key(*, conversation: Conversation, session_id: str) -> str:
    return (
        f"{SEARCH_KNOWLEDGE_CURSOR_CACHE_PREFIX}:"
        f"{conversation.business_profile_id}:"
        f"{conversation.id}:"
        f"{session_id}"
    )


def _encode_search_cursor(*, session_id: str, offset: int) -> str:
    payload = {"sid": str(session_id), "o": int(offset)}
    return signing.dumps(payload, salt=SEARCH_KNOWLEDGE_CURSOR_SALT)


def _decode_search_cursor(token: str, *, max_age_seconds: int) -> dict[str, object] | None:
    try:
        decoded = signing.loads(token, salt=SEARCH_KNOWLEDGE_CURSOR_SALT, max_age=max_age_seconds)
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _mcp_log_pii_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_PII", MCP_LOG_PII_DEFAULT))


def _mcp_log_snippet_previews_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_SNIPPET_PREVIEWS", MCP_LOG_SNIPPET_PREVIEWS_DEFAULT))


def _mcp_log_full_snippet_content_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_FULL_SNIPPET_CONTENT", MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT))


def _log_safe_text_fields(field: str, value: str | None) -> dict[str, object]:
    if value is None:
        return {}
    text = str(value)
    if not text:
        return {}
    if _mcp_log_pii_enabled():
        return {field: text, f"{field}_len": len(text)}
    return {f"{field}_sha256": sha256_hex(text), f"{field}_len": len(text)}


def _record_knowledge_audit_event_once(
    *,
    context: ToolExecutionContext,
    conversation: Conversation,
    upload: KnowledgeUpload,
    action: str,
    engine: str | None,
    status: str | None,
    metadata: Mapping[str, object] | None = None,
) -> None:
    if not upload or not upload.id:
        return
    action_value = str(action or "").strip() or KnowledgeAuditAction.READ
    engine_value = str(engine or "").strip()
    status_value = str(status or "").strip()
    fingerprint = (str(conversation.id), str(upload.id), action_value)
    try:
        if fingerprint in context.audit_event_fingerprints:
            return
        context.audit_event_fingerprints.add(fingerprint)
    except Exception:
        pass
    safe_metadata: dict[str, object] = {
        "tool": "read_knowledge",
        "engine": engine_value or None,
        "status": status_value or None,
        "conversation_id": str(conversation.id),
    }
    try:
        label = (
            getattr(upload, "display_name", None)
            or getattr(upload, "source_name", None)
            or getattr(upload, "external_reference", None)
            or ""
        )
        safe_metadata.update(_log_safe_text_fields("upload_label", str(label).strip() or None))
    except Exception:
        pass
    if metadata:
        for key, value in dict(metadata).items():
            if value in (None, "", [], {}):
                continue
            safe_metadata[key] = value
    try:
        KnowledgeAuditEvent.objects.create(
            business_profile=upload.business_profile,
            upload=upload,
            upload_id_snapshot=upload.id,
            actor_agent=conversation.agent_profile,
            action=action_value,
            description="Knowledge accessed via tool call.",
            metadata=safe_metadata,
        )
    except Exception:
        logger.exception(
            "knowledge.audit_event_failed business=%s upload=%s action=%s",
            getattr(upload, "business_profile_id", None),
            getattr(upload, "id", None),
            action_value,
        )


def _has_prompt_evidence(envelope: Mapping[str, object]) -> bool:
    evidence = envelope.get("evidence") if isinstance(envelope.get("evidence"), Mapping) else {}
    rows = evidence.get("rows") if isinstance(evidence.get("rows"), list) else []
    snippets = evidence.get("snippets") if isinstance(evidence.get("snippets"), list) else []
    return bool(rows or snippets)


def _function_schema(
    *,
    name: str,
    description: str,
    properties: Mapping[str, Mapping[str, object]],
    required: tuple[str, ...] = (),
) -> Mapping[str, object]:
    """Helper to keep tool definitions concise and consistent."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        },
    }


def _bounded_cache_store(cache: dict, key, payload: Mapping[str, object], *, limit: int = 16) -> None:
    cache[key] = copy.deepcopy(payload)
    while len(cache) > limit:
        oldest_key = next(iter(cache))
        cache.pop(oldest_key, None)


def _search_cache_key(
    query: str,
    limit: int | None,
    identifier_filter: Mapping[str, object] | None,
    locked_key: str | None,
    locked_value: str | None,
) -> tuple[str, int, str, str | None, str | None]:
    normalized_query = (query or "").strip().lower()
    safe_limit = int(limit or 0)
    filter_blob = json.dumps(identifier_filter or {}, sort_keys=True, default=str)
    return (normalized_query, safe_limit, filter_blob, locked_key, locked_value)


def _read_cache_key(
    document_id: str,
    page_index: int,
    mode: str,
    neighbor_window: int,
    token_budget: int | None,
) -> tuple[str, int, str, int, int | None]:
    normalized_id = str(document_id)
    normalized_mode = (mode or "excerpt").strip().lower()
    return (normalized_id, int(page_index), normalized_mode, int(neighbor_window), token_budget)


GATEWAY_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    _function_schema(
        name="mcp_search_tools",
        description=(
            "Search available external MCP tools and return a small list of candidates. "
            "Results include tool_id (stable identifier), connection_name, remote_tool, description, "
            "and required_args (names + types only)."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "Natural-language description of what you want to do (e.g. 'list GitHub repos').",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of tools to return (1-10).",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
            "connection_id": {
                "type": "string",
                "description": "Optional: restrict search to a specific MCP connection id.",
            },
        },
        required=("query",),
    ),
    _function_schema(
        name="mcp_call_tool",
        description=(
            "Call an external MCP tool by tool_id. "
            "Provide arguments as an object. Returns status + output and may include approval metadata."
        ),
        properties={
            "tool_id": {
                "type": "string",
                "description": "Tool identifier from mcp_search_tools results[].tool_id.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "arguments": {
                "type": "object",
                "description": "Arguments for the selected tool.",
                "additionalProperties": True,
            },
        },
        required=("tool_id", "arguments"),
    ),
)


# Native integration tool policy metadata consumed by the orchestrator.
NATIVE_INTEGRATION_TOOL_REGISTRY: dict[str, dict[str, object]] = {
    # Google Calendar
    "calendar_list_events": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "calendar_get_event": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "calendar_create_event": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "calendar_update_event": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # Google Drive
    "drive_search_files": {
        "integration_type": IntegrationType.GOOGLE_DRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "drive_get_file": {
        "integration_type": IntegrationType.GOOGLE_DRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "drive_list_files": {
        "integration_type": IntegrationType.GOOGLE_DRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # OneDrive
    "onedrive_search_files": {
        "integration_type": IntegrationType.ONEDRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "onedrive_get_file": {
        "integration_type": IntegrationType.ONEDRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "onedrive_list_files": {
        "integration_type": IntegrationType.ONEDRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # Slack
    "slack_list_channels": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "slack_read_channel": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "slack_send_message": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "slack_search_messages": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # HubSpot
    "hubspot_search_contacts": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "hubspot_get_contact": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "hubspot_create_contact": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "hubspot_search_deals": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
}

EMAIL_PROVIDER_INTEGRATION_TYPES: dict[str, str] = {
    EmailAccountProvider.GOOGLE: "google_email",
    EmailAccountProvider.MICROSOFT: "microsoft_email",
}

EMAIL_INTEGRATION_TOOL_REGISTRY: dict[str, dict[str, object]] = {
    "email_search": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_get_message": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_get_thread": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_create_draft": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_send_draft": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
}

_NATIVE_TOOL_PRESENTATION: dict[str, tuple[str, str]] = {
    "calendar_list_events": ("List calendar events", "Read upcoming events from the connected calendar."),
    "calendar_get_event": ("Get event details", "Read details for a specific calendar event."),
    "calendar_create_event": ("Create calendar event", "Create a new event in the connected calendar."),
    "calendar_update_event": ("Update calendar event", "Modify an existing calendar event."),
    "drive_search_files": ("Search Drive files", "Search files in the connected Google Drive account."),
    "drive_get_file": ("Get Drive file", "Read content/metadata for a specific Google Drive file."),
    "drive_list_files": ("List Drive files", "List files from the connected Google Drive account."),
    "onedrive_search_files": ("Search OneDrive files", "Search files in the connected OneDrive account."),
    "onedrive_get_file": ("Get OneDrive file", "Read content/metadata for a specific OneDrive file."),
    "onedrive_list_files": ("List OneDrive files", "List files from the connected OneDrive account."),
    "slack_list_channels": ("List Slack channels", "Read available channels in the connected Slack workspace."),
    "slack_read_channel": ("Read Slack channel", "Read messages from a selected Slack channel."),
    "slack_send_message": ("Send Slack message", "Send a message to a Slack channel."),
    "slack_search_messages": ("Search Slack messages", "Search workspace messages in Slack."),
    "hubspot_search_contacts": ("Search HubSpot contacts", "Search contacts in the connected HubSpot workspace."),
    "hubspot_get_contact": ("Get HubSpot contact", "Read a specific HubSpot contact."),
    "hubspot_create_contact": ("Create HubSpot contact", "Create a new contact in HubSpot."),
    "hubspot_search_deals": ("Search HubSpot deals", "Search deals in HubSpot."),
    "email_search": ("Search email", "Search messages in the connected mailbox."),
    "email_get_message": ("Get email message", "Read a specific email message."),
    "email_get_thread": ("Get email thread", "Read a full conversation thread."),
    "email_create_draft": ("Create email draft", "Create a draft email in the connected mailbox."),
    "email_send_draft": ("Send email draft", "Send an existing draft email from the connected mailbox."),
}


def _native_tool_label(tool_name: str) -> str:
    value = str(tool_name or "").strip()
    if not value:
        return "Tool"
    preset = _NATIVE_TOOL_PRESENTATION.get(value)
    if preset:
        return preset[0]
    return value.replace("_", " ").strip().title()


def _native_tool_description(tool_name: str, *, operation_type: str) -> str:
    value = str(tool_name or "").strip()
    preset = _NATIVE_TOOL_PRESENTATION.get(value)
    if preset:
        return preset[1]
    op = str(operation_type or "").strip().lower()
    if op == McpToolOperationType.WRITE:
        return "Write operation for this connected integration."
    if op == McpToolOperationType.READ:
        return "Read operation for this connected integration."
    return "Native integration operation."


def _coerce_preference_bool(value: object, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _tool_preferences_from_metadata(metadata_obj: object) -> dict[str, bool]:
    metadata = metadata_obj if isinstance(metadata_obj, Mapping) else {}
    raw = metadata.get("tool_settings")
    if raw is None:
        raw = metadata.get("toolSettings")
    if not isinstance(raw, Mapping):
        return {}
    preferences: dict[str, bool] = {}
    for tool_name, payload in raw.items():
        normalized_name = str(tool_name or "").strip()
        if not normalized_name:
            continue
        if isinstance(payload, Mapping):
            enabled = _coerce_preference_bool(payload.get("enabled"), default=True)
        else:
            enabled = _coerce_preference_bool(payload, default=True)
        preferences[normalized_name] = enabled
    return preferences


def _native_tool_preferences_from_account(account: IntegrationAccount | None) -> dict[str, bool]:
    if account is None:
        return {}
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    return _tool_preferences_from_metadata(metadata)


def get_native_tool_enabled_map_for_account(
    account: IntegrationAccount,
    *,
    tool_names: Iterable[str] | None = None,
) -> dict[str, bool]:
    preferences = _native_tool_preferences_from_account(account)
    names = [str(name).strip() for name in (tool_names or preferences.keys()) if str(name or "").strip()]
    return {name: bool(preferences.get(name, True)) for name in names}


def is_native_tool_enabled_for_account(*, account: IntegrationAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    preferences = _native_tool_preferences_from_account(account)
    return bool(preferences.get(normalized_name, True))


def _email_tool_preferences_from_account(account: EmailAccount | None) -> dict[str, bool]:
    if account is None:
        return {}
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    return _tool_preferences_from_metadata(metadata)


def get_email_tool_enabled_map_for_account(
    account: EmailAccount,
    *,
    tool_names: Iterable[str] | None = None,
) -> dict[str, bool]:
    preferences = _email_tool_preferences_from_account(account)
    names = [str(name).strip() for name in (tool_names or preferences.keys()) if str(name or "").strip()]
    return {name: bool(preferences.get(name, True)) for name in names}


def is_email_tool_enabled_for_account(*, account: EmailAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    preferences = _email_tool_preferences_from_account(account)
    return bool(preferences.get(normalized_name, True))


def get_native_integration_tools_for_type(integration_type: str) -> list[dict[str, object]]:
    normalized_type = str(integration_type or "").strip()
    tools: list[dict[str, object]] = []
    for tool_name, meta in NATIVE_INTEGRATION_TOOL_REGISTRY.items():
        if not isinstance(meta, Mapping):
            continue
        if str(meta.get("integration_type") or "").strip() != normalized_type:
            continue
        operation_type = str(meta.get("operation_type") or McpToolOperationType.UNKNOWN).strip() or McpToolOperationType.UNKNOWN
        tools.append(
            {
                "toolName": str(tool_name),
                "label": _native_tool_label(str(tool_name)),
                "description": _native_tool_description(str(tool_name), operation_type=operation_type),
                "integrationType": normalized_type,
                "operationType": operation_type,
                "requiresConnectedAccount": bool(meta.get("requires_connected_account", True)),
                "requiresActorUserBinding": bool(meta.get("requires_actor_user_binding", True)),
            }
        )
    tools.sort(key=lambda item: str(item.get("label") or item.get("toolName") or ""))
    return tools


def get_native_integration_tool_metadata(tool_name: str) -> dict[str, object] | None:
    meta = NATIVE_INTEGRATION_TOOL_REGISTRY.get(str(tool_name or "").strip())
    if not isinstance(meta, Mapping):
        return None
    return dict(meta)


def get_native_integration_tool_registry() -> dict[str, dict[str, object]]:
    return {name: dict(meta) for name, meta in NATIVE_INTEGRATION_TOOL_REGISTRY.items()}


def get_native_integration_tool_names() -> set[str]:
    return set(NATIVE_INTEGRATION_TOOL_REGISTRY.keys())


def get_email_integration_tool_names() -> set[str]:
    return set(EMAIL_INTEGRATION_TOOL_REGISTRY.keys())


def get_email_integration_type_for_provider(provider: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    return EMAIL_PROVIDER_INTEGRATION_TYPES.get(normalized_provider, "")


def get_email_integration_tools_for_provider(provider: str) -> list[dict[str, object]]:
    normalized_provider = str(provider or "").strip().lower()
    integration_type = get_email_integration_type_for_provider(normalized_provider)
    if not integration_type:
        return []
    tools: list[dict[str, object]] = []
    for tool_name, meta in EMAIL_INTEGRATION_TOOL_REGISTRY.items():
        if not isinstance(meta, Mapping):
            continue
        providers_raw = meta.get("providers")
        providers = {
            str(value or "").strip().lower()
            for value in (providers_raw if isinstance(providers_raw, (list, tuple, set)) else [])
            if str(value or "").strip()
        }
        if providers and normalized_provider not in providers:
            continue
        operation_type = str(meta.get("operation_type") or McpToolOperationType.UNKNOWN).strip() or McpToolOperationType.UNKNOWN
        tools.append(
            {
                "toolName": str(tool_name),
                "label": _native_tool_label(str(tool_name)),
                "description": _native_tool_description(str(tool_name), operation_type=operation_type),
                "integrationType": integration_type,
                "provider": normalized_provider,
                "operationType": operation_type,
                "requiresConnectedAccount": bool(meta.get("requires_connected_account", True)),
                "requiresActorUserBinding": bool(meta.get("requires_actor_user_binding", True)),
            }
        )
    tools.sort(key=lambda item: str(item.get("label") or item.get("toolName") or ""))
    return tools


def list_connected_native_integration_types(*, conversation: Conversation) -> set[str]:
    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    if not actor_user_uuid:
        return set()
    business_id = getattr(conversation, "business_profile_id", None)
    supported_types = {
        str(meta.get("integration_type") or "").strip()
        for meta in NATIVE_INTEGRATION_TOOL_REGISTRY.values()
        if str(meta.get("integration_type") or "").strip()
    }
    if not supported_types:
        return set()
    types = IntegrationAccount.objects.filter(
        business_profile_id=business_id,
        user_id=actor_user_uuid,
        status=IntegrationAccountStatus.CONNECTED,
        integration_type__in=list(supported_types),
    ).values_list("integration_type", flat=True)
    return {str(value) for value in types if str(value).strip()}


def list_enabled_native_integration_tool_names(
    *,
    conversation: Conversation,
    registry: Mapping[str, Mapping[str, object]] | None = None,
) -> set[str]:
    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    if not actor_user_uuid:
        return set()
    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return set()

    source_registry = registry if isinstance(registry, Mapping) else NATIVE_INTEGRATION_TOOL_REGISTRY
    integration_types = {
        str(meta.get("integration_type") or "").strip()
        for meta in source_registry.values()
        if isinstance(meta, Mapping) and str(meta.get("integration_type") or "").strip()
    }
    if not integration_types:
        return set()

    connected_accounts = IntegrationAccount.objects.filter(
        business_profile_id=business_id,
        user_id=actor_user_uuid,
        status=IntegrationAccountStatus.CONNECTED,
        integration_type__in=list(integration_types),
    ).order_by("-updated_at")
    account_by_type: dict[str, IntegrationAccount] = {}
    for account in connected_accounts:
        integration_type = str(getattr(account, "integration_type", "") or "").strip()
        if not integration_type or integration_type in account_by_type:
            continue
        account_by_type[integration_type] = account

    enabled_tools: set[str] = set()
    for tool_name, meta in source_registry.items():
        if not isinstance(meta, Mapping):
            continue
        normalized_tool = str(tool_name or "").strip()
        if not normalized_tool:
            continue
        integration_type = str(meta.get("integration_type") or "").strip()
        requires_connected_account = bool(meta.get("requires_connected_account", True))
        account = account_by_type.get(integration_type)
        if requires_connected_account and account is None:
            continue
        if account is not None and not is_native_tool_enabled_for_account(account=account, tool_name=normalized_tool):
            continue
        enabled_tools.add(normalized_tool)

    return enabled_tools


def list_enabled_email_tool_names(
    *,
    conversation: Conversation,
    registry: Mapping[str, Mapping[str, object]] | None = None,
) -> set[str]:
    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    if not actor_user_uuid:
        return set()
    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return set()

    source_registry = registry if isinstance(registry, Mapping) else EMAIL_INTEGRATION_TOOL_REGISTRY
    supported_providers: set[str] = set()
    for meta in source_registry.values():
        if not isinstance(meta, Mapping):
            continue
        providers_raw = meta.get("providers")
        if not isinstance(providers_raw, (list, tuple, set)):
            continue
        for provider in providers_raw:
            normalized_provider = str(provider or "").strip().lower()
            if normalized_provider:
                supported_providers.add(normalized_provider)
    if not supported_providers:
        return set()

    connected_accounts = EmailAccount.objects.filter(
        business_profile_id=business_id,
        user_id=actor_user_uuid,
        status=EmailAccountStatus.CONNECTED,
        provider__in=list(supported_providers),
    ).order_by("-updated_at")
    accounts_by_provider: dict[str, list[EmailAccount]] = defaultdict(list)
    for account in connected_accounts:
        provider = str(getattr(account, "provider", "") or "").strip().lower()
        if not provider:
            continue
        accounts_by_provider[provider].append(account)

    enabled_tools: set[str] = set()
    for tool_name, meta in source_registry.items():
        if not isinstance(meta, Mapping):
            continue
        normalized_tool = str(tool_name or "").strip()
        if not normalized_tool:
            continue
        providers_raw = meta.get("providers")
        providers = [
            str(provider or "").strip().lower()
            for provider in (providers_raw if isinstance(providers_raw, (list, tuple, set)) else [])
            if str(provider or "").strip()
        ]
        requires_connected_account = bool(meta.get("requires_connected_account", True))
        candidate_accounts: list[EmailAccount] = []
        for provider in providers:
            candidate_accounts.extend(accounts_by_provider.get(provider, []))
        if requires_connected_account and not candidate_accounts:
            continue
        if candidate_accounts and not any(
            is_email_tool_enabled_for_account(account=account, tool_name=normalized_tool)
            for account in candidate_accounts
        ):
            continue
        enabled_tools.add(normalized_tool)

    return enabled_tools


def is_native_integration_tool_enabled_for_conversation(
    *,
    tool_name: str,
    conversation: Conversation,
) -> bool:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return False
    meta = NATIVE_INTEGRATION_TOOL_REGISTRY.get(normalized_tool)
    if not isinstance(meta, Mapping):
        return False
    enabled = list_enabled_native_integration_tool_names(
        conversation=conversation,
        registry={normalized_tool: dict(meta)},
    )
    return normalized_tool in enabled


def resolve_native_integration_account_for_tool(
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    conversation: Conversation,
) -> tuple[IntegrationAccount | None, Mapping[str, object] | None]:
    meta = get_native_integration_tool_metadata(tool_name)
    if not isinstance(meta, Mapping):
        return None, _integration_error(
            str(tool_name or "unknown_tool"),
            error_code="unsupported_tool",
            hint="Unsupported native integration tool.",
        )
    integration_type = str(meta.get("integration_type") or "").strip()
    if not integration_type:
        return None, _integration_error(
            str(tool_name or "unknown_tool"),
            error_code="unsupported_tool",
            hint="Missing integration type metadata.",
        )
    requires_actor_user_binding = bool(meta.get("requires_actor_user_binding", True))
    return _resolve_integration_account_for_tool(
        integration_type=integration_type,
        tool=str(tool_name or "unknown_tool"),
        arguments=arguments,
        conversation=conversation,
        requires_actor_user_binding=requires_actor_user_binding,
    )


TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
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
    _function_schema(
        name="create_agent_request",
        description=(
            "Send a structured request from Agent A to Agent B (agent-to-agent inbox). "
            "Use this when you need another agent/department to answer something. "
            "Provide references (ids/links) instead of raw dumps."
        ),
        properties={
            "to_agent_slug": {
                "type": "string",
                "description": "Recipient agent slug (optional). Defaults to the current agent.",
            },
            "subject": {
                "type": "string",
                "description": "Short subject line for the request.",
            },
            "question": {
                "type": "string",
                "description": "The question/task for the recipient agent (avoid pasting large raw context).",
            },
            "context_refs": {
                "type": "array",
                "description": "Structured references for context (conversation_id, run_id, message_id, artifact_id, etc.).",
                "items": {"type": "object", "additionalProperties": True},
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "additionalProperties": True,
            },
        },
        required=("question",),
    ),
    _function_schema(
        name="create_agent_run",
        description=(
            "Create a background AgentRun (sub-agent) anchored to this conversation. "
            "Use this when the visitor asks for a long-running or multi-step task so the chat can continue "
            "while the work happens in the Tasks panel."
        ),
        properties={
            "goal": {
                "type": "string",
                "description": "Clear task goal for the background run.",
            },
            "title": {
                "type": "string",
                "description": "Optional short title shown in the Tasks panel.",
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
                "description": "Optional planner output to display in the Tasks panel.",
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
            "List background runs (sub-agents) for this conversation. "
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
            "Continue an existing background run (sub-agent) with a follow-up message. "
            "Use this to send additional instructions to a completed or waiting run instead of creating a new one. "
            "The sub-agent will resume with its full conversation history."
        ),
        properties={
            "run_id": {
                "type": "string",
                "description": "UUID of the agent run to continue.",
            },
            "message": {
                "type": "string",
                "description": "Follow-up instruction or message for the sub-agent.",
            },
        },
        required=("run_id", "message"),
    ),
    _function_schema(
        name="search_knowledge",
        description="Search the knowledge base using a natural-language query.",
        properties={
            "cursor": {
                "type": "string",
                "description": "Opaque cursor from a prior search_knowledge response to fetch the next page.",
            },
            "query": {
                "type": "string",
                "description": "Single search query (back-compat). Prefer `queries` for multiple variants.",
            },
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": _search_queries_schema_description(),
            },
            "exclude_seen": {
                "type": "boolean",
                "description": "Exclude results already shown in this conversation (server default is configurable).",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of snippets to return (1..MCP_PROMPT_MAX_SNIPPETS).",
                "minimum": 1,
                "maximum": SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX,
                "default": SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT,
            },
        },
        required=(),
    ),
    _function_schema(
        name="search_conversation_files",
        description="Search files uploaded in this chat session (PDFs).",
        properties={
            "query": {
                "type": "string",
                "description": "What to look for in uploaded files.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of snippets to return (1-8).",
                "minimum": 1,
                "maximum": 8,
                "default": 5,
            },
        },
        required=("query",),
    ),
    _function_schema(
        name="read_conversation_file",
        description=(
            "Read extracted text from uploaded chat files. "
            "In agentic mode prefer ids[] from search_conversation_files."
        ),
        properties={
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of chunk IDs from search_conversation_files results (agentic mode).",
                "minItems": 1,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum total characters to return across all ids.",
                "minimum": 500,
                "maximum": 20000,
                "default": 8000,
            },
        },
        required=("ids",),
    ),
    _function_schema(
        name="pdf_generate",
        description=(
            "Generate a PDF from provided text/markdown and attach it to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "content": {"type": "string", "description": "Main content to render into the PDF."},
            "title": {"type": "string", "description": "Optional title displayed at the top."},
            "filename": {
                "type": "string",
                "description": "Optional output filename (e.g., 'summary.pdf').",
            },
            "format": {
                "type": "string",
                "enum": ["text", "markdown"],
                "default": "markdown",
                "description": "Input content format. Markdown is rendered as plain text (no HTML).",
            },
            "page_size": {
                "type": "string",
                "enum": ["letter", "a4"],
                "default": "letter",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {"type": "string"},
                },
            },
        },
        required=("content",),
    ),
    _function_schema(
        name="pdf_merge",
        description=(
            "Merge multiple PDFs (uploaded or generated in this chat) and attach the merged PDF to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "file_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": "List of PDF file IDs to merge (ConversationFile IDs).",
            },
            "filename": {"type": "string", "description": "Optional output filename (e.g., 'merged.pdf')."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_ids",),
    ),
    _function_schema(
        name="pdf_extract_pages",
        description=(
            "Extract specific pages from a PDF and attach the new PDF to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "file_id": {"type": "string", "description": "PDF file ID (ConversationFile ID)."},
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 1,
                "description": "1-based page numbers to extract.",
            },
            "filename": {"type": "string", "description": "Optional output filename (e.g., 'pages.pdf')."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_id", "pages"),
    ),
    _function_schema(
        name="pdf_extract_text",
        description="Extract text from a PDF (optionally specific pages).",
        properties={
            "file_id": {"type": "string", "description": "PDF file ID (ConversationFile ID)."},
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Optional list of 1-based page numbers to extract.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 50000,
                "default": 12000,
                "description": "Maximum characters to return.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_id",),
    ),
    _function_schema(
        name="read_knowledge",
        description=(
            "Read canonical evidence from the knowledge base (agentic contract). "
            "Provide refs from search_knowledge; include cursors only when continuing a partial read."
        ),
        properties={
            "refs": {
                "type": "array",
                "minItems": 1,
                "description": "List of refs to read. Each item is {id} or {id,cursor}.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Ref id from search_knowledge refs[].id.",
                        },
                        "cursor": {
                            "type": "string",
                            "description": "Opaque continuation cursor from a previous read_knowledge response.",
                        },
                        "row_start": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "For table refs only: 0-based row offset within the returned table body "
                                "(excluding header/separator rows)."
                            ),
                        },
                        "row_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": "For table refs only: maximum number of rows to return for this read.",
                        },
                    },
                    "required": ["id"],
                },
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum total characters to return across all refs (bounded by server caps).",
                "minimum": 500,
                "maximum": READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
                "default": READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
            },
        },
        required=("refs", "max_chars"),
    ),
    _function_schema(
        name="create_case",
        description="Create a structured customer case with diagnosis and suggested actions.",
        properties={
            "title": {"type": "string", "description": "Short case title provided to internal teams."},
            "description": {"type": "string", "description": "Detailed summary of the issue."},
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Relative urgency.",
            },
            "ai_diagnosis": {"type": "string", "description": "What you believe is happening."},
            "ai_actions_taken": {"type": "string", "description": "What you already did for the visitor."},
            "ai_suggested_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Follow-up steps you recommend.",
            },
        },
        required=("title", "description", "priority", "ai_diagnosis", "ai_actions_taken"),
    ),
    _function_schema(
        name="update_case_status",
        description="Update the status of the currently linked case.",
        properties={
            "case_id": {"type": "string", "description": "UUID of the case to update."},
            "status": {
                "type": "string",
                "enum": ["open", "closed"],
                "description": "New lifecycle status (open/closed).",
            },
            "note": {"type": "string", "description": "Optional explanation surfaced to humans."},
        },
        required=("case_id", "status"),
    ),
    _function_schema(
        name="update_case_details",
        description="Revise the title/description/priority of an existing case when new facts arrive.",
        properties={
            "case_id": {"type": "string", "description": "UUID of the case to update."},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "allow_description_overwrite": {
                "type": "boolean",
                "description": "Set true only when the prior description is now incorrect.",
                "default": False,
            },
        },
        required=("case_id",),
    ),
    _function_schema(
        name="add_case_history",
        description="Log a case history entry documenting progress or clarifications.",
        properties={
            "case_id": {"type": "string"},
            "summary": {"type": "string", "description": "What changed or what was confirmed."},
        },
        required=("case_id", "summary"),
    ),
    _function_schema(
        name="flag_escalation",
        description="Escalate a conversation for human follow-up.",
        properties={
            "reason": {"type": "string", "description": "Why the escalation is needed."},
            "details": {"type": "string", "description": "Context to hand off to the human team."},
        },
        required=("reason",),
    ),
    _function_schema(
        name="create_customer",
        description="Create or match a customer record when identifiers are provided.",
        properties={
            "full_name": {"type": "string"},
            "email": {"type": "string"},
            "phone": {"type": "string"},
            "metadata": {
                "type": "object",
                "description": "Optional extra context (company, notes, etc.).",
            },
        },
        required=("full_name",),
    ),
    _function_schema(
        name="update_customer",
        description="Update an existing customer profile when the visitor confirms a change.",
        properties={
            "customer_id": {"type": "string"},
            "full_name": {"type": "string"},
            "metadata": {"type": "object"},
        },
        required=("customer_id",),
    ),
    _function_schema(
        name="create_lead",
        description="Capture a sales lead discovered in chat.",
        properties={
            "title": {"type": "string"},
            "description": {"type": "string"},
            "source": {"type": "string"},
        },
        required=("title", "description"),
    ),
    _function_schema(
        name="create_appointment",
        description="Schedule or request an appointment for the visitor.",
        properties={
            "topic": {"type": "string"},
            "preferred_time": {"type": "string", "description": "ISO timestamp or natural language slot."},
            "notes": {"type": "string"},
        },
        required=("topic",),
    ),
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
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — Google Drive
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="drive_search_files",
        description="Search files in the connected Google Drive.",
        properties={
            "query": {"type": "string", "description": "Search query (file name or content)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="drive_get_file",
        description="Get file content from Google Drive (text-based files). Returns metadata for binary files.",
        properties={
            "file_id": {"type": "string", "description": "Google Drive file ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("file_id",),
    ),
    _function_schema(
        name="drive_list_files",
        description="List files in a Google Drive folder (or root if no folder specified).",
        properties={
            "folder_id": {"type": "string", "description": "Google Drive folder ID (optional, omit for root)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — OneDrive
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="onedrive_search_files",
        description="Search files in the connected OneDrive.",
        properties={
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="onedrive_get_file",
        description="Get file content from OneDrive (text-based files). Returns metadata for binary files.",
        properties={
            "file_id": {"type": "string", "description": "OneDrive item ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("file_id",),
    ),
    _function_schema(
        name="onedrive_list_files",
        description="List files in a OneDrive folder (or root if no folder specified).",
        properties={
            "folder_id": {"type": "string", "description": "OneDrive folder ID (optional, omit for root)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
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


def get_tool_definitions() -> tuple[Mapping[str, object], ...]:
    """
    Return tool schemas with runtime-tuned descriptions.

    Some guidance text depends on current settings (for example
    MCP_SEARCH_MAX_QUERY_VARIANTS), so we patch those fields per request.
    """

    definitions: list[Mapping[str, object]] = copy.deepcopy(list(TOOL_DEFINITIONS))
    queries_description = _search_queries_schema_description()

    for tool_def in definitions:
        function_block = tool_def.get("function")
        if not isinstance(function_block, Mapping):
            continue
        if str(function_block.get("name") or "").strip() != "search_knowledge":
            continue
        parameters = function_block.get("parameters")
        if not isinstance(parameters, Mapping):
            break
        properties = parameters.get("properties")
        if not isinstance(properties, Mapping):
            break
        queries_schema = properties.get("queries")
        if isinstance(queries_schema, dict):
            queries_schema["description"] = queries_description
        break

    return tuple(definitions)


def execute_tool(
    name: str,
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext | None = None,
) -> Mapping[str, object]:
    """
    Execute the requested tool call and return the serialized result.

    The `context` parameter carries per-turn business constraints (chunk-read
    budgets, ingestion warnings, etc.) so the orchestrator can enforce them no
    matter which tool the LLM selects.
    """

    normalized_name = (name or "").strip()
    handler = _TOOL_HANDLERS.get(normalized_name)
    if not handler:
        return {
            "tool": normalized_name or "unknown_tool",
            "status": "error",
            "error": "unsupported_tool",
            "error_code": "unsupported_tool",
            "hint": "Unsupported tool. Use search_knowledge or read_knowledge.",
        }
    ctx = context or ToolExecutionContext()
    # Track tool-level read calls (one per tool invocation, regardless of how many
    # internal chunks/pages the handler touches).
    if normalized_name == "read_knowledge":
        try:
            ctx.reserve_read()
        except Exception:
            # Budget tracking should never break tool execution.
            pass
    business_id = getattr(conversation, "business_profile_id", None)
    try:
        with tenant_context(business_id):
            result = handler(arguments, conversation=conversation, context=ctx)
    except ToolConstraintError as exc:
        status = "constraint_error"
        error_code = "constraint_error"
        if isinstance(exc, ToolRateLimitExceeded):
            status = "throttled"
            error_code = "rate_limited"
        elif isinstance(exc, CharacterBudgetExceeded):
            status = "throttled"
            error_code = "prompt_budget_exceeded"
        elif isinstance(exc, ChunkReadBudgetExceeded):
            status = "throttled"
            error_code = "chunk_read_budget_exceeded"
        elif isinstance(exc, ChunkPageBudgetExceeded):
            status = "throttled"
            error_code = "chunk_page_budget_exceeded"
        elif isinstance(exc, SearchBudgetExceeded):
            status = "throttled"
            error_code = "search_budget_exceeded"
        result = {
            "tool": normalized_name,
            "status": status,
            "error": error_code,
            "error_code": error_code,
            "hint": str(exc) or "Tool constraint exceeded. Narrow the request and try again.",
        }
    except Exception:
        logger.exception(
            "mcp.tool_failed tool=%s business=%s conversation=%s",
            normalized_name,
            getattr(conversation, "business_profile_id", None),
            getattr(conversation, "id", None),
        )
        result = {
            "tool": normalized_name,
            "status": "error",
            "error": "tool_failed",
            "error_code": "tool_failed",
            "hint": "Tool execution failed unexpectedly. Try a narrower request.",
        }

    # Layer 2: include per-turn budget snapshot in every knowledge tool response.
    if (
        bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
        and normalized_name in {"search_knowledge", "read_knowledge"}
        and isinstance(result, Mapping)
    ):
        enriched = dict(result)
        enriched["budget"] = ctx.budget_snapshot()
        return enriched

    return result


# ---------------------------------------------------------------------------
# Shared helpers and tool handlers


ToolHandler = Callable[[Mapping[str, object], Conversation, ToolExecutionContext], Mapping[str, object]]


@lru_cache(maxsize=1)
def _knowledge_service() -> KnowledgeSearchService:
    """
    Lazily construct the shared KnowledgeSearchService.

    The underlying service is stateless with respect to conversations, so it is
    safe to reuse across tool calls within a process.
    """

    return KnowledgeSearchService()


@lru_cache(maxsize=1)
def _portal_file_embedding_service():
    """
    Lazily construct the shared embedding service for conversation-file retrieval.

    Uses the same provider selection as the knowledge base (OpenAI → local → None).
    """

    try:
        from apps.rag.embeddings import build_embedding_service
    except Exception:  # pragma: no cover - defensive
        return None
    return build_embedding_service()


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_priority(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    aliases = {
        "low": "low",
        "medium": "medium",
        "med": "medium",
        "high": "high",
        "urgent": "high",
        "critical": "critical",
    }
    normalized = aliases.get(value, value)
    if normalized in {"low", "medium", "high", "critical"}:
        return normalized
    return None


@dataclass(frozen=True, slots=True)
class AgentKnowledgeScope:
    mode: str  # all|documents
    explicit_upload_ids: frozenset[str] = frozenset()

    @property
    def restricted(self) -> bool:
        return self.mode != "all"


_AGENT_SCOPE_SENTINEL: object = object()


def _agent_knowledge_scope(conversation: Conversation, context: ToolExecutionContext) -> AgentKnowledgeScope:
    cached = getattr(context, "_agent_knowledge_scope", _AGENT_SCOPE_SENTINEL)
    if isinstance(cached, AgentKnowledgeScope):
        return cached

    agent_id = getattr(conversation, "agent_profile_id", None)
    if not agent_id:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    agent = AgentProfile.objects.filter(
        id=agent_id,
        business_profile=conversation.business_profile,
    ).first()
    if agent is None:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    has_doc_rules = agent.allowed_documents.filter(business_profile=conversation.business_profile).exists()
    if not has_doc_rules:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    explicit_upload_ids: set[str] = set()
    if has_doc_rules:
        explicit_upload_ids.update(
            str(value)
            for value in apply_customer_visible_uploads(
                agent.allowed_documents.filter(
                    business_profile=conversation.business_profile,
                    status=KnowledgeStatus.ACTIVE,
                )
            ).values_list("id", flat=True)
        )

    scope = AgentKnowledgeScope(
        mode="documents",
        explicit_upload_ids=frozenset(explicit_upload_ids),
    )
    context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
    return scope


def _agent_scope_allows_upload(
    *,
    scope: AgentKnowledgeScope,
    conversation: Conversation,
    upload_id: uuid.UUID,
) -> bool:
    if not scope.restricted:
        return True
    return str(upload_id) in scope.explicit_upload_ids


def _apply_agent_scope_to_upload_queryset(queryset, scope: AgentKnowledgeScope):
    if not scope.restricted:
        return queryset
    if not scope.explicit_upload_ids:
        return queryset.none()
    return queryset.filter(id__in=list(scope.explicit_upload_ids)).distinct()


def _scope_upload_ids_to_uuids(scope: Iterable[str] | None) -> list[uuid.UUID] | None:
    if scope is None:
        return None
    ids: list[uuid.UUID] = []
    for value in scope:
        try:
            ids.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return ids


def _query_intent(query: str) -> dict[str, object]:
    """
    Lightweight heuristic to classify the query and suggest search/read defaults.
    """
    text = (query or "").strip()
    lowered = text.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", lowered) if t]
    has_digits = any(ch.isdigit() for ch in text)
    identifier_like = has_digits or any(sym in text for sym in ("-", "_"))
    table_hit = any(
        kw in lowered
        for kw in (
            "table",
            "sheet",
            "csv",
            "grid",
            "column",
            "row",
            "spreadsheet",
            "report",
            "statement",
        )
    )
    length = len(tokens)
    if identifier_like and length <= 6:
        intent = "identifier"
    elif table_hit:
        intent = "table"
    elif length >= 14:
        intent = "long"
    else:
        intent = "short"
    return {
        "intent": intent,
        "tokens": length,
        "has_digits": has_digits,
        "table": table_hit,
    }


def _snippet_text_for_sufficiency(payload: Mapping[str, object]) -> str:
    for key in ("content", "summary", "snippet", "preview", "text", "raw_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _snippet_has_truncation(payload: Mapping[str, object]) -> bool:
    if payload.get("truncated") or payload.get("partial_index") or payload.get("truncation_note"):
        return True
    diagnostics = payload.get("source_diagnostics")
    if not isinstance(diagnostics, Mapping):
        return False
    return any(
        diagnostics.get(key)
        for key in (
            "table_truncated",
            "truncated_rows",
            "truncated_columns",
            "truncated_tables",
            "truncated_entities",
            "table_partial_tables",
            "partial_index",
        )
    )


def _snippet_has_table_truncation(payload: Mapping[str, object]) -> bool:
    diagnostics = payload.get("source_diagnostics")
    if not isinstance(diagnostics, Mapping):
        return False
    return any(
        diagnostics.get(key)
        for key in (
            "table_truncated",
            "truncated_rows",
            "truncated_columns",
            "truncated_tables",
            "table_partial_tables",
        )
    )


def _compute_read_required(
    payload: Mapping[str, object],
) -> tuple[bool, list[str]]:
    read_state = str(payload.get("read_state") or "summary").strip().lower()
    if read_state not in {"summary", "preview"}:
        return False, []

    reasons: list[str] = []
    snippet_text = _snippet_text_for_sufficiency(payload)
    if not snippet_text:
        reasons.append("preview_empty")

    if _snippet_has_truncation(payload):
        reasons.append("content_truncated")

    if _snippet_has_table_truncation(payload):
        reasons.append("table_truncated")

    return bool(reasons), reasons


def _match_knowledge_entry(
    context: ToolExecutionContext | None,
    identifiers: Sequence[str],
) -> Mapping[str, object] | None:
    """
    Locate snippet metadata associated with the requested chunk/upload.
    """

    if not context or not identifiers:
        return None
    normalized = {str(value).strip().lower() for value in identifiers if value}
    if not normalized:
        return None
    for entry in getattr(context, "knowledge_results", []):
        if not isinstance(entry, Mapping):
            continue
        candidate_ids = {
            str(entry.get("chunk_id") or "").strip().lower(),
            str(entry.get("upload_id") or "").strip().lower(),
            str(entry.get("id") or "").strip().lower(),
        }
        if normalized & {cid for cid in candidate_ids if cid}:
            return entry
    return None


def _detect_full_page_intent(
    conversation: Conversation,
    requested_mode: str | None,
    *,
    document_entry: Mapping[str, object] | None = None,
    upload: KnowledgeUpload | None = None,
) -> bool:
    """
    Decide whether to allow a full-page read. Prefers excerpts unless:
    - The visitor explicitly asks for a page/section.
    - The referenced table is small (few rows/columns) and not truncated.
    """

    def _coerce_int(value: object) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    if requested_mode:
        return requested_mode.strip().lower() == "full_page"

    latest = (
        conversation.messages.order_by("-sent_at", "-created_at")
        .first()
    )
    body = latest.body if latest else ""
    text = (body or "").lower()
    explicit_keywords = (
        "full page",
        "entire page",
        "whole page",
        "page ",
        "صفحة",
        "sheet",
        "section",
        "document",
        "pdf",
    )
    if any(keyword in text for keyword in explicit_keywords):
        return True

    diag = document_entry.get("source_diagnostics") if isinstance(document_entry, Mapping) else {}
    truncated = bool(diag.get("table_truncated"))
    total_rows = _coerce_int(diag.get("table_total_rows") or diag.get("table_indexed_rows"))
    if not total_rows and upload:
        metadata = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
        table_stats = metadata.get("table_stats") if isinstance(metadata, Mapping) else None
        if isinstance(table_stats, Mapping):
            total_rows = _coerce_int(table_stats.get("total_rows"))
            truncated = truncated or bool(table_stats.get("partial_index"))

    if total_rows and total_rows <= 20 and not truncated:
        return True

    return False


def _budget_allows_full_page(
    context: ToolExecutionContext,
    *,
    business_profile,
    service: KnowledgeSearchService,
) -> bool:
    if context.char_budget_per_turn is None:
        return True
    inline_cap = service.inline_char_limit_for_business(business_profile)
    remaining = max(0, context.char_budget_per_turn - context.characters_used)
    threshold = max(800, int(inline_cap * 0.6))
    return remaining >= threshold


def _apply_seen_item_filter(
    snippets: list[dict[str, object]],
    context: ToolExecutionContext,
    mark_as_seen: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Compute seen-item metadata without filtering results.

    We track which items were previously shown so the LLM can decide
    whether to repeat or summarize. We no longer suppress repeats.
    """
    if not snippets:
        return snippets, {"shown": 0, "total_found": 0, "already_seen": 0, "has_more": False}

    already_seen_count = 0
    for snippet in snippets:
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        if not chunk_id:
            continue
        if context.is_chunk_seen(chunk_id):
            already_seen_count += 1
        if mark_as_seen:
            context.mark_chunk_shown(chunk_id)

    completeness = {
        "shown": len(snippets),
        "total_found": len(snippets),
        "already_seen": already_seen_count,
        "has_more": False,
    }

    return snippets, completeness


def _mark_snippets_as_seen(snippets: list[dict[str, object]], context: ToolExecutionContext) -> None:
    """Mark snippets as seen after they've been finalized for the response."""
    for snippet in snippets:
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        if chunk_id:
            context.mark_chunk_shown(chunk_id)


def _apply_seen_row_filter(
    rows: list[dict[str, object]],
    document_id: str,
    context: ToolExecutionContext,
    mark_as_seen: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Compute seen-row metadata without filtering results."""
    if not rows:
        return rows, {"shown": 0, "total_found": 0, "already_seen": 0, "has_more": False}

    already_seen_count = 0
    for row in rows:
        row_index = row.get("row_index")
        if row_index is None:
            continue
        if context.is_row_seen(document_id, row_index):
            already_seen_count += 1
        if mark_as_seen:
            context.mark_row_shown(document_id, row_index)

    completeness = {
        "shown": len(rows),
        "total_found": len(rows),
        "already_seen": already_seen_count,
        "has_more": False,
    }

    return rows, completeness


def _mark_rows_as_seen(rows: list[dict[str, object]], document_id: str, context: ToolExecutionContext) -> None:
    """Mark rows as seen after they've been finalized for the response."""
    for row in rows:
        row_index = row.get("row_index")
        if row_index is not None:
            context.mark_row_shown(document_id, row_index)


def _serialize_snippets(snippets: Sequence[object]) -> list[dict[str, object]]:
    """
    Convert KnowledgeSnippet instances into prompt/diagnostic-friendly dicts.

    Reuses the legacy orchestrator's serializer so ingestion warnings and
    downstream diagnostics behave consistently across MCP and non-MCP paths.
    """

    payloads: list[dict[str, object]] = []
    for snippet in snippets:
        try:
            payloads.append(AiOrchestratorService._serialize_snippet(snippet))  # type: ignore[arg-type]
        except Exception:
            continue
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, object]] = []
    for entry in payloads:
        evidence_group_id = str(entry.get("evidence_group_id") or "").strip()
        chunk_id = str(entry.get("chunk_id") or "").strip()
        entry_id = str(entry.get("id") or "").strip()
        upload_id = str(entry.get("upload_id") or "").strip() or None
        identity = evidence_group_id or chunk_id or entry_id
        if not identity:
            deduped.append(entry)
            continue
        key = (identity, upload_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _sanitize_snippet_payloads_for_prompt(
    snippet_payloads: Sequence[Mapping[str, object]],
    *,
    conversation: Conversation,
) -> list[dict[str, object]]:
    if not snippet_payloads:
        return []
    sanitized: list[dict[str, object]] = []
    for payload in snippet_payloads:
        if not isinstance(payload, Mapping):
            continue
        out = dict(payload)
        # Never pass extracted identifier values to the LLM; they can leak user data.
        out.pop("identifiers", None)
        sanitized.append(out)
    return sanitized


def _estimate_tokens(characters: int) -> int:
    """
    Rough heuristic to approximate tokens for logging purposes.
    """

    if characters <= 0:
        return 0
    return max(1, math.ceil(characters / 4))


TOTAL_COLUMN_KEYWORDS = (
    "total",
    "sum",
    "overall",
    "اجمالي",
    "إجمالي",
    "الاجمالي",
    "المجموع",
)
PRIMARY_TOTAL_TERMS = (
    "total",
    "overall",
    "sum",
    "اجمالي",
    "إجمالي",
    "الاجمالي",
    "المجموع",
)


def _normalize_column_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


def _normalize_identifier_value(value: object) -> str:
    """
    Normalize identifier-like values (invoice/order/ticket IDs) for strict matching.

    We remove whitespace and lowercase to avoid common ingestion artefacts like
    padding while preserving punctuation/hyphens.
    """

    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.strip("`\"'")
    return re.sub(r"\s+", "", text).lower()


_IDENTIFIER_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _extract_identifier_candidate(text: object) -> str | None:
    raw = _coerce_str(text).strip()
    if not raw:
        return None
    cleaned = raw.strip("`\"'")
    if not cleaned:
        return None
    if _IDENTIFIER_EMAIL_RE.match(cleaned):
        return cleaned
    if cleaned.isdigit() and len(cleaned) >= 6:
        return cleaned
    digit_runs = re.findall(r"\d{6,}", cleaned)
    if digit_runs:
        return max(digit_runs, key=len)
    token_runs = re.findall(r"[A-Za-z0-9][A-Za-z0-9_/-]{7,}", cleaned)
    if token_runs:
        return max(token_runs, key=len)
    return None


def _pick_best_identifier_column(
    columns: Sequence[str],
    *,
    query_text: str,
    identifier_value: str,
) -> str | None:
    candidates: list[str] = []
    for col in columns:
        if not isinstance(col, str):
            continue
        col_clean = col.strip()
        if not col_clean:
            continue
        if _column_suggests_identifier(col_clean):
            candidates.append(col_clean)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    query_norm = _normalize_column_name(query_text)
    ident_norm = _normalize_identifier_value(identifier_value)
    is_email = "@" in ident_norm
    is_digits = ident_norm.isdigit()

    best: str | None = None
    best_score = -1
    for col in candidates:
        col_norm = _normalize_column_name(col)
        if not col_norm:
            continue
        score = 0

        if is_email:
            if any(term in col_norm for term in ("email", "e-mail", "mail")):
                score += 200
        if is_digits:
            if "invoice" in col_norm:
                score += 80
            if "serial" in col_norm:
                score += 50
            if "order" in col_norm:
                score += 60
            if "ticket" in col_norm:
                score += 60
        if "invoice" in query_norm and "invoice" in col_norm:
            score += 60
        if "order" in query_norm and "order" in col_norm:
            score += 60
        if "ticket" in query_norm and "ticket" in col_norm:
            score += 60
        if "serial" in query_norm and "serial" in col_norm:
            score += 25
        if any(term in query_norm for term in ("id", "ref", "#")) and any(term in col_norm for term in ("id", "ref", "#")):
            score += 15
        if any(term in col_norm for term in ("id", "ref", "reference", "number", "no", "#")):
            score += 10

        if score > best_score:
            best_score = score
            best = col

    if best is None:
        return None
    if best_score >= 20:
        return best
    return None


def _column_suggests_identifier(column: object) -> bool:
    normalized = _normalize_column_name(column)
    if not normalized:
        return False
    if any(term in normalized for term in ("email", "e-mail", "mail")):
        return True
    if any(term in normalized for term in ("phone", "mobile", "msisdn")):
        return True
    if any(term in normalized for term in ("invoice", "order", "ticket")):
        if any(term in normalized for term in ("id", "serial", "number", "no", "ref", "reference", "#")):
            return True
        return True
    if "serial" in normalized or "reference" in normalized or re.search(r"(?:^|[\s_-])ref(?:$|[\s_-])", normalized):
        return True
    if re.search(r"(?:^|[\s_-])id(?:$|[\s_-])", normalized):
        return True
    if " code" in normalized or normalized.endswith("code") or "sku" in normalized:
        return True
    if "number" in normalized or normalized.endswith(" no") or normalized.endswith(" #"):
        return True
    return False


def _should_force_exact_identifier_match(column: object, value: object) -> bool:
    """
    Decide whether we should force op=eq (and reject contains/prefix) for this filter.
    """

    normalized = _normalize_identifier_value(value)
    if not normalized:
        return False
    if "@" in normalized:
        return True
    if normalized.isdigit() and len(normalized) >= 6:
        return True
    if len(normalized) >= 8 and any(ch.isdigit() for ch in normalized):
        return True
    if _column_suggests_identifier(column) and len(normalized) >= 4:
        return True
    return False


def _total_column_priority(value: object) -> int:
    normalized = _normalize_column_name(value)
    if not normalized:
        return 0
    if normalized in PRIMARY_TOTAL_TERMS:
        return 3
    for term in PRIMARY_TOTAL_TERMS:
        if normalized.startswith(f"{term} ") or normalized.endswith(f" {term}"):
            return 2
    if any(keyword in normalized for keyword in TOTAL_COLUMN_KEYWORDS):
        return 1
    return 0


def _is_total_column_label(value: object) -> bool:
    return _total_column_priority(value) > 0


def _snippet_payload_metrics(snippets: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """
    Compute lightweight telemetry for snippet payloads so we can benchmark prompt costs.
    """

    total_chars = 0
    chunk_count = 0
    upload_count = 0
    table_rich = 0
    issue_rich = 0
    truncated_tables = 0
    read_state_counter: Counter[str] = Counter()

    for entry in snippets:
        content = entry.get("content")
        if not isinstance(content, str):
            content = entry.get("summary") if isinstance(entry.get("summary"), str) else ""
        total_chars += len(content or "")
        if entry.get("chunk_id"):
            chunk_count += 1
        else:
            upload_count += 1
        structured_table_count = 0
        issue_count = 0
        try:
            structured_table_count = int(entry.get("structured_table_count") or 0)
            issue_count = int(entry.get("issue_count") or 0)
        except (TypeError, ValueError):
            structured_table_count = 0
            issue_count = 0
        if structured_table_count:
            table_rich += 1
        if issue_count:
            issue_rich += 1
        diag = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
        if diag and diag.get("table_truncated"):
            truncated_tables += 1
        read_state = str(entry.get("read_state") or "summary")
        read_state_counter[read_state] += 1

    metrics = {
        "snippet_count": len(snippets),
        "chunk_snippet_count": chunk_count,
        "upload_snippet_count": upload_count,
        "char_count": total_chars,
        "token_estimate": _estimate_tokens(total_chars),
        "table_snippet_count": table_rich,
        "issue_snippet_count": issue_rich,
        "table_truncated_count": truncated_tables,
        "read_state_breakdown": dict(read_state_counter),
    }
    return metrics


def _log_tool_metrics(
    *,
    tool: str,
    conversation: Conversation,
    snippets: Sequence[Mapping[str, object]],
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """
    Emit structured telemetry for MCP tools without changing runtime behavior.
    """
    metrics = _snippet_payload_metrics(snippets)
    if extra:
        merged_extra = dict(extra)
    else:
        merged_extra = {}
    merged_extra.update(metrics)
    structured_log(
        "mcp",
        f"tool.{tool}",
        merged_extra,
        context={"business": conversation.business_profile_id, "conversation": conversation.id},
    )
    tags = {
        "tool": tool,
        "business": str(conversation.business_profile_id),
    }
    latency_monitor.observe("mcp.tool.char_count", metrics.get("char_count"), tags=tags)
    latency_monitor.observe("mcp.tool.snippet_count", metrics.get("snippet_count"), tags=tags)
    return metrics


def _snippet_preview_text(payload: Mapping[str, object]) -> str:
    candidates = [
        payload.get("raw_text"),
        payload.get("text"),
        payload.get("preview"),
        payload.get("content"),
        payload.get("summary"),
        payload.get("snippet"),
    ]
    for value in candidates:
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed[:200]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        first_row = rows[0]
        if isinstance(first_row, Mapping):
            cells = first_row.get("cells")
            if isinstance(cells, list):
                values: list[str] = []
                for cell in cells[:6]:
                    if isinstance(cell, Mapping):
                        text = cell.get("text") or cell.get("value")
                        if isinstance(text, str) and text.strip():
                            values.append(text.strip())
                if values:
                    return " | ".join(values)[:200]
    return ""


def _log_snippet_payloads(
    *,
    tool: str,
    conversation: Conversation,
    snippet_payloads: Sequence[Mapping[str, object]],
    meta: Mapping[str, object] | None = None,
) -> None:
    preview_items: list[dict[str, object]] = []
    include_previews = _mcp_log_snippet_previews_enabled()
    include_pii = _mcp_log_pii_enabled()
    include_full_content = _mcp_log_full_snippet_content_enabled()
    for payload in snippet_payloads[:5]:
        upload_id = payload.get("upload_id")
        chunk_id = payload.get("chunk_id") or payload.get("id")
        preview_text = _snippet_preview_text(payload) if include_previews else ""
        preview: str | None = None
        preview_hash: str | None = None
        preview_len: int | None = None
        if preview_text:
            preview_len = len(preview_text)
            if include_pii:
                preview = preview_text
            else:
                preview_hash = sha256_hex(preview_text)
        
        item_dict = {
            "label": payload.get("public_label") or payload.get("title") or payload.get("label"),
            "upload_id": str(upload_id) if upload_id else None,
            "chunk_id": str(chunk_id) if chunk_id else None,
            "read_state": payload.get("read_state"),
            "read_required": bool(payload.get("read_required")),
            "read_required_reasons": payload.get("read_required_reasons"),
            "is_table_chunk": bool(payload.get("is_table_chunk")),
            "score": payload.get("score"),
            "preview": preview,
            "preview_sha256": preview_hash,
            "preview_len": preview_len,
        }
        
        # Add full content when enabled
        if include_full_content:
            content = payload.get("content")
            if content is not None and content != "":
                content_text = content if isinstance(content, str) else str(content)
                item_dict["full_content_len"] = len(content_text)
                if include_pii:
                    item_dict["full_content"] = content_text
                else:
                    item_dict["full_content_sha256"] = sha256_hex(content_text)
            
            summary = payload.get("summary")
            if summary is not None and summary != "":
                summary_text = summary if isinstance(summary, str) else str(summary)
                item_dict["summary_len"] = len(summary_text)
                if include_pii:
                    item_dict["summary"] = summary_text
                else:
                    item_dict["summary_sha256"] = sha256_hex(summary_text)
            
            rows = payload.get("rows")
            if isinstance(rows, list) and rows:
                item_dict["rows_count"] = len(rows)
                if include_pii:
                    item_dict["rows"] = rows
        
        preview_items.append(item_dict)
    detail = dict(meta or {})
    if "query" in detail:
        query_value = _coerce_str(detail.pop("query")).strip()
        detail.update(_log_safe_text_fields("query", query_value or None))
    detail["snippet_count"] = len(snippet_payloads)
    if preview_items:
        detail["snippets"] = [{k: v for k, v in entry.items() if v not in (None, "")} for entry in preview_items]
    structured_log(
        "mcp",
        f"{tool}.snippets",
        detail,
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )


def _maybe_throttle_full_page(
    context: ToolExecutionContext,
    business_profile,
    service: KnowledgeSearchService,
) -> dict[str, object] | None:
    """
    Determine if a full-page request should be downgraded to an excerpt.
    """

    limit = context.char_budget_per_turn
    if limit is not None and limit > 0:
        remaining = max(0, limit - context.characters_used)
        inline_cap = service.inline_char_limit_for_business(business_profile)
        threshold = max(1200, int(inline_cap * 0.75))
        if remaining < threshold:
            return {
                "reason": "char_budget_low",
                "remaining_characters": remaining,
                "threshold": threshold,
            }
    page_limit = context.max_chunk_pages_per_turn
    if page_limit is not None and page_limit > 0:
        throttle_floor = max(1, int(page_limit * 0.5))
        if context.chunk_pages_used > throttle_floor:
            return {
                "reason": "page_window_throttle",
                "used_pages": context.chunk_pages_used,
                "page_limit": page_limit,
            }
    return None


def _extract_no_result_reason(diagnostics: Mapping[str, object] | None) -> str:
    valid_reasons = {"not_found", "not_applicable_to_segment", "insufficient_evidence"}
    diag = diagnostics or {}
    reason = str(diag.get("no_result_reason") or "").strip().lower()
    if reason in valid_reasons:
        return reason
    contract = diag.get("auto_decision_contract")
    if isinstance(contract, Mapping):
        contract_reason = str(contract.get("no_result_reason") or "").strip().lower()
        if contract_reason in valid_reasons:
            return contract_reason
    return ""


def _extract_conflict_detected(diagnostics: Mapping[str, object] | None) -> bool:
    diag = diagnostics or {}
    if bool(diag.get("conflict_detected")):
        return True
    contract = diag.get("auto_decision_contract")
    if isinstance(contract, Mapping):
        return bool(contract.get("conflict_detected"))
    return False


def _is_uuid_ref_id(value: object) -> bool:
    try:
        uuid.UUID(str(value or "").strip())
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _scope_invalid_read_retry_limit() -> int:
    try:
        configured = int(getattr(settings, "MCP_INVALID_READ_REF_RETRY_LIMIT", 2) or 2)
    except (TypeError, ValueError):
        configured = 2
    return max(1, min(5, configured))


def _build_ingestion_warnings(
    snippet_payloads: Sequence[Mapping[str, object]],
    knowledge_reads: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """
    Derive ingestion warnings from snippet diagnostics, mirroring the legacy path.
    """

    if not knowledge_reads:
        return []
    relevant_ids: set[str] = set()
    for read in knowledge_reads:
        identifier = read.get("id")
        if identifier:
            relevant_ids.add(str(identifier))
    if not relevant_ids:
        return []

    warnings: list[dict[str, object]] = []
    for entry in snippet_payloads:
        entry_id = str(entry.get("id") or "")
        if entry_id not in relevant_ids:
            continue
        label = entry.get("public_label") or entry.get("title") or "Knowledge source"
        upload_id = str(entry.get("upload_id") or entry_id)
        # Issue-derived warnings
        warnings.extend(
            AiOrchestratorService._issue_warning_payloads(  # type: ignore[attr-defined]
                entry.get("issues"),
                label=label,  # type: ignore[arg-type]
                upload_id=upload_id,
            )
        )
        diagnostics = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
        partial_index = bool(entry.get("partial_index"))
        truncation_note_val = entry.get("truncation_note")
        truncation_note = truncation_note_val if isinstance(truncation_note_val, str) else None
        diag_warning = AiOrchestratorService._diagnostic_warning_payload(  # type: ignore[attr-defined]
            label=label,  # type: ignore[arg-type]
            upload_id=upload_id,
            diagnostics=diagnostics,
            partial_index=partial_index,
            truncation_note=truncation_note,
        )
        if diag_warning:
            warnings.append(diag_warning)  # type: ignore[arg-type]
    return warnings


def _convert_to_agentic_search_response(
    legacy_payload: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext | None = None,
) -> dict[str, object]:
    """
    Convert legacy search_knowledge response to agentic format.
    
    Phase 1 (EvidenceRefs): return pointers only (optionally with short previews).

    The agentic format returns a compact list of "refs" the model can read via
    read_knowledge(). This intentionally avoids duplicating facts in multiple
    representations (table row + text restatement) and keeps the prompt small.
    """
    snippets = legacy_payload.get("snippets", [])
    refs: list[dict[str, object]] = []
    agentic_read_v2_enabled = (
        bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
        and bool(getattr(settings, "MCP_AGENTIC_READ_V2_ENABLED", False))
    )
    preview_full_enabled = bool(getattr(settings, "MCP_AGENTIC_SEARCH_PREVIEWS_ENABLED", False))
    preview_hybrid_enabled = bool(getattr(settings, "MCP_AGENTIC_SEARCH_PREVIEWS_HYBRID_ENABLED", False))
    try:
        preview_chars_cap = int(getattr(settings, "MCP_PROMPT_SNIPPET_CONTENT_CHARS", 1200) or 1200)
    except (TypeError, ValueError):
        preview_chars_cap = 1200
    preview_chars_cap = max(0, preview_chars_cap)
    hybrid_preview_max_items = 10
    hybrid_preview_chars_cap = min(preview_chars_cap, 400) if preview_chars_cap else 0
    previews_attached = 0

    def _preview_text(snippet: Mapping[str, object], *, max_chars: int) -> tuple[str, bool]:
        if max_chars <= 0:
            return "", False
        raw = snippet.get("summary") or snippet.get("content") or ""
        if not isinstance(raw, str):
            raw = str(raw or "")
        text = raw.strip()
        if not text:
            return "", False
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars].rstrip() + "…", True

    def _is_table_direct(snippet: Mapping[str, object]) -> bool:
        stage = str(snippet.get("search_stage") or "").strip().lower()
        source = str(snippet.get("source") or "").strip().lower()
        return stage in {"table_direct", "table_blended"} or source == "table_direct"

    def _looks_like_chunk_label(value: object) -> bool:
        text = str(value or "").strip().lower()
        return bool(text and "chunk " in text)

    def _anchor_key(snippet: Mapping[str, object]) -> str:
        """Canonical dedupe key for EvidenceRefs (avoid duplicates across search stages)."""
        diagnostics = snippet.get("source_diagnostics") if isinstance(snippet.get("source_diagnostics"), Mapping) else {}
        table_id = diagnostics.get("table_id")
        # Preserve row_index=0 (0 is valid but falsy).
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        if table_id and row_index is not None:
            return f"table:{table_id}:{row_index}"
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        upload_id = str(snippet.get("upload_id") or "").strip()
        if upload_id:
            return f"upload:{upload_id}"
        return f"fallback:{sha256_hex(json.dumps(dict(snippet), sort_keys=True, default=str)[:800])}"

    def _evidence_group_key(snippet: Mapping[str, object]) -> str:
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        if evidence_group_id:
            return f"evidence:{evidence_group_id}"
        return _anchor_key(snippet)

    def _representation(snippet: Mapping[str, object]) -> str:
        raw = str(snippet.get("representation") or "").strip().lower()
        if raw in {"text", "table", "json"}:
            return raw
        if bool(snippet.get("is_table_chunk")):
            return "table"
        if str(snippet.get("entity_type") or "").strip():
            return "json"
        return "text"

    def _evidence_tokens(text: str) -> set[str]:
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", (text or "").lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return set()
        return {
            token
            for token in normalized.split(" ")
            if token and (len(token) >= 3 or token.isdigit())
        }

    def _evidence_conflict(snippets_for_group: Sequence[Mapping[str, object]]) -> bool:
        if len(snippets_for_group) < 2:
            return False
        by_representation: dict[str, Mapping[str, object]] = {}
        for snippet in snippets_for_group:
            by_representation.setdefault(_representation(snippet), snippet)
        if len(by_representation) < 2:
            return False
        reps = list(by_representation.keys())
        for i, left_rep in enumerate(reps):
            for right_rep in reps[i + 1 :]:
                left = by_representation[left_rep]
                right = by_representation[right_rep]
                left_text = str(left.get("content") or left.get("summary") or "")
                right_text = str(right.get("content") or right.get("summary") or "")
                left_tokens = _evidence_tokens(left_text)
                right_tokens = _evidence_tokens(right_text)
                if min(len(left_tokens), len(right_tokens)) < 4:
                    continue
                union = left_tokens | right_tokens
                if not union:
                    continue
                overlap = len(left_tokens & right_tokens) / len(union)
                if overlap < 0.25:
                    return True
        return False

    def _coerce_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _snippet_row_index(snippet: Mapping[str, object]) -> int | None:
        diagnostics_local = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        row_index_local = diagnostics_local.get("row_index")
        if row_index_local is None:
            row_index_local = diagnostics_local.get("table_row_index")
        return _coerce_int(row_index_local)

    def _snippet_char_estimate(snippet: Mapping[str, object]) -> int:
        diagnostics_local = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        content = snippet.get("content") or ""
        summary = snippet.get("summary") or ""
        char_estimate_local = len(content) if content else len(summary) * 3
        limit_hint = 0
        for key in ("inline_char_limit", "page_char_limit"):
            try:
                limit_hint = max(limit_hint, int(diagnostics_local.get(key) or 0))
            except (TypeError, ValueError):
                continue
        if limit_hint:
            char_estimate_local = max(
                char_estimate_local,
                min(limit_hint, int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)),
            )
        if bool(snippet.get("is_table_chunk")) and _snippet_row_index(snippet) is None:
            row_count = (
                diagnostics_local.get("table_total_rows")
                or diagnostics_local.get("table_row_count")
                or snippet.get("row_count")
            )
            column_count = diagnostics_local.get("table_column_count") or snippet.get("column_count")
            if row_count and column_count:
                try:
                    table_size_estimate = int(row_count) * int(column_count) * 25 + int(row_count) * 32
                    char_estimate_local = max(char_estimate_local, table_size_estimate)
                except (TypeError, ValueError):
                    pass
        return max(0, int(char_estimate_local))

    def _suggest_max_chars_for_estimate(char_estimate: object, *, max_chars_allowed: int) -> int:
        """
        Convert a rough char estimate into a safe `max_chars` suggestion for read_knowledge.

        We intentionally add headroom so the model doesn't under-allocate and get truncated.
        """
        try:
            estimate = int(char_estimate or 0)
        except (TypeError, ValueError):
            estimate = 0
        max_chars_allowed_int = max(1, int(max_chars_allowed))

        if estimate <= 0:
            return min(int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT), max_chars_allowed_int)

        # +20% headroom + a small constant for JSON/table framing overhead.
        suggested = int(estimate * 1.2) + 200
        suggested = max(500, suggested)
        return min(max_chars_allowed_int, suggested)

    def _suggest_table_read_chars(
        *,
        base_suggested: int,
        row_count: object,
        column_count: object,
        char_estimate_local: int,
        row_index: object,
        max_chars_allowed: int,
    ) -> int:
        rows = _coerce_int(row_count) or 0
        cols = _coerce_int(column_count) or 0
        # Prefer explicit column counts when available; otherwise use a conservative default.
        cols = cols if cols > 0 else 5
        row_index_int = _coerce_int(row_index)
        if row_index_int is not None and row_index_int >= 0:
            estimated_row_payload_chars = max(
                int(char_estimate_local),
                int(char_estimate_local + cols * 22 + 180),
            )
            tuned = _suggest_max_chars_for_estimate(
                estimated_row_payload_chars,
                max_chars_allowed=max_chars_allowed,
            )
            return max(500, min(int(max_chars_allowed), int(tuned)))
        if rows <= 0:
            return int(base_suggested)
        estimated_table_chars = max(
            int(char_estimate_local),
            int(rows * max(80, cols * 22) + 400),
        )
        tuned = _suggest_max_chars_for_estimate(
            estimated_table_chars,
            max_chars_allowed=max_chars_allowed,
        )
        if rows >= 25:
            tuned = max(tuned, 1800)
        if rows >= 40:
            tuned = max(tuned, 2800)
        if rows >= 80:
            tuned = max(tuned, 4500)
        return max(500, min(int(max_chars_allowed), int(tuned)))

    # Evidence planner (Phase 1): convert search snippets into lightweight refs.
    # Dedupe by canonical anchors so the model doesn't see the same fact twice, but
    # do not drop entire categories of evidence based on search stage (agentic mode
    # can page/iterate if it needs more).
    raw_snippets: list[Mapping[str, object]] = [
        snippet for snippet in snippets if isinstance(snippet, Mapping)
    ]
    table_direct_present = any(_is_table_direct(snippet) for snippet in raw_snippets)
    candidate_snippets = raw_snippets

    seen_evidence_keys: set[str] = set()
    planned_snippets: list[Mapping[str, object]] = []
    evidence_variants: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    conflict_keys: set[str] = set()
    for snippet in candidate_snippets:
        key = _evidence_group_key(snippet)
        evidence_variants[key].append(snippet)
        if key in seen_evidence_keys:
            continue
        seen_evidence_keys.add(key)
        planned_snippets.append(snippet)
    for key, group_snippets in evidence_variants.items():
        if _evidence_conflict(group_snippets):
            conflict_keys.add(key)

    def _canonical_table_id(raw_table_id: object) -> str:
        value = str(raw_table_id or "").strip()
        if not value:
            return ""
        try:
            return str(uuid.UUID(value))
        except (TypeError, ValueError):
            return ""

    table_row_ref_counts: Counter[str] = Counter()
    table_row_hit_scores: dict[str, dict[int, float]] = defaultdict(dict)
    table_estimated_rows: dict[str, int] = {}
    table_estimated_columns: dict[str, int] = {}
    for snippet in planned_snippets:
        if not bool(snippet.get("is_table_chunk")):
            continue
        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        table_id = diagnostics.get("table_id")
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        if not table_id or row_index is None:
            continue
        canonical_table_id = _canonical_table_id(table_id)
        if not canonical_table_id:
            continue
        row_index_int = _coerce_int(row_index)
        if row_index_int is None or row_index_int < 0:
            continue
        table_row_ref_counts[canonical_table_id] += 1

        score_val = 0.0
        try:
            raw_score = snippet.get("confidence_score")
            if raw_score is not None:
                score_val = float(raw_score)
        except (TypeError, ValueError):
            score_val = 0.0
        prior = table_row_hit_scores[canonical_table_id].get(int(row_index_int))
        if prior is None or score_val > float(prior):
            table_row_hit_scores[canonical_table_id][int(row_index_int)] = float(score_val)

        est_rows = _coerce_int(
            diagnostics.get("table_total_rows")
            or diagnostics.get("table_row_count")
            or snippet.get("row_count")
        )
        if est_rows is not None and est_rows > 0:
            table_estimated_rows[canonical_table_id] = max(
                int(table_estimated_rows.get(canonical_table_id, 0)),
                int(est_rows),
            )
        est_cols = _coerce_int(diagnostics.get("table_column_count") or snippet.get("column_count"))
        if est_cols is not None and est_cols > 0:
            table_estimated_columns[canonical_table_id] = max(
                int(table_estimated_columns.get(canonical_table_id, 0)),
                int(est_cols),
            )
    tables_with_multiple_row_refs = {
        table_id
        for table_id, count in table_row_ref_counts.items()
        if int(count) >= 2
    }

    # Table-row promotion is intentionally disabled on the active path.
    # We keep distinct row refs visible to the model instead of collapsing
    # semantically different row hits into one table-level winner.
    promoted_table_candidates: set[str] = set()
    promote_table_context = False
    promoted_table_ids: set[str] = set()
    precomputed_table_anchor_manifests: dict[str, dict[str, object]] = {}

    def _build_multi_anchor_manifest(table_id: str) -> dict[str, object] | None:
        row_scores = table_row_hit_scores.get(table_id)
        if not isinstance(row_scores, Mapping) or not row_scores:
            return None
        row_items: list[tuple[int, float]] = []
        for raw_index, raw_score in row_scores.items():
            try:
                idx = int(raw_index)
            except (TypeError, ValueError):
                continue
            if idx < 0:
                continue
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            row_items.append((idx, score))
        if not row_items:
            return None
        row_items.sort(key=lambda item: item[0])

        clusters: list[list[tuple[int, float]]] = []
        current: list[tuple[int, float]] = []
        prev_idx: int | None = None
        for idx, score in row_items:
            if prev_idx is not None and current and (idx - prev_idx) >= 4:
                clusters.append(current)
                current = []
            current.append((idx, score))
            prev_idx = idx
        if current:
            clusters.append(current)

        anchor_candidates: list[tuple[float, int]] = []
        for cluster in clusters:
            # Highest score wins; tie-breaker is the lowest row index.
            best_idx, best_score = max(cluster, key=lambda item: (float(item[1]), -int(item[0])))
            anchor_candidates.append((float(best_score), int(best_idx)))
        anchor_candidates.sort(key=lambda item: (-float(item[0]), int(item[1])))

        anchor_rows = [int(idx) for _score, idx in anchor_candidates[:2]]
        if not anchor_rows:
            return None
        matched_row_index = int(anchor_rows[0])

        manifest: dict[str, object] = {
            "ref_id": str(table_id),
            "table_id": str(table_id),
            "matched_row_index": matched_row_index,
            "anchors": [{"row_index": int(idx)} for idx in anchor_rows],
        }
        est_rows = _coerce_int(table_estimated_rows.get(table_id))
        if est_rows is not None and est_rows > 0:
            manifest["estimated_rows"] = int(est_rows)
        est_cols = _coerce_int(table_estimated_columns.get(table_id))
        if est_cols is not None and est_cols > 0:
            manifest["estimated_columns"] = int(est_cols)
        return manifest

    for table_id in promoted_table_candidates:
        manifest = _build_multi_anchor_manifest(str(table_id))
        if isinstance(manifest, Mapping):
            precomputed_table_anchor_manifests[str(table_id)] = dict(manifest)

    promoted_table_anchor_manifests: dict[str, dict[str, object]] = {}

    def _ref_sort_key(snippet: Mapping[str, object]) -> tuple[float, int]:
        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        row_index_local = diagnostics.get("row_index")
        if row_index_local is None:
            row_index_local = diagnostics.get("table_row_index")
        is_table_local = bool(snippet.get("is_table_chunk"))
        if is_table_local and row_index_local is not None:
            specificity_rank = 0
        elif not is_table_local:
            specificity_rank = 1
        else:
            specificity_rank = 2
        try:
            confidence = float(snippet.get("confidence_score") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return (-confidence, specificity_rank)

    for snippet in sorted(planned_snippets, key=_ref_sort_key):
        
        # Determine type
        is_table = bool(snippet.get("is_table_chunk"))
        content_type = "table" if is_table else "text"
        representation = _representation(snippet)
        evidence_type = str(snippet.get("evidence_type") or "").strip().lower()

        # Get IDs
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        upload_id = str(snippet.get("upload_id") or "")
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        evidence_key = _evidence_group_key(snippet)

        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        char_estimate = _snippet_char_estimate(snippet)

        row_count = diagnostics.get("table_total_rows") or diagnostics.get("table_row_count") or snippet.get("row_count")
        column_count = diagnostics.get("table_column_count") or snippet.get("column_count")
        table_id = diagnostics.get("table_id")
        # Preserve row_index=0 (0 is valid but falsy).
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        canonical_table_id = _canonical_table_id(table_id) if (content_type == "table" and table_id) else ""
        if (
            content_type == "table"
            and canonical_table_id
            and row_index is None
            and canonical_table_id in tables_with_multiple_row_refs
        ):
            continue
        # Strict one-ref-per-promoted-table: skip chunk-only table refs when the table
        # is already eligible for promotion via row hits (we'll emit the table ref instead).
        if (
            content_type == "table"
            and canonical_table_id
            and canonical_table_id in promoted_table_candidates
            and row_index is None
        ):
            continue

        suggested_max_chars = _suggest_max_chars_for_estimate(
            char_estimate,
            max_chars_allowed=int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX),
        )
        if content_type == "table":
            suggested_max_chars = _suggest_table_read_chars(
                base_suggested=suggested_max_chars,
                row_count=row_count,
                column_count=column_count,
                char_estimate_local=char_estimate,
                row_index=row_index,
                max_chars_allowed=int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX),
            )

        # EvidenceRef fields
        entity_name = snippet.get("entity_name")
        title = snippet.get("title") or snippet.get("public_label") or "Untitled"
        label_parts: list[str] = []
        if isinstance(entity_name, str) and entity_name.strip():
            label_parts.append(entity_name.strip())
        if isinstance(title, str) and title.strip():
            label_parts.append(title.strip())
        label = " — ".join(label_parts) if label_parts else str(title or "Untitled")
        if isinstance(label, str) and len(label) > 240:
            label = f"{label[:240].rstrip()}…"

        score_val: float | None = None
        try:
            raw_score = snippet.get("confidence_score")
            if raw_score is not None:
                score_val = float(raw_score)
        except (TypeError, ValueError):
            score_val = None

        kind = "text_anchor"
        promote_table_ref = bool(
            content_type == "table"
            and table_id
            and row_index is not None
            and promote_table_context
            and canonical_table_id in promoted_table_candidates
        )
        if content_type == "table":
            kind = "table_row" if (diagnostics.get("table_id") and row_index is not None) else "table_chunk"
            if promote_table_ref:
                kind = "table_chunk"

        coverage_hint: dict[str, object] = {}
        if evidence_group_id:
            coverage_hint["evidence_group_id"] = evidence_group_id
        if content_type == "table":
            if table_id:
                coverage_hint["table_id"] = str(table_id)
            if row_index is not None:
                coverage_hint["row_index"] = row_index
            if row_count is not None:
                coverage_hint["estimated_rows"] = row_count
            if column_count is not None:
                coverage_hint["estimated_columns"] = column_count
            page_number = snippet.get("page_number")
            if page_number is not None:
                try:
                    coverage_hint["page"] = int(page_number)
                except (TypeError, ValueError):
                    pass
        else:
            page_number = snippet.get("page_number")
            if page_number is not None:
                try:
                    coverage_hint["page"] = int(page_number)
                except (TypeError, ValueError):
                    pass
            else:
                chunk_index = snippet.get("chunk_index")
                if isinstance(chunk_index, int):
                    coverage_hint["offset"] = chunk_index

        read_hint_in = snippet.get("read_hint")
        read_hint_out: dict[str, object] = {"suggested_max_chars": suggested_max_chars}
        if not agentic_read_v2_enabled and isinstance(read_hint_in, Mapping) and read_hint_in:
            # Preserve legacy read hint metadata for non-v2 agentic reads.
            read_hint_out = dict(read_hint_in)
            read_hint_out.setdefault("suggested_max_chars", suggested_max_chars)

        ref_id = chunk_id
        if promote_table_ref and canonical_table_id:
            promoted_title = ""
            for candidate in (
                diagnostics.get("table_title"),
                diagnostics.get("section_heading"),
            ):
                candidate_text = str(candidate or "").strip()
                if candidate_text:
                    promoted_title = candidate_text
                    break
            if promoted_title:
                label_parts = []
                if isinstance(entity_name, str) and entity_name.strip():
                    normalized_entity_name = entity_name.strip()
                    if normalized_entity_name.lower() != promoted_title.lower():
                        label_parts.append(normalized_entity_name)
                label_parts.append(promoted_title)
                label = " — ".join(label_parts) if label_parts else promoted_title
            elif _looks_like_chunk_label(title):
                order_index = _coerce_int(diagnostics.get("table_order_index"))
                fallback_title = f"Table {order_index}" if order_index is not None and order_index >= 0 else "Table"
                label_parts = []
                if isinstance(entity_name, str) and entity_name.strip():
                    label_parts.append(entity_name.strip())
                label_parts.append(fallback_title)
                label = " — ".join(label_parts)
            if canonical_table_id not in promoted_table_anchor_manifests:
                manifest = precomputed_table_anchor_manifests.get(canonical_table_id)
                if isinstance(manifest, Mapping):
                    promoted_table_anchor_manifests[canonical_table_id] = dict(manifest)
                else:
                    matched_row_index = _coerce_int(row_index)
                    if matched_row_index is not None and matched_row_index >= 0:
                        promoted_table_anchor_manifests[canonical_table_id] = {
                            "ref_id": canonical_table_id,
                            "table_id": canonical_table_id,
                            "matched_row_index": int(matched_row_index),
                        }
            if canonical_table_id in promoted_table_ids:
                continue
            promoted_table_ids.add(canonical_table_id)
            ref_id = canonical_table_id
        if not ref_id:
            continue
        why: list[str] = []
        stage = str(snippet.get("search_stage") or "").strip().lower()
        if stage:
            why.append(f"search_stage:{stage}")
        if _is_table_direct(snippet):
            why.append("match:table_direct")
        if kind.startswith("table"):
            if promote_table_ref:
                why.append("kind:table_context")
            else:
                why.append("kind:table")
        else:
            why.append("kind:text")
        ref_item: dict[str, object] = {
            "id": ref_id,
            "document_id": upload_id,
            "kind": kind,
            "type": content_type,
            "label": label,
            "score": score_val if score_val is not None else 0.0,
            "char_estimate": char_estimate,
            "read_hint": read_hint_out,
        }
        include_preview = False
        preview_cap = preview_chars_cap
        if preview_full_enabled and preview_chars_cap:
            include_preview = True
        elif (not preview_full_enabled) and preview_hybrid_enabled and hybrid_preview_chars_cap:
            # Hybrid mode: only attach previews to the top-ranked refs.
            include_preview = len(refs) < hybrid_preview_max_items
            preview_cap = hybrid_preview_chars_cap
        if include_preview:
            preview, preview_truncated = _preview_text(snippet, max_chars=preview_cap)
            if preview:
                ref_item["preview"] = preview
                if preview_truncated:
                    ref_item["preview_truncated"] = True
                previews_attached += 1
        if why:
            ref_item["why"] = why[:3]
        if evidence_group_id:
            ref_item["evidence_group_id"] = evidence_group_id
        if evidence_type:
            ref_item["evidence_type"] = evidence_type
        if representation:
            ref_item["representation"] = representation
        conflict_flag = bool(diagnostics.get("evidence_conflict")) or (evidence_key in conflict_keys)
        if conflict_flag:
            ref_item["conflict"] = {
                "type": "representation_mismatch",
                "resolution": "read_full",
            }
            read_hint_out["suggested_mode"] = "full"
            why.append("evidence_conflict")
            ref_item["read_hint"] = read_hint_out
            ref_item["why"] = why[:3]
        if promote_table_ref and isinstance(coverage_hint, dict):
            matched_row_index: int | None = None
            anchor_row_indexes: list[int] = []
            if canonical_table_id:
                manifest = precomputed_table_anchor_manifests.get(canonical_table_id)
                if isinstance(manifest, Mapping):
                    matched_row_index = _coerce_int(manifest.get("matched_row_index"))
                    raw_anchors = manifest.get("anchors")
                    if isinstance(raw_anchors, list):
                        for entry in raw_anchors:
                            parsed = _coerce_int(entry.get("row_index")) if isinstance(entry, Mapping) else _coerce_int(entry)
                            if parsed is None or parsed < 0:
                                continue
                            anchor_row_indexes.append(int(parsed))
            if matched_row_index is None:
                matched_row_index = _coerce_int(coverage_hint.get("row_index"))
            if matched_row_index is not None:
                coverage_hint["matched_row_index"] = int(matched_row_index)
            if anchor_row_indexes:
                seen_anchor_rows: set[int] = set()
                coverage_hint["anchor_row_indexes"] = [
                    row_index_value
                    for row_index_value in anchor_row_indexes
                    if not (row_index_value in seen_anchor_rows or seen_anchor_rows.add(row_index_value))
                ][:5]
            coverage_hint.pop("row_index", None)
        if coverage_hint:
            ref_item["coverage_hint"] = coverage_hint
        # Keep source metadata for internal debugging / operator traces.
        source_val = snippet.get("source_file") or snippet.get("source")
        if isinstance(source_val, str) and source_val.strip():
            ref_item["source"] = source_val.strip()
        if diagnostics.get("table_truncated"):
            ref_item["partial_index"] = True
        refs.append(ref_item)

    if context is not None and promoted_table_anchor_manifests:
        table_manifest_cache = getattr(context, "table_row_anchor_manifests", None)
        if isinstance(table_manifest_cache, dict):
            for table_ref_id, manifest in promoted_table_anchor_manifests.items():
                table_manifest_cache[str(table_ref_id)] = dict(manifest)
            while len(table_manifest_cache) > 100:
                oldest_key = next(iter(table_manifest_cache))
                table_manifest_cache.pop(oldest_key, None)
    
    # Build agentic response
    status = legacy_payload.get("status", "ok")
    total_found = legacy_payload.get("completeness", {}).get("total_found", len(refs))

    agentic_response: dict[str, object] = {
        "tool": "search_knowledge",
        "status": status if refs else "empty",
        "refs": refs,
        "total_found": total_found,
    }

    # Provide a read_budget_hint so the LLM can plan max_chars for read_knowledge.
    if refs:
        total_suggested = 0
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            read_hint = ref.get("read_hint")
            if not isinstance(read_hint, Mapping):
                continue
            try:
                total_suggested += int(read_hint.get("suggested_max_chars") or 0)
            except (TypeError, ValueError):
                continue
        max_chars_allowed = int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)
        agentic_response["read_budget_hint"] = {
            "total_suggested_max_chars": min(total_suggested, max_chars_allowed),
            "max_chars_allowed": max_chars_allowed,
        }

    # NOTE: In agentic mode, we may dedupe/collapse a legacy "page" of N snippets
    # down to fewer refs (e.g., 10 legacy snippets -> 3 refs). The legacy
    # completeness metadata refers to snippet paging, not ref paging, which is
    # confusing in debugging. Keep both counts and make `shown` consistent with
    # the actual returned refs.
    completeness_in = legacy_payload.get("completeness")
    completeness_out: dict[str, object] = {}
    if isinstance(completeness_in, Mapping) and completeness_in:
        completeness_out = dict(completeness_in)

    legacy_shown = _coerce_int(completeness_out.get("shown"))
    if legacy_shown is None:
        legacy_shown = len(raw_snippets)
    legacy_already_seen = _coerce_int(completeness_out.get("already_seen"))
    if legacy_already_seen is None:
        legacy_already_seen = 0

    planned_already_seen = legacy_already_seen
    if context is not None:
        planned_already_seen = 0
        for snippet in planned_snippets:
            if not isinstance(snippet, Mapping):
                continue
            chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
            if chunk_id and context.is_chunk_seen(chunk_id):
                planned_already_seen += 1

    completeness_out.setdefault("total_found", total_found)
    # Make agentic completeness consistent with what we actually returned.
    completeness_out["shown"] = len(refs)
    completeness_out["already_seen"] = planned_already_seen
    # Preserve the legacy counts for debug visibility.
    completeness_out["legacy_shown"] = legacy_shown
    completeness_out["legacy_already_seen"] = legacy_already_seen
    completeness_out["planned_unique_snippets"] = len(planned_snippets)
    completeness_out["raw_snippet_count"] = len(raw_snippets)

    if "has_more" not in completeness_out and "has_more" in legacy_payload:
        completeness_out["has_more"] = bool(legacy_payload.get("has_more"))

    if completeness_out:
        agentic_response["completeness"] = completeness_out

    # Pagination hints (cursor-based "next page" support).
    next_cursor = legacy_payload.get("next_cursor")
    if isinstance(next_cursor, str) and next_cursor.strip():
        agentic_response["next_cursor"] = next_cursor.strip()
    if "has_more" in legacy_payload:
        agentic_response["has_more"] = bool(legacy_payload.get("has_more"))
    legacy_prefetched_read_status = str(legacy_payload.get("prefetched_read_status") or "").strip().lower()
    if legacy_prefetched_read_status:
        agentic_response["prefetched_read_status"] = legacy_prefetched_read_status
    legacy_prefetched_evidence = legacy_payload.get("prefetched_evidence")
    if isinstance(legacy_prefetched_evidence, Sequence) and not isinstance(
        legacy_prefetched_evidence,
        (str, bytes, bytearray),
    ):
        prefetched_out: list[dict[str, object]] = []
        for item in legacy_prefetched_evidence[:8]:
            if not isinstance(item, Mapping):
                continue
            entry: dict[str, object] = {}
            for key in (
                "id",
                "document_id",
                "title",
                "type",
                "kind",
                "chars",
                "truncated",
                "next_cursor",
                "text",
                "rows_shown",
                "total_rows",
            ):
                value = item.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                entry[key] = value
            if entry:
                prefetched_out.append(entry)
        if prefetched_out:
            agentic_response["prefetched_evidence"] = prefetched_out

    # Log the conversion for debugging
    structured_log(
        "mcp",
        "search.agentic_conversion",
        {
            "limit_requested": _coerce_int(legacy_payload.get("limit")) or 0,
            "legacy_snippet_count": len(snippets),
            "legacy_completeness_shown": _coerce_int(completeness_in.get("shown")) if isinstance(completeness_in, Mapping) else None,
            "agentic_ref_count": len(refs),
            "total_found": total_found,
            "agentic_read_v2_enabled": agentic_read_v2_enabled,
            "table_direct_present": table_direct_present,
            "table_context_promote_enabled": promote_table_context,
            "table_context_promote_candidates": len(promoted_table_candidates),
            "table_context_promoted_refs": len(promoted_table_ids),
            "planner_dropped": max(0, len(raw_snippets) - len(planned_snippets)),
            "planned_unique_snippets": len(planned_snippets),
            "evidence_conflict_groups": len(conflict_keys),
            "previews_full_enabled": preview_full_enabled,
            "previews_hybrid_enabled": preview_hybrid_enabled,
            "previews_attached_count": previews_attached,
            "table_anchor_manifests_cached": len(promoted_table_anchor_manifests),
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )
    
    return agentic_response


def _fuse_batched_search_runs(
    runs: Sequence[Mapping[str, object]],
    *,
    clip_limit: int | None,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    def _snippet_dedupe_key(snippet: Mapping[str, object]) -> str:
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        if evidence_group_id:
            return f"evidence:{evidence_group_id}"
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        upload_id = str(snippet.get("upload_id") or "").strip()
        if upload_id:
            return f"upload:{upload_id}"
        content = snippet.get("content")
        if isinstance(content, str) and content.strip():
            return f"content:{sha256_hex(content)}"
        summary = snippet.get("summary")
        if isinstance(summary, str) and summary.strip():
            return f"summary:{sha256_hex(summary)}"
        return json.dumps(snippet, sort_keys=True, default=str)

    if not runs:
        return [], None
    if len(runs) == 1:
        snippets = [
            dict(snippet)
            for snippet in runs[0].get("snippets", [])
            if isinstance(snippet, Mapping)
        ]
        if clip_limit:
            snippets = snippets[:clip_limit]
        return snippets, None

    fusion = {"method": "rrf_dedupe", "runs": len(runs)}
    fused: dict[str, dict[str, object]] = {}
    # Use rank fusion across query variants so later variants can still surface
    # the best shared evidence before we clip to the outward limit.
    rrf_k = 60.0
    for run_index, run in enumerate(runs):
        snippets = run.get("snippets", [])
        if not isinstance(snippets, Sequence) or isinstance(snippets, (str, bytes, bytearray)):
            continue
        for rank, snippet in enumerate(snippets):
            if not isinstance(snippet, Mapping):
                continue
            dedup_key = _snippet_dedupe_key(snippet)
            raw_confidence = snippet.get("confidence_score")
            try:
                confidence = float(raw_confidence) if raw_confidence is not None else 0.0
            except (TypeError, ValueError):
                confidence = 0.0
            entry = fused.get(dedup_key)
            rrf_increment = 1.0 / (rrf_k + float(rank) + 1.0)
            if entry is None:
                fused[dedup_key] = {
                    "snippet": dict(snippet),
                    "rrf_score": rrf_increment,
                    "best_confidence": confidence,
                    "best_rank": int(rank),
                    "best_run_index": int(run_index),
                }
                continue
            entry["rrf_score"] = float(entry.get("rrf_score") or 0.0) + rrf_increment
            if confidence > float(entry.get("best_confidence") or 0.0):
                entry["best_confidence"] = confidence
                entry["snippet"] = dict(snippet)
                entry["best_rank"] = int(rank)
                entry["best_run_index"] = int(run_index)
            elif confidence == float(entry.get("best_confidence") or 0.0):
                prior_run = int(entry.get("best_run_index") or 0)
                prior_rank = int(entry.get("best_rank") or 0)
                if (run_index, rank) < (prior_run, prior_rank):
                    entry["snippet"] = dict(snippet)
                    entry["best_rank"] = int(rank)
                    entry["best_run_index"] = int(run_index)

    ordered_entries = sorted(
        fused.values(),
        key=lambda entry: (
            -float(entry.get("rrf_score") or 0.0),
            -float(entry.get("best_confidence") or 0.0),
            int(entry.get("best_run_index") or 0),
            int(entry.get("best_rank") or 0),
        ),
    )
    if clip_limit is not None:
        ordered_entries = ordered_entries[:clip_limit]
    deduped_snippets = [dict(entry["snippet"]) for entry in ordered_entries]
    return deduped_snippets, fusion


def _search_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    new_contract_enabled = bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
    rag_agentic_enabled = bool(getattr(feature_state, "rag_agentic_mode", False)) and new_contract_enabled

    pagination_enabled = bool(getattr(settings, "MCP_SEARCH_PAGINATION_ENABLED", True))
    try:
        cursor_ttl_seconds = int(getattr(settings, "MCP_SEARCH_PAGINATION_TTL_SECONDS", 3600) or 3600)
    except (TypeError, ValueError):
        cursor_ttl_seconds = 3600
    cursor_ttl_seconds = max(60, cursor_ttl_seconds)
    try:
        pagination_prefetch_min = int(getattr(settings, "MCP_SEARCH_PAGINATION_PREFETCH_MIN", 50) or 50)
    except (TypeError, ValueError):
        pagination_prefetch_min = 50
    pagination_prefetch_min = max(0, pagination_prefetch_min)

    exclude_seen = bool(getattr(settings, "MCP_SEARCH_EXCLUDE_SEEN_ENABLED", False))
    raw_exclude_seen = arguments.get("exclude_seen")
    if isinstance(raw_exclude_seen, bool):
        exclude_seen = raw_exclude_seen

    def _excluded_chunk_ids() -> set[str]:
        if not exclude_seen:
            return set()
        try:
            shown = context.get_all_shown_this_conversation()
            chunk_ids = shown.get("chunk_ids", set())
            if isinstance(chunk_ids, set):
                return {str(cid) for cid in chunk_ids if cid}
        except Exception:
            pass
        # Best-effort fallback.
        return {
            str(cid)
            for cid in (getattr(context, "seen_chunk_ids", set()) | getattr(context, "newly_shown_chunk_ids", set()))
            if cid
        }

    def _page_snippets(
        snippets: Sequence[Mapping[str, object]],
        *,
        offset: int,
        page_size: int,
        excluded_chunk_ids: set[str],
    ) -> tuple[list[dict[str, object]], int, bool, int]:
        out: list[dict[str, object]] = []
        excluded = 0
        idx = max(0, int(offset))
        size = max(1, int(page_size))

        def _chunk_id(entry: Mapping[str, object]) -> str:
            return str(entry.get("chunk_id") or entry.get("id") or "").strip()

        while idx < len(snippets) and len(out) < size:
            entry = snippets[idx]
            idx += 1
            if not isinstance(entry, Mapping):
                continue
            cid = _chunk_id(entry)
            if cid and cid in excluded_chunk_ids:
                excluded += 1
                continue
            out.append(dict(entry))

        has_more = False
        if idx < len(snippets):
            if not excluded_chunk_ids:
                has_more = True
            else:
                for j in range(idx, len(snippets)):
                    entry = snippets[j]
                    if not isinstance(entry, Mapping):
                        continue
                    cid = _chunk_id(entry)
                    if cid and cid not in excluded_chunk_ids:
                        has_more = True
                        break

        return out, idx, has_more, excluded

    def _read_budget_hint_for_refs(refs: Sequence[Mapping[str, object]]) -> dict[str, int] | None:
        if not refs:
            return None
        total_suggested = 0
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            read_hint = ref.get("read_hint")
            if not isinstance(read_hint, Mapping):
                continue
            try:
                total_suggested += int(read_hint.get("suggested_max_chars") or 0)
            except (TypeError, ValueError):
                continue
        max_chars_allowed = int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)
        return {
            "total_suggested_max_chars": min(int(total_suggested), int(max_chars_allowed)),
            "max_chars_allowed": int(max_chars_allowed),
        }

    def _extract_agentic_manifests(
        refs: Sequence[Mapping[str, object]],
    ) -> dict[str, dict[str, object]]:
        """
        Persist only the manifests needed to resolve table_chunk refs.

        These manifests live on ToolExecutionContext and are populated by
        _convert_to_agentic_search_response. Cursor paging needs them to be
        present so read_knowledge can hydrate table anchors without requiring a re-search.
        """
        table_manifests: dict[str, dict[str, object]] = {}
        table_cache = getattr(context, "table_row_anchor_manifests", None)
        if not isinstance(table_cache, dict):
            return table_manifests

        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            kind = str(ref.get("kind") or "").strip().lower()
            ref_id = str(ref.get("id") or "").strip()
            if not ref_id:
                continue
            if kind == "table_chunk" and isinstance(table_cache, dict):
                manifest = table_cache.get(ref_id)
                if isinstance(manifest, Mapping):
                    table_manifests[ref_id] = dict(manifest)
        return table_manifests

    def _enforce_search_rate_limit() -> Mapping[str, object] | None:
        window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
        try:
            calls_per_minute = int(getattr(settings, "MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE", 120) or 0)
        except (TypeError, ValueError):
            calls_per_minute = 120
        calls_per_minute = 0 if calls_per_minute < 0 else calls_per_minute
        try:
            enforce_tool_rate_limit(
                business_profile=conversation.business_profile,
                tool="search_knowledge",
                rate_limit=ToolRateLimit(
                    calls_per_minute=None if calls_per_minute <= 0 else calls_per_minute,
                    window_seconds=window_seconds,
                    scope="business",
                ),
            )
        except ToolRateLimitExceeded as exc:
            return {
                "tool": "search_knowledge",
                "status": "throttled",
                "error": "rate_limited",
                "error_code": "rate_limited",
                "snippets": [],
                "throttle_notice": {"type": "rate_limited", "message": str(exc)},
            }
        return None

    raw_cursor = _coerce_str(arguments.get("cursor")).strip()
    if raw_cursor:
        # Cursor paging: bypass duplicate-intent reuse and serve next page from server cache.
        limited = _enforce_search_rate_limit()
        if limited is not None:
            return limited
        context.reserve_search()

        if not pagination_enabled:
            return {
                "tool": "search_knowledge",
                "status": "constraint_error",
                "error": "pagination_disabled",
                "error_code": "pagination_disabled",
            }

        decoded = _decode_search_cursor(raw_cursor, max_age_seconds=cursor_ttl_seconds)
        if not decoded:
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "invalid_cursor",
                "error_code": "invalid_cursor",
                "snippets": [],
            }
        session_id = str(decoded.get("sid") or "").strip()
        try:
            offset = int(decoded.get("o") or 0)
        except (TypeError, ValueError):
            offset = 0
        if not session_id:
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "invalid_cursor",
                "error_code": "invalid_cursor",
                "snippets": [],
            }

        cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
        session = cache.get(cache_key)
        if not isinstance(session, Mapping):
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "cursor_expired",
                "error_code": "cursor_expired",
                "snippets": [],
            }

        raw_limit = arguments.get("limit")
        try:
            page_size = int(raw_limit) if raw_limit is not None else None
        except (TypeError, ValueError):
            page_size = None
        if page_size is None:
            try:
                page_size = int(session.get("page_size") or 0) or SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT
            except (TypeError, ValueError):
                page_size = SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT
        page_size = max(1, min(int(page_size), MCP_PROMPT_MAX_SNIPPETS_CAP))

        session_version = 1
        try:
            session_version = int(session.get("version") or 1)
        except (TypeError, ValueError):
            session_version = 1

        # Agentic ref paging (v2): return up to limit refs (not snippet rows) and page over refs.
        if session_version >= 2:
            refs_full = session.get("refs")
            if isinstance(refs_full, list):
                offset_refs = max(0, int(offset))
                refs_total_found = session.get("refs_total_found")
                try:
                    refs_total_found = int(refs_total_found) if refs_total_found is not None else len(refs_full)
                except (TypeError, ValueError):
                    refs_total_found = len(refs_full)

                snippet_total_found = session.get("snippets_total_found")
                try:
                    snippet_total_found_int = int(snippet_total_found) if snippet_total_found is not None else None
                except (TypeError, ValueError):
                    snippet_total_found_int = None

                # Rehydrate anchor manifests so read_knowledge can resolve table_chunk refs.
                manifests_table = session.get("table_row_anchor_manifests")
                if isinstance(manifests_table, Mapping):
                    cache_value = getattr(context, "table_row_anchor_manifests", None)
                    if isinstance(cache_value, dict):
                        for key, value in dict(manifests_table).items():
                            if isinstance(value, Mapping):
                                cache_value[str(key)] = dict(value)

                refs_page = [dict(ref) for ref in refs_full[offset_refs : offset_refs + page_size] if isinstance(ref, Mapping)]
                next_offset = offset_refs + len(refs_page)
                has_more_refs = bool(next_offset < refs_total_found)
                next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset) if has_more_refs else None

                completeness_base = session.get("completeness_base")
                completeness: dict[str, object] = dict(completeness_base) if isinstance(completeness_base, Mapping) else {}
                # For transparency, keep snippet totals even though we page over refs.
                if snippet_total_found_int is not None:
                    completeness.setdefault("total_found", snippet_total_found_int)
                    completeness["snippets_total_found"] = snippet_total_found_int
                completeness["refs_total_found"] = int(refs_total_found)
                completeness["paging_mode"] = "refs"
                completeness["ref_offset"] = int(offset_refs)
                completeness["shown"] = len(refs_page)
                completeness["has_more"] = bool(has_more_refs)

                payload: dict[str, object] = {
                    "tool": "search_knowledge",
                    "query": str(session.get("query") or "").strip(),
                    "limit": page_size,
                    "query_intent": session.get("query_intent"),
                    "status": "ok" if refs_page else "not_found",
                    "diagnostics": {"cursor_used": True, "offset": offset_refs, "paging_mode": "refs"},
                    "refs": refs_page,
                    "total_found": int(refs_total_found),
                    "completeness": completeness,
                    "has_more": bool(has_more_refs),
                }
                read_budget_hint = _read_budget_hint_for_refs(refs_page)
                if read_budget_hint:
                    payload["read_budget_hint"] = read_budget_hint
                if next_cursor:
                    payload["next_cursor"] = next_cursor

                structured_log(
                    "mcp",
                    "search.agentic_paging",
                    {
                        "session_version": int(session_version),
                        "paging_mode": "refs",
                        "cursor_used": True,
                        "offset": int(offset_refs),
                        "limit": int(page_size),
                        "snippets_total_found": snippet_total_found_int,
                        "refs_total_found": int(refs_total_found),
                        "returned_refs": len(refs_page),
                        "has_more": bool(has_more_refs),
                    },
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                    logger_obj=logger,
                )

                return payload

        results_full = session.get("results")
        if not isinstance(results_full, list):
            results_full = []

        excluded_chunk_ids = _excluded_chunk_ids()
        page_snippets, next_offset, has_more, excluded_count = _page_snippets(
            results_full,
            offset=offset,
            page_size=page_size,
            excluded_chunk_ids=excluded_chunk_ids,
        )
        next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset) if has_more else None

        _page_snippets_out, completeness = _apply_seen_item_filter(page_snippets, context, mark_as_seen=False)
        page_snippets = _page_snippets_out
        completeness["total_found"] = len(results_full)
        completeness["has_more"] = bool(has_more)
        if excluded_count:
            completeness["excluded_seen"] = excluded_count
        if completeness.get("shown", 0) > 0 and not completeness.get("has_more"):
            if completeness.get("already_seen") == completeness.get("shown"):
                completeness["all_previously_shown"] = True
                completeness["message"] = (
                    f"All {completeness['shown']} matching results have already been shown in this conversation. "
                    "Try a different search term or ask the user if they need something specific."
                )

        query_text = str(session.get("query") or "").strip()
        query_intent = session.get("query_intent")
        if not page_snippets and results_full and exclude_seen:
            completeness["all_previously_shown"] = True
            completeness["message"] = (
                "No new results: all remaining matches were already shown earlier in this conversation. "
                "Use the earlier results, or change the query to find different matches."
            )
        payload: dict[str, object] = {
            "tool": "search_knowledge",
            "query": query_text,
            "limit": page_size,
            "query_intent": query_intent,
            "status": "ok" if page_snippets else "not_found",
            "diagnostics": {"cursor_used": True, "offset": offset},
            "snippets": page_snippets,
            "completeness": completeness,
            "has_more": bool(has_more),
        }
        if next_cursor:
            payload["next_cursor"] = next_cursor

        # Convert to agentic format when enabled.
        if rag_agentic_enabled:
            return _convert_to_agentic_search_response(
                payload,
                conversation=conversation,
                context=context,
            )
        return payload

    # Build queries list.
    # - `query` is the primary, single-query interface (back-compat and simpler).
    # - `queries[]` allows multiple variants for fanout.
    raw_queries_param = arguments.get("queries")
    raw_query_param = _coerce_str(arguments.get("query")).strip()
    queries: list[str] = []
    seen_queries: set[str] = set()

    def _append_query(candidate: str) -> None:
        normalized = candidate.strip()
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen_queries:
            return
        seen_queries.add(lowered)
        queries.append(normalized)

    if raw_query_param:
        _append_query(raw_query_param)
    if isinstance(raw_queries_param, (list, tuple)):
        for candidate in raw_queries_param:
            candidate_str = _coerce_str(candidate).strip()
            if candidate_str:
                _append_query(candidate_str)

    primary_query = queries[0] if queries else ""

    # =========================================================================
    # DIAGNOSTIC: Log document context state at search start
    # =========================================================================
    structured_log(
        "mcp",
        "search.context_state",
        {
            "query": primary_query,
            "primary_upload_id": context.primary_upload_id if context else None,
            "primary_document_title": context.get_primary_document_title() if context else None,
            "referenced_upload_ids": list(context.referenced_upload_ids)[:5] if context else [],
            "search_history_count": len(context.search_history) if context else 0,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )

    # =========================================================================
    # Context-Aware Query Rewriting (Conversation-Aware RAG)
    # =========================================================================
    # Rewrite follow-up queries to include document context for better retrieval.
    # Example: "fees for withdrawal" -> "Trade Bills EN: fees for withdrawal"
    rewrite_result = None
    rewrite_enabled = str(getattr(settings, "RAG_CONTEXT_QUERY_REWRITE_ENABLED", "true")).lower() in {"1", "true", "yes"}
    if rewrite_enabled and primary_query and context and context.has_strong_primary_document():
        try:
            from apps.rag.query_rewriter import (
                build_rewrite_context_from_tool_context,
                get_query_rewriter,
            )

            rewriter = get_query_rewriter()
            rewrite_context = build_rewrite_context_from_tool_context(context, conversation=conversation)
            rewrite_result = rewriter.rewrite(primary_query, rewrite_context)

            if rewrite_result.context_injected:
                # Replace the first query with the rewritten version.
                queries[0] = rewrite_result.rewritten_query
                primary_query = rewrite_result.rewritten_query
                structured_log(
                    "mcp",
                    "search.query_rewrite",
                    {
                        "original_query": rewrite_result.original_query,
                        "rewritten_query": rewrite_result.rewritten_query,
                        "strategy": rewrite_result.rewrite_strategy,
                        "confidence": rewrite_result.confidence,
                        "primary_document": context.primary_upload_id,
                    },
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                    logger_obj=logger,
                )
            else:
                # Log why rewrite was skipped
                structured_log(
                    "mcp",
                    "search.query_rewrite_skipped",
                    {
                        "query": primary_query,
                        "strategy": rewrite_result.rewrite_strategy,
                        "confidence": rewrite_result.confidence,
                        "primary_upload_id": context.primary_upload_id if context else None,
                        "primary_document_title": rewrite_context.primary_document_title,
                        "has_previous_queries": bool(rewrite_context.previous_queries),
                    },
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                    logger_obj=logger,
                )
        except Exception as exc:
            # Don't fail the search if rewriting fails
            logger.warning("Query rewriting failed: %s", exc, exc_info=True)

    # Fanout variants are controlled via MCP_SEARCH_MAX_QUERY_VARIANTS.
    # Keep this fully env-configurable so operators can tune recall/cost tradeoffs.
    query_variant_limit = max(
        1,
        int(getattr(settings, "MCP_SEARCH_MAX_QUERY_VARIANTS", DEFAULT_MAX_SEARCH_QUERY_VARIANTS)),
    )
    fanout_budget_ms = max(
        0,
        int(getattr(settings, "MCP_SEARCH_FANOUT_BUDGET_MS", 0) or 0),
    )

    def _prune_queries(values: Sequence[str]) -> list[str]:
        if len(values) <= query_variant_limit:
            return list(values)
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in values:
            normalized = entry.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(entry)
            if len(deduped) >= query_variant_limit:
                break
        if not deduped and values:
            deduped.append(values[0])
        return deduped

    optimized_queries = _prune_queries(queries)
    if len(optimized_queries) < len(queries):
        structured_log(
            "mcp",
            "search.query_pruned",
            {
                "original_count": len(queries),
                "kept": len(optimized_queries),
                "limit": query_variant_limit,
            },
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )
    queries = optimized_queries

    if not queries:
        return {
            "tool": "search_knowledge",
            "status": "error",
            "error": "query is required",
            "snippets": [],
        }

    # Layer 3: Semantic duplicate search detection (one intent per user turn).
    # Duplicate intents reuse prior results and do not consume per-turn search budget.
    duplicate_intent_enabled = bool(getattr(settings, "MCP_SEARCH_DUPLICATE_INTENT_ENABLED", True))
    result_fingerprint_dedup_enabled = bool(
        getattr(settings, "MCP_SEARCH_RESULT_FINGERPRINT_DEDUP_ENABLED", True)
    )
    try:
        result_fingerprint_top_k = int(
            getattr(settings, "MCP_SEARCH_RESULT_FINGERPRINT_TOP_K", 8) or 8
        )
    except (TypeError, ValueError):
        result_fingerprint_top_k = 8
    result_fingerprint_top_k = max(1, min(20, int(result_fingerprint_top_k)))

    def _normalize_intent_text(values: Sequence[str]) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw in values:
            token = str(raw or "").strip().lower()
            if not token:
                continue
            if token in seen:
                continue
            seen.add(token)
            parts.append(token)
        return " | ".join(parts)

    def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float | None:
        if not a or not b:
            return None
        if len(a) != len(b):
            return None
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b, strict=False):
            try:
                xf = float(x)
                yf = float(y)
            except (TypeError, ValueError):
                return None
            dot += xf * yf
            norm_a += xf * xf
            norm_b += yf * yf
        if norm_a <= 0.0 or norm_b <= 0.0:
            return None
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

    def _response_top_ids(response: Mapping[str, object], *, top_k: int) -> list[str]:
        ids: list[str] = []
        refs = response.get("refs")
        if isinstance(refs, list):
            for entry in refs:
                if not isinstance(entry, Mapping):
                    continue
                ref_id = str(entry.get("evidence_group_id") or entry.get("id") or "").strip()
                if ref_id:
                    ids.append(ref_id)
                if len(ids) >= top_k:
                    break
            return ids[:top_k]

        snippets_local = response.get("snippets")
        if isinstance(snippets_local, list):
            for entry in snippets_local:
                if not isinstance(entry, Mapping):
                    continue
                ref_id = str(
                    entry.get("evidence_group_id")
                    or entry.get("chunk_id")
                    or entry.get("id")
                    or ""
                ).strip()
                if ref_id:
                    ids.append(ref_id)
                if len(ids) >= top_k:
                    break
        return ids[:top_k]

    def _response_result_fingerprint(response: Mapping[str, object], *, top_k: int) -> tuple[str, list[str]]:
        top_ids = _response_top_ids(response, top_k=top_k)
        if not top_ids:
            return "", []
        digest = hashlib.sha256("|".join(top_ids).encode("utf-8")).hexdigest()[:16]
        return digest, top_ids

    intent_text = _normalize_intent_text(queries)
    intent_embedding: list[float] | None = None
    if new_contract_enabled and duplicate_intent_enabled:
        embedder = _portal_file_embedding_service()
        if embedder and intent_text:
            try:
                embedded = embedder.embed_text(intent_text)
                if isinstance(embedded, list) and embedded:
                    intent_embedding = [float(v) for v in embedded]
            except Exception:
                intent_embedding = None

        history = getattr(context, "search_history", None) or []
        best_match: Mapping[str, object] | None = None
        best_similarity: float | None = None
        if intent_text and isinstance(history, list) and history:
            # Only compare against a small recent window to avoid unbounded work.
            for entry in reversed(history[-12:]):
                if not isinstance(entry, Mapping):
                    continue
                prior_response = entry.get("response")
                if not isinstance(prior_response, Mapping):
                    continue
                prior_intent = str(entry.get("intent") or entry.get("query") or "").strip().lower()
                if not prior_intent:
                    continue

                similarity: float | None = None
                if intent_embedding is not None and embedder:
                    prior_embedding = entry.get("embedding")
                    if not isinstance(prior_embedding, list) or not prior_embedding:
                        try:
                            embedded = embedder.embed_text(prior_intent)
                            if isinstance(embedded, list) and embedded:
                                prior_embedding = [float(v) for v in embedded]
                                # Cache embedding for future comparisons (not returned to the LLM).
                                try:
                                    entry["embedding"] = prior_embedding
                                except Exception:
                                    pass
                        except Exception:
                            prior_embedding = None
                    if isinstance(prior_embedding, list) and prior_embedding:
                        similarity = _cosine_similarity(intent_embedding, prior_embedding)

                if similarity is None:
                    similarity = 1.0 if prior_intent == intent_text else 0.0

                if best_similarity is None or similarity > best_similarity:
                    best_similarity = similarity
                    best_match = entry

            if best_match is not None and best_similarity is not None and best_similarity >= 0.85:
                prior_response = best_match.get("response")
                if isinstance(prior_response, Mapping):
                    duplicate_payload: dict[str, object] = {
                        "tool": "search_knowledge",
                        "status": "duplicate",
                        "error": "duplicate_intent",
                        "error_code": "duplicate_intent",
                        "diagnostics": {"dedup_similarity": round(float(best_similarity), 4)},
                    }
                    if rag_agentic_enabled:
                        # Phase 1: agentic search returns EvidenceRefs (`refs[]`).
                        duplicate_payload["refs"] = list(prior_response.get("refs") or prior_response.get("results") or [])
                        if prior_response.get("total_found") not in {None, ""}:
                            duplicate_payload["total_found"] = prior_response.get("total_found")
                    else:
                        duplicate_payload["snippets"] = list(prior_response.get("snippets") or [])
                    # Bubble up pagination hints so the model can page instead of re-searching.
                    if prior_response.get("next_cursor"):
                        duplicate_payload["next_cursor"] = prior_response.get("next_cursor")
                    if prior_response.get("has_more") not in {None, ""}:
                        duplicate_payload["has_more"] = prior_response.get("has_more")
                    return duplicate_payload

    raw_limit = arguments.get("limit")
    try:
        page_size_requested = int(raw_limit) if raw_limit is not None else None
    except (TypeError, ValueError):
        page_size_requested = None
    if page_size_requested is None:
        # Server-side default when callers omit `limit`. Without this, the underlying
        # search service may treat limit=None as unbounded and return huge result sets.
        page_size_requested = SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT

    page_size = max(1, min(int(page_size_requested), MCP_PROMPT_MAX_SNIPPETS_CAP))
    fetch_limit = page_size
    if pagination_enabled and pagination_prefetch_min:
        # Prefetch more candidates than page_size so cursor pagination has results to serve.
        # Use SEARCH_PREFETCH_ABSOLUTE_CAP (not MCP_PROMPT_MAX_SNIPPETS_CAP) to avoid
        # clamping prefetch to the same value as page_size, which made pagination a no-op.
        fetch_limit = max(page_size, min(int(pagination_prefetch_min), SEARCH_PREFETCH_ABSOLUTE_CAP))
    requested_limit = fetch_limit

    service = _knowledge_service()
    identifier_filter: dict[str, object] | None = None
    locked_key = None
    locked_value = None

    agent_scope = _agent_knowledge_scope(conversation, context)
    agent_explicit_upload_ids = _scope_upload_ids_to_uuids(agent_scope.explicit_upload_ids) if agent_scope.explicit_upload_ids else None
    combined_upload_ids: list[uuid.UUID] | None = agent_explicit_upload_ids if agent_scope.restricted else None

    def _effective_limit(base_limit: int | None) -> int | None:
        # Agentic contract: intent classification is advisory (routing/logging),
        # and must not override the caller's explicit result size request.
        limit_val = base_limit
        if limit_val is not None:
            limit_val = max(1, min(int(limit_val), MCP_PROMPT_MAX_SNIPPETS_CAP))
        return limit_val

    def _log_search_performance(
        *,
        snippets: Sequence[Mapping[str, object]],
        diagnostics: Mapping[str, object] | None,
        intent: str | None,
        limit_value: int | None,
        status: str,
        note: str | None = None,
    ) -> None:
        diag = dict(diagnostics or {})
        snippet_count = len(snippets)
        diag.setdefault("snippet_count", snippet_count)
        warn_ms = int(getattr(settings, "MCP_SLO_SEARCH_WARN_MS", 1200) or 0)
        detail = {
            "status": status,
            "intent": intent,
            "path": diag.get("path"),
            "snippet_count": snippet_count,
            "limit": limit_value,
            "agent_scope_mode": agent_scope.mode,
            "agent_scope_explicit_uploads": len(agent_scope.explicit_upload_ids) if agent_scope.restricted else None,
            "effective_scope_uploads": len(combined_upload_ids) if combined_upload_ids is not None else None,
            "total_ms": diag.get("total_duration_ms"),
            "alias_ms": diag.get("alias_duration_ms"),
            "vector_ms": diag.get("vector_duration_ms"),
            "lexical_ms": diag.get("fts_duration_ms"),
            "rerank_ms": diag.get("rerank_duration_ms"),
            "table_ms": diag.get("table_duration_ms"),
            "table_context_ms": diag.get("table_context_ms"),
            "table_presence_ms": diag.get("table_presence_ms"),
            "chunk_candidates": diag.get("chunk_candidate_count"),
            "alias_hits": diag.get("alias_hits"),
            "table_reason": diag.get("table_reason"),
            "cache_hit": diag.get("cache_hit"),
            "cache_scope": diag.get("cache_scope"),
            "read_required": sum(1 for payload in snippets if isinstance(payload, Mapping) and payload.get("read_required")),
        }
        if note:
            detail["note"] = note
        total_ms = detail.get("total_ms")
        slow = bool(
            warn_ms
            and isinstance(total_ms, (int, float))
            and float(total_ms) >= float(warn_ms)
        )
        if slow:
            detail["slo"] = "slow"
            detail["slo_warn_ms"] = warn_ms
        structured_log(
            "mcp",
            "search.performance",
            detail,
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            level=logging.WARNING if slow else logging.INFO,
        )

    def _execute_single_query(
        query_text: str,
        *,
        intent_info_override: Mapping[str, object] | None = None,
        intent_override: str | None = None,
        limit_override: int | None = None,
        precomputed_result: object | None = None,
    ) -> Mapping[str, object]:
        # Thin MCP wrapper: execute a single RAG search and translate the result
        # into MCP's stable tool contract. Retrieval planning, ranking, fusion,
        # and fallback decisions must remain in apps.rag.ai_orchestrator.
        intent_info = intent_info_override or _query_intent(query_text)
        intent = intent_override or intent_info.get("intent")
        normalized_query = query_text.lower()
        aggregation_keywords = (
            "total",
            "sum",
            "overall",
            "aggregate",
            "اجمالي",
            "إجمالي",
            "الاجمالي",
            "المجموع",
        )
        aggregation_query = any(keyword in normalized_query for keyword in aggregation_keywords)
        limit_for_run = limit_override if limit_override is not None else _effective_limit(requested_limit)
        search_cache_key = _search_cache_key(
            query_text,
            limit_for_run,
            identifier_filter,
            locked_key,
            str(locked_value) if locked_value is not None else None,
        )
        cached_result = None
        if context.search_cache and search_cache_key in context.search_cache:
            cached_result = copy.deepcopy(context.search_cache[search_cache_key])
        if cached_result:
            structured_log(
                "mcp",
                "search.cache_hit",
                {
                    "query": query_text,
                    "intent": intent,
                    "limit": limit_for_run,
                },
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
            )
            cached_result["query"] = query_text
            cached_result["limit_used"] = limit_for_run
            cached_result["cache_hit"] = True
            cached_result.setdefault("query_intent", intent)
            cached_result.setdefault("intent_signal", intent_info)
            cached_result.setdefault("snippets", [])
            cached_result["snippets"] = [dict(snippet) for snippet in cached_result.get("snippets", [])]
            cached_result["cache_hit"] = True
            return cached_result

        if precomputed_result is not None:
            result = precomputed_result
        else:
            session_context = {
                "primary_upload_id": context.primary_upload_id,
                "referenced_upload_ids": list(context.referenced_upload_ids),
                "document_context": context.document_context,
            } if context else None
            result = service.search(
                business_profile=conversation.business_profile,
                query=query_text,
                limit=limit_for_run,
                identifier_filter=identifier_filter,
                allowed_upload_ids=combined_upload_ids,
                allowed_explicit_upload_ids=agent_explicit_upload_ids,
                session_context=session_context,
            )
        combined_snippets: list[object] = list(getattr(result, "snippets", []) or [])
        snippet_payloads = _serialize_snippets(combined_snippets)

        # Track referenced documents so later turns can keep document context.
        doc_context_enabled = str(getattr(settings, "RAG_DOCUMENT_CONTEXT_ENABLED", "true")).lower() in {"1", "true", "yes"}
        if doc_context_enabled:
            for payload in snippet_payloads:
                upload_id = payload.get("upload_id") or payload.get("document_id")
                if upload_id:
                    # Extract document title from snippet
                    title = payload.get("title", "")
                    if title and " – " in title:
                        # Format is often "Document Name – chunk N"
                        title = title.split(" – ")[0].strip()
                    elif title and " - " in title:
                        title = title.split(" - ")[0].strip()

                    context.track_document_reference(
                        upload_id=str(upload_id),
                        title=title,
                        stage=payload.get("search_stage", "unknown"),
                        confidence=payload.get("confidence_score") if isinstance(payload.get("confidence_score"), (int, float)) else None,
                    )

        read_required = False
        read_required_reasons_summary: set[str] = set()
        for payload in snippet_payloads:
            payload_read_required, reasons = _compute_read_required(payload)
            payload["read_required"] = payload_read_required
            if reasons:
                payload["read_required_reasons"] = reasons
                read_required_reasons_summary.update(reasons)
            if payload_read_required:
                read_required = True
            chunk_id = payload.get("chunk_id") or payload.get("id")
            upload_id = payload.get("upload_id")
            chunk_index = payload.get("chunk_index")
            
            # FIXED: Use actual page number from metadata, not chunk_index + 1
            # Per Codex review: text.page now means PDF page number, not chunk index
            payload_meta = payload.get("metadata") or {}
            if isinstance(payload_meta, dict):
                actual_page = (
                    payload_meta.get("table_page_number") or 
                    payload_meta.get("chunk_page") or 
                    payload_meta.get("page_number") or
                    payload.get("page_number")
                )
            else:
                actual_page = payload.get("page_number")
            
            is_table_payload = bool(
                payload.get("is_table_chunk")
                or payload.get("structured_table_count")
                or payload.get("table_read_only")
                or (isinstance(payload_meta, dict) and payload_meta.get("is_table_chunk"))
            )
            read_id = str(chunk_id or "").strip() if is_table_payload else str(upload_id or chunk_id or "").strip()
            # Build read_hint with page (if known) or offset (for chunk-based access)
            mode_hint = "full_page" if is_table_payload else ("full_page" if intent == "identifier" else "excerpt")
            read_hint: dict[str, object] = {
                # For table chunks, prefer the chunk id so readers can upgrade to full-page table content.
                "document_id": read_id,
                "mode": mode_hint,
            }
            
            if actual_page:
                try:
                    page_num = int(actual_page)
                    if page_num >= 1:
                        read_hint["page"] = page_num
                except (TypeError, ValueError):
                    pass
            
            # Fallback: use offset if no page number known
            if "page" not in read_hint and isinstance(chunk_index, int):
                read_hint["offset"] = chunk_index
            
            payload["read_hint"] = read_hint
        snippet_payloads = _sanitize_snippet_payloads_for_prompt(snippet_payloads, conversation=conversation)
        log_meta = {"query": query_text, "intent": intent, "read_required": read_required}
        if read_required_reasons_summary:
            log_meta["read_required_reasons"] = sorted(read_required_reasons_summary)
        _log_snippet_payloads(
            tool="search_knowledge",
            conversation=conversation,
            snippet_payloads=snippet_payloads,
            meta=log_meta,
        )
        _log_search_performance(
            snippets=snippet_payloads,
            diagnostics=result.diagnostics,
            intent=intent,
            limit_value=limit_for_run,
            status=result.status,
        )
        result_diagnostics = dict(result.diagnostics or {})

        payload = {
            "tool": "search_knowledge",
            "query": query_text,
            "limit": limit_for_run,
            "limit_used": limit_for_run,
            "query_intent": intent,
            "intent_signal": intent_info,
            "status": result.status,
            "diagnostics": result_diagnostics,
            "snippets": snippet_payloads,
        }

        payload["read_required_summary"] = {
            "any": read_required,
            "reasons": sorted(read_required_reasons_summary),
        }
        if search_cache_key:
            _bounded_cache_store(context.search_cache, search_cache_key, copy.deepcopy(payload))
        return payload

    resolved_runs: list[tuple[int, Mapping[str, object]]] = []
    pending_specs: list[tuple[int, str, Mapping[str, object], str | None, int | None]] = []
    non_cached_queries = 0
    for idx, query_text in enumerate(queries):
        intent_info = _query_intent(query_text)
        intent = intent_info.get("intent")
        limit_for_run = _effective_limit(requested_limit)
        search_cache_key = _search_cache_key(
            query_text,
            limit_for_run,
            identifier_filter,
            locked_key,
            str(locked_value) if locked_value is not None else None,
        )
        cached_result = None
        if context.search_cache and search_cache_key in context.search_cache:
            cached_result = copy.deepcopy(context.search_cache[search_cache_key])
        if cached_result:
            structured_log(
                "mcp",
                "search.cache_hit",
                {
                    "query": query_text,
                    "intent": intent,
                    "limit": limit_for_run,
                },
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
            )
            cached_result["query"] = query_text
            cached_result["limit_used"] = limit_for_run
            cached_result.setdefault("query_intent", intent)
            cached_result.setdefault("intent_signal", intent_info)
            cached_result.setdefault("snippets", [])
            cached_result["snippets"] = [dict(snippet) for snippet in cached_result.get("snippets", [])]
            cached_result["cache_hit"] = True
            resolved_runs.append((idx, cached_result))
            continue
        pending_specs.append((idx, query_text, intent_info, intent, limit_for_run))
        non_cached_queries += 1

    search_budget_reserved = False
    if non_cached_queries > 0:
        # Enforce limits only when this call needs a backend search.
        # Pure cache reuses (same intent/query in the same turn) should not
        # consume per-turn search budget.
        limited = _enforce_search_rate_limit()
        if limited is not None:
            return limited
        context.reserve_search()
        search_budget_reserved = True

    executor: ThreadPoolExecutor | None = None
    futures: list[tuple[int, str, Mapping[str, object], str | None, int | None, object]] = []
    fanout_start = time.perf_counter()
    fanout_parallel_enabled = bool(getattr(settings, "MCP_SEARCH_FANOUT_PARALLEL", False))
    max_parallel_workers = int(getattr(settings, "MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS", 4) or 4)
    max_parallel_workers = max(1, min(8, max_parallel_workers))
    use_parallel = bool(fanout_parallel_enabled and non_cached_queries > 1 and fanout_budget_ms <= 0)
    if use_parallel:
        executor = ThreadPoolExecutor(
            max_workers=min(non_cached_queries, max_parallel_workers),
            thread_name_prefix="mcp_search",
        )
    try:
        for idx, query_text, intent_info, intent, limit_for_run in pending_specs:
            if not executor and fanout_budget_ms:
                elapsed_ms = int((time.perf_counter() - fanout_start) * 1000)
                if resolved_runs and elapsed_ms >= fanout_budget_ms:
                    structured_log(
                        "mcp",
                        "search.fanout_budget_exceeded",
                        {
                            "budget_ms": fanout_budget_ms,
                            "elapsed_ms": elapsed_ms,
                            "queries_planned": len(pending_specs),
                            "queries_run": len(resolved_runs),
                        },
                        context={
                            "conversation": conversation.id,
                            "business": conversation.business_profile_id,
                        },
                        logger_obj=logger,
                        level=logging.WARNING,
                    )
                    break
            if executor:
                future = executor.submit(
                    service.search,
                    business_profile=conversation.business_profile,
                    query=query_text,
                    limit=limit_for_run,
                    identifier_filter=identifier_filter,
                )
                futures.append((idx, query_text, intent_info, intent, limit_for_run, future))
            else:
                result = service.search(
                    business_profile=conversation.business_profile,
                    query=query_text,
                    limit=limit_for_run,
                    identifier_filter=identifier_filter,
                    allowed_upload_ids=combined_upload_ids,
                    allowed_explicit_upload_ids=agent_explicit_upload_ids,
                )
                run_payload = _execute_single_query(
                    query_text,
                    intent_info_override=intent_info,
                    intent_override=intent,
                    limit_override=limit_for_run,
                    precomputed_result=result,
                )
                resolved_runs.append((idx, run_payload))
                if run_payload.get("status") not in {"ok", "not_found"}:
                    if len(queries) > 1:
                        run_payload = dict(run_payload)
                        run_payload["batched_queries"] = tuple(queries)
                    return run_payload
        if executor:
            for idx, query_text, intent_info, intent, limit_for_run, future in sorted(futures, key=lambda entry: entry[0]):
                result = future.result()
                run_payload = _execute_single_query(
                    query_text,
                    intent_info_override=intent_info,
                    intent_override=intent,
                    limit_override=limit_for_run,
                    precomputed_result=result,
                )
                resolved_runs.append((idx, run_payload))
                if run_payload.get("status") not in {"ok", "not_found"}:
                    if len(queries) > 1:
                        run_payload = dict(run_payload)
                        run_payload["batched_queries"] = tuple(queries)
                    return run_payload
    finally:
        if executor:
            executor.shutdown(wait=True)

    resolved_runs.sort(key=lambda entry: entry[0])
    runs = [payload for _, payload in resolved_runs]

    if not runs:
        return {
            "tool": "search_knowledge",
            "query": "",
            "limit": requested_limit,
            "status": "not_found",
            "snippets": [],
            "error": "no_query",
        }

    primary_run = runs[0]
    limit_cap = primary_run.get("limit_used")
    clip_limit = int(limit_cap) if isinstance(limit_cap, int) and limit_cap > 0 else None
    deduped_snippets, fusion = _fuse_batched_search_runs(
        runs,
        clip_limit=clip_limit,
    )

    results_full = deduped_snippets
    total_found = len(results_full)

    excluded_chunk_ids = _excluded_chunk_ids()
    page_snippets, next_offset, has_more, excluded_count = _page_snippets(
        results_full,
        offset=0,
        page_size=page_size,
        excluded_chunk_ids=excluded_chunk_ids,
    )

    next_cursor = None
    session_id = None
    if pagination_enabled and has_more and results_full and not rag_agentic_enabled:
        session_id = str(uuid.uuid4())
        cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
        cache.set(
            cache_key,
            {
                "version": 1,
                "query": primary_run.get("query"),
                "query_intent": primary_run.get("query_intent") or primary_run.get("intent"),
                "page_size": page_size,
                "results": results_full,
            },
            cursor_ttl_seconds,
        )
        next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset)

    _page_snippets_out, completeness = _apply_seen_item_filter(page_snippets, context, mark_as_seen=False)
    page_snippets = _page_snippets_out
    completeness["total_found"] = total_found
    completeness["has_more"] = bool(has_more)
    if excluded_count:
        completeness["excluded_seen"] = excluded_count
    if completeness.get("shown", 0) > 0 and not completeness.get("has_more"):
        if completeness.get("already_seen") == completeness.get("shown"):
            completeness["all_previously_shown"] = True
            completeness["message"] = (
                f"All {completeness['shown']} matching results have already been shown in this conversation. "
                "Try a different search term or ask the user if they need something specific."
            )

    # Mark the returned page as seen (for follow-up paging within this turn).
    _mark_snippets_as_seen(page_snippets, context)

    # Log seen-item tracking results for debugging
    if completeness.get("already_seen", 0) > 0 or completeness.get("clipped", 0) > 0:
        structured_log(
            "mcp",
            "search.seen_tracking",
            {
                "shown": completeness.get("shown", 0),
                "already_seen": completeness.get("already_seen", 0),
                "clipped": completeness.get("clipped", 0),
                "total_found": completeness.get("total_found", 0),
                "all_previously_shown": completeness.get("all_previously_shown", False),
            },
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
        )

    def _normalized_run_status(run: Mapping[str, object] | None) -> str:
        if not isinstance(run, Mapping):
            return ""
        return str(run.get("status") or "").strip().lower()

    # Agentic RAG should not block on "needs_clarification". Always return best-effort evidence
    # (if any) and let the assistant handle ambiguity/conflicts in the response.
    status_source_run: Mapping[str, object] = next(
        (run for run in runs if _normalized_run_status(run) == "ok"),
        primary_run,
    )
    if page_snippets:
        final_status = "ok"
    else:
        non_default_status_run = next(
            (
                run
                for run in runs
                if _normalized_run_status(run) not in {"", "not_found", "needs_clarification"}
            ),
            None,
        )
        if non_default_status_run is not None:
            status_source_run = non_default_status_run
            final_status = _normalized_run_status(non_default_status_run)
        else:
            status_source_run = runs[-1]
            final_status = _normalized_run_status(status_source_run) or "not_found"
            if final_status == "needs_clarification":
                final_status = "not_found"

    for snippet in page_snippets:
        context.add_retrieval_candidate(snippet)

    metrics = _log_tool_metrics(
        tool="search_knowledge",
        conversation=conversation,
        snippets=page_snippets,
        extra={
            "status": final_status,
            "primary_status": primary_run.get("status"),
            "status_source_query": status_source_run.get("query"),
            "limit": page_size,
            "query_length": len(str(primary_run.get("query") or "")),
            "fusion": fusion,
            "fanout_budget_ms": fanout_budget_ms,
            "fanout_parallel_enabled": bool(fanout_parallel_enabled),
            "fanout_parallel_used": bool(use_parallel),
            "queries_planned": len(queries),
            "queries_run": len(runs),
        },
    )
    context.reserve_characters(int(metrics.get("char_count", 0)))

    diag = dict(status_source_run.get("diagnostics") or {})
    try:
        diag["final_status_source_index"] = int(runs.index(status_source_run))
    except ValueError:
        diag["final_status_source_index"] = 0
    diag["final_status_source_query"] = status_source_run.get("query")
    if final_status == "not_found":
        no_result_reason = _extract_no_result_reason(diag)
        if not no_result_reason:
            for run in runs:
                run_diag = run.get("diagnostics")
                if not isinstance(run_diag, Mapping):
                    continue
                candidate_reason = _extract_no_result_reason(run_diag)
                if candidate_reason:
                    no_result_reason = candidate_reason
                    break
        if no_result_reason:
            diag["no_result_reason"] = no_result_reason
    diag["batched_runs"] = [
        {
            "query": run.get("query"),
            "status": run.get("status"),
            "snippet_count": len(run.get("snippets", [])),
            "cache_hit": bool(run.get("cache_hit")),
        }
        for run in runs
    ]
    diag["batched_queries"] = queries

    query_intent = (
        status_source_run.get("query_intent")
        or status_source_run.get("intent")
        or primary_run.get("query_intent")
        or primary_run.get("intent")
    )

    if not page_snippets and total_found and exclude_seen:
        completeness["all_previously_shown"] = True
        completeness["message"] = (
            f"No new results: the top {total_found} matches were already shown earlier in this conversation. "
            "Use the earlier results, or change the query to find different matches."
        )

    payload = {
        "tool": "search_knowledge",
        "query": primary_run.get("query"),
        "limit": page_size,
        "query_intent": query_intent,
        "intent_signal": primary_run.get("intent_signal"),
        "status": final_status,
        "diagnostics": diag,
        "snippets": page_snippets,
    }

    # Always include completeness metadata for transparent decisions
    payload["completeness"] = completeness
    payload["has_more"] = bool(has_more)
    if next_cursor:
        payload["next_cursor"] = next_cursor

    if fusion:
        payload["fusion"] = fusion
    if len(queries) > 1:
        payload["batched_queries"] = tuple(queries)

    # Convert to agentic format when enabled, and persist search intent metadata
    # for semantic dedup within this user turn.
    if rag_agentic_enabled:
        if final_status in {"ok", "not_found"} and results_full:
            # Agentic search must return up to `limit` unique refs. The legacy flow
            # converts only the first snippet page, which can collapse to fewer refs
            # (e.g., 10 snippets -> 3 refs) and starve the model of evidence breadth.
            #
            # Fix: derive refs from the full candidate set (prefetch window) and page
            # over refs (not snippets). This preserves stable pagination and avoids
            # skipping refs when dedupe collapses multiple snippets into one ref.
            full_payload = dict(payload)
            full_payload["snippets"] = [dict(snippet) for snippet in results_full if isinstance(snippet, Mapping)]
            agentic_full = _convert_to_agentic_search_response(
                full_payload,
                conversation=conversation,
                context=context,
            )
            refs_full_raw = agentic_full.get("refs")
            refs_full: list[dict[str, object]] = (
                [dict(ref) for ref in refs_full_raw if isinstance(ref, Mapping)]
                if isinstance(refs_full_raw, list)
                else []
            )
            refs_total_found = len(refs_full)
            refs_page = refs_full[:page_size]
            has_more_refs = bool(refs_total_found > len(refs_page))

            # Cache a ref paging session for cursor paging.
            next_cursor = None
            session_id = None
            if pagination_enabled and has_more_refs and refs_full:
                session_id = str(uuid.uuid4())
                cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
                manifests_table = _extract_agentic_manifests(refs_full)
                completeness_base = agentic_full.get("completeness")
                completeness_base_out = dict(completeness_base) if isinstance(completeness_base, Mapping) else {}
                completeness_base_out["refs_total_found"] = int(refs_total_found)
                completeness_base_out["paging_mode"] = "refs"
                completeness_base_out["snippets_total_found"] = int(total_found)
                cache.set(
                    cache_key,
                    {
                        "version": 2,
                        "query": payload.get("query"),
                        "query_intent": payload.get("query_intent"),
                        "page_size": page_size,
                        "snippets_total_found": int(total_found),
                        "refs_total_found": int(refs_total_found),
                        "refs": refs_full,
                        "completeness_base": completeness_base_out,
                        "table_row_anchor_manifests": manifests_table,
                    },
                    cursor_ttl_seconds,
                )
                next_cursor = _encode_search_cursor(session_id=session_id, offset=len(refs_page))

            completeness_out = dict(agentic_full.get("completeness") or {}) if isinstance(agentic_full.get("completeness"), Mapping) else {}
            # Normalize agentic completeness to what we actually returned.
            completeness_out.setdefault("total_found", int(total_found))  # snippet candidates
            completeness_out["snippets_total_found"] = int(total_found)
            completeness_out["refs_total_found"] = int(refs_total_found)
            completeness_out["paging_mode"] = "refs"
            completeness_out["ref_offset"] = 0
            completeness_out["shown"] = len(refs_page)
            completeness_out["has_more"] = bool(has_more_refs)

            final_response = {
                **{k: v for k, v in dict(agentic_full).items() if k not in {"refs", "next_cursor", "has_more", "total_found", "read_budget_hint", "completeness"}},
                "tool": "search_knowledge",
                "query": payload.get("query"),
                "limit": int(page_size),
                "query_intent": payload.get("query_intent"),
                "status": agentic_full.get("status") if refs_page else "not_found",
                "refs": refs_page,
                # In agentic ref paging, total_found refers to refs (not snippets).
                "total_found": int(refs_total_found),
                "completeness": completeness_out,
                "has_more": bool(has_more_refs),
            }
            read_budget_hint = _read_budget_hint_for_refs(refs_page)
            if read_budget_hint:
                final_response["read_budget_hint"] = read_budget_hint
            if next_cursor:
                final_response["next_cursor"] = next_cursor

            structured_log(
                "mcp",
                "search.agentic_paging",
                {
                    "session_version": 2,
                    "session_stored": bool(next_cursor),
                    "paging_mode": "refs",
                    "cursor_used": False,
                    "offset": 0,
                    "limit": int(page_size),
                    "snippets_total_found": int(total_found),
                    "refs_total_found": int(refs_total_found),
                    "returned_refs": len(refs_page),
                    "has_more": bool(has_more_refs),
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )
        else:
            final_response = _convert_to_agentic_search_response(
                payload,
                conversation=conversation,
                context=context,
            )
    else:
        final_response = payload

    result_fingerprint = ""
    result_top_ids: list[str] = []
    if isinstance(final_response, Mapping):
        result_fingerprint, result_top_ids = _response_result_fingerprint(
            final_response,
            top_k=result_fingerprint_top_k,
        )

    if (
        new_contract_enabled
        and duplicate_intent_enabled
        and result_fingerprint_dedup_enabled
        and result_fingerprint
    ):
        history = getattr(context, "search_history", None) or []
        if isinstance(history, list) and history:
            fingerprint_match: Mapping[str, object] | None = None
            for entry in reversed(history[-12:]):
                if not isinstance(entry, Mapping):
                    continue
                prior_response = entry.get("response")
                if not isinstance(prior_response, Mapping):
                    continue
                prior_fingerprint = str(entry.get("result_fingerprint") or "").strip()
                prior_top_ids_raw = entry.get("result_top_ids")
                if not prior_fingerprint:
                    prior_fingerprint, prior_top_ids = _response_result_fingerprint(
                        prior_response,
                        top_k=result_fingerprint_top_k,
                    )
                    prior_top_ids_raw = prior_top_ids
                if prior_fingerprint != result_fingerprint:
                    continue
                normalized_prior_top_ids = [
                    str(value).strip()
                    for value in (prior_top_ids_raw if isinstance(prior_top_ids_raw, list) else [])
                    if str(value).strip()
                ][:result_fingerprint_top_k]
                if normalized_prior_top_ids and normalized_prior_top_ids != result_top_ids[:result_fingerprint_top_k]:
                    continue
                fingerprint_match = entry
                break

            if fingerprint_match is not None:
                prior_response = fingerprint_match.get("response")
                if isinstance(prior_response, Mapping):
                    if search_budget_reserved and int(getattr(context, "searches_used", 0) or 0) > 0:
                        context.searches_used = max(0, int(context.searches_used) - 1)

                    duplicate_payload: dict[str, object] = {
                        "tool": "search_knowledge",
                        "status": "duplicate",
                        "error": "duplicate_results",
                        "error_code": "duplicate_results",
                        "diagnostics": {
                            "dedup_strategy": "result_fingerprint",
                            "dedup_result_fingerprint": result_fingerprint,
                            "dedup_top_ids": result_top_ids[:result_fingerprint_top_k],
                        },
                    }
                    if rag_agentic_enabled:
                        duplicate_payload["refs"] = list(
                            prior_response.get("refs") or prior_response.get("results") or []
                        )
                        if prior_response.get("total_found") not in {None, ""}:
                            duplicate_payload["total_found"] = prior_response.get("total_found")
                    else:
                        duplicate_payload["snippets"] = list(prior_response.get("snippets") or [])
                    if prior_response.get("next_cursor"):
                        duplicate_payload["next_cursor"] = prior_response.get("next_cursor")
                    if prior_response.get("has_more") not in {None, ""}:
                        duplicate_payload["has_more"] = prior_response.get("has_more")
                    final_response = duplicate_payload
                    result_fingerprint, result_top_ids = _response_result_fingerprint(
                        prior_response,
                        top_k=result_fingerprint_top_k,
                    )

    try:
        history_entry = {
            "intent": intent_text,
            "embedding": intent_embedding,
            "response": copy.deepcopy(final_response),
            "result_fingerprint": result_fingerprint,
            "result_top_ids": list(result_top_ids[:result_fingerprint_top_k]),
        }
        context.search_history.append(history_entry)
        if len(context.search_history) > 25:
            context.search_history = context.search_history[-25:]
    except Exception:
        pass

    return final_response


_AGENTIC_READ_CURSOR_V2_SALT = b"mcp.read_cursor.v2"
_AGENTIC_READ_CURSOR_V2_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign_agentic_read_cursor_v2(payload: Mapping[str, object]) -> str:
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    body = _b64url_encode(json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    sig = hmac.new(secret, _AGENTIC_READ_CURSOR_V2_SALT + body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _verify_agentic_read_cursor_v2(cursor: str) -> dict[str, object]:
    raw = (cursor or "").strip()
    if not raw or "." not in raw:
        raise ValueError("invalid cursor")
    body_b64, sig_b64 = raw.split(".", 1)
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    expected = hmac.new(secret, _AGENTIC_READ_CURSOR_V2_SALT + body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(sig_b64)
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if not hmac.compare_digest(expected, provided):
        raise ValueError("invalid cursor")
    try:
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid cursor")
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        raise ValueError("invalid cursor")
    if exp and exp <= int(time.time()):
        raise ValueError("expired cursor")
    return payload


def _agentic_read_v2_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Agentic Read V2 engine for refs-first retrieval.

    Internal interface (called by read_knowledge wrapper):
      - items=[{id,cursor?}...], max_chars=...

    Public interface (enforced by read_knowledge):
      - refs=[{id,cursor?}...], max_chars=...

    The tool selects the retrieval strategy internally (page blocks vs table rows vs
    chunk-window fallback) and returns deterministic continuation cursors when the
    requested content doesn't fit.
    """
    raw_items = arguments.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_items",
            "error_code": "missing_items",
            "evidence": [],
            "hint": "refs[] is required (use ids/cursors from search_knowledge/read_knowledge).",
        }

    # Per-call output cap: stay under MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS minus margin.
    try:
        raw_prompt_output_limit = getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", 12000)
        prompt_output_limit = int(raw_prompt_output_limit if raw_prompt_output_limit is not None else 12000)
    except (TypeError, ValueError):
        prompt_output_limit = 12000
    try:
        raw_safety_margin = (
            getattr(settings, "MCP_READ_KNOWLEDGE_MAX_CHARS_MARGIN", None)
            or getattr(settings, "MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", 800)
        )
        safety_margin = int(raw_safety_margin if raw_safety_margin is not None else 800)
    except (TypeError, ValueError):
        safety_margin = 800
    safe_prompt_limit = max(200, prompt_output_limit - max(0, safety_margin))

    remaining_turn_budget: int | None = None
    if context.char_budget_per_turn is not None:
        remaining_turn_budget = max(0, int(context.char_budget_per_turn) - int(context.characters_used or 0))

    max_chars_allowed = max(200, min(20000, safe_prompt_limit))
    if remaining_turn_budget is not None:
        max_chars_allowed = max(0, min(max_chars_allowed, remaining_turn_budget))
    if max_chars_allowed < 200:
        return {
            "tool": "read_knowledge",
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "evidence": [],
            "hint": "Prompt budget exceeded for this turn. Answer from collected evidence or ask a narrower question.",
        }

    raw_max_chars = arguments.get("max_chars")
    try:
        requested_max_chars = int(raw_max_chars)
    except (TypeError, ValueError):
        requested_max_chars = None
    if requested_max_chars is None:
        max_chars = max_chars_allowed
    else:
        max_chars = max(200, requested_max_chars)
        if max_chars > max_chars_allowed:
            return {
                "tool": "read_knowledge",
                "status": "constraint_error",
                "error": "max_chars_exceeded",
                "error_code": "max_chars_exceeded",
                "evidence": [],
                "requested_max_chars": max_chars,
                "max_chars_allowed": max_chars_allowed,
                "hint": (
                    f"max_chars is too high for this chat (requested {max_chars}, max {max_chars_allowed}). "
                    f"Retry with max_chars <= {max_chars_allowed}, or read fewer items."
                ),
            }

    try:
        raw_overflow_margin = int(getattr(settings, "MCP_READ_KNOWLEDGE_OVERFLOW_MARGIN", 200) or 0)
    except (TypeError, ValueError):
        raw_overflow_margin = 200
    overflow_margin = max(0, min(int(raw_overflow_margin), max(0, int(max_chars_allowed) - int(max_chars))))
    overflow_remaining = int(overflow_margin)

    # The backend chooses the correct representation and paging strategy.
    # `mode` is intentionally not part of the public agentic contract.
    mode = "auto"

    # Dedup items by (id,cursor) while preserving order.
    ordered_items: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str | None, int | None, int | None]] = set()
    for entry in raw_items:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or "").strip()
        if not item_id:
            continue
        cursor = entry.get("cursor")
        cursor_str = str(cursor).strip() if isinstance(cursor, str) and cursor.strip() else None
        row_start_raw = entry.get("row_start")
        row_limit_raw = entry.get("row_limit")
        row_start_value: int | None = None
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start_value = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start_value = None
        row_limit_value: int | None = None
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit_value = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit_value = None
        if row_limit_value is not None:
            row_limit_value = max(1, min(200, int(row_limit_value)))

        key = (item_id, cursor_str, row_start_value, row_limit_value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out_item: dict[str, object] = {"id": item_id, "cursor": cursor_str}
        if row_start_value is not None:
            out_item["row_start"] = row_start_value
        if row_limit_value is not None:
            out_item["row_limit"] = row_limit_value
        ordered_items.append(out_item)

    # ── Repeat-read detection (loop prevention) ──────────────────────────
    # Allow cursor-continuation reads (same ID + cursor = new page of content).
    # Block non-cursor re-reads of the same ref within a single turn.
    already_read_ids: set[str] = getattr(context, "read_ref_ids_this_turn", None) or set()
    filtered_items: list[dict[str, object]] = []
    blocked_items: list[dict[str, object]] = []
    for item in ordered_items:
        item_id = str(item["id"])
        has_cursor = bool(item.get("cursor"))
        has_row_start = item.get("row_start") is not None
        if item_id in already_read_ids and not has_cursor and not has_row_start:
            blocked_items.append({"id": item_id, "error": "Already read this turn. Answer from available evidence."})
        else:
            filtered_items.append(item)

    if not filtered_items:
        # ALL refs were already read this turn (non-cursor)
        return {
            "tool": "read_knowledge",
            "status": "already_read",
            "error_code": "already_read",
            "evidence": [],
            "blocked": blocked_items,
            "hint": "All requested refs were already read this turn. Answer from the evidence you have.",
        }

    if blocked_items:
        # Some refs blocked, continue with the rest
        ordered_items = filtered_items
    # ── End repeat-read detection ────────────────────────────────────────

    if not ordered_items:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_items",
            "error_code": "missing_items",
            "evidence": [],
            "hint": "items[] must contain at least one {id} from search_knowledge results.",
        }

    # NOTE: Some unit tests pass a lightweight conversation stub (SimpleNamespace)
    # instead of a real BusinessProfile instance. Avoid blowing up inside ORM
    # lookups by normalizing to a UUID early and returning a structured error
    # when missing/invalid.
    business_uuid: uuid.UUID | None = None
    raw_business_id = getattr(conversation, "business_profile_id", None)
    try:
        if raw_business_id is not None:
            business_uuid = uuid.UUID(str(raw_business_id))
    except (TypeError, ValueError, AttributeError):
        business_uuid = None
    business = getattr(conversation, "business_profile", None)

    def _upload_title(upload: KnowledgeUpload | None) -> str:
        if not upload:
            return "Untitled"
        title = (
            getattr(upload, "display_name", None)
            or getattr(upload, "filename", None)
            or getattr(upload, "source_name", None)
            or getattr(upload, "external_reference", None)
            or getattr(upload, "slug", None)
            or str(getattr(upload, "id", "") or "")
        )
        title_text = str(title or "").strip() or "Untitled"
        return title_text

    def _cursor_payload_base(*, item_id: str, kind: str) -> dict[str, object]:
        exp = int(time.time()) + _AGENTIC_READ_CURSOR_V2_TTL_SECONDS
        return {
            "v": 2,
            "exp": exp,
            "conversation_id": str(conversation.id),
            "business_id": str(conversation.business_profile_id),
            "item_id": item_id,
            "kind": kind,
        }

    def _decode_cursor(item_id: str, cursor: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        try:
            payload = _verify_agentic_read_cursor_v2(cursor)
        except ValueError as exc:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": str(exc) or "Invalid cursor."}
        if str(payload.get("conversation_id") or "") != str(conversation.id):
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different conversation."}
        if str(payload.get("business_id") or "") != str(conversation.business_profile_id):
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different business."}
        if str(payload.get("item_id") or "") != item_id:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor does not match the requested id."}
        if int(payload.get("v") or 0) != 2:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Unsupported cursor version."}
        return payload, None

    def _resolve_cursor_from_handle(cursor_token: str | None) -> str | None:
        token = str(cursor_token or "").strip()
        if not token:
            return None
        cache_value = getattr(context, "read_cursor_handles", None)
        if isinstance(cache_value, dict):
            mapped = cache_value.get(token)
            if isinstance(mapped, str) and mapped.strip():
                return mapped.strip()
        return token

    def _store_cursor_handle(cursor_signed: str | None) -> str | None:
        token = str(cursor_signed or "").strip()
        if not token:
            return None
        cache_value = getattr(context, "read_cursor_handles", None)
        reverse_cache_value = getattr(context, "read_cursor_reverse_handles", None)
        if not isinstance(cache_value, dict) or not isinstance(reverse_cache_value, dict):
            return token

        existing_handle = reverse_cache_value.get(token)
        if isinstance(existing_handle, str) and existing_handle.strip():
            cached_token = cache_value.get(existing_handle.strip())
            if isinstance(cached_token, str) and cached_token == token:
                return existing_handle.strip()

        handle = f"c_{uuid.uuid4().hex[:20]}"
        cache_value[handle] = token
        reverse_cache_value[token] = handle

        while len(cache_value) > 500:
            oldest_handle = next(iter(cache_value))
            oldest_cursor = cache_value.pop(oldest_handle, None)
            if isinstance(oldest_cursor, str):
                reverse_cache_value.pop(oldest_cursor, None)
        while len(reverse_cache_value) > 500:
            oldest_cursor_key = next(iter(reverse_cache_value))
            oldest_handle_value = reverse_cache_value.pop(oldest_cursor_key, None)
            if isinstance(oldest_handle_value, str):
                cache_value.pop(oldest_handle_value, None)

        return handle

    def _load_table_anchor_manifest(*, ref_id: str, table_id: str | None = None) -> dict[str, object] | None:
        cache_value = getattr(context, "table_row_anchor_manifests", None)
        if not isinstance(cache_value, dict):
            return None
        candidate_keys: list[str] = []
        ref_key = str(ref_id or "").strip()
        table_key = str(table_id or "").strip()
        if ref_key:
            candidate_keys.append(ref_key)
        if table_key and table_key not in candidate_keys:
            candidate_keys.append(table_key)
        for key in candidate_keys:
            raw_manifest = cache_value.get(key)
            if not isinstance(raw_manifest, Mapping):
                continue
            anchors: list[int] = []
            raw_anchors = raw_manifest.get("anchors")
            if isinstance(raw_anchors, list):
                for entry in raw_anchors:
                    if isinstance(entry, Mapping):
                        parsed = _coerce_int(entry.get("row_index"))
                    else:
                        parsed = _coerce_int(entry)
                    if parsed is None or parsed < 0:
                        continue
                    anchors.append(int(parsed))
            # Deduplicate anchors while preserving order.
            if anchors:
                seen_rows: set[int] = set()
                anchors = [row for row in anchors if (row not in seen_rows and not seen_rows.add(row))]

            matched_row_index = _coerce_int(raw_manifest.get("matched_row_index"))
            if matched_row_index is None:
                matched_row_index = -1
            if matched_row_index < 0:
                if anchors:
                    matched_row_index = int(anchors[0])
                else:
                    continue

            # Ensure anchors includes matched_row_index, and keep it first.
            if matched_row_index in anchors:
                anchors = [matched_row_index] + [row for row in anchors if row != matched_row_index]
            else:
                anchors = [matched_row_index] + anchors
            anchors = anchors[:5]

            out: dict[str, object] = {
                "ref_id": key,
                "table_id": table_key or str(raw_manifest.get("table_id") or "").strip(),
                "matched_row_index": int(matched_row_index),
            }
            if anchors:
                out["anchors"] = list(anchors)

            try:
                estimated_rows = int(raw_manifest.get("estimated_rows") or 0)
            except (TypeError, ValueError):
                estimated_rows = 0
            if estimated_rows > 0:
                out["estimated_rows"] = int(estimated_rows)

            try:
                estimated_columns = int(raw_manifest.get("estimated_columns") or 0)
            except (TypeError, ValueError):
                estimated_columns = 0
            if estimated_columns > 0:
                out["estimated_columns"] = int(estimated_columns)

            return out
        return None

    def _resolve_target(item_id: str) -> tuple[
        KnowledgeUploadChunk | None,
        KnowledgeUpload | None,
        KnowledgeUploadTable | None,
        KnowledgeUploadTableRow | None,
        dict[str, object] | None,
    ]:
        if business_uuid is None:
            return (
                None,
                None,
                None,
                None,
                {
                    "id": item_id,
                    "error_code": "invalid_business_profile",
                    "hint": "conversation.business_profile_id must be a UUID.",
                },
            )
        try:
            identifier = uuid.UUID(item_id)
        except (TypeError, ValueError):
            return None, None, None, None, {"id": item_id, "error_code": "invalid_id", "hint": "id must be a valid UUID from search_knowledge results."}

        chunk_record = (
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    id=identifier,
                    business_profile_id=business_uuid,
                    upload__status=KnowledgeStatus.ACTIVE,
                )
            )
            .select_related("upload")
            .first()
        )
        if chunk_record:
            upload = getattr(chunk_record, "upload", None)
            return chunk_record, upload, None, None, None

        upload_record = apply_customer_visible_uploads(
            KnowledgeUpload.objects.filter(
                id=identifier,
                business_profile_id=business_uuid,
                status=KnowledgeStatus.ACTIVE,
            )
        ).first()
        if upload_record:
            return None, upload_record, None, None, None

        table_record = (
            KnowledgeUploadTable.objects.filter(
                id=identifier,
                upload__business_profile_id=business_uuid,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
            .select_related("upload")
            .only(
                "id",
                "upload_id",
                "title",
                "section_heading",
                "order_index",
                "upload__id",
                "upload__display_name",
                "upload__source_name",
                "upload__external_reference",
                "upload__slug",
            )
            .first()
        )
        if table_record:
            return None, None, table_record, None, None

        row_record = (
            KnowledgeUploadTableRow.objects.filter(
                id=identifier,
                table__upload__business_profile_id=business_uuid,
                table__upload__status=KnowledgeStatus.ACTIVE,
            )
            .exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
            .select_related("table", "table__upload")
            .only(
                "id",
                "row_index",
                "table__id",
                "table__title",
                "table__section_heading",
                "table__order_index",
                "table__upload_id",
                "table__upload__id",
                "table__upload__display_name",
                "table__upload__source_name",
                "table__upload__external_reference",
                "table__upload__slug",
            )
            .first()
        )
        if row_record:
            return None, None, None, row_record, None

        return None, None, None, None, {"id": item_id, "error_code": "not_found", "hint": "Document not found for this business."}

    agent_scope = _agent_knowledge_scope(conversation, context)

    def _enforce_access(upload_id: str, *, item_id: str) -> dict[str, object] | None:
        if upload_id and not _agent_scope_allows_upload(scope=agent_scope, conversation=conversation, upload_id=upload_id):
            return {"id": item_id, "error_code": "forbidden_document", "hint": "This agent is not permitted to access that document."}
        return None

    def _is_dataset_upload(upload: KnowledgeUpload | None) -> bool:
        if not upload:
            return False
        try:
            ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
        except Exception:
            ingestion_meta = {}
        dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, Mapping) else None
        dataset_enabled = bool(isinstance(dataset_meta, Mapping) and dataset_meta.get("enabled"))
        format_hint = str(ingestion_meta.get("format") or "").strip().lower()
        native_tabular = format_hint in {"csv", "tsv", "xls", "xlsx", "jsonl"}
        if dataset_enabled or native_tabular:
            return True
        # Legacy heuristic: uploads that have tables but no pages are usually spreadsheets/datasets.
        try:
            if upload.tables.exists() and not upload.pages.exists():
                return True
        except Exception:
            pass
        return False

    def _read_page_blocks_segment(
        *,
        item_id: str,
        upload: KnowledgeUpload,
        upload_id: str,
        page_number: int,
        start_order: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
    ) -> tuple[str, dict[str, object] | None, bool]:
        try:
            from apps.knowledge.models import (
                KnowledgeUploadPage,
                KnowledgeUploadPageBlock,
            )
        except Exception:
            return "", None, True

        page_obj = (
            KnowledgeUploadPage.objects.filter(upload_id=upload_id, page_number=page_number)
            .only("id")
            .first()
        )
        if not page_obj:
            return "", None, True

        blocks_qs = (
            KnowledgeUploadPageBlock.objects.filter(page_id=page_obj.id)
            .exclude(text="")
            .order_by("order_index")
            .values_list("order_index", "text")
        )

        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        # Only prepend a separator when resuming at an element boundary.
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        started = False
        for order_index, text in blocks_qs.iterator():  # type: ignore[attr-defined]
            try:
                order_int = int(order_index)
            except (TypeError, ValueError):
                continue
            if order_int < int(start_order):
                continue
            raw_text = str(text or "")
            if not raw_text:
                continue

            chunk_text = raw_text
            offset = 0
            if not started:
                started = True
                offset = max(0, int(start_offset))
                if offset:
                    chunk_text = chunk_text[offset:]
            separator = "\n\n" if (out_parts or prepend_sep) else ""
            # If we can't fit the separator + at least one character, stop and continue on next call.
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="page_blocks"),
                    "upload_id": upload_id,
                    "page_number": int(page_number),
                    "block_order": int(order_int),
                    "char_offset": int(offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(chunk_text) <= remaining:
                out_parts.append(chunk_text)
                remaining -= len(chunk_text)
                # Continue to next block
                continue

            # Partial block
            out_parts.append(chunk_text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="page_blocks"),
                "upload_id": upload_id,
                "page_number": int(page_number),
                "block_order": int(order_int),
                "char_offset": int(offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete


    def _read_tabular_rows_segment_facts(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        start_row_index: int = 0,
        budget_chars: int,
        max_rows: int | None = None,
        business_profile,
        selection_mode: str | None = None,
    ) -> tuple[dict[str, object], dict[str, object] | None, bool]:
        """
        Facts-first table read for the LLM.

        Contract:
        - `start_row_index` is a 0-based row offset within the visible table body
          (header/separator rows excluded by ingestion metadata).
        - Returns only the table facts + paging signals needed to continue via
          `row_start` + `row_limit` (no table cursors).
        """

        budget_limit = max(0, int(budget_chars))
        complete = True

        payload: dict[str, object] = {
            "type": "table",
            "table_id": str(table_id),
            "columns": [],
            "rows": [],
            "row_offset": 0,
            "rows_shown": 0,
            "total_rows": 0,
        }
        if isinstance(selection_mode, str) and selection_mode.strip():
            payload["selection_mode"] = selection_mode.strip()

        try:
            table_uuid = uuid.UUID(str(table_id))
        except (TypeError, ValueError):
            return payload, None, True

        table = (
            KnowledgeUploadTable.objects.filter(
                id=table_uuid,
                upload_id=upload_id,
                upload__business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .only("id", "order_index", "title", "section_heading", "column_schema")
            .first()
        )
        if not table:
            return payload, None, True

        def _dedupe_column_labels(raw_columns: Sequence[str]) -> list[str]:
            deduped: list[str] = []
            seen: dict[str, int] = {}
            for index, raw_column in enumerate(raw_columns):
                label = str(raw_column or "").strip() or f"column_{index + 1}"
                key = label.lower()
                count = seen.get(key, 0) + 1
                seen[key] = count
                deduped.append(label if count == 1 else f"{label}_{count}")
            return deduped

        # Column labels: prefer column_schema if it looks like a list of strings.
        raw_schema = table.column_schema if isinstance(getattr(table, "column_schema", None), list) else []
        columns: list[str] = []
        for entry in raw_schema:
            if isinstance(entry, str) and entry.strip():
                columns.append(entry.strip())
            elif isinstance(entry, Mapping):
                for key in ("column", "name", "label", "key", "column_key"):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip():
                        columns.append(value.strip())
                        break
        columns = columns[:200]

        # Visible rows are non-header rows only (in ingestion metadata).
        non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
            metadata__row_type__in=["header", "section_header"]
        )

        # If schema is missing/empty, infer columns from the first visible row's cells.
        if not columns:
            row_obj = (
                table.rows.filter(non_header_q)
                .order_by("row_index")
                .only("id", "row_index")
                .first()
            )
            if row_obj:
                cell_qs = (
                    KnowledgeUploadTableCell.objects.filter(row_id=row_obj.id)
                    .order_by("column_index")
                    .values_list("column_index", "column_key")
                )
                inferred: list[tuple[int, str]] = []
                for col_idx, col_key in cell_qs:
                    try:
                        idx = int(col_idx)
                    except (TypeError, ValueError):
                        continue
                    label = str(col_key or "").strip() or f"column_{idx + 1}"
                    inferred.append((idx, label))
                inferred.sort(key=lambda item: item[0])
                columns = [label for _idx, label in inferred][:200]

        columns = _dedupe_column_labels(columns[:200])
        payload["columns"] = columns

        try:
            start_row = max(0, int(start_row_index))
        except (TypeError, ValueError):
            start_row = 0
        payload["row_offset"] = start_row

        try:
            total_rows = int(table.rows.filter(non_header_q).count())
        except Exception:
            total_rows = 0
        payload["total_rows"] = total_rows

        # Bound reads: even for "list everything", never scan unbounded rows in one call.
        try:
            scan_cap = int(max(50, min(500, int(budget_chars) // 30 or 50)))
        except Exception:
            scan_cap = 200
        limit_rows = scan_cap
        if isinstance(max_rows, int) and max_rows > 0:
            limit_rows = min(limit_rows, int(max_rows))
        limit_rows = max(1, int(limit_rows))

        row_qs = (
            table.rows.filter(non_header_q)
            .order_by("row_index")
            .only("id", "row_index", "metadata")
        )
        row_qs = row_qs[start_row : start_row + limit_rows]
        row_qs = row_qs.prefetch_related(
            Prefetch(
                "cells",
                queryset=KnowledgeUploadTableCell.objects.order_by("column_index").only(
                    "row_id",
                    "column_index",
                    "column_key",
                    "raw_text",
                ),
            )
        )

        def _normalize_cell_text(value: str) -> str:
            normalized = str(value or "").strip().lower()
            normalized = re.sub(r"\\s+", " ", normalized)
            return normalized

        def _is_uniform_section_separator(values: Sequence[str]) -> tuple[bool, str]:
            non_empty = [str(cell or "").strip() for cell in values if str(cell or "").strip()]
            if len(non_empty) < 4:
                return False, ""
            normalized = [_normalize_cell_text(cell) for cell in non_empty]
            first = normalized[0] if normalized else ""
            if not first:
                return False, ""
            if any(cell != first for cell in normalized[1:]):
                return False, ""
            return True, non_empty[0]

        scope_overlay_reasons = {
            "scope_explicit_span",
            "scope_repeated_value_span",
            "scope_sparse_expansion",
            "scope_edge_completion",
        }
        column_index_lookup: dict[str, int] = {}
        for idx, label in enumerate(columns):
            normalized = _normalize_column_name(label)
            if normalized and normalized not in column_index_lookup:
                column_index_lookup[normalized] = idx
            lowered = str(label or "").strip().lower()
            if lowered and lowered not in column_index_lookup:
                column_index_lookup[lowered] = idx

        index_offset: int | None = None
        rows_out: list[list[str]] = []

        def _payload_size(candidate_rows: Sequence[Sequence[str]]) -> int:
            candidate: dict[str, object] = {
                "type": "table",
                "table_id": str(table_id),
                "columns": list(columns),
                "rows": [list(row) for row in candidate_rows],
                "row_offset": int(start_row),
                "rows_shown": int(len(candidate_rows)),
                "total_rows": int(total_rows),
            }
            if isinstance(selection_mode, str) and selection_mode.strip():
                candidate["selection_mode"] = selection_mode.strip()
            try:
                return len(json.dumps(candidate, ensure_ascii=False, default=str))
            except Exception:
                return 0

        for row in row_qs:
            cell_lookup: dict[int, str] = {}
            for cell in row.cells.all():  # type: ignore[attr-defined]
                try:
                    idx = int(getattr(cell, "column_index", None) or 0)
                except (TypeError, ValueError):
                    continue
                text = str(getattr(cell, "raw_text", "") or "")
                cell_lookup[idx] = text

            if index_offset is None:
                keys = list(cell_lookup.keys())
                if 0 in cell_lookup:
                    index_offset = 0
                elif 1 in cell_lookup:
                    index_offset = 1
                elif keys:
                    index_offset = min(keys)
                else:
                    index_offset = 0

            values: list[str] = []
            for col_idx in range(len(columns)):
                values.append(str(cell_lookup.get(int(col_idx) + int(index_offset or 0), "")))

            # Collapse broad-span note rows (same text duplicated across many columns) to:
            #   [note_text, "", "", ...]
            is_uniform, uniform_label = _is_uniform_section_separator(values)
            if is_uniform and uniform_label:
                values = [uniform_label] + [""] * max(0, len(columns) - 1)

            row_meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
            inferred_scope_columns: list[str] = []
            raw_applies_to = row_meta.get("inferred_scope_columns")
            if isinstance(raw_applies_to, (list, tuple)):
                for scope_entry in raw_applies_to:
                    label = str(scope_entry or "").strip()
                    if label:
                        inferred_scope_columns.append(label)
            scope_reason = str(row_meta.get("scope_reason") or "").strip()
            scope_reason_key = scope_reason.lower()
            fee_value = str(row_meta.get("scope_value") or "").strip()

            effective_values = list(values)
            if (
                fee_value
                and inferred_scope_columns
                and (
                    scope_reason_key.startswith("inferred_")
                    or scope_reason_key in scope_overlay_reasons
                )
            ):
                for scope_label in inferred_scope_columns:
                    normalized_scope = _normalize_column_name(scope_label)
                    if not normalized_scope:
                        continue
                    col_pos = column_index_lookup.get(normalized_scope)
                    if col_pos is None:
                        col_pos = column_index_lookup.get(str(scope_label).strip().lower())
                    if col_pos is None or col_pos < 0 or col_pos >= len(effective_values):
                        continue
                    if str(effective_values[col_pos] or "").strip():
                        continue
                    effective_values[col_pos] = fee_value

            candidate_rows = [*rows_out, effective_values]
            if _payload_size(candidate_rows) > budget_limit:
                complete = False
                break
            rows_out = candidate_rows

        payload["rows"] = rows_out
        payload["rows_shown"] = len(rows_out)
        if (start_row + len(rows_out)) < int(total_rows or 0):
            payload["next_row_start"] = int(start_row + len(rows_out))

        return payload, None, complete

    def _table_payload_is_informative(table_payload: Mapping[str, object]) -> bool:
        rows_value = table_payload.get("rows")
        if not isinstance(rows_value, list) or not rows_value:
            return False
        for row in rows_value:
            if isinstance(row, (list, tuple)):
                if any(str(cell or "").strip() for cell in row):
                    return True
            elif str(row or "").strip():
                return True
        return False

    def _table_payload_size(table_payload: Mapping[str, object]) -> int:
        try:
            return len(json.dumps(dict(table_payload), ensure_ascii=False, default=str))
        except Exception:
            return 0

    def _merge_table_anchor_payloads(
        *,
        base_payload: Mapping[str, object],
        incoming_payload: Mapping[str, object],
        budget_chars: int,
        selection_mode: str,
    ) -> tuple[dict[str, object], int, bool]:
        merged_columns = list(base_payload.get("columns") or incoming_payload.get("columns") or [])
        if not merged_columns:
            merged_columns = list(incoming_payload.get("columns") or [])

        base_rows_raw = base_payload.get("rows")
        incoming_rows_raw = incoming_payload.get("rows")
        base_rows = [list(row) for row in base_rows_raw] if isinstance(base_rows_raw, list) else []
        incoming_rows = [list(row) for row in incoming_rows_raw] if isinstance(incoming_rows_raw, list) else []

        merged: dict[str, object] = {
            "type": "table",
            "table_id": str(base_payload.get("table_id") or incoming_payload.get("table_id") or ""),
            "columns": merged_columns,
            "rows": [],
            "row_offset": min(
                max(0, _coerce_int(base_payload.get("row_offset")) or 0),
                max(0, _coerce_int(incoming_payload.get("row_offset")) or 0),
            ),
            "rows_shown": 0,
            "total_rows": max(
                max(0, _coerce_int(base_payload.get("total_rows")) or 0),
                max(0, _coerce_int(incoming_payload.get("total_rows")) or 0),
            ),
            "selection_mode": selection_mode,
        }

        merged_rows: list[list[str]] = []
        seen_rows: set[tuple[str, ...]] = set()
        for row in [*base_rows, *incoming_rows]:
            normalized_row = tuple(str(cell or "") for cell in row)
            if normalized_row in seen_rows:
                continue
            candidate_rows = [*merged_rows, list(row)]
            candidate_payload = dict(merged)
            candidate_payload["rows"] = candidate_rows
            candidate_payload["rows_shown"] = len(candidate_rows)
            if _table_payload_size(candidate_payload) > max(0, int(budget_chars)):
                merged["rows"] = merged_rows
                merged["rows_shown"] = len(merged_rows)
                if (
                    int(merged["row_offset"]) + len(merged_rows)
                ) < int(merged.get("total_rows") or 0):
                    merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
                return merged, max(0, len(merged_rows) - len(base_rows)), True
            merged_rows = candidate_rows
            seen_rows.add(normalized_row)

        merged["rows"] = merged_rows
        merged["rows_shown"] = len(merged_rows)
        if (
            int(merged["row_offset"]) + len(merged_rows)
        ) < int(merged.get("total_rows") or 0):
            merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
        return merged, max(0, len(merged_rows) - len(base_rows)), False

    def _table_db_row_index_to_visible_row_start(
        *,
        table_id: str,
        upload_id: str,
        business_profile,
        db_row_index: int,
    ) -> int:
        """
        Convert a stored table row index into a visible `row_start` offset.

        The visible table body excludes header / section-header rows, so row refs
        and anchor reads must normalize DB row indexes before paging facts.
        """
        try:
            raw_idx = int(db_row_index)
        except (TypeError, ValueError):
            return 0
        if raw_idx < 0:
            return 0
        try:
            table_uuid = uuid.UUID(str(table_id))
        except (TypeError, ValueError):
            return max(0, raw_idx)

        non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
            metadata__row_type__in=["header", "section_header"]
        )

        base_qs = KnowledgeUploadTableRow.objects.filter(
            table_id=table_uuid,
            table__upload_id=upload_id,
            table__upload__business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        )

        target = (
            base_qs.filter(non_header_q, row_index__gte=raw_idx)
            .order_by("row_index")
            .only("row_index")
            .first()
        )
        if target is None:
            target = (
                base_qs.filter(non_header_q, row_index__lte=raw_idx)
                .order_by("-row_index")
                .only("row_index")
                .first()
            )
        if target is None:
            return 0

        try:
            target_db_idx = int(getattr(target, "row_index", 0) or 0)
        except (TypeError, ValueError):
            target_db_idx = raw_idx

        try:
            visible_before = int(
                base_qs.filter(non_header_q, row_index__lt=int(target_db_idx)).count()
            )
        except Exception:
            visible_before = max(0, raw_idx)
        return max(0, int(visible_before))

    def _read_exact_table_row(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        db_row_index: int,
        budget_chars: int,
        business_profile,
    ) -> tuple[dict[str, object], bool]:
        visible_row_start = _table_db_row_index_to_visible_row_start(
            table_id=table_id,
            upload_id=upload_id,
            business_profile=business_profile,
            db_row_index=db_row_index,
        )
        table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=visible_row_start,
            budget_chars=budget_chars,
            max_rows=1,
            business_profile=business_profile,
            selection_mode="row_ref",
        )
        return table_payload, complete

    def _read_tabular_rows_with_anchor(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        start_row_index: int,
        budget_chars: int,
        max_rows: int | None,
        business_profile,
        use_anchor: bool,
    ) -> tuple[dict[str, object], dict[str, object] | None, bool, bool, bool]:
        anchor_used = False
        fallback_used = False
        anchor_start_row = int(start_row_index)

        if use_anchor and anchor_start_row <= 0:
            manifest = _load_table_anchor_manifest(ref_id=item_id, table_id=table_id)
            if isinstance(manifest, Mapping):
                anchor_rows: list[int] = []
                primary_anchor = _coerce_int(manifest.get("matched_row_index"))
                if primary_anchor is not None and primary_anchor >= 0:
                    anchor_rows.append(int(primary_anchor))
                raw_anchors = manifest.get("anchors")
                if isinstance(raw_anchors, list):
                    for entry in raw_anchors:
                        parsed = _coerce_int(entry)
                        if parsed is None or parsed < 0:
                            continue
                        anchor_rows.append(int(parsed))
                if anchor_rows:
                    # Deduplicate while preserving order, and cap attempts.
                    seen_rows: set[int] = set()
                    anchor_rows = [row for row in anchor_rows if (row not in seen_rows and not seen_rows.add(row))]
                    anchor_used = True
                    merged_payload: dict[str, object] | None = None
                    merged_complete = True
                    merged_anchor_hits = 0
                    for row_idx in anchor_rows[:2]:
                        visible_row_start = _table_db_row_index_to_visible_row_start(
                            table_id=table_id,
                            upload_id=upload_id,
                            business_profile=business_profile,
                            db_row_index=int(row_idx),
                        )
                        anchor_start_row = max(0, int(visible_row_start) - 1)
                        table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
                            item_id=item_id,
                            upload_id=upload_id,
                            table_id=table_id,
                            start_row_index=anchor_start_row,
                            budget_chars=budget_chars,
                            max_rows=max_rows,
                            business_profile=business_profile,
                            selection_mode="anchor_match",
                        )
                        if not _table_payload_is_informative(table_payload):
                            merged_complete = False
                            continue
                        if merged_payload is None:
                            merged_payload = dict(table_payload)
                            merged_complete = bool(complete)
                            merged_anchor_hits = 1
                            continue
                        merged_payload, added_rows, merge_budget_exhausted = _merge_table_anchor_payloads(
                            base_payload=merged_payload,
                            incoming_payload=table_payload,
                            budget_chars=budget_chars,
                            selection_mode="anchor_merge",
                        )
                        if added_rows > 0:
                            merged_anchor_hits += 1
                        merged_complete = bool(merged_complete and complete and not merge_budget_exhausted)
                    if merged_payload is not None and _table_payload_is_informative(merged_payload):
                        if merged_anchor_hits <= 1:
                            merged_payload["selection_mode"] = "anchor_match"
                        return merged_payload, None, merged_complete, anchor_used, fallback_used
                    # Anchors were tried but didn't yield useful rows; fall back.
                    fallback_used = True

        table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=max(0, int(start_row_index)),
            budget_chars=budget_chars,
            max_rows=max_rows,
            business_profile=business_profile,
            selection_mode="row_range",
        )

        return table_payload, cursor_out, complete, anchor_used, fallback_used

    def _read_chunk_window_segment(
        *,
        item_id: str,
        upload_id: str,
        start_index: int,
        end_index: int,
        current_index: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
        business_profile,
    ) -> tuple[str, dict[str, object] | None, bool]:
        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        # Only prepend a separator when resuming at an element boundary.
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        window_qs = (
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    upload_id=upload_id,
                    business_profile=business_profile,
                    upload__status=KnowledgeStatus.ACTIVE,
                    chunk_index__gte=int(start_index),
                    chunk_index__lte=int(end_index),
                )
            )
            .exclude(content="")
            .order_by("chunk_index")
            .values_list("chunk_index", "content")
        )

        cur_idx = int(current_index)
        offset = max(0, int(start_offset))
        started = False
        for chunk_index, content in window_qs.iterator():  # type: ignore[attr-defined]
            try:
                ci = int(chunk_index)
            except (TypeError, ValueError):
                continue
            if ci < cur_idx:
                continue
            raw = str(content or "")
            if not raw:
                continue
            text = raw
            local_offset = 0
            if not started:
                started = True
                local_offset = offset
                if local_offset:
                    text = text[local_offset:]

            separator = "\n\n" if (out_parts or prepend_sep) else ""
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="chunk_window"),
                    "upload_id": upload_id,
                    "chunk_start": int(start_index),
                    "chunk_end": int(end_index),
                    "chunk_index": int(ci),
                    "char_offset": int(local_offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(text) <= remaining:
                out_parts.append(text)
                remaining -= len(text)
                continue

            out_parts.append(text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="chunk_window"),
                "upload_id": upload_id,
                "chunk_start": int(start_index),
                "chunk_end": int(end_index),
                "chunk_index": int(ci),
                "char_offset": int(local_offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete

    def _read_artifact_segment(
        *,
        item_id: str,
        artifact_id: str,
        start_offset: int,
        budget_chars: int,
    ) -> tuple[str, dict[str, object] | None, bool, dict[str, object] | None]:
        """
        Read from a stored tool-output artifact (Phase 4 fallback).

        Artifact payload shape (response JSON):
          { "text": "...", "title": "...", "type": "text|table", "cursor_after": "opaque_or_null" }
        """

        try:
            artifact_uuid = uuid.UUID(str(artifact_id))
        except (TypeError, ValueError):
            return "", None, True, {"id": item_id, "error_code": "invalid_cursor", "hint": "Invalid artifact id."}

        artifact = (
            McpToolOutputArtifact.objects.filter(
                id=artifact_uuid,
                conversation=conversation,
                invoked_tool="read_knowledge",
            )
            .only("id", "response")
            .first()
        )
        if not artifact:
            return "", None, True, {"id": item_id, "error_code": "artifact_not_found", "hint": "Artifact not found for this conversation."}

        payload = artifact.response if isinstance(getattr(artifact, "response", None), Mapping) else {}
        full_text = payload.get("text")
        if not isinstance(full_text, str):
            full_text = str(full_text or "")
        if not full_text:
            return "", None, True, {"id": item_id, "error_code": "artifact_empty", "hint": "Artifact has no readable text."}

        title_override = payload.get("title")
        type_override = payload.get("type")

        start = max(0, int(start_offset or 0))
        remaining = max(0, int(budget_chars))
        out = full_text[start : start + remaining]
        end = start + len(out)

        cursor_after = payload.get("cursor_after")
        if end < len(full_text):
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="artifact"),
                "artifact_id": str(artifact.id),
                "char_offset": int(end),
            }
            cursor_str = _sign_agentic_read_cursor_v2(cursor_next)
            return out, {"cursor": cursor_str}, False, {"title": title_override, "type": type_override}

        if isinstance(cursor_after, str) and cursor_after.strip():
            # Once the artifact stream is consumed, resume the original knowledge cursor (if any).
            return out, {"cursor": cursor_after.strip()}, False, {"title": title_override, "type": type_override}

        return out, None, True, {"title": title_override, "type": type_override}

    contents: list[dict[str, object]] = []
    read: list[dict[str, object]] = []
    deferred: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []

    successfully_read_ids: set[str] = set()
    table_anchor_manifest_lookups = 0
    table_anchor_manifest_hits = 0
    table_anchor_fallback_reads = 0

    remaining_chars = max(0, int(max_chars))
    total_chars = 0

    for idx, entry in enumerate(ordered_items):
        item_id = str(entry.get("id") or "").strip()
        cursor_in = entry.get("cursor")
        row_start: int | None = None
        row_limit: int | None = None
        row_start_raw = entry.get("row_start")
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start = None
        row_limit_raw = entry.get("row_limit")
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit = None
        if row_limit is not None:
            row_limit = max(1, min(200, int(row_limit)))
        cursor_in_resolved = _resolve_cursor_from_handle(cursor_in if isinstance(cursor_in, str) else None)
        effective_remaining_chars = max(0, int(remaining_chars) + int(overflow_remaining))
        if effective_remaining_chars < 200:
            deferred.append(
                {
                    "id": item_id,
                    "reason": "not_enough_remaining_chars",
                    "hint": "Not enough remaining chars in this call; retry with a higher max_chars or fewer items.",
                }
            )
            continue

        # Greedy packing by priority: let higher-ranked refs consume the remaining
        # budget instead of forcing an equal-share slice that can starve the first
        # ref and create avoidable follow-up reads.
        per_item_budget = max(200, effective_remaining_chars)

        cursor_payload: dict[str, object] | None = None
        if isinstance(cursor_in_resolved, str) and cursor_in_resolved.strip():
            cursor_payload, cursor_error = _decode_cursor(item_id, cursor_in_resolved)
            if cursor_error:
                errors.append(cursor_error)
                read.append({"id": item_id, "status": "error"})
                continue

        chunk_record, upload_record, table_record, row_record, resolve_error = _resolve_target(item_id)
        if resolve_error:
            errors.append(resolve_error)
            read.append({"id": item_id, "status": "error"})
            continue

        upload = (
            upload_record
            or getattr(chunk_record, "upload", None)
            or getattr(table_record, "upload", None)
            or getattr(getattr(row_record, "table", None), "upload", None)
        )
        upload_id = str(getattr(upload, "id", "") or "")
        if not upload_id:
            errors.append({"id": item_id, "error_code": "not_found", "hint": "Upload not found."})
            read.append({"id": item_id, "status": "error"})
            continue

        access_error = _enforce_access(upload_id, item_id=item_id)
        if access_error:
            errors.append(access_error)
            read.append({"id": item_id, "status": "error"})
            continue

        # Block dataset/spreadsheet reads through read_knowledge (not supported in agentic KB tools).
        if _is_dataset_upload(upload) and (chunk_record is None or bool((chunk_record.metadata or {}).get("is_table_chunk"))):
            errors.append(
                {
                    "id": item_id,
                    "error_code": "wrong_tool_for_table",
                    "hint": "This item is a structured dataset/spreadsheet table and is not supported by this tool.",
                }
            )
            read.append({"id": item_id, "status": "error"})
            continue

        # Track for follow-up context.
        try:
            context.track_document_read(upload_id, title=_upload_title(upload))
        except Exception:
            pass

        payload: dict[str, object] = {"type": "text", "text": ""}
        payload_type = "text"
        evidence_kind = "text_excerpt"
        title = _upload_title(upload)
        cursor_used = (
            cursor_in_resolved
            if isinstance(cursor_in_resolved, str) and cursor_in_resolved.strip()
            else None
        )
        next_cursor: str | None = None
        complete = True
        evidence_coverage_hint: dict[str, object] | None = None

        # Strategy selection (AUTO):
        if cursor_payload:
            kind = str(cursor_payload.get("kind") or "")
            prepend_sep = bool(cursor_payload.get("prepend_sep"))
            if kind == "page_blocks":
                page_number = int(cursor_payload.get("page_number") or 1)
                block_order = int(cursor_payload.get("block_order") or 0)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_page_blocks_segment(
                    item_id=item_id,
                    upload=upload,  # type: ignore[arg-type]
                    upload_id=upload_id,
                    page_number=page_number,
                    start_order=block_order,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "table_rows":
                payload_type = "table"
                evidence_kind = "table_rows"
                table_id = str(cursor_payload.get("table_id") or "").strip()
                raw_row_index = cursor_payload.get("row_index")
                try:
                    start_row_index = int(raw_row_index) if raw_row_index is not None else 0
                except (TypeError, ValueError):
                    start_row_index = 0
                effective_start_row = row_start if row_start is not None else start_row_index
                table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
                    item_id=item_id,
                    upload_id=upload_id,
                    table_id=table_id,
                    start_row_index=effective_start_row,
                    budget_chars=per_item_budget,
                    max_rows=row_limit,
                    business_profile=business,
                    selection_mode="row_range",
                )
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif kind == "chunk_window":
                start_index = int(cursor_payload.get("chunk_start") or 0)
                end_index = int(cursor_payload.get("chunk_end") or start_index)
                chunk_index = int(cursor_payload.get("chunk_index") or start_index)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_chunk_window_segment(
                    item_id=item_id,
                    upload_id=upload_id,
                    start_index=start_index,
                    end_index=end_index,
                    current_index=chunk_index,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                    business_profile=business,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "artifact":
                artifact_id = str(cursor_payload.get("artifact_id") or "").strip()
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete, artifact_meta = _read_artifact_segment(
                    item_id=item_id,
                    artifact_id=artifact_id,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                )
                if artifact_meta and artifact_meta.get("error_code"):
                    errors.append(dict(artifact_meta))
                    read.append({"id": item_id, "status": "error"})
                    continue
                if artifact_meta:
                    title_override = artifact_meta.get("title")
                    if isinstance(title_override, str) and title_override.strip():
                        title = title_override.strip()
                    type_override = artifact_meta.get("type")
                    if isinstance(type_override, str) and type_override.strip():
                        payload_type = type_override.strip()
                        evidence_kind = "table_rows" if payload_type == "table" else "text_excerpt"
                if payload_type == "table":
                    # Artifact segments currently stream text; tables should not route here.
                    payload = {"type": "table", "columns": [], "rows": []}
                else:
                    payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            else:
                errors.append({"id": item_id, "error_code": "invalid_cursor", "hint": "Unknown cursor kind."})
                read.append({"id": item_id, "status": "error"})
                continue
        else:
            # New read: decide best source.
            chunk_meta = chunk_record.metadata if chunk_record and isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
            is_table_chunk = bool(chunk_meta.get("is_table_chunk") or chunk_meta.get("table_chunk_role"))
            table_role = str(chunk_meta.get("table_chunk_role") or "").strip().lower()
            table_id = str(chunk_meta.get("table_id") or "").strip()

            if row_record is not None:
                payload_type = "table"
                evidence_kind = "table_rows"
                table_obj = getattr(row_record, "table", None)
                table_id = str(getattr(table_obj, "id", "") or "").strip()
                row_index_raw = getattr(row_record, "row_index", 0)
                try:
                    row_index = int(row_index_raw) if row_index_raw is not None else 0
                except (TypeError, ValueError):
                    row_index = 0

                table_title = str(getattr(table_obj, "title", "") or "").strip() or str(
                    getattr(table_obj, "section_heading", "") or ""
                ).strip()
                if not table_title:
                    order_index = getattr(table_obj, "order_index", None)
                    table_title = f"Table {order_index}" if order_index else "Table"
                title = table_title
                explicit_range = row_start is not None or row_limit is not None
                if explicit_range:
                    errors.append(
                        {
                            "id": item_id,
                            "error_code": "row_ref_range_not_supported",
                            "hint": (
                                f"This id is a single table row. Use table id {table_id} "
                                "with row_start/row_limit to browse table rows."
                            ),
                        }
                    )
                    read.append({"id": item_id, "status": "error"})
                    continue

                table_payload, complete = _read_exact_table_row(
                    item_id=item_id,
                    upload_id=upload_id,
                    table_id=table_id,
                    db_row_index=row_index,
                    budget_chars=per_item_budget,
                    business_profile=business,
                )
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif table_record is not None:
                payload_type = "table"
                evidence_kind = "table_rows"
                table_id = str(getattr(table_record, "id", "") or "").strip()
                table_title = str(getattr(table_record, "title", "") or "").strip() or str(getattr(table_record, "section_heading", "") or "").strip()
                if not table_title:
                    order_index = getattr(table_record, "order_index", None)
                    table_title = f"Table {order_index}" if order_index else "Table"
                title = table_title

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                else:
                    table_anchor_manifest_lookups += 1
                    table_payload, _cursor_out, complete, anchor_used, fallback_used = _read_tabular_rows_with_anchor(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=0,
                        budget_chars=per_item_budget,
                        max_rows=None,
                        business_profile=business,
                        use_anchor=True,
                    )
                    if anchor_used:
                        table_anchor_manifest_hits += 1
                    if fallback_used:
                        table_anchor_fallback_reads += 1
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif upload_record is not None and chunk_record is None:
                page_number = 1
                has_page_blocks = False
                try:
                    from apps.knowledge.models import KnowledgeUploadPageBlock
                    has_page_blocks = KnowledgeUploadPageBlock.objects.filter(
                        upload_id=upload_id,
                        page__page_number=page_number,
                    ).exclude(text="").exists()
                except Exception:
                    has_page_blocks = False

                if has_page_blocks:
                    content_text, cursor_out, complete = _read_page_blocks_segment(
                        item_id=item_id,
                        upload=upload,  # type: ignore[arg-type]
                        upload_id=upload_id,
                        page_number=page_number,
                        start_order=0,
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                else:
                    errors.append(
                        {
                            "id": item_id,
                            "error_code": "not_found",
                            "hint": "No readable content found for that id.",
                        }
                    )
                    read.append({"id": item_id, "status": "error"})
                    continue
            elif mode != "excerpt" and is_table_chunk and table_id and (mode == "table_rows" or table_role not in {"row"}):
                payload_type = "table"
                evidence_kind = "table_rows"
                # Resolve table title for friendlier output.
                try:
                    table_obj = (
                        KnowledgeUploadTable.objects.filter(id=table_id)
                        .only("id", "title", "section_heading", "order_index")
                        .first()
                    )
                    if table_obj:
                        table_title = (table_obj.title or table_obj.section_heading or "").strip()
                        if not table_title:
                            table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                        title = table_title
                except Exception:
                    pass

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                else:
                    table_anchor_manifest_lookups += 1
                    table_payload, _cursor_out, complete, anchor_used, fallback_used = _read_tabular_rows_with_anchor(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=0,
                        budget_chars=per_item_budget,
                        max_rows=None,
                        business_profile=business,
                        use_anchor=True,
                    )
                    if anchor_used:
                        table_anchor_manifest_hits += 1
                    if fallback_used:
                        table_anchor_fallback_reads += 1
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif mode != "excerpt" and is_table_chunk and table_id and table_role == "row":
                payload_type = "table"
                evidence_kind = "table_rows"
                # Resolve table title for friendlier output (row-chunk reads should still
                # look like "Table X", not just the upload title).
                try:
                    table_obj = (
                        KnowledgeUploadTable.objects.filter(id=table_id)
                        .only("id", "title", "section_heading", "order_index")
                        .first()
                    )
                    if table_obj:
                        table_title = (table_obj.title or table_obj.section_heading or "").strip()
                        if not table_title:
                            table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                        title = table_title
                except Exception:
                    pass
                if row_start is not None or row_limit is not None:
                    errors.append(
                        {
                            "id": item_id,
                            "error_code": "row_ref_range_not_supported",
                            "hint": (
                                f"This id is a single table row. Use table id {table_id} "
                                "with row_start/row_limit to browse table rows."
                            ),
                        }
                    )
                    read.append({"id": item_id, "status": "error"})
                    continue

                row_index_raw = chunk_meta.get("table_row_index")
                try:
                    row_index_value = int(row_index_raw) if row_index_raw is not None else 0
                except (TypeError, ValueError):
                    row_index_value = 0

                table_payload, complete = _read_exact_table_row(
                    item_id=item_id,
                    upload_id=upload_id,
                    table_id=table_id,
                    db_row_index=row_index_value,
                    budget_chars=per_item_budget,
                    business_profile=business,
                )
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            else:
                # Prefer page blocks when a page can be resolved; fall back to a chunk window.
                page_number = 1
                if chunk_record:
                    meta_page = chunk_meta.get("table_page_number") or chunk_meta.get("chunk_page") or chunk_meta.get("page_number")
                    if meta_page:
                        try:
                            parsed_page = int(meta_page)
                            if parsed_page >= 1:
                                page_number = parsed_page
                        except (TypeError, ValueError):
                            pass

                # If page blocks exist for the resolved page, use them.
                has_page_blocks = False
                try:
                    from apps.knowledge.models import KnowledgeUploadPageBlock
                    has_page_blocks = KnowledgeUploadPageBlock.objects.filter(
                        upload_id=upload_id,
                        page__page_number=page_number,
                    ).exclude(text="").exists()
                except Exception:
                    has_page_blocks = False

                if has_page_blocks:
                    content_text, cursor_out, complete = _read_page_blocks_segment(
                        item_id=item_id,
                        upload=upload,  # type: ignore[arg-type]
                        upload_id=upload_id,
                        page_number=page_number,
                        start_order=0,
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                elif chunk_record and chunk_record.chunk_index is not None:
                    neighbor = 1
                    start_index = max(0, int(chunk_record.chunk_index) - neighbor)
                    end_index = int(chunk_record.chunk_index) + neighbor
                    content_text, cursor_out, complete = _read_chunk_window_segment(
                        item_id=item_id,
                        upload_id=upload_id,
                        start_index=start_index,
                        end_index=end_index,
                        current_index=start_index,
                        start_offset=0,
                        budget_chars=per_item_budget,
                        business_profile=business,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                else:
                    errors.append({"id": item_id, "error_code": "not_found", "hint": "No readable content found for that id."})
                    read.append({"id": item_id, "status": "error"})
                    continue

        # If we couldn't read anything for this item, mark as deferred rather than returning empty content.
        has_payload = False
        if payload_type == "table":
            rows_value = payload.get("rows")
            has_payload = isinstance(rows_value, list) and bool(rows_value)
        else:
            text_value = payload.get("text")
            has_payload = isinstance(text_value, str) and bool(text_value)
        if not has_payload:
            if not complete:
                # Budget was too small to fit even one row/block — data exists but didn't fit.
                deferred.append(
                    {
                        "id": item_id,
                        "reason": "budget_too_small",
                        "hint": "Budget too small to fit content. Retry with a higher max_chars (at least the suggested_max_chars from search).",
                    }
                )
            else:
                deferred.append(
                    {
                        "id": item_id,
                        "reason": "empty",
                        "hint": "No readable content was available for this item.",
                    }
                )
            read.append({"id": item_id, "status": "deferred"})
            continue

        successfully_read_ids.add(item_id)

        try:
            if payload_type == "table":
                item_chars = len(json.dumps(payload, ensure_ascii=False, default=str))
            else:
                item_chars = len(str(payload.get("text") or ""))
        except Exception:
            item_chars = 0

        overflow_used = max(0, item_chars - max(0, int(remaining_chars)))
        if overflow_used:
            overflow_remaining = max(0, int(overflow_remaining) - int(overflow_used))
        remaining_chars = max(0, remaining_chars - item_chars)
        total_chars += item_chars

        table_more_rows_available = bool(
            payload_type == "table"
            and isinstance(payload.get("next_row_start"), (int, float))
            and not bool(next_cursor)
        )

        evidence_entry: dict[str, object] = {
            "id": item_id,
            "document_id": upload_id,
            "title": title,
            "type": payload_type,
            "kind": evidence_kind,
            "payload": payload,
            "chars": item_chars,
            "complete": bool(complete and not next_cursor),
            # Back-compat: orchestrator coverage ledger expects "truncated" on items.
            # For paged table reads, "more rows available" is not the same as a risky
            # truncation; keep completion false but avoid overstating truncation.
            "truncated": bool((not complete or bool(next_cursor)) and not table_more_rows_available),
        }
        if table_more_rows_available:
            evidence_entry["more_rows_available"] = True
        if isinstance(evidence_coverage_hint, Mapping) and evidence_coverage_hint:
            evidence_entry["coverage_hint"] = dict(evidence_coverage_hint)
        if cursor_used:
            evidence_entry["cursor_used"] = cursor_used
        next_cursor_handle = _store_cursor_handle(next_cursor) if next_cursor else None
        if next_cursor_handle:
            evidence_entry["next_cursor"] = next_cursor_handle
        contents.append(evidence_entry)
        context.add_read_evidence(evidence_entry)

        is_truncated = not complete or bool(next_cursor_handle)
        read_entry: dict[str, object] = {
            "id": item_id,
            "status": "full" if not is_truncated else ("more_available" if table_more_rows_available else "truncated"),
            "chars": item_chars,
        }
        if is_truncated:
            # Build informative hint so the LLM can decide whether to
            # follow up.  Include row labels already fetched so it can
            # judge if the answer is already complete.
            hint_parts: list[str] = []
            if payload_type == "table":
                next_row_start = payload.get("next_row_start")
                if isinstance(next_row_start, (int, float)):
                    if table_more_rows_available:
                        hint_parts.append(f"More rows available with row_start={int(next_row_start)} if needed.")
                    else:
                        hint_parts.append(f"Continue with row_start={int(next_row_start)}.")
                hint_parts.append(f"Max chars allowed: {int(max_chars_allowed)}.")
            else:
                hint_parts.append(
                    f"Only use next_cursor if you still need data not yet returned. "
                    f"Max chars allowed: {int(max_chars_allowed)}."
                )
            read_entry["hint"] = " ".join(hint_parts)
        read.append(read_entry)
        continue

    # ---------------------------------------------------------------------
    # Phase 4: Artifact fallback when the *final* tool payload would exceed the
    # prompt tool-output cap. This prevents orchestrator-side truncation and
    # enables deterministic paging via `kind=artifact` cursors.
    # ---------------------------------------------------------------------

    try:
        artifact_retention_days = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_RETENTION_DAYS", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS", 30)
            or 30
        )
    except (TypeError, ValueError):
        artifact_retention_days = 30
    artifact_retention_days = max(1, min(365, artifact_retention_days))
    try:
        artifact_max_per_conversation = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_MAX_PER_CONVERSATION", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION", 200)
            or 200
        )
    except (TypeError, ValueError):
        artifact_max_per_conversation = 200
    artifact_max_per_conversation = max(0, min(5000, artifact_max_per_conversation))

    output_limit = max(0, int(prompt_output_limit))

    PROMPT_VIEW_INLINE_MIN_CHARS = 200
    PROMPT_VIEW_INLINE_MAX_CHARS = 1600
    if output_limit:
        # Reserve room for JSON framing + cursors + budget metadata when the prompt cap is small.
        PROMPT_VIEW_INLINE_MAX_CHARS = min(PROMPT_VIEW_INLINE_MAX_CHARS, max(PROMPT_VIEW_INLINE_MIN_CHARS, output_limit // 2))

    def _response_status() -> str:
        if errors or deferred or any(str(entry.get("status") or "") in {"truncated", "partial", "artifact"} for entry in read):
            return "truncated" if contents else "error"
        return "ok"

    def _compact_read_summaries_for_prompt(entries: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        """
        Keep read summaries lean for prompt injection.

        We preserve control-flow entries (truncated/error/deferred/covered/artifact/etc.)
        and only keep "full" entries when they carry actionable metadata (hint/cursor/artifact).
        """

        compact_entries: list[dict[str, object]] = []
        for entry in entries:
            status_raw = str(entry.get("status") or "").strip()
            status_key = status_raw.lower()
            has_hint = isinstance(entry.get("hint"), str) and bool(str(entry.get("hint") or "").strip())
            has_cursor = isinstance(entry.get("next_cursor"), str) and bool(str(entry.get("next_cursor") or "").strip())
            has_artifact = isinstance(entry.get("artifact_id"), str) and bool(str(entry.get("artifact_id") or "").strip())
            keep_entry = status_key != "full" or has_hint or has_cursor or has_artifact
            if not keep_entry:
                continue

            out: dict[str, object] = {}
            item_id = entry.get("id")
            if isinstance(item_id, str) and item_id.strip():
                out["id"] = item_id.strip()
            elif item_id is not None:
                out["id"] = item_id

            if status_raw:
                out["status"] = status_raw

            chars_value = entry.get("chars")
            if isinstance(chars_value, bool):
                chars_value = None
            if isinstance(chars_value, (int, float)):
                out["chars"] = int(chars_value)
            elif isinstance(chars_value, str) and chars_value.strip():
                try:
                    out["chars"] = int(chars_value.strip())
                except (TypeError, ValueError):
                    pass

            error_code = entry.get("error_code")
            if isinstance(error_code, str) and error_code.strip():
                out["error_code"] = error_code.strip()

            if has_artifact:
                out["artifact_id"] = str(entry.get("artifact_id")).strip()
            if has_cursor:
                out["next_cursor"] = str(entry.get("next_cursor")).strip()
            if has_hint:
                out["hint"] = str(entry.get("hint")).strip()

            if out:
                compact_entries.append(out)

        return compact_entries

    def _build_response(*, total_chars_value: int, hint: str | None = None) -> dict[str, object]:
        read_out = _compact_read_summaries_for_prompt(read)
        payload: dict[str, object] = {
            "tool": "read_knowledge",
            "status": _response_status(),
            "evidence": contents,
            "deferred": deferred,
            "max_chars": int(max_chars),
            "max_chars_allowed": int(max_chars_allowed),
            "total_chars": int(total_chars_value),
            "budget": context.budget_snapshot(),
        }
        if read_out:
            payload["read"] = read_out
        if errors:
            payload["errors"] = errors
        if hint:
            payload["hint"] = hint
        elif not contents and (deferred or errors):
            payload["hint"] = (
                "No content could be read. Ensure ids/cursors come from tool results, "
                "increase max_chars (up to max_chars_allowed), or retry with fewer items."
            )
        return payload

    def _payload_len_with_budget(payload: Mapping[str, object]) -> int:
        try:
            return len(json.dumps(dict(payload), ensure_ascii=False, default=str))
        except Exception:
            return 0

    def _attach_artifact_for_item(item: dict[str, object], trace: dict[str, object]) -> bool:
        item_id = str(item.get("id") or "").strip()
        payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
        if str(payload_obj.get("type") or "") != "text":
            return False
        full_text = payload_obj.get("text")
        if not item_id or not isinstance(full_text, str) or not full_text:
            return False
        if isinstance(item.get("artifact_id"), str) and str(item.get("artifact_id") or "").strip():
            return False

        preview_len = min(len(full_text), PROMPT_VIEW_INLINE_MAX_CHARS)
        if len(full_text) >= PROMPT_VIEW_INLINE_MIN_CHARS:
            preview_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, preview_len)
        preview_text = full_text[:preview_len]
        cursor_after_raw = item.get("next_cursor")
        cursor_after = _resolve_cursor_from_handle(cursor_after_raw if isinstance(cursor_after_raw, str) else None)
        cursor_used_local = item.get("cursor_used")
        if isinstance(cursor_used_local, str) and cursor_used_local.strip():
            # Avoid nested artifacts: if this segment was already read from an artifact cursor,
            # we can always clip + continue with that cursor instead of creating a new artifact.
            try:
                used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
            except ValueError:
                used_payload = None
            if isinstance(used_payload, dict) and str(used_payload.get("kind") or "") == "artifact":
                return False

        artifact_payload: dict[str, object] = {
            "kind": "agentic_read_v2",
            "item_id": item_id,
            "title": item.get("title"),
            "type": item.get("type"),
            "text": full_text,
        }
        if isinstance(cursor_after, str) and cursor_after.strip():
            artifact_payload["cursor_after"] = cursor_after.strip()
        if isinstance(cursor_used_local, str) and cursor_used_local.strip():
            artifact_payload["cursor_used"] = cursor_used_local.strip()

        artifact_id = store_local_tool_output_artifact(
            conversation=conversation,
            invoked_tool="read_knowledge",
            request={"refs": [{"id": item_id, **({"cursor": cursor_used_local} if cursor_used_local else {})}]},
            response=artifact_payload,
            status="ok",
            is_error=False,
            retention_days=artifact_retention_days,
            max_per_conversation=artifact_max_per_conversation,
        )
        if not artifact_id:
            return False

        cursor_next = {
            **_cursor_payload_base(item_id=item_id, kind="artifact"),
            "artifact_id": artifact_id,
            "char_offset": int(preview_len),
        }
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next)

        item["artifact_id"] = artifact_id
        payload_obj = dict(payload_obj)
        payload_obj["text"] = preview_text
        item["payload"] = payload_obj
        item["chars"] = len(preview_text)
        item["next_cursor"] = _store_cursor_handle(cursor_str) or cursor_str
        item["complete"] = False
        item["truncated"] = True

        trace["status"] = "artifact"
        trace["chars"] = len(full_text)
        trace["artifact_id"] = artifact_id
        return True

    # Start with the raw v2 output; if it exceeds the prompt cap once budgets are added,
    # convert the largest content entries to artifacts until it fits.
    total_chars_final = int(total_chars)
    response_hint: str | None = None
    response_candidate = _build_response(total_chars_value=total_chars_final)
    if output_limit and _payload_len_with_budget(response_candidate) > output_limit:
        read_by_id: dict[str, dict[str, object]] = {}
        for entry in read:
            if isinstance(entry, Mapping) and entry.get("id"):
                read_by_id[str(entry.get("id"))] = entry  # type: ignore[assignment]

        # Convert biggest items first (best shrink per artifact).
        contents_sorted = sorted(
            (item for item in contents if isinstance(item, Mapping)),
            key=lambda item: int(item.get("chars") or 0),
            reverse=True,
        )
        converted_any = False
        for item in contents_sorted:
            item_id = str(item.get("id") or "").strip()
            trace = read_by_id.get(item_id)
            if not trace or not isinstance(item, dict):
                continue
            if not _attach_artifact_for_item(item, trace):
                continue
            converted_any = True
            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = _build_response(total_chars_value=total_chars_final)
            if _payload_len_with_budget(response_candidate) <= output_limit:
                break

        if converted_any:
            response_hint = (
                "Some content was stored as an artifact to fit prompt limits. "
                "Use next_cursor to continue reading until complete."
            )

    response_candidate = _build_response(total_chars_value=total_chars_final, hint=response_hint)
    if output_limit and response_hint and _payload_len_with_budget(response_candidate) > output_limit:
        # Hints are nice-to-have; when the prompt cap is very small, prefer staying under
        # the hard tool-output limit over including extra explanatory text.
        response_hint = None
        response_candidate = _build_response(total_chars_value=total_chars_final)

    # If we're still above the prompt cap (e.g., cursor/budget overhead), clip artifact previews
    # until the JSON payload fits. This doesn't lose evidence because the full text is stored
    # in the artifact and `next_cursor` continues deterministically.
    if output_limit:
        guard_loops = 0
        payload_chars = _payload_len_with_budget(response_candidate)
        while payload_chars > output_limit and guard_loops < 20:
            guard_loops += 1
            artifact_streams: list[tuple[dict[str, object], str, int]] = []
            for item in contents:
                if not isinstance(item, dict):
                    continue
                payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                if str(payload_obj.get("type") or "") != "text" or not isinstance(payload_obj.get("text"), str):
                    continue
                artifact_id_direct = item.get("artifact_id")
                if isinstance(artifact_id_direct, str) and artifact_id_direct.strip():
                    artifact_streams.append((item, artifact_id_direct.strip(), 0))
                    continue
                cursor_used_local = item.get("cursor_used")
                if not (isinstance(cursor_used_local, str) and cursor_used_local.strip()):
                    continue
                try:
                    used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
                except ValueError:
                    continue
                if str(used_payload.get("kind") or "") != "artifact":
                    continue
                artifact_id = str(used_payload.get("artifact_id") or "").strip()
                if not artifact_id:
                    continue
                try:
                    base_offset = int(used_payload.get("char_offset") or 0)
                except (TypeError, ValueError):
                    base_offset = 0
                artifact_streams.append((item, artifact_id, max(0, base_offset)))

            if not artifact_streams:
                break
            target, target_artifact_id, base_offset = max(
                artifact_streams,
                key=lambda it: len(str(((it[0].get("payload") or {}) if isinstance(it[0].get("payload"), Mapping) else {}).get("text") or "")),
            )
            current_payload = target.get("payload") if isinstance(target.get("payload"), Mapping) else {}
            current_text = current_payload.get("text")
            if not isinstance(current_text, str) or len(current_text) <= PROMPT_VIEW_INLINE_MIN_CHARS:
                break

            excess = max(1, payload_chars - output_limit)
            new_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, len(current_text) - excess - 25)
            if new_len >= len(current_text):
                new_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, len(current_text) - 25)
            clipped_text = current_text[:new_len]
            current_payload = dict(current_payload)
            current_payload["text"] = clipped_text
            target["payload"] = current_payload
            target["chars"] = len(clipped_text)

            item_id = str(target.get("id") or "").strip()
            if item_id and target_artifact_id:
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="artifact"),
                    "artifact_id": target_artifact_id,
                    "char_offset": int(base_offset + len(clipped_text)),
                }
                cursor_signed = _sign_agentic_read_cursor_v2(cursor_next)
                target["next_cursor"] = _store_cursor_handle(cursor_signed) or cursor_signed
                target["complete"] = False
                target["truncated"] = True

            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = _build_response(total_chars_value=total_chars_final, hint=response_hint)
            payload_chars = _payload_len_with_budget(response_candidate)

    response = response_candidate

    try:
        artifact_items = sum(1 for entry in read if isinstance(entry, Mapping) and str(entry.get("status") or "") == "artifact")
        truncated_items = sum(1 for entry in read if isinstance(entry, Mapping) and str(entry.get("status") or "") in {"truncated", "partial"})
        response_chars = _payload_len_with_budget(response)
        structured_log(
            "mcp",
            "read_knowledge.agentic_v2",
            {
                "items": len(ordered_items),
                "contents": len(contents),
                "truncated_items": int(truncated_items),
                "artifact_items": int(artifact_items),
                "deferred": len(deferred),
                "errors": len(errors),
                "total_chars": int(total_chars_final),
                "max_chars": int(max_chars),
                "overflow_margin": int(overflow_margin),
                "overflow_used": int(max(0, int(overflow_margin) - int(overflow_remaining))),
                "output_chars": int(response_chars),
                "output_limit": int(output_limit or 0),
                "table_anchor_manifest_lookups": int(table_anchor_manifest_lookups),
                "table_anchor_manifest_hits": int(table_anchor_manifest_hits),
                "table_anchor_fallback_reads": int(table_anchor_fallback_reads),
            },
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
        )
    except Exception:
        # Logging must never break the tool boundary.
        pass

    try:
        context.reserve_characters(int(total_chars_final))
    except CharacterBudgetExceeded as exc:
        return {
            "tool": "read_knowledge",
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "evidence": [],
            "throttle_notice": {"type": "prompt_budget", "message": str(exc)},
            "hint": "Prompt budget exceeded. Ask a narrower question or request fewer items.",
        }

    # Record only refs that actually returned content for repeat-read detection.
    # Deferred/errored refs must remain retryable within the same turn.
    for ref_id in successfully_read_ids:
        context.read_ref_ids_this_turn.add(ref_id)

    return response


def _agentic_table_chunk_snippets(
    *,
    chunk_record: KnowledgeUploadChunk,
    business,
    max_chars: int | None,
    service: KnowledgeSearchService,
) -> list[KnowledgeSnippet] | None:
    chunk_meta = chunk_record.metadata if isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
    if not chunk_meta.get("is_table_chunk"):
        return None

    table_role = str(chunk_meta.get("table_chunk_role") or "").strip().lower()
    table_row_index = chunk_meta.get("table_row_index")
    if table_role == "row" or table_row_index is not None:
        return list(
            service.load_chunk_contents(
                business_profile=business,
                chunk_ids=[str(chunk_record.id)],
                neighbor=0,
                max_chars=max_chars,
            )
        )

    table_id = chunk_meta.get("table_id")
    if not table_id:
        return None

    row_chunks = list(
        apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                upload=chunk_record.upload,
                business_profile=business,
                upload__status=KnowledgeStatus.ACTIVE,
                metadata__table_id=str(table_id),
                metadata__table_chunk_role="row",
            )
        ).order_by("chunk_index")
    )
    if not row_chunks:
        return None

    content_parts = [row.content.strip() for row in row_chunks if row.content]
    combined = "\n\n".join(content_parts).strip()
    if not combined:
        return None

    truncated = False
    if max_chars and len(combined) > max_chars:
        combined = combined[:max_chars]
        truncated = True

    table_title = None
    table_order_index = None
    page_number = None
    try:
        table_obj = (
            KnowledgeUploadTable.objects.filter(id=table_id)
            .select_related("page")
            .only("id", "title", "section_heading", "order_index", "page__page_number")
            .first()
        )
        if table_obj:
            table_title = table_obj.title or table_obj.section_heading
            table_order_index = table_obj.order_index
            page_number = table_obj.page.page_number if table_obj.page else None
    except Exception:
        table_obj = None

    if not table_title:
        table_title = f"Table {table_order_index}" if table_order_index else "Table"

    summary = combined.splitlines()[0][:280] if combined else table_title
    trunc_metrics = service._truncation_metrics(chunk_record.upload)
    partial_index = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False
    source_diag: dict[str, object] = {
        "table_id": str(table_id),
        "table_row_count": len(row_chunks),
        "table_chunk_role": table_role or "preview",
        "table_read_only": True,
    }
    if truncated:
        source_diag["partial_content"] = True
    if trunc_metrics:
        source_diag.update(trunc_metrics)

    snippet = KnowledgeSnippet(
        id=chunk_record.id,
        title=table_title,
        summary=summary,
        source=chunk_record.upload.get_source_type_display(),
        content=combined,
        content_mode="table_rows",
        public_label=table_title,
        structured_tables=tuple(),
        issues=tuple(),
        page_summaries=tuple(),
        read_state=KNOWLEDGE_READ_STATE_PREVIEW if truncated else KNOWLEDGE_READ_STATE_FULL,
        topic_hints=tuple(),
        is_pinned=False,
        upload_id=chunk_record.upload_id,
        chunk_id=chunk_record.id,
        chunk_index=chunk_record.chunk_index,
        page_number=page_number,
        page_mode=None,
        entity_type=chunk_meta.get("entity_type"),
        entity_name=chunk_meta.get("entity_name"),
        entity_business=chunk_meta.get("entity_business"),
        is_table_chunk=True,
        aliases=tuple(chunk_meta.get("aliases") or ()),
        search_stage="table_rows",
        confidence_score=None,
        truncated=truncated,
        source_diagnostics=source_diag,
        partial_index=partial_index,
        structured_table_count=1,
        issue_count=0,
        structured_table_hint=None,
    )

    return [snippet]


def _read_knowledge_agentic_wrapper(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Agentic read_knowledge contract.

    Supported interface:
      read_knowledge(refs=[{id,cursor?}...], max_chars=...)

    This wrapper rejects legacy knobs in agentic mode to keep the contract small and predictable.
    """

    def _has_value(key: str) -> bool:
        value = arguments.get(key)
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    # Accept both "refs" (new) and "items" (compat alias) to reduce brittleness.
    raw_refs = arguments.get("refs")
    if not isinstance(raw_refs, list):
        raw_refs = arguments.get("items")

    if not isinstance(raw_refs, list) or not raw_refs:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_refs",
            "error_code": "missing_refs",
            "evidence": [],
            "hint": "refs[] is required (use ids/cursors from search_knowledge/read_knowledge).",
        }

    # Reject legacy parameters (besides UI-only metadata) to keep the contract tight.
    # NOTE: `mode` is deprecated (kept only as a no-op compat field). The backend
    # chooses the correct representation (tables -> table_rows, text -> excerpts)
    # and paging strategy.
    allowed = {"refs", "items", "max_chars", "mode", "__ui"}
    extra = [key for key in arguments.keys() if key not in allowed and _has_value(str(key))]
    if extra:
        return {
            "tool": "read_knowledge",
            "status": "constraint_error",
            "error": "unsupported_parameters",
            "error_code": "unsupported_parameters",
            "evidence": [],
            "unsupported_fields": extra,
            "hint": "Unsupported parameters for read_knowledge. Use only refs[] + max_chars (+ optional __ui).",
        }

    # Normalize "refs" into the internal "items" shape used by the read engine.
    items: list[dict[str, object]] = []
    seen_item_keys: set[tuple[str, str | None, int | None, int | None]] = set()
    unresolved_invalid_ids: list[str] = []
    unresolved_invalid_errors: list[dict[str, object]] = []
    for entry in raw_refs:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or entry.get("ref") or "").strip()
        if not item_id:
            continue
        cursor = entry.get("cursor")
        cursor_value = cursor.strip() if isinstance(cursor, str) and cursor.strip() else None
        row_start_raw = entry.get("row_start")
        if row_start_raw is None:
            row_start_raw = entry.get("start_row")
        row_limit_raw = entry.get("row_limit")
        if row_limit_raw is None:
            row_limit_raw = entry.get("max_rows")

        row_start_value: int | None = None
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start_value = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start_value = None

        row_limit_value: int | None = None
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit_value = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit_value = None
        if row_limit_value is not None:
            row_limit_value = max(1, min(200, int(row_limit_value)))
        if _is_uuid_ref_id(item_id):
            canonical_id = str(uuid.UUID(item_id))
            item_key = (canonical_id, cursor_value, row_start_value, row_limit_value)
            if item_key in seen_item_keys:
                continue
            seen_item_keys.add(item_key)
            out: dict[str, object] = {"id": canonical_id}
            if cursor_value:
                out["cursor"] = cursor_value
            if row_start_value is not None:
                out["row_start"] = row_start_value
            if row_limit_value is not None:
                out["row_limit"] = row_limit_value
            items.append(out)
            continue

        unresolved_invalid_ids.append(item_id)
        unresolved_invalid_errors.append(
            {
                "id": item_id,
                "error_code": "invalid_id",
                "hint": "id must be a valid UUID from search_knowledge results.",
            }
        )

    retry_tracker = getattr(context, "invalid_read_ref_attempts", None)
    if not isinstance(retry_tracker, dict):
        retry_tracker = {}
        context.invalid_read_ref_attempts = retry_tracker
    retry_limit = _scope_invalid_read_retry_limit()
    retry_limit_hit = False
    for unresolved_id in unresolved_invalid_ids:
        tracker_key = re.sub(r"\s+", " ", str(unresolved_id or "").strip().lower())[:160]
        if not tracker_key:
            continue
        attempt_count = int(retry_tracker.get(tracker_key, 0) or 0) + 1
        retry_tracker[tracker_key] = attempt_count
        if attempt_count >= retry_limit:
            retry_limit_hit = True

    if not items:
        if retry_limit_hit:
            structured_log(
                "mcp",
                "read_knowledge.invalid_refs",
                {
                    "stage": "retry_limit_blocked",
                    "invalid_ref_count": len(unresolved_invalid_ids),
                    "repaired_ref_count": 0,
                    "retry_limit": retry_limit,
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )
            return {
                "tool": "read_knowledge",
                "status": "blocked",
                "error": "invalid_ref_retry_limit",
                "error_code": "invalid_ref_retry_limit",
                "evidence": [],
                "errors": unresolved_invalid_errors,
                "budget": context.budget_snapshot(),
                "hint": (
                    "Repeated non-UUID refs were blocked to prevent retry loops. "
                    "Use UUID refs returned by search_knowledge/read_knowledge only."
                ),
            }
        structured_log(
            "mcp",
            "read_knowledge.invalid_refs",
            {
                "stage": "invalid_refs_rejected",
                "invalid_ref_count": len(unresolved_invalid_ids),
                "repaired_ref_count": 0,
            },
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "invalid_refs",
            "error_code": "invalid_refs",
            "evidence": [],
            "errors": unresolved_invalid_errors,
            "budget": context.budget_snapshot(),
            "hint": (
                "refs[] must contain UUID ids from search_knowledge results. "
                "If refs came from a category label, run search_knowledge first and use the returned UUID refs."
            ),
        }

    # Pass through to the agentic read engine.
    engine_args: dict[str, object] = {
        "items": items,
        "max_chars": arguments.get("max_chars"),
    }
    engine_result = _agentic_read_v2_handler(engine_args, conversation, context)
    if unresolved_invalid_errors:
        patched_result = dict(engine_result)
        existing_errors = patched_result.get("errors")
        merged_errors = (
            [dict(item) for item in existing_errors if isinstance(item, Mapping)]
            if isinstance(existing_errors, list)
            else []
        )
        merged_errors.extend(unresolved_invalid_errors)
        patched_result["errors"] = merged_errors
        hint_text = str(patched_result.get("hint") or "").strip()
        reminder = "Ignored non-UUID refs and continued with valid UUID refs."
        patched_result["hint"] = f"{hint_text} {reminder}".strip() if hint_text else reminder
        return patched_result
    return engine_result


def _read_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Public read_knowledge entrypoint.

    Enforces the refs-first agentic contract.
    """
    return _read_knowledge_agentic_wrapper(arguments, conversation, context)


def _action_tool_result(action: ActionType, payload: Mapping[str, object]) -> Mapping[str, object]:
    """
    Structure a tool result as a planned action without side effects.

    The legacy ActionDispatcher will still execute these actions based on the
    AiOrchestratorPlan, so MCP tooling focuses on planning, not persistence.
    """

    return {
        "action": action.value,
        "payload": dict(payload),
    }


def _create_case_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only; no side effects
    title = _coerce_str(arguments.get("title")).strip() or "Customer request"
    description = _coerce_str(arguments.get("description")).strip()
    priority = _normalize_priority(arguments.get("priority")) or "medium"
    ai_diagnosis = _coerce_str(arguments.get("ai_diagnosis")).strip()
    ai_actions_taken = _coerce_str(arguments.get("ai_actions_taken")).strip()
    raw_suggestions = arguments.get("ai_suggested_actions") or []
    suggestions: list[str] = [
        str(item)
        for item in raw_suggestions
        if isinstance(item, (str, int, float))
    ]
    payload = {
        "title": title,
        "description": description,
        "priority": priority,
        "ai_diagnosis": ai_diagnosis,
        "ai_actions_taken": ai_actions_taken,
        "ai_suggested_actions": suggestions,
        "metadata": {"source": "mcp_orchestrator"},
    }
    return _action_tool_result(ActionType.CREATE_CASE, payload)


def _update_case_status_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation  # planning only
    status_raw = _coerce_str(arguments.get("status")).strip().lower()
    if status_raw in {"resolve", "resolved", "close", "closed"}:
        status = "closed"
    elif status_raw in {"open", "reopen", "re-open"}:
        status = "open"
    else:
        status = status_raw or "open"
    payload = {
        "case_id": _coerce_str(arguments.get("case_id")).strip(),
        "status": status,
        "note": _coerce_str(arguments.get("note")).strip() or None,
    }
    return _action_tool_result(ActionType.UPDATE_CASE_STATUS, payload)


def _update_case_details_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    payload: dict[str, object] = {
        "case_id": _coerce_str(arguments.get("case_id")).strip(),
        "allow_description_overwrite": bool(arguments.get("allow_description_overwrite") or False),
    }
    for key in ("title", "description", "priority"):
        value = arguments.get(key)
        if value is None:
            continue
        if key == "priority":
            normalized = _normalize_priority(value)
            if normalized:
                payload[key] = normalized
        else:
            text = _coerce_str(value).strip()
            if text:
                payload[key] = text
    return _action_tool_result(ActionType.UPDATE_CASE_DETAILS, payload)


def _add_case_history_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    payload = {
        "case_id": _coerce_str(arguments.get("case_id")).strip(),
        "summary": _coerce_str(arguments.get("summary")).strip(),
    }
    return _action_tool_result(ActionType.ADD_CASE_HISTORY, payload)


def _flag_escalation_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    reason = _coerce_str(arguments.get("reason")).strip() or "Escalated by MCP orchestrator"
    details = _coerce_str(arguments.get("details")).strip()
    payload: dict[str, object] = {"reason": reason}
    if details:
        payload["metadata"] = {"details": details}
    return _action_tool_result(ActionType.FLAG_ESCALATION, payload)


def _create_customer_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only
    full_name = _coerce_str(arguments.get("full_name")).strip() or "Web Visitor"
    email = _coerce_str(arguments.get("email")).strip()
    phone = _coerce_str(arguments.get("phone")).strip()
    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), Mapping) else {}
    payload = {
        "display_name": full_name,
        "primary_email": email,
        "primary_phone": phone,
        "metadata": metadata,
        # record_origin is filled by the legacy handler if omitted
    }
    return _action_tool_result(ActionType.CREATE_CUSTOMER, payload)


def _update_customer_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    full_name = _coerce_str(arguments.get("full_name")).strip()
    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), Mapping) else {}
    payload: dict[str, object] = {
        "customer_id": _coerce_str(arguments.get("customer_id")).strip(),
    }
    if full_name:
        payload["display_name"] = full_name
    if metadata:
        payload["metadata"] = metadata
    return _action_tool_result(ActionType.UPDATE_CUSTOMER, payload)


def _create_lead_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only
    payload = {
        "title": _coerce_str(arguments.get("title")).strip(),
        "description": _coerce_str(arguments.get("description")).strip(),
        "source": _coerce_str(arguments.get("source")).strip() or "mcp_orchestrator",
        "conversation_id": str(conversation.id),
    }
    return _action_tool_result(ActionType.CREATE_LEAD, payload)


def _create_appointment_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only
    payload = {
        "topic": _coerce_str(arguments.get("topic")).strip(),
        "preferred_time": _coerce_str(arguments.get("preferred_time")).strip(),
        "notes": _coerce_str(arguments.get("notes")).strip(),
        "conversation_id": str(conversation.id),
    }
    return _action_tool_result(ActionType.CREATE_APPOINTMENT, payload)


# ---------------------------------------------------------------------------
# Backwards-compatible stub factory (for undefined tools)


def _require_provider_stub(tool_name: str, *, preflight: Callable[[ToolExecutionContext], None] | None = None) -> ToolHandler:
    """
    Produce a placeholder handler that raises a helpful error.

    This lets us wire up the dispatcher surface without accidentally invoking
    partially implemented logic.
    """

    def _handler(arguments: Mapping[str, object], conversation: Conversation, context: ToolExecutionContext) -> Mapping[str, object]:
        if preflight:
            preflight(context)
        del arguments, conversation
        raise NotImplementedError(f"MCP tool '{tool_name}' execution is pending.")

    return _handler


def _enforce_single_chunk_read(context: ToolExecutionContext) -> None:
    context.reserve_chunk_reads(1)

def _resolve_file_context_conversation(conversation: Conversation) -> Conversation:
    """
    Agent runs execute in isolated conversations, but still need access to the
    anchor chat's uploaded files/artifacts.

    If the current conversation is an agent-run execution context and it has an
    `anchor_conversation_id`, route file tools against that anchor conversation.
    """

    cached = getattr(conversation, "_file_context_conversation", None)
    if isinstance(cached, Conversation):
        return cached

    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    if isinstance(meta, Mapping):
        source = str(meta.get("source") or "").strip().lower()
        if source == "agent_run":
            anchor_id_raw = str(meta.get("anchor_conversation_id") or meta.get("anchorConversationId") or "").strip()
            if anchor_id_raw:
                try:
                    anchor_uuid = uuid.UUID(anchor_id_raw)
                except (TypeError, ValueError):
                    anchor_uuid = None
                if anchor_uuid:
                    anchor = Conversation.objects.filter(
                        id=anchor_uuid,
                        business_profile_id=getattr(conversation, "business_profile_id", None),
                    ).first()
                    if anchor is not None:
                        setattr(conversation, "_file_context_conversation", anchor)
                        return anchor

    setattr(conversation, "_file_context_conversation", conversation)
    return conversation


def _search_conversation_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    query_text = _coerce_str(arguments.get("query")).strip()
    if not query_text:
        return {
            "tool": "search_conversation_files",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "query is required.",
        }

    try:
        limit = int(arguments.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(8, limit))

    base_qs = (
        ConversationFileChunk.objects.filter(
            conversation=file_conversation,
            conversation_file__status="ready",
            conversation_file__kind="upload",
        )
        .select_related("conversation_file")
        .order_by("id")
    )
    if not base_qs.exists():
        return {
            "tool": "search_conversation_files",
            "status": "ok",
            "snippets": [],
            "hint": "No uploaded files are available in this chat yet.",
        }

    snippets: list[dict[str, object]] = []
    embedder = _portal_file_embedding_service()
    query_vector: list[float] | None = None
    if embedder:
        try:
            query_vector = embedder.embed_text(query_text)
        except Exception:
            query_vector = None

    if query_vector:
        try:
            from pgvector.django import CosineDistance
        except Exception:  # pragma: no cover - defensive
            query_vector = None
        else:
            ann_limit = max(limit * 10, 40)
            ann_qs = (
                base_qs.exclude(embedding__isnull=True)
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance", "id")[:ann_limit]
            )
            for chunk in ann_qs[:limit]:
                distance = getattr(chunk, "distance", None)
                try:
                    distance_val = float(distance) if distance is not None else None
                except (TypeError, ValueError):
                    distance_val = None
                file = getattr(chunk, "conversation_file", None)
                snippets.append(
                    {
                        "id": str(chunk.id),
                        "file": {
                            "id": str(getattr(file, "id", "")),
                            "filename": getattr(file, "filename", ""),
                            "page_count": getattr(file, "page_count", 0),
                        },
                        "preview": (chunk.content or "")[:800],
                        "vector_distance": distance_val,
                        "read_hint": {"ids": [str(chunk.id)]},
                    }
                )

    if not snippets:
        # Lexical fallback for environments without embeddings.
        try:
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
        except Exception:  # pragma: no cover - defensive
            SearchVector = None  # type: ignore
        if SearchVector is not None:
            config = str(getattr(settings, "RAG_FTS_CONFIG", "english") or "english")
            vector = SearchVector("content", config=config)
            search_query = SearchQuery(query_text, search_type="websearch", config=config)
            fts_qs = (
                base_qs.annotate(rank=SearchRank(vector, search_query, cover_density=True))
                .filter(rank__gt=0)
                .order_by("-rank", "id")[:limit]
            )
            for chunk in fts_qs:
                file = getattr(chunk, "conversation_file", None)
                snippets.append(
                    {
                        "id": str(chunk.id),
                        "file": {
                            "id": str(getattr(file, "id", "")),
                            "filename": getattr(file, "filename", ""),
                            "page_count": getattr(file, "page_count", 0),
                        },
                        "preview": (chunk.content or "")[:800],
                        "rank": float(getattr(chunk, "rank", 0.0) or 0.0),
                        "read_hint": {"ids": [str(chunk.id)]},
                    }
                )

    if not snippets:
        return {
            "tool": "search_conversation_files",
            "status": "ok",
            "snippets": [],
            "hint": "No matches found in uploaded files.",
        }

    return {
        "tool": "search_conversation_files",
        "status": "ok",
        "snippets": snippets[:limit],
    }


def _read_conversation_file_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    raw_ids = arguments.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return {
            "tool": "read_conversation_file",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "ids[] is required (from search_conversation_files).",
        }

    ids: list[str] = []
    uuid_ids: list[uuid.UUID] = []
    for item in raw_ids:
        token = str(item or "").strip()
        if not token:
            continue
        try:
            uuid_ids.append(uuid.UUID(token))
            ids.append(token)
        except (TypeError, ValueError):
            continue
    if not uuid_ids:
        return {
            "tool": "read_conversation_file",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "ids[] must contain valid UUIDs from search_conversation_files.",
        }

    try:
        max_chars = int(arguments.get("max_chars") or 8000)
    except (TypeError, ValueError):
        max_chars = 8000
    max_chars = max(500, min(20000, max_chars))

    rows = list(
        ConversationFileChunk.objects.filter(
            conversation=file_conversation,
            id__in=uuid_ids,
            conversation_file__status="ready",
        )
        .select_related("conversation_file")
        .order_by("id")
    )
    by_id = {str(row.id): row for row in rows}

    out_chunks: list[dict[str, object]] = []
    remaining = max_chars
    for chunk_id in ids:
        row = by_id.get(chunk_id)
        if row is None:
            continue
        content = (row.content or "").strip()
        if not content:
            continue
        clipped = content[:remaining]
        remaining -= len(clipped)
        file = getattr(row, "conversation_file", None)
        out_chunks.append(
            {
                "id": str(row.id),
                "file": {
                    "id": str(getattr(file, "id", "")),
                    "filename": getattr(file, "filename", ""),
                    "page_count": getattr(file, "page_count", 0),
                },
                "content": clipped,
            }
        )
        if remaining <= 0:
            break

    if not out_chunks:
        return {
            "tool": "read_conversation_file",
            "status": "ok",
            "chunks": [],
            "hint": "No content could be read for the requested ids.",
        }

    return {
        "tool": "read_conversation_file",
        "status": "ok",
        "chunks": out_chunks,
    }


def _portal_file_download_url(conversation: Conversation, file_id: uuid.UUID) -> str:
    from datetime import timedelta

    from apps.conversations.portal_files import sign_portal_file_token

    ttl_seconds = int(getattr(settings, "PORTAL_FILE_DOWNLOAD_TTL_SECONDS", 3600) or 0)
    if ttl_seconds <= 0:
        ttl_seconds = 3600
    token = sign_portal_file_token(
        file_id=file_id,
        business_id=conversation.business_profile_id,
        ttl=timedelta(seconds=ttl_seconds),
    )
    return f"/api/chat/portal/files/{file_id}/download/?token={token}"


def _pdf_generate_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    content = _coerce_str(arguments.get("content")).strip()
    if not content:
        return {
            "tool": "pdf_generate",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "content is required.",
        }

    title = _coerce_str(arguments.get("title")).strip()
    filename = _coerce_str(arguments.get("filename")).strip() or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    fmt = _coerce_str(arguments.get("format")).strip().lower() or "markdown"
    page_size = _coerce_str(arguments.get("page_size")).strip().lower() or "letter"

    try:
        import io

        from reportlab.lib.pagesizes import A4, LETTER
        from reportlab.lib.units import inch
        from reportlab.lib.utils import simpleSplit
        from reportlab.pdfgen import canvas
    except Exception:
        return {
            "tool": "pdf_generate",
            "status": "error",
            "error": "pdf_generation_unavailable",
            "error_code": "pdf_generation_unavailable",
            "hint": "PDF generation backend is not installed.",
        }

    if page_size == "a4":
        pagesize = A4
    else:
        pagesize = LETTER
    width, height = pagesize

    # Render markdown as plain text for now (no HTML/CSS rendering).
    text = content
    if fmt not in {"markdown", "text"}:
        fmt = "text"

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=pagesize)

    margin_x = 0.75 * inch
    margin_y = 0.75 * inch
    line_height = 14
    body_font = ("Helvetica", 11)
    title_font = ("Helvetica-Bold", 16)

    y = height - margin_y
    if title:
        c.setFont(*title_font)
        for line in simpleSplit(title, title_font[0], title_font[1], width - (2 * margin_x)):
            if y <= margin_y:
                c.showPage()
                y = height - margin_y
                c.setFont(*title_font)
            c.drawString(margin_x, y, line)
            y -= line_height + 2
        y -= 6

    c.setFont(*body_font)
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            y -= line_height
            if y <= margin_y:
                c.showPage()
                y = height - margin_y
                c.setFont(*body_font)
            continue
        wrapped = simpleSplit(line, body_font[0], body_font[1], width - (2 * margin_x))
        for segment in wrapped:
            if y <= margin_y:
                c.showPage()
                y = height - margin_y
                c.setFont(*body_font)
            c.drawString(margin_x, y, segment)
            y -= line_height

    c.save()
    pdf_bytes = buf.getvalue()

    from apps.conversations.portal_files import create_conversation_artifact_from_bytes

    artifact = create_conversation_artifact_from_bytes(
        conversation=file_conversation,
        filename=filename,
        content_type="application/pdf",
        payload=pdf_bytes,
        sender="ai",
        max_pdf_pages=int(getattr(settings, "PORTAL_PDF_MAX_PAGES", 250) or 0) or None,
    )
    download_url = _portal_file_download_url(file_conversation, artifact.id)
    return {
        "tool": "pdf_generate",
        "status": "ok",
        "artifact": {
            "file_id": str(artifact.id),
            "filename": artifact.filename,
            "download_url": download_url,
        },
        "download_url": download_url,
    }


def _pdf_merge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    raw_ids = arguments.get("file_ids")
    if not isinstance(raw_ids, list) or len(raw_ids) < 2:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_ids[] must contain at least two PDF file IDs.",
        }

    ordered_ids: list[str] = []
    uuid_ids: list[uuid.UUID] = []
    for item in raw_ids:
        token = str(item or "").strip()
        if not token:
            continue
        try:
            uuid_ids.append(uuid.UUID(token))
            ordered_ids.append(token)
        except (TypeError, ValueError):
            continue
    if len(uuid_ids) < 2:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_ids[] must contain valid UUIDs.",
        }

    filename = _coerce_str(arguments.get("filename")).strip() or "merged.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    files = list(
        ConversationFile.objects.filter(
            conversation=file_conversation,
            id__in=uuid_ids,
            status="ready",
        ).order_by("id")
    )
    by_id = {str(f.id): f for f in files}
    resolved = [by_id.get(file_id) for file_id in ordered_ids]
    resolved = [f for f in resolved if f is not None]
    if len(resolved) < 2:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "not_found",
            "error_code": "not_found",
            "hint": "One or more PDFs were not found in this chat session.",
        }

    try:
        import io

        from pypdf import PdfReader, PdfWriter
    except Exception:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "pdf_backend_unavailable",
            "error_code": "pdf_backend_unavailable",
            "hint": "PDF backend is not available.",
        }

    from apps.conversations.portal_files import resolve_portal_file_path

    writer = PdfWriter()
    for f in resolved:
        if not (f.content_type == "application/pdf" or str(f.filename or "").lower().endswith(".pdf")):
            return {
                "tool": "pdf_merge",
                "status": "error",
                "error": "validation_error",
                "error_code": "validation_error",
                "hint": f"{f.filename} is not a PDF.",
            }
        path = resolve_portal_file_path(f)
        if not path.exists():
            return {
                "tool": "pdf_merge",
                "status": "error",
                "error": "not_found",
                "error_code": "not_found",
                "hint": f"Missing file on disk: {f.filename}",
            }
        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            return {
                "tool": "pdf_merge",
                "status": "error",
                "error": "invalid_pdf",
                "error_code": "invalid_pdf",
                "hint": f"Invalid PDF: {f.filename}.",
            }
        for page in reader.pages:
            writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    pdf_bytes = out.getvalue()

    from apps.conversations.portal_files import create_conversation_artifact_from_bytes

    artifact = create_conversation_artifact_from_bytes(
        conversation=file_conversation,
        filename=filename,
        content_type="application/pdf",
        payload=pdf_bytes,
        sender="ai",
        max_pdf_pages=int(getattr(settings, "PORTAL_PDF_MAX_PAGES", 250) or 0) or None,
    )
    download_url = _portal_file_download_url(file_conversation, artifact.id)
    return {
        "tool": "pdf_merge",
        "status": "ok",
        "artifact": {
            "file_id": str(artifact.id),
            "filename": artifact.filename,
            "download_url": download_url,
        },
        "download_url": download_url,
    }


def _pdf_extract_pages_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    file_id_raw = _coerce_str(arguments.get("file_id")).strip()
    if not file_id_raw:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id is required.",
        }
    try:
        file_id = uuid.UUID(file_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id must be a valid UUID.",
        }

    pages_raw = arguments.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "pages[] is required.",
        }
    pages: list[int] = []
    for item in pages_raw:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page >= 1:
            pages.append(page)
    pages = sorted(set(pages))
    if not pages:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "pages[] must contain 1-based page numbers.",
        }

    filename = _coerce_str(arguments.get("filename")).strip() or "pages.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    file = ConversationFile.objects.filter(conversation=file_conversation, id=file_id, status="ready").first()
    if file is None:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "not_found",
            "error_code": "not_found",
            "hint": "PDF not found in this chat session.",
        }
    if not (file.content_type == "application/pdf" or str(file.filename or "").lower().endswith(".pdf")):
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "Selected file is not a PDF.",
        }

    try:
        import io

        from pypdf import PdfReader, PdfWriter
    except Exception:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "pdf_backend_unavailable",
            "error_code": "pdf_backend_unavailable",
            "hint": "PDF backend is not available.",
        }

    from apps.conversations.portal_files import resolve_portal_file_path

    path = resolve_portal_file_path(file)
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    invalid = [p for p in pages if p > total_pages]
    if invalid:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": f"Invalid pages {invalid}; PDF has {total_pages} pages.",
        }
    writer = PdfWriter()
    for page_no in pages:
        writer.add_page(reader.pages[page_no - 1])
    out = io.BytesIO()
    writer.write(out)
    pdf_bytes = out.getvalue()

    from apps.conversations.portal_files import create_conversation_artifact_from_bytes

    artifact = create_conversation_artifact_from_bytes(
        conversation=file_conversation,
        filename=filename,
        content_type="application/pdf",
        payload=pdf_bytes,
        sender="ai",
        max_pdf_pages=int(getattr(settings, "PORTAL_PDF_MAX_PAGES", 250) or 0) or None,
    )
    download_url = _portal_file_download_url(file_conversation, artifact.id)
    return {
        "tool": "pdf_extract_pages",
        "status": "ok",
        "artifact": {
            "file_id": str(artifact.id),
            "filename": artifact.filename,
            "download_url": download_url,
        },
        "download_url": download_url,
    }


def _pdf_extract_text_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    file_id_raw = _coerce_str(arguments.get("file_id")).strip()
    if not file_id_raw:
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id is required.",
        }
    try:
        file_id = uuid.UUID(file_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id must be a valid UUID.",
        }

    try:
        max_chars = int(arguments.get("max_chars") or 12000)
    except (TypeError, ValueError):
        max_chars = 12000
    max_chars = max(500, min(50000, max_chars))

    pages_raw = arguments.get("pages")
    pages: list[int] | None = None
    if isinstance(pages_raw, list) and pages_raw:
        extracted_pages: list[int] = []
        for item in pages_raw:
            try:
                page = int(item)
            except (TypeError, ValueError):
                continue
            if page >= 1:
                extracted_pages.append(page)
        extracted_pages = sorted(set(extracted_pages))
        if extracted_pages:
            pages = extracted_pages

    file = ConversationFile.objects.filter(conversation=file_conversation, id=file_id, status="ready").first()
    if file is None:
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "not_found",
            "error_code": "not_found",
            "hint": "PDF not found in this chat session.",
        }
    if not (file.content_type == "application/pdf" or str(file.filename or "").lower().endswith(".pdf")):
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "Selected file is not a PDF.",
        }

    try:
        from pypdf import PdfReader
    except Exception:
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "pdf_backend_unavailable",
            "error_code": "pdf_backend_unavailable",
            "hint": "PDF backend is not available.",
        }

    from apps.conversations.portal_files import resolve_portal_file_path

    path = resolve_portal_file_path(file)
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    if pages:
        invalid = [p for p in pages if p > total_pages]
        if invalid:
            return {
                "tool": "pdf_extract_text",
                "status": "error",
                "error": "validation_error",
                "error_code": "validation_error",
                "hint": f"Invalid pages {invalid}; PDF has {total_pages} pages.",
            }
        page_numbers = pages
    else:
        page_numbers = list(range(1, total_pages + 1))

    fragments: list[str] = []
    for p in page_numbers:
        try:
            fragments.append(reader.pages[p - 1].extract_text() or "")
        except Exception:
            fragments.append("")
        if sum(len(x) for x in fragments) >= max_chars:
            break
    text = "\n".join(fragments).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return {
        "tool": "pdf_extract_text",
        "status": "ok",
        "file": {"id": str(file.id), "filename": file.filename, "page_count": total_pages},
        "text": text,
    }


def _mcp_gateway_mode_enabled(conversation: Conversation) -> bool:
    # Gateway mode is permanently enabled.
    del conversation
    return True


def _gateway_clip_text(value: object, limit: int) -> str:
    text = _coerce_str(value).strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _gateway_schema_type_hint(schema: object) -> str:
    if not isinstance(schema, Mapping):
        return "any"

    raw_type = schema.get("type")
    types: list[str] = []
    if isinstance(raw_type, str) and raw_type.strip():
        types = [raw_type.strip()]
    elif isinstance(raw_type, list):
        types = [str(entry).strip() for entry in raw_type if str(entry).strip()]

    if not types:
        for key in ("anyOf", "oneOf", "allOf"):
            options = schema.get(key)
            if not isinstance(options, list):
                continue
            option_types: list[str] = []
            for option in options[:8]:
                hint = _gateway_schema_type_hint(option)
                if hint and hint != "any":
                    option_types.append(hint)
            if option_types:
                types = option_types
                break

    if not types:
        if isinstance(schema.get("properties"), Mapping):
            return "object"
        if schema.get("items") is not None:
            return "array"
        return "any"

    if "array" in types:
        items = schema.get("items")
        item_hint = _gateway_schema_type_hint(items)
        return f"array<{item_hint}>"

    if len(types) == 1:
        return types[0]
    return "|".join(types[:4])


def _gateway_required_args(
    schema: object,
    *,
    satisfied_keys: set[str] | None = None,
    limit: int = 12,
) -> list[dict[str, str]]:
    if not isinstance(schema, Mapping):
        return []
    required = schema.get("required")
    if not isinstance(required, list) or not required:
        return []
    properties = schema.get("properties")
    props = properties if isinstance(properties, Mapping) else {}
    out: list[dict[str, str]] = []
    for key in required:
        name = str(key).strip()
        if not name:
            continue
        if satisfied_keys and name in satisfied_keys:
            continue
        hint = _gateway_schema_type_hint(props.get(name))
        out.append({"name": name, "type": hint})
        if len(out) >= limit:
            break
    return out


_GATEWAY_TOOL_VERB_HINTS: set[str] = {
    "add",
    "create",
    "delete",
    "describe",
    "edit",
    "fetch",
    "find",
    "get",
    "list",
    "lookup",
    "modify",
    "patch",
    "post",
    "put",
    "query",
    "read",
    "remove",
    "retrieve",
    "search",
    "send",
    "set",
    "show",
    "update",
    "upload",
    "view",
    "write",
}

_GATEWAY_VENDOR_TOKENS: set[str] = {
    "github",
    "gitlab",
    "jira",
    "slack",
    "notion",
    "linear",
    "google",
    "drive",
    "gmail",
}


def _gateway_tokenize(text: str) -> set[str]:
    tokens = {token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if token}
    expanded = set(tokens)

    for token in list(tokens):
        if len(token) < 4:
            continue
        if token.endswith("ies") and len(token) > 4:
            expanded.add(token[:-3] + "y")  # repositories -> repository
        if token.endswith("es") and not token.endswith("ies") and len(token) > 4:
            expanded.add(token[:-2])  # branches -> branch (best-effort)
        if token.endswith("s") and not token.endswith("ss") and len(token) > 4:
            expanded.add(token[:-1])  # issues -> issue

    if "repos" in expanded or "repo" in expanded:
        expanded.update({"repository", "repositories"})
    if "pr" in expanded:
        expanded.update({"pull", "request", "requests"})
    if "my" in expanded:
        expanded.update({"me", "user", "account", "profile"})

    return {token for token in expanded if token}


def _gateway_token_weight(token: str) -> int:
    normalized = str(token or "").strip().lower()
    if not normalized:
        return 0
    if normalized in _GATEWAY_TOOL_VERB_HINTS:
        return 1
    if normalized in _GATEWAY_VENDOR_TOKENS:
        return 2
    return 4


def _gateway_search_score(
    query_tokens: set[str],
    *,
    remote_tokens: set[str],
    description_tokens: set[str],
    connection_tokens: set[str],
    required_count: int,
) -> int:
    if not query_tokens:
        return 0

    score = 0

    overlap_remote = query_tokens.intersection(remote_tokens)
    overlap_description = query_tokens.intersection(description_tokens)
    overlap_connection = query_tokens.intersection(connection_tokens)

    score += sum(_gateway_token_weight(token) for token in overlap_remote) * 6
    score += sum(_gateway_token_weight(token) for token in overlap_description) * 2
    score += sum(_gateway_token_weight(token) for token in overlap_connection) * 1

    if required_count <= 0:
        score += 3
    elif required_count == 1:
        score += 2
    elif required_count >= 5:
        score -= 2

    return score


def _mcp_search_tools_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del conversation

    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return {
            "tool": "mcp_search_tools",
            "status": "error",
            "error": "missing_query",
            "error_code": "missing_query",
            "results": [],
            "hint": "Provide a natural-language query describing the tool you want (e.g. 'list GitHub repos').",
        }

    try:
        limit = int(arguments.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(10, limit))

    connection_filter = _coerce_str(arguments.get("connection_id")).strip()

    catalog = getattr(context, "mcp_gateway_catalog", None)
    if not isinstance(catalog, Mapping) or not catalog:
        return {
            "tool": "mcp_search_tools",
            "status": "ok",
            "results": [],
            "hint": "No external MCP tools are available for this agent.",
        }

    query_norm = query.lower()
    query_tokens = _gateway_tokenize(query_norm)

    scored: list[tuple[int, str, dict[str, object]]] = []
    for tool_id, meta in catalog.items():
        if not isinstance(meta, Mapping):
            continue
        conn_id = str(meta.get("connection_id") or "").strip()
        if connection_filter and conn_id != connection_filter:
            continue
        connection_name = _coerce_str(meta.get("connection_name")).strip()
        remote_tool = _coerce_str(meta.get("remote_tool")).strip()
        description = _coerce_str(meta.get("description")).strip()
        schema = meta.get("input_schema")
        satisfied_raw = meta.get("default_arg_keys")
        satisfied_keys = (
            {str(value).strip() for value in satisfied_raw if str(value).strip()}
            if isinstance(satisfied_raw, (list, tuple, set))
            else None
        )
        required_args = _gateway_required_args(schema, satisfied_keys=satisfied_keys)
        remote_tokens = _gateway_tokenize(remote_tool)
        description_tokens = _gateway_tokenize(description)
        connection_tokens = _gateway_tokenize(connection_name)
        score = _gateway_search_score(
            query_tokens,
            remote_tokens=remote_tokens,
            description_tokens=description_tokens,
            connection_tokens=connection_tokens,
            required_count=len(required_args),
        )
        if score <= 0:
            continue
        scored.append((score, tool_id, dict(meta)))

    scored.sort(key=lambda entry: (-entry[0], entry[1]))

    results: list[dict[str, object]] = []
    for _, tool_id, meta in scored[:limit]:
        schema = meta.get("input_schema")
        satisfied_raw = meta.get("default_arg_keys")
        satisfied_keys = (
            {str(value).strip() for value in satisfied_raw if str(value).strip()}
            if isinstance(satisfied_raw, (list, tuple, set))
            else None
        )
        results.append(
            {
                "tool_id": str(tool_id),
                "connection_name": _gateway_clip_text(meta.get("connection_name"), 80),
                "remote_tool": _gateway_clip_text(meta.get("remote_tool"), 80),
                "description": _gateway_clip_text(meta.get("description"), 240),
                "required_args": _gateway_required_args(schema, satisfied_keys=satisfied_keys),
            }
        )

    if not results:
        hint = "No tools matched that query."
        if connection_filter:
            hint = "No tools matched that query for the requested connection_id."
        return {
            "tool": "mcp_search_tools",
            "status": "ok",
            "results": [],
            "hint": hint,
        }

    return {
        "tool": "mcp_search_tools",
        "status": "ok",
        "results": results,
    }


def _mcp_call_tool_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del arguments, conversation, context
    return {
        "tool": "mcp_call_tool",
        "status": "error",
        "error": "handled_by_orchestrator",
        "error_code": "handled_by_orchestrator",
        "output": None,
        "hint": "mcp_call_tool is executed by the MCP orchestrator tool loop.",
    }


def _email_error(tool: str, *, error_code: str, hint: str) -> Mapping[str, object]:
    return {
        "tool": tool,
        "status": "error",
        "error": error_code,
        "error_code": error_code,
        "hint": hint,
    }


def _resolve_email_account_for_tool(
    *,
    tool: str,
    arguments: Mapping[str, object],
    conversation: Conversation,
) -> tuple[EmailAccount | None, Mapping[str, object] | None]:
    """
    Resolve the EmailAccount to use for a tool call.

    Preferred:
    - explicit email_account_id argument
    Fallback (for dashboard chat sessions):
    - conversation.metadata.actor_user_id (or user_id)
    """

    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    raw_account_id = _coerce_str(arguments.get("email_account_id") or arguments.get("emailAccountId")).strip()
    if raw_account_id:
        try:
            account_uuid = uuid.UUID(raw_account_id)
        except (TypeError, ValueError):
            return None, _email_error(tool, error_code="validation_error", hint="email_account_id must be a valid UUID.")
        account = EmailAccount.objects.filter(
            id=account_uuid,
            business_profile_id=getattr(conversation, "business_profile_id", None),
        ).first()
        if not account:
            return None, _email_error(tool, error_code="email_account_not_found", hint="Email account not found.")
        if account.status != EmailAccountStatus.CONNECTED:
            return None, _email_error(tool, error_code="email_not_connected", hint="Email account is not connected.")
        if actor_user_uuid and account.user_id != actor_user_uuid:
            return None, _email_error(
                tool,
                error_code="account_mismatch",
                hint="Connected account belongs to a different user in this workspace.",
            )
        if not is_email_tool_enabled_for_account(account=account, tool_name=tool):
            return None, _email_error(
                tool,
                error_code="tool_disabled",
                hint="This integration tool is disabled in Integrations settings.",
            )
        return account, None

    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    actor_user_id = None
    if isinstance(meta, Mapping):
        actor_user_id = meta.get("actor_user_id") or meta.get("actorUserId") or meta.get("user_id") or meta.get("userId")
    if actor_user_id:
        try:
            user_uuid = uuid.UUID(str(actor_user_id))
        except (TypeError, ValueError):
            user_uuid = None
        if user_uuid:
            account = EmailAccount.objects.filter(
                business_profile_id=getattr(conversation, "business_profile_id", None),
                user_id=user_uuid,
            ).first()
            if account and account.status == EmailAccountStatus.CONNECTED:
                if not is_email_tool_enabled_for_account(account=account, tool_name=tool):
                    return None, _email_error(
                        tool,
                        error_code="tool_disabled",
                        hint="This integration tool is disabled in Integrations settings.",
                    )
                return account, None

    return None, _email_error(
        tool,
        error_code="email_not_connected",
        hint="No connected email account found. Connect Gmail/Microsoft via OAuth first.",
    )


def _email_search_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _email_error("email_search", error_code="missing_query", hint="query is required.")
    try:
        limit = int(arguments.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(25, limit))

    account, error = _resolve_email_account_for_tool(tool="email_search", arguments=arguments, conversation=conversation)
    if error:
        return error
    assert account is not None

    if account.provider != EmailAccountProvider.GOOGLE:
        if account.provider != EmailAccountProvider.MICROSOFT:
            return _email_error(
                "email_search",
                error_code="provider_not_supported",
                hint="Email provider is not supported yet.",
            )

    try:
        account = ensure_fresh_email_credentials(account)
    except Exception:
        logger.exception("email.oauth_refresh_failed tool=email_search account=%s", getattr(account, "id", None))
        return _email_error(
            "email_search",
            error_code="oauth_refresh_failed",
            hint="Email OAuth refresh failed. Reconnect the email account and try again.",
        )

    creds = account.credentials or {}
    access_token = str(creds.get("access_token") or "").strip()
    if not access_token:
        return _email_error(
            "email_search",
            error_code="missing_access_token",
            hint="Email account is missing an access token. Reconnect the email account and try again.",
        )

    after = _coerce_str(arguments.get("after") or arguments.get("after_at") or arguments.get("afterAt")).strip() or None
    before = _coerce_str(arguments.get("before") or arguments.get("before_at") or arguments.get("beforeAt")).strip() or None
    sender = _coerce_str(arguments.get("from")).strip() or None
    to_value = _coerce_str(arguments.get("to")).strip() or None
    subject = _coerce_str(arguments.get("subject")).strip() or None

    effective_query = query
    try:
        if account.provider == EmailAccountProvider.GOOGLE:
            effective_query = build_gmail_query(
                query=query,
                after=after,
                before=before,
                sender=sender,
                to=to_value,
                subject=subject,
            )
            payload = gmail_search_messages(
                access_token=access_token,
                query=effective_query,
                limit=limit,
                include_snippets_limit=5,
            )
        else:
            payload = graph_search_messages(
                access_token=access_token,
                query=effective_query,
                limit=limit,
                after=after,
                before=before,
            )
    except (GmailApiError, GraphApiError) as exc:
        logger.warning(
            "email.provider_search_failed account=%s provider=%s conversation=%s error=%s",
            getattr(account, "id", None),
            getattr(account, "provider", None),
            getattr(conversation, "id", None),
            str(exc),
        )
        return _email_error(
            "email_search",
            error_code="provider_error",
            hint=f"Email search failed: {str(exc)[:180]}",
        )

    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    return {
        "tool": "email_search",
        "status": "ok",
        "provider": account.provider,
        "email_account_id": str(account.id),
        "query": effective_query,
        "results": results,
        "result_size_estimate": payload.get("result_size_estimate"),
        "next_page_token": payload.get("next_page_token"),
        "hint": "No messages matched that query." if not results else "",
    }


def _email_get_message_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    message_id = _coerce_str(arguments.get("message_id") or arguments.get("messageId")).strip()
    if not message_id:
        return _email_error("email_get_message", error_code="missing_message_id", hint="message_id is required.")

    account, error = _resolve_email_account_for_tool(tool="email_get_message", arguments=arguments, conversation=conversation)
    if error:
        return error
    assert account is not None

    if account.provider not in {EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT}:
        return _email_error(
            "email_get_message",
            error_code="provider_not_supported",
            hint="Email provider is not supported yet.",
        )

    try:
        account = ensure_fresh_email_credentials(account)
    except Exception:
        logger.exception("email.oauth_refresh_failed tool=email_get_message account=%s", getattr(account, "id", None))
        return _email_error(
            "email_get_message",
            error_code="oauth_refresh_failed",
            hint="Email OAuth refresh failed. Reconnect the email account and try again.",
        )

    creds = account.credentials or {}
    access_token = str(creds.get("access_token") or "").strip()
    if not access_token:
        return _email_error(
            "email_get_message",
            error_code="missing_access_token",
            hint="Email account is missing an access token. Reconnect the email account and try again.",
        )

    try:
        if account.provider == EmailAccountProvider.GOOGLE:
            payload = gmail_get_message(access_token=access_token, message_id=message_id)
        else:
            payload = graph_get_message(access_token=access_token, message_id=message_id)
    except (GmailApiError, GraphApiError) as exc:
        logger.warning(
            "email.provider_get_message_failed account=%s provider=%s message=%s error=%s",
            getattr(account, "id", None),
            getattr(account, "provider", None),
            message_id,
            str(exc),
        )
        return _email_error(
            "email_get_message",
            error_code="provider_error",
            hint=f"Email fetch failed: {str(exc)[:180]}",
        )

    return {
        "tool": "email_get_message",
        "status": "ok",
        "provider": account.provider,
        "email_account_id": str(account.id),
        **payload,
    }


def _email_get_thread_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    thread_id = _coerce_str(arguments.get("thread_id") or arguments.get("threadId")).strip()
    if not thread_id:
        return _email_error("email_get_thread", error_code="missing_thread_id", hint="thread_id is required.")

    account, error = _resolve_email_account_for_tool(tool="email_get_thread", arguments=arguments, conversation=conversation)
    if error:
        return error
    assert account is not None

    if account.provider not in {EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT}:
        return _email_error(
            "email_get_thread",
            error_code="provider_not_supported",
            hint="Email provider is not supported yet.",
        )

    try:
        account = ensure_fresh_email_credentials(account)
    except Exception:
        logger.exception("email.oauth_refresh_failed tool=email_get_thread account=%s", getattr(account, "id", None))
        return _email_error(
            "email_get_thread",
            error_code="oauth_refresh_failed",
            hint="Email OAuth refresh failed. Reconnect the email account and try again.",
        )

    creds = account.credentials or {}
    access_token = str(creds.get("access_token") or "").strip()
    if not access_token:
        return _email_error(
            "email_get_thread",
            error_code="missing_access_token",
            hint="Email account is missing an access token. Reconnect the email account and try again.",
        )

    try:
        if account.provider == EmailAccountProvider.GOOGLE:
            payload = gmail_get_thread(access_token=access_token, thread_id=thread_id)
        else:
            payload = graph_get_thread(access_token=access_token, thread_id=thread_id)
    except (GmailApiError, GraphApiError) as exc:
        logger.warning(
            "email.provider_get_thread_failed account=%s provider=%s thread=%s error=%s",
            getattr(account, "id", None),
            getattr(account, "provider", None),
            thread_id,
            str(exc),
        )
        return _email_error(
            "email_get_thread",
            error_code="provider_error",
            hint=f"Email thread fetch failed: {str(exc)[:180]}",
        )

    return {
        "tool": "email_get_thread",
        "status": "ok",
        "provider": account.provider,
        "email_account_id": str(account.id),
        **payload,
    }


def _email_create_draft_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    to_value = arguments.get("to")
    if not isinstance(to_value, list) or not any(str(item or "").strip() for item in to_value):
        return _email_error("email_create_draft", error_code="validation_error", hint="to must be a non-empty array of email addresses.")
    subject = _coerce_str(arguments.get("subject")).strip()
    body_text = _coerce_str(arguments.get("body_text") or arguments.get("bodyText")).strip()
    if not subject or not body_text:
        return _email_error("email_create_draft", error_code="validation_error", hint="subject and body_text are required.")

    account, error = _resolve_email_account_for_tool(tool="email_create_draft", arguments=arguments, conversation=conversation)
    if error:
        return error
    assert account is not None

    if account.provider not in {EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT}:
        return _email_error(
            "email_create_draft",
            error_code="provider_not_supported",
            hint="Email provider is not supported yet.",
        )

    try:
        account = ensure_fresh_email_credentials(account)
    except Exception:
        logger.exception("email.oauth_refresh_failed tool=email_create_draft account=%s", getattr(account, "id", None))
        return _email_error(
            "email_create_draft",
            error_code="oauth_refresh_failed",
            hint="Email OAuth refresh failed. Reconnect the email account and try again.",
        )

    creds = account.credentials or {}
    access_token = str(creds.get("access_token") or "").strip()
    if not access_token:
        return _email_error(
            "email_create_draft",
            error_code="missing_access_token",
            hint="Email account is missing an access token. Reconnect the email account and try again.",
        )

    cc_value = arguments.get("cc")
    bcc_value = arguments.get("bcc")
    cc_list = cc_value if isinstance(cc_value, list) else None
    bcc_list = bcc_value if isinstance(bcc_value, list) else None

    truncated = False
    if len(body_text) > 12_000:
        body_text = body_text[:12_000].rstrip()
        truncated = True

    try:
        to_list = [str(item).strip() for item in to_value if str(item).strip()]
        cc_out = [str(item).strip() for item in (cc_list or []) if str(item).strip()] if cc_list else None
        bcc_out = [str(item).strip() for item in (bcc_list or []) if str(item).strip()] if bcc_list else None
        if account.provider == EmailAccountProvider.GOOGLE:
            payload = gmail_create_draft(
                access_token=access_token,
                to=to_list,
                cc=cc_out,
                bcc=bcc_out,
                subject=subject,
                body_text=body_text,
            )
        else:
            payload = graph_create_draft(
                access_token=access_token,
                to=to_list,
                cc=cc_out,
                bcc=bcc_out,
                subject=subject,
                body_text=body_text,
            )
    except (GmailApiError, GraphApiError) as exc:
        logger.warning(
            "email.provider_create_draft_failed account=%s provider=%s error=%s",
            getattr(account, "id", None),
            getattr(account, "provider", None),
            str(exc),
        )
        return _email_error(
            "email_create_draft",
            error_code="provider_error",
            hint=f"Email draft creation failed: {str(exc)[:180]}",
        )

    return {
        "tool": "email_create_draft",
        "status": "ok",
        "provider": account.provider,
        "email_account_id": str(account.id),
        "body_truncated": truncated,
        **payload,
        "hint": "Draft created. Use email_send_draft to send (approval may be required).",
    }


def _email_send_draft_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    draft_id = _coerce_str(arguments.get("draft_id") or arguments.get("draftId")).strip()
    if not draft_id:
        return _email_error("email_send_draft", error_code="missing_draft_id", hint="draft_id is required.")

    account, error = _resolve_email_account_for_tool(tool="email_send_draft", arguments=arguments, conversation=conversation)
    if error:
        return error
    assert account is not None

    if account.provider not in {EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT}:
        return _email_error(
            "email_send_draft",
            error_code="provider_not_supported",
            hint="Email provider is not supported yet.",
        )

    try:
        account = ensure_fresh_email_credentials(account)
    except Exception:
        logger.exception("email.oauth_refresh_failed tool=email_send_draft account=%s", getattr(account, "id", None))
        return _email_error(
            "email_send_draft",
            error_code="oauth_refresh_failed",
            hint="Email OAuth refresh failed. Reconnect the email account and try again.",
        )

    creds = account.credentials or {}
    access_token = str(creds.get("access_token") or "").strip()
    if not access_token:
        return _email_error(
            "email_send_draft",
            error_code="missing_access_token",
            hint="Email account is missing an access token. Reconnect the email account and try again.",
        )

    try:
        if account.provider == EmailAccountProvider.GOOGLE:
            payload = gmail_send_draft(access_token=access_token, draft_id=draft_id)
        else:
            payload = graph_send_draft(access_token=access_token, draft_id=draft_id)
            payload.setdefault("thread_id", "")
    except (GmailApiError, GraphApiError) as exc:
        logger.warning(
            "email.provider_send_draft_failed account=%s provider=%s draft=%s error=%s",
            getattr(account, "id", None),
            getattr(account, "provider", None),
            draft_id,
            str(exc),
        )
        return _email_error(
            "email_send_draft",
            error_code="provider_error",
            hint=f"Email send failed: {str(exc)[:180]}",
        )

    return {
        "tool": "email_send_draft",
        "status": "ok",
        "provider": account.provider,
        "email_account_id": str(account.id),
        "draft_id": draft_id,
        **payload,
        "hint": "Draft sent.",
    }


def _request_user_input_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    prompt = str(arguments.get("prompt") or arguments.get("question") or "").strip()
    raw_questions = arguments.get("questions")
    questions: list[str] = []
    if isinstance(raw_questions, list):
        for item in raw_questions:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                questions.append(text)
    if not questions and prompt:
        questions = [prompt]
    if not questions:
        return {
            "tool": "request_user_input",
            "status": "error",
            "error_code": "validation_failed",
            "error": "missing_prompt",
            "hint": "Provide prompt or questions for request_user_input.",
        }

    schema = arguments.get("schema")
    schema_payload = dict(schema) if isinstance(schema, Mapping) else {}
    return {
        "tool": "request_user_input",
        "status": "needs_user",
        "questions": questions[:10],
        "schema": schema_payload,
        "hint": "Awaiting user input. Ask the user, then resume after they reply.",
    }


def _create_agent_request_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context

    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
    if not bool(getattr(feature_state, "sub_agents_v1", False)):
        return {
            "tool": "create_agent_request",
            "status": "error",
            "error_code": "feature_disabled",
            "error": "sub_agents_disabled",
            "hint": (
                "Sub-agents are disabled for this business. "
                "Enable the per-business feature flag `sub_agents_v1` or set `SUB_AGENTS_V1_GLOBAL_OVERRIDE=true` "
                "and restart the server."
            ),
        }

    from_agent_profile = getattr(conversation, "agent_profile", None)
    if not from_agent_profile:
        return {
            "tool": "create_agent_request",
            "status": "error",
            "error_code": "missing_agent_profile",
            "error": "missing_agent_profile",
            "hint": "Conversation must be linked to an agent_profile to create agent requests.",
        }

    question = str(arguments.get("question") or arguments.get("body") or "").strip()
    subject = str(arguments.get("subject") or "").strip()
    to_agent_slug = _coerce_str(arguments.get("to_agent_slug") or arguments.get("toAgentSlug")).strip()

    if not question:
        return {
            "tool": "create_agent_request",
            "status": "error",
            "error_code": "validation_failed",
            "error": "missing_question",
            "hint": "Provide question for create_agent_request.",
        }

    if not subject:
        subject = (question[:120].strip() or "Agent request").rstrip()

    def _sanitize_value(value: object, *, depth: int) -> object:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value.strip()[:280]
        if depth >= 1:
            return str(value)[:180]
        if isinstance(value, Mapping):
            payload: dict[str, object] = {}
            for key, inner in list(value.items())[:10]:
                if not isinstance(key, str):
                    continue
                key_norm = key.strip()
                if not key_norm or len(key_norm) > 64:
                    continue
                payload[key_norm] = _sanitize_value(inner, depth=depth + 1)
            return payload
        if isinstance(value, list):
            items: list[object] = []
            for inner in value[:10]:
                items.append(_sanitize_value(inner, depth=depth + 1))
            return items
        return str(value)[:180]

    raw_refs = arguments.get("context_refs") or arguments.get("contextRefs") or []
    context_refs: list[dict[str, object]] = []
    if isinstance(raw_refs, list):
        for item in raw_refs[:20]:
            if not isinstance(item, Mapping):
                continue
            cleaned: dict[str, object] = {}
            for key, value in item.items():
                if not isinstance(key, str):
                    continue
                key_norm = key.strip()
                if not key_norm or len(key_norm) > 64:
                    continue
                cleaned[key_norm] = _sanitize_value(value, depth=0)
            if cleaned:
                context_refs.append(cleaned)

    from apps.accounts.models import AgentProfile
    from apps.conversations.models import AgentRequest, AgentRequestStatus, AgentRun

    to_agent_profile = from_agent_profile
    if to_agent_slug:
        resolved = AgentProfile.objects.filter(
            business_profile_id=conversation.business_profile_id,
            slug=to_agent_slug,
        ).first()
        if not resolved:
            return {
                "tool": "create_agent_request",
                "status": "error",
                "error_code": "recipient_not_found",
                "error": "recipient_not_found",
                "hint": "Recipient agent was not found for to_agent_slug.",
            }
        to_agent_profile = resolved

    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    run_id_raw = str(meta.get("agent_run_id") or meta.get("agentRunId") or "").strip()
    agent_run_id = None
    if run_id_raw:
        try:
            run_uuid = uuid.UUID(run_id_raw)
        except (TypeError, ValueError):
            run_uuid = None
        if run_uuid:
            agent_run_id = (
                AgentRun.objects.filter(id=run_uuid, business_profile_id=conversation.business_profile_id)
                .values_list("id", flat=True)
                .first()
            )

    req = AgentRequest.objects.create(
        business_profile_id=conversation.business_profile_id,
        from_agent_profile_id=from_agent_profile.id,
        to_agent_profile_id=to_agent_profile.id,
        conversation_id=conversation.id,
        agent_run_id=agent_run_id,
        created_by_id=getattr(from_agent_profile, "user_id", None),
        status=AgentRequestStatus.OPEN,
        subject=subject[:240],
        question=question[:6000],
        context_refs=context_refs,
        metadata={
            "source": "mcp_tool",
            "to_agent_slug": to_agent_slug,
        },
    )

    return {
        "tool": "create_agent_request",
        "status": "needs_external",
        "agent_request_id": str(req.id),
        "request": {
            "id": str(req.id),
            "status": req.status,
            "subject": req.subject,
            "from_agent": {"id": str(from_agent_profile.id), "name": from_agent_profile.name, "slug": from_agent_profile.slug},
            "to_agent": {"id": str(to_agent_profile.id), "name": to_agent_profile.name, "slug": to_agent_profile.slug},
        },
        "hint": "Request created. Await response in the agent inbox, then resume.",
    }


def _create_agent_run_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context

    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
    if not bool(getattr(feature_state, "sub_agents_v1", False)):
        return {
            "tool": "create_agent_run",
            "status": "error",
            "error_code": "feature_disabled",
            "error": "sub_agents_disabled",
            "hint": (
                "Sub-agents are disabled for this business. "
                "Enable the per-business feature flag `sub_agents_v1` or set `SUB_AGENTS_V1_GLOBAL_OVERRIDE=true` "
                "and restart the server."
            ),
        }

    convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    convo_source = str(convo_meta.get("source") or "").strip().lower()
    if convo_source == "agent_run" or convo_meta.get("agent_run_id") or convo_meta.get("agentRunId"):
        return {
            "tool": "create_agent_run",
            "status": "error",
            "error_code": "nested_runs_forbidden",
            "error": "nested_runs_forbidden",
            "hint": "Creating background runs from inside another run is not supported in v1.",
        }

    agent_profile = getattr(conversation, "agent_profile", None)
    if not agent_profile:
        return {
            "tool": "create_agent_run",
            "status": "error",
            "error_code": "missing_agent_profile",
            "error": "missing_agent_profile",
            "hint": "Conversation must be linked to an agent_profile to create runs.",
        }

    goal = str(arguments.get("goal") or "").strip()
    if not goal:
        return {
            "tool": "create_agent_run",
            "status": "error",
            "error_code": "validation_failed",
            "error": "missing_goal",
            "hint": "Provide goal for create_agent_run.",
        }

    title = str(arguments.get("title") or "").strip()
    if not title:
        title = (goal[:200].strip() or "Background run").rstrip()

    followup_mode = str(arguments.get("followup_mode") or "").strip().lower() or "handoff"
    if followup_mode not in {"handoff", "supervisor"}:
        followup_mode = "handoff"

    actor_raw = str(convo_meta.get("actor_user_id") or convo_meta.get("actorUserId") or "").strip()
    actor_id: uuid.UUID | None = None
    if actor_raw:
        try:
            actor_id = uuid.UUID(actor_raw)
        except (TypeError, ValueError):
            actor_id = None

    business_owner_id = getattr(getattr(conversation, "business_profile", None), "user_id", None)
    agent_user_id = getattr(agent_profile, "user_id", None)

    if not actor_id:
        return {
            "tool": "create_agent_run",
            "status": "error",
            "error_code": "missing_actor_user",
            "error": "missing_actor_user",
            "hint": "Authenticated actor_user_id is required to start background runs.",
        }

    if actor_id not in {business_owner_id, agent_user_id}:
        return {
            "tool": "create_agent_run",
            "status": "error",
            "error_code": "forbidden",
            "error": "forbidden",
            "hint": "Actor is not permitted to start runs for this business.",
        }

    success_raw = arguments.get("success_criteria")
    success_criteria: list[str] = []
    if isinstance(success_raw, list):
        for item in success_raw[:20]:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                success_criteria.append(text[:280])
    constraints = arguments.get("constraints")
    constraints_payload = dict(constraints) if isinstance(constraints, Mapping) else {}
    output_schema = arguments.get("output_schema")
    output_schema_payload = dict(output_schema) if isinstance(output_schema, Mapping) else {}
    approval = arguments.get("approval")
    approval_payload = dict(approval) if isinstance(approval, Mapping) else {}
    metadata = arguments.get("metadata")
    metadata_payload = dict(metadata) if isinstance(metadata, Mapping) else {}

    delegate_mode_enabled = bool(convo_meta.get("delegate_mode") or convo_meta.get("delegateMode"))
    explicit_delegate = False
    trigger_message_id = ""
    last_customer_body = ""
    try:
        last_customer = (
            conversation.messages.filter(sender="customer")
            .order_by("-sent_at", "-created_at")
            .values("id", "body")
            .first()
        )
    except Exception:  # pragma: no cover - best effort only
        last_customer = None
    if isinstance(last_customer, Mapping):
        trigger_message_id = str(last_customer.get("id") or "").strip()
        last_customer_body = str(last_customer.get("body") or "").strip()
    if last_customer_body:
        needle = last_customer_body.lower()
        tokens = (
            "delegate",
            "delegat",
            "subagent",
            "sub-agent",
            "sub agent",
            "background",
            "in the background",
            "run this in background",
            "offload",
            "hand off",
            "spawn",
        )
        explicit_delegate = any(t in needle for t in tokens)
    delegate_intent = "explicit" if explicit_delegate else "implicit"
    followup_requested = bool(explicit_delegate or delegate_mode_enabled)

    visibility = str(arguments.get("visibility") or "initiator").strip().lower()
    if visibility not in {"initiator", "managers", "workspace"}:
        visibility = "initiator"

    from django.db import transaction
    from django.utils import timezone as django_timezone

    from apps.conversations.models import (
        AgentRun,
        AgentRunEvent,
        AgentRunEventStream,
        AgentRunEventType,
        AgentRunSource,
        AgentRunStatus,
        Conversation,
        ConversationChannel,
    )
    from apps.conversations.run_contracts import normalize_run_spec

    run_spec_snapshot = normalize_run_spec(
        {
            "version": 1,
            "goal": goal[:6000],
            "success_criteria": success_criteria[:10],
            **({"constraints": constraints_payload} if constraints_payload else {}),
            **({"output_schema": output_schema_payload} if output_schema_payload else {}),
            **({"approval": approval_payload} if approval_payload else {}),
            "visibility": visibility,
            "metadata": metadata_payload,
        }
    )

    plan = arguments.get("plan")
    plan_payload = dict(plan) if isinstance(plan, Mapping) else {}

    now = django_timezone.now()
    with transaction.atomic():
        existing_run = None
        if trigger_message_id:
            # Dedup by trigger_message_id AND title to allow multiple distinct runs
            # from the same user message while preventing true duplicates.
            existing_run = (
                AgentRun.objects.filter(
                    business_profile_id=conversation.business_profile_id,
                    conversation_id=conversation.id,
                    created_by_id=actor_id,
                    source=AgentRunSource.CHAT,
                    title=title,  # Different titles = different runs
                )
                .exclude(status__in={AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED})
                .filter(metadata__trigger_message_id=trigger_message_id)
                .order_by("-created_at")
                .first()
            )

        if existing_run is not None:
            next_meta = dict(existing_run.metadata or {}) if isinstance(getattr(existing_run, "metadata", None), dict) else {}
            changed = False
            if trigger_message_id and next_meta.get("trigger_message_id") != trigger_message_id:
                next_meta["trigger_message_id"] = trigger_message_id
                changed = True
            if delegate_intent == "explicit" and next_meta.get("delegate_intent") != "explicit":
                next_meta["delegate_intent"] = "explicit"
                changed = True
            if followup_requested and not bool(next_meta.get("followup_requested")):
                next_meta["followup_requested"] = True
                changed = True
            if next_meta.get("followup_mode") != followup_mode:
                next_meta["followup_mode"] = followup_mode
                changed = True
            if changed:
                AgentRun.objects.filter(id=existing_run.id).update(metadata=next_meta, updated_at=now)
                existing_run.metadata = next_meta
            return {
                "tool": "create_agent_run",
                "status": "ok",
                "run_id": str(existing_run.id),
                "deduped": True,
                "run": {
                    "id": str(existing_run.id),
                    "title": existing_run.title,
                    "status": existing_run.status,
                    "source": existing_run.source,
                    "visibility": existing_run.visibility,
                },
                "hint": "Background run already queued for this message. Watch the Tasks panel for progress.",
            }

        run = AgentRun.objects.create(
            business_profile_id=conversation.business_profile_id,
            agent_profile_id=agent_profile.id,
            conversation_id=conversation.id,
            created_by_id=actor_id,
            run_spec_snapshot=run_spec_snapshot,
            title=title[:200],
            source=AgentRunSource.CHAT,
            status=AgentRunStatus.QUEUED,
            visibility=visibility,
            plan=plan_payload,
            metadata={
                **metadata_payload,
                "source": "mcp_tool",
                "trigger_message_id": trigger_message_id,
                "delegate_intent": delegate_intent,
                "followup_requested": followup_requested,
                "followup_mode": followup_mode,
            },
            run_after=now,
        )
        exec_metadata: dict[str, object] = {
            "source": "agent_run",
            "agent_run_id": str(run.id),
            "anchor_conversation_id": str(conversation.id),
        }
        if actor_id:
            exec_metadata["actor_user_id"] = str(actor_id)
        execution_conversation = Conversation.objects.create(
            business_profile_id=conversation.business_profile_id,
            agent_profile_id=agent_profile.id,
            channel=ConversationChannel.API,
            metadata=exec_metadata,
        )
        run.execution_conversation = execution_conversation
        run.metadata = {
            **(run.metadata or {}),
            "execution_conversation_id": str(execution_conversation.id),
        }
        run.save(update_fields=["execution_conversation", "metadata", "updated_at"])
        AgentRunEvent.objects.create(
            run=run,
            sequence_index=1,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Queued",
            payload={"status": AgentRunStatus.QUEUED},
        )

    return {
        "tool": "create_agent_run",
        "status": "ok",
        "run_id": str(run.id),
        "run": {
            "id": str(run.id),
            "title": run.title,
            "status": run.status,
            "source": run.source,
            "visibility": run.visibility,
        },
        "hint": "Background run queued. Watch the Tasks panel for progress.",
    }


def _list_agent_runs_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """List agent runs for the current conversation."""
    del context

    status_filter = str(arguments.get("status_filter") or "all").strip().lower()
    limit = min(20, max(1, int(arguments.get("limit") or 10)))
    refresh = bool(arguments.get("refresh"))

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return {
            "tool": "list_agent_runs",
            "status": "error",
            "error_code": "missing_business",
            "error": "Conversation missing business_profile_id.",
        }

    # Map status filter to actual statuses
    status_mapping = {
        "all": None,
        "active": [AgentRunStatus.QUEUED, AgentRunStatus.RUNNING],
        "waiting": [AgentRunStatus.WAITING_USER, AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_EXTERNAL, AgentRunStatus.PAUSED],
        "completed": [AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED],
    }
    statuses = status_mapping.get(status_filter)

    try:
        cache_ttl = int(getattr(settings, "MCP_LIST_AGENT_RUNS_CACHE_TTL_SECONDS", 5) or 5)
    except (TypeError, ValueError):
        cache_ttl = 5
    cache_ttl = max(0, min(cache_ttl, 60))
    cache_key = f"mcp:list_agent_runs:{business_id}:{conversation.id}:{status_filter}:{limit}"

    if not refresh and cache_ttl > 0:
        cached = cache.get(cache_key)
        if isinstance(cached, Mapping):
            return dict(cached)

    with tenant_context(business_id):
        qs = AgentRun.objects.filter(conversation_id=conversation.id).order_by("-created_at")
        if statuses:
            qs = qs.filter(status__in=statuses)
        runs = list(qs[:limit])

    run_summaries = []
    for run in runs:
        result_payload = run.result if isinstance(getattr(run, "result", None), dict) else {}
        response_text = str(result_payload.get("response_text") or "").strip()
        # Truncate for summary
        if len(response_text) > 500:
            response_text = response_text[:497] + "..."

        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        pending_approval_id = str(meta.get("pending_approval_id") or "").strip()
        pending_user_input = bool(meta.get("pending_user_input"))

        run_summaries.append({
            "id": str(run.id),
            "title": run.title or "",
            "status": run.status,
            "source": run.source,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "response_preview": response_text or None,
            "waiting_for": (
                "approval" if pending_approval_id else
                "user_input" if pending_user_input else
                None
            ),
        })

    response = {
        "tool": "list_agent_runs",
        "status": "ok",
        "runs": run_summaries,
        "count": len(run_summaries),
        "filter": status_filter,
    }
    if cache_ttl > 0:
        cache.set(cache_key, dict(response), timeout=cache_ttl)
    return response


def _get_agent_run_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """Get detailed status of a specific agent run."""
    del context

    run_id_raw = str(arguments.get("run_id") or "").strip()
    include_events = bool(arguments.get("include_events"))

    if not run_id_raw:
        return {
            "tool": "get_agent_run",
            "status": "error",
            "error_code": "missing_run_id",
            "error": "run_id is required.",
        }

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "get_agent_run",
            "status": "error",
            "error_code": "invalid_run_id",
            "error": "run_id is not a valid UUID.",
        }

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return {
            "tool": "get_agent_run",
            "status": "error",
            "error_code": "missing_business",
            "error": "Conversation missing business_profile_id.",
        }

    with tenant_context(business_id):
        run = AgentRun.objects.filter(
            id=run_uuid,
            conversation_id=conversation.id,
        ).first()

        if not run:
            return {
                "tool": "get_agent_run",
                "status": "error",
                "error_code": "not_found",
                "error": f"Run {run_id_raw} not found in this conversation.",
            }

        result_payload = run.result if isinstance(getattr(run, "result", None), dict) else {}
        response_text = str(result_payload.get("response_text") or "").strip()
        # Allow longer text for detailed view so LLM can use the full result
        if len(response_text) > 8000:
            response_text = response_text[:7997] + "..."

        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        pending_approval_id = str(meta.get("pending_approval_id") or "").strip()
        pending_user_input = meta.get("pending_user_input") if isinstance(meta.get("pending_user_input"), dict) else None

        run_detail: dict[str, object] = {
            "id": str(run.id),
            "title": run.title or "",
            "status": run.status,
            "source": run.source,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "attempt_count": run.attempt_count,
            "error_detail": run.error_detail or None,
            "response_text": response_text or None,
        }

        # Add waiting context if applicable
        if pending_approval_id:
            run_detail["waiting_for"] = "approval"
            run_detail["pending_approval_id"] = pending_approval_id
        elif pending_user_input:
            run_detail["waiting_for"] = "user_input"
            questions = pending_user_input.get("questions") if isinstance(pending_user_input, dict) else []
            if isinstance(questions, list):
                run_detail["pending_questions"] = [str(q)[:200] for q in questions[:5]]

        # Include recent events if requested
        if include_events:
            events = list(
                AgentRunEvent.objects.filter(run_id=run.id)
                .order_by("-sequence_index")[:15]
            )
            run_detail["recent_events"] = [
                {
                    "sequence": event.sequence_index,
                    "stream": event.stream,
                    "type": event.event_type,
                    "label": event.label,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
                for event in reversed(events)
            ]

    return {
        "tool": "get_agent_run",
        "status": "ok",
        "run": run_detail,
    }


def _continue_agent_run_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """Continue an existing agent run with a follow-up message."""
    del context
    from django.utils import timezone
    from apps.conversations.models import ConversationMessage, ConversationSender

    run_id_raw = str(arguments.get("run_id") or "").strip()
    message = str(arguments.get("message") or "").strip()

    if not run_id_raw:
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "missing_run_id",
            "error": "run_id is required.",
        }

    if not message:
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "missing_message",
            "error": "message is required.",
        }

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "invalid_run_id",
            "error": "run_id is not a valid UUID.",
        }

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "missing_business",
            "error": "Conversation missing business_profile_id.",
        }

    # States that can be continued
    continuable_states = {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.WAITING_USER,
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.WAITING_EXTERNAL,
        AgentRunStatus.PAUSED,
    }

    with tenant_context(business_id):
        run = AgentRun.objects.filter(
            id=run_uuid,
            conversation_id=conversation.id,
        ).first()

        if not run:
            return {
                "tool": "continue_agent_run",
                "status": "error",
                "error_code": "not_found",
                "error": f"Run {run_id_raw} not found in this conversation.",
            }

        if run.status not in continuable_states:
            return {
                "tool": "continue_agent_run",
                "status": "error",
                "error_code": "not_continuable",
                "error": f"Run is currently '{run.status}' and cannot be continued. Wait for it to complete or pause.",
                "hint": "Runs can only be continued when completed, failed, waiting, or paused.",
            }

        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        next_spec_snapshot = None

        execution_conversation = None
        if run.execution_conversation_id:
            execution_conversation = Conversation.objects.filter(
                id=run.execution_conversation_id,
                business_profile_id=business_id,
            ).first()

        if execution_conversation is None:
            return {
                "tool": "continue_agent_run",
                "status": "error",
                "error_code": "no_execution_conversation",
                "error": "Run has no execution conversation to continue. The run may not have started yet.",
                "hint": "Wait for the run to start processing before continuing it.",
            }

        now = timezone.now()

        # Append the follow-up message to the execution conversation
        ConversationMessage.objects.create(
            conversation=execution_conversation,
            sender=ConversationSender.CUSTOMER,
            body=message,
            metadata={
                "source": "agent_run_continuation",
                "agent_run_id": str(run.id),
                "from_conversation_id": str(conversation.id),
                "type": "orchestrator_followup",
            },
        )
        Conversation.objects.filter(id=execution_conversation.id).update(last_activity_at=now)

        # Update run metadata to track continuation
        next_meta = dict(meta)
        continuations = next_meta.get("continuations") or []
        if not isinstance(continuations, list):
            continuations = []
        continuations.append({
            "at": now.isoformat(),
            "from_status": run.status,
            "message_preview": message[:200],
        })
        next_meta["continuations"] = continuations[-10:]  # Keep last 10
        next_meta.pop("pending_approval_id", None)
        next_meta.pop("pending_user_input", None)
        next_meta.pop("pending_tool_call", None)

        # Re-queue the run
        update_fields = {
            "status": AgentRunStatus.QUEUED,
            "run_after": now,
            "lease_expires_at": None,
            "finished_at": None,
            "error_detail": "",
            "metadata": next_meta,
            "updated_at": now,
        }
        if next_spec_snapshot is not None:
            update_fields["run_spec_snapshot"] = next_spec_snapshot

        AgentRun.objects.filter(id=run.id).update(**update_fields)

        # Log the continuation event
        from apps.conversations.models import AgentRunEventStream, AgentRunEventType
        from django.db.models import Max

        next_index = (
            AgentRunEvent.objects.filter(run_id=run.id).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
        )
        AgentRunEvent.objects.create(
            run_id=run.id,
            sequence_index=int(next_index) + 1,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Continued by orchestrator",
            payload={
                "from_status": run.status,
                "message_preview": message[:200],
                "from_conversation_id": str(conversation.id),
            },
        )

    return {
        "tool": "continue_agent_run",
        "status": "ok",
        "run_id": str(run.id),
        "previous_status": run.status,
        "new_status": AgentRunStatus.QUEUED,
        "message_appended": True,
        "hint": "Run re-queued with your follow-up message. It will continue with full conversation history.",
    }


def _retrieve_earlier_context_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext | None = None,
) -> Mapping[str, object]:
    del context
    start = time.perf_counter()

    query = str(arguments.get("query") or "").strip()
    segment_id_raw = str(arguments.get("segment_id") or "").strip()
    timeframe = str(arguments.get("timeframe") or "all").strip().lower()
    include_full_segment = bool(arguments.get("include_full_segment"))
    try:
        max_messages = int(arguments.get("max_messages") or (50 if include_full_segment else 12))
    except (TypeError, ValueError):
        max_messages = 50 if include_full_segment else 12
    max_messages = max(1, min(50, max_messages))

    def _log_performance(
        payload: Mapping[str, object],
        *,
        segments_available: int | None = None,
        semantic_enabled: bool | None = None,
        embeddings_backfilled: int | None = None,
        embeddings_backfill_attempted: int | None = None,
        ranked_candidates: int | None = None,
    ) -> None:
        duration_ms = int((time.perf_counter() - start) * 1000.0)
        warn_ms = int(getattr(settings, "MCP_SLO_RETRIEVE_EARLIER_CONTEXT_WARN_MS", 1200) or 0)
        slow = bool(warn_ms and duration_ms >= warn_ms)
        status_value = str(payload.get("status") or "").strip().lower() or "ok"
        detail: dict[str, object] = {
            "status": payload.get("status"),
            "found": payload.get("found"),
            "match_method": payload.get("match_method"),
            "segment_id": payload.get("segment_id"),
            "vector_distance": payload.get("vector_distance"),
            "duration_ms": duration_ms,
            "timeframe": timeframe,
            "include_full_segment": include_full_segment,
            "max_messages": max_messages,
            "query_chars": len(query),
            "segment_id_provided": bool(segment_id_raw),
            "segments_available": segments_available,
            "semantic_enabled": semantic_enabled,
            "ranked_candidates": ranked_candidates,
            "embeddings_backfilled": embeddings_backfilled,
            "embeddings_backfill_attempted": embeddings_backfill_attempted,
        }
        try:
            messages = payload.get("messages")
            if isinstance(messages, (list, tuple)):
                detail["messages_returned"] = len(messages)
        except Exception:
            pass
        if slow:
            detail["slo"] = "slow"
            detail["slo_warn_ms"] = warn_ms
        structured_log(
            "mcp",
            "retrieve_earlier_context.performance",
            detail,
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            level=logging.WARNING if slow or status_value in {"error"} else logging.INFO,
        )

    if segment_id_raw:
        try:
            seg_uuid = uuid.UUID(segment_id_raw)
        except (TypeError, ValueError):
            payload = {
                "tool": "retrieve_earlier_context",
                "status": "error",
                "error": "invalid_segment_id",
                "hint": "segment_id must be a valid UUID.",
            }
            _log_performance(payload, semantic_enabled=False)
            return payload
        segment = conversation.compacted_segments.filter(id=seg_uuid).first()
        if not segment:
            payload = {
                "tool": "retrieve_earlier_context",
                "status": "ok",
                "found": False,
                "message": "Compacted segment not found for this conversation.",
                "segment_id": segment_id_raw,
            }
            _log_performance(payload, semantic_enabled=False)
            return payload

        # Enforce tenant maximum retention even for direct segment fetches.
        business_profile = getattr(conversation, "business_profile", None)
        max_retention_days = None
        if business_profile is not None:
            try:
                config = business_profile.memory_config
            except Exception:
                config = None
            if config and config.maximum_retention_days is not None:
                try:
                    max_retention_days = int(config.maximum_retention_days)
                except (TypeError, ValueError):
                    max_retention_days = None
        if max_retention_days and max_retention_days > 0:
            from django.utils import timezone as django_timezone

            cutoff = django_timezone.now() - timedelta(days=max_retention_days)
            segment_end = getattr(segment, "end_message_sent_at", None) or getattr(segment, "compacted_at", None)
            if segment_end is not None and segment_end < cutoff:
                payload = {
                    "tool": "retrieve_earlier_context",
                    "status": "ok",
                    "found": False,
                    "message": "Compacted segment is outside this tenant's retention window.",
                    "segment_id": segment_id_raw,
                }
                _log_performance(payload, semantic_enabled=False)
                return payload

        messages_out = list(segment.full_messages or [])
        if len(messages_out) > max_messages:
            messages_out = messages_out[:max_messages]
        payload = {
            "tool": "retrieve_earlier_context",
            "status": "ok",
            "found": True,
            "match_method": "direct",
            "segment_id": str(segment.id),
            "segment": str(segment.segment_range or ""),
            "summary": str(segment.summary or ""),
            "facts": segment.extracted_facts if isinstance(segment.extracted_facts, Mapping) else {},
            "decisions": segment.extracted_decisions if isinstance(segment.extracted_decisions, Mapping) else {},
            "messages": messages_out if include_full_segment else [],
        }
        _log_performance(payload, semantic_enabled=False)
        return payload

    if not query:
        payload = {
            "tool": "retrieve_earlier_context",
            "status": "error",
            "error": "missing_query",
            "hint": "Provide a query string (or segment_id) to search compacted history.",
        }
        _log_performance(payload, semantic_enabled=False)
        return payload

    business_profile = getattr(conversation, "business_profile", None)
    max_retention_days = None
    if business_profile is not None:
        try:
            config = business_profile.memory_config
        except Exception:
            config = None
        if config and config.maximum_retention_days is not None:
            try:
                max_retention_days = int(config.maximum_retention_days)
            except (TypeError, ValueError):
                max_retention_days = None

    base_qs = conversation.compacted_segments.all()
    if max_retention_days and max_retention_days > 0:
        from django.db.models import Q as DjangoQ
        from django.utils import timezone as django_timezone

        cutoff = django_timezone.now() - timedelta(days=max_retention_days)
        # Retention is based on the underlying message timestamps, not the compaction timestamp.
        # Fallback to compacted_at for legacy rows that haven't been backfilled yet.
        base_qs = base_qs.filter(
            DjangoQ(end_message_sent_at__gte=cutoff)
            | DjangoQ(end_message_sent_at__isnull=True, compacted_at__gte=cutoff)
        )

    # Apply coarse timeframe narrowing (fine-grained turn-range filtering happens after ranking).
    if timeframe in {"recent"}:
        base_qs = base_qs.order_by("-end_message_sent_at", "-compacted_at")[:15]
    elif timeframe in {"oldest"}:
        base_qs = base_qs.order_by("end_message_sent_at", "compacted_at")[:15]

    segments = list(base_qs)
    if not segments:
        payload = {
            "tool": "retrieve_earlier_context",
            "status": "ok",
            "found": False,
            "message": "No compacted history segments are available yet.",
            "query": query,
        }
        _log_performance(payload, segments_available=0, semantic_enabled=False)
        return payload

    def _parse_range(label: str) -> tuple[int, int] | None:
        match = re.match(r"^turns_(\\d+)_to_(\\d+)$", str(label or "").strip().lower())
        if not match:
            return None
        try:
            start = int(match.group(1))
            end = int(match.group(2))
        except (TypeError, ValueError):
            return None
        if start <= 0 or end <= 0:
            return None
        if end < start:
            start, end = end, start
        return (start, end)

    def _overlaps_turn_range(segment_range: str, start: int, end: int) -> bool:
        parsed = _parse_range(segment_range)
        if not parsed:
            return False
        seg_start, seg_end = parsed
        return not (seg_end < start or seg_start > end)

    desired_turn_range = None
    if timeframe == "first_10_turns":
        desired_turn_range = (1, 10)
    elif timeframe == "turns_10_to_20":
        desired_turn_range = (10, 20)
    if desired_turn_range:
        start, end = desired_turn_range
        range_filtered = [s for s in segments if _overlaps_turn_range(str(s.segment_range or ""), start, end)]
        if range_filtered:
            segments = range_filtered

    query_tokens = {token for token in re.split(r"\\W+", query.lower()) if token}

    def _score_text(text: str) -> int:
        if not text or not query_tokens:
            return 0
        lowered = text.lower()
        score = 0
        for token in query_tokens:
            if token and token in lowered:
                score += 1
        return score

    best_segment = None
    match_method = "lexical"
    vector_distance = None

    embedder = _portal_file_embedding_service()
    query_vector: list[float] | None = None
    if embedder:
        try:
            query_vector = embedder.embed_text(query)
        except Exception:
            query_vector = None

    semantic_enabled = bool(query_vector)
    embeddings_backfill_attempted = 0
    embeddings_backfilled = 0
    ranked_candidates = None

    if query_vector:
        # Opportunistic backfill: ensure recent segments have embeddings.
        expected_dim = int(getattr(settings, "EMBED_DIM", 384) or 384)
        missing = [s for s in segments if getattr(s, "embedding", None) is None and str(getattr(s, "summary", "") or "").strip()]
        for seg in missing[:10]:
            embeddings_backfill_attempted += 1
            try:
                vec = embedder.embed_text(str(seg.summary or "").strip())
            except Exception:
                continue
            if vec and len(vec) == expected_dim:
                try:
                    updated = seg.__class__.objects.filter(id=seg.id).update(embedding=vec)
                    if updated:
                        embeddings_backfilled += 1
                except Exception:
                    continue

        try:
            from pgvector.django import CosineDistance
        except Exception:
            query_vector = None
        else:
            ann_limit = max(20, min(80, len(segments) * 5))
            seg_ids = [s.id for s in segments]
            ranked = (
                conversation.compacted_segments.filter(id__in=seg_ids)
                .exclude(embedding__isnull=True)
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance", "id")[:ann_limit]
            )
            ranked_list = list(ranked)
            ranked_candidates = len(ranked_list)
            if ranked_list:
                best_segment = ranked_list[0]
                match_method = "semantic"
                try:
                    vector_distance = float(getattr(best_segment, "distance", None) or 0.0)
                except (TypeError, ValueError):
                    vector_distance = None

    if best_segment is None:
        best_score = 0
        for segment in segments:
            score = _score_text(str(segment.summary or ""))
            if isinstance(segment.extracted_facts, Mapping):
                score += _score_text(json.dumps(segment.extracted_facts, ensure_ascii=False))
            if isinstance(segment.extracted_decisions, Mapping):
                score += _score_text(json.dumps(segment.extracted_decisions, ensure_ascii=False))
            if score > best_score:
                best_score = score
                best_segment = segment
        if not best_segment or best_score == 0:
            payload = {
                "tool": "retrieve_earlier_context",
                "status": "ok",
                "found": False,
                "message": "No matching compacted history found for that query.",
                "query": query,
            }
            _log_performance(
                payload,
                segments_available=len(segments),
                semantic_enabled=semantic_enabled,
                embeddings_backfilled=embeddings_backfilled,
                embeddings_backfill_attempted=embeddings_backfill_attempted,
                ranked_candidates=ranked_candidates,
            )
            return payload

    # Message selection
    if include_full_segment:
        messages_out = list(best_segment.full_messages or [])
        if len(messages_out) > max_messages:
            messages_out = messages_out[:max_messages]
    else:
        matched_messages: list[dict[str, object]] = []
        for msg in best_segment.full_messages or []:
            if not isinstance(msg, Mapping):
                continue
            body = str(msg.get("body") or "")
            meta = msg.get("metadata") if isinstance(msg.get("metadata"), Mapping) else {}
            block_text = ""
            blocks = msg.get("content_blocks")
            if isinstance(blocks, list):
                block_text = json.dumps(blocks, ensure_ascii=False)
            if _score_text(body) > 0 or _score_text(block_text) > 0 or _score_text(json.dumps(meta, ensure_ascii=False)) > 0:
                matched_messages.append(
                    {
                        "id": str(msg.get("id") or ""),
                        "sender": msg.get("sender"),
                        "body": body[:2400],
                        "metadata": meta,
                        "sent_at": msg.get("sent_at"),
                    }
                )
            if len(matched_messages) >= max_messages:
                break
        messages_out = matched_messages

    payload = {
        "tool": "retrieve_earlier_context",
        "status": "ok",
        "found": True,
        "match_method": match_method,
        "query": query,
        "segment_id": str(best_segment.id),
        "segment": str(best_segment.segment_range or ""),
        "summary": str(best_segment.summary or ""),
        "vector_distance": vector_distance,
        "facts": best_segment.extracted_facts if isinstance(best_segment.extracted_facts, Mapping) else {},
        "decisions": best_segment.extracted_decisions if isinstance(best_segment.extracted_decisions, Mapping) else {},
        "messages": messages_out,
    }
    _log_performance(
        payload,
        segments_available=len(segments),
        semantic_enabled=semantic_enabled,
        embeddings_backfilled=embeddings_backfilled,
        embeddings_backfill_attempted=embeddings_backfill_attempted,
        ranked_candidates=ranked_candidates,
    )
    return payload

# ═══════════════════════════════════════════════════════════════════════════════
# Native Integration tool helpers and handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _integration_error(tool: str, *, error_code: str, hint: str) -> Mapping[str, object]:
    return {
        "tool": tool,
        "status": "error",
        "error": error_code,
        "error_code": error_code,
        "hint": hint,
    }


def _conversation_actor_user_uuid(conversation: Conversation) -> uuid.UUID | None:
    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    actor_user_id = None
    if isinstance(meta, Mapping):
        actor_user_id = meta.get("actor_user_id") or meta.get("actorUserId") or meta.get("user_id") or meta.get("userId")
    if not actor_user_id:
        return None
    try:
        return uuid.UUID(str(actor_user_id))
    except (TypeError, ValueError):
        return None


def _resolve_integration_account_for_tool(
    *,
    integration_type: str,
    tool: str,
    arguments: Mapping[str, object],
    conversation: Conversation,
    requires_actor_user_binding: bool = True,
) -> tuple[IntegrationAccount | None, Mapping[str, object] | None]:
    """
    Resolve the IntegrationAccount to use for a tool call.

    Preferred: explicit integration_account_id argument.
    Fallback: conversation metadata actor_user_id.
    """

    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    business_id = getattr(conversation, "business_profile_id", None)
    raw_account_id = _coerce_str(arguments.get("integration_account_id") or arguments.get("integrationAccountId")).strip()
    if raw_account_id:
        try:
            account_uuid = uuid.UUID(raw_account_id)
        except (TypeError, ValueError):
            return None, _integration_error(tool, error_code="validation_error", hint="integration_account_id must be a valid UUID.")
        account = IntegrationAccount.objects.filter(
            id=account_uuid,
            integration_type=integration_type,
            business_profile_id=business_id,
        ).first()
        if not account:
            return None, _integration_error(
                tool,
                error_code="not_connected",
                hint=f"{integration_type} is not connected for this workspace/user.",
            )
        if account.status != IntegrationAccountStatus.CONNECTED:
            return None, _integration_error(
                tool,
                error_code="not_connected",
                hint=f"{integration_type} is not connected for this workspace/user.",
            )
        if actor_user_uuid and account.user_id != actor_user_uuid:
            return None, _integration_error(
                tool,
                error_code="account_mismatch",
                hint="Connected account belongs to a different user in this workspace.",
            )
        if requires_actor_user_binding and not actor_user_uuid:
            return None, _integration_error(
                tool,
                error_code="account_mismatch",
                hint="Missing actor-user binding for this integration tool call.",
            )
        if not is_native_tool_enabled_for_account(account=account, tool_name=tool):
            return None, _integration_error(
                tool,
                error_code="tool_disabled",
                hint="This integration tool is disabled in Integrations settings.",
            )
        return account, None

    if actor_user_uuid:
        account = (
            IntegrationAccount.objects.filter(
                business_profile_id=business_id,
                user_id=actor_user_uuid,
                integration_type=integration_type,
                status=IntegrationAccountStatus.CONNECTED,
            )
            .order_by("-updated_at")
            .first()
        )
        if account:
            if not is_native_tool_enabled_for_account(account=account, tool_name=tool):
                return None, _integration_error(
                    tool,
                    error_code="tool_disabled",
                    hint="This integration tool is disabled in Integrations settings.",
                )
            return account, None
        return None, _integration_error(
            tool,
            error_code="not_connected",
            hint=f"No connected {integration_type} account found for this user. Connect it from Integrations.",
        )

    if requires_actor_user_binding:
        return None, _integration_error(
            tool,
            error_code="account_mismatch",
            hint="Missing actor-user binding for this integration tool call.",
        )

    return None, _integration_error(
        tool,
        error_code="not_connected",
        hint=f"No connected {integration_type} account found. Connect it from Integrations.",
    )


def _get_integration_access_token(account: IntegrationAccount, tool: str) -> tuple[str | None, Mapping[str, object] | None]:
    """Refresh credentials and extract access_token. Returns (token, error)."""
    try:
        refreshed = ensure_fresh_integration_credentials(account)
    except Exception:
        logger.exception("integration.oauth_refresh_failed tool=%s account=%s", tool, getattr(account, "id", None))
        return None, _integration_error(
            tool,
            error_code="token_expired",
            hint="Integration OAuth refresh failed. Reconnect the integration and try again.",
        )
    creds = refreshed.credentials or {}
    access_token = str(creds.get("access_token") or "").strip()
    if not access_token:
        return None, _integration_error(
            tool,
            error_code="token_expired",
            hint="Integration account is missing an access token. Reconnect and try again.",
        )
    return access_token, None


# ── Google Calendar handlers ──

def _calendar_list_events_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.GOOGLE_CALENDAR, tool="calendar_list_events",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "calendar_list_events")
    if token_error:
        return token_error
    assert access_token is not None

    time_min = _coerce_str(arguments.get("time_min")).strip() or None
    time_max = _coerce_str(arguments.get("time_max")).strip() or None
    query = _coerce_str(arguments.get("query")).strip() or None
    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = calendar_list_events(
            access_token, time_min=time_min, time_max=time_max,
            query=query, max_results=max_results,
        )
    except CalendarApiError as exc:
        return _integration_error("calendar_list_events", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "calendar_list_events", "status": "ok", "integration_account_id": str(account.id), **payload}


def _calendar_get_event_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    event_id = _coerce_str(arguments.get("event_id")).strip()
    if not event_id:
        return _integration_error("calendar_get_event", error_code="missing_event_id", hint="event_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.GOOGLE_CALENDAR, tool="calendar_get_event",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "calendar_get_event")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = calendar_get_event(access_token, event_id)
    except CalendarApiError as exc:
        return _integration_error("calendar_get_event", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "calendar_get_event", "status": "ok", "integration_account_id": str(account.id), **payload}


def _calendar_create_event_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    summary = _coerce_str(arguments.get("summary")).strip()
    start_time = _coerce_str(arguments.get("start_time")).strip()
    end_time = _coerce_str(arguments.get("end_time")).strip()
    if not summary or not start_time or not end_time:
        return _integration_error("calendar_create_event", error_code="validation_error", hint="summary, start_time, and end_time are required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.GOOGLE_CALENDAR, tool="calendar_create_event",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "calendar_create_event")
    if token_error:
        return token_error
    assert access_token is not None

    description = _coerce_str(arguments.get("description")).strip()
    location = _coerce_str(arguments.get("location")).strip()
    raw_attendees = arguments.get("attendees")
    attendees = list(raw_attendees) if isinstance(raw_attendees, (list, tuple)) else None

    try:
        payload = calendar_create_event(
            access_token, summary=summary, start_time=start_time, end_time=end_time,
            description=description, attendees=attendees, location=location,
        )
    except CalendarApiError as exc:
        return _integration_error("calendar_create_event", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "calendar_create_event", "status": "ok", "integration_account_id": str(account.id), **payload}


def _calendar_update_event_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    event_id = _coerce_str(arguments.get("event_id")).strip()
    if not event_id:
        return _integration_error("calendar_update_event", error_code="missing_event_id", hint="event_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.GOOGLE_CALENDAR, tool="calendar_update_event",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "calendar_update_event")
    if token_error:
        return token_error
    assert access_token is not None

    updates: dict[str, Any] = {}
    for key in ("summary", "start_time", "end_time", "description", "location"):
        val = _coerce_str(arguments.get(key)).strip()
        if val:
            updates[key] = val

    try:
        payload = calendar_update_event(access_token, event_id, updates=updates)
    except CalendarApiError as exc:
        return _integration_error("calendar_update_event", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "calendar_update_event", "status": "ok", "integration_account_id": str(account.id), **payload}


# ── Google Drive handlers ──

def _drive_search_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("drive_search_files", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.GOOGLE_DRIVE, tool="drive_search_files",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "drive_search_files")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = drive_search_files(access_token, query=query, max_results=max_results)
    except DriveApiError as exc:
        return _integration_error("drive_search_files", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "drive_search_files", "status": "ok", "integration_account_id": str(account.id), **payload}


def _drive_get_file_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_id = _coerce_str(arguments.get("file_id")).strip()
    if not file_id:
        return _integration_error("drive_get_file", error_code="missing_file_id", hint="file_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.GOOGLE_DRIVE, tool="drive_get_file",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "drive_get_file")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = drive_get_file_content(access_token, file_id)
    except DriveApiError as exc:
        return _integration_error("drive_get_file", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "drive_get_file", "status": "ok", "integration_account_id": str(account.id), **payload}


def _drive_list_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.GOOGLE_DRIVE, tool="drive_list_files",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "drive_list_files")
    if token_error:
        return token_error
    assert access_token is not None

    folder_id = _coerce_str(arguments.get("folder_id")).strip() or None
    try:
        max_results = int(arguments.get("max_results") or 20)
    except (TypeError, ValueError):
        max_results = 20

    try:
        payload = drive_list_files(access_token, folder_id=folder_id, max_results=max_results)
    except DriveApiError as exc:
        return _integration_error("drive_list_files", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "drive_list_files", "status": "ok", "integration_account_id": str(account.id), **payload}


# ── OneDrive handlers ──

def _onedrive_search_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("onedrive_search_files", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.ONEDRIVE, tool="onedrive_search_files",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "onedrive_search_files")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = onedrive_search_files(access_token, query=query, max_results=max_results)
    except OneDriveApiError as exc:
        return _integration_error("onedrive_search_files", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "onedrive_search_files", "status": "ok", "integration_account_id": str(account.id), **payload}


def _onedrive_get_file_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_id = _coerce_str(arguments.get("file_id")).strip()
    if not file_id:
        return _integration_error("onedrive_get_file", error_code="missing_file_id", hint="file_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.ONEDRIVE, tool="onedrive_get_file",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "onedrive_get_file")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = onedrive_get_file_content(access_token, file_id)
    except OneDriveApiError as exc:
        return _integration_error("onedrive_get_file", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "onedrive_get_file", "status": "ok", "integration_account_id": str(account.id), **payload}


def _onedrive_list_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.ONEDRIVE, tool="onedrive_list_files",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "onedrive_list_files")
    if token_error:
        return token_error
    assert access_token is not None

    folder_id = _coerce_str(arguments.get("folder_id")).strip() or None
    try:
        max_results = int(arguments.get("max_results") or 20)
    except (TypeError, ValueError):
        max_results = 20

    try:
        payload = onedrive_list_files(access_token, folder_id=folder_id, max_results=max_results)
    except OneDriveApiError as exc:
        return _integration_error("onedrive_list_files", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "onedrive_list_files", "status": "ok", "integration_account_id": str(account.id), **payload}


# ── Slack handlers ──

def _slack_list_channels_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.SLACK, tool="slack_list_channels",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "slack_list_channels")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 20)
    except (TypeError, ValueError):
        max_results = 20

    try:
        payload = slack_list_channels(access_token, max_results=max_results)
    except SlackApiError as exc:
        return _integration_error("slack_list_channels", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "slack_list_channels", "status": "ok", "integration_account_id": str(account.id), **payload}


def _slack_read_channel_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    channel_id = _coerce_str(arguments.get("channel_id")).strip()
    if not channel_id:
        return _integration_error("slack_read_channel", error_code="missing_channel_id", hint="channel_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.SLACK, tool="slack_read_channel",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "slack_read_channel")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        limit = int(arguments.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20

    try:
        payload = slack_read_channel(access_token, channel_id, limit=limit)
    except SlackApiError as exc:
        return _integration_error("slack_read_channel", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "slack_read_channel", "status": "ok", "integration_account_id": str(account.id), **payload}


def _slack_send_message_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    channel_id = _coerce_str(arguments.get("channel_id")).strip()
    text = _coerce_str(arguments.get("text")).strip()
    if not channel_id or not text:
        return _integration_error("slack_send_message", error_code="validation_error", hint="channel_id and text are required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.SLACK, tool="slack_send_message",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "slack_send_message")
    if token_error:
        return token_error
    assert access_token is not None

    thread_ts = _coerce_str(arguments.get("thread_ts")).strip() or None

    try:
        payload = slack_send_message(access_token, channel_id=channel_id, text=text, thread_ts=thread_ts)
    except SlackApiError as exc:
        return _integration_error("slack_send_message", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "slack_send_message", "status": "ok", "integration_account_id": str(account.id), **payload}


def _slack_search_messages_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("slack_search_messages", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.SLACK, tool="slack_search_messages",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "slack_search_messages")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = slack_search_messages(access_token, query=query, max_results=max_results)
    except SlackApiError as exc:
        return _integration_error("slack_search_messages", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "slack_search_messages", "status": "ok", "integration_account_id": str(account.id), **payload}


# ── HubSpot handlers ──

def _hubspot_search_contacts_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("hubspot_search_contacts", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_search_contacts",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_search_contacts")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = hubspot_search_contacts(access_token, query=query, max_results=max_results)
    except HubSpotApiError as exc:
        return _integration_error("hubspot_search_contacts", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_search_contacts", "status": "ok", "integration_account_id": str(account.id), **payload}


def _hubspot_get_contact_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    contact_id = _coerce_str(arguments.get("contact_id")).strip()
    if not contact_id:
        return _integration_error("hubspot_get_contact", error_code="missing_contact_id", hint="contact_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_get_contact",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_get_contact")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = hubspot_get_contact(access_token, contact_id)
    except HubSpotApiError as exc:
        return _integration_error("hubspot_get_contact", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_get_contact", "status": "ok", "integration_account_id": str(account.id), **payload}


def _hubspot_create_contact_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    email = _coerce_str(arguments.get("email")).strip()
    if not email:
        return _integration_error("hubspot_create_contact", error_code="validation_error", hint="email is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_create_contact",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_create_contact")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = hubspot_create_contact(
            access_token,
            email=email,
            first_name=_coerce_str(arguments.get("first_name")).strip(),
            last_name=_coerce_str(arguments.get("last_name")).strip(),
            phone=_coerce_str(arguments.get("phone")).strip(),
            company=_coerce_str(arguments.get("company")).strip(),
            job_title=_coerce_str(arguments.get("job_title")).strip(),
        )
    except HubSpotApiError as exc:
        return _integration_error("hubspot_create_contact", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_create_contact", "status": "ok", "integration_account_id": str(account.id), **payload}


def _hubspot_search_deals_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("hubspot_search_deals", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_search_deals",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_search_deals")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = hubspot_search_deals(access_token, query=query, max_results=max_results)
    except HubSpotApiError as exc:
        return _integration_error("hubspot_search_deals", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_search_deals", "status": "ok", "integration_account_id": str(account.id), **payload}


# Voice tools live in the voice app to keep this registry focused.
try:  # pragma: no cover - optional feature gate
    from apps.voice.mcp_tools import initiate_phone_call_tool as _initiate_phone_call_handler
except Exception:  # pragma: no cover - voice feature may be disabled in some deployments
    _initiate_phone_call_handler = None  # type: ignore[assignment]


_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "retrieve_earlier_context": _retrieve_earlier_context_handler,
    "mcp_search_tools": _mcp_search_tools_handler,
    "mcp_call_tool": _mcp_call_tool_handler,
    "request_user_input": _request_user_input_handler,
    "create_agent_request": _create_agent_request_handler,
    "create_agent_run": _create_agent_run_handler,
    "list_agent_runs": _list_agent_runs_handler,
    "get_agent_run": _get_agent_run_handler,
    "continue_agent_run": _continue_agent_run_handler,
    "search_knowledge": _search_knowledge_handler,
    "search_conversation_files": _search_conversation_files_handler,
    "read_knowledge": _read_knowledge_handler,
    "read_conversation_file": _read_conversation_file_handler,
    "pdf_generate": _pdf_generate_handler,
    "pdf_merge": _pdf_merge_handler,
    "pdf_extract_pages": _pdf_extract_pages_handler,
    "pdf_extract_text": _pdf_extract_text_handler,
    "create_case": _create_case_handler,
    "update_case_status": _update_case_status_handler,
    "update_case_details": _update_case_details_handler,
    "add_case_history": _add_case_history_handler,
    "flag_escalation": _flag_escalation_handler,
    "create_customer": _create_customer_handler,
    "update_customer": _update_customer_handler,
    "create_lead": _create_lead_handler,
    "create_appointment": _create_appointment_handler,
    "email_search": _email_search_handler,
    "email_get_message": _email_get_message_handler,
    "email_get_thread": _email_get_thread_handler,
    "email_create_draft": _email_create_draft_handler,
    "email_send_draft": _email_send_draft_handler,
    # Native integrations — Google Calendar
    "calendar_list_events": _calendar_list_events_handler,
    "calendar_get_event": _calendar_get_event_handler,
    "calendar_create_event": _calendar_create_event_handler,
    "calendar_update_event": _calendar_update_event_handler,
    # Native integrations — Google Drive
    "drive_search_files": _drive_search_files_handler,
    "drive_get_file": _drive_get_file_handler,
    "drive_list_files": _drive_list_files_handler,
    # Native integrations — OneDrive
    "onedrive_search_files": _onedrive_search_files_handler,
    "onedrive_get_file": _onedrive_get_file_handler,
    "onedrive_list_files": _onedrive_list_files_handler,
    # Native integrations — Slack
    "slack_list_channels": _slack_list_channels_handler,
    "slack_read_channel": _slack_read_channel_handler,
    "slack_send_message": _slack_send_message_handler,
    "slack_search_messages": _slack_search_messages_handler,
    # Native integrations — HubSpot
    "hubspot_search_contacts": _hubspot_search_contacts_handler,
    "hubspot_get_contact": _hubspot_get_contact_handler,
    "hubspot_create_contact": _hubspot_create_contact_handler,
    "hubspot_search_deals": _hubspot_search_deals_handler,
    **({"initiate_phone_call": _initiate_phone_call_handler} if _initiate_phone_call_handler else {}),
}
