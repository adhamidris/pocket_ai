from __future__ import annotations

from typing import Mapping

from apps.accounts.models import IntegrationType
from apps.conversations.models import Conversation
from apps.integrations.providers.slack import (
    SlackApiError,
    slack_list_channels,
    slack_read_channel,
    slack_search_messages,
    slack_send_message,
)

from ...types import ToolExecutionContext
from .shared import (
    _coerce_str,
    _get_integration_access_token,
    _integration_error,
    _resolve_integration_account_for_tool,
)


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
