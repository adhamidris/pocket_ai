from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0043_mcp_connection_approvals"),
        ("conversations", "0003_identifierevent"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationToolApproval",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tool_approvals",
                        to="conversations.conversation",
                    ),
                ),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tool_approvals",
                        blank=True,
                        null=True,
                        to="accounts.mcpconnection",
                    ),
                ),
                ("tool_name", models.CharField(max_length=128)),
                ("remote_tool_name", models.CharField(max_length=128, blank=True, default="")),
                ("tool_call_id", models.CharField(max_length=128, blank=True, default="")),
                ("event_id", models.CharField(max_length=128, blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        max_length=16,
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("denied", "Denied"),
                            ("expired", "Expired"),
                        ],
                        default="pending",
                    ),
                ),
                ("requested_at", models.DateTimeField(default=timezone.now, db_index=True)),
                ("resolved_at", models.DateTimeField(null=True, blank=True)),
                ("expires_at", models.DateTimeField(null=True, blank=True)),
                ("input_payload", models.JSONField(default=dict, blank=True)),
                ("metadata", models.JSONField(default=dict, blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "conversations_conversation_tool_approval",
                "ordering": ("-requested_at",),
                "indexes": [
                    models.Index(fields=["conversation", "status"], name="conv_tool_approval_status_idx"),
                    models.Index(fields=["connection", "status"], name="conv_tool_approval_conn_idx"),
                ],
            },
        ),
    ]
