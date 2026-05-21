from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import (
    IntegrationCredentialEventType,
    KnowledgeIntegrationStatus,
)
from apps.integrations.models import KnowledgeIntegration

logger = logging.getLogger(__name__)

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_DRIVE_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
GOOGLE_SHEETS_ENDPOINT = "https://sheets.googleapis.com/v4/spreadsheets"


class GoogleOAuthError(Exception):
    """Raised when the Google OAuth handshake fails."""


class GoogleSheetsDiscoveryError(GoogleOAuthError):
    """Raised when listing spreadsheets or tabs fails."""


@dataclass
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str]


def get_google_oauth_config() -> GoogleOAuthConfig:
    client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    redirect_uri = getattr(settings, "GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    scopes = list(getattr(settings, "GOOGLE_OAUTH_SCOPES", []))

    if not client_id or not client_secret or not redirect_uri:
        raise ImproperlyConfigured(
            "Google OAuth is missing configuration. Ensure GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and"
            " GOOGLE_OAUTH_REDIRECT_URI are set."
        )
    if not scopes:
        raise ImproperlyConfigured("GOOGLE_OAUTH_SCOPES must define at least one scope.")

    return GoogleOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=scopes,
    )


def build_google_authorization_url(state: str, *, access_type: str = "offline", prompt: str = "consent") -> str:
    config = get_google_oauth_config()
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scopes),
        "state": state,
        "access_type": access_type,
        "prompt": prompt,
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


def _apply_expiration(tokens: dict[str, Any]) -> dict[str, Any]:
    expires_in = tokens.get("expires_in")
    try:
        expires_delta = max(int(expires_in), 1)
    except (TypeError, ValueError):
        expires_delta = 3600
    expires_at = timezone.now() + timedelta(seconds=expires_delta - 60)
    tokens["expires_at"] = expires_at.isoformat()
    return tokens


def exchange_google_authorization_code(code: str) -> dict[str, Any]:
    config = get_google_oauth_config()
    payload = {
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        response = requests.post(GOOGLE_TOKEN_ENDPOINT, data=payload, timeout=15)
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"Failed to reach Google token endpoint: {exc}") from exc

    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise GoogleOAuthError(f"Google token exchange failed ({response.status_code}): {detail}")

    tokens = response.json()
    _apply_expiration(tokens)
    return tokens


def add_bearer(headers: dict[str, str] | None, token: str) -> dict[str, str]:
    hdrs = dict(headers or {})
    hdrs["Authorization"] = f"Bearer {token}"
    return hdrs


def refresh_google_access_token(refresh_token: str) -> dict[str, Any]:
    config = get_google_oauth_config()
    payload = {
        "refresh_token": refresh_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": "refresh_token",
    }
    try:
        response = requests.post(GOOGLE_TOKEN_ENDPOINT, data=payload, timeout=15)
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"Failed to refresh Google token: {exc}") from exc

    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise GoogleOAuthError(f"Google token refresh failed ({response.status_code}): {detail}")

    tokens = response.json()
    _apply_expiration(tokens)
    return tokens


def fetch_google_account_profile(access_token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(GOOGLE_USERINFO_ENDPOINT, headers=headers, timeout=10)
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"Failed to fetch Google account info: {exc}") from exc

    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise GoogleOAuthError(f"Google account info request failed ({response.status_code}): {detail}")

    return response.json()


def _drive_search_query(search: str | None) -> str:
    base = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    if search:
        term = search.replace("'", "\'")
        base += f" and name contains '{term}'"
    return base


