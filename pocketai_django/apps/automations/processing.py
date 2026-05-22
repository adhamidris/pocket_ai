from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from django.db import connection as db_connection, transaction
from django.utils import timezone

from apps.agent_runs.models import AgentRun, AgentRunEvent, AgentRunEventStream, AgentRunEventType, AgentRunSource, AgentRunStatus
from apps.automations.models import Automation, AutomationStatus, AutomationTriggerType
from apps.automations.scheduling import CronScheduleError, compute_next_automation_schedule_at
from apps.conversations.instruction_contracts import normalize_workflow_instructions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutomationProcessResult:
    automation_id: str
    action: str
    run_id: str | None = None
    error: str | None = None


def compute_next_automation_trigger_at(trigger_config: object, *, after=None):
    config = dict(trigger_config or {}) if isinstance(trigger_config, Mapping) else {}
    config.setdefault("type", "cron")
    return compute_next_automation_schedule_at("cron", config, after=after or timezone.now())


def run_snapshot(automation: Automation) -> dict[str, Any]:
    instructions = automation.instructions if isinstance(automation.instructions, dict) else {}
    return normalize_workflow_instructions(
        {
            **instructions,
            "name": automation.name,
            "description": automation.description,
            "trigger_type": automation.trigger_type,
            "trigger_config": automation.trigger_config if isinstance(automation.trigger_config, dict) else {},
            "source_config": automation.source_config if isinstance(automation.source_config, dict) else {},
            "destination_config": automation.destination_config if isinstance(automation.destination_config, dict) else {},
            "notification_config": automation.notification_config if isinstance(automation.notification_config, dict) else {},
            "review_mode": automation.review_mode,
            "autonomy_mode": automation.autonomy_mode,
        }
    )


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


class AutomationProcessingService:
    def process_next_due_automation(self) -> AutomationProcessResult | None:
        now = timezone.now()
        qs = (
            Automation.objects.select_related("business_profile", "agent_profile", "conversation")
            .filter(status=AutomationStatus.ACTIVE)
            .filter(trigger_type=AutomationTriggerType.SCHEDULE)
            .filter(next_trigger_at__lte=now)
            .order_by("next_trigger_at", "created_at")
        )
        with transaction.atomic():
            for_update_kwargs: dict[str, Any] = {}
            if getattr(db_connection.features, "has_select_for_update_skip_locked", False):
                for_update_kwargs["skip_locked"] = True
            if getattr(db_connection.features, "has_select_for_update_of", False):
                for_update_kwargs["of"] = ("self",)
            automation = qs.select_for_update(**for_update_kwargs).first()
            if automation is None:
                return None
            return self._trigger_scheduled(automation, now=now)

    def _trigger_scheduled(self, automation: Automation, *, now) -> AutomationProcessResult:
        try:
            next_at = compute_next_automation_trigger_at(automation.trigger_config, after=now)
        except CronScheduleError as exc:
            Automation.objects.filter(id=automation.id).update(status=AutomationStatus.PAUSED, last_error=str(exc)[:1000], updated_at=now)
            return AutomationProcessResult(automation_id=str(automation.id), action="paused", error=str(exc)[:400])
        run = AgentRun.objects.create(
            business_profile=automation.business_profile,
            agent_profile=automation.agent_profile,
            automation=automation,
            conversation=None,
            created_by=automation.created_by,
            run_snapshot=run_snapshot(automation),
            title=(automation.name or "Scheduled task")[:200],
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.QUEUED,
            visibility=automation.visibility,
            metadata={
                "automation_id": str(automation.id),
                "trigger": "schedule",
            },
            run_after=now,
        )
        append_run_event(run, label="Queued (automation schedule)", payload={"automation_id": str(automation.id), "trigger": "schedule"})
        Automation.objects.filter(id=automation.id).update(last_triggered_at=now, next_trigger_at=next_at, updated_at=now)
        return AutomationProcessResult(automation_id=str(automation.id), action="triggered", run_id=str(run.id))
