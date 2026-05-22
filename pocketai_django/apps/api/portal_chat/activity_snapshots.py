from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.agent_runs.models import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunCheckpointStatus,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
)
from apps.agentic_tasks.models import AgenticTask
from apps.agentic_tasks.processing import ACTIVE_RUN_STATUSES, ensure_task_conversation
from apps.api.portal_chat.serializers import _message_to_dict
from apps.conversations.models import AgentRequest, Conversation, ConversationMessage
from apps.conversations.portal_session.serializers import (
    serialize_agent_run_checkpoint_for_portal,
    serialize_agent_run_event_for_portal,
    serialize_agent_run_for_portal,
)
from apps.conversations.instruction_contracts import normalize_workflow_instructions
from core.tenancy import tenant_context


def _clip_portal_text(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _serialize_agent_run_for_portal(run: AgentRun) -> dict[str, object]:
    return serialize_agent_run_for_portal(run)


def _serialize_agent_run_checkpoint_for_portal(checkpoint: AgentRunCheckpoint | None) -> dict[str, object] | None:
    return serialize_agent_run_checkpoint_for_portal(checkpoint)


def _serialize_agentic_task_for_portal(
    agentic_task: AgenticTask,
    *,
    latest_run: AgentRun | None = None,
    open_checkpoint: AgentRunCheckpoint | None = None,
    recent_runs: list[AgentRun] | None = None,
    messages: list[ConversationMessage] | None = None,
) -> dict[str, object]:
    return {
        "id": str(agentic_task.id),
        "agentId": str(agentic_task.agent_profile_id),
        "agentName": getattr(getattr(agentic_task, "agent_profile", None), "name", "") or "",
        "activeConversationId": str(agentic_task.active_conversation_id) if agentic_task.active_conversation_id else None,
        "name": agentic_task.name,
        "description": agentic_task.description or "",
        "status": agentic_task.status,
        "scheduleEnabled": bool(agentic_task.schedule_enabled),
        "scheduleConfig": agentic_task.schedule_config if isinstance(agentic_task.schedule_config, dict) else {},
        "nextTriggerAt": agentic_task.next_trigger_at.isoformat() if agentic_task.next_trigger_at else None,
        "lastTriggeredAt": agentic_task.last_triggered_at.isoformat() if agentic_task.last_triggered_at else None,
        "latestRun": _serialize_agent_run_for_portal(latest_run) if latest_run else None,
        "openCheckpoint": _serialize_agent_run_checkpoint_for_portal(open_checkpoint),
        "recentRuns": [_serialize_agent_run_for_portal(item) for item in (recent_runs or [])[:10]],
        "messages": [_message_to_dict(item) for item in (messages or [])[-40:]],
        "attentionState": "needs_attention" if open_checkpoint else ("active" if latest_run and latest_run.status in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING, AgentRunStatus.WAITING_CHILD, AgentRunStatus.WAITING_EXTERNAL} else agentic_task.status),
        "createdAt": agentic_task.created_at.isoformat() if agentic_task.created_at else None,
        "updatedAt": agentic_task.updated_at.isoformat() if agentic_task.updated_at else None,
    }


def _is_runnable_agentic_task(agentic_task: AgenticTask) -> bool:
    return bool(agentic_task and agentic_task.status != "archived")


def _serialize_agent_run_event_for_portal(event: AgentRunEvent) -> dict[str, object]:
    return serialize_agent_run_event_for_portal(event)


def _append_agent_run_event(
    run: AgentRun,
    *,
    stream: str,
    event_type: str,
    label: str = "",
    payload: dict[str, object] | None = None,
) -> AgentRunEvent:
    with transaction.atomic():
        locked_run = AgentRun.objects.select_for_update().get(id=run.id)
        next_index = (
            AgentRunEvent.objects.filter(run=locked_run).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
        )
        return AgentRunEvent.objects.create(
            run=locked_run,
            sequence_index=int(next_index) + 1,
            stream=stream,
            event_type=event_type,
            label=(label or "")[:240],
            payload=payload or {},
        )


def _run_snapshot_for_portal_run(agentic_task: AgenticTask) -> dict[str, Any]:
    instructions = agentic_task.instructions if isinstance(getattr(agentic_task, "instructions", None), dict) else {}
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


