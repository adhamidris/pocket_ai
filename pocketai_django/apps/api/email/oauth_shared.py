from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import timedelta
from http import HTTPStatus
from typing import Mapping
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
    EmailAccountAuditAction,
    EmailAccountProvider,
    EmailAccountStatus,
)
from apps.integrations.models import (
    EmailAccount,
    EmailAccountAuditEvent,
    EmailOAuthState,
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

GOOGLE_EMAIL_OAUTH_PROVIDER_KEY = "google_email"
MICROSOFT_EMAIL_OAUTH_PROVIDER_KEY = "microsoft_email"


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

        // If the popup cannot close (browser restrictions), navigate somewhere useful.
        setTimeout(function () {{
          try {{ window.location.href = {redirect_json}; }} catch (_err) {{}}
        }}, 300);
      }})();
    </script>
  </body>
</html>"""
    return HttpResponse(html, content_type="text/html")


def _default_redirect_after() -> str:
    return str(getattr(settings, "INTEGRATIONS_DASHBOARD_URL", "/dashboard/") or "/dashboard/").strip() or "/dashboard/"


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


def _provider_display_name(email_provider: str) -> str:
    normalized = str(email_provider or "").strip().lower()
    if normalized == EmailAccountProvider.GOOGLE:
        return "Gmail"
    if normalized == EmailAccountProvider.MICROSOFT:
        return "Outlook / Microsoft 365"
    return normalized.title() if normalized else "Email"


def _normalize_email_tool_updates(
    *,
    email_provider: str,
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
        for row in mcp_tools.get_email_integration_tools_for_provider(email_provider)
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


def _serialize_email_tools_for_account(
    *,
    account: EmailAccount,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    from apps.mcp import tools as mcp_tools

    provider = str(account.provider or "").strip().lower()
    catalog = mcp_tools.get_email_integration_tools_for_provider(provider)
    tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
    enabled_map = mcp_tools.get_email_tool_enabled_map_for_account(account, tool_names=tool_names)

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
            business_uuid = uuid.UUID(candidate)
        except (TypeError, ValueError):
            return None, _popup_html(
                {"type": "email_oauth_error", "error": "invalid_business_id"},
                fallback_redirect=_default_redirect_after(),
            )
        business = BusinessProfile.objects.filter(id=business_uuid).first()
        if not business:
            return None, _popup_html(
                {"type": "email_oauth_error", "error": "business_not_found"},
                fallback_redirect=_default_redirect_after(),
            )
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
        if not business:
            return None, _popup_html(
                {"type": "email_oauth_error", "error": "business_required"},
                fallback_redirect=_default_redirect_after(),
            )

    if request.user.is_staff:
        return business, None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, _popup_html(
            {"type": "email_oauth_error", "error": "forbidden"},
            fallback_redirect=_default_redirect_after(),
        )

    return business, None


def _provider_mapping(provider_key: str) -> tuple[str, str] | None:
    normalized = str(provider_key or "").strip().lower()
    if normalized == "google":
        return EmailAccountProvider.GOOGLE, GOOGLE_EMAIL_OAUTH_PROVIDER_KEY
    if normalized in {"microsoft", "ms", "outlook"}:
        return EmailAccountProvider.MICROSOFT, MICROSOFT_EMAIL_OAUTH_PROVIDER_KEY
    return None


def _bootstrap_email_oauth_provider(provider_key: str) -> OAuthProvider | None:
    """
    Best-effort bootstrap for email OAuth providers from env-backed settings.

    This mirrors the MCP marketplace OAuth bootstrap: environments can set env vars
    and avoid manual admin configuration.
    """

    key = str(provider_key or "").strip()
    if not key:
        return None

    if key == GOOGLE_EMAIL_OAUTH_PROVIDER_KEY:
        client_id = str(getattr(settings, "EMAIL_OAUTH_GOOGLE_CLIENT_ID", "") or "").strip()
        client_secret = str(getattr(settings, "EMAIL_OAUTH_GOOGLE_CLIENT_SECRET", "") or "").strip()
        scopes = list(getattr(settings, "EMAIL_OAUTH_GOOGLE_SCOPES", []) or [])
        authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"
        token_url = "https://oauth2.googleapis.com/token"
        name = "Google (Email)"
    elif key == MICROSOFT_EMAIL_OAUTH_PROVIDER_KEY:
        client_id = str(getattr(settings, "EMAIL_OAUTH_MICROSOFT_CLIENT_ID", "") or "").strip()
        client_secret = str(getattr(settings, "EMAIL_OAUTH_MICROSOFT_CLIENT_SECRET", "") or "").strip()
        scopes = list(getattr(settings, "EMAIL_OAUTH_MICROSOFT_SCOPES", []) or [])
        authorization_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        name = "Microsoft (Email)"
    else:
        return None

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


def _fetch_google_userinfo(access_token: str) -> dict[str, object]:
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
    return payload


def _fetch_microsoft_profile(access_token: str) -> dict[str, object]:
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
    return payload


__all__ = [name for name in globals() if not name.startswith("__")]

