from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from http import HTTPStatus
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.models import AgentProfile
from apps.agent_runs.models import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunCheckpointKind,
    AgentRunCheckpointStatus,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
    AgentRunVisibility,
)
from apps.assistants.models import CustomAssistant, CustomAssistantStatus
from apps.automations.models import (
    Automation,
    AutomationAutonomyMode,
    AutomationReviewMode,
    AutomationStatus,
    AutomationTriggerType,
)
from apps.automations.scheduling import CronScheduleError, compute_next_automation_schedule_at
from apps.conversations.instruction_contracts import normalize_workflow_instructions
from apps.conversations.models import (
    Conversation,
    ConversationChannel,
    ConversationSender,
    ConversationStatus,
    MemoryAuditAction,
    MemoryAuditEvent,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    MemoryVisibility,
)
from apps.conversations.run_display import build_agent_run_display


def _parse_json_body(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse({"error": "INVALID_JSON", "message": "Request body must be valid JSON."}, status=HTTPStatus.BAD_REQUEST)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "INVALID_JSON", "message": "Request body must be a JSON object."}, status=HTTPStatus.BAD_REQUEST)
    return payload, None


def _parse_uuid(value: object, *, field: str) -> tuple[uuid.UUID | None, JsonResponse | None]:
    if value in (None, ""):
        return None, None
    try:
        return uuid.UUID(str(value)), None
    except (TypeError, ValueError):
        return None, JsonResponse({"error": "VALIDATION_ERROR", "message": f"{field} must be a valid UUID."}, status=HTTPStatus.BAD_REQUEST)


def _resolve_agent_for_request(request: HttpRequest, agent_id: uuid.UUID) -> tuple[AgentProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    qs = AgentProfile.objects.select_related("business_profile")
    if not request.user.is_staff:
        qs = qs.filter(Q(user=request.user) | Q(business_profile__user=request.user))
    agent = qs.filter(id=agent_id).first()
    if agent is None:
        return None, JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)
    return agent, None


def _user_is_business_owner(request: HttpRequest, agent: AgentProfile) -> bool:
    return bool(request.user.is_authenticated and getattr(agent.business_profile, "user_id", None) == request.user.id)


def _run_visibility_filter(request: HttpRequest, *, agent: AgentProfile) -> Q:
    if request.user.is_staff:
        return Q()
    base = Q(created_by=request.user) | Q(visibility=AgentRunVisibility.WORKSPACE)
    if _user_is_business_owner(request, agent):
        base |= Q(visibility=AgentRunVisibility.MANAGERS)
    return base


