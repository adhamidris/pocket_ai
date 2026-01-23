from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping

import requests
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0/me"

DEFAULT_TIMEOUT_S = 15
MAX_BODY_CHARS = 12_000
MAX_THREAD_MESSAGES = 12


class GraphApiError(RuntimeError):
    """Raised when a Microsoft Graph API call fails."""


def _odata_escape(value: str) -> str:
    return (value or "").replace("'", "''")


def _parse_date_filter(value: str | None) -> datetime | None:
    """
    Parse an ISO-ish date/time and return an aware datetime in UTC when possible.

    - Accepts "YYYY-MM-DD" (interpreted as midnight local time) or ISO datetime.
    - Returns None on parse failure.
    """

    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    parsed_date: date | None = parse_date(raw)
    if parsed_date is not None:
        dt = datetime.combine(parsed_date, time.min)
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt.astimezone(timezone.utc)
    parsed_dt = parse_datetime(raw)
    if parsed_dt is None:
        return None
    if timezone.is_naive(parsed_dt):
        parsed_dt = timezone.make_aware(parsed_dt, timezone.get_current_timezone())
    return parsed_dt.astimezone(timezone.utc)


def _build_messages_filter(
    *,
    after: str | None = None,
    before: str | None = None,
) -> str | None:
    """
    Build a conservative OData filter for message dates only.

    NOTE: We intentionally keep this small and reliable. More complex filters
    (from/to/subject) vary by tenant/config and can trigger Graph query edge cases.
    """

    clauses: list[str] = []
    after_dt = _parse_date_filter(after)
    before_dt = _parse_date_filter(before)
    if after_dt:
        clauses.append(f"receivedDateTime ge {after_dt.isoformat().replace('+00:00', 'Z')}")
    if before_dt:
        clauses.append(f"receivedDateTime le {before_dt.isoformat().replace('+00:00', 'Z')}")
    return " and ".join([c for c in clauses if c]) or None


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = (data or "").strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value or "")
        parser.close()
    except Exception:  # pragma: no cover - best effort only
        return re.sub(r"<[^>]+>", " ", value or "").strip()
    return parser.get_text()


def _graph_request(
    *,
    method: str,
    url: str,
    access_token: str,
    params: dict[str, object] | None = None,
    json_body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[int, dict[str, Any]]:
    base_headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if headers:
        base_headers.update({str(k): str(v) for k, v in headers.items() if str(k).strip() and str(v).strip()})
    try:
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            headers=base_headers,
            timeout=max(1, int(timeout_s)),
        )
    except requests.RequestException as exc:
        raise GraphApiError(f"Failed to reach Microsoft Graph: {exc.__class__.__name__}") from exc

    if response.status_code in {202, 204}:
        return response.status_code, {}

    if not response.ok:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, Mapping):
                    detail = str(err.get("message") or err)[:240]
                else:
                    detail = str(payload)[:240]
            else:
                detail = str(payload)[:240]
        except ValueError:
            detail = (response.text or "")[:240]
        raise GraphApiError(f"Graph API error ({response.status_code}): {detail or 'unknown_error'}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GraphApiError("Microsoft Graph returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise GraphApiError("Microsoft Graph returned an unexpected response.")
    return response.status_code, payload


def _address_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        email_obj = item.get("emailAddress") if isinstance(item.get("emailAddress"), Mapping) else {}
        address = str(email_obj.get("address") or "").strip()
        if address:
            out.append(address)
    return out


def _format_addresses(values: object) -> str:
    return ", ".join(_address_list(values))


def graph_search_messages(
    *,
    access_token: str,
    query: str,
    limit: int,
    after: str | None = None,
    before: str | None = None,
) -> dict[str, object]:
    safe_limit = max(1, min(int(limit or 0), 25))
    search_value = str(query or "").strip()
    if not search_value:
        return {"results": []}
    search_value = search_value.replace('"', "").strip()

    params: dict[str, object] = {
        "$top": safe_limit,
        "$orderby": "receivedDateTime desc",
        "$select": ",".join(
            [
                "id",
                "conversationId",
                "receivedDateTime",
                "subject",
                "bodyPreview",
                "from",
                "toRecipients",
            ]
        ),
        "$search": f'"{search_value}"',
        "$count": "true",
    }

    date_filter = _build_messages_filter(after=after, before=before)
    if date_filter:
        params["$filter"] = date_filter

    _, payload = _graph_request(
        method="GET",
        url=f"{GRAPH_API_BASE}/messages",
        access_token=access_token,
        params=params,
        headers={"ConsistencyLevel": "eventual"},
    )

    items = payload.get("value") if isinstance(payload.get("value"), list) else []
    results: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        message_id = str(item.get("id") or "").strip()
        thread_id = str(item.get("conversationId") or "").strip()
        if not message_id:
            continue
        from_obj = item.get("from") if isinstance(item.get("from"), Mapping) else {}
        from_email = ""
        if isinstance(from_obj, Mapping):
            email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), Mapping) else {}
            from_email = str(email_obj.get("address") or "").strip()
        results.append(
            {
                "message_id": message_id,
                "thread_id": thread_id,
                "snippet": str(item.get("bodyPreview") or "").strip(),
                "subject": str(item.get("subject") or "").strip(),
                "from": from_email,
                "to": _format_addresses(item.get("toRecipients")),
                "date": str(item.get("receivedDateTime") or "").strip(),
            }
        )

    next_link = payload.get("@odata.nextLink") if isinstance(payload.get("@odata.nextLink"), str) else None
    count_value = payload.get("@odata.count") if isinstance(payload.get("@odata.count"), int) else None
    return {
        "results": results,
        "result_size_estimate": count_value,
        "next_page_token": next_link,
    }


