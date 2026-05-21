from __future__ import annotations

import uuid
from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.crm.api.serializers import _serialize_duplicate_suggestion
from apps.crm.api.shared import (
    _json_error,
    _parse_json_body,
    _parse_limit,
    _parse_uuid_value,
    _resolve_business,
    _validation_error_response,
)
from apps.crm.domain.services import (
    get_duplicate_suggestion,
    ignore_duplicate_suggestion,
    list_duplicate_suggestions,
    merge_duplicate_suggestion,
    reopen_duplicate_suggestion,
)
from apps.crm.models import CrmDuplicateSuggestion


@require_http_methods(["GET"])
def crm_duplicate_suggestions_collection(request: HttpRequest) -> JsonResponse:
    business, error = _resolve_business(request, request.GET.get("business_id"), action="read")
    if error:
        return error
    assert business is not None
    try:
        items = list_duplicate_suggestions(
            business_profile=business,
            status=request.GET.get("status") or None,
            limit=_parse_limit(request.GET.get("limit"), default=100, maximum=200),
        )
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"items": [_serialize_duplicate_suggestion(item) for item in items]}, status=HTTPStatus.OK)


@require_http_methods(["GET", "PATCH"])
def crm_duplicate_suggestion_detail(request: HttpRequest, suggestion_id: uuid.UUID) -> JsonResponse:
    payload = None
    if request.method == "PATCH":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "duplicate_manage")
    if error:
        return error
    assert business is not None
    try:
        suggestion = get_duplicate_suggestion(business_profile=business, suggestion_id=suggestion_id)
    except CrmDuplicateSuggestion.DoesNotExist:
        return _json_error("NOT_FOUND", "Duplicate suggestion not found.", status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"duplicateSuggestion": _serialize_duplicate_suggestion(suggestion)}, status=HTTPStatus.OK)
    action = str((payload or {}).get("action") or "").strip().lower()
    try:
        if action == "ignore":
            suggestion = ignore_duplicate_suggestion(
                business_profile=business,
                actor=request.user,
                suggestion=suggestion,
                resolution_note=str((payload or {}).get("resolutionNote") or ""),
            )
        elif action == "reopen":
            suggestion = reopen_duplicate_suggestion(business_profile=business, actor=request.user, suggestion=suggestion)
        elif action == "merge":
            merged_record_id = None
            if (payload or {}).get("recordId"):
                merged_record_id = _parse_uuid_value((payload or {}).get("recordId"), field_name="recordId")
            suggestion = merge_duplicate_suggestion(
                business_profile=business,
                actor=request.user,
                suggestion=suggestion,
                merged_record_id=merged_record_id,
                resolution_note=str((payload or {}).get("resolutionNote") or ""),
            )
        else:
            return _json_error(
                "VALIDATION_ERROR",
                "action must be one of ignore, reopen, or merge.",
                status=HTTPStatus.BAD_REQUEST,
                details={"action": ["Unsupported action."]},
            )
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"duplicateSuggestion": _serialize_duplicate_suggestion(suggestion)}, status=HTTPStatus.OK)
