from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, TypedDict

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from apps.accounts.credential_secrets import (
    IntegrationSecretError,
    credential_policy,
    credentials_are_stale,
    get_secret_manager,
)
from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    User,
)


logger = logging.getLogger(__name__)


class IntegrationColumnPrivacyConfig(TypedDict, total=False):
    shared_columns: list[str]
    internal_only_columns: list[str]
    excluded_columns: list[str]


class IntegrationResourceConfig(TypedDict, total=False):
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
    frequency: str
    timezone: str
    next_run_at: str | None
    last_run_at: str | None
    last_status: str
    last_duration_ms: int
    paused: bool


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


def default_knowledge_integration_settings() -> dict[str, Any]:
    return {
        "default_visibility": KnowledgeVisibility.PRIVATE,
        "default_sync_frequency": IntegrationSyncFrequency.DAILY,
        "resources": [],
    }


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


class EmailAccount(models.Model):
    """
    Represents a first-party email account connection (Google/Microsoft) for a tenant user.

    Privacy note: this model stores OAuth tokens encrypted at rest. Do not log raw
    credentials or full email contents in audit events.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="email_accounts",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        related_name="email_accounts",
        on_delete=models.CASCADE,
    )
    provider = models.CharField(
        max_length=16,
        choices=EmailAccountProvider.choices,
    )
    email_address = models.EmailField(max_length=255, blank=True, default="", db_index=True)
    external_account_id = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(
        max_length=16,
        choices=EmailAccountStatus.choices,
        default=EmailAccountStatus.DISCONNECTED,
    )
    send_mode = models.CharField(
        max_length=24,
        choices=EmailSendMode.choices,
        default=EmailSendMode.DRAFT_APPROVAL,
        help_text="Default send behavior for this connected mailbox.",
    )
    policy_config = models.JSONField(default=dict, blank=True)

    credentials_encrypted = models.TextField(blank=True, default="")
    credentials_key_version = models.PositiveSmallIntegerField(default=1)
    credentials_last_rotated_at = models.DateTimeField(null=True, blank=True)
    credential_error_count = models.PositiveSmallIntegerField(default=0)

    metadata = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_health_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_email_account"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "user"],
                name="email_account_unique_user",
            ),
        ]
        indexes = [
            models.Index(fields=["business_profile", "status"], name="email_acct_biz_status_idx"),
            models.Index(fields=["business_profile", "provider"], name="email_acct_biz_provider_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        label = self.email_address or self.external_account_id or "unlinked"
        return f"{label} ({self.provider})"

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
            logger.warning("email_account_credentials_decrypt_failed account=%s error=%s", self.id, exc)
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


class AgentEmailAccountPolicyOverride(models.Model):
    """
    Per-agent overrides for how email tools behave for a specific mailbox.

    This is used to support "Auto-send per connection" with an optional per-agent override.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_profile = models.ForeignKey(
        AgentProfile,
        related_name="email_policy_overrides",
        on_delete=models.CASCADE,
    )
    email_account = models.ForeignKey(
        EmailAccount,
        related_name="agent_policy_overrides",
        on_delete=models.CASCADE,
    )
    send_mode = models.CharField(
        max_length=24,
        choices=EmailSendMode.choices,
        null=True,
        blank=True,
        help_text="Override the email account default. NULL = inherit from EmailAccount.",
    )
    policy_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_agent_email_policy_override"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["agent_profile", "email_account"],
                name="agent_email_policy_override_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["agent_profile", "email_account"], name="agent_email_ov_agent_acct_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.email_account_id}"


