from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import timedelta
from http import HTTPStatus
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.models import (
    BusinessProfile,
    EmailAccount,
    EmailAccountAuditAction,
    EmailAccountAuditEvent,
    EmailAccountProvider,
    EmailAccountStatus,
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
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>PocketAI OAuth</title>
  </head>
  <body>
    <p style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; padding: 16px;">
      You can close this window.
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


@require_http_methods(["GET"])
def email_oauth_start(request: HttpRequest, provider_key: str) -> HttpResponse:
    """
    Start OAuth flow for first-party email connectors.

    Intended usage: open this endpoint in a popup window and let it redirect to the provider.
    """

    mapping = _provider_mapping(provider_key)
    if not mapping:
        return _popup_html(
            {"type": "email_oauth_error", "error": "unknown_provider", "provider": provider_key},
            fallback_redirect=_default_redirect_after(),
        )

    email_provider, oauth_provider_key = mapping
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business, error = _resolve_business(request, business_param)
    if error:
        return error
    assert business is not None

    provider = OAuthProvider.objects.filter(key=oauth_provider_key, is_active=True).first()
    if not provider:
        provider = _bootstrap_email_oauth_provider(oauth_provider_key)
    if not provider:
        return _popup_html(
            {
                "type": "email_oauth_error",
                "error": "provider_not_configured",
                "provider": str(provider_key),
                "hint": "Set EMAIL_OAUTH_<PROVIDER>_CLIENT_ID/SECRET (or configure OAuthProvider in admin).",
            },
            fallback_redirect=_default_redirect_after(),
        )

    scopes = provider.scopes if isinstance(provider.scopes, list) else []
    if not scopes:
        return _popup_html(
            {"type": "email_oauth_error", "error": "provider_missing_scopes", "provider": str(provider_key)},
            fallback_redirect=_default_redirect_after(),
        )

    redirect_after = _safe_redirect_after(request, request.GET.get("redirect"))

    state_token = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(code_verifier)

    callback_path = reverse("api:email_oauth_callback", kwargs={"provider_key": str(provider_key).strip().lower()})
    callback_url = request.build_absolute_uri(callback_path)

    EmailOAuthState.objects.create(
        business_profile=business,
        user=request.user,
        provider=provider,
        email_provider=email_provider,
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

    if email_provider == EmailAccountProvider.GOOGLE:
        params.update(
            {
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
        )
    else:
        params["response_mode"] = "query"

    auth_url = f"{provider.authorization_url}?{urlencode(params)}"
    return redirect(auth_url)


@require_http_methods(["GET"])
def email_oauth_callback(request: HttpRequest, provider_key: str) -> HttpResponse:
    """
    OAuth callback for first-party email connectors.

    GET /api/email/oauth/callback/google/?code=...&state=...
    """

    mapping = _provider_mapping(provider_key)
    if not mapping:
        return _popup_html(
            {"type": "email_oauth_error", "error": "unknown_provider", "provider": provider_key},
            fallback_redirect=_default_redirect_after(),
        )

    email_provider, oauth_provider_key = mapping
    redirect_fallback = _default_redirect_after()

    error_code = request.GET.get("error")
    if error_code:
        description = request.GET.get("error_description") or error_code
        return _popup_html(
            {"type": "email_oauth_error", "error": str(error_code), "detail": str(description)[:200]},
            fallback_redirect=redirect_fallback,
        )

    code = request.GET.get("code")
    state_token = request.GET.get("state")
    if not code or not state_token:
        return _popup_html(
            {"type": "email_oauth_error", "error": "missing_params"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state = (
        EmailOAuthState.objects.select_related("provider", "business_profile", "user")
        .filter(
            state_token=state_token,
            email_provider=email_provider,
            provider__key=oauth_provider_key,
            is_used=False,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not oauth_state:
        return _popup_html(
            {"type": "email_oauth_error", "error": "invalid_state"},
            fallback_redirect=redirect_fallback,
        )

    if request.user.is_authenticated and not request.user.is_staff and request.user.id != oauth_state.user_id:
        return _popup_html(
            {"type": "email_oauth_error", "error": "forbidden"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state.is_used = True
    oauth_state.save(update_fields=["is_used"])

    provider = oauth_state.provider
    business = oauth_state.business_profile

    scope_param = None
    if email_provider == EmailAccountProvider.MICROSOFT:
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
            {"type": "email_oauth_error", "error": "token_exchange_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return _popup_html(
            {"type": "email_oauth_error", "error": "no_access_token"},
            fallback_redirect=redirect_fallback,
        )

    expires_at = compute_expires_at(token_payload)
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    token_type = str(token_payload.get("token_type") or token_payload.get("tokenType") or "Bearer").strip() or "Bearer"

    try:
        if email_provider == EmailAccountProvider.GOOGLE:
            profile = _fetch_google_userinfo(access_token)
            email_address = str(profile.get("email") or "").strip()
            external_id = str(profile.get("sub") or profile.get("id") or "").strip()
            display_name = str(profile.get("name") or "").strip()
        else:
            profile = _fetch_microsoft_profile(access_token)
            email_address = str(profile.get("mail") or profile.get("userPrincipalName") or "").strip()
            external_id = str(profile.get("id") or "").strip()
            display_name = str(profile.get("displayName") or "").strip()
    except OAuthFlowError as exc:
        return _popup_html(
            {"type": "email_oauth_error", "error": "profile_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    if not email_address:
        return _popup_html(
            {"type": "email_oauth_error", "error": "missing_email"},
            fallback_redirect=redirect_fallback,
        )

    created = False
    with tenant_context(business.id):
        account = EmailAccount.objects.filter(business_profile=business, user=oauth_state.user).first()
        if not account:
            account = EmailAccount(
                business_profile=business,
                user=oauth_state.user,
                provider=email_provider,
            )
            created = True
        account.provider = email_provider
        account.email_address = email_address
        if external_id:
            account.external_account_id = external_id
        account.status = EmailAccountStatus.CONNECTED
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
        credentials["provider"] = email_provider
        credentials["updated_at"] = timezone.now().isoformat()
        account.credentials = credentials

        meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        meta["email_profile"] = {
            "email": email_address,
            "name": display_name,
            "provider": email_provider,
            "linked_at": timezone.now().isoformat(),
        }
        account.metadata = meta

        account.save()

        EmailAccountAuditEvent.objects.create(
            business_profile=business,
            email_account=account,
            email_account_id_snapshot=account.id,
            actor_user=oauth_state.user,
            action=EmailAccountAuditAction.CONNECTED if created else EmailAccountAuditAction.UPDATED,
            description="Email account connected via OAuth.",
            metadata={
                "provider": email_provider,
                "email_sha256": _sha256_hex(email_address),
            },
        )

    redirect_after = oauth_state.redirect_after or redirect_fallback
    return _popup_html(
        {
            "type": "email_oauth_success",
            "provider": email_provider,
            "email": email_address,
        },
        fallback_redirect=redirect_after,
    )

