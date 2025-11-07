from __future__ import annotations

import json
import logging
import uuid
from http import HTTPStatus

from django.conf import settings
from django.contrib.auth import login as auth_login
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.accounts.models import AgentProfile, BusinessProfile, KnowledgeUpload
from apps.cases.models import Case, CaseMessage, CasePriority, CaseStatus
from apps.customers.models import Customer, CustomerNoteAuthor
from apps.services.action_controls import list_action_settings, set_action_setting
from apps.services.agents import (
    AgentListValidationError,
    agent_identifier,
    display_role_label,
    display_tone_label,
    get_agent_detail,
    list_agents,
)
from apps.services.cases import (
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
from apps.services.customers import CustomerDetail as CustomerDetailData, CustomerSummary, get_customer_detail
from apps.services.documents import (
    CsvPreviewError,
    DocumentDetail,
    DocumentListItem,
    DocumentListValidationError as KnowledgeDocumentListValidationError,
    DocumentScrapeError,
    get_document_detail as get_knowledge_document_detail,
    list_documents as list_knowledge_documents,
    preview_csv_upload,
    scrape_document_source,
)
from apps.services.registration import (
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
        "tags": list(item.tags),
        "collections": list(item.collections),
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


def _serialize_document_detail(detail: DocumentDetail) -> dict:
    payload = {
        "summary": _serialize_document_summary(detail.summary),
        "description": detail.description,
        "summaryText": detail.summary_text,
        "metadata": detail.metadata,
        "ingestionMetadata": detail.ingestion_metadata,
        "retentionPolicy": detail.retention_policy,
        "createdByAgent": detail.created_by_agent,
    }
    if detail.file_detail:
        payload["file"] = {
            "filename": detail.file_detail.filename,
            "contentType": detail.file_detail.content_type,
            "sizeBytes": detail.file_detail.size_bytes,
            "pageCount": detail.file_detail.page_count,
        }
    if detail.url_detail:
        payload["url"] = {
            "url": detail.url_detail.url,
            "host": detail.url_detail.host,
        }
    if detail.text_detail:
        payload["text"] = {
            "characters": detail.text_detail.characters,
            "preview": detail.text_detail.preview,
        }
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
        payload = {
            "error": "VALIDATION_ERROR",
            "message": str(exc),
        }
        if exc.field:
            payload["field"] = exc.field
        return JsonResponse(payload, status=HTTPStatus.BAD_REQUEST)

    response = {
        "items": [_serialize_document_summary(item) for item in result.items],
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def knowledge_document_detail(request: HttpRequest, document_id: uuid.UUID) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    try:
        detail = get_knowledge_document_detail(business_profile=business, document_id=document_id)
    except KnowledgeUpload.DoesNotExist:
        return JsonResponse(
            {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    return JsonResponse({"document": _serialize_document_detail(detail)}, status=HTTPStatus.OK)


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
                "resourceType": upload.resource_type,
                "url": upload.url,
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
            "id": str(session.id),
            "currentStep": session.current_step,
            "stepsCompleted": session.steps_completed,
            "totalSteps": session.total_steps,
            "isComplete": session.is_complete,
        },
        "nextStep": "complete",
        "redirectUrl": redirect_url,
    }
    return JsonResponse(response, status=HTTPStatus.OK)
