from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeUpload,
    KnowledgeUploadFile,
    RegistrationSession,
)


User = get_user_model()


class DashboardKnowledgeUploadTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="dashboard-owner@example.com", password="changeme123")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
        )
        self.client.force_login(self.user)
        self.url = reverse("frontend:dashboard-knowledge-upload")

    def _post_json(self, data: dict) -> tuple[int, dict]:
        response = self.client.post(
            self.url,
            data=data,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        return response.status_code, response.json()

    @patch("frontend.views.queue_ingestion_job")
    def test_bulk_file_upload_creates_one_document_per_file(self, queue_mock) -> None:
        first_file = SimpleUploadedFile("faq.txt", b"faq content", content_type="text/plain")
        second_file = SimpleUploadedFile("policy.txt", b"policy content", content_type="text/plain")

        status_code, payload = self._post_json(
            {
                "source_type": KnowledgeSourceType.FILE,
                "knowledge_file": [first_file, second_file],
            }
        )

        self.assertEqual(status_code, 201)
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("message"), "2 files added to your knowledge base.")
        self.assertEqual(len(payload.get("documents", [])), 2)
        self.assertEqual(payload.get("documents_total"), 2)
        self.assertEqual([item["name"] for item in payload["documents"]], ["faq.txt", "policy.txt"])

        self.assertEqual(KnowledgeUpload.objects.filter(business_profile=self.business).count(), 2)
        self.assertEqual(KnowledgeUploadFile.objects.filter(upload__business_profile=self.business).count(), 2)
        self.assertEqual(queue_mock.call_count, 2)

    @patch("frontend.views.queue_ingestion_job")
    def test_single_file_upload_keeps_single_document_shape(self, queue_mock) -> None:
        upload = SimpleUploadedFile("handbook.txt", b"handbook content", content_type="text/plain")

        status_code, payload = self._post_json(
            {
                "source_type": KnowledgeSourceType.FILE,
                "display_name": "Employee Handbook",
                "knowledge_file": upload,
            }
        )

        self.assertEqual(status_code, 201)
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("message"), '"Employee Handbook" added to your knowledge base.')
        self.assertEqual(len(payload.get("documents", [])), 1)
        self.assertEqual(payload["document"]["id"], payload["documents"][0]["id"])
        self.assertEqual(payload["document"]["name"], "Employee Handbook")
        self.assertEqual(queue_mock.call_count, 1)
