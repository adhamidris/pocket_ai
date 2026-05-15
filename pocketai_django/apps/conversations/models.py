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
    Represents a single chat session owned by an authenticated workspace user.

    `session_token` is retained for transport continuity with the existing portal
    streaming/event contract, but ownership is modeled explicitly through
    `owner_user` so history and authorization can be backend-driven.
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
    workflow = models.ForeignKey(
        "conversations.AssistantWorkflow",
        related_name="sessions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional Custom Assistant this chat session belongs to.",
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_conversations",
        on_delete=models.CASCADE,
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
            models.Index(
                fields=["owner_user", "business_profile", "last_activity_at"],
                name="conv_owner_biz_activity_idx",
            ),
            models.Index(fields=["workflow", "last_activity_at"], name="conv_workflow_activity_idx"),
        ]

    def __str__(self) -> str:
        return f"Conversation<{self.session_token}>"

    def save(self, *args, **kwargs):
        if not self.session_token:
            token = generate_session_token()
            while Conversation.objects.filter(session_token=token).exists():
                token = generate_session_token()
            self.session_token = token
        if not self.owner_user_id and self.business_profile_id:
            business_user_id = None
            business = getattr(self, "business_profile", None)
            if business is not None and getattr(business, "id", None) == self.business_profile_id:
                business_user_id = getattr(business, "user_id", None)
            if not business_user_id:
                business_user_id = (
                    Conversation.objects.filter(pk=self.pk)
                    .values_list("owner_user_id", flat=True)
                    .first()
                )
            if not business_user_id:
                from apps.accounts.models import BusinessProfile

                business_user_id = (
                    BusinessProfile.objects.filter(pk=self.business_profile_id)
                    .values_list("user_id", flat=True)
                    .first()
                )
            self.owner_user_id = business_user_id
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
        "mcp.McpConnection",
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
    INITIATOR = "initiator", "Initiator"
    MANAGERS = "managers", "Managers"
    WORKSPACE = "workspace", "Workspace"


class AssistantWorkflowStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"


class AssistantWorkflowTriggerType(models.TextChoices):
    MANUAL = "manual", "Manual"
    SCHEDULE = "schedule", "Schedule"
    WEBHOOK = "webhook", "Webhook"
    EMAIL_INBOX = "email_inbox", "Email inbox"


class AssistantWorkflowKind(models.TextChoices):
    CUSTOM_ASSISTANT = "custom_assistant", "Custom assistant"
    AUTOMATION = "automation", "Automation"


class AssistantWorkflowReviewMode(models.TextChoices):
    NONE = "none", "None"
    ON_RISK = "on_risk", "On risk"
    ALWAYS = "always", "Always"


class AssistantWorkflowAutonomyMode(models.TextChoices):
    SUGGEST_ONLY = "suggest_only", "Suggest only"
    DRAFT_FOR_APPROVAL = "draft_for_approval", "Draft for approval"
    AUTONOMOUS_WITH_POLICY = "autonomous_with_policy", "Autonomous with policy"
    FULL_AUTONOMY_EXPLICIT = "full_autonomy_explicit", "Full autonomy explicit"


