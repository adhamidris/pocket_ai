from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    McpConnectionAuthType,
    McpToolOperationType,
)
from apps.accounts.oauth_helpers import OAuthFlowError, ensure_fresh_oauth_credentials
from apps.mcp.connectors import _infer_operation_type_from_tool_name
from apps.mcp.models import (
    McpConnection,
    McpConnectionAgentOptOut,
    McpConnectionTestJob,
    McpConnectionTestJobStatus,
    McpConnectionToolSetting,
)

logger = logging.getLogger(__name__)


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
