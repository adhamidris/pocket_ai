from __future__ import annotations

import os
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation, PortalTurn, PortalTurnEvent, PortalTurnStatus
from apps.conversations.portal_turn_events import (
    append_turn_event,
    portal_turn_redis_seq_key,
    portal_turn_redis_stream_key,
)
from core.tenancy import tenant_context


User = get_user_model()


class PortalTurnRedisEventBusTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="portal-redis-bus@example.com", password="changeme123", first_name="Redis")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Redis Bus Co",
            industry="Support",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": False}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Redis Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-portal-redis-bus",
        )

    @override_settings(
        PORTAL_TURN_EVENT_BUS="redis",
        PORTAL_TURN_EVENT_LOG_MODE="db",
        PORTAL_TURN_EVENT_BUS_REDIS_STREAM_TTL_SECONDS=600,
        PORTAL_TURN_EVENT_BUS_REDIS_STREAM_PREFIX="portal:turn",
    )
    def test_append_turn_event_publishes_to_redis_stream_on_commit(self) -> None:
        with tenant_context(self.business.id):
            turn = PortalTurn.objects.create(
                conversation=self.conversation,
                agent_profile=self.agent,
                status=PortalTurnStatus.STREAMING,
                run_after=timezone.now(),
                user_message="hello",
                metadata={"execution_mode": "worker", "source": "test"},
            )

        pipe = mock.Mock()
        conn = mock.Mock()
        conn.pipeline.return_value = pipe
        pipe.execute.return_value = [None, True]

        with mock.patch("apps.conversations.portal_turn_events.get_portal_redis_client", return_value=conn):
            with self.captureOnCommitCallbacks(execute=True):
                with tenant_context(self.business.id):
                    append_turn_event(turn_id=turn.id, event_type="status", payload={"state": "responding"})

        expected_key = portal_turn_redis_stream_key(turn_id=turn.id)
        expected_seq_key = portal_turn_redis_seq_key(turn_id=turn.id)
        pipe.xadd.assert_called_once()
        args, kwargs = pipe.xadd.call_args
        self.assertEqual(args[0], expected_key)
        self.assertEqual(kwargs.get("id"), "1-0")
        fields = args[1]
        self.assertEqual(fields.get("type"), "status")
        self.assertEqual(json.loads(fields.get("payload") or "{}").get("state"), "responding")
        pipe.expire.assert_has_calls(
            [
                mock.call(expected_key, 600),
                mock.call(expected_seq_key, 600),
            ]
        )

    @override_settings(
        PORTAL_TURN_EVENT_BUS="redis",
        PORTAL_TURN_EVENT_LOG_MODE="off",
        PORTAL_TURN_EVENT_BUS_REDIS_STREAM_TTL_SECONDS=600,
        PORTAL_TURN_EVENT_BUS_REDIS_STREAM_PREFIX="portal:turn",
    )
    def test_off_mode_does_not_persist_portal_turn_event_rows(self) -> None:
        with tenant_context(self.business.id):
            turn = PortalTurn.objects.create(
                conversation=self.conversation,
                agent_profile=self.agent,
                status=PortalTurnStatus.STREAMING,
                run_after=timezone.now(),
                user_message="hello",
                metadata={"execution_mode": "worker", "source": "test"},
            )

        pipe = mock.Mock()
        conn = mock.Mock()
        conn.pipeline.return_value = pipe
        conn.incr.return_value = 1
        pipe.execute.return_value = [None, True, True]

        with mock.patch.dict(os.environ, {"REDIS_URL": "redis://redis:6379/0"}):
            with mock.patch("apps.conversations.portal_turn_events.get_portal_redis_client", return_value=conn):
                with tenant_context(self.business.id):
                    append_turn_event(turn_id=turn.id, event_type="status", payload={"state": "responding"})

        with tenant_context(self.business.id):
            self.assertEqual(PortalTurnEvent.objects.filter(turn_id=turn.id).count(), 0)
