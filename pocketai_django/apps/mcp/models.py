from __future__ import annotations

import uuid

from django.db import models


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

