from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as django_timezone

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    EmailAccount,
    McpConnection,
    McpConnectionApprovalMode,
    McpConnectionAuthType,
    McpConnectionStatus,
    McpConnectionToolSetting,
    McpToolOperationType,
    IntegrationAccount,
    IntegrationAccountStatus,
    IntegrationProvider,
    IntegrationType,
    RegistrationSession,
)
from apps.mcp.connectors import _is_cache_expired, build_remote_tool_definitions
from apps.mcp.remote_client import (
    McpRemoteHttpStatusError,
    McpRemoteServerInfo,
    McpRemoteSession,
    McpRemoteTool,
    _streamable_http_list_tools,
)


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
        connections = list_payload.get("connections") or []
        self.assertEqual(len(connections), 1)
        self.assertIn("testJob", connections[0])
        self.assertEqual(connections[0]["testJob"]["status"], "queued")
        self.assertIn("marketplace", list_payload)

    def test_create_connection_with_approval_mode(self) -> None:
        create_url = reverse("api:mcp-connections")
        response = self.client.post(
            create_url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "name": "GitHub MCP",
                    "serverUrl": "https://example.com/mcp",
                    "enabled": True,
                    "defaultApprovalMode": McpConnectionApprovalMode.AUTO,
                    "auth": {"type": "none"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["connection"]["defaultApprovalMode"], McpConnectionApprovalMode.AUTO)
        connection = McpConnection.objects.get(id=payload["connection"]["id"])
        self.assertEqual(connection.default_approval_mode, McpConnectionApprovalMode.AUTO)

    def test_update_connection_approval_mode(self) -> None:
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Update MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
        )
        update_url = reverse("api:mcp-connection-detail", kwargs={"connection_id": connection.id})
        response = self.client.put(
            update_url,
            data=json.dumps({"businessId": str(self.business.id), "defaultApprovalMode": McpConnectionApprovalMode.APPROVE_ALL}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        connection.refresh_from_db()
        self.assertEqual(connection.default_approval_mode, McpConnectionApprovalMode.APPROVE_ALL)

    def test_marketplace_includes_github_official_entry(self) -> None:
        url = reverse("api:mcp-connections")
        resp = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(resp.status_code, 200)
        marketplace = resp.json().get("marketplace") or []
        github = next((item for item in marketplace if item.get("key") == "github"), None)
        self.assertIsNotNone(github)
        self.assertEqual(github.get("recommendedAuth"), "bearer")
        self.assertEqual(github.get("serverUrl"), "https://api.githubcopilot.com/mcp/")

    def test_default_surface_excludes_native_oauth_marketplace_and_accounts(self) -> None:
        EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider="google",
            email_address="owner@example.com",
            status=EmailAccountStatus.CONNECTED,
        )
        IntegrationAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            integration_type=IntegrationType.GOOGLE_DRIVE,
            provider=IntegrationProvider.GOOGLE,
            account_identifier="owner@example.com",
            status=IntegrationAccountStatus.CONNECTED,
        )

        url = reverse("api:mcp-connections")
        resp = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(resp.status_code, 200)

        payload = resp.json()
        marketplace = payload.get("marketplace") or []
        connection_types = {str(item.get("connectionType") or "").strip().lower() for item in marketplace}
        self.assertNotIn("email_oauth", connection_types)
        self.assertNotIn("integration_oauth", connection_types)
        self.assertEqual(payload.get("emailAccounts"), [])
        self.assertEqual(payload.get("integrationAccounts"), [])

    def test_integrations_surface_includes_native_oauth_marketplace_and_accounts(self) -> None:
        email_account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider="google",
            email_address="owner@example.com",
            status=EmailAccountStatus.CONNECTED,
        )
        integration_account = IntegrationAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            integration_type=IntegrationType.GOOGLE_DRIVE,
            provider=IntegrationProvider.GOOGLE,
            account_identifier="owner@example.com",
            status=IntegrationAccountStatus.CONNECTED,
        )

        url = reverse("api:mcp-connections")
        resp = self.client.get(
            url,
            {
                "business_id": str(self.business.id),
                "surface": "integrations",
            },
        )
        self.assertEqual(resp.status_code, 200)

        payload = resp.json()
        marketplace = payload.get("marketplace") or []
        marketplace_keys = {str(item.get("key") or "").strip() for item in marketplace}
        self.assertIn("gmail", marketplace_keys)
        self.assertIn("google_drive", marketplace_keys)

        email_rows = payload.get("emailAccounts") or []
        integration_rows = payload.get("integrationAccounts") or []
        self.assertTrue(any(row.get("id") == str(email_account.id) for row in email_rows))
        self.assertTrue(any(row.get("id") == str(integration_account.id) for row in integration_rows))

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

    def test_tools_endpoint_returns_tool_settings(self) -> None:
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Tool Settings MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
            default_approval_mode=McpConnectionApprovalMode.APPROVE_WRITES,
            metadata={
                "tool_cache": {
                    "tool_count": 2,
                    "tools": [
                        {"name": "list_issues", "description": "List issues", "inputSchema": {"type": "object"}},
                        {"name": "create_issue", "description": "Create issue", "inputSchema": {"type": "object"}},
                    ],
                }
            },
        )
        McpConnectionToolSetting.objects.create(
            connection=connection,
            tool_name="create_issue",
            operation_type=McpToolOperationType.WRITE,
            approval_mode=McpConnectionApprovalMode.APPROVE_ALL,
            description="Create a new issue",
        )
        tools_url = reverse("api:mcp-connection-tools", kwargs={"connection_id": connection.id})
        resp = self.client.get(tools_url, {"business_id": str(self.business.id)})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        tools = payload.get("tools") or []
        self.assertEqual(len(tools), 2)
        create_tool = next((item for item in tools if item.get("toolName") == "create_issue"), None)
        self.assertIsNotNone(create_tool)
        self.assertEqual(create_tool["approvalMode"], McpConnectionApprovalMode.APPROVE_ALL)
        self.assertEqual(create_tool["operationType"], McpToolOperationType.WRITE)
        list_tool = next((item for item in tools if item.get("toolName") == "list_issues"), None)
        self.assertIsNotNone(list_tool)
        self.assertIsNone(list_tool["approvalMode"])
        self.assertEqual(list_tool["effectiveApprovalMode"], McpConnectionApprovalMode.APPROVE_WRITES)

    def test_tools_endpoint_updates_tool_settings(self) -> None:
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Update Tools MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
            metadata={
                "tool_cache": {
                    "tool_count": 1,
                    "tools": [{"name": "create_issue", "description": "Create issue", "inputSchema": {"type": "object"}}],
                }
            },
        )
        tools_url = reverse("api:mcp-connection-tools", kwargs={"connection_id": connection.id})
        resp = self.client.post(
            tools_url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "updates": [
                        {
                            "toolName": "create_issue",
                            "operationType": McpToolOperationType.WRITE,
                            "approvalMode": McpConnectionApprovalMode.APPROVE_ALL,
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        applied = resp.json().get("applied") or []
        self.assertEqual(len(applied), 1)
        setting = McpConnectionToolSetting.objects.get(connection=connection, tool_name="create_issue")
        self.assertEqual(setting.operation_type, McpToolOperationType.WRITE)
        self.assertEqual(setting.approval_mode, McpConnectionApprovalMode.APPROVE_ALL)

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

    @mock.patch("apps.api.mcp_connections.test_mcp_server")
    def test_test_endpoint_returns_429_on_upstream_rate_limit(self, mock_test_server) -> None:
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Rate Limited MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
        )
        mock_test_server.side_effect = McpRemoteHttpStatusError(
            "MCP request failed during streamable_http.tools_list (HTTP 429 Too Many Requests). Retry-After: 60.",
            status_code=429,
            retry_after="60",
        )
        test_url = reverse("api:mcp-connection-test", kwargs={"connection_id": connection.id})
        resp = self.client.post(
            test_url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 429)
        payload = resp.json()
        self.assertEqual(payload.get("upstreamStatus"), 429)
        self.assertEqual(payload.get("retryAfter"), "60")
        connection.refresh_from_db()
        cache = (connection.metadata or {}).get("tool_cache") or {}
        self.assertEqual(cache.get("status_code"), 429)

    def test_ssrf_blocks_localhost(self) -> None:
        """SSRF validation should block localhost URLs."""
        create_url = reverse("api:mcp-connections")
        response = self.client.post(
            create_url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "name": "Localhost MCP",
                    "serverUrl": "http://localhost:8080/mcp",
                    "enabled": True,
                    "auth": {"type": "none"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("not allowed", payload.get("message", "").lower())

    def test_ssrf_blocks_private_ip_literal(self) -> None:
        """SSRF validation should block private IP addresses in URLs."""
        create_url = reverse("api:mcp-connections")
        response = self.client.post(
            create_url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "name": "Private IP MCP",
                    "serverUrl": "http://192.168.1.1/mcp",
                    "enabled": True,
                    "auth": {"type": "none"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("private", payload.get("message", "").lower())

    def test_ssrf_blocks_hostname_resolving_to_private_ip(self) -> None:
        """SSRF validation should block hostnames that resolve to private IPs."""
        create_url = reverse("api:mcp-connections")
        # Stop the default patcher temporarily
        self.resolve_patcher.stop()
        # Mock DNS to return a private IP
        with mock.patch(
            "apps.api.mcp_connections.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
        ):
            response = self.client.post(
                create_url,
                data=json.dumps(
                    {
                        "businessId": str(self.business.id),
                        "name": "Sneaky MCP",
                        "serverUrl": "https://sneaky-internal.example.com/mcp",
                        "enabled": True,
                        "auth": {"type": "none"},
                    }
                ),
                content_type="application/json",
            )
        # Restart the patcher for other tests
        self.resolve_patcher.start()
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("private", payload.get("message", "").lower())

    @mock.patch("apps.api.mcp_connections.test_mcp_server")
    def test_test_endpoint_sets_cache_expiration(self, mock_test_server) -> None:
        """Test endpoint should set expires_at on the tool cache."""
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Expiring MCP",
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
        self.assertIsNotNone(cache.get("expires_at"))
        # Verify expires_at is in the future
        expires_at = datetime.fromisoformat(cache["expires_at"].replace("Z", "+00:00"))
        self.assertGreater(expires_at, datetime.now(timezone.utc))

    def test_agent_approval_defaults_get_lists_agents(self) -> None:
        agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Support Agent",
            role="support",
            tone="friendly",
            status="active",
        )
        url = reverse("api:mcp-agent-approval-defaults")
        resp = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("businessId"), str(self.business.id))
        agents = payload.get("agents") or []
        self.assertTrue(any(item.get("id") == str(agent.id) for item in agents))

    def test_agent_approval_defaults_post_updates_mode(self) -> None:
        agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Support Agent",
            role="support",
            tone="friendly",
            status="active",
        )
        url = reverse("api:mcp-agent-approval-defaults")
        resp = self.client.post(
            url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "agentId": str(agent.id),
                    "defaultApprovalMode": McpConnectionApprovalMode.APPROVE_ALL,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        agent.refresh_from_db()
        self.assertEqual(agent.mcp_default_approval_mode, McpConnectionApprovalMode.APPROVE_ALL)


class CacheTTLTests(TestCase):
    """Tests for tool cache TTL expiration logic."""

    def test_is_cache_expired_returns_false_for_future_expiry(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        cache = {"expires_at": future, "tools": []}
        self.assertFalse(_is_cache_expired(cache))

    def test_is_cache_expired_returns_true_for_past_expiry(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        cache = {"expires_at": past, "tools": []}
        self.assertTrue(_is_cache_expired(cache))

    def test_is_cache_expired_returns_false_for_missing_expiry(self) -> None:
        """Legacy caches without expires_at should be considered valid."""
        cache = {"tools": [{"name": "test_tool"}]}
        self.assertFalse(_is_cache_expired(cache))

    def test_build_remote_tool_definitions_skips_expired_cache(self) -> None:
        """Expired tool caches remain usable (stale schema fallback)."""
        user = User.objects.create_user(email="test@example.com", password="test123")
        registration = RegistrationSession.objects.create(user=user)
        business = BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Test Business",
            industry="Tech",
        )
        # Create connection with expired cache
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        connection = McpConnection.objects.create(
            business_profile=business,
            created_by=user,
            name="Expired MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
            metadata={
                "tool_cache": {
                    "expires_at": expired_time,
                    "tools": [{"name": "expired_tool", "description": "Should still be exposed"}],
                }
            },
        )

        tool_defs, registry = build_remote_tool_definitions([connection])
        self.assertEqual(len(tool_defs), 1)
        self.assertEqual(len(registry), 1)

    def test_build_remote_tool_definitions_includes_valid_cache(self) -> None:
        """build_remote_tool_definitions should include connections with valid caches."""
        user = User.objects.create_user(email="test2@example.com", password="test123")
        registration = RegistrationSession.objects.create(user=user)
        business = BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Test Business 2",
            industry="Tech",
        )
        # Create connection with valid cache
        future_time = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        connection = McpConnection.objects.create(
            business_profile=business,
            created_by=user,
            name="Valid MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
            metadata={
                "tool_cache": {
                    "expires_at": future_time,
                    "tools": [{"name": "valid_tool", "description": "Should be included", "inputSchema": {"type": "object"}}],
                }
            },
        )

        tool_defs, registry = build_remote_tool_definitions([connection])
        self.assertEqual(len(tool_defs), 1)
        self.assertEqual(len(registry), 1)
        # Check the tool name follows the expected pattern
        tool_name = list(registry.keys())[0]
        self.assertTrue(tool_name.startswith("mcp_"))


class RemoteClientPaginationTests(TestCase):
    """Tests for remote_client pagination and loop termination."""

    @mock.patch("apps.mcp.remote_client._post_jsonrpc_with_retry_after")
    @mock.patch("apps.mcp.remote_client._read_json_or_sse_response")
    def test_pagination_terminates_without_next_cursor(self, mock_read, mock_post) -> None:
        """Tool listing should terminate cleanly when no nextCursor is returned."""
        mock_response = mock.MagicMock()
        mock_response.raise_for_status = mock.MagicMock()
        mock_post.return_value = mock_response
        mock_read.return_value = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {"name": "tool_one", "description": "First tool"},
                    {"name": "tool_two", "description": "Second tool"},
                ]
                # No nextCursor - should terminate
            },
        }

        session = McpRemoteSession(
            transport="streamable_http",
            endpoint_url="https://example.com/mcp",
            message_url=None,
            session_id="test-session",
            protocol_version="2025-11-25",
            server_info=None,
        )

        # Create a mock client
        mock_client = mock.MagicMock()

        tools = _streamable_http_list_tools(client=mock_client, session=session, headers=None)

        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0].name, "tool_one")
        self.assertEqual(tools[1].name, "tool_two")
        # Should only call once since there's no nextCursor
        self.assertEqual(mock_post.call_count, 1)

    @mock.patch("apps.mcp.remote_client._post_jsonrpc_with_retry_after")
    @mock.patch("apps.mcp.remote_client._read_json_or_sse_response")
    def test_pagination_follows_next_cursor(self, mock_read, mock_post) -> None:
        """Tool listing should follow nextCursor for paginated results."""
        mock_response = mock.MagicMock()
        mock_response.raise_for_status = mock.MagicMock()
        mock_post.return_value = mock_response

        # First page has nextCursor, second page doesn't
        mock_read.side_effect = [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [{"name": "page1_tool", "description": "Page 1 tool"}],
                    "nextCursor": "cursor_page_2",
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "tools": [{"name": "page2_tool", "description": "Page 2 tool"}],
                    # No nextCursor - should terminate
                },
            },
        ]

        session = McpRemoteSession(
            transport="streamable_http",
            endpoint_url="https://example.com/mcp",
            message_url=None,
            session_id="test-session",
            protocol_version="2025-11-25",
            server_info=None,
        )

        mock_client = mock.MagicMock()
        tools = _streamable_http_list_tools(client=mock_client, session=session, headers=None)

        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0].name, "page1_tool")
        self.assertEqual(tools[1].name, "page2_tool")
        # Should call twice (two pages)
        self.assertEqual(mock_post.call_count, 2)

    @mock.patch("apps.mcp.remote_client._post_jsonrpc_with_retry_after")
    @mock.patch("apps.mcp.remote_client._read_json_or_sse_response")
    def test_pagination_respects_max_pages_limit(self, mock_read, mock_post) -> None:
        """Tool listing should stop at max_pages to prevent infinite loops."""
        mock_response = mock.MagicMock()
        mock_response.raise_for_status = mock.MagicMock()
        mock_post.return_value = mock_response

        # Always return a nextCursor (simulates infinite pagination)
        def infinite_pages(response, expect_id):
            return {
                "jsonrpc": "2.0",
                "id": expect_id,
                "result": {
                    "tools": [{"name": f"tool_{expect_id}", "description": f"Tool {expect_id}"}],
                    "nextCursor": f"cursor_{expect_id + 1}",  # Always has next cursor
                },
            }

        mock_read.side_effect = infinite_pages

        session = McpRemoteSession(
            transport="streamable_http",
            endpoint_url="https://example.com/mcp",
            message_url=None,
            session_id="test-session",
            protocol_version="2025-11-25",
            server_info=None,
        )

        mock_client = mock.MagicMock()
        # Use a small max_pages limit for testing
        tools = _streamable_http_list_tools(client=mock_client, session=session, headers=None, max_pages=3)

        # Should stop at max_pages (3) even though more pages exist
        self.assertEqual(len(tools), 3)
        self.assertEqual(mock_post.call_count, 3)
