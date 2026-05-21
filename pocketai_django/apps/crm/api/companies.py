from __future__ import annotations

import uuid
from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.crm.api.serializers import _serialize_company_detail, _serialize_company_summary
from apps.crm.api.shared import (
    _json_error,
    _parse_json_body,
    _parse_limit,
    _parse_uuid_value,
    _resolve_business,
    _validation_error_response,
)
from apps.crm.domain.services import (
    archive_company,
    create_company,
    delete_company,
    get_company,
    list_companies,
    merge_companies,
    restore_company,
    update_company,
)
from apps.crm.models import CrmCompany


@require_http_methods(["GET", "POST"])
def crm_companies_collection(request: HttpRequest) -> JsonResponse:
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
        result = list_companies(business_profile=business, search=request.GET.get("q", ""), status=request.GET.get("status", ""), limit=limit)
        return JsonResponse({"items": [_serialize_company_summary(item) for item in result.items], "total": result.total}, status=HTTPStatus.OK)
    try:
        company = create_company(business_profile=business, actor=request.user if request.user.is_authenticated else None, payload=payload or {})
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"company": _serialize_company_detail(get_company(business_profile=business, company_id=company.id))}, status=HTTPStatus.CREATED)


@require_http_methods(["GET", "PATCH", "DELETE"])
def crm_company_detail(request: HttpRequest, company_id: uuid.UUID) -> JsonResponse:
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
        company = get_company(business_profile=business, company_id=company_id)
    except CrmCompany.DoesNotExist:
        return _json_error("NOT_FOUND", "Company not found.", status=HTTPStatus.NOT_FOUND)
    if request.method == "GET":
        return JsonResponse({"company": _serialize_company_detail(company)}, status=HTTPStatus.OK)
    action = (payload or {}).get("action")
    if request.method == "DELETE":
        company = archive_company(business_profile=business, actor=request.user, company=company)
        return JsonResponse({"company": _serialize_company_detail(get_company(business_profile=business, company_id=company.id)), "deleted": False, "archived": True}, status=HTTPStatus.OK)
    try:
        if action == "archive":
            company = archive_company(business_profile=business, actor=request.user, company=company)
        elif action == "restore":
            company = restore_company(business_profile=business, actor=request.user, company=company)
        elif action == "hard_delete":
            delete_company(company=company, actor=request.user)
            return JsonResponse({"deleted": True, "hardDeleted": True}, status=HTTPStatus.OK)
        else:
            company = update_company(business_profile=business, actor=request.user, company=company, payload=payload or {})
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"company": _serialize_company_detail(get_company(business_profile=business, company_id=company.id))}, status=HTTPStatus.OK)


@require_http_methods(["POST"])
def crm_company_merge(request: HttpRequest, company_id: uuid.UUID) -> JsonResponse:
    payload, error = _parse_json_body(request)
    if error:
        return error
    business, error = _resolve_business(request, payload.get("businessId"), action="merge")
    if error:
        return error
    assert business is not None
    try:
        survivor = get_company(business_profile=business, company_id=company_id)
        merged = get_company(business_profile=business, company_id=_parse_uuid_value(payload.get("mergedId"), field_name="mergedId"))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except CrmCompany.DoesNotExist:
        return _json_error("NOT_FOUND", "Company not found.", status=HTTPStatus.NOT_FOUND)
    try:
        survivor = merge_companies(business_profile=business, actor=request.user, survivor=survivor, merged=merged)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"company": _serialize_company_detail(get_company(business_profile=business, company_id=survivor.id))}, status=HTTPStatus.OK)
