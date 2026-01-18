from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urljoin

import httpx


logger = logging.getLogger(__name__)

DEFAULT_PROTOCOL_VERSION = "2025-11-25"


class McpRemoteError(RuntimeError):
    pass


class McpRemoteTransportError(McpRemoteError):
    pass


class McpRemoteProtocolError(McpRemoteError):
    pass


class McpRemoteStreamableNotSupported(McpRemoteTransportError):
    """Raised when a server rejects Streamable HTTP initialize (legacy transport expected)."""


class McpRemoteHttpStatusError(McpRemoteTransportError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


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
        resp = _request_with_transport_errors(action, lambda: client.post(url, json=payload, headers=dict(headers or {})))
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


@dataclass(frozen=True)
class McpRemoteServerInfo:
    name: str | None = None
    title: str | None = None
    version: str | None = None
    description: str | None = None
    website_url: str | None = None


@dataclass(frozen=True)
class McpRemoteTool:
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class McpRemoteSession:
    transport: str
    endpoint_url: str
    message_url: str | None
    session_id: str | None
    protocol_version: str
    server_info: McpRemoteServerInfo | None


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


def _streamable_http_initialize(
    *,
    client: httpx.Client,
    endpoint_url: str,
    headers: Mapping[str, str] | None,
    protocol_version: str,
) -> McpRemoteSession:
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
    post_headers["Accept"] = "application/json, text/event-stream"
    post_headers["Content-Type"] = "application/json"

    response = _post_jsonrpc_with_retry_after(
        client=client,
        url=endpoint_url,
        payload=init_payload,
        headers=post_headers,
        action="streamable_http.initialize",
    )
    if response.status_code in {400, 404, 405}:
        raise McpRemoteStreamableNotSupported(f"Streamable HTTP initialize rejected ({response.status_code}).")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _raise_transport_for_http_status(exc, action="streamable_http.initialize")

    session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("MCP-Session-Id")
    parsed = _read_json_or_sse_response(response, expect_id=init_id)
    if "error" in parsed:
        raise McpRemoteProtocolError(f"MCP initialize error: {parsed.get('error')}")
    result = parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
    negotiated_version = str(result.get("protocolVersion") or protocol_version)

    server_info = None
    info_in = result.get("serverInfo")
    if isinstance(info_in, dict):
        server_info = McpRemoteServerInfo(
            name=info_in.get("name"),
            title=info_in.get("title"),
            version=info_in.get("version"),
            description=info_in.get("description"),
            website_url=info_in.get("websiteUrl"),
        )

    # Client MUST notify initialized before normal operations.
    initialized_payload = _jsonrpc_request("notifications/initialized", request_id=None)
    init_notif_headers = _headers_with_protocol(headers, protocol_version=negotiated_version, session_id=session_id)
    init_notif_headers["Accept"] = "application/json, text/event-stream"
    init_notif_headers["Content-Type"] = "application/json"
    notif_resp = _post_jsonrpc_with_retry_after(
        client=client,
        url=endpoint_url,
        payload=initialized_payload,
        headers=init_notif_headers,
        action="streamable_http.initialized",
    )
    if notif_resp.status_code not in {200, 202, 204}:
        try:
            notif_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_transport_for_http_status(exc, action="streamable_http.initialized")

    return McpRemoteSession(
        transport="streamable_http",
        endpoint_url=endpoint_url,
        message_url=None,
        session_id=session_id,
        protocol_version=negotiated_version,
        server_info=server_info,
    )


def _streamable_http_list_tools(
    *,
    client: httpx.Client,
    session: McpRemoteSession,
    headers: Mapping[str, str] | None,
    max_pages: int = 50,
) -> list[McpRemoteTool]:
    """
    Fetch tools from an MCP server using cursor-based pagination.

    Args:
        client: HTTP client instance
        session: Active MCP session
        headers: Optional auth/custom headers
        max_pages: Safety limit to prevent infinite pagination (default 50)

    Returns:
        List of discovered tools
    """
    tools: list[McpRemoteTool] = []
    cursor: str | None = None
    request_id = 2
    page_count = 0

    while page_count < max_pages:
        page_count += 1
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        payload = _jsonrpc_request("tools/list", request_id=request_id, params=params)
        call_headers = _headers_with_protocol(headers, protocol_version=session.protocol_version, session_id=session.session_id)
        call_headers["Accept"] = "application/json, text/event-stream"
        call_headers["Content-Type"] = "application/json"
        resp = _post_jsonrpc_with_retry_after(
            client=client,
            url=session.endpoint_url,
            payload=payload,
            headers=call_headers,
            action="streamable_http.tools_list",
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_transport_for_http_status(exc, action="streamable_http.tools_list")
        parsed = _read_json_or_sse_response(resp, expect_id=request_id)
        if "error" in parsed:
            raise McpRemoteProtocolError(f"MCP tools/list error: {parsed.get('error')}")
        result = parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
        tools_in = result.get("tools")
        if isinstance(tools_in, list):
            for tool in tools_in:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "").strip()
                if not name:
                    continue
                tools.append(
                    McpRemoteTool(
                        name=name,
                        title=tool.get("title"),
                        description=tool.get("description"),
                        input_schema=tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else None,
                    )
                )
        next_cursor = result.get("nextCursor")
        if isinstance(next_cursor, str) and next_cursor.strip():
            cursor = next_cursor.strip()
            request_id += 1
            continue
        # No more pages - exit the loop
        break

    if page_count >= max_pages:
        logger.warning(
            "mcp_tools_list_pagination_limit url=%s pages=%s tools=%s",
            session.endpoint_url,
            page_count,
            len(tools),
        )

    return tools


def _legacy_sse_bootstrap(
    *,
    client: httpx.Client,
    sse_url: str,
    headers: Mapping[str, str] | None,
) -> tuple[httpx.Response, Iterator[dict[str, str]], str]:
    stream_headers = dict(headers or {})
    stream_headers["Accept"] = "text/event-stream"
    try:
        sse_response = client.stream("GET", sse_url, headers=stream_headers).__enter__()
    except httpx.TimeoutException as exc:
        raise McpRemoteTransportError("MCP request timed out during legacy_sse.bootstrap.") from exc
    except httpx.RequestError as exc:
        raise McpRemoteTransportError(f"MCP network error during legacy_sse.bootstrap: {exc.__class__.__name__}.") from exc
    try:
        sse_response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            sse_response.close()
        except Exception:
            pass
        _raise_transport_for_http_status(exc, action="legacy_sse.bootstrap")
    events = _iter_sse_events(sse_response.iter_lines())
    for event in events:
        if (event.get("event") or "").strip() != "endpoint":
            continue
        data = (event.get("data") or "").strip()
        if not data:
            continue
        message_url = urljoin(sse_url, data)
        return sse_response, events, message_url
    sse_response.close()
    raise McpRemoteTransportError("Legacy SSE transport did not provide an endpoint event.")


def _legacy_sse_wait_for_response(
    events: Iterator[dict[str, str]],
    *,
    expect_id: int,
) -> dict[str, Any]:
    for event in events:
        data = (event.get("data") or "").strip()
        if not data:
            continue
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            continue
        parsed = _parse_jsonrpc_response(message)
        if parsed.get("id") == expect_id:
            return parsed
    raise McpRemoteProtocolError("No JSON-RPC response received from legacy SSE stream.")


def _legacy_sse_initialize_and_list_tools(
    *,
    client: httpx.Client,
    sse_url: str,
    headers: Mapping[str, str] | None,
    protocol_version: str,
) -> tuple[McpRemoteSession, list[McpRemoteTool]]:
    sse_response, events, message_url = _legacy_sse_bootstrap(client=client, sse_url=sse_url, headers=headers)
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
        try:
            init_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_transport_for_http_status(exc, action="legacy_sse.initialize")

        parsed = _legacy_sse_wait_for_response(events, expect_id=init_id)
        if "error" in parsed:
            raise McpRemoteProtocolError(f"MCP initialize error: {parsed.get('error')}")
        result = parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
        negotiated_version = str(result.get("protocolVersion") or protocol_version)

        server_info = None
        info_in = result.get("serverInfo")
        if isinstance(info_in, dict):
            server_info = McpRemoteServerInfo(
                name=info_in.get("name"),
                title=info_in.get("title"),
                version=info_in.get("version"),
                description=info_in.get("description"),
                website_url=info_in.get("websiteUrl"),
            )

        initialized_payload = _jsonrpc_request("notifications/initialized", request_id=None)
        notif_resp = _request_with_transport_errors(
            "legacy_sse.initialized",
            lambda: client.post(message_url, json=initialized_payload, headers=post_headers),
        )
        try:
            notif_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_transport_for_http_status(exc, action="legacy_sse.initialized")

        list_id = 2
        list_payload = _jsonrpc_request("tools/list", request_id=list_id, params={})
        list_resp = _request_with_transport_errors(
            "legacy_sse.tools_list",
            lambda: client.post(message_url, json=list_payload, headers=post_headers),
        )
        try:
            list_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_transport_for_http_status(exc, action="legacy_sse.tools_list")

        list_parsed = _legacy_sse_wait_for_response(events, expect_id=list_id)
        if "error" in list_parsed:
            raise McpRemoteProtocolError(f"MCP tools/list error: {list_parsed.get('error')}")
        list_result = list_parsed.get("result") if isinstance(list_parsed.get("result"), dict) else {}
        tools_in = list_result.get("tools")
        tools: list[McpRemoteTool] = []
        if isinstance(tools_in, list):
            for tool in tools_in:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "").strip()
                if not name:
                    continue
                tools.append(
                    McpRemoteTool(
                        name=name,
                        title=tool.get("title"),
                        description=tool.get("description"),
                        input_schema=tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else None,
                    )
                )

        session = McpRemoteSession(
            transport="legacy_sse",
            endpoint_url=sse_url,
            message_url=message_url,
            session_id=None,
            protocol_version=negotiated_version,
            server_info=server_info,
        )
        return session, tools
    finally:
        try:
            sse_response.close()
        except Exception:
            pass


