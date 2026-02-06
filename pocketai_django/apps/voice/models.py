from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.accounts.credential_secrets import (
    IntegrationSecretError,
    credentials_are_stale,
    get_secret_manager,
)


class CallType(models.TextChoices):
    SERVICE = "service", "Service"
    MARKETING = "marketing", "Marketing"


class CallDirection(models.TextChoices):
    OUTBOUND = "outbound", "Outbound"


class CallStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    INITIATING = "initiating", "Initiating"
    RINGING = "ringing", "Ringing"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class VoiceTrustTier(models.TextChoices):
    TRIAL = "trial", "Trial"
    VERIFIED = "verified", "Verified"
    ENTERPRISE = "enterprise", "Enterprise"


class VoiceConfiguration(models.Model):
    """
    Workspace-level voice configuration.

    Owner-controlled "hard caps" are enforced in services; this model is a tenant
    configuration surface that can be further bounded by global settings.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.OneToOneField(
        "accounts.BusinessProfile",
        related_name="voice_configuration",
        on_delete=models.CASCADE,
    )

    trust_tier = models.CharField(max_length=24, choices=VoiceTrustTier.choices, default=VoiceTrustTier.TRIAL)
    service_calls_enabled = models.BooleanField(default=False)
    marketing_calls_enabled = models.BooleanField(default=False)

    # Country allow-list (ISO 3166-1 alpha-2), used after auto-detecting from E.164.
    allowed_countries = models.JSONField(default=list, blank=True)

    # Guardrails (tenant-level; owner may enforce stricter caps).
    max_concurrent_calls = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    max_calls_per_day = models.PositiveIntegerField(default=10, validators=[MinValueValidator(0)])
    max_call_duration_seconds = models.PositiveIntegerField(default=600, validators=[MinValueValidator(30)])

    # Budgeting: enforcement uses cost_estimate + month-to-date spend.
    monthly_budget_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    default_language = models.CharField(max_length=8, default="en")

    # AI disclosure is mandatory everywhere; copy defaults from settings if blank.
    ai_disclosure_template = models.TextField(blank=True, default="")
    ai_disclosure_template_ar = models.TextField(blank=True, default="")
    # Recording consent collection is mandatory everywhere for now.
    recording_consent_required = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"VoiceConfiguration<{self.business_profile_id}>"


class VoiceProviderConnection(models.Model):
    """
    Workspace-level provider credentials for the voice engine.

    Credentials are encrypted at rest and tenant-scoped.
    """

    class Provider(models.TextChoices):
        TWILIO = "twilio", "Twilio"
        DEEPGRAM = "deepgram", "Deepgram"
        ELEVENLABS = "elevenlabs", "ElevenLabs"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="voice_provider_connections",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="voice_provider_connections",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    provider = models.CharField(max_length=24, choices=Provider.choices)
    enabled = models.BooleanField(default=False)

    credentials_encrypted = models.TextField(blank=True, default="")
    credentials_key_version = models.PositiveSmallIntegerField(default=1)
    credentials_last_rotated_at = models.DateTimeField(null=True, blank=True)
    credential_error_count = models.PositiveSmallIntegerField(default=0)

    last_tested_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "voice_provider_connection"
        ordering = ("provider",)
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "provider"],
                name="voice_provider_business_provider_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["business_profile", "provider"], name="voice_voice_busines_a317f6_idx"),
            models.Index(fields=["business_profile", "enabled"], name="voice_voice_busines_1ec414_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"VoiceProviderConnection<{self.business_profile_id}:{self.provider}>"

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
        except IntegrationSecretError:
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

    def clear_credentials(self) -> None:
        self.credentials_encrypted = ""
        self.credentials_key_version = 1
        self.credentials_last_rotated_at = None
        self.credential_error_count = 0
        self._cache_credentials({})

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._clear_cached_credentials()


class VoiceCountryPolicy(models.Model):
    """
    Owner-defined country policy module.

    This is the "Phase 3 compliance engine" configuration surface.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    country = models.CharField(max_length=2, unique=True, db_index=True)  # ISO 3166-1 alpha-2
    timezone = models.CharField(max_length=64, blank=True, default="")  # IANA TZ (e.g., Africa/Cairo)

    is_active = models.BooleanField(default=True)

    service_calls_allowed = models.BooleanField(default=True)
    marketing_calls_allowed = models.BooleanField(default=False)

    # Recording + consent rules.
    recording_allowed = models.BooleanField(default=True)
    recording_consent_required = models.BooleanField(default=True)

    # AI disclosure is mandatory, but keep a flag for future flexibility.
    ai_disclosure_required = models.BooleanField(default=True)

    # Optional country-local call window enforcement (empty/null = no restriction).
    allowed_weekdays = models.JSONField(default=list, blank=True)  # [0..6] Monday=0
    allowed_call_time_start = models.TimeField(null=True, blank=True)
    allowed_call_time_end = models.TimeField(null=True, blank=True)

    policy_config = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"VoiceCountryPolicy<{self.country}>"


