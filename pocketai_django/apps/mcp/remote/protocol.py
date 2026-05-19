from __future__ import annotations

import json
from typing import Any, Iterable, Iterator, Mapping

import httpx

from .errors import McpRemoteProtocolError


def _headers_with_protocol(
    base_headers: Mapping[str, str] | None,
    *,
    protocol_version: str | None,
    session_id: str | None,
) -> dict[str, str]:
    headers = dict(base_headers or {})
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _jsonrpc_request(method: str, *, request_id: int | None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        payload["id"] = request_id
    if params is not None:
        payload["params"] = params
    return payload


def _iter_sse_events(lines: Iterable[str]) -> Iterator[dict[str, str]]:
    """
    Minimal SSE parser that yields events as dicts with keys: event, data, id, retry.

    References: MCP Streamable HTTP transport allows text/event-stream responses.
    """

    event: dict[str, str] = {}
    data_lines: list[str] = []
    for raw in lines:
        line = (raw or "").rstrip("\n")
        if not line:
            if data_lines:
                event["data"] = "\n".join(data_lines)
            if event:
                yield dict(event)
            event = {}
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if ":" in line:
            field, value = line.split(":", 1)
            value = value.lstrip(" ")
        else:
            field, value = line, ""
        if field == "data":
            data_lines.append(value)
        elif field in {"event", "id", "retry"}:
            event[field] = value
    if data_lines:
        event["data"] = "\n".join(data_lines)
    if event:
        yield dict(event)


def _parse_jsonrpc_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise McpRemoteProtocolError("Invalid JSON-RPC payload.")
    if payload.get("jsonrpc") != "2.0":
        raise McpRemoteProtocolError("Invalid JSON-RPC version.")
    return payload


def _read_json_or_sse_response(
    response: httpx.Response,
    *,
    expect_id: int | None,
) -> dict[str, Any]:
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type == "application/json" or not content_type:
        try:
            return _parse_jsonrpc_response(response.json())
        except (ValueError, json.JSONDecodeError) as exc:
            raise McpRemoteProtocolError("Invalid JSON response from MCP server.") from exc

    if content_type != "text/event-stream":
        body_preview = (response.text or "")[:200]
        raise McpRemoteProtocolError(f"Unsupported response Content-Type: {content_type} ({body_preview})")

    for event in _iter_sse_events(response.iter_lines()):
        data = (event.get("data") or "").strip()
        if not data:
            continue
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            continue
        parsed = _parse_jsonrpc_response(message)
        if expect_id is None:
            return parsed
        if parsed.get("id") == expect_id:
            return parsed
    raise McpRemoteProtocolError("No JSON-RPC response received in SSE stream.")
