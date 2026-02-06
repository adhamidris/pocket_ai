from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.models import BusinessProfile
from apps.voice.deepgram_stt import DeepgramConfig
from apps.voice.elevenlabs_tts import ElevenLabsConfig
from apps.voice.models import VoiceProviderConnection
from apps.voice.provider_credentials import (
    VOICE_PROVIDER_DEEPGRAM,
    VOICE_PROVIDER_ELEVENLABS,
    VOICE_PROVIDER_ORDER,
    VOICE_PROVIDER_TENANT_MANAGED,
    VOICE_PROVIDER_TWILIO,
    extract_safe_provider_settings,
    is_tenant_managed_provider,
    mask_provider_credentials,
)


VOICE_PROVIDER_LABELS = {
    VOICE_PROVIDER_TWILIO: "Twilio",
    VOICE_PROVIDER_DEEPGRAM: "Deepgram",
    VOICE_PROVIDER_ELEVENLABS: "ElevenLabs",
}

VOICE_PROVIDER_DESCRIPTIONS = {
    VOICE_PROVIDER_TWILIO: "Call transport, webhooks, and recording callbacks.",
    VOICE_PROVIDER_DEEPGRAM: "Live speech-to-text for caller audio.",
    VOICE_PROVIDER_ELEVENLABS: "Low-latency text-to-speech voice responses.",
}

VOICE_PROVIDER_REQUIRED_FIELDS = {
    VOICE_PROVIDER_TWILIO: ("account_sid", "auth_token", "webhook_base_url", "from_number"),
}

VOICE_PROVIDER_ALLOWED_FIELDS = {
    VOICE_PROVIDER_TWILIO: {"account_sid", "auth_token", "webhook_base_url", "from_number"},
}

PLATFORM_MANAGED_MESSAGE = (
    "This provider is platform-managed and not configurable per workspace."
)


def _parse_json_body(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not isinstance(payload, dict):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be a JSON object."},
            status=HTTPStatus.BAD_REQUEST,
        )
    return payload, None


def _resolve_business_for_request(
    request: HttpRequest,
    business_id: str | None,
) -> tuple[BusinessProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse(
            {"error": "UNAUTHORIZED", "message": "Login required."},
            status=HTTPStatus.UNAUTHORIZED,
        )

    candidate = business_id or request.headers.get("X-Business-Id") or request.META.get("HTTP_X_BUSINESS_ID")
    if candidate:
        try:
            business_uuid = candidate if isinstance(candidate, uuid.UUID) else uuid.UUID(str(candidate))
        except (TypeError, ValueError):
            return None, JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "business_id must be a valid UUID."},
                status=HTTPStatus.BAD_REQUEST,
            )
        try:
            business = BusinessProfile.objects.get(id=business_uuid)
        except BusinessProfile.DoesNotExist:
            return None, JsonResponse(
                {"error": "BUSINESS_NOT_FOUND", "message": "Business profile not found."},
                status=HTTPStatus.NOT_FOUND,
            )
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
        if not business:
            return None, JsonResponse(
                {"error": "BUSINESS_REQUIRED", "message": "A business_id is required to perform this action."},
                status=HTTPStatus.BAD_REQUEST,
            )

    if request.user.is_staff:
        return business, None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, JsonResponse(
            {"error": "FORBIDDEN", "message": "You do not have access to this business profile."},
            status=HTTPStatus.FORBIDDEN,
        )
    return business, None


def _normalize_provider(value: str) -> str:
    return str(value or "").strip().lower()


def _is_supported_provider(provider: str) -> bool:
    return provider in VOICE_PROVIDER_ORDER


