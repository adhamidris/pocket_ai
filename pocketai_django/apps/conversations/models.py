from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from pgvector.django import VectorField


def generate_session_token() -> str:
    """Return a URL-safe token to identify anonymous chat sessions."""

    return secrets.token_urlsafe(32)


class ConversationChannel(models.TextChoices):
    WEB_WIDGET = "web_widget", "Web Widget"
    WHATSAPP = "whatsapp", "WhatsApp"
    MESSENGER = "messenger", "Messenger"
    API = "api", "API"
    EMAIL = "email", "Email"
    OTHER = "other", "Other"


class ConversationStatus(models.TextChoices):
    NEW = "new", "New"
    LIVE = "live", "Live"
    RESOLVED = "resolved", "Resolved"
    ESCALATED = "escalated", "Escalated"
    CLOSED = "closed", "Closed"
    EXPIRED = "expired", "Expired"


class ConversationSender(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    AI = "ai", "AI"
    SYSTEM = "system", "System"


class ConversationExtractionType(models.TextChoices):
    CASE = "case", "Case"
    LEAD = "lead", "Lead"
    APPOINTMENT = "appointment", "Appointment"
    ESCALATION = "escalation", "Escalation"
    COMPLAINT = "complaint", "Complaint"
    NOTE = "note", "Note"


class Conversation(models.Model):
    """
    Represents a single visitor chat session.

    Each conversation is scoped to an agent profile and is referenced by a random
    session token that the public portal uses instead of authentication.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="conversations",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="conversations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        "customers.Customer",
        related_name="conversations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    case = models.ForeignKey(
        "cases.Case",
        related_name="conversations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    session_token = models.CharField(max_length=96, unique=True, db_index=True)
    channel = models.CharField(
        max_length=32,
        choices=ConversationChannel.choices,
        default=ConversationChannel.WEB_WIDGET,
    )
    status = models.CharField(
        max_length=24,
        choices=ConversationStatus.choices,
        default=ConversationStatus.NEW,
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    summary = models.TextField(blank=True)
    first_customer_message_at = models.DateTimeField(null=True, blank=True)
    first_ai_message_at = models.DateTimeField(null=True, blank=True)
    csat_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    csat_comment = models.TextField(blank=True)
    csat_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "conversations_conversation"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="conv_business_status_idx"),
            models.Index(fields=["business_profile", "last_activity_at"], name="conv_activity_idx"),
        ]

    def __str__(self) -> str:
        return f"Conversation<{self.session_token}>"

    def save(self, *args, **kwargs):
        if not self.session_token:
            token = generate_session_token()
            while Conversation.objects.filter(session_token=token).exists():
                token = generate_session_token()
            self.session_token = token
        self.last_activity_at = timezone.now()
        if self.status in {ConversationStatus.RESOLVED, ConversationStatus.CLOSED, ConversationStatus.EXPIRED} and not self.closed_at:
            self.closed_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        if self.status in {ConversationStatus.RESOLVED, ConversationStatus.CLOSED, ConversationStatus.EXPIRED}:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True


class ConversationMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        related_name="messages",
        on_delete=models.CASCADE,
    )
    sender = models.CharField(max_length=16, choices=ConversationSender.choices)
    body = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    content_blocks = models.JSONField(default=list, blank=True)
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations_conversation_message"
        ordering = ("sent_at", "created_at")
        indexes = [
            models.Index(fields=["conversation", "sent_at"], name="conv_message_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.conversation_id}:{self.sender}"


class CompactedHistorySegment(models.Model):
    """
    Stores compacted conversation segments for on-demand retrieval.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        related_name="compacted_segments",
        on_delete=models.CASCADE,
    )
    segment_range = models.CharField(max_length=64)
    start_message_id = models.UUIDField()
    end_message_id = models.UUIDField()
    start_message_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    end_message_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    summary = models.TextField()
    full_messages = models.JSONField()
    embedding = VectorField(dimensions=settings.EMBED_DIM, null=True, blank=True)
    extracted_facts = models.JSONField(default=dict, blank=True)
    extracted_decisions = models.JSONField(default=dict, blank=True)
    token_count_original = models.IntegerField()
    token_count_summary = models.IntegerField()
    compression_ratio = models.FloatField()
    compacted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations_compacted_history_segment"
        ordering = ("-compacted_at",)
        indexes = [
            models.Index(fields=["conversation", "compacted_at"], name="conv_compacted_at_idx"),
        ]
        constraints = [
            # Guard against duplicate inserts when multiple workers attempt to compact the same range.
            models.UniqueConstraint(
                fields=["conversation", "start_message_id", "end_message_id"],
                name="uniq_compacted_history_segment_bounds",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.conversation_id}:{self.segment_range}"


class ConversationMaintenanceJobKind(models.TextChoices):
    COMPACT_HISTORY = "compact_history", "Compact History"


class ConversationMaintenanceJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class ConversationMaintenanceJob(models.Model):
    """
    DB-backed queue for background conversation maintenance tasks.

    Phase 6: used to run compaction/embedding out-of-band (no inline threads).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="conversation_maintenance_jobs",
        on_delete=models.CASCADE,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="maintenance_jobs",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(
        max_length=64,
        choices=ConversationMaintenanceJobKind.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=24,
        choices=ConversationMaintenanceJobStatus.choices,
        default=ConversationMaintenanceJobStatus.QUEUED,
        db_index=True,
    )
    run_after = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=10)
    error_detail = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_conversation_maintenance_job"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="job_biz_status_idx"),
            models.Index(fields=["status", "run_after"], name="job_status_run_after_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="job_status_lease_idx"),
            models.Index(fields=["conversation", "created_at"], name="job_conv_created_idx"),
        ]
        constraints = [
            # Idempotency: never enqueue more than one active maintenance job of the same kind
            # for a conversation. (Succeeded/failed jobs don't block new work.)
            models.UniqueConstraint(
                fields=["conversation", "kind"],
                condition=Q(status__in=[ConversationMaintenanceJobStatus.QUEUED, ConversationMaintenanceJobStatus.RUNNING]),
                name="uniq_active_conversation_maintenance_job",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        super().save(*args, **kwargs)


class ConversationToolApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DENIED = "denied", "Denied"
    EXPIRED = "expired", "Expired"


class ConversationToolApproval(models.Model):
    """
    Stores approval state for MCP tool calls that require explicit confirmation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        related_name="tool_approvals",
        on_delete=models.CASCADE,
    )
    turn = models.ForeignKey(
        "PortalTurn",
        related_name="turn_approvals",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    connection = models.ForeignKey(
        "accounts.McpConnection",
        related_name="tool_approvals",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    tool_name = models.CharField(max_length=128)
    remote_tool_name = models.CharField(max_length=128, blank=True, default="")
    tool_call_id = models.CharField(max_length=128, blank=True, default="")
    event_id = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=ConversationToolApprovalStatus.choices,
        default=ConversationToolApprovalStatus.PENDING,
    )
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    input_payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_conversation_tool_approval"
        ordering = ("-requested_at",)
        indexes = [
            models.Index(fields=["conversation", "status"], name="conv_tool_approval_status_idx"),
            models.Index(fields=["connection", "status"], name="conv_tool_approval_conn_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.conversation_id}:{self.tool_name}:{self.status}"


class PortalTurnStatus(models.TextChoices):
    STREAMING = "streaming", "Streaming"
    WAITING_APPROVAL = "waiting_approval", "Waiting approval"
    FINALIZING = "finalizing", "Finalizing"
    FINALIZED = "finalized", "Finalized"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class PortalTurn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        related_name="portal_turns",
        on_delete=models.CASCADE,
    )
    message = models.ForeignKey(
        ConversationMessage,
        related_name="portal_turns",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    user_message = models.TextField(blank=True, default="")
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="portal_turns",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=24,
        choices=PortalTurnStatus.choices,
        default=PortalTurnStatus.STREAMING,
    )
    last_event_seq = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    run_after = models.DateTimeField(default=timezone.now, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    error_detail = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_portal_turn"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["conversation", "status"], name="portal_turn_conv_status_idx"),
            models.Index(fields=["status", "run_after"], name="portal_turn_status_run_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="portal_turn_status_lease_idx"),
            models.Index(fields=["conversation", "created_at"], name="portal_turn_conv_created_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.conversation_id}:{self.status}"


class PortalTurnEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    turn = models.ForeignKey(
        PortalTurn,
        related_name="events",
        on_delete=models.CASCADE,
    )
    seq = models.PositiveIntegerField()
    type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations_portal_turn_event"
        ordering = ("seq",)
        indexes = [
            models.Index(fields=["turn", "created_at"], name="portal_turn_event_time_idx"),
            models.Index(fields=["turn", "type"], name="portal_turn_event_type_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["turn", "seq"], name="portal_turn_event_seq_unique"),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.turn_id}:{self.type}:{self.seq}"


class ConversationExtraction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        related_name="extractions",
        on_delete=models.CASCADE,
    )
    extraction_type = models.CharField(max_length=24, choices=ConversationExtractionType.choices)
    reference_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations_conversation_extraction"
        indexes = [
            models.Index(fields=["conversation", "extraction_type"], name="conv_extract_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.conversation_id}:{self.extraction_type}"


class ConversationFeedback(models.Model):
    class FeedbackType(models.TextChoices):
        NOT_FOUND_INCORRECT = "not_found_incorrect", "Not Found Incorrect"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        related_name="feedback_entries",
        on_delete=models.CASCADE,
    )
    message = models.ForeignKey(
        ConversationMessage,
        related_name="feedback_entries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    feedback_type = models.CharField(max_length=32, choices=FeedbackType.choices)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations_conversation_feedback"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["conversation", "feedback_type"], name="conv_feedback_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.conversation_id}:{self.feedback_type}"


class IdentifierEvent(models.Model):
    """
    Observability record for identifier gating decisions during retrieval.
    Stores hashed identifiers only; raw values are never persisted.
    """

    STATUS_CHOICES = (
        ("ok", "OK"),
        ("identifier_required", "Identifier Required"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="identifier_events",
        on_delete=models.CASCADE,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="identifier_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    upload_id = models.UUIDField(null=True, blank=True)
    tool = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="ok")
    match_policy = models.CharField(max_length=8, default="or")
    required_keys = models.JSONField(default=list, blank=True)
    provided_keys = models.JSONField(default=list, blank=True)
    provided_hashes = models.JSONField(default=dict, blank=True)
    blocked_uploads = models.JSONField(default=list, blank=True)
    missing_by_upload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations_identifier_event"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="identifier_event_status_idx"),
            models.Index(fields=["business_profile", "created_at"], name="identifier_event_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.business_profile_id}:{self.status}:{self.tool}"


class ConversationFileKind(models.TextChoices):
    UPLOAD = "upload", "Upload"
    ARTIFACT = "artifact", "Artifact"


class ConversationFileStatus(models.TextChoices):
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class ConversationFile(models.Model):
    """
    Conversation-scoped files for the public chat portal.

    This model intentionally stays separate from the business-wide knowledge base
    so visitor-provided documents do not become globally retrievable across tenants
    or across unrelated conversations by default.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="conversation_files",
        on_delete=models.CASCADE,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="files",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(
        max_length=24,
        choices=ConversationFileKind.choices,
        default=ConversationFileKind.UPLOAD,
    )
    status = models.CharField(
        max_length=24,
        choices=ConversationFileStatus.choices,
        default=ConversationFileStatus.PROCESSING,
    )
    sender = models.CharField(
        max_length=16,
        choices=ConversationSender.choices,
        default=ConversationSender.CUSTOMER,
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True, default="")
    storage_path = models.CharField(max_length=512)
    size_bytes = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    checksum_sha256 = models.CharField(max_length=128, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_conversation_file"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "created_at"], name="conv_file_biz_created_idx"),
            models.Index(fields=["conversation", "created_at"], name="conv_file_conv_created_idx"),
            models.Index(fields=["conversation", "kind"], name="conv_file_conv_kind_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.conversation_id}:{self.filename}"


class ConversationFileChunk(models.Model):
    """
    Chunked extracted text for conversation files (PDFs, etc.) for retrieval.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="conversation_file_chunks",
        on_delete=models.CASCADE,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="file_chunks",
        on_delete=models.CASCADE,
    )
    conversation_file = models.ForeignKey(
        ConversationFile,
        related_name="chunks",
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
        db_table = "conversations_conversation_file_chunk"
        ordering = ("conversation_file_id", "chunk_index")
        indexes = [
            models.Index(fields=["conversation_file", "chunk_index"], name="conv_file_chunk_window_idx"),
            models.Index(fields=["conversation", "chunk_index"], name="conv_file_chunk_conv_idx"),
            models.Index(fields=["business_profile", "conversation"], name="conv_file_chunk_biz_conv_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation_file", "chunk_index"],
                name="conv_file_chunk_unique_index",
            )
        ]

    def save(self, *args, **kwargs):
        if self.conversation_file_id:
            if not self.conversation_id and getattr(self, "conversation_file", None):
                self.conversation = self.conversation_file.conversation
            if not self.business_profile_id and getattr(self, "conversation_file", None):
                self.business_profile = self.conversation_file.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.conversation_file_id}:{self.chunk_index}"


class AgentRunVisibility(models.TextChoices):
    """
    High-level visibility control for agent runs.

    Teams/hierarchy is deferred, so these map to workspace-level defaults for now.
    """

    INITIATOR = "initiator", "Initiator"
    MANAGERS = "managers", "Managers"
    WORKSPACE = "workspace", "Workspace"


class AgentRunSpecStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class AgentRunSpec(models.Model):
    """
    Run specification/template owned by a tenant/agent.

    This represents the *contract* for a background run:
    goal, success criteria, tool allowlist, constraints, output schema, approval requirements, and visibility.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_run_specs",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="run_specs",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_run_specs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=160)
    status = models.CharField(
        max_length=24,
        choices=AgentRunSpecStatus.choices,
        default=AgentRunSpecStatus.DRAFT,
    )
    visibility = models.CharField(
        max_length=24,
        choices=AgentRunVisibility.choices,
        default=AgentRunVisibility.INITIATOR,
    )
    spec = models.JSONField(
        default=dict,
        blank=True,
        help_text="Serialized RunSpec payload (goal, tools, constraints, output schema, approvals).",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_agent_run_spec"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="run_spec_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="run_spec_agent_status_idx"),
            models.Index(fields=["business_profile", "created_at"], name="run_spec_biz_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["agent_profile", "name"],
                name="run_spec_unique_agent_name",
            )
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - human readable only
        return f"{self.agent_profile_id}:{self.name}"


class AgentRunSource(models.TextChoices):
    CHAT = "chat", "Chat"
    AUTOMATION = "automation", "Automation"
    WATCHER = "watcher", "Watcher"
    API = "api", "API"


class AgentRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    WAITING_USER = "waiting_user", "Waiting for user"
    WAITING_APPROVAL = "waiting_approval", "Waiting for approval"
    WAITING_EXTERNAL = "waiting_external", "Waiting for external"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class AgentRun(models.Model):
    """
    A single background execution of a RunSpec.

    Runs may be spawned from a chat request, an automation trigger, a watcher, or the API.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_runs",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="runs",
        on_delete=models.CASCADE,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="agent_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional anchor conversation (chat-originated run or automation thread).",
    )
    execution_conversation = models.ForeignKey(
        Conversation,
        related_name="execution_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Isolated execution conversation for this run.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_agent_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run_spec = models.ForeignKey(
        AgentRunSpec,
        related_name="runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run_spec_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Immutable RunSpec snapshot used for this run (copied from run_spec at creation time).",
    )
    title = models.CharField(max_length=200, blank=True, default="")
    source = models.CharField(
        max_length=24,
        choices=AgentRunSource.choices,
        default=AgentRunSource.CHAT,
    )
    status = models.CharField(
        max_length=24,
        choices=AgentRunStatus.choices,
        default=AgentRunStatus.QUEUED,
        db_index=True,
    )
    visibility = models.CharField(
        max_length=24,
        choices=AgentRunVisibility.choices,
        default=AgentRunVisibility.INITIATOR,
    )
    plan = models.JSONField(
        default=dict,
        blank=True,
        help_text="Planner output (intended steps). This is separate from executed logs.",
    )
    result = models.JSONField(
        default=dict,
        blank=True,
        help_text="Final structured result payload (if any).",
    )
    metadata = models.JSONField(default=dict, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    run_after = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_agent_run"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="run_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="run_agent_status_idx"),
            models.Index(fields=["status", "run_after"], name="run_status_run_after_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="run_status_lease_idx"),
            models.Index(fields=["conversation", "created_at"], name="run_conv_created_idx"),
            models.Index(fields=["business_profile", "created_at"], name="run_biz_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.status}:{self.id}"


class AgentRunEventStream(models.TextChoices):
    PLAN = "plan", "Plan"
    EXECUTED = "executed", "Executed"
    SYSTEM = "system", "System"


class AgentRunEventType(models.TextChoices):
    PROGRESS = "progress", "Progress"
    NEEDS_USER = "needs_user", "Needs user"
    NEEDS_APPROVAL = "needs_approval", "Needs approval"
    RESULT = "result", "Result"
    ERROR = "error", "Error"
    PAUSED = "paused", "Paused"
    CANCELLED = "cancelled", "Cancelled"


class AgentRunEvent(models.Model):
    """
    Append-only event log for a run.

    Truthfulness invariant: "plan" stream describes intent; "executed" stream is the source of truth for actions taken.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        related_name="events",
        on_delete=models.CASCADE,
    )
    sequence_index = models.PositiveIntegerField(help_text="Monotonic per-run ordering for deterministic playback.")
    stream = models.CharField(
        max_length=16,
        choices=AgentRunEventStream.choices,
        default=AgentRunEventStream.SYSTEM,
    )
    event_type = models.CharField(
        max_length=24,
        choices=AgentRunEventType.choices,
    )
    label = models.CharField(max_length=240, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "conversations_agent_run_event"
        ordering = ("run_id", "sequence_index")
        indexes = [
            models.Index(fields=["run", "sequence_index"], name="run_event_order_idx"),
            models.Index(fields=["run", "created_at"], name="run_event_created_idx"),
            models.Index(fields=["run", "event_type"], name="run_event_type_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence_index"], name="run_event_unique_sequence"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.run_id}:{self.stream}:{self.event_type}:{self.sequence_index}"


class AgentRunArtifactKind(models.TextChoices):
    FILE = "file", "File"
    LINK = "link", "Link"
    RECORD = "record", "Record"
    MESSAGE = "message", "Message"


class AgentRunArtifact(models.Model):
    """
    Reference to a run output (file/artifact, link, or created/updated record).

    Storage is intentionally "pointer-like" so large payloads remain out-of-band.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        related_name="artifacts",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(
        max_length=24,
        choices=AgentRunArtifactKind.choices,
        default=AgentRunArtifactKind.FILE,
    )
    label = models.CharField(max_length=200, blank=True, default="")
    conversation_file = models.ForeignKey(
        ConversationFile,
        related_name="agent_run_artifacts",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    url = models.URLField(blank=True, default="")
    reference_type = models.CharField(max_length=64, blank=True, default="")
    reference_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "conversations_agent_run_artifact"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["run", "created_at"], name="run_artifact_created_idx"),
            models.Index(fields=["run", "kind"], name="run_artifact_kind_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.run_id}:{self.kind}:{self.id}"


class AgentRunMemoryKind(models.TextChoices):
    FACT = "fact", "Fact"
    SOP = "sop", "SOP"
    DECISION = "decision", "Decision"
    NOTE = "note", "Note"
    # New kinds for context optimization (Phase 2)
    EXTRACTED_DATA = "extracted_data", "Extracted Data"
    WORKFLOW_STATE = "workflow_state", "Workflow State"
    CONTEXT_SNAPSHOT = "context_snapshot", "Context Snapshot"


class AgentRunMemoryItem(models.Model):
    """
    Structured memory entries emitted during a run (facts, SOP steps, decisions).

    Raw tool dumps should be stored as artifacts or in executed logs, not as memory items.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        related_name="memory_items",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=24, choices=AgentRunMemoryKind.choices)
    key = models.CharField(max_length=160, blank=True, default="", db_index=True)
    content = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_agent_run_memory_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "conversations_agent_run_memory_item"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["run", "created_at"], name="run_memory_created_idx"),
            models.Index(fields=["run", "kind"], name="run_memory_kind_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.run_id}:{self.kind}:{self.id}"


class AgentRequestStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    RESOLVED = "resolved", "Resolved"


class AgentRequest(models.Model):
    """
    An agent-to-agent request (Agent A -> Agent B) with structured context references.

    V1: Designed to be single-user compatible (A/B can be the same agent profile) while
    remaining ready for future team hierarchies and permissioning.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_requests",
        on_delete=models.CASCADE,
    )
    from_agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="agent_requests_sent",
        on_delete=models.CASCADE,
    )
    to_agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="agent_requests_received",
        on_delete=models.CASCADE,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="agent_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional anchor conversation for context and traceability.",
    )
    agent_run = models.ForeignKey(
        AgentRun,
        related_name="agent_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional originating AgentRun (for background tasks that need another agent).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_agent_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=24,
        choices=AgentRequestStatus.choices,
        default=AgentRequestStatus.OPEN,
        db_index=True,
    )
    subject = models.CharField(max_length=240, blank=True, default="")
    question = models.TextField(blank=True, default="")
    context_refs = models.JSONField(
        default=list,
        blank=True,
        help_text="Structured context references (no raw dumps).",
    )
    resolution = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_agent_request"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="agent_request_biz_status_idx"),
            models.Index(fields=["to_agent_profile", "status"], name="agent_request_to_status_idx"),
            models.Index(fields=["from_agent_profile", "created_at"], name="agent_request_from_created_idx"),
            models.Index(fields=["business_profile", "created_at"], name="agent_request_biz_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.from_agent_profile_id and not self.business_profile_id and getattr(self, "from_agent_profile", None):
            self.business_profile = self.from_agent_profile.business_profile
        if self.to_agent_profile_id and not self.business_profile_id and getattr(self, "to_agent_profile", None):
            self.business_profile = self.to_agent_profile.business_profile
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        if self.agent_run_id and not self.business_profile_id and getattr(self, "agent_run", None):
            self.business_profile = self.agent_run.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.from_agent_profile_id}->{self.to_agent_profile_id}:{self.status}:{self.id}"


class AgentAutomationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ARCHIVED = "archived", "Archived"


class AgentAutomationTriggerType(models.TextChoices):
    CRON = "cron", "Cron"
    WEBHOOK = "webhook", "Webhook"
    MANUAL = "manual", "Manual"


class AgentAutomation(models.Model):
    """
    A tenant-owned automation that spawns AgentRuns based on a trigger configuration.

    Scheduling/execution is handled by a worker loop; this model captures persistence + UI/API wiring.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_automations",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="automations",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_agent_automations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run_spec = models.ForeignKey(
        AgentRunSpec,
        related_name="automations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run_spec_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Immutable RunSpec snapshot used when spawning runs from this automation.",
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="agent_automations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional automation thread conversation used as the default destination for results.",
    )
    name = models.CharField(max_length=160)
    status = models.CharField(
        max_length=24,
        choices=AgentAutomationStatus.choices,
        default=AgentAutomationStatus.DRAFT,
        db_index=True,
    )
    visibility = models.CharField(
        max_length=24,
        choices=AgentRunVisibility.choices,
        default=AgentRunVisibility.INITIATOR,
    )
    trigger_type = models.CharField(
        max_length=24,
        choices=AgentAutomationTriggerType.choices,
        default=AgentAutomationTriggerType.CRON,
    )
    trigger_config = models.JSONField(default=dict, blank=True)
    destination_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Destination policy (automation thread, notifications) for spawned runs.",
    )
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    next_trigger_at = models.DateTimeField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_agent_automation"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="automation_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="automation_agent_status_idx"),
            models.Index(fields=["status", "next_trigger_at"], name="automation_status_next_idx"),
            models.Index(fields=["business_profile", "created_at"], name="automation_biz_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["agent_profile", "name"], name="automation_unique_agent_name"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.status}:{self.name}"


class AgentWatcherStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ARCHIVED = "archived", "Archived"


class AgentWatcherType(models.TextChoices):
    EMAIL_INBOX = "email_inbox", "Email inbox"


class AgentWatcher(models.Model):
    """
    A tenant-owned watcher that polls an external system and spawns AgentRuns.

    V1 scope is email inbox polling. Webhook/push watchers will be added later.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_watchers",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="watchers",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_agent_watchers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run_spec = models.ForeignKey(
        AgentRunSpec,
        related_name="watchers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run_spec_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Immutable RunSpec snapshot used when spawning runs from this watcher.",
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="agent_watchers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional destination conversation used as the default thread for watcher runs.",
    )
    email_account = models.ForeignKey(
        "accounts.EmailAccount",
        related_name="agent_watchers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Email account used by email-based watchers.",
    )
    name = models.CharField(max_length=160)
    status = models.CharField(
        max_length=24,
        choices=AgentWatcherStatus.choices,
        default=AgentWatcherStatus.DRAFT,
        db_index=True,
    )
    visibility = models.CharField(
        max_length=24,
        choices=AgentRunVisibility.choices,
        default=AgentRunVisibility.INITIATOR,
    )
    watcher_type = models.CharField(
        max_length=24,
        choices=AgentWatcherType.choices,
        default=AgentWatcherType.EMAIL_INBOX,
    )
    watch_config = models.JSONField(default=dict, blank=True)
    destination_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Destination policy (watcher thread, notifications) for spawned runs.",
    )
    poll_interval_seconds = models.PositiveIntegerField(default=300, help_text="Minimum seconds between polls.")
    max_events_per_poll = models.PositiveSmallIntegerField(default=5)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    next_poll_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_agent_watcher"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="watcher_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="watcher_agent_status_idx"),
            models.Index(fields=["status", "next_poll_at"], name="watcher_status_next_poll_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="watcher_status_lease_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["agent_profile", "name"], name="watcher_unique_agent_name"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        if self.email_account_id and not self.business_profile_id and getattr(self, "email_account", None):
            self.business_profile = self.email_account.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.status}:{self.name}"


class AgentWatcherDedupeKey(models.Model):
    """
    Dedupe keys for watcher-triggered events to prevent duplicate runs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_watcher_dedupe_keys",
        on_delete=models.CASCADE,
    )
    watcher = models.ForeignKey(
        AgentWatcher,
        related_name="dedupe_keys",
        on_delete=models.CASCADE,
    )
    dedupe_key = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "conversations_agent_watcher_dedupe_key"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["watcher", "created_at"], name="watcher_dedupe_created_idx"),
            models.Index(fields=["business_profile", "created_at"], name="watcher_dedupe_biz_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["watcher", "dedupe_key"], name="watcher_dedupe_unique_key"),
        ]

    def save(self, *args, **kwargs):
        if self.watcher_id and not self.business_profile_id and getattr(self, "watcher", None):
            self.business_profile = self.watcher.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.watcher_id}:{self.dedupe_key}"
