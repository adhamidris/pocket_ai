import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def encrypt_existing_credentials(apps, schema_editor):
    from apps.accounts.credential_secrets import get_secret_manager

    Integration = apps.get_model("accounts", "KnowledgeIntegration")

    manager = get_secret_manager()
    now = timezone.now()
    for integration in Integration.objects.all():
        credentials = integration.credentials or {}
        if not isinstance(credentials, dict) or not credentials:
            continue
        tenant = str(integration.business_profile_id or "")
        if not tenant:
            continue
        try:
            ciphertext = manager.encrypt(credentials, tenant=tenant)
        except Exception:
            continue
        integration.credentials_encrypted = ciphertext
        integration.credentials_key_version = manager.key_version
        integration.credentials_last_rotated_at = now
        integration.save(update_fields=[
            "credentials_encrypted",
            "credentials_key_version",
            "credentials_last_rotated_at",
        ])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0022_knowledgeintegration_settings_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="knowledgeintegration",
            name="credential_error_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="knowledgeintegration",
            name="credentials_encrypted",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="knowledgeintegration",
            name="credentials_key_version",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="knowledgeintegration",
            name="credentials_last_rotated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="IntegrationCredentialEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=32, choices=[
                    ("created", "Created"),
                    ("refreshed", "Refreshed"),
                    ("error", "Error"),
                    ("cleared", "Cleared"),
                    ("rotation_required", "Rotation Required"),
                ])),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("business_profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="integration_credential_events", to="accounts.businessprofile")),
                ("integration", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="credential_events", to="accounts.knowledgeintegration")),
                ("triggered_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="integration_credential_events", to="accounts.user")),
            ],
            options={
                "db_table": "accounts_integration_credential_event",
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(fields=["integration", "event_type"], name="integration_cred_evt_type_idx"),
                ],
            },
        ),
        migrations.RunPython(encrypt_existing_credentials, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="knowledgeintegration",
            name="credentials",
        ),
    ]
