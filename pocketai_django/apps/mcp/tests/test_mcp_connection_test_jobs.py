from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import BusinessProfile, McpConnection, RegistrationSession
from apps.mcp.connection_test_jobs import McpConnectionTestJobRunner, enqueue_mcp_connection_test_job
from apps.mcp.models import McpConnectionTestJobStatus
from apps.mcp.remote_client import McpRemoteHttpStatusError, McpRemoteSession, McpRemoteTool


User = get_user_model()


class McpConnectionTestJobRunnerTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="jobs@example.com", password="changeme123", first_name="Jobs")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Jobs Corp",
            industry="Retail",
        )
        self.connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Postgres MCP",
            server_url="https://93.184.216.34/mcp",
            status="enabled",
            auth_type="none",
            source_type="marketplace",
            marketplace_key="postgres",
        )

    def test_runner_processes_job_and_updates_tool_cache(self) -> None:
        job = enqueue_mcp_connection_test_job(connection=self.connection, trigger="test")
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.status, McpConnectionTestJobStatus.QUEUED)

        session = McpRemoteSession(
            transport="streamable_http",
            endpoint_url=self.connection.server_url,
            message_url=None,
            session_id=None,
            protocol_version="2025-11-25",
            server_info=None,
        )
        tools = [McpRemoteTool(name="query", title="Query", description="Run query", input_schema={"type": "object"})]

        with mock.patch("apps.mcp.connection_test_jobs.test_mcp_server", return_value=(session, tools)):
            runner = McpConnectionTestJobRunner(lease_seconds=30, idle_sleep_s=0.1)
            processed = runner.run_once(limit=5)
            self.assertEqual(processed, 1)

        job.refresh_from_db()
        self.assertEqual(job.status, McpConnectionTestJobStatus.SUCCEEDED)

        self.connection.refresh_from_db()
        tool_cache = (self.connection.metadata or {}).get("tool_cache") or {}
        self.assertEqual(tool_cache.get("tool_count"), 1)

    def test_enqueue_coalesces_queued_jobs(self) -> None:
        job1 = enqueue_mcp_connection_test_job(connection=self.connection, trigger="create")
        self.assertIsNotNone(job1)
        job2 = enqueue_mcp_connection_test_job(connection=self.connection, trigger="update")
        self.assertIsNotNone(job2)
        assert job1 is not None
        assert job2 is not None

        job1.refresh_from_db()
        job2.refresh_from_db()
        self.assertEqual(job1.status, McpConnectionTestJobStatus.CANCELLED)
        self.assertEqual(job2.status, McpConnectionTestJobStatus.QUEUED)

    def test_runner_requeues_on_429_retry_after(self) -> None:
        job = enqueue_mcp_connection_test_job(connection=self.connection, trigger="test")
        self.assertIsNotNone(job)
        assert job is not None

        with mock.patch(
            "apps.mcp.connection_test_jobs.test_mcp_server",
            side_effect=McpRemoteHttpStatusError("rate limited", status_code=429, retry_after="3"),
        ):
            runner = McpConnectionTestJobRunner(lease_seconds=30, idle_sleep_s=0.1)
            processed = runner.run_once(limit=5)
            self.assertEqual(processed, 1)

        job.refresh_from_db()
        self.assertEqual(job.status, McpConnectionTestJobStatus.QUEUED)
        self.assertIsNotNone(job.run_after)
        self.assertGreaterEqual(job.attempt_count, 1)
