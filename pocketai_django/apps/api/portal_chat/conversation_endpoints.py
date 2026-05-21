from __future__ import annotations

import uuid

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.api.portal_chat.request_context import (
    _attach_actor_user_id_if_authorized,
    _json_error,
    _normalize_portal_metadata,
    _parse_json_body,
    _require_authenticated_user,
    _resolve_request_conversation,
    _service,
    _session_summary_to_dict,
    _with_ui_language,
)
from apps.api.portal_chat.serializers import (
    _bootstrap_to_dict,
    _message_to_dict,
    _portal_turn_to_dict,
    _session_to_dict,
)
from apps.conversations.models import (
    ConversationSender,
    PortalTurn,
    PortalTurnStatus,
)
from apps.conversations.portal import (
    PortalAuthorizationError,
    PortalMessage,
    PortalNotFoundError,
    PortalValidationError,
)
from apps.conversations.portal_service.auth import (
    get_authorized_conversation,
    resolve_scope_for_user,
)
from apps.conversations.portal_turn.runner import run_turn_background
from core.tenancy import tenant_context

# ------------------------------------------------------------------
# Session Management Endpoints
# ------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def conversations_collection(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        user = _require_authenticated_user(request)
    except PortalAuthorizationError as exc:
        return _json_error("auth_required", str(exc), status=401)

    if request.method == "GET":
        business_slug = (request.GET.get("business_slug") or request.GET.get("businessSlug") or "").strip()
        agent_slug = (request.GET.get("agent_slug") or request.GET.get("agentSlug") or "").strip()
        if not business_slug or not agent_slug:
            return _json_error("validation_error", "business_slug and agent_slug are required.")
        raw_limit = request.GET.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else 50
        except (TypeError, ValueError):
            return _json_error("validation_error", "limit must be an integer.")
        limit = max(1, min(limit, 100))
        try:
            sessions = service.list_owned_sessions(
                owner_user=user,
                business_slug=business_slug,
                agent_slug=agent_slug,
                limit=limit,
            )
        except (PortalNotFoundError, PortalAuthorizationError) as exc:
            status = 403 if isinstance(exc, PortalAuthorizationError) else 404
            code = "forbidden" if status == 403 else "not_found"
            return _json_error(code, str(exc), status=status)
        return JsonResponse({"conversations": [_session_summary_to_dict(item) for item in sessions]})

    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    business_slug = (payload.get("business_slug") or payload.get("businessSlug") or "").strip()
    agent_slug = (payload.get("agent_slug") or payload.get("agentSlug") or "").strip()
    metadata = _with_ui_language(request, _normalize_portal_metadata(payload.get("metadata") or {}))
    custom_assistant_id = str(payload.get("custom_assistant_id") or payload.get("customAssistantId") or "").strip()
    if not business_slug or not agent_slug:
        return _json_error("validation_error", "business_slug and agent_slug are required.")

    metadata = _attach_actor_user_id_if_authorized(
        service=service,
        request=request,
        business_slug=business_slug,
        agent_slug=agent_slug,
        metadata=metadata,
    )
    try:
        resolve_scope_for_user(
            service=service,
            user=user,
            business_slug=business_slug,
            agent_slug=agent_slug,
        )
        if custom_assistant_id:
            result = service.create_owned_custom_assistant_session(
                owner_user=user,
                business_slug=business_slug,
                agent_slug=agent_slug,
                custom_assistant_id=custom_assistant_id,
                metadata=metadata,
                title=str(payload.get("title") or "").strip(),
            )
        else:
            result = service.create_owned_session(
                owner_user=user,
                business_slug=business_slug,
                agent_slug=agent_slug,
                metadata=metadata,
            )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except (PortalNotFoundError, PortalAuthorizationError) as exc:
        status = 403 if isinstance(exc, PortalAuthorizationError) else 404
        code = "forbidden" if status == 403 else "not_found"
        return _json_error(code, str(exc), status=status)
    return JsonResponse(_bootstrap_to_dict(result), status=201)


