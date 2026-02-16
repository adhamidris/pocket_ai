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


# ─────────────────────────────────────────────────────────────────────────────
# OAuth start / callback views
# ─────────────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def integration_oauth_start(request: HttpRequest, integration_type: str) -> HttpResponse:
    """Start OAuth flow for a native integration type."""

    config = _get_type_config(integration_type)
    if not config:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "unknown_integration_type", "integration_type": integration_type},
            fallback_redirect=_default_redirect_after(),
        )

    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business, error = _resolve_business(request, business_param)
    if error:
        return error
    assert business is not None

    oauth_provider_key = config["oauth_provider_key"]
    provider = OAuthProvider.objects.filter(key=oauth_provider_key, is_active=True).first()
    if not provider:
        provider = _bootstrap_integration_oauth_provider(config)
    if not provider:
        return _popup_html(
            {
                "type": "integration_oauth_error",
                "error": "provider_not_configured",
                "integration_type": integration_type,
                "hint": f"Set {config['client_id_setting']}/{config['client_secret_setting']} (or configure OAuthProvider in admin).",
            },
            fallback_redirect=_default_redirect_after(),
        )

    scopes = provider.scopes if isinstance(provider.scopes, list) else []
    if not scopes:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "provider_missing_scopes", "integration_type": integration_type},
            fallback_redirect=_default_redirect_after(),
        )

    redirect_after = _safe_redirect_after(request, request.GET.get("redirect"))

    state_token = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(code_verifier)

    callback_path = reverse(
        "api:integration_oauth_callback",
        kwargs={"integration_type": str(integration_type).strip().lower()},
    )

    base_url = str(getattr(settings, "API_PUBLIC_URL", "") or "").strip()
    if base_url:
        callback_url = f"{base_url.rstrip('/')}{callback_path}"
    else:
        callback_url = request.build_absolute_uri(callback_path)

    IntegrationOAuthState.objects.create(
        business_profile=business,
        user=request.user,
        provider=provider,
        integration_type=integration_type,
        state_token=state_token,
        redirect_after=redirect_after,
        redirect_uri=callback_url,
        code_verifier=code_verifier,
        expires_at=timezone.now() + timedelta(minutes=DEFAULT_STATE_TTL_MINUTES),
    )

    params: dict[str, str] = {
        "client_id": provider.client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": " ".join([str(scope).strip() for scope in scopes if str(scope).strip()]),
        "state": state_token,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    profile_type = config.get("profile_type", "")
    if profile_type == "google":
        params.update({
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        })
    elif profile_type == "microsoft":
        params["response_mode"] = "query"
    elif profile_type == "slack":
        # Slack uses user_scope for user tokens
        params.pop("scope", None)
        params["user_scope"] = " ".join([str(scope).strip() for scope in scopes if str(scope).strip()])

    auth_url = f"{provider.authorization_url}?{urlencode(params)}"
    return redirect(auth_url)


@require_http_methods(["GET"])
def integration_oauth_callback(request: HttpRequest, integration_type: str) -> HttpResponse:
    """OAuth callback for native integrations."""

    config = _get_type_config(integration_type)
    if not config:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "unknown_integration_type", "integration_type": integration_type},
            fallback_redirect=_default_redirect_after(),
        )

    redirect_fallback = _default_redirect_after()

    error_code = request.GET.get("error")
    if error_code:
        description = request.GET.get("error_description") or error_code
        return _popup_html(
            {"type": "integration_oauth_error", "error": str(error_code), "detail": str(description)[:200]},
            fallback_redirect=redirect_fallback,
        )

    code = request.GET.get("code")
    state_token = request.GET.get("state")
    if not code or not state_token:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "missing_params"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state = (
        IntegrationOAuthState.objects.select_related("provider", "business_profile", "user")
        .filter(
            state_token=state_token,
            integration_type=integration_type,
            provider__key=config["oauth_provider_key"],
            is_used=False,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not oauth_state:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "invalid_state"},
            fallback_redirect=redirect_fallback,
        )

    if request.user.is_authenticated and not request.user.is_staff and request.user.id != oauth_state.user_id:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "forbidden"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state.is_used = True
    oauth_state.save(update_fields=["is_used"])

    provider = oauth_state.provider
    business = oauth_state.business_profile

    scope_param = None
    profile_type = config.get("profile_type", "")
    if profile_type in {"microsoft", "hubspot"}:
        scopes = provider.scopes if isinstance(provider.scopes, list) else []
        scope_param = " ".join([str(scope).strip() for scope in scopes if str(scope).strip()]) or None

    try:
        token_payload = exchange_authorization_code(
            provider,
            code=str(code),
            redirect_uri=oauth_state.redirect_uri or "",
            scope=scope_param,
            code_verifier=oauth_state.code_verifier or None,
        )
    except OAuthFlowError as exc:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "token_exchange_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    # Slack v2 returns tokens in authed_user sub-object
    if profile_type == "slack" and isinstance(token_payload.get("authed_user"), dict):
        token_payload = {**token_payload, **token_payload["authed_user"]}

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "no_access_token"},
            fallback_redirect=redirect_fallback,
        )

    expires_at = compute_expires_at(token_payload)
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    token_type = str(token_payload.get("token_type") or token_payload.get("tokenType") or "Bearer").strip() or "Bearer"

    fetcher = _PROFILE_FETCHERS.get(profile_type)
    if not fetcher:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "no_profile_fetcher"},
            fallback_redirect=redirect_fallback,
        )

    try:
        profile = fetcher(access_token)
    except OAuthFlowError as exc:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "profile_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    identifier = profile.get("identifier", "")
    external_id = profile.get("external_id", "")
    display_name = profile.get("display_name", "")

    created = False
    with tenant_context(business.id):
        account = IntegrationAccount.objects.filter(
            business_profile=business,
            user=oauth_state.user,
            integration_type=integration_type,
        ).first()
        if not account:
            account = IntegrationAccount(
                business_profile=business,
                user=oauth_state.user,
                integration_type=integration_type,
                provider=config["provider"],
            )
            created = True
        account.provider = config["provider"]
        account.account_identifier = identifier
        if external_id:
            account.external_account_id = external_id
        account.status = IntegrationAccountStatus.CONNECTED
        account.last_error = ""

        credentials = dict(account.credentials or {})
        existing_refresh = str(credentials.get("refresh_token") or "").strip() or None
        credentials["access_token"] = access_token
        if refresh_token or existing_refresh:
            credentials["refresh_token"] = refresh_token or existing_refresh
        if expires_at is not None:
            credentials["expires_at"] = expires_at.isoformat()
        if token_type:
            credentials["token_type"] = token_type
        scope_value = token_payload.get("scope")
        if scope_value:
            credentials["scope"] = scope_value
        credentials["provider"] = config["provider"]
        credentials["updated_at"] = timezone.now().isoformat()
        account.credentials = credentials

        meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        meta["profile"] = {
            "identifier": identifier,
            "name": display_name,
            "provider": config["provider"],
            "linked_at": timezone.now().isoformat(),
        }
        account.metadata = meta

        account.save()

        IntegrationAccountAuditEvent.objects.create(
            business_profile=business,
            integration_account=account,
            integration_account_id_snapshot=account.id,
            actor_user=oauth_state.user,
            action=IntegrationAccountAuditAction.CONNECTED if created else IntegrationAccountAuditAction.UPDATED,
            description=f"{config['name']} integration connected via OAuth.",
            metadata={
                "integration_type": integration_type,
                "provider": config["provider"],
                "identifier_sha256": _sha256_hex(identifier),
            },
        )

    redirect_after = oauth_state.redirect_after or redirect_fallback
    return _popup_html(
        {
            "type": "integration_oauth_success",
            "integration_type": integration_type,
            "provider": config["provider"],
            "identifier": identifier,
        },
        fallback_redirect=redirect_after,
    )


