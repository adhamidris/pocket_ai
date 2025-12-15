from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, TypedDict

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.postgres.indexes import GinIndex
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


class AgentProfile(models.Model):
    """
    Stores the virtual agent configuration gathered during registration step 3.

    The agent is linked one-to-one with a business profile, ensuring future
    retrieval/knowledge settings can pivot off the same business entity.
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
    business_profile = models.OneToOneField(
        BusinessProfile,
        related_name="agent_profile",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(User, related_name="agent_profiles", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    slug = models.SlugField(
        max_length=160,
        blank=True,
        help_text="Shareable slug segment used to route requests to this agent (e.g. 'agentnameai').",
    )
    role = models.CharField(max_length=120, blank=True)
    tone = models.CharField(max_length=60, blank=True)
    traits = models.JSONField(default=list, blank=True)
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
    allowed_documents = models.ManyToManyField(
        "KnowledgeUpload",
        through="AgentKnowledgeAccess",
        related_name="permitted_agents",
        blank=True,
        help_text="Knowledge uploads this agent is permitted to use during conversations.",
    )
    status = models.CharField(
        max_length=32,
        choices=(
            ("draft", "Draft"),
            ("review", "Review"),
            ("active", "Active"),
        ),
        default="draft",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_agent_profile"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "status"], name="agent_user_status_idx"),
            models.Index(fields=["status", "updated_at"], name="agent_status_updated_idx"),
            models.Index(fields=["business_profile", "slug"], name="agent_business_slug_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "slug"],
                condition=~models.Q(slug=""),
                name="agent_unique_business_slug",
            )
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


def default_knowledge_integration_settings() -> dict[str, Any]:
    """Provide a predictable structure for integration.settings JSON."""

    return {
        "default_visibility": KnowledgeVisibility.PRIVATE,
        "default_sync_frequency": IntegrationSyncFrequency.DAILY,
        "resources": [],
    }


class KnowledgeCollectionVisibility(models.TextChoices):
    PRIVATE = "private", "Private"
    AGENTS = "agents", "Agents Only"
    BUSINESS = "business", "Entire Business"


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


class KnowledgeUpload(models.Model):
    """
    Central knowledge artifact powering the AI agent experience.

    Supports uploads, URLs, manual snippets, and integration-sourced content while
    tracking ingestion state, access metadata, and collection membership. When the
    source type is ``integration`` the ``source_uid`` tracks the integration
    resource id (e.g., a Drive file + sheet gid) so sync jobs can upsert rows
    deterministically.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_uploads",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(User, related_name="knowledge_uploads", on_delete=models.CASCADE)
    created_by_agent = models.ForeignKey(
        AgentProfile,
        related_name="knowledge_contributions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    integration = models.ForeignKey(
        "KnowledgeIntegration",
        related_name="uploads",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    display_name = models.CharField(max_length=255, blank=True, default="")
    slug = models.SlugField(max_length=160, blank=True, db_index=True, default="")
    description = models.TextField(blank=True, default="")
    summary = models.TextField(blank=True, default="")
    source_name = models.CharField(max_length=255, blank=True, default="")
    source_uid = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Identifier for deduping integration resources (e.g., drive_file:sheet_gid).",
    )
    external_reference = models.CharField(max_length=255, blank=True, default="")
    legacy_url = models.URLField(blank=True, default="")
    source_type = models.CharField(
        max_length=32,
        choices=KnowledgeSourceType.choices,
        default=KnowledgeSourceType.FILE,
        db_column="resource_type",
    )
    status = models.CharField(max_length=32, choices=KnowledgeStatus.choices, default=KnowledgeStatus.PENDING)
    visibility = models.CharField(max_length=32, choices=KnowledgeVisibility.choices, default=KnowledgeVisibility.PRIVATE)
    language = models.CharField(max_length=32, blank=True, default="")
    category = models.CharField(max_length=64, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ingestion_metadata = models.JSONField(default=dict, blank=True)
    retention_policy = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional rules for expiry or redaction.",
    )
    checksum_sha256 = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    token_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)
    is_sensitive = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    last_ingested_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    ingestion_error = models.TextField(blank=True, default="")
    collections = models.ManyToManyField(
        "KnowledgeCollection",
        through="KnowledgeCollectionLink",
        related_name="knowledge_uploads",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="upload_business_status_idx"),
            models.Index(fields=["business_profile", "source_type"], name="upload_business_source_idx"),
            models.Index(fields=["business_profile", "slug"], name="upload_business_slug_idx"),
            models.Index(fields=["integration", "status"], name="upload_integration_status_idx"),
            GinIndex(
                fields=["display_name"],
                name="upload_display_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "slug"],
                condition=~models.Q(slug=""),
                name="knowledge_unique_business_slug",
            )
        ]

    def __str__(self) -> str:
        label = self.display_name or self.source_name or self.external_reference or str(self.id)
        return f"{label} ({self.get_source_type_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.display_name:
            self.display_name = self.source_name or self.external_reference or self.legacy_url or "Knowledge Item"

        if not self.slug:
            base_slug = slugify(self.display_name) or "knowledge"
            candidate = base_slug
            suffix = 1
            while KnowledgeUpload.objects.filter(
                business_profile=self.business_profile,
                slug=candidate,
            ).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base_slug}-{suffix}"
            self.slug = candidate

        super().save(*args, **kwargs)


