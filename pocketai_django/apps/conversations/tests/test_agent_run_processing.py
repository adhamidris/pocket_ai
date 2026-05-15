from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.agent_run_processing import AgentRunProcessingService
from apps.conversations.models import (
    AgentRun,
    AgentRunNotification,
    AgentRunSource,
    AgentRunStatus,
    AssistantWorkflow,
    AssistantWorkflowStatus,
    Conversation,
)


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
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Ops Agent",
        )

    def test_process_next_run_requeues_when_provider_missing(self) -> None:
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            title="Test Run",
            status=AgentRunStatus.QUEUED,
            run_after=timezone.now(),
            workflow_snapshot={"goal": "Do something safely"},
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

    def test_execute_run_uses_isolated_execution_conversation(self) -> None:
        anchor = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="anchor-session",
            metadata={"actor_user_id": str(self.user.id)},
        )
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            conversation=anchor,
            created_by=self.user,
            title="Hello World",
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
            lease_expires_at=timezone.now(),
            workflow_snapshot={"goal": "Say Hello world"},
            max_attempts=2,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=2, max_retry_delay_seconds=1.0)
        mock_turn = mock.Mock(
            response_text="Hello world",
            response_blocks=[],
            planned_actions=[],
            extractions=[],
            llm_usage={},
            tool_trace=[],
        )
        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=mock.Mock()):
            with mock.patch("apps.mcp.orchestrator.McpOrchestratorService.stream_turn", return_value=mock_turn) as patched:
                result = service._execute_run(run)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        patched.assert_called_once()
        called_conversation = patched.call_args.kwargs.get("conversation")
        self.assertIsNotNone(called_conversation)
        meta = getattr(called_conversation, "metadata", None) or {}
        self.assertEqual(str(meta.get("source") or ""), "agent_run")
        self.assertEqual(str(meta.get("anchor_conversation_id") or ""), str(anchor.id))
        self.assertNotEqual(str(getattr(called_conversation, "id", "")), str(anchor.id))

        run.refresh_from_db()
        self.assertEqual(run.conversation_id, anchor.id)
        self.assertEqual(str(run.execution_conversation_id or ""), str(getattr(called_conversation, "id", "")))

    def test_workflow_run_persists_report_state_and_notification(self) -> None:
        workflow = AssistantWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Subscription checker",
            status=AssistantWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={"goal": "Check subscriptions"},
        )
        anchor = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="workflow-anchor",
            metadata={"actor_user_id": str(self.user.id), "type": "workflow_thread"},
        )
        workflow.conversation = anchor
        workflow.save(update_fields=["conversation", "updated_at"])
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            conversation=anchor,
            created_by=self.user,
            title="Subscription checker",
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
            lease_expires_at=timezone.now(),
            workflow_snapshot={"goal": "Check subscriptions"},
            max_attempts=2,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=2, max_retry_delay_seconds=1.0)
        mock_turn = mock.Mock(
            response_text='{"run_report":{"objective":"Check subscriptions","status":"completed","findings":["A plan renews soon"],"actions_taken":[],"evidence_refs":[],"confidence":0.9,"changed_entities":[{"type":"subscription","identity":"demo","state":{"renewal":"2026-06-01","price":"10"}}],"notification_candidate":{"kind":"run_result","priority":"normal","title":"Subscription update","body":"A plan renews soon","payload":{}},"recommended_next_step":"Review"}}',
            response_blocks=[],
            planned_actions=[],
            extractions=[],
            llm_usage={},
            tool_trace=[],
        )
        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=mock.Mock()):
            with mock.patch("apps.mcp.orchestrator.McpOrchestratorService.stream_turn", return_value=mock_turn):
                result = service._execute_run(run)

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        run.refresh_from_db()
        self.assertIn("run_report", run.result)
        workflow.refresh_from_db()
        self.assertIn("last_run_report", workflow.state)
        self.assertTrue(AgentRunNotification.objects.filter(run=run, status="delivered").exists())
