from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.models import AgentProfile
from apps.conversations.models import (
    AgentAutomation,
    AgentAutomationStatus,
    AgentAutomationTriggerType,
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunMemoryItem,
    AgentRunMemoryKind,
    AgentRunSource,
    AgentRunSpec,
    AgentRunSpecStatus,
    AgentRunStatus,
    AgentRunVisibility,
    Conversation,
)
from apps.conversations.run_contracts import normalize_run_spec


def _parse_json_body(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not isinstance(payload, dict):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be a JSON object."},
            status=HTTPStatus.BAD_REQUEST,
        )
    return payload, None


def _parse_uuid(value: object, *, field: str) -> tuple[uuid.UUID | None, JsonResponse | None]:
    if value is None or value == "":
        return None, None
    try:
        return uuid.UUID(str(value)), None
    except (TypeError, ValueError):
        return None, JsonResponse(
            {"error": "VALIDATION_ERROR", "message": f"{field} must be a valid UUID."},
            status=HTTPStatus.BAD_REQUEST,
        )


def _resolve_agent_for_request(request: HttpRequest, agent_id: uuid.UUID) -> tuple[AgentProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    qs = AgentProfile.objects.select_related("business_profile")
    if not request.user.is_staff:
        qs = qs.filter(user=request.user)
    agent = qs.filter(id=agent_id).first()
    if agent is None:
        return None, JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)
    return agent, None


def _serialize_run_spec(spec: AgentRunSpec) -> dict[str, object]:
    return {
        "id": str(spec.id),
        "agentId": str(spec.agent_profile_id),
        "businessId": str(spec.business_profile_id),
        "name": spec.name,
        "status": spec.status,
        "visibility": spec.visibility,
        "spec": spec.spec if isinstance(spec.spec, dict) else {},
        "metadata": spec.metadata if isinstance(spec.metadata, dict) else {},
        "createdBy": str(spec.created_by_id) if spec.created_by_id else None,
        "createdAt": spec.created_at.isoformat() if spec.created_at else None,
        "updatedAt": spec.updated_at.isoformat() if spec.updated_at else None,
    }


