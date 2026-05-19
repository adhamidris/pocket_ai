from __future__ import annotations

from .run_shared import *  # noqa: F403

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
