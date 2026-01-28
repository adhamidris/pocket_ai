from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import AgentAutomation, AgentAutomationStatus, AgentRun, AgentRunStatus


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
                    "spec": {"version": 1, "goal": "Summarize sales", "tool_allowlist": ["search_knowledge"]},
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
            data=json.dumps({"name": "One-off task", "status": "active", "spec": {"goal": "Do X", "tool_allowlist": []}}),
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

    def test_automation_create_and_trigger(self) -> None:
        spec_url = reverse("api:agent-run-specs", args=[self.agent.id])
        spec_res = self.client.post(
            spec_url,
            data=json.dumps({"name": "Auto task", "status": "active", "spec": {"goal": "Auto", "tool_allowlist": []}}),
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

        trigger_url = reverse("api:agent-automation-trigger", args=[self.agent.id, automation_id])
        trigger_res = self.client.post(trigger_url, data=json.dumps({}), content_type="application/json")
        self.assertEqual(trigger_res.status_code, 201)
        self.assertEqual(trigger_res.json()["run"]["source"], "automation")

        automation = AgentAutomation.objects.get(id=uuid.UUID(automation_id))
        self.assertEqual(automation.status, AgentAutomationStatus.ACTIVE)
