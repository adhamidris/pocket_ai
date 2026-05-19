from __future__ import annotations

import uuid
from http import HTTPStatus

from django.http import HttpRequest, JsonResponse

from apps.accounts.models import BusinessProfile


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


def _iso(dt):
    return dt.isoformat() if dt else None