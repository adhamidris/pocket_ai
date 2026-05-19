from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Mapping
from urllib.parse import urljoin

import httpx

from .errors import (
    McpRemoteProtocolError,
    McpRemoteSsrBlockedError,
    McpRemoteTransportError,
)
from .protocol import _iter_sse_events, _jsonrpc_request, _parse_jsonrpc_response
from .security import _validate_mcp_url_for_ssrf
from .transport import (
    _raise_for_redirect_response,
    _raise_transport_for_http_status,
    _request_with_transport_errors,
)
from .types import McpRemoteServerInfo, McpRemoteSession, McpRemoteTool


logger = logging.getLogger(__name__)


def _legacy_sse_bootstrap(
    *,
    client: httpx.Client,
    sse_url: str,
    headers: Mapping[str, str] | None,
) -> tuple[httpx.Response, Iterator[dict[str, str]], str]:
    _validate_mcp_url_for_ssrf(sse_url, action="legacy_sse.bootstrap")
    stream_headers = dict(headers or {})
    stream_headers["Accept"] = "text/event-stream"
    try:
        sse_response = client.stream("GET", sse_url, headers=stream_headers).__enter__()
    except httpx.TimeoutException as exc:
        raise McpRemoteTransportError("MCP request timed out during legacy_sse.bootstrap.") from exc
    except httpx.RequestError as exc:
        raise McpRemoteTransportError(f"MCP network error during legacy_sse.bootstrap: {exc.__class__.__name__}.") from exc
    _raise_for_redirect_response(sse_response, action="legacy_sse.bootstrap")
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
        try:
            _validate_mcp_url_for_ssrf(message_url, action="legacy_sse.endpoint")
        except McpRemoteSsrBlockedError:
            sse_response.close()
            raise
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
    max_pages: int = 50,
) -> tuple[McpRemoteSession, list[McpRemoteTool]]:
    """
    Initialize a legacy SSE MCP session and list tools with pagination support.

    Args:
        client: HTTP client instance
        sse_url: SSE endpoint URL
        headers: Optional auth/custom headers
        protocol_version: MCP protocol version
        max_pages: Safety limit to prevent infinite pagination (default 50)

    Returns:
        Tuple of (session, tools)
    """
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
        _raise_for_redirect_response(init_resp, action="legacy_sse.initialize")
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
        _raise_for_redirect_response(notif_resp, action="legacy_sse.initialized")
        try:
            notif_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_transport_for_http_status(exc, action="legacy_sse.initialized")

        # Paginated tools/list with cursor support
        tools: list[McpRemoteTool] = []
        cursor: str | None = None
        request_id = 2
        page_count = 0

        while page_count < max_pages:
            page_count += 1
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            list_payload = _jsonrpc_request("tools/list", request_id=request_id, params=params)
            list_resp = _request_with_transport_errors(
                "legacy_sse.tools_list",
                lambda: client.post(message_url, json=list_payload, headers=post_headers),
            )
            _raise_for_redirect_response(list_resp, action="legacy_sse.tools_list")
            try:
                list_resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _raise_transport_for_http_status(exc, action="legacy_sse.tools_list")

            list_parsed = _legacy_sse_wait_for_response(events, expect_id=request_id)
            if "error" in list_parsed:
                raise McpRemoteProtocolError(f"MCP tools/list error: {list_parsed.get('error')}")
            list_result = list_parsed.get("result") if isinstance(list_parsed.get("result"), dict) else {}
            tools_in = list_result.get("tools")
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

            # Check for pagination cursor
            next_cursor = list_result.get("nextCursor")
            if isinstance(next_cursor, str) and next_cursor.strip():
                cursor = next_cursor.strip()
                request_id += 1
                continue
            # No more pages - exit the loop
            break

        if page_count >= max_pages:
            logger.warning(
                "mcp_legacy_sse_tools_list_pagination_limit url=%s pages=%s tools=%s",
                sse_url,
                page_count,
                len(tools),
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
