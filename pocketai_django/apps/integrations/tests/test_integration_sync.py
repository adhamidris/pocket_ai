from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import (
    BusinessProfile,
    IntegrationResourceConfig,
    KnowledgeIntegrationStatus,
    KnowledgeIntegrationType,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
)
from apps.integrations.models import KnowledgeIntegration
from apps.integrations.sync.service import ExportedSheet, IntegrationSyncError, IntegrationSyncService


User = get_user_model()


class IntegrationSyncServiceTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.owner = User.objects.create_user(email="ops@example.com", password="changeme123", first_name="Ops")
        self.registration = RegistrationSession.objects.create(user=self.owner)
        self.business = BusinessProfile.objects.create(
            user=self.owner,
            registration_session=self.registration,
            name="Acme",
            industry="SaaS",
        )
        self.integration = KnowledgeIntegration.objects.create(
            business_profile=self.business,
            created_by=self.owner,
            name="Google Drive",
            integration_type=KnowledgeIntegrationType.GOOGLE_DRIVE,
            status=KnowledgeIntegrationStatus.CONNECTED,
        )

    def _service(self) -> IntegrationSyncService:
        return IntegrationSyncService(storage_root=Path(self.temp_dir))

    def test_sync_integrations_skips_when_schedule_not_due(self):
        schedule = self.integration.get_sync_schedule()
        schedule["next_run_at"] = (timezone.now() + timedelta(hours=2)).isoformat()
        self.integration.set_sync_schedule(schedule)
        self.integration.save(update_fields=["metadata"])
        service = self._service()

        with mock.patch.object(service, "_run_sync") as mock_run:
            results = service.sync_integrations([self.integration])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].message, "scheduled_later")
        mock_run.assert_not_called()

    def test_save_and_queue_skips_duplicate_checksums(self):
        resource: IntegrationResourceConfig = {
            "resource_id": "drive:sheet",
            "drive_file_id": "drive",
            "drive_file_name": "Playbook",
            "sheet_gid": "0",
            "sheet_name": "Main",
            "visibility": "private",
            "sync_frequency": "daily",
        }
        self.integration.set_resource_configs([resource])
        self.integration.save(update_fields=["settings"])
        exported = ExportedSheet(
            content=b"header\n1,2,3",
            filename="demo",
            content_type="text/csv",
            extension="csv",
        )
        service = self._service()

        with mock.patch("apps.integrations.sync.service.queue_ingestion_job") as mock_queue:
            upload, bytes_written, job_id, changed, _ = service._save_and_queue(
                self.integration,
                resource,
                exported,
                synced_at=timezone.now(),
            )

        self.assertTrue(changed)
        self.assertEqual(bytes_written, len(exported.content))
        self.assertIsNotNone(job_id)
        self.assertEqual(mock_queue.call_count, 1)

        with mock.patch("apps.integrations.sync.service.queue_ingestion_job") as mock_queue:
            _, bytes_written_second, job_id_second, changed_second, _ = service._save_and_queue(
                self.integration,
                resource,
                exported,
                synced_at=timezone.now(),
            )

        self.assertFalse(changed_second)
        self.assertEqual(bytes_written_second, 0)
        self.assertIsNone(job_id_second)
        mock_queue.assert_not_called()

    def test_resource_failure_marks_upload_stale(self):
        resource: IntegrationResourceConfig = {
            "resource_id": "drive:sheet",
            "drive_file_id": "drive",
            "drive_file_name": "Playbook",
            "sheet_gid": "0",
            "sheet_name": "Main",
            "visibility": "private",
            "sync_frequency": "daily",
        }
        self.integration.set_resource_configs([resource])
        self.integration.save(update_fields=["settings"])
        upload = self.integration.uploads.create(
            business_profile=self.business,
            user=self.owner,
            display_name="Playbook",
            source_name="Playbook",
            source_type=KnowledgeSourceType.INTEGRATION,
            source_uid="drive:sheet",
            visibility="private",
            status=KnowledgeStatus.READY,
        )
        upload.refresh_from_db()

        class FailingAdapter:
            def __init__(self, integration):
                self.integration = integration

            def export_resource(self, _resource):
                raise IntegrationSyncError("sheet not found")

            def supports_batch_export(self):
                return False

        service = self._service()
        adapter = FailingAdapter(self.integration)
        with mock.patch.object(service, "_build_adapter", return_value=adapter):
            result = service.sync_integration(self.integration)

        self.assertEqual(result.resources[0].status, "failed")
        updated_settings = self.integration.resource_configs
        self.assertIsNotNone(updated_settings[0].get("stale_since"))
        upload.refresh_from_db()
        sync_meta = (upload.metadata or {}).get("integration_sync") or {}
        self.assertTrue(sync_meta.get("stale"))
        self.assertEqual(upload.status, KnowledgeStatus.FAILED)

    def test_save_and_queue_respects_masking_policy(self):
        self.business.metadata = {
            "table_privacy": {
                "masking_required": True,
                "required_masking_columns": ["ssn"],
            }
        }
        self.business.save(update_fields=["metadata"])
        resource: IntegrationResourceConfig = {
            "resource_id": "drive:sheet",
            "drive_file_id": "drive",
            "drive_file_name": "Playbook",
            "sheet_gid": "0",
            "sheet_name": "Main",
            "visibility": "private",
            "sync_frequency": "daily",
            "column_privacy": {
                "shared_columns": ["Name"],
                "internal_only_columns": [],
                "excluded_columns": [],
            },
        }
        self.integration.set_resource_configs([resource])
        self.integration.save(update_fields=["settings"])
        exported = ExportedSheet(
            content=b"header",
            filename="sheet",
            content_type="text/csv",
            extension="csv",
        )
        service = self._service()

        with self.assertRaises(IntegrationSyncError):
            service._save_and_queue(
                self.integration,
                resource,
                exported,
                synced_at=timezone.now(),
            )

    def test_save_and_queue_sets_table_privacy_metadata(self):
        resource: IntegrationResourceConfig = {
            "resource_id": "drive:sheet",
            "drive_file_id": "drive",
            "drive_file_name": "Playbook",
            "sheet_gid": "0",
            "sheet_name": "Main",
            "visibility": "private",
            "sync_frequency": "daily",
            "column_privacy": {
                "shared_columns": ["Name"],
                "internal_only_columns": ["SSN"],
                "excluded_columns": ["Notes"],
            },
        }
        self.integration.set_resource_configs([resource])
        self.integration.save(update_fields=["settings"])
        exported = ExportedSheet(
            content=b"header",
            filename="sheet",
            content_type="text/csv",
            extension="csv",
        )
        service = self._service()
        upload, *_ = service._save_and_queue(
            self.integration,
            resource,
            exported,
            synced_at=timezone.now(),
        )
        upload.refresh_from_db()
        table_privacy = (upload.metadata or {}).get("table_privacy") or {}
        self.assertIn("SSN", table_privacy.get("sensitive_columns", []))
        self.assertIn("Notes", table_privacy.get("excluded_columns", []))

    def test_save_and_queue_populates_sheet_labels_and_row_counts(self):
        resource: IntegrationResourceConfig = {
            "resource_id": "drive:sheet",
            "drive_file_id": "drive-id",
            "drive_file_name": "Playbook",
            "sheet_gid": "0",
            "sheet_name": "Main",
            "visibility": "private",
            "sync_frequency": "daily",
        }
        self.integration.set_resource_configs([resource])
        self.integration.save(update_fields=["settings"])
        exported = ExportedSheet(
            content=b"col_a,col_b\n1,2\n",
            filename="sheet",
            content_type="text/csv",
            extension="csv",
        )
        service = self._service()

        upload, bytes_written, _, changed, row_count = service._save_and_queue(
            self.integration,
            resource,
            exported,
            synced_at=timezone.now(),
        )

        self.assertTrue(changed)
        self.assertEqual(bytes_written, len(exported.content))
        self.assertGreater(row_count, 0)
        upload.refresh_from_db()
        expected_label = "Playbook – Main"
        self.assertEqual(upload.display_name, expected_label)
        metadata = upload.metadata or {}
        self.assertEqual(metadata.get("public_label"), expected_label)
        integration_resource = metadata.get("integration_resource") or {}
        self.assertEqual(integration_resource.get("sheet_label"), expected_label)
        self.assertGreater(int(integration_resource.get("row_count") or 0), 0)