@require_GET
def conversation_messages(request: HttpRequest, conversation_id: uuid.UUID) -> JsonResponse:
    service = _service()
    try:
        conversation = get_authorized_conversation(
            service=service,
            user=_require_authenticated_user(request),
            conversation_id=conversation_id,
            include_messages=False,
        )
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    limit_param = request.GET.get("limit")
    limit = None
    if limit_param:
        try:
            limit = max(1, min(200, int(limit_param)))
        except ValueError:
            return _json_error("validation_error", "limit must be an integer between 1 and 200")
    messages = service.list_messages_for_conversation(conversation=conversation, limit=limit)
    session = service.get_session_state_for_conversation(conversation)
    return JsonResponse(
        {"session": _session_to_dict(session), "messages": [_message_to_dict(msg) for msg in messages]}
    )


@require_POST
def conversation_turns_create(request: HttpRequest, conversation_id: uuid.UUID) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))
    try:
        conversation = get_authorized_conversation(
            service=service,
            user=_require_authenticated_user(request),
            conversation_id=conversation_id,
            include_messages=False,
        )
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    body = (payload.get("body") or "").strip()
    metadata = _with_ui_language(request, _normalize_portal_metadata(payload.get("metadata") or {}))
    if not body:
        return _json_error("validation_error", "body is required.")

    try:
        customer_message = service.append_message(
            session_token=conversation.session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
            conversation=conversation,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    agent = conversation.agent_profile
    if not agent:
        return _json_error("validation_error", "Agent profile is missing.", status=500)

    execution_mode = str(getattr(settings, "PORTAL_TURN_EXECUTION_MODE", "thread") or "thread").strip().lower()
    turn_metadata: dict[str, object] = {
        "source": "portal",
        "origin": "conversation_turn_create",
        "execution_mode": execution_mode,
    }
    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.create(
            conversation=conversation,
            agent_profile=agent,
            status=PortalTurnStatus.STREAMING,
            run_after=timezone.now(),
            user_message=body,
            metadata=turn_metadata,
        )
    if execution_mode != "worker":
        run_turn_background(turn_id=turn.id, business_id=business_id)

    session = service.get_session_state_for_conversation(conversation)
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "turn": _portal_turn_to_dict(turn),
            "customer_message_id": str(customer_message.id) if customer_message else None,
        },
        status=201,
    )


@require_POST
def list_portal_sessions(request: HttpRequest) -> JsonResponse:
    return _json_error(
        "deprecated",
        "Session-token history is retired. Use /api/chat/conversations/ for backend-owned conversation history.",
        status=410,
    )


@require_POST
def create_portal_session(request: HttpRequest) -> JsonResponse:
    return _json_error(
        "deprecated",
        "Public session creation is retired. Use POST /api/chat/conversations/ from the authenticated chat workspace.",
        status=410,
    )


@require_POST
def portal_turn_create(request: HttpRequest) -> JsonResponse:
    """
    Create a new portal turn (event-sourced streaming).

    Request body:
    {
        "session_token": "...",
        "body": "...",
        "metadata": {}
    }
    """
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    body = (payload.get("body") or "").strip()
    metadata = _with_ui_language(request, _normalize_portal_metadata(payload.get("metadata") or {}))

    if not body:
        return _json_error("validation_error", "body is required.")

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    customer_message: PortalMessage | None = None
    try:
        customer_message = service.append_message(
            session_token=conversation.session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
            conversation=conversation,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    agent = conversation.agent_profile
    if not agent:
        return _json_error("validation_error", "Agent profile is missing.", status=500)

    execution_mode = str(getattr(settings, "PORTAL_TURN_EXECUTION_MODE", "thread") or "thread").strip().lower()
    turn_metadata: dict[str, object] = {
        "source": "portal",
        "origin": "turn_create",
        "execution_mode": execution_mode,
    }
    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.create(
            conversation=conversation,
            agent_profile=agent,
            status=PortalTurnStatus.STREAMING,
            run_after=timezone.now(),
            user_message=body,
            metadata=turn_metadata,
        )
    if execution_mode == "worker":
        # Phase 2: turn execution is handled by a dedicated DB-leased worker pool.
        # The portal streams events from Postgres (LISTEN/NOTIFY + event log), so the UX remains identical.
        pass
    else:
        run_turn_background(turn_id=turn.id, business_id=business_id)

    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "turn": _portal_turn_to_dict(turn),
            "customer_message_id": str(customer_message.id) if customer_message else None,
        },
        status=201,
    )
