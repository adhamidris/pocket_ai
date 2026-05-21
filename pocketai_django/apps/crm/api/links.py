from __future__ import annotations

import uuid
from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.crm.api.serializers import _serialize_contact_company_link
from apps.crm.api.shared import (
    _json_error,
    _parse_json_body,
    _parse_uuid_value,
    _resolve_business,
    _validation_error_response,
)
from apps.crm.domain.services import (
    get_company,
    get_contact,
    link_contact_to_company,
    unlink_contact_from_company,
    update_contact_company_link,
)
from apps.crm.models import CrmCompany, CrmContact, CrmContactCompanyLink


@require_http_methods(["GET", "POST"])
def crm_contact_company_links_collection(request: HttpRequest, contact_id: uuid.UUID) -> JsonResponse:
    payload = None
    if request.method == "POST":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "link_manage")
    if error:
        return error
    assert business is not None
    try:
        contact = get_contact(business_profile=business, contact_id=contact_id)
    except CrmContact.DoesNotExist:
        return _json_error("NOT_FOUND", "Contact not found.", status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"items": [_serialize_contact_company_link(item) for item in contact.company_links.all()]}, status=HTTPStatus.OK)
    try:
        company_id = _parse_uuid_value((payload or {}).get("companyId"), field_name="companyId")
        company = get_company(business_profile=business, company_id=company_id)
    except ValidationError as exc:
        return _validation_error_response(exc)
    except CrmCompany.DoesNotExist:
        return _json_error("NOT_FOUND", "Company not found.", status=HTTPStatus.NOT_FOUND)
    try:
        link = link_contact_to_company(business_profile=business, actor=request.user, contact=contact, company=company, payload=payload or {})
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"companyLink": _serialize_contact_company_link(link)}, status=HTTPStatus.CREATED)


@require_http_methods(["PATCH", "DELETE"])
def crm_contact_company_link_detail(request: HttpRequest, contact_id: uuid.UUID, company_id: uuid.UUID) -> JsonResponse:
    payload, error = _parse_json_body(request)
    if error:
        return error
    business, error = _resolve_business(request, payload.get("businessId"), action="link_manage")
    if error:
        return error
    assert business is not None
    try:
        contact = get_contact(business_profile=business, contact_id=contact_id)
        _company = get_company(business_profile=business, company_id=company_id)
        link = CrmContactCompanyLink.objects.get(business_profile=business, contact=contact, company_id=company_id)
    except (CrmContact.DoesNotExist, CrmCompany.DoesNotExist, CrmContactCompanyLink.DoesNotExist):
        return _json_error("NOT_FOUND", "Company link not found.", status=HTTPStatus.NOT_FOUND)
    try:
        if request.method == "DELETE":
            unlink_contact_from_company(business_profile=business, actor=request.user, link=link)
            return JsonResponse({"deleted": True}, status=HTTPStatus.OK)
        link = update_contact_company_link(business_profile=business, actor=request.user, link=link, payload=payload or {})
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"companyLink": _serialize_contact_company_link(link)}, status=HTTPStatus.OK)
