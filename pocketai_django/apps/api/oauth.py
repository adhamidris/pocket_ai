from __future__ import annotations

import logging
import json
import secrets
import uuid
from datetime import timedelta
from http import HTTPStatus
from urllib.parse import urlencode

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
    McpConnectionAuditAction,
    McpConnectionAuthType,
    McpConnectionSourceType,
    McpConnectionStatus,
)
from apps.integrations.models import (
    OAuthProvider,
    OAuthState,
)
from apps.mcp.models import McpConnection
from apps.accounts.oauth_helpers import (
    OAuthFlowError,
    compute_expires_at,
    ensure_fresh_oauth_credentials,
    exchange_authorization_code,
)


DEFAULT_STATE_TTL_MINUTES = 10

logger = logging.getLogger(__name__)


def _bootstrap_oauth_provider(provider_key: str) -> OAuthProvider | None:
    """
    Best-effort bootstrap for marketplace OAuth providers from env-backed settings.

    This improves out-of-the-box marketplace OAuth UX in environments where the
    OAuthProvider rows were not created via admin yet.
    """

    key = str(provider_key or "").strip()
    if not key:
        return None

    if key == "google":
        client_id = str(getattr(settings, "MCP_OAUTH_GOOGLE_CLIENT_ID", "") or "").strip()
        client_secret = str(getattr(settings, "MCP_OAUTH_GOOGLE_CLIENT_SECRET", "") or "").strip()
        scopes = list(getattr(settings, "MCP_OAUTH_GOOGLE_SCOPES", []) or [])
        authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"
        token_url = "https://oauth2.googleapis.com/token"
        name = "Google"
    elif key == "slack":
        client_id = str(getattr(settings, "MCP_OAUTH_SLACK_CLIENT_ID", "") or "").strip()
        client_secret = str(getattr(settings, "MCP_OAUTH_SLACK_CLIENT_SECRET", "") or "").strip()
        scopes = list(getattr(settings, "MCP_OAUTH_SLACK_SCOPES", []) or [])
        authorization_url = "https://slack.com/oauth/v2/authorize"
        token_url = "https://slack.com/api/oauth.v2.access"
        name = "Slack"
    else:
        return None

    if not client_id or not client_secret:
        return None
    if not isinstance(scopes, list) or not any(str(scope or "").strip() for scope in scopes):
        return None

    existing = OAuthProvider.objects.filter(key=key).first()
    if existing and not existing.is_active:
        # Respect explicit disablement; do not auto-enable.
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


def _popup_html(payload: dict[str, object], *, fallback_redirect: str = "/dashboard/mcp/") -> HttpResponse:
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


