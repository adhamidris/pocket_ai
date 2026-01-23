from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class McpToolOutputArtifact(models.Model):
    """
    Stores full external MCP tool outputs out-of-band so the LLM prompt can stay lean.

    Tenant scoping is enforced via the Conversation foreign key (which is business-scoped).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        "conversations.Conversation",
        related_name="mcp_tool_output_artifacts",
        on_delete=models.CASCADE,
    )
    tool_call_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    tool_event_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    invoked_tool = models.CharField(max_length=200, blank=True, default="", db_index=True)

    # In gateway mode, this is the `tool_id` returned by mcp_search_tools (safe_name).
    tool_id = models.CharField(max_length=240, blank=True, default="", db_index=True)

    remote_connection_id = models.UUIDField(null=True, blank=True, db_index=True)
    remote_connection_name = models.CharField(max_length=240, blank=True, default="")
    remote_tool = models.CharField(max_length=240, blank=True, default="")

    status = models.CharField(max_length=48, blank=True, default="")
    is_error = models.BooleanField(default=False)

    request = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mcp_tool_output_artifact"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="mcp_artifact_conv_created_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        label = self.remote_tool or self.tool_id or self.invoked_tool or "mcp_tool"
        return f"{label}:{self.id}"


class McpConnectionTestJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class McpConnectionTestJob(models.Model):
    """
    Background job to test an MCP connection and refresh its tool cache.

    Designed for a simple management-command worker (production-friendly, no in-memory queues).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="mcp_connection_test_jobs",
        on_delete=models.CASCADE,
    )
    connection = models.ForeignKey(
        "accounts.McpConnection",
        related_name="test_jobs",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=24,
        choices=McpConnectionTestJobStatus.choices,
        default=McpConnectionTestJobStatus.QUEUED,
    )
    trigger = models.CharField(max_length=48, blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    run_after = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_detail = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mcp_connection_test_job"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "run_after"], name="mcp_test_job_run_after_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="mcp_test_job_lease_idx"),
            models.Index(fields=["connection", "status"], name="mcp_test_job_conn_status_idx"),
            models.Index(fields=["business_profile", "status"], name="mcp_testjob_biz_status_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.connection_id}:{self.get_status_display()}"

    def mark_cancelled(self, *, reason: str = "") -> None:
        now = timezone.now()
        self.status = McpConnectionTestJobStatus.CANCELLED
        self.finished_at = now
        if reason:
            self.error_detail = (reason or "")[:500]
        self.lease_expires_at = None
        self.run_after = None