def _run_snapshot(automation: Automation | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if automation is not None:
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
    return normalize_workflow_instructions(payload or {})


def _run_automation_id(run: AgentRun) -> str | None:
    if run.automation_id:
        return str(run.automation_id)
    metadata = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
    value = str(metadata.get("automation_id") or metadata.get("automationId") or "").strip()
    return value or None


def _run_automation_name(run: AgentRun) -> str:
    automation_name = getattr(getattr(run, "automation", None), "name", "") or ""
    if automation_name:
        return automation_name
    snapshot = run.run_snapshot if isinstance(getattr(run, "run_snapshot", None), dict) else {}
    return str(snapshot.get("name") or snapshot.get("task") or "").strip()


def _serialize_run_summary(run: AgentRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "automationId": _run_automation_id(run),
        "automationName": _run_automation_name(run),
        "title": run.title or "",
        "source": run.source,
        "status": run.status,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "durationMs": int((run.finished_at - run.started_at).total_seconds() * 1000)
        if run.started_at and run.finished_at
        else None,
        "errorDetail": run.error_detail or "",
        "display": build_agent_run_display(run),
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "updatedAt": run.updated_at.isoformat() if run.updated_at else None,
    }


def _serialize_checkpoint(checkpoint: AgentRunCheckpoint | None) -> dict[str, object] | None:
    if checkpoint is None:
        return None
    return {
        "id": str(checkpoint.id),
        "automationId": str(checkpoint.automation_id) if checkpoint.automation_id else None,
        "runId": str(checkpoint.run_id),
        "childRunId": str(checkpoint.child_run_id) if checkpoint.child_run_id else None,
        "conversationId": str(checkpoint.conversation_id) if checkpoint.conversation_id else None,
        "kind": checkpoint.kind,
        "status": checkpoint.status,
        "title": checkpoint.title or "",
        "prompt": checkpoint.prompt or "",
        "payload": checkpoint.payload if isinstance(checkpoint.payload, dict) else {},
        "resolution": checkpoint.resolution if isinstance(checkpoint.resolution, dict) else {},
        "expiresAt": checkpoint.expires_at.isoformat() if checkpoint.expires_at else None,
        "resolvedAt": checkpoint.resolved_at.isoformat() if checkpoint.resolved_at else None,
        "createdAt": checkpoint.created_at.isoformat() if checkpoint.created_at else None,
        "updatedAt": checkpoint.updated_at.isoformat() if checkpoint.updated_at else None,
    }


def _serialize_custom_assistant_session(conversation: Conversation) -> dict[str, object]:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
    return {
        "id": str(conversation.id),
        "conversationId": str(conversation.id),
        "sessionToken": conversation.session_token,
        "customAssistantId": str(conversation.custom_assistant_id) if conversation.custom_assistant_id else None,
        "title": conversation.summary or str(metadata.get("title") or metadata.get("custom_assistant_name") or metadata.get("customAssistantName") or "").strip() or "New assistant session",
        "status": conversation.status,
        "messageCount": getattr(conversation, "message_count", None),
        "startedAt": conversation.started_at.isoformat() if conversation.started_at else None,
        "lastActivityAt": conversation.last_activity_at.isoformat() if conversation.last_activity_at else None,
        "metadata": metadata,
    }


def _serialize_custom_assistant(assistant: CustomAssistant) -> dict[str, object]:
    session_count = getattr(assistant, "session_count", None)
    return {
        "id": str(assistant.id),
        "agentId": str(assistant.agent_profile_id),
        "agentName": getattr(getattr(assistant, "agent_profile", None), "name", "") or "",
        "businessId": str(assistant.business_profile_id),
        "name": assistant.name,
        "description": assistant.description or "",
        "status": assistant.status,
        "instructions": assistant.instructions if isinstance(assistant.instructions, dict) else {},
        "metadata": assistant.metadata if isinstance(assistant.metadata, dict) else {},
        "sessionCount": int(session_count or 0),
        "createdBy": str(assistant.created_by_id) if assistant.created_by_id else None,
        "createdAt": assistant.created_at.isoformat() if assistant.created_at else None,
        "updatedAt": assistant.updated_at.isoformat() if assistant.updated_at else None,
    }


def _serialize_automation(automation: Automation, latest_run: AgentRun | None = None) -> dict[str, object]:
    open_checkpoint = getattr(automation, "open_checkpoint", None)
    return {
        "id": str(automation.id),
        "agentId": str(automation.agent_profile_id),
        "agentName": getattr(getattr(automation, "agent_profile", None), "name", "") or "",
        "businessId": str(automation.business_profile_id),
        "conversationId": str(automation.conversation_id) if automation.conversation_id else None,
        "name": automation.name,
        "description": automation.description or "",
        "status": automation.status,
        "visibility": automation.visibility,
        "triggerType": automation.trigger_type,
        "triggerConfig": automation.trigger_config if isinstance(automation.trigger_config, dict) else {},
        "sourceConfig": automation.source_config if isinstance(automation.source_config, dict) else {},
        "destinationConfig": automation.destination_config if isinstance(automation.destination_config, dict) else {},
        "notificationConfig": automation.notification_config if isinstance(automation.notification_config, dict) else {},
        "reviewMode": automation.review_mode,
        "autonomyMode": automation.autonomy_mode,
        "instructions": automation.instructions if isinstance(automation.instructions, dict) else {},
        "state": automation.state if isinstance(automation.state, dict) else {},
        "lastTriggeredAt": automation.last_triggered_at.isoformat() if automation.last_triggered_at else None,
        "nextTriggerAt": automation.next_trigger_at.isoformat() if automation.next_trigger_at else None,
        "leaseExpiresAt": automation.lease_expires_at.isoformat() if automation.lease_expires_at else None,
        "errorCount": int(automation.error_count or 0),
        "lastError": automation.last_error or "",
        "latestRun": _serialize_run_summary(latest_run) if latest_run is not None else None,
        "openCheckpoint": _serialize_checkpoint(open_checkpoint) if isinstance(open_checkpoint, AgentRunCheckpoint) else None,
        "attentionState": "needs_attention" if isinstance(open_checkpoint, AgentRunCheckpoint) else ("active" if latest_run and latest_run.status in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING, AgentRunStatus.WAITING_CHILD, AgentRunStatus.WAITING_EXTERNAL} else automation.status),
        "metadata": automation.metadata if isinstance(automation.metadata, dict) else {},
        "createdBy": str(automation.created_by_id) if automation.created_by_id else None,
        "createdAt": automation.created_at.isoformat() if automation.created_at else None,
        "updatedAt": automation.updated_at.isoformat() if automation.updated_at else None,
    }


