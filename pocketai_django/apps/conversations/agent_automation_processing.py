from __future__ import annotations

import dataclasses
import logging

from django.db import connection as db_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context

from apps.accounts.feature_flags import FeatureFlagService
from apps.conversations.automation_scheduling import CronScheduleError, compute_next_automation_trigger_at
from apps.conversations.output_destinations import ensure_automation_thread
from apps.conversations.models import (
    AgentAutomation,
    AgentAutomationStatus,
    AgentAutomationTriggerType,
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
)
from apps.conversations.run_contracts import normalize_run_spec


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class AgentAutomationProcessResult:
    automation_id: str
    action: str
    run_id: str | None = None
    next_trigger_at: str | None = None
    error: str | None = None


class AgentAutomationProcessingService:
    """
    Background worker service for cron-triggered AgentAutomations.

    V1 scope:
    - Initialize next_trigger_at for newly activated cron automations.
    - Trigger due automations and spawn AgentRuns.
    """

    def process_next_due_automation(self) -> AgentAutomationProcessResult | None:
        now = timezone.now()
        qs = (
            AgentAutomation.objects.select_related("business_profile", "agent_profile", "conversation", "run_spec")
            .filter(status=AgentAutomationStatus.ACTIVE, trigger_type=AgentAutomationTriggerType.CRON)
            .filter(Q(next_trigger_at__lte=now) | Q(next_trigger_at__isnull=True))
            .order_by("next_trigger_at", "created_at")
        )

        supports_skip_locked = bool(
            getattr(db_connection.features, "has_select_for_update", False)
            and getattr(db_connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(db_connection.features, "has_select_for_update_of", False))
        supports_for_update = bool(getattr(db_connection.features, "has_select_for_update", False))

        with tenant_bypass():
            with transaction.atomic():
                automation: AgentAutomation | None = None
                if supports_for_update:
                    for_update_kwargs: dict[str, object] = {}
                    if supports_skip_locked:
                        for_update_kwargs["skip_locked"] = True
                    if supports_for_update_of:
                        for_update_kwargs["of"] = ("self",)
                    automation = qs.select_for_update(**for_update_kwargs).first()
                else:
                    automation = qs.first()

                if automation is None:
                    return None

                enabled = bool(getattr(FeatureFlagService.snapshot(automation.business_profile), "sub_agents_v1", False))

                if automation.next_trigger_at is None:
                    try:
                        next_at = compute_next_automation_trigger_at(
                            automation.trigger_type,
                            automation.trigger_config,
                            after=now,
                        )
                    except CronScheduleError as exc:
                        return self._pause_invalid_schedule(automation, str(exc), now=now)

                    metadata = dict(automation.metadata or {}) if isinstance(automation.metadata, dict) else {}
                    if not enabled:
                        metadata["last_skip"] = {"at": now.isoformat(), "reason": "sub_agents_disabled"}
                    AgentAutomation.objects.filter(id=automation.id).update(
                        next_trigger_at=next_at,
                        metadata=metadata,
                        updated_at=now,
                    )
                    return AgentAutomationProcessResult(
                        automation_id=str(automation.id),
                        action="scheduled" if enabled else "scheduled_skipped",
                        next_trigger_at=next_at.isoformat() if next_at else None,
                    )

                if automation.next_trigger_at and automation.next_trigger_at > now:
                    return None

                try:
                    next_at = compute_next_automation_trigger_at(
                        automation.trigger_type,
                        automation.trigger_config,
                        after=now,
                    )
                except CronScheduleError as exc:
                    return self._pause_invalid_schedule(automation, str(exc), now=now)

                if not enabled:
                    metadata = dict(automation.metadata or {}) if isinstance(automation.metadata, dict) else {}
                    metadata["last_skip"] = {"at": now.isoformat(), "reason": "sub_agents_disabled"}
                    AgentAutomation.objects.filter(id=automation.id).update(
                        next_trigger_at=next_at,
                        metadata=metadata,
                        updated_at=now,
                    )
                    return AgentAutomationProcessResult(
                        automation_id=str(automation.id),
                        action="skipped_disabled",
                        next_trigger_at=next_at.isoformat() if next_at else None,
                    )

                with tenant_context(automation.business_profile_id):
                    if automation.conversation_id is None:
                        ensure_automation_thread(automation)

                run = AgentRun.objects.create(
                    business_profile=automation.business_profile,
                    agent_profile=automation.agent_profile,
                    conversation=automation.conversation,
                    created_by=automation.created_by,
                    run_spec=automation.run_spec,
                    run_spec_snapshot=normalize_run_spec(automation.run_spec_snapshot),
                    title=(automation.name or "Automation run")[:200],
                    source=AgentRunSource.AUTOMATION,
                    status=AgentRunStatus.QUEUED,
                    visibility=automation.visibility,
                    metadata={
                        "automation_id": str(automation.id),
                        "trigger": "cron",
                        "destination_config": dict(automation.destination_config or {})
                        if isinstance(automation.destination_config, dict)
                        else {},
                    },
                    run_after=now,
                )
                AgentRunEvent.objects.create(
                    run=run,
                    sequence_index=1,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=AgentRunEventType.PROGRESS,
                    label="Queued (automation)",
                    payload={"automation_id": str(automation.id), "trigger": "cron"},
                )
                AgentAutomation.objects.filter(id=automation.id).update(
                    last_triggered_at=now,
                    next_trigger_at=next_at,
                    updated_at=now,
                )
                return AgentAutomationProcessResult(
                    automation_id=str(automation.id),
                    action="triggered",
                    run_id=str(run.id),
                    next_trigger_at=next_at.isoformat() if next_at else None,
                )

    def _pause_invalid_schedule(self, automation: AgentAutomation, message: str, *, now) -> AgentAutomationProcessResult:
        metadata = dict(automation.metadata or {}) if isinstance(automation.metadata, dict) else {}
        metadata["schedule_error"] = {"at": now.isoformat(), "message": (message or "")[:400]}
        AgentAutomation.objects.filter(id=automation.id).update(
            status=AgentAutomationStatus.PAUSED,
            next_trigger_at=None,
            metadata=metadata,
            updated_at=now,
        )
        logger.warning("agent_automation.paused_invalid_schedule automation=%s error=%s", automation.id, message)
        return AgentAutomationProcessResult(automation_id=str(automation.id), action="paused", error=(message or "")[:400])
