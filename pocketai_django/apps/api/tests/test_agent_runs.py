from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AgentDepartment, AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import (
    AgentRun,
    AgentRunStatus,
    AgentWorkflow,
    AgentWorkflowStatus,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    MemoryVisibility,
)
from apps.conversations.workflow_processing import AgentWorkflowProcessingService
from apps.integrations.models import EmailAccount, EmailAccountProvider, EmailAccountStatus


User = get_user_model()


class AgentRunsApiTests(TestCase):
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
        self.client.force_login(self.user)

    def test_departments_and_agent_hierarchy_create_via_api(self) -> None:
        dept_res = self.client.post(
            reverse("api:departments-list") + f"?business_id={self.business.id}",
            data=json.dumps({"name": "Finance", "description": "Money work"}),
            content_type="application/json",
        )
        self.assertEqual(dept_res.status_code, 201)
        department_id = dept_res.json()["department"]["id"]

        lead_res = self.client.post(
            reverse("api:agents-list") + f"?business_id={self.business.id}",
            data=json.dumps({"name": "Finance Lead", "agentType": "department_lead", "departmentId": department_id}),
            content_type="application/json",
        )
        self.assertEqual(lead_res.status_code, 201)
        self.assertEqual(lead_res.json()["agent"]["departmentId"], department_id)
        self.assertTrue(lead_res.json()["agent"]["canManageTasks"])

        specialist_res = self.client.post(
            reverse("api:agents-list") + f"?business_id={self.business.id}",
            data=json.dumps(
                {
                    "name": "Invoice Checker",
                    "agentType": "background",
                    "departmentId": department_id,
                    "managerAgentId": lead_res.json()["agent"]["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(specialist_res.status_code, 201)
        self.assertEqual(specialist_res.json()["agent"]["managerAgentId"], lead_res.json()["agent"]["id"])
        self.assertTrue(AgentDepartment.objects.filter(id=uuid.UUID(department_id), agents__name="Invoice Checker").exists())

    def test_api_rejects_second_active_main_agent(self) -> None:
        main_res = self.client.post(
            reverse("api:agents-list") + f"?business_id={self.business.id}",
            data=json.dumps({"name": "Main", "agentType": "main"}),
            content_type="application/json",
        )
        self.assertEqual(main_res.status_code, 201)

        duplicate_res = self.client.post(
            reverse("api:agents-list") + f"?business_id={self.business.id}",
            data=json.dumps({"name": "Second Main", "agentType": "main"}),
            content_type="application/json",
        )
        self.assertEqual(duplicate_res.status_code, 400)

    def test_workflow_create_list_and_manual_run(self) -> None:
        url = reverse("api:agent-workflows", args=[self.agent.id])
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "Daily Sales Summary",
                    "status": "active",
                    "visibility": "initiator",
                    "triggerType": "manual",
                    "instructions": {"version": 1, "goal": "Summarize sales"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        workflow_id = response.json()["workflow"]["id"]
        self.assertEqual(response.json()["workflow"]["reviewMode"], "on_risk")
        self.assertEqual(response.json()["workflow"]["autonomyMode"], "draft_for_approval")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()["workflows"]), 1)

        run_url = reverse("api:agent-workflow-runs", args=[self.agent.id, workflow_id])
        run_res = self.client.post(run_url, data="{}", content_type="application/json")
        self.assertEqual(run_res.status_code, 201)
        self.assertEqual(run_res.json()["run"]["workflowId"], workflow_id)
        self.assertEqual(run_res.json()["run"]["status"], AgentRunStatus.QUEUED)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        listed = next(item for item in response.json()["workflows"] if item["id"] == workflow_id)
        self.assertEqual(listed["latestRun"]["id"], run_res.json()["run"]["id"])

    def test_pausing_workflow_cancels_open_runs_by_default(self) -> None:
        workflow = AgentWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Recurring task",
            status=AgentWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={"goal": "Check things"},
            next_trigger_at=timezone.now(),
        )
        queued = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            created_by=self.user,
            title="Queued",
            status=AgentRunStatus.QUEUED,
        )
        waiting = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            created_by=self.user,
            title="Waiting",
            status=AgentRunStatus.WAITING_APPROVAL,
        )

        response = self.client.patch(
            reverse("api:agent-workflow-detail", args=[self.agent.id, workflow.id]),
            data=json.dumps({"status": "paused"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workflow"]["status"], "paused")
        self.assertIsNone(response.json()["workflow"]["nextTriggerAt"])
        queued.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual(queued.status, AgentRunStatus.CANCELLED)
        self.assertEqual(waiting.status, AgentRunStatus.CANCELLED)

    def test_deleting_workflow_removes_flow_and_cancels_open_runs(self) -> None:
        thread = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.user,
            channel=ConversationChannel.API,
            status=ConversationStatus.LIVE,
            metadata={"type": "workflow_thread"},
        )
        workflow = AgentWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            conversation=thread,
            name="Delete me",
            status=AgentWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={"goal": "Check things"},
            next_trigger_at=timezone.now(),
        )
        thread.metadata = {"type": "workflow_thread", "workflow_id": str(workflow.id), "workflow_name": workflow.name}
        thread.save(update_fields=["metadata", "last_activity_at"])
        queued = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            conversation=thread,
            workflow=workflow,
            created_by=self.user,
            title="Queued",
            status=AgentRunStatus.QUEUED,
        )
        completed = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            conversation=thread,
            workflow=workflow,
            created_by=self.user,
            title="Completed",
            status=AgentRunStatus.COMPLETED,
            workflow_snapshot={"name": workflow.name, "goal": "Check things"},
        )

        response = self.client.delete(reverse("api:agent-workflow-detail", args=[self.agent.id, workflow.id]))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(AgentWorkflow.objects.filter(id=workflow.id).exists())
        self.assertFalse(Conversation.objects.filter(id=thread.id).exists())
        queued.refresh_from_db()
        completed.refresh_from_db()
        self.assertEqual(queued.status, AgentRunStatus.CANCELLED)
        self.assertIsNone(queued.workflow_id)
        self.assertIsNone(queued.conversation_id)
        self.assertEqual(queued.metadata["cancelled_by_workflow"], "delete")
        self.assertEqual(completed.status, AgentRunStatus.COMPLETED)
        self.assertIsNone(completed.workflow_id)
        self.assertEqual(completed.workflow_snapshot["name"], "Delete me")

    def test_runs_create_cancel_user_input_and_events(self) -> None:
        runs_url = reverse("api:agent-runs", args=[self.agent.id])
        run_res = self.client.post(
            runs_url,
            data=json.dumps({"title": "Test run", "workflowSnapshot": {"goal": "Do X"}}),
            content_type="application/json",
        )
        self.assertEqual(run_res.status_code, 201)
        run_id = run_res.json()["run"]["id"]

        cancel_url = reverse("api:agent-run-cancel", args=[self.agent.id, run_id])
        cancel_res = self.client.post(cancel_url, data=json.dumps({"reason": "stop"}), content_type="application/json")
        self.assertEqual(cancel_res.status_code, 200)
        self.assertEqual(cancel_res.json()["run"]["status"], "cancelled")

        run = AgentRun.objects.get(id=uuid.UUID(run_id))
        run.status = AgentRunStatus.WAITING_USER
        run.finished_at = None
        run.error_detail = ""
        run.save(update_fields=["status", "finished_at", "error_detail", "updated_at"])

        user_input_url = reverse("api:agent-run-user-input", args=[self.agent.id, run_id])
        ui_res = self.client.post(user_input_url, data=json.dumps({"message": "ok"}), content_type="application/json")
        self.assertEqual(ui_res.status_code, 200)
        self.assertEqual(ui_res.json()["run"]["status"], "queued")
        self.assertTrue(MemoryItem.objects.filter(run=run, key="user_input").exists())

        events_url = reverse("api:agent-run-events", args=[self.agent.id, run_id])
        events_res = self.client.get(events_url)
        self.assertEqual(events_res.status_code, 200)
        self.assertGreaterEqual(len(events_res.json()["events"]), 1)

    def test_scheduled_workflow_worker_triggers_due_workflow(self) -> None:
        workflow = AgentWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Due schedule",
            status=AgentWorkflowStatus.ACTIVE,
            trigger_type="schedule",
            trigger_config={"cron": "* * * * *"},
            instructions={"goal": "Auto"},
            next_trigger_at=timezone.now(),
        )
        result = AgentWorkflowProcessingService().process_next_due_workflow()
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "triggered")
        run = AgentRun.objects.get(id=uuid.UUID(result.run_id))
        self.assertEqual(run.workflow, workflow)
        self.assertEqual(run.source, "schedule")

    def test_schedule_webhook_and_email_workflows_create_via_api(self) -> None:
        url = reverse("api:agent-workflows", args=[self.agent.id])
        schedule_res = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "Weekday brief",
                    "status": "active",
                    "triggerType": "schedule",
                    "triggerConfig": {"type": "cron", "cron": "0 9 * * 1-5", "timezone": "Africa/Cairo"},
                    "instructions": {"goal": "Send a weekday brief"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(schedule_res.status_code, 201)
        self.assertIsNotNone(schedule_res.json()["workflow"]["nextTriggerAt"])

        webhook_res = self.client.post(
            url,
            data=json.dumps({"name": "CRM webhook", "status": "active", "triggerType": "webhook", "instructions": {"goal": "Handle webhook"}}),
            content_type="application/json",
        )
        self.assertEqual(webhook_res.status_code, 201)
        webhook = webhook_res.json()["workflow"]
        self.assertTrue(webhook["triggerConfig"]["secret"])

        trigger_res = self.client.post(
            reverse("api:workflow-webhook-trigger", args=[webhook["id"], webhook["triggerConfig"]["secret"]]),
            data=json.dumps({"event": "created"}),
            content_type="application/json",
        )
        self.assertEqual(trigger_res.status_code, 201)
        self.assertTrue(AgentRun.objects.filter(id=uuid.UUID(trigger_res.json()["runId"]), source="webhook").exists())

        account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            email_address="owner@example.com",
            status=EmailAccountStatus.CONNECTED,
        )
        email_res = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "Inbox triage",
                    "status": "active",
                    "triggerType": "email_inbox",
                    "emailAccountId": str(account.id),
                    "sourceConfig": {"query": "newer_than:1d", "unreadOnly": True},
                    "pollIntervalSeconds": 300,
                    "maxEventsPerPoll": 3,
                    "instructions": {"goal": "Triage incoming email"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(email_res.status_code, 201)
        self.assertEqual(email_res.json()["workflow"]["emailAccountId"], str(account.id))

    def test_operations_endpoint_reports_queue_and_email_accounts(self) -> None:
        EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            email_address="owner@example.com",
            status=EmailAccountStatus.CONNECTED,
        )
        AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            title="Queued",
            status=AgentRunStatus.QUEUED,
        )
        response = self.client.get(reverse("api:agent-operations", args=[self.agent.id]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["operations"]["queuedRuns"], 1)
        self.assertEqual(len(payload["emailAccounts"]), 1)

    def test_memory_api_is_agentic_create_human_manage(self) -> None:
        create_res = self.client.post(
            reverse("api:memory-list"),
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "agentId": str(self.agent.id),
                    "content": "Always escalate payment disputes.",
                    "kind": "instruction",
                    "sensitivity": "normal",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_res.status_code, 405)

        memory = MemoryItem.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            scope=MemoryScope.AGENT,
            kind=MemoryKind.INSTRUCTION,
            key="payment_disputes",
            content="Always escalate payment disputes.",
            status=MemoryStatus.PENDING_REVIEW,
            visibility=MemoryVisibility.SHARED,
        )
        approve_res = self.client.post(reverse("api:memory-approve", args=[memory.id]), data="{}", content_type="application/json")
        self.assertEqual(approve_res.status_code, 200)
        self.assertEqual(approve_res.json()["memory"]["status"], MemoryStatus.ACTIVE)

        delete_res = self.client.post(reverse("api:memory-delete", args=[memory.id]), data="{}", content_type="application/json")
        self.assertEqual(delete_res.status_code, 200)
        self.assertEqual(delete_res.json()["memory"]["status"], MemoryStatus.DELETED)

    def test_memory_list_defaults_to_curated_records(self) -> None:
        workflow = AgentWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Email Checker",
        )
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            created_by=self.user,
            title="Email run",
        )
        curated = MemoryItem.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            scope=MemoryScope.AGENT,
            kind=MemoryKind.PREFERENCE,
            key="report_style",
            content="Keep reports brief.",
            visibility=MemoryVisibility.SHARED,
        )
        MemoryItem.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            run=run,
            scope=MemoryScope.WORKFLOW,
            kind=MemoryKind.STATE_NOTE,
            key=f"run_report_{run.id}",
            content='{"status": "no_change", "findings": ["No inbox connected."]}',
            payload={"source": "run_report"},
            visibility=MemoryVisibility.SHARED,
        )
        MemoryItem.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            run=run,
            scope=MemoryScope.RUN,
            kind=MemoryKind.EXTRACTED_DATA,
            key="search_knowledge_arg_query",
            content="email subscriptions",
            visibility=MemoryVisibility.PRIVATE,
        )

        saved = self.client.get(reverse("api:memory-list"), {"agentId": self.agent.id})
        self.assertEqual(saved.status_code, 200)
        payload = saved.json()
        self.assertEqual(payload["view"], "saved")
        self.assertNotIn("summary", payload)
        self.assertEqual([item["id"] for item in payload["memory"]], [str(curated.id)])

        invalid_view = self.client.get(reverse("api:memory-list"), {"agentId": self.agent.id, "view": "workflow_state"})
        self.assertEqual(invalid_view.status_code, 400)
