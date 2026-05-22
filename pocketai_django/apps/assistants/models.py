from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class CustomAssistantStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class CustomAssistant(models.Model):
    """
    Chat-first assistant configuration.

    Custom Assistants own reusable instructions/persona for chat sessions. They
    are not runnable background agentic_tasks and never own AgentRun records.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        "accounts.BusinessProfile",
        related_name="custom_assistants",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="custom_assistants",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_custom_assistants",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=24,
        choices=CustomAssistantStatus.choices,
        default=CustomAssistantStatus.DRAFT,
        db_index=True,
    )
    instructions = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assistants_custom_assistant"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="asst_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="asst_agent_status_idx"),
            models.Index(fields=["business_profile", "created_at"], name="asst_biz_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["agent_profile", "name"], name="asst_unique_agent_name"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent_profile_id}:{self.status}:{self.name}"
