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


__all__ = [name for name in globals() if not name.startswith("__")]