def _merge_credentials(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, raw_value in incoming.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if raw_value is None:
            merged.pop(key_text, None)
            continue
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value:
                merged[key_text] = value
            else:
                merged.pop(key_text, None)
            continue
        merged[key_text] = raw_value
    return merged


def _normalize_credentials(provider: str, credentials: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    allowed = VOICE_PROVIDER_ALLOWED_FIELDS.get(provider, set())
    normalized: dict[str, Any] = {}
    for key in allowed:
        raw_value = credentials.get(key)
        if raw_value is None:
            continue
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value:
                normalized[key] = value
            continue
        normalized[key] = raw_value

    if provider == VOICE_PROVIDER_TWILIO:
        webhook = str(normalized.get("webhook_base_url") or "").strip().rstrip("/")
        if webhook:
            parsed = urlsplit(webhook)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return {}, "Twilio webhook_base_url must be a valid http(s) URL."
            normalized["webhook_base_url"] = webhook

    return normalized, None


def _missing_required(provider: str, credentials: Mapping[str, Any]) -> list[str]:
    required = list(VOICE_PROVIDER_REQUIRED_FIELDS.get(provider, ()))
    return [field for field in required if not str(credentials.get(field) or "").strip()]


def _platform_provider_snapshot(provider: str) -> dict[str, Any]:
    if provider == VOICE_PROVIDER_DEEPGRAM:
        try:
            cfg = DeepgramConfig.from_env(language="en")
            return {
                "configured": True,
                "status": "connected",
                "error": "",
                "publicSettings": {
                    "model": cfg.model,
                    "endpointing_ms": cfg.endpointing_ms,
                },
                "maskedSecrets": {"api_key": "configured"},
            }
        except Exception as exc:
            return {
                "configured": False,
                "status": "not_configured",
                "error": str(exc),
                "publicSettings": {},
                "maskedSecrets": {},
            }

    if provider == VOICE_PROVIDER_ELEVENLABS:
        try:
            cfg = ElevenLabsConfig.from_env(language="en")
            return {
                "configured": True,
                "status": "connected",
                "error": "",
                "publicSettings": {
                    "voice_id": cfg.voice_id,
                    "model_id": cfg.model_id,
                    "output_format": cfg.output_format,
                },
                "maskedSecrets": {"api_key": "configured"},
            }
        except Exception as exc:
            return {
                "configured": False,
                "status": "not_configured",
                "error": str(exc),
                "publicSettings": {},
                "maskedSecrets": {},
            }

    return {
        "configured": False,
        "status": "not_configured",
        "error": "",
        "publicSettings": {},
        "maskedSecrets": {},
    }


def _serialize_provider(
    *,
    provider: str,
    connection: VoiceProviderConnection | None,
) -> dict[str, Any]:
    if not is_tenant_managed_provider(provider):
        snapshot = _platform_provider_snapshot(provider)
        return {
            "provider": provider,
            "label": VOICE_PROVIDER_LABELS.get(provider, provider.title()),
            "description": VOICE_PROVIDER_DESCRIPTIONS.get(provider, ""),
            "managementMode": "platform",
            "editable": False,
            "status": snapshot.get("status") or "not_configured",
            "enabled": bool(snapshot.get("configured")),
            "hasCredentials": bool(snapshot.get("configured")),
            "lastTestedAt": None,
            "lastError": str(snapshot.get("error") or ""),
            "requiredFields": [],
            "publicSettings": dict(snapshot.get("publicSettings") or {}),
            "maskedSecrets": dict(snapshot.get("maskedSecrets") or {}),
        }

    creds = connection.credentials if connection else {}
    has_credentials = bool(connection and connection.has_credentials())
    enabled = bool(connection and connection.enabled)
    status = "not_configured"
    if connection and not has_credentials:
        status = "not_configured"
    elif connection and has_credentials and not enabled:
        status = "disabled"
    elif connection and has_credentials and str(connection.last_error or "").strip():
        status = "error"
    elif connection and has_credentials and enabled:
        status = "connected"

    return {
        "provider": provider,
        "label": VOICE_PROVIDER_LABELS.get(provider, provider.title()),
        "description": VOICE_PROVIDER_DESCRIPTIONS.get(provider, ""),
        "managementMode": "tenant",
        "editable": True,
        "status": status,
        "enabled": enabled,
        "hasCredentials": has_credentials,
        "lastTestedAt": connection.last_tested_at.isoformat() if connection and connection.last_tested_at else None,
        "lastError": str(connection.last_error or "") if connection else "",
        "requiredFields": list(VOICE_PROVIDER_REQUIRED_FIELDS.get(provider, ())),
        "publicSettings": extract_safe_provider_settings(provider, creds),
        "maskedSecrets": mask_provider_credentials(provider, creds),
    }


def _run_provider_test(provider: str, credentials: Mapping[str, Any]) -> tuple[bool, str]:
    timeout = 20

    if provider == VOICE_PROVIDER_TWILIO:
        account_sid = str(credentials.get("account_sid") or "").strip()
        auth_token = str(credentials.get("auth_token") or "").strip()
        if not account_sid or not auth_token:
            return False, "Twilio account_sid and auth_token are required."
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json"
        response = requests.get(url, auth=(account_sid, auth_token), timeout=timeout)
        if response.status_code >= 400:
            return False, f"Twilio test failed ({response.status_code})."
        return True, "Twilio connection validated."

    return False, "Unsupported provider."


@csrf_protect
@require_http_methods(["GET"])
def voice_providers_collection(request: HttpRequest) -> JsonResponse:
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    with tenant_context(business.id):
        connections = {
            conn.provider: conn
            for conn in VoiceProviderConnection.objects.filter(
                business_profile=business,
                provider__in=VOICE_PROVIDER_TENANT_MANAGED,
            ).order_by("provider")
        }
        providers = [
            _serialize_provider(provider=provider, connection=connections.get(provider))
            for provider in VOICE_PROVIDER_ORDER
        ]

    return JsonResponse(
        {
            "businessId": str(business.id),
            "dashboardUrl": "/dashboard/voice/",
            "providers": providers,
        },
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET", "PUT", "DELETE"])
def voice_provider_detail(request: HttpRequest, provider: str) -> JsonResponse:
    provider_key = _normalize_provider(provider)
    if not _is_supported_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Unsupported voice provider."},
            status=HTTPStatus.BAD_REQUEST,
        )

    payload: dict[str, Any] | None = None
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    if request.method in {"PUT", "DELETE"}:
        payload, parse_error = _parse_json_body(request)
        if parse_error:
            return parse_error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method in {"PUT", "DELETE"} and not is_tenant_managed_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": PLATFORM_MANAGED_MESSAGE},
            status=HTTPStatus.BAD_REQUEST,
        )

    with tenant_context(business.id):
        connection = VoiceProviderConnection.objects.filter(
            business_profile=business,
            provider=provider_key,
        ).first()

        if request.method == "GET":
            return JsonResponse(
                {"provider": _serialize_provider(provider=provider_key, connection=connection)},
                status=HTTPStatus.OK,
            )

        if request.method == "DELETE":
            if connection:
                connection.enabled = False
                connection.clear_credentials()
                connection.last_error = ""
                connection.save(
                    update_fields=[
                        "enabled",
                        "credentials_encrypted",
                        "credentials_key_version",
                        "credentials_last_rotated_at",
                        "credential_error_count",
                        "last_error",
                        "updated_at",
                    ]
                )
            return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

        assert payload is not None
        if connection is None:
            connection = VoiceProviderConnection(
                business_profile=business,
                provider=provider_key,
                created_by=request.user,
            )

        credentials_payload = payload.get("credentials")
        if credentials_payload is not None and not isinstance(credentials_payload, dict):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "credentials must be a JSON object."},
                status=HTTPStatus.BAD_REQUEST,
            )

        credentials_supplied = isinstance(credentials_payload, dict)
        clear_credentials = bool(payload.get("clearCredentials"))

        current_credentials = connection.credentials if connection.pk else {}
        next_credentials = dict(current_credentials or {})
        if clear_credentials:
            next_credentials = {}
        elif credentials_supplied:
            next_credentials = _merge_credentials(next_credentials, credentials_payload or {})

        normalized_credentials, normalize_error = _normalize_credentials(
            provider_key,
            next_credentials,
        )
        if normalize_error:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": normalize_error},
                status=HTTPStatus.BAD_REQUEST,
            )

        enabled_payload = payload.get("enabled")
        if enabled_payload is None:
            next_enabled = connection.enabled if connection.pk else bool(normalized_credentials)
        else:
            next_enabled = bool(enabled_payload)

        if next_enabled:
            missing = _missing_required(provider_key, normalized_credentials)
            if missing:
                return JsonResponse(
                    {
                        "error": "VALIDATION_ERROR",
                        "message": f"Missing required fields for {VOICE_PROVIDER_LABELS.get(provider_key, provider_key)}: {', '.join(missing)}",
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )

        if credentials_supplied or clear_credentials:
            connection.credentials = normalized_credentials
        connection.enabled = next_enabled
        connection.last_error = ""
        connection.save()

        return JsonResponse(
            {"provider": _serialize_provider(provider=provider_key, connection=connection)},
            status=HTTPStatus.OK,
        )


@csrf_protect
@require_http_methods(["POST"])
def voice_provider_test(request: HttpRequest, provider: str) -> JsonResponse:
    provider_key = _normalize_provider(provider)
    if not _is_supported_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Unsupported voice provider."},
            status=HTTPStatus.BAD_REQUEST,
        )

    payload, parse_error = _parse_json_body(request)
    if parse_error:
        return parse_error

    business_param = (
        (payload or {}).get("businessId")
        or (payload or {}).get("business_id")
        or request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    if not is_tenant_managed_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": PLATFORM_MANAGED_MESSAGE},
            status=HTTPStatus.BAD_REQUEST,
        )

    with tenant_context(business.id):
        connection = VoiceProviderConnection.objects.filter(
            business_profile=business,
            provider=provider_key,
        ).first()
        if not connection or not connection.has_credentials():
            return JsonResponse(
                {"error": "NOT_CONFIGURED", "message": "Provider credentials are not configured yet."},
                status=HTTPStatus.BAD_REQUEST,
            )

        credentials = connection.credentials or {}

        try:
            ok, message = _run_provider_test(provider_key, credentials)
        except Exception as exc:
            ok = False
            message = str(exc) or "Provider test failed."

        connection.last_tested_at = timezone.now()
        connection.last_error = "" if ok else message
        connection.save(update_fields=["last_tested_at", "last_error", "updated_at"])

        response_payload = {
            "ok": ok,
            "message": message,
            "provider": _serialize_provider(provider=provider_key, connection=connection),
        }
        status_code = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
        return JsonResponse(response_payload, status=status_code)
