from __future__ import annotations

from unittest import mock

import httpx
from django.test import SimpleTestCase

from apps.mcp import remote_client


class McpRemoteClientSsrfTests(SimpleTestCase):
    def test_validate_blocks_private_ip_literal(self) -> None:
        with self.assertRaises(remote_client.McpRemoteSsrBlockedError):
            remote_client._validate_mcp_url_for_ssrf("http://127.0.0.1:8080/mcp", action="test")

    def test_validate_blocks_localhost(self) -> None:
        with self.assertRaises(remote_client.McpRemoteSsrBlockedError):
            remote_client._validate_mcp_url_for_ssrf("https://localhost/mcp", action="test")

    def test_validate_blocks_mixed_dns_resolution(self) -> None:
        def fake_getaddrinfo(host: str, port: int, type: int | None = None):  # noqa: A002 - match socket signature
            self.assertEqual(host, "mixed.example")
            self.assertEqual(port, 443)
            return [
                (2, None, None, "", ("93.184.216.34", port)),
                (2, None, None, "", ("127.0.0.1", port)),
            ]

        with mock.patch("apps.mcp.remote_client.socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with self.assertRaises(remote_client.McpRemoteSsrBlockedError):
                remote_client._validate_mcp_url_for_ssrf("https://mixed.example/mcp", action="test")

    def test_legacy_sse_bootstrap_blocks_endpoint_event_private_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            content = b"event: endpoint\ndata: http://127.0.0.1/internal\n\n"
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=content,
                request=request,
            )

        def safe_getaddrinfo(host: str, port: int, type: int | None = None):  # noqa: A002 - match socket signature
            self.assertEqual(host, "example.com")
            return [(2, None, None, "", ("93.184.216.34", port))]

        transport = httpx.MockTransport(handler)
        with mock.patch("apps.mcp.remote_client.socket.getaddrinfo", side_effect=safe_getaddrinfo):
            with httpx.Client(transport=transport) as client:
                with self.assertRaises(remote_client.McpRemoteSsrBlockedError):
                    remote_client._legacy_sse_bootstrap(client=client, sse_url="https://example.com/mcp", headers=None)

    def test_redirect_to_private_is_blocked_by_policy(self) -> None:
        request = httpx.Request("POST", "https://example.com/mcp")
        response = httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/internal"},
            request=request,
        )
        with self.assertRaises(remote_client.McpRemoteSsrBlockedError):
            remote_client._raise_for_redirect_response(response, action="test")

