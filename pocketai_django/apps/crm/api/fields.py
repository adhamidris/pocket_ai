from __future__ import annotations

import uuid
from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.crm.api.serializers import _serialize_field_definition
from apps.crm.api.shared import _json_error, _parse_json_body, _resolve_business, _validation_error_response
from apps.crm.domain.services import (
    archive_field_definition,
    get_field_definition,
    list_field_definitions,
    restore_field_definition,
    update_field_definition,
    upsert_field_definition,
)
from apps.crm.models import CrmFieldDefinition


@require_http_methods(["GET", "POST"])
def crm_field_definitions_collection(request: HttpRequest) -> JsonResponse:
    payload = None
    if request.method == "POST":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "field_definition_manage")
    if error:
        return error
    assert business is not None
    if request.method == "GET":
        items = list_field_definitions(business_profile=business, target_object=request.GET.get("target"))
        return JsonResponse({"items": [_serialize_field_definition(item) for item in items]}, status=HTTPStatus.OK)
    try:
        definition = upsert_field_definition(business_profile=business, payload=payload or {}, actor=request.user)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"fieldDefinition": _serialize_field_definition(definition)}, status=HTTPStatus.CREATED)


@require_http_methods(["GET", "PATCH", "DELETE"])
def crm_field_definition_detail(request: HttpRequest, field_definition_id: uuid.UUID) -> JsonResponse:
    payload = None
    if request.method in {"PATCH", "DELETE"}:
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "field_definition_manage")
    if error:
        return error
    assert business is not None
    try:
        definition = get_field_definition(business_profile=business, field_definition_id=field_definition_id)
    except CrmFieldDefinition.DoesNotExist:
        return _json_error("NOT_FOUND", "Field definition not found.", status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"fieldDefinition": _serialize_field_definition(definition)}, status=HTTPStatus.OK)
    try:
        if request.method == "DELETE" or (payload or {}).get("action") == "archive":
            definition = archive_field_definition(business_profile=business, actor=request.user, definition=definition)
        elif (payload or {}).get("action") == "restore":
            definition = restore_field_definition(business_profile=business, actor=request.user, definition=definition)
        else:
            definition = update_field_definition(business_profile=business, definition=definition, payload=payload or {}, actor=request.user)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"fieldDefinition": _serialize_field_definition(definition)}, status=HTTPStatus.OK)
