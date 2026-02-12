from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    EmailAccountProvider,
    EmailAccountStatus,
    RegistrationSession,
)
from apps.integrations.models import EmailAccount
from apps.conversations.agent_automation_processing import AgentAutomationProcessingService
from apps.conversations.agent_watcher_processing import AgentWatcherProcessingService
from apps.conversations.models import AgentAutomation, AgentAutomationStatus, AgentRun, AgentRunStatus, Conversation, ConversationChannel


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
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Ops Agent",
            status="active",
        )
        self.client.force_login(self.user)

    def test_run_specs_create_and_list(self) -> None:
        url = reverse("api:agent-run-specs", args=[self.agent.id])
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "Daily Sales Summary",
                    "status": "active",
                    "visibility": "initiator",
                    "spec": {"version": 1, "goal": "Summarize sales"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        spec_id = response.json()["runSpec"]["id"]
        self.assertTrue(spec_id)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()["runSpecs"]), 1)

    def test_runs_create_cancel_user_input_and_events(self) -> None:
        spec_url = reverse("api:agent-run-specs", args=[self.agent.id])
        spec_res = self.client.post(
            spec_url,
            data=json.dumps({"name": "One-off task", "status": "active", "spec": {"goal": "Do X"}}),
            content_type="application/json",
        )
        self.assertEqual(spec_res.status_code, 201)
        spec_id = spec_res.json()["runSpec"]["id"]

        runs_url = reverse("api:agent-runs", args=[self.agent.id])
        run_res = self.client.post(
            runs_url,
            data=json.dumps({"runSpecId": spec_id, "title": "Test run"}),
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

        events_url = reverse("api:agent-run-events", args=[self.agent.id, run_id])
        events_res = self.client.get(events_url)
        self.assertEqual(events_res.status_code, 200)
        self.assertGreaterEqual(len(events_res.json()["events"]), 1)

    def test_run_visibility_initiator_vs_manager_access(self) -> None:
        employee = User.objects.create_user(email="employee@example.com", password="changeme123", first_name="Employee")
        self.agent.user = employee
        self.agent.save(update_fields=["user", "updated_at"])

        # Employee creates an initiator-only run.
        self.client.force_login(employee)
        runs_url = reverse("api:agent-runs", args=[self.agent.id])
        res_initiator = self.client.post(
            runs_url,
            data=json.dumps({"title": "Initiator run", "runSpecSnapshot": {"goal": "Do X"}}),
            content_type="application/json",
        )
        self.assertEqual(res_initiator.status_code, 201)
        initiator_run_id = res_initiator.json()["run"]["id"]

        # Employee creates a manager-visible run.
        res_managers = self.client.post(
            runs_url,
            data=json.dumps(
                {"title": "Managers run", "visibility": "managers", "runSpecSnapshot": {"goal": "Do Y"}}
            ),
            content_type="application/json",
        )
        self.assertEqual(res_managers.status_code, 201)
        managers_run_id = res_managers.json()["run"]["id"]

        # Business owner can access the agent, but should only see manager-visible runs.
        self.client.force_login(self.user)
        list_res = self.client.get(runs_url)
        self.assertEqual(list_res.status_code, 200)
        returned_ids = {item["id"] for item in (list_res.json().get("runs") or [])}
        self.assertIn(managers_run_id, returned_ids)
        self.assertNotIn(initiator_run_id, returned_ids)

        detail_url = reverse("api:agent-run-detail", args=[self.agent.id, initiator_run_id])
        detail_res = self.client.get(detail_url)
        self.assertEqual(detail_res.status_code, 404)

        detail_url_ok = reverse("api:agent-run-detail", args=[self.agent.id, managers_run_id])
        detail_res_ok = self.client.get(detail_url_ok)
        self.assertEqual(detail_res_ok.status_code, 200)
        self.assertEqual(detail_res_ok.json()["run"]["id"], managers_run_id)

    def test_automation_create_and_trigger(self) -> None:
        spec_url = reverse("api:agent-run-specs", args=[self.agent.id])
        spec_res = self.client.post(
            spec_url,
            data=json.dumps({"name": "Auto task", "status": "active", "spec": {"goal": "Auto"}}),
            content_type="application/json",
        )
        self.assertEqual(spec_res.status_code, 201)
        spec_id = spec_res.json()["runSpec"]["id"]

        automations_url = reverse("api:agent-automations", args=[self.agent.id])
        auto_res = self.client.post(
            automations_url,
            data=json.dumps(
                {
                    "name": "Daily Auto",
                    "status": "active",
                    "triggerType": "manual",
                    "runSpecId": spec_id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(auto_res.status_code, 201)
        automation_id = auto_res.json()["automation"]["id"]
        self.assertTrue(auto_res.json()["automation"]["conversationId"])

        trigger_url = reverse("api:agent-automation-trigger", args=[self.agent.id, automation_id])
        trigger_res = self.client.post(trigger_url, data=json.dumps({}), content_type="application/json")
        self.assertEqual(trigger_res.status_code, 201)
        self.assertEqual(trigger_res.json()["run"]["source"], "automation")

        automation = AgentAutomation.objects.get(id=uuid.UUID(automation_id))
        self.assertEqual(automation.status, AgentAutomationStatus.ACTIVE)

    def test_automation_cron_active_sets_next_trigger_at(self) -> None:
        spec_url = reverse("api:agent-run-specs", args=[self.agent.id])
        spec_res = self.client.post(
            spec_url,
            data=json.dumps({"name": "Cron task", "status": "active", "spec": {"goal": "Cron"}}),
            content_type="application/json",
        )
        self.assertEqual(spec_res.status_code, 201)
        spec_id = spec_res.json()["runSpec"]["id"]

        automations_url = reverse("api:agent-automations", args=[self.agent.id])
        res = self.client.post(
            automations_url,
            data=json.dumps(
                {
                    "name": "Every minute",
                    "status": "active",
                    "triggerType": "cron",
                    "triggerConfig": {"cron": "*/1 * * * *", "timezone": "UTC"},
                    "runSpecId": spec_id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        payload = res.json()["automation"]
        self.assertTrue(payload.get("nextTriggerAt"))

        automation = AgentAutomation.objects.get(id=uuid.UUID(payload["id"]))
        self.assertIsNotNone(automation.next_trigger_at)

    def test_automation_cron_active_invalid_schedule_rejected(self) -> None:
        automations_url = reverse("api:agent-automations", args=[self.agent.id])
        res = self.client.post(
            automations_url,
            data=json.dumps(
                {
                    "name": "Bad cron",
                    "status": "active",
                    "triggerType": "cron",
                    "triggerConfig": {},
                    "runSpecSnapshot": {"goal": "Auto"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_automation_worker_triggers_due_cron(self) -> None:
        automation = AgentAutomation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            name="Due cron",
            status=AgentAutomationStatus.ACTIVE,
            trigger_type="cron",
            trigger_config={"cron": "*/1 * * * *", "timezone": "UTC"},
            run_spec_snapshot={"goal": "Auto"},
            next_trigger_at=timezone.now() - timedelta(minutes=1),
        )
        service = AgentAutomationProcessingService()
        result = service.process_next_due_automation()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.action, "triggered")

        run = AgentRun.objects.get(id=uuid.UUID(result.run_id))
        self.assertEqual(run.source, "automation")
        automation.refresh_from_db()
        self.assertIsNotNone(automation.last_triggered_at)
        self.assertIsNotNone(automation.next_trigger_at)

    def test_automation_webhook_trigger_endpoint(self) -> None:
        automations_url = reverse("api:agent-automations", args=[self.agent.id])
        res = self.client.post(
            automations_url,
            data=json.dumps(
                {
                    "name": "Webhook auto",
                    "status": "active",
                    "triggerType": "webhook",
                    "triggerConfig": {},
                    "runSpecSnapshot": {"goal": "Auto"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        automation_payload = res.json()["automation"]
        automation_id = automation_payload["id"]
        token = (automation_payload.get("triggerConfig") or {}).get("secret")
        self.assertTrue(token)

        self.client.logout()
        webhook_url = reverse("api:automation-webhook-trigger", args=[automation_id, token])
        hook_res = self.client.post(webhook_url, data=b"{}", content_type="application/json")
        self.assertEqual(hook_res.status_code, 201)
        run_id = hook_res.json().get("runId")
        self.assertTrue(run_id)

    def test_watcher_create_and_list(self) -> None:
        account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            status=EmailAccountStatus.CONNECTED,
            email_address="owner@example.com",
        )
        account.credentials = {"access_token": "token"}
        account.save(update_fields=["credentials_encrypted", "credentials_key_version", "credentials_last_rotated_at", "updated_at"])

        url = reverse("api:agent-watchers", args=[self.agent.id])
        res = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "Inbox watcher",
                    "status": "active",
                    "watcherType": "email_inbox",
                    "emailAccountId": str(account.id),
                    "watchConfig": {"query": "is:unread", "unreadOnly": True},
                    "runSpecSnapshot": {"goal": "Handle new emails"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        payload = res.json()["watcher"]
        self.assertEqual(payload["status"], "active")
        self.assertTrue(payload.get("nextPollAt"))
        self.assertTrue(payload.get("conversationId"))

        list_res = self.client.get(url)
        self.assertEqual(list_res.status_code, 200)
        self.assertGreaterEqual(len(list_res.json()["watchers"]), 1)

    def test_watcher_worker_polls_and_dedupes(self) -> None:
        account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            status=EmailAccountStatus.CONNECTED,
            email_address="owner@example.com",
        )
        account.credentials = {"access_token": "token"}
        account.save(update_fields=["credentials_encrypted", "credentials_key_version", "credentials_last_rotated_at", "updated_at"])

        watchers_url = reverse("api:agent-watchers", args=[self.agent.id])
        create_res = self.client.post(
            watchers_url,
            data=json.dumps(
                {
                    "name": "Inbox watcher",
                    "status": "active",
                    "watcherType": "email_inbox",
                    "emailAccountId": str(account.id),
                    "watchConfig": {"query": "is:unread", "unreadOnly": True},
                    "pollIntervalSeconds": 60,
                    "maxEventsPerPoll": 3,
                    "runSpecSnapshot": {"goal": "Handle new emails"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_res.status_code, 201)
        watcher_id = create_res.json()["watcher"]["id"]
        self.assertTrue(create_res.json()["watcher"].get("conversationId"))

        # Force it due immediately.
        trigger_url = reverse("api:agent-watcher-trigger", args=[self.agent.id, watcher_id])
        trigger_res = self.client.post(trigger_url, data=json.dumps({}), content_type="application/json")
        self.assertEqual(trigger_res.status_code, 200)

        service = AgentWatcherProcessingService()
        with patch(
            "apps.conversations.agent_watcher_processing.gmail_search_messages",
            return_value={
                "results": [
                    {
                        "message_id": "m1",
                        "thread_id": "t1",
                        "subject": "Hello",
                        "from": "sender@example.com",
                        "snippet": "Hi there",
                        "date": timezone.now().isoformat(),
                    }
                ]
            },
        ):
            result = service.process_next_watcher()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.action, "polled")
        self.assertEqual(len(result.triggered_run_ids), 1)

        run = AgentRun.objects.get(id=uuid.UUID(result.triggered_run_ids[0]))
        self.assertEqual(run.source, "watcher")
        trigger = (run.metadata or {}).get("trigger")
        self.assertTrue(isinstance(trigger, dict))
        self.assertEqual(trigger.get("message_id"), "m1")

        # Poll again with the same email: should be deduped (no new run).
        self.client.post(trigger_url, data=json.dumps({}), content_type="application/json")
        service = AgentWatcherProcessingService()
        with patch(
            "apps.conversations.agent_watcher_processing.gmail_search_messages",
            return_value={
                "results": [
                    {
                        "message_id": "m1",
                        "thread_id": "t1",
                        "subject": "Hello",
                        "from": "sender@example.com",
                        "snippet": "Hi there",
                        "date": timezone.now().isoformat(),
                    }
                ]
            },
        ):
            result3 = service.process_next_watcher()
        self.assertIsNotNone(result3)
        assert result3 is not None
        self.assertEqual(result3.action, "polled")
        self.assertEqual(len(result3.triggered_run_ids), 0)

    def test_watcher_destination_config_saved(self) -> None:
        account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            status=EmailAccountStatus.CONNECTED,
            email_address="owner@example.com",
        )
        account.credentials = {"access_token": "token"}
        account.save(update_fields=["credentials_encrypted", "credentials_key_version", "credentials_last_rotated_at", "updated_at"])

        summary_conv = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            channel=ConversationChannel.API,
            metadata={"type": "summary_target"},
        )

        url = reverse("api:agent-watchers", args=[self.agent.id])
        res = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "Inbox watcher",
                    "status": "active",
                    "watcherType": "email_inbox",
                    "emailAccountId": str(account.id),
                    "watchConfig": {"query": "is:unread", "unreadOnly": True},
                    "destinationConfig": {"postSummaryToConversationId": str(summary_conv.id), "summaryMaxChars": 200},
                    "runSpecSnapshot": {"goal": "Handle new emails"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        dest = res.json()["watcher"].get("destinationConfig") or {}
        self.assertEqual(str(dest.get("postSummaryToConversationId")), str(summary_conv.id))
