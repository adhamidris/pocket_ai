from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from http import HTTPStatus
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Max, Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from core.tenancy import tenant_bypass, tenant_context

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import AgentProfile, EmailAccountStatus
from apps.conversations.workflow_scheduling import CronScheduleError, compute_next_workflow_schedule_at
from apps.conversations.models import (
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
    AgentRunVisibility,
    AgentWorkflow,
    AgentWorkflowStatus,
    AgentWorkflowTriggerType,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    MemoryAuditAction,
    MemoryAuditEvent,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemorySensitivity,
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
    qs = AgentProfile.objects.select_related("business_profile")
    if not request.user.is_staff:
        qs = qs.filter(Q(user=request.user) | Q(business_profile__user=request.user))
    agent = qs.filter(id=agent_id).first()
    if agent is None:
        return None, JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)
    return agent, None


def _user_is_business_owner(request: HttpRequest, agent: AgentProfile) -> bool:
    return bool(request.user.is_authenticated and getattr(agent.business_profile, "user_id", None) == request.user.id)


def _ensure_agent_workforce_enabled(
    request: HttpRequest,
    *,
    agent: AgentProfile,
    allow_read_only: bool = True,
) -> JsonResponse | None:
    if request.user.is_staff:
        return None
    enabled = bool(getattr(FeatureFlagService.snapshot(agent.business_profile), "agent_workforce_v1", False))
    if enabled or (allow_read_only and request.method == "GET"):
        return None
    return JsonResponse(
        {"error": "FEATURE_DISABLED", "message": "Agent workforce features are not enabled for this business."},
        status=HTTPStatus.FORBIDDEN,
    )


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
            }
        )
    return normalize_workflow_instructions(payload or {})


