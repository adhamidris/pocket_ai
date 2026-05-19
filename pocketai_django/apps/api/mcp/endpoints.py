from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from http import HTTPStatus
from typing import Any, Mapping

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    McpConnectionApprovalMode,
    McpConnectionAuditAction,
    McpConnectionAuthType,
    McpConnectionSourceType,
    McpConnectionStatus,
    McpToolOperationType,
)
from apps.api.mcp.audit import _log_mcp_audit
from apps.api.mcp.controls import (
    _controls_agentic_operational_tool_names,
    _controls_available_integration_tool_names,
    _controls_connection_tool_items,
    _controls_default_mode_for_operation_type,
    _controls_internal_tool_items,
    _load_business_tool_approval_overrides,
    _save_business_tool_approval_overrides,
)
from apps.api.mcp.marketplace import (
    _MCP_SURFACE_INTEGRATIONS,
    _extract_setup_fields,
    _filter_marketplace_by_industry,
    _get_common_tools,
    _get_email_accounts_payload,
    _get_industry_display_names,
    _get_integration_accounts_payload,
    _get_marketplace_categories,
    _is_native_oauth_marketplace_item,
    _mcp_marketplace_catalog,
    _validate_setup_fields,
)
from apps.api.mcp.serializers import (
    _mcp_auth_headers,
    _serialize_mcp_connection,
    _serialize_tool_setting,
)
from apps.api.mcp.shared import (
    _parse_json_body,
    _resolve_business_for_request,
    _validate_mcp_server_url,
)
from apps.mcp.connectors import _infer_operation_type_from_tool_name
from apps.mcp.models import (
    McpConnection,
    McpConnectionAgentOptOut,
    McpConnectionTestJob,
    McpConnectionTestJobStatus,
    McpConnectionToolSetting,
)
from apps.mcp.remote_client import McpRemoteError, test_mcp_server

logger = logging.getLogger(__name__)


# Tool cache TTL in hours (default: 24 hours)
MCP_TOOL_CACHE_TTL_HOURS = getattr(settings, "MCP_TOOL_CACHE_TTL_HOURS", 24)

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
                "dashboardUrl": "/dashboard/connectors/",
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
