from __future__ import annotations

import logging
from typing import Mapping

from apps.accounts.models import EmailAccountProvider
from apps.conversations.models import Conversation
from apps.integrations.accounts.email import ensure_fresh_email_credentials
from apps.integrations.providers.gmail import (
    GmailApiError,
    gmail_create_draft,
    gmail_send_draft,
)
from apps.integrations.providers.microsoft_graph import (
    GraphApiError,
    graph_create_draft,
    graph_send_draft,
)

from ...types import ToolExecutionContext
from .shared import _coerce_str, _email_error, _resolve_email_account_for_tool


logger = logging.getLogger(__name__)


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
