from __future__ import annotations

import uuid
from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.crm.api.serializers import _serialize_note
from apps.crm.api.shared import _json_error, _parse_json_body, _resolve_business, _validation_error_response
from apps.crm.domain.services import add_note, get_company, get_contact
from apps.crm.models import CrmCompany, CrmContact


@require_http_methods(["GET", "POST"])
def crm_contact_notes_collection(request: HttpRequest, contact_id: uuid.UUID) -> JsonResponse:
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
    try:
        contact = get_contact(business_profile=business, contact_id=contact_id)
    except CrmContact.DoesNotExist:
        return _json_error("NOT_FOUND", "Contact not found.", status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"items": [_serialize_note(item) for item in contact.notes.all()]}, status=HTTPStatus.OK)
    body = str((payload or {}).get("body") or "").strip()
    if not body:
        return _json_error("VALIDATION_ERROR", "body is required.", status=HTTPStatus.BAD_REQUEST, details={"body": ["This field is required."]})
    try:
        note = add_note(business_profile=business, actor=request.user, body=body, contact=contact)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"note": _serialize_note(note)}, status=HTTPStatus.CREATED)


@require_http_methods(["GET", "POST"])
def crm_company_notes_collection(request: HttpRequest, company_id: uuid.UUID) -> JsonResponse:
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
    try:
        company = get_company(business_profile=business, company_id=company_id)
    except CrmCompany.DoesNotExist:
        return _json_error("NOT_FOUND", "Company not found.", status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"items": [_serialize_note(item) for item in company.notes.all()]}, status=HTTPStatus.OK)
    body = str((payload or {}).get("body") or "").strip()
    if not body:
        return _json_error("VALIDATION_ERROR", "body is required.", status=HTTPStatus.BAD_REQUEST, details={"body": ["This field is required."]})
    try:
        note = add_note(business_profile=business, actor=request.user, body=body, company=company)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"note": _serialize_note(note)}, status=HTTPStatus.CREATED)