class KnowledgeUploadFile(models.Model):
    """
    File metadata for a document-type knowledge upload.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField(
        KnowledgeUpload,
        related_name="file_detail",
        on_delete=models.CASCADE,
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True, default="")
    storage_path = models.CharField(max_length=512)
    size_bytes = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    checksum_sha256 = models.CharField(max_length=128, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_file"

    def __str__(self) -> str:
        return f"{self.filename} ({self.content_type or 'unknown'})"


class KnowledgeUploadUrl(models.Model):
    """
    Captures external URL references saved into the knowledge base.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField(
        KnowledgeUpload,
        related_name="url_detail",
        on_delete=models.CASCADE,
    )
    url = models.URLField()
    normalized_host = models.CharField(max_length=120, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_url"

    def __str__(self) -> str:
        return self.url


class KnowledgeUploadText(models.Model):
    """
    Stores manual snippets or playbooks entered directly via the dashboard.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField(
        KnowledgeUpload,
        related_name="text_detail",
        on_delete=models.CASCADE,
    )
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_text"

    def __str__(self) -> str:
        return f"Text snippet for {self.upload}"


class KnowledgeUploadChunk(models.Model):
    """
    Normalized chunk of extracted knowledge text for search + embedding retrieval.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="chunks",
        on_delete=models.CASCADE,
    )
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_chunks",
        on_delete=models.CASCADE,
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    embedding = VectorField(dimensions=settings.EMBED_DIM, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_chunk"
        ordering = ("upload_id", "chunk_index")
        indexes = [
            models.Index(fields=["upload", "chunk_index"], name="knowledge_chunk_window_idx"),
            models.Index(fields=["business_profile", "chunk_index"], name="kn_chunk_biz_idx"),
            models.Index(fields=["business_profile", "upload"], name="kn_chunk_biz_upload_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "chunk_index"],
                name="knowledge_chunk_unique_index",
            )
        ]

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} for {self.upload_id}"

    def save(self, *args, **kwargs):
        if self.upload_id and not self.business_profile_id and getattr(self, "upload", None):
            self.business_profile = self.upload.business_profile
        super().save(*args, **kwargs)


