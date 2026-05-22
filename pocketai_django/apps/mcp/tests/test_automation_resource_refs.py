from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.assistants.models import CustomAssistant
from apps.agentic_tasks.models import AgenticTask
from apps.conversations.models import Conversation
from apps.mcp.prompts import build_messages
from apps.mcp.tools import _draft_agentic_task_handler
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


class AgenticTaskResourceRefsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(email="agentic_task-refs@example.com", first_name="AgenticTask")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="AgenticTask Ref Bank",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Sarah",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.user,
            session_token="agentic_task-ref-session",
            metadata={"actor_user_id": str(self.user.id)},
        )

    def tearDown(self) -> None:
        self.tenant_scope.__exit__(None, None, None)

    def test_draft_task_stores_pending_agentic_task_ref_for_prompt_context(self) -> None:
        result = _draft_agentic_task_handler(
            {
                "name": "Subscription Expiry Email Monitor",
                "goal": "Check the latest 10 emails every 5 minutes for subscriptions about to expire.",
                "schedule_enabled": True,
                "schedule_config": {"cron": "*/5 * * * *"},
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        task_id = result["agentic_task"]["id"]
        self.conversation.refresh_from_db()
        metadata = self.conversation.metadata
        self.assertEqual(metadata["pending_agentic_task_activation_id"], task_id)
        self.assertEqual(metadata["resource_refs"][0]["type"], "agentic_task")
        self.assertEqual(metadata["resource_refs"][0]["id"], task_id)

        messages = build_messages(
            conversation=self.conversation,
            user_message="alright let's do it",
            model_id="deepseek-chat",
        )
        system_text = str(messages[0]["content"])
        self.assertIn("Known Agentic Task references", system_text)
        self.assertIn(str(task_id), system_text)
        self.assertIn("pending_activation=true", system_text)

    def test_custom_assistant_session_injects_custom_instruction_contract_without_agentic_task(self) -> None:
        custom_assistant = CustomAssistant.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Refund Policy Reviewer",
            description="Reviews refund requests against policy.",
            instructions={
                "goal": "Review refund requests and flag policy exceptions before any action.",
                "custom_instructions": "Act as a strict refund operations specialist.",
            },
        )
        self.conversation.custom_assistant = custom_assistant
        self.conversation.save(update_fields=["custom_assistant", "last_activity_at"])

        messages = build_messages(
            conversation=self.conversation,
            user_message="Should I approve refund #382?",
            model_id="deepseek-chat",
        )

        system_text = str(messages[0]["content"])
        self.assertIn("Custom Assistant session override (active for this conversation).", system_text)
        self.assertIn("Active Custom Assistant name/role: Refund Policy Reviewer", system_text)
        self.assertIn("Review refund requests and flag policy exceptions", system_text)
        self.assertFalse(AgenticTask.objects.exists())
