from __future__ import annotations

import uuid

from django.test import TestCase

from apps.accounts.models import (
    BusinessProfile,
    IdentifierColumnMapping,
    IdentifierColumnStatus,
    IdentifierSchema,
    IdentifierSchemaStatus,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadTable,
    RegistrationSession,
    User,
)
from apps.conversations.models import Conversation, IdentifierEvent
from apps.mcp.identifier_registry import (
    IdentifierDetector,
    IdentifierGuardrail,
    IdentifierRegistryService,
)
from apps.mcp.identifier_eval import IdentifierEvalCase, IdentifierEvalHarness, IdentifierEvalError


class IdentifierRegistryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(email="detector@example.com", first_name="Detector")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Detector Biz",
            industry="support",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Contacts",
        )

    def test_detector_picks_common_identifiers(self) -> None:
        detector = IdentifierDetector()
        proposals = detector.detect(["Email", "Phone Number", "Notes"])
        keys = {p["key"] for p in proposals}
        self.assertIn("email", keys)
        self.assertIn("phone", keys)

    def test_detector_fuzzy_identifier_hints(self) -> None:
        detector = IdentifierDetector()
        proposals = detector.detect(["Applicant Email", "Contact mobile", "Ticket ID"])
        keys = {p["key"] for p in proposals}
        self.assertIn("email", keys)
        self.assertIn("phone", keys)
        self.assertIn("external_id", keys)

    def test_propose_from_headers_creates_schema_and_column(self) -> None:
        schemas = IdentifierRegistryService.propose_from_headers(
            business_profile=self.business,
            headers=["Email", "Phone"],
            upload=self.upload,
            auto_promote=False,
            match_policy="and",
        )
        self.assertTrue(schemas)
        email_schema = IdentifierSchema.objects.filter(business_profile=self.business, key="email").first()
        self.assertIsNotNone(email_schema)
        self.assertEqual(email_schema.status, IdentifierSchemaStatus.PROPOSED)
        column = IdentifierColumnMapping.objects.filter(identifier=email_schema, upload=self.upload).first()
        self.assertIsNotNone(column)
        self.assertEqual(column.status, IdentifierColumnStatus.PROPOSED)
        self.assertEqual(IdentifierRegistryService.get_match_policy(self.business), "and")

    def test_propose_with_empty_headers_falls_back_to_common(self) -> None:
        schemas = IdentifierRegistryService.propose_from_headers(
            business_profile=self.business,
            headers=[],
            upload=self.upload,
            auto_promote=False,
        )
        keys = {schema.key for schema in schemas}
        self.assertIn("email", keys)
        self.assertIn("phone", keys)
        self.assertIn("customer_id", keys)

    def test_propose_autopromotes_columns_when_schema_active(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="email",
            display_name="Email",
            status=IdentifierSchemaStatus.ACTIVE,
            is_required=True,
        )
        schemas = IdentifierRegistryService.propose_from_headers(
            business_profile=self.business,
            headers=["Email"],
            upload=self.upload,
            auto_promote=False,
        )
        self.assertTrue(schemas)
        column = IdentifierColumnMapping.objects.filter(identifier=schema, upload=self.upload).first()
        self.assertIsNotNone(column)
        self.assertEqual(column.status, IdentifierColumnStatus.ACTIVE)

    def test_propose_uses_upload_table_headers(self) -> None:
        KnowledgeUploadTable.objects.create(
            upload=self.upload,
            column_schema=["Ticket ID", "Customer ID", "Email", "Plan"],
        )
        schemas = IdentifierRegistryService.propose_from_headers(
            business_profile=self.business,
            headers=[],
            upload=self.upload,
            auto_promote=False,
        )
        keys = {schema.key for schema in schemas}
        self.assertIn("email", keys)
        self.assertIn("customer_id", keys)
        self.assertNotIn("phone", keys)  # came from headers, not fallback

    def test_auto_promote_marks_active(self) -> None:
        schemas = IdentifierRegistryService.propose_from_headers(
            business_profile=self.business,
            headers=["Customer ID"],
            upload=self.upload,
            auto_promote=True,
        )
        self.assertTrue(schemas)
        schema = IdentifierSchema.objects.get(business_profile=self.business, key="customer_id")
        self.assertEqual(schema.status, IdentifierSchemaStatus.ACTIVE)
        column = IdentifierColumnMapping.objects.get(identifier=schema, upload=self.upload)
        self.assertEqual(column.status, IdentifierColumnStatus.ACTIVE)

    def test_guard_includes_hashed_identifiers(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="email",
            display_name="Email",
            status=IdentifierSchemaStatus.ACTIVE,
            is_required=True,
        )
        IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=schema,
            upload=self.upload,
            column_name="Email",
            status=IdentifierColumnStatus.ACTIVE,
        )
        conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="tok-guard",
            metadata={"customer_identifiers": {"email": "user@example.com"}},
        )
        guard = IdentifierGuardrail.from_conversation(conversation)
        decision = guard.require_for_upload(str(self.upload.id))
        self.assertEqual(decision.status, "ok")
        hashes = decision.provided_hashes
        self.assertIn("email", hashes)
        self.assertTrue(hashes["email"])
        self.assertNotEqual(hashes["email"], "user@example.com")

    def test_identifier_event_recording(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="email",
            display_name="Email",
            status=IdentifierSchemaStatus.ACTIVE,
            is_required=True,
        )
        IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=schema,
            upload=self.upload,
            column_name="Email",
            status=IdentifierColumnStatus.ACTIVE,
        )
        conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="tok-event",
            metadata={"customer_identifiers": {"email": "event@example.com"}},
        )
        guard = IdentifierGuardrail.from_conversation(conversation)
        decision = guard.require_for_upload(str(self.upload.id))
        IdentifierRegistryService.record_event(
            business_profile=self.business,
            decision=decision,
            tool="read_document",
            conversation=conversation,
            upload_ids=[str(self.upload.id)],
        )
        self.assertTrue(IdentifierEvent.objects.filter(business_profile=self.business).exists())

    def test_identifier_eval_harness_pass_fail(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="email",
            display_name="Email",
            status=IdentifierSchemaStatus.ACTIVE,
            is_required=True,
        )
        IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=schema,
            upload=self.upload,
            column_name="Email",
            status=IdentifierColumnStatus.ACTIVE,
        )
        harness = IdentifierEvalHarness(business_profile=self.business, enforce=False)
        cases = [
            IdentifierEvalCase(
                name="with_email",
                upload_id=self.upload.id,
                provided_identifiers={"email": "ok@example.com"},
                expected_status="ok",
            ),
            IdentifierEvalCase(
                name="missing_email",
                upload_id=self.upload.id,
                provided_identifiers={},
                expected_status="identifier_required",
            ),
        ]
        results = harness.run(cases)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].passed)
        self.assertTrue(results[1].passed)

        harness_enforce = IdentifierEvalHarness(business_profile=self.business, enforce=True)
        failing_case = IdentifierEvalCase(
            name="should_fail",
            upload_id=self.upload.id,
            provided_identifiers={},
            expected_status="ok",
        )
        with self.assertRaises(IdentifierEvalError):
            harness_enforce.run([failing_case])

    def test_approve_schema_promotes_columns(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="phone",
            display_name="Phone",
            status=IdentifierSchemaStatus.PROPOSED,
            is_required=True,
        )
        column = IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=schema,
            upload=self.upload,
            column_name="Phone",
            status=IdentifierColumnStatus.PROPOSED,
        )
        IdentifierRegistryService.approve_schema(schema)
        column.refresh_from_db()
        self.assertEqual(column.status, IdentifierColumnStatus.ACTIVE)

    def test_reject_schema_disables_columns(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="phone",
            display_name="Phone",
            status=IdentifierSchemaStatus.PROPOSED,
            is_required=True,
        )
        column = IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=schema,
            upload=self.upload,
            column_name="Phone",
            status=IdentifierColumnStatus.ACTIVE,
        )
        IdentifierRegistryService.reject_schema(schema)
        schema.refresh_from_db()
        column.refresh_from_db()
        self.assertEqual(schema.status, IdentifierSchemaStatus.DISABLED)
        self.assertEqual(column.status, IdentifierColumnStatus.DISABLED)
