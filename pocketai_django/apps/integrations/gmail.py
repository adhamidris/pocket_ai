from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping

import requests
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone


logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

DEFAULT_TIMEOUT_S = 15
MAX_BODY_CHARS = 12_000
MAX_THREAD_MESSAGES = 12


class GmailApiError(RuntimeError):
    """Raised when a Gmail API call fails."""


def _b64url_decode(data: str | None) -> bytes:
    raw = str(data or "").strip()
    if not raw:
        return b""
    padding = "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(raw + padding)
    except (ValueError, binascii.Error):  # type: ignore[name-defined]  # pragma: no cover - defensive
        return b""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


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


def _header_map(headers: object) -> dict[str, str]:
    if not isinstance(headers, list):
        return {}
    out: dict[str, str] = {}
    for item in headers:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        if not name or not value:
            continue
        if name not in out:
            out[name] = value
    return out


def _extract_part_bodies(payload: Mapping[str, object]) -> tuple[str, str]:
    """
    Return (text_plain, text_html) aggregated from the MIME payload tree.
    """

    text_plain: list[str] = []
    text_html: list[str] = []

    def walk(node: Mapping[str, object]) -> None:
        mime = str(node.get("mimeType") or "").strip().lower()
        body = node.get("body") if isinstance(node.get("body"), Mapping) else {}
        data = str(body.get("data") or "")
        if data:
            decoded = _b64url_decode(data).decode("utf-8", errors="replace")
            if mime.startswith("text/plain"):
                text_plain.append(decoded)
            elif mime.startswith("text/html"):
                text_html.append(decoded)
        parts = node.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, Mapping):
                    walk(part)

    walk(payload)
    return ("\n".join(text_plain).strip(), "\n".join(text_html).strip())


def _parse_date_filter(value: str | None) -> date | None:
    if not value:
        return None
    parsed = parse_date(value)
    if parsed is not None:
        return parsed
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.date()


def build_gmail_query(
    *,
    query: str,
    after: str | None = None,
    before: str | None = None,
    sender: str | None = None,
    to: str | None = None,
    subject: str | None = None,
) -> str:
    """
    Build a Gmail-compatible query string.

    Note: The free-form `query` is preserved. Structured filters are appended to
    reduce user friction.
    """

    tokens: list[str] = []
    base = (query or "").strip()
    if base:
        tokens.append(base)

    after_date = _parse_date_filter(after)
    before_date = _parse_date_filter(before)
    if after_date:
        tokens.append(f"after:{after_date.strftime('%Y/%m/%d')}")
    if before_date:
        tokens.append(f"before:{before_date.strftime('%Y/%m/%d')}")

    sender_value = (sender or "").strip()
    if sender_value:
        tokens.append(f"from:{sender_value}")
    to_value = (to or "").strip()
    if to_value:
        tokens.append(f"to:{to_value}")
    subject_value = (subject or "").strip()
    if subject_value:
        safe_subject = subject_value.replace('"', "")
        if " " in safe_subject:
            tokens.append(f'subject:"{safe_subject}"')
        else:
            tokens.append(f"subject:{safe_subject}")

    return " ".join([t for t in tokens if t]).strip()