class VoiceCallAuditAction(models.TextChoices):
    POLICY_EVALUATED = "policy_evaluated", "Policy Evaluated"
    POLICY_BLOCKED = "policy_blocked", "Policy Blocked"
    DISCLOSURE_REQUIRED = "disclosure_required", "Disclosure Required"
    CONSENT_REQUIRED = "consent_required", "Consent Required"


class VoiceCallAuditEvent(models.Model):
    """
    Immutable log of key voice compliance events for audit/debugging.

    Avoid storing raw call audio/transcripts in metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="voice_call_audit_events",
        on_delete=models.CASCADE,
    )
    call_session = models.ForeignKey(
        "voice.CallSession",
        related_name="audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    call_session_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Snapshot of the CallSession UUID for retention when the session is deleted.",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="voice_call_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_agent = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="voice_call_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=32, choices=VoiceCallAuditAction.choices)
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["call_session", "action"]),
            models.Index(fields=["call_session_id_snapshot", "action"]),
            models.Index(fields=["business_profile", "occurred_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.call_session_id and not self.call_session_id_snapshot:
            try:
                self.call_session_id_snapshot = self.call_session_id
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        ref = self.call_session_id_snapshot or self.call_session_id or "unknown-call"
        return f"{ref} - {self.get_action_display()}"


class VoicePhoneNumber(models.Model):
    """
    Outbound caller identity per workspace.

    For Phase 1 we keep Twilio as the only provider, but model this as a
    provider mapping so BYOC/local carriers can be added later.
    """

    class Provider(models.TextChoices):
        TWILIO = "twilio", "Twilio"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"
        PENDING = "pending", "Pending"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="voice_phone_numbers",
        on_delete=models.CASCADE,
    )
    provider = models.CharField(max_length=24, choices=Provider.choices, default=Provider.TWILIO)
    phone_number = models.CharField(max_length=32)  # E.164
    provider_sid = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["business_profile", "status"]),
            models.Index(fields=["business_profile", "phone_number"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"VoicePhoneNumber<{self.business_profile_id}:{self.phone_number}>"


class VoiceSuppressionEntry(models.Model):
    """
    Workspace-level suppression list (e.g., "don't call me again").
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="voice_suppression_entries",
        on_delete=models.CASCADE,
    )
    phone_number = models.CharField(max_length=32)  # E.164
    reason = models.CharField(max_length=240, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_voice_suppression_entries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business_profile", "phone_number"], name="voice_suppress_unique_number"),
        ]
        indexes = [
            models.Index(fields=["business_profile", "created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"VoiceSuppressionEntry<{self.business_profile_id}:{self.phone_number}>"


class CallSession(models.Model):
    """
    Call session representing a single outbound phone call.

    Phase 0 shipped a spike implementation; Phase 1 adds queue/worker fields,
    tenant scoping, and artifacts for monitoring + post-call processing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Tenant / linkage
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="voice_call_sessions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="voice_call_sessions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_voice_call_sessions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    initiating_conversation = models.ForeignKey(
        "conversations.Conversation",
        related_name="initiated_voice_calls",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    execution_conversation = models.ForeignKey(
        "conversations.Conversation",
        related_name="voice_call_transcripts",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    voice_phone_number = models.ForeignKey(
        "voice.VoicePhoneNumber",
        related_name="call_sessions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    # Intent
    objective = models.TextField()
    call_type = models.CharField(max_length=32, choices=CallType.choices, default=CallType.SERVICE, db_index=True)
    direction = models.CharField(max_length=16, choices=CallDirection.choices, default=CallDirection.OUTBOUND)
    language = models.CharField(max_length=8, default="en")
    country = models.CharField(max_length=2, blank=True, default="", db_index=True)  # ISO 3166-1 alpha-2
    context_items = models.JSONField(default=list, blank=True)
    max_duration_seconds = models.PositiveIntegerField(default=600, validators=[MinValueValidator(30)])

    # Phone numbers
    to_phone_number = models.CharField(max_length=32)
    from_phone_number = models.CharField(max_length=32, blank=True, default="")

    # Provider state (Twilio v1)
    twilio_call_sid = models.CharField(max_length=64, blank=True, default="", db_index=True)
    twilio_stream_sid = models.CharField(max_length=64, blank=True, default="", db_index=True)

    status = models.CharField(max_length=32, choices=CallStatus.choices, default=CallStatus.QUEUED, db_index=True)
    last_error = models.TextField(blank=True, default="")

    # Queue/worker coordination (DB-backed queue).
    queued_at = models.DateTimeField(default=timezone.now, db_index=True)
    run_after = models.DateTimeField(null=True, blank=True, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Mandatory recording consent (DTMF) before recording starts.
    consent_obtained = models.BooleanField(default=False, db_index=True)
    consent_obtained_at = models.DateTimeField(null=True, blank=True)
    consent_method = models.CharField(max_length=16, blank=True, default="")  # dtmf

    # WebSocket authentication token (included in Stream URL).
    stream_token = models.CharField(max_length=128, blank=True, default="", db_index=True)

    # Recording references (provider first; copied to R2 in later phases)
    recording_sid = models.CharField(max_length=64, blank=True, default="", db_index=True)
    recording_url = models.TextField(blank=True, default="")
    recording_r2_bucket = models.CharField(max_length=128, blank=True, default="")
    recording_r2_key = models.CharField(max_length=512, blank=True, default="", db_index=True)
    recording_r2_etag = models.CharField(max_length=128, blank=True, default="")

    # Costs (estimated at queue time, finalized post-call).
    cost_estimate_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    cost_total_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))

    # Post-call artifacts.
    summary = models.TextField(blank=True, default="")
    action_items = models.JSONField(default=list, blank=True)
    # Structured post-call insights (versioned schema).
    insights = models.JSONField(default=dict, blank=True)

    # Post-call processing status.
    post_processed = models.BooleanField(default=False, db_index=True)
    post_processed_at = models.DateTimeField(null=True, blank=True)
    post_processing_error = models.TextField(blank=True, default="")

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"CallSession({self.id}) to={self.to_phone_number} status={self.status}"


class CallEvent(models.Model):
    """
    Append-only call event log for the spike.

    We keep payloads structured so later phases can drive SSE/UI reliably.
    """

    id = models.BigAutoField(primary_key=True)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="voice_call_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    call_session = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["call_session", "created_at"]),
            models.Index(fields=["call_session", "event_type"]),
            models.Index(fields=["business_profile", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.call_session_id and not self.business_profile_id:
            try:
                self.business_profile_id = self.call_session.business_profile_id
            except Exception:
                pass
        super().save(*args, **kwargs)
