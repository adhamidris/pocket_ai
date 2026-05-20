from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.agent_runs.models import AgentRun
from apps.automations.models import Automation
from apps.conversations.memory_extraction import MemoryExtractionService
from apps.conversations.models import MemoryItem


User = get_user_model()


class MemoryExtractionServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
            status="active",
        )
        self.agent = AgentProfile.objects.create(business_profile=self.business, user=self.user, name="Ops Agent")
        self.workflow = Automation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Email Checker",
            instructions={"goal": "Check email"},
        )
        self.run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            automation=self.workflow,
            created_by=self.user,
            title="Email Checker",
            run_snapshot={"name": self.workflow.name},
        )

    def test_email_draft_ids_are_not_saved_as_memory(self) -> None:
        service = MemoryExtractionService(use_llm_fallback=False)

        service.extract_from_tool_result(
            run=self.run,
            tool_name="email_create_draft",
            arguments={},
            result={
                "status": "ok",
                "draft_id": "r-4613223831426038363",
                "draftId": "r-4613223831426038363",
                "to": "customer@example.com",
                "subject": "Subscription update",
            },
            user=self.user,
        )

        self.assertFalse(MemoryItem.objects.filter(key__in=["email_draft_draft_id", "email_draft_draftId"]).exists())
        self.assertFalse(MemoryItem.objects.filter(content="r-4613223831426038363").exists())
        self.assertTrue(MemoryItem.objects.filter(key="email_draft_subject", content="Subscription update").exists())
        self.assertTrue(MemoryItem.objects.filter(key="email_draft_to", content="customer@example.com").exists())
