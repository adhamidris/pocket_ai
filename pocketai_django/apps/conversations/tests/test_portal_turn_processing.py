from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation, PortalTurn, PortalTurnStatus
from apps.conversations.portal_turn_processing import PortalTurnProcessingService


User = get_user_model()


class PortalTurnProcessingTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="portal-worker@example.com", password="changeme123", first_name="Worker")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Worker Co",
            industry="Support",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": False}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Worker Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-portal-worker",
        )

    def test_claim_skips_active_lease(self) -> None:
        now = timezone.now()
        PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            run_after=now,
            lease_expires_at=now + timedelta(seconds=60),
            user_message="hello",
            metadata={"execution_mode": "worker", "source": "test"},
        )

        service = PortalTurnProcessingService(lease_seconds=60)
        claimed = service._claim_next_turn()

        self.assertIsNone(claimed)

    def test_claims_expired_lease_and_increments_attempts(self) -> None:
        now = timezone.now()
        turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            run_after=now,
            lease_expires_at=now - timedelta(seconds=60),
            user_message="hello",
            metadata={"execution_mode": "worker", "source": "test"},
        )

        service = PortalTurnProcessingService(lease_seconds=60)
        claimed = service._claim_next_turn()

        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.id, turn.id)

        turn.refresh_from_db()
        self.assertEqual(turn.attempt_count, 1)
        self.assertIsNotNone(turn.lease_expires_at)
