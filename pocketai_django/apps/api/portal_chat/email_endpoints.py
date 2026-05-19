from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_POST

from apps.api.portal_chat.email_drafts import (
    _clear_pending_email_draft_meta,
    _pending_email_account_id_for_draft,
)
from apps.api.portal_chat.request_context import (
    _json_error,
    _parse_json_body,
    _resolve_request_conversation,
    _service,
)
from apps.api.portal_chat.serializers import _session_to_dict
from apps.conversations.portal import (
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalValidationError,
)


@require_POST
def portal_email_send_draft(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    draft_id = (payload.get("draft_id") or payload.get("draftId") or "").strip()
    email_account_id = (payload.get("email_account_id") or payload.get("emailAccountId") or "").strip()
    if not draft_id:
        return _json_error("validation_error", "conversation_id/session_token and draft_id are required.")

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

    if not email_account_id:
        email_account_id = _pending_email_account_id_for_draft(conversation, draft_id=draft_id)

    arguments: dict[str, object] = {"draft_id": draft_id}
    if email_account_id:
        arguments["email_account_id"] = email_account_id

    from apps.mcp.tools import execute_tool
    from apps.mcp.types import ToolExecutionContext

    result = execute_tool(
        "email_send_draft",
        arguments,
        conversation=conversation,
        context=ToolExecutionContext(),
    )

    status_value = str(result.get("status") or "").strip().lower()
    if status_value != "ok":
        hint = str(result.get("hint") or result.get("error") or "Email send failed.").strip()
        return JsonResponse(
            {
                "session": _session_to_dict(session),
                "result": result,
                "error": {"code": "email_send_failed", "message": hint or "Email send failed."},
            },
            status=400,
        )

    _clear_pending_email_draft_meta(conversation, draft_id=draft_id, email_account_id=email_account_id or None)
    return JsonResponse({"session": _session_to_dict(session), "result": result})


@require_POST
def portal_email_discard_draft(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    draft_id = (payload.get("draft_id") or payload.get("draftId") or "").strip()
    email_account_id = (payload.get("email_account_id") or payload.get("emailAccountId") or "").strip()
    if not draft_id:
        return _json_error("validation_error", "conversation_id/session_token and draft_id are required.")

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

    if not email_account_id:
        email_account_id = _pending_email_account_id_for_draft(conversation, draft_id=draft_id)

    cleared = _clear_pending_email_draft_meta(conversation, draft_id=draft_id, email_account_id=email_account_id or None)
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "discarded": True,
            "cleared_pending": cleared,
        }
    )