class KnowledgeEntity(models.Model):
    """
    Structured entity detected during ingestion (primarily from JSON sources).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_entities",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="entities",
        on_delete=models.CASCADE,
    )
    chunk = models.OneToOneField(
        KnowledgeUploadChunk,
        related_name="entity_record",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    entity_type = models.CharField(max_length=120, blank=True, default="")
    entity_name = models.CharField(max_length=255, blank=True, default="")
    primary_label = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_entity"
        indexes = [
            models.Index(fields=["business_profile", "entity_type"], name="knowledge_entity_type_idx"),
            models.Index(fields=["upload"], name="knowledge_entity_upload_idx"),
        ]

    def __str__(self) -> str:
        label = self.entity_name or self.primary_label or str(self.id)
        return f"{label} ({self.entity_type or 'entity'})"


class KnowledgeAlias(models.Model):
    """
    Normalized alias/identifier tied to a structured entity for deterministic lookups.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_aliases",
        on_delete=models.CASCADE,
    )
    entity = models.ForeignKey(
        KnowledgeEntity,
        related_name="aliases",
        on_delete=models.CASCADE,
    )
    alias_raw = models.CharField(max_length=255)
    alias_normalized = models.CharField(max_length=255, db_index=True)
    alias_search_vector = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=60, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_alias"
        indexes = [
            models.Index(fields=["business_profile", "alias_normalized"], name="kn_alias_biz_norm_idx"),
            models.Index(
                fields=["alias_normalized"],
                name="kn_alias_norm_len_idx",
                condition=models.Q(alias_normalized__regex=r".{5,}"),
            ),
            GinIndex(
                fields=["alias_normalized"],
                name="kn_alias_norm_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["alias_search_vector"],
                name="knowledge_alias_search_gin",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "alias_normalized"],
                name="knowledge_alias_unique_entity_alias",
            )
        ]

    def __str__(self) -> str:
        return self.alias_raw


class IdentifierSchemaStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class IdentifierSchemaSource(models.TextChoices):
    USER = "user", "User"
    AI = "ai", "AI"


class IdentifierColumnStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class IdentifierSchema(models.Model):
    """
    Canonical identifier definition (email, phone, customer_id) scoped to a business.

    Schemas can be user-defined or AI-proposed; activation gates MCP retrieval.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="identifier_schemas",
        on_delete=models.CASCADE,
    )
    key = models.CharField(max_length=80, help_text="Machine-friendly identifier key (e.g., email, phone, customer_id).")
    display_name = models.CharField(
        max_length=160,
        help_text="Human-readable label shown in admin surfaces.",
    )
    status = models.CharField(
        max_length=24,
        choices=IdentifierSchemaStatus.choices,
        default=IdentifierSchemaStatus.PROPOSED,
        help_text="Activation status for retrieval guardrails.",
    )
    source = models.CharField(
        max_length=16,
        choices=IdentifierSchemaSource.choices,
        default=IdentifierSchemaSource.USER,
        help_text="Whether this identifier was user-defined or proposed by AI.",
    )
    is_required = models.BooleanField(default=True, help_text="If true, retrieval must be scoped by this identifier when present.")
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_identifier_schema"
        ordering = ("-updated_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="identifier_schema_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "key"],
                name="identifier_schema_unique_key",
            )
        ]

    def save(self, *args, **kwargs):
        self.key = _normalize_identifier_token(self.key) or self.key
        if not self.display_name:
            self.display_name = self.key
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.key} ({self.business_profile_id})"


class IdentifierColumnMapping(models.Model):
    """
    Maps upload/sheet columns to identifier schemas for guardrails and reuse.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="identifier_columns",
        on_delete=models.CASCADE,
    )
    identifier = models.ForeignKey(
        IdentifierSchema,
        related_name="column_mappings",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="identifier_columns",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    sheet_name = models.CharField(max_length=255, blank=True, default="")
    column_name = models.CharField(max_length=255)
    column_normalized = models.CharField(max_length=255, db_index=True)
    status = models.CharField(
        max_length=24,
        choices=IdentifierColumnStatus.choices,
        default=IdentifierColumnStatus.PROPOSED,
    )
    source = models.CharField(
        max_length=16,
        choices=IdentifierSchemaSource.choices,
        default=IdentifierSchemaSource.USER,
    )
    confidence = models.FloatField(null=True, blank=True)
    is_required = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Override for identifier.is_required. When true, queries for this upload must be scoped by this "
            "identifier; when false, it is optional for this upload."
        ),
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_identifier_column"
        ordering = ("-updated_at",)
        indexes = [
            models.Index(
                fields=["business_profile", "column_normalized"],
                name="identifier_column_norm_idx",
            ),
            models.Index(
                fields=["business_profile", "upload"],
                name="identifier_column_upload_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["identifier", "upload", "column_normalized", "sheet_name"],
                name="identifier_column_unique_scope",
            )
        ]

    def save(self, *args, **kwargs):
        self.column_normalized = _normalize_identifier_token(self.column_name) or self.column_name
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        label = self.column_name or self.column_normalized
        return f"{label} -> {self.identifier.key}"


