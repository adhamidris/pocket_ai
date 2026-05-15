from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.mcp.prompts import build_messages
from apps.mcp.tools import _draft_task_handler, _request_task_activation_handler
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


class WorkflowResourceRefsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="workflow-refs@example.com", first_name="Workflow")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Workflow Ref Bank",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Sarah",
            role="Account Manager",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.user,
            session_token="workflow-ref-session",
            metadata={"actor_user_id": str(self.user.id)},
        )

    def tearDown(self) -> None:
        self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_draft_task_stores_pending_workflow_ref_for_prompt_context(self) -> None:
        result = _draft_task_handler(
            {
                "name": "Subscription Expiry Email Monitor",
                "goal": "Check the latest 10 emails every 5 minutes for subscriptions about to expire.",
                "trigger_type": "schedule",
                "trigger_config": {"cron": "*/5 * * * *"},
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        task_id = result["task"]["id"]
        self.conversation.refresh_from_db()
        metadata = self.conversation.metadata
        self.assertEqual(metadata["pending_workflow_activation_id"], task_id)
        self.assertEqual(metadata["resource_refs"][0]["id"], task_id)
        self.assertEqual(metadata["resource_refs"][0]["purpose"], "pending activation")

        messages = build_messages(
            conversation=self.conversation,
            user_message="alright let's do it",
            model_id="deepseek-chat",
        )
        system_text = str(messages[0]["content"])
        self.assertIn("Known workflow/task references", system_text)
        self.assertIn(str(task_id), system_text)
        self.assertIn("pending_activation=true", system_text)

    def test_activation_updates_ref_and_clears_pending_pointer(self) -> None:
        draft = _draft_task_handler(
            {
                "name": "Subscription Expiry Email Monitor",
                "goal": "Check the latest 10 emails every 5 minutes for subscriptions about to expire.",
                "trigger_type": "schedule",
                "trigger_config": {"cron": "*/5 * * * *"},
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        task_id = draft["task"]["id"]

        result = _request_task_activation_handler(
            {"task_id": task_id, "approved": True},
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result["status"], "ok")
        self.conversation.refresh_from_db()
        metadata = self.conversation.metadata
        self.assertNotIn("pending_workflow_activation_id", metadata)
        self.assertEqual(metadata["resource_refs"][0]["id"], task_id)
        self.assertEqual(metadata["resource_refs"][0]["status"], "active")
        self.assertEqual(metadata["resource_refs"][0]["purpose"], "active")
