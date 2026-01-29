from __future__ import annotations

import json
import secrets
import uuid
from http import HTTPStatus
from typing import Any

from django.db import transaction
from django.db.models import Max, Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from core.tenancy import tenant_bypass, tenant_context

from apps.accounts.models import AgentProfile, EmailAccount, EmailAccountStatus
from apps.accounts.feature_flags import FeatureFlagService
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
    AgentWatcher,
    AgentWatcherStatus,
    AgentWatcherType,
    Conversation,
)
from apps.conversations.automation_scheduling import CronScheduleError, compute_next_automation_trigger_at
from apps.conversations.output_destinations import ensure_automation_thread, ensure_watcher_thread
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
        # Teams/roles are pending; for now treat the business owner as "manager"
        # with visibility into the workspace, in addition to the agent's assigned user.
        qs = qs.filter(Q(user=request.user) | Q(business_profile__user=request.user))
    agent = qs.filter(id=agent_id).first()
    if agent is None:
        return None, JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)
    return agent, None


def _user_is_business_owner(request: HttpRequest, agent: AgentProfile) -> bool:
    business = getattr(agent, "business_profile", None)
    owner_id = getattr(business, "user_id", None)
    return bool(request.user.is_authenticated and owner_id and owner_id == request.user.id)


def _ensure_subagents_enabled(
    request: HttpRequest,
    *,
    agent: AgentProfile,
    allow_read_only: bool = True,
) -> JsonResponse | None:
    """
    Enforce per-business rollout toggle for sub-agents surfaces.

    Teams/roles are pending; this is a coarse per-tenant feature gate.
    """

    if request.user.is_staff:
        return None
    enabled = bool(getattr(FeatureFlagService.snapshot(agent.business_profile), "sub_agents_v1", False))
    if enabled:
        return None
    if allow_read_only and request.method == "GET":
        return None
    return JsonResponse(
        {"error": "FEATURE_DISABLED", "message": "Sub-agents are not enabled for this business."},
        status=HTTPStatus.FORBIDDEN,
    )


def _run_visibility_filter(request: HttpRequest, *, agent: AgentProfile) -> Q:
    """
    Runs are workspace-scoped but may be restricted by visibility.

    - initiator: only the initiating user (created_by)
    - managers: initiator + business owner (until full team RBAC lands)
    - workspace: any user who can access this agent in this tenant
    """

    if request.user.is_staff:
        return Q()

    base = Q(created_by=request.user) | Q(visibility=AgentRunVisibility.WORKSPACE)
    if _user_is_business_owner(request, agent):
        base |= Q(visibility=AgentRunVisibility.MANAGERS)
    return base


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


