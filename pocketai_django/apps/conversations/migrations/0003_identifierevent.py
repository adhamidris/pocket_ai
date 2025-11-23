from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_identifier_schema"),
        ("conversations", "0002_conversationfeedback"),
    ]

    operations = [
        migrations.CreateModel(
            name="IdentifierEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("upload_id", models.UUIDField(blank=True, null=True)),
                ("tool", models.CharField(blank=True, default="", max_length=64)),
                ("status", models.CharField(choices=[("ok", "OK"), ("identifier_required", "Identifier Required")], default="ok", max_length=32)),
                ("match_policy", models.CharField(default="or", max_length=8)),
                ("required_keys", models.JSONField(blank=True, default=list)),
                ("provided_keys", models.JSONField(blank=True, default=list)),
                ("provided_hashes", models.JSONField(blank=True, default=dict)),
                ("blocked_uploads", models.JSONField(blank=True, default=list)),
                ("missing_by_upload", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("business_profile", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="identifier_events", to="accounts.businessprofile")),
                ("conversation", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="identifier_events", to="conversations.conversation")),
            ],
            options={
                "db_table": "conversations_identifier_event",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="identifierevent",
            index=models.Index(fields=["business_profile", "status"], name="identifier_event_status_idx"),
        ),
        migrations.AddIndex(
            model_name="identifierevent",
            index=models.Index(fields=["business_profile", "created_at"], name="identifier_event_created_idx"),
        ),
    ]
