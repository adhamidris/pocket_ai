from __future__ import annotations

from typing import Any, Mapping

from apps.accounts.models import IntegrationType
from apps.conversations.models import Conversation
from apps.integrations.providers.google_calendar import (
    CalendarApiError,
    calendar_create_event,
    calendar_get_event,
    calendar_list_events,
    calendar_update_event,
)

from ...types import ToolExecutionContext
from .shared import (
    _coerce_str,
    _get_integration_access_token,
    _integration_error,
    _resolve_integration_account_for_tool,
)


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
