from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import BusinessProfile, McpConnection, RegistrationSession
from apps.mcp.models import McpConnectionTestJob, McpConnectionTestJobStatus


User = get_user_model()


class McpSetupFieldsApiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="setup@example.com", password="changeme123", first_name="Setup")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Setup Corp",
            industry="Retail",
        )
        self.client.force_login(self.user)

    def test_create_connection_persists_setup_fields_and_enqueues_test_job(self) -> None:
        url = reverse("api:mcp-connections")
        payload = {
            "businessId": str(self.business.id),
            "name": "Postgres MCP",
            "serverUrl": "https://93.184.216.34/mcp",
            "enabled": True,
            "sourceType": "marketplace",
            "marketplaceKey": "postgres",
            "auth": {"type": "none"},
            "metadata": {"setupFields": {"connection_string": "postgresql://user:pass@host:5432/db"}},
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        connection = McpConnection.objects.filter(business_profile=self.business, marketplace_key="postgres").first()
        self.assertIsNotNone(connection)
        assert connection is not None
        self.assertEqual(connection.credentials.get("setup_fields", {}).get("connection_string"), payload["metadata"]["setupFields"]["connection_string"])

        job = McpConnectionTestJob.objects.filter(connection=connection).order_by("-created_at").first()
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.status, McpConnectionTestJobStatus.QUEUED)

    def test_update_auth_preserves_setup_fields(self) -> None:
        connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Postgres MCP",
            server_url="https://93.184.216.34/mcp",
            source_type="marketplace",
            marketplace_key="postgres",
            auth_type="bearer",
            status="enabled",
        )
        connection.credentials = {
            "token": "old-token",
            "setup_fields": {"connection_string": "postgresql://user:pass@host:5432/db"},
        }
        connection.save()

        url = reverse("api:mcp-connection-detail", kwargs={"connection_id": connection.id})
        resp = self.client.put(
            url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "auth": {"type": "bearer", "token": "new-token"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        connection.refresh_from_db()
        creds = connection.credentials
        self.assertEqual(creds.get("token"), "new-token")
        self.assertEqual(creds.get("setup_fields", {}).get("connection_string"), "postgresql://user:pass@host:5432/db")

    def test_invalid_setup_field_is_rejected(self) -> None:
        url = reverse("api:mcp-connections")
        payload = {
            "businessId": str(self.business.id),
            "name": "Postgres MCP",
            "serverUrl": "https://93.184.216.34/mcp",
            "enabled": True,
            "sourceType": "marketplace",
            "marketplaceKey": "postgres",
            "auth": {"type": "none"},
            "metadata": {"setupFields": {"not_allowed": "x"}},
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body.get("error"), "VALIDATION_ERROR")
        self.assertEqual(body.get("field"), "setupFields")

