from __future__ import annotations

import json
from typing import Any, Mapping

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from pocketai.language import metadata_ui_language, normalize_language_code

from apps.conversations.models import Conversation
from apps.conversations.portal import (
    ChatPortalService,
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalSessionState,
    PortalValidationError,
)
from apps.conversations.portal_auth import (
    can_access_conversation,
    get_authorized_conversation,
    resolve_scope_for_user,
)


def _service() -> ChatPortalService:
    return ChatPortalService()


def _attach_actor_user_id_if_authorized(
    *,
    service: ChatPortalService,
    request: HttpRequest,
    business_slug: str,
    agent_slug: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return metadata
    if "actor_user_id" in metadata or "actorUserId" in metadata:
        return metadata
    try:
        resolve_scope_for_user(
            service=service,
            user=user,
            business_slug=business_slug,
            agent_slug=agent_slug,
        )
    except (PortalAuthorizationError, PortalNotFoundError):
        return metadata
    metadata["actor_user_id"] = str(user.id)
    return metadata


def _json_error(code: str, message: str, *, status: int = 400, extra: dict | None = None) -> JsonResponse:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if extra:
        payload["error"].update(extra)
    return JsonResponse(payload, status=status)


def _session_summary_to_dict(summary) -> dict[str, object]:
    return {
        "conversation_id": str(summary.conversation_id),
        "session_token": summary.session_token,
        "title": summary.title,
        "started_at": summary.started_at.isoformat(),
        "last_activity_at": summary.last_activity_at.isoformat(),
        "status": summary.status,
        "message_count": summary.message_count,
        "preview": summary.preview,
        "session_type": getattr(summary, "session_type", "chat"),
        "custom_assistant_id": str(summary.custom_assistant_id) if getattr(summary, "custom_assistant_id", None) else None,
        "custom_assistant_name": getattr(summary, "custom_assistant_name", ""),
        "custom_assistant_agent_name": getattr(summary, "custom_assistant_agent_name", ""),
    }


def _require_authenticated_user(request: HttpRequest):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        raise PortalAuthorizationError("Authentication is required.")
    return user


def _extract_conversation_id(payload: Mapping[str, object] | None = None, request: HttpRequest | None = None) -> str:
    if payload:
        value = payload.get("conversation_id") or payload.get("conversationId")
        if value:
            return str(value).strip()
    if request is not None:
        value = request.GET.get("conversation_id") or request.GET.get("conversationId")
        if value:
            return str(value).strip()
    return ""


def _resolve_request_conversation(
    *,
    service: ChatPortalService,
    request: HttpRequest,
    payload: Mapping[str, object] | None = None,
    include_messages: bool = False,
) -> tuple[Conversation, PortalSessionState]:
    user = _require_authenticated_user(request)
    conversation_id = _extract_conversation_id(payload, request)
    if conversation_id:
        conversation = get_authorized_conversation(
            service=service,
            user=user,
            conversation_id=conversation_id,
            include_messages=include_messages,
        )
        session = service.get_session_state_for_conversation(conversation)
        return conversation, session

    session_token = ""
    if payload:
        session_token = str(payload.get("session_token") or payload.get("sessionToken") or "").strip()
    if not session_token and request is not None:
        session_token = str(request.GET.get("session_token") or request.GET.get("sessionToken") or "").strip()
    if not session_token:
        raise PortalValidationError("conversation_id or session_token is required.")

    conversation = service.get_conversation(session_token=session_token, include_messages=include_messages)
    if not can_access_conversation(user, conversation):
        raise PortalAuthorizationError("You do not have access to this conversation.")
    session = service.get_session_state(session_token=session_token, conversation=conversation)
    return conversation, session


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PortalValidationError("Invalid JSON payload") from exc


def _normalize_portal_metadata(raw: object) -> dict[str, object]:
    return dict(raw) if isinstance(raw, Mapping) else {}


def _request_ui_language(request: HttpRequest) -> str:
    normalized = normalize_language_code(getattr(request, "LANGUAGE_CODE", ""))
    if normalized:
        return normalized
    cookie_name = str(getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language") or "django_language")
    cookie_language = normalize_language_code(request.COOKIES.get(cookie_name))
    if cookie_language:
        return cookie_language
    return ""


def _with_ui_language(request: HttpRequest, metadata: Mapping[str, object] | None) -> dict[str, object]:
    payload: dict[str, object] = dict(metadata) if isinstance(metadata, Mapping) else {}
    selected = metadata_ui_language(payload) or _request_ui_language(request)
    if selected:
        payload["ui_language"] = selected
    return payload
