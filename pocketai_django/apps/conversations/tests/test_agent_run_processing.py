from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.agent_run_processing import AgentRunProcessingService
from apps.conversations.models import AgentRun, AgentRunStatus


User = get_user_model()


class AgentRunProcessingTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
            status="active",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Ops Agent",
            status="active",
        )

    def test_process_next_run_requeues_when_provider_missing(self) -> None:
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            title="Test Run",
            status=AgentRunStatus.QUEUED,
            run_after=timezone.now(),
            run_spec_snapshot={"goal": "Do something safely", "tool_allowlist": []},
            max_attempts=2,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=2, max_retry_delay_seconds=1.0)
        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=None):
            result = service.process_next_run()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.requeued)

        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.QUEUED)
        self.assertEqual(run.attempt_count, 1)
        self.assertIsNotNone(run.run_after)
        self.assertIsNone(run.lease_expires_at)