class EmailAccountAuditEvent(models.Model):
    """
    Immutable log of key email connector events for compliance and debugging.

    IMPORTANT: Do not store raw email bodies or OAuth tokens in metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="email_audit_events",
        on_delete=models.CASCADE,
    )
    email_account = models.ForeignKey(
        EmailAccount,
        related_name="audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    email_account_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Snapshot of the EmailAccount UUID for retention when the account is deleted.",
    )
    actor_user = models.ForeignKey(
        User,
        related_name="email_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_agent = models.ForeignKey(
        AgentProfile,
        related_name="email_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=32, choices=EmailAccountAuditAction.choices)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_email_account_audit_event"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["email_account", "action"], name="email_audit_action_idx"),
            models.Index(fields=["email_account_id_snapshot", "action"], name="email_audit_snap_action_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        ref = self.email_account_id_snapshot or getattr(self.email_account, "id", None) or "unknown-email-account"
        return f"{ref} - {self.get_action_display()}"


class EmailAccountHealthJob(models.Model):
    """
    Background job to verify/refresh an EmailAccount OAuth connection.

    Used for production-friendly "health checks" (status updates) without relying
    on in-memory queues.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="email_account_health_jobs",
        on_delete=models.CASCADE,
    )
    email_account = models.ForeignKey(
        EmailAccount,
        related_name="health_jobs",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=24,
        choices=EmailAccountHealthJobStatus.choices,
        default=EmailAccountHealthJobStatus.QUEUED,
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
        db_table = "accounts_email_account_health_job"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "run_after"], name="email_health_run_after_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="email_health_lease_idx"),
            models.Index(fields=["email_account", "status"], name="email_health_acct_status_idx"),
            models.Index(fields=["business_profile", "status"], name="email_health_biz_status_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.email_account_id}:{self.get_status_display()}"

    def mark_cancelled(self, *, reason: str = "") -> None:
        now = timezone.now()
        self.status = EmailAccountHealthJobStatus.CANCELLED
        self.finished_at = now
        if reason:
            self.error_detail = (reason or "")[:500]
        self.lease_expires_at = None
        self.run_after = None
        self.save(update_fields=["status", "finished_at", "error_detail", "lease_expires_at", "run_after", "updated_at"])


class OAuthProvider(models.Model):
    """
    Stores OAuth app credentials for marketplace OAuth flows.

    This is a global configuration managed by staff/admin users and shared by all
    tenants. Client secrets are encrypted using the same credential manager as
    other integrations.
    """

    key = models.CharField(max_length=50, unique=True)  # e.g. "google", "slack"
    name = models.CharField(max_length=100)

    authorization_url = models.URLField(max_length=500)
    token_url = models.URLField(max_length=500)

    client_id = models.CharField(max_length=500)
    client_secret_encrypted = models.TextField(blank=True, default="")
    client_secret_key_version = models.PositiveSmallIntegerField(default=1)
    client_secret_last_rotated_at = models.DateTimeField(null=True, blank=True)
    client_secret_error_count = models.PositiveSmallIntegerField(default=0)

    scopes = models.JSONField(default=list, blank=True)
    marketplace_keys = models.JSONField(
        default=list,
        blank=True,
        help_text="Marketplace keys that should use this provider (e.g. ['gmail', 'google_drive']).",
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_oauth_provider"
        ordering = ("key",)

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.name} ({self.key})"

    def _secret_tenant(self) -> str:
        return f"oauth_provider:{self.key}"

    def get_client_secret(self) -> str:
        if not self.client_secret_encrypted:
            return ""
        manager = get_secret_manager()
        try:
            payload = manager.decrypt(self.client_secret_encrypted, tenant=self._secret_tenant())
        except IntegrationSecretError as exc:
            logger.warning("oauth_provider_secret_decrypt_failed provider=%s error=%s", self.key, exc)
            return ""
        secret = payload.get("client_secret") if isinstance(payload, dict) else None
        return str(secret or "")

    def set_client_secret(self, value: str | None) -> None:
        secret = str(value or "").strip()
        if not secret:
            self.client_secret_encrypted = ""
            self.client_secret_key_version = 1
            self.client_secret_last_rotated_at = None
            self.client_secret_error_count = 0
            return
        manager = get_secret_manager()
        ciphertext = manager.encrypt({"client_secret": secret}, tenant=self._secret_tenant())
        self.client_secret_encrypted = ciphertext
        self.client_secret_key_version = manager.key_version
        self.client_secret_last_rotated_at = timezone.now()
        self.client_secret_error_count = 0

    def supports_marketplace_key(self, marketplace_key: str) -> bool:
        candidate = str(marketplace_key or "").strip()
        if not candidate:
            return False
        keys = self.marketplace_keys or []
        return candidate in keys if isinstance(keys, list) else False


class OAuthState(models.Model):
    """Ephemeral state record for OAuth handshakes (CSRF protection)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="oauth_states",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        related_name="oauth_states",
        on_delete=models.CASCADE,
    )
    provider = models.ForeignKey(
        OAuthProvider,
        related_name="oauth_states",
        on_delete=models.CASCADE,
    )
    marketplace_key = models.CharField(max_length=100)
    state_token = models.CharField(max_length=128, unique=True)
    redirect_after = models.URLField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_oauth_state"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["expires_at"], name="oauth_state_expires_idx"),
            models.Index(fields=["provider", "is_used"], name="oauth_state_provider_used_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"OAuthState<{self.provider_id}:{self.marketplace_key}>"


class EmailOAuthState(models.Model):
    """Ephemeral state record for email connector OAuth handshakes (CSRF + PKCE)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="email_oauth_states",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        related_name="email_oauth_states",
        on_delete=models.CASCADE,
    )
    provider = models.ForeignKey(
        OAuthProvider,
        related_name="email_oauth_states",
        on_delete=models.CASCADE,
        help_text="OAuth provider configuration (e.g., google_email, microsoft_email).",
    )
    email_provider = models.CharField(max_length=16, choices=EmailAccountProvider.choices)
    state_token = models.CharField(max_length=128, unique=True)
    redirect_after = models.URLField(max_length=500, blank=True, default="")
    redirect_uri = models.URLField(max_length=500, blank=True, default="")
    code_verifier = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_email_oauth_state"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["expires_at"], name="email_oauth_exp_idx"),
            models.Index(fields=["provider", "is_used"], name="email_oauth_prov_used_idx"),
            models.Index(fields=["business_profile", "email_provider"], name="email_oauth_biz_prov_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"EmailOAuthState<{self.email_provider}:{self.provider_id}>"



class IntegrationAccount(models.Model):
    """
    Generic model for first-party native integrations (Calendar, Drive, OneDrive, Slack, HubSpot).

    Privacy note: OAuth tokens are encrypted at rest. Do not log raw credentials
    or full API responses in audit events.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="integration_accounts",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        related_name="integration_accounts",
        on_delete=models.CASCADE,
    )
    integration_type = models.CharField(
        max_length=32,
        choices=IntegrationType.choices,
    )
    provider = models.CharField(
        max_length=16,
        choices=IntegrationProvider.choices,
    )
    account_identifier = models.CharField(
        max_length=320,
        blank=True,
        default="",
        help_text="Email address, workspace name, or other human-readable identifier.",
    )
    external_account_id = models.CharField(max_length=320, blank=True, default="")

    status = models.CharField(
        max_length=16,
        choices=IntegrationAccountStatus.choices,
        default=IntegrationAccountStatus.DISCONNECTED,
    )

    credentials_encrypted = models.TextField(blank=True, default="")
    credentials_key_version = models.PositiveSmallIntegerField(default=1)
    credentials_last_rotated_at = models.DateTimeField(null=True, blank=True)
    credential_error_count = models.PositiveSmallIntegerField(default=0)

    metadata = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_health_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_integration_account"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "user", "integration_type"],
                name="integration_account_unique_user_type",
            ),
        ]
        indexes = [
            models.Index(fields=["business_profile", "status"], name="integ_acct_biz_status_idx"),
            models.Index(fields=["business_profile", "integration_type"], name="integ_acct_biz_type_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        label = self.account_identifier or self.external_account_id or "unlinked"
        return f"{label} ({self.integration_type})"

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
            logger.warning("integration_account_credentials_decrypt_failed account=%s error=%s", self.id, exc)
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


class IntegrationAccountAuditEvent(models.Model):
    """
    Immutable log of native integration events for compliance and debugging.

    IMPORTANT: Do not store raw API responses or OAuth tokens in metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="integration_audit_events",
        on_delete=models.CASCADE,
    )
    integration_account = models.ForeignKey(
        IntegrationAccount,
        related_name="audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    integration_account_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Snapshot of the IntegrationAccount UUID for retention when the account is deleted.",
    )
    actor_user = models.ForeignKey(
        User,
        related_name="integration_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_agent = models.ForeignKey(
        AgentProfile,
        related_name="integration_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=32, choices=IntegrationAccountAuditAction.choices)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_integration_account_audit_event"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["integration_account", "action"], name="integ_audit_action_idx"),
            models.Index(fields=["integration_account_id_snapshot", "action"], name="integ_audit_snap_action_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        ref = self.integration_account_id_snapshot or getattr(self.integration_account, "id", None) or "unknown"
        return f"{ref} - {self.get_action_display()}"


class IntegrationOAuthState(models.Model):
    """Ephemeral state record for native integration OAuth handshakes (CSRF + PKCE)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="integration_oauth_states",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        related_name="integration_oauth_states",
        on_delete=models.CASCADE,
    )
    provider = models.ForeignKey(
        OAuthProvider,
        related_name="integration_oauth_states",
        on_delete=models.CASCADE,
        help_text="OAuth provider configuration (e.g., google_calendar, slack_native).",
    )
    integration_type = models.CharField(max_length=32, choices=IntegrationType.choices)
    state_token = models.CharField(max_length=128, unique=True)
    redirect_after = models.URLField(max_length=500, blank=True, default="")
    redirect_uri = models.URLField(max_length=500, blank=True, default="")
    code_verifier = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_integration_oauth_state"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["expires_at"], name="integ_oauth_exp_idx"),
            models.Index(fields=["provider", "is_used"], name="integ_oauth_prov_used_idx"),
            models.Index(fields=["business_profile", "integration_type"], name="integ_oauth_biz_type_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"IntegrationOAuthState<{self.integration_type}:{self.provider_id}>"
