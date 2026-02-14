from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.api import chat_portal
from apps.conversations.models import Conversation, PortalTurn, PortalTurnStatus
from apps.conversations.portal_turn_events import append_turn_event
from core.tenancy import tenant_context


User = get_user_model()


def _parse_sse_events(raw_text: str) -> list[dict[str, object]]:
    """
    Parse a minimal subset of SSE needed for contract tests.

    We support:
    - comments: lines starting with ':'
    - events with `id:`, `event:`, and `data:`
    """
    blocks = [b for b in raw_text.split("\n\n") if b.strip()]
    events: list[dict[str, object]] = []
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        # Ignore pure comments / keepalives
        if all(line.startswith(":") for line in lines):
            continue
        event_id = None
        event_name = None
        data_json = None
        for line in lines:
            if line.startswith("id:"):
                event_id = line.split(":", 1)[1].strip()
            elif line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_json = line.split(":", 1)[1].strip()
        if not event_name or data_json is None:
            continue
        payload = json.loads(data_json)
        events.append({"id": event_id, "event": event_name, "data": payload})
    return events


class PortalSseContractV1Tests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(email="sse-contract@example.com", password="changeme123", first_name="SSE")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="SSE Corp",
            industry="Support",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": False}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="SSE Agent",
            status="active",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-sse-contract",
        )

    @override_settings(PORTAL_TURN_EVENT_BUS="postgres")
    def test_turn_events_stream_contract_envelope(self) -> None:
        with tenant_context(self.business.id):
            turn = PortalTurn.objects.create(
                conversation=self.conversation,
                agent_profile=self.agent,
                status=PortalTurnStatus.STREAMING,
                run_after=timezone.now(),
                user_message="hello",
                metadata={"source": "test"},
            )
            append_turn_event(turn_id=turn.id, event_type="status", payload={"state": "responding", "label": "Responding"})
            append_turn_event(turn_id=turn.id, event_type="block_start", payload={"block": {"block_id": "blk_1", "type": "paragraph", "created_at": timezone.now().isoformat(), "payload": {"content": []}}})
            append_turn_event(turn_id=turn.id, event_type="block_delta", payload={"block_id": "blk_1", "ops": [{"op": "append_inline", "nodes": [{"text": "Hi"}]}]})
            PortalTurn.objects.filter(id=turn.id).update(status=PortalTurnStatus.FINALIZED, finalized_at=timezone.now())

        request = self.factory.get(
            f"/api/chat/turns/{turn.id}/events/?session_token={self.conversation.session_token}",
        )

        # Avoid opening a dedicated LISTEN connection in tests.
        with mock.patch.object(chat_portal, "_open_portal_turn_listen_connection", return_value=None):
            response = chat_portal.portal_turn_events(request, turn_id=turn.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertIn("X-Portal-Stream-Protocol-Version", response)

        raw = b"".join(response.streaming_content)
        text = raw.decode("utf-8")
        events = _parse_sse_events(text)

        # We inserted 3 events, which should be delivered as `event: turnEvent`.
        self.assertEqual(len(events), 3)
        for idx, item in enumerate(events, start=1):
            self.assertEqual(item["event"], "turnEvent")
            self.assertEqual(item["id"], str(idx))
            data = item["data"]
            self.assertIsInstance(data, dict)
            self.assertEqual(data.get("turn_id"), str(turn.id))
            self.assertEqual(data.get("seq"), idx)
            self.assertIn("type", data)
            self.assertIn("payload", data)

    @override_settings(PORTAL_TURN_EVENT_BUS="postgres")
    def test_turn_events_stream_omits_legacy_text_delta(self) -> None:
        with tenant_context(self.business.id):
            turn = PortalTurn.objects.create(
                conversation=self.conversation,
                agent_profile=self.agent,
                status=PortalTurnStatus.STREAMING,
                run_after=timezone.now(),
                user_message="hello",
                metadata={"source": "test"},
            )
            append_turn_event(turn_id=turn.id, event_type="text_delta", payload={"text": "legacy"})
            append_turn_event(
                turn_id=turn.id,
                event_type="block_start",
                payload={
                    "block": {
                        "block_id": "blk_1",
                        "type": "paragraph",
                        "created_at": timezone.now().isoformat(),
                        "payload": {"content": []},
                    }
                },
            )
            PortalTurn.objects.filter(id=turn.id).update(status=PortalTurnStatus.FINALIZED, finalized_at=timezone.now())

        request = self.factory.get(
            f"/api/chat/turns/{turn.id}/events/?session_token={self.conversation.session_token}",
        )

        with mock.patch.object(chat_portal, "_open_portal_turn_listen_connection", return_value=None):
            response = chat_portal.portal_turn_events(request, turn_id=turn.id)

        events = _parse_sse_events(b"".join(response.streaming_content).decode("utf-8"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["type"], "block_start")

    @override_settings(PORTAL_TURN_EVENT_BUS="redis")
    def test_turn_events_redis_drain_does_not_block_forever(self) -> None:
        with tenant_context(self.business.id):
            turn = PortalTurn.objects.create(
                conversation=self.conversation,
                agent_profile=self.agent,
                status=PortalTurnStatus.FINALIZED,
                run_after=timezone.now(),
                user_message="hello",
                metadata={"source": "test"},
            )

        request = self.factory.get(
            f"/api/chat/turns/{turn.id}/events/?session_token={self.conversation.session_token}",
        )

        stream_key = f"portal:turn:{turn.id}:events"
        redis_conn = mock.Mock()
        calls = {"n": 0}

        def _xread(streams, count=None, block=None):
            del streams, count
            calls["n"] += 1
            # Regression: Redis Streams BLOCK 0 means "block forever", which caused the ~35s SSE tail.
            self.assertNotEqual(block, 0)
            if calls["n"] == 1:
                payload = {"message_id": "msg_1", "text": "done", "content_blocks": []}
                return [(stream_key, [(b"1-0", {b"type": b"turn_persisted", b"payload": json.dumps(payload).encode("utf-8")})])]
            if calls["n"] == 2:
                # After turn_persisted is delivered, the SSE loop should use a short block window.
                self.assertEqual(block, 200)
                return []
            # Drain loop: must be non-blocking (no BLOCK 0).
            return []

        redis_conn.xread.side_effect = _xread

        with mock.patch.object(chat_portal, "get_portal_redis_client", return_value=redis_conn):
            response = chat_portal.portal_turn_events(request, turn_id=turn.id)
            raw = b"".join(response.streaming_content)
        events = _parse_sse_events(raw.decode("utf-8"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["type"], "turn_persisted")

    @override_settings(PORTAL_TURN_EVENT_BUS="postgres")
    def test_session_events_stream_starts_with_status_changed(self) -> None:
        request = self.factory.get(f"/api/chat/events/?session_token={self.conversation.session_token}")
        response = chat_portal.events(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertIn("X-Portal-Stream-Protocol-Version", response)

        it = iter(response.streaming_content)
        first = next(it)
        second = next(it)
        if isinstance(first, bytes):
            first = first.decode("utf-8")
        if isinstance(second, bytes):
            second = second.decode("utf-8")

        self.assertTrue(first.startswith("event: statusChanged"))
        self.assertTrue(second.startswith("data:"))
