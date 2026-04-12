from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.accounts.models import BusinessProfile
from .flags import crm_v1_enabled
from .imports import enqueue_import_job, infer_default_mapping, save_import_template, store_import_source_file
from .models import (
    CrmCompany,
    CrmContact,
    CrmContactCompanyLink,
    CrmDuplicateSuggestion,
    CrmFieldDefinition,
    CrmImportJob,
    CrmImportSourceFile,
    CrmImportTemplate,
)
from .services import (
    add_note,
    archive_company,
    archive_contact,
    archive_field_definition,
    create_company,
    create_contact,
    delete_company,
    delete_contact,
    get_company,
    get_contact,
    get_duplicate_suggestion,
    get_field_definition,
    ignore_duplicate_suggestion,
    link_contact_to_company,
    list_companies,
    list_contacts,
    list_duplicate_suggestions,
    list_field_definitions,
    merge_duplicate_suggestion,
    merge_companies,
    merge_contacts,
    reopen_duplicate_suggestion,
    restore_company,
    restore_contact,
    restore_field_definition,
    unlink_contact_from_company,
    update_company,
    update_contact_company_link,
    update_contact,
    update_field_definition,
    upsert_field_definition,
)


def _json_error(error: str, message: str, *, status: HTTPStatus, details: dict[str, Any] | None = None) -> JsonResponse:
    payload: dict[str, Any] = {"error": error, "message": message}
    if details:
        payload["details"] = details
    return JsonResponse(payload, status=status)


def _parse_json_body(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, _json_error("INVALID_JSON", "Request body must be valid JSON.", status=HTTPStatus.BAD_REQUEST)
    if not isinstance(payload, dict):
        return None, _json_error("INVALID_JSON", "Request body must be a JSON object.", status=HTTPStatus.BAD_REQUEST)
    return payload, None


def _validation_error_response(exc: ValidationError) -> JsonResponse:
    details = exc.message_dict if hasattr(exc, "message_dict") else {"nonFieldErrors": exc.messages}
    return _json_error("VALIDATION_ERROR", "Validation failed.", status=HTTPStatus.BAD_REQUEST, details=details)


def _parse_limit(value: str | None, *, default: int = 50, maximum: int = 200) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"limit": "Limit must be an integer."}) from exc
    if parsed < 1 or parsed > maximum:
        raise ValidationError({"limit": f"Limit must be between 1 and {maximum}."})
    return parsed