def _create_portal_manual_agentic_task_run(
    *,
    agentic_task: AgenticTask,
    created_by,
    portal_conversation: Conversation,
) -> AgentRun:
    active_run = AgentRun.objects.filter(agentic_task=agentic_task, status__in=ACTIVE_RUN_STATUSES).order_by("-created_at").first()
    if active_run is not None:
        return active_run
    conversation = ensure_task_conversation(agentic_task)
    snapshot = _run_snapshot_for_portal_run(agentic_task)
    run = AgentRun.objects.create(
        business_profile=agentic_task.business_profile,
        agent_profile=agentic_task.agent_profile,
        conversation=conversation,
        execution_conversation=conversation,
        created_by=created_by,
        agentic_task=agentic_task,
        run_snapshot=snapshot,
        title=(agentic_task.name or snapshot.get("name") or snapshot.get("goal") or "AgenticTask run")[:200],
        source=AgentRunSource.TASK,
        status=AgentRunStatus.QUEUED,
        visibility=agentic_task.visibility,
        metadata={
            "agentic_task_id": str(agentic_task.id),
            "trigger": "manual",
            "trigger_source": "portal_task_panel",
            "portal_conversation_id": str(portal_conversation.id),
            "task_conversation_id": str(conversation.id),
        },
        run_after=timezone.now(),
    )
    _append_agent_run_event(
        run,
        stream=AgentRunEventStream.SYSTEM,
        event_type=AgentRunEventType.PROGRESS,
        label="Queued",
        payload={
            "status": AgentRunStatus.QUEUED,
            "agentic_task_id": str(agentic_task.id),
            "trigger": "manual",
            "trigger_source": "portal_task_panel",
            "task_conversation_id": str(conversation.id),
        },
    )
    return run


def _build_portal_agent_runs_snapshot(
    *,
    conversation_id: uuid.UUID,
    business_id: uuid.UUID | None,
    agent_profile_id: uuid.UUID | None = None,
    runs_limit: int = 15,
    events_limit_per_run: int = 80,
) -> dict[str, object]:
    runs_limit = max(1, min(int(runs_limit), 50))
    events_limit_per_run = max(0, min(int(events_limit_per_run), 120))
    snapshot_started_at = timezone.now()
    live_statuses = {
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_USER,
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.WAITING_CHILD,
        AgentRunStatus.WAITING_EXTERNAL,
        AgentRunStatus.PAUSED,
    }

    with tenant_context(business_id):
        agentic_task_agents: list[dict[str, object]] = []
        if agent_profile_id:
            agentic_tasks = list(
                AgenticTask.objects.filter(
                    business_profile_id=business_id,
                    agent_profile_id=agent_profile_id,
                )
                .select_related("agent_profile")
                .order_by("-updated_at", "-created_at")[:100]
            )
            agentic_task_ids = [agentic_task.id for agentic_task in agentic_tasks]
            latest_runs: dict[uuid.UUID, AgentRun] = {}
            recent_runs_by_agentic_task: dict[uuid.UUID, list[AgentRun]] = {}
            open_checkpoints: dict[uuid.UUID, AgentRunCheckpoint] = {}
            if agentic_task_ids:
                for run in (
                    AgentRun.objects.select_related("agentic_task")
                    .filter(
                        agentic_task_id__in=agentic_task_ids,
                        business_profile_id=business_id,
                        agent_profile_id=agent_profile_id,
                    )
                    .order_by("-created_at")[:500]
                ):
                    if run.agentic_task_id:
                        recent_runs_by_agentic_task.setdefault(run.agentic_task_id, []).append(run)
                        latest_runs.setdefault(run.agentic_task_id, run)
                for checkpoint in (
                    AgentRunCheckpoint.objects.filter(
                        agentic_task_id__in=agentic_task_ids,
                        status=AgentRunCheckpointStatus.OPEN,
                    )
                    .order_by("-updated_at", "-created_at")[:300]
                ):
                    if checkpoint.agentic_task_id and checkpoint.agentic_task_id not in open_checkpoints:
                        open_checkpoints[checkpoint.agentic_task_id] = checkpoint
            messages_by_conversation: dict[uuid.UUID, list[ConversationMessage]] = {}
            conversation_ids = [task.active_conversation_id for task in agentic_tasks if task.active_conversation_id]
            if conversation_ids:
                for message in (
                    ConversationMessage.objects.filter(conversation_id__in=conversation_ids)
                    .order_by("conversation_id", "-sent_at", "-created_at")[:4000]
                ):
                    messages_by_conversation.setdefault(message.conversation_id, []).append(message)
                for conversation_id, messages in list(messages_by_conversation.items()):
                    messages_by_conversation[conversation_id] = list(reversed(messages[:40]))
            agentic_task_agents = [
                _serialize_agentic_task_for_portal(
                    agentic_task,
                    latest_run=latest_runs.get(agentic_task.id),
                    open_checkpoint=open_checkpoints.get(agentic_task.id),
                    recent_runs=[
                        run
                        for run in recent_runs_by_agentic_task.get(agentic_task.id, [])
                        if run.status in live_statuses
                    ],
                    messages=messages_by_conversation.get(agentic_task.active_conversation_id, []) if agentic_task.active_conversation_id else [],
                )
                for agentic_task in agentic_tasks
            ]

        runs = list(
            AgentRun.objects.select_related("agentic_task")
            .filter(conversation_id=conversation_id, agentic_task_id__isnull=True)
            .order_by("-created_at")[:runs_limit]
        )
        # The activity panel renders all runs in this snapshot. Include the
        # persisted event log for those visible runs so completed tasks can
        # still rebuild their "Worked for ..." activity after a refresh.
        run_ids = [run.id for run in runs]
        for agentic_task in agentic_task_agents:
            for item in [agentic_task.get("latestRun"), *(agentic_task.get("recentRuns") or [])]:
                if isinstance(item, dict) and item.get("id"):
                    run_id_value = str(item.get("id") or "").strip()
                    try:
                        run_ids.append(uuid.UUID(run_id_value))
                    except (TypeError, ValueError):
                        pass

        events_by_run: dict[str, list[dict[str, object]]] = {}
        max_created_at: datetime | None = None

        if run_ids and events_limit_per_run:
            counts: dict[str, int] = {}
            grouped: dict[str, list[AgentRunEvent]] = {}
            qs = (
                AgentRunEvent.objects.filter(run_id__in=run_ids)
                .order_by("run_id", "-sequence_index")
            )
            for event in qs:
                run_id_str = str(event.run_id)
                current = counts.get(run_id_str, 0)
                if current >= events_limit_per_run:
                    continue
                counts[run_id_str] = current + 1
                grouped.setdefault(run_id_str, []).append(event)
                if event.created_at and (max_created_at is None or event.created_at > max_created_at):
                    max_created_at = event.created_at
            for run_id_str, event_list in grouped.items():
                events_by_run[run_id_str] = [
                    _serialize_agent_run_event_for_portal(item) for item in reversed(event_list)
                ]

        cursor_at = max_created_at if max_created_at and max_created_at > snapshot_started_at else snapshot_started_at
        cursor_value = cursor_at.isoformat()
        return {
            "conversationId": str(conversation_id),
            "runs": [_serialize_agent_run_for_portal(run) for run in runs],
            "agenticTasks": agentic_task_agents,
            "eventsByRun": events_by_run,
            "cursor": {"since": cursor_value},
        }


