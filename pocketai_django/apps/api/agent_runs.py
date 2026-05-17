from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from http import HTTPStatus
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from core.tenancy import tenant_bypass, tenant_context

from apps.accounts.models import AgentProfile, EmailAccountStatus
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
from apps.conversations.run_display import build_agent_run_display
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
from apps.conversations.instruction_contracts import normalize_workflow_instructions
from apps.integrations.models import EmailAccount


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
        "emailAccountId": str(automation.email_account_id) if automation.email_account_id else None,
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
        "pollIntervalSeconds": int(automation.poll_interval_seconds or 0),
        "maxEventsPerPoll": int(automation.max_events_per_poll or 0),
        "lastTriggeredAt": automation.last_triggered_at.isoformat() if automation.last_triggered_at else None,
        "lastPolledAt": automation.last_polled_at.isoformat() if automation.last_polled_at else None,
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


def _compute_next_trigger(trigger_type: str, trigger_config: dict[str, Any], *, after=None):
    if trigger_type != AutomationTriggerType.SCHEDULE:
        return None
    cron_config = dict(trigger_config)
    cron_config.setdefault("type", "cron")
    return compute_next_automation_schedule_at("cron", cron_config, after=after or timezone.now())


@csrf_protect
@require_http_methods(["GET", "POST"])
def custom_assistants_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            qs = (
                CustomAssistant.objects.select_related("agent_profile")
                .filter(agent_profile=agent)
                .annotate(session_count=Count("sessions"))
                .order_by("-created_at")
            )
            if status and status != "all":
                qs = qs.filter(status=status)
            assistants = list(qs[:200])
            return JsonResponse({"customAssistants": [_serialize_custom_assistant(item) for item in assistants]}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)
        status = str((payload or {}).get("status") or CustomAssistantStatus.DRAFT).strip().lower()
        if status not in {choice for choice, _ in CustomAssistantStatus.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
        source_conversation_id, err = _parse_uuid((payload or {}).get("sourceConversationId") or (payload or {}).get("source_conversation_id"), field="sourceConversationId")
        if err:
            return err
        source_conversation = None
        if source_conversation_id:
            source_conversation = Conversation.objects.filter(id=source_conversation_id, business_profile=agent.business_profile).first()
            if source_conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Source conversation not found."}, status=HTTPStatus.NOT_FOUND)

        metadata_payload = dict((payload or {}).get("metadata") or {})
        creation_brief = _source_conversation_brief(source_conversation)
        if creation_brief:
            metadata_payload["creation_brief"] = creation_brief

        assistant = CustomAssistant.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            created_by=request.user,
            name=name[:160],
            description=str((payload or {}).get("description") or "")[:4000],
            status=status,
            instructions=normalize_workflow_instructions((payload or {}).get("instructions") or {}),
            metadata=metadata_payload,
        )
        if bool((payload or {}).get("createSession", (payload or {}).get("create_session", True))):
            session = _create_custom_assistant_session(assistant, created_by=request.user, source_conversation=source_conversation)
            session_meta = dict(session.metadata or {})
            if creation_brief:
                session_meta["creation_brief"] = creation_brief
                Conversation.objects.filter(id=session.id).update(metadata=session_meta, last_activity_at=timezone.now())
        assistant.session_count = Conversation.objects.filter(custom_assistant=assistant).count()
        return JsonResponse({"customAssistant": _serialize_custom_assistant(assistant)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def custom_assistant_detail(request: HttpRequest, agent_id: uuid.UUID, custom_assistant_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        assistant = CustomAssistant.objects.filter(id=custom_assistant_id, agent_profile=agent).first()
        if assistant is None:
            return JsonResponse({"error": "CUSTOM_ASSISTANT_NOT_FOUND", "message": "Custom Assistant not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            assistant.session_count = Conversation.objects.filter(custom_assistant=assistant).count()
            return JsonResponse({"customAssistant": _serialize_custom_assistant(assistant)}, status=HTTPStatus.OK)
        if request.method == "DELETE":
            Conversation.objects.filter(custom_assistant=assistant).delete()
            assistant.delete()
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        payload, error = _parse_json_body(request)
        if error:
            return error
        updates: list[str] = []
        for field in ("name", "description"):
            if field in payload:
                setattr(assistant, field, str(payload.get(field) or "").strip()[: 160 if field == "name" else 4000])
                updates.append(field)
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in {choice for choice, _ in CustomAssistantStatus.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
            assistant.status = status
            updates.append("status")
        for public, field in (("instructions", "instructions"), ("metadata", "metadata")):
            if public in payload or field in payload:
                value = payload.get(public) if public in payload else payload.get(field)
                setattr(assistant, field, normalize_workflow_instructions(value) if field == "instructions" else dict(value or {}))
                updates.append(field)
        if updates:
            assistant.save(update_fields=sorted(set([*updates, "updated_at"])))
        return JsonResponse({"customAssistant": _serialize_custom_assistant(assistant)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET", "POST"])
def custom_assistant_sessions(request: HttpRequest, agent_id: uuid.UUID, custom_assistant_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    with tenant_context(agent.business_profile_id):
        assistant = CustomAssistant.objects.filter(id=custom_assistant_id, agent_profile=agent).first()
        if assistant is None:
            return JsonResponse({"error": "CUSTOM_ASSISTANT_NOT_FOUND", "message": "Custom Assistant not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            sessions = (
                Conversation.objects.filter(custom_assistant=assistant, business_profile=agent.business_profile)
                .annotate(message_count=Count("messages"))
                .order_by("-last_activity_at", "-started_at")[:100]
            )
            return JsonResponse({"sessions": [_serialize_custom_assistant_session(item) for item in sessions]}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        source_id, err = _parse_uuid((payload or {}).get("sourceConversationId") or (payload or {}).get("source_conversation_id"), field="sourceConversationId")
        if err:
            return err
        source_conversation = None
        if source_id:
            source_conversation = Conversation.objects.filter(id=source_id, business_profile=agent.business_profile).first()
            if source_conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Source conversation not found."}, status=HTTPStatus.NOT_FOUND)
        title = str((payload or {}).get("title") or "").strip()
        session = _create_custom_assistant_session(assistant, created_by=request.user, title=title, source_conversation=source_conversation)
        brief = _source_conversation_brief(source_conversation)
        if brief:
            meta = dict(session.metadata or {})
            meta["creation_brief"] = brief
            Conversation.objects.filter(id=session.id).update(metadata=meta, last_activity_at=timezone.now())
            session.metadata = meta
        return JsonResponse({"session": _serialize_custom_assistant_session(session)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "POST"])
def automations_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            trigger_type_filter = _normalize_trigger_type(request.GET.get("triggerType") or request.GET.get("trigger_type")) if str(request.GET.get("triggerType") or request.GET.get("trigger_type") or "").strip() else ""
            qs = Automation.objects.select_related("agent_profile").filter(agent_profile=agent).order_by("-created_at")
            if status and status != "all":
                qs = qs.filter(status=status)
            if trigger_type_filter and trigger_type_filter in {choice for choice, _ in AutomationTriggerType.choices}:
                qs = qs.filter(trigger_type=trigger_type_filter)
            automations = list(qs[:200])
            automation_ids = [item.id for item in automations]
            latest_runs: dict[uuid.UUID, AgentRun] = {}
            open_checkpoints: dict[uuid.UUID, AgentRunCheckpoint] = {}
            if automation_ids:
                run_qs = (
                    AgentRun.objects.select_related("automation")
                    .filter(agent_profile=agent, automation_id__in=automation_ids)
                    .filter(_run_visibility_filter(request, agent=agent))
                    .order_by("-created_at")[:500]
                )
                for run in run_qs:
                    if run.automation_id not in latest_runs:
                        latest_runs[run.automation_id] = run
                    if len(latest_runs) == len(automation_ids):
                        break
                checkpoint_qs = (
                    AgentRunCheckpoint.objects.filter(
                        automation_id__in=automation_ids,
                        status=AgentRunCheckpointStatus.OPEN,
                    )
                    .order_by("-updated_at", "-created_at")[:500]
                )
                for checkpoint in checkpoint_qs:
                    if checkpoint.automation_id and checkpoint.automation_id not in open_checkpoints:
                        open_checkpoints[checkpoint.automation_id] = checkpoint
            for automation in automations:
                automation.open_checkpoint = open_checkpoints.get(automation.id)
            return JsonResponse({"automations": [_serialize_automation(item, latest_runs.get(item.id)) for item in automations]}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)
        status = str((payload or {}).get("status") or AutomationStatus.DRAFT).strip().lower()
        if status not in {choice for choice, _ in AutomationStatus.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
        visibility = str((payload or {}).get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
        review_mode = str((payload or {}).get("reviewMode") or (payload or {}).get("review_mode") or AutomationReviewMode.ON_RISK).strip().lower()
        if review_mode not in {choice for choice, _ in AutomationReviewMode.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid reviewMode."}, status=HTTPStatus.BAD_REQUEST)
        autonomy_mode = str((payload or {}).get("autonomyMode") or (payload or {}).get("autonomy_mode") or AutomationAutonomyMode.DRAFT_FOR_APPROVAL).strip().lower()
        if autonomy_mode not in {choice for choice, _ in AutomationAutonomyMode.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid autonomyMode."}, status=HTTPStatus.BAD_REQUEST)
        trigger_type = _normalize_trigger_type((payload or {}).get("triggerType") or (payload or {}).get("trigger_type"))
        if trigger_type not in {choice for choice, _ in AutomationTriggerType.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid triggerType."}, status=HTTPStatus.BAD_REQUEST)
        trigger_config = dict((payload or {}).get("triggerConfig") or (payload or {}).get("trigger_config") or {})
        if trigger_type == AutomationTriggerType.WEBHOOK and not str(trigger_config.get("secret") or "").strip():
            trigger_config["secret"] = secrets.token_urlsafe(24)
        next_trigger_at = None
        if status == AutomationStatus.ACTIVE:
            try:
                next_trigger_at = _compute_next_trigger(trigger_type, trigger_config, after=timezone.now())
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        email_account = None
        email_account_id, err = _parse_uuid((payload or {}).get("emailAccountId") or (payload or {}).get("email_account_id"), field="emailAccountId")
        if err:
            return err
        if trigger_type == AutomationTriggerType.EMAIL_INBOX:
            if not email_account_id:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "emailAccountId is required for email inbox automations."}, status=HTTPStatus.BAD_REQUEST)
            account_qs = EmailAccount.objects.filter(id=email_account_id, business_profile=agent.business_profile)
            if not request.user.is_staff:
                account_qs = account_qs.filter(user=request.user)
            email_account = account_qs.first()
            if email_account is None:
                return JsonResponse({"error": "EMAIL_ACCOUNT_NOT_FOUND", "message": "Email account not found."}, status=HTTPStatus.NOT_FOUND)
            if email_account.status != EmailAccountStatus.CONNECTED:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Email account must be connected before enabling an email inbox automation."}, status=HTTPStatus.BAD_REQUEST)

        automation = Automation.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            created_by=request.user,
            email_account=email_account,
            name=name[:160],
            description=str((payload or {}).get("description") or "")[:4000],
            status=status,
            visibility=visibility,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            source_config=dict((payload or {}).get("sourceConfig") or (payload or {}).get("source_config") or {}),
            destination_config=dict((payload or {}).get("destinationConfig") or (payload or {}).get("destination_config") or {}),
            notification_config=dict((payload or {}).get("notificationConfig") or (payload or {}).get("notification_config") or {}),
            review_mode=review_mode,
            autonomy_mode=autonomy_mode,
            instructions=normalize_workflow_instructions((payload or {}).get("instructions") or {}),
            state=dict((payload or {}).get("state") or {}),
            poll_interval_seconds=max(60, min(int((payload or {}).get("pollIntervalSeconds") or (payload or {}).get("poll_interval_seconds") or 300), 86400)),
            max_events_per_poll=max(1, min(int((payload or {}).get("maxEventsPerPoll") or (payload or {}).get("max_events_per_poll") or 5), 25)),
            next_trigger_at=next_trigger_at,
            metadata=dict((payload or {}).get("metadata") or {}),
        )
        return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def automation_detail(request: HttpRequest, agent_id: uuid.UUID, automation_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        automation = Automation.objects.filter(id=automation_id, agent_profile=agent).first()
        if automation is None:
            return JsonResponse({"error": "AUTOMATION_NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            automation.open_checkpoint = (
                AgentRunCheckpoint.objects.filter(automation=automation, status=AgentRunCheckpointStatus.OPEN)
                .order_by("-updated_at", "-created_at")
                .first()
            )
            return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)
        if request.method == "DELETE":
            with transaction.atomic():
                automation = (
                    Automation.objects.select_for_update()
                    .get(id=automation.id)
                )
                _cancel_open_automation_runs(automation, reason="Automation deleted", action="delete")
                automation.delete()
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        payload, error = _parse_json_body(request)
        if error:
            return error
        updates: list[str] = []
        for field in ("name", "description"):
            if field in payload:
                setattr(automation, field, str(payload.get(field) or "").strip()[: 160 if field == "name" else 4000])
                updates.append(field)
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in {choice for choice, _ in AutomationStatus.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
            automation.status = status
            updates.append("status")
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            automation.visibility = visibility
            updates.append("visibility")
        if "reviewMode" in payload or "review_mode" in payload:
            review_mode = str(payload.get("reviewMode") or payload.get("review_mode") or "").strip().lower()
            if review_mode not in {choice for choice, _ in AutomationReviewMode.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid reviewMode."}, status=HTTPStatus.BAD_REQUEST)
            automation.review_mode = review_mode
            updates.append("review_mode")
        if "autonomyMode" in payload or "autonomy_mode" in payload:
            autonomy_mode = str(payload.get("autonomyMode") or payload.get("autonomy_mode") or "").strip().lower()
            if autonomy_mode not in {choice for choice, _ in AutomationAutonomyMode.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid autonomyMode."}, status=HTTPStatus.BAD_REQUEST)
            automation.autonomy_mode = autonomy_mode
            updates.append("autonomy_mode")
        if "triggerType" in payload or "trigger_type" in payload:
            trigger_type = _normalize_trigger_type(payload.get("triggerType") or payload.get("trigger_type"))
            if trigger_type not in {choice for choice, _ in AutomationTriggerType.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid triggerType."}, status=HTTPStatus.BAD_REQUEST)
            automation.trigger_type = trigger_type
            updates.append("trigger_type")
        for public, field in (("triggerConfig", "trigger_config"), ("sourceConfig", "source_config"), ("destinationConfig", "destination_config"), ("notificationConfig", "notification_config"), ("instructions", "instructions"), ("state", "state"), ("metadata", "metadata")):
            if public in payload or field in payload:
                value = payload.get(public) if public in payload else payload.get(field)
                setattr(automation, field, normalize_workflow_instructions(value) if field == "instructions" else dict(value or {}))
                updates.append(field)
        if "pollIntervalSeconds" in payload or "poll_interval_seconds" in payload:
            automation.poll_interval_seconds = max(60, min(int(payload.get("pollIntervalSeconds") or payload.get("poll_interval_seconds") or 300), 86400))
            updates.append("poll_interval_seconds")
        if "maxEventsPerPoll" in payload or "max_events_per_poll" in payload:
            automation.max_events_per_poll = max(1, min(int(payload.get("maxEventsPerPoll") or payload.get("max_events_per_poll") or 5), 25))
            updates.append("max_events_per_poll")
        if "emailAccountId" in payload or "email_account_id" in payload:
            email_account_id, err = _parse_uuid(payload.get("emailAccountId") or payload.get("email_account_id"), field="emailAccountId")
            if err:
                return err
            email_account = None
            if email_account_id:
                account_qs = EmailAccount.objects.filter(id=email_account_id, business_profile=agent.business_profile)
                if not request.user.is_staff:
                    account_qs = account_qs.filter(user=request.user)
                email_account = account_qs.first()
                if email_account is None:
                    return JsonResponse({"error": "EMAIL_ACCOUNT_NOT_FOUND", "message": "Email account not found."}, status=HTTPStatus.NOT_FOUND)
            automation.email_account = email_account
            updates.append("email_account")
        if automation.trigger_type == AutomationTriggerType.WEBHOOK:
            cfg = dict(automation.trigger_config or {})
            if not str(cfg.get("secret") or "").strip():
                cfg["secret"] = secrets.token_urlsafe(24)
                automation.trigger_config = cfg
                updates.append("trigger_config")
        if {"status", "trigger_type", "trigger_config"} & set(updates):
            try:
                automation.next_trigger_at = _compute_next_trigger(automation.trigger_type, automation.trigger_config, after=timezone.now()) if automation.status == AutomationStatus.ACTIVE else None
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            updates.append("next_trigger_at")
        if not updates:
            return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)
        automation.save(update_fields=sorted(set([*updates, "updated_at"])))
        if "status" in updates and automation.status == AutomationStatus.PAUSED:
            cancel_existing = bool((payload or {}).get("cancelOpenRuns", True))
            if cancel_existing:
                cancelled = _cancel_open_automation_runs(automation, reason="Automation paused")
                automation_meta = dict(automation.metadata or {}) if isinstance(automation.metadata, dict) else {}
                automation_meta["last_pause_cancelled_runs"] = cancelled
                Automation.objects.filter(id=automation.id).update(metadata=automation_meta, updated_at=timezone.now())
                automation.metadata = automation_meta
        return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET"])
def agent_operations_status(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    now = timezone.now()
    stale_before = now - timedelta(seconds=90)
    automation_heartbeat = cache.get("automation_processor_heartbeat")
    run_heartbeat = cache.get("agent_run_processor_heartbeat")

    def _heartbeat_payload(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {"active": False, "lastSeenAt": None}
        raw_seen = value.get("at")
        last_seen = None
        if raw_seen:
            try:
                last_seen = datetime.fromisoformat(str(raw_seen))
                if timezone.is_naive(last_seen):
                    last_seen = timezone.make_aware(last_seen, timezone=dt_timezone.utc)
            except ValueError:
                last_seen = None
        active = bool(last_seen and last_seen >= stale_before)
        return {"active": active, "lastSeenAt": last_seen.isoformat() if last_seen else None}

    with tenant_context(agent.business_profile_id):
        runs = AgentRun.objects.filter(agent_profile=agent)
        automations = Automation.objects.filter(agent_profile=agent)
        email_accounts = EmailAccount.objects.filter(business_profile=agent.business_profile)
        if not request.user.is_staff:
            email_accounts = email_accounts.filter(user=request.user)
        payload = {
            "operations": {
                "taskProcessingActive": bool(_heartbeat_payload(automation_heartbeat)["active"] and _heartbeat_payload(run_heartbeat)["active"]),
                "automationProcessor": _heartbeat_payload(automation_heartbeat),
                "runProcessor": _heartbeat_payload(run_heartbeat),
                "dueAutomations": automations.filter(
                    status=AutomationStatus.ACTIVE,
                    trigger_type__in=[AutomationTriggerType.SCHEDULE, AutomationTriggerType.EMAIL_INBOX],
                    next_trigger_at__lte=now,
                ).count(),
                "queuedRuns": runs.filter(status=AgentRunStatus.QUEUED).count(),
                "runningRuns": runs.filter(status=AgentRunStatus.RUNNING).count(),
                "failedRuns": runs.filter(status=AgentRunStatus.FAILED).count(),
            },
            "emailAccounts": [
                {
                    "id": str(account.id),
                    "provider": account.provider,
                    "email": account.email_address,
                    "displayName": account.email_address or account.get_provider_display(),
                    "status": account.status,
                }
                for account in email_accounts.order_by("provider", "email_address")[:100]
            ],
        }
    return JsonResponse(payload, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def automation_run(request: HttpRequest, agent_id: uuid.UUID, automation_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    with tenant_context(agent.business_profile_id):
        automation = Automation.objects.filter(id=automation_id, agent_profile=agent).first()
        if automation is None:
            return JsonResponse({"error": "AUTOMATION_NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)
        payload, error = _parse_json_body(request)
        if error:
            return error
        conversation_id, err = _parse_uuid((payload or {}).get("conversationId") or (payload or {}).get("conversation_id"), field="conversationId")
        if err:
            return err
        conversation = None
        if conversation_id:
            conversation = Conversation.objects.filter(id=conversation_id, business_profile=agent.business_profile).first()
            if conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."}, status=HTTPStatus.NOT_FOUND)
        elif automation.conversation_id:
            conversation = automation.conversation
        run = _create_run(
            agent=agent,
            created_by=request.user,
            automation=automation,
            conversation=conversation,
            title=automation.name,
            source=AgentRunSource.AUTOMATION,
            visibility=automation.visibility,
            snapshot=_run_snapshot(automation),
            metadata={"automation_id": str(automation.id), "trigger": "manual"},
        )
        Automation.objects.filter(id=automation.id).update(last_triggered_at=timezone.now(), updated_at=timezone.now())
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_checkpoint_resolve(request: HttpRequest, agent_id: uuid.UUID, checkpoint_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    payload, error = _parse_json_body(request)
    if error:
        return error
    action = str((payload or {}).get("action") or (payload or {}).get("decision") or "resolve").strip().lower()
    message = str((payload or {}).get("message") or (payload or {}).get("note") or "").strip()
    extra = dict((payload or {}).get("payload") or {})
    if action not in {"approve", "deny", "reply", "resolve", "cancel", "expire"}:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid checkpoint action."}, status=HTTPStatus.BAD_REQUEST)

    with tenant_context(agent.business_profile_id):
        checkpoint = (
            AgentRunCheckpoint.objects.select_related("run", "automation", "conversation")
            .filter(id=checkpoint_id, business_profile=agent.business_profile)
            .filter(Q(automation__agent_profile=agent) | Q(run__agent_profile=agent))
            .first()
        )
        if checkpoint is None:
            return JsonResponse({"error": "CHECKPOINT_NOT_FOUND", "message": "Checkpoint not found."}, status=HTTPStatus.NOT_FOUND)
        if checkpoint.status != AgentRunCheckpointStatus.OPEN:
            return JsonResponse({"error": "CHECKPOINT_CLOSED", "message": "Checkpoint is already resolved."}, status=HTTPStatus.CONFLICT)
        run = checkpoint.run
        now = timezone.now()
        resolution = {"action": action, "message": message, "payload": extra}
        checkpoint.status = AgentRunCheckpointStatus.EXPIRED if action == "expire" else (
            AgentRunCheckpointStatus.CANCELLED if action == "cancel" else AgentRunCheckpointStatus.RESOLVED
        )
        checkpoint.resolution = resolution
        checkpoint.resolved_at = now
        checkpoint.resolved_by = request.user if request.user.is_authenticated else None
        checkpoint.save(update_fields=["status", "resolution", "resolved_at", "resolved_by", "updated_at"])

        event_type = AgentRunEventType.CANCELLED if action in {"deny", "cancel"} else AgentRunEventType.PROGRESS
        _append_run_event(
            run.id,
            stream=AgentRunEventStream.EXECUTED,
            event_type=event_type,
            label="Checkpoint resolved",
            payload={"checkpoint_id": str(checkpoint.id), **resolution},
        )
        MemoryItem.objects.create(
            business_profile=run.business_profile,
            scope=MemoryScope.RUN,
            agent_profile=run.agent_profile,
            automation=run.automation,
            run=run,
            conversation=run.conversation,
            kind=MemoryKind.DECISION if checkpoint.kind == AgentRunCheckpointKind.APPROVAL else MemoryKind.STATE_NOTE,
            key=f"checkpoint_{checkpoint.kind}",
            content=message[:4000] or action,
            payload={"checkpoint_id": str(checkpoint.id), **resolution},
            visibility=MemoryVisibility.PRIVATE,
            status=MemoryStatus.ACTIVE,
            created_by=request.user if request.user.is_authenticated else None,
        )

        next_meta = dict(run.metadata or {}) if isinstance(getattr(run, "metadata", None), dict) else {}
        next_meta.pop("pending_checkpoint_id", None)
        if checkpoint.kind == AgentRunCheckpointKind.APPROVAL:
            next_meta.pop("pending_approval_id", None)
        elif checkpoint.kind == AgentRunCheckpointKind.USER_INPUT:
            next_meta.pop("pending_user_input", None)
        elif checkpoint.kind == AgentRunCheckpointKind.CHILD_RUN:
            next_meta.pop("pending_child_run_id", None)
        elif checkpoint.kind == AgentRunCheckpointKind.EXTERNAL:
            next_meta.pop("pending_agent_request_id", None)
            next_meta.pop("pending_call_session_id", None)

        if action in {"deny", "cancel"}:
            _set_run_status(run.id, status=AgentRunStatus.CANCELLED, error_detail=message or action, finished=True)
        elif action == "expire":
            AgentRun.objects.filter(id=run.id).update(
                status=AgentRunStatus.PAUSED,
                lease_expires_at=None,
                run_after=None,
                metadata=next_meta,
                error_detail=message or "checkpoint expired",
                updated_at=now,
            )
        else:
            execution_conversation = None
            if run.execution_conversation_id:
                execution_conversation = Conversation.objects.filter(id=run.execution_conversation_id, business_profile=agent.business_profile).first()
            if execution_conversation and (message or extra):
                body = message or "Checkpoint response:\n" + json.dumps(extra, ensure_ascii=False)
                Conversation.objects.filter(id=execution_conversation.id).update(last_activity_at=now)
                execution_conversation.messages.create(
                    sender=ConversationSender.CUSTOMER,
                    body=body,
                    metadata={"source": "agent_run_checkpoint", "agent_run_id": str(run.id), "checkpoint_id": str(checkpoint.id)},
                )
            AgentRun.objects.filter(id=run.id).update(
                status=AgentRunStatus.QUEUED,
                run_after=now,
                lease_expires_at=None,
                finished_at=None,
                error_detail="",
                metadata=next_meta,
                updated_at=now,
            )
        run.refresh_from_db()
        checkpoint.refresh_from_db()
    return JsonResponse({"checkpoint": _serialize_checkpoint(checkpoint), "run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_exempt
@require_POST
def automation_webhook_trigger(request: HttpRequest, automation_id: uuid.UUID, token: str) -> JsonResponse:
    token_value = str(token or "").strip()
    if not token_value:
        return JsonResponse({"error": "NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)
    with tenant_bypass():
        automation = (
            Automation.objects.select_related("agent_profile", "business_profile", "conversation")
            .filter(id=automation_id, trigger_type=AutomationTriggerType.WEBHOOK, status=AutomationStatus.ACTIVE)
            .first()
        )
        if automation is None:
            return JsonResponse({"error": "NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)
        secret_value = str((automation.trigger_config or {}).get("secret") or "").strip()
        if not secret_value or not secrets.compare_digest(secret_value, token_value):
            return JsonResponse({"error": "NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)
        run = _create_run(
            agent=automation.agent_profile,
            created_by=None,
            automation=automation,
            conversation=automation.conversation,
            title=automation.name,
            source=AgentRunSource.WEBHOOK,
            visibility=automation.visibility,
            snapshot=_run_snapshot(automation),
            metadata={"automation_id": str(automation.id), "trigger": "webhook"},
        )
        Automation.objects.filter(id=automation.id).update(last_triggered_at=timezone.now(), updated_at=timezone.now())
        return JsonResponse({"runId": str(run.id)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "POST"])
def agent_runs_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            qs = (
                AgentRun.objects.select_related("automation")
                .prefetch_related("artifacts")
                .filter(agent_profile=agent)
                .filter(_run_visibility_filter(request, agent=agent))
                .order_by("-created_at")
            )
            kind = str(request.GET.get("kind") or request.GET.get("type") or "").strip().lower()
            if kind in {"custom_assistant", "custom_assistants", "assistant", "assistants", "manual"}:
                qs = qs.none()
            elif kind in {"automation", "automations", "scheduled_task", "scheduled_tasks", "background"}:
                qs = qs.filter(automation__isnull=False)
            status = str(request.GET.get("status") or "").strip().lower()
            if status:
                qs = qs.filter(status=status)
            automation_id, err = _parse_uuid(request.GET.get("automationId") or request.GET.get("automation_id"), field="automationId")
            if err:
                return err
            if automation_id:
                qs = qs.filter(automation_id=automation_id)
            limit = max(1, min(int(str(request.GET.get("limit") or "50")), 200))
            offset = max(0, int(str(request.GET.get("offset") or "0")))
            return JsonResponse({"runs": [_serialize_run(item) for item in qs[offset : offset + limit]], "total": qs.count(), "limit": limit, "offset": offset}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        automation_id, err = _parse_uuid((payload or {}).get("automationId") or (payload or {}).get("automation_id"), field="automationId")
        if err:
            return err
        automation = None
        if automation_id:
            automation = Automation.objects.filter(id=automation_id, agent_profile=agent).first()
            if automation is None:
                return JsonResponse({"error": "AUTOMATION_NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)
        conversation_id, err = _parse_uuid((payload or {}).get("conversationId") or (payload or {}).get("conversation_id"), field="conversationId")
        if err:
            return err
        conversation = None
        if conversation_id:
            conversation = Conversation.objects.filter(id=conversation_id, business_profile=agent.business_profile).first()
            if conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."}, status=HTTPStatus.NOT_FOUND)
        elif automation and automation.conversation_id:
            conversation = automation.conversation
        visibility = str((payload or {}).get("visibility") or (automation.visibility if automation else AgentRunVisibility.INITIATOR)).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
        source = str((payload or {}).get("source") or (AgentRunSource.AUTOMATION if automation else AgentRunSource.CHAT)).strip().lower()
        if source not in {choice for choice, _ in AgentRunSource.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid source."}, status=HTTPStatus.BAD_REQUEST)
        run = _create_run(
            agent=agent,
            created_by=request.user,
            automation=automation,
            conversation=conversation,
            title=str((payload or {}).get("title") or (automation.name if automation else "")),
            source=source,
            visibility=visibility,
            snapshot=_run_snapshot(automation, (payload or {}).get("runSnapshot") or (payload or {}).get("automation") or {}),
            metadata=dict((payload or {}).get("metadata") or {}),
        )
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PATCH", "PUT"])
def agent_run_detail(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    with tenant_context(agent.business_profile_id):
        run = AgentRun.objects.filter(id=run_id, agent_profile=agent).filter(_run_visibility_filter(request, agent=agent)).first()
        if run is None:
            return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)
        payload, error = _parse_json_body(request)
        if error:
            return error
        updates: list[str] = []
        for field in ("title", "plan", "metadata"):
            if field in payload:
                setattr(run, field, dict(payload.get(field) or {}) if field in {"plan", "metadata"} else str(payload.get(field) or "")[:200])
                updates.append(field)
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            run.visibility = visibility
            updates.append("visibility")
        if updates:
            run.save(update_fields=[*updates, "updated_at"])
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET"])
def agent_run_events(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    run = AgentRun.objects.filter(id=run_id, agent_profile=agent).filter(_run_visibility_filter(request, agent=agent)).first()
    if run is None:
        return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)
    limit = max(1, min(int(str(request.GET.get("limit") or "200")), 500))
    qs = AgentRunEvent.objects.filter(run=run).order_by("sequence_index")
    after = request.GET.get("after")
    if after:
        try:
            qs = qs.filter(sequence_index__gt=int(str(after)))
        except ValueError:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "after must be an integer."}, status=HTTPStatus.BAD_REQUEST)
    return JsonResponse({"events": [_serialize_run_event(event) for event in qs[:limit]]}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_cancel(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    return _run_state_action(request, agent_id, run_id, action="cancel")


@csrf_protect
@require_http_methods(["POST"])
def agent_run_resume(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    return _run_state_action(request, agent_id, run_id, action="resume")


def _run_state_action(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID, *, action: str) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    payload, error = _parse_json_body(request)
    if error:
        return error
    run = AgentRun.objects.filter(id=run_id, agent_profile=agent).filter(_run_visibility_filter(request, agent=agent)).first()
    if run is None:
        return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)
    immutable = _ensure_run_mutable(run)
    if immutable:
        return immutable
    reason = str((payload or {}).get("reason") or "").strip()
    if action == "cancel":
        _append_run_event(run.id, stream=AgentRunEventStream.EXECUTED, event_type=AgentRunEventType.CANCELLED, label="Cancelled", payload={"reason": reason} if reason else {})
        _set_run_status(run.id, status=AgentRunStatus.CANCELLED, error_detail=reason or "cancelled", finished=True)
    else:
        _append_run_event(run.id, stream=AgentRunEventStream.EXECUTED, event_type=AgentRunEventType.PROGRESS, label="Resumed", payload={"reason": reason} if reason else {})
        _set_run_status(run.id, status=AgentRunStatus.QUEUED, run_after=True)
    run.refresh_from_db()
    return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_user_input(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    return _run_note_action(request, agent_id, run_id, kind=MemoryKind.STATE_NOTE, event_label="User input received", status=AgentRunStatus.QUEUED)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_approval(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    return _run_note_action(request, agent_id, run_id, kind=MemoryKind.DECISION, event_label="Approval resolved", status=None)


def _run_note_action(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID, *, kind: str, event_label: str, status: str | None) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    payload, error = _parse_json_body(request)
    if error:
        return error
    run = AgentRun.objects.filter(id=run_id, agent_profile=agent).filter(_run_visibility_filter(request, agent=agent)).first()
    if run is None:
        return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)
    immutable = _ensure_run_mutable(run)
    if immutable:
        return immutable
    message = str((payload or {}).get("message") or (payload or {}).get("note") or (payload or {}).get("decision") or "").strip()
    extra = dict((payload or {}).get("payload") or {})
    _append_run_event(run.id, stream=AgentRunEventStream.EXECUTED, event_type=AgentRunEventType.PROGRESS, label=event_label, payload={"message": message, "payload": extra})
    item = MemoryItem.objects.create(
        business_profile=run.business_profile,
        scope=MemoryScope.RUN,
        agent_profile=run.agent_profile,
        automation=run.automation,
        run=run,
        conversation=run.conversation,
        kind=kind,
        key="approval" if kind == MemoryKind.DECISION else "user_input",
        content=message[:4000],
        payload=extra,
        visibility=MemoryVisibility.PRIVATE,
        status=MemoryStatus.ACTIVE,
        created_by=request.user,
    )
    if run.automation_id:
        MemoryItem.objects.create(
            business_profile=run.business_profile,
            scope=MemoryScope.AUTOMATION,
            agent_profile=run.agent_profile,
            automation=run.automation,
            run=run,
            conversation=run.conversation,
            kind=kind,
            key="approval" if kind == MemoryKind.DECISION else "user_input",
            content=message[:4000],
            payload={**extra, "source_run_id": str(run.id)},
            visibility=MemoryVisibility.SHARED,
            status=MemoryStatus.ACTIVE,
            created_by=request.user,
        )
    MemoryAuditEvent.objects.create(memory_item=item, business_profile=item.business_profile, actor_user=request.user, action=MemoryAuditAction.CREATED, after=_serialize_memory(item))
    if kind == MemoryKind.DECISION and str((payload or {}).get("decision") or "").strip().lower() == "deny":
        _set_run_status(run.id, status=AgentRunStatus.CANCELLED, error_detail=message or "denied", finished=True)
    else:
        _set_run_status(run.id, status=status or AgentRunStatus.QUEUED, run_after=True)
    run.refresh_from_db()
    return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET"])
def memory_collection(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    agent_id, err = _parse_uuid(request.GET.get("agentId") or request.GET.get("agent_id"), field="agentId")
    if err:
        return err
    business_id, err = _parse_uuid(request.GET.get("businessId") or request.GET.get("business_id"), field="businessId")
    if err:
        return err
    qs = MemoryItem.objects.select_related("business_profile", "agent_profile", "custom_assistant", "automation", "run", "conversation")
    if business_id:
        qs = qs.filter(business_profile_id=business_id)
    else:
        qs = qs.filter(Q(business_profile__user=request.user) | Q(agent_profile__user=request.user))
    if agent_id:
        qs = qs.filter(Q(agent_profile_id=agent_id) | Q(visibility=MemoryVisibility.SHARED))
    view = str(request.GET.get("view") or "saved").strip().lower()
    if view not in {"saved", "pending_review"}:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid memory view."}, status=HTTPStatus.BAD_REQUEST)
    status = MemoryStatus.PENDING_REVIEW if view == "pending_review" else MemoryStatus.ACTIVE
    qs = qs.filter(_curated_memory_filter(), status=status)
    query = str(request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(Q(content__icontains=query) | Q(key__icontains=query))
    limit = max(1, min(int(str(request.GET.get("limit") or "50")), 200))
    return JsonResponse(
        {"memory": [_serialize_memory(item) for item in qs.order_by("-updated_at")[:limit]], "view": view},
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET"])
def memory_detail(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    item = MemoryItem.objects.filter(id=memory_id).filter(Q(business_profile__user=request.user) | Q(agent_profile__user=request.user)).first()
    if item is None:
        return JsonResponse({"error": "MEMORY_NOT_FOUND", "message": "Memory item not found."}, status=HTTPStatus.NOT_FOUND)
    return JsonResponse({"memory": _serialize_memory(item)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def memory_approve(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.ACTIVE, action=MemoryAuditAction.APPROVED)


@csrf_protect
@require_http_methods(["POST"])
def memory_reject(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.ARCHIVED, action=MemoryAuditAction.REJECTED)


@csrf_protect
@require_http_methods(["POST"])
def memory_archive(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.ARCHIVED, action=MemoryAuditAction.ARCHIVED)


@csrf_protect
@require_http_methods(["POST"])
def memory_delete(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.DELETED, action=MemoryAuditAction.DELETED)


def _memory_status_action(request: HttpRequest, memory_id: uuid.UUID, *, status: str, action: str) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    item = MemoryItem.objects.filter(id=memory_id).filter(Q(business_profile__user=request.user) | Q(agent_profile__user=request.user)).first()
    if item is None:
        return JsonResponse({"error": "MEMORY_NOT_FOUND", "message": "Memory item not found."}, status=HTTPStatus.NOT_FOUND)
    before = _serialize_memory(item)
    item.status = status
    item.reviewed_by = request.user
    item.reviewed_at = timezone.now()
    item.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    MemoryAuditEvent.objects.create(memory_item=item, business_profile=item.business_profile, actor_user=request.user, action=action, before=before, after=_serialize_memory(item))
    return JsonResponse({"memory": _serialize_memory(item)}, status=HTTPStatus.OK)
