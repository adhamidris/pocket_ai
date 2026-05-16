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
    AgentRunEvent,
    AgentRunNotification,
    AgentRunSource,
    AgentRunStatus,
    AssistantWorkflow,
    AssistantWorkflowStatus,
    Conversation,
)
from apps.conversations.portal_session_serializers import serialize_agent_run_for_portal


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

    def test_portal_serializer_exposes_structured_run_report_and_clean_display(self) -> None:
        run_report = {
            "objective": "Monitor inbox",
            "status": "no_change",
            "findings": ["No sales-related emails found in unread inbox."],
            "actions_taken": [{"tool": "email_search", "status": "ok"}, {"tool": "email_get_message", "status": "ok", "count": 21}],
            "recommended_next_step": "No action needed.",
            "memory_update": {"current_summary": "Checked 21 unread emails.", "inspected_items": ["msg-1", "msg-2"]},
        }
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            title="Sales monitor",
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.COMPLETED,
            result={"response_text": '{"run_report": {"objective": "Monitor inbox", "status": "no_change"}}', "run_report": run_report},
            workflow_snapshot={"goal": "Monitor inbox"},
            max_attempts=1,
        )

        payload = serialize_agent_run_for_portal(run)

        self.assertEqual(payload["result"]["runReport"], run_report)
        self.assertEqual(payload["display"]["summary"], "No sales-related emails found in unread inbox.")
        self.assertNotIn("run_report", payload["display"]["summary"])
        self.assertEqual(payload["display"]["agentMessage"], "No sales-related emails found in unread inbox.")
        self.assertTrue(payload["display"]["rawAvailable"])
        self.assertNotIn("rawDebug", payload["display"])

    def test_execute_run_persists_visible_llm_stream_between_tool_events(self) -> None:
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            title="Sales monitor",
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
            lease_expires_at=timezone.now(),
            workflow_snapshot={"goal": "Check unread sales emails"},
            max_attempts=1,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=1, max_retry_delay_seconds=1.0)
        mock_turn = mock.Mock(
            response_text='{"run_report":{"status":"no_change","findings":["No sales emails found"],"actions_taken":[],"notification_candidate":null,"memory_update":{"current_summary":"No sales emails found."}}}',
            response_blocks=[],
            planned_actions=[],
            extractions=[],
            llm_usage={},
            tool_trace=[],
        )

        def fake_stream_turn(**kwargs):
            kwargs["on_response_text_delta"]("I'll check the unread inbox first.")
            kwargs["on_tool_event"]({"phase": "finished", "tool_name": "email_search", "status": "ok", "output": {"status": "ok"}})
            return mock_turn

        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=mock.Mock()):
            with mock.patch("apps.mcp.orchestrator.McpOrchestratorService.stream_turn", side_effect=fake_stream_turn):
                result = service._execute_run(run)

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        events = list(AgentRunEvent.objects.filter(run=run).order_by("sequence_index"))
        assistant_idx = next(i for i, event in enumerate(events) if event.payload.get("kind") == "assistant_message")
        tool_idx = next(i for i, event in enumerate(events) if event.stream == "executed")
        self.assertLess(assistant_idx, tool_idx)
        self.assertEqual(events[assistant_idx].payload["text"], "I'll check the unread inbox first.")

    def test_forced_final_tool_loop_marks_run_failed(self) -> None:
        workflow = AssistantWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Sales monitor",
            status=AssistantWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={"goal": "Find sales emails and send a report"},
        )
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            created_by=self.user,
            title="Sales monitor",
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
            lease_expires_at=timezone.now(),
            workflow_snapshot={"goal": "Find sales emails and send a report"},
            max_attempts=2,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=2, max_retry_delay_seconds=1.0)
        mock_turn = mock.Mock(
            response_text="I found sales emails and will send the report now.",
            response_blocks=[],
            planned_actions=[],
            extractions=[],
            llm_usage={},
            tool_trace=[
                {"tool": "email_search", "status": "ok"},
                {
                    "tool": "__orchestrator__",
                    "status": "forced_final",
                    "reason": "iteration_limit",
                    "next_tools": ["email_send_draft"],
                },
            ],
        )
        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=mock.Mock()):
            with mock.patch("apps.mcp.orchestrator.McpOrchestratorService.stream_turn", return_value=mock_turn):
                result = service._execute_run(run)

        self.assertEqual(result.status, AgentRunStatus.FAILED)
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.FAILED)
        self.assertIn("safety limit", run.error_detail)
        self.assertIn("email_send_draft", run.error_detail)
        self.assertTrue(run.finished_at)

    def test_dsml_final_output_marks_workflow_run_failed_without_notification(self) -> None:
        workflow = AssistantWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Sales monitor",
            status=AssistantWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={"goal": "Find sales emails and send a report", "memory_shape": "email_monitor"},
        )
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            created_by=self.user,
            title="Sales monitor",
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
            lease_expires_at=timezone.now(),
            workflow_snapshot=workflow.instructions,
            max_attempts=2,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=2, max_retry_delay_seconds=1.0)
        mock_turn = mock.Mock(
            response_text='<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="email_get_message">\n</｜｜DSML｜｜invoke>',
            response_blocks=[],
            planned_actions=[],
            extractions=[],
            llm_usage={},
            tool_trace=[{"tool": "email_get_message", "status": "ok"}],
        )
        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=mock.Mock()):
            with mock.patch("apps.mcp.orchestrator.McpOrchestratorService.stream_turn", return_value=mock_turn):
                result = service._execute_run(run)

        self.assertEqual(result.status, AgentRunStatus.FAILED)
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.FAILED)
        self.assertIn("malformed internal tool-call markup", run.error_detail)
        self.assertEqual(run.result["response_text"], "")
        self.assertIn("raw_response_text", run.result)
        self.assertFalse(AgentRunNotification.objects.filter(run=run).exists())

    def test_workflow_run_injects_wake_up_prompt_and_compact_memory(self) -> None:
        workflow = AssistantWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Sales monitor",
            status=AssistantWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={
                "goal": "Find sales emails",
                "wake_up_prompt": "Check new unread messages for sales intent and do not reread inspected IDs.",
                "memory_instructions": "Remember inspected and notified message IDs.",
                "workflow_type": "monitor",
                "memory_shape": "email_monitor",
            },
            state={
                "workflow_memory": {
                    "memory_shape": "email_monitor",
                    "inspected_items": ["msg-1"],
                    "last_run_summary": "No relevant findings.",
                }
            },
        )
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            created_by=self.user,
            title="Sales monitor",
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
            lease_expires_at=timezone.now(),
            workflow_snapshot=workflow.instructions,
            max_attempts=2,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=2, max_retry_delay_seconds=1.0)
        mock_turn = mock.Mock(
            response_text='{"run_report":{"status":"no_change","findings":[],"notification_candidate":null,"memory_update":{"current_summary":"No new matches."}}}',
            response_blocks=[],
            planned_actions=[],
            extractions=[],
            llm_usage={},
            tool_trace=[],
        )
        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=mock.Mock()):
            with mock.patch("apps.mcp.orchestrator.McpOrchestratorService.stream_turn", return_value=mock_turn) as patched:
                result = service._execute_run(run)

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        user_message = patched.call_args.kwargs["user_message"]
        self.assertIn("<workflow_instructions>", user_message)
        self.assertIn("do not reread inspected IDs", user_message)
        self.assertIn("<workflow_memory>", user_message)
        self.assertIn("msg-1", user_message)

    def test_workflow_memory_writeback_merges_report_and_email_trace(self) -> None:
        workflow = AssistantWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Sales monitor",
            status=AssistantWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={"goal": "Find sales emails", "workflow_type": "monitor", "memory_shape": "email_monitor"},
            state={"workflow_memory": {"inspected_items": ["old-msg"]}},
        )
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            created_by=self.user,
            title="Sales monitor",
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
            lease_expires_at=timezone.now(),
            workflow_snapshot=workflow.instructions,
            max_attempts=2,
        )

        service = AgentRunProcessingService(lease_seconds=1.0, max_retries_default=2, max_retry_delay_seconds=1.0)
        mock_turn = mock.Mock(
            response_text='{"run_report":{"status":"completed","findings":["Lead found"],"notification_candidate":null,"memory_update":{"ignored_items":["msg-ignore"],"notified_items":["msg-new"],"decisions":["Sales means demo or purchase intent."]}}}',
            response_blocks=[],
            planned_actions=[],
            extractions=[],
            llm_usage={},
            tool_trace=[
                {
                    "tool": "email_get_message",
                    "arguments": {"message_id": "msg-new"},
                    "status": "ok",
                    "output_summary": {"message_id": "msg-new", "status": "ok"},
                },
                {
                    "tool": "email_get_message",
                    "arguments": {"message_id": "msg-failed"},
                    "status": "error",
                    "error_code": "provider_error",
                },
            ],
        )
        with mock.patch("apps.llm.llm_provider.load_mcp_provider", return_value=mock.Mock()):
            with mock.patch("apps.mcp.orchestrator.McpOrchestratorService.stream_turn", return_value=mock_turn):
                result = service._execute_run(run)

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        workflow.refresh_from_db()
        memory = workflow.state["workflow_memory"]
        self.assertIn("old-msg", memory["inspected_items"])
        self.assertIn("msg-new", memory["inspected_items"])
        self.assertIn("msg-ignore", memory["ignored_items"])
        self.assertIn("msg-new", memory["notified_items"])
        self.assertIn("msg-failed (provider_error)", memory["failed_items"])
        self.assertIn("Sales means demo or purchase intent.", memory["decisions"])
