from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import (
    BusinessProfile,
    IntegrationSyncFrequency,
    KnowledgeIntegration,
    KnowledgeIntegrationStatus,
    KnowledgeIntegrationType,
    RegistrationSession,
)


User = get_user_model()


class GoogleIntegrationViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="viewer@example.com", password="changeme123", first_name="Viewer")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme",
            industry="Retail",
        )
        self.client.force_login(self.user)

    def _create_integration(self) -> KnowledgeIntegration:
        return KnowledgeIntegration.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Google Drive",
            integration_type=KnowledgeIntegrationType.GOOGLE_DRIVE,
            status=KnowledgeIntegrationStatus.CONNECTED,
        )

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="test",
        GOOGLE_OAUTH_CLIENT_SECRET="secret",
        GOOGLE_OAUTH_REDIRECT_URI="https://example.com/callback",
    )
    def test_start_google_drive_oauth_returns_authorization_payload(self):
        url = reverse("api:integrations-google-start")
        response = self.client.post(
            url,
            data=json.dumps({"businessId": str(self.business.id)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("integrationId", payload)
        self.assertIn("authorizationUrl", payload)
        integration = KnowledgeIntegration.objects.get(id=payload["integrationId"])
        self.assertEqual(integration.status, KnowledgeIntegrationStatus.SYNCING)
        self.assertIn("oauth_state", integration.metadata)

    @mock.patch("apps.api.views.discover_google_sheet_resources")
    def test_google_drive_resources_lists_available_tabs(self, mock_discover):
        mock_discover.return_value = [
            {
                "resource_id": "file1:0",
                "drive_file_id": "file1",
                "drive_file_name": "Playbooks",
                "sheet_gid": "0",
                "sheet_name": "Sheet1",
                "row_count": 10,
                "column_count": 4,
                "modified_time": "2024-01-01T00:00:00Z",
                "owner": "Ops",
                "owner_email": "ops@example.com",
                "web_view_link": "https://example.com",
            }
        ]
        integration = self._create_integration()
        integration.set_resource_configs(
            [
                {
                    "resource_id": "file1:0",
                    "drive_file_id": "file1",
                    "drive_file_name": "Playbooks",
                    "sheet_gid": "0",
                    "sheet_name": "Sheet1",
                    "sync_frequency": IntegrationSyncFrequency.DAILY,
                    "visibility": "private",
                    "column_privacy": {
                        "shared_columns": ["name"],
                        "internal_only_columns": [],
                        "excluded_columns": [],
                    },
                    "metadata": {},
                }
            ]
        )
        integration.save()

        url = reverse("api:integrations-google-resources")
        response = self.client.get(
            url,
            {
                "business_id": str(self.business.id),
                "integration_id": str(integration.id),
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["integration"]["id"], str(integration.id))
        self.assertEqual(len(payload["availableResources"]), 1)
        self.assertTrue(payload["availableResources"][0]["selected"])

    def test_google_drive_save_resources_updates_settings(self):
        integration = self._create_integration()
        url = reverse("api:integrations-google-resources-save")
        body = {
            "businessId": str(self.business.id),
            "integrationId": str(integration.id),
            "defaultVisibility": "private",
            "defaultSyncFrequency": IntegrationSyncFrequency.HOURLY,
            "resources": [
                {
                    "resourceId": "file2:1",
                    "driveFileId": "file2",
                    "driveFileName": "Changelog",
                    "sheetGid": "1",
                    "sheetName": "Weekly",
                    "visibility": "internal",
                    "syncFrequency": IntegrationSyncFrequency.WEEKLY,
                    "columnPrivacy": {
                        "sharedColumns": ["public"],
                        "internalOnlyColumns": ["notes"],
                        "excludedColumns": [],
                    },
                }
            ],
        }
        response = self.client.post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        integration.refresh_from_db()
        self.assertEqual(len(integration.resource_configs), 1)
        resource = integration.resource_configs[0]
        self.assertEqual(resource["resource_id"], "file2:1")
        self.assertEqual(resource["visibility"], "internal")

    def test_integrations_collection_lists_and_creates(self):
        integration = self._create_integration()

        list_url = reverse("api:integrations-collection")
        list_response = self.client.get(list_url, {"business_id": str(self.business.id)})
        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        self.assertIn("integrations", payload)
        self.assertTrue(any(entry["id"] == str(integration.id) for entry in payload["integrations"]))
        self.assertIn("providers", payload)

        create_response = self.client.post(
            list_url,
            data=json.dumps({"businessId": str(self.business.id), "name": "Custom", "type": KnowledgeIntegrationType.CUSTOM}),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201)
        created = create_response.json()["integration"]
        self.assertEqual(created["name"], "Custom")

    @mock.patch("apps.api.views.discover_google_sheet_resources")
    def test_integration_sheets_collection_proxies_google_resources(self, mock_discover):
        integration = self._create_integration()
        mock_discover.return_value = [
            {
                "resource_id": "sheet:0",
                "drive_file_id": "sheet",
                "drive_file_name": "Playbook",
                "sheet_gid": "0",
                "sheet_name": "Main",
                "row_count": 10,
                "column_count": 4,
                "modified_time": "2024-01-01T00:00:00Z",
                "owner": "Ops",
                "owner_email": "ops@example.com",
                "web_view_link": "https://example.com",
            }
        ]
        url = reverse("api:integrations-sheets", args=[integration.id])
        response = self.client.get(url, {"business_id": str(self.business.id)})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["integration"]["id"], str(integration.id))
        self.assertEqual(len(payload["availableResources"]), 1)

    def test_integration_sheets_collection_accepts_post(self):
        integration = self._create_integration()
        url = reverse("api:integrations-sheets", args=[integration.id])
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "businessId": str(self.business.id),
                    "defaultVisibility": "private",
                    "resources": [
                        {
                            "resourceId": "sheet:1",
                            "driveFileId": "sheet",
                            "sheetGid": "1",
                            "sheetName": "Ops",
                            "columnPrivacy": {"sharedColumns": [], "internalOnlyColumns": [], "excludedColumns": []},
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        integration.refresh_from_db()
        self.assertEqual(len(integration.resource_configs), 1)

    def test_google_drive_save_resources_enforces_masking_policy(self):
        self.business.metadata = {
            "table_privacy": {
                "masking_required": True,
                "required_masking_columns": ["ssn"],
            }
        }
        self.business.save(update_fields=["metadata"])
        integration = self._create_integration()
        url = reverse("api:integrations-google-resources-save")

        body = {
            "businessId": str(self.business.id),
            "integrationId": str(integration.id),
            "resources": [
                {
                    "resourceId": "sheet:1",
                    "driveFileId": "sheet",
                    "sheetGid": "1",
                    "sheetName": "Ops",
                    "columnPrivacy": {"sharedColumns": ["Name"], "internalOnlyColumns": [], "excludedColumns": []},
                }
            ],
        }
        response = self.client.post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("missing masking", response.json()["message"])

        body["resources"][0]["columnPrivacy"]["internalOnlyColumns"] = ["SSN"]
        response = self.client.post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 200)
