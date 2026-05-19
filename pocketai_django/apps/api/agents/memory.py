from __future__ import annotations

from .run_shared import *  # noqa: F403

@csrf_protect
@require_http_methods(["GET"])
def memory_collection(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    agent_id, err = _parse_uuid(request.GET.get("agentId") or request.GET.get("agent_id"), field="agentId")
    if err:
        return err
    business_id, err = _parse_uuid(request.GET.get("businessId") or request.GET.get("business_id"), field="businessId")
    if err:
        return err
    qs = MemoryItem.objects.select_related("business_profile", "agent_profile", "custom_assistant", "automation", "run", "conversation")
    if business_id:
        qs = qs.filter(business_profile_id=business_id)
    else:
        qs = qs.filter(Q(business_profile__user=request.user) | Q(agent_profile__user=request.user))
    if agent_id:
        qs = qs.filter(Q(agent_profile_id=agent_id) | Q(visibility=MemoryVisibility.SHARED))
    view = str(request.GET.get("view") or "saved").strip().lower()
    if view not in {"saved", "pending_review"}:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "Invalid memory view."}, status=HTTPStatus.BAD_REQUEST)
    status = MemoryStatus.PENDING_REVIEW if view == "pending_review" else MemoryStatus.ACTIVE
    qs = qs.filter(_curated_memory_filter(), status=status)
    query = str(request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(Q(content__icontains=query) | Q(key__icontains=query))
    limit = max(1, min(int(str(request.GET.get("limit") or "50")), 200))
    return JsonResponse(
        {"memory": [_serialize_memory(item) for item in qs.order_by("-updated_at")[:limit]], "view": view},
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET"])
def memory_detail(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    item = MemoryItem.objects.filter(id=memory_id).filter(Q(business_profile__user=request.user) | Q(agent_profile__user=request.user)).first()
    if item is None:
        return JsonResponse({"error": "MEMORY_NOT_FOUND", "message": "Memory item not found."}, status=HTTPStatus.NOT_FOUND)
    return JsonResponse({"memory": _serialize_memory(item)}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def memory_approve(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.ACTIVE, action=MemoryAuditAction.APPROVED)


@csrf_protect
@require_http_methods(["POST"])
def memory_reject(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.ARCHIVED, action=MemoryAuditAction.REJECTED)


@csrf_protect
@require_http_methods(["POST"])
def memory_archive(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.ARCHIVED, action=MemoryAuditAction.ARCHIVED)


@csrf_protect
@require_http_methods(["POST"])
def memory_delete(request: HttpRequest, memory_id: uuid.UUID) -> JsonResponse:
    return _memory_status_action(request, memory_id, status=MemoryStatus.DELETED, action=MemoryAuditAction.DELETED)


def _memory_status_action(request: HttpRequest, memory_id: uuid.UUID, *, status: str, action: str) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
    item = MemoryItem.objects.filter(id=memory_id).filter(Q(business_profile__user=request.user) | Q(agent_profile__user=request.user)).first()
    if item is None:
        return JsonResponse({"error": "MEMORY_NOT_FOUND", "message": "Memory item not found."}, status=HTTPStatus.NOT_FOUND)
    before = _serialize_memory(item)
    item.status = status
    item.reviewed_by = request.user
    item.reviewed_at = timezone.now()
    item.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    MemoryAuditEvent.objects.create(memory_item=item, business_profile=item.business_profile, actor_user=request.user, action=action, before=before, after=_serialize_memory(item))
    return JsonResponse({"memory": _serialize_memory(item)}, status=HTTPStatus.OK)
