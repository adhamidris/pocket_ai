from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import urljoin

import httpx

from .errors import McpRemoteHttpStatusError, McpRemoteTransportError
from .security import _validate_mcp_url_for_ssrf


def _raise_for_redirect_response(response: httpx.Response, *, action: str) -> None:
    if response.status_code < 300 or response.status_code >= 400:
        return
    location = (response.headers.get("Location") or "").strip()
    if not location:
        raise McpRemoteTransportError(f"MCP request failed during {action} (redirect without Location header).")
    base_url = str(getattr(response.request, "url", "") or "")
    target_url = urljoin(base_url, location) if base_url else location
    _validate_mcp_url_for_ssrf(target_url, action=action)
    raise McpRemoteTransportError(
        f"MCP request failed during {action} (redirects are not supported; use the final MCP endpoint URL)."
    )


def _http_reason_phrase(response: httpx.Response) -> str:
    try:
        phrase = response.reason_phrase
    except Exception:
        phrase = ""
    return (phrase or "").strip()


def _raise_transport_for_http_status(exc: httpx.HTTPStatusError, *, action: str) -> None:
    response = exc.response
    status_code = response.status_code if response is not None else None
    phrase = _http_reason_phrase(response) if response is not None else ""
    retry_after = (response.headers.get("Retry-After") if response is not None else None) or None

    details = f"HTTP {status_code}" if status_code is not None else "HTTP error"
    if phrase:
        details = f"{details} {phrase}"

    message = f"MCP request failed during {action} ({details})."
    if retry_after:
        message = f"{message} Retry-After: {retry_after}."

    raise McpRemoteHttpStatusError(message, status_code=status_code, retry_after=retry_after) from exc


def _request_with_transport_errors(action: str, fn):
    try:
        return fn()
    except httpx.HTTPStatusError as exc:
        _raise_transport_for_http_status(exc, action=action)
    except httpx.TimeoutException as exc:
        raise McpRemoteTransportError(f"MCP request timed out during {action}.") from exc
    except httpx.RequestError as exc:
        raise McpRemoteTransportError(f"MCP network error during {action}: {exc.__class__.__name__}.") from exc


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.isdigit():
        return float(raw)
    try:
        when = parsedate_to_datetime(raw)
    except Exception:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def _post_jsonrpc_with_retry_after(
    *,
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    headers: Mapping[str, str] | None,
    action: str,
    max_retries: int = 2,
    max_auto_wait_s: float = 2.0,
) -> httpx.Response:
    """
    POST a JSON-RPC payload and optionally auto-retry small 429 Retry-After windows.

    Some hosted MCP servers rate-limit rapid sequential requests (initialize → initialized → tools/list).
    """

    attempt = 0
    while True:
        _validate_mcp_url_for_ssrf(url, action=action)
        resp = _request_with_transport_errors(action, lambda: client.post(url, json=payload, headers=dict(headers or {})))
        _raise_for_redirect_response(resp, action=action)
        if resp.status_code != 429:
            return resp

        retry_after_value = resp.headers.get("Retry-After")
        wait_s = _parse_retry_after_seconds(retry_after_value)
        if wait_s is None or wait_s <= 0:
            return resp
        if wait_s > max_auto_wait_s or attempt >= max_retries:
            return resp

        try:
            resp.close()
        except Exception:
            pass
        time.sleep(wait_s)
        attempt += 1
