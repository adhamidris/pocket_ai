from __future__ import annotations

import json
import socket
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    McpConnection,
    McpConnectionAuthType,
    McpConnectionStatus,
    RegistrationSession,
)
from apps.mcp.remote_client import McpRemoteServerInfo, McpRemoteSession, McpRemoteTool


User = get_user_model()


class McpConnectionsApiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme",
            industry="Retail",
        )
        self.client.force_login(self.user)

        self.resolve_patcher = mock.patch(
            "apps.api.mcp_connections.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        )
        self.resolve_patcher.start()

    def tearDown(self) -> None:
        self.resolve_patcher.stop()
        super().tearDown()

    def test_create_and_list_mcp_connection(self) -> None:
        create_url = reverse("api:mcp-connections")
        response = self.client.post(
            create_url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "name": "Docs MCP",
                    "serverUrl": "https://example.com/mcp",
                    "enabled": True,
                    "auth": {"type": "none"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("connection", payload)
        self.assertEqual(payload["connection"]["name"], "Docs MCP")
        self.assertEqual(payload["connection"]["status"], "enabled")

        list_resp = self.client.get(create_url, {"business_id": str(self.business.id)})
        self.assertEqual(list_resp.status_code, 200)
        list_payload = list_resp.json()
        self.assertEqual(len(list_payload.get("connections") or []), 1)
        self.assertIn("marketplace", list_payload)

    def test_agent_opt_out_toggle(self) -> None:
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Test MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
        )
        agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Support Agent",
            role="support",
            tone="friendly",
        )

        agents_url = reverse("api:mcp-connection-agents", kwargs={"connection_id": connection.id})
        resp = self.client.get(agents_url, {"business_id": str(self.business.id)})
        self.assertEqual(resp.status_code, 200)
        agents = resp.json().get("agents") or []
        self.assertEqual(len(agents), 1)
        self.assertTrue(agents[0]["enabled"])

        toggle_resp = self.client.post(
            agents_url,
            data=json.dumps({"businessId": str(self.business.id), "agentId": str(agent.id), "enabled": False}),
            content_type="application/json",
        )
        self.assertEqual(toggle_resp.status_code, 200)

        resp2 = self.client.get(agents_url, {"business_id": str(self.business.id)})
        self.assertEqual(resp2.status_code, 200)
        agents2 = resp2.json().get("agents") or []
        self.assertFalse(agents2[0]["enabled"])

    @mock.patch("apps.api.mcp_connections.test_mcp_server")
    def test_test_endpoint_caches_tools(self, mock_test_server) -> None:
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Test MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
        )
        mock_test_server.return_value = (
            McpRemoteSession(
                transport="streamable_http",
                endpoint_url="https://example.com/mcp",
                message_url=None,
                session_id=None,
                protocol_version="2025-11-25",
                server_info=McpRemoteServerInfo(name="ExampleServer", version="1.0.0"),
            ),
            [McpRemoteTool(name="get_weather", description="Get weather", input_schema={"type": "object"})],
        )
        test_url = reverse("api:mcp-connection-test", kwargs={"connection_id": connection.id})
        resp = self.client.post(
            test_url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        connection.refresh_from_db()
        cache = (connection.metadata or {}).get("tool_cache") or {}
        self.assertEqual(cache.get("tool_count"), 1)
        self.assertTrue(cache.get("tested_at"))