def graph_get_message(
    *,
    access_token: str,
    message_id: str,
) -> dict[str, object]:
    message_id = str(message_id or "").strip()
    if not message_id:
        raise GraphApiError("message_id is required.")

    _, payload = _graph_request(
        method="GET",
        url=f"{GRAPH_API_BASE}/messages/{message_id}",
        access_token=access_token,
        params={
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "receivedDateTime",
                    "subject",
                    "bodyPreview",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "bccRecipients",
                    "body",
                ]
            )
        },
        headers={
            "Prefer": 'outlook.body-content-type="text"',
        },
    )

    body_obj = payload.get("body") if isinstance(payload.get("body"), Mapping) else {}
    body_content = str(body_obj.get("content") or "").strip()
    body_type = str(body_obj.get("contentType") or "").strip().lower()
    if body_content and body_type == "html":
        body_content = _html_to_text(body_content)

    truncated = False
    if len(body_content) > MAX_BODY_CHARS:
        body_content = body_content[:MAX_BODY_CHARS].rstrip()
        truncated = True

    from_obj = payload.get("from") if isinstance(payload.get("from"), Mapping) else {}
    from_email = ""
    if isinstance(from_obj, Mapping):
        email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), Mapping) else {}
        from_email = str(email_obj.get("address") or "").strip()

    return {
        "message_id": str(payload.get("id") or "").strip() or message_id,
        "thread_id": str(payload.get("conversationId") or "").strip(),
        "snippet": str(payload.get("bodyPreview") or "").strip(),
        "labels": [],
        "headers": {
            "from": from_email,
            "to": _format_addresses(payload.get("toRecipients")),
            "subject": str(payload.get("subject") or "").strip(),
            "date": str(payload.get("receivedDateTime") or "").strip(),
        },
        "body_text": body_content,
        "body_truncated": truncated,
    }


def graph_get_thread(
    *,
    access_token: str,
    thread_id: str,
    max_messages: int = MAX_THREAD_MESSAGES,
) -> dict[str, object]:
    thread_id = str(thread_id or "").strip()
    if not thread_id:
        raise GraphApiError("thread_id is required.")
    safe_max = max(1, min(int(max_messages or 0), MAX_THREAD_MESSAGES))
    escaped = _odata_escape(thread_id)

    _, payload = _graph_request(
        method="GET",
        url=f"{GRAPH_API_BASE}/messages",
        access_token=access_token,
        params={
            "$top": safe_max,
            "$orderby": "receivedDateTime asc",
            "$filter": f"conversationId eq '{escaped}'",
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "receivedDateTime",
                    "subject",
                    "bodyPreview",
                    "from",
                    "toRecipients",
                    "body",
                ]
            ),
        },
        headers={
            "Prefer": 'outlook.body-content-type="text"',
        },
    )

    items = payload.get("value") if isinstance(payload.get("value"), list) else []
    normalized: list[dict[str, object]] = []
    for item in items[:safe_max]:
        if not isinstance(item, Mapping):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        from_obj = item.get("from") if isinstance(item.get("from"), Mapping) else {}
        from_email = ""
        if isinstance(from_obj, Mapping):
            email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), Mapping) else {}
            from_email = str(email_obj.get("address") or "").strip()
        body_obj = item.get("body") if isinstance(item.get("body"), Mapping) else {}
        body_content = str(body_obj.get("content") or "").strip()
        body_type = str(body_obj.get("contentType") or "").strip().lower()
        if body_content and body_type == "html":
            body_content = _html_to_text(body_content)
        truncated = False
        if len(body_content) > MAX_BODY_CHARS:
            body_content = body_content[:MAX_BODY_CHARS].rstrip()
            truncated = True
        normalized.append(
            {
                "message_id": mid,
                "thread_id": str(item.get("conversationId") or "").strip(),
                "snippet": str(item.get("bodyPreview") or "").strip(),
                "labels": [],
                "headers": {
                    "from": from_email,
                    "to": _format_addresses(item.get("toRecipients")),
                    "subject": str(item.get("subject") or "").strip(),
                    "date": str(item.get("receivedDateTime") or "").strip(),
                },
                "body_text": body_content,
                "body_truncated": truncated,
                "received_at": str(item.get("receivedDateTime") or "").strip(),
            }
        )

    return {
        "thread_id": thread_id,
        "message_count": len(items),
        "messages": normalized,
        "truncated": len(items) > len(normalized),
    }