class AssistantWorkflow(models.Model):
    """
    Stored definition for Custom Assistants and Automations owned by the default Business Assistant.

    Manual trigger rows are Custom Assistants. Scheduled, webhook, and email
    trigger rows are Automations. Every background execution is an AgentRun
    with an immutable workflow_snapshot.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="assistant_workflows",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="assistant_workflows",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_assistant_workflows",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="assistant_workflows",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional destination conversation for automation run results.",
    )
    email_account = models.ForeignKey(
        "integrations.EmailAccount",
        related_name="assistant_workflows",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Email account used by email-inbox automations.",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=24, choices=AssistantWorkflowStatus.choices, default=AssistantWorkflowStatus.DRAFT, db_index=True)
    visibility = models.CharField(max_length=24, choices=AgentRunVisibility.choices, default=AgentRunVisibility.INITIATOR)
    kind = models.CharField(
        max_length=32,
        choices=AssistantWorkflowKind.choices,
        default=AssistantWorkflowKind.CUSTOM_ASSISTANT,
        db_index=True,
    )
    trigger_type = models.CharField(
        max_length=24,
        choices=AssistantWorkflowTriggerType.choices,
        default=AssistantWorkflowTriggerType.MANUAL,
        db_index=True,
    )
    trigger_config = models.JSONField(default=dict, blank=True)
    source_config = models.JSONField(default=dict, blank=True)
    destination_config = models.JSONField(default=dict, blank=True)
    notification_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Delivery preferences for workflow notifications. Ownership stays separate from delivery.",
    )
    review_mode = models.CharField(
        max_length=24,
        choices=AssistantWorkflowReviewMode.choices,
        default=AssistantWorkflowReviewMode.ON_RISK,
        db_index=True,
    )
    autonomy_mode = models.CharField(
        max_length=32,
        choices=AssistantWorkflowAutonomyMode.choices,
        default=AssistantWorkflowAutonomyMode.DRAFT_FOR_APPROVAL,
        db_index=True,
    )
    instructions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Assistant/automation contract: goal, success criteria, tool allowlist, constraints, output preferences.",
    )
    state = models.JSONField(default=dict, blank=True)
    poll_interval_seconds = models.PositiveIntegerField(default=300)
    max_events_per_poll = models.PositiveSmallIntegerField(default=5)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    next_trigger_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_assistant_workflow"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="asst_wf_biz_status_idx"),
            models.Index(fields=["business_profile", "kind", "status"], name="asst_wf_biz_kind_idx"),
            models.Index(fields=["agent_profile", "status"], name="asst_wf_agent_status_idx"),
            models.Index(fields=["status", "trigger_type", "next_trigger_at"], name="asst_wf_due_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="asst_wf_lease_idx"),
            models.Index(fields=["business_profile", "created_at"], name="asst_wf_biz_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["agent_profile", "name"], name="asst_wf_unique_agent_name"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        if self.email_account_id and not self.business_profile_id and getattr(self, "email_account", None):
            self.business_profile = self.email_account.business_profile
        expected_kind = (
            AssistantWorkflowKind.CUSTOM_ASSISTANT
            if self.trigger_type == AssistantWorkflowTriggerType.MANUAL
            else AssistantWorkflowKind.AUTOMATION
        )
        if self.kind != expected_kind:
            self.kind = expected_kind
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = sorted({*update_fields, "kind"})
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.status}:{self.name}"


class AssistantWorkflowDedupeKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="assistant_workflow_dedupe_keys",
        on_delete=models.CASCADE,
    )
    workflow = models.ForeignKey(
        AssistantWorkflow,
        related_name="dedupe_keys",
        on_delete=models.CASCADE,
    )
    dedupe_key = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "conversations_assistant_workflow_dedupe_key"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["workflow", "created_at"], name="asst_wf_dedupe_created_idx"),
            models.Index(fields=["business_profile", "created_at"], name="asst_wf_dedupe_biz_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["workflow", "dedupe_key"], name="asst_wf_dedupe_unique_key"),
        ]

    def save(self, *args, **kwargs):
        if self.workflow_id and not self.business_profile_id and getattr(self, "workflow", None):
            self.business_profile = self.workflow.business_profile
        super().save(*args, **kwargs)


class AgentRunSource(models.TextChoices):
    CHAT = "chat", "Chat"
    WORKFLOW = "workflow", "Workflow"
    SCHEDULE = "schedule", "Schedule"
    WEBHOOK = "webhook", "Webhook"
    EMAIL_INBOX = "email_inbox", "Email inbox"
    DELEGATION = "delegation", "Delegation"
    API = "api", "API"


class AgentRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    WAITING_USER = "waiting_user", "Waiting for user"
    WAITING_APPROVAL = "waiting_approval", "Waiting for approval"
    WAITING_CHILD = "waiting_child", "Waiting for child run"
    WAITING_EXTERNAL = "waiting_external", "Waiting for external"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class AgentRun(models.Model):
    """
    A single background execution owned by an agent.

    Runs may be spawned from chat, workflows, schedules, webhooks, email inbox triggers, delegation, or the API.
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
        help_text="Optional anchor conversation (chat-originated run or workflow thread).",
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
    workflow = models.ForeignKey(
        AssistantWorkflow,
        related_name="runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    parent_run = models.ForeignKey(
        "self",
        related_name="child_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    delegated_by_agent = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="delegated_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    workflow_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Immutable AssistantWorkflow snapshot used for this run.",
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
            models.Index(fields=["workflow", "created_at"], name="run_workflow_created_idx"),
            models.Index(fields=["parent_run", "created_at"], name="run_parent_created_idx"),
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
    NEEDS_CHILD = "needs_child", "Needs child run"
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


class AgentRunCheckpointKind(models.TextChoices):
    APPROVAL = "approval", "Approval"
    USER_INPUT = "user_input", "User input"
    CHILD_RUN = "child_run", "Child run"
    EXTERNAL = "external", "External"


class AgentRunCheckpointStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class AgentRunCheckpoint(models.Model):
    """
    Structured pause/resume point for Custom Assistant and Automation runs.

    Checkpoints are the portal-facing source of truth for approvals, user input,
    child-run waits, and external waits. The private execution conversation keeps
    the scratchpad; this row keeps the operational state queryable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_run_checkpoints",
        on_delete=models.CASCADE,
    )
    workflow = models.ForeignKey(
        AssistantWorkflow,
        related_name="checkpoints",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run = models.ForeignKey(
        AgentRun,
        related_name="checkpoints",
        on_delete=models.CASCADE,
    )
    conversation = models.ForeignKey(
        Conversation,
        related_name="agent_run_checkpoints",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    child_run = models.ForeignKey(
        AgentRun,
        related_name="parent_checkpoints",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=24, choices=AgentRunCheckpointKind.choices, db_index=True)
    status = models.CharField(
        max_length=24,
        choices=AgentRunCheckpointStatus.choices,
        default=AgentRunCheckpointStatus.OPEN,
        db_index=True,
    )
    title = models.CharField(max_length=240, blank=True, default="")
    prompt = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    resolution = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_agent_run_checkpoints",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="resolved_agent_run_checkpoints",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_agent_run_checkpoint"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status", "updated_at"], name="checkpoint_biz_status_idx"),
            models.Index(fields=["workflow", "status", "updated_at"], name="checkpoint_wf_status_idx"),
            models.Index(fields=["run", "status", "created_at"], name="checkpoint_run_status_idx"),
            models.Index(fields=["status", "expires_at"], name="checkpoint_expiry_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.run_id and not self.business_profile_id and getattr(self, "run", None):
            self.business_profile = self.run.business_profile
        if self.run_id and not self.workflow_id and getattr(self, "run", None):
            self.workflow = self.run.workflow
        if self.run_id and not self.conversation_id and getattr(self, "run", None):
            self.conversation = self.run.conversation
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.run_id}:{self.kind}:{self.status}"


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


class AgentRunNotificationStatus(models.TextChoices):
    CANDIDATE = "candidate", "Candidate"
    DELIVERED = "delivered", "Delivered"
    SUPPRESSED = "suppressed", "Suppressed"
    FAILED = "failed", "Failed"


class AgentRunNotification(models.Model):
    """
    Unified notification candidate/delivery record for background work.

    Runs produce candidates; routing decides whether and where to deliver them.
    This keeps execution scratchpads from becoming direct user-notification surfaces.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agent_run_notifications",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="agent_run_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    owner_agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="owned_run_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    workflow = models.ForeignKey(AssistantWorkflow, related_name="notifications", on_delete=models.SET_NULL, null=True, blank=True)
    run = models.ForeignKey(AgentRun, related_name="notifications", on_delete=models.CASCADE)
    target_conversation = models.ForeignKey(
        Conversation,
        related_name="agent_run_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=24,
        choices=AgentRunNotificationStatus.choices,
        default=AgentRunNotificationStatus.CANDIDATE,
        db_index=True,
    )
    kind = models.CharField(max_length=48, default="run_update", db_index=True)
    priority = models.CharField(max_length=24, default="normal", db_index=True)
    title = models.CharField(max_length=240, blank=True, default="")
    body = models.TextField(blank=True, default="")
    dedupe_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_agent_run_notification"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status", "created_at"], name="run_notif_biz_status_idx"),
            models.Index(fields=["workflow", "created_at"], name="run_notif_workflow_idx"),
            models.Index(fields=["run", "created_at"], name="run_notif_run_idx"),
            models.Index(fields=["target_conversation", "created_at"], name="run_notif_target_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.run_id and not self.business_profile_id and getattr(self, "run", None):
            self.business_profile = self.run.business_profile
        if self.run_id and not self.agent_profile_id and getattr(self, "run", None):
            self.agent_profile = self.run.agent_profile
        if self.workflow_id and not self.business_profile_id and getattr(self, "workflow", None):
            self.business_profile = self.workflow.business_profile
        super().save(*args, **kwargs)


class MemoryScope(models.TextChoices):
    WORKSPACE = "workspace", "Workspace"
    AGENT = "agent", "Agent"
    WORKFLOW = "workflow", "Workflow"
    RUN = "run", "Run"
    CONVERSATION = "conversation", "Conversation"
    CRM_CONTACT = "crm_contact", "CRM contact"
    CRM_COMPANY = "crm_company", "CRM company"


class MemoryKind(models.TextChoices):
    FACT = "fact", "Fact"
    PREFERENCE = "preference", "Preference"
    POLICY = "policy", "Policy"
    DECISION = "decision", "Decision"
    INSTRUCTION = "instruction", "Instruction"
    RELATIONSHIP = "relationship", "Relationship"
    STATE_NOTE = "state_note", "State note"
    ARTIFACT_REF = "artifact_ref", "Artifact reference"
    EXTRACTED_DATA = "extracted_data", "Extracted data"


class MemoryStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PENDING_REVIEW = "pending_review", "Pending review"
    ARCHIVED = "archived", "Archived"
    DELETED = "deleted", "Deleted"


class MemoryVisibility(models.TextChoices):
    PRIVATE = "private", "Private"
    SHARED = "shared", "Shared"


class MemorySensitivity(models.TextChoices):
    NORMAL = "normal", "Normal"
    SENSITIVE = "sensitive", "Sensitive"
    SECRET = "secret", "Secret"


class MemoryItem(models.Model):
    """
    Unified long-term memory record.

    Conversation compaction stays in CompactedHistorySegment; this model stores
    scoped durable facts, preferences, policies, decisions, and workflow state.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="memory_items",
        on_delete=models.CASCADE,
    )
    scope = models.CharField(max_length=32, choices=MemoryScope.choices, default=MemoryScope.WORKSPACE, db_index=True)
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="memory_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    workflow = models.ForeignKey(AssistantWorkflow, related_name="memory_items", on_delete=models.SET_NULL, null=True, blank=True)
    run = models.ForeignKey(AgentRun, related_name="memory_items", on_delete=models.SET_NULL, null=True, blank=True)
    conversation = models.ForeignKey(Conversation, related_name="memory_items", on_delete=models.SET_NULL, null=True, blank=True)
    crm_contact = models.ForeignKey("crm.CrmContact", related_name="memory_items", on_delete=models.SET_NULL, null=True, blank=True)
    crm_company = models.ForeignKey("crm.CrmCompany", related_name="memory_items", on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=32, choices=MemoryKind.choices, default=MemoryKind.FACT, db_index=True)
    key = models.CharField(max_length=160, blank=True, default="", db_index=True)
    content = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    visibility = models.CharField(max_length=16, choices=MemoryVisibility.choices, default=MemoryVisibility.SHARED, db_index=True)
    sensitivity = models.CharField(max_length=16, choices=MemorySensitivity.choices, default=MemorySensitivity.NORMAL, db_index=True)
    status = models.CharField(max_length=24, choices=MemoryStatus.choices, default=MemoryStatus.ACTIVE, db_index=True)
    source_type = models.CharField(max_length=64, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)
    confidence = models.FloatField(default=1.0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_memory_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_memory_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversations_memory_item"
        ordering = ("-updated_at", "-created_at")
        indexes = [
            models.Index(fields=["business_profile", "status", "visibility"], name="mem_biz_status_vis_idx"),
            models.Index(fields=["business_profile", "scope", "status"], name="memory_biz_scope_status_idx"),
            models.Index(fields=["agent_profile", "status", "updated_at"], name="memory_agent_status_time_idx"),
            models.Index(fields=["workflow", "status", "updated_at"], name="mem_wf_status_time_idx"),
            models.Index(fields=["run", "created_at"], name="memory_run_created_idx"),
            models.Index(fields=["conversation", "created_at"], name="memory_conv_created_idx"),
            models.Index(fields=["crm_contact", "status"], name="memory_contact_status_idx"),
            models.Index(fields=["crm_company", "status"], name="memory_company_status_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        if self.workflow_id and not self.business_profile_id and getattr(self, "workflow", None):
            self.business_profile = self.workflow.business_profile
        if self.run_id and not self.business_profile_id and getattr(self, "run", None):
            self.business_profile = self.run.business_profile
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.business_profile_id}:{self.scope}:{self.kind}:{self.id}"


class MemoryAuditAction(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    ARCHIVED = "archived", "Archived"
    DELETED = "deleted", "Deleted"
    PROMOTED = "promoted", "Promoted"


class MemoryAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    memory_item = models.ForeignKey(MemoryItem, related_name="audit_events", on_delete=models.CASCADE)
    business_profile = models.ForeignKey("accounts.BusinessProfile", related_name="memory_audit_events", on_delete=models.CASCADE)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="memory_audit_events", on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=32, choices=MemoryAuditAction.choices)
    description = models.TextField(blank=True, default="")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations_memory_audit_event"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["business_profile", "occurred_at"], name="memory_audit_biz_time_idx"),
            models.Index(fields=["memory_item", "occurred_at"], name="memory_audit_item_time_idx"),
        ]


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
