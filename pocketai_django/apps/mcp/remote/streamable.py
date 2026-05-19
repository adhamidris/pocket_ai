from __future__ import annotations

import logging
from typing import Any, Mapping

import httpx

from .errors import McpRemoteProtocolError, McpRemoteStreamableNotSupported
from .protocol import _headers_with_protocol, _jsonrpc_request, _read_json_or_sse_response
from .transport import _post_jsonrpc_with_retry_after, _raise_transport_for_http_status
from .types import McpRemoteServerInfo, McpRemoteSession, McpRemoteTool


logger = logging.getLogger(__name__)


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