def _gmail_request(
    *,
    method: str,
    url: str,
    access_token: str,
    params: dict[str, object] | None = None,
    json_body: dict[str, object] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=max(1, int(timeout_s)),
        )
    except requests.RequestException as exc:
        raise GmailApiError(f"Failed to reach Gmail API: {exc.__class__.__name__}") from exc

    if not response.ok:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("error") or payload)[:240]
            else:
                detail = str(payload)[:240]
        except ValueError:
            detail = (response.text or "")[:240]
        raise GmailApiError(f"Gmail API error ({response.status_code}): {detail or 'unknown_error'}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailApiError("Gmail API returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise GmailApiError("Gmail API returned an unexpected response.")
    return payload


@dataclass(frozen=True, slots=True)
class GmailMessageSummary:
    message_id: str
    thread_id: str
    snippet: str
    subject: str
    from_email: str
    to_email: str
    date: str


def gmail_search_messages(
    *,
    access_token: str,
    query: str,
    limit: int,
    include_snippets_limit: int = 5,
) -> dict[str, object]:
    """
    Search Gmail and return a bounded list of message summaries.

    The list call returns ids only; we fetch metadata for up to `include_snippets_limit`
    results to keep latency predictable.
    """

    safe_limit = max(1, min(int(limit or 0), 25))
    list_payload = _gmail_request(
        method="GET",
        url=f"{GMAIL_API_BASE}/messages",
        access_token=access_token,
        params={
            "q": query,
            "maxResults": safe_limit,
            "includeSpamTrash": "false",
            "fields": "messages(id,threadId),resultSizeEstimate,nextPageToken",
        },
    )
    messages = list_payload.get("messages") if isinstance(list_payload.get("messages"), list) else []
    ids: list[tuple[str, str]] = []
    for item in messages:
        if not isinstance(item, Mapping):
            continue
        mid = str(item.get("id") or "").strip()
        tid = str(item.get("threadId") or "").strip()
        if mid and tid:
            ids.append((mid, tid))

    summaries: list[dict[str, object]] = []
    for message_id, thread_id in ids[: max(0, min(include_snippets_limit, safe_limit))]:
        meta_payload = _gmail_request(
            method="GET",
            url=f"{GMAIL_API_BASE}/messages/{message_id}",
            access_token=access_token,
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
                "fields": "id,threadId,snippet,internalDate,payload(headers)",
            },
        )
        payload = meta_payload.get("payload") if isinstance(meta_payload.get("payload"), Mapping) else {}
        headers = _header_map(payload.get("headers"))
        summaries.append(
            {
                "message_id": message_id,
                "thread_id": thread_id,
                "snippet": str(meta_payload.get("snippet") or "").strip(),
                "subject": headers.get("subject", ""),
                "from": headers.get("from", ""),
                "to": headers.get("to", ""),
                "date": headers.get("date", ""),
            }
        )

    # Include remaining ids without metadata to keep the tool deterministic.
    for message_id, thread_id in ids[len(summaries) : safe_limit]:
        summaries.append(
            {
                "message_id": message_id,
                "thread_id": thread_id,
            }
        )

    return {
        "results": summaries,
        "result_size_estimate": list_payload.get("resultSizeEstimate"),
        "next_page_token": list_payload.get("nextPageToken"),
    }


def gmail_get_message(
    *,
    access_token: str,
    message_id: str,
) -> dict[str, object]:
    payload = _gmail_request(
        method="GET",
        url=f"{GMAIL_API_BASE}/messages/{message_id}",
        access_token=access_token,
        params={
            "format": "full",
            "fields": "id,threadId,snippet,internalDate,payload, labelIds",
        },
    )
    msg_payload = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else {}
    headers = _header_map(msg_payload.get("headers"))
    text_plain, text_html = _extract_part_bodies(msg_payload)
    body_text = text_plain.strip()
    if not body_text and text_html:
        body_text = _html_to_text(text_html)

    truncated = False
    if len(body_text) > MAX_BODY_CHARS:
        body_text = body_text[:MAX_BODY_CHARS].rstrip()
        truncated = True

    return {
        "message_id": str(payload.get("id") or "").strip(),
        "thread_id": str(payload.get("threadId") or "").strip(),
        "snippet": str(payload.get("snippet") or "").strip(),
        "labels": payload.get("labelIds") if isinstance(payload.get("labelIds"), list) else [],
        "headers": {
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
        },
        "body_text": body_text,
        "body_truncated": truncated,
    }


