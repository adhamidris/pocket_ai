from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import (
    BusinessProfile,
    EmailAccountAuditAction,
    EmailAccountProvider,
    EmailAccountStatus,
    IntegrationAccountAuditAction,
    IntegrationAccountStatus,
    IntegrationProvider,
    IntegrationType,
    RegistrationSession,
)
from apps.integrations.models import (
    EmailAccount,
    EmailAccountAuditEvent,
    IntegrationAccount,
    IntegrationAccountAuditEvent,
)


User = get_user_model()


class NativeOauthDisconnectApiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme",
            industry="Retail",
        )
        self.client.force_login(self.user)

    def test_email_disconnect_marks_account_disconnected_and_clears_tokens(self) -> None:
        account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            email_address="owner@example.com",
            status=EmailAccountStatus.CONNECTED,
        )
        account.credentials = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
        }
        account.save()

        url = reverse("api:email_oauth_disconnect", kwargs={"provider_key": "google"})
        response = self.client.post(
            url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        account.refresh_from_db()
        self.assertEqual(account.status, EmailAccountStatus.DISCONNECTED)
        self.assertFalse(account.has_credentials())

        audit = EmailAccountAuditEvent.objects.filter(
            email_account=account,
            action=EmailAccountAuditAction.DISCONNECTED,
        ).first()
        self.assertIsNotNone(audit)

    def test_integration_disconnect_marks_account_disconnected_and_clears_tokens(self) -> None:
        account = IntegrationAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            integration_type=IntegrationType.GOOGLE_CALENDAR,
            provider=IntegrationProvider.GOOGLE,
            account_identifier="owner@example.com",
            status=IntegrationAccountStatus.CONNECTED,
        )
        account.credentials = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
        }
        account.save()

        url = reverse("api:integration_oauth_disconnect", kwargs={"integration_type": "google_calendar"})
        response = self.client.post(
            url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        account.refresh_from_db()
        self.assertEqual(account.status, IntegrationAccountStatus.DISCONNECTED)
        self.assertFalse(account.has_credentials())

        audit = IntegrationAccountAuditEvent.objects.filter(
            integration_account=account,
            action=IntegrationAccountAuditAction.DISCONNECTED,
        ).first()
        self.assertIsNotNone(audit)

    def test_disconnect_requires_business_membership(self) -> None:
        outsider = User.objects.create_user(email="outsider@example.com", password="changeme123")
        self.client.force_login(outsider)

        url = reverse("api:integration_oauth_disconnect", kwargs={"integration_type": "google_calendar"})
        response = self.client.post(
            url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("error"), "FORBIDDEN")

    def test_integration_tools_endpoint_lists_and_updates_enabled_flags(self) -> None:
        account = IntegrationAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            integration_type=IntegrationType.GOOGLE_CALENDAR,
            provider=IntegrationProvider.GOOGLE,
            account_identifier="owner@example.com",
            status=IntegrationAccountStatus.CONNECTED,
            metadata={},
        )

        url = reverse("api:integration_oauth_tools", kwargs={"integration_type": "google_calendar"})
        get_response = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(get_response.status_code, 200)
        get_payload = get_response.json()
        tools = get_payload.get("tools") or []
        self.assertTrue(any(tool.get("toolName") == "calendar_create_event" for tool in tools))
        self.assertTrue(all(bool(tool.get("enabled")) for tool in tools))

        post_response = self.client.post(
            url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "updates": [
                        {"toolName": "calendar_create_event", "enabled": False},
                        {"toolName": "calendar_list_events", "enabled": True},
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(post_response.status_code, 200)
        post_payload = post_response.json()
        post_tools = {tool.get("toolName"): tool for tool in (post_payload.get("tools") or [])}
        self.assertIn("calendar_create_event", post_tools)
        self.assertFalse(bool(post_tools["calendar_create_event"].get("enabled")))
        self.assertIn("calendar_list_events", post_tools)
        self.assertTrue(bool(post_tools["calendar_list_events"].get("enabled")))

        account.refresh_from_db()
        tool_settings = (account.metadata or {}).get("tool_settings") or {}
        self.assertIn("calendar_create_event", tool_settings)
        self.assertFalse(bool((tool_settings.get("calendar_create_event") or {}).get("enabled", True)))

    def test_integration_tools_endpoint_requires_connected_account(self) -> None:
        url = reverse("api:integration_oauth_tools", kwargs={"integration_type": "google_calendar"})
        response = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json().get("error"), "INTEGRATION_ACCOUNT_NOT_FOUND")

    def test_email_tools_endpoint_lists_and_updates_enabled_flags(self) -> None:
        account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            email_address="owner@example.com",
            status=EmailAccountStatus.CONNECTED,
            metadata={},
        )

        url = reverse("api:email_oauth_tools", kwargs={"provider_key": "google"})
        get_response = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(get_response.status_code, 200)
        get_payload = get_response.json()
        tools = get_payload.get("tools") or []
        self.assertTrue(any(tool.get("toolName") == "email_send_draft" for tool in tools))
        self.assertTrue(all(bool(tool.get("enabled")) for tool in tools))

        post_response = self.client.post(
            url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "updates": [
                        {"toolName": "email_send_draft", "enabled": False},
                        {"toolName": "email_search", "enabled": True},
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(post_response.status_code, 200)
        post_payload = post_response.json()
        post_tools = {tool.get("toolName"): tool for tool in (post_payload.get("tools") or [])}
        self.assertIn("email_send_draft", post_tools)
        self.assertFalse(bool(post_tools["email_send_draft"].get("enabled")))
        self.assertIn("email_search", post_tools)
        self.assertTrue(bool(post_tools["email_search"].get("enabled")))

        account.refresh_from_db()
        tool_settings = (account.metadata or {}).get("tool_settings") or {}
        self.assertIn("email_send_draft", tool_settings)
        self.assertFalse(bool((tool_settings.get("email_send_draft") or {}).get("enabled", True)))

    def test_email_tools_endpoint_requires_connected_account(self) -> None:
        url = reverse("api:email_oauth_tools", kwargs={"provider_key": "google"})
        response = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json().get("error"), "EMAIL_ACCOUNT_NOT_FOUND")
