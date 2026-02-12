from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.credential_secrets import (
    IntegrationSecretError,
    credentials_are_stale,
    get_secret_manager,
)
from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    McpConnectionApprovalMode,
    McpConnectionAuditAction,
    McpConnectionAuthType,
    McpConnectionSourceType,
    McpConnectionStatus,
    McpToolOperationType,
    User,
)


logger = logging.getLogger(__name__)


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
        "mcp.McpConnection",
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

class McpConnection(models.Model):
    """
    Represents an MCP server connection configured for a workspace (business).

    Connections are assigned to all agents by default. Opt-outs are stored in
    McpConnectionAgentOptOut.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="mcp_connections",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        User,
        related_name="mcp_connections",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, blank=True, db_index=True, default="")
    source_type = models.CharField(
        max_length=24,
        choices=McpConnectionSourceType.choices,
        default=McpConnectionSourceType.MANUAL,
    )
    marketplace_key = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Optional identifier for a curated marketplace entry.",
    )
    server_url = models.URLField(max_length=500)
    status = models.CharField(
        max_length=16,
        choices=McpConnectionStatus.choices,
        default=McpConnectionStatus.ENABLED,
    )
    auth_type = models.CharField(
        max_length=16,
        choices=McpConnectionAuthType.choices,
        default=McpConnectionAuthType.NONE,
    )
    default_approval_mode = models.CharField(
        max_length=24,
        choices=McpConnectionApprovalMode.choices,
        default=McpConnectionApprovalMode.APPROVE_WRITES,
        help_text="Default approval behavior for tools from this connection.",
    )
    credentials_encrypted = models.TextField(blank=True, default="")
    credentials_key_version = models.PositiveSmallIntegerField(default=1)
    credentials_last_rotated_at = models.DateTimeField(null=True, blank=True)
    credential_error_count = models.PositiveSmallIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_mcp_connection"
        ordering = ("name",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="mcp_conn_business_status_idx"),
            models.Index(fields=["business_profile", "slug"], name="mcp_conn_business_slug_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "slug"],
                condition=~models.Q(slug=""),
                name="mcp_conn_slug_unique",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.name} ({self.business_profile_id})"

    def _credential_tenant(self) -> str:
        business_id = self.business_profile_id or getattr(self.business_profile, "id", None)
        if not business_id:
            raise ValueError("Business profile must be saved before storing credentials.")
        return str(business_id)

    def _cache_credentials(self, payload: dict[str, Any]) -> None:
        self._cached_credentials = dict(payload)

    def _get_cached_credentials(self) -> dict[str, Any] | None:
        return getattr(self, "_cached_credentials", None)

    def _clear_cached_credentials(self) -> None:
        if hasattr(self, "_cached_credentials"):
            delattr(self, "_cached_credentials")

    @property
    def credentials(self) -> dict[str, Any]:
        cached = self._get_cached_credentials()
        if cached is not None:
            return dict(cached)
        tenant = self.business_profile_id or getattr(self.business_profile, "id", None)
        if not tenant or not self.credentials_encrypted:
            self._cache_credentials({})
            return {}
        manager = get_secret_manager()
        try:
            payload = manager.decrypt(self.credentials_encrypted, tenant=str(tenant))
        except IntegrationSecretError as exc:
            logger.warning("mcp_credentials_decrypt_failed connection=%s error=%s", self.id, exc)
            payload = {}
        self._cache_credentials(payload)
        return dict(payload)

    @credentials.setter
    def credentials(self, value: dict[str, Any] | None) -> None:
        payload = dict(value or {})
        if not payload:
            self.credentials_encrypted = ""
            self.credentials_key_version = 1
            self.credentials_last_rotated_at = None
            self.credential_error_count = 0
            self._cache_credentials({})
            return
        manager = get_secret_manager()
        ciphertext = manager.encrypt(payload, tenant=self._credential_tenant())
        self.credentials_encrypted = ciphertext
        self.credentials_key_version = manager.key_version
        self.credentials_last_rotated_at = timezone.now()
        self.credential_error_count = 0
        self._cache_credentials(payload)

    def has_credentials(self) -> bool:
        return bool(self.credentials_encrypted)

    def credentials_need_rotation(self) -> bool:
        return credentials_are_stale(self.credentials_last_rotated_at)

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._clear_cached_credentials()

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            base_slug = slugify(self.name) or "mcp"
            candidate = base_slug
            suffix = 1
            while McpConnection.objects.filter(
                business_profile=self.business_profile,
                slug=candidate,
            ).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base_slug}-{suffix}"
            self.slug = candidate
        super().save(*args, **kwargs)


class McpConnectionAgentOptOut(models.Model):
    """
    Stores explicit opt-outs from an MCP connection for a specific agent.

    Default behavior: all agents inherit all enabled MCP connections unless opted out.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        McpConnection,
        related_name="agent_opt_outs",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        AgentProfile,
        related_name="mcp_opt_outs",
        on_delete=models.CASCADE,
    )
    opted_out_by = models.ForeignKey(
        User,
        related_name="mcp_opt_out_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    opted_out_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_mcp_connection_opt_out"
        ordering = ("-opted_out_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "agent_profile"],
                name="mcp_conn_agent_opt_out_unique",
            )
        ]
        indexes = [
            models.Index(fields=["connection", "opted_out_at"], name="mcp_conn_opt_out_idx"),
            models.Index(fields=["agent_profile", "opted_out_at"], name="mcp_agent_opt_out_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.connection_id} -> {self.agent_profile_id} (opted out)"


class McpConnectionToolSetting(models.Model):
    """
    Per-tool approval settings for an MCP connection.

    Allows overriding the connection's default approval mode for specific tools.
    For example, a GitHub connection might auto-approve read operations but require
    approval for create_issue or delete_branch.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        McpConnection,
        related_name="tool_settings",
        on_delete=models.CASCADE,
    )
    tool_name = models.CharField(
        max_length=255,
        help_text="The remote tool name from the MCP server (e.g., 'create_issue').",
    )
    operation_type = models.CharField(
        max_length=16,
        choices=McpToolOperationType.choices,
        default=McpToolOperationType.UNKNOWN,
        help_text="Classification: read (safe) or write (needs approval).",
    )
    approval_mode = models.CharField(
        max_length=24,
        choices=McpConnectionApprovalMode.choices,
        null=True,
        blank=True,
        help_text="Override the connection default. NULL = inherit from connection.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Human-readable description of what this tool does.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_mcp_connection_tool_setting"
        ordering = ("tool_name",)
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "tool_name"],
                name="mcp_tool_setting_unique",
            )
        ]
        indexes = [
            models.Index(fields=["connection", "operation_type"], name="mcp_tool_op_type_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.connection_id}:{self.tool_name} ({self.operation_type})"

    def get_effective_approval_mode(self) -> str:
        """Return the effective approval mode (own or inherited from connection)."""
        if self.approval_mode:
            return self.approval_mode
        return self.connection.default_approval_mode


class AgentMcpToolSetting(models.Model):
    """
    Per-tool approval settings for a specific agent + MCP connection.

    Used for "Always allow this tool" shortcuts scoped to a single agent.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_profile = models.ForeignKey(
        AgentProfile,
        related_name="mcp_tool_settings",
        on_delete=models.CASCADE,
    )
    connection = models.ForeignKey(
        McpConnection,
        related_name="agent_tool_settings",
        on_delete=models.CASCADE,
    )
    tool_name = models.CharField(
        max_length=255,
        help_text="The remote tool name from the MCP server (e.g., 'create_issue').",
    )
    operation_type = models.CharField(
        max_length=16,
        choices=McpToolOperationType.choices,
        default=McpToolOperationType.UNKNOWN,
        help_text="Classification: read (safe) or write (needs approval).",
    )
    approval_mode = models.CharField(
        max_length=24,
        choices=McpConnectionApprovalMode.choices,
        null=True,
        blank=True,
        help_text="Override the agent default for this tool. NULL = inherit.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_agent_mcp_tool_setting"
        ordering = ("tool_name",)
        constraints = [
            models.UniqueConstraint(
                fields=["agent_profile", "connection", "tool_name"],
                name="agent_mcp_tool_setting_unique",
            )
        ]
        indexes = [
            models.Index(fields=["agent_profile", "connection"], name="agt_mcp_tool_agent_conn_idx"),
            models.Index(fields=["connection", "tool_name"], name="agt_mcp_tool_conn_tool_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.connection_id}:{self.tool_name}"


class McpConnectionAuditEvent(models.Model):
    """
    Immutable log of key MCP connection events for compliance and debugging.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="mcp_audit_events",
        on_delete=models.CASCADE,
    )
    connection = models.ForeignKey(
        McpConnection,
        related_name="audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    connection_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Snapshot of the connection UUID for retention when the connection is deleted.",
    )
    actor_user = models.ForeignKey(
        User,
        related_name="mcp_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=32, choices=McpConnectionAuditAction.choices)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_mcp_connection_audit_event"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["connection", "action"], name="mcp_audit_action_idx"),
            models.Index(fields=["connection_id_snapshot", "action"], name="mcp_audit_snap_action_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        ref = self.connection_id_snapshot or getattr(self.connection, "id", None) or "unknown-connection"
        return f"{ref} - {self.get_action_display()}"