def _serialize_run(run: AgentRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "agentId": str(run.agent_profile_id),
        "businessId": str(run.business_profile_id),
        "conversationId": str(run.conversation_id) if run.conversation_id else None,
        "runSpecId": str(run.run_spec_id) if run.run_spec_id else None,
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


def _serialize_automation(automation: AgentAutomation) -> dict[str, object]:
    return {
        "id": str(automation.id),
        "agentId": str(automation.agent_profile_id),
        "businessId": str(automation.business_profile_id),
        "conversationId": str(automation.conversation_id) if automation.conversation_id else None,
        "runSpecId": str(automation.run_spec_id) if automation.run_spec_id else None,
        "name": automation.name,
        "status": automation.status,
        "visibility": automation.visibility,
        "triggerType": automation.trigger_type,
        "triggerConfig": automation.trigger_config if isinstance(automation.trigger_config, dict) else {},
        "destinationConfig": automation.destination_config if isinstance(automation.destination_config, dict) else {},
        "lastTriggeredAt": automation.last_triggered_at.isoformat() if automation.last_triggered_at else None,
        "nextTriggerAt": automation.next_trigger_at.isoformat() if automation.next_trigger_at else None,
        "metadata": automation.metadata if isinstance(automation.metadata, dict) else {},
        "createdBy": str(automation.created_by_id) if automation.created_by_id else None,
        "createdAt": automation.created_at.isoformat() if automation.created_at else None,
        "updatedAt": automation.updated_at.isoformat() if automation.updated_at else None,
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


def _set_run_status(
    run_id: uuid.UUID,
    *,
    status: str,
    error_detail: str | None = None,
    finished: bool = False,
    run_after: bool = False,
) -> None:
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
        return JsonResponse(
            {"error": "RUN_IMMUTABLE", "message": "Run is already finished."},
            status=HTTPStatus.CONFLICT,
        )
    return None


@csrf_protect
@require_http_methods(["GET", "POST"])
def agent_run_specs_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            qs = AgentRunSpec.objects.filter(agent_profile=agent).order_by("-created_at")
            if status:
                qs = qs.filter(status=status)
            items = [_serialize_run_spec(item) for item in qs[:200]]
            return JsonResponse({"runSpecs": items}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error

        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)

        status = str((payload or {}).get("status") or AgentRunSpecStatus.DRAFT).strip().lower()
        if status not in {choice for choice, _ in AgentRunSpecStatus.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)

        visibility = str((payload or {}).get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)

        spec_payload = (payload or {}).get("spec") or (payload or {}).get("runSpec") or {}
        spec_normalized = normalize_run_spec(spec_payload)

        run_spec = AgentRunSpec.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            created_by=request.user,
            name=name,
            status=status,
            visibility=visibility,
            spec=spec_normalized,
            metadata=(payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {},
        )
        return JsonResponse({"runSpec": _serialize_run_spec(run_spec)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PUT", "DELETE"])
def agent_run_spec_detail(request: HttpRequest, agent_id: uuid.UUID, spec_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        run_spec = AgentRunSpec.objects.filter(id=spec_id, agent_profile=agent).first()
        if run_spec is None:
            return JsonResponse({"error": "RUN_SPEC_NOT_FOUND", "message": "Run spec not found."}, status=HTTPStatus.NOT_FOUND)

        if request.method == "GET":
            return JsonResponse({"runSpec": _serialize_run_spec(run_spec)}, status=HTTPStatus.OK)

        if request.method == "DELETE":
            run_spec.status = AgentRunSpecStatus.ARCHIVED
            run_spec.save(update_fields=["status", "updated_at"])
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        payload, error = _parse_json_body(request)
        if error:
            return error

        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "name cannot be blank."}, status=HTTPStatus.BAD_REQUEST)
            run_spec.name = name[:160]
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in {choice for choice, _ in AgentRunSpecStatus.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
            run_spec.status = status
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            run_spec.visibility = visibility
        if "spec" in payload or "runSpec" in payload:
            spec_payload = payload.get("spec") or payload.get("runSpec") or {}
            run_spec.spec = normalize_run_spec(spec_payload)
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            run_spec.metadata = dict(payload.get("metadata") or {})

        run_spec.save(update_fields=["name", "status", "visibility", "spec", "metadata", "updated_at"])
        return JsonResponse({"runSpec": _serialize_run_spec(run_spec)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET", "POST"])
def agent_runs_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            conversation_id = request.GET.get("conversation_id") or request.GET.get("conversationId")
            limit = int(str(request.GET.get("limit") or "50") or 50)
            offset = int(str(request.GET.get("offset") or "0") or 0)
            limit = max(1, min(limit, 200))
            offset = max(0, offset)

            qs = AgentRun.objects.filter(agent_profile=agent).order_by("-created_at")
            if status:
                qs = qs.filter(status=status)
            if conversation_id:
                conv_uuid, err = _parse_uuid(conversation_id, field="conversation_id")
                if err:
                    return err
                if conv_uuid:
                    qs = qs.filter(conversation_id=conv_uuid)

            total = qs.count()
            items = [_serialize_run(item) for item in qs[offset : offset + limit]]
            return JsonResponse({"runs": items, "total": total, "limit": limit, "offset": offset}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error

        run_spec_id_raw = (payload or {}).get("runSpecId") or (payload or {}).get("run_spec_id")
        run_spec_id, err = _parse_uuid(run_spec_id_raw, field="runSpecId")
        if err:
            return err

        conversation_id_raw = (payload or {}).get("conversationId") or (payload or {}).get("conversation_id")
        conversation_id, err = _parse_uuid(conversation_id_raw, field="conversationId")
        if err:
            return err

        conversation = None
        if conversation_id:
            conversation = Conversation.objects.filter(id=conversation_id, business_profile=agent.business_profile).first()
            if conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."}, status=HTTPStatus.NOT_FOUND)

        run_spec = None
        run_spec_snapshot: dict[str, Any] = {}
        if run_spec_id:
            run_spec = AgentRunSpec.objects.filter(id=run_spec_id, agent_profile=agent).first()
            if run_spec is None:
                return JsonResponse({"error": "RUN_SPEC_NOT_FOUND", "message": "Run spec not found."}, status=HTTPStatus.NOT_FOUND)
            run_spec_snapshot = normalize_run_spec(run_spec.spec)
        else:
            snapshot_payload = (payload or {}).get("runSpec") or (payload or {}).get("runSpecSnapshot") or {}
            run_spec_snapshot = normalize_run_spec(snapshot_payload)

        visibility = str((payload or {}).get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)

        title = str((payload or {}).get("title") or "").strip()
        if not title:
            title = str(run_spec_snapshot.get("goal") or run_spec_snapshot.get("name") or run_spec.name if run_spec else "").strip()

        plan = (payload or {}).get("plan")
        plan_dict = dict(plan) if isinstance(plan, dict) else {}

        source = str((payload or {}).get("source") or AgentRunSource.CHAT).strip().lower() or AgentRunSource.CHAT
        if source not in {choice for choice, _ in AgentRunSource.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid source."}, status=HTTPStatus.BAD_REQUEST)

        run = AgentRun.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            conversation=conversation,
            created_by=request.user,
            run_spec=run_spec,
            run_spec_snapshot=run_spec_snapshot,
            title=title[:200],
            source=source,
            status=AgentRunStatus.QUEUED,
            visibility=visibility,
            plan=plan_dict,
            metadata=(payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {},
            run_after=timezone.now(),
        )
        _append_run_event(
            run.id,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Queued",
            payload={"status": AgentRunStatus.QUEUED},
        )
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PUT"])
def agent_run_detail(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        run = AgentRun.objects.filter(id=run_id, agent_profile=agent).first()
        if run is None:
            return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)

        if request.method == "GET":
            return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error

        updates: list[str] = []
        if "title" in payload:
            run.title = str(payload.get("title") or "").strip()[:200]
            updates.append("title")
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            run.visibility = visibility
            updates.append("visibility")
        if "plan" in payload and isinstance(payload.get("plan"), dict):
            run.plan = dict(payload.get("plan") or {})
            updates.append("plan")
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            run.metadata = dict(payload.get("metadata") or {})
            updates.append("metadata")
        if not updates:
            return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)

        updates.append("updated_at")
        run.save(update_fields=updates)
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET"])
def agent_run_events(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        run = AgentRun.objects.filter(id=run_id, agent_profile=agent).first()
        if run is None:
            return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)

        after = request.GET.get("after")
        limit = int(str(request.GET.get("limit") or "200") or 200)
        limit = max(1, min(limit, 500))
        qs = AgentRunEvent.objects.filter(run=run).order_by("sequence_index")
        if after:
            try:
                after_index = int(str(after))
            except ValueError:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "after must be an integer."}, status=HTTPStatus.BAD_REQUEST)
            qs = qs.filter(sequence_index__gt=after_index)
        events = [_serialize_run_event(event) for event in qs[:limit]]
        return JsonResponse({"events": events}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_cancel(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    payload, error = _parse_json_body(request)
    if error:
        return error

    reason = str((payload or {}).get("reason") or "").strip()

    with tenant_context(agent.business_profile_id):
        run = AgentRun.objects.filter(id=run_id, agent_profile=agent).first()
        if run is None:
            return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)

        immutable = _ensure_run_mutable(run)
        if immutable:
            return immutable

        _append_run_event(
            run.id,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.CANCELLED,
            label="Cancelled",
            payload={"reason": reason} if reason else {},
        )
        _set_run_status(run.id, status=AgentRunStatus.CANCELLED, error_detail=reason or "cancelled", finished=True)
        run.refresh_from_db()
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_user_input(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    payload, error = _parse_json_body(request)
    if error:
        return error

    message = str((payload or {}).get("message") or "").strip()
    extra = (payload or {}).get("payload")
    extra_payload = dict(extra) if isinstance(extra, dict) else {}

    if not message and not extra_payload:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "message or payload is required."},
            status=HTTPStatus.BAD_REQUEST,
        )

    with tenant_context(agent.business_profile_id):
        run = AgentRun.objects.filter(id=run_id, agent_profile=agent).first()
        if run is None:
            return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)

        immutable = _ensure_run_mutable(run)
        if immutable:
            return immutable

        if run.status not in {AgentRunStatus.WAITING_USER, AgentRunStatus.PAUSED, AgentRunStatus.WAITING_EXTERNAL}:
            return JsonResponse(
                {"error": "RUN_NOT_WAITING_USER", "message": "Run is not waiting for user input."},
                status=HTTPStatus.CONFLICT,
            )

        _append_run_event(
            run.id,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.PROGRESS,
            label="User input received",
            payload={"message": message, "payload": extra_payload} if extra_payload else {"message": message},
        )
        AgentRunMemoryItem.objects.create(
            run=run,
            kind=AgentRunMemoryKind.NOTE,
            key="user_input",
            content=message[:4000],
            payload=extra_payload,
            created_by=request.user,
        )
        _set_run_status(run.id, status=AgentRunStatus.QUEUED, run_after=True)
        run.refresh_from_db()
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_approval(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    payload, error = _parse_json_body(request)
    if error:
        return error

    decision = str((payload or {}).get("decision") or "").strip().lower()
    if decision not in {"approve", "deny"}:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "decision must be 'approve' or 'deny'."},
            status=HTTPStatus.BAD_REQUEST,
        )

    note = str((payload or {}).get("note") or "").strip()
    extra = (payload or {}).get("payload")
    extra_payload = dict(extra) if isinstance(extra, dict) else {}

    with tenant_context(agent.business_profile_id):
        run = AgentRun.objects.filter(id=run_id, agent_profile=agent).first()
        if run is None:
            return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)

        immutable = _ensure_run_mutable(run)
        if immutable:
            return immutable

        if run.status not in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.PAUSED}:
            return JsonResponse(
                {"error": "RUN_NOT_WAITING_APPROVAL", "message": "Run is not waiting for approval."},
                status=HTTPStatus.CONFLICT,
            )

        _append_run_event(
            run.id,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.PROGRESS,
            label="Approved" if decision == "approve" else "Denied",
            payload={"decision": decision, "note": note, "payload": extra_payload} if extra_payload else {"decision": decision, "note": note},
        )
        AgentRunMemoryItem.objects.create(
            run=run,
            kind=AgentRunMemoryKind.DECISION,
            key="approval",
            content=(note or decision)[:4000],
            payload={"decision": decision, **extra_payload},
            created_by=request.user,
        )

        if decision == "deny":
            _set_run_status(run.id, status=AgentRunStatus.CANCELLED, error_detail=note or "denied", finished=True)
        else:
            _set_run_status(run.id, status=AgentRunStatus.QUEUED, run_after=True)

        run.refresh_from_db()
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_run_resume(request: HttpRequest, agent_id: uuid.UUID, run_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    payload, error = _parse_json_body(request)
    if error:
        return error

    reason = str((payload or {}).get("reason") or "").strip()

    with tenant_context(agent.business_profile_id):
        run = AgentRun.objects.filter(id=run_id, agent_profile=agent).first()
        if run is None:
            return JsonResponse({"error": "RUN_NOT_FOUND", "message": "Run not found."}, status=HTTPStatus.NOT_FOUND)

        immutable = _ensure_run_mutable(run)
        if immutable:
            return immutable

        if run.status not in {
            AgentRunStatus.PAUSED,
            AgentRunStatus.WAITING_USER,
            AgentRunStatus.WAITING_APPROVAL,
            AgentRunStatus.WAITING_EXTERNAL,
        }:
            return JsonResponse(
                {"error": "RUN_NOT_RESUMABLE", "message": "Run is not resumable from its current state."},
                status=HTTPStatus.CONFLICT,
            )

        _append_run_event(
            run.id,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.PROGRESS,
            label="Resumed",
            payload={"reason": reason} if reason else {},
        )
        _set_run_status(run.id, status=AgentRunStatus.QUEUED, run_after=True)
        run.refresh_from_db()
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET", "POST"])
def agent_automations_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            qs = AgentAutomation.objects.filter(agent_profile=agent).order_by("-created_at")
            if status:
                qs = qs.filter(status=status)
            items = [_serialize_automation(item) for item in qs[:200]]
            return JsonResponse({"automations": items}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error

        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)

        status = str((payload or {}).get("status") or AgentAutomationStatus.DRAFT).strip().lower()
        if status not in {choice for choice, _ in AgentAutomationStatus.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)

        visibility = str((payload or {}).get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)

        trigger_type = str((payload or {}).get("triggerType") or (payload or {}).get("trigger_type") or AgentAutomationTriggerType.CRON).strip().lower()
        if trigger_type not in {"cron", "webhook", "manual"}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid triggerType."}, status=HTTPStatus.BAD_REQUEST)

        trigger_config = (payload or {}).get("triggerConfig")
        trigger_config = dict(trigger_config) if isinstance(trigger_config, dict) else {}

        destination_config = (payload or {}).get("destinationConfig")
        destination_config = dict(destination_config) if isinstance(destination_config, dict) else {}

        conversation_id_raw = (payload or {}).get("conversationId") or (payload or {}).get("conversation_id")
        conversation_id, err = _parse_uuid(conversation_id_raw, field="conversationId")
        if err:
            return err

        conversation = None
        if conversation_id:
            conversation = Conversation.objects.filter(id=conversation_id, business_profile=agent.business_profile).first()
            if conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."}, status=HTTPStatus.NOT_FOUND)

        run_spec_id_raw = (payload or {}).get("runSpecId") or (payload or {}).get("run_spec_id")
        run_spec_id, err = _parse_uuid(run_spec_id_raw, field="runSpecId")
        if err:
            return err

        run_spec = None
        run_spec_snapshot: dict[str, Any] = {}
        if run_spec_id:
            run_spec = AgentRunSpec.objects.filter(id=run_spec_id, agent_profile=agent).first()
            if run_spec is None:
                return JsonResponse({"error": "RUN_SPEC_NOT_FOUND", "message": "Run spec not found."}, status=HTTPStatus.NOT_FOUND)
            run_spec_snapshot = normalize_run_spec(run_spec.spec)
        else:
            snapshot_payload = (payload or {}).get("runSpec") or (payload or {}).get("runSpecSnapshot") or {}
            run_spec_snapshot = normalize_run_spec(snapshot_payload)

        automation = AgentAutomation.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            created_by=request.user,
            run_spec=run_spec,
            run_spec_snapshot=run_spec_snapshot,
            conversation=conversation,
            name=name,
            status=status,
            visibility=visibility,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            destination_config=destination_config,
            metadata=(payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {},
        )
        return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PUT", "DELETE"])
def agent_automation_detail(request: HttpRequest, agent_id: uuid.UUID, automation_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        automation = AgentAutomation.objects.filter(id=automation_id, agent_profile=agent).first()
        if automation is None:
            return JsonResponse({"error": "AUTOMATION_NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)

        if request.method == "GET":
            return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)

        if request.method == "DELETE":
            automation.status = AgentAutomationStatus.ARCHIVED
            automation.save(update_fields=["status", "updated_at"])
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        payload, error = _parse_json_body(request)
        if error:
            return error

        updates: list[str] = []
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "name cannot be blank."}, status=HTTPStatus.BAD_REQUEST)
            automation.name = name[:160]
            updates.append("name")
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in {choice for choice, _ in AgentAutomationStatus.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
            automation.status = status
            updates.append("status")
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            automation.visibility = visibility
            updates.append("visibility")
        if "triggerType" in payload or "trigger_type" in payload:
            trigger_type = str(payload.get("triggerType") or payload.get("trigger_type") or "").strip().lower()
            if trigger_type not in {"cron", "webhook", "manual"}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid triggerType."}, status=HTTPStatus.BAD_REQUEST)
            automation.trigger_type = trigger_type
            updates.append("trigger_type")
        if "triggerConfig" in payload and isinstance(payload.get("triggerConfig"), dict):
            automation.trigger_config = dict(payload.get("triggerConfig") or {})
            updates.append("trigger_config")
        if "destinationConfig" in payload and isinstance(payload.get("destinationConfig"), dict):
            automation.destination_config = dict(payload.get("destinationConfig") or {})
            updates.append("destination_config")
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            automation.metadata = dict(payload.get("metadata") or {})
            updates.append("metadata")

        if not updates:
            return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)

        updates.append("updated_at")
        automation.save(update_fields=updates)
        return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_automation_trigger(request: HttpRequest, agent_id: uuid.UUID, automation_id: uuid.UUID) -> JsonResponse:
    """
    Manually trigger an automation to spawn a run (used by "Run now" and future schedulers).
    """

    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        automation = AgentAutomation.objects.filter(id=automation_id, agent_profile=agent).first()
        if automation is None:
            return JsonResponse({"error": "AUTOMATION_NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)

        if automation.status not in {AgentAutomationStatus.ACTIVE, AgentAutomationStatus.PAUSED, AgentAutomationStatus.DRAFT}:
            return JsonResponse(
                {"error": "AUTOMATION_NOT_TRIGGERABLE", "message": "Automation is not triggerable."},
                status=HTTPStatus.CONFLICT,
            )

        run = AgentRun.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            conversation=automation.conversation,
            created_by=request.user,
            run_spec=automation.run_spec,
            run_spec_snapshot=normalize_run_spec(automation.run_spec_snapshot),
            title=(automation.name or "Automation run")[:200],
            source=AgentRunSource.AUTOMATION,
            status=AgentRunStatus.QUEUED,
            visibility=automation.visibility,
            metadata={"automation_id": str(automation.id)},
            run_after=timezone.now(),
        )
        _append_run_event(
            run.id,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Queued (automation)",
            payload={"automation_id": str(automation.id)},
        )
        AgentAutomation.objects.filter(id=automation.id).update(last_triggered_at=timezone.now(), updated_at=timezone.now())
        return JsonResponse({"run": _serialize_run(run)}, status=HTTPStatus.CREATED)
