from __future__ import annotations

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounts.models import BusinessProfile, RegistrationSession
from apps.knowledge.models import KnowledgeUpload


User = get_user_model()


class QueueKnowledgeReingestionCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="queue-reingest-owner@example.com",
            password="changeme123",
            first_name="Owner",
        )
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Queue Reingest Co",
            industry="Retail",
            status="active",
        )
        self.other_registration = RegistrationSession.objects.create(user=self.user)
        self.other_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.other_registration,
            name="Other Co",
            industry="Retail",
            status="active",
        )

        self.active_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            display_name="Active Upload",
            source_name="active.pdf",
            source_type="file",
            status="active",
        )
        self.archived_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            display_name="Archived Upload",
            source_name="archived.pdf",
            source_type="file",
            status="archived",
        )
        self.other_upload = KnowledgeUpload.objects.create(
            business_profile=self.other_business,
            user=self.user,
            display_name="Other Upload",
            source_name="other.pdf",
            source_type="file",
            status="active",
        )

    def test_requires_explicit_scope(self) -> None:
        with self.assertRaises(CommandError):
            call_command("queue_knowledge_reingestion")

    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.ensure_upload_preflight")
    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.queue_ingestion_job")
    def test_queues_matching_uploads_with_force_true(self, queue_mock, preflight_mock) -> None:
        preflight_mock.return_value = {"status": "ok", "warnings": []}
        queue_mock.side_effect = [
            mock.Mock(id="job-1", status="queued"),
            mock.Mock(id="job-2", status="queued"),
        ]

        output = StringIO()
        call_command(
            "queue_knowledge_reingestion",
            "--all",
            "--business-id",
            str(self.business.id),
            stdout=output,
        )

        self.assertEqual(queue_mock.call_count, 1)
        args, kwargs = queue_mock.call_args
        self.assertEqual(args[0].id, self.active_upload.id)
        self.assertEqual(kwargs["trigger"], "bulk_manual_reingest")
        self.assertTrue(kwargs["force"])
        self.assertIn("queued=1", output.getvalue())

    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.ensure_upload_preflight")
    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.queue_ingestion_job")
    def test_dry_run_does_not_queue_jobs(self, queue_mock, preflight_mock) -> None:
        preflight_mock.return_value = {"status": "ok", "warnings": []}
        output = StringIO()

        call_command(
            "queue_knowledge_reingestion",
            "--upload-id",
            str(self.active_upload.id),
            "--dry-run",
            stdout=output,
        )

        queue_mock.assert_not_called()
        self.assertIn("reingestable=yes", output.getvalue())

    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.ensure_upload_preflight")
    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.queue_ingestion_job")
    def test_include_archived_allows_archived_uploads(self, queue_mock, preflight_mock) -> None:
        preflight_mock.return_value = {"status": "ok", "warnings": []}
        queue_mock.side_effect = [
            mock.Mock(id="job-1", status="queued"),
            mock.Mock(id="job-2", status="queued"),
        ]

        call_command(
            "queue_knowledge_reingestion",
            "--all",
            "--business-id",
            str(self.business.id),
            "--include-archived",
        )

        queued_upload_ids = [call.args[0].id for call in queue_mock.call_args_list]
        self.assertEqual(set(queued_upload_ids), {self.active_upload.id, self.archived_upload.id})

    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.ensure_upload_preflight")
    @mock.patch("apps.accounts.management.commands.queue_knowledge_reingestion.queue_ingestion_job")
    def test_skips_uploads_that_need_reupload(self, queue_mock, preflight_mock) -> None:
        preflight_mock.side_effect = [
            {"status": "error", "warnings": ["File is missing from storage; ingestion cannot read it."]},
        ]
        output = StringIO()

        call_command(
            "queue_knowledge_reingestion",
            "--upload-id",
            str(self.active_upload.id),
            stdout=output,
        )

        queue_mock.assert_not_called()
        self.assertIn("needs_reupload", output.getvalue())
        self.assertIn("needs_reupload=1", output.getvalue())
