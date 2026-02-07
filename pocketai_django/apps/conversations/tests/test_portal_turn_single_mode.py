from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import (
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
    PortalTurn,
    PortalTurnStatus,
)
from apps.conversations.portal_turn_runner import PortalTurnRunner


User = get_user_model()


class _FakeOrchestrator:
    def __init__(
        self,
        *,
        emit_model_blocks: bool = False,
        stream_text: str = "Hello from stream.",
        response_text: str | None = None,
    ) -> None:
        self.emit_model_blocks = emit_model_blocks
        self.stream_text = stream_text
        self.response_text = response_text or stream_text

    def stream_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta=None,
        on_status_change=None,
        on_placeholder_response=None,
        on_stream_complete=None,
        on_spinner_update=None,
        on_tool_event=None,
        on_block_event=None,
        on_reasoning_event=None,
        should_cancel=None,
        **_kwargs,
    ):
        del conversation, user_message, on_placeholder_response, on_stream_complete, on_spinner_update, on_tool_event, on_reasoning_event, should_cancel

        if self.emit_model_blocks and on_block_event:
            on_block_event(
                {
                    "type": "block_start",
                    "payload": {
                        "block": {
                            "block_id": "model-block-fixed-0001",
                            "type": "paragraph",
                            "payload": {"content": [{"type": "text", "text": "MODEL"}]},
                        }
                    },
                }
            )

        if on_status_change:
            on_status_change({"code": "responding"})

        if on_response_text_delta:
            on_response_text_delta(self.stream_text)

        return SimpleNamespace(
            streamed_chunks=(self.stream_text,),
            response_text=self.response_text,
        )