class IdentifierColumnMemory(models.Model):
    """
    Remembers approved identifier mappings per business to bias future proposals.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="identifier_memories",
        on_delete=models.CASCADE,
    )
    identifier_schema = models.ForeignKey(
        IdentifierSchema,
        related_name="identifier_memories",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    identifier_key = models.CharField(max_length=80)
    normalized_column = models.CharField(max_length=255, db_index=True)
    pattern_signature = models.CharField(max_length=255, blank=True, default="")
    last_confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_identifier_memory"
        ordering = ("-updated_at",)
        indexes = [
            models.Index(
                fields=["business_profile", "normalized_column"],
                name="identifier_memory_column_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "normalized_column", "pattern_signature"],
                name="identifier_memory_unique_signature",
            )
        ]

    def __str__(self) -> str:
        return f"{self.normalized_column} -> {self.identifier_key}"


class KnowledgeUploadPage(models.Model):
    """
    Captures per-page layout, measurements, and extraction metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="pages",
        on_delete=models.CASCADE,
    )
    page_number = models.PositiveIntegerField()
    width = models.FloatField(default=0.0)
    height = models.FloatField(default=0.0)
    rotation = models.IntegerField(default=0)
    text_density = models.FloatField(default=0.0)
    has_ocr_content = models.BooleanField(default=False)
    content_type = models.CharField(max_length=100, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_page"
        ordering = ("upload_id", "page_number")
        indexes = [
            models.Index(fields=["upload", "page_number"], name="knowledge_page_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "page_number"],
                name="knowledge_page_unique_number",
            )
        ]

    def __str__(self) -> str:
        return f"Page {self.page_number} ({self.upload_id})"


class KnowledgeUploadPageBlock(models.Model):
    """
    Stores layout-aware text/image/table blocks with bounding box provenance.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="page_blocks",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        KnowledgeUploadPage,
        related_name="blocks",
        on_delete=models.CASCADE,
    )
    block_type = models.CharField(
        max_length=32,
        choices=KnowledgeBlockType.choices,
        default=KnowledgeBlockType.PARAGRAPH,
    )
    order_index = models.PositiveIntegerField(default=0)
    text = models.TextField(blank=True, default="")
    bbox = models.JSONField(default=dict, blank=True)
    section_heading = models.CharField(max_length=255, blank=True, default="")
    heading_path = models.JSONField(default=list, blank=True)
    detected_language = models.CharField(max_length=32, blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_page_block"
        ordering = ("page_id", "order_index")
        indexes = [
            models.Index(fields=["upload", "block_type"], name="knowledge_block_type_idx"),
            models.Index(fields=["page", "block_type"], name="knowledge_block_page_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "order_index"],
                name="knowledge_block_unique_order",
            )
        ]

    def __str__(self) -> str:
        return f"Block {self.order_index} ({self.block_type}) on page {self.page_id}"


class KnowledgeUploadTable(models.Model):
    """
    Normalized representation of detected tables with provenance metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="tables",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        KnowledgeUploadPage,
        related_name="tables",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    source_block = models.ForeignKey(
        KnowledgeUploadPageBlock,
        related_name="tables",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255, blank=True, default="")
    section_heading = models.CharField(max_length=255, blank=True, default="")
    order_index = models.PositiveIntegerField(default=0)
    bbox = models.JSONField(default=dict, blank=True)
    column_schema = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered schema describing each detected column.",
    )
    data_dictionary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional metadata describing column semantics/normalization.",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_table"
        ordering = ("upload_id", "order_index")
        indexes = [
            models.Index(fields=["upload", "order_index"], name="knowledge_table_upload_idx"),
            models.Index(fields=["page", "order_index"], name="knowledge_table_page_idx"),
        ]

    def __str__(self) -> str:
        return f"Table {self.order_index} for {self.upload_id}"


