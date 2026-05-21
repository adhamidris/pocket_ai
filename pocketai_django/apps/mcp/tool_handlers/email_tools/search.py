from __future__ import annotations

import logging
from typing import Mapping

from apps.accounts.models import EmailAccountProvider
from apps.conversations.models import Conversation
from apps.integrations.accounts.email import ensure_fresh_email_credentials
from apps.integrations.providers.gmail import (
    GmailApiError,
    build_gmail_query,
    gmail_search_messages,
)
from apps.integrations.providers.microsoft_graph import (
    GraphApiError,
    graph_search_messages,
)

from ...types import ToolExecutionContext
from .shared import _coerce_str, _email_error, _resolve_email_account_for_tool


logger = logging.getLogger(__name__)


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
