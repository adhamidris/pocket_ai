from __future__ import annotations

from typing import Any, Mapping

import httpx

from .settings import DEFAULT_PROTOCOL_VERSION
from .errors import McpRemoteProtocolError, McpRemoteStreamableNotSupported
from .legacy_sse import _legacy_sse_bootstrap, _legacy_sse_wait_for_response
from .protocol import _headers_with_protocol, _jsonrpc_request, _read_json_or_sse_response
from .security import _validate_mcp_url_for_ssrf
from .streamable import _streamable_http_initialize
from .transport import (
    _post_jsonrpc_with_retry_after,
    _raise_for_redirect_response,
    _raise_transport_for_http_status,
    _request_with_transport_errors,
)


def normalize_mcp_tool_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """
    Convert a tools/call result into a prompt-friendly dict.
    """

    content = result.get("content")
    is_error = bool(result.get("isError"))
    text_parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
    return {
        "is_error": is_error,
        "text": "\n".join(text_parts).strip() if text_parts else "",
        "content": content if isinstance(content, list) else [],
    }


def call_mcp_tool_streamable_http(
    *,
    endpoint_url: str,
    tool_name: str,
    arguments: dict[str, Any] | None,
    headers: Mapping[str, str] | None = None,
    idempotency_key: str | None = None,
    timeout_s: float = 45.0,
    protocol_version: str = DEFAULT_PROTOCOL_VERSION,
) -> dict[str, Any]:
    """
    Minimal Streamable HTTP client that performs initialize + tools/call.

    This is intentionally stateless (new session per call) for correctness in beta.
    """

    _validate_mcp_url_for_ssrf(endpoint_url, action="mcp_tools_call")
    sanitized_idempotency_key: str | None = None
    if idempotency_key is not None:
        raw_key = str(idempotency_key).strip()
        if raw_key:
            sanitized = "".join(ch for ch in raw_key if ch.isalnum() or ch in "-_.:")
            sanitized = sanitized[:128]
            if sanitized:
                sanitized_idempotency_key = sanitized

    with httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False) as client:
        try:
            session = _streamable_http_initialize(
                client=client,
                endpoint_url=endpoint_url,
                headers=headers,
                protocol_version=protocol_version,
            )
            request_id = 100
            payload = _jsonrpc_request(
                "tools/call",
                request_id=request_id,
                params={"name": tool_name, "arguments": arguments or {}},
            )
            call_headers = _headers_with_protocol(headers, protocol_version=session.protocol_version, session_id=session.session_id)
            call_headers["Accept"] = "application/json, text/event-stream"
            call_headers["Content-Type"] = "application/json"
            if sanitized_idempotency_key:
                call_headers["Idempotency-Key"] = sanitized_idempotency_key
            resp = _post_jsonrpc_with_retry_after(
                client=client,
                url=session.endpoint_url,
                payload=payload,
                headers=call_headers,
                action="streamable_http.tools_call",
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _raise_transport_for_http_status(exc, action="streamable_http.tools_call")
            parsed = _read_json_or_sse_response(resp, expect_id=request_id)
        except McpRemoteStreamableNotSupported:
            # Legacy HTTP+SSE: open stream, POST messages to endpoint supplied by server.
            sse_response, events, message_url = _legacy_sse_bootstrap(client=client, sse_url=endpoint_url, headers=headers)
            try:
                init_id = 1
                init_payload = _jsonrpc_request(
                    "initialize",
                    request_id=init_id,
                    params={
                        "protocolVersion": protocol_version,
                        "capabilities": {},
                        "clientInfo": {"name": "PocketAI", "version": "1.0.0"},
                    },
                )
                post_headers = dict(headers or {})
                post_headers["Content-Type"] = "application/json"
                init_resp = _request_with_transport_errors(
                    "legacy_sse.initialize",
                    lambda: client.post(message_url, json=init_payload, headers=post_headers),
                )
                _raise_for_redirect_response(init_resp, action="legacy_sse.initialize")
                try:
                    init_resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    _raise_transport_for_http_status(exc, action="legacy_sse.initialize")
                _legacy_sse_wait_for_response(events, expect_id=init_id)
                notif_resp = _request_with_transport_errors(
                    "legacy_sse.initialized",
                    lambda: client.post(
                        message_url,
                        json=_jsonrpc_request("notifications/initialized", request_id=None),
                        headers=post_headers,
                    ),
                )
                _raise_for_redirect_response(notif_resp, action="legacy_sse.initialized")
                try:
                    notif_resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    _raise_transport_for_http_status(exc, action="legacy_sse.initialized")
                request_id = 100
                payload = _jsonrpc_request(
                    "tools/call",
                    request_id=request_id,
                    params={"name": tool_name, "arguments": arguments or {}},
                )
                call_post_headers = dict(post_headers)
                if sanitized_idempotency_key:
                    call_post_headers["Idempotency-Key"] = sanitized_idempotency_key
                call_resp = _request_with_transport_errors(
                    "legacy_sse.tools_call",
                    lambda: client.post(message_url, json=payload, headers=call_post_headers),
                )
                _raise_for_redirect_response(call_resp, action="legacy_sse.tools_call")
                try:
                    call_resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    _raise_transport_for_http_status(exc, action="legacy_sse.tools_call")
                parsed = _legacy_sse_wait_for_response(events, expect_id=request_id)
            finally:
                try:
                    sse_response.close()
                except Exception:
                    pass

        if "error" in parsed:
            raise McpRemoteProtocolError(f"MCP tools/call protocol error: {parsed.get('error')}")
        result = parsed.get("result")
        if not isinstance(result, Mapping):
            raise McpRemoteProtocolError("Invalid tools/call result payload.")
        return normalize_mcp_tool_result(result)
