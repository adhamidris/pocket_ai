from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from .managers import UserManager


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

    def save(self, *args, **kwargs):
        self.ensure_slug()
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


class KnowledgeCollectionVisibility(models.TextChoices):
    PRIVATE = "private", "Private"
    AGENTS = "agents", "Agents Only"
    BUSINESS = "business", "Entire Business"


class KnowledgeIngestionJobType(models.TextChoices):
    INGEST = "ingest", "Initial Ingest"
    REBUILD = "rebuild", "Rebuild"
    DELETE = "delete", "Delete"
    SYNC = "sync", "Sync"


class KnowledgeIngestionJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
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


class KnowledgeUpload(models.Model):
    """
    Central knowledge artifact powering the AI agent experience.

    Supports uploads, URLs, manual snippets, and integration-sourced content while
    tracking ingestion state, access metadata, and collection membership.
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
    source_uid = models.CharField(max_length=255, blank=True, default="")
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
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    embedding = models.JSONField(default=list, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_chunk"
        ordering = ("upload_id", "chunk_index")
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "chunk_index"],
                name="knowledge_chunk_unique_index",
            )
        ]

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} for {self.upload_id}"


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
    """
    Represents a third-party source synced into the knowledge base (e.g., Notion).
    """

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
    credentials = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
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
