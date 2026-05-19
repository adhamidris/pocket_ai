from __future__ import annotations

import json
import logging
import secrets
import uuid
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.accounts.models import (
    BusinessProfile,
    IntegrationCredentialEventType,
    IntegrationSyncFrequency,
    KnowledgeIntegrationStatus,
    KnowledgeIntegrationType,
    KnowledgeVisibility,
)
from apps.api.shared import _iso, _resolve_business_profile
from apps.integrations.google_drive import (
    GoogleOAuthError,
    GoogleSheetsDiscoveryError,
    build_google_authorization_url,
    discover_google_sheet_resources,
    exchange_google_authorization_code,
    fetch_google_account_profile,
)
from apps.integrations.integration_sync import IntegrationSyncError, IntegrationSyncService
from apps.integrations.models import KnowledgeIntegration

logger = logging.getLogger(__name__)


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
