from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse

from apps.accounts.models import BusinessProfile
from apps.crm.flags import crm_v1_enabled


def _json_error(error: str, message: str, *, status: HTTPStatus, details: dict[str, Any] | None = None) -> JsonResponse:
    payload: dict[str, Any] = {"error": error, "message": message}
    if details:
        payload["details"] = details
    return JsonResponse(payload, status=status)


def crm_legacy_cases_retired(_request: HttpRequest) -> JsonResponse:
    return _json_error(
        "FEATURE_DISABLED",
        "The legacy CRM cases runtime is retired.",
        status=HTTPStatus.NOT_FOUND,
    )


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
