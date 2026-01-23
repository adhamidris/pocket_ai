from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.tenancy import tenant_context

from .models import McpConnection, OAuthProvider


logger = logging.getLogger(__name__)

DEFAULT_OAUTH_TIMEOUT_S = 15
DEFAULT_EXPIRES_SKEW_S = 60


class OAuthFlowError(RuntimeError):
    """Raised when an OAuth exchange or refresh fails."""


def _coerce_int(value: object, *, default: int | None = None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_token_response(provider: OAuthProvider, response: requests.Response) -> dict[str, Any]:
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise OAuthFlowError("OAuth provider returned an invalid JSON response.") from exc

    if not isinstance(payload, dict):
        raise OAuthFlowError("OAuth provider returned an unexpected token response.")

    if provider.key == "slack":
        if not payload.get("ok", True):
            raise OAuthFlowError(f"Slack OAuth failed: {payload.get('error') or 'unknown_error'}")

    if not response.ok:
        detail = payload.get("error_description") or payload.get("error") or str(payload)[:200]
        raise OAuthFlowError(f"OAuth token request failed ({response.status_code}): {detail}")

    return payload


def compute_expires_at(token_payload: dict[str, Any], *, now: datetime | None = None) -> datetime | None:
    expires_in = token_payload.get("expires_in") or token_payload.get("expiresIn")
    seconds = _coerce_int(expires_in, default=None)
    if seconds is None:
        return None
    seconds = max(1, seconds)
    reference = now or timezone.now()
    return reference + timedelta(seconds=max(1, seconds - DEFAULT_EXPIRES_SKEW_S))


def parse_expires_at(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)
    return None


def exchange_authorization_code(
    provider: OAuthProvider,
    *,
    code: str,
    redirect_uri: str,
    scope: str | None = None,
    code_verifier: str | None = None,
    timeout_s: int = DEFAULT_OAUTH_TIMEOUT_S,
) -> dict[str, Any]:
    secret = provider.get_client_secret()
    if not secret:
        raise OAuthFlowError("OAuth provider is missing a client secret.")

    payload = {
        "client_id": provider.client_id,
        "client_secret": secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if scope:
        payload["scope"] = str(scope).strip()
    if code_verifier:
        payload["code_verifier"] = str(code_verifier).strip()
    try:
        response = requests.post(
            provider.token_url,
            data=payload,
            headers={"Accept": "application/json"},
            timeout=max(1, int(timeout_s)),
        )
    except requests.RequestException as exc:
        raise OAuthFlowError(f"Failed to reach OAuth token endpoint: {exc.__class__.__name__}") from exc

    return _parse_token_response(provider, response)


def refresh_access_token(
    provider: OAuthProvider,
    *,
    refresh_token: str,
    scope: str | None = None,
    timeout_s: int = DEFAULT_OAUTH_TIMEOUT_S,
) -> dict[str, Any]:
    secret = provider.get_client_secret()
    if not secret:
        raise OAuthFlowError("OAuth provider is missing a client secret.")

    payload = {
        "client_id": provider.client_id,
        "client_secret": secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if scope:
        payload["scope"] = str(scope).strip()
    try:
        response = requests.post(
            provider.token_url,
            data=payload,
            headers={"Accept": "application/json"},
            timeout=max(1, int(timeout_s)),
        )
    except requests.RequestException as exc:
        raise OAuthFlowError(f"Failed to refresh OAuth token: {exc.__class__.__name__}") from exc

    return _parse_token_response(provider, response)


def credentials_are_expiring(credentials: dict[str, Any], *, now: datetime | None = None, skew_s: int = DEFAULT_EXPIRES_SKEW_S) -> bool:
    expires_at = parse_expires_at(credentials.get("expires_at") or credentials.get("expiresAt"))
    if expires_at is None:
        return False
    reference = now or timezone.now()
    return expires_at <= reference + timedelta(seconds=max(0, int(skew_s)))


def ensure_fresh_oauth_credentials(connection: McpConnection, *, now: datetime | None = None) -> McpConnection:
    """
    If the MCP connection stores OAuth credentials and is near expiry, refresh it.

    This is best-effort: if a provider isn't configured, or the connection does not
    have a refresh token, the function returns without changes.
    """

    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    provider_key = str(metadata.get("oauth_provider") or "").strip()
    if not provider_key:
        return connection

    credentials = connection.credentials or {}
    refresh_token = str(credentials.get("refresh_token") or "").strip()
    if not refresh_token:
        return connection

    if not credentials_are_expiring(credentials, now=now):
        return connection

    provider = OAuthProvider.objects.filter(key=provider_key, is_active=True).first()
    if not provider:
        return connection

    tokens = refresh_access_token(provider, refresh_token=refresh_token)
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        raise OAuthFlowError("OAuth provider refresh did not return an access_token.")

    next_refresh = str(tokens.get("refresh_token") or "").strip() or refresh_token
    expires_at = compute_expires_at(tokens, now=now)

    updated = dict(credentials)
    updated["token"] = access_token
    updated["refresh_token"] = next_refresh
    if expires_at is not None:
        updated["expires_at"] = expires_at.isoformat()
    token_type = tokens.get("token_type") or tokens.get("tokenType") or updated.get("token_type") or updated.get("tokenType")
    if token_type:
        updated["token_type"] = token_type

    connection.credentials = updated
    next_metadata = dict(metadata)
    next_metadata["oauth_last_refreshed_at"] = timezone.now().isoformat()
    connection.metadata = next_metadata

    business_id = connection.business_profile_id or getattr(connection.business_profile, "id", None)
    if business_id:
        with tenant_context(business_id):
            connection.save(
                update_fields=[
                    "credentials_encrypted",
                    "credentials_key_version",
                    "credentials_last_rotated_at",
                    "credential_error_count",
                    "metadata",
                    "updated_at",
                ]
            )

    return connection
