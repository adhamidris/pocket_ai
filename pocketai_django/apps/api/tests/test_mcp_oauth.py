from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import (
    BusinessProfile,
    McpConnection,
    OAuthProvider,
    OAuthState,
    RegistrationSession,
)


User = get_user_model()


class McpMarketplaceOAuthTests(TestCase):
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

        self.provider = OAuthProvider.objects.create(
            key="google",
            name="Google",
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            client_id="client-id",
            scopes=["scope-a", "scope-b"],
            marketplace_keys=["gmail"],
        )
        self.provider.set_client_secret("client-secret")
        self.provider.save()

    def test_oauth_start_redirects_to_provider(self) -> None:
        url = reverse("api:oauth_start", kwargs={"provider_key": "google", "marketplace_key": "gmail"})
        resp = self.client.get(
            url,
            data={"business_id": str(self.business.id), "redirect": "https://evil.example.com/"},
        )
        self.assertEqual(resp.status_code, 302)
        location = resp["Location"]
        parsed = urlparse(location)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "accounts.google.com")
        query = parse_qs(parsed.query)
        self.assertEqual(query.get("client_id"), ["client-id"])
        self.assertEqual(query.get("response_type"), ["code"])
        self.assertEqual(query.get("scope"), ["scope-a scope-b"])
        self.assertIn("state", query)

        state_row = OAuthState.objects.filter(provider=self.provider, marketplace_key="gmail").first()
        self.assertIsNotNone(state_row)
        assert state_row is not None
        self.assertTrue(state_row.state_token)
        self.assertEqual(state_row.business_profile_id, self.business.id)
        self.assertEqual(state_row.user_id, self.user.id)
        # Disallowed redirect should be replaced by safe default.
        self.assertEqual(state_row.redirect_after, "/dashboard/mcp/")

    @mock.patch("apps.accounts.oauth_helpers.requests.post")
    def test_oauth_callback_creates_connection(self, mock_post) -> None:
        mock_response = mock.Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_post.return_value = mock_response

        start_url = reverse("api:oauth_start", kwargs={"provider_key": "google", "marketplace_key": "gmail"})
        self.client.get(start_url, data={"business_id": str(self.business.id)})
        state_row = OAuthState.objects.order_by("-created_at").first()
        self.assertIsNotNone(state_row)
        assert state_row is not None

        callback_url = reverse("api:oauth_callback", kwargs={"provider_key": "google"})
        resp = self.client.get(callback_url, data={"code": "auth-code", "state": state_row.state_token})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp["Content-Type"])

        state_row.refresh_from_db()
        self.assertTrue(state_row.is_used)

        connection = McpConnection.objects.filter(business_profile=self.business, marketplace_key="gmail").first()
        self.assertIsNotNone(connection)
        assert connection is not None
        creds = connection.credentials
        self.assertEqual(creds.get("token"), "access-token")
        self.assertEqual(creds.get("refresh_token"), "refresh-token")
        self.assertTrue(creds.get("expires_at"))
        self.assertEqual(connection.metadata.get("oauth_provider"), "google")

    @mock.patch("apps.accounts.oauth_helpers.requests.post")
    def test_oauth_refresh_updates_credentials(self, mock_post) -> None:
        mock_response = mock.Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_post.return_value = mock_response

        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Gmail",
            server_url="https://mcp.composio.dev/gmail",
            source_type="marketplace",
            marketplace_key="gmail",
            auth_type="bearer",
            status="enabled",
            metadata={"oauth_provider": "google"},
        )
        connection.credentials = {
            "token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "expires_at": (timezone.now() - timedelta(minutes=5)).isoformat(),
        }
        connection.save()

        refresh_url = reverse("api:oauth_refresh", kwargs={"connection_id": connection.id})
        resp = self.client.post(
            refresh_url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        connection.refresh_from_db()
        creds = connection.credentials
        self.assertEqual(creds.get("token"), "new-access-token")
        self.assertEqual(creds.get("refresh_token"), "new-refresh-token")
        self.assertTrue(creds.get("expires_at"))

