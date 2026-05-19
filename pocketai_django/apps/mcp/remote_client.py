from __future__ import annotations

import logging
import socket
import time
from typing import Any, Mapping

import httpx

from .remote.errors import (
    McpRemoteError,
    McpRemoteHttpStatusError,
    McpRemoteProtocolError,
    McpRemoteSsrBlockedError,
    McpRemoteStreamableNotSupported,
    McpRemoteTransportError,
)
from .remote.security import _is_forbidden_ip, _validate_mcp_url_for_ssrf
from .remote.legacy_sse import (
    _legacy_sse_bootstrap,
    _legacy_sse_initialize_and_list_tools,
    _legacy_sse_wait_for_response,
)
from .remote.protocol import (
    _headers_with_protocol,
    _iter_sse_events,
    _jsonrpc_request,
    _parse_jsonrpc_response,
    _read_json_or_sse_response,
)
from .remote.settings import DEFAULT_PROTOCOL_VERSION
from .remote.streamable import _streamable_http_initialize, _streamable_http_list_tools
from .remote.tool_call import call_mcp_tool_streamable_http, normalize_mcp_tool_result
from .remote.transport import (
    _http_reason_phrase,
    _parse_retry_after_seconds,
    _post_jsonrpc_with_retry_after,
    _raise_for_redirect_response,
    _raise_transport_for_http_status,
    _request_with_transport_errors,
)
from .remote.types import McpRemoteServerInfo, McpRemoteSession, McpRemoteTool


logger = logging.getLogger(__name__)







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
    _validate_mcp_url_for_ssrf(server_url, action="mcp_server_test")
    with httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False) as client:
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
