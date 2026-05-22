from __future__ import annotations

from .run_shared import *  # noqa: F403
from apps.agentic_tasks.processing import ACTIVE_RUN_STATUSES, ensure_task_conversation


@csrf_protect
@require_http_methods(["POST"])
def agentic_task_run(request: HttpRequest, agent_id: uuid.UUID, agentic_task_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None
    with tenant_context(agent.business_profile_id):
        agentic_task = AgenticTask.objects.filter(id=agentic_task_id, agent_profile=agent).first()
        if agentic_task is None:
            return JsonResponse({"error": "TASK_NOT_FOUND", "message": "Agentic Task not found."}, status=HTTPStatus.NOT_FOUND)
        payload, error = _parse_json_body(request)
        if error:
            return error
        active_run = AgentRun.objects.filter(agentic_task=agentic_task, status__in=ACTIVE_RUN_STATUSES).order_by("-created_at").first()
        conversation = ensure_task_conversation(agentic_task)
        if active_run is not None:
            return JsonResponse({"run": _serialize_run(active_run), "agenticTask": _serialize_agentic_task(agentic_task)}, status=HTTPStatus.OK)
        run = _create_run(
            agent=agent,
            created_by=request.user,
            agentic_task=agentic_task,
            conversation=conversation,
            title=agentic_task.name,
            source=AgentRunSource.TASK,
            visibility=agentic_task.visibility,
            snapshot=_run_snapshot(agentic_task),
            metadata={"agentic_task_id": str(agentic_task.id), "trigger": "manual"},
        )
        AgenticTask.objects.filter(id=agentic_task.id).update(last_triggered_at=timezone.now(), updated_at=timezone.now())
        return JsonResponse({"run": _serialize_run(run), "agenticTask": _serialize_agentic_task(agentic_task)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "POST"])
def agentic_tasks_collection(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        if request.method == "GET":
            status = str(request.GET.get("status") or "").strip().lower()
            qs = AgenticTask.objects.select_related("agent_profile").filter(agent_profile=agent).order_by("-created_at")
            if status and status != "all":
                qs = qs.filter(status=status)
            agentic_tasks = list(qs[:200])
            agentic_task_ids = [item.id for item in agentic_tasks]
            latest_runs: dict[uuid.UUID, AgentRun] = {}
            open_checkpoints: dict[uuid.UUID, AgentRunCheckpoint] = {}
            if agentic_task_ids:
                run_qs = (
                    AgentRun.objects.select_related("agentic_task")
                    .filter(agent_profile=agent, agentic_task_id__in=agentic_task_ids)
                    .filter(_run_visibility_filter(request, agent=agent))
                    .order_by("-created_at")[:500]
                )
                for run in run_qs:
                    if run.agentic_task_id not in latest_runs:
                        latest_runs[run.agentic_task_id] = run
                    if len(latest_runs) == len(agentic_task_ids):
                        break
                checkpoint_qs = (
                    AgentRunCheckpoint.objects.filter(
                        agentic_task_id__in=agentic_task_ids,
                        status=AgentRunCheckpointStatus.OPEN,
                    )
                    .order_by("-updated_at", "-created_at")[:500]
                )
                for checkpoint in checkpoint_qs:
                    if checkpoint.agentic_task_id and checkpoint.agentic_task_id not in open_checkpoints:
                        open_checkpoints[checkpoint.agentic_task_id] = checkpoint
            for agentic_task in agentic_tasks:
                agentic_task.open_checkpoint = open_checkpoints.get(agentic_task.id)
            return JsonResponse({"agenticTasks": [_serialize_agentic_task(item, latest_runs.get(item.id)) for item in agentic_tasks]}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "name is required."}, status=HTTPStatus.BAD_REQUEST)
        status = str((payload or {}).get("status") or AgenticTaskStatus.DRAFT).strip().lower()
        if status not in {choice for choice, _ in AgenticTaskStatus.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
        visibility = str((payload or {}).get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
        review_mode = str((payload or {}).get("reviewMode") or (payload or {}).get("review_mode") or AgenticTaskReviewMode.ON_RISK).strip().lower()
        if review_mode not in {choice for choice, _ in AgenticTaskReviewMode.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid reviewMode."}, status=HTTPStatus.BAD_REQUEST)
        autonomy_mode = str((payload or {}).get("autonomyMode") or (payload or {}).get("autonomy_mode") or AgenticTaskAutonomyMode.DRAFT_FOR_APPROVAL).strip().lower()
        if autonomy_mode not in {choice for choice, _ in AgenticTaskAutonomyMode.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid autonomyMode."}, status=HTTPStatus.BAD_REQUEST)
        schedule_enabled = bool((payload or {}).get("scheduleEnabled") or (payload or {}).get("schedule_enabled"))
        schedule_config = dict((payload or {}).get("scheduleConfig") or (payload or {}).get("schedule_config") or {})
        next_trigger_at = None
        if status == AgenticTaskStatus.ACTIVE and schedule_enabled:
            try:
                next_trigger_at = _compute_next_schedule(schedule_config, after=timezone.now())
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        agentic_task = AgenticTask.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            created_by=request.user,
            name=name[:160],
            description=str((payload or {}).get("description") or "")[:4000],
            status=status,
            visibility=visibility,
            schedule_enabled=schedule_enabled,
            schedule_config=schedule_config,
            review_mode=review_mode,
            autonomy_mode=autonomy_mode,
            instructions=normalize_workflow_instructions((payload or {}).get("instructions") or {}),
            state=dict((payload or {}).get("state") or {}),
            next_trigger_at=next_trigger_at,
            metadata=dict((payload or {}).get("metadata") or {}),
        )
        ensure_task_conversation(agentic_task)
        return JsonResponse({"agenticTask": _serialize_agentic_task(agentic_task)}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
def agentic_task_detail(request: HttpRequest, agent_id: uuid.UUID, agentic_task_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    with tenant_context(agent.business_profile_id):
        agentic_task = AgenticTask.objects.filter(id=agentic_task_id, agent_profile=agent).first()
        if agentic_task is None:
            return JsonResponse({"error": "TASK_NOT_FOUND", "message": "Agentic Task not found."}, status=HTTPStatus.NOT_FOUND)
        if request.method == "GET":
            agentic_task.open_checkpoint = (
                AgentRunCheckpoint.objects.filter(agentic_task=agentic_task, status=AgentRunCheckpointStatus.OPEN)
                .order_by("-updated_at", "-created_at")
                .first()
            )
            return JsonResponse({"agenticTask": _serialize_agentic_task(agentic_task)}, status=HTTPStatus.OK)
        if request.method == "DELETE":
            with transaction.atomic():
                agentic_task = (
                    AgenticTask.objects.select_for_update()
                    .get(id=agentic_task.id)
                )
                _cancel_open_agentic_task_runs(agentic_task, reason="Agentic Task archived", action="delete")
                agentic_task.status = AgenticTaskStatus.ARCHIVED
                agentic_task.next_trigger_at = None
                agentic_task.save(update_fields=["status", "next_trigger_at", "updated_at"])
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        payload, error = _parse_json_body(request)
        if error:
            return error
        updates: list[str] = []
        for field in ("name", "description"):
            if field in payload:
                setattr(agentic_task, field, str(payload.get(field) or "").strip()[: 160 if field == "name" else 4000])
                updates.append(field)
        if "status" in payload:
            status = str(payload.get("status") or "").strip().lower()
            if status not in {choice for choice, _ in AgenticTaskStatus.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid status."}, status=HTTPStatus.BAD_REQUEST)
            agentic_task.status = status
            updates.append("status")
        if "visibility" in payload:
            visibility = str(payload.get("visibility") or "").strip().lower()
            if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
            agentic_task.visibility = visibility
            updates.append("visibility")
        if "reviewMode" in payload or "review_mode" in payload:
            review_mode = str(payload.get("reviewMode") or payload.get("review_mode") or "").strip().lower()
            if review_mode not in {choice for choice, _ in AgenticTaskReviewMode.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid reviewMode."}, status=HTTPStatus.BAD_REQUEST)
            agentic_task.review_mode = review_mode
            updates.append("review_mode")
        if "autonomyMode" in payload or "autonomy_mode" in payload:
            autonomy_mode = str(payload.get("autonomyMode") or payload.get("autonomy_mode") or "").strip().lower()
            if autonomy_mode not in {choice for choice, _ in AgenticTaskAutonomyMode.choices}:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid autonomyMode."}, status=HTTPStatus.BAD_REQUEST)
            agentic_task.autonomy_mode = autonomy_mode
            updates.append("autonomy_mode")
        if "scheduleEnabled" in payload or "schedule_enabled" in payload:
            agentic_task.schedule_enabled = bool(payload.get("scheduleEnabled") if "scheduleEnabled" in payload else payload.get("schedule_enabled"))
            updates.append("schedule_enabled")
        for public, field in (("scheduleConfig", "schedule_config"), ("instructions", "instructions"), ("state", "state"), ("metadata", "metadata")):
            if public in payload or field in payload:
                value = payload.get(public) if public in payload else payload.get(field)
                setattr(agentic_task, field, normalize_workflow_instructions(value) if field == "instructions" else dict(value or {}))
                updates.append(field)
        if {"status", "schedule_enabled", "schedule_config"} & set(updates):
            try:
                agentic_task.next_trigger_at = _compute_next_schedule(agentic_task.schedule_config, after=timezone.now()) if agentic_task.status == AgenticTaskStatus.ACTIVE and agentic_task.schedule_enabled else None
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            updates.append("next_trigger_at")
        if not updates:
            return JsonResponse({"agenticTask": _serialize_agentic_task(agentic_task)}, status=HTTPStatus.OK)
        agentic_task.save(update_fields=sorted(set([*updates, "updated_at"])))
        if "status" in updates and agentic_task.status == AgenticTaskStatus.PAUSED:
            cancel_existing = bool((payload or {}).get("cancelOpenRuns", False))
            if cancel_existing:
                cancelled = _cancel_open_agentic_task_runs(agentic_task, reason="Agentic Task paused")
                agentic_task_meta = dict(agentic_task.metadata or {}) if isinstance(agentic_task.metadata, dict) else {}
                agentic_task_meta["last_pause_cancelled_runs"] = cancelled
                AgenticTask.objects.filter(id=agentic_task.id).update(metadata=agentic_task_meta, updated_at=timezone.now())
                agentic_task.metadata = agentic_task_meta
        return JsonResponse({"agenticTask": _serialize_agentic_task(agentic_task)}, status=HTTPStatus.OK)
