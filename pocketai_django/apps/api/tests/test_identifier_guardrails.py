from __future__ import annotations

import json

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import (
    BusinessProfile,
    IdentifierColumnMapping,
    IdentifierColumnStatus,
    IdentifierSchema,
    IdentifierSchemaStatus,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    RegistrationSession,
    User,
)
from apps.conversations.models import Conversation
from apps.services.mcp.identifier_registry import IdentifierGuardrail, IdentifierRegistryService


class IdentifierGuardrailsApiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(email="guard@example.com", first_name="Guard")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Guard Corp",
            industry="support",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Policies",
        )
        IdentifierRegistryService.set_match_policy(business_profile=self.business, policy="and")
        self.schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="email",
            display_name="Email",
            status=IdentifierSchemaStatus.ACTIVE,
            is_required=True,
        )
        IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=self.schema,
            upload=self.upload,
            column_name="Email",
            status=IdentifierColumnStatus.ACTIVE,
        )
        conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="guard-session",
            metadata={"customer_identifiers": {"email": "visitor@example.com"}},
        )
        guard = IdentifierGuardrail.from_conversation(conversation)
        decision = guard.require_for_upload(str(self.upload.id))
        IdentifierRegistryService.record_event(
            business_profile=self.business,
            decision=decision,
            tool="search_knowledge",
            conversation=conversation,
            upload_ids=[str(self.upload.id)],
        )

    def test_guardrails_overview_returns_registry_uploads_and_events(self) -> None:
        url = reverse("api:identifier-guardrails", kwargs={"business_id": self.business.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["match_policy"], "and")
        self.assertEqual(len(payload["registry"]), 1)
        schema = payload["registry"][0]
        self.assertEqual(schema["key"], "email")
        self.assertEqual(schema["status"], "active")
        self.assertTrue(schema["columns"])

        uploads = payload["uploads"]
        self.assertTrue(any(item["id"] == str(self.upload.id) for item in uploads))

        events = payload["events"]
        self.assertEqual(events["summary"]["total"], 1)
        self.assertEqual(events["summary"]["ok"], 1)
        self.assertEqual(events["summary"]["required"], 0)
        self.assertEqual(events["items"][0]["status"], "ok")

    def test_identifier_eval_reports_pass_and_fail(self) -> None:
        url = reverse("api:identifier-eval", kwargs={"business_id": self.business.id})
        ok_payload = {"upload_id": str(self.upload.id), "identifiers": {"email": "ok@example.com"}}
        ok_response = self.client.post(
            url,
            data=json.dumps(ok_payload),
            content_type="application/json",
        )
        self.assertEqual(ok_response.status_code, 200)
        ok_data = ok_response.json()
        self.assertEqual(ok_data["status"], "ok")
        self.assertTrue(ok_data["passed"])
        self.assertIn("email", ok_data["required_keys"])

        missing_payload = {"upload_id": str(self.upload.id), "identifiers": {}}
        missing_response = self.client.post(
            url,
            data=json.dumps(missing_payload),
            content_type="application/json",
        )
        self.assertEqual(missing_response.status_code, 200)
        missing_data = missing_response.json()
        self.assertEqual(missing_data["status"], "identifier_required")
        self.assertFalse(missing_data["passed"])
        self.assertIn("email", missing_data["required_keys"])