def _safe_redirect_after(request: HttpRequest, value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return "/dashboard/mcp/"
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return "/dashboard/mcp/"


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
            return None, _popup_html({"type": "mcp_oauth_error", "error": "invalid_business_id"})
        business = BusinessProfile.objects.filter(id=business_uuid).first()
        if not business:
            return None, _popup_html({"type": "mcp_oauth_error", "error": "business_not_found"})
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
        if not business:
            return None, _popup_html({"type": "mcp_oauth_error", "error": "business_required"})

    if request.user.is_staff:
        return business, None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, _popup_html({"type": "mcp_oauth_error", "error": "forbidden"})

    return business, None


def _marketplace_entry(marketplace_key: str) -> dict[str, object] | None:
    from apps.api.mcp_connections import _mcp_marketplace_catalog

    catalog = _mcp_marketplace_catalog()
    return next((item for item in catalog if item.get("key") == marketplace_key), None)


@require_http_methods(["GET"])
def oauth_start(request: HttpRequest, provider_key: str, marketplace_key: str) -> HttpResponse:
    """
    Start OAuth flow. Redirects user to provider's auth page.

    GET /api/oauth/start/google/gmail/?business_id=...&redirect=...
    """

    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business, error = _resolve_business(request, business_param)
    if error:
        return error
    assert business is not None

    provider = OAuthProvider.objects.filter(key=provider_key, is_active=True).first()
    if not provider:
        provider = _bootstrap_oauth_provider(provider_key)
    if not provider:
        return _popup_html(
            {
                "type": "mcp_oauth_error",
                "error": "provider_not_configured",
                "provider": provider_key,
                "hint": "Ask an admin to configure OAuth providers (or set MCP_OAUTH_<PROVIDER>_CLIENT_ID/SECRET).",
            }
        )

    entry = _marketplace_entry(marketplace_key)
    if not entry:
        return _popup_html({"type": "mcp_oauth_error", "error": "mcp_not_found", "marketplaceKey": marketplace_key})

    if entry.get("connectionType") != "oauth":
        return _popup_html({"type": "mcp_oauth_error", "error": "mcp_not_oauth", "marketplaceKey": marketplace_key})

    entry_provider = entry.get("oauthProvider")
    if entry_provider and str(entry_provider) != provider.key:
        return _popup_html({"type": "mcp_oauth_error", "error": "provider_mismatch", "marketplaceKey": marketplace_key})

    if provider.marketplace_keys and not provider.supports_marketplace_key(marketplace_key):
        return _popup_html({"type": "mcp_oauth_error", "error": "provider_not_allowed", "marketplaceKey": marketplace_key})

    if not provider.client_id or not provider.get_client_secret():
        return _popup_html({"type": "mcp_oauth_error", "error": "provider_incomplete_config", "provider": provider_key})

    scopes = provider.scopes if isinstance(provider.scopes, list) else []
    if not scopes:
        return _popup_html({"type": "mcp_oauth_error", "error": "provider_missing_scopes", "provider": provider_key})

    redirect_after = _safe_redirect_after(request, request.GET.get("redirect"))

    state_token = secrets.token_urlsafe(32)

    OAuthState.objects.create(
        business_profile=business,
        user=request.user,
        provider=provider,
        marketplace_key=marketplace_key,
        state_token=state_token,
        redirect_after=redirect_after,
        expires_at=timezone.now() + timedelta(minutes=DEFAULT_STATE_TTL_MINUTES),
    )

    callback_path = reverse("api:oauth_callback", kwargs={"provider_key": provider.key})
    callback_url = request.build_absolute_uri(callback_path)

    scope_delimiter = "," if provider.key == "slack" else " "
    params: dict[str, str] = {
        "client_id": provider.client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": scope_delimiter.join([str(scope).strip() for scope in scopes if str(scope).strip()]),
        "state": state_token,
    }

    if provider.key == "google":
        params.update(
            {
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
        )
    if provider.key == "slack":
        params["token_access_type"] = "offline"

    auth_url = f"{provider.authorization_url}?{urlencode(params)}"
    return redirect(auth_url)


@require_http_methods(["GET"])
def oauth_callback(request: HttpRequest, provider_key: str) -> HttpResponse:
    """
    OAuth callback. Exchanges code for tokens and saves to McpConnection.

    GET /api/oauth/callback/google/?code=xxx&state=xxx
    """

    error = request.GET.get("error")
    if error:
        return _popup_html({"type": "mcp_oauth_error", "error": str(error)})

    code = request.GET.get("code")
    state_token = request.GET.get("state")
    if not code or not state_token:
        return _popup_html({"type": "mcp_oauth_error", "error": "missing_params"})

    oauth_state = (
        OAuthState.objects.select_related("provider", "business_profile", "user")
        .filter(
            state_token=state_token,
            provider__key=provider_key,
            is_used=False,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not oauth_state:
        return _popup_html({"type": "mcp_oauth_error", "error": "invalid_state"})

    if request.user.is_authenticated and not request.user.is_staff and request.user.id != oauth_state.user_id:
        return _popup_html({"type": "mcp_oauth_error", "error": "forbidden"})

    oauth_state.is_used = True
    oauth_state.save(update_fields=["is_used"])

    provider = oauth_state.provider
    business = oauth_state.business_profile

    callback_path = reverse("api:oauth_callback", kwargs={"provider_key": provider.key})
    callback_url = request.build_absolute_uri(callback_path)

    try:
        token_payload = exchange_authorization_code(provider, code=code, redirect_uri=callback_url)
    except OAuthFlowError as exc:
        return _popup_html({"type": "mcp_oauth_error", "error": "token_exchange_failed", "detail": str(exc)[:200]})

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return _popup_html({"type": "mcp_oauth_error", "error": "no_access_token"})

    expires_at = compute_expires_at(token_payload)
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    token_type = str(token_payload.get("token_type") or token_payload.get("tokenType") or "Bearer").strip() or "Bearer"

    entry = _marketplace_entry(oauth_state.marketplace_key)
    if not entry:
        return _popup_html({"type": "mcp_oauth_error", "error": "mcp_not_found", "marketplaceKey": oauth_state.marketplace_key})

    name = str(entry.get("name") or oauth_state.marketplace_key).strip() or oauth_state.marketplace_key
    server_url = str(entry.get("serverUrl") or "").strip()

    created = False

    with tenant_context(business.id):
        connection = (
            McpConnection.objects.filter(business_profile=business, marketplace_key=oauth_state.marketplace_key)
            .order_by("-created_at")
            .first()
        )
        if not connection:
            connection = McpConnection(
                business_profile=business,
                created_by=oauth_state.user,
                name=name,
                server_url=server_url,
                source_type=McpConnectionSourceType.MARKETPLACE,
                marketplace_key=oauth_state.marketplace_key,
                status=McpConnectionStatus.ENABLED,
                auth_type=McpConnectionAuthType.BEARER,
            )
            created = True
        else:
            connection.name = name
            if server_url:
                connection.server_url = server_url
            connection.source_type = McpConnectionSourceType.MARKETPLACE
            connection.status = McpConnectionStatus.ENABLED
            connection.auth_type = McpConnectionAuthType.BEARER

        credentials = dict(connection.credentials or {})
        existing_refresh = str(credentials.get("refresh_token") or "").strip() or None
        credentials["token"] = access_token
        if refresh_token or existing_refresh:
            credentials["refresh_token"] = refresh_token or existing_refresh
        if expires_at is not None:
            credentials["expires_at"] = expires_at.isoformat()
        if token_type:
            credentials["token_type"] = token_type
        scope = token_payload.get("scope")
        if scope:
            credentials["scope"] = scope

        connection.credentials = credentials

        next_metadata = dict(connection.metadata or {}) if isinstance(connection.metadata, dict) else {}
        next_metadata["oauth_provider"] = provider.key
        next_metadata["oauth_marketplace_key"] = oauth_state.marketplace_key
        connection.metadata = next_metadata

        connection.save()

        from apps.api.mcp_connections import _log_mcp_audit

        _log_mcp_audit(
            business=business,
            connection=connection,
            actor=oauth_state.user,
            action=McpConnectionAuditAction.CREATED if created else McpConnectionAuditAction.UPDATED,
            description="OAuth credentials connected via marketplace.",
            metadata={"marketplace_key": oauth_state.marketplace_key, "provider": provider.key},
        )
        try:
            from apps.mcp.connection_test_jobs import enqueue_mcp_connection_test_job

            enqueue_mcp_connection_test_job(connection=connection, trigger=f"oauth_{provider.key}")
        except Exception:  # pragma: no cover - background enqueue must not break OAuth callback
            logger.exception("mcp_test_job_enqueue_failed connection=%s", connection.id)

    redirect_after = oauth_state.redirect_after or "/dashboard/mcp/"
    return _popup_html(
        {"type": "mcp_oauth_success", "marketplaceKey": oauth_state.marketplace_key, "provider": provider.key},
        fallback_redirect=redirect_after,
    )


@csrf_protect
@require_http_methods(["POST"])
def oauth_refresh(request: HttpRequest, connection_id: uuid.UUID) -> JsonResponse:
    """
    Refresh expired OAuth tokens for a connection.

    POST /api/oauth/refresh/<connection_id>/
    """

    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    business_param = (
        request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = {}
    if isinstance(payload, dict):
        business_param = business_param or payload.get("businessId") or payload.get("business_id")

    business, error = _resolve_business(request, str(business_param or "") if business_param else None)
    if error:
        return JsonResponse({"error": "FORBIDDEN", "message": "Invalid business profile."}, status=HTTPStatus.FORBIDDEN)
    assert business is not None

    try:
        connection = McpConnection.objects.get(id=connection_id, business_profile=business)
    except McpConnection.DoesNotExist:
        return JsonResponse({"error": "MCP_CONNECTION_NOT_FOUND", "message": "MCP connection not found."}, status=HTTPStatus.NOT_FOUND)

    try:
        with tenant_context(business.id):
            ensure_fresh_oauth_credentials(connection)
    except OAuthFlowError as exc:
        return JsonResponse({"error": "OAUTH_REFRESH_FAILED", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    credentials = connection.credentials or {}
    return JsonResponse(
        {
            "connectionId": str(connection.id),
            "status": "ok",
            "expires_at": credentials.get("expires_at"),
        },
        status=HTTPStatus.OK,
    )