def gmail_get_thread(
    *,
    access_token: str,
    thread_id: str,
    max_messages: int = MAX_THREAD_MESSAGES,
) -> dict[str, object]:
    safe_max = max(1, min(int(max_messages or 0), MAX_THREAD_MESSAGES))
    payload = _gmail_request(
        method="GET",
        url=f"{GMAIL_API_BASE}/threads/{thread_id}",
        access_token=access_token,
        params={
            "format": "full",
            "fields": "id,messages(id,threadId,snippet,internalDate,payload,labelIds)",
        },
    )
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    normalized: list[dict[str, object]] = []
    for message in messages[:safe_max]:
        if not isinstance(message, Mapping):
            continue
        msg_payload = message.get("payload") if isinstance(message.get("payload"), Mapping) else {}
        headers = _header_map(msg_payload.get("headers"))
        text_plain, text_html = _extract_part_bodies(msg_payload)
        body_text = text_plain.strip()
        if not body_text and text_html:
            body_text = _html_to_text(text_html)
        truncated = False
        if len(body_text) > MAX_BODY_CHARS:
            body_text = body_text[:MAX_BODY_CHARS].rstrip()
            truncated = True
        normalized.append(
            {
                "message_id": str(message.get("id") or "").strip(),
                "thread_id": str(message.get("threadId") or "").strip(),
                "snippet": str(message.get("snippet") or "").strip(),
                "labels": message.get("labelIds") if isinstance(message.get("labelIds"), list) else [],
                "headers": {
                    "from": headers.get("from", ""),
                    "to": headers.get("to", ""),
                    "subject": headers.get("subject", ""),
                    "date": headers.get("date", ""),
                },
                "body_text": body_text,
                "body_truncated": truncated,
                "internal_date_ms": message.get("internalDate"),
            }
        )

    normalized.sort(key=lambda item: int(item.get("internal_date_ms") or 0))
    truncated_thread = len(messages) > len(normalized)
    return {
        "thread_id": str(payload.get("id") or "").strip() or str(thread_id),
        "message_count": len(messages),
        "messages": normalized,
        "truncated": truncated_thread,
    }


def gmail_create_draft(
    *,
    access_token: str,
    to: Iterable[str],
    cc: Iterable[str] | None,
    bcc: Iterable[str] | None,
    subject: str,
    body_text: str,
) -> dict[str, object]:
    # RFC 2822 minimal message
    to_value = ", ".join([addr.strip() for addr in to if addr and str(addr).strip()])
    cc_value = ", ".join([addr.strip() for addr in (cc or []) if addr and str(addr).strip()])
    bcc_value = ", ".join([addr.strip() for addr in (bcc or []) if addr and str(addr).strip()])

    lines: list[str] = []
    lines.append(f"To: {to_value}")
    if cc_value:
        lines.append(f"Cc: {cc_value}")
    if bcc_value:
        lines.append(f"Bcc: {bcc_value}")
    lines.append(f"Subject: {subject}")
    lines.append("MIME-Version: 1.0")
    lines.append('Content-Type: text/plain; charset="UTF-8"')
    lines.append("Content-Transfer-Encoding: 8bit")
    lines.append("")
    lines.append(body_text)
    raw_bytes = "\r\n".join(lines).encode("utf-8", errors="replace")
    raw_b64 = _b64url_encode(raw_bytes)

    payload = _gmail_request(
        method="POST",
        url=f"{GMAIL_API_BASE}/drafts",
        access_token=access_token,
        json_body={"message": {"raw": raw_b64}},
    )

    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    return {
        "draft_id": str(payload.get("id") or "").strip(),
        "message_id": str(message.get("id") or "").strip(),
        "thread_id": str(message.get("threadId") or "").strip(),
    }


def gmail_get_draft_headers(
    *,
    access_token: str,
    draft_id: str,
) -> dict[str, str]:
    payload = _gmail_request(
        method="GET",
        url=f"{GMAIL_API_BASE}/drafts/{draft_id}",
        access_token=access_token,
        params={
            "format": "metadata",
            "metadataHeaders": ["From", "To", "Cc", "Bcc", "Subject"],
            "fields": "id,message(payload(headers))",
        },
    )
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    msg_payload = message.get("payload") if isinstance(message.get("payload"), Mapping) else {}
    headers = _header_map(msg_payload.get("headers"))
    return {
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "bcc": headers.get("bcc", ""),
        "subject": headers.get("subject", ""),
    }


def gmail_send_draft(
    *,
    access_token: str,
    draft_id: str,
) -> dict[str, object]:
    payload = _gmail_request(
        method="POST",
        url=f"{GMAIL_API_BASE}/drafts/send",
        access_token=access_token,
        json_body={"id": draft_id},
    )
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    return {
        "draft_id": str(payload.get("id") or "").strip() or str(draft_id),
        "message_id": str(message.get("id") or "").strip(),
        "thread_id": str(message.get("threadId") or "").strip(),
    }
