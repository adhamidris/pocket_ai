from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


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
