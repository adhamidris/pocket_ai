from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
)
from apps.knowledge.models import KnowledgeUpload


User = get_user_model()


class KnowledgeDocumentDeleteTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
        )
        self.client.force_login(self.user)

    def _create_upload(self) -> KnowledgeUpload:
        return KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            display_name="Playbook",
            source_type=KnowledgeSourceType.TEXT,
            status=KnowledgeStatus.READY,
        )

    def test_delete_document_removes_upload(self) -> None:
        upload = self._create_upload()
        url = reverse("api:knowledge-documents-detail", args=[upload.id])
        response = self.client.delete(f"{url}?business_id={self.business.id}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(KnowledgeUpload.objects.filter(id=upload.id).exists())

    def test_delete_document_returns_not_found_for_missing_upload(self) -> None:
        missing_id = uuid.uuid4()
        url = reverse("api:knowledge-documents-detail", args=[missing_id])
        response = self.client.delete(f"{url}?business_id={self.business.id}")
        self.assertEqual(response.status_code, 404)
