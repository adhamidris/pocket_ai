from __future__ import annotations

from .run_shared import *  # noqa: F403
from apps.agentic_tasks.processing import ensure_task_conversation

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
                AgentRun.objects.select_related("agentic_task")
                .prefetch_related("artifacts")
                .filter(agent_profile=agent)
                .filter(_run_visibility_filter(request, agent=agent))
                .order_by("-created_at")
            )
            kind = str(request.GET.get("kind") or request.GET.get("type") or "").strip().lower()
            if kind in {"custom_assistant", "custom_assistants", "assistant", "assistants", "manual"}:
                qs = qs.none()
            elif kind in {"agentic_task", "agentic_tasks", "scheduled_task", "scheduled_tasks", "background"}:
                qs = qs.filter(agentic_task__isnull=False)
            status = str(request.GET.get("status") or "").strip().lower()
            if status:
                qs = qs.filter(status=status)
            agentic_task_id, err = _parse_uuid(request.GET.get("agenticTaskId"), field="agenticTaskId")
            if err:
                return err
            if agentic_task_id:
                qs = qs.filter(agentic_task_id=agentic_task_id)
            limit = max(1, min(int(str(request.GET.get("limit") or "50")), 200))
            offset = max(0, int(str(request.GET.get("offset") or "0")))
            return JsonResponse({"runs": [_serialize_run(item) for item in qs[offset : offset + limit]], "total": qs.count(), "limit": limit, "offset": offset}, status=HTTPStatus.OK)

        payload, error = _parse_json_body(request)
        if error:
            return error
        agentic_task_id, err = _parse_uuid((payload or {}).get("agenticTaskId"), field="agenticTaskId")
        if err:
            return err
        agentic_task = None
        if agentic_task_id:
            agentic_task = AgenticTask.objects.filter(id=agentic_task_id, agent_profile=agent).first()
            if agentic_task is None:
                return JsonResponse({"error": "TASK_NOT_FOUND", "message": "Agentic Task not found."}, status=HTTPStatus.NOT_FOUND)
        conversation_id, err = _parse_uuid((payload or {}).get("conversationId") or (payload or {}).get("conversation_id"), field="conversationId")
        if err:
            return err
        conversation = None
        if conversation_id:
            conversation = Conversation.objects.filter(id=conversation_id, business_profile=agent.business_profile).first()
            if conversation is None:
                return JsonResponse({"error": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."}, status=HTTPStatus.NOT_FOUND)
        elif agentic_task:
            conversation = ensure_task_conversation(agentic_task)
        visibility = str((payload or {}).get("visibility") or (agentic_task.visibility if agentic_task else AgentRunVisibility.INITIATOR)).strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid visibility."}, status=HTTPStatus.BAD_REQUEST)
        source = str((payload or {}).get("source") or (AgentRunSource.TASK if agentic_task else AgentRunSource.CHAT)).strip().lower()
        if source not in {choice for choice, _ in AgentRunSource.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid source."}, status=HTTPStatus.BAD_REQUEST)
        run = _create_run(
            agent=agent,
            created_by=request.user,
            agentic_task=agentic_task,
            conversation=conversation,
            title=str((payload or {}).get("title") or (agentic_task.name if agentic_task else "")),
            source=source,
            visibility=visibility,
            snapshot=_run_snapshot(agentic_task, (payload or {}).get("runSnapshot") or (payload or {}).get("agentic_task") or {}),
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
        agentic_task=run.agentic_task,
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
    if run.agentic_task_id:
        MemoryItem.objects.create(
            business_profile=run.business_profile,
            scope=MemoryScope.TASK,
            agent_profile=run.agent_profile,
            agentic_task=run.agentic_task,
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
            AgentRunCheckpoint.objects.select_related("run", "agentic_task", "conversation")
            .filter(id=checkpoint_id, business_profile=agent.business_profile)
            .filter(Q(agentic_task__agent_profile=agent) | Q(run__agent_profile=agent))
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
            agentic_task=run.agentic_task,
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


@csrf_protect
@require_http_methods(["GET"])
def agent_operations_status(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    agent, error = _resolve_agent_for_request(request, agent_id)
    if error:
        return error
    assert agent is not None

    now = timezone.now()
    stale_before = now - timedelta(seconds=90)
    agentic_task_schedule_heartbeat = cache.get("agentic_task_processor_heartbeat")
    agentic_task_run_heartbeat = cache.get("agentic_task_run_processor_heartbeat")
    sub_agent_run_heartbeat = cache.get("sub_agent_run_processor_heartbeat")

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
        agentic_tasks = AgenticTask.objects.filter(agent_profile=agent)
        schedule_processor = _heartbeat_payload(agentic_task_schedule_heartbeat)
        task_run_processor = _heartbeat_payload(agentic_task_run_heartbeat)
        sub_agent_processor = _heartbeat_payload(sub_agent_run_heartbeat)
        payload = {
            "operations": {
                "taskProcessingActive": bool(schedule_processor["active"] and task_run_processor["active"]),
                "agenticTaskProcessor": schedule_processor,
                "agenticTaskRunProcessor": task_run_processor,
                "subAgentRunProcessor": sub_agent_processor,
                "dueAgenticTasks": agentic_tasks.filter(
                    status=AgenticTaskStatus.ACTIVE,
                    schedule_enabled=True,
                    next_trigger_at__lte=now,
                ).count(),
                "queuedTaskRuns": runs.filter(agentic_task_id__isnull=False, status=AgentRunStatus.QUEUED).count(),
                "runningTaskRuns": runs.filter(agentic_task_id__isnull=False, status=AgentRunStatus.RUNNING).count(),
                "queuedSubAgentRuns": runs.filter(agentic_task_id__isnull=True, status=AgentRunStatus.QUEUED).count(),
                "runningSubAgentRuns": runs.filter(agentic_task_id__isnull=True, status=AgentRunStatus.RUNNING).count(),
                "failedRuns": runs.filter(status=AgentRunStatus.FAILED).count(),
            },
        }
    return JsonResponse(payload, status=HTTPStatus.OK)
