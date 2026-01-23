from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone

from core.tenancy import tenant_context

from apps.accounts.models import (
    EmailAccount,
    EmailAccountProvider,
    OAuthProvider,
)
from apps.accounts.oauth_helpers import (
    OAuthFlowError,
    compute_expires_at,
    credentials_are_expiring,
    refresh_access_token,
)


logger = logging.getLogger(__name__)

GOOGLE_EMAIL_OAUTH_PROVIDER_KEY = "google_email"
MICROSOFT_EMAIL_OAUTH_PROVIDER_KEY = "microsoft_email"


def _resolve_oauth_provider_key(email_provider: str) -> str | None:
    provider = str(email_provider or "").strip().lower()
    if provider == EmailAccountProvider.GOOGLE:
        return GOOGLE_EMAIL_OAUTH_PROVIDER_KEY
    if provider == EmailAccountProvider.MICROSOFT:
        return MICROSOFT_EMAIL_OAUTH_PROVIDER_KEY
    return None


def ensure_fresh_email_credentials(account: EmailAccount, *, now: datetime | None = None) -> EmailAccount:
    """
    If the EmailAccount stores OAuth credentials and is near expiry, refresh it.

    This is best-effort: if a provider isn't configured, or the account does not
    have a refresh token, the function returns without changes.
    """

    credentials = account.credentials or {}
    refresh_token = str(credentials.get("refresh_token") or "").strip()
    if not refresh_token:
        return account

    access_token = str(credentials.get("access_token") or "").strip()
    if access_token and not credentials_are_expiring(credentials, now=now):
        return account

    oauth_key = _resolve_oauth_provider_key(account.provider)
    if not oauth_key:
        return account

    provider = OAuthProvider.objects.filter(key=oauth_key, is_active=True).first()
    if not provider:
        return account

    scope_param = None
    if account.provider == EmailAccountProvider.MICROSOFT:
        scopes = provider.scopes if isinstance(provider.scopes, list) else []
        scope_param = " ".join([str(scope).strip() for scope in scopes if str(scope).strip()]) or None

    tokens = refresh_access_token(provider, refresh_token=refresh_token, scope=scope_param)
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        raise OAuthFlowError("OAuth provider refresh did not return an access_token.")

    expires_at = compute_expires_at(tokens, now=now)
    next_refresh = str(tokens.get("refresh_token") or "").strip() or refresh_token
    token_type = str(tokens.get("token_type") or tokens.get("tokenType") or credentials.get("token_type") or "Bearer").strip() or "Bearer"

    updated = dict(credentials)
    updated["access_token"] = access_token
    updated["refresh_token"] = next_refresh
    if expires_at is not None:
        updated["expires_at"] = expires_at.isoformat()
    if token_type:
        updated["token_type"] = token_type

    account.credentials = updated
    next_meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
    next_meta["oauth_last_refreshed_at"] = timezone.now().isoformat()
    account.metadata = next_meta

    business_id = account.business_profile_id or getattr(account.business_profile, "id", None)
    if business_id:
        with tenant_context(business_id):
            account.save(
                update_fields=[
                    "credentials_encrypted",
                    "credentials_key_version",
                    "credentials_last_rotated_at",
                    "credential_error_count",
                    "metadata",
                    "updated_at",
                ]
            )

    logger.info("email_credentials_refreshed account=%s provider=%s", account.id, account.provider)
    return account
