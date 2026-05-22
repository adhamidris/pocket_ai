from __future__ import annotations

import json
import uuid
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation, ConversationToolApproval, ConversationToolApprovalStatus
from apps.mcp.orchestrator import McpOrchestratorService


class _DuplicatePhoneProvider:
    def __init__(self) -> None:
        self.model = "deepseek-chat"
        self.calls = 0

    def chat(
        self,
        messages,
        *,
        tools=None,
        on_stream_delta=None,
        on_reasoning_delta=None,
        on_tool_call_start=None,
        on_tool_call_delta=None,
        response_format=None,
        should_cancel=None,
    ):
        del messages, tools, on_reasoning_delta, on_tool_call_delta, response_format, should_cancel
        self.calls += 1
        if self.calls == 1:
            tool_call_1 = {
                "id": "call_phone_1",
                "type": "function",
                "function": {
                    "name": "initiate_phone_call",
                    "arguments": json.dumps(
                        {
                            "phone_number": "+201092129119",
                            "objective": "Discuss a random topic about life for testing purposes.",
                        },
                        ensure_ascii=False,
                    ),
                },
            }
            tool_call_2 = {
                "id": "call_phone_2",
                "type": "function",
                "function": {
                    "name": "initiate_phone_call",
                    "arguments": json.dumps(
                        {
                            "phone_number": "+201092129119",
                            "objective": "Discuss a random topic about life for testing purposes.",
                        },
                        ensure_ascii=False,
                    ),
                },
            }
            if on_tool_call_start:
                on_tool_call_start(tool_call_1)
                on_tool_call_start(tool_call_2)
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tool_call_1, tool_call_2]}}

        content = "I initiated one call."
        if on_stream_delta:
            on_stream_delta(content)
        return {"message": {"role": "assistant", "content": content}}


class PhoneCallApprovalBehaviorTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="phone-approval@example.com", first_name="Phone")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Phone Approval Co",
            industry="support",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Phone Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="phone-approval-session",
        )

    def _create_approved_phone_approval(self) -> ConversationToolApproval:
        return ConversationToolApproval.objects.create(
            conversation=self.conversation,
            connection=None,
            tool_name="initiate_phone_call",
            remote_tool_name="",
            status=ConversationToolApprovalStatus.APPROVED,
            tool_call_id="call_prev",
            event_id="evt_prev",
            requested_at=timezone.now(),
            resolved_at=timezone.now(),
            expires_at=timezone.now(),
            input_payload={
                "phone_number": "+201092129119",
                "objective": "Discuss a random topic about life for testing purposes.",
            },
            metadata={"approval_mode": "phone_call", "operation_type": "write", "reason": "phone_call"},
        )

    def test_phone_approval_not_reused_across_turns_by_default(self) -> None:
        previous = self._create_approved_phone_approval()
        service = McpOrchestratorService(agent=self.agent, provider=None)

        approved, approval, result = service._maybe_request_phone_tool_approval(
            conversation=self.conversation,
            tool_name="initiate_phone_call",
            tool_call_id="call_new",
            tool_event_id="call_new",
            arguments={
                "phone_number": "+201092129119",
                "objective": "Discuss a random topic about life for testing purposes.",
            },
            on_tool_event=None,
            wait_for_approval=False,
        )

        self.assertFalse(approved)
        self.assertIsNotNone(approval)
        assert approval is not None
        self.assertNotEqual(approval.id, previous.id)
        self.assertEqual(approval.status, ConversationToolApprovalStatus.PENDING)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "pending_approval")

    @override_settings(MCP_PHONE_TOOL_APPROVAL_REUSE_ENABLED=True)
    def test_phone_approval_can_be_reused_when_explicitly_enabled(self) -> None:
        previous = self._create_approved_phone_approval()
        service = McpOrchestratorService(agent=self.agent, provider=None)

        approved, approval, result = service._maybe_request_phone_tool_approval(
            conversation=self.conversation,
            tool_name="initiate_phone_call",
            tool_call_id="call_new",
            tool_event_id="call_new",
            arguments={
                "phone_number": "+201092129119",
                "objective": "Discuss a random topic about life for testing purposes.",
            },
            on_tool_event=None,
            wait_for_approval=False,
        )

        self.assertTrue(approved)
        self.assertIsNotNone(approval)
        assert approval is not None
        self.assertEqual(approval.id, previous.id)
        self.assertIsNone(result)

    @mock.patch.object(McpOrchestratorService, "_maybe_request_phone_tool_approval", return_value=(True, None, None))
    @mock.patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_duplicate_phone_calls_in_single_turn_are_suppressed(
        self,
        execute_tool_mock: mock.Mock,
        approval_mock: mock.Mock,
    ) -> None:
        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            del arguments, conversation, context
            self.assertEqual(name, "initiate_phone_call")
            return {
                "tool": "initiate_phone_call",
                "status": "ok",
                "call_session_id": str(uuid.uuid4()),
            }

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _DuplicatePhoneProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="Call the customer now.",
        )

        phone_calls = [call for call in execute_tool_mock.call_args_list if call.args and call.args[0] == "initiate_phone_call"]
        self.assertEqual(len(phone_calls), 1)
        self.assertEqual(approval_mock.call_count, 1)

        phone_trace = [entry for entry in context.tool_trace if entry.get("tool") == "initiate_phone_call"]
        self.assertEqual(len(phone_trace), 2)
        statuses = {str(entry.get("status") or "").strip().lower() for entry in phone_trace}
        self.assertIn("ok", statuses)
        self.assertIn("blocked", statuses)
        self.assertTrue(any(entry.get("error_code") == "duplicate_phone_call" for entry in phone_trace))
