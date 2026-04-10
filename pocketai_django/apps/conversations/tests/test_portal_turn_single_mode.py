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
    PortalTurnEvent,
    PortalTurnStatus,
)
from apps.conversations.content_blocks import extract_text_from_content_blocks
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
        del conversation, user_message, on_placeholder_response, on_spinner_update, on_tool_event, on_reasoning_event, should_cancel

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
        if on_stream_complete:
            on_stream_complete()

        return SimpleNamespace(
            streamed_chunks=(self.stream_text,),
            response_text=self.response_text,
        )


class _FakeToolLoopOrchestrator:
    def __init__(self) -> None:
        self.pre_tool_text = "I'll search for the fee now."
        self.final_text = "Final answer: 1% with minimum USD 2, applies to Private too."

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
        del conversation, user_message, on_placeholder_response, on_spinner_update, on_block_event, on_reasoning_event, should_cancel

        if on_status_change:
            on_status_change({"code": "thinking"})

        if on_response_text_delta:
            on_response_text_delta(self.pre_tool_text)

        if on_tool_event:
            on_tool_event(
                {
                    "event_id": "evt_tool_1",
                    "phase": "started",
                    "status": "running",
                    "tool_call_id": "call_tool_1",
                    "tool_name": "search_knowledge",
                    "kind": "tool",
                }
            )
            on_tool_event(
                {
                    "event_id": "evt_tool_1",
                    "phase": "finished",
                    "status": "ok",
                    "tool_call_id": "call_tool_1",
                    "tool_name": "search_knowledge",
                    "kind": "tool",
                    "duration_ms": 45,
                    "output": {"status": "ok"},
                }
            )

        if on_response_text_delta:
            on_response_text_delta(self.final_text)

        if on_stream_complete:
            on_stream_complete()

        return SimpleNamespace(
            streamed_chunks=(self.pre_tool_text, self.final_text),
            response_text=self.final_text,
        )


