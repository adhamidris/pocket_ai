from __future__ import annotations

import uuid
from http import HTTPStatus

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.crm.api.shared import (
    _json_error,
    _parse_json_body,
    _parse_uuid_value,
    _resolve_business,
    _validation_error_response,
)
from apps.crm.import_pipeline.service import (
    enqueue_import_job,
    infer_default_mapping,
    save_import_template,
    store_import_source_file,
)
from apps.crm.models import CrmImportJob, CrmImportSourceFile, CrmImportTemplate


@require_http_methods(["GET", "POST"])
def crm_import_templates_collection(request: HttpRequest) -> JsonResponse:
    payload = None
    if request.method == "POST":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "import")
    if error:
        return error
    assert business is not None
    if request.method == "GET":
        items = CrmImportTemplate.objects.filter(business_profile=business).order_by("name")
        return JsonResponse({"items": [{"id": str(item.id), "name": item.name, "mapping": item.mapping} for item in items]}, status=HTTPStatus.OK)
    template = save_import_template(business_profile=business, created_by=request.user, name=str(payload.get("name") or "").strip() or "Default mapping", mapping=payload.get("mapping") if isinstance(payload.get("mapping"), dict) else {})
    return JsonResponse({"template": {"id": str(template.id), "name": template.name}}, status=HTTPStatus.CREATED)


@require_http_methods(["POST"])
def crm_import_sources_collection(request: HttpRequest) -> JsonResponse:
    business, error = _resolve_business(request, request.POST.get("businessId") or request.GET.get("business_id"), action="import")
    if error:
        return error
    assert business is not None
    source_file = request.FILES.get("source_file")
    if source_file is None:
        return _json_error("VALIDATION_ERROR", "source_file is required.", status=HTTPStatus.BAD_REQUEST)
    try:
        source = store_import_source_file(business_profile=business, uploaded_by=request.user, uploaded_file=source_file)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse(
        {
            "sourceFile": {
                "id": str(source.id),
                "columns": source.column_snapshot,
                "sampleRows": source.sample_rows,
                "suggestedMapping": infer_default_mapping(source.column_snapshot),
            }
        },
        status=HTTPStatus.CREATED,
    )


@require_http_methods(["GET", "POST"])
def crm_import_jobs_collection(request: HttpRequest) -> JsonResponse:
    payload = None
    if request.method == "POST":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_id = payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business(request, business_id, action="read" if request.method == "GET" else "import")
    if error:
        return error
    assert business is not None
    if request.method == "GET":
        items = CrmImportJob.objects.filter(business_profile=business).order_by("-created_at")[:25]
        return JsonResponse({"items": [{"id": str(item.id), "status": item.status, "summary": item.summary} for item in items]}, status=HTTPStatus.OK)
    try:
        source = CrmImportSourceFile.objects.get(business_profile=business, id=_parse_uuid_value(payload.get("sourceFileId"), field_name="sourceFileId"))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except CrmImportSourceFile.DoesNotExist:
        return _json_error("NOT_FOUND", "Import source file not found.", status=HTTPStatus.NOT_FOUND)
    template = None
    if payload.get("templateId"):
        try:
            template = CrmImportTemplate.objects.filter(business_profile=business, id=_parse_uuid_value(payload["templateId"], field_name="templateId")).first()
        except ValidationError as exc:
            return _validation_error_response(exc)
    if "mapping" in payload and not isinstance(payload.get("mapping"), dict):
        return _json_error("VALIDATION_ERROR", "mapping must be a JSON object.", status=HTTPStatus.BAD_REQUEST, details={"mapping": ["Mapping must be a JSON object."]})
    try:
        job = enqueue_import_job(business_profile=business, source_file=source, initiated_by=request.user, mapping=payload.get("mapping") if isinstance(payload.get("mapping"), dict) else {}, template=template)
    except ValidationError as exc:
        return _validation_error_response(exc)
    return JsonResponse({"job": {"id": str(job.id), "status": job.status}}, status=HTTPStatus.CREATED)


@require_http_methods(["GET"])
def crm_import_job_detail(request: HttpRequest, job_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business(request, request.GET.get("business_id"), action="read")
    if error:
        return error
    assert business is not None
    try:
        job = CrmImportJob.objects.get(business_profile=business, id=job_id)
    except CrmImportJob.DoesNotExist:
        return _json_error("NOT_FOUND", "Import job not found.", status=HTTPStatus.NOT_FOUND)
    return JsonResponse({"job": {"id": str(job.id), "status": job.status, "summary": job.summary, "rows": [{"rowNumber": row.row_number, "status": row.status, "messages": row.messages} for row in job.row_results.order_by("row_number")[:100]]}}, status=HTTPStatus.OK)