def _serialize_workflow(workflow: AgentWorkflow) -> dict[str, object]:
    return {
        "id": str(workflow.id),
        "agentId": str(workflow.agent_profile_id),
        "businessId": str(workflow.business_profile_id),
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
        "workflowId": str(run.workflow_id) if run.workflow_id else None,
        "workflowName": getattr(getattr(run, "workflow", None), "name", "") or "",
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


def _ensure_workflow_conversation(workflow: AgentWorkflow) -> Conversation:
    if workflow.conversation_id:
        return workflow.conversation
    conversation = Conversation.objects.create(
        business_profile=workflow.business_profile,
        agent_profile=workflow.agent_profile,
        owner_user=workflow.created_by or workflow.agent_profile.user,
        channel=ConversationChannel.API,
        status=ConversationStatus.LIVE,
        metadata={"workflow_id": str(workflow.id), "purpose": "workflow_thread"},
    )
    AgentWorkflow.objects.filter(id=workflow.id).update(conversation=conversation, updated_at=timezone.now())
    workflow.conversation = conversation
    return conversation


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
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            qs = AgentWorkflow.objects.filter(agent_profile=agent).order_by("-created_at")
            if status:
                qs = qs.filter(status=status)
            return JsonResponse({"workflows": [_serialize_workflow(item) for item in qs[:200]]}, status=HTTPStatus.OK)

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

        workflow = AgentWorkflow.objects.create(
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
            instructions=normalize_workflow_instructions((payload or {}).get("instructions") or (payload or {}).get("workflow") or {}),
            state=dict((payload or {}).get("state") or {}),
            poll_interval_seconds=max(60, min(int((payload or {}).get("pollIntervalSeconds") or (payload or {}).get("poll_interval_seconds") or 300), 86400)),
            max_events_per_poll=max(1, min(int((payload or {}).get("maxEventsPerPoll") or (payload or {}).get("max_events_per_poll") or 5), 25)),
            next_trigger_at=next_trigger_at,
            metadata=dict((payload or {}).get("metadata") or {}),
        )
        _ensure_workflow_conversation(workflow)
        return JsonResponse({"workflow": _serialize_workflow(workflow)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def agent_workflow_detail(request: HttpRequest, agent_id: uuid.UUID, workflow_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        workflow = AgentWorkflow.objects.filter(id=workflow_id, agent_profile=agent).first()
        if workflow is None:
            return JsonResponse({"error": "WORKFLOW_NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            return JsonResponse({"workflow": _serialize_workflow(workflow)}, status=HTTPStatus.OK)
        if request.method == "DELETE":
            workflow.status = AgentWorkflowStatus.ARCHIVED
            workflow.next_trigger_at = None
            workflow.lease_expires_at = None
            workflow.save(update_fields=["status", "next_trigger_at", "lease_expires_at", "updated_at"])
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
        if "triggerType" in payload or "trigger_type" in payload:
            trigger_type = _normalize_trigger_type(payload.get("triggerType") or payload.get("trigger_type"))
            if trigger_type not in {choice for choice, _ in AgentWorkflowTriggerType.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid triggerType."}, status=HTTPStatus.BAD_REQUEST)
            workflow.trigger_type = trigger_type
            updates.append("trigger_type")
        for public, field in (("triggerConfig", "trigger_config"), ("sourceConfig", "source_config"), ("destinationConfig", "destination_config"), ("instructions", "instructions"), ("state", "state"), ("metadata", "metadata")):
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
        if workflow.conversation_id is None:
            _ensure_workflow_conversation(workflow)
        return JsonResponse({"workflow": _serialize_workflow(workflow)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET"])
def agent_operations_status(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

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
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err
    with tenant_context(agent.business_profile_id):
        workflow = AgentWorkflow.objects.filter(id=workflow_id, agent_profile=agent).first()
        if workflow is None:
            return JsonResponse({"error": "WORKFLOW_NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        conversation = _ensure_workflow_conversation(workflow)
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


@csrf_exempt
@require_POST
def workflow_webhook_trigger(request: HttpRequest, workflow_id: uuid.UUID, token: str) -> JsonResponse:
    token_value = str(token or "").strip()
    if not token_value:
        return JsonResponse({"error": "NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
    with tenant_bypass():
        workflow = (
            AgentWorkflow.objects.select_related("agent_profile", "business_profile", "conversation")
            .filter(id=workflow_id, trigger_type=AgentWorkflowTriggerType.WEBHOOK)
            .first()
        )
        if workflow is None:
            return JsonResponse({"error": "NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        secret_value = str((workflow.trigger_config or {}).get("secret") or "").strip()
        if not secret_value or not secrets.compare_digest(secret_value, token_value):
            return JsonResponse({"error": "NOT_FOUND", "message": "Workflow not found."}, status=HTTPStatus.NOT_FOUND)
        if not bool(getattr(FeatureFlagService.snapshot(workflow.business_profile), "agent_workforce_v1", False)):
            return JsonResponse({"error": "FEATURE_DISABLED", "message": "Agent workforce features are not enabled for this business."}, status=HTTPStatus.FORBIDDEN)
        conversation = _ensure_workflow_conversation(workflow)
        run = _create_run(
            agent=workflow.agent_profile,
            created_by=None,
            workflow=workflow,
            conversation=conversation,
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
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err
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
            conversation = _ensure_workflow_conversation(workflow)
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
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err
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
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err
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
    feature_err = _ensure_agent_workforce_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err
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
    MemoryAuditEvent.objects.create(memory_item=item, business_profile=item.business_profile, actor_user=request.user, action=MemoryAuditAction.CREATED, after=_serialize_memory(item))
    if kind == MemoryKind.DECISION and str((payload or {}).get("decision") or "").strip().lower() == "deny":
        _set_run_status(run.id, status=AgentRunStatus.CANCELLED, error_detail=message or "denied", finished=True)
    else:
        _set_run_status(run.id, status=status or AgentRunStatus.QUEUED, run_after=True)
    run.refresh_from_db()
    return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET", "POST"])
def memory_collection(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    if request.method == "GET":
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
        status = str(request.GET.get("status") or MemoryStatus.ACTIVE).strip().lower()
        if status:
            qs = qs.filter(status=status)
        query = str(request.GET.get("q") or "").strip()
        if query:
            qs = qs.filter(Q(content__icontains=query) | Q(key__icontains=query))
        limit = max(1, min(int(str(request.GET.get("limit") or "50")), 200))
        return JsonResponse({"memory": [_serialize_memory(item) for item in qs.order_by("-updated_at")[:limit]]}, status=HTTPStatus.OK)

    payload, error = _parse_json_body(request)
    if error:
        return error
    business_id, err = _parse_uuid((payload or {}).get("businessId") or (payload or {}).get("business_id"), field="businessId")
    if err:
        return err
    agent_id, err = _parse_uuid((payload or {}).get("agentId") or (payload or {}).get("agent_id"), field="agentId")
    if err:
        return err
    agent = None
    if agent_id:
        agent = AgentProfile.objects.select_related("business_profile").filter(id=agent_id).filter(Q(user=request.user) | Q(business_profile__user=request.user)).first()
        if agent is None:
            return JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)
    if not business_id and agent is not None:
        business_id = agent.business_profile_id
    if not business_id:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "businessId or agentId is required."}, status=HTTPStatus.BAD_REQUEST)
    content = str((payload or {}).get("content") or "").strip()
    if not content:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "content is required."}, status=HTTPStatus.BAD_REQUEST)
    sensitivity = str((payload or {}).get("sensitivity") or MemorySensitivity.NORMAL).strip().lower()
    kind = str((payload or {}).get("kind") or MemoryKind.FACT).strip().lower()
    status = MemoryStatus.PENDING_REVIEW if sensitivity in {MemorySensitivity.SENSITIVE, MemorySensitivity.SECRET} or kind == MemoryKind.INSTRUCTION else MemoryStatus.ACTIVE
    item = MemoryItem.objects.create(
        business_profile_id=business_id,
        agent_profile=agent,
        scope=str((payload or {}).get("scope") or (MemoryScope.AGENT if agent else MemoryScope.WORKSPACE)).strip().lower(),
        kind=kind,
        key=str((payload or {}).get("key") or "").strip()[:160],
        content=content[:8000],
        payload=dict((payload or {}).get("payload") or {}),
        visibility=str((payload or {}).get("visibility") or MemoryVisibility.SHARED).strip().lower(),
        sensitivity=sensitivity,
        status=status,
        source_type=str((payload or {}).get("sourceType") or (payload or {}).get("source_type") or "api")[:64],
        created_by=request.user,
    )
    MemoryAuditEvent.objects.create(memory_item=item, business_profile=item.business_profile, actor_user=request.user, action=MemoryAuditAction.CREATED, after=_serialize_memory(item))
    return JsonResponse({"memory": _serialize_memory(item)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PATCH", "PUT"])
def memory_detail(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    item = MemoryItem.objects.filter(id=memory_id).filter(Q(business_profile__user=request.user) | Q(agent_profile__user=request.user)).first()
    if item is None:
        return JsonResponse({"error": "MEMORY_NOT_FOUND", "message": "Memory item not found."}, status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"memory": _serialize_memory(item)}, status=HTTPStatus.OK)
    before = _serialize_memory(item)
    payload, error = _parse_json_body(request)
    if error:
        return error
    for field in ("scope", "kind", "key", "content", "visibility", "sensitivity", "status"):
        if field in payload:
            setattr(item, field, str(payload.get(field) or "").strip()[:8000 if field == "content" else 160])
    if "payload" in payload and isinstance(payload.get("payload"), dict):
        item.payload = dict(payload.get("payload") or {})
    item.save()
    MemoryAuditEvent.objects.create(memory_item=item, business_profile=item.business_profile, actor_user=request.user, action=MemoryAuditAction.UPDATED, before=before, after=_serialize_memory(item))
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
