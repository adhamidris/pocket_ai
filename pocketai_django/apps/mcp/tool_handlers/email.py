"""
Email MCP tool handlers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Mapping

from apps.accounts.models import EmailAccountProvider, EmailAccountStatus
from apps.conversations.models import Conversation
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
from apps.integrations.models import EmailAccount

from ..types import ToolExecutionContext


logger = logging.getLogger(__name__)


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


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


def _email_tool_preferences_from_account(account: EmailAccount | None) -> dict[str, bool]:
    if account is None:
        return {}
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    return _tool_preferences_from_metadata(metadata)


def _is_email_tool_enabled_for_account(*, account: EmailAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    preferences = _email_tool_preferences_from_account(account)
    return bool(preferences.get(normalized_name, True))


def _conversation_actor_user_uuid(conversation: Conversation) -> uuid.UUID | None:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    actor_user_id = metadata.get("actor_user_id") or metadata.get("actorUserId") or metadata.get("user_id") or metadata.get("userId")
    if not actor_user_id:
        return None
    try:
        return uuid.UUID(str(actor_user_id))
    except (TypeError, ValueError):
        return None


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