def _serialize_run(run: AgentRun) -> dict[str, object]:
    artifacts_qs = getattr(run, "artifacts", None)
    artifacts = []
    if artifacts_qs is not None:
        artifacts = [
            {
                "id": str(artifact.id),
                "kind": artifact.kind,
                "label": artifact.label or "",
                "url": artifact.url or "",
                "referenceType": artifact.reference_type or "",
                "referenceId": str(artifact.reference_id) if artifact.reference_id else None,
                "metadata": artifact.metadata if isinstance(artifact.metadata, dict) else {},
                "createdAt": artifact.created_at.isoformat() if artifact.created_at else None,
            }
            for artifact in artifacts_qs.all()[:20]
        ]
    return {
        "id": str(run.id),
        "agentId": str(run.agent_profile_id),
        "businessId": str(run.business_profile_id),
        "conversationId": str(run.conversation_id) if run.conversation_id else None,
        "automationId": _run_automation_id(run),
        "automationName": _run_automation_name(run),
        "parentRunId": str(run.parent_run_id) if run.parent_run_id else None,
        "delegatedByAgentId": str(run.delegated_by_agent_id) if run.delegated_by_agent_id else None,
        "title": run.title or "",
        "source": run.source,
        "status": run.status,
        "visibility": run.visibility,
        "attemptCount": int(run.attempt_count),
        "maxAttempts": int(run.max_attempts),
        "runAfter": run.run_after.isoformat() if run.run_after else None,
        "leaseExpiresAt": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "errorDetail": run.error_detail or "",
        "plan": run.plan if isinstance(run.plan, dict) else {},
        "result": run.result if isinstance(run.result, dict) else {},
        "artifacts": artifacts,
        "metadata": run.metadata if isinstance(run.metadata, dict) else {},
        "createdBy": str(run.created_by_id) if run.created_by_id else None,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "updatedAt": run.updated_at.isoformat() if run.updated_at else None,
    }


def _serialize_run_event(event: AgentRunEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "runId": str(event.run_id),
        "sequenceIndex": int(event.sequence_index),
        "stream": event.stream,
        "type": event.event_type,
        "label": event.label or "",
        "payload": event.payload if isinstance(event.payload, dict) else {},
        "createdAt": event.created_at.isoformat() if event.created_at else None,
    }


