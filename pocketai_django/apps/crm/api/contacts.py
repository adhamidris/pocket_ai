from __future__ import annotations

import uuid
from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.crm.api.serializers import _serialize_contact_detail, _serialize_contact_summary
from apps.crm.api.shared import (
    _json_error,
    _parse_json_body,
    _parse_limit,
    _parse_uuid_value,
    _resolve_business,
    _validation_error_response,
)
from apps.crm.domain.services import (
    archive_contact,
    create_contact,
    delete_contact,
    get_contact,
    list_contacts,
    merge_contacts,
    restore_contact,
    update_contact,
)
from apps.crm.models import CrmContact


@require_http_methods(["GET", "POST"])
def crm_contacts_collection(request: HttpRequest) -> JsonResponse:
    payload = None
    if request.method == "POST":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "write")
    if error:
        return error
    assert business is not None
    if request.method == "GET":
        try:
            limit = _parse_limit(request.GET.get("limit"))
        except ValidationError as exc:
            return _validation_error_response(exc)
        result = list_contacts(business_profile=business, search=request.GET.get("q", ""), status=request.GET.get("status", ""), limit=limit)
        return JsonResponse({"items": [_serialize_contact_summary(item) for item in result.items], "total": result.total}, status=HTTPStatus.OK)
    try:
        contact = create_contact(business_profile=business, actor=request.user if request.user.is_authenticated else None, payload=payload or {})
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"contact": _serialize_contact_detail(get_contact(business_profile=business, contact_id=contact.id))}, status=HTTPStatus.CREATED)


@require_http_methods(["GET", "PATCH", "DELETE"])
def crm_contact_detail(request: HttpRequest, contact_id: uuid.UUID) -> JsonResponse:
    payload = None
    if request.method in {"PATCH", "DELETE"}:
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "write")
    if error:
        return error
    assert business is not None
    try:
        contact = get_contact(business_profile=business, contact_id=contact_id)
    except CrmContact.DoesNotExist:
        return _json_error("NOT_FOUND", "Contact not found.", status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"contact": _serialize_contact_detail(contact)}, status=HTTPStatus.OK)
    action = (payload or {}).get("action")
    if request.method == "DELETE":
        contact = archive_contact(business_profile=business, actor=request.user, contact=contact)
        return JsonResponse({"contact": _serialize_contact_detail(get_contact(business_profile=business, contact_id=contact.id)), "deleted": False, "archived": True}, status=HTTPStatus.OK)
    try:
        if action == "archive":
            contact = archive_contact(business_profile=business, actor=request.user, contact=contact)
        elif action == "restore":
            contact = restore_contact(business_profile=business, actor=request.user, contact=contact)
        elif action == "hard_delete":
            delete_contact(contact=contact, actor=request.user)
            return JsonResponse({"deleted": True, "hardDeleted": True}, status=HTTPStatus.OK)
        else:
            contact = update_contact(business_profile=business, actor=request.user, contact=contact, payload=payload or {})
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"contact": _serialize_contact_detail(get_contact(business_profile=business, contact_id=contact.id))}, status=HTTPStatus.OK)


@require_http_methods(["POST"])
def crm_contact_merge(request: HttpRequest, contact_id: uuid.UUID) -> JsonResponse:
    payload, error = _parse_json_body(request)
    if error:
        return error
    business, error = _resolve_business(request, payload.get("businessId"), action="merge")
    if error:
        return error
    assert business is not None
    try:
        survivor = get_contact(business_profile=business, contact_id=contact_id)
        merged = get_contact(business_profile=business, contact_id=_parse_uuid_value(payload.get("mergedId"), field_name="mergedId"))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except CrmContact.DoesNotExist:
        return _json_error("NOT_FOUND", "Contact not found.", status=HTTPStatus.NOT_FOUND)
    try:
        survivor = merge_contacts(business_profile=business, actor=request.user, survivor=survivor, merged=merged)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"contact": _serialize_contact_detail(get_contact(business_profile=business, contact_id=survivor.id))}, status=HTTPStatus.OK)
