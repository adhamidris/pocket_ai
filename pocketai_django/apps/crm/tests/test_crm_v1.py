from __future__ import annotations

import json
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import BusinessProfile, RegistrationSession, User
from apps.crm.imports import process_next_job
from apps.crm.models import (
    CrmActivity,
    CrmCompany,
    CrmContact,
    CrmContactCompanyLink,
    CrmDuplicateSuggestion,
    CrmFieldDefinition,
    CrmFieldType,
    CrmFieldValue,
    CrmImportJob,
    CrmImportJobStatus,
)
from apps.crm.services import add_note
from apps.mcp.tools import execute_tool, get_tool_definitions


@override_settings(CRM_V1_GLOBAL_OVERRIDE=None)
class CrmV1RuntimeTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.media_root = tempfile.mkdtemp(prefix="crm-test-media-")
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()

        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            first_name="Owner",
            status="active",
        )
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="CRM Test Business",
            industry="Software",
            status="active",
        )
        self.client.force_login(self.user)
        self.other_user = User.objects.create_user(
            email="other-owner@example.com",
            password="testpass123",
            first_name="Other",
            status="active",
        )
        self.other_registration = RegistrationSession.objects.create(user=self.other_user)
        self.other_business = BusinessProfile.objects.create(
            user=self.other_user,
            registration_session=self.other_registration,
            name="Other Business",
            industry="Retail",
            status="active",
        )

    def tearDown(self) -> None:
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def _enable_crm(self) -> None:
        FeatureFlagService.set_flags(self.business, updates={"crm_v1": True})
        self.business.refresh_from_db()

    def test_crm_api_is_hidden_until_feature_enabled(self) -> None:
        response = self.client.get(f"/api/crm/contacts/?business_id={self.business.id}")
        self.assertEqual(response.status_code, 404)

        self._enable_crm()

        response = self.client.get(f"/api/crm/contacts/?business_id={self.business.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)

    def test_legacy_crm_runtime_stays_retired(self) -> None:
        self._enable_crm()

        api_response = self.client.get(f"/api/cases/?business_id={self.business.id}")
        self.assertEqual(api_response.status_code, 404)
        self.assertEqual(api_response.json()["error"], "FEATURE_DISABLED")

        ui_response = self.client.get("/dashboard/cases/")
        self.assertEqual(ui_response.status_code, 404)

    def test_import_job_uses_inferred_mapping_and_creates_contact_and_company(self) -> None:
        self._enable_crm()
        upload = SimpleUploadedFile(
            "contacts.csv",
            b"Name,Email,Company,Website\nJane Doe,jane@example.com,Acme Inc,https://acme.test\n",
            content_type="text/csv",
        )

        upload_response = self.client.post(
            "/api/crm/imports/sources/",
            {"businessId": str(self.business.id), "source_file": upload},
        )
        self.assertEqual(upload_response.status_code, 201)
        source_payload = upload_response.json()["sourceFile"]
        self.assertIn("suggestedMapping", source_payload)
        self.assertIn("contact", source_payload["suggestedMapping"])

        job_response = self.client.post(
            "/api/crm/imports/jobs/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "sourceFileId": source_payload["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(job_response.status_code, 201)
        process_next_job()

        contact = CrmContact.objects.get(business_profile=self.business, primary_email="jane@example.com")
        company = CrmCompany.objects.get(business_profile=self.business, name="Acme Inc")
        self.assertEqual(contact.display_name, "Jane Doe")
        self.assertEqual(company.website, "https://acme.test")
        self.assertTrue(
            CrmContactCompanyLink.objects.filter(
                business_profile=self.business,
                contact=contact,
                company=company,
            ).exists()
        )

    def test_import_duplicate_creates_reviewable_suggestion(self) -> None:
        self._enable_crm()
        CrmContact.objects.create(
            business_profile=self.business,
            display_name="Existing Jane",
            primary_email="jane@example.com",
        )
        upload = SimpleUploadedFile(
            "dupe.csv",
            b"Name,Email\nJane Doe,jane@example.com\n",
            content_type="text/csv",
        )
        upload_response = self.client.post(
            "/api/crm/imports/sources/",
            {"businessId": str(self.business.id), "source_file": upload},
        )
        source_payload = upload_response.json()["sourceFile"]

        self.client.post(
            "/api/crm/imports/jobs/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "sourceFileId": source_payload["id"],
                }
            ),
            content_type="application/json",
        )
        process_next_job()

        suggestion = CrmDuplicateSuggestion.objects.get(business_profile=self.business)
        self.assertIsNone(suggestion.record_id)
        self.assertEqual(suggestion.source_row_number, 1)
        self.assertEqual(suggestion.incoming_snapshot["Email"], "jane@example.com")
        self.assertIn("exact_email", suggestion.match_reasons)

    def test_delete_contact_archives_by_default(self) -> None:
        self._enable_crm()
        contact = CrmContact.objects.create(
            business_profile=self.business,
            display_name="Archive Me",
            primary_email="archive@example.com",
        )

        response = self.client.delete(
            f"/api/crm/contacts/{contact.id}/",
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        contact.refresh_from_db()
        self.assertEqual(contact.status, "archived")
        self.assertIsNotNone(contact.archived_at)
        self.assertFalse(response.json()["deleted"])
        self.assertTrue(response.json()["archived"])

    def test_contact_list_rejects_invalid_limit(self) -> None:
        self._enable_crm()

        response = self.client.get(f"/api/crm/contacts/?business_id={self.business.id}&limit=abc")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.assertIn("limit", response.json()["details"])

    def test_contact_create_rejects_unknown_custom_field(self) -> None:
        self._enable_crm()

        response = self.client.post(
            "/api/crm/contacts/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "display_name": "Unknown Field Contact",
                    "custom_fields": {"unknown_key": "value"},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.assertIn("custom_fields", response.json()["details"])

    def test_boolean_custom_field_false_string_is_coerced_to_false(self) -> None:
        self._enable_crm()
        CrmFieldDefinition.objects.create(
            business_profile=self.business,
            target_object="contact",
            key="newsletter_opt_in",
            label="Newsletter Opt In",
            field_type=CrmFieldType.BOOLEAN,
        )

        response = self.client.post(
            "/api/crm/contacts/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "display_name": "Boolean Contact",
                    "custom_fields": {"newsletter_opt_in": "false"},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        contact = CrmContact.objects.get(business_profile=self.business, display_name="Boolean Contact")
        field_value = CrmFieldValue.objects.get(contact=contact, field_definition__key="newsletter_opt_in")
        self.assertEqual(field_value.value_json, False)
        self.assertEqual(field_value.boolean_value, False)

    def test_contact_detail_returns_rich_crm_payload(self) -> None:
        self._enable_crm()
        company = CrmCompany.objects.create(
            business_profile=self.business,
            name="Acme Inc",
            website="https://acme.test",
        )
        contact = CrmContact.objects.create(
            business_profile=self.business,
            owner=self.user,
            display_name="Jane Doe",
            first_name="Jane",
            last_name="Doe",
            primary_email="jane@example.com",
            title="CEO",
            source="manual",
            tags=["vip"],
        )
        CrmContactCompanyLink.objects.create(
            business_profile=self.business,
            contact=contact,
            company=company,
            is_primary=True,
            relationship_title="Primary",
        )
        CrmFieldDefinition.objects.create(
            business_profile=self.business,
            target_object="contact",
            key="segment",
            label="Segment",
            field_type=CrmFieldType.TEXT,
        )
        add_note(business_profile=self.business, actor=self.user, body="Important note", contact=contact)

        response = self.client.get(f"/api/crm/contacts/{contact.id}/?business_id={self.business.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["contact"]
        self.assertEqual(payload["publicId"], str(contact.public_id))
        self.assertEqual(payload["owner"]["id"], str(self.user.id))
        self.assertEqual(payload["companyLinks"][0]["companyId"], str(company.id))
        self.assertIsInstance(payload["customFields"], list)
        self.assertIsInstance(payload["notes"], list)
        self.assertIsInstance(payload["activities"], list)
        self.assertIn("createdAt", payload)
        self.assertIn("updatedAt", payload)
        self.assertIn("archivedAt", payload)

    def test_contact_notes_endpoint_creates_and_lists_notes(self) -> None:
        self._enable_crm()
        contact = CrmContact.objects.create(
            business_profile=self.business,
            display_name="Jane Doe",
        )

        create_response = self.client.post(
            f"/api/crm/contacts/{contact.id}/notes/",
            data=json.dumps({"businessId": str(self.business.id), "body": "First note"}),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["note"]["body"], "First note")

        list_response = self.client.get(f"/api/crm/contacts/{contact.id}/notes/?business_id={self.business.id}")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()["items"]), 1)
        self.assertEqual(list_response.json()["items"][0]["body"], "First note")

    def test_duplicate_suggestion_can_be_ignored(self) -> None:
        self._enable_crm()
        candidate = CrmContact.objects.create(
            business_profile=self.business,
            display_name="Existing Jane",
            primary_email="jane@example.com",
        )
        suggestion = CrmDuplicateSuggestion.objects.create(
            business_profile=self.business,
            record_type="contact",
            candidate_record_id=candidate.id,
            incoming_snapshot={"Email": "jane@example.com"},
            match_reasons=["exact_email"],
        )

        response = self.client.patch(
            f"/api/crm/duplicates/{suggestion.id}/",
            data=json.dumps({"businessId": str(self.business.id), "action": "ignore", "resolutionNote": "Reviewed manually"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, "ignored")
        self.assertEqual(suggestion.resolution_note, "Reviewed manually")

    def test_non_owner_is_forbidden_from_accessing_business_crm(self) -> None:
        self._enable_crm()
        FeatureFlagService.set_flags(self.other_business, updates={"crm_v1": True})
        self.client.force_login(self.other_user)

        response = self.client.get(f"/api/crm/contacts/?business_id={self.business.id}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "PERMISSION_DENIED")

    def test_contact_company_link_endpoints_manage_relationship(self) -> None:
        self._enable_crm()
        contact = CrmContact.objects.create(
            business_profile=self.business,
            display_name="Jane Doe",
        )
        company = CrmCompany.objects.create(
            business_profile=self.business,
            name="Acme Inc",
        )

        create_response = self.client.post(
            f"/api/crm/contacts/{contact.id}/company-links/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "companyId": str(company.id),
                    "relationship_title": "CEO",
                    "is_primary": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["companyLink"]["relationshipTitle"], "CEO")

        patch_response = self.client.patch(
            f"/api/crm/contacts/{contact.id}/company-links/{company.id}/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "relationship_title": "Founder",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["companyLink"]["relationshipTitle"], "Founder")

        delete_response = self.client.delete(
            f"/api/crm/contacts/{contact.id}/company-links/{company.id}/",
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(CrmContactCompanyLink.objects.filter(contact=contact, company=company).exists())

    def test_field_definition_detail_archives_and_updates(self) -> None:
        self._enable_crm()
        definition = CrmFieldDefinition.objects.create(
            business_profile=self.business,
            target_object="contact",
            key="segment",
            label="Segment",
            field_type=CrmFieldType.TEXT,
        )

        patch_response = self.client.patch(
            f"/api/crm/field-definitions/{definition.id}/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "label": "Customer Segment",
                    "searchable": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["fieldDefinition"]["label"], "Customer Segment")

        delete_response = self.client.delete(
            f"/api/crm/field-definitions/{definition.id}/",
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(delete_response.status_code, 200)
        definition.refresh_from_db()
        self.assertTrue(definition.archived)

    def test_hard_delete_contact_creates_audit_activity(self) -> None:
        self._enable_crm()
        contact = CrmContact.objects.create(
            business_profile=self.business,
            display_name="Delete Me",
        )

        response = self.client.patch(
            f"/api/crm/contacts/{contact.id}/",
            data=json.dumps({"businessId": str(self.business.id), "action": "hard_delete"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CrmContact.objects.filter(id=contact.id).exists())
        self.assertTrue(
            CrmActivity.objects.filter(
                business_profile=self.business,
                summary="Contact hard deleted",
            ).exists()
        )

    def test_import_queue_and_finish_create_audit_activities(self) -> None:
        self._enable_crm()
        upload = SimpleUploadedFile(
            "audit.csv",
            b"Name,Email\nJane Doe,jane@example.com\n",
            content_type="text/csv",
        )
        upload_response = self.client.post(
            "/api/crm/imports/sources/",
            {"businessId": str(self.business.id), "source_file": upload},
        )
        source_payload = upload_response.json()["sourceFile"]

        create_job_response = self.client.post(
            "/api/crm/imports/jobs/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "sourceFileId": source_payload["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_job_response.status_code, 201)
        process_next_job()

        summaries = list(
            CrmActivity.objects.filter(business_profile=self.business)
            .order_by("created_at")
            .values_list("summary", flat=True)
        )
        self.assertIn("Import job queued", summaries)
        self.assertIn("Import job started", summaries)
        self.assertIn("Import job finished", summaries)

    def test_invalid_select_field_definition_is_rejected(self) -> None:
        self._enable_crm()

        response = self.client.post(
            "/api/crm/field-definitions/",
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "target_object": "contact",
                    "key": "lifecycle_stage",
                    "label": "Lifecycle Stage",
                    "field_type": "select",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.assertIn("options", response.json()["details"])

    def test_import_job_failure_does_not_stick_in_running(self) -> None:
        self._enable_crm()
        upload = SimpleUploadedFile(
            "contacts.csv",
            b"Name,Email\nJane Doe,jane@example.com\n",
            content_type="text/csv",
        )
        upload_response = self.client.post(
            "/api/crm/imports/sources/",
            {"businessId": str(self.business.id), "source_file": upload},
        )
        source_payload = upload_response.json()["sourceFile"]
        job = CrmImportJob.objects.create(
            business_profile=self.business,
            source_file_id=source_payload["id"],
            initiated_by=self.user,
            max_attempts=1,
        )

        with patch("apps.crm.import_pipeline.jobs._load_rows", side_effect=RuntimeError("boom")):
            process_next_job()

        job.refresh_from_db()
        self.assertEqual(job.status, CrmImportJobStatus.FAILED)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.error_detail, "boom")
        self.assertIsNone(job.lease_expires_at)

    def test_retired_legacy_crm_tools_are_not_advertised_or_executable(self) -> None:
        tool_names = {
            tool_def.get("function", {}).get("name")
            for tool_def in get_tool_definitions()
            if isinstance(tool_def, dict)
        }
        self.assertNotIn("create_customer", tool_names)
        self.assertNotIn("create_case", tool_names)

        result = execute_tool("create_customer", {}, conversation=None)
        self.assertEqual(result["error_code"], "unsupported_tool")