def _serialize_memory(item: MemoryItem) -> dict[str, object]:
    audit_events = []
    events_qs = getattr(item, "audit_events", None)
    if events_qs is not None:
        audit_events = [
            {
                "id": str(event.id),
                "action": event.action,
                "actorUserId": str(event.actor_user_id) if event.actor_user_id else None,
                "createdAt": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events_qs.all()[:10]
        ]
    return {
        "id": str(item.id),
        "businessId": str(item.business_profile_id),
        "scope": item.scope,
        "agentId": str(item.agent_profile_id) if item.agent_profile_id else None,
        "customAssistantId": str(item.custom_assistant_id) if item.custom_assistant_id else None,
        "automationId": str(item.automation_id) if item.automation_id else None,
        "runId": str(item.run_id) if item.run_id else None,
        "conversationId": str(item.conversation_id) if item.conversation_id else None,
        "crmContactId": str(item.crm_contact_id) if item.crm_contact_id else None,
        "crmCompanyId": str(item.crm_company_id) if item.crm_company_id else None,
        "kind": item.kind,
        "key": item.key or "",
        "content": item.content or "",
        "payload": item.payload if isinstance(item.payload, dict) else {},
        "visibility": item.visibility,
        "sensitivity": item.sensitivity,
        "status": item.status,
        "sourceType": item.source_type or "",
        "sourceId": str(item.source_id) if item.source_id else None,
        "confidence": float(item.confidence or 0),
        "createdBy": str(item.created_by_id) if item.created_by_id else None,
        "reviewedBy": str(item.reviewed_by_id) if item.reviewed_by_id else None,
        "reviewedAt": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
        "auditEvents": audit_events,
    }


CURATED_MEMORY_KINDS = {
    MemoryKind.FACT,
    MemoryKind.PREFERENCE,
    MemoryKind.POLICY,
    MemoryKind.DECISION,
    MemoryKind.INSTRUCTION,
    MemoryKind.RELATIONSHIP,
}


def _curated_memory_filter() -> Q:
    return (
        Q(kind__in=CURATED_MEMORY_KINDS)
        & ~Q(scope=MemoryScope.RUN)
        & ~Q(key__startswith="run_report_")
        & ~Q(key__startswith="search_")
        & ~Q(key__contains="_arg_")
        & ~Q(key__contains="_status")
    )


def _append_run_event(
    run_id: uuid.UUID,
    *,
    stream: str,
    event_type: str,
    label: str = "",
    payload: dict[str, object] | None = None,
) -> AgentRunEvent:
    with transaction.atomic():
        locked_run = AgentRun.objects.select_for_update().get(id=run_id)
        next_index = AgentRunEvent.objects.filter(run=locked_run).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
        return AgentRunEvent.objects.create(
            run=locked_run,
            sequence_index=int(next_index) + 1,
            stream=stream,
            event_type=event_type,
            label=(label or "")[:240],
            payload=payload or {},
        )


def _create_run(
    *,
    agent: AgentProfile,
    created_by,
    automation: Automation | None,
    conversation: Conversation | None,
    title: str,
    source: str,
    visibility: str,
    snapshot: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    parent_run: AgentRun | None = None,
    delegated_by_agent: AgentProfile | None = None,
) -> AgentRun:
    run = AgentRun.objects.create(
        business_profile=agent.business_profile,
        agent_profile=agent,
        conversation=conversation,
        created_by=created_by,
        automation=automation,
        parent_run=parent_run,
        delegated_by_agent=delegated_by_agent,
        run_snapshot=snapshot,
        title=(title or snapshot.get("name") or snapshot.get("goal") or "Agent run")[:200],
        source=source,
        status=AgentRunStatus.QUEUED,
        visibility=visibility,
        metadata=metadata or {},
        run_after=timezone.now(),
    )
    _append_run_event(
        run.id,
        stream=AgentRunEventStream.SYSTEM,
        event_type=AgentRunEventType.PROGRESS,
        label="Queued",
        payload={"status": AgentRunStatus.QUEUED, "automation_id": str(automation.id) if automation else None},
    )
    return run


def _set_run_status(run_id: uuid.UUID, *, status: str, error_detail: str | None = None, finished: bool = False, run_after: bool = False) -> None:
    updates: dict[str, object] = {"status": status, "updated_at": timezone.now()}
    if error_detail is not None:
        updates["error_detail"] = (error_detail or "")[:2000]
    if finished:
        updates["finished_at"] = timezone.now()
        updates["lease_expires_at"] = None
        updates["run_after"] = None
    elif run_after:
        updates["run_after"] = timezone.now()
    AgentRun.objects.filter(id=run_id).update(**updates)


def _ensure_run_mutable(run: AgentRun) -> JsonResponse | None:
    if run.status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
        return JsonResponse({"error": "RUN_IMMUTABLE", "message": "Run is already finished."}, status=HTTPStatus.CONFLICT)
    return None


def _create_custom_assistant_session(assistant: CustomAssistant, *, created_by=None, title: str = "", source_conversation: Conversation | None = None) -> Conversation:
    metadata: dict[str, object] = {
        "type": "custom_assistant_session",
        "custom_assistant_id": str(assistant.id),
        "custom_assistant_name": assistant.name,
        "custom_assistant_agent_name": assistant.agent_profile.name,
    }
    if source_conversation is not None:
        metadata["source_conversation_id"] = str(source_conversation.id)
    summary = (title or assistant.name or "Assistant session").strip()
    conversation = Conversation.objects.create(
        business_profile=assistant.business_profile,
        agent_profile=assistant.agent_profile,
        custom_assistant=assistant,
        owner_user=assistant.created_by or assistant.agent_profile.user,
        channel=ConversationChannel.API,
        status=ConversationStatus.LIVE,
        metadata=metadata,
        summary=summary,
    )
    return conversation


def _latest_custom_assistant_session(assistant: CustomAssistant) -> Conversation | None:
    return (
        Conversation.objects.filter(custom_assistant=assistant, business_profile=assistant.business_profile)
        .order_by("-last_activity_at", "-started_at")
        .first()
    )


def _resolve_or_create_custom_assistant_session(assistant: CustomAssistant, *, created_by=None, conversation_id: uuid.UUID | None = None) -> Conversation:
    if conversation_id:
        conversation = Conversation.objects.filter(
            id=conversation_id,
            business_profile=assistant.business_profile,
            custom_assistant=assistant,
        ).first()
        if conversation is None:
            raise ValueError("Custom Assistant session not found.")
        return conversation
    return _latest_custom_assistant_session(assistant) or _create_custom_assistant_session(assistant, created_by=created_by)


def _source_conversation_brief(conversation: Conversation | None) -> dict[str, object]:
    if conversation is None:
        return {}
    messages = list(
        conversation.messages.order_by("-sent_at", "-created_at")
        .only("sender", "body", "sent_at")[:12]
    )
    lines = []
    for message in reversed(messages):
        body = str(message.body or "").strip()
        if not body:
            continue
        lines.append(
            {
                "sender": message.sender,
                "body": body[:600],
                "sentAt": message.sent_at.isoformat() if message.sent_at else None,
            }
        )
    return {
        "sourceConversationId": str(conversation.id),
        "sourceSessionToken": conversation.session_token,
        "messages": lines,
    }


def _cancel_open_automation_runs(automation: Automation, *, reason: str, action: str = "pause") -> int:
    open_statuses = [
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_USER,
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.WAITING_EXTERNAL,
        AgentRunStatus.PAUSED,
    ]
    runs = list(AgentRun.objects.filter(automation=automation, status__in=open_statuses).only("id", "metadata")[:200])
    now = timezone.now()
    cancelled = 0
    for run in runs:
        _append_run_event(
            run.id,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.CANCELLED,
            label="Cancelled by automation deletion" if action == "delete" else "Cancelled by automation pause",
            payload={"reason": reason, "automation_id": str(automation.id)},
        )
        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        next_meta = dict(meta)
        next_meta["cancelled_by_automation"] = action
        if action == "pause":
            next_meta["cancelled_by_automation_pause"] = True
        elif action == "delete":
            next_meta["cancelled_by_automation_delete"] = True
        AgentRun.objects.filter(id=run.id).update(
            status=AgentRunStatus.CANCELLED,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail=reason[:2000],
            metadata=next_meta,
            updated_at=now,
        )
        cancelled += 1
    return cancelled


def _normalize_trigger_type(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw == "cron":
        return AutomationTriggerType.SCHEDULE
    return raw


def _compute_next_trigger(trigger_config: dict[str, Any], *, after=None):
    cron_config = dict(trigger_config)
    cron_config.setdefault("type", "cron")
    return compute_next_automation_schedule_at("cron", cron_config, after=after or timezone.now())


__all__ = [name for name in globals() if not name.startswith("__")]
