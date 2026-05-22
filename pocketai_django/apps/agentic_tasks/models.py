from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.agent_runs.models import AgentRunVisibility


class AgenticTaskStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ARCHIVED = "archived", "Archived"


class AgenticTaskReviewMode(models.TextChoices):
    NONE = "none", "None"
    ON_RISK = "on_risk", "On risk"
    ALWAYS = "always", "Always"


class AgenticTaskAutonomyMode(models.TextChoices):
    SUGGEST_ONLY = "suggest_only", "Suggest only"
    DRAFT_FOR_APPROVAL = "draft_for_approval", "Draft for approval"
    AUTONOMOUS_WITH_POLICY = "autonomous_with_policy", "Autonomous with policy"
    FULL_AUTONOMY_EXPLICIT = "full_autonomy_explicit", "Full autonomy explicit"


class AgenticTask(models.Model):
    """Persistent task agent that can be run manually or by schedule."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="agentic_tasks",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="agentic_tasks",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_agentic_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    active_conversation = models.ForeignKey(
        "conversations.Conversation",
        related_name="agentic_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Current visible task session for manual and scheduled runs.",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=24, choices=AgenticTaskStatus.choices, default=AgenticTaskStatus.DRAFT, db_index=True)
    visibility = models.CharField(max_length=24, choices=AgentRunVisibility.choices, default=AgentRunVisibility.INITIATOR)
    schedule_enabled = models.BooleanField(default=False, db_index=True)
    schedule_config = models.JSONField(default=dict, blank=True)
    review_mode = models.CharField(max_length=24, choices=AgenticTaskReviewMode.choices, default=AgenticTaskReviewMode.ON_RISK, db_index=True)
    autonomy_mode = models.CharField(
        max_length=32,
        choices=AgenticTaskAutonomyMode.choices,
        default=AgenticTaskAutonomyMode.DRAFT_FOR_APPROVAL,
        db_index=True,
    )
    instructions = models.JSONField(default=dict, blank=True)
    state = models.JSONField(default=dict, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    next_trigger_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agentic_tasks_task"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="task_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="task_agent_status_idx"),
            models.Index(fields=["status", "schedule_enabled", "next_trigger_at"], name="task_due_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="task_lease_idx"),
            models.Index(fields=["business_profile", "created_at"], name="task_biz_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["agent_profile", "name"], name="task_unique_agent_name"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        if self.active_conversation_id and not self.business_profile_id and getattr(self, "active_conversation", None):
            self.business_profile = self.active_conversation.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.status}:{self.name}"
