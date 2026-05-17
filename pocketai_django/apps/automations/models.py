from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.agent_runs.models import AgentRunVisibility


class AutomationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"


class AutomationTriggerType(models.TextChoices):
    SCHEDULE = "schedule", "Schedule"
    WEBHOOK = "webhook", "Webhook"
    EMAIL_INBOX = "email_inbox", "Email inbox"


class AutomationReviewMode(models.TextChoices):
    NONE = "none", "None"
    ON_RISK = "on_risk", "On risk"
    ALWAYS = "always", "Always"


class AutomationAutonomyMode(models.TextChoices):
    SUGGEST_ONLY = "suggest_only", "Suggest only"
    DRAFT_FOR_APPROVAL = "draft_for_approval", "Draft for approval"
    AUTONOMOUS_WITH_POLICY = "autonomous_with_policy", "Autonomous with policy"
    FULL_AUTONOMY_EXPLICIT = "full_autonomy_explicit", "Full autonomy explicit"


class Automation(models.Model):
    """Runnable autonomous/background automation configuration."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="automations",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="automations",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_automations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        related_name="automations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional destination conversation for automation run results.",
    )
    email_account = models.ForeignKey(
        "integrations.EmailAccount",
        related_name="automations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Email account used by email-inbox automations.",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=24, choices=AutomationStatus.choices, default=AutomationStatus.DRAFT, db_index=True)
    visibility = models.CharField(max_length=24, choices=AgentRunVisibility.choices, default=AgentRunVisibility.INITIATOR)
    trigger_type = models.CharField(max_length=24, choices=AutomationTriggerType.choices, db_index=True)
    trigger_config = models.JSONField(default=dict, blank=True)
    source_config = models.JSONField(default=dict, blank=True)
    destination_config = models.JSONField(default=dict, blank=True)
    notification_config = models.JSONField(default=dict, blank=True)
    review_mode = models.CharField(max_length=24, choices=AutomationReviewMode.choices, default=AutomationReviewMode.ON_RISK, db_index=True)
    autonomy_mode = models.CharField(
        max_length=32,
        choices=AutomationAutonomyMode.choices,
        default=AutomationAutonomyMode.DRAFT_FOR_APPROVAL,
        db_index=True,
    )
    instructions = models.JSONField(default=dict, blank=True)
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
        db_table = "automations_automation"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="auto_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="auto_agent_status_idx"),
            models.Index(fields=["status", "trigger_type", "next_trigger_at"], name="auto_due_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="auto_lease_idx"),
            models.Index(fields=["business_profile", "created_at"], name="auto_biz_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["agent_profile", "name"], name="auto_unique_agent_name"),
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


class AutomationDedupeKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="automation_dedupe_keys",
        on_delete=models.CASCADE,
    )
    automation = models.ForeignKey(
        Automation,
        related_name="dedupe_keys",
        on_delete=models.CASCADE,
    )
    dedupe_key = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "automations_dedupe_key"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["automation", "created_at"], name="auto_dedupe_created_idx"),
            models.Index(fields=["business_profile", "created_at"], name="auto_dedupe_biz_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["automation", "dedupe_key"], name="auto_dedupe_unique_key"),
        ]

    def save(self, *args, **kwargs):
        if self.automation_id and not self.business_profile_id and getattr(self, "automation", None):
            self.business_profile = self.automation.business_profile
        super().save(*args, **kwargs)
