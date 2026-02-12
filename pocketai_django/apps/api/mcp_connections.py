from __future__ import annotations

import ipaddress
import json
import logging
import socket
import uuid
from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import urlsplit

from datetime import timedelta

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

# Tool cache TTL in hours (default: 24 hours)
MCP_TOOL_CACHE_TTL_HOURS = getattr(settings, "MCP_TOOL_CACHE_TTL_HOURS", 24)

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    EmailAccountStatus,
    IntegrationAccountStatus,
    McpConnectionApprovalMode,
    McpConnectionAuditAction,
    McpConnectionAuthType,
    McpConnectionSourceType,
    McpConnectionStatus,
    McpToolOperationType,
)
from apps.integrations.models import (
    EmailAccount,
    IntegrationAccount,
)
from apps.mcp.models import (
    McpConnection,
    McpConnectionAgentOptOut,
    McpConnectionAuditEvent,
    McpConnectionToolSetting,
)
from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.oauth_helpers import OAuthFlowError, ensure_fresh_oauth_credentials
from apps.mcp.connectors import _infer_operation_type_from_tool_name
from apps.mcp.models import McpConnectionTestJob, McpConnectionTestJobStatus
from apps.mcp.remote_client import McpRemoteError, test_mcp_server


logger = logging.getLogger(__name__)

_MCP_SETUP_FIELDS_MAX_KEYS = 25
_MCP_SETUP_FIELD_MAX_CHARS = 4096
_MCP_SETUP_FIELDS_TOTAL_MAX_CHARS = 16384
_MCP_NATIVE_OAUTH_CONNECTION_TYPES = frozenset({"email_oauth", "integration_oauth"})
_MCP_SURFACE_INTEGRATIONS = "integrations"
_TOOL_APPROVAL_OVERRIDES_META_KEY = "tool_approval_overrides"


def _parse_json_body(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not isinstance(payload, dict):
        return None, JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be a JSON object."},
            status=HTTPStatus.BAD_REQUEST,
        )
    return payload, None


def _resolve_business_for_request(
    request: HttpRequest,
    business_id: str | None,
) -> tuple[BusinessProfile | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    candidate = business_id or request.headers.get("X-Business-Id") or request.META.get("HTTP_X_BUSINESS_ID")
    if candidate:
        try:
            business_uuid = candidate if isinstance(candidate, uuid.UUID) else uuid.UUID(str(candidate))
        except (TypeError, ValueError):
            return None, JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "business_id must be a valid UUID."},
                status=HTTPStatus.BAD_REQUEST,
            )
        try:
            business = BusinessProfile.objects.get(id=business_uuid)
        except BusinessProfile.DoesNotExist:
            return None, JsonResponse({"error": "BUSINESS_NOT_FOUND", "message": "Business profile not found."}, status=HTTPStatus.NOT_FOUND)
    else:
        business = request.user.business_profiles.order_by("-created_at").first()
        if not business:
            return None, JsonResponse(
                {"error": "BUSINESS_REQUIRED", "message": "A business_id is required to perform this action."},
                status=HTTPStatus.BAD_REQUEST,
            )

    if request.user.is_staff:
        return business, None

    if not request.user.business_profiles.filter(id=business.id).exists():
        return None, JsonResponse({"error": "FORBIDDEN", "message": "You do not have access to this business profile."}, status=HTTPStatus.FORBIDDEN)

    return business, None


