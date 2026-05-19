from __future__ import annotations

import uuid

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.constants import DEFAULT_ASSISTANT_ROLE
from apps.conversations.models import ConversationSender
from apps.conversations.portal import (
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalValidationError,
)
from apps.api.portal_chat.request_context import (
    _attach_actor_user_id_if_authorized,
    _json_error,
    _normalize_portal_metadata,
    _parse_json_body,
    _require_authenticated_user,
    _resolve_request_conversation,
    _service,
    _with_ui_language,
)
from apps.api.portal_chat.serializers import (
    _bootstrap_to_dict,
    _message_to_dict,
    _session_to_dict,
)


@require_GET
def resolve_portal_handle(request: HttpRequest, business_slug: str, agent_slug: str) -> JsonResponse:
    service = _service()
    try:
        business, agent = service.resolve_handle(business_slug, agent_slug)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    return JsonResponse(
        {
            "business": {"id": str(business.id), "name": business.name, "slug": business.slug},
            "agent": {
                "id": str(agent.id),
                "name": agent.name,
                "role": DEFAULT_ASSISTANT_ROLE,
                "slug": agent.slug,
                "shareable_path": agent.shareable_path,
            },
        }
    )


@require_POST
def bootstrap_session(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        _require_authenticated_user(request)
    except PortalAuthorizationError as exc:
        return _json_error("auth_required", str(exc), status=401)
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    business_slug = (payload.get("business_slug") or payload.get("businessSlug") or "").strip()
    agent_slug = (payload.get("agent_slug") or payload.get("agentSlug") or "").strip()
    existing_session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip() or None
    metadata = _with_ui_language(request, _normalize_portal_metadata(payload.get("metadata") or {}))

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
        result = service.bootstrap_session(
            business_slug=business_slug,
            agent_slug=agent_slug,
            existing_session_token=existing_session_token,
            metadata=metadata,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse(_bootstrap_to_dict(result), status=200)


@require_http_methods(["GET", "POST"])
def messages_endpoint(request: HttpRequest) -> JsonResponse:
    service = _service()
    if request.method == "GET":
        limit_param = request.GET.get("limit")
        limit = None
        if limit_param:
            try:
                limit = max(1, min(200, int(limit_param)))
            except ValueError:
                return _json_error("validation_error", "limit must be an integer between 1 and 200")
        try:
            conversation, session = _resolve_request_conversation(
                service=service,
                request=request,
                include_messages=False,
            )
            messages = service.list_messages_for_conversation(conversation=conversation, limit=limit)
        except PortalAuthorizationError as exc:
            status = 401 if str(exc) == "Authentication is required." else 403
            code = "auth_required" if status == 401 else "forbidden"
            return _json_error(code, str(exc), status=status)
        except PortalNotFoundError as exc:
            return _json_error("not_found", str(exc), status=404)
        except PortalValidationError as exc:
            return _json_error("validation_error", str(exc))
        return JsonResponse(
            {"session": _session_to_dict(session), "messages": [_message_to_dict(msg) for msg in messages]}
        )

    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    body = (payload.get("body") or "").strip()
    metadata = _with_ui_language(request, _normalize_portal_metadata(payload.get("metadata") or {}))

    try:
        conversation, _session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
        message = service.append_message(
            session_token=conversation.session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
            conversation=conversation,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)

    return JsonResponse({"message": _message_to_dict(message)}, status=201)


@require_POST
def submit_csat(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    try:
        score = int(payload.get("score"))
    except (TypeError, ValueError):
        return _json_error("validation_error", "score must be an integer between 1 and 5")
    comment = (payload.get("comment") or "").strip() or None

    try:
        conversation, _session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
        session = service.record_csat(
            session_token=conversation.session_token,
            score=score,
            comment=comment,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)

    return JsonResponse({"session": _session_to_dict(session)}, status=200)


@require_POST
def submit_feedback(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    feedback_type = (payload.get("feedback_type") or payload.get("feedbackType") or "").strip()
    if not feedback_type:
        return _json_error("validation_error", "conversation_id/session_token and feedback_type are required.")
    message_id_value = payload.get("message_id") or payload.get("messageId")
    message_id: uuid.UUID | None = None
    if message_id_value:
        try:
            message_id = uuid.UUID(str(message_id_value))
        except (TypeError, ValueError):
            return _json_error("validation_error", "message_id must be a valid UUID.")
    feedback_payload = {
        "query_text": payload.get("query_text"),
        "expected_behavior": payload.get("expected_behavior"),
        "expected_entities": payload.get("expected_entities") or [],
        "expected_aliases": payload.get("expected_aliases") or [],
        "notes": payload.get("notes"),
        "auto_promote": payload.get("auto_promote", True),
    }
    try:
        conversation, _session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
        feedback = service.record_feedback(
            session_token=conversation.session_token,
            feedback_type=feedback_type,
            message_id=message_id,
            payload=feedback_payload,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)

    return JsonResponse(
        {
            "feedback": {
                "id": str(feedback.id),
                "feedback_type": feedback.feedback_type,
                "created_at": feedback.created_at.isoformat(),
            }
        },
        status=201,
    )
