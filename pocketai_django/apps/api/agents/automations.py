from __future__ import annotations

from .run_shared import *  # noqa: F403


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
        next_trigger_at = None
        if status == AutomationStatus.ACTIVE:
            try:
                next_trigger_at = _compute_next_trigger(trigger_config, after=timezone.now())
            except CronScheduleError as exc:
                return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        automation = Automation.objects.create(
            business_profile=agent.business_profile,
            agent_profile=agent,
            created_by=request.user,
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
        if {"status", "trigger_type", "trigger_config"} & set(updates):
            try:
                automation.next_trigger_at = _compute_next_trigger(automation.trigger_config, after=timezone.now()) if automation.status == AutomationStatus.ACTIVE else None
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