def graph_create_draft(
    *,
    access_token: str,
    to: Iterable[str],
    cc: Iterable[str] | None,
    bcc: Iterable[str] | None,
    subject: str,
    body_text: str,
) -> dict[str, object]:
    def to_recipient(address: str) -> dict[str, object]:
        return {"emailAddress": {"address": address}}

    to_list = [str(addr).strip() for addr in to if str(addr).strip()]
    cc_list = [str(addr).strip() for addr in (cc or []) if str(addr).strip()]
    bcc_list = [str(addr).strip() for addr in (bcc or []) if str(addr).strip()]
    if not to_list:
        raise GraphApiError("to recipients are required.")

    _, payload = _graph_request(
        method="POST",
        url=f"{GRAPH_API_BASE}/messages",
        access_token=access_token,
        json_body={
            "subject": str(subject or ""),
            "body": {"contentType": "Text", "content": str(body_text or "")},
            "toRecipients": [to_recipient(addr) for addr in to_list],
            **({"ccRecipients": [to_recipient(addr) for addr in cc_list]} if cc_list else {}),
            **({"bccRecipients": [to_recipient(addr) for addr in bcc_list]} if bcc_list else {}),
        },
    )

    message_id = str(payload.get("id") or "").strip()
    return {
        "draft_id": message_id,
        "message_id": message_id,
        "thread_id": str(payload.get("conversationId") or "").strip(),
    }


def graph_get_draft_headers(
    *,
    access_token: str,
    draft_id: str,
) -> dict[str, str]:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        raise GraphApiError("draft_id is required.")
    _, payload = _graph_request(
        method="GET",
        url=f"{GRAPH_API_BASE}/messages/{draft_id}",
        access_token=access_token,
        params={
            "$select": ",".join(
                [
                    "id",
                    "conversationId",
                    "subject",
                    "from",
                    "toRecipients",
                    "ccRecipients",
                    "bccRecipients",
                ]
            )
        },
    )

    from_obj = payload.get("from") if isinstance(payload.get("from"), Mapping) else {}
    from_email = ""
    if isinstance(from_obj, Mapping):
        email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), Mapping) else {}
        from_email = str(email_obj.get("address") or "").strip()

    return {
        "from": from_email,
        "to": _format_addresses(payload.get("toRecipients")),
        "cc": _format_addresses(payload.get("ccRecipients")),
        "bcc": _format_addresses(payload.get("bccRecipients")),
        "subject": str(payload.get("subject") or "").strip(),
    }


def graph_send_draft(
    *,
    access_token: str,
    draft_id: str,
) -> dict[str, object]:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        raise GraphApiError("draft_id is required.")
    _graph_request(
        method="POST",
        url=f"{GRAPH_API_BASE}/messages/{draft_id}/send",
        access_token=access_token,
    )
    return {
        "draft_id": draft_id,
        "message_id": draft_id,
    }


def graph_get_profile(
    *,
    access_token: str,
) -> dict[str, object]:
    """
    Fetch the current user's profile from Microsoft Graph.

    This is a lightweight call useful for connection health checks.
    """
    _, payload = _graph_request(
        method="GET",
        url=GRAPH_API_BASE,
        access_token=access_token,
        params={"$select": "id,displayName,mail,userPrincipalName"},
    )
    return {
        "id": str(payload.get("id") or "").strip(),
        "display_name": str(payload.get("displayName") or "").strip(),
        "email": str(payload.get("mail") or payload.get("userPrincipalName") or "").strip(),
    }
