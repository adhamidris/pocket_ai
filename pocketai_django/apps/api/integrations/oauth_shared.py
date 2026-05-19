from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import timedelta
from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language, get_language_bidi, gettext as _
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.models import (
    BusinessProfile,
    IntegrationAccountAuditAction,
    IntegrationAccountStatus,
    IntegrationProvider,
    IntegrationType,
)
from apps.integrations.models import (
    IntegrationAccount,
    IntegrationAccountAuditEvent,
    IntegrationOAuthState,
    OAuthProvider,
)
from apps.accounts.oauth_helpers import (
    OAuthFlowError,
    compute_expires_at,
    exchange_authorization_code,
)


logger = logging.getLogger(__name__)

DEFAULT_STATE_TTL_MINUTES = 10
DEFAULT_OAUTH_TIMEOUT_S = 15

GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
MICROSOFT_GRAPH_ME_ENDPOINT = "https://graph.microsoft.com/v1.0/me"
SLACK_AUTH_TEST_ENDPOINT = "https://slack.com/api/auth.test"
HUBSPOT_TOKEN_INFO_ENDPOINT = "https://api.hubapi.com/oauth/v1/access-tokens"


def _sha256_hex(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return _base64url(digest)


def _popup_html(payload: dict[str, object], *, fallback_redirect: str) -> HttpResponse:
    data_json = json.dumps(payload)
    redirect_json = json.dumps(fallback_redirect)
    language_code = str(get_language() or "en")
    direction = "rtl" if get_language_bidi() else "ltr"
    popup_title = _("PocketAI OAuth")
    close_message = _("You can close this window.")
    html = f"""<!doctype html>
<html lang="{language_code}" dir="{direction}">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{popup_title}</title>
  </head>
  <body>
    <p style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; padding: 16px;">
      {close_message}
    </p>
    <script>
      (function () {{
        var payload = {data_json};
        try {{
          if (window.opener && window.opener !== window) {{
            window.opener.postMessage(payload, window.location.origin);
          }}
        }} catch (_err) {{}}

        try {{
          window.close();
        }} catch (_err) {{}}

        setTimeout(function () {{
          try {{ window.location.href = {redirect_json}; }} catch (_err) {{}}
        }}, 300);
      }})();
    </script>
  </body>
</html>"""
    return HttpResponse(html, content_type="text/html")


def _default_redirect_after() -> str:
    return str(getattr(settings, "INTEGRATIONS_DASHBOARD_URL", "/dashboard/integrations/") or "/dashboard/integrations/").strip() or "/dashboard/integrations/"


def _safe_redirect_after(request: HttpRequest, value: str | None) -> str:
    candidate = (value or "").strip()
    fallback = _default_redirect_after()
    if not candidate:
        return fallback
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _parse_json_payload(request: HttpRequest) -> tuple[dict[str, object], JsonResponse | None]:
    if not request.body:
        return {}, None
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not isinstance(payload, dict):
        return {}, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be a JSON object."},
            status=HTTPStatus.BAD_REQUEST,
        )
    return payload, None


def _resolve_business_api(
    request: HttpRequest,
    business_id: str | None,
) -> tuple[BusinessProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse(
            {"error": "UNAUTHORIZED", "message": "Login required."},
            status=HTTPStatus.UNAUTHORIZED,
        )

    candidate = str(business_id or "").strip()
    if candidate:
        try:
            business_uuid = uuid.UUID(candidate)
        except (TypeError, ValueError):
            return None, JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "business_id is invalid."},
                status=HTTPStatus.BAD_REQUEST,
            )
        business = BusinessProfile.objects.filter(id=business_uuid).first()
        if not business:
            return None, JsonResponse(
                {"error": "BUSINESS_NOT_FOUND", "message": "Business not found."},
                status=HTTPStatus.NOT_FOUND,
            )
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
        if not business:
            return None, JsonResponse(
                {"error": "BUSINESS_REQUIRED", "message": "No business profile available."},
                status=HTTPStatus.BAD_REQUEST,
            )

    if request.user.is_staff:
        return business, None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, JsonResponse(
            {"error": "FORBIDDEN", "message": "You do not have access to this business."},
            status=HTTPStatus.FORBIDDEN,
        )

    return business, None


