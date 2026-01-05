from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import uuid
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import login as auth_login
from django.core.exceptions import ImproperlyConfigured
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    IntegrationSyncFrequency,
    IntegrationCredentialEventType,
    KnowledgeAuditAction,
    KnowledgeAuditEvent,
    KnowledgeCollection,
    KnowledgeIntegration,
    KnowledgeIntegrationStatus,
    KnowledgeIntegrationType,
    KnowledgeSourceType,
    KnowledgeUpload,
    KnowledgeVisibility,
    _normalize_identifier_token,
)
from apps.conversations.models import IdentifierEvent
from apps.cases.models import Case, CaseMessage, CasePriority, CaseStatus
from apps.customers.models import Customer, CustomerNoteAuthor
from apps.accounts.action_controls import list_action_settings, set_action_setting
from apps.accounts.agents import (
    AgentListValidationError,
    agent_identifier,
    display_role_label,
    display_tone_label,
    get_agent_detail,
    list_agents,
)
from apps.cases.services import (
    CaseDetail,
    CaseListResult,
    CaseSummary,
    CaseServiceError,
    add_case_message,
    add_case_note,
    add_history_entry,
    create_case,
    get_case_detail,
    list_cases,
    update_case,
)
from apps.customers.services import CustomerDetail as CustomerDetailData, CustomerSummary, get_customer_detail
from apps.knowledge.documents import (
    CsvPreviewError,
    delete_document as delete_knowledge_document,
    DocumentDetail,
    DocumentListItem,
    DocumentListValidationError as KnowledgeDocumentListValidationError,
    DocumentScrapeError,
    get_document_detail as get_knowledge_document_detail,
    list_documents as list_knowledge_documents,
    preview_csv_upload,
    scrape_document_source,
)
from apps.knowledge.collections import (
    KnowledgeCollectionValidationError,
    create_knowledge_collection,
    delete_knowledge_collection,
    list_knowledge_collections,
    set_upload_collections,
    update_knowledge_collection,
)
from apps.integrations.integration_sync import IntegrationSyncError, IntegrationSyncService
from apps.integrations.google_drive import (
    GoogleOAuthError,
    GoogleSheetsDiscoveryError,
    build_google_authorization_url,
    discover_google_sheet_resources,
    exchange_google_authorization_code,
    fetch_google_account_profile,
)
from apps.accounts.registration import (
    AgentProfileError,
    AgentProfileResult,
    BusinessProfileError,
    BusinessProfileResult,
    EmailAlreadyRegistered,
    KnowledgeUploadError,
    KnowledgeUploadResult,
    RegistrationError,
    RegistrationResult,
    configure_agent_profile,
    finalize_knowledge_uploads,
    start_registration as start_registration_service,
    upsert_business_profile,
)
from apps.mcp.identifier_eval import IdentifierEvalCase, IdentifierEvalHarness
from apps.mcp.identifier_registry import IdentifierRegistryError, IdentifierRegistryService

logger = logging.getLogger(__name__)


def placeholder(_request):
    """Placeholder endpoint to be fleshed out in backend migration."""
    return JsonResponse({"status": "ok", "message": "API scaffold ready"}, status=200)


@csrf_protect
@require_http_methods(["POST"])
def start_registration(request: HttpRequest) -> JsonResponse:
    """Handle the first step of the registration wizard."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    first_name = str(payload.get("firstName") or "").strip()
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    confirm_password = str(payload.get("confirmPassword") or "")

    errors: dict[str, str] = {}
    if len(first_name) < 2:
        errors["firstName"] = "First name must be at least 2 characters."
    if not email or "@" not in email:
        errors["email"] = "Enter a valid email address."
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    if password != confirm_password:
        errors["confirmPassword"] = "Passwords must match."

    if errors:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Fix the highlighted fields.", "errors": errors},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        result: RegistrationResult = start_registration_service(
            first_name=first_name,
            email=email,
            password=password,
        )
    except EmailAlreadyRegistered:
        return JsonResponse(
            {
                "error": "EMAIL_REGISTERED",
                "message": "An account with this email already exists.",
                "field": "email",
            },
            status=HTTPStatus.CONFLICT,
        )
    except RegistrationError as exc:
        return JsonResponse(
            {"error": "REGISTRATION_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to start registration.")
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to start registration right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    session = result.session
    user = result.user

    # Log the user in immediately so they can resume registration after page refresh
    backend_path = (
        settings.AUTHENTICATION_BACKENDS[0]
        if settings.AUTHENTICATION_BACKENDS
        else "django.contrib.auth.backends.ModelBackend"
    )
    auth_login(request, user, backend=backend_path)
    request.session["registration_session_id"] = str(session.id)
    request.session["auth_entrypoint"] = "registration"

    response = {
        "registrationId": str(session.id),
        "user": {
            "id": str(user.public_id),
            "email": user.email,
            "firstName": user.first_name,
        },
        "nextStep": "business",
        "session": {
            "id": str(session.id),
            "currentStep": session.current_step,
            "stepsCompleted": session.steps_completed,
            "totalSteps": session.total_steps,
        },
    }
    return JsonResponse(response, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["PUT"])
def update_business_profile(request: HttpRequest, session_id: str) -> JsonResponse:
    """Persist the business profile for the given registration session."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    business_name = str(payload.get("businessName") or "").strip()
    industry = str(payload.get("industry") or "").strip()
    industry_key = str(payload.get("industryKey") or "").strip()
    line_of_business = payload.get("lineOfBusiness") or []
    line_of_business_custom = payload.get("lineOfBusinessCustom") or []
    country = str(payload.get("country") or "").strip()
    website = str(payload.get("website") or "").strip()

    if business_name and len(business_name) > 255:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Business name is too long."},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        result: BusinessProfileResult = upsert_business_profile(
            session_id=session_id,
            name=business_name,
            industry=industry,
            industry_key=industry_key,
            line_of_business=line_of_business,
            line_of_business_custom=line_of_business_custom,
            country=country,
            website=website,
        )
    except BusinessProfileError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to store business profile.")
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to save the business profile right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    profile = result.profile
    session = result.session

    response = {
        "business": {
            "id": str(profile.id),
            "name": profile.name,
            "industry": profile.industry,
            "industryKey": profile.industry_key,
            "lineOfBusiness": profile.line_of_business,
            "lineOfBusinessCustom": profile.line_of_business_custom,
            "country": profile.country,
            "website": profile.website,
            "status": profile.status,
        },
        "session": {
            "id": str(session.id),
            "currentStep": session.current_step,
            "stepsCompleted": session.steps_completed,
            "totalSteps": session.total_steps,
        },
        "nextStep": "agent",
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def identifier_registry(request: HttpRequest, business_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error

    if request.method == "GET":
        registry = IdentifierRegistryService.list_registry(business_profile=business)
        match_policy = IdentifierRegistryService.get_match_policy(business)
        return JsonResponse({"items": registry, "match_policy": match_policy}, status=HTTPStatus.OK)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    key = str(payload.get("key") or payload.get("identifier") or "").strip()
    display_name = str(payload.get("display_name") or payload.get("displayName") or key).strip()
    status_value = (payload.get("status") or "").strip() or None
    source_value = (payload.get("source") or "").strip() or None
    description = str(payload.get("description") or "").strip()
    is_required_raw = payload.get("is_required", payload.get("isRequired", True))
    if isinstance(is_required_raw, str):
        is_required = is_required_raw.strip().lower() not in {"false", "0", "no", "off"}
    else:
        is_required = bool(is_required_raw)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    match_policy_raw = payload.get("match_policy") or payload.get("matchPolicy")
    if match_policy_raw:
        IdentifierRegistryService.set_match_policy(business_profile=business, policy=match_policy_raw)
    columns = payload.get("columns") if isinstance(payload.get("columns"), list) else None

    if not key:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "key is required."},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        schema = IdentifierRegistryService.create_schema(
            business_profile=business,
            key=key,
            display_name=display_name or key,
            source=source_value,
            status=status_value,
            is_required=is_required,
            description=description,
            metadata=metadata,
            columns=columns,
        )
    except IdentifierRegistryError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("identifier_registry.create failed business=%s", business.id)
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to save identifier schema right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    logger.info(
        "identifier_registry.create business=%s key=%s source=%s status=%s",
        business.id,
        key,
        source_value or "user",
        status_value or "proposed",
    )
    return JsonResponse(IdentifierRegistryService.serialize_schema(schema), status=HTTPStatus.CREATED)


@csrf_exempt
@require_http_methods(["POST"])
def identifier_schema_approve(request: HttpRequest, business_id: uuid.UUID, schema_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error
    schema = IdentifierRegistryService.get_schema(business_profile=business, schema_id=schema_id)
    if not schema:
        return JsonResponse(
            {"error": "NOT_FOUND", "message": "Identifier schema not found for this business."},
            status=HTTPStatus.NOT_FOUND,
        )
    try:
        schema = IdentifierRegistryService.approve_schema(schema)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("identifier_registry.approve failed business=%s schema=%s", business.id, schema_id)
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to approve identifier schema right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return JsonResponse(IdentifierRegistryService.serialize_schema(schema), status=HTTPStatus.OK)


@csrf_exempt
@require_http_methods(["POST"])
def identifier_schema_columns(request: HttpRequest, business_id: uuid.UUID, schema_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error
    schema = IdentifierRegistryService.get_schema(business_profile=business, schema_id=schema_id)
    if not schema:
        return JsonResponse(
            {"error": "NOT_FOUND", "message": "Identifier schema not found for this business."},
            status=HTTPStatus.NOT_FOUND,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )
    columns = payload.get("columns") or payload.get("items")
    if not isinstance(columns, list):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "columns must be a list of column definitions."},
            status=HTTPStatus.BAD_REQUEST,
        )
    try:
        schema = IdentifierRegistryService.add_columns(schema=schema, columns=columns)
    except IdentifierRegistryError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("identifier_registry.add_columns failed business=%s schema=%s", business.id, schema_id)
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to save identifier columns right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return JsonResponse(IdentifierRegistryService.serialize_schema(schema), status=HTTPStatus.OK)


@csrf_exempt
@require_http_methods(["POST"])
def identifier_propose_headers(request: HttpRequest, business_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    headers = payload.get("headers") or payload.get("columns")
    upload_id = payload.get("upload_id") or payload.get("uploadId")
    auto_promote = bool(payload.get("auto_promote", payload.get("autoPromote", False)))
    model_assist = bool(payload.get("model_assist", payload.get("modelAssist", False)))
    match_policy_raw = payload.get("match_policy") or payload.get("matchPolicy")

    upload_obj = None
    if upload_id:
        try:
            upload_uuid = upload_id if isinstance(upload_id, uuid.UUID) else uuid.UUID(str(upload_id))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "upload_id must be a valid UUID."},
                status=HTTPStatus.BAD_REQUEST,
            )
        upload_obj = KnowledgeUpload.objects.filter(id=upload_uuid, business_profile=business).first()
        if not upload_obj:
            return JsonResponse(
                {"error": "NOT_FOUND", "message": "Upload not found for this business."},
                status=HTTPStatus.NOT_FOUND,
            )
    try:
        schemas = IdentifierRegistryService.propose_from_headers(
            business_profile=business,
            headers=headers if isinstance(headers, list) else None,
            upload=upload_obj,
            auto_promote=auto_promote,
            match_policy=match_policy_raw,
            model_assist=model_assist,
        )
    except IdentifierRegistryError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("identifier_registry.propose failed business=%s", business.id)
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to propose identifiers right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    match_policy = IdentifierRegistryService.get_match_policy(business)
    return JsonResponse(
        {
            "items": [IdentifierRegistryService.serialize_schema(schema) for schema in schemas],
            "match_policy": match_policy,
        },
        status=HTTPStatus.OK,
    )