def _serialize_watcher(watcher: AgentWatcher) -> dict[str, object]:
    return {
        "id": str(watcher.id),
        "agentId": str(watcher.agent_profile_id),
        "businessId": str(watcher.business_profile_id),
        "conversationId": str(watcher.conversation_id) if watcher.conversation_id else None,
        "runSpecId": str(watcher.run_spec_id) if watcher.run_spec_id else None,
        "emailAccountId": str(watcher.email_account_id) if watcher.email_account_id else None,
        "name": watcher.name,
        "status": watcher.status,
        "visibility": watcher.visibility,
        "watcherType": watcher.watcher_type,
        "watchConfig": watcher.watch_config if isinstance(watcher.watch_config, dict) else {},
        "destinationConfig": watcher.destination_config if isinstance(getattr(watcher, "destination_config", None), dict) else {},
        "pollIntervalSeconds": int(watcher.poll_interval_seconds or 0),
        "maxEventsPerPoll": int(watcher.max_events_per_poll or 0),
        "lastPolledAt": watcher.last_polled_at.isoformat() if watcher.last_polled_at else None,
        "nextPollAt": watcher.next_poll_at.isoformat() if watcher.next_poll_at else None,
        "leaseExpiresAt": watcher.lease_expires_at.isoformat() if watcher.lease_expires_at else None,
        "errorCount": int(watcher.error_count or 0),
        "lastError": watcher.last_error or "",
        "metadata": watcher.metadata if isinstance(watcher.metadata, dict) else {},
        "createdBy": str(watcher.created_by_id) if watcher.created_by_id else None,
        "createdAt": watcher.created_at.isoformat() if watcher.created_at else None,
        "updatedAt": watcher.updated_at.isoformat() if watcher.updated_at else None,
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            conversation_id = request.GET.get("conversation_id") or request.GET.get("conversationId")
            limit = int(str(request.GET.get("limit") or "50") or 50)
            offset = int(str(request.GET.get("offset") or "0") or 0)
            limit = max(1, min(limit, 200))
            offset = max(0, offset)

            qs = AgentRun.objects.filter(agent_profile=agent).filter(_run_visibility_filter(request, agent=agent)).order_by("-created_at")
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        run = (
            AgentRun.objects.filter(id=run_id, agent_profile=agent)
            .filter(_run_visibility_filter(request, agent=agent))
            .first()
        )
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        run = (
            AgentRun.objects.filter(id=run_id, agent_profile=agent)
            .filter(_run_visibility_filter(request, agent=agent))
            .first()
        )
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err

    payload, error = _parse_json_body(request)
    if error:
        return error

    reason = str((payload or {}).get("reason") or "").strip()

    with tenant_context(agent.business_profile_id):
        run = (
            AgentRun.objects.filter(id=run_id, agent_profile=agent)
            .filter(_run_visibility_filter(request, agent=agent))
            .first()
        )
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err

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
        run = (
            AgentRun.objects.filter(id=run_id, agent_profile=agent)
            .filter(_run_visibility_filter(request, agent=agent))
            .first()
        )
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err

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
        run = (
            AgentRun.objects.filter(id=run_id, agent_profile=agent)
            .filter(_run_visibility_filter(request, agent=agent))
            .first()
        )
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err

    payload, error = _parse_json_body(request)
    if error:
        return error

    reason = str((payload or {}).get("reason") or "").strip()

    with tenant_context(agent.business_profile_id):
        run = (
            AgentRun.objects.filter(id=run_id, agent_profile=agent)
            .filter(_run_visibility_filter(request, agent=agent))
            .first()
        )
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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

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

        if trigger_type == AgentAutomationTriggerType.WEBHOOK:
            secret_value = str(trigger_config.get("secret") or "").strip()
            if not secret_value:
                trigger_config["secret"] = secrets.token_urlsafe(24)

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

        next_trigger_at = None
        if status == AgentAutomationStatus.ACTIVE and trigger_type == AgentAutomationTriggerType.CRON:
            try:
                next_trigger_at = compute_next_automation_trigger_at(trigger_type, trigger_config, after=timezone.now())
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

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
            next_trigger_at=next_trigger_at,
            metadata=(payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {},
        )
        if automation.conversation_id is None:
            ensure_automation_thread(automation)
        return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PUT", "DELETE"])
def agent_automation_detail(request: HttpRequest, agent_id: uuid.UUID, automation_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

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

        if ("trigger_type" in updates or "trigger_config" in updates) and automation.trigger_type == AgentAutomationTriggerType.WEBHOOK:
            current_config = dict(automation.trigger_config or {}) if isinstance(automation.trigger_config, dict) else {}
            secret_value = str(current_config.get("secret") or "").strip()
            if not secret_value:
                current_config["secret"] = secrets.token_urlsafe(24)
                automation.trigger_config = current_config
                if "trigger_config" not in updates:
                    updates.append("trigger_config")

        if {"status", "trigger_type", "trigger_config"} & set(updates):
            next_trigger_at = None
            if automation.status == AgentAutomationStatus.ACTIVE and automation.trigger_type == AgentAutomationTriggerType.CRON:
                try:
                    next_trigger_at = compute_next_automation_trigger_at(
                        automation.trigger_type,
                        automation.trigger_config,
                        after=timezone.now(),
                    )
                except CronScheduleError as exc:
                    return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            automation.next_trigger_at = next_trigger_at
            updates.append("next_trigger_at")

        if not updates:
            return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)

        updates.append("updated_at")
        automation.save(update_fields=updates)
        if automation.conversation_id is None:
            ensure_automation_thread(automation)
        return JsonResponse({"automation": _serialize_automation(automation)}, status=HTTPStatus.OK)


@csrf_exempt
@require_POST
def automation_webhook_trigger(request: HttpRequest, automation_id: uuid.UUID, token: str) -> JsonResponse:
    """
    Trigger a webhook automation without a user session.

    Security model (V1): UUID + per-automation shared secret in trigger_config.secret.
    """

    token_value = str(token or "").strip()
    if not token_value:
        return JsonResponse({"error": "NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)

    with tenant_bypass():
        automation = (
            AgentAutomation.objects.select_related("agent_profile", "business_profile", "conversation", "run_spec")
            .filter(id=automation_id, trigger_type=AgentAutomationTriggerType.WEBHOOK)
            .first()
        )
        if automation is None:
            return JsonResponse({"error": "NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)

        config = automation.trigger_config if isinstance(automation.trigger_config, dict) else {}
        secret_value = str(config.get("secret") or "").strip()
        if not secret_value or not secrets.compare_digest(secret_value, token_value):
            return JsonResponse({"error": "NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)

        enabled = bool(getattr(FeatureFlagService.snapshot(automation.business_profile), "sub_agents_v1", False))
        if not enabled:
            return JsonResponse(
                {"error": "FEATURE_DISABLED", "message": "Sub-agents are not enabled for this business."},
                status=HTTPStatus.FORBIDDEN,
            )

        if automation.status not in {AgentAutomationStatus.ACTIVE, AgentAutomationStatus.PAUSED, AgentAutomationStatus.DRAFT}:
            return JsonResponse(
                {"error": "AUTOMATION_NOT_TRIGGERABLE", "message": "Automation is not triggerable."},
                status=HTTPStatus.CONFLICT,
            )

        now = timezone.now()
        with tenant_context(automation.business_profile_id):
            if automation.conversation_id is None:
                ensure_automation_thread(automation)
        run = AgentRun.objects.create(
            business_profile=automation.business_profile,
            agent_profile=automation.agent_profile,
            conversation=automation.conversation,
            created_by=None,
            run_spec=automation.run_spec,
            run_spec_snapshot=normalize_run_spec(automation.run_spec_snapshot),
            title=(automation.name or "Automation run")[:200],
            source=AgentRunSource.AUTOMATION,
            status=AgentRunStatus.QUEUED,
            visibility=automation.visibility,
            metadata={"automation_id": str(automation.id), "trigger": "webhook", "destination_config": dict(automation.destination_config or {}) if isinstance(automation.destination_config, dict) else {}},
            run_after=now,
        )
        _append_run_event(
            run.id,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Queued (automation)",
            payload={"automation_id": str(automation.id), "trigger": "webhook"},
        )
        AgentAutomation.objects.filter(id=automation.id).update(last_triggered_at=now, updated_at=now)
        return JsonResponse({"runId": str(run.id)}, status=HTTPStatus.CREATED)


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
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        automation = AgentAutomation.objects.filter(id=automation_id, agent_profile=agent).first()
        if automation is None:
            return JsonResponse({"error": "AUTOMATION_NOT_FOUND", "message": "Automation not found."}, status=HTTPStatus.NOT_FOUND)

        if automation.status not in {AgentAutomationStatus.ACTIVE, AgentAutomationStatus.PAUSED, AgentAutomationStatus.DRAFT}:
            return JsonResponse(
                {"error": "AUTOMATION_NOT_TRIGGERABLE", "message": "Automation is not triggerable."},
                status=HTTPStatus.CONFLICT,
            )

        if automation.conversation_id is None:
            ensure_automation_thread(automation)

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
            metadata={"automation_id": str(automation.id), "destination_config": dict(automation.destination_config or {}) if isinstance(automation.destination_config, dict) else {}},
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


@csrf_protect
@require_http_methods(["GET", "POST"])
def agent_watchers_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            qs = AgentWatcher.objects.filter(agent_profile=agent).order_by("-created_at")
            if status:
                qs = qs.filter(status=status)
            items = [_serialize_watcher(item) for item in qs[:200]]
            return JsonResponse({"watchers": items}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error

        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)

        status = str((payload or {}).get("status") or AgentWatcherStatus.DRAFT).strip().lower()
        if status not in {choice for choice, _ in AgentWatcherStatus.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)

        visibility = str((payload or {}).get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)

        watcher_type = str((payload or {}).get("watcherType") or (payload or {}).get("watcher_type") or AgentWatcherType.EMAIL_INBOX).strip().lower()
        if watcher_type not in {choice for choice, _ in AgentWatcherType.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid watcherType."}, status=HTTPStatus.BAD_REQUEST)

        try:
            poll_interval = int((payload or {}).get("pollIntervalSeconds") or (payload or {}).get("poll_interval_seconds") or 300)
        except (TypeError, ValueError):
            poll_interval = 300
        poll_interval = max(60, min(int(poll_interval), 24 * 60 * 60))

        try:
            max_events = int((payload or {}).get("maxEventsPerPoll") or (payload or {}).get("max_events_per_poll") or 5)
        except (TypeError, ValueError):
            max_events = 5
        max_events = max(1, min(int(max_events), 25))

        watch_config = (payload or {}).get("watchConfig") or (payload or {}).get("watch_config") or {}
        watch_config = dict(watch_config) if isinstance(watch_config, dict) else {}

        destination_config = (payload or {}).get("destinationConfig") or (payload or {}).get("destination_config") or {}
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

        email_account = None
        email_account_id_raw = (payload or {}).get("emailAccountId") or (payload or {}).get("email_account_id")
        email_account_id, err = _parse_uuid(email_account_id_raw, field="emailAccountId")
        if err:
            return err
        if watcher_type == AgentWatcherType.EMAIL_INBOX:
            if not email_account_id:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "emailAccountId is required for email watchers."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            account_qs = EmailAccount.objects.filter(id=email_account_id, business_profile=agent.business_profile)
            if not request.user.is_staff:
                account_qs = account_qs.filter(user=request.user)
            email_account = account_qs.first()
            if email_account is None:
                return JsonResponse({"error": "EMAIL_ACCOUNT_NOT_FOUND", "message": "Email account not found."}, status=HTTPStatus.NOT_FOUND)
            if email_account.status != EmailAccountStatus.CONNECTED:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "Email account must be connected before enabling a watcher."},
                    status=HTTPStatus.BAD_REQUEST,
                )

        next_poll_at = timezone.now() if status == AgentWatcherStatus.ACTIVE else None

        watcher = AgentWatcher.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            created_by=request.user,
            run_spec=run_spec,
            run_spec_snapshot=run_spec_snapshot,
            conversation=conversation,
            email_account=email_account,
            name=name,
            status=status,
            visibility=visibility,
            watcher_type=watcher_type,
            watch_config=watch_config,
            destination_config=destination_config,
            poll_interval_seconds=poll_interval,
            max_events_per_poll=max_events,
            next_poll_at=next_poll_at,
            metadata=(payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {},
        )
        if watcher.conversation_id is None:
            ensure_watcher_thread(watcher)
        return JsonResponse({"watcher": _serialize_watcher(watcher)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PUT", "DELETE"])
def agent_watcher_detail(request: HttpRequest, agent_id: uuid.UUID, watcher_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=True)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        watcher = AgentWatcher.objects.filter(id=watcher_id, agent_profile=agent).select_related("email_account").first()
        if watcher is None:
            return JsonResponse({"error": "WATCHER_NOT_FOUND", "message": "Watcher not found."}, status=HTTPStatus.NOT_FOUND)

        if request.method == "GET":
            return JsonResponse({"watcher": _serialize_watcher(watcher)}, status=HTTPStatus.OK)

        if request.method == "DELETE":
            watcher.status = AgentWatcherStatus.ARCHIVED
            watcher.next_poll_at = None
            watcher.lease_expires_at = None
            watcher.save(update_fields=["status", "next_poll_at", "lease_expires_at", "updated_at"])
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        payload, error = _parse_json_body(request)
        if error:
            return error

        updates: list[str] = []
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "name cannot be blank."}, status=HTTPStatus.BAD_REQUEST)
            watcher.name = name[:160]
            updates.append("name")
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in {choice for choice, _ in AgentWatcherStatus.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
            watcher.status = status
            updates.append("status")
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            watcher.visibility = visibility
            updates.append("visibility")
        if "watcherType" in payload or "watcher_type" in payload:
            watcher_type = str(payload.get("watcherType") or payload.get("watcher_type") or "").strip().lower()
            if watcher_type not in {choice for choice, _ in AgentWatcherType.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid watcherType."}, status=HTTPStatus.BAD_REQUEST)
            watcher.watcher_type = watcher_type
            updates.append("watcher_type")
        if "watchConfig" in payload and isinstance(payload.get("watchConfig"), dict):
            watcher.watch_config = dict(payload.get("watchConfig") or {})
            updates.append("watch_config")
        if "destinationConfig" in payload and isinstance(payload.get("destinationConfig"), dict):
            watcher.destination_config = dict(payload.get("destinationConfig") or {})
            updates.append("destination_config")
        if "pollIntervalSeconds" in payload or "poll_interval_seconds" in payload:
            raw = payload.get("pollIntervalSeconds") if "pollIntervalSeconds" in payload else payload.get("poll_interval_seconds")
            try:
                poll_interval = int(raw)
            except (TypeError, ValueError):
                poll_interval = int(watcher.poll_interval_seconds or 300)
            watcher.poll_interval_seconds = max(60, min(int(poll_interval), 24 * 60 * 60))
            updates.append("poll_interval_seconds")
        if "maxEventsPerPoll" in payload or "max_events_per_poll" in payload:
            raw = payload.get("maxEventsPerPoll") if "maxEventsPerPoll" in payload else payload.get("max_events_per_poll")
            try:
                max_events = int(raw)
            except (TypeError, ValueError):
                max_events = int(watcher.max_events_per_poll or 5)
            watcher.max_events_per_poll = max(1, min(int(max_events), 25))
            updates.append("max_events_per_poll")
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            watcher.metadata = dict(payload.get("metadata") or {})
            updates.append("metadata")

        if "emailAccountId" in payload or "email_account_id" in payload:
            email_account_id_raw = payload.get("emailAccountId") or payload.get("email_account_id")
            email_account_id, err = _parse_uuid(email_account_id_raw, field="emailAccountId")
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
            watcher.email_account = email_account
            updates.append("email_account")

        if "conversationId" in payload or "conversation_id" in payload:
            conversation_id_raw = payload.get("conversationId") or payload.get("conversation_id")
            conversation_id, err = _parse_uuid(conversation_id_raw, field="conversationId")
            if err:
                return err
            conversation = None
            if conversation_id:
                conversation = Conversation.objects.filter(id=conversation_id, business_profile=agent.business_profile).first()
                if conversation is None:
                    return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."}, status=HTTPStatus.NOT_FOUND)
            watcher.conversation = conversation
            updates.append("conversation")

        if "runSpecId" in payload or "run_spec_id" in payload:
            run_spec_id_raw = payload.get("runSpecId") or payload.get("run_spec_id")
            run_spec_id, err = _parse_uuid(run_spec_id_raw, field="runSpecId")
            if err:
                return err
            run_spec = None
            if run_spec_id:
                run_spec = AgentRunSpec.objects.filter(id=run_spec_id, agent_profile=agent).first()
                if run_spec is None:
                    return JsonResponse({"error": "RUN_SPEC_NOT_FOUND", "message": "Run spec not found."}, status=HTTPStatus.NOT_FOUND)
                watcher.run_spec_snapshot = normalize_run_spec(run_spec.spec)
            watcher.run_spec = run_spec
            updates.append("run_spec")
            updates.append("run_spec_snapshot")

        if watcher.status == AgentWatcherStatus.ACTIVE:
            watcher.next_poll_at = timezone.now()
            watcher.lease_expires_at = None
            updates.extend(["next_poll_at", "lease_expires_at"])
        elif watcher.status in {AgentWatcherStatus.PAUSED, AgentWatcherStatus.ARCHIVED, AgentWatcherStatus.DRAFT}:
            watcher.next_poll_at = None
            watcher.lease_expires_at = None
            updates.extend(["next_poll_at", "lease_expires_at"])

        if not updates:
            return JsonResponse({"watcher": _serialize_watcher(watcher)}, status=HTTPStatus.OK)

        updates.append("updated_at")
        watcher.save(update_fields=sorted(set(updates)))
        if watcher.conversation_id is None:
            ensure_watcher_thread(watcher)
        return JsonResponse({"watcher": _serialize_watcher(watcher)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def agent_watcher_trigger(request: HttpRequest, agent_id: uuid.UUID, watcher_id: uuid.UUID) -> JsonResponse:
    """
    Manually nudge a watcher to poll immediately (used by "Run now").
    """

    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    feature_err = _ensure_subagents_enabled(request, agent=agent, allow_read_only=False)
    if feature_err:
        return feature_err

    with tenant_context(agent.business_profile_id):
        watcher = AgentWatcher.objects.filter(id=watcher_id, agent_profile=agent).first()
        if watcher is None:
            return JsonResponse({"error": "WATCHER_NOT_FOUND", "message": "Watcher not found."}, status=HTTPStatus.NOT_FOUND)

        if watcher.status == AgentWatcherStatus.ARCHIVED:
            return JsonResponse(
                {"error": "WATCHER_NOT_TRIGGERABLE", "message": "Watcher is archived."},
                status=HTTPStatus.CONFLICT,
            )

        if watcher.conversation_id is None:
            ensure_watcher_thread(watcher)

        AgentWatcher.objects.filter(id=watcher.id).update(next_poll_at=timezone.now(), lease_expires_at=None, updated_at=timezone.now())
        watcher.refresh_from_db()
        return JsonResponse({"watcher": _serialize_watcher(watcher)}, status=HTTPStatus.OK)
