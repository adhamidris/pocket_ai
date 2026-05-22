from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class AgentRunVisibility(models.TextChoices):
    INITIATOR = "initiator", "Initiator"
    MANAGERS = "managers", "Managers"
    WORKSPACE = "workspace", "Workspace"


class AgentRunSource(models.TextChoices):
    CHAT = "chat", "Chat"
    AUTOMATION = "automation", "Automation"
    SCHEDULE = "schedule", "Schedule"
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
    """A single background execution owned by an agent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey("accounts.BusinessProfile", related_name="agent_runs", on_delete=models.CASCADE)
    agent_profile = models.ForeignKey("accounts.AgentProfile", related_name="runs", on_delete=models.CASCADE)
    conversation = models.ForeignKey(
        "conversations.Conversation",
        related_name="agent_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional anchor conversation for chat-originated or automation-associated runs.",
    )
    execution_conversation = models.ForeignKey(
        "conversations.Conversation",
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
    automation = models.ForeignKey(
        "automations.Automation",
        related_name="runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    parent_run = models.ForeignKey("self", related_name="child_runs", on_delete=models.SET_NULL, null=True, blank=True)
    delegated_by_agent = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="delegated_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    run_snapshot = models.JSONField(default=dict, blank=True, help_text="Immutable execution contract snapshot used for this run.")
    title = models.CharField(max_length=200, blank=True, default="")
    source = models.CharField(max_length=24, choices=AgentRunSource.choices, default=AgentRunSource.CHAT)
    status = models.CharField(max_length=24, choices=AgentRunStatus.choices, default=AgentRunStatus.QUEUED, db_index=True)
    visibility = models.CharField(max_length=24, choices=AgentRunVisibility.choices, default=AgentRunVisibility.INITIATOR)
    plan = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
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
        db_table = "agent_runs_agent_run"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="run_biz_status_idx"),
            models.Index(fields=["agent_profile", "status"], name="run_agent_status_idx"),
            models.Index(fields=["status", "run_after"], name="run_status_run_after_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="run_status_lease_idx"),
            models.Index(fields=["conversation", "created_at"], name="run_conv_created_idx"),
            models.Index(fields=["business_profile", "created_at"], name="run_biz_created_idx"),
            models.Index(fields=["automation", "created_at"], name="run_automation_created_idx"),
            models.Index(fields=["parent_run", "created_at"], name="run_parent_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.agent_profile_id and not self.business_profile_id and getattr(self, "agent_profile", None):
            self.business_profile = self.agent_profile.business_profile
        if self.conversation_id and not self.business_profile_id and getattr(self, "conversation", None):
            self.business_profile = self.conversation.business_profile
        if self.automation_id and not self.business_profile_id and getattr(self, "automation", None):
            self.business_profile = self.automation.business_profile
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, related_name="events", on_delete=models.CASCADE)
    sequence_index = models.PositiveIntegerField(help_text="Monotonic per-run ordering for deterministic playback.")
    stream = models.CharField(max_length=16, choices=AgentRunEventStream.choices, default=AgentRunEventStream.SYSTEM)
    event_type = models.CharField(max_length=24, choices=AgentRunEventType.choices)
    label = models.CharField(max_length=240, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "agent_runs_event"
        ordering = ("run_id", "sequence_index")
        indexes = [
            models.Index(fields=["run", "sequence_index"], name="run_event_order_idx"),
            models.Index(fields=["run", "created_at"], name="run_event_created_idx"),
            models.Index(fields=["run", "event_type"], name="run_event_type_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence_index"], name="run_event_unique_sequence"),
        ]


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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey("accounts.BusinessProfile", related_name="agent_run_checkpoints", on_delete=models.CASCADE)
    automation = models.ForeignKey("automations.Automation", related_name="checkpoints", on_delete=models.SET_NULL, null=True, blank=True)
    run = models.ForeignKey(AgentRun, related_name="checkpoints", on_delete=models.CASCADE)
    conversation = models.ForeignKey("conversations.Conversation", related_name="agent_run_checkpoints", on_delete=models.SET_NULL, null=True, blank=True)
    child_run = models.ForeignKey(AgentRun, related_name="parent_checkpoints", on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=24, choices=AgentRunCheckpointKind.choices, db_index=True)
    status = models.CharField(max_length=24, choices=AgentRunCheckpointStatus.choices, default=AgentRunCheckpointStatus.OPEN, db_index=True)
    title = models.CharField(max_length=240, blank=True, default="")
    prompt = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    resolution = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="created_agent_run_checkpoints", on_delete=models.SET_NULL, null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="resolved_agent_run_checkpoints", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_runs_checkpoint"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status", "updated_at"], name="checkpoint_biz_status_idx"),
            models.Index(fields=["automation", "status", "updated_at"], name="checkpoint_auto_status_idx"),
            models.Index(fields=["run", "status", "created_at"], name="checkpoint_run_status_idx"),
            models.Index(fields=["status", "expires_at"], name="checkpoint_expiry_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.run_id and not self.business_profile_id and getattr(self, "run", None):
            self.business_profile = self.run.business_profile
        if self.run_id and not self.automation_id and getattr(self, "run", None):
            self.automation = self.run.automation
        if self.run_id and not self.conversation_id and getattr(self, "run", None):
            self.conversation = self.run.conversation
        super().save(*args, **kwargs)


class AgentRunArtifactKind(models.TextChoices):
    FILE = "file", "File"
    LINK = "link", "Link"
    RECORD = "record", "Record"
    MESSAGE = "message", "Message"


class AgentRunArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, related_name="artifacts", on_delete=models.CASCADE)
    kind = models.CharField(max_length=24, choices=AgentRunArtifactKind.choices, default=AgentRunArtifactKind.FILE)
    label = models.CharField(max_length=200, blank=True, default="")
    conversation_file = models.ForeignKey(
        "conversations.ConversationFile",
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
        db_table = "agent_runs_artifact"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["run", "created_at"], name="run_artifact_created_idx"),
            models.Index(fields=["run", "kind"], name="run_artifact_kind_idx"),
        ]


class AgentRunNotificationStatus(models.TextChoices):
    CANDIDATE = "candidate", "Candidate"
    DELIVERED = "delivered", "Delivered"
    SUPPRESSED = "suppressed", "Suppressed"
    FAILED = "failed", "Failed"


class AgentRunNotification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey("accounts.BusinessProfile", related_name="agent_run_notifications", on_delete=models.CASCADE)
    agent_profile = models.ForeignKey("accounts.AgentProfile", related_name="agent_run_notifications", on_delete=models.SET_NULL, null=True, blank=True)
    owner_agent_profile = models.ForeignKey("accounts.AgentProfile", related_name="owned_run_notifications", on_delete=models.SET_NULL, null=True, blank=True)
    automation = models.ForeignKey("automations.Automation", related_name="notifications", on_delete=models.SET_NULL, null=True, blank=True)
    run = models.ForeignKey(AgentRun, related_name="notifications", on_delete=models.CASCADE)
    target_conversation = models.ForeignKey(
        "conversations.Conversation",
        related_name="agent_run_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=24, choices=AgentRunNotificationStatus.choices, default=AgentRunNotificationStatus.CANDIDATE, db_index=True)
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
        db_table = "agent_runs_notification"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status", "created_at"], name="run_notif_biz_status_idx"),
            models.Index(fields=["automation", "created_at"], name="run_notif_automation_idx"),
            models.Index(fields=["run", "created_at"], name="run_notif_run_idx"),
            models.Index(fields=["target_conversation", "created_at"], name="run_notif_target_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.run_id and not self.business_profile_id and getattr(self, "run", None):
            self.business_profile = self.run.business_profile
        if self.run_id and not self.agent_profile_id and getattr(self, "run", None):
            self.agent_profile = self.run.agent_profile
        if self.automation_id and not self.business_profile_id and getattr(self, "automation", None):
            self.business_profile = self.automation.business_profile
        super().save(*args, **kwargs)