def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_mcp_server_url(value: str) -> tuple[str | None, str | None]:
    raw = (value or "").strip()
    if not raw:
        return None, "serverUrl is required."
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None, "serverUrl must be a valid URL."

    if parsed.scheme not in {"http", "https"}:
        return None, "serverUrl must start with http:// or https://"
    if not parsed.netloc or not parsed.hostname:
        return None, "serverUrl must include a hostname."

    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost"} or hostname.endswith(".local"):
        return None, "serverUrl hostname is not allowed."

    # Block direct IP literals in private ranges and also resolve hostnames to defend against SSRF.
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_forbidden_ip(ip):
            return None, "serverUrl must not point to a private or local network address."
        return raw, None
    except ValueError:
        pass

    port = parsed.port
    try:
        infos = socket.getaddrinfo(hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None, "serverUrl hostname could not be resolved."

    resolved_ips: set[str] = set()
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        if not sockaddr:
            continue
        candidate_ip = sockaddr[0]
        if not candidate_ip:
            continue
        resolved_ips.add(candidate_ip)

    if not resolved_ips:
        return None, "serverUrl hostname could not be resolved."

    for candidate_ip in resolved_ips:
        try:
            ip = ipaddress.ip_address(candidate_ip)
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            return None, "serverUrl must not resolve to a private or local network address."

    return raw, None


def _marketplace_entry(marketplace_key: str) -> dict[str, Any] | None:
    key = str(marketplace_key or "").strip()
    if not key:
        return None
    for item in _mcp_marketplace_catalog():
        if str(item.get("key") or "").strip() == key:
            return item
    return None


def _is_native_oauth_marketplace_item(item: Mapping[str, Any]) -> bool:
    connection_type = str(item.get("connectionType") or "").strip().lower()
    return connection_type in _MCP_NATIVE_OAUTH_CONNECTION_TYPES


def _extract_setup_fields(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """
    Extract optional setup fields from request payload.

    Supports either top-level "setupFields" or legacy "metadata.setupFields".
    """
    direct = payload.get("setupFields")
    if isinstance(direct, dict):
        return direct
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        nested = metadata.get("setupFields")
        if isinstance(nested, dict):
            return nested
    return None


def _validate_setup_fields(
    setup_fields: Mapping[str, Any],
    *,
    marketplace_key: str | None,
) -> tuple[dict[str, str] | None, str | None]:
    """
    Validate + normalize marketplace setup fields.

    Stored values are treated as sensitive and persisted encrypted (inside McpConnection.credentials).
    """
    entry = _marketplace_entry(marketplace_key or "")
    if entry is None:
        if marketplace_key:
            return None, "marketplaceKey is invalid."
        return None, "setupFields require a marketplaceKey."
    allowed = entry.get("setupFields") if isinstance(entry, Mapping) else None
    allowed_keys = [str(key).strip() for key in allowed if str(key).strip()] if isinstance(allowed, list) else []
    allowed_set = set(allowed_keys)

    if not allowed_set:
        return None, "This MCP does not accept setup fields."

    cleaned: dict[str, str] = {}
    total_chars = 0
    for raw_key, raw_value in setup_fields.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if key not in allowed_set:
            return None, f"Unknown setup field: {key}."

        if raw_value is None:
            continue
        if not isinstance(raw_value, str):
            return None, f"Setup field {key} must be a string."
        value = raw_value.strip()
        if not value:
            continue
        if len(value) > _MCP_SETUP_FIELD_MAX_CHARS:
            return None, f"Setup field {key} is too long."
        cleaned[key] = value
        total_chars += len(value)
        if total_chars > _MCP_SETUP_FIELDS_TOTAL_MAX_CHARS:
            return None, "Setup fields payload is too large."
        if len(cleaned) > _MCP_SETUP_FIELDS_MAX_KEYS:
            return None, "Too many setup fields."

    return cleaned, None


def _mcp_auth_headers(connection: McpConnection) -> dict[str, str]:
    if connection.auth_type == McpConnectionAuthType.NONE:
        return {}
    if connection.auth_type == McpConnectionAuthType.BEARER:
        try:
            ensure_fresh_oauth_credentials(connection)
        except OAuthFlowError as exc:
            logger.warning("mcp_oauth_refresh_failed connection_id=%s error=%s", connection.id, str(exc)[:200])
        credentials = connection.credentials or {}
        token = str(credentials.get("token") or credentials.get("access_token") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}
    credentials = connection.credentials or {}
    if connection.auth_type == McpConnectionAuthType.HEADER:
        header_name = str(credentials.get("header_name") or "").strip()
        header_value = str(credentials.get("header_value") or "").strip()
        if header_name and header_value:
            return {header_name: header_value}
    return {}


def _is_tool_cache_expired(tool_cache: dict[str, Any]) -> bool:
    """Check if a tool cache has expired based on its expires_at timestamp."""
    expires_at = tool_cache.get("expires_at")
    if not expires_at:
        return False
    try:
        from datetime import datetime
        if isinstance(expires_at, str):
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        elif isinstance(expires_at, datetime):
            expiry = expires_at
        else:
            return False
        now = timezone.now()
        if expiry.tzinfo is None:
            from datetime import timezone as dt_tz
            expiry = expiry.replace(tzinfo=dt_tz.utc)
        return now > expiry
    except (ValueError, TypeError):
        return False


def _serialize_tool_setting(setting: McpConnectionToolSetting) -> dict[str, Any]:
    """Serialize a per-tool setting."""
    return {
        "id": str(setting.id),
        "toolName": setting.tool_name,
        "operationType": setting.operation_type,
        "operationTypeLabel": setting.get_operation_type_display(),
        "approvalMode": setting.approval_mode,
        "approvalModeLabel": setting.get_approval_mode_display() if setting.approval_mode else None,
        "effectiveApprovalMode": setting.get_effective_approval_mode(),
        "description": setting.description,
    }


def _serialize_mcp_connection(connection: McpConnection, *, business: BusinessProfile, include_tool_settings: bool = False) -> dict[str, Any]:
    metadata = connection.metadata or {}
    tool_cache = metadata.get("tool_cache") if isinstance(metadata.get("tool_cache"), dict) else {}
    tool_count = tool_cache.get("tool_count") or tool_cache.get("count") or metadata.get("tool_count") or 0
    last_tested_at = tool_cache.get("tested_at") or metadata.get("last_tested_at")
    last_error = tool_cache.get("error") or metadata.get("last_error")
    cache_expires_at = tool_cache.get("expires_at")
    cache_expired = _is_tool_cache_expired(tool_cache) if tool_cache else False

    total_agents = AgentProfile.objects.filter(business_profile=business).count()
    opt_out_count = McpConnectionAgentOptOut.objects.filter(connection=connection).count()
    assigned_agents = max(0, total_agents - opt_out_count)

    auth_configured = connection.auth_type != McpConnectionAuthType.NONE and connection.has_credentials()
    header_name = None
    if connection.auth_type == McpConnectionAuthType.HEADER:
        header_name = str((connection.credentials or {}).get("header_name") or "").strip() or None

    # Build tool settings list with cached tools info
    tool_settings_list = []
    if include_tool_settings:
        # Get existing per-tool settings
        existing_settings = {s.tool_name: s for s in McpConnectionToolSetting.objects.filter(connection=connection)}

        # Get cached tools from last test
        cached_tools = tool_cache.get("tools") or []
        for tool in cached_tools:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("name") or "").strip()
            if not tool_name:
                continue

            if tool_name in existing_settings:
                tool_settings_list.append(_serialize_tool_setting(existing_settings[tool_name]))
            else:
                # Tool exists in cache but no explicit setting - show with inferred defaults
                inferred_type = _infer_operation_type_from_tool_name(tool_name)
                inferred_label = {
                    McpToolOperationType.READ: "Read (inferred)",
                    McpToolOperationType.WRITE: "Write (inferred)",
                    McpToolOperationType.UNKNOWN: "Unknown (treat as write)",
                }.get(inferred_type, "Unknown (treat as write)")
                tool_settings_list.append({
                    "id": None,
                    "toolName": tool_name,
                    "operationType": inferred_type,
                    "operationTypeLabel": inferred_label,
                    "operationTypeInferred": inferred_type != McpToolOperationType.UNKNOWN,
                    "approvalMode": None,
                    "approvalModeLabel": None,
                    "effectiveApprovalMode": connection.default_approval_mode,
                    "description": str(tool.get("description") or ""),
                })

    result = {
        "id": str(connection.id),
        "name": connection.name,
        "slug": connection.slug,
        "status": connection.status,
        "statusLabel": connection.get_status_display(),
        "sourceType": connection.source_type,
        "sourceLabel": connection.get_source_type_display(),
        "marketplaceKey": connection.marketplace_key or None,
        "serverUrl": connection.server_url,
        "authType": connection.auth_type,
        "authLabel": connection.get_auth_type_display(),
        "authConfigured": auth_configured,
        "headerName": header_name,
        "defaultApprovalMode": connection.default_approval_mode,
        "defaultApprovalModeLabel": connection.get_default_approval_mode_display(),
        "toolCount": int(tool_count) if str(tool_count).isdigit() else tool_count,
        "lastTestedAt": last_tested_at,
        "lastError": last_error,
        "cacheExpiresAt": cache_expires_at,
        "cacheExpired": cache_expired,
        "agents": {
            "total": total_agents,
            "assigned": assigned_agents,
            "optedOut": opt_out_count,
        },
        "createdAt": connection.created_at.isoformat() if connection.created_at else None,
        "updatedAt": connection.updated_at.isoformat() if connection.updated_at else None,
    }

    test_job = _serialize_mcp_connection_test_job(connection)
    if test_job:
        result["testJob"] = test_job

    if include_tool_settings:
        result["toolSettings"] = tool_settings_list

    return result


def _serialize_mcp_connection_test_job(connection: McpConnection) -> dict[str, Any] | None:
    """
    Surface active background test jobs so the UI can show 'Testing…' without manual clicks.

    Prefers annotated fields when present (list endpoint), falls back to a DB lookup otherwise.
    """

    status = getattr(connection, "active_test_job_status", None)
    if isinstance(status, str) and status.strip():
        job_id = getattr(connection, "active_test_job_id", None)
        run_after = getattr(connection, "active_test_job_run_after", None)
        lease_expires_at = getattr(connection, "active_test_job_lease_expires_at", None)
        updated_at = getattr(connection, "active_test_job_updated_at", None)
        return {
            "id": str(job_id) if job_id else None,
            "status": status,
            "trigger": getattr(connection, "active_test_job_trigger", None),
            "attemptCount": getattr(connection, "active_test_job_attempt_count", None),
            "maxAttempts": getattr(connection, "active_test_job_max_attempts", None),
            "runAfter": run_after.isoformat() if hasattr(run_after, "isoformat") and run_after else None,
            "leaseExpiresAt": lease_expires_at.isoformat()
            if hasattr(lease_expires_at, "isoformat") and lease_expires_at
            else None,
            "updatedAt": updated_at.isoformat() if hasattr(updated_at, "isoformat") and updated_at else None,
        }

    try:
        job = (
            McpConnectionTestJob.objects.filter(
                connection=connection,
                business_profile_id=connection.business_profile_id,
                status__in=(McpConnectionTestJobStatus.QUEUED, McpConnectionTestJobStatus.RUNNING),
            )
            .order_by("-created_at")
            .first()
        )
    except Exception:
        return None
    if not job:
        return None
    return {
        "id": str(job.id),
        "status": job.status,
        "trigger": job.trigger,
        "attemptCount": job.attempt_count,
        "maxAttempts": job.max_attempts,
        "runAfter": job.run_after.isoformat() if job.run_after else None,
        "leaseExpiresAt": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
        "updatedAt": job.updated_at.isoformat() if job.updated_at else None,
    }


def _mcp_marketplace_catalog() -> list[dict[str, Any]]:
    """
    Curated MCP marketplace (templates).

    Note: Entries are templates—customers still supply their own server URL unless
    an entry includes a hosted URL.

    Categories:
    - communication: Email, chat, messaging tools
    - storage: File storage, cloud drives
    - productivity: Project management, notes, databases
    - crm: Customer relationship management
    - analytics: Data analytics, reporting
    - development: Code, repos, CI/CD
    - marketing: Ads, email marketing, social
    - ecommerce: Shopping, payments, inventory
    - finance: Accounting, invoicing, banking
    - utilities: Search, web scraping, general tools

    Industries (matches BusinessProfile.industry_key):
    - marketing, ecommerce, healthcare, legal, real_estate, saas_tech, finance, consulting, general
    """

    return [
        # ═══════════════════════════════════════════════════════════════════════
        # COMMUNICATION - EMAIL (Native first-party connectors)
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "gmail",
            "name": "Gmail",
            "description": "Search, read, and send emails via Gmail. Native integration with draft approval for safe sending.",
            "category": "communication",
            "industries": ["marketing", "ecommerce", "legal", "real_estate", "consulting", "general"],
            "connectionType": "email_oauth",  # Special type for native email connectors
            "oauthProvider": "google_email",  # Uses /api/email/oauth/start/
            "serverUrl": "__builtin__",  # Native - no external MCP server
            "docsUrl": "https://developers.google.com/gmail/api",
            "badge": "Popular",
            "tier": 1,  # First-party = tier 1
            "setupFields": [],
        },
        {
            "key": "outlook",
            "name": "Outlook / Microsoft 365",
            "description": "Search, read, and send emails via Microsoft Graph. Native integration with draft approval for safe sending.",
            "category": "communication",
            "industries": ["consulting", "finance", "legal", "real_estate", "general"],
            "connectionType": "email_oauth",  # Special type for native email connectors
            "oauthProvider": "microsoft_email",  # Uses /api/email/oauth/start/
            "serverUrl": "__builtin__",  # Native - no external MCP server
            "docsUrl": "https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview",
            "badge": "Popular",
            "tier": 1,  # First-party = tier 1
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # COMMUNICATION - MESSAGING
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "slack",
            "name": "Slack",
            "description": "Send messages, read channels, and search across your Slack workspace. Native integration with encrypted credentials.",
            "category": "communication",
            "industries": ["marketing", "saas_tech", "consulting", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "slack_native",
            "serverUrl": "__builtin__",
            "docsUrl": "https://api.slack.com/",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        {
            "key": "microsoft_teams",
            "name": "Microsoft Teams",
            "description": "Send messages and manage team channels via Teams API.",
            "category": "communication",
            "industries": ["consulting", "finance", "legal", "general"],
            "connectionType": "oauth",
            "oauthProvider": "microsoft",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/microsoft-teams",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        {
            "key": "discord",
            "name": "Discord",
            "description": "Bot integration for Discord servers and channels.",
            "category": "communication",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/discord",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],  # Just needs bot token
            "setupLabels": {"token": "Discord Bot Token"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # STORAGE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "google_drive",
            "name": "Google Drive",
            "description": "Search, list, and read files in Google Drive. Native integration with encrypted credentials.",
            "category": "storage",
            "industries": ["marketing", "ecommerce", "legal", "consulting", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "google_drive",
            "serverUrl": "__builtin__",
            "docsUrl": "https://developers.google.com/drive/api",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        {
            "key": "dropbox",
            "name": "Dropbox",
            "description": "File storage and sharing via Dropbox API.",
            "category": "storage",
            "industries": ["consulting", "legal", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/dropbox",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        {
            "key": "onedrive",
            "name": "OneDrive",
            "description": "Search, list, and read files in OneDrive. Native integration with encrypted credentials.",
            "category": "storage",
            "industries": ["consulting", "finance", "legal", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "microsoft_drive",
            "serverUrl": "__builtin__",
            "docsUrl": "https://learn.microsoft.com/en-us/graph/api/resources/onedrive",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # PRODUCTIVITY
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "notion",
            "name": "Notion",
            "description": "Access pages, databases, and workspace content in Notion.",
            "category": "productivity",
            "industries": ["marketing", "saas_tech", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/notion",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Notion Integration Token"},
            "setupHelp": {"token": "Create an integration at notion.so/my-integrations"},
        },
        {
            "key": "airtable",
            "name": "Airtable",
            "description": "Database and spreadsheet hybrid for structured data management.",
            "category": "productivity",
            "industries": ["marketing", "ecommerce", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/airtable",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Airtable API Key"},
            "setupHelp": {"token": "Find at airtable.com/account"},
        },
        {
            "key": "trello",
            "name": "Trello",
            "description": "Kanban boards and task management via Trello API.",
            "category": "productivity",
            "industries": ["marketing", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/trello",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Trello API Key"},
        },
        {
            "key": "asana",
            "name": "Asana",
            "description": "Project and task management via Asana API.",
            "category": "productivity",
            "industries": ["marketing", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/asana",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        {
            "key": "google_calendar",
            "name": "Google Calendar",
            "description": "List, create, and manage calendar events. Native integration with encrypted credentials.",
            "category": "productivity",
            "industries": ["real_estate", "consulting", "legal", "healthcare", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "google_calendar",
            "serverUrl": "__builtin__",
            "docsUrl": "https://developers.google.com/calendar/api",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # CRM
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "salesforce",
            "name": "Salesforce",
            "description": "Access leads, opportunities, accounts, and CRM data.",
            "category": "crm",
            "industries": ["marketing", "ecommerce", "consulting", "general"],
            "connectionType": "oauth",
            "oauthProvider": "salesforce",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/salesforce",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Premium",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "hubspot",
            "name": "HubSpot",
            "description": "Search contacts, manage deals, and access CRM data. Native integration with encrypted credentials.",
            "category": "crm",
            "industries": ["marketing", "saas_tech", "consulting", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "hubspot",
            "serverUrl": "__builtin__",
            "docsUrl": "https://developers.hubspot.com/docs/api/overview",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        {
            "key": "pipedrive",
            "name": "Pipedrive",
            "description": "Sales pipeline and deal management CRM.",
            "category": "crm",
            "industries": ["real_estate", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/pipedrive",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Pipedrive API Token"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # ANALYTICS
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "google_analytics",
            "name": "Google Analytics",
            "description": "Website traffic and user behavior analytics.",
            "category": "analytics",
            "industries": ["marketing", "ecommerce", "saas_tech", "general"],
            "connectionType": "oauth",
            "oauthProvider": "google",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/googleanalytics",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "mixpanel",
            "name": "Mixpanel",
            "description": "Product analytics and user event tracking.",
            "category": "analytics",
            "industries": ["saas_tech", "ecommerce", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/mixpanel",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Mixpanel API Secret"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # DEVELOPMENT
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "github",
            "name": "GitHub",
            "description": "Official GitHub MCP for repos, issues, pull requests, and code.",
            "category": "development",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://api.githubcopilot.com/mcp/",
            "docsUrl": "https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp/set-up-the-github-mcp-server",
            "badge": "Official",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "GitHub Personal Access Token"},
            "setupHelp": {"token": "Create at github.com/settings/tokens"},
        },
        {
            "key": "gitlab",
            "name": "GitLab",
            "description": "Repository management, issues, and CI/CD pipelines.",
            "category": "development",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/gitlab",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "GitLab Personal Access Token"},
        },
        {
            "key": "jira",
            "name": "Jira",
            "description": "Issue tracking and agile project management.",
            "category": "development",
            "industries": ["saas_tech", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/jira",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Jira API Token"},
            "setupHelp": {"token": "Create at id.atlassian.com/manage-profile/security/api-tokens"},
        },
        {
            "key": "linear",
            "name": "Linear",
            "description": "Modern issue tracking for software teams.",
            "category": "development",
            "industries": ["saas_tech"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/linear",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Linear API Key"},
        },
        {
            "key": "context7",
            "name": "Context7 Docs",
            "description": "Up-to-date library documentation tools via MCP.",
            "category": "development",
            "industries": ["saas_tech", "general"],
            "connectionType": "none",
            "recommendedAuth": "none",
            "serverUrl": "https://mcp.context7.com/mcp",
            "docsUrl": "https://context7.com/",
            "badge": "Popular",
            "tier": 1,
            "setupFields": [],  # 1-click - no setup needed
        },
        # ═══════════════════════════════════════════════════════════════════════
        # MARKETING
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "google_ads",
            "name": "Google Ads",
            "description": "Campaign management and advertising analytics.",
            "category": "marketing",
            "industries": ["marketing", "ecommerce", "general"],
            "connectionType": "oauth",
            "oauthProvider": "google",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/googleads",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "mailchimp",
            "name": "Mailchimp",
            "description": "Email marketing campaigns and audience management.",
            "category": "marketing",
            "industries": ["marketing", "ecommerce", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/mailchimp",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Mailchimp API Key"},
            "setupHelp": {"token": "Find at mailchimp.com/account/api"},
        },
        {
            "key": "sendgrid",
            "name": "SendGrid",
            "description": "Transactional and marketing email delivery.",
            "category": "marketing",
            "industries": ["saas_tech", "ecommerce", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/sendgrid",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "SendGrid API Key"},
        },
        {
            "key": "linkedin",
            "name": "LinkedIn",
            "description": "Professional network data and posting (read-only for most).",
            "category": "marketing",
            "industries": ["marketing", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/linkedin",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # ECOMMERCE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "shopify",
            "name": "Shopify",
            "description": "E-commerce store management, orders, and products.",
            "category": "ecommerce",
            "industries": ["ecommerce"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/shopify",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": ["token", "store_url"],
            "setupLabels": {"token": "Shopify Access Token", "store_url": "Store URL"},
            "setupHelp": {"store_url": "e.g., mystore.myshopify.com"},
        },
        {
            "key": "stripe",
            "name": "Stripe",
            "description": "Payment processing, subscriptions, and invoices.",
            "category": "ecommerce",
            "industries": ["ecommerce", "saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/stripe",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Stripe Secret Key"},
            "setupHelp": {"token": "Find at dashboard.stripe.com/apikeys"},
        },
        {
            "key": "woocommerce",
            "name": "WooCommerce",
            "description": "WordPress e-commerce store management.",
            "category": "ecommerce",
            "industries": ["ecommerce"],
            "connectionType": "api_key",
            "recommendedAuth": "header",
            "serverUrl": "https://mcp.composio.dev/woocommerce",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["consumer_key", "consumer_secret", "store_url"],
            "setupLabels": {"consumer_key": "Consumer Key", "consumer_secret": "Consumer Secret", "store_url": "Store URL"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # FINANCE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "quickbooks",
            "name": "QuickBooks",
            "description": "Accounting, invoicing, and financial reporting.",
            "category": "finance",
            "industries": ["finance", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/quickbooks",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "xero",
            "name": "Xero",
            "description": "Cloud accounting and bookkeeping.",
            "category": "finance",
            "industries": ["finance", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/xero",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # LEGAL
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "docusign",
            "name": "DocuSign",
            "description": "Electronic signatures and document workflows.",
            "category": "legal",
            "industries": ["legal", "real_estate", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/docusign",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "clio",
            "name": "Clio",
            "description": "Legal practice management and case tracking.",
            "category": "legal",
            "industries": ["legal"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/clio",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # REAL ESTATE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "zillow",
            "name": "Zillow",
            "description": "Property listings and real estate data.",
            "category": "real_estate",
            "industries": ["real_estate"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/zillow",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Zillow API Key"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # UTILITIES (Universal tools available to all)
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "brave_search",
            "name": "Brave Search",
            "description": "Web search with privacy-focused results.",
            "category": "utilities",
            "industries": ["general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/bravesearch",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 1,
            "setupFields": ["token"],
            "setupLabels": {"token": "Brave Search API Key"},
            "setupHelp": {"token": "Get at brave.com/search/api"},
        },
        {
            "key": "postgres",
            "name": "PostgreSQL",
            "description": "Query and analytics workflows via a Postgres MCP server.",
            "category": "utilities",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/postgresql",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["connection_string"],
            "setupLabels": {"connection_string": "Connection String"},
            "setupHelp": {"connection_string": "postgresql://user:pass@host:5432/db"},
        },
        {
            "key": "excel",
            "name": "Excel / Sheets",
            "description": "Spreadsheet creation and manipulation. Built into your AI.",
            "category": "utilities",
            "industries": ["finance", "consulting", "marketing", "general"],
            "connectionType": "none",
            "recommendedAuth": "none",
            "serverUrl": "__builtin__",
            "docsUrl": "",
            "badge": "Built-in",
            "tier": 1,
            "setupFields": [],  # Built-in, no setup
            "isBuiltIn": True,
        },
        {
            "key": "pdf_tools",
            "name": "PDF Tools",
            "description": "Create, read, and manipulate PDF documents. Built into your AI.",
            "category": "utilities",
            "industries": ["legal", "consulting", "general"],
            "connectionType": "none",
            "recommendedAuth": "none",
            "serverUrl": "__builtin__",
            "docsUrl": "",
            "badge": "Built-in",
            "tier": 1,
            "setupFields": [],  # Built-in, no setup
            "isBuiltIn": True,
        },
    ]


def _get_marketplace_categories() -> list[dict[str, str]]:
    """Return available marketplace categories for filtering."""
    return [
        {"key": "communication", "label": "Communication"},
        {"key": "storage", "label": "Storage"},
        {"key": "productivity", "label": "Productivity"},
        {"key": "crm", "label": "CRM"},
        {"key": "analytics", "label": "Analytics"},
        {"key": "development", "label": "Development"},
        {"key": "marketing", "label": "Marketing"},
        {"key": "ecommerce", "label": "E-commerce"},
        {"key": "finance", "label": "Finance"},
        {"key": "legal", "label": "Legal"},
        {"key": "real_estate", "label": "Real Estate"},
        {"key": "utilities", "label": "Utilities"},
    ]


def _get_industry_display_names() -> dict[str, str]:
    """Map industry keys to display names."""
    return {
        "marketing": "Marketing",
        "ecommerce": "E-commerce",
        "healthcare": "Healthcare",
        "legal": "Legal",
        "real_estate": "Real Estate",
        "saas_tech": "SaaS & Tech",
        "finance": "Finance",
        "consulting": "Consulting",
        "general": "General",
    }


def _filter_marketplace_by_industry(catalog: list[dict[str, Any]], industry_key: str) -> list[dict[str, Any]]:
    """Filter marketplace items that match a given industry."""
    if not industry_key:
        return []

    industry_lower = industry_key.lower().replace(" ", "_").replace("-", "_")

    matched = []
    for item in catalog:
        industries = item.get("industries") or []
        industries_lower = [i.lower() for i in industries]
        if industry_lower in industries_lower or "general" in industries_lower:
            matched.append(item)

    return matched


def _get_common_tools(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tools that are common across all industries (tier 1 or general)."""
    common = []
    for item in catalog:
        tier = item.get("tier", 2)
        industries = item.get("industries") or []
        if tier == 1 or "general" in industries:
            common.append(item)
    return common


def _get_email_accounts_payload(business: BusinessProfile) -> list[dict[str, Any]]:
    """
    Return serialized email accounts for the business.

    These are native first-party email connectors (Gmail/Outlook) that create
    EmailAccount records rather than McpConnection records.
    """
    accounts = EmailAccount.objects.filter(business_profile=business).order_by("email_address")
    from apps.mcp import tools as mcp_tools
    result = []
    for account in accounts:
        # Map provider to marketplace key
        provider = str(account.provider or "").strip().lower()
        if provider == "google":
            marketplace_key = "gmail"
            display_name = "Gmail"
        elif provider == "microsoft":
            marketplace_key = "outlook"
            display_name = "Outlook"
        else:
            marketplace_key = provider
            display_name = provider.title()

        catalog = mcp_tools.get_email_integration_tools_for_provider(provider)
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        enabled_map = mcp_tools.get_email_tool_enabled_map_for_account(account, tool_names=tool_names) if tool_names else {}
        total_tool_count = len(tool_names)
        enabled_tool_count = sum(1 for name in tool_names if bool(enabled_map.get(name, True)))

        result.append({
            "id": str(account.id),
            "type": "email_account",  # Distinguish from MCP connections
            "marketplaceKey": marketplace_key,
            "name": f"{display_name} ({account.email_address})",
            "provider": provider,
            "emailAddress": account.email_address,
            "status": account.status,
            "sendMode": account.send_mode,
            "totalToolCount": total_tool_count,
            "enabledToolCount": enabled_tool_count,
            "lastError": account.last_error or "",
            "lastHealthCheckedAt": account.last_health_checked_at.isoformat() if account.last_health_checked_at else None,
            "createdAt": account.created_at.isoformat() if account.created_at else None,
            "updatedAt": account.updated_at.isoformat() if account.updated_at else None,
        })
    return result


def _get_integration_accounts_payload(business: BusinessProfile) -> list[dict[str, Any]]:
    """
    Return serialized native integration accounts for the business.

    These are first-party integrations (Calendar, Drive, OneDrive, Slack, HubSpot)
    that create IntegrationAccount records.
    """
    accounts = IntegrationAccount.objects.filter(business_profile=business).order_by("integration_type")
    result = []
    from apps.mcp import tools as mcp_tools
    for account in accounts:
        catalog = mcp_tools.get_native_integration_tools_for_type(str(account.integration_type or ""))
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        enabled_map = mcp_tools.get_native_tool_enabled_map_for_account(account, tool_names=tool_names) if tool_names else {}
        total_tool_count = len(tool_names)
        enabled_tool_count = sum(1 for name in tool_names if bool(enabled_map.get(name, True)))
        result.append({
            "id": str(account.id),
            "type": "integration_account",
            "integration_type": account.integration_type,
            "provider": account.provider,
            "account_identifier": account.account_identifier,
            "status": account.status,
            "totalToolCount": total_tool_count,
            "enabledToolCount": enabled_tool_count,
            "lastError": account.last_error or "",
            "lastHealthCheckedAt": account.last_health_checked_at.isoformat() if account.last_health_checked_at else None,
            "createdAt": account.created_at.isoformat() if account.created_at else None,
            "updatedAt": account.updated_at.isoformat() if account.updated_at else None,
        })
    return result


def _log_mcp_audit(
    *,
    business: BusinessProfile,
    connection: McpConnection | None,
    actor,
    action: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        McpConnectionAuditEvent.objects.create(
            business_profile=business,
            connection=connection,
            connection_id_snapshot=(connection.id if connection else None),
            actor_user=actor if getattr(actor, "is_authenticated", False) else None,
            action=action,
            description=description or "",
            metadata=metadata or {},
        )
    except Exception:  # pragma: no cover
        logger.exception("mcp_audit_write_failed action=%s business=%s connection=%s", action, business.id, getattr(connection, "id", None))


@csrf_protect
@require_http_methods(["GET", "POST"])
def mcp_connections_collection(request: HttpRequest) -> JsonResponse:
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    payload: dict[str, Any] | None = None
    if request.method != "GET":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or request.GET.get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method == "GET":
        surface = str(request.GET.get("surface") or "").strip().lower()
        include_native_oauth = surface == _MCP_SURFACE_INTEGRATIONS

        with tenant_context(business.id):
            active_jobs = (
                McpConnectionTestJob.objects.filter(
                    connection_id=OuterRef("id"),
                    business_profile_id=OuterRef("business_profile_id"),
                    status__in=(McpConnectionTestJobStatus.QUEUED, McpConnectionTestJobStatus.RUNNING),
                )
                .order_by("-created_at")
            )
            connections = list(
                McpConnection.objects.filter(business_profile=business)
                .annotate(
                    active_test_job_id=Subquery(active_jobs.values("id")[:1]),
                    active_test_job_status=Subquery(active_jobs.values("status")[:1]),
                    active_test_job_trigger=Subquery(active_jobs.values("trigger")[:1]),
                    active_test_job_attempt_count=Subquery(active_jobs.values("attempt_count")[:1]),
                    active_test_job_max_attempts=Subquery(active_jobs.values("max_attempts")[:1]),
                    active_test_job_run_after=Subquery(active_jobs.values("run_after")[:1]),
                    active_test_job_lease_expires_at=Subquery(active_jobs.values("lease_expires_at")[:1]),
                    active_test_job_updated_at=Subquery(active_jobs.values("updated_at")[:1]),
                )
                .order_by("name")
            )
            connections_payload = [_serialize_mcp_connection(connection, business=business) for connection in connections]

        # Get full marketplace catalog
        full_catalog = _mcp_marketplace_catalog()
        if not include_native_oauth:
            full_catalog = [item for item in full_catalog if not _is_native_oauth_marketplace_item(item)]

        # Get industry-specific recommendations
        industry_key = getattr(business, "industry_key", "") or ""
        industry_display = business.industry if hasattr(business, "industry") else ""

        # Filter for industry-specific tools
        industry_tools = _filter_marketplace_by_industry(full_catalog, industry_key)

        # Get common tools (tier 1 / universal)
        common_tools = _get_common_tools(full_catalog)

        # Get connected email accounts (native Gmail/Outlook)
        email_accounts_payload = _get_email_accounts_payload(business) if include_native_oauth else []

        # Get connected integration accounts (Calendar, Drive, OneDrive, Slack, HubSpot)
        integration_accounts_payload = _get_integration_accounts_payload(business) if include_native_oauth else []

        return JsonResponse(
            {
                "businessId": str(business.id),
                "dashboardUrl": "/dashboard/mcp/",
                "connections": connections_payload,
                "emailAccounts": email_accounts_payload,
                "integrationAccounts": integration_accounts_payload,
                "marketplace": full_catalog,
                "industryTools": industry_tools,
                "commonTools": common_tools,
                "industryKey": industry_key,
                "industryDisplay": industry_display or _get_industry_display_names().get(industry_key, "Your Industry"),
                "categories": _get_marketplace_categories(),
            },
            status=HTTPStatus.OK,
        )

    name = str((payload or {}).get("name") or "").strip()
    server_url, url_error = _validate_mcp_server_url(str((payload or {}).get("serverUrl") or (payload or {}).get("server_url") or ""))
    if url_error:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": url_error, "field": "serverUrl"}, status=HTTPStatus.BAD_REQUEST)

    source_type = str((payload or {}).get("sourceType") or (payload or {}).get("source_type") or McpConnectionSourceType.MANUAL).strip()
    if source_type not in {choice for choice, _ in McpConnectionSourceType.choices}:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "sourceType is invalid.", "field": "sourceType"}, status=HTTPStatus.BAD_REQUEST)
    marketplace_key = str((payload or {}).get("marketplaceKey") or "").strip()

    setup_fields_in = _extract_setup_fields(payload or {})
    setup_fields_cleaned: dict[str, str] | None = None
    if setup_fields_in is not None:
        setup_fields_cleaned, setup_error = _validate_setup_fields(
            setup_fields_in,
            marketplace_key=marketplace_key or None,
        )
        if setup_error:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": setup_error, "field": "setupFields"},
                status=HTTPStatus.BAD_REQUEST,
            )

    auth_payload = (payload or {}).get("auth") if isinstance((payload or {}).get("auth"), dict) else {}
    auth_type = str(auth_payload.get("type") or (payload or {}).get("authType") or McpConnectionAuthType.NONE).strip() or McpConnectionAuthType.NONE
    if auth_type not in {choice for choice, _ in McpConnectionAuthType.choices}:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "authType is invalid.", "field": "authType"}, status=HTTPStatus.BAD_REQUEST)

    credentials_value: dict[str, Any] = {}
    if auth_type == McpConnectionAuthType.BEARER:
        token = str(auth_payload.get("token") or auth_payload.get("bearerToken") or "").strip()
        if not token:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "Bearer token is required.", "field": "auth.token"}, status=HTTPStatus.BAD_REQUEST)
        credentials_value = {"token": token}
    elif auth_type == McpConnectionAuthType.HEADER:
        header_name = str(auth_payload.get("headerName") or "").strip()
        header_value = str(auth_payload.get("headerValue") or "").strip()
        if not header_name or not header_value:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "Header name and value are required.", "field": "auth.headerName"},
                status=HTTPStatus.BAD_REQUEST,
            )
        if any(char in header_name for char in ("\r", "\n", ":")):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "Header name is invalid.", "field": "auth.headerName"},
                status=HTTPStatus.BAD_REQUEST,
            )
        credentials_value = {"header_name": header_name, "header_value": header_value}

    if setup_fields_cleaned:
        credentials_value = dict(credentials_value)
        credentials_value["setup_fields"] = setup_fields_cleaned

    status_value = (payload or {}).get("enabled")
    status = McpConnectionStatus.ENABLED if bool(status_value) else McpConnectionStatus.DISABLED

    # Parse approval mode (default to APPROVE_WRITES for external tools)
    approval_mode = str((payload or {}).get("defaultApprovalMode") or (payload or {}).get("approvalMode") or McpConnectionApprovalMode.APPROVE_WRITES).strip()
    if approval_mode not in {choice for choice, _ in McpConnectionApprovalMode.choices}:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "approvalMode is invalid.", "field": "defaultApprovalMode"}, status=HTTPStatus.BAD_REQUEST)

    if not name:
        name = "MCP Connection"

    with tenant_context(business.id):
        connection = McpConnection.objects.create(
            business_profile=business,
            created_by=request.user,
            name=name,
            server_url=server_url or "",
            status=status,
            source_type=source_type,
            marketplace_key=marketplace_key,
            auth_type=auth_type,
            default_approval_mode=approval_mode,
        )
        connection.credentials = credentials_value

        connection.save(update_fields=["credentials_encrypted", "credentials_key_version", "credentials_last_rotated_at", "credential_error_count", "updated_at"])

        _log_mcp_audit(
            business=business,
            connection=connection,
            actor=request.user,
            action=McpConnectionAuditAction.CREATED,
            description="MCP connection created.",
            metadata={"source_type": source_type, "marketplace_key": marketplace_key or None},
        )
        try:
            from apps.mcp.connection_test_jobs import enqueue_mcp_connection_test_job

            enqueue_mcp_connection_test_job(connection=connection, trigger="create")
        except Exception:  # pragma: no cover - background enqueue must not break API
            logger.exception("mcp_test_job_enqueue_failed connection=%s", connection.id)
        serialized = _serialize_mcp_connection(connection, business=business)

    return JsonResponse({"connection": serialized}, status=HTTPStatus.CREATED)


@csrf_protect
@require_http_methods(["GET", "PUT", "DELETE"])
def mcp_connection_detail(request: HttpRequest, connection_id: uuid.UUID) -> JsonResponse:
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    payload: dict[str, Any] | None = None
    if request.method != "GET":
        payload, error = _parse_json_body(request) if request.method == "PUT" else ({}, None)
        if error:
            return error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or request.GET.get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    try:
        connection = McpConnection.objects.get(id=connection_id, business_profile=business)
    except McpConnection.DoesNotExist:
        return JsonResponse({"error": "MCP_CONNECTION_NOT_FOUND", "message": "MCP connection not found."}, status=HTTPStatus.NOT_FOUND)

    if request.method == "GET":
        with tenant_context(business.id):
            return JsonResponse({"connection": _serialize_mcp_connection(connection, business=business)}, status=HTTPStatus.OK)

    if request.method == "DELETE":
        _log_mcp_audit(
            business=business,
            connection=connection,
            actor=request.user,
            action=McpConnectionAuditAction.UPDATED,
            description="MCP connection deleted.",
            metadata={"deleted": True},
        )
        connection.delete()
        return JsonResponse({}, status=HTTPStatus.NO_CONTENT)

    # PUT update
    assert payload is not None
    updates: dict[str, Any] = {}
    name = str(payload.get("name") or "").strip()
    if name:
        updates["name"] = name

    if "serverUrl" in payload or "server_url" in payload:
        server_url, url_error = _validate_mcp_server_url(str(payload.get("serverUrl") or payload.get("server_url") or ""))
        if url_error:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": url_error, "field": "serverUrl"}, status=HTTPStatus.BAD_REQUEST)
        updates["server_url"] = server_url or connection.server_url

    enabled_value = payload.get("enabled")
    if enabled_value is not None:
        updates["status"] = McpConnectionStatus.ENABLED if bool(enabled_value) else McpConnectionStatus.DISABLED

    # Handle approval mode update
    approval_mode_value = payload.get("defaultApprovalMode") or payload.get("approvalMode")
    if approval_mode_value is not None:
        approval_mode = str(approval_mode_value).strip()
        if approval_mode not in {choice for choice, _ in McpConnectionApprovalMode.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "approvalMode is invalid.", "field": "defaultApprovalMode"}, status=HTTPStatus.BAD_REQUEST)
        updates["default_approval_mode"] = approval_mode

    setup_fields_in = _extract_setup_fields(payload)
    setup_fields_cleaned: dict[str, str] | None = None
    if setup_fields_in is not None:
        setup_fields_cleaned, setup_error = _validate_setup_fields(
            setup_fields_in,
            marketplace_key=connection.marketplace_key or None,
        )
        if setup_error:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": setup_error, "field": "setupFields"},
                status=HTTPStatus.BAD_REQUEST,
            )

    auth_payload = payload.get("auth") if isinstance(payload.get("auth"), dict) else None
    auth_type_value = payload.get("authType")
    if auth_type_value or auth_payload:
        auth_type = str((auth_payload or {}).get("type") or auth_type_value or connection.auth_type).strip()
        if auth_type not in {choice for choice, _ in McpConnectionAuthType.choices}:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "authType is invalid.", "field": "authType"}, status=HTTPStatus.BAD_REQUEST)
        updates["auth_type"] = auth_type

    desired_auth_type = updates.get("auth_type", connection.auth_type)
    if auth_payload is not None or auth_type_value is not None:
        if desired_auth_type == McpConnectionAuthType.BEARER:
            token = str((auth_payload or {}).get("token") or (auth_payload or {}).get("bearerToken") or "").strip()
            if not token and (desired_auth_type != connection.auth_type) and not connection.has_credentials():
                return JsonResponse({"error": "VALIDATION_ERROR", "message": "Bearer token is required.", "field": "auth.token"}, status=HTTPStatus.BAD_REQUEST)
        elif desired_auth_type == McpConnectionAuthType.HEADER:
            header_name = str((auth_payload or {}).get("headerName") or "").strip()
            header_value = str((auth_payload or {}).get("headerValue") or "").strip()
            if (desired_auth_type != connection.auth_type) and not connection.has_credentials():
                if not header_name or not header_value:
                    return JsonResponse(
                        {"error": "VALIDATION_ERROR", "message": "Header name and value are required.", "field": "auth.headerName"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            if header_name and any(char in header_name for char in ("\r", "\n", ":")):
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "Header name is invalid.", "field": "auth.headerName"},
                    status=HTTPStatus.BAD_REQUEST,
                )

    with tenant_context(business.id):
        for field, value in updates.items():
            setattr(connection, field, value)
        connection.save(update_fields=[*updates.keys(), "updated_at"])

        credentials_changed = False
        next_credentials = dict(connection.credentials or {})

        if auth_payload is not None or auth_type_value is not None:
            auth_type = desired_auth_type
            if auth_type == McpConnectionAuthType.BEARER:
                token = str((auth_payload or {}).get("token") or (auth_payload or {}).get("bearerToken") or "").strip()
                if token:
                    next_credentials["token"] = token
                    credentials_changed = True
            elif auth_type == McpConnectionAuthType.HEADER:
                header_name = str((auth_payload or {}).get("headerName") or "").strip()
                header_value = str((auth_payload or {}).get("headerValue") or "").strip()
                if header_name and header_value:
                    next_credentials["header_name"] = header_name
                    next_credentials["header_value"] = header_value
                    credentials_changed = True
            elif auth_type == McpConnectionAuthType.NONE:
                for key in (
                    "token",
                    "access_token",
                    "refresh_token",
                    "expires_at",
                    "token_type",
                    "scope",
                    "header_name",
                    "header_value",
                ):
                    if key in next_credentials:
                        next_credentials.pop(key, None)
                        credentials_changed = True

        if setup_fields_in is not None:
            if setup_fields_cleaned:
                next_credentials["setup_fields"] = setup_fields_cleaned
            else:
                next_credentials.pop("setup_fields", None)
            credentials_changed = True

        if credentials_changed:
            connection.credentials = next_credentials
            connection.save(
                update_fields=[
                    "credentials_encrypted",
                    "credentials_key_version",
                    "credentials_last_rotated_at",
                    "credential_error_count",
                    "updated_at",
                ]
            )

        try:
            from apps.mcp.connection_test_jobs import enqueue_mcp_connection_test_job

            trigger = None
            if "server_url" in updates:
                trigger = "update_server_url"
            elif auth_payload is not None or auth_type_value is not None:
                trigger = "update_auth"
            elif setup_fields_in is not None:
                trigger = "update_setup_fields"
            elif updates.get("status") == McpConnectionStatus.ENABLED:
                trigger = "update_enabled"

            if trigger:
                enqueue_mcp_connection_test_job(connection=connection, trigger=trigger)
        except Exception:  # pragma: no cover - background enqueue must not break API
            logger.exception("mcp_test_job_enqueue_failed connection=%s", connection.id)

        _log_mcp_audit(
            business=business,
            connection=connection,
            actor=request.user,
            action=McpConnectionAuditAction.UPDATED,
            description="MCP connection updated.",
            metadata={
                "fields": sorted(list(updates.keys())),
                **(
                    {"setup_fields_keys": sorted(list(setup_fields_cleaned.keys()))}
                    if setup_fields_in is not None and setup_fields_cleaned
                    else {}
                ),
            },
        )
        serialized = _serialize_mcp_connection(connection, business=business)

    return JsonResponse({"connection": serialized}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["POST"])
def mcp_connection_test(request: HttpRequest, connection_id: uuid.UUID) -> JsonResponse:
    payload, error = _parse_json_body(request)
    if error:
        return error

    business_param = (
        (payload or {}).get("businessId")
        or (payload or {}).get("business_id")
        or request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    try:
        connection = McpConnection.objects.get(id=connection_id, business_profile=business)
    except McpConnection.DoesNotExist:
        return JsonResponse({"error": "MCP_CONNECTION_NOT_FOUND", "message": "MCP connection not found."}, status=HTTPStatus.NOT_FOUND)

    headers = _mcp_auth_headers(connection)
    server_url, url_error = _validate_mcp_server_url(connection.server_url)
    if url_error:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": url_error, "field": "serverUrl"}, status=HTTPStatus.BAD_REQUEST)

    try:
        session, tools = test_mcp_server(server_url=server_url or connection.server_url, headers=headers)
    except McpRemoteError as exc:
        upstream_status = getattr(exc, "status_code", None)
        retry_after = getattr(exc, "retry_after", None)
        status = HTTPStatus.BAD_REQUEST
        if isinstance(upstream_status, int) and upstream_status == HTTPStatus.TOO_MANY_REQUESTS:
            status = HTTPStatus.TOO_MANY_REQUESTS
        tested_at = timezone.now().isoformat()
        error_message = str(exc)
        with tenant_context(business.id):
            metadata = dict(connection.metadata or {})
            existing_cache = metadata.get("tool_cache") if isinstance(metadata.get("tool_cache"), dict) else {}
            existing_tools = existing_cache.get("tools") if isinstance(existing_cache.get("tools"), list) else None
            existing_expires_at = existing_cache.get("expires_at") if existing_cache else None
            existing_tool_count = (
                len(existing_tools)
                if isinstance(existing_tools, list)
                else int(existing_cache.get("tool_count") or 0)
                if str(existing_cache.get("tool_count") or "").isdigit()
                else 0
            )

            next_cache = dict(existing_cache) if isinstance(existing_cache, dict) else {}
            next_cache.update(
                {
                    "tested_at": tested_at,
                    "error": error_message[:500],
                    "status_code": upstream_status,
                    "retry_after": retry_after,
                }
            )

            # Preserve the last known good tool schema if available so tools don't
            # "disappear" from the orchestrator due to a transient test failure.
            if isinstance(existing_tools, list) and existing_tools:
                next_cache["tools"] = existing_tools
                next_cache["tool_count"] = existing_tool_count
                if existing_expires_at:
                    next_cache["expires_at"] = existing_expires_at
            else:
                next_cache.pop("tools", None)
                next_cache["tool_count"] = 0

            metadata["tool_cache"] = next_cache
            connection.metadata = metadata
            connection.save(update_fields=["metadata", "updated_at"])
            _log_mcp_audit(
                business=business,
                connection=connection,
                actor=request.user,
                action=McpConnectionAuditAction.UPDATED,
                description="MCP connection test failed.",
                metadata={"error": error_message[:500], "status_code": upstream_status},
            )
        payload = {"error": "MCP_TEST_FAILED", "message": error_message}
        if upstream_status is not None:
            payload["upstreamStatus"] = upstream_status
        if retry_after:
            payload["retryAfter"] = retry_after
        return JsonResponse(payload, status=status)

    tested_at = timezone.now().isoformat()
    tools_payload = [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        for tool in tools
    ]

    with tenant_context(business.id):
        metadata = dict(connection.metadata or {})
        cache_expires_at = (timezone.now() + timedelta(hours=MCP_TOOL_CACHE_TTL_HOURS)).isoformat()
        metadata["tool_cache"] = {
            "tested_at": tested_at,
            "expires_at": cache_expires_at,
            "transport": session.transport,
            "protocol_version": session.protocol_version,
            "server_info": session.server_info.__dict__ if session.server_info else None,
            "tool_count": len(tools),
            "tools": tools_payload,
        }
        connection.metadata = metadata
        connection.save(update_fields=["metadata", "updated_at"])

        _log_mcp_audit(
            business=business,
            connection=connection,
            actor=request.user,
            action=McpConnectionAuditAction.UPDATED,
            description="MCP connection tested successfully.",
            metadata={"tool_count": len(tools), "transport": session.transport},
        )
        serialized = _serialize_mcp_connection(connection, business=business)

    return JsonResponse(
        {
            "connection": serialized,
            "test": {"serverInfo": session.server_info.__dict__ if session.server_info else None, "toolCount": len(tools)},
        },
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET", "POST"])
def mcp_connection_agents(request: HttpRequest, connection_id: uuid.UUID) -> JsonResponse:
    payload: dict[str, Any] | None = None
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    if request.method != "GET":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or request.GET.get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    try:
        connection = McpConnection.objects.get(id=connection_id, business_profile=business)
    except McpConnection.DoesNotExist:
        return JsonResponse({"error": "MCP_CONNECTION_NOT_FOUND", "message": "MCP connection not found."}, status=HTTPStatus.NOT_FOUND)

    if request.method == "GET":
        with tenant_context(business.id):
            agents = list(AgentProfile.objects.filter(business_profile=business).order_by("created_at"))
            opted_out = set(
                McpConnectionAgentOptOut.objects.filter(connection=connection).values_list("agent_profile_id", flat=True)
            )
            rows = [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "slug": agent.slug,
                    "status": agent.status,
                    "statusLabel": agent.get_status_display(),
                    "enabled": agent.id not in opted_out,
                    "optedOut": agent.id in opted_out,
                }
                for agent in agents
            ]
        total = len(rows)
        opted_out_count = sum(1 for row in rows if row["optedOut"])
        enabled_count = total - opted_out_count
        return JsonResponse(
            {
                "businessId": str(business.id),
                "connectionId": str(connection.id),
                "agents": rows,
                "summary": {"total": total, "enabled": enabled_count, "optedOut": opted_out_count},
            },
            status=HTTPStatus.OK,
        )

    assert payload is not None
    updates = payload.get("updates")
    if isinstance(updates, list) and updates:
        items = updates
    else:
        agent_id = payload.get("agentId") or payload.get("agent_id")
        enabled = payload.get("enabled")
        items = [{"agentId": agent_id, "enabled": enabled}]

    applied: list[dict[str, Any]] = []
    with tenant_context(business.id):
        for item in items:
            if not isinstance(item, dict):
                continue
            agent_id = item.get("agentId") or item.get("agent_id")
            enabled = item.get("enabled")
            try:
                agent_uuid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
            except (TypeError, ValueError):
                continue
            if enabled is None:
                continue
            try:
                agent = AgentProfile.objects.get(id=agent_uuid, business_profile=business)
            except AgentProfile.DoesNotExist:
                continue

            if bool(enabled):
                deleted, _ = McpConnectionAgentOptOut.objects.filter(connection=connection, agent_profile=agent).delete()
                if deleted:
                    _log_mcp_audit(
                        business=business,
                        connection=connection,
                        actor=request.user,
                        action=McpConnectionAuditAction.AGENT_OPTED_IN,
                        description="Agent opted in to MCP connection.",
                        metadata={"agent_id": str(agent.id)},
                    )
                applied.append({"agentId": str(agent.id), "enabled": True, "optedOut": False})
            else:
                _, created = McpConnectionAgentOptOut.objects.get_or_create(
                    connection=connection,
                    agent_profile=agent,
                    defaults={"opted_out_by": request.user, "metadata": {}},
                )
                if created:
                    _log_mcp_audit(
                        business=business,
                        connection=connection,
                        actor=request.user,
                        action=McpConnectionAuditAction.AGENT_OPTED_OUT,
                        description="Agent opted out from MCP connection.",
                        metadata={"agent_id": str(agent.id)},
                    )
                applied.append({"agentId": str(agent.id), "enabled": False, "optedOut": True})

    return JsonResponse({"applied": applied}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET", "POST"])
def mcp_connection_tools(request: HttpRequest, connection_id: uuid.UUID) -> JsonResponse:
    """
    GET: List all tools for a connection with their approval settings.
    POST: Update approval settings for one or more tools.
    """
    payload: dict[str, Any] | None = None
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    if request.method != "GET":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or request.GET.get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    try:
        connection = McpConnection.objects.get(id=connection_id, business_profile=business)
    except McpConnection.DoesNotExist:
        return JsonResponse({"error": "MCP_CONNECTION_NOT_FOUND", "message": "MCP connection not found."}, status=HTTPStatus.NOT_FOUND)

    if request.method == "GET":
        with tenant_context(business.id):
            serialized = _serialize_mcp_connection(connection, business=business, include_tool_settings=True)
        return JsonResponse(
            {
                "connectionId": str(connection.id),
                "connectionName": connection.name,
                "defaultApprovalMode": connection.default_approval_mode,
                "defaultApprovalModeLabel": connection.get_default_approval_mode_display(),
                "tools": serialized.get("toolSettings", []),
                "approvalModeOptions": [
                    {"value": choice, "label": label}
                    for choice, label in McpConnectionApprovalMode.choices
                ],
                "operationTypeOptions": [
                    {"value": choice, "label": label}
                    for choice, label in McpToolOperationType.choices
                ],
            },
            status=HTTPStatus.OK,
        )

    # POST: Update tool settings
    assert payload is not None
    updates = payload.get("updates")
    if isinstance(updates, list) and updates:
        items = updates
    else:
        # Single tool update
        tool_name = payload.get("toolName") or payload.get("tool_name")
        items = [payload] if tool_name else []

    applied: list[dict[str, Any]] = []
    with tenant_context(business.id):
        for item in items:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("toolName") or item.get("tool_name") or "").strip()
            if not tool_name:
                continue

            if "operationType" in item:
                operation_type = item.get("operationType")
            elif "operation_type" in item:
                operation_type = item.get("operation_type")
            else:
                operation_type = None

            if "approvalMode" in item:
                approval_mode = item.get("approvalMode")
            elif "approval_mode" in item:
                approval_mode = item.get("approval_mode")
            else:
                approval_mode = None

            # Validate operation_type if provided
            if operation_type is not None:
                operation_type = str(operation_type).strip()
                if operation_type not in {choice for choice, _ in McpToolOperationType.choices}:
                    continue

            # Validate approval_mode if provided (can be null to inherit)
            if approval_mode is not None and approval_mode != "":
                approval_mode = str(approval_mode).strip()
                if approval_mode not in {choice for choice, _ in McpConnectionApprovalMode.choices}:
                    continue
            elif approval_mode == "":
                approval_mode = None  # Explicitly set to inherit

            # Use update_or_create to handle race conditions gracefully
            # Build defaults dict for creation and updates dict for existing records
            defaults = {
                "operation_type": operation_type or McpToolOperationType.UNKNOWN,
                "description": str(item.get("description") or "").strip(),
            }
            # Only include approval_mode if explicitly provided in the request
            if "approvalMode" in item or "approval_mode" in item:
                defaults["approval_mode"] = approval_mode

            try:
                setting, created = McpConnectionToolSetting.objects.update_or_create(
                    connection=connection,
                    tool_name=tool_name,
                    defaults=defaults,
                )
            except Exception:
                # Handle any remaining edge cases (e.g., concurrent deletes)
                logger.warning(
                    "mcp_tool_setting_update_failed connection=%s tool=%s",
                    connection.id,
                    tool_name,
                )
                continue

            applied.append(_serialize_tool_setting(setting))

    return JsonResponse({"applied": applied}, status=HTTPStatus.OK)


def _controls_mode_from_approval_mode(value: str | None, *, operation_type: str | None = None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == McpConnectionApprovalMode.AUTO:
        return "auto"
    if normalized == McpConnectionApprovalMode.APPROVE_WRITES:
        op = str(operation_type or "").strip().lower()
        return "auto" if op == McpToolOperationType.READ else "confirm"
    return "confirm"


def _normalize_controls_mode(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"auto", "confirm"}:
        return normalized
    return None


def _controls_default_mode_for_operation_type(operation_type: str | None) -> str:
    op = str(operation_type or "").strip().lower()
    return "auto" if op == McpToolOperationType.READ else "confirm"


def _load_business_tool_approval_overrides(business: BusinessProfile) -> dict[str, str]:
    metadata = business.metadata if isinstance(getattr(business, "metadata", None), Mapping) else {}
    raw = metadata.get(_TOOL_APPROVAL_OVERRIDES_META_KEY)
    if raw is None:
        raw = metadata.get("toolApprovalOverrides")
    if not isinstance(raw, Mapping):
        return {}
    overrides: dict[str, str] = {}
    for tool_name, mode_value in raw.items():
        normalized_tool_name = str(tool_name or "").strip()
        normalized_mode = _normalize_controls_mode(mode_value)
        if not normalized_tool_name or normalized_mode is None:
            continue
        overrides[normalized_tool_name] = normalized_mode
    return overrides


def _save_business_tool_approval_overrides(*, business: BusinessProfile, overrides: Mapping[str, str]) -> None:
    metadata = dict(business.metadata) if isinstance(getattr(business, "metadata", None), Mapping) else {}
    cleaned = {
        str(tool_name or "").strip(): str(mode or "").strip().lower()
        for tool_name, mode in dict(overrides).items()
        if str(tool_name or "").strip() and str(mode or "").strip().lower() in {"auto", "confirm"}
    }
    if cleaned:
        metadata[_TOOL_APPROVAL_OVERRIDES_META_KEY] = cleaned
    else:
        metadata.pop(_TOOL_APPROVAL_OVERRIDES_META_KEY, None)
    metadata.pop("toolApprovalOverrides", None)
    business.metadata = metadata
    business.save(update_fields=["metadata", "updated_at"])


def _controls_tool_label(tool_name: str) -> str:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return "Tool"
    return normalized.replace("_", " ").strip().title()


def _controls_available_integration_tool_names(
    *,
    business: BusinessProfile,
    enabled_connections: list[McpConnection],
) -> set[str]:
    from apps.mcp import tools as mcp_tools

    available: set[str] = set()

    integration_accounts = IntegrationAccount.objects.filter(
        business_profile=business,
        status=IntegrationAccountStatus.CONNECTED,
    ).order_by("-updated_at")
    for account in integration_accounts:
        catalog = mcp_tools.get_native_integration_tools_for_type(str(account.integration_type or ""))
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        if not tool_names:
            continue
        enabled_map = mcp_tools.get_native_tool_enabled_map_for_account(account, tool_names=tool_names)
        for tool_name in tool_names:
            if bool(enabled_map.get(tool_name, True)):
                available.add(tool_name)

    email_accounts = EmailAccount.objects.filter(
        business_profile=business,
        status=EmailAccountStatus.CONNECTED,
    ).order_by("-updated_at")
    for account in email_accounts:
        provider = str(account.provider or "").strip().lower()
        if not provider:
            continue
        catalog = mcp_tools.get_email_integration_tools_for_provider(provider)
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        if not tool_names:
            continue
        enabled_map = mcp_tools.get_email_tool_enabled_map_for_account(account, tool_names=tool_names)
        for tool_name in tool_names:
            if bool(enabled_map.get(tool_name, True)):
                available.add(tool_name)

    if enabled_connections:
        available.update({"mcp_search_tools", "mcp_call_tool"})

    return available


def _controls_internal_tool_items(
    *,
    overrides: Mapping[str, str] | None = None,
    available_integration_tool_names: set[str] | None = None,
    allowed_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    from apps.mcp import tools as mcp_tools

    overrides_map = dict(overrides or {})
    available_integration_tools = set(available_integration_tool_names or set())
    filter_integration_tools = available_integration_tool_names is not None
    allowed_tools = set(allowed_tool_names or set())
    filter_allowed_tools = allowed_tool_names is not None
    native_registry = mcp_tools.get_native_integration_tool_registry()
    email_registry = getattr(mcp_tools, "EMAIL_INTEGRATION_TOOL_REGISTRY", {})
    gateway_tools = {"mcp_search_tools", "mcp_call_tool"}

    definitions = list(mcp_tools.get_tool_definitions()) + list(getattr(mcp_tools, "GATEWAY_TOOL_DEFINITIONS", ()))
    seen: set[str] = set()
    items: list[dict[str, Any]] = []

    for definition in definitions:
        if not isinstance(definition, Mapping):
            continue
        function_block = definition.get("function")
        if not isinstance(function_block, Mapping):
            continue

        tool_name = str(function_block.get("name") or "").strip()
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        if filter_allowed_tools and tool_name not in allowed_tools:
            continue

        description = str(function_block.get("description") or "").strip()

        native_meta = native_registry.get(tool_name) if isinstance(native_registry, Mapping) else None
        email_meta = email_registry.get(tool_name) if isinstance(email_registry, Mapping) else None
        source_type = "internal"
        integration_type = "internal"
        source_label = "Internal tool"
        integration_backed = False

        if isinstance(native_meta, Mapping):
            integration_backed = True
            source_type = "integration"
            integration_type = str(native_meta.get("integration_type") or "").strip() or "integration"
            source_label = "Integration"
            operation_type = str(native_meta.get("operation_type") or "").strip() or McpToolOperationType.UNKNOWN
        elif isinstance(email_meta, Mapping):
            integration_backed = True
            source_type = "integration"
            integration_type = "email"
            source_label = "Integration"
            operation_type = str(email_meta.get("operation_type") or "").strip() or McpToolOperationType.UNKNOWN
        elif tool_name in gateway_tools:
            integration_backed = True
            source_type = "integration"
            integration_type = "mcp_gateway"
            source_label = "Integration"
            operation_type = McpToolOperationType.READ
        else:
            operation_type = _infer_operation_type_from_tool_name(tool_name)

        if integration_backed and filter_integration_tools and tool_name not in available_integration_tools:
            continue

        default_controls_mode = _controls_default_mode_for_operation_type(operation_type)
        controls_mode = overrides_map.get(tool_name) or default_controls_mode
        approval_hint = "Workspace override" if tool_name in overrides_map else "System default"
        effective_approval_mode = (
            McpConnectionApprovalMode.AUTO if controls_mode == "auto" else McpConnectionApprovalMode.APPROVE_ALL
        )

        items.append(
            {
                "id": f"internal:{tool_name}",
                "toolName": tool_name,
                "label": _controls_tool_label(tool_name),
                "description": description or "Tool exposed to the LLM runtime.",
                "sourceType": source_type,
                "sourceLabel": source_label,
                "integrationType": integration_type,
                "connectionId": None,
                "connectionName": None,
                "scope": "system",
                "editable": True,
                "exposedToLlm": True,
                "operationType": operation_type,
                "effectiveApprovalMode": effective_approval_mode,
                "controlsMode": controls_mode,
                "approvalHint": approval_hint,
            }
        )

    items.sort(key=lambda item: (str(item.get("sourceType") or ""), str(item.get("label") or item.get("toolName") or "")))
    return items


def _controls_agentic_operational_tool_names(
    *,
    business: BusinessProfile,
    enabled_connections: list[McpConnection],
    available_integration_tool_names: set[str],
) -> set[str]:
    feature_state = FeatureFlagService.snapshot(business)
    sub_agents_enabled = bool(getattr(feature_state, "sub_agents_v1", False))

    # Keep Controls aligned with the operational agentic surface, while excluding
    # UI/internal helpers and legacy retrieval tools.
    allowed: set[str] = {
        "search_knowledge",
        "read_knowledge",
        "search_conversation_files",
        "read_conversation_file",
        "pdf_generate",
        "pdf_merge",
        "pdf_extract_pages",
        "pdf_extract_text",
        "initiate_phone_call",
    }
    if sub_agents_enabled:
        allowed.update(
            {
                "create_agent_request",
                "create_agent_run",
                "list_agent_runs",
                "get_agent_run",
                "continue_agent_run",
            }
        )
    if enabled_connections:
        allowed.update({"mcp_search_tools", "mcp_call_tool"})
    allowed.update(available_integration_tool_names)
    return allowed


def _controls_connection_tool_items(*, business: BusinessProfile, connections: list[McpConnection]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for connection in connections:
        serialized = _serialize_mcp_connection(connection, business=business, include_tool_settings=True)
        for tool in serialized.get("toolSettings", []):
            if not isinstance(tool, Mapping):
                continue
            tool_name = str(tool.get("toolName") or "").strip()
            if not tool_name:
                continue

            operation_type = str(tool.get("operationType") or "").strip() or _infer_operation_type_from_tool_name(tool_name)
            effective_mode = str(tool.get("effectiveApprovalMode") or connection.default_approval_mode).strip() or connection.default_approval_mode
            controls_mode = _controls_mode_from_approval_mode(effective_mode, operation_type=operation_type)
            description = str(tool.get("description") or "").strip()
            source_type = "integration"
            source_label = "Integration"
            marketplace_key = str(connection.marketplace_key or "").strip()

            items.append(
                {
                    "id": f"mcp:{connection.id}:{tool_name}",
                    "toolName": tool_name,
                    "label": _controls_tool_label(tool_name),
                    "description": description or "Tool exposed from a connected MCP integration.",
                    "sourceType": source_type,
                    "sourceLabel": source_label,
                    "integrationType": marketplace_key or "mcp_connection",
                    "connectionId": str(connection.id),
                    "connectionName": connection.name,
                    "scope": "connection",
                    "editable": True,
                    "exposedToLlm": True,
                    "operationType": operation_type,
                    "effectiveApprovalMode": effective_mode,
                    "controlsMode": controls_mode,
                    "approvalHint": "Connection override",
                }
            )

    items.sort(key=lambda item: (str(item.get("connectionName") or ""), str(item.get("label") or item.get("toolName") or "")))
    return items


@csrf_protect
@require_http_methods(["GET", "POST"])
def mcp_controls_tools(request: HttpRequest) -> JsonResponse:
    payload: dict[str, Any] | None = None
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    if request.method != "GET":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or request.GET.get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method == "GET":
        with tenant_context(business.id):
            connections = list(
                McpConnection.objects.filter(
                    business_profile=business,
                    status=McpConnectionStatus.ENABLED,
                ).order_by("name")
            )
            integration_items = _controls_connection_tool_items(business=business, connections=connections)
            available_integration_tool_names = _controls_available_integration_tool_names(
                business=business,
                enabled_connections=connections,
            )
            allowed_tool_names = _controls_agentic_operational_tool_names(
                business=business,
                enabled_connections=connections,
                available_integration_tool_names=available_integration_tool_names,
            )
            internal_overrides = _load_business_tool_approval_overrides(business)
        internal_items = _controls_internal_tool_items(
            overrides=internal_overrides,
            available_integration_tool_names=available_integration_tool_names,
            allowed_tool_names=allowed_tool_names,
        )
        items = integration_items + internal_items
        summary = {
            "total": len(items),
            "integration": len([item for item in items if item.get("sourceType") == "integration"]),
            "internal": len([item for item in items if item.get("sourceType") == "internal"]),
            "editable": len([item for item in items if bool(item.get("editable"))]),
        }
        return JsonResponse(
            {
                "businessId": str(business.id),
                "items": items,
                "summary": summary,
                "approvalOptions": [
                    {"value": "auto", "label": "Auto approve"},
                    {"value": "confirm", "label": "Always confirm"},
                ],
            },
            status=HTTPStatus.OK,
        )

    assert payload is not None
    updates = payload.get("updates")
    if not isinstance(updates, list):
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "updates must be a list."}, status=HTTPStatus.BAD_REQUEST)

    applied: list[dict[str, Any]] = []
    allowed_operations = {choice for choice, _ in McpToolOperationType.choices}
    with tenant_context(business.id):
        enabled_connections = list(
            McpConnection.objects.filter(
                business_profile=business,
                status=McpConnectionStatus.ENABLED,
            )
        )
        connection_map = {
            str(conn.id): conn
            for conn in enabled_connections
        }
        available_integration_tool_names = _controls_available_integration_tool_names(
            business=business,
            enabled_connections=enabled_connections,
        )
        allowed_tool_names = _controls_agentic_operational_tool_names(
            business=business,
            enabled_connections=enabled_connections,
            available_integration_tool_names=available_integration_tool_names,
        )
        internal_items = _controls_internal_tool_items(
            overrides=_load_business_tool_approval_overrides(business),
            available_integration_tool_names=available_integration_tool_names,
            allowed_tool_names=allowed_tool_names,
        )
        internal_tool_names = {str(item.get("toolName") or "").strip() for item in internal_items if str(item.get("toolName") or "").strip()}
        internal_defaults = {
            str(item.get("toolName") or "").strip(): _controls_default_mode_for_operation_type(str(item.get("operationType") or "").strip())
            for item in internal_items
            if str(item.get("toolName") or "").strip()
        }
        internal_overrides = _load_business_tool_approval_overrides(business)
        internal_changed = False

        for item in updates:
            if not isinstance(item, Mapping):
                continue
            tool_name = str(item.get("toolName") or "").strip()
            controls_mode = str(item.get("approvalMode") or "").strip().lower()
            if not tool_name or controls_mode not in {"auto", "confirm"}:
                continue

            connection_id = str(item.get("connectionId") or "").strip()
            connection = connection_map.get(connection_id)
            if connection is None:
                if tool_name not in internal_tool_names:
                    continue
                default_mode = internal_defaults.get(tool_name, "confirm")
                current_mode = internal_overrides.get(tool_name)
                if controls_mode == default_mode:
                    if tool_name in internal_overrides:
                        del internal_overrides[tool_name]
                        internal_changed = True
                elif current_mode != controls_mode:
                    internal_overrides[tool_name] = controls_mode
                    internal_changed = True
                applied.append(
                    {
                        "toolName": tool_name,
                        "approvalMode": controls_mode,
                        "scope": "system",
                    }
                )
                continue

            approval_mode = (
                McpConnectionApprovalMode.AUTO
                if controls_mode == "auto"
                else McpConnectionApprovalMode.APPROVE_ALL
            )
            inferred_operation = _infer_operation_type_from_tool_name(tool_name)
            operation_value = inferred_operation if inferred_operation in allowed_operations else McpToolOperationType.UNKNOWN
            setting, _ = McpConnectionToolSetting.objects.get_or_create(
                connection=connection,
                tool_name=tool_name,
                defaults={
                    "operation_type": operation_value,
                    "description": "",
                    "approval_mode": approval_mode,
                },
            )
            if setting.operation_type not in allowed_operations:
                setting.operation_type = operation_value
            setting.approval_mode = approval_mode
            setting.save(update_fields=["operation_type", "approval_mode", "updated_at"])
            applied.append(
                {
                    "connectionId": connection_id,
                    "toolName": tool_name,
                    "approvalMode": controls_mode,
                }
            )

        if internal_changed:
            _save_business_tool_approval_overrides(business=business, overrides=internal_overrides)

    return JsonResponse({"applied": applied}, status=HTTPStatus.OK)


@csrf_protect
@require_http_methods(["GET", "POST"])
def mcp_agent_approval_defaults(request: HttpRequest) -> JsonResponse:
    payload: dict[str, Any] | None = None
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    if request.method != "GET":
        payload, error = _parse_json_body(request)
        if error:
            return error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or request.GET.get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method == "GET":
        with tenant_context(business.id):
            agents = list(AgentProfile.objects.filter(business_profile=business).order_by("created_at"))
        return JsonResponse(
            {
                "businessId": str(business.id),
                "approvalModeOptions": [
                    {"value": choice, "label": label}
                    for choice, label in McpConnectionApprovalMode.choices
                ],
                "agents": [
                    {
                        "id": str(agent.id),
                        "name": agent.name,
                        "slug": agent.slug,
                        "status": agent.status,
                        "statusLabel": agent.get_status_display(),
                        "mcpDefaultApprovalMode": getattr(agent, "mcp_default_approval_mode", None),
                    }
                    for agent in agents
                ],
            },
            status=HTTPStatus.OK,
        )

    assert payload is not None
    agent_id = payload.get("agentId") or payload.get("agent_id")
    default_mode = payload.get("defaultApprovalMode") or payload.get("default_approval_mode")
    if not agent_id:
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "agentId is required."}, status=HTTPStatus.BAD_REQUEST)

    try:
        agent_uuid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
    except (TypeError, ValueError):
        return JsonResponse({"error": "VALIDATION_ERROR", "message": "agentId must be a valid UUID."}, status=HTTPStatus.BAD_REQUEST)

    if default_mode is None or str(default_mode).strip() == "" or str(default_mode).strip().lower() == "inherit":
        default_mode_value: str | None = None
    else:
        default_mode_value = str(default_mode).strip()
        allowed = {choice for choice, _ in McpConnectionApprovalMode.choices}
        if default_mode_value not in allowed:
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "defaultApprovalMode is invalid."}, status=HTTPStatus.BAD_REQUEST)

    with tenant_context(business.id):
        agent = AgentProfile.objects.filter(id=agent_uuid, business_profile=business).first()
        if agent is None:
            return JsonResponse({"error": "AGENT_NOT_FOUND", "message": "Agent profile not found."}, status=HTTPStatus.NOT_FOUND)
        agent.mcp_default_approval_mode = default_mode_value
        agent.save(update_fields=["mcp_default_approval_mode", "updated_at"])

    return JsonResponse(
        {
            "agent": {
                "id": str(agent.id),
                "mcpDefaultApprovalMode": agent.mcp_default_approval_mode,
            }
        },
        status=HTTPStatus.OK,
    )