class KnowledgeUploadTableRow(models.Model):
    """
    Row-level representation to retain positional accuracy and provenance.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(
        KnowledgeUploadTable,
        related_name="rows",
        on_delete=models.CASCADE,
    )
    row_index = models.PositiveIntegerField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    bbox = models.JSONField(default=dict, blank=True)
    raw_text = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_table_row"
        ordering = ("table_id", "row_index")
        constraints = [
            models.UniqueConstraint(
                fields=["table", "row_index"],
                name="knowledge_table_row_unique_index",
            )
        ]

    def __str__(self) -> str:
        return f"Row {self.row_index} for table {self.table_id}"


class KnowledgeUploadTableCell(models.Model):
    """
    Cell-level storage for both raw and normalized values plus coordinates.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(
        KnowledgeUploadTable,
        related_name="cells",
        on_delete=models.CASCADE,
    )
    row = models.ForeignKey(
        KnowledgeUploadTableRow,
        related_name="cells",
        on_delete=models.CASCADE,
    )
    column_index = models.PositiveIntegerField()
    column_key = models.CharField(max_length=160, blank=True, default="")
    raw_text = models.TextField(blank=True, default="")
    normalized_value = models.JSONField(
        default=dict,
        blank=True,
        help_text="Parsed/typed representation (e.g., amount, currency).",
    )
    bbox = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_table_cell"
        ordering = ("table_id", "row_id", "column_index")
        indexes = [
            models.Index(fields=["table", "column_index"], name="knowledge_cell_column_idx"),
            models.Index(fields=["column_key"], name="knowledge_cell_column_key_idx"),
            GinIndex(
                fields=["raw_text"],
                name="knowledge_cell_raw_text_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["row", "column_index"],
                name="knowledge_cell_unique_row_column",
            )
        ]

    def __str__(self) -> str:
        return f"Cell r{self.row_id}-c{self.column_index}"


