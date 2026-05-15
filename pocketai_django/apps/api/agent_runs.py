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
from apps.conversations.workflow_scheduling import CronScheduleError, compute_next_workflow_schedule_at
from apps.conversations.run_display import build_agent_run_display
from apps.conversations.models import (
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
    AgentWorkflow,
    AgentWorkflowAutonomyMode,
    AgentWorkflowReviewMode,
    AgentWorkflowStatus,
    AgentWorkflowTriggerType,
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
from apps.conversations.workflow_contracts import normalize_workflow_instructions
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
    qs = AgentProfile.objects.select_related("business_profile", "department")
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


def _workflow_snapshot(workflow: AgentWorkflow | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if workflow is not None:
        instructions = workflow.instructions if isinstance(workflow.instructions, dict) else {}
        return normalize_workflow_instructions(
            {
                **instructions,
                "name": workflow.name,
                "description": workflow.description,
                "trigger_type": workflow.trigger_type,
                "trigger_config": workflow.trigger_config if isinstance(workflow.trigger_config, dict) else {},
                "source_config": workflow.source_config if isinstance(workflow.source_config, dict) else {},
                "destination_config": workflow.destination_config if isinstance(workflow.destination_config, dict) else {},
                "notification_config": workflow.notification_config if isinstance(workflow.notification_config, dict) else {},
                "review_mode": workflow.review_mode,
                "autonomy_mode": workflow.autonomy_mode,
            }
        )
    return normalize_workflow_instructions(payload or {})


def _run_workflow_id(run: AgentRun) -> str | None:
    if run.workflow_id:
        return str(run.workflow_id)
    metadata = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
    value = str(metadata.get("workflow_id") or metadata.get("workflowId") or "").strip()
    return value or None


def _run_workflow_name(run: AgentRun) -> str:
    workflow_name = getattr(getattr(run, "workflow", None), "name", "") or ""
    if workflow_name:
        return workflow_name
    snapshot = run.workflow_snapshot if isinstance(getattr(run, "workflow_snapshot", None), dict) else {}
    return str(snapshot.get("name") or snapshot.get("task") or "").strip()


def _serialize_run_summary(run: AgentRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "workflowId": _run_workflow_id(run),
        "workflowName": _run_workflow_name(run),
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
        "workflowId": str(checkpoint.workflow_id) if checkpoint.workflow_id else None,
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


def _serialize_workflow_session(conversation: Conversation) -> dict[str, object]:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
    return {
        "id": str(conversation.id),
        "conversationId": str(conversation.id),
        "sessionToken": conversation.session_token,
        "workflowId": str(conversation.workflow_id) if conversation.workflow_id else None,
        "title": conversation.summary or str(metadata.get("title") or metadata.get("workflow_name") or metadata.get("workflowName") or "").strip() or "New workflow session",
        "status": conversation.status,
        "messageCount": getattr(conversation, "message_count", None),
        "startedAt": conversation.started_at.isoformat() if conversation.started_at else None,
        "lastActivityAt": conversation.last_activity_at.isoformat() if conversation.last_activity_at else None,
        "metadata": metadata,
    }


def _serialize_workflow(workflow: AgentWorkflow, latest_run: AgentRun | None = None) -> dict[str, object]:
    open_checkpoint = getattr(workflow, "open_checkpoint", None)
    session_count = getattr(workflow, "session_count", None)
    return {
        "id": str(workflow.id),
        "agentId": str(workflow.agent_profile_id),
        "agentName": getattr(getattr(workflow, "agent_profile", None), "name", "") or "",
        "businessId": str(workflow.business_profile_id),
        "departmentId": str(workflow.department_id) if workflow.department_id else None,
        "departmentName": getattr(getattr(workflow, "department", None), "name", "") or "",
        "conversationId": str(workflow.conversation_id) if workflow.conversation_id else None,
        "emailAccountId": str(workflow.email_account_id) if workflow.email_account_id else None,
        "name": workflow.name,
        "description": workflow.description or "",
        "status": workflow.status,
        "visibility": workflow.visibility,
        "triggerType": workflow.trigger_type,
        "triggerConfig": workflow.trigger_config if isinstance(workflow.trigger_config, dict) else {},
        "sourceConfig": workflow.source_config if isinstance(workflow.source_config, dict) else {},
        "destinationConfig": workflow.destination_config if isinstance(workflow.destination_config, dict) else {},
        "notificationConfig": workflow.notification_config if isinstance(workflow.notification_config, dict) else {},
        "reviewMode": workflow.review_mode,
        "autonomyMode": workflow.autonomy_mode,
        "instructions": workflow.instructions if isinstance(workflow.instructions, dict) else {},
        "state": workflow.state if isinstance(workflow.state, dict) else {},
        "pollIntervalSeconds": int(workflow.poll_interval_seconds or 0),
        "maxEventsPerPoll": int(workflow.max_events_per_poll or 0),
        "lastTriggeredAt": workflow.last_triggered_at.isoformat() if workflow.last_triggered_at else None,
        "lastPolledAt": workflow.last_polled_at.isoformat() if workflow.last_polled_at else None,
        "nextTriggerAt": workflow.next_trigger_at.isoformat() if workflow.next_trigger_at else None,
        "leaseExpiresAt": workflow.lease_expires_at.isoformat() if workflow.lease_expires_at else None,
        "errorCount": int(workflow.error_count or 0),
        "lastError": workflow.last_error or "",
        "latestRun": _serialize_run_summary(latest_run) if latest_run is not None else None,
        "openCheckpoint": _serialize_checkpoint(open_checkpoint) if isinstance(open_checkpoint, AgentRunCheckpoint) else None,
        "sessionCount": int(session_count or 0),
        "attentionState": "needs_attention" if isinstance(open_checkpoint, AgentRunCheckpoint) else ("active" if latest_run and latest_run.status in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING, AgentRunStatus.WAITING_CHILD, AgentRunStatus.WAITING_EXTERNAL} else workflow.status),
        "metadata": workflow.metadata if isinstance(workflow.metadata, dict) else {},
        "createdBy": str(workflow.created_by_id) if workflow.created_by_id else None,
        "createdAt": workflow.created_at.isoformat() if workflow.created_at else None,
        "updatedAt": workflow.updated_at.isoformat() if workflow.updated_at else None,
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
        "workflowId": _run_workflow_id(run),
        "workflowName": _run_workflow_name(run),
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
        "workflowId": str(item.workflow_id) if item.workflow_id else None,
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
    workflow: AgentWorkflow | None,
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
        workflow=workflow,
        parent_run=parent_run,
        delegated_by_agent=delegated_by_agent,
        workflow_snapshot=snapshot,
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
        payload={"status": AgentRunStatus.QUEUED, "workflow_id": str(workflow.id) if workflow else None},
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


def _create_workflow_session(workflow: AgentWorkflow, *, created_by=None, title: str = "", source_conversation: Conversation | None = None) -> Conversation:
    metadata: dict[str, object] = {
        "type": "workflow_agent_session",
        "workflow_id": str(workflow.id),
        "workflow_name": workflow.name,
        "workflow_agent_name": workflow.agent_profile.name,
        "workflow_department_name": workflow.department.name if workflow.department_id else "",
    }
    if source_conversation is not None:
        metadata["source_conversation_id"] = str(source_conversation.id)
    summary = (title or workflow.name or "Workflow session").strip()
    conversation = Conversation.objects.create(
        business_profile=workflow.business_profile,
        agent_profile=workflow.agent_profile,
        workflow=workflow,
        owner_user=workflow.created_by or workflow.agent_profile.user,
        channel=ConversationChannel.API,
        status=ConversationStatus.LIVE,
        metadata=metadata,
        summary=summary,
    )
    return conversation


def _latest_workflow_session(workflow: AgentWorkflow) -> Conversation | None:
    return (
        Conversation.objects.filter(workflow=workflow, business_profile=workflow.business_profile)
        .order_by("-last_activity_at", "-started_at")
        .first()
    )


def _resolve_or_create_workflow_session(workflow: AgentWorkflow, *, created_by=None, conversation_id: uuid.UUID | None = None) -> Conversation:
    if conversation_id:
        conversation = Conversation.objects.filter(
            id=conversation_id,
            business_profile=workflow.business_profile,
            workflow=workflow,
        ).first()
        if conversation is None:
            raise ValueError("Workflow session not found.")
        return conversation
    return _latest_workflow_session(workflow) or _create_workflow_session(workflow, created_by=created_by)


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


def _cancel_open_workflow_runs(workflow: AgentWorkflow, *, reason: str, action: str = "pause") -> int:
    open_statuses = [
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_USER,
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.WAITING_EXTERNAL,
        AgentRunStatus.PAUSED,
    ]
    runs = list(AgentRun.objects.filter(workflow=workflow, status__in=open_statuses).only("id", "metadata")[:200])
    now = timezone.now()
    cancelled = 0
    for run in runs:
        _append_run_event(
            run.id,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.CANCELLED,
            label="Cancelled by workflow deletion" if action == "delete" else "Cancelled by workflow pause",
            payload={"reason": reason, "workflow_id": str(workflow.id)},
        )
        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        next_meta = dict(meta)
        next_meta["cancelled_by_workflow"] = action
        if action == "pause":
            next_meta["cancelled_by_workflow_pause"] = True
        elif action == "delete":
            next_meta["cancelled_by_workflow_delete"] = True
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
    raw = str(value or AgentWorkflowTriggerType.MANUAL).strip().lower()
    if raw == "cron":
        return AgentWorkflowTriggerType.SCHEDULE
    return raw


def _compute_next_trigger(trigger_type: str, trigger_config: dict[str, Any], *, after=None):
    if trigger_type != AgentWorkflowTriggerType.SCHEDULE:
        return None
    cron_config = dict(trigger_config)
    cron_config.setdefault("type", "cron")
    return compute_next_workflow_schedule_at("cron", cron_config, after=after or timezone.now())


@csrf_protect
@require_http_methods(["GET", "POST"])
def agent_workflows_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            qs = (
                AgentWorkflow.objects.select_related("agent_profile", "department")
                .filter(agent_profile=agent)
                .annotate(session_count=Count("sessions"))
                .order_by("-created_at")
            )
            if status and status != "all":
                qs = qs.filter(status=status)
            workflows = list(qs[:200])
            workflow_ids = [item.id for item in workflows]
            latest_runs: dict[uuid.UUID, AgentRun] = {}
            open_checkpoints: dict[uuid.UUID, AgentRunCheckpoint] = {}
            if workflow_ids:
                run_qs = (
                    AgentRun.objects.select_related("workflow")
                    .filter(agent_profile=agent, workflow_id__in=workflow_ids)
                    .filter(_run_visibility_filter(request, agent=agent))
                    .order_by("-created_at")[:500]
                )
                for run in run_qs:
                    if run.workflow_id not in latest_runs:
                        latest_runs[run.workflow_id] = run
                    if len(latest_runs) == len(workflow_ids):
                        break
                checkpoint_qs = (
                    AgentRunCheckpoint.objects.filter(
                        workflow_id__in=workflow_ids,
                        status=AgentRunCheckpointStatus.OPEN,
                    )
                    .order_by("-updated_at", "-created_at")[:500]
                )
                for checkpoint in checkpoint_qs:
                    if checkpoint.workflow_id and checkpoint.workflow_id not in open_checkpoints:
                        open_checkpoints[checkpoint.workflow_id] = checkpoint
            for workflow in workflows:
                workflow.open_checkpoint = open_checkpoints.get(workflow.id)
            return JsonResponse({"workflows": [_serialize_workflow(item, latest_runs.get(item.id)) for item in workflows]}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)
        status = str((payload or {}).get("status") or AgentWorkflowStatus.DRAFT).strip().lower()
        if status not in {choice for choice, _ in AgentWorkflowStatus.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
        visibility = str((payload or {}).get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
        review_mode = str((payload or {}).get("reviewMode") or (payload or {}).get("review_mode") or AgentWorkflowReviewMode.ON_RISK).strip().lower()
        if review_mode not in {choice for choice, _ in AgentWorkflowReviewMode.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid reviewMode."}, status=HTTPStatus.BAD_REQUEST)
        autonomy_mode = str((payload or {}).get("autonomyMode") or (payload or {}).get("autonomy_mode") or AgentWorkflowAutonomyMode.DRAFT_FOR_APPROVAL).strip().lower()
        if autonomy_mode not in {choice for choice, _ in AgentWorkflowAutonomyMode.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid autonomyMode."}, status=HTTPStatus.BAD_REQUEST)
        trigger_type = _normalize_trigger_type((payload or {}).get("triggerType") or (payload or {}).get("trigger_type"))
        if trigger_type not in {choice for choice, _ in AgentWorkflowTriggerType.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid triggerType."}, status=HTTPStatus.BAD_REQUEST)
        trigger_config = dict((payload or {}).get("triggerConfig") or (payload or {}).get("trigger_config") or {})
        if trigger_type == AgentWorkflowTriggerType.WEBHOOK and not str(trigger_config.get("secret") or "").strip():
            trigger_config["secret"] = secrets.token_urlsafe(24)
        next_trigger_at = None
        if status == AgentWorkflowStatus.ACTIVE:
            try:
                next_trigger_at = _compute_next_trigger(trigger_type, trigger_config, after=timezone.now())
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        email_account = None
        email_account_id, err = _parse_uuid((payload or {}).get("emailAccountId") or (payload or {}).get("email_account_id"), field="emailAccountId")
        if err:
            return err
        if trigger_type == AgentWorkflowTriggerType.EMAIL_INBOX:
            if not email_account_id:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "emailAccountId is required for email inbox workflows."}, status=HTTPStatus.BAD_REQUEST)
            account_qs = EmailAccount.objects.filter(id=email_account_id, business_profile=agent.business_profile)
            if not request.user.is_staff:
                account_qs = account_qs.filter(user=request.user)
            email_account = account_qs.first()
            if email_account is None:
                return JsonResponse({"error": "EMAIL_ACCOUNT_NOT_FOUND", "message": "Email account not found."}, status=HTTPStatus.NOT_FOUND)
            if email_account.status != EmailAccountStatus.CONNECTED:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Email account must be connected before enabling an email inbox workflow."}, status=HTTPStatus.BAD_REQUEST)

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

        workflow = AgentWorkflow.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            department=agent.department,
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
            instructions=normalize_workflow_instructions((payload or {}).get("instructions") or (payload or {}).get("workflow") or {}),
            state=dict((payload or {}).get("state") or {}),
            poll_interval_seconds=max(60, min(int((payload or {}).get("pollIntervalSeconds") or (payload or {}).get("poll_interval_seconds") or 300), 86400)),
            max_events_per_poll=max(1, min(int((payload or {}).get("maxEventsPerPoll") or (payload or {}).get("max_events_per_poll") or 5), 25)),
            next_trigger_at=next_trigger_at,
            metadata=metadata_payload,
        )
        if bool((payload or {}).get("createSession", (payload or {}).get("create_session", True))):
            session = _create_workflow_session(workflow, created_by=request.user, source_conversation=source_conversation)
            session_meta = dict(session.metadata or {})
            if creation_brief:
                session_meta["creation_brief"] = creation_brief
                Conversation.objects.filter(id=session.id).update(metadata=session_meta, last_activity_at=timezone.now())
        workflow.session_count = Conversation.objects.filter(workflow=workflow).count()
        return JsonResponse({"workflow": _serialize_workflow(workflow)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def agent_workflow_detail(request: HttpRequest, agent_id: uuid.UUID, workflow_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        workflow = AgentWorkflow.objects.filter(id=workflow_id, agent_profile=agent).first()
        if workflow is None:
            return JsonResponse({"error": "WORKFLOW_NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            workflow.session_count = Conversation.objects.filter(workflow=workflow).count()
            workflow.open_checkpoint = (
                AgentRunCheckpoint.objects.filter(workflow=workflow, status=AgentRunCheckpointStatus.OPEN)
                .order_by("-updated_at", "-created_at")
                .first()
            )
            return JsonResponse({"workflow": _serialize_workflow(workflow)}, status=HTTPStatus.OK)
        if request.method == "DELETE":
            with transaction.atomic():
                workflow = (
                    AgentWorkflow.objects.select_for_update()
                    .get(id=workflow.id)
                )
                legacy_conversation = workflow.conversation
                _cancel_open_workflow_runs(workflow, reason="Workflow deleted", action="delete")
                Conversation.objects.filter(workflow=workflow).delete()
                workflow.delete()
                if legacy_conversation is not None:
                    legacy_meta = legacy_conversation.metadata if isinstance(getattr(legacy_conversation, "metadata", None), dict) else {}
                    legacy_type = str(legacy_meta.get("type") or legacy_meta.get("purpose") or "").strip().lower()
                    legacy_workflow_id = str(legacy_meta.get("workflow_id") or legacy_meta.get("workflowId") or "").strip()
                    if legacy_type == "workflow_thread" or legacy_workflow_id == str(workflow.id):
                        legacy_conversation.delete()
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        payload, error = _parse_json_body(request)
        if error:
            return error
        updates: list[str] = []
        for field in ("name", "description"):
            if field in payload:
                setattr(workflow, field, str(payload.get(field) or "").strip()[: 160 if field == "name" else 4000])
                updates.append(field)
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in {choice for choice, _ in AgentWorkflowStatus.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
            workflow.status = status
            updates.append("status")
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            workflow.visibility = visibility
            updates.append("visibility")
        if "reviewMode" in payload or "review_mode" in payload:
            review_mode = str(payload.get("reviewMode") or payload.get("review_mode") or "").strip().lower()
            if review_mode not in {choice for choice, _ in AgentWorkflowReviewMode.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid reviewMode."}, status=HTTPStatus.BAD_REQUEST)
            workflow.review_mode = review_mode
            updates.append("review_mode")
        if "autonomyMode" in payload or "autonomy_mode" in payload:
            autonomy_mode = str(payload.get("autonomyMode") or payload.get("autonomy_mode") or "").strip().lower()
            if autonomy_mode not in {choice for choice, _ in AgentWorkflowAutonomyMode.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid autonomyMode."}, status=HTTPStatus.BAD_REQUEST)
            workflow.autonomy_mode = autonomy_mode
            updates.append("autonomy_mode")
        if "triggerType" in payload or "trigger_type" in payload:
            trigger_type = _normalize_trigger_type(payload.get("triggerType") or payload.get("trigger_type"))
            if trigger_type not in {choice for choice, _ in AgentWorkflowTriggerType.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid triggerType."}, status=HTTPStatus.BAD_REQUEST)
            workflow.trigger_type = trigger_type
            updates.append("trigger_type")
        for public, field in (("triggerConfig", "trigger_config"), ("sourceConfig", "source_config"), ("destinationConfig", "destination_config"), ("notificationConfig", "notification_config"), ("instructions", "instructions"), ("state", "state"), ("metadata", "metadata")):
            if public in payload or field in payload:
                value = payload.get(public) if public in payload else payload.get(field)
                setattr(workflow, field, normalize_workflow_instructions(value) if field == "instructions" else dict(value or {}))
                updates.append(field)
        if "pollIntervalSeconds" in payload or "poll_interval_seconds" in payload:
            workflow.poll_interval_seconds = max(60, min(int(payload.get("pollIntervalSeconds") or payload.get("poll_interval_seconds") or 300), 86400))
            updates.append("poll_interval_seconds")
        if "maxEventsPerPoll" in payload or "max_events_per_poll" in payload:
            workflow.max_events_per_poll = max(1, min(int(payload.get("maxEventsPerPoll") or payload.get("max_events_per_poll") or 5), 25))
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
            workflow.email_account = email_account
            updates.append("email_account")
        if workflow.trigger_type == AgentWorkflowTriggerType.WEBHOOK:
            cfg = dict(workflow.trigger_config or {})
            if not str(cfg.get("secret") or "").strip():
                cfg["secret"] = secrets.token_urlsafe(24)
                workflow.trigger_config = cfg
                updates.append("trigger_config")
        if {"status", "trigger_type", "trigger_config"} & set(updates):
            try:
                workflow.next_trigger_at = _compute_next_trigger(workflow.trigger_type, workflow.trigger_config, after=timezone.now()) if workflow.status == AgentWorkflowStatus.ACTIVE else None
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            updates.append("next_trigger_at")
        if not updates:
            return JsonResponse({"workflow": _serialize_workflow(workflow)}, status=HTTPStatus.OK)
        workflow.save(update_fields=sorted(set([*updates, "updated_at"])))
        if "status" in updates and workflow.status == AgentWorkflowStatus.PAUSED:
            cancel_existing = bool((payload or {}).get("cancelOpenRuns", True))
            if cancel_existing:
                cancelled = _cancel_open_workflow_runs(workflow, reason="Workflow paused")
                workflow_meta = dict(workflow.metadata or {}) if isinstance(workflow.metadata, dict) else {}
                workflow_meta["last_pause_cancelled_runs"] = cancelled
                AgentWorkflow.objects.filter(id=workflow.id).update(metadata=workflow_meta, updated_at=timezone.now())
                workflow.metadata = workflow_meta
        return JsonResponse({"workflow": _serialize_workflow(workflow)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET"])
def agent_operations_status(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    now = timezone.now()
    stale_before = now - timedelta(seconds=90)
    workflow_heartbeat = cache.get("agent_workflow_processor_heartbeat")
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
        workflows = AgentWorkflow.objects.filter(agent_profile=agent)
        email_accounts = EmailAccount.objects.filter(business_profile=agent.business_profile)
        if not request.user.is_staff:
            email_accounts = email_accounts.filter(user=request.user)
        payload = {
            "operations": {
                "taskProcessingActive": bool(_heartbeat_payload(workflow_heartbeat)["active"] and _heartbeat_payload(run_heartbeat)["active"]),
                "workflowProcessor": _heartbeat_payload(workflow_heartbeat),
                "runProcessor": _heartbeat_payload(run_heartbeat),
                "dueWorkflows": workflows.filter(
                    status=AgentWorkflowStatus.ACTIVE,
                    trigger_type__in=[AgentWorkflowTriggerType.SCHEDULE, AgentWorkflowTriggerType.EMAIL_INBOX],
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
def agent_workflow_run(request: HttpRequest, agent_id: uuid.UUID, workflow_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    with tenant_context(agent.business_profile_id):
        workflow = AgentWorkflow.objects.filter(id=workflow_id, agent_profile=agent).first()
        if workflow is None:
            return JsonResponse({"error": "WORKFLOW_NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        payload, error = _parse_json_body(request)
        if error:
            return error
        conversation_id, err = _parse_uuid((payload or {}).get("conversationId") or (payload or {}).get("conversation_id"), field="conversationId")
        if err:
            return err
        try:
            conversation = _resolve_or_create_workflow_session(workflow, created_by=request.user, conversation_id=conversation_id)
        except ValueError:
            return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Workflow session not found."}, status=HTTPStatus.NOT_FOUND)
        run = _create_run(
            agent=agent,
            created_by=request.user,
            workflow=workflow,
            conversation=conversation,
            title=workflow.name,
            source=AgentRunSource.WORKFLOW,
            visibility=workflow.visibility,
            snapshot=_workflow_snapshot(workflow),
            metadata={"workflow_id": str(workflow.id), "trigger": "manual"},
        )
        AgentWorkflow.objects.filter(id=workflow.id).update(last_triggered_at=timezone.now(), updated_at=timezone.now())
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "POST"])
def agent_workflow_sessions(request: HttpRequest, agent_id: uuid.UUID, workflow_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    with tenant_context(agent.business_profile_id):
        workflow = AgentWorkflow.objects.filter(id=workflow_id, agent_profile=agent).first()
        if workflow is None:
            return JsonResponse({"error": "WORKFLOW_NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            sessions = (
                Conversation.objects.filter(workflow=workflow, business_profile=agent.business_profile)
                .annotate(message_count=Count("messages"))
                .order_by("-last_activity_at", "-started_at")[:100]
            )
            return JsonResponse({"sessions": [_serialize_workflow_session(item) for item in sessions]}, status=HTTPStatus.OK)

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
        session = _create_workflow_session(workflow, created_by=request.user, title=title, source_conversation=source_conversation)
        brief = _source_conversation_brief(source_conversation)
        if brief:
            meta = dict(session.metadata or {})
            meta["creation_brief"] = brief
            Conversation.objects.filter(id=session.id).update(metadata=meta, last_activity_at=timezone.now())
            session.metadata = meta
        return JsonResponse({"session": _serialize_workflow_session(session)}, status=HTTPStatus.CREATED)


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
            AgentRunCheckpoint.objects.select_related("run", "workflow", "conversation")
            .filter(id=checkpoint_id, business_profile=agent.business_profile)
            .filter(Q(workflow__agent_profile=agent) | Q(run__agent_profile=agent))
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
            workflow=run.workflow,
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
def workflow_webhook_trigger(request: HttpRequest, workflow_id: uuid.UUID, token: str) -> JsonResponse:
    token_value = str(token or "").strip()
    if not token_value:
        return JsonResponse({"error": "NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
    with tenant_bypass():
        workflow = (
            AgentWorkflow.objects.select_related("agent_profile", "business_profile", "conversation")
            .filter(id=workflow_id, trigger_type=AgentWorkflowTriggerType.WEBHOOK, status=AgentWorkflowStatus.ACTIVE)
            .first()
        )
        if workflow is None:
            return JsonResponse({"error": "NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        secret_value = str((workflow.trigger_config or {}).get("secret") or "").strip()
        if not secret_value or not secrets.compare_digest(secret_value, token_value):
            return JsonResponse({"error": "NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        run = _create_run(
            agent=workflow.agent_profile,
            created_by=None,
            workflow=workflow,
            conversation=None,
            title=workflow.name,
            source=AgentRunSource.WEBHOOK,
            visibility=workflow.visibility,
            snapshot=_workflow_snapshot(workflow),
            metadata={"workflow_id": str(workflow.id), "trigger": "webhook"},
        )
        AgentWorkflow.objects.filter(id=workflow.id).update(last_triggered_at=timezone.now(), updated_at=timezone.now())
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
                AgentRun.objects.select_related("workflow")
                .prefetch_related("artifacts")
                .filter(agent_profile=agent)
                .filter(_run_visibility_filter(request, agent=agent))
                .order_by("-created_at")
            )
            status = str(request.GET.get("status") or "").strip().lower()
            if status:
                qs = qs.filter(status=status)
            workflow_id, err = _parse_uuid(request.GET.get("workflowId") or request.GET.get("workflow_id"), field="workflowId")
            if err:
                return err
            if workflow_id:
                qs = qs.filter(workflow_id=workflow_id)
            limit = max(1, min(int(str(request.GET.get("limit") or "50")), 200))
            offset = max(0, int(str(request.GET.get("offset") or "0")))
            return JsonResponse({"runs": [_serialize_run(item) for item in qs[offset : offset + limit]], "total": qs.count(), "limit": limit, "offset": offset}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        workflow_id, err = _parse_uuid((payload or {}).get("workflowId") or (payload or {}).get("workflow_id"), field="workflowId")
        if err:
            return err
        workflow = None
        if workflow_id:
            workflow = AgentWorkflow.objects.filter(id=workflow_id, agent_profile=agent).first()
            if workflow is None:
                return JsonResponse({"error": "WORKFLOW_NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        conversation_id, err = _parse_uuid((payload or {}).get("conversationId") or (payload or {}).get("conversation_id"), field="conversationId")
        if err:
            return err
        conversation = None
        if conversation_id:
            conversation = Conversation.objects.filter(id=conversation_id, business_profile=agent.business_profile).first()
            if conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."}, status=HTTPStatus.NOT_FOUND)
        elif workflow:
            conversation = _latest_workflow_session(workflow)
        visibility = str((payload or {}).get("visibility") or (workflow.visibility if workflow else AgentRunVisibility.INITIATOR)).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
        source = str((payload or {}).get("source") or (AgentRunSource.WORKFLOW if workflow else AgentRunSource.CHAT)).strip().lower()
        if source not in {choice for choice, _ in AgentRunSource.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid source."}, status=HTTPStatus.BAD_REQUEST)
        run = _create_run(
            agent=agent,
            created_by=request.user,
            workflow=workflow,
            conversation=conversation,
            title=str((payload or {}).get("title") or (workflow.name if workflow else "")),
            source=source,
            visibility=visibility,
            snapshot=_workflow_snapshot(workflow, (payload or {}).get("workflowSnapshot") or (payload or {}).get("workflow") or {}),
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
        workflow=run.workflow,
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
    if run.workflow_id:
        MemoryItem.objects.create(
            business_profile=run.business_profile,
            scope=MemoryScope.WORKFLOW,
            agent_profile=run.agent_profile,
            workflow=run.workflow,
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
    qs = MemoryItem.objects.select_related("business_profile", "agent_profile", "workflow", "run", "conversation")
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
