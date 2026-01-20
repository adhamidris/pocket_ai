from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("conversations", "0004_conversation_tool_approval"),
    ]

    operations = [
        migrations.CreateModel(
            name="McpToolOutputArtifact",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("tool_call_id", models.CharField(max_length=128, blank=True, default="", db_index=True)),
                ("tool_event_id", models.CharField(max_length=128, blank=True, default="", db_index=True)),
                ("invoked_tool", models.CharField(max_length=200, blank=True, default="", db_index=True)),
                ("tool_id", models.CharField(max_length=240, blank=True, default="", db_index=True)),
                ("remote_connection_id", models.UUIDField(null=True, blank=True, db_index=True)),
                ("remote_connection_name", models.CharField(max_length=240, blank=True, default="")),
                ("remote_tool", models.CharField(max_length=240, blank=True, default="")),
                ("status", models.CharField(max_length=48, blank=True, default="")),
                ("is_error", models.BooleanField(default=False)),
                ("request", models.JSONField(default=dict, blank=True)),
                ("response", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_tool_output_artifacts",
                        to="conversations.conversation",
                    ),
                ),
            ],
            options={
                "db_table": "mcp_tool_output_artifact",
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["conversation", "created_at"],
                        name="mcp_artifact_conv_created_idx",
                    )
                ],
            },
        ),
    ]

