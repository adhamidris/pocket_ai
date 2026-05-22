from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from django.db import connection as db_connection, transaction
from django.utils import timezone

from apps.agent_runs.models import AgentRun, AgentRunEvent, AgentRunEventStream, AgentRunEventType, AgentRunSource, AgentRunStatus
from apps.agentic_tasks.models import AgenticTask, AgenticTaskStatus
from apps.agentic_tasks.scheduling import CronScheduleError, compute_next_agentic_task_schedule_at
from apps.conversations.instruction_contracts import normalize_workflow_instructions
from apps.conversations.models import Conversation, ConversationChannel, ConversationMessage, ConversationSender, ConversationStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgenticTaskProcessResult:
    agentic_task_id: str
    action: str
    run_id: str | None = None
    error: str | None = None


def compute_next_agentic_task_run_at(schedule_config: object, *, after=None):
    config = dict(schedule_config or {}) if isinstance(schedule_config, Mapping) else {}
    config.setdefault("type", "cron")
    return compute_next_agentic_task_schedule_at("cron", config, after=after or timezone.now())


def run_snapshot(agentic_task: AgenticTask) -> dict[str, Any]:
    instructions = agentic_task.instructions if isinstance(agentic_task.instructions, dict) else {}
    return normalize_workflow_instructions(
        {
            **instructions,
            "name": agentic_task.name,
            "description": agentic_task.description,
            "schedule_enabled": agentic_task.schedule_enabled,
            "schedule_config": agentic_task.schedule_config if isinstance(agentic_task.schedule_config, dict) else {},
            "review_mode": agentic_task.review_mode,
            "autonomy_mode": agentic_task.autonomy_mode,
        }
    )


ACTIVE_RUN_STATUSES = {
    AgentRunStatus.QUEUED,
    AgentRunStatus.RUNNING,
    AgentRunStatus.WAITING_USER,
    AgentRunStatus.WAITING_APPROVAL,
    AgentRunStatus.WAITING_CHILD,
    AgentRunStatus.WAITING_EXTERNAL,
    AgentRunStatus.PAUSED,
}


def ensure_task_conversation(agentic_task: AgenticTask) -> Conversation:
    if agentic_task.active_conversation_id:
        conversation = Conversation.objects.filter(
            id=agentic_task.active_conversation_id,
            business_profile=agentic_task.business_profile,
        ).first()
        if conversation is not None:
            return conversation
    owner = agentic_task.created_by or getattr(agentic_task.agent_profile, "user", None) or agentic_task.business_profile.user
    conversation = Conversation.objects.create(
        business_profile=agentic_task.business_profile,
        agent_profile=agentic_task.agent_profile,
        owner_user=owner,
        channel=ConversationChannel.API,
        status=ConversationStatus.LIVE,
        summary=agentic_task.name,
        metadata={
            "type": "agentic_task_session",
            "agentic_task_id": str(agentic_task.id),
            "agentic_task_name": agentic_task.name,
            "session_state": "active",
        },
    )
    AgenticTask.objects.filter(id=agentic_task.id).update(active_conversation=conversation, updated_at=timezone.now())
    agentic_task.active_conversation = conversation
    agentic_task.active_conversation_id = conversation.id
    return conversation


def append_run_event(run: AgentRun, *, label: str, payload: dict[str, object] | None = None) -> None:
    with transaction.atomic():
        locked = AgentRun.objects.select_for_update().get(id=run.id)
        next_index = AgentRunEvent.objects.filter(run=locked).count() + 1
        AgentRunEvent.objects.create(
            run=locked,
            sequence_index=next_index,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label=label[:240],
            payload=payload or {},
        )


class AgenticTaskProcessingService:
    def process_next_due_agentic_task(self) -> AgenticTaskProcessResult | None:
        now = timezone.now()
        qs = (
            AgenticTask.objects.select_related("business_profile", "agent_profile", "active_conversation")
            .filter(status=AgenticTaskStatus.ACTIVE)
            .filter(schedule_enabled=True)
            .filter(next_trigger_at__lte=now)
            .order_by("next_trigger_at", "created_at")
        )
        with transaction.atomic():
            for_update_kwargs: dict[str, Any] = {}
            if getattr(db_connection.features, "has_select_for_update_skip_locked", False):
                for_update_kwargs["skip_locked"] = True
            if getattr(db_connection.features, "has_select_for_update_of", False):
                for_update_kwargs["of"] = ("self",)
            agentic_task = qs.select_for_update(**for_update_kwargs).first()
            if agentic_task is None:
                return None
            return self._trigger_scheduled(agentic_task, now=now)

    def _trigger_scheduled(self, agentic_task: AgenticTask, *, now) -> AgenticTaskProcessResult:
        try:
            next_at = compute_next_agentic_task_run_at(agentic_task.schedule_config, after=now)
        except CronScheduleError as exc:
            AgenticTask.objects.filter(id=agentic_task.id).update(status=AgenticTaskStatus.PAUSED, last_error=str(exc)[:1000], updated_at=now)
            return AgenticTaskProcessResult(agentic_task_id=str(agentic_task.id), action="paused", error=str(exc)[:400])
        active_run = AgentRun.objects.filter(agentic_task=agentic_task, status__in=ACTIVE_RUN_STATUSES).order_by("-created_at").first()
        if active_run is not None:
            metadata = dict(agentic_task.metadata or {}) if isinstance(getattr(agentic_task, "metadata", None), dict) else {}
            metadata["last_skipped_due_to_active_run_at"] = now.isoformat()
            metadata["last_skipped_active_run_id"] = str(active_run.id)
            AgenticTask.objects.filter(id=agentic_task.id).update(next_trigger_at=next_at, metadata=metadata, updated_at=now)
            return AgenticTaskProcessResult(agentic_task_id=str(agentic_task.id), action="skipped_active_run", run_id=str(active_run.id))
        conversation = ensure_task_conversation(agentic_task)
        ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.SYSTEM,
            body=f"Scheduled trigger fired for Agentic Task: {agentic_task.name}",
            metadata={"type": "agentic_task_trigger", "trigger": "schedule", "agentic_task_id": str(agentic_task.id)},
        )
        run = AgentRun.objects.create(
            business_profile=agentic_task.business_profile,
            agent_profile=agentic_task.agent_profile,
            agentic_task=agentic_task,
            conversation=conversation,
            execution_conversation=conversation,
            created_by=agentic_task.created_by,
            run_snapshot=run_snapshot(agentic_task),
            title=(agentic_task.name or "Scheduled task")[:200],
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.QUEUED,
            visibility=agentic_task.visibility,
            metadata={
                "agentic_task_id": str(agentic_task.id),
                "trigger": "schedule",
            },
            run_after=now,
        )
        append_run_event(run, label="Queued (Agentic Task schedule)", payload={"agentic_task_id": str(agentic_task.id), "trigger": "schedule"})
        AgenticTask.objects.filter(id=agentic_task.id).update(last_triggered_at=now, next_trigger_at=next_at, updated_at=now)
        return AgenticTaskProcessResult(agentic_task_id=str(agentic_task.id), action="triggered", run_id=str(run.id))
