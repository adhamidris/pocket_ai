from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone

from core.tenancy import tenant_context

from apps.accounts.models import IntegrationProvider
from apps.integrations.models import (
    IntegrationAccount,
    OAuthProvider,
)
from apps.accounts.oauth_helpers import (
    OAuthFlowError,
    compute_expires_at,
    credentials_are_expiring,
    refresh_access_token,
)


logger = logging.getLogger(__name__)

# Maps (provider value) -> OAuthProvider.key used during bootstrap
_OAUTH_PROVIDER_KEYS: dict[str, dict[str, str]] = {
    "google_calendar": {"provider": "google", "key": "google_calendar"},
    "google_drive": {"provider": "google", "key": "google_drive"},
    "onedrive": {"provider": "microsoft", "key": "microsoft_drive"},
    "slack": {"provider": "slack", "key": "slack_native"},
    "hubspot": {"provider": "hubspot", "key": "hubspot"},
}


def _resolve_oauth_provider_key(integration_type: str) -> str | None:
    entry = _OAUTH_PROVIDER_KEYS.get(str(integration_type or "").strip().lower())
    return entry["key"] if entry else None


def ensure_fresh_integration_credentials(account: IntegrationAccount, *, now: datetime | None = None) -> IntegrationAccount:
    """
    If the IntegrationAccount stores OAuth credentials near expiry, refresh them.

    Best-effort: returns unchanged if no refresh token or provider not found.
    """

    credentials = account.credentials or {}
    refresh_token = str(credentials.get("refresh_token") or "").strip()
    if not refresh_token:
        return account

    access_token = str(credentials.get("access_token") or "").strip()
    if access_token and not credentials_are_expiring(credentials, now=now):
        return account

    oauth_key = _resolve_oauth_provider_key(account.integration_type)
    if not oauth_key:
        return account

    provider = OAuthProvider.objects.filter(key=oauth_key, is_active=True).first()
    if not provider:
        return account

    scope_param = None
    if account.provider in {IntegrationProvider.MICROSOFT, IntegrationProvider.HUBSPOT}:
        scopes = provider.scopes if isinstance(provider.scopes, list) else []
        scope_param = " ".join([str(scope).strip() for scope in scopes if str(scope).strip()]) or None

    tokens = refresh_access_token(provider, refresh_token=refresh_token, scope=scope_param)
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        raise OAuthFlowError("OAuth provider refresh did not return an access_token.")

    expires_at = compute_expires_at(tokens, now=now)
    next_refresh = str(tokens.get("refresh_token") or "").strip() or refresh_token
    token_type = str(
        tokens.get("token_type") or tokens.get("tokenType") or credentials.get("token_type") or "Bearer"
    ).strip() or "Bearer"

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

    logger.info(
        "integration_credentials_refreshed account=%s type=%s provider=%s",
        account.id, account.integration_type, account.provider,
    )
    return account