def test_mcp_server(
    *,
    server_url: str,
    headers: Mapping[str, str] | None = None,
    timeout_s: float = 20.0,
    protocol_version: str = DEFAULT_PROTOCOL_VERSION,
) -> tuple[McpRemoteSession, list[McpRemoteTool]]:
    """
    Validate an MCP server by completing initialization + tools/list.

    Attempts Streamable HTTP first. If rejected with 400/404/405, callers may
    fall back to legacy HTTP+SSE (not implemented here yet).
    """

    start = time.monotonic()
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        try:
            session = _streamable_http_initialize(
                client=client,
                endpoint_url=server_url,
                headers=headers,
                protocol_version=protocol_version,
            )
            tools = _streamable_http_list_tools(client=client, session=session, headers=headers)
        except McpRemoteStreamableNotSupported:
            session, tools = _legacy_sse_initialize_and_list_tools(
                client=client,
                sse_url=server_url,
                headers=headers,
                protocol_version=protocol_version,
            )
    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "mcp_server_test transport=%s url=%s tools=%s duration_ms=%s",
        session.transport,
        server_url,
        len(tools),
        elapsed_ms,
    )
    return session, tools


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
    timeout_s: float = 45.0,
    protocol_version: str = DEFAULT_PROTOCOL_VERSION,
) -> dict[str, Any]:
    """
    Minimal Streamable HTTP client that performs initialize + tools/call.

    This is intentionally stateless (new session per call) for correctness in beta.
    """

    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
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
                call_resp = _request_with_transport_errors(
                    "legacy_sse.tools_call",
                    lambda: client.post(message_url, json=payload, headers=post_headers),
                )
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