@csrf_protect
@require_http_methods(["POST"])
def integration_oauth_disconnect(request: HttpRequest, integration_type: str) -> JsonResponse:
    """Disconnect a native OAuth integration account for the current business + actor."""

    config = _get_type_config(integration_type)
    if not config:
        return JsonResponse(
            {"error": "UNKNOWN_INTEGRATION_TYPE", "message": "Unknown integration type."},
            status=HTTPStatus.NOT_FOUND,
        )

    payload, payload_error = _parse_json_payload(request)
    if payload_error:
        return payload_error

    business_param = (
        payload.get("businessId")
        or payload.get("business_id")
        or request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    business, business_error = _resolve_business_api(request, str(business_param or ""))
    if business_error:
        return business_error
    assert business is not None

    normalized_type = str(integration_type or "").strip().lower()

    with tenant_context(business.id):
        account_qs = IntegrationAccount.objects.filter(
            business_profile=business,
            integration_type=normalized_type,
            user=request.user,
        )
        account = account_qs.order_by("-updated_at").first()
        if not account:
            return JsonResponse(
                {
                    "error": "INTEGRATION_ACCOUNT_NOT_FOUND",
                    "message": "No integration account found for this provider.",
                },
                status=HTTPStatus.NOT_FOUND,
            )

        identifier = str(account.account_identifier or "")
        account.status = IntegrationAccountStatus.DISCONNECTED
        account.last_error = ""
        account.credentials = {}

        metadata = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        profile_meta = metadata.get("profile")
        if isinstance(profile_meta, dict):
            profile_meta["disconnected_at"] = timezone.now().isoformat()
            metadata["profile"] = profile_meta
        else:
            metadata["disconnected_at"] = timezone.now().isoformat()
        account.metadata = metadata

        account.save(
            update_fields=[
                "status",
                "last_error",
                "metadata",
                "credentials_encrypted",
                "credentials_key_version",
                "credentials_last_rotated_at",
                "credential_error_count",
                "updated_at",
            ]
        )

        IntegrationAccountAuditEvent.objects.create(
            business_profile=business,
            integration_account=account,
            integration_account_id_snapshot=account.id,
            actor_user=request.user if request.user.is_authenticated else None,
            action=IntegrationAccountAuditAction.DISCONNECTED,
            description=f"{config['name']} integration disconnected.",
            metadata={
                "integration_type": normalized_type,
                "provider": config["provider"],
                "identifier_sha256": _sha256_hex(identifier),
            },
        )

    return JsonResponse(
        {
            "status": IntegrationAccountStatus.DISCONNECTED,
            "integration_type": normalized_type,
            "provider": config["provider"],
        },
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET", "POST"])
def integration_oauth_tools(request: HttpRequest, integration_type: str) -> JsonResponse:
    """List/update per-tool enabled settings for a connected native integration account."""

    config = _get_type_config(integration_type)
    if not config:
        return JsonResponse(
            {"error": "UNKNOWN_INTEGRATION_TYPE", "message": "Unknown integration type."},
            status=HTTPStatus.NOT_FOUND,
        )

    payload: dict[str, object] = {}
    if request.method != "GET":
        payload, payload_error = _parse_json_payload(request)
        if payload_error:
            return payload_error

    business_param = (
        payload.get("businessId")
        or payload.get("business_id")
        or request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    business, business_error = _resolve_business_api(request, str(business_param or ""))
    if business_error:
        return business_error
    assert business is not None

    normalized_type = str(integration_type or "").strip().lower()

    with tenant_context(business.id):
        account = (
            IntegrationAccount.objects.filter(
                business_profile=business,
                integration_type=normalized_type,
                user=request.user,
                status=IntegrationAccountStatus.CONNECTED,
            )
            .order_by("-updated_at")
            .first()
        )
        if not account:
            return JsonResponse(
                {
                    "error": "INTEGRATION_ACCOUNT_NOT_FOUND",
                    "message": "No connected integration account found for this provider.",
                },
                status=HTTPStatus.NOT_FOUND,
            )

        if request.method == "POST":
            updates = _normalize_native_tool_updates(integration_type=normalized_type, payload=payload)
            metadata = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
            raw_settings = metadata.get("tool_settings")
            if raw_settings is None:
                raw_settings = metadata.get("toolSettings")
            settings_map = dict(raw_settings) if isinstance(raw_settings, Mapping) else {}
            now_iso = timezone.now().isoformat()
            applied: list[dict[str, object]] = []
            for item in updates:
                tool_name = str(item.get("toolName") or "").strip()
                enabled = bool(item.get("enabled"))
                if not tool_name:
                    continue
                if enabled:
                    settings_map.pop(tool_name, None)
                else:
                    settings_map[tool_name] = {"enabled": False, "updated_at": now_iso}
                applied.append({"toolName": tool_name, "enabled": enabled})
            if settings_map:
                metadata["tool_settings"] = settings_map
            else:
                metadata.pop("tool_settings", None)
                metadata.pop("toolSettings", None)
            account.metadata = metadata
            account.save(update_fields=["metadata", "updated_at"])

            if applied:
                IntegrationAccountAuditEvent.objects.create(
                    business_profile=business,
                    integration_account=account,
                    integration_account_id_snapshot=account.id,
                    actor_user=request.user if request.user.is_authenticated else None,
                    action=IntegrationAccountAuditAction.UPDATED,
                    description=f"{config['name']} tool settings updated.",
                    metadata={
                        "integration_type": normalized_type,
                        "provider": config["provider"],
                        "changed_tools": len(applied),
                    },
                )

        tools_payload, summary = _serialize_native_tools_for_account(account=account, integration_type=normalized_type)

    return JsonResponse(
        {
            "businessId": str(business.id),
            "integrationType": normalized_type,
            "integrationLabel": config["name"],
            "provider": config["provider"],
            "accountId": str(account.id),
            "accountIdentifier": str(account.account_identifier or ""),
            "status": str(account.status or ""),
            "summary": summary,
            "tools": tools_payload,
        },
        status=HTTPStatus.OK,
    )
