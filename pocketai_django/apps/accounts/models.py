from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, TypedDict

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from .managers import UserManager

from django.conf import settings
from pgvector.django import VectorField

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY, sanitize_feature_payload
from apps.accounts.credential_secrets import (
    IntegrationSecretError,
    credential_policy,
    credentials_are_stale,
    get_secret_manager,
)


logger = logging.getLogger(__name__)


AGENT_MCP_APPROVAL_MODE_CHOICES = (
    ("auto", "Auto-approve all"),
    ("approve_writes", "Approve write operations"),
    ("approve_all", "Approve all operations"),
)


def _normalize_identifier_token(value: str) -> str:
    """
    Lowercase + collapse non-alphanumerics for identifier keys/columns.

    Keeps the representation stable across user/AI-sourced values.
    """

    text = (value or "").strip().lower()
    if not text:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", text)
    return normalized.strip("_")


class IntegrationColumnPrivacyConfig(TypedDict, total=False):
    """Typed representation of column-level privacy knobs for sheet resources."""

    shared_columns: list[str]
    internal_only_columns: list[str]
    excluded_columns: list[str]


class IntegrationResourceConfig(TypedDict, total=False):
    """Snapshot of a drive file + sheet/tab that should sync into the knowledge base."""

    resource_id: str
    drive_file_id: str
    drive_file_name: str
    sheet_gid: str
    sheet_name: str
    sync_frequency: str
    visibility: str
    column_privacy: IntegrationColumnPrivacyConfig
    metadata: dict[str, Any]
    last_synced_at: str
    last_sync_status: str
    last_sync_error: str
    last_sync_bytes: int
    stale_since: str
    stale_reason: str


class IntegrationSyncSchedule(TypedDict, total=False):
    """Metadata stored per integration describing cadence and next run timestamps."""

    frequency: str
    timezone: str
    next_run_at: str | None
    last_run_at: str | None
    last_status: str
    last_duration_ms: int
    paused: bool


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model using email as the login identifier.

    Designed to scale with future business/agent relationships while keeping
    lookup operations fast through explicit indexes and UUID identifiers.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    email = models.EmailField(unique=True, db_index=True, max_length=255)
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=32,
        choices=(
            ("active", "Active"),
            ("pending", "Pending"),
            ("disabled", "Disabled"),
        ),
        default="pending",
    )
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["public_id"], name="user_public_idx"),
            models.Index(fields=["status", "created_at"], name="user_status_created_idx"),
        ]

    def __str__(self) -> str:
        return self.email

    def get_full_name(self) -> str:
        """Mirror Django's default user API for compatibility with templates/views."""

        parts = [self.first_name or "", self.last_name or ""]
        return " ".join(part for part in parts if part).strip()

    def get_short_name(self) -> str:
        return (self.first_name or "").strip() or self.email