def _normalize_native_tool_updates(
    *,
    integration_type: str,
    payload: Mapping[str, object],
) -> list[dict[str, object]]:
    updates = payload.get("updates")
    if isinstance(updates, list):
        items = [item for item in updates if isinstance(item, Mapping)]
    else:
        items = [payload]

    from apps.mcp import tools as mcp_tools

    known_tool_names = {
        str(row.get("toolName") or "").strip()
        for row in mcp_tools.get_native_integration_tools_for_type(integration_type)
        if str(row.get("toolName") or "").strip()
    }

    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        tool_name = str(item.get("toolName") or item.get("tool_name") or "").strip()
        if not tool_name or tool_name not in known_tool_names or tool_name in seen:
            continue
        if "enabled" not in item:
            continue
        enabled_raw = item.get("enabled")
        enabled = bool(enabled_raw) if isinstance(enabled_raw, bool) else str(enabled_raw).strip().lower() not in {"0", "false", "no", "off"}
        normalized.append({"toolName": tool_name, "enabled": enabled})
        seen.add(tool_name)
    return normalized


def _serialize_native_tools_for_account(
    *,
    account: IntegrationAccount,
    integration_type: str,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    from apps.mcp import tools as mcp_tools

    catalog = mcp_tools.get_native_integration_tools_for_type(integration_type)
    tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
    enabled_map = mcp_tools.get_native_tool_enabled_map_for_account(account, tool_names=tool_names)

    tools_payload: list[dict[str, object]] = []
    read_count = 0
    write_count = 0
    enabled_count = 0
    for row in catalog:
        tool_name = str(row.get("toolName") or "").strip()
        if not tool_name:
            continue
        operation_type = str(row.get("operationType") or "").strip()
        if operation_type == "read":
            read_count += 1
        elif operation_type == "write":
            write_count += 1
        enabled = bool(enabled_map.get(tool_name, True))
        if enabled:
            enabled_count += 1
        tools_payload.append(
            {
                "toolName": tool_name,
                "label": str(row.get("label") or tool_name),
                "description": str(row.get("description") or ""),
                "operationType": operation_type or "unknown",
                "enabled": enabled,
            }
        )

    summary = {
        "total": len(tools_payload),
        "enabled": enabled_count,
        "read": read_count,
        "write": write_count,
    }
    return tools_payload, summary


def _resolve_business(request: HttpRequest, business_id: str | None) -> tuple[BusinessProfile | None, HttpResponse | None]:
    if not request.user.is_authenticated:
        login_url = reverse("accounts:login")
        next_param = request.get_full_path()
        return None, redirect(f"{login_url}?{urlencode({'next': next_param})}")

    candidate = (business_id or "").strip()
    if candidate:
        try:
            import uuid
            business_uuid = uuid.UUID(candidate)
        except (TypeError, ValueError):
            return None, _popup_html(
                {"type": "integration_oauth_error", "error": "invalid_business_id"},
                fallback_redirect=_default_redirect_after(),
            )
        business = BusinessProfile.objects.filter(id=business_uuid).first()
        if not business:
            return None, _popup_html(
                {"type": "integration_oauth_error", "error": "business_not_found"},
                fallback_redirect=_default_redirect_after(),
            )
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
        if not business:
            return None, _popup_html(
                {"type": "integration_oauth_error", "error": "business_required"},
                fallback_redirect=_default_redirect_after(),
            )

    if request.user.is_staff:
        return business, None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, _popup_html(
            {"type": "integration_oauth_error", "error": "forbidden"},
            fallback_redirect=_default_redirect_after(),
        )

    return business, None


# ─────────────────────────────────────────────────────────────────────────────
# Integration type mapping
# ─────────────────────────────────────────────────────────────────────────────

_INTEGRATION_TYPE_MAP: dict[str, dict[str, str]] = {
    IntegrationType.GOOGLE_CALENDAR: {
        "provider": IntegrationProvider.GOOGLE,
        "oauth_provider_key": "google_calendar",
        "client_id_setting": "INTEGRATION_OAUTH_GOOGLE_CLIENT_ID",
        "client_secret_setting": "INTEGRATION_OAUTH_GOOGLE_CLIENT_SECRET",
        "scopes_setting": "INTEGRATION_OAUTH_GOOGLE_CALENDAR_SCOPES",
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "name": "Google Calendar",
        "profile_type": "google",
    },
    IntegrationType.GOOGLE_DRIVE: {
        "provider": IntegrationProvider.GOOGLE,
        "oauth_provider_key": "google_drive",
        "client_id_setting": "INTEGRATION_OAUTH_GOOGLE_CLIENT_ID",
        "client_secret_setting": "INTEGRATION_OAUTH_GOOGLE_CLIENT_SECRET",
        "scopes_setting": "INTEGRATION_OAUTH_GOOGLE_DRIVE_SCOPES",
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "name": "Google Drive",
        "profile_type": "google",
    },
    IntegrationType.ONEDRIVE: {
        "provider": IntegrationProvider.MICROSOFT,
        "oauth_provider_key": "microsoft_drive",
        "client_id_setting": "INTEGRATION_OAUTH_MICROSOFT_CLIENT_ID",
        "client_secret_setting": "INTEGRATION_OAUTH_MICROSOFT_CLIENT_SECRET",
        "scopes_setting": "INTEGRATION_OAUTH_MICROSOFT_DRIVE_SCOPES",
        "authorization_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "name": "OneDrive",
        "profile_type": "microsoft",
    },
    IntegrationType.SLACK: {
        "provider": IntegrationProvider.SLACK,
        "oauth_provider_key": "slack_native",
        "client_id_setting": "INTEGRATION_OAUTH_SLACK_CLIENT_ID",
        "client_secret_setting": "INTEGRATION_OAUTH_SLACK_CLIENT_SECRET",
        "scopes_setting": "INTEGRATION_OAUTH_SLACK_SCOPES",
        "authorization_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "name": "Slack",
        "profile_type": "slack",
    },
    IntegrationType.HUBSPOT: {
        "provider": IntegrationProvider.HUBSPOT,
        "oauth_provider_key": "hubspot",
        "client_id_setting": "INTEGRATION_OAUTH_HUBSPOT_CLIENT_ID",
        "client_secret_setting": "INTEGRATION_OAUTH_HUBSPOT_CLIENT_SECRET",
        "scopes_setting": "INTEGRATION_OAUTH_HUBSPOT_SCOPES",
        "authorization_url": "https://app.hubspot.com/oauth/authorize",
        "token_url": "https://api.hubapi.com/oauth/v1/token",
        "name": "HubSpot",
        "profile_type": "hubspot",
    },
}


def _get_type_config(integration_type: str) -> dict[str, str] | None:
    normalized = str(integration_type or "").strip().lower()
    return _INTEGRATION_TYPE_MAP.get(normalized)


def _bootstrap_integration_oauth_provider(config: dict[str, str]) -> OAuthProvider | None:
    key = config["oauth_provider_key"]
    client_id = str(getattr(settings, config["client_id_setting"], "") or "").strip()
    client_secret = str(getattr(settings, config["client_secret_setting"], "") or "").strip()
    scopes = list(getattr(settings, config["scopes_setting"], []) or [])
    authorization_url = config["authorization_url"]
    token_url = config["token_url"]
    name = config["name"]

    if not client_id or not client_secret:
        return None
    if not isinstance(scopes, list) or not any(str(scope or "").strip() for scope in scopes):
        return None

    existing = OAuthProvider.objects.filter(key=key).first()
    if existing and not existing.is_active:
        return None

    provider = existing or OAuthProvider(
        key=key,
        name=name,
        authorization_url=authorization_url,
        token_url=token_url,
        client_id=client_id,
        scopes=scopes,
        marketplace_keys=[],
        is_active=True,
    )

    if not provider.name:
        provider.name = name
    if not str(getattr(provider, "authorization_url", "") or "").strip():
        provider.authorization_url = authorization_url
    if not str(getattr(provider, "token_url", "") or "").strip():
        provider.token_url = token_url
    if not str(getattr(provider, "client_id", "") or "").strip():
        provider.client_id = client_id
    if not isinstance(getattr(provider, "scopes", None), list) or not provider.scopes:
        provider.scopes = scopes
    if provider.get_client_secret() == "":
        provider.set_client_secret(client_secret)
    provider.is_active = True
    provider.save()
    return provider


# ─────────────────────────────────────────────────────────────────────────────
# Profile fetchers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_google_profile(access_token: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(GOOGLE_USERINFO_ENDPOINT, headers=headers, timeout=DEFAULT_OAUTH_TIMEOUT_S)
    except requests.RequestException as exc:
        raise OAuthFlowError(f"Failed to reach Google userinfo endpoint: {exc.__class__.__name__}") from exc
    if not response.ok:
        raise OAuthFlowError(f"Google userinfo request failed ({response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthFlowError("Google userinfo returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise OAuthFlowError("Google userinfo returned an unexpected response.")
    return {
        "identifier": str(payload.get("email") or "").strip(),
        "external_id": str(payload.get("sub") or payload.get("id") or "").strip(),
        "display_name": str(payload.get("name") or "").strip(),
    }


def _fetch_microsoft_profile(access_token: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(MICROSOFT_GRAPH_ME_ENDPOINT, headers=headers, timeout=DEFAULT_OAUTH_TIMEOUT_S)
    except requests.RequestException as exc:
        raise OAuthFlowError(f"Failed to reach Microsoft Graph: {exc.__class__.__name__}") from exc
    if not response.ok:
        raise OAuthFlowError(f"Microsoft Graph /me request failed ({response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthFlowError("Microsoft Graph returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise OAuthFlowError("Microsoft Graph returned an unexpected response.")
    return {
        "identifier": str(payload.get("mail") or payload.get("userPrincipalName") or "").strip(),
        "external_id": str(payload.get("id") or "").strip(),
        "display_name": str(payload.get("displayName") or "").strip(),
    }


def _fetch_slack_profile(access_token: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(SLACK_AUTH_TEST_ENDPOINT, headers=headers, timeout=DEFAULT_OAUTH_TIMEOUT_S)
    except requests.RequestException as exc:
        raise OAuthFlowError(f"Failed to reach Slack auth.test: {exc.__class__.__name__}") from exc
    if not response.ok:
        raise OAuthFlowError(f"Slack auth.test request failed ({response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthFlowError("Slack auth.test returned invalid JSON.") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise OAuthFlowError(f"Slack auth.test failed: {payload.get('error', 'unknown')}")
    return {
        "identifier": str(payload.get("team") or "").strip(),
        "external_id": str(payload.get("team_id") or "").strip(),
        "display_name": str(payload.get("user") or "").strip(),
    }


def _fetch_hubspot_profile(access_token: str) -> dict[str, str]:
    try:
        response = requests.get(
            f"{HUBSPOT_TOKEN_INFO_ENDPOINT}/{access_token}",
            timeout=DEFAULT_OAUTH_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise OAuthFlowError(f"Failed to reach HubSpot token info: {exc.__class__.__name__}") from exc
    if not response.ok:
        raise OAuthFlowError(f"HubSpot token info request failed ({response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthFlowError("HubSpot token info returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise OAuthFlowError("HubSpot token info returned an unexpected response.")
    return {
        "identifier": str(payload.get("user") or payload.get("hub_domain") or "").strip(),
        "external_id": str(payload.get("hub_id") or "").strip(),
        "display_name": str(payload.get("hub_domain") or "").strip(),
    }


_PROFILE_FETCHERS = {
    "google": _fetch_google_profile,
    "microsoft": _fetch_microsoft_profile,
    "slack": _fetch_slack_profile,
    "hubspot": _fetch_hubspot_profile,
}


__all__ = [name for name in globals() if not name.startswith("__")]

