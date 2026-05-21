from __future__ import annotations

import logging
from typing import Mapping

from apps.accounts.models import EmailAccountProvider
from apps.conversations.models import Conversation
from apps.integrations.accounts.email import ensure_fresh_email_credentials
from apps.integrations.providers.gmail import (
    GmailApiError,
    gmail_get_message,
    gmail_get_thread,
)
from apps.integrations.providers.microsoft_graph import (
    GraphApiError,
    graph_get_message,
    graph_get_thread,
)

from ...types import ToolExecutionContext
from .shared import _coerce_str, _email_error, _resolve_email_account_for_tool


logger = logging.getLogger(__name__)


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