def _parse_uuid_value(value: Any, *, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError({field_name: "Must be a valid UUID."}) from exc


def _require_crm_permission(request: HttpRequest, business: BusinessProfile, *, action: str) -> JsonResponse | None:
    if request.user.id != business.user_id:
        return _json_error(
            "PERMISSION_DENIED",
            f"You do not have permission to {action} for this CRM business.",
            status=HTTPStatus.FORBIDDEN,
        )
    return None


def _resolve_business(request: HttpRequest, business_id: str | None, *, action: str = "read") -> tuple[BusinessProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, _json_error("UNAUTHORIZED", "Login required.", status=HTTPStatus.UNAUTHORIZED)
    candidate = business_id or request.GET.get("business_id") or request.headers.get("X-Business-Id")
    business = None
    if candidate:
        try:
            business = BusinessProfile.objects.get(id=uuid.UUID(str(candidate)))
        except (ValueError, BusinessProfile.DoesNotExist):
            return None, _json_error("BUSINESS_NOT_FOUND", "Business profile not found.", status=HTTPStatus.NOT_FOUND)
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
    if business is None:
        return None, _json_error("BUSINESS_REQUIRED", "A business profile is required.", status=HTTPStatus.BAD_REQUEST)
    permission_error = _require_crm_permission(request, business, action=action)
    if permission_error:
        return None, permission_error
    if not crm_v1_enabled(business):
        return None, _json_error("FEATURE_DISABLED", "CRM is not enabled for this business.", status=HTTPStatus.NOT_FOUND)
    return business, None


def _serialize_owner(owner) -> dict[str, object] | None:
    if owner is None:
        return None
    return {
        "id": str(owner.id),
        "email": owner.email,
        "name": (f"{owner.first_name} {owner.last_name}".strip() or owner.email),
    }


def _serialize_note(note) -> dict[str, object]:
    return {
        "id": str(note.id),
        "body": note.body,
        "author": _serialize_owner(note.author),
        "createdAt": note.created_at.isoformat(),
        "updatedAt": note.updated_at.isoformat(),
    }


def _serialize_activity(activity) -> dict[str, object]:
    return {
        "id": str(activity.id),
        "type": activity.activity_type,
        "actorType": activity.actor_type,
        "actor": _serialize_owner(activity.actor_user),
        "summary": activity.summary,
        "detail": activity.detail,
        "occurredAt": activity.occurred_at.isoformat(),
    }


def _serialize_external_identity(identity) -> dict[str, object]:
    return {
        "id": str(identity.id),
        "recordType": identity.record_type,
        "sourceSystem": identity.source_system,
        "sourceAccountRef": identity.source_account_ref,
        "externalObjectType": identity.external_object_type,
        "externalId": identity.external_id,
        "externalLabel": identity.external_label,
        "lastSeenAt": identity.last_seen_at.isoformat(),
    }


def _serialize_custom_field_value(field_value) -> dict[str, object]:
    definition = field_value.field_definition
    return {
        "definitionId": str(definition.id),
        "key": definition.key,
        "label": definition.label,
        "fieldType": definition.field_type,
        "searchable": definition.searchable,
        "filterable": definition.filterable,
        "pii": definition.pii,
        "value": field_value.value_json,
    }


def _serialize_contact_company_link(link: CrmContactCompanyLink) -> dict[str, object]:
    return {
        "contactId": str(link.contact_id),
        "companyId": str(link.company_id),
        "companyName": link.company.name,
        "relationshipTitle": link.relationship_title,
        "isPrimary": link.is_primary,
        "startedAt": link.started_at.isoformat() if link.started_at else None,
        "endedAt": link.ended_at.isoformat() if link.ended_at else None,
        "metadata": link.metadata,
        "createdAt": link.created_at.isoformat(),
        "updatedAt": link.updated_at.isoformat(),
    }


def _serialize_company_contact_link(link: CrmContactCompanyLink) -> dict[str, object]:
    return {
        "contactId": str(link.contact_id),
        "contactName": link.contact.display_name,
        "relationshipTitle": link.relationship_title,
        "isPrimary": link.is_primary,
        "startedAt": link.started_at.isoformat() if link.started_at else None,
        "endedAt": link.ended_at.isoformat() if link.ended_at else None,
        "metadata": link.metadata,
        "createdAt": link.created_at.isoformat(),
        "updatedAt": link.updated_at.isoformat(),
    }


def _serialize_field_definition(definition: CrmFieldDefinition) -> dict[str, object]:
    return {
        "id": str(definition.id),
        "targetObject": definition.target_object,
        "key": definition.key,
        "label": definition.label,
        "fieldType": definition.field_type,
        "required": definition.required,
        "searchable": definition.searchable,
        "filterable": definition.filterable,
        "pii": definition.pii,
        "archived": definition.archived,
        "options": definition.options,
        "schema": definition.schema,
        "createdAt": definition.created_at.isoformat(),
        "updatedAt": definition.updated_at.isoformat(),
    }


def _serialize_duplicate_suggestion(item: CrmDuplicateSuggestion) -> dict[str, object]:
    return {
        "id": str(item.id),
        "recordType": item.record_type,
        "recordId": str(item.record_id) if item.record_id else None,
        "candidateRecordId": str(item.candidate_record_id),
        "sourceRowNumber": item.source_row_number,
        "incomingSnapshot": item.incoming_snapshot,
        "matchReasons": item.match_reasons,
        "status": item.status,
        "resolutionNote": item.resolution_note,
        "createdAt": item.created_at.isoformat(),
        "resolvedAt": item.resolved_at.isoformat() if item.resolved_at else None,
    }


def _serialize_contact_summary(contact: CrmContact) -> dict[str, object]:
    return {
        "id": str(contact.id),
        "publicId": str(contact.public_id),
        "displayName": contact.display_name,
        "firstName": contact.first_name,
        "lastName": contact.last_name,
        "primaryEmail": contact.primary_email,
        "primaryPhone": contact.primary_phone,
        "title": contact.title,
        "source": contact.source,
        "owner": _serialize_owner(contact.owner),
        "status": contact.status,
        "tags": list(contact.tags or []),
        "companyLinks": [{"companyId": str(link.company_id), "companyName": link.company.name, "isPrimary": link.is_primary} for link in getattr(contact, "company_links", []).all()] if hasattr(getattr(contact, "company_links", None), "all") else [],
        "createdAt": contact.created_at.isoformat(),
        "updatedAt": contact.updated_at.isoformat(),
        "archivedAt": contact.archived_at.isoformat() if contact.archived_at else None,
    }


def _serialize_contact_detail(contact: CrmContact) -> dict[str, object]:
    payload = _serialize_contact_summary(contact)
    payload.update(
        {
            "customFields": [_serialize_custom_field_value(item) for item in contact.field_values.all()],
            "externalIdentities": [_serialize_external_identity(item) for item in contact.external_identities.all()],
            "notes": [_serialize_note(item) for item in contact.notes.all()],
            "activities": [_serialize_activity(item) for item in contact.activities.all()],
            "metadata": contact.metadata,
        }
    )
    return payload


def _serialize_company_summary(company: CrmCompany) -> dict[str, object]:
    return {
        "id": str(company.id),
        "publicId": str(company.public_id),
        "name": company.name,
        "website": company.website,
        "websiteDomain": company.website_domain_normalized,
        "primaryPhone": company.primary_phone,
        "source": company.source,
        "owner": _serialize_owner(company.owner),
        "status": company.status,
        "tags": list(company.tags or []),
        "createdAt": company.created_at.isoformat(),
        "updatedAt": company.updated_at.isoformat(),
        "archivedAt": company.archived_at.isoformat() if company.archived_at else None,
    }


def _serialize_company_detail(company: CrmCompany) -> dict[str, object]:
    payload = _serialize_company_summary(company)
    payload.update(
        {
            "contactLinks": [_serialize_company_contact_link(item) for item in company.contact_links.all()],
            "customFields": [_serialize_custom_field_value(item) for item in company.field_values.all()],
            "externalIdentities": [_serialize_external_identity(item) for item in company.external_identities.all()],
            "notes": [_serialize_note(item) for item in company.notes.all()],
            "activities": [_serialize_activity(item) for item in company.activities.all()],
            "metadata": company.metadata,
        }
    )
    return payload


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