def _serialize_agent_request_for_portal(request: AgentRequest) -> dict[str, object]:
    context_refs = request.context_refs if isinstance(getattr(request, "context_refs", None), list) else []
    from_agent = getattr(request, "from_agent_profile", None)
    to_agent = getattr(request, "to_agent_profile", None)
    return {
        "id": str(request.id),
        "status": request.status,
        "subject": request.subject or "",
        "question": _clip_portal_text(str(request.question or ""), 6000),
        "contextRefs": context_refs,
        "resolution": _clip_portal_text(str(request.resolution or ""), 6000),
        "fromAgent": {
            "id": str(getattr(from_agent, "id", "") or ""),
            "name": str(getattr(from_agent, "name", "") or ""),
            "slug": str(getattr(from_agent, "slug", "") or ""),
        }
        if from_agent
        else {},
        "toAgent": {
            "id": str(getattr(to_agent, "id", "") or ""),
            "name": str(getattr(to_agent, "name", "") or ""),
            "slug": str(getattr(to_agent, "slug", "") or ""),
        }
        if to_agent
        else {},
        "conversationId": str(request.conversation_id) if request.conversation_id else None,
        "agentRunId": str(request.agent_run_id) if request.agent_run_id else None,
        "createdAt": request.created_at.isoformat() if request.created_at else None,
        "updatedAt": request.updated_at.isoformat() if request.updated_at else None,
        "resolvedAt": request.resolved_at.isoformat() if request.resolved_at else None,
    }


def _build_portal_agent_requests_snapshot(
    *,
    business_id: uuid.UUID | None,
    agent_profile_id: uuid.UUID | None,
    limit: int = 25,
) -> dict[str, object]:
    limit = max(1, min(int(limit), 100))
    if not business_id or not agent_profile_id:
        return {
            "agentProfileId": str(agent_profile_id) if agent_profile_id else None,
            "requests": [],
            "cursor": {"since": timezone.now().isoformat()},
        }

    with tenant_context(business_id):
        qs = (
            AgentRequest.objects.select_related("from_agent_profile", "to_agent_profile")
            .filter(business_profile_id=business_id)
            .filter(Q(to_agent_profile_id=agent_profile_id) | Q(from_agent_profile_id=agent_profile_id))
            .order_by("-updated_at")[:limit]
        )
        requests = list(qs)
        max_updated = None
        for req in requests:
            if req.updated_at and (max_updated is None or req.updated_at > max_updated):
                max_updated = req.updated_at
        cursor_value = (max_updated or timezone.now()).isoformat()
        return {
            "agentProfileId": str(agent_profile_id),
            "requests": [_serialize_agent_request_for_portal(req) for req in requests],
            "cursor": {"since": cursor_value},
        }
