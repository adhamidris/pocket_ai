from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import (
    BusinessProfile,
    IntegrationCredentialEvent,
    IntegrationCredentialEventType,
    KnowledgeIntegration,
    KnowledgeIntegrationStatus,
    KnowledgeIntegrationType,
    RegistrationSession,
)


User = get_user_model()


class KnowledgeIntegrationCredentialTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(email="owner@example.com", password="pass12345", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.owner)
        self.business = BusinessProfile.objects.create(
            user=self.owner,
            registration_session=self.registration,
            name="Acme",
            industry="SaaS",
        )

    def _create_integration(self) -> KnowledgeIntegration:
        return KnowledgeIntegration.objects.create(
            business_profile=self.business,
            created_by=self.owner,
            name="Google Drive",
            integration_type=KnowledgeIntegrationType.GOOGLE_DRIVE,
            status=KnowledgeIntegrationStatus.CONNECTED,
        )

    def test_credentials_are_encrypted_and_round_trip(self):
        integration = self._create_integration()
        payload = {"access_token": "abc", "refresh_token": "xyz"}
        integration.credentials = payload
        integration.save()

        integration.refresh_from_db()
        self.assertNotEqual(integration.credentials_encrypted, "")
        stored = integration.credentials
        self.assertEqual(stored["access_token"], "abc")
        self.assertEqual(stored["refresh_token"], "xyz")
        self.assertIsNotNone(integration.credentials_last_rotated_at)

    @override_settings(INTEGRATION_CREDENTIAL_MAX_ERRORS=1)
    def test_register_credential_failure_marks_reauth_required(self):
        integration = self._create_integration()
        integration.register_credential_failure(reason="bad token", actor=self.owner)
        self.assertEqual(integration.credential_error_count, 1)
        self.assertEqual(integration.status, KnowledgeIntegrationStatus.DISCONNECTED)
        self.assertIn("bad token", integration.sync_error or "")
        event = IntegrationCredentialEvent.objects.filter(integration=integration).last()
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, IntegrationCredentialEventType.ROTATION_REQUIRED)

    def test_clear_credentials_logs_audit_event(self):
        integration = self._create_integration()
        integration.credentials = {"access_token": "temp"}
        integration.save()

        integration.clear_credentials(actor=self.owner, reason="user_request")
        integration.save()

        self.assertEqual(integration.credentials_encrypted, "")
        events = IntegrationCredentialEvent.objects.filter(integration=integration, event_type=IntegrationCredentialEventType.CLEARED)
        self.assertEqual(events.count(), 1)
        metadata = events.first().metadata
        self.assertEqual(metadata.get("reason"), "user_request")