class _FakeToolLoopTailMismatchOrchestrator:
    def __init__(self) -> None:
        self.pre_tool_text = "I'll search for the fee now."
        self.streamed_final_text = "The assessment fee for personal loans is "
        self.final_text = "The assessment fee for personal loans is EGP 200 (paid once)."

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
        del conversation, user_message, on_placeholder_response, on_spinner_update, on_block_event, on_reasoning_event, should_cancel

        if on_status_change:
            on_status_change({"code": "thinking"})

        if on_response_text_delta:
            on_response_text_delta(self.pre_tool_text)

        if on_tool_event:
            on_tool_event(
                {
                    "event_id": "evt_tool_1",
                    "phase": "started",
                    "status": "running",
                    "tool_call_id": "call_tool_1",
                    "tool_name": "search_knowledge",
                    "kind": "tool",
                }
            )
            on_tool_event(
                {
                    "event_id": "evt_tool_1",
                    "phase": "finished",
                    "status": "ok",
                    "tool_call_id": "call_tool_1",
                    "tool_name": "search_knowledge",
                    "kind": "tool",
                    "duration_ms": 45,
                    "output": {"status": "ok"},
                }
            )

        if on_response_text_delta:
            on_response_text_delta(self.streamed_final_text)

        if on_stream_complete:
            on_stream_complete()

        return SimpleNamespace(
            streamed_chunks=(self.pre_tool_text, self.streamed_final_text),
            response_text=self.final_text,
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
        self.assertIn("block_start", event_types)
        self.assertIn("block_delta", event_types)
        self.assertIn("turn_persisted", event_types)
        self.assertNotIn("text_delta", event_types)
        streamed_ids: list[str] = []
        for event_type, payload in events:
            if event_type != "block_start":
                continue
            block = payload.get("block")
            if not isinstance(block, dict):
                continue
            block_id = str(block.get("block_id") or "").strip()
            if block_id and block_id not in streamed_ids:
                streamed_ids.append(block_id)
        persisted_payloads = [payload for event_type, payload in events if event_type == "turn_persisted"]
        self.assertTrue(persisted_payloads)
        persisted_blocks = persisted_payloads[-1].get("content_blocks")
        self.assertIsInstance(persisted_blocks, list)
        persisted_ids = [
            str(block.get("block_id") or "").strip()
            for block in (persisted_blocks or [])
            if isinstance(block, dict) and str(block.get("block_id") or "").strip()
        ]
        self.assertEqual(streamed_ids, persisted_ids)

        turn.refresh_from_db()
        self.assertEqual(turn.status, PortalTurnStatus.FINALIZED)
        self.assertIsNotNone(turn.message_id)
        self.assertIsNotNone(turn.message)
        self.assertEqual(turn.message.sender, ConversationSender.AI)
        message_ids = [
            str(block.get("block_id") or "").strip()
            for block in (turn.message.content_blocks or [])
            if isinstance(block, dict) and str(block.get("block_id") or "").strip()
        ]
        self.assertEqual(streamed_ids, message_ids)
        self.assertIn("Hello from stream.", turn.message.body)

    def test_portal_turn_persists_streamed_blocks_as_source_of_truth(self) -> None:
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
        block_text = extract_text_from_content_blocks(turn.message.content_blocks or [])
        self.assertEqual(turn.message.body, block_text)
        self.assertIn("Interest Rate: 3.", turn.message.body)
        self.assertNotIn("Interest Rate: 3.99%", turn.message.body)

    def test_portal_turn_preserves_streamed_blocks_without_text_reconciliation(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="What is the traveler cheque fee?",
        )
        stream_text = (
            "I'll search for fees.\n\n"
            "Summary:\n"
            "- Applicable to: All customer segments (Prime, Plus, Wealth, Exclusive Wealth, "
        )
        final_text = (
            "Summary:\n"
            "- Applicable to: All customer segments (Prime, Plus, Wealth, Exclusive Wealth, Private)"
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
        self.assertEqual(turn.message.body, extract_text_from_content_blocks(turn.message.content_blocks or []))
        block_text = extract_text_from_content_blocks(turn.message.content_blocks or [])
        self.assertIn("I'll search for fees.", block_text)
        self.assertNotIn("Exclusive Wealth, Private)", block_text)

    def test_portal_turn_preserves_pre_tool_text_for_tool_use_turns(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="What is traveler cheque fee?",
        )
        orchestrator = _FakeToolLoopOrchestrator()
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

        turn.refresh_from_db()
        self.assertIsNotNone(turn.message)
        # Body includes both pre-tool narration and the final answer.
        block_text = extract_text_from_content_blocks(turn.message.content_blocks or [])
        self.assertIn(orchestrator.pre_tool_text, block_text)
        self.assertIn(orchestrator.final_text, block_text)

        first_tool_use_idx = next((idx for idx, item in enumerate(events) if item[0] == "block_tool_use"), -1)
        first_block_remove_idx = next((idx for idx, item in enumerate(events) if item[0] == "block_remove"), -1)
        first_text_delta_idx = -1
        for idx, (event_type, payload) in enumerate(events):
            if event_type != "block_delta":
                continue
            ops = payload.get("ops")
            if not isinstance(ops, list):
                continue
            delta_text = ""
            for op in ops:
                if not isinstance(op, dict):
                    continue
                if str(op.get("op") or "").strip() != "append_inline":
                    continue
                nodes = op.get("nodes")
                if isinstance(nodes, list):
                    for node in nodes:
                        if isinstance(node, dict):
                            delta_text += str(node.get("text") or "")
            if delta_text.strip():
                first_text_delta_idx = idx
                break

        self.assertGreaterEqual(first_tool_use_idx, 0)
        # Pre-tool text stays — no block_remove during streaming.
        self.assertEqual(first_block_remove_idx, -1)
        self.assertGreaterEqual(first_text_delta_idx, 0)
        # Pre-tool text streams before tool cards appear.
        self.assertLess(first_text_delta_idx, first_tool_use_idx)

    def test_portal_turn_tool_loop_does_not_replay_final_text_after_stream(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="What is the assessment fee for personal loans?",
        )
        orchestrator = _FakeToolLoopTailMismatchOrchestrator()
        runner = PortalTurnRunner(turn=turn, conversation=self.conversation)

        with mock.patch.object(PortalTurnRunner, "_select_orchestrator", return_value=orchestrator):
            runner.run()

        turn.refresh_from_db()
        self.assertIsNotNone(turn.message)
        block_text = extract_text_from_content_blocks(turn.message.content_blocks or [])
        self.assertEqual(turn.message.body, block_text)
        self.assertIn(orchestrator.pre_tool_text, block_text)
        self.assertIn(orchestrator.streamed_final_text.strip(), block_text)
        self.assertNotIn(orchestrator.final_text, block_text)

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

    def test_tool_event_preserves_spinner_text_in_block_payload(self) -> None:
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            user_message="Find fees",
        )
        runner = PortalTurnRunner(turn=turn, conversation=self.conversation)

        runner.builder.on_tool_event(
            {
                "event_id": "evt_spinner_1",
                "phase": "started",
                "status": "running",
                "tool_call_id": "call_spinner_1",
                "tool_name": "search_knowledge",
                "kind": "tool",
                "spinner_text": "Searching for account fees",
            }
        )

        event = PortalTurnEvent.objects.filter(turn=turn, type="block_tool_use").order_by("-seq").first()
        self.assertIsNotNone(event)
        block = event.payload.get("block") if isinstance(event.payload, dict) else None
        self.assertIsInstance(block, dict)
        payload = block.get("payload") if isinstance(block, dict) else None
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("spinner_text"), "Searching for account fees")
