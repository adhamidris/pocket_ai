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

from apps.accounts.models import BusinessProfile
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


@csrf_protect
@require_http_methods(["PUT"])
def configure_agent(request: HttpRequest, business_id: str) -> JsonResponse:
    """Persist the default assistant basics for the given business profile."""
    if not request.user.is_authenticated:
        return JsonResponse(
            {"error": "UNAUTHORIZED", "message": "Login required."},
            status=HTTPStatus.UNAUTHORIZED,
        )

    business = None
    try:
        business_uuid = business_id if isinstance(business_id, uuid.UUID) else uuid.UUID(str(business_id))
        business = BusinessProfile.objects.filter(id=business_uuid).first()
    except (TypeError, ValueError):
        business = None

    if business is None:
        return JsonResponse(
            {"error": "BUSINESS_NOT_FOUND", "message": "Business profile not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    if not (request.user.is_staff or request.user.business_profiles.filter(id=business.id).exists()):
        return JsonResponse(
            {"error": "FORBIDDEN", "message": "You do not have access to this business profile."},
            status=HTTPStatus.FORBIDDEN,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    agent_name = str(payload.get("agentName") or "").strip()
    agent_tone = str(payload.get("agentTone") or "").strip()

    try:
        result: AgentProfileResult = configure_agent_profile(
            business_id=str(business.id),
            name=agent_name,
            tone=agent_tone,
        )
    except AgentProfileError as exc:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to configure default assistant.")
        return JsonResponse(
            {"error": "SERVER_ERROR", "message": "Unable to save the default assistant right now."},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    profile = result.profile
    session = result.session

    response = {
        "agent": {
            "id": str(profile.id),
            "name": profile.name,
            "tone": profile.tone,
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
