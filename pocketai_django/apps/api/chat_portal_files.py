from __future__ import annotations

import uuid
from datetime import timedelta

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from apps.conversations.portal import (
    ChatPortalService,
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalValidationError,
)
from apps.conversations.portal_auth import can_access_conversation, get_authorized_conversation
from apps.conversations.portal_files import (
    PortalFileError,
    PortalFileTokenError,
    create_conversation_upload_from_request,
    open_portal_file_response,
    portal_file_block,
    sign_portal_file_token,
    verify_portal_file_token,
)
from core.tenancy import tenant_context


def _json_error(code: str, message: str, *, status: int = 400, extra: dict | None = None) -> JsonResponse:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if extra:
        payload["error"].update(extra)
    return JsonResponse(payload, status=status)


def _service() -> ChatPortalService:
    return ChatPortalService()


def _resolve_file_conversation(
    *,
    request: HttpRequest,
    service: ChatPortalService,
):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        raise PortalAuthorizationError("Authentication is required.")
    conversation_id = (request.POST.get("conversation_id") or request.POST.get("conversationId") or "").strip()
    if not conversation_id:
        conversation_id = (request.GET.get("conversation_id") or request.GET.get("conversationId") or "").strip()
    if conversation_id:
        return get_authorized_conversation(
            service=service,
            user=user,
            conversation_id=conversation_id,
            include_messages=False,
        )

    session_token = (request.POST.get("session_token") or request.POST.get("sessionToken") or "").strip()
    if not session_token:
        session_token = (request.GET.get("session_token") or request.GET.get("sessionToken") or "").strip()
    if not session_token:
        raise PortalValidationError("conversation_id or session_token is required.")
    conversation = service.get_conversation(session_token=session_token, include_messages=False)
    if not can_access_conversation(user, conversation):
        raise PortalAuthorizationError("You do not have access to this conversation.")
    return conversation


def _serialize_file(obj) -> dict[str, object]:
    return {
        "id": str(obj.id),
        "kind": obj.kind,
        "status": obj.status,
        "filename": obj.filename,
        "content_type": obj.content_type,
        "size_bytes": obj.size_bytes,
        "page_count": obj.page_count,
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
        "metadata": obj.metadata or {},
    }


@require_POST
def portal_file_upload(request: HttpRequest) -> JsonResponse:
    """
    Upload a file for an authenticated chat conversation.

    POST multipart/form-data:
      - session_token
      - file
    """

    uploaded = request.FILES.get("file")
    if uploaded is None:
        return _json_error("validation_error", "file is required.")

    from django.conf import settings

    filename = (getattr(uploaded, "name", "") or "").lower()
    content_type = (getattr(uploaded, "content_type", "") or "").lower()
    if not (filename.endswith(".pdf") or "pdf" in content_type):
        return _json_error("validation_error", "Only PDF uploads are supported right now.")

    max_bytes = int(getattr(settings, "PORTAL_FILE_UPLOAD_MAX_BYTES", 25 * 1024 * 1024) or 0) or (25 * 1024 * 1024)
    max_pdf_pages = int(getattr(settings, "PORTAL_PDF_MAX_PAGES", 250) or 0) or None

    service = _service()
    try:
        conversation = _resolve_file_conversation(request=request, service=service)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    with tenant_context(conversation.business_profile_id):
        try:
            convo_file, ingest_meta = create_conversation_upload_from_request(
                conversation=conversation,
                uploaded_file=uploaded,
                max_bytes=max_bytes,
                max_pdf_pages=max_pdf_pages,
            )
        except PortalFileError as exc:
            return _json_error("upload_failed", str(exc), status=400)

        try:
            message = service.append_message(
                session_token=conversation.session_token,
                sender="customer",
                body=convo_file.filename,
                metadata={
                    "type": "file_upload",
                    "file_ids": [str(convo_file.id)],
                },
                content_blocks=[portal_file_block(convo_file, label="Uploaded")],
                conversation=conversation,
            )
        except (PortalValidationError, PortalNotFoundError):
            message = None

        ttl_seconds = int(getattr(settings, "PORTAL_FILE_DOWNLOAD_TTL_SECONDS", 3600) or 0)
        if ttl_seconds <= 0:
            ttl_seconds = 3600
        token = sign_portal_file_token(
            file_id=convo_file.id,
            business_id=conversation.business_profile_id,
            ttl=timedelta(seconds=ttl_seconds),
        )
        download_url = f"/api/chat/portal/files/{convo_file.id}/download/?token={token}"

        response: dict[str, object] = {
            "file": _serialize_file(convo_file),
            "download_url": download_url,
            "ingest": ingest_meta,
        }
        if message is not None:
            response["message"] = {
                "id": str(message.id),
                "sender": message.sender,
                "body": message.body,
                "sent_at": message.sent_at.isoformat(),
                "metadata": message.metadata,
                "content_blocks": message.content_blocks,
            }
        return JsonResponse(response, status=201)


@require_GET
def portal_file_download(request: HttpRequest, file_id: uuid.UUID):
    """
    Download a portal file (upload or generated artifact) via a signed token.
    """

    token = (request.GET.get("token") or "").strip()
    if not token:
        return _json_error("validation_error", "token is required.", status=400)
    try:
        payload = verify_portal_file_token(token)
    except PortalFileTokenError as exc:
        return _json_error("invalid_token", str(exc), status=403)
    if payload.file_id != file_id:
        return _json_error("invalid_token", "token does not match file id.", status=403)

    # We don't require session_token for downloads; signature + TTL is the access boundary.
    from apps.conversations.models import ConversationFile

    with tenant_context(payload.business_id):
        file = ConversationFile.objects.filter(id=file_id).select_related("conversation").first()
        if file is None:
            return _json_error("not_found", "file not found.", status=404)
        try:
            return open_portal_file_response(file=file, download=True)
        except FileNotFoundError:
            return _json_error("not_found", "file not found.", status=404)
        except PortalFileError as exc:
            return _json_error("download_failed", str(exc), status=403)


@require_GET
def portal_file_download_url(request: HttpRequest, file_id: uuid.UUID) -> JsonResponse:
    """
    Return a fresh signed download URL for a portal file.

    This avoids persisting expiring tokens inside message transcripts.
    """

    service = _service()
    try:
        conversation = _resolve_file_conversation(request=request, service=service)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    from apps.conversations.models import ConversationFile
    from django.conf import settings

    with tenant_context(conversation.business_profile_id):
        file = ConversationFile.objects.filter(id=file_id, conversation=conversation).first()
        if file is None:
            return _json_error("not_found", "file not found.", status=404)

        ttl_seconds = int(getattr(settings, "PORTAL_FILE_DOWNLOAD_TTL_SECONDS", 3600) or 0)
        if ttl_seconds <= 0:
            ttl_seconds = 3600
        token = sign_portal_file_token(
            file_id=file.id,
            business_id=conversation.business_profile_id,
            ttl=timedelta(seconds=ttl_seconds),
        )
        download_url = f"/api/chat/portal/files/{file.id}/download/?token={token}"
        return JsonResponse({"download_url": download_url})