@require_http_methods(["GET"])
def identifier_guardrails_overview(request: HttpRequest, business_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error

    registry = IdentifierRegistryService.list_registry(business_profile=business)
    match_policy = IdentifierRegistryService.get_match_policy(business)

    uploads_qs = (
        KnowledgeUpload.objects.filter(business_profile=business)
        .select_related("integration")
        .order_by("-updated_at")[:120]
    )
    uploads = [
        {
            "id": str(upload.id),
            "display_name": upload.display_name,
            "source_type": upload.source_type,
            "status": upload.status,
            "integration_name": upload.integration.name if upload.integration else "",
            "last_ingested_at": upload.last_ingested_at.isoformat() if upload.last_ingested_at else None,
            "updated_at": upload.updated_at.isoformat() if upload.updated_at else None,
            "chunk_count": upload.chunk_count,
            "token_count": upload.token_count,
        }
        for upload in uploads_qs
    ]

    events_qs = IdentifierEvent.objects.filter(business_profile=business).order_by("-created_at")
    events = list(
        events_qs[:200].values(
            "id",
            "status",
            "tool",
            "match_policy",
            "upload_id",
            "required_keys",
            "provided_keys",
            "provided_hashes",
            "blocked_uploads",
            "missing_by_upload",
            "created_at",
        )
    )
    summary = {
        "total": events_qs.count(),
        "required": events_qs.filter(status="identifier_required").count(),
        "ok": events_qs.filter(status="ok").count(),
    }

    payload = {
        "match_policy": match_policy,
        "registry": registry,
        "uploads": uploads,
        "events": {
            "items": events,
            "summary": summary,
        },
    }
    return JsonResponse(payload, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def identifier_events(request: HttpRequest, business_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error
    status_filter = (request.GET.get("status") or "").strip().lower() or None
    qs = IdentifierEvent.objects.filter(business_profile=business)
    if status_filter:
        qs = qs.filter(status=status_filter)
    qs = qs.order_by("-created_at")[:200]
    events = list(qs.values("id", "status", "tool", "match_policy", "upload_id", "required_keys", "provided_keys", "provided_hashes", "blocked_uploads", "missing_by_upload", "created_at"))
    summary = {
        "total": qs.count(),
        "required": qs.filter(status="identifier_required").count(),
        "ok": qs.filter(status="ok").count(),
    }
    return JsonResponse({"items": events, "summary": summary}, status=HTTPStatus.OK, safe=False)


@csrf_exempt
@require_http_methods(["POST"])
def identifier_eval(request: HttpRequest, business_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    upload_id_raw = payload.get("upload_id") or payload.get("uploadId")
    provided_identifiers = (
        payload.get("identifiers")
        or payload.get("provided_identifiers")
        or payload.get("providedIdentifiers")
        or {}
    )
    match_policy_raw = payload.get("match_policy") or payload.get("matchPolicy")
    expected_status_raw = payload.get("expected_status") or payload.get("expectedStatus") or "ok"

    if not upload_id_raw:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "upload_id is required."},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        upload_uuid = upload_id_raw if isinstance(upload_id_raw, uuid.UUID) else uuid.UUID(str(upload_id_raw))
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "upload_id must be a valid UUID."},
            status=HTTPStatus.BAD_REQUEST,
        )

    upload = KnowledgeUpload.objects.filter(id=upload_uuid, business_profile=business).first()
    if not upload:
        return JsonResponse(
            {"error": "NOT_FOUND", "message": "Upload not found for this business."},
            status=HTTPStatus.NOT_FOUND,
        )

    if provided_identifiers is None:
        provided_identifiers = {}
    if not isinstance(provided_identifiers, dict):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "identifiers must be an object of key/value pairs."},
            status=HTTPStatus.BAD_REQUEST,
        )

    expected_status = str(expected_status_raw or "ok").strip().lower() or "ok"
    if expected_status not in {"ok", "identifier_required"}:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "expected_status must be ok or identifier_required."},
            status=HTTPStatus.BAD_REQUEST,
        )

    normalized_policy = (
        IdentifierRegistryService.normalize_match_policy(match_policy_raw) if match_policy_raw else None
    )

    case = IdentifierEvalCase(
        name="inline",
        upload_id=upload.id,
        provided_identifiers=provided_identifiers,
        expected_status=expected_status,
        match_policy=normalized_policy,
        notes="dashboard-eval",
    )
    harness = IdentifierEvalHarness(business_profile=business, enforce=False)
    try:
        result = harness.run([case])[0]
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("identifier_eval failed business=%s upload=%s", business.id, upload.id)
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to evaluate identifier gating right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    response = {
        "upload_id": str(upload.id),
        "status": result.status,
        "expected_status": expected_status,
        "match_policy": result.match_policy,
        "required_keys": result.required_keys,
        "provided_keys": result.provided_keys,
        "passed": result.passed,
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@csrf_exempt
@require_http_methods(["POST"])
def identifier_schema_reject(request: HttpRequest, business_id: uuid.UUID, schema_id: uuid.UUID) -> JsonResponse:
    business, error = _resolve_business_profile(request, str(business_id))
    if error:
        return error
    schema = IdentifierRegistryService.get_schema(business_profile=business, schema_id=schema_id)
    if not schema:
        return JsonResponse(
            {"error": "NOT_FOUND", "message": "Identifier schema not found for this business."},
            status=HTTPStatus.NOT_FOUND,
        )
    try:
        schema = IdentifierRegistryService.reject_schema(schema)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("identifier_registry.reject failed business=%s schema=%s", business.id, schema_id)
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to reject identifier schema right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return JsonResponse(IdentifierRegistryService.serialize_schema(schema), status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["PUT"])
def configure_agent(request: HttpRequest, business_id: str) -> JsonResponse:
    """Persist the agent configuration for the given business profile."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    agent_name = str(payload.get("agentName") or "").strip()
    agent_role = str(payload.get("agentTitle") or "").strip()
    agent_tone = str(payload.get("agentTone") or "").strip()
    agent_traits = payload.get("agentTraits") or []
    agent_escalation = str(payload.get("agentEscalation") or "").strip()

    if agent_traits and not isinstance(agent_traits, list):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Traits must be a list of strings."},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        result: AgentProfileResult = configure_agent_profile(
            business_id=business_id,
            name=agent_name,
            role=agent_role,
            tone=agent_tone,
            traits=agent_traits,
            escalation_rule=agent_escalation,
        )
    except AgentProfileError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to configure agent profile.")
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to save the agent profile right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    profile = result.profile
    session = result.session

    response = {
        "agent": {
            "id": str(profile.id),
            "name": profile.name,
            "role": profile.role,
            "tone": profile.tone,
            "traits": profile.traits,
            "escalationRule": profile.escalation_rule,
            "status": profile.status,
        },
        "session": {
            "id": str(session.id),
            "currentStep": session.current_step,
            "stepsCompleted": session.steps_completed,
            "totalSteps": session.total_steps,
        },
        "nextStep": "uploads",
    }
    return JsonResponse(response, status=HTTPStatus.OK)


def _resolve_business_profile(request: HttpRequest, business_id: str | None) -> tuple[BusinessProfile | None, JsonResponse | None]:
    """
    Determine the business profile for the request either from parameter or the authenticated user.
    """

    header_business_id = request.headers.get("X-Business-Id") or request.META.get("HTTP_X_BUSINESS_ID")
    candidate = business_id or header_business_id

    if candidate:
        try:
            candidate_uuid = candidate if isinstance(candidate, uuid.UUID) else uuid.UUID(str(candidate))
        except (TypeError, ValueError):
            return None, JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "business_id must be a valid UUID."},
                status=HTTPStatus.BAD_REQUEST,
            )
        try:
            return BusinessProfile.objects.get(id=candidate_uuid), None
        except BusinessProfile.DoesNotExist:
            return None, JsonResponse(
                {"error": "BUSINESS_NOT_FOUND", "message": "Business profile not found."},
                status=HTTPStatus.NOT_FOUND,
            )

    if request.user.is_authenticated:
        business = request.user.business_profiles.order_by("-created_at").first()
        if business:
            return business, None

    return None, JsonResponse(
        {"error": "BUSINESS_REQUIRED", "message": "A business_id is required to perform this action."},
        status=HTTPStatus.BAD_REQUEST,
    )


def _resolve_owned_business_profile(
    request: HttpRequest,
    business_id: str | None,
) -> tuple[BusinessProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse(
            {"error": "UNAUTHORIZED", "message": "Login required."},
            status=HTTPStatus.UNAUTHORIZED,
        )

    business, error = _resolve_business_profile(request, business_id)
    if error:
        return None, error
    assert business is not None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, JsonResponse(
            {"error": "FORBIDDEN", "message": "You do not have access to this business profile."},
            status=HTTPStatus.FORBIDDEN,
        )
    return business, None


def _visibility_label(value: str | None) -> str:
    return (value or "").replace("_", " ").title() or "Private"


def _serialize_collection(collection: KnowledgeCollection, *, documents: int | None = None) -> dict[str, object]:
    return {
        "id": str(collection.id),
        "name": collection.name,
        "slug": collection.slug,
        "description": collection.description or "",
        "visibility": collection.visibility,
        "visibilityLabel": _visibility_label(collection.visibility),
        "documents": int(documents) if documents is not None else None,
        "updatedAt": _iso(getattr(collection, "updated_at", None)),
    }


def _get_google_integration(business: BusinessProfile, integration_id: uuid.UUID | None = None) -> KnowledgeIntegration | None:
    qs = KnowledgeIntegration.objects.filter(
        business_profile=business,
        integration_type=KnowledgeIntegrationType.GOOGLE_DRIVE,
    ).order_by("-created_at")
    if integration_id:
        qs = qs.filter(id=integration_id)
    return qs.first()


def _resolve_integration_state(state_value: str) -> tuple[KnowledgeIntegration | None, str | None]:
    if not state_value or ":" not in state_value:
        return None, None
    integration_raw, token = state_value.split(":", 1)
    try:
        integration_id = uuid.UUID(integration_raw)
    except (TypeError, ValueError):
        return None, None
    integration = KnowledgeIntegration.objects.filter(id=integration_id).first()
    return integration, token


def _validate_oauth_state(integration: KnowledgeIntegration, nonce: str) -> bool:
    metadata = integration.metadata if isinstance(integration.metadata, dict) else {}
    oauth_state = metadata.get("oauth_state") or {}
    return nonce and oauth_state.get("token") == nonce


def _mark_integration_error(integration: KnowledgeIntegration, message: str) -> None:
    metadata = dict(integration.metadata or {})
    metadata.pop("oauth_state", None)
    integration.metadata = metadata
    integration.status = KnowledgeIntegrationStatus.ERROR
    integration.sync_error = message
    integration.save(update_fields=["metadata", "status", "sync_error", "updated_at"])
    logger.warning(
        "knowledge_integration_error integration=%s business=%s error=%s",
        integration.id,
        integration.business_profile_id,
        message,
    )


def _integration_redirect(provider: str, status: str, message: str | None = None) -> HttpResponseRedirect:
    base = getattr(settings, "INTEGRATIONS_DASHBOARD_URL", "/") or "/"
    params = {"provider": provider, "status": status}
    if message:
        params["message"] = message
    separator = "?"
    if "?" in base:
        if base.endswith("?") or base.endswith("&"):
            separator = ""
        else:
            separator = "&"
    return HttpResponseRedirect(f"{base}{separator}{urlencode(params)}")


def _parse_uuid_param(value: str | None, field: str) -> tuple[uuid.UUID | None, JsonResponse | None]:
    if not value:
        return None, None
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None, JsonResponse(
            {"error": "VALIDATION_ERROR", "message": f"{field} must be a valid UUID."},
            status=HTTPStatus.BAD_REQUEST,
        )
    return parsed, None


def _serialize_resource_config(resource: dict[str, Any]) -> dict[str, Any]:
    column_privacy = resource.get("column_privacy") or {}
    return {
        "resourceId": resource.get("resource_id"),
        "driveFileId": resource.get("drive_file_id"),
        "driveFileName": resource.get("drive_file_name"),
        "sheetGid": resource.get("sheet_gid"),
        "sheetName": resource.get("sheet_name"),
        "visibility": resource.get("visibility"),
        "syncFrequency": resource.get("sync_frequency"),
        "columnPrivacy": {
            "sharedColumns": column_privacy.get("shared_columns") or [],
            "internalOnlyColumns": column_privacy.get("internal_only_columns") or [],
            "excludedColumns": column_privacy.get("excluded_columns") or [],
        },
        "lastSyncedAt": resource.get("last_synced_at"),
        "lastSyncStatus": resource.get("last_sync_status"),
        "lastSyncError": resource.get("last_sync_error"),
    }


def _normalize_integration_resources(
    resources: Any,
    *,
    default_visibility: str,
    default_sync_frequency: str,
    privacy_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not resources:
        return []
    if not isinstance(resources, list):
        raise ValueError("resources must be a list")
    valid_visibilities = {choice for choice, _ in KnowledgeVisibility.choices}
    valid_frequencies = {choice for choice, _ in IntegrationSyncFrequency.choices}
    normalized: list[dict[str, Any]] = []

    def _coerce_list(value: Any) -> list[str]:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            return [part for part in parts if part]
        if isinstance(value, (list, tuple)):
            return [str(part).strip() for part in value if str(part).strip()]
        return []

    policy = privacy_policy or {}
    require_masking = bool(policy.get("masking_required"))
    required_lookup: dict[str, str] = {}

    def _canonical_column(value: str | None) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split()).strip().lower()

    for raw in policy.get("required_columns") or []:
        canonical = _canonical_column(raw)
        if canonical:
            required_lookup[canonical] = raw

    for idx, resource in enumerate(resources, start=1):
        if not isinstance(resource, dict):
            raise ValueError(f"Resource #{idx} must be an object.")
        drive_file_id = str(
            resource.get("driveFileId")
            or resource.get("drive_file_id")
            or resource.get("fileId")
            or ""
        ).strip()
        sheet_gid = str(resource.get("sheetGid") or resource.get("sheet_gid") or resource.get("gid") or "").strip()
        sheet_name = str(resource.get("sheetName") or resource.get("sheet_name") or "").strip()
        drive_file_name = str(resource.get("driveFileName") or resource.get("drive_file_name") or drive_file_id).strip() or drive_file_id
        if not drive_file_id or not sheet_gid or not sheet_name:
            raise ValueError("Each resource must include driveFileId, sheetGid, and sheetName.")
        resource_id = str(resource.get("resourceId") or resource.get("resource_id") or f"{drive_file_id}:{sheet_gid}").strip()
        sync_frequency = str(resource.get("syncFrequency") or resource.get("sync_frequency") or default_sync_frequency)
        if sync_frequency not in valid_frequencies:
            raise ValueError(f"Resource #{idx} has invalid sync frequency '{sync_frequency}'.")
        visibility = str(resource.get("visibility") or default_visibility)
        if visibility not in valid_visibilities:
            raise ValueError(f"Resource #{idx} has invalid visibility '{visibility}'.")
        column_privacy = resource.get("columnPrivacy") or resource.get("column_privacy") or {}
        if not isinstance(column_privacy, dict):
            column_privacy = {}
        shared_columns = _coerce_list(column_privacy.get("sharedColumns") or column_privacy.get("shared_columns"))
        internal_columns = _coerce_list(column_privacy.get("internalOnlyColumns") or column_privacy.get("internal_only_columns"))
        excluded_columns = _coerce_list(column_privacy.get("excludedColumns") or column_privacy.get("excluded_columns"))
        masked_canonical = {_canonical_column(value) for value in [*internal_columns, *excluded_columns] if _canonical_column(value)}
        if require_masking and not masked_canonical:
            raise ValueError(
                f"Resource #{idx} must mark at least one column as internalOnlyColumns or excludedColumns before saving."
            )
        if required_lookup:
            missing = [required_lookup[key] for key in required_lookup.keys() if key not in masked_canonical]
            if missing:
                missing_csv = ", ".join(sorted(missing))
                raise ValueError(
                    f"Resource #{idx} is missing masking for required columns: {missing_csv}."
                )
        normalized.append(
            {
                "resource_id": resource_id,
                "drive_file_id": drive_file_id,
                "drive_file_name": drive_file_name,
                "sheet_gid": sheet_gid,
                "sheet_name": sheet_name,
                "sync_frequency": sync_frequency,
                "visibility": visibility,
                "column_privacy": {
                    "shared_columns": shared_columns,
                    "internal_only_columns": internal_columns,
                    "excluded_columns": excluded_columns,
                },
                "metadata": resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {},
            }
        )
    return normalized


def _integration_provider_catalog() -> list[dict[str, Any]]:
    return [
        {
            "type": KnowledgeIntegrationType.GOOGLE_DRIVE,
            "label": "Google Drive",
            "description": "Connect Sheets via OAuth and keep tables in sync.",
            "status": "available",
            "connectUrl": reverse("api:integrations-google-start"),
            "requiresOAuth": True,
            "supportsSheets": True,
        },
        {
            "type": "excel_online",
            "label": "Excel Online",
            "description": "OneDrive-hosted spreadsheets (coming soon).",
            "status": "coming_soon",
            "connectUrl": "",
            "requiresOAuth": True,
            "supportsSheets": True,
        },
    ]


def _integration_stats(integrations: list[KnowledgeIntegration]) -> dict[str, int]:
    total = len(integrations)
    connected = sum(1 for integration in integrations if integration.status == KnowledgeIntegrationStatus.CONNECTED)
    errors = sum(1 for integration in integrations if integration.status == KnowledgeIntegrationStatus.ERROR)
    syncing = sum(1 for integration in integrations if integration.status == KnowledgeIntegrationStatus.SYNCING)
    return {
        "total": total,
        "connected": connected,
        "errors": errors,
        "syncing": syncing,
    }


def _integration_actions(integration: KnowledgeIntegration) -> dict[str, str]:
    actions = {
        "sheets": reverse("api:integrations-sheets", args=[integration.id]),
    }
    if integration.integration_type == KnowledgeIntegrationType.GOOGLE_DRIVE:
        actions["sync"] = reverse("api:integrations-google-sync")
    return actions


def _serialize_integration_summary(integration: KnowledgeIntegration) -> dict[str, Any]:
    metadata = integration.metadata or {}
    google_account = metadata.get("google_account") if isinstance(metadata, dict) else {}
    sync_stats = metadata.get("sync_stats") if isinstance(metadata, dict) else {}
    schedule = integration.get_sync_schedule()
    stale_resources = [
        {
            "resourceId": resource.get("resource_id"),
            "driveFileName": resource.get("drive_file_name"),
            "sheetName": resource.get("sheet_name"),
            "staleSince": resource.get("stale_since"),
            "reason": resource.get("stale_reason"),
        }
        for resource in integration.resource_configs
        if resource.get("stale_since")
    ]
    return {
        "id": str(integration.id),
        "name": integration.name,
        "type": integration.integration_type,
        "status": integration.status,
        "lastSyncedAt": _iso(integration.last_synced_at),
        "resourceCount": len(integration.resource_configs or []),
        "syncError": integration.sync_error,
        "defaultVisibility": integration.get_default_visibility(),
        "defaultSyncFrequency": integration.get_default_sync_frequency(),
        "nextSyncAt": schedule.get("next_run_at"),
        "schedule": {
            "frequency": schedule.get("frequency"),
            "nextRunAt": schedule.get("next_run_at"),
            "lastRunAt": schedule.get("last_run_at"),
            "status": schedule.get("last_status"),
            "paused": schedule.get("paused"),
        },
        "staleResourceCount": len(stale_resources),
        "staleResources": stale_resources[:5],
        "metrics": {
            "resourcesAttempted": (sync_stats or {}).get("resources_attempted"),
            "successCount": (sync_stats or {}).get("success_count"),
            "failureCount": (sync_stats or {}).get("failure_count"),
            "bytesWritten": (sync_stats or {}).get("bytes_written"),
            "rowsIngested": (sync_stats or {}).get("rows_ingested"),
            "durationMs": (sync_stats or {}).get("duration_ms"),
            "lastRunAt": (sync_stats or {}).get("last_run_at"),
        },
        "hasCredentials": integration.has_credentials(),
        "actions": _integration_actions(integration),
        "account": {
            "email": (google_account or {}).get("email"),
            "name": (google_account or {}).get("name"),
            "linkedAt": (google_account or {}).get("linked_at"),
        },
    }


def _serve_document_file(file_detail, *, download: bool) -> FileResponse:
    media_root = Path(getattr(settings, "MEDIA_ROOT", ""))
    if not media_root:
        raise PermissionError("MEDIA_ROOT is not configured.")

    media_root = media_root.resolve()
    storage_path = Path(file_detail.storage_path)
    absolute = (media_root / storage_path).resolve()
    try:
        absolute.relative_to(media_root)
    except ValueError as exc:
        raise PermissionError("Invalid storage path.") from exc

    if not absolute.exists():
        raise FileNotFoundError(file_detail.storage_path)

    filename = file_detail.filename or absolute.name
    content_type = file_detail.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = FileResponse(
        absolute.open("rb"),
        as_attachment=download,
        filename=filename,
    )
    response["Content-Type"] = content_type
    return response


def _iso(dt):
    return dt.isoformat() if dt else None


def _serialize_case_summary(summary: CaseSummary) -> dict:
    return {
        "id": str(summary.id),
        "caseNumber": summary.case_number,
        "title": summary.title,
        "description": summary.description,
        "priority": summary.priority,
        "status": summary.status,
        "customer": {
            "id": str(summary.customer_id) if summary.customer_id else None,
            "displayName": summary.customer_name,
            "email": summary.customer_email,
            "initials": summary.customer_initials,
        },
        "agent": {
            "id": str(summary.agent_id) if summary.agent_id else None,
            "name": summary.agent_name,
        },
        "channel": summary.channel,
        "startedAt": _iso(summary.started_at),
        "updatedAt": _iso(summary.updated_at),
        "closedAt": _iso(summary.closed_at),
        "lastMessageAt": _iso(summary.last_message_at),
    }


def _serialize_document_summary(item: DocumentListItem) -> dict:
    return {
        "id": str(item.id),
        "name": item.name,
        "status": item.status,
        "statusLabel": item.status_label,
        "sourceType": item.source_type,
        "sourceLabel": item.source_label,
        "language": item.language,
        "category": item.category,
        "tokenCount": item.token_count,
        "sizeBytes": item.size_bytes,
        "isSensitive": item.is_sensitive,
        "lastIngestedAt": _iso(item.last_ingested_at),
        "lastSyncedAt": _iso(item.last_synced_at),
        "updatedAt": _iso(item.updated_at),
        "integrationName": item.integration_name,
        "ingestionError": item.ingestion_error,
    }


DOCUMENT_GUARDRAILS_KEY = "identifier_guardrails"
DOCUMENT_GUARDRAILS_REQUIRED_KEY = "required_keys"


def _normalize_guardrail_identifiers(raw_keys: object) -> list[str]:
    if raw_keys is None:
        return []
    if isinstance(raw_keys, str):
        values = [raw_keys]
    elif isinstance(raw_keys, list):
        values = raw_keys
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for entry in values:
        token = _normalize_identifier_token(str(entry or ""))
        if not token or token in seen:
            continue
        normalized.append(token)
        seen.add(token)
    return normalized


def _read_document_guardrails(metadata: object) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    guardrails = metadata.get(DOCUMENT_GUARDRAILS_KEY)
    if not isinstance(guardrails, dict):
        return []
    raw = (
        guardrails.get(DOCUMENT_GUARDRAILS_REQUIRED_KEY)
        or guardrails.get("required")
        or guardrails.get("requiredIdentifiers")
        or guardrails.get("required_identifiers")
    )
    return _normalize_guardrail_identifiers(raw)


def _serialize_document_detail(detail: DocumentDetail) -> dict:
    payload = {
        "summary": _serialize_document_summary(detail.summary),
        "collections": [
            {
                "id": str(collection.id),
                "name": collection.name,
                "slug": collection.slug,
                "visibility": collection.visibility,
                "visibilityLabel": _visibility_label(collection.visibility),
            }
            for collection in getattr(detail, "collections", ())
        ],
        "summaryText": detail.summary_text,
        "createdByAgent": detail.created_by_agent,
        "guardrails": {"requiredIdentifiers": _read_document_guardrails(detail.metadata)},
    }
    ingestion_meta = detail.ingestion_metadata if isinstance(detail.ingestion_metadata, dict) else None
    if ingestion_meta is not None:
        payload["ingestionMetadata"] = ingestion_meta
    quality_report = detail.ingestion_metadata.get("quality_report") if isinstance(detail.ingestion_metadata, dict) else None
    if isinstance(quality_report, dict) and quality_report:
        payload["qualityReport"] = quality_report
    if detail.pages:
        payload["layoutPages"] = [
            {
                "pageNumber": page.page_number,
                "width": page.width,
                "height": page.height,
                "rotation": page.rotation,
                "textDensity": page.text_density,
                "hasOcrContent": page.has_ocr_content,
                "contentType": page.content_type,
                "metadata": page.metadata,
                "blocks": [
                    {
                        "blockType": block.block_type,
                        "orderIndex": block.order_index,
                        "text": block.text,
                        "bbox": block.bbox,
                        "sectionHeading": block.section_heading,
                        "headingPath": list(block.heading_path),
                        "detectedLanguage": block.detected_language,
                        "confidence": block.confidence,
                        "metadata": block.metadata,
                    }
                    for block in page.blocks
                ],
            }
            for page in detail.pages
        ]
    if detail.tables:
        payload["structuredTables"] = [
            {
                "orderIndex": table.order_index,
                "title": table.title,
                "sectionHeading": table.section_heading,
                "pageNumber": table.page_number,
                "columnSchema": list(table.column_schema),
                "rowCount": table.row_count,
                "bbox": table.bbox,
                "metadata": table.metadata,
                "rows": [
                    {
                        "rowIndex": row.row_index,
                        "pageNumber": row.page_number,
                        "bbox": row.bbox,
                        "rawText": row.raw_text,
                        "metadata": row.metadata,
                        "cells": [
                            {
                                "columnIndex": cell.column_index,
                                "columnKey": cell.column_key,
                                "rawText": cell.raw_text,
                                "normalizedValue": cell.normalized_value,
                                "bbox": cell.bbox,
                                "confidence": cell.confidence,
                                "metadata": cell.metadata,
                            }
                            for cell in row.cells
                        ],
                    }
                    for row in table.rows
                ],
            }
            for table in detail.tables
        ]
    if detail.issues:
        payload["issues"] = [
            {
                "code": issue.code,
                "severity": issue.severity,
                "description": issue.description,
                "pageNumber": issue.page_number,
                "tableOrderIndex": issue.table_order_index,
                "rowIndex": issue.row_index,
                "columnIndex": issue.column_index,
                "details": issue.details,
                "createdAt": _iso(issue.created_at),
            }
            for issue in detail.issues
        ]
    if detail.chunks:
        payload["chunks"] = [
            {
                "index": chunk.index,
                "content": chunk.content,
                "tokenCount": chunk.token_count,
                "metadata": chunk.metadata,
            }
            for chunk in detail.chunks
        ]
    return payload


def _serialize_case_detail(detail: CaseDetail) -> dict:
    return {
        "case": _serialize_case_summary(detail.summary),
        "description": detail.description,
        "aiDiagnosis": detail.ai_diagnosis,
        "aiActionsTaken": detail.ai_actions_taken,
        "aiSuggestedActions": list(detail.ai_suggested_actions),
        "metadata": detail.metadata,
        "history": [
            {
                "id": str(item.id),
                "summary": item.summary,
                "source": item.source,
                "occurredAt": _iso(item.occurred_at),
                "sessionReference": item.session_reference or None,
                "metadata": item.metadata,
            }
            for item in detail.history
        ],
        "messages": [
            {
                "id": str(msg.id),
                "sender": msg.sender,
                "senderDisplayName": msg.sender_display_name,
                "content": msg.content,
                "contentType": msg.content_type,
                "sentAt": _iso(msg.sent_at),
                "sessionReference": msg.session_reference or None,
                "metadata": msg.metadata,
            }
            for msg in detail.messages
        ],
        "documents": [
            {
                "id": str(doc.id),
                "name": doc.name,
                "documentUrl": doc.document_url,
                "knowledgeUploadId": str(doc.knowledge_upload_id) if doc.knowledge_upload_id else None,
                "knowledgeUploadName": doc.knowledge_upload_name,
                "capturedAt": _iso(doc.captured_at),
                "metadata": doc.metadata,
            }
            for doc in detail.documents
        ],
        "notes": [
            {
                "id": str(note.id),
                "authorType": note.author_type,
                "content": note.content,
                "isPinned": note.is_pinned,
                "createdAt": _iso(note.created_at),
                "metadata": note.metadata,
            }
            for note in detail.notes
        ],
    }


def _serialize_case_list(result: CaseListResult) -> dict:
    return {
        "items": [_serialize_case_summary(item) for item in result.items],
        "total": result.total_count,
        "metrics": {
            "open": result.metrics.open_total,
            "urgent": result.metrics.urgent_open,
            "urgentDeltaHint": result.metrics.urgent_delta_hint,
            "avgOpenHours": result.metrics.average_open_hours,
        },
        "filters": result.filters_applied,
    }


def _serialize_customer_summary(summary: CustomerSummary) -> dict:
    return {
        "id": str(summary.id),
        "displayName": summary.display_name,
        "email": summary.email,
        "state": summary.state,
        "stateLabel": (summary.state or "").replace("_", " ").title() if summary.state else None,
        "lastInteractionAt": _iso(summary.last_interaction_at),
        "totalCases": summary.total_cases,
        "openCases": summary.open_cases,
    }


def _serialize_customer_detail(detail: CustomerDetailData) -> dict:
    return {
        "customer": _serialize_customer_summary(detail.summary),
        "primaryPhone": detail.primary_phone,
        "primaryAddress": detail.primary_address,
        "firstSeenAt": _iso(detail.first_seen_at),
        "stats": {
            "total_cases": detail.stats.get("total_cases"),
            "open_cases": detail.stats.get("open_cases"),
            "closed_cases": detail.stats.get("closed_cases"),
            "first_seen_at": _iso(detail.stats.get("first_seen_at")),
            "last_interaction_at": _iso(detail.stats.get("last_interaction_at")),
        },
        "contacts": [
            {
                "contactType": contact.contact_type,
                "label": contact.label,
                "value": contact.value,
                "isPrimary": contact.is_primary,
            }
            for contact in detail.contacts
        ],
        "tags": list(detail.tags),
        "casesOpen": [
            {
                "id": str(case.id),
                "caseNumber": case.case_number,
                "status": case.status,
                "startedAt": _iso(case.started_at),
            }
            for case in detail.cases_open
        ],
        "casesClosed": [
            {
                "id": str(case.id),
                "caseNumber": case.case_number,
                "status": case.status,
                "startedAt": _iso(case.started_at),
            }
            for case in detail.cases_closed
        ],
        "activity": [
            {
                "id": str(item.id),
                "subject": item.subject,
                "actorType": item.actor_type,
                "activityType": item.activity_type,
                "description": item.description,
                "occurredAt": _iso(item.occurred_at),
                "caseNumber": item.case_number,
            }
            for item in detail.activity
        ],
        "notes": [
            {
                "id": str(note.id),
                "authorType": note.author_type,
                "content": note.content,
                "isPinned": note.is_pinned,
                "createdAt": _iso(note.created_at),
            }
            for note in detail.notes
        ],
    }


@require_http_methods(["GET"])
def agents_collection(request: HttpRequest) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    q_name = request.GET.get("q_name") or request.GET.get("qName")
    role = request.GET.get("role")
    limit_param = request.GET.get("limit")
    offset_param = request.GET.get("offset")
    sort_by = request.GET.get("sort_by") or request.GET.get("sortBy") or "created_at"
    order = request.GET.get("order") or "desc"

    limit = limit_param if limit_param not in (None, "") else 50
    offset = offset_param if offset_param not in (None, "") else 0

    try:
        result = list_agents(
            business_profile=business,
            q_name=q_name,
            role=role,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            order=order,
        )
    except AgentListValidationError as exc:
        payload = {
            "error": "VALIDATION_ERROR",
            "message": str(exc),
        }
        if exc.field:
            payload["field"] = exc.field
        return JsonResponse(payload, status=HTTPStatus.BAD_REQUEST)

    response = {
        "items": [
            {
                "id": str(item.id),
                "identifier": agent_identifier(item.id),
                "name": item.name,
                "role": item.role,
                "roleLabel": display_role_label(item.role),
                "tone": item.tone,
                "toneLabel": display_tone_label(item.tone),
                "status": item.status,
                "statusLabel": (item.status or "").replace("_", " ").title(),
                "publicSlug": item.public_slug,
                "conversations": item.conversations,
                "openCases": item.open_cases,
                "closedCases": item.closed_cases,
                "escalations": item.escalations,
                "averageHandleSeconds": item.average_handle_seconds,
                "lastActiveAt": _iso(item.last_active_at),
                "updatedAt": _iso(item.updated_at),
                "createdAt": _iso(item.created_at),
            }
            for item in result.items
        ],
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def agent_detail_view(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    try:
        detail = get_agent_detail(business_profile=business, agent_id=agent_id)
    except AgentProfile.DoesNotExist:
        return JsonResponse(
            {"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    response = {
        "agent": {
            "id": str(detail.summary.id),
            "identifier": agent_identifier(detail.summary.id),
            "name": detail.summary.name,
            "status": detail.summary.status,
            "statusLabel": (detail.summary.status or "").replace("_", " ").title(),
            "role": detail.summary.role,
            "roleLabel": display_role_label(detail.summary.role),
            "tone": detail.summary.tone,
            "toneLabel": display_tone_label(detail.summary.tone),
            "publicSlug": detail.summary.public_slug,
            "shareablePath": detail.shareable_path,
            "traits": list(detail.traits),
            "escalationRule": detail.escalation_rule,
            "kpis": list(detail.selected_kpis),
            "customKpis": list(detail.custom_kpis),
            "allowCustomKpiWeighting": detail.allow_custom_kpi_weighting,
            "knowledge": {
                "mode": detail.knowledge_mode,
                "documents": [
                    {
                        "id": str(doc.id),
                        "name": doc.name,
                        "status": doc.status,
                        "sourceType": doc.source_type,
                        "lastSyncedAt": _iso(doc.last_synced_at),
                    }
                    for doc in detail.knowledge_documents
                ],
                "collections": [
                    {
                        "id": str(collection.id),
                        "name": collection.name,
                        "visibility": collection.visibility,
                        "visibilityLabel": _visibility_label(collection.visibility),
                        "updatedAt": _iso(collection.updated_at),
                    }
                    for collection in getattr(detail, "knowledge_collections", ())
                ],
            },
            "stats": {
                "casesTotal": detail.stats.total_cases,
                "casesOpen": detail.stats.open_cases,
                "casesClosed": detail.stats.closed_cases,
                "escalations": detail.stats.escalations,
                "averageHandleSeconds": detail.stats.average_handle_seconds,
                "lastActiveAt": _iso(detail.stats.last_active_at),
            },
            "createdAt": _iso(detail.summary.created_at),
            "updatedAt": _iso(detail.summary.updated_at),
        }
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["GET", "PUT"])
def agent_action_settings_view(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    agent = (
        AgentProfile.objects.select_related("business_profile")
        .prefetch_related("action_permissions")
        .filter(id=agent_id, user=request.user)
        .first()
    )
    if agent is None:
        return JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)

    if request.method == "GET":
        settings = list_action_settings(agent)
        return JsonResponse(
            {
                "actions": [
                    {
                        "key": setting.key,
                        "label": setting.label,
                        "description": setting.description,
                        "enabled": setting.enabled,
                    }
                    for setting in settings
                ]
            }
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "INVALID_JSON", "message": "Body must be valid JSON."}, status=HTTPStatus.BAD_REQUEST)

    action_key = str(payload.get("action") or payload.get("key") or "").strip()
    if not action_key:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "action is required."}, status=HTTPStatus.BAD_REQUEST)
    enabled = bool(payload.get("enabled"))

    try:
        setting = set_action_setting(agent, action_key=action_key, enabled=enabled)
    except ValueError as exc:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    return JsonResponse(
        {
            "action": {
                "key": setting.key,
                "label": setting.label,
                "description": setting.description,
                "enabled": setting.enabled,
            }
        }
    )


@require_http_methods(["GET", "PUT"])
def agent_knowledge_access_view(request: HttpRequest, agent_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    agent = (
        AgentProfile.objects.select_related("business_profile")
        .prefetch_related("allowed_documents", "allowed_collections")
        .filter(id=agent_id, user=request.user)
        .first()
    )
    if agent is None:
        return JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)

    business = agent.business_profile

    if request.method == "GET":
        with tenant_context(business.id):
            collection_ids = list(
                agent.allowed_collections.filter(business_profile=business).values_list("id", flat=True)
            )
            document_ids = list(agent.allowed_documents.filter(business_profile=business).values_list("id", flat=True))
        return JsonResponse(
            {
                "knowledgeAccess": {
                    "mode": "select" if (collection_ids or document_ids) else "all",
                    "collectionIds": [str(value) for value in collection_ids],
                    "documentIds": [str(value) for value in document_ids],
                }
            },
            status=HTTPStatus.OK,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "INVALID_JSON", "message": "Body must be valid JSON."}, status=HTTPStatus.BAD_REQUEST)

    mode = str(payload.get("mode") or payload.get("knowledgeMode") or "").strip().lower()
    if mode not in {"all", "select"}:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "mode must be either 'all' or 'select'."},
            status=HTTPStatus.BAD_REQUEST,
        )

    collection_ids_raw = payload.get("collectionIds")
    document_ids_raw = payload.get("documentIds")

    def _parse_id_list(values: object, *, field: str) -> list[uuid.UUID]:
        if values is None:
            return []
        if not isinstance(values, list):
            raise KnowledgeCollectionValidationError(f"{field} must be a list.", field=field)
        parsed: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for entry in values:
            try:
                entry_id = entry if isinstance(entry, uuid.UUID) else uuid.UUID(str(entry))
            except (TypeError, ValueError) as exc:
                raise KnowledgeCollectionValidationError(f"{field} must contain valid UUIDs.", field=field) from exc
            if entry_id in seen:
                continue
            parsed.append(entry_id)
            seen.add(entry_id)
        return parsed

    try:
        collection_ids = _parse_id_list(collection_ids_raw, field="collectionIds")
        document_ids = _parse_id_list(document_ids_raw, field="documentIds")
    except KnowledgeCollectionValidationError as exc:
        response_payload = {"error": "VALIDATION_ERROR", "message": str(exc)}
        if exc.field:
            response_payload["field"] = exc.field
        return JsonResponse(response_payload, status=HTTPStatus.BAD_REQUEST)

    with tenant_context(business.id):
        if mode == "all":
            agent.allowed_collections.clear()
            agent.allowed_documents.clear()
            updated_collections: list[str] = []
            updated_documents: list[str] = []
        else:
            collections = list(KnowledgeCollection.objects.filter(business_profile=business, id__in=collection_ids).only("id"))
            found_collections = {collection.id for collection in collections}
            missing = [str(value) for value in collection_ids if value not in found_collections]
            if missing:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": f"Unknown collection ids: {', '.join(missing)}", "field": "collectionIds"},
                    status=HTTPStatus.BAD_REQUEST,
                )

            documents = list(KnowledgeUpload.objects.filter(business_profile=business, id__in=document_ids).only("id"))
            found_documents = {doc.id for doc in documents}
            missing_documents = [str(value) for value in document_ids if value not in found_documents]
            if missing_documents:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": f"Unknown document ids: {', '.join(missing_documents)}", "field": "documentIds"},
                    status=HTTPStatus.BAD_REQUEST,
                )

            agent.allowed_collections.set(collections)
            if document_ids_raw is not None:
                agent.allowed_documents.set(documents)

            updated_collections = [str(value) for value in collection_ids]
            updated_documents = [str(value) for value in document_ids] if document_ids_raw is not None else [
                str(value) for value in agent.allowed_documents.values_list("id", flat=True)
            ]

    return JsonResponse(
        {
            "knowledgeAccess": {
                "mode": "select" if (updated_collections or updated_documents) else "all",
                "collectionIds": updated_collections,
                "documentIds": updated_documents,
            }
        },
        status=HTTPStatus.OK,
    )


@require_http_methods(["GET"])
def knowledge_documents_collection(request: HttpRequest) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    q_name = request.GET.get("q")
    collection_slug = request.GET.get("collection")
    status = request.GET.get("status")
    source_type = request.GET.get("source_type")
    limit = request.GET.get("limit") or 50
    offset = request.GET.get("offset") or 0

    try:
        result = list_knowledge_documents(
            business_profile=business,
            q_name=q_name,
            collection_slug=collection_slug,
            status=status,
            source_type=source_type,
            limit=limit,
            offset=offset,
        )
    except KnowledgeDocumentListValidationError as exc:
        logger.warning(
            "knowledge_documents_collection validation_error user=%s business=%s field=%s message=%s",
            getattr(request.user, "id", None),
            getattr(business, "id", None),
            exc.field,
            exc,
        )
        payload = {
            "error": "VALIDATION_ERROR",
            "message": str(exc),
        }
        if exc.field:
            payload["field"] = exc.field
        return JsonResponse(payload, status=HTTPStatus.BAD_REQUEST)

    logger.info(
        "knowledge_documents_collection user=%s business=%s total=%s limit=%s offset=%s filters=%s",
        getattr(request.user, "id", None),
        business.id,
        result.total,
        result.limit,
        result.offset,
        {
            "q": q_name,
            "collection": collection_slug,
            "status": status,
            "source_type": source_type,
        },
    )

    response = {
        "items": [_serialize_document_summary(item) for item in result.items],
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["GET", "DELETE", "PATCH"])
def knowledge_document_detail(request: HttpRequest, document_id: uuid.UUID):
    payload: dict | None = None
    if request.method == "PATCH":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )
        business_id = request.GET.get("business_id") or (payload or {}).get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    if request.method == "PATCH":
        if not request.user.is_authenticated:
            return JsonResponse(
                {"error": "UNAUTHORIZED", "message": "Login required to update documents."},
                status=HTTPStatus.UNAUTHORIZED,
            )
        payload = payload or {}
        display_name_keys = ("display_name", "displayName", "name")
        display_name_provided = any(key in payload for key in display_name_keys)
        display_name = ""
        if display_name_provided:
            display_name = (
                str(payload.get("display_name") or payload.get("displayName") or payload.get("name") or "").strip()
            )
            if not display_name:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "Display name is required."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            if len(display_name) > 255:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "Display name must be 255 characters or fewer."},
                    status=HTTPStatus.BAD_REQUEST,
                )

        guardrails_payload = payload.get("guardrails")
        raw_required = None
        if "requiredIdentifiers" in payload:
            raw_required = payload.get("requiredIdentifiers")
        elif guardrails_payload is not None:
            if not isinstance(guardrails_payload, dict):
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "guardrails must be an object."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            for key in ("requiredIdentifiers", "required_keys", "required", "required_identifiers"):
                if key in guardrails_payload:
                    raw_required = guardrails_payload.get(key)
                    break

        collections_provided = any(key in payload for key in ("collectionIds", "collection_ids", "collections"))
        collection_ids_raw = None
        if collections_provided:
            collection_ids_raw = payload.get("collectionIds")
            if collection_ids_raw is None:
                collection_ids_raw = payload.get("collection_ids")
            if collection_ids_raw is None:
                collection_ids_raw = payload.get("collections")

        if not display_name_provided and raw_required is None and not collections_provided:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "No changes provided."},
                status=HTTPStatus.BAD_REQUEST,
            )

        if raw_required is not None and not isinstance(raw_required, list):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "requiredIdentifiers must be a list."},
                status=HTTPStatus.BAD_REQUEST,
            )

        upload = KnowledgeUpload.objects.filter(business_profile=business, id=document_id).first()
        if not upload:
            return JsonResponse(
                {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
                status=HTTPStatus.NOT_FOUND,
            )

        update_fields: list[str] = []
        if display_name_provided:
            upload.display_name = display_name[:255]
            update_fields.append("display_name")

        required_identifiers: list[str] = []
        if raw_required is not None:
            required_identifiers = _normalize_guardrail_identifiers(raw_required or [])
            metadata = upload.metadata if isinstance(upload.metadata, dict) else {}
            metadata_copy = dict(metadata)
            guardrails_meta = metadata_copy.get(DOCUMENT_GUARDRAILS_KEY)
            if not isinstance(guardrails_meta, dict):
                guardrails_meta = {}
            if required_identifiers:
                guardrails_meta[DOCUMENT_GUARDRAILS_REQUIRED_KEY] = required_identifiers
                metadata_copy[DOCUMENT_GUARDRAILS_KEY] = guardrails_meta
            else:
                guardrails_meta.pop(DOCUMENT_GUARDRAILS_REQUIRED_KEY, None)
                if guardrails_meta:
                    metadata_copy[DOCUMENT_GUARDRAILS_KEY] = guardrails_meta
                else:
                    metadata_copy.pop(DOCUMENT_GUARDRAILS_KEY, None)
            upload.metadata = metadata_copy
            update_fields.append("metadata")

        updated_collections = None
        if collections_provided:
            if collection_ids_raw is None:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "collectionIds must be provided.", "field": "collectionIds"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            if not isinstance(collection_ids_raw, list):
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "collectionIds must be a list.", "field": "collectionIds"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            try:
                updated_collections = set_upload_collections(
                    business_profile=business,
                    upload_id=upload.id,
                    collection_ids=collection_ids_raw,
                    added_by=request.user,
                )
            except KnowledgeUpload.DoesNotExist:
                return JsonResponse(
                    {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
                    status=HTTPStatus.NOT_FOUND,
                )
            except KnowledgeCollectionValidationError as exc:
                response_payload = {"error": "VALIDATION_ERROR", "message": str(exc)}
                if exc.field:
                    response_payload["field"] = exc.field
                return JsonResponse(response_payload, status=HTTPStatus.BAD_REQUEST)

        if update_fields:
            upload.save(update_fields=update_fields + ["updated_at"])
        logger.info(
            "knowledge_document_update user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        response_payload: dict[str, object] = {"success": True, "document": {"id": str(upload.id), "name": upload.display_name}}
        if raw_required is not None:
            response_payload["guardrails"] = {"requiredIdentifiers": required_identifiers}
        if updated_collections is not None:
            response_payload["collections"] = [
                {
                    "id": str(collection.id),
                    "name": collection.name,
                    "slug": collection.slug,
                    "visibility": collection.visibility,
                    "visibilityLabel": _visibility_label(collection.visibility),
                }
                for collection in updated_collections
            ]
        return JsonResponse(response_payload, status=HTTPStatus.OK)

    if request.method == "DELETE":
        try:
            delete_knowledge_document(business_profile=business, document_id=document_id)
        except KnowledgeUpload.DoesNotExist:
            logger.warning(
                "knowledge_document_delete_not_found user=%s business=%s document=%s",
                getattr(request.user, "id", None),
                business.id,
                document_id,
            )
            return JsonResponse(
                {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
                status=HTTPStatus.NOT_FOUND,
            )

        logger.info(
            "knowledge_document_delete user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return HttpResponse(status=HTTPStatus.NO_CONTENT)

    try:
        detail = get_knowledge_document_detail(business_profile=business, document_id=document_id)
    except KnowledgeUpload.DoesNotExist:
        logger.warning(
            "knowledge_document_detail not_found user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    logger.info(
        "knowledge_document_detail user=%s business=%s document=%s status=%s source=%s",
        getattr(request.user, "id", None),
        business.id,
        document_id,
        detail.summary.status,
        detail.summary.source_type,
    )

    return JsonResponse({"document": _serialize_document_detail(detail)}, status=HTTPStatus.OK)


@require_http_methods(["GET", "POST"])
def knowledge_collections_collection(request: HttpRequest) -> JsonResponse:
    business_param = request.GET.get("business_id")
    business, error = _resolve_owned_business_profile(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method == "GET":
        limit = request.GET.get("limit") or 200
        offset = request.GET.get("offset") or 0
        try:
            items = list_knowledge_collections(business_profile=business, limit=limit, offset=offset)
        except KnowledgeCollectionValidationError as exc:
            payload = {"error": "VALIDATION_ERROR", "message": str(exc)}
            if exc.field:
                payload["field"] = exc.field
            return JsonResponse(payload, status=HTTPStatus.BAD_REQUEST)

        with tenant_context(business.id):
            total = KnowledgeCollection.objects.filter(business_profile=business).count()

        return JsonResponse(
            {
                "items": [
                    {
                        "id": str(item.id),
                        "name": item.name,
                        "slug": item.slug,
                        "description": item.description,
                        "visibility": item.visibility,
                        "visibilityLabel": _visibility_label(item.visibility),
                        "documents": item.documents,
                        "owner": item.owner,
                        "focus": "—",
                        "updatedAt": _iso(item.updated_at),
                    }
                    for item in items
                ],
                "total": total,
                "limit": int(limit) if str(limit).isdigit() else None,
                "offset": int(offset) if str(offset).isdigit() else None,
            },
            status=HTTPStatus.OK,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "INVALID_JSON", "message": "Body must be valid JSON."}, status=HTTPStatus.BAD_REQUEST)

    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    visibility = payload.get("visibility")

    try:
        collection = create_knowledge_collection(
            business_profile=business,
            created_by=request.user,
            name=name,
            description=description,
            visibility=str(visibility).strip() if visibility else None,
        )
    except KnowledgeCollectionValidationError as exc:
        response_payload = {"error": "VALIDATION_ERROR", "message": str(exc)}
        if exc.field:
            response_payload["field"] = exc.field
        return JsonResponse(response_payload, status=HTTPStatus.BAD_REQUEST)

    return JsonResponse({"collection": _serialize_collection(collection, documents=0)}, status=HTTPStatus.CREATED)


@require_http_methods(["GET", "PATCH", "DELETE"])
def knowledge_collection_detail(request: HttpRequest, collection_id: uuid.UUID) -> JsonResponse:
    business_param = request.GET.get("business_id")
    business, error = _resolve_owned_business_profile(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method == "DELETE":
        try:
            delete_knowledge_collection(business_profile=business, collection_id=collection_id)
        except KnowledgeCollection.DoesNotExist:
            return JsonResponse(
                {"error": "COLLECTION_NOT_FOUND", "message": "Collection not found."},
                status=HTTPStatus.NOT_FOUND,
            )
        return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

    if request.method == "PATCH":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "INVALID_JSON", "message": "Body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )

        name = payload.get("name")
        description = payload.get("description")
        visibility = payload.get("visibility")
        try:
            collection = update_knowledge_collection(
                business_profile=business,
                collection_id=collection_id,
                name=str(name).strip() if name is not None else None,
                description=str(description).strip() if description is not None else None,
                visibility=str(visibility).strip() if visibility is not None else None,
            )
        except KnowledgeCollection.DoesNotExist:
            return JsonResponse(
                {"error": "COLLECTION_NOT_FOUND", "message": "Collection not found."},
                status=HTTPStatus.NOT_FOUND,
            )
        except KnowledgeCollectionValidationError as exc:
            response_payload = {"error": "VALIDATION_ERROR", "message": str(exc)}
            if exc.field:
                response_payload["field"] = exc.field
            return JsonResponse(response_payload, status=HTTPStatus.BAD_REQUEST)

        with tenant_context(business.id):
            doc_count = collection.links.count()

        return JsonResponse({"collection": _serialize_collection(collection, documents=doc_count)}, status=HTTPStatus.OK)

    with tenant_context(business.id):
        collection = KnowledgeCollection.objects.filter(business_profile=business, id=collection_id).first()
        if not collection:
            return JsonResponse(
                {"error": "COLLECTION_NOT_FOUND", "message": "Collection not found."},
                status=HTTPStatus.NOT_FOUND,
            )
        doc_count = collection.links.count()
    return JsonResponse({"collection": _serialize_collection(collection, documents=doc_count)}, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def knowledge_document_download(request: HttpRequest, document_id: uuid.UUID):
    if not request.user.is_authenticated:
        logger.warning("knowledge_document_download unauthorized document=%s", document_id)
        return JsonResponse(
            {"error": "UNAUTHORIZED", "message": "Login required to download documents."},
            status=HTTPStatus.UNAUTHORIZED,
        )

    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    owns_business = request.user.is_staff or request.user.business_profiles.filter(id=business.id).exists()
    if not owns_business:
        logger.warning(
            "knowledge_document_download forbidden user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "FORBIDDEN", "message": "You do not have access to this document."},
            status=HTTPStatus.FORBIDDEN,
        )

    upload = (
        KnowledgeUpload.objects.filter(
            business_profile=business,
            id=document_id,
            source_type=KnowledgeSourceType.FILE,
        )
        .select_related("file_detail")
        .first()
    )
    if upload is None or not upload.file_detail:
        logger.warning(
            "knowledge_document_download missing_file user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "DOCUMENT_NOT_FOUND", "message": "File not found for download."},
            status=HTTPStatus.NOT_FOUND,
        )

    download = request.GET.get("download") == "1"
    try:
        response = _serve_document_file(upload.file_detail, download=download)
    except PermissionError:
        logger.error(
            "knowledge_document_download invalid_path user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "FILE_INVALID", "message": "File path is invalid."},
            status=HTTPStatus.BAD_REQUEST,
        )
    except FileNotFoundError:
        logger.error(
            "knowledge_document_download file_missing user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "FILE_MISSING", "message": "Original file is no longer available."},
            status=HTTPStatus.GONE,
        )

    logger.info(
        "knowledge_document_download success user=%s business=%s document=%s as_attachment=%s",
        getattr(request.user, "id", None),
        business.id,
        document_id,
        download,
    )
    try:
        KnowledgeAuditEvent.objects.create(
            business_profile=business,
            upload=upload,
            actor_user=request.user,
            action=KnowledgeAuditAction.EXPORTED,
            description="Knowledge document downloaded.",
            metadata={
                "endpoint": "knowledge_document_download",
                "as_attachment": bool(download),
            },
        )
    except Exception:
        logger.exception(
            "knowledge.audit_export_failed user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
    return response


@require_http_methods(["POST"])
def knowledge_document_scrape(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    url = str(payload.get("url") or "").strip()
    if not url:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Provide a URL to scrape.", "field": "url"},
            status=HTTPStatus.BAD_REQUEST,
        )

    timeout = payload.get("timeout")
    max_bytes = payload.get("maxBytes")

    timeout_value = 10.0
    if timeout is not None:
        try:
            timeout_value = max(1.0, min(float(timeout), 30.0))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "timeout must be numeric.", "field": "timeout"},
                status=HTTPStatus.BAD_REQUEST,
            )

    max_bytes_value = 2_000_000
    if max_bytes is not None:
        try:
            max_bytes_value = int(max_bytes)
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "maxBytes must be numeric.", "field": "maxBytes"},
                status=HTTPStatus.BAD_REQUEST,
            )
        max_bytes_value = max(100_000, min(max_bytes_value, 5_000_000))

    try:
        scraped = scrape_document_source(url=url, timeout=timeout_value, max_bytes=max_bytes_value)
    except DocumentScrapeError as exc:
        return JsonResponse(
            {"error": "SCRAPE_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )

    response = {
        "scraped": {
            "url": scraped.url,
            "finalUrl": scraped.final_url,
            "statusCode": scraped.status_code,
            "contentType": scraped.content_type,
            "elapsedMs": scraped.elapsed_ms,
            "contentLength": scraped.content_length,
            "truncated": scraped.truncated,
            "preview": scraped.preview,
            "wordCount": scraped.word_count,
            "text": scraped.text,
        }
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["POST"])
def knowledge_document_preview_csv(request: HttpRequest) -> JsonResponse:
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Upload a CSV file to preview.", "field": "file"},
            status=HTTPStatus.BAD_REQUEST,
        )

    max_rows = request.POST.get("max_rows")
    max_rows_value = 50
    if max_rows is not None:
        try:
            max_rows_value = max(1, min(int(max_rows), 200))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "max_rows must be numeric.", "field": "max_rows"},
                status=HTTPStatus.BAD_REQUEST,
            )

    try:
        preview = preview_csv_upload(upload, max_rows=max_rows_value)
    except CsvPreviewError as exc:
        return JsonResponse(
            {"error": "CSV_PREVIEW_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )

    response = {
        "preview": {
            "columns": list(preview.columns),
            "rows": [list(row) for row in preview.rows],
            "rowCount": preview.row_count,
            "truncated": preview.truncated,
            "dialect": preview.dialect,
        }
    }
    return JsonResponse(response, status=HTTPStatus.OK)


def _google_resources_payload(
    integration: KnowledgeIntegration,
    *,
    limit: int,
    search: str | None = None,
) -> dict[str, Any]:
    available = discover_google_sheet_resources(integration, limit=limit, search=search)
    config_map = {resource.get("resource_id"): resource for resource in integration.resource_configs}
    default_visibility = integration.get_default_visibility()
    default_sync_frequency = integration.get_default_sync_frequency()

    available_payload: list[dict[str, Any]] = []
    for entry in available:
        rid = entry.get("resource_id")
        config = config_map.get(rid)
        column_privacy = (config or {}).get("column_privacy") or {}
        available_payload.append(
            {
                "resourceId": rid,
                "driveFileId": entry.get("drive_file_id"),
                "driveFileName": entry.get("drive_file_name"),
                "sheetGid": entry.get("sheet_gid"),
                "sheetName": entry.get("sheet_name"),
                "rowCount": entry.get("row_count"),
                "columnCount": entry.get("column_count"),
                "modifiedAt": entry.get("modified_time"),
                "owner": entry.get("owner"),
                "ownerEmail": entry.get("owner_email"),
                "webViewLink": entry.get("web_view_link"),
                "selected": bool(config),
                "visibility": (config or {}).get("visibility") or default_visibility,
                "syncFrequency": (config or {}).get("sync_frequency") or default_sync_frequency,
                "columnPrivacy": {
                    "sharedColumns": column_privacy.get("shared_columns") or [],
                    "internalOnlyColumns": column_privacy.get("internal_only_columns") or [],
                    "excludedColumns": column_privacy.get("excluded_columns") or [],
                },
                "lastSyncedAt": (config or {}).get("last_synced_at"),
                "lastSyncStatus": (config or {}).get("last_sync_status"),
                "lastSyncError": (config or {}).get("last_sync_error"),
            }
        )

    payload = {
        "integration": {
            "id": str(integration.id),
            "name": integration.name,
            "status": integration.status,
            "lastSyncedAt": _iso(integration.last_synced_at),
        },
        "defaultVisibility": default_visibility,
        "defaultSyncFrequency": default_sync_frequency,
        "availableResources": available_payload,
        "selectedResources": [_serialize_resource_config(resource) for resource in integration.resource_configs],
    }
    return payload


def _save_google_resources(
    integration: KnowledgeIntegration,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    default_visibility = str(payload.get("defaultVisibility") or integration.get_default_visibility())
    valid_visibilities = {choice for choice, _ in KnowledgeVisibility.choices}
    if default_visibility not in valid_visibilities:
        default_visibility = integration.get_default_visibility()
    default_sync_frequency = str(payload.get("defaultSyncFrequency") or integration.get_default_sync_frequency())
    valid_freqs = {choice for choice, _ in IntegrationSyncFrequency.choices}
    if default_sync_frequency not in valid_freqs:
        default_sync_frequency = integration.get_default_sync_frequency()

    resources_payload = payload.get("resources") or []
    normalized = _normalize_integration_resources(
        resources_payload,
        default_visibility=default_visibility,
        default_sync_frequency=default_sync_frequency,
        privacy_policy=integration.business_profile.table_privacy_policy(),
    )

    integration.set_default_visibility(default_visibility)
    integration.set_default_sync_frequency(default_sync_frequency)
    integration.set_resource_configs(normalized)
    integration.sync_error = ""
    if integration.status == KnowledgeIntegrationStatus.DISCONNECTED and normalized:
        integration.status = KnowledgeIntegrationStatus.CONNECTED
    integration.save(update_fields=["settings", "status", "sync_error", "updated_at"])

    return {
        "integration": {
            "id": str(integration.id),
            "status": integration.status,
        },
        "defaultVisibility": default_visibility,
        "defaultSyncFrequency": default_sync_frequency,
        "resources": [_serialize_resource_config(resource) for resource in normalized],
    }


@require_http_methods(["GET"])
def google_drive_resources(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    integration_param = request.GET.get("integration_id")
    integration_id, error = _parse_uuid_param(integration_param, "integration_id")
    if error:
        return error
    integration = _get_google_integration(business, integration_id)
    if integration is None:
        return JsonResponse(
            {"error": "INTEGRATION_NOT_FOUND", "message": "Connect Google Drive to list resources."},
            status=HTTPStatus.NOT_FOUND,
        )

    limit_param = request.GET.get("limit")
    try:
        limit = max(1, min(int(limit_param) if limit_param else 20, 50))
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "limit must be numeric."},
            status=HTTPStatus.BAD_REQUEST,
        )
    search = request.GET.get("q")

    try:
        payload = _google_resources_payload(integration, limit=limit, search=search)
    except (GoogleOAuthError, GoogleSheetsDiscoveryError) as exc:
        logger.warning(
            "google_drive_resources_failed integration=%s business=%s error=%s",
            integration.id,
            business.id,
            exc,
        )
        return JsonResponse(
            {"error": "GOOGLE_API_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_GATEWAY,
        )

    return JsonResponse(payload, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def google_drive_save_resources(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    business_id = payload.get("businessId") or request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    integration_param = payload.get("integrationId") or request.GET.get("integration_id")
    integration_id, error = _parse_uuid_param(integration_param, "integration_id")
    if error:
        return error
    integration = _get_google_integration(business, integration_id)
    if integration is None:
        return JsonResponse(
            {"error": "INTEGRATION_NOT_FOUND", "message": "Connect Google Drive before saving resources."},
            status=HTTPStatus.NOT_FOUND,
        )

    try:
        result = _save_google_resources(integration, payload=payload)
    except ValueError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )

    return JsonResponse(result, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET", "POST"])
def integrations_collection(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    payload: dict[str, Any] | None = None
    if request.method == "GET":
        business_param = request.GET.get("business_id") or request.GET.get("businessId")
    else:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )
        business_param = payload.get("businessId") or payload.get("business_id") or request.GET.get("business_id")

    business, error = _resolve_business_profile(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method == "GET":
        integrations = list(
            KnowledgeIntegration.objects.filter(business_profile=business).order_by("name")
        )
        response = {
            "businessId": str(business.id),
            "dashboardUrl": getattr(settings, "INTEGRATIONS_DASHBOARD_URL", "/dashboard/knowledge?panel=integrations"),
            "integrations": [_serialize_integration_summary(integration) for integration in integrations],
            "providers": _integration_provider_catalog(),
            "stats": _integration_stats(integrations),
        }
        return JsonResponse(response, status=HTTPStatus.OK)

    name = str((payload or {}).get("name") or (payload or {}).get("displayName") or "").strip()
    integration_type = str((payload or {}).get("type") or (payload or {}).get("integrationType") or "").strip() or KnowledgeIntegrationType.CUSTOM
    valid_types = {choice for choice, _ in KnowledgeIntegrationType.choices}
    if integration_type not in valid_types:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "integration type is invalid."},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not name:
        name = dict(KnowledgeIntegrationType.choices).get(integration_type, "Integration")

    integration = KnowledgeIntegration.objects.create(
        business_profile=business,
        created_by=request.user,
        name=name,
        integration_type=integration_type,
        status=KnowledgeIntegrationStatus.DISCONNECTED,
    )
    return JsonResponse(
        {"integration": _serialize_integration_summary(integration)},
        status=HTTPStatus.CREATED,
    )


@csrf_protect
@require_http_methods(["GET", "POST"])
def integration_sheets_collection(request: HttpRequest, integration_id: uuid.UUID) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    body_payload: dict[str, Any] | None = None
    if request.method == "GET":
        business_param = request.GET.get("business_id") or request.GET.get("businessId")
    else:
        try:
            body_payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )
        business_param = body_payload.get("businessId") or body_payload.get("business_id") or request.GET.get("business_id")

    business, error = _resolve_business_profile(request, business_param)
    if error:
        return error
    assert business is not None

    try:
        integration = KnowledgeIntegration.objects.get(id=integration_id, business_profile=business)
    except KnowledgeIntegration.DoesNotExist:
        return JsonResponse(
            {"error": "INTEGRATION_NOT_FOUND", "message": "Integration not found for this business."},
            status=HTTPStatus.NOT_FOUND,
        )

    if integration.integration_type != KnowledgeIntegrationType.GOOGLE_DRIVE:
        return JsonResponse(
            {
                "error": "UNSUPPORTED_PROVIDER",
                "message": "Sheets configuration is not available for this integration type.",
            },
            status=HTTPStatus.NOT_IMPLEMENTED,
        )

    if request.method == "GET":
        limit_param = request.GET.get("limit")
        try:
            limit = max(1, min(int(limit_param) if limit_param else 20, 50))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "limit must be numeric."},
                status=HTTPStatus.BAD_REQUEST,
            )
        search = request.GET.get("q") or request.GET.get("search")
        try:
            payload = _google_resources_payload(integration, limit=limit, search=search)
        except (GoogleOAuthError, GoogleSheetsDiscoveryError) as exc:
            logger.warning(
                "integration_sheets_failed integration=%s business=%s error=%s",
                integration.id,
                business.id,
                exc,
            )
            return JsonResponse(
                {"error": "GOOGLE_API_ERROR", "message": str(exc)},
                status=HTTPStatus.BAD_GATEWAY,
            )
        return JsonResponse(payload, status=HTTPStatus.OK)

    try:
        result = _save_google_resources(integration, payload=body_payload or {})
    except ValueError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    return JsonResponse(result, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def google_drive_sync_now(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    business_id = payload.get("businessId") or request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    integration_param = payload.get("integrationId") or request.GET.get("integration_id")
    integration_id, error = _parse_uuid_param(integration_param, "integration_id")
    if error:
        return error
    integration = _get_google_integration(business, integration_id)
    if integration is None:
        return JsonResponse(
            {"error": "INTEGRATION_NOT_FOUND", "message": "Google Drive integration not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    resource_ids = payload.get("resourceIds")
    if resource_ids is not None and not isinstance(resource_ids, list):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "resourceIds must be an array."},
            status=HTTPStatus.BAD_REQUEST,
        )

    service = IntegrationSyncService()
    try:
        result = service.sync_integration(integration, resource_ids=resource_ids or None)
    except (IntegrationSyncError, GoogleOAuthError) as exc:
        logger.warning(
            "google_drive_sync_now_failed integration=%s business=%s error=%s",
            integration.id,
            business.id,
            exc,
        )
        return JsonResponse(
            {"error": "SYNC_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_GATEWAY,
        )
    integration.refresh_from_db()
    schedule = integration.get_sync_schedule()

    summary = {
        "integrationId": result.integration_id,
        "provider": result.provider,
        "nextSyncAt": schedule.get("next_run_at"),
        "rowsIngested": result.rows_ingested,
        "status": result.status,
        "message": result.message,
        "resources": [
            {
                "resourceId": outcome.resource_id,
                "status": outcome.status,
                "uploadId": outcome.upload_id,
                "bytesWritten": outcome.bytes_written,
                "jobId": outcome.job_id,
                "message": outcome.message,
                "rowsIngested": outcome.rows_ingested,
            }
            for outcome in result.resources
        ],
    }
    return JsonResponse(summary, status=HTTPStatus.OK)


@require_http_methods(["POST"])
def start_google_drive_oauth(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse(
            {"error": "UNAUTHORIZED", "message": "Login required."},
            status=HTTPStatus.UNAUTHORIZED,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    business_id = payload.get("businessId") or payload.get("business_id") or request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    integration = (
        KnowledgeIntegration.objects.filter(
            business_profile=business,
            integration_type=KnowledgeIntegrationType.GOOGLE_DRIVE,
        )
        .order_by("-created_at")
        .first()
    )
    if integration is None:
        integration = KnowledgeIntegration(
            business_profile=business,
            created_by=request.user,
            integration_type=KnowledgeIntegrationType.GOOGLE_DRIVE,
            name="Google Drive",
            status=KnowledgeIntegrationStatus.DISCONNECTED,
        )
    else:
        if not integration.created_by_id:
            integration.created_by = request.user
        if not integration.name:
            integration.name = "Google Drive"

    state_token = secrets.token_urlsafe(32)
    metadata = dict(integration.metadata or {})
    metadata["oauth_state"] = {
        "token": state_token,
        "created_at": timezone.now().isoformat(),
        "initiated_by": str(getattr(request.user, "id", "")),
    }
    integration.metadata = metadata
    integration.status = KnowledgeIntegrationStatus.SYNCING
    integration.sync_error = ""
    integration.save()

    state = f"{integration.id}:{state_token}"
    try:
        authorization_url = build_google_authorization_url(state)
    except (GoogleOAuthError, ImproperlyConfigured) as exc:
        logger.exception("google_drive_oauth_start_failed business=%s error=%s", business.id, exc)
        return JsonResponse(
            {"error": "OAUTH_CONFIGURATION", "message": str(exc)},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    logger.info(
        "google_drive_oauth_start business=%s integration=%s user=%s",
        business.id,
        integration.id,
        getattr(request.user, "id", None),
    )

    return JsonResponse(
        {
            "integrationId": str(integration.id),
            "authorizationUrl": authorization_url,
            "state": state,
        },
        status=HTTPStatus.OK,
    )


@require_http_methods(["GET"])
def google_drive_oauth_callback(request: HttpRequest) -> HttpResponseRedirect:
    state_value = request.GET.get("state")
    if not state_value:
        return _integration_redirect("google_drive", "error", "missing_state")

    integration, nonce = _resolve_integration_state(state_value)
    if integration is None or not nonce:
        return _integration_redirect("google_drive", "error", "unknown_integration")

    if not _validate_oauth_state(integration, nonce):
        _mark_integration_error(integration, "State mismatch.")
        return _integration_redirect("google_drive", "error", "state_mismatch")

    error_code = request.GET.get("error")
    if error_code:
        description = request.GET.get("error_description") or error_code
        _mark_integration_error(integration, description)
        return _integration_redirect("google_drive", "error", error_code)

    code = request.GET.get("code")
    if not code:
        _mark_integration_error(integration, "Missing authorization code.")
        return _integration_redirect("google_drive", "error", "missing_code")

    try:
        tokens = exchange_google_authorization_code(code)
    except (GoogleOAuthError, ImproperlyConfigured) as exc:
        _mark_integration_error(integration, str(exc))
        return _integration_redirect("google_drive", "error", "exchange_failed")

    access_token = tokens.get("access_token")
    if not access_token:
        _mark_integration_error(integration, "Google did not return an access token.")
        return _integration_redirect("google_drive", "error", "missing_access_token")

    try:
        account_profile = fetch_google_account_profile(access_token)
    except GoogleOAuthError as exc:
        _mark_integration_error(integration, str(exc))
        return _integration_redirect("google_drive", "error", "profile_failed")

    refresh_token = tokens.get("refresh_token") or (integration.credentials or {}).get("refresh_token")
    credentials = dict(integration.credentials or {})
    credentials.update(  # store the latest token snapshot for sync workers
        {
            "provider": "google_drive",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": tokens.get("expires_in"),
            "expires_at": tokens.get("expires_at"),
            "scope": tokens.get("scope"),
            "token_type": tokens.get("token_type", credentials.get("token_type") or "Bearer"),
            "account_email": account_profile.get("email"),
            "account_name": account_profile.get("name") or account_profile.get("email"),
            "account_picture": account_profile.get("picture"),
            "updated_at": timezone.now().isoformat(),
        }
    )
    metadata = dict(integration.metadata or {})
    metadata.pop("oauth_state", None)
    metadata["google_account"] = {
        "email": account_profile.get("email"),
        "name": account_profile.get("name"),
        "picture": account_profile.get("picture"),
        "linked_at": timezone.now().isoformat(),
    }

    integration.credentials = credentials
    integration.metadata = metadata
    integration.external_account_id = (
        account_profile.get("sub")
        or account_profile.get("id")
        or credentials.get("account_email")
        or integration.external_account_id
    )
    integration.status = KnowledgeIntegrationStatus.CONNECTED
    integration.sync_error = ""
    integration.reset_credential_failures()
    integration.save(update_fields=[
        "credentials_encrypted",
        "credentials_key_version",
        "credentials_last_rotated_at",
        "credential_error_count",
        "metadata",
        "external_account_id",
        "status",
        "sync_error",
        "updated_at",
    ])
    integration.log_credential_event(
        IntegrationCredentialEventType.CREATED,
        actor=request.user if request.user.is_authenticated else integration.created_by,
        metadata={
            "email": account_profile.get("email"),
            "name": account_profile.get("name"),
        },
    )

    logger.info(
        "google_drive_oauth_success integration=%s business=%s",
        integration.id,
        integration.business_profile_id,
    )

    return _integration_redirect("google_drive", "connected")


@require_http_methods(["GET", "POST"])
def cases_collection(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        business_id = request.GET.get("business_id")
    else:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )
        business_id = payload.get("businessId")

    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None  # for mypy

    if request.method == "GET":
        status_filter = request.GET.get("status")
        priority_filter = request.GET.get("priority")
        search = request.GET.get("search")
        agent_id_str = request.GET.get("agent_id")
        customer_id_str = request.GET.get("customer_id")
        limit = int(request.GET.get("limit", 50))
        offset = int(request.GET.get("offset", 0))

        agent_id = None
        if agent_id_str:
            try:
                agent_id = uuid.UUID(agent_id_str)
            except ValueError:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "agent_id must be a valid UUID."},
                    status=HTTPStatus.BAD_REQUEST,
                )

        customer_id = None
        if customer_id_str:
            try:
                customer_id = uuid.UUID(customer_id_str)
            except ValueError:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "customer_id must be a valid UUID."},
                    status=HTTPStatus.BAD_REQUEST,
                )

        result = list_cases(
            business_profile=business,
            status=status_filter,
            priority=priority_filter,
            search=search,
            agent_id=agent_id,
            customer_id=customer_id,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
        )

        return JsonResponse(_serialize_case_list(result), status=HTTPStatus.OK)

    # POST - create new case
    payload = payload or {}
    title = (payload.get("title") or "").strip()
    description = (payload.get("description") or "").strip()
    priority = (payload.get("priority") or CasePriority.MEDIUM).lower()
    status = (payload.get("status") or CaseStatus.OPEN).lower()
    agent_id = payload.get("agentId")
    customer_id = payload.get("customerId")
    ai_diagnosis = payload.get("aiDiagnosis") or ""
    ai_actions_taken = payload.get("aiActionsTaken") or ""
    ai_suggested_actions = payload.get("aiSuggestedActions") or []
    metadata = payload.get("metadata") or {}
    customer_snapshot = payload.get("customerSnapshot") or {}

    if not title:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Case title is required.", "field": "title"},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not description:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Case description is required.", "field": "description"},
            status=HTTPStatus.BAD_REQUEST,
        )

    agent_profile = None
    if agent_id:
        try:
            agent_profile = AgentProfile.objects.get(id=agent_id, business_profile=business)
        except AgentProfile.DoesNotExist:
            return JsonResponse(
                {"error": "AGENT_NOT_FOUND", "message": "Agent profile not found for business."},
                status=HTTPStatus.BAD_REQUEST,
            )

    customer = None
    if customer_id:
        try:
            customer = Customer.objects.get(id=customer_id, business_profile=business)
        except Customer.DoesNotExist:
            return JsonResponse(
                {"error": "CUSTOMER_NOT_FOUND", "message": "Customer not found for business."},
                status=HTTPStatus.BAD_REQUEST,
            )

    try:
        case = create_case(
            business_profile=business,
            title=title,
            description=description,
            priority=priority,
            status=status,
            agent_profile=agent_profile,
            customer=customer,
            customer_snapshot=customer_snapshot,
            ai_diagnosis=ai_diagnosis,
            ai_actions_taken=ai_actions_taken,
            ai_suggested_actions=ai_suggested_actions,
            metadata=metadata,
            created_by=request.user if request.user.is_authenticated else None,
        )
    except CaseServiceError as exc:
        return JsonResponse(
            {"error": "CASE_CREATE_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:
        logger.exception("Failed to create case.")
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to create case right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    detail = get_case_detail(business_profile=business, case_id=case.id)
    return JsonResponse({"case": _serialize_case_detail(detail)}, status=HTTPStatus.CREATED)


@require_http_methods(["GET", "PATCH"])
def case_detail_view(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    payload: dict | None = None
    if request.method == "PATCH":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )
        business_id = request.GET.get("business_id") or payload.get("businessId")
    else:
        business_id = request.GET.get("business_id")

    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    if request.method == "GET":
        try:
            detail = get_case_detail(business_profile=business, case_id=case_id)
        except Case.DoesNotExist:
            return JsonResponse(
                {"error": "CASE_NOT_FOUND", "message": "Case not found."},
                status=HTTPStatus.NOT_FOUND,
            )
        return JsonResponse(_serialize_case_detail(detail), status=HTTPStatus.OK)

    payload = payload or {}

    try:
        update_case(
            business_profile=business,
            case_id=case_id,
            title=payload.get("title"),
            description=payload.get("description"),
            priority=(payload.get("priority") or "").lower() or None,
            status=(payload.get("status") or "").lower() or None,
            ai_diagnosis=payload.get("aiDiagnosis"),
            ai_actions_taken=payload.get("aiActionsTaken"),
            ai_suggested_actions=payload.get("aiSuggestedActions"),
            metadata=payload.get("metadata"),
        )
    except CaseServiceError as exc:
        return JsonResponse(
            {"error": "CASE_UPDATE_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:
        logger.exception("Failed to update case.")
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to update case right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    detail = get_case_detail(business_profile=business, case_id=case_id)
    return JsonResponse(_serialize_case_detail(detail), status=HTTPStatus.OK)


@require_http_methods(["POST"])
def case_history_view(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    business_id = request.GET.get("business_id") or payload.get("businessId")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    summary = (payload.get("summary") or "").strip()
    source = (payload.get("source") or "system").lower()
    session_reference = payload.get("sessionReference")
    metadata = payload.get("metadata") or {}

    if not summary:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "History summary is required.", "field": "summary"},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        case = Case.objects.get(business_profile=business, id=case_id)
    except Case.DoesNotExist:
        return JsonResponse(
            {"error": "CASE_NOT_FOUND", "message": "Case not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    entry = add_history_entry(
        case=case,
        summary=summary,
        source=source,
        session_reference=session_reference,
        metadata=metadata,
    )

    detail = get_case_detail(business_profile=business, case_id=case_id)
    return JsonResponse(_serialize_case_detail(detail), status=HTTPStatus.CREATED)


@require_http_methods(["POST"])
def case_messages_view(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    business_id = request.GET.get("business_id") or payload.get("businessId")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    sender = (payload.get("sender") or "").lower()
    content = (payload.get("content") or "").strip()
    sender_display = payload.get("senderDisplayName") or ""
    session_reference = payload.get("sessionReference")
    content_type = payload.get("contentType") or "text"
    metadata = payload.get("metadata") or {}

    if sender not in CaseMessage.Sender.values:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Sender must be one of the supported types."},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not content:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Message content is required.", "field": "content"},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        case = Case.objects.get(business_profile=business, id=case_id)
    except Case.DoesNotExist:
        return JsonResponse(
            {"error": "CASE_NOT_FOUND", "message": "Case not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    add_case_message(
        case=case,
        sender=sender,
        content=content,
        sender_display_name=sender_display,
        session_reference=session_reference,
        content_type=content_type,
        metadata=metadata,
    )

    detail = get_case_detail(business_profile=business, case_id=case_id)
    return JsonResponse(_serialize_case_detail(detail), status=HTTPStatus.CREATED)


@require_http_methods(["POST"])
def case_notes_view(request: HttpRequest, case_id: uuid.UUID) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    business_id = request.GET.get("business_id") or payload.get("businessId")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    author_type = (payload.get("authorType") or "").lower()
    content = (payload.get("content") or "").strip()
    is_pinned = bool(payload.get("isPinned"))
    metadata = payload.get("metadata") or {}

    if not content:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Note content is required.", "field": "content"},
            status=HTTPStatus.BAD_REQUEST,
        )

    if author_type and author_type not in CustomerNoteAuthor.values:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Invalid author type supplied."},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        case = Case.objects.select_related("customer").get(business_profile=business, id=case_id)
    except Case.DoesNotExist:
        return JsonResponse(
            {"error": "CASE_NOT_FOUND", "message": "Case not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    if not case.customer:
        return JsonResponse(
            {"error": "NOTE_NOT_ALLOWED", "message": "Notes require an associated customer."},
            status=HTTPStatus.BAD_REQUEST,
        )

    add_case_note(
        case=case,
        customer=case.customer,
        author_type=author_type or CustomerNoteAuthor.AI_AGENT,
        content=content,
        is_pinned=is_pinned,
        metadata=metadata,
    )

    detail = get_case_detail(business_profile=business, case_id=case_id)
    return JsonResponse(_serialize_case_detail(detail), status=HTTPStatus.CREATED)


@require_http_methods(["GET"])
def customer_detail_view(request: HttpRequest, customer_id: uuid.UUID) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    try:
        detail = get_customer_detail(business_profile=business, customer_id=customer_id)
    except Customer.DoesNotExist:
        return JsonResponse(
            {"error": "CUSTOMER_NOT_FOUND", "message": "Customer not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    return JsonResponse(_serialize_customer_detail(detail), status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["PUT"])
def finalize_uploads(request: HttpRequest, business_id: str) -> JsonResponse:
    """Persist knowledge uploads for the final registration step."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    selected = payload.get("selected") or []
    links_payload = payload.get("links") or {}
    skip_raw = payload.get("skip")

    if not isinstance(selected, list):
        selected = []
    normalized_selected = [str(value) for value in selected if isinstance(value, str)]

    skip_requested = False
    if isinstance(skip_raw, bool):
        skip_requested = skip_raw
    elif isinstance(skip_raw, str):
        skip_requested = skip_raw.strip().lower() in {"true", "1", "yes", "on"}
    elif isinstance(skip_raw, int):
        skip_requested = skip_raw == 1

    normalized_links: dict[str, list[str]] = {}
    if isinstance(links_payload, dict):
        for key, values in links_payload.items():
            if not isinstance(key, str):
                continue
            if not isinstance(values, list):
                continue
            cleaned_values = [str(item) for item in values if isinstance(item, str)]
            if cleaned_values:
                normalized_links[key] = cleaned_values

    if skip_requested:
        normalized_selected = []
        normalized_links = {}

    try:
        result: KnowledgeUploadResult = finalize_knowledge_uploads(
            business_id=business_id,
            selected_types=normalized_selected,
            link_map=normalized_links,
            skip=skip_requested,
        )
    except KnowledgeUploadError as exc:
        return JsonResponse(
            {
                "error": "VALIDATION_ERROR",
                "message": str(exc),
                "field": getattr(exc, "field", None),
            },
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to store knowledge uploads.")
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to save uploads right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    session = result.session
    business = result.business

    user = getattr(business, "user", None)
    if user is not None:
        backend_path = settings.AUTHENTICATION_BACKENDS[0] if settings.AUTHENTICATION_BACKENDS else "django.contrib.auth.backends.ModelBackend"
        auth_login(request, user, backend=backend_path)
        request.session["auth_entrypoint"] = "app"

    redirect_url = f"/dashboard/?business_id={business.id}"

    response = {
        "uploads": [
            {
                "id": str(upload.id),
                "resourceType": (
                    (upload.metadata or {}).get("registration_material")
                    if isinstance(upload.metadata, dict)
                    else None
                ),
                "sourceType": upload.source_type,
                "category": upload.category,
                "url": upload.legacy_url,
                "status": upload.status,
                "sourceName": upload.source_name,
            }
            for upload in result.uploads
        ],
        "business": {
            "id": str(business.id),
            "status": business.status,
        },
        "session": {
            "id": str(session.id) if session else None,
            "currentStep": session.current_step if session else None,
            "stepsCompleted": session.steps_completed if session else None,
            "totalSteps": session.total_steps if session else None,
            "isComplete": session.is_complete if session else True,
        } if session else None,
        "nextStep": "complete",
        "redirectUrl": redirect_url,
    }
    return JsonResponse(response, status=HTTPStatus.OK)
