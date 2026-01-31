from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, TenantMemoryConfiguration
from apps.conversations.models import (
    AgentRun,
    AgentRunMemoryItem,
    CompactedHistorySegment,
    Conversation,
    ConversationMessage,
    ConversationSender,
)
from apps.conversations.retention_purge import TenantRetentionPurgeService


User = get_user_model()


class TenantRetentionPurgeTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Retention Co",
            industry="banking",
            status="active",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Ops Agent",
            status="active",
        )

    def test_purge_deletes_old_memory_items_and_segments_and_trims_overlaps(self) -> None:
        now = timezone.now()
        cutoff_days = 30

        TenantMemoryConfiguration.objects.create(
            business_profile=self.business,
            maximum_retention_days=cutoff_days,
            purge_enabled=True,
            legal_hold=False,
        )

        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            title="Test Run",
            run_spec_snapshot={"goal": "Do something"},
        )

        mem_old = AgentRunMemoryItem.objects.create(
            run=run,
            kind="fact",
            key="old",
            content="old memory",
            created_by=self.user,
        )
        mem_new = AgentRunMemoryItem.objects.create(
            run=run,
            kind="fact",
            key="new",
            content="new memory",
            created_by=self.user,
        )
        AgentRunMemoryItem.objects.filter(id=mem_old.id).update(created_at=now - timedelta(days=45))
        AgentRunMemoryItem.objects.filter(id=mem_new.id).update(created_at=now - timedelta(days=10))

        conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="retention-session",
        )

        # Segment that straddles the cutoff: should be trimmed down to the within-retention message.
        msg_expired = ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.CUSTOMER,
            body="Expired message",
            sent_at=now - timedelta(days=45),
        )
        msg_kept = ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.AI,
            body="Kept message",
            sent_at=now - timedelta(days=10),
        )
        overlap_segment = CompactedHistorySegment.objects.create(
            conversation=conversation,
            segment_range="turns_1_to_2",
            start_message_id=msg_expired.id,
            end_message_id=msg_kept.id,
            start_message_sent_at=msg_expired.sent_at,
            end_message_sent_at=msg_kept.sent_at,
            summary="Old summary",
            full_messages=[
                {
                    "id": str(msg_expired.id),
                    "sender": msg_expired.sender,
                    "body": msg_expired.body,
                    "metadata": {},
                    "content_blocks": [],
                    "sent_at": msg_expired.sent_at.isoformat(),
                    "created_at": msg_expired.created_at.isoformat(),
                },
                {
                    "id": str(msg_kept.id),
                    "sender": msg_kept.sender,
                    "body": msg_kept.body,
                    "metadata": {},
                    "content_blocks": [],
                    "sent_at": msg_kept.sent_at.isoformat(),
                    "created_at": msg_kept.created_at.isoformat(),
                },
            ],
            extracted_facts={},
            extracted_decisions={},
            token_count_original=100,
            token_count_summary=20,
            compression_ratio=0.2,
        )

        # Fully expired segment: should be deleted.
        msg_expired_2 = ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.CUSTOMER,
            body="Expired message 2",
            sent_at=now - timedelta(days=60),
        )
        msg_expired_3 = ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.AI,
            body="Expired message 3",
            sent_at=now - timedelta(days=50),
        )
        expired_segment = CompactedHistorySegment.objects.create(
            conversation=conversation,
            segment_range="turns_3_to_4",
            start_message_id=msg_expired_2.id,
            end_message_id=msg_expired_3.id,
            start_message_sent_at=msg_expired_2.sent_at,
            end_message_sent_at=msg_expired_3.sent_at,
            summary="Expired summary",
            full_messages=[
                {
                    "id": str(msg_expired_2.id),
                    "sender": msg_expired_2.sender,
                    "body": msg_expired_2.body,
                    "metadata": {},
                    "content_blocks": [],
                    "sent_at": msg_expired_2.sent_at.isoformat(),
                    "created_at": msg_expired_2.created_at.isoformat(),
                },
                {
                    "id": str(msg_expired_3.id),
                    "sender": msg_expired_3.sender,
                    "body": msg_expired_3.body,
                    "metadata": {},
                    "content_blocks": [],
                    "sent_at": msg_expired_3.sent_at.isoformat(),
                    "created_at": msg_expired_3.created_at.isoformat(),
                },
            ],
            extracted_facts={},
            extracted_decisions={},
            token_count_original=100,
            token_count_summary=20,
            compression_ratio=0.2,
        )

        service = TenantRetentionPurgeService()
        result = service.purge_business(self.business, dry_run=False, batch_size=50, now=now)

        self.assertFalse(result.skipped)
        self.assertEqual(result.max_retention_days, cutoff_days)

        # Memory items: only the new one remains.
        self.assertFalse(AgentRunMemoryItem.objects.filter(id=mem_old.id).exists())
        self.assertTrue(AgentRunMemoryItem.objects.filter(id=mem_new.id).exists())

        # Expired segment is deleted.
        self.assertFalse(CompactedHistorySegment.objects.filter(id=expired_segment.id).exists())

        # Overlap segment is trimmed to only the kept message.
        overlap_segment.refresh_from_db()
        self.assertEqual(overlap_segment.start_message_id, msg_kept.id)
        self.assertEqual(len(overlap_segment.full_messages), 1)
        self.assertEqual(overlap_segment.full_messages[0]["id"], str(msg_kept.id))

    def test_legal_hold_skips_purge(self) -> None:
        now = timezone.now()
        TenantMemoryConfiguration.objects.create(
            business_profile=self.business,
            maximum_retention_days=30,
            purge_enabled=True,
            legal_hold=True,
        )

        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            title="Test Run",
            run_spec_snapshot={"goal": "Do something"},
        )
        mem_old = AgentRunMemoryItem.objects.create(
            run=run,
            kind="fact",
            key="old",
            content="old memory",
            created_by=self.user,
        )
        AgentRunMemoryItem.objects.filter(id=mem_old.id).update(created_at=now - timedelta(days=45))

        service = TenantRetentionPurgeService()
        result = service.purge_business(self.business, dry_run=False, batch_size=50, now=now)
        self.assertTrue(result.skipped)
        self.assertEqual(result.skip_reason, "legal_hold")
        self.assertTrue(AgentRunMemoryItem.objects.filter(id=mem_old.id).exists())

