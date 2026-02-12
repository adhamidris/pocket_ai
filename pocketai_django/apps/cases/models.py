from __future__ import annotations

import uuid
from typing import Any

from django.db import models
from django.utils import timezone

from apps.accounts.models import AgentProfile, BusinessProfile
from apps.knowledge.models import KnowledgeUpload


class CasePriority(models.TextChoices):
    """Supported urgency levels for a case."""

    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class CaseStatus(models.TextChoices):
    """Lifecycle state for a case."""

    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


def generate_case_number() -> str:
    """
    Produce a short, human-friendly case identifier.

    Format: CAS-YYYYMMDD-XXXXXX (X is uppercase hex). Collisions are unlikely but
    guarded against during save by re-generating until the number is unique.
    """

    today_fragment = timezone.now().strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"CAS-{today_fragment}-{suffix}"


class Case(models.Model):
    """
    Represents a business case created by the AI agent during a customer interaction.

    The model stores AI-generated context (diagnosis, actions, suggestions) alongside
    manual fields (priority, status) so humans can triage or resolve the case.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case_number = models.CharField(max_length=24, unique=True, editable=False, db_index=True)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="cases",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        AgentProfile,
        related_name="cases",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    customer = models.ForeignKey(
        "customers.Customer",
        related_name="cases",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Direct link to the customer if the interaction could be attributed.",
    )
    # External linkage remains for legacy systems or while placeholder records are resolved.
    customer_reference = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="External identifier for cross-system linkage or legacy records.",
    )
    customer_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Immutable customer details captured at creation time for quick access.",
    )
    title = models.CharField(max_length=255, help_text="Short, human-friendly summary of the case.")
    description = models.TextField(help_text="AI generated case brief describing the situation.")
    priority = models.CharField(max_length=16, choices=CasePriority.choices, default=CasePriority.MEDIUM)
    status = models.CharField(max_length=16, choices=CaseStatus.choices, default=CaseStatus.OPEN)
    ai_diagnosis = models.TextField(blank=True, help_text="AI generated problem analysis.")
    ai_actions_taken = models.TextField(blank=True, help_text="Actions executed automatically by the AI agent.")
    ai_suggested_actions = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of recommendations for human follow-up.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional case metadata such as classification or routing hints.",
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "cases_case"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="case_business_status_idx"),
            models.Index(fields=["customer", "status"], name="case_customer_status_idx"),
            models.Index(fields=["priority", "status"], name="case_priority_status_idx"),
            models.Index(fields=["started_at"], name="case_started_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.case_number}: {self.title}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.case_number:
            self.case_number = self._generate_unique_case_number()

        if self.status == CaseStatus.CLOSED and self.closed_at is None:
            self.closed_at = timezone.now()
        elif self.status != CaseStatus.CLOSED and self.closed_at is not None:
            # Case re-opened; clear the closed timestamp so SLA tracking remains accurate.
            self.closed_at = None

        super().save(*args, **kwargs)

    def _generate_unique_case_number(self) -> str:
        case_cls = self.__class__
        candidate = generate_case_number()
        while case_cls.objects.filter(case_number=candidate).exists():
            candidate = generate_case_number()
        return candidate


class CaseHistoryEntry(models.Model):
    """
    Captures a summarized update for a case, typically one per chat session or follow-up.

    Provides a chronological timeline for agents to review how the issue evolved.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, related_name="history_entries", on_delete=models.CASCADE)
    session_reference = models.CharField(
        max_length=64,
        blank=True,
        help_text="Identifier linking to the underlying chat session or transcript.",
    )
    summary = models.TextField(help_text="Single-line description of the change or update.")
    source = models.CharField(
        max_length=32,
        choices=(
            ("ai", "AI"),
            ("customer", "Customer"),
            ("agent", "Agent"),
            ("system", "System"),
        ),
        default="ai",
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cases_case_history_entry"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["case", "occurred_at"], name="case_history_case_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.case.case_number} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class CaseMessage(models.Model):
    """
    Stores the conversational flow between the AI agent and the customer for a case.

    Messages persist the verbatim dialogue for auditability, quality review, and re-training.
    """

    class Sender(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        AGENT = "agent", "Agent"
        AI = "ai", "AI"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, related_name="messages", on_delete=models.CASCADE)
    session_reference = models.CharField(
        max_length=64,
        blank=True,
        help_text="Identifier linking to the conversation session the message belongs to.",
    )
    sender = models.CharField(max_length=16, choices=Sender.choices)
    sender_display_name = models.CharField(max_length=120, blank=True)
    content = models.TextField()
    content_type = models.CharField(
        max_length=32,
        default="text",
        help_text="Mime-like hint about the content (e.g., text, markdown, html).",
    )
    metadata = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cases_case_message"
        ordering = ("sent_at", "created_at")
        indexes = [
            models.Index(fields=["case", "sent_at"], name="case_message_case_time_idx"),
            models.Index(fields=["session_reference", "sent_at"], name="case_message_session_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.case.case_number} [{self.sender}]: {self.content[:40]}"


class CaseDocumentLink(models.Model):
    """
    Associates uploaded or shared documents to a case.

    A direct ForeignKey to a dedicated Document model will replace the external reference
    once that application is introduced.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, related_name="document_links", on_delete=models.CASCADE)
    knowledge_upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="case_document_links",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Optional reference when the document originates from an existing knowledge upload.",
    )
    external_document_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="Identifier pointing to the future documents service or storage record.",
    )
    name = models.CharField(max_length=255, blank=True)
    document_url = models.URLField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    captured_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cases_case_document_link"
        ordering = ("-captured_at",)
        indexes = [
            models.Index(fields=["case", "captured_at"], name="case_document_case_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.case.case_number}:{self.name or self.external_document_id}"