def _list_google_spreadsheet_files(access_token: str, *, limit: int = 20, search: str | None = None) -> list[dict[str, Any]]:
    headers = add_bearer({}, access_token)
    params = {
        "q": _drive_search_query(search),
        "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, owners(displayName,emailAddress), webViewLink)",
        "pageSize": max(1, min(limit, 50)),
        "orderBy": "modifiedTime desc",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    try:
        response = requests.get(GOOGLE_DRIVE_FILES_ENDPOINT, headers=headers, params=params, timeout=20)
    except requests.RequestException as exc:
        raise GoogleSheetsDiscoveryError(f"Failed to list Google Drive files: {exc}") from exc
    if response.status_code == 401:
        raise GoogleSheetsDiscoveryError("Google Drive rejected the access token.")
    if not response.ok:
        raise GoogleSheetsDiscoveryError(
            f"Google Drive API error {response.status_code}: {response.text[:200]}"
        )
    payload = response.json()
    return payload.get("files", [])


def _fetch_sheet_tabs(access_token: str, spreadsheet_id: str) -> list[dict[str, Any]]:
    headers = add_bearer({}, access_token)
    params = {
        # hidden is a sibling property of gridProperties; request both explicitly.
        "fields": "spreadsheetId,properties(title),sheets(properties(sheetId,title,index,gridProperties(rowCount,columnCount),hidden))",
    }
    url = f"{GOOGLE_SHEETS_ENDPOINT}/{spreadsheet_id}"
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
    except requests.RequestException as exc:
        raise GoogleSheetsDiscoveryError(f"Failed to fetch sheet metadata: {exc}") from exc
    if response.status_code == 401:
        raise GoogleSheetsDiscoveryError("Google Sheets rejected the access token.")
    if not response.ok:
        raise GoogleSheetsDiscoveryError(
            f"Google Sheets API error {response.status_code}: {response.text[:200]}"
        )
    data = response.json()
    sheets = data.get("sheets", [])
    formatted: list[dict[str, Any]] = []
    for sheet in sheets:
        props = (sheet or {}).get("properties") or {}
        grid = props.get("gridProperties") or {}
        formatted.append(
            {
                "gid": str(props.get("sheetId")),
                "title": props.get("title"),
                "index": props.get("index"),
                "rowCount": grid.get("rowCount"),
                "columnCount": grid.get("columnCount"),
                "hidden": grid.get("hidden", False),
            }
        )
    return formatted


def discover_google_sheet_resources(
    integration: KnowledgeIntegration,
    *,
    limit: int = 20,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Return spreadsheets/tabs available to the connected Google account."""
    maybe_refresh_google_credentials(integration)
    credentials = integration.credentials or {}
    access_token = credentials.get("access_token")
    if not access_token:
        raise GoogleSheetsDiscoveryError("Google integration is missing an access token.")

    files = _list_google_spreadsheet_files(access_token, limit=limit, search=search)
    resources: list[dict[str, Any]] = []
    for file_info in files:
        spreadsheet_id = file_info.get("id")
        if not spreadsheet_id:
            continue
        try:
            sheets = _fetch_sheet_tabs(access_token, spreadsheet_id)
        except GoogleSheetsDiscoveryError as exc:
            logger.warning(
                "google_sheet_metadata_failed integration=%s file=%s error=%s",
                integration.id,
                spreadsheet_id,
                exc,
            )
            continue
        for sheet in sheets:
            if sheet.get("hidden"):
                continue
            sheet_gid = sheet.get("gid") or sheet.get("sheetId")
            if sheet_gid is None:
                continue
            resource_id = f"{spreadsheet_id}:{sheet_gid}"
            resources.append(
                {
                    "resource_id": resource_id,
                    "drive_file_id": spreadsheet_id,
                    "drive_file_name": file_info.get("name"),
                    "sheet_gid": str(sheet_gid),
                    "sheet_name": sheet.get("title") or f"Sheet {sheet.get('index', 0)}",
                    "row_count": sheet.get("rowCount"),
                    "column_count": sheet.get("columnCount"),
                    "modified_time": file_info.get("modifiedTime"),
                    "owner": (file_info.get("owners") or [{}])[0].get("displayName"),
                    "owner_email": (file_info.get("owners") or [{}])[0].get("emailAddress"),
                    "web_view_link": file_info.get("webViewLink"),
                }
            )
    return resources


def _parse_expiration(timestamp: str | None):
    if not timestamp:
        return None
    parsed = parse_datetime(timestamp)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.utc)
    return parsed


def maybe_refresh_google_credentials(integration: KnowledgeIntegration) -> bool:
    """Refresh access token when close to expiry. Returns True if refreshed."""

    credentials = integration.credentials or {}
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        return False

    expires_at = _parse_expiration(credentials.get("expires_at"))
    if expires_at and expires_at > timezone.now() + timedelta(seconds=60):
        return False

    try:
        tokens = refresh_google_access_token(refresh_token)
    except GoogleOAuthError as exc:
        integration.register_credential_failure(reason=f"Google token refresh failed: {exc}")
        integration.status = KnowledgeIntegrationStatus.ERROR
        integration.sync_error = f"Google token refresh failed: {exc}"
        integration.save(
            update_fields=[
                "status",
                "sync_error",
                "credential_error_count",
                "updated_at",
            ]
        )
        logger.warning(
            "google_drive_refresh_failed integration=%s business=%s error=%s",
            integration.id,
            integration.business_profile_id,
            exc,
        )
        raise

    credentials.update(
        {
            "access_token": tokens.get("access_token"),
            "expires_in": tokens.get("expires_in"),
            "expires_at": tokens.get("expires_at"),
            "token_type": tokens.get("token_type", credentials.get("token_type", "Bearer")),
            "scope": tokens.get("scope", credentials.get("scope")),
            "updated_at": timezone.now().isoformat(),
        }
    )
    integration.credentials = credentials
    integration.status = KnowledgeIntegrationStatus.CONNECTED
    integration.sync_error = ""
    integration.reset_credential_failures()
    integration.save(
        update_fields=[
            "credentials_encrypted",
            "credentials_key_version",
            "credentials_last_rotated_at",
            "credential_error_count",
            "status",
            "sync_error",
            "updated_at",
        ]
    )
    integration.log_credential_event(
        IntegrationCredentialEventType.REFRESHED,
        metadata={"scope": credentials.get("scope"), "expiresAt": credentials.get("expires_at")},
    )
    logger.info(
        "google_drive_token_refreshed integration=%s business=%s",
        integration.id,
        integration.business_profile_id,
    )
    return True
