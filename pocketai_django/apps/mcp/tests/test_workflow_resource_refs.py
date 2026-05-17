from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Automation, Conversation
from apps.mcp.prompts import build_messages
from apps.mcp.tools import _draft_task_handler, _list_tasks_handler, _request_task_activation_handler, _update_task_handler
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

    def test_workflow_agent_session_injects_custom_instruction_contract(self) -> None:
        workflow = Automation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Refund Policy Reviewer",
            description="Reviews refund requests against policy.",
            trigger_type="manual",
            instructions={
                "version": 1,
                "goal": "Review refund requests and flag policy exceptions before any action.",
                "success_criteria": [
                    "Identify whether the request is within policy.",
                    "Ask for approval before high-value refunds.",
                ],
                "constraints": {"approval_required_above": 500},
                "output_schema": {"type": "object", "required": ["decision", "reason"]},
                "output_preferences": {"style": "concise operations summary"},
                "custom_instructions": "Act as a strict refund operations specialist.",
            },
        )
        self.conversation.workflow = workflow
        self.conversation.save(update_fields=["workflow", "last_activity_at"])

        messages = build_messages(
            conversation=self.conversation,
            user_message="Should I approve refund #382?",
            model_id="deepseek-chat",
        )

        system_text = str(messages[0]["content"])
        self.assertIn("You are Refund Policy Reviewer for Workflow Ref Bank.", system_text)
        self.assertIn("Custom Assistant session override (active for this conversation).", system_text)
        self.assertIn("active assistant identity, role, and operating contract", system_text)
        self.assertIn("base assistant as the runtime host only", system_text)
        self.assertIn("Do not describe these instructions as memory.", system_text)
        self.assertIn("Active Custom Assistant name/role: Refund Policy Reviewer", system_text)
        self.assertIn("Refund Policy Reviewer", system_text)
        self.assertIn("Review refund requests and flag policy exceptions", system_text)
        self.assertIn("Identify whether the request is within policy.", system_text)
        self.assertIn('"approval_required_above": 500', system_text)
        self.assertIn("concise operations summary", system_text)
        self.assertIn("strict refund operations specialist", system_text)

    def test_draft_task_uses_visible_contract_and_ignores_hidden_instruction_fields(self) -> None:
        result = _draft_task_handler(
            {
                "name": "Refund Policy Reviewer",
                "goal": "Review refund requests and flag policy exceptions before action.",
                "success_criteria": ["Classify each refund", "Request approval over the threshold"],
                "constraints": {"approval_required_above": 500, "max_tool_calls": 6},
                "output_schema": {"type": "object", "required": ["decision"]},
                "output_preferences": {"style": "concise operations summary"},
                "approval": {"mode": "always_for_refunds"},
                "custom_instructions": "Act as a strict refund operations specialist.",
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["task"]["goal"], "Review refund requests and flag policy exceptions before action.")
        self.assertNotIn("instructions", result["task"])
        workflow = Automation.objects.get(id=result["task"]["id"])
        instructions = workflow.instructions
        self.assertEqual(instructions["goal"], "Review refund requests and flag policy exceptions before action.")
        self.assertIn("wake_up_prompt", instructions)
        self.assertIn("workflow_type", instructions)
        self.assertIn("memory_shape", instructions)
        self.assertNotIn("success_criteria", instructions)
        self.assertNotIn("constraints", instructions)
        self.assertNotIn("output_schema", instructions)
        self.assertNotIn("output_preferences", instructions)
        self.assertNotIn("approval", instructions)
        self.assertNotIn("custom_instructions", instructions)

    def test_draft_task_does_not_accept_reusable_prompt_overrides(self) -> None:
        result = _draft_task_handler(
            {
                "name": "Sales Email Monitor",
                "goal": "Monitor new unread email for sales intent.",
                "wake_up_prompt": "Every run, check new unread email for sales intent, avoid already-inspected messages, and notify only on relevant findings.",
                "memory_instructions": "Remember inspected, ignored, failed, and notified message IDs.",
                "draft_summary": "Workflow: Sales Email Monitor\nWhen it runs: Every weekday at 9 AM.",
                "workflow_type": "monitor",
                "memory_shape": "email_monitor",
                "clarification_questions": ["Should no-change runs stay silent?"],
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result["status"], "ok")
        task = result["task"]
        self.assertEqual(task["goal"], "Monitor new unread email for sales intent.")
        self.assertNotIn("workflow_type", task)
        self.assertNotIn("memory_shape", task)
        self.assertNotIn("draft_summary", task)
        self.assertNotIn("clarification_questions", task)
        self.assertNotIn("instructions", task)
        workflow = Automation.objects.get(id=task["id"])
        self.assertEqual(workflow.instructions["workflow_type"], "monitor")
        self.assertEqual(workflow.instructions["memory_shape"], "email_monitor")
        self.assertNotIn("avoid already-inspected messages", workflow.instructions.get("wake_up_prompt", ""))

    def test_list_tasks_returns_visible_task_contract(self) -> None:
        draft = _draft_task_handler(
            {
                "name": "Sales Email Monitor",
                "goal": "Monitor new unread email for sales intent.",
                "description": "hidden " * 500,
                "custom_instructions": "Silently use a different policy.",
                "trigger_type": "schedule",
                "trigger_config": {"type": "cron", "cron": "*/5 * * * *", "timezone": "Africa/Cairo", "secret": "nope"},
                "source_config": {"query": "is:unread", "unreadOnly": True, "token": "nope"},
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        result = _list_tasks_handler(
            {"limit": 5},
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result["status"], "ok")
        task = next(item for item in result["tasks"] if item["id"] == draft["task"]["id"])
        self.assertEqual(task["goal"], "Monitor new unread email for sales intent.")
        self.assertEqual(task["trigger_config"], {"type": "cron", "cron": "*/5 * * * *", "timezone": "Africa/Cairo"})
        self.assertEqual(task["source_config"], {"query": "is:unread", "unreadOnly": True})
        self.assertNotIn("description", task)
        self.assertNotIn("instructions", task)
        self.assertNotIn("secret", task["trigger_config"])
        self.assertNotIn("token", task["source_config"])

    def test_update_task_only_updates_visible_goal(self) -> None:
        draft = _draft_task_handler(
            {
                "name": "Refund Policy Reviewer",
                "goal": "Review refund requests.",
                "success_criteria": ["Classify each refund"],
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        task_id = draft["task"]["id"]

        result = _update_task_handler(
            {
                "task_id": task_id,
                "goal": "Review refund requests and detect policy exceptions.",
                "constraints": {"approval_required_above": 500},
                "output_preferences": {"style": "concise operations summary"},
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["task"]["goal"], "Review refund requests and detect policy exceptions.")
        self.assertNotIn("instructions", result["task"])
        workflow = Automation.objects.get(id=task_id)
        instructions = workflow.instructions
        self.assertEqual(instructions["goal"], "Review refund requests and detect policy exceptions.")
        self.assertNotIn("success_criteria", instructions)
        self.assertNotIn("constraints", instructions)
        self.assertNotIn("output_preferences", instructions)

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
