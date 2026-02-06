from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import BusinessProfile, RegistrationSession
from apps.voice.models import VoiceProviderConnection
from core.tenancy import tenant_context


User = get_user_model()


class VoiceProviderConnectionsApiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="changeme123",
            first_name="Owner",
        )
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme",
            industry="Retail",
        )
        self.client.force_login(self.user)

    def test_list_voice_providers_defaults(self) -> None:
        url = reverse("api:voice-providers")
        response = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        providers = payload.get("providers") or []
        self.assertEqual(len(providers), 3)
        keys = {item.get("provider") for item in providers}
        self.assertEqual(keys, {"twilio", "deepgram", "elevenlabs"})
        indexed = {item.get("provider"): item for item in providers}

        twilio = indexed["twilio"]
        self.assertEqual(twilio.get("status"), "not_configured")
        self.assertFalse(twilio.get("hasCredentials"))
        self.assertTrue(twilio.get("editable"))
        self.assertEqual(twilio.get("managementMode"), "tenant")

        deepgram = indexed["deepgram"]
        self.assertFalse(deepgram.get("editable"))
        self.assertEqual(deepgram.get("managementMode"), "platform")

        elevenlabs = indexed["elevenlabs"]
        self.assertFalse(elevenlabs.get("editable"))
        self.assertEqual(elevenlabs.get("managementMode"), "platform")

    def test_put_twilio_provider_persists_encrypted_credentials(self) -> None:
        url = reverse("api:voice-provider-detail", args=["twilio"])
        response = self.client.put(
            url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "enabled": True,
                    "credentials": {
                        "account_sid": "AC123",
                        "auth_token": "secret",
                        "webhook_base_url": "https://voice.example.com",
                        "from_number": "+15551234567",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        provider_payload = response.json()["provider"]
        self.assertEqual(provider_payload["provider"], "twilio")
        self.assertTrue(provider_payload["enabled"])
        self.assertTrue(provider_payload["hasCredentials"])

        with tenant_context(self.business.id):
            connection = VoiceProviderConnection.objects.get(
                business_profile=self.business,
                provider=VoiceProviderConnection.Provider.TWILIO,
            )
            self.assertNotEqual(connection.credentials_encrypted, "")
            self.assertEqual(connection.credentials.get("account_sid"), "AC123")
            self.assertEqual(connection.credentials.get("webhook_base_url"), "https://voice.example.com")

    def test_put_platform_managed_provider_is_rejected(self) -> None:
        url = reverse("api:voice-provider-detail", args=["elevenlabs"])
        response = self.client.put(
            url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "enabled": True,
                    "credentials": {
                        "api_key": "eleven-secret",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload.get("error"), "VALIDATION_ERROR")
        self.assertIn("platform-managed", payload.get("message", ""))

    def test_test_platform_managed_provider_is_rejected(self) -> None:
        test_url = reverse("api:voice-provider-test", args=["deepgram"])
        response = self.client.post(
            test_url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload.get("error"), "VALIDATION_ERROR")
        self.assertIn("platform-managed", payload.get("message", ""))

    @mock.patch("apps.api.voice_providers.requests.get")
    def test_provider_test_updates_last_tested_at(self, mock_get) -> None:
        mock_get.return_value.status_code = 200
        create_url = reverse("api:voice-provider-detail", args=["twilio"])
        create_response = self.client.put(
            create_url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "enabled": True,
                    "credentials": {
                        "account_sid": "AC123",
                        "auth_token": "secret",
                        "webhook_base_url": "https://voice.example.com",
                        "from_number": "+15551234567",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 200)

        test_url = reverse("api:voice-provider-test", args=["twilio"])
        test_response = self.client.post(
            test_url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(test_response.status_code, 200)
        self.assertTrue(test_response.json().get("ok"))

        with tenant_context(self.business.id):
            connection = VoiceProviderConnection.objects.get(
                business_profile=self.business,
                provider=VoiceProviderConnection.Provider.TWILIO,
            )
            self.assertIsNotNone(connection.last_tested_at)
            self.assertEqual(connection.last_error, "")

    def test_delete_provider_clears_credentials(self) -> None:
        create_url = reverse("api:voice-provider-detail", args=["twilio"])
        create_response = self.client.put(
            create_url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "enabled": True,
                    "credentials": {
                        "account_sid": "AC123",
                        "auth_token": "secret",
                        "webhook_base_url": "https://voice.example.com",
                        "from_number": "+15551234567",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 200)

        delete_response = self.client.delete(
            create_url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(delete_response.status_code, 204)

        with tenant_context(self.business.id):
            connection = VoiceProviderConnection.objects.get(
                business_profile=self.business,
                provider=VoiceProviderConnection.Provider.TWILIO,
            )
            self.assertFalse(connection.enabled)
            self.assertEqual(connection.credentials_encrypted, "")
