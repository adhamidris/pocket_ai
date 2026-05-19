"""
Native integration MCP tool helpers and handlers.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from apps.accounts.models import IntegrationAccountStatus, IntegrationType
from apps.conversations.models import Conversation
from apps.integrations.google_calendar_api import (
    CalendarApiError,
    calendar_create_event,
    calendar_get_event,
    calendar_list_events,
    calendar_update_event,
)
from apps.integrations.google_drive_native_api import (
    DriveApiError,
    drive_get_file_content,
    drive_list_files,
    drive_search_files,
)
from apps.integrations.hubspot_api import (
    HubSpotApiError,
    hubspot_create_contact,
    hubspot_get_contact,
    hubspot_search_contacts,
    hubspot_search_deals,
)
from apps.integrations.integration_accounts import ensure_fresh_integration_credentials
from apps.integrations.microsoft_onedrive_api import (
    OneDriveApiError,
    onedrive_get_file_content,
    onedrive_list_files,
    onedrive_search_files,
)
from apps.integrations.models import IntegrationAccount
from apps.integrations.slack_api import (
    SlackApiError,
    slack_list_channels,
    slack_read_channel,
    slack_search_messages,
    slack_send_message,
)

from ..types import ToolExecutionContext


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


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


def _is_native_tool_enabled_for_account(*, account: IntegrationAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    preferences = _tool_preferences_from_metadata(metadata)
    return bool(preferences.get(normalized_name, True))


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
        if not _is_native_tool_enabled_for_account(account=account, tool_name=tool):
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
            if not _is_native_tool_enabled_for_account(account=account, tool_name=tool):
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
