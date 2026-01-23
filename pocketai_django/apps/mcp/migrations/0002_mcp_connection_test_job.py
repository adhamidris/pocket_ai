from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0047_oauthprovider_oauthstate"),
        ("mcp", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="McpConnectionTestJob",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("status", models.CharField(max_length=24, choices=[("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")], default="queued")),
                ("trigger", models.CharField(max_length=48, blank=True, default="")),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=5)),
                ("run_after", models.DateTimeField(null=True, blank=True)),
                ("lease_expires_at", models.DateTimeField(null=True, blank=True)),
                ("started_at", models.DateTimeField(null=True, blank=True)),
                ("finished_at", models.DateTimeField(null=True, blank=True)),
                ("error_detail", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_connection_test_jobs",
                        to="accounts.businessprofile",
                    ),
                ),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="test_jobs",
                        to="accounts.mcpconnection",
                    ),
                ),
            ],
            options={
                "db_table": "mcp_connection_test_job",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="mcpconnectiontestjob",
            index=models.Index(fields=["status", "run_after"], name="mcp_test_job_run_after_idx"),
        ),
        migrations.AddIndex(
            model_name="mcpconnectiontestjob",
            index=models.Index(fields=["status", "lease_expires_at"], name="mcp_test_job_lease_idx"),
        ),
        migrations.AddIndex(
            model_name="mcpconnectiontestjob",
            index=models.Index(fields=["connection", "status"], name="mcp_test_job_conn_status_idx"),
        ),
        migrations.AddIndex(
            model_name="mcpconnectiontestjob",
            index=models.Index(fields=["business_profile", "status"], name="mcp_testjob_biz_status_idx"),
        ),
    ]
