from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation, ConversationSender, PortalTurn, PortalTurnStatus
from apps.conversations.portal_turn_runner import PortalTurnRunner


User = get_user_model()


class _FakeOrchestrator:
    def __init__(self, *, emit_model_blocks: bool = False) -> None:
        self.emit_model_blocks = emit_model_blocks

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
            on_response_text_delta("Hello from stream.")

        return SimpleNamespace(streamed_chunks=("Hello from stream.",))


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