class PortalTurnSingleModeTests(TransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(
            email="portal-single-mode@example.com",
            password="changeme123",
            first_name="Portal",
        )
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Single Mode Co",
            industry="Support",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"rag_agentic_mode": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Portal Agent",
            status="active",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token=f"portal-single-mode-{uuid.uuid4()}",
        )

    def test_portal_turn_ignores_model_block_events(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="Hello",
        )
        orchestrator = _FakeOrchestrator(emit_model_blocks=True)
        runner = PortalTurnRunner(turn=turn, conversation=self.conversation)

        events: list[tuple[str, dict]] = []

        def _append_event(*, turn_id, event_type, payload=None):
            del turn_id
            events.append((str(event_type), dict(payload or {})))
            return None

        with (
            mock.patch.object(PortalTurnRunner, "_select_orchestrator", return_value=orchestrator),
            mock.patch("apps.conversations.portal_turn_runner.append_turn_event", side_effect=_append_event),
        ):
            runner.run()

        # Guardrail: the model must not flip block_ops_active during portal turns.
        self.assertFalse(runner.builder.block_ops_active)

        # Any model-driven portal block ids must not be present in the emitted stream.
        started = [payload.get("block") for etype, payload in events if etype == "block_start"]
        started_ids = [b.get("block_id") for b in started if isinstance(b, dict)]
        self.assertNotIn("model-block-fixed-0001", started_ids)

    def test_portal_turn_emits_stream_events_and_turn_persisted(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="Hello",
        )
        orchestrator = _FakeOrchestrator(emit_model_blocks=False)
        runner = PortalTurnRunner(turn=turn, conversation=self.conversation)

        events: list[tuple[str, dict]] = []

        def _append_event(*, turn_id, event_type, payload=None):
            del turn_id
            events.append((str(event_type), dict(payload or {})))
            return None

        with (
            mock.patch.object(PortalTurnRunner, "_select_orchestrator", return_value=orchestrator),
            mock.patch("apps.conversations.portal_turn_runner.append_turn_event", side_effect=_append_event),
        ):
            runner.run()

        event_types = [t for t, _ in events]
        self.assertIn("text_delta", event_types)
        self.assertIn("turn_persisted", event_types)

        turn.refresh_from_db()
        self.assertEqual(turn.status, PortalTurnStatus.FINALIZED)
        self.assertIsNotNone(turn.message_id)
        self.assertIsNotNone(turn.message)
        self.assertEqual(turn.message.sender, ConversationSender.AI)
        self.assertIn("Hello from stream.", turn.message.body)

    def test_portal_turn_prefers_orchestrator_response_text_for_persistence(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="Read the full fee table",
        )
        stream_text = (
            "Interest Rate: 3.\n"
            "Issuance and Renewal Fees: EGP\n"
            "Over-limit Fees: EGP"
        )
        final_text = (
            "Interest Rate: 3.99%\n"
            "Issuance and Renewal Fees: EGP 300\n"
            "Over-limit Fees: EGP 150"
        )
        orchestrator = _FakeOrchestrator(
            emit_model_blocks=False,
            stream_text=stream_text,
            response_text=final_text,
        )
        runner = PortalTurnRunner(turn=turn, conversation=self.conversation)

        with mock.patch.object(PortalTurnRunner, "_select_orchestrator", return_value=orchestrator):
            runner.run()

        turn.refresh_from_db()
        self.assertIsNotNone(turn.message)
        self.assertEqual(turn.message.body, final_text)

    @override_settings(PORTAL_DEBUG_TOOL_TRACE=True)
    def test_portal_turn_persists_debug_tools_in_message_metadata(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="Show me debug trace",
        )
        orchestrator = _FakeOrchestrator(emit_model_blocks=False)
        runner = PortalTurnRunner(turn=turn, conversation=self.conversation)

        events: list[tuple[str, dict]] = []
        debug_payload = {
            "tool_trace": [
                {
                    "tool": "search_knowledge",
                    "status": "ok",
                    "llm_request": {"tool": "search_knowledge", "arguments": {"query": "fees"}},
                    "llm_response": {"content": "{\"status\":\"ok\"}"},
                }
            ]
        }

        def _append_event(*, turn_id, event_type, payload=None):
            del turn_id
            events.append((str(event_type), dict(payload or {})))
            return None

        with (
            mock.patch.object(PortalTurnRunner, "_select_orchestrator", return_value=orchestrator),
            mock.patch("apps.api.chat_portal._serialize_debug_tools_payload", return_value=debug_payload),
            mock.patch("apps.conversations.portal_turn_runner.append_turn_event", side_effect=_append_event),
        ):
            runner.run()

        turn.refresh_from_db()
        self.assertIsNotNone(turn.message)
        self.assertIsInstance(turn.message.metadata, dict)
        self.assertEqual(turn.message.metadata.get("debug_tools"), debug_payload)

        persisted_payloads = [payload for event_type, payload in events if event_type == "turn_persisted"]
        self.assertTrue(persisted_payloads)
        self.assertEqual(persisted_payloads[-1].get("debug_tools"), debug_payload)

    @override_settings(PORTAL_DEBUG_TOOL_TRACE=True)
    def test_portal_turn_merges_debug_tools_with_existing_message_metadata(self) -> None:
        existing_message = ConversationMessage.objects.create(
            conversation=self.conversation,
            sender=ConversationSender.AI,
            body="placeholder",
            metadata={"agent_run_id": "run_123", "source": "agent_run"},
        )
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            message=existing_message,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="Continue",
        )
        orchestrator = _FakeOrchestrator(emit_model_blocks=False)
        runner = PortalTurnRunner(turn=turn, conversation=self.conversation)

        debug_payload = {"tool_trace": [{"tool": "read_knowledge", "status": "ok"}]}

        with (
            mock.patch.object(PortalTurnRunner, "_select_orchestrator", return_value=orchestrator),
            mock.patch("apps.api.chat_portal._serialize_debug_tools_payload", return_value=debug_payload),
        ):
            runner.run()

        existing_message.refresh_from_db()
        self.assertEqual(existing_message.metadata.get("agent_run_id"), "run_123")
        self.assertEqual(existing_message.metadata.get("source"), "agent_run")
        self.assertEqual(existing_message.metadata.get("debug_tools"), debug_payload)

    def test_tool_approval_link_does_not_overwrite_existing_turn(self) -> None:
        first_turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.WAITING_APPROVAL,
            user_message="First",
        )
        second_turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="Second",
        )
        approval = ConversationToolApproval.objects.create(
            conversation=self.conversation,
            connection=None,
            tool_name="initiate_phone_call",
            remote_tool_name="",
            status=ConversationToolApprovalStatus.PENDING,
            tool_call_id="call_test_1",
            event_id="evt_test_1",
            turn=first_turn,
            input_payload={"phone_number": "+201092129119", "objective": "Test"},
            metadata={"approval_mode": "phone_call", "operation_type": "write", "reason": "phone_call"},
        )

        runner = PortalTurnRunner(turn=second_turn, conversation=self.conversation)
        runner.builder.on_tool_event(
            {
                "event_id": "evt_test_1",
                "phase": "approval_requested",
                "status": "pending_approval",
                "tool_call_id": "call_test_1",
                "tool_name": "initiate_phone_call",
                "kind": "phone",
                "approval_id": str(approval.id),
                "approval": {
                    "id": str(approval.id),
                    "status": "pending",
                },
            }
        )

        approval.refresh_from_db()
        self.assertEqual(approval.turn_id, first_turn.id)