class RegistrationSession(models.Model):
    """
    Tracks multi-step onboarding progress for a user.

    Future steps (business profile, agent config, knowledge uploads) will
    relate back to this model for authorization and state management.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, related_name="registration_sessions", on_delete=models.CASCADE)
    current_step = models.CharField(max_length=32, default="form")
    steps_completed = models.PositiveSmallIntegerField(default=0)
    total_steps = models.PositiveSmallIntegerField(default=4)
    is_complete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_registration_session"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user"], name="registration_user_idx"),
            models.Index(fields=["is_complete", "updated_at"], name="registration_status_idx"),
        ]

    def mark_step(self, step: str, completed_steps: int | None = None):
        self.current_step = step
        if completed_steps is not None:
            self.steps_completed = completed_steps
        self.last_activity_at = timezone.now()
        self.save(update_fields=["current_step", "steps_completed", "last_activity_at", "updated_at"])

    def __str__(self) -> str:
        return f"RegistrationSession<{self.id}> for {self.user.email}"


class BusinessProfile(models.Model):
    """
    Captures core business metadata collected during registration step 2.

    Designed to attach to both the owning user and the registration session so
    future flows (agent setup, knowledge uploads) can be correlated easily.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, related_name="business_profiles", on_delete=models.CASCADE)
    registration_session = models.OneToOneField(
        RegistrationSession,
        related_name="business_profile",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    industry = models.CharField(max_length=120)
    industry_key = models.CharField(max_length=64, blank=True)
    line_of_business = models.JSONField(default=list, blank=True)
    line_of_business_custom = models.JSONField(default=list, blank=True)
    country = models.CharField(max_length=80, blank=True)
    website = models.URLField(blank=True)
    slug = models.SlugField(
        max_length=160,
        blank=True,
        help_text="Public slug used to route to the chat portal (derived from the business name).",
    )
    status = models.CharField(
        max_length=32,
        choices=(
            ("draft", "Draft"),
            ("pending", "Pending"),
            ("active", "Active"),
        ),
        default="draft",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_business_profile"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "status"], name="business_user_status_idx"),
            models.Index(fields=["industry"], name="business_industry_idx"),
            models.Index(fields=["slug"], name="business_slug_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=~models.Q(slug=""),
                name="business_slug_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.user.email})"

    def ensure_slug(self) -> None:
        if self.slug:
            return
        base_slug = slugify(self.name) or "business"
        candidate = base_slug
        suffix = 2
        cls = self.__class__
        while cls.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base_slug}-{suffix}"
            suffix += 1
        self.slug = candidate

    def ensure_feature_flags(self) -> None:
        metadata_source = self.metadata if isinstance(self.metadata, dict) else {}
        current = metadata_source.get(FEATURE_FLAG_METADATA_KEY)
        normalized = sanitize_feature_payload(current)
        metadata_copy = dict(metadata_source) if isinstance(metadata_source, dict) else {}
        if metadata_copy.get(FEATURE_FLAG_METADATA_KEY) != normalized:
            metadata_copy[FEATURE_FLAG_METADATA_KEY] = normalized
            self.metadata = metadata_copy

    def table_privacy_policy(self) -> dict[str, Any]:
        metadata_source = self.metadata if isinstance(self.metadata, dict) else {}
        config = metadata_source.get("table_privacy") or metadata_source.get("sensitive_table_config") or {}
        if not isinstance(config, dict):
            config = {}
        required = config.get("required_masking_columns")
        if not isinstance(required, (list, tuple)):
            required = config.get("sensitive_columns") or []
        normalized_required = [str(value).strip() for value in required if str(value or "").strip()]
        return {
            "masking_required": bool(config.get("masking_required")),
            "required_columns": normalized_required,
            "policy_version": config.get("policy_version"),
        }

    def requires_column_masking(self) -> bool:
        policy = self.table_privacy_policy()
        return bool(policy.get("masking_required"))

    def save(self, *args, **kwargs):
        self.ensure_slug()
        self.ensure_feature_flags()
        super().save(*args, **kwargs)


class TenantMemoryConfiguration(models.Model):
    """
    Per-tenant configuration for memory lifecycle and retention.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.OneToOneField(
        BusinessProfile,
        related_name="memory_config",
        on_delete=models.CASCADE,
    )
    default_hot_period_days = models.IntegerField(default=7, validators=[MinValueValidator(0)])
    default_warm_period_days = models.IntegerField(default=30, validators=[MinValueValidator(0)])
    default_archive_after_days = models.IntegerField(default=90, validators=[MinValueValidator(0)])
    custom_rules = models.JSONField(default=dict, blank=True)
    minimum_retention_days = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    maximum_retention_days = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(0)])
    purge_enabled = models.BooleanField(default=True)
    legal_hold = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_tenant_memory_configuration"
        ordering = ("-updated_at",)

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"TenantMemoryConfiguration<{self.business_profile_id}>"

    def clean(self) -> None:
        super().clean()

        errors: dict[str, list[str]] = {}

        hot = int(self.default_hot_period_days or 0)
        warm = int(self.default_warm_period_days or 0)
        archive = int(self.default_archive_after_days or 0)
        minimum_retention = int(self.minimum_retention_days or 0)
        maximum_retention: int | None = (
            int(self.maximum_retention_days) if self.maximum_retention_days is not None else None
        )

        if warm < hot:
            errors.setdefault("default_warm_period_days", []).append(
                "Warm period must be greater than or equal to hot period."
            )

        if archive < warm:
            errors.setdefault("default_archive_after_days", []).append(
                "Archive-after period must be greater than or equal to warm period."
            )

        if maximum_retention is not None:
            if maximum_retention < minimum_retention:
                errors.setdefault("maximum_retention_days", []).append(
                    "Maximum retention must be greater than or equal to minimum retention."
                )

            # Keeping these aligned avoids confusing configurations where the tenant
            # can't ever reach the configured hot/warm/archive windows.
            if maximum_retention < hot:
                errors.setdefault("maximum_retention_days", []).append(
                    "Maximum retention must be greater than or equal to the hot period."
                )
            if maximum_retention < warm:
                errors.setdefault("maximum_retention_days", []).append(
                    "Maximum retention must be greater than or equal to the warm period."
                )
            if maximum_retention < archive:
                errors.setdefault("maximum_retention_days", []).append(
                    "Maximum retention must be greater than or equal to the archive-after period."
                )

        if self.custom_rules and not isinstance(self.custom_rules, dict):
            errors.setdefault("custom_rules", []).append("Custom rules must be a JSON object (dictionary).")

        if errors:
            raise ValidationError(errors)


class TenantMemoryConfigurationAuditEvent(models.Model):
    """
    Phase 8: immutable audit log for per-tenant memory policy changes.

    Stored as a separate table so changes are queryable/reviewable without
    relying on Django admin LogEntry formatting.
    """

    class ActionChoices(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        PRESET_APPLIED = "preset_applied", "Preset applied"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="memory_config_audit_events",
        on_delete=models.CASCADE,
    )
    actor_user = models.ForeignKey(
        User,
        related_name="memory_config_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=32, choices=ActionChoices.choices)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_tenant_memory_configuration_audit_event"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["business_profile", "occurred_at"], name="memcfg_audit_bp_time_idx"),
            models.Index(fields=["actor_user", "occurred_at"], name="memcfg_audit_actor_time_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"TenantMemoryConfigurationAuditEvent<{self.business_profile_id}:{self.action}>"


class AgentProfile(models.Model):
    """
    Stores one AI employee profile for a business workspace.

    A business can own multiple named agents. Each agent has its own role,
    instructions, permissions, workflows, and scoped memory.
    """

    class KPIChoices(models.TextChoices):
        CUSTOMER_SATISFACTION = "customer_satisfaction", "Customer Satisfaction"
        FIRST_CONTACT_RESOLUTION = "first_contact_resolution", "First Contact Resolution"
        RESPONSE_TIME = "response_time", "Response Time"
        RESOLUTION_RATE = "resolution_rate", "Resolution Rate"
        ESCALATION_RATE = "escalation_rate", "Escalation Rate"
        SALES_CONVERSION = "sales_conversion", "Sales Conversion"
        DEFLECTION_RATE = "deflection_rate", "Deflection Rate"
        AVERAGE_HANDLING_TIME = "average_handling_time", "Average Handling Time"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    class StatusChoices(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        ARCHIVED = "archived", "Archived"

    class AgentTypeChoices(models.TextChoices):
        MAIN = "main", "Main Agent"
        SPECIALIST = "specialist", "Specialist"
        BACKGROUND = "background", "Background Agent"

    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="agent_profiles",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(User, related_name="agent_profiles", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=24, choices=StatusChoices.choices, default=StatusChoices.ACTIVE, db_index=True)
    slug = models.SlugField(
        max_length=160,
        blank=True,
        help_text="Shareable slug segment used to route requests to this agent (e.g. 'agentnameai').",
    )
    role = models.CharField(max_length=120, blank=True)
    agent_type = models.CharField(
        max_length=32,
        choices=AgentTypeChoices.choices,
        default=AgentTypeChoices.SPECIALIST,
        db_index=True,
    )
    manager_agent = models.ForeignKey(
        "self",
        related_name="managed_agents",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    responsibilities = models.JSONField(default=list, blank=True)
    instructions = models.TextField(blank=True, default="")
    tone = models.CharField(max_length=60, blank=True)
    traits = models.JSONField(default=list, blank=True)
    can_manage_tasks = models.BooleanField(default=False)
    permission_config = models.JSONField(default=dict, blank=True)
    escalation_rule = models.CharField(max_length=60, blank=True)
    selected_kpis = models.JSONField(
        default=list,
        blank=True,
        help_text="List of KPI identifiers guiding the agent response strategy.",
    )
    custom_kpis = models.JSONField(
        default=list,
        blank=True,
        help_text="Custom KPIs defined by the business to specialize agent performance.",
    )
    allow_custom_kpi_weighting = models.BooleanField(
        default=False,
        help_text="Allow orchestrator to prioritize custom KPIs ahead of default ones.",
    )
    mcp_default_approval_mode = models.CharField(
        max_length=24,
        choices=AGENT_MCP_APPROVAL_MODE_CHOICES,
        null=True,
        blank=True,
        help_text="Optional default approval mode for external MCP tools (overrides connection defaults when set).",
    )
    mcp_gateway_mode = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "Optional override for MCP gateway mode. "
            "When enabled, the agent uses a small gateway tool surface for external MCP tools "
            "instead of inlining every remote tool schema into the LLM prompt."
        ),
    )
    allowed_documents = models.ManyToManyField(
        "knowledge.KnowledgeUpload",
        through="knowledge.AgentKnowledgeAccess",
        related_name="permitted_agents",
        blank=True,
        help_text="Knowledge uploads this agent is permitted to use during conversations.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_agent_profile"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "slug"], name="agent_business_slug_idx"),
            models.Index(fields=["business_profile", "agent_type"], name="agent_business_type_idx"),
            models.Index(fields=["manager_agent", "status"], name="agent_manager_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "slug"],
                condition=~models.Q(slug=""),
                name="agent_unique_business_slug",
            ),
            models.UniqueConstraint(
                fields=["business_profile", "agent_type"],
                condition=models.Q(agent_type="main", status="active"),
                name="agent_unique_active_main",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.business_profile.name})"

    @property
    def shareable_path(self) -> str:
        """
        Construct the public-friendly route for accessing the agent.

        Intended for use when wiring dashboard links (e.g. `/acme-inc/supportai`).
        """

        business_slug = self.business_profile.slug or slugify(self.business_profile.name)
        return f"/{business_slug}/{self.slug}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Ensure a shareable slug exists. Auto-generates with business-scoped uniqueness.
        """

        if not self.slug:
            base_slug = slugify(self.name) or "agent"
            candidate = base_slug
            suffix = 1
            while AgentProfile.objects.filter(
                business_profile=self.business_profile,
                slug=candidate,
            ).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base_slug}-{suffix}"
            self.slug = candidate

        super().save(*args, **kwargs)


class AgentActionPermission(models.Model):
    """
    Per-agent toggle for orchestrator actions (create case, update customer, etc).

    Entries are optional; when absent the orchestrator falls back to the default
    action registry definition. Stored here so operators can selectively disable
    behaviours per business requirements.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_profile = models.ForeignKey(
        AgentProfile,
        related_name="action_permissions",
        on_delete=models.CASCADE,
    )
    action_key = models.CharField(max_length=64, db_index=True)
    is_enabled = models.BooleanField(default=True)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_agent_action_permission"
        unique_together = ("agent_profile", "action_key")
        indexes = [
            models.Index(fields=["action_key"], name="agent_action_key_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.agent_profile.name}:{self.action_key} ({'on' if self.is_enabled else 'off'})"


class KnowledgeSourceType(models.TextChoices):
    FILE = "file", "File Upload"
    LINK = "link", "External Link"
    TEXT = "text", "Manual Entry"
    INTEGRATION = "integration", "Integration Sync"
    EMBED = "embed", "Embedded Content"


class KnowledgeStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    ACTIVE = "active", "Active"
    FAILED = "failed", "Failed"
    ARCHIVED = "archived", "Archived"


class KnowledgeVisibility(models.TextChoices):
    PRIVATE = "private", "Private"
    INTERNAL = "internal", "Internal"
    SHARED = "shared", "Shared"


class KnowledgeIntegrationType(models.TextChoices):
    CUSTOM = "custom", "Custom"
    NOTION = "notion", "Notion"
    GOOGLE_DRIVE = "google_drive", "Google Drive"
    ZENDESK = "zendesk", "Zendesk"
    HUBSPOT = "hubspot", "HubSpot"
    SLACK = "slack", "Slack"
    CONFLUENCE = "confluence", "Confluence"


class KnowledgeIntegrationStatus(models.TextChoices):
    CONNECTED = "connected", "Connected"
    SYNCING = "syncing", "Syncing"
    DISCONNECTED = "disconnected", "Disconnected"
    ERROR = "error", "Error"


class IntegrationSyncFrequency(models.TextChoices):
    MANUAL = "manual", "Manual"
    HOURLY = "hourly", "Hourly"
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"


class IntegrationCredentialEventType(models.TextChoices):
    CREATED = "created", "Created"
    REFRESHED = "refreshed", "Refreshed"
    ERROR = "error", "Error"
    CLEARED = "cleared", "Cleared"
    ROTATION_REQUIRED = "rotation_required", "Rotation Required"


class EmailAccountProvider(models.TextChoices):
    GOOGLE = "google", "Google (Gmail/Workspace)"
    MICROSOFT = "microsoft", "Microsoft (Outlook/M365)"


class EmailAccountStatus(models.TextChoices):
    DISCONNECTED = "disconnected", "Disconnected"
    CONNECTING = "connecting", "Connecting"
    CONNECTED = "connected", "Connected"
    ERROR = "error", "Error"


class EmailSendMode(models.TextChoices):
    DRAFT_APPROVAL = "draft_approval", "Draft + approval"
    AUTO_SEND = "auto_send", "Auto-send"


class EmailAccountAuditAction(models.TextChoices):
    CONNECTED = "connected", "Connected"
    UPDATED = "updated", "Updated"
    DISCONNECTED = "disconnected", "Disconnected"
    SEARCHED = "searched", "Searched"
    READ_MESSAGE = "read_message", "Read message"
    READ_THREAD = "read_thread", "Read thread"
    DRAFT_CREATED = "draft_created", "Draft created"
    SEND_REQUESTED = "send_requested", "Send requested"
    SEND_APPROVED = "send_approved", "Send approved"
    SEND_DENIED = "send_denied", "Send denied"
    SENT = "sent", "Sent"
    ERROR = "error", "Error"


class EmailAccountHealthJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class McpConnectionSourceType(models.TextChoices):
    MARKETPLACE = "marketplace", "Marketplace"
    MANUAL = "manual", "Manual"


class McpConnectionStatus(models.TextChoices):
    ENABLED = "enabled", "Enabled"
    DISABLED = "disabled", "Disabled"


class McpConnectionAuthType(models.TextChoices):
    NONE = "none", "No auth"
    BEARER = "bearer", "Bearer token"
    HEADER = "header", "Custom header"


class McpConnectionAuditAction(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    ENABLED = "enabled", "Enabled"
    DISABLED = "disabled", "Disabled"
    AGENT_OPTED_OUT = "agent_opted_out", "Agent Opted Out"
    AGENT_OPTED_IN = "agent_opted_in", "Agent Opted In"
    TOOL_APPROVED = "tool_approved", "Tool Approved"
    TOOL_DENIED = "tool_denied", "Tool Denied"


class McpConnectionApprovalMode(models.TextChoices):
    """
    Approval modes for MCP connection tool execution.

    - AUTO: All tools execute automatically without user confirmation
    - APPROVE_WRITES: Read operations auto-approve, write operations require approval
    - APPROVE_ALL: All tool calls require user approval before execution
    """
    AUTO = "auto", "Auto-approve all"
    APPROVE_WRITES = "approve_writes", "Approve write operations"
    APPROVE_ALL = "approve_all", "Approve all operations"


class McpToolOperationType(models.TextChoices):
    """Classification of tool operations for approval purposes."""
    READ = "read", "Read (safe)"
    WRITE = "write", "Write (requires approval)"
    UNKNOWN = "unknown", "Unknown (treat as write)"


def default_knowledge_integration_settings() -> dict[str, Any]:
    """Provide a predictable structure for integration.settings JSON."""

    return {
        "default_visibility": KnowledgeVisibility.PRIVATE,
        "default_sync_frequency": IntegrationSyncFrequency.DAILY,
        "resources": [],
    }


class KnowledgeIngestionJobType(models.TextChoices):
    INGEST = "ingest", "Initial Ingest"
    EMBED = "embed", "Embedding"
    REBUILD = "rebuild", "Rebuild"
    DELETE = "delete", "Delete"
    SYNC = "sync", "Sync"


class KnowledgeIngestionJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DEFERRED = "deferred", "Deferred"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class KnowledgeAuditAction(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    INGESTED = "ingested", "Ingested"
    READ = "read", "Read"
    EXPORTED = "exported", "Exported"
    PERMISSION_GRANTED = "permission_granted", "Permission Granted"
    PERMISSION_REVOKED = "permission_revoked", "Permission Revoked"
    ARCHIVED = "archived", "Archived"
    RESTORED = "restored", "Restored"


class KnowledgeBlockType(models.TextChoices):
    HEADING = "heading", "Heading"
    PARAGRAPH = "paragraph", "Paragraph"
    LIST = "list", "List"
    TABLE = "table", "Table"
    FIGURE = "figure", "Figure"
    IMAGE = "image", "Image"
    FOOTER = "footer", "Footer"
    HEADER = "header", "Header"
    OCR_ONLY = "ocr_only", "OCR Text"
    OTHER = "other", "Other"


class KnowledgeIssueSeverity(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class IntegrationType(models.TextChoices):
    GOOGLE_CALENDAR = "google_calendar", "Google Calendar"
    GOOGLE_DRIVE = "google_drive", "Google Drive"
    ONEDRIVE = "onedrive", "OneDrive"
    SLACK = "slack", "Slack"
    HUBSPOT = "hubspot", "HubSpot"


class IntegrationProvider(models.TextChoices):
    GOOGLE = "google", "Google"
    MICROSOFT = "microsoft", "Microsoft"
    SLACK = "slack", "Slack"
    HUBSPOT = "hubspot", "HubSpot"


class IntegrationAccountStatus(models.TextChoices):
    DISCONNECTED = "disconnected", "Disconnected"
    CONNECTING = "connecting", "Connecting"
    CONNECTED = "connected", "Connected"
    ERROR = "error", "Error"


class IntegrationAccountAuditAction(models.TextChoices):
    CONNECTED = "connected", "Connected"
    UPDATED = "updated", "Updated"
    DISCONNECTED = "disconnected", "Disconnected"
    TOOL_CALLED = "tool_called", "Tool Called"
    ERROR = "error", "Error"