class KnowledgeUploadIssue(models.Model):
    """
    Structured issues log connected back to uploads, pages, and table artifacts.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="issues",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        KnowledgeUploadPage,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    table = models.ForeignKey(
        KnowledgeUploadTable,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    table_row = models.ForeignKey(
        KnowledgeUploadTableRow,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    table_cell = models.ForeignKey(
        KnowledgeUploadTableCell,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    issue_code = models.CharField(max_length=120)
    severity = models.CharField(
        max_length=16,
        choices=KnowledgeIssueSeverity.choices,
        default=KnowledgeIssueSeverity.INFO,
    )
    description = models.TextField(blank=True, default="")
    detected_by = models.CharField(max_length=64, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_issue"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["upload", "severity"], name="knowledge_issue_severity_idx"),
            models.Index(fields=["table", "issue_code"], name="knowledge_issue_table_idx"),
        ]

    def __str__(self) -> str:
        return f"Issue {self.issue_code} ({self.severity}) for upload {self.upload_id}"


class KnowledgeCollection(models.Model):
    """
    Logical grouping of knowledge uploads surfaced in the dashboard collections tab.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_collections",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        User,
        related_name="knowledge_collections",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, blank=True, db_index=True, default="")
    description = models.TextField(blank=True, default="")
    visibility = models.CharField(
        max_length=32,
        choices=KnowledgeCollectionVisibility.choices,
        default=KnowledgeCollectionVisibility.PRIVATE,
    )
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_collection"
        ordering = ("name",)
        indexes = [
            models.Index(fields=["business_profile", "slug"], name="knowledge_coll_slug_idx"),
            models.Index(fields=["business_profile", "visibility"], name="knowledge_coll_vis_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "slug"],
                condition=~models.Q(slug=""),
                name="knowledge_coll_slug_uniq",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            base_slug = slugify(self.name) or "collection"
            candidate = base_slug
            suffix = 1
            while KnowledgeCollection.objects.filter(
                business_profile=self.business_profile,
                slug=candidate,
            ).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base_slug}-{suffix}"
            self.slug = candidate

        super().save(*args, **kwargs)


class KnowledgeCollectionLink(models.Model):
    """
    Junction table mapping uploads into collections with optional ordering metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        KnowledgeCollection,
        related_name="links",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="collection_links",
        on_delete=models.CASCADE,
    )
    position = models.PositiveIntegerField(default=0)
    added_by = models.ForeignKey(
        User,
        related_name="knowledge_collection_links",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    metadata = models.JSONField(default=dict, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_knowledge_collection_link"
        ordering = ("position", "added_at")
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "upload"],
                name="knowledge_coll_upload_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.collection.name} -> {self.upload}"


class KnowledgeIntegration(models.Model):
    """Drive, sheet, or wiki connector that syncs content into KnowledgeUpload rows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_integrations",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        User,
        related_name="knowledge_integrations",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, blank=True, db_index=True, default="")
    integration_type = models.CharField(
        max_length=32,
        choices=KnowledgeIntegrationType.choices,
        default=KnowledgeIntegrationType.CUSTOM,
    )
    status = models.CharField(max_length=32, choices=KnowledgeIntegrationStatus.choices, default=KnowledgeIntegrationStatus.CONNECTED)
    external_account_id = models.CharField(max_length=255, blank=True, default="")
    credentials_encrypted = models.TextField(blank=True, default="")
    credentials_key_version = models.PositiveSmallIntegerField(default=1)
    credentials_last_rotated_at = models.DateTimeField(null=True, blank=True)
    credential_error_count = models.PositiveSmallIntegerField(default=0)
    settings = models.JSONField(default=default_knowledge_integration_settings, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_integration"
        ordering = ("name",)
        indexes = [
            models.Index(fields=["business_profile", "integration_type"], name="knowledge_integration_type_idx"),
            models.Index(fields=["business_profile", "slug"], name="knowledge_integration_slug_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "slug"],
                condition=~models.Q(slug=""),
                name="knowledge_integr_slug_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_integration_type_display()})"

    # --- Integration resource helpers -------------------------------------------------
    @property
    def resource_configs(self) -> list[IntegrationResourceConfig]:
        settings = self.settings or {}
        resources = settings.get("resources") or []
        if isinstance(resources, list):
            return [resource for resource in resources if isinstance(resource, dict)]
        return []

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
            logger.warning("integration_credentials_decrypt_failed integration=%s error=%s", self.id, exc)
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

    def clear_credentials(self, *, actor: "User" | None = None, reason: str | None = None) -> None:
        self.credentials_encrypted = ""
        self.credentials_key_version = 1
        self.credentials_last_rotated_at = None
        self.credential_error_count = 0
        self._cache_credentials({})
        self.log_credential_event(
            IntegrationCredentialEventType.CLEARED,
            actor=actor,
            metadata={"reason": reason} if reason else None,
        )

    def log_credential_event(
        self,
        event_type: str,
        *,
        actor: "User" | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        IntegrationCredentialEvent.objects.create(
            business_profile=self.business_profile,
            integration=self,
            triggered_by=actor,
            event_type=event_type,
            metadata=metadata or {},
        )

    def register_credential_failure(self, *, reason: str | None = None, actor: "User" | None = None) -> None:
        policy = credential_policy()
        self.credential_error_count = (self.credential_error_count or 0) + 1
        metadata = {"reason": reason, "failureCount": self.credential_error_count}
        self.log_credential_event(IntegrationCredentialEventType.ERROR, actor=actor, metadata=metadata)
        if self.credential_error_count >= policy.error_threshold:
            self.status = KnowledgeIntegrationStatus.DISCONNECTED
            self.sync_error = reason or "Integration requires reconnection."
            self.log_credential_event(
                IntegrationCredentialEventType.ROTATION_REQUIRED,
                actor=actor,
                metadata={"reason": self.sync_error},
            )

    def reset_credential_failures(self) -> None:
        if self.credential_error_count:
            self.credential_error_count = 0

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._clear_cached_credentials()

    def set_resource_configs(self, resources: list[IntegrationResourceConfig]) -> None:
        settings = self.settings or {}
        settings["resources"] = resources
        self.settings = settings

    def get_default_visibility(self) -> str:
        settings = self.settings or {}
        return settings.get("default_visibility") or KnowledgeVisibility.PRIVATE

    def set_default_visibility(self, visibility: str) -> None:
        settings = self.settings or {}
        settings["default_visibility"] = visibility
        self.settings = settings

    def get_default_sync_frequency(self) -> str:
        settings = self.settings or {}
        return settings.get("default_sync_frequency") or IntegrationSyncFrequency.DAILY

    def set_default_sync_frequency(self, frequency: str) -> None:
        settings = self.settings or {}
        settings["default_sync_frequency"] = frequency
        self.settings = settings

    def get_sync_schedule(self) -> IntegrationSyncSchedule:
        metadata = self.metadata or {}
        schedule = metadata.get("sync_schedule")
        if not isinstance(schedule, dict):
            schedule = {}
        normalized: IntegrationSyncSchedule = {
            "frequency": schedule.get("frequency") or self.get_default_sync_frequency(),
            "timezone": schedule.get("timezone") or "UTC",
            "next_run_at": schedule.get("next_run_at"),
            "last_run_at": schedule.get("last_run_at"),
            "last_status": schedule.get("last_status") or "",
            "last_duration_ms": schedule.get("last_duration_ms") or 0,
            "paused": bool(schedule.get("paused")),
        }
        return normalized

    def set_sync_schedule(self, schedule: IntegrationSyncSchedule | dict[str, Any]) -> None:
        metadata = dict(self.metadata or {})
        metadata["sync_schedule"] = dict(schedule)
        self.metadata = metadata

    def due_for_sync(self, *, now: datetime | None = None) -> bool:
        schedule = self.get_sync_schedule()
        if schedule.get("paused"):
            return False
        frequency = schedule.get("frequency") or self.get_default_sync_frequency()
        if frequency == IntegrationSyncFrequency.MANUAL:
            return False
        reference = now or timezone.now()
        next_run_at = self._parse_schedule_timestamp(schedule.get("next_run_at"))
        if next_run_at is None:
            return True
        return reference >= next_run_at

    def calculate_next_sync_at(self, *, from_time: datetime | None = None, frequency: str | None = None) -> datetime | None:
        freq = frequency or self.get_sync_schedule().get("frequency") or self.get_default_sync_frequency()
        if freq == IntegrationSyncFrequency.MANUAL:
            return None
        start = from_time or timezone.now()
        if freq == IntegrationSyncFrequency.HOURLY:
            delta = timedelta(hours=1)
        elif freq == IntegrationSyncFrequency.WEEKLY:
            delta = timedelta(days=7)
        else:
            delta = timedelta(days=1)
        return start + delta

    def record_sync_schedule(
        self,
        *,
        started_at: datetime,
        duration_ms: int | None = None,
        status: str = "success",
    ) -> None:
        schedule = self.get_sync_schedule()
        next_run = self.calculate_next_sync_at(from_time=started_at, frequency=schedule.get("frequency"))
        schedule.update(
            {
                "last_run_at": started_at.isoformat(),
                "last_status": status,
                "last_duration_ms": duration_ms or schedule.get("last_duration_ms") or 0,
                "next_run_at": next_run.isoformat() if next_run else None,
            }
        )
        self.set_sync_schedule(schedule)

    def _parse_schedule_timestamp(self, value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone=timezone.utc)
        return parsed

    def get_resource_by_id(self, resource_id: str) -> IntegrationResourceConfig | None:
        for resource in self.resource_configs:
            rid = resource.get("resource_id")
            if rid and rid == resource_id:
                return resource
        return None

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            base_slug = slugify(self.name) or "integration"
            candidate = base_slug
            suffix = 1
            while KnowledgeIntegration.objects.filter(
                business_profile=self.business_profile,
                slug=candidate,
            ).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base_slug}-{suffix}"
            self.slug = candidate

        super().save(*args, **kwargs)


class IntegrationCredentialEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="integration_credential_events",
        on_delete=models.CASCADE,
    )
    integration = models.ForeignKey(
        KnowledgeIntegration,
        related_name="credential_events",
        on_delete=models.CASCADE,
    )
    triggered_by = models.ForeignKey(
        User,
        related_name="integration_credential_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    event_type = models.CharField(max_length=32, choices=IntegrationCredentialEventType.choices)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_integration_credential_event"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["integration", "event_type"], name="integration_cred_evt_type_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.integration_id}:{self.event_type}"


class KnowledgeIngestionJob(models.Model):
    """
    Tracks asynchronous ingestion, syncing, and re-index tasks for knowledge uploads.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_jobs",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="ingestion_jobs",
        on_delete=models.CASCADE,
    )
    job_type = models.CharField(max_length=24, choices=KnowledgeIngestionJobType.choices)
    status = models.CharField(max_length=24, choices=KnowledgeIngestionJobStatus.choices, default=KnowledgeIngestionJobStatus.QUEUED)
    payload = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_ingestion_job"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="knowledge_job_status_idx"),
            models.Index(fields=["upload", "job_type"], name="knowledge_job_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_job_type_display()} for {self.upload}"


class KnowledgeDriftSample(models.Model):
    class SampleKind(models.TextChoices):
        INGESTION = "ingestion", "Ingestion"
        RETRIEVAL = "retrieval", "Retrieval"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="drift_samples",
        on_delete=models.CASCADE,
    )
    sample_kind = models.CharField(max_length=24, choices=SampleKind.choices)
    metrics = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_knowledge_drift_sample"
        ordering = ("-observed_at",)
        indexes = [
            models.Index(fields=["business_profile", "sample_kind", "observed_at"], name="knowledge_drift_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.business_profile_id}:{self.sample_kind} at {self.observed_at:%Y-%m-%d %H:%M}"


class RAGEvaluationRun(models.Model):
    class RunStatus(models.TextChoices):
        PASSED = "pass", "Pass"
        FAILED = "fail", "Fail"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="evaluation_runs",
        on_delete=models.CASCADE,
    )
    slug = models.CharField(max_length=64)
    status = models.CharField(max_length=8, choices=RunStatus.choices, default=RunStatus.PASSED)
    metrics = models.JSONField(default=dict, blank=True)
    latencies = models.JSONField(default=dict, blank=True)
    thresholds = models.JSONField(default=dict, blank=True)
    violations = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_rag_evaluation_run"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "slug"], name="rag_eval_business_slug_idx"),
            models.Index(fields=["status", "created_at"], name="rag_eval_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.slug} ({self.get_status_display()})"


class KnowledgeFeedbackCase(models.Model):
    class BehaviorChoices(models.TextChoices):
        ALIAS = "alias_exact", "Alias Path"
        HYBRID = "hybrid", "Hybrid Search"
        NOT_FOUND = "not_found", "Not Found"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="feedback_cases",
        on_delete=models.CASCADE,
    )
    conversation_feedback = models.OneToOneField(
        "conversations.ConversationFeedback",
        related_name="knowledge_case",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    query_text = models.TextField()
    expected_behavior = models.CharField(max_length=24, choices=BehaviorChoices.choices, default=BehaviorChoices.ALIAS)
    expected_entities = models.JSONField(default=list, blank=True)
    expected_aliases = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=32, default="feedback")
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_feedback_case"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "is_active"], name="knowledge_feedback_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.business_profile_id}:{self.query_text[:40]}"


class KnowledgeAuditEvent(models.Model):
    """
    Immutable log of key knowledge events for compliance and debugging.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_audit_events",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="audit_events",
        on_delete=models.CASCADE,
    )
    actor_user = models.ForeignKey(
        User,
        related_name="knowledge_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_agent = models.ForeignKey(
        AgentProfile,
        related_name="knowledge_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=32, choices=KnowledgeAuditAction.choices)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_knowledge_audit_event"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["upload", "action"], name="knowledge_audit_action_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.upload} - {self.get_action_display()}"


class AgentKnowledgeAccess(models.Model):
    """
    Through model that tracks explicit knowledge resources granted to an agent.

    Enables fine-grained permissions and auditing for document usage.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_profile = models.ForeignKey(
        "AgentProfile",
        related_name="knowledge_access_rules",
        on_delete=models.CASCADE,
    )
    knowledge_upload = models.ForeignKey(
        "KnowledgeUpload",
        related_name="agent_access_rules",
        on_delete=models.CASCADE,
    )
    granted_by = models.ForeignKey(
        User,
        related_name="agent_knowledge_grants",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional context about why access was granted.",
    )

    class Meta:
        db_table = "accounts_agent_knowledge_grant"
        ordering = ("-granted_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["agent_profile", "knowledge_upload"],
                name="agent_knowledge_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.agent_profile} -> {self.knowledge_upload}"
