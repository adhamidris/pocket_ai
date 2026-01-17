from __future__ import annotations

import ipaddress
import json
import logging
import socket
import uuid
from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    McpConnection,
    McpConnectionAgentOptOut,
    McpConnectionAuditAction,
    McpConnectionAuditEvent,
    McpConnectionAuthType,
    McpConnectionSourceType,
    McpConnectionStatus,
)
from apps.mcp.remote_client import McpRemoteError, test_mcp_server


logger = logging.getLogger(__name__)


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


def _mcp_auth_headers(connection: McpConnection) -> dict[str, str]:
    if connection.auth_type == McpConnectionAuthType.NONE:
        return {}
    credentials = connection.credentials or {}
    if connection.auth_type == McpConnectionAuthType.BEARER:
        token = str(credentials.get("token") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}
    if connection.auth_type == McpConnectionAuthType.HEADER:
        header_name = str(credentials.get("header_name") or "").strip()
        header_value = str(credentials.get("header_value") or "").strip()
        if header_name and header_value:
            return {header_name: header_value}
    return {}


def _serialize_mcp_connection(connection: McpConnection, *, business: BusinessProfile) -> dict[str, Any]:
    metadata = connection.metadata or {}
    tool_cache = metadata.get("tool_cache") if isinstance(metadata.get("tool_cache"), dict) else {}
    tool_count = tool_cache.get("tool_count") or tool_cache.get("count") or metadata.get("tool_count") or 0
    last_tested_at = tool_cache.get("tested_at") or metadata.get("last_tested_at")
    last_error = tool_cache.get("error") or metadata.get("last_error")

    total_agents = AgentProfile.objects.filter(business_profile=business).count()
    opt_out_count = McpConnectionAgentOptOut.objects.filter(connection=connection).count()
    assigned_agents = max(0, total_agents - opt_out_count)

    auth_configured = connection.auth_type != McpConnectionAuthType.NONE and connection.has_credentials()
    header_name = None
    if connection.auth_type == McpConnectionAuthType.HEADER:
        header_name = str((connection.credentials or {}).get("header_name") or "").strip() or None

    return {
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
        "toolCount": int(tool_count) if str(tool_count).isdigit() else tool_count,
        "lastTestedAt": last_tested_at,
        "lastError": last_error,
        "agents": {
            "total": total_agents,
            "assigned": assigned_agents,
            "optedOut": opt_out_count,
        },
        "createdAt": connection.created_at.isoformat() if connection.created_at else None,
        "updatedAt": connection.updated_at.isoformat() if connection.updated_at else None,
    }


def _mcp_marketplace_catalog() -> list[dict[str, Any]]:
    """
    Curated MCP marketplace (templates).

    Note: Entries are templates—customers still supply their own server URL unless
    an entry includes a hosted URL.
    """

    return [
        {
            "key": "context7",
            "name": "Context7 Docs",
            "description": "Up-to-date library documentation tools via MCP.",
            "recommendedAuth": "none",
            "serverUrl": "",
            "docsUrl": "https://context7.com/",
            "badge": "Popular",
        },
        {
            "key": "github",
            "name": "GitHub (MCP)",
            "description": "Issue/PR automation via a GitHub MCP server deployment.",
            "recommendedAuth": "bearer",
            "serverUrl": "",
            "docsUrl": "https://modelcontextprotocol.io/examples",
            "badge": "Template",
        },
        {
            "key": "slack",
            "name": "Slack (MCP)",
            "description": "Messaging and channel automation via a Slack MCP server deployment.",
            "recommendedAuth": "bearer",
            "serverUrl": "",
            "docsUrl": "https://modelcontextprotocol.io/examples",
            "badge": "Template",
        },
        {
            "key": "postgres",
            "name": "Postgres (MCP)",
            "description": "Query and analytics workflows via a Postgres MCP server deployment.",
            "recommendedAuth": "bearer",
            "serverUrl": "",
            "docsUrl": "https://modelcontextprotocol.io/examples",
            "badge": "Template",
        },
    ]


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
        with tenant_context(business.id):
            connections = list(McpConnection.objects.filter(business_profile=business).order_by("name"))
            connections_payload = [_serialize_mcp_connection(connection, business=business) for connection in connections]
        return JsonResponse(
            {
                "businessId": str(business.id),
                "dashboardUrl": "/dashboard/mcp/",
                "connections": connections_payload,
                "marketplace": _mcp_marketplace_catalog(),
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

    status_value = (payload or {}).get("enabled")
    status = McpConnectionStatus.ENABLED if bool(status_value) else McpConnectionStatus.DISABLED

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

        if auth_payload is not None or auth_type_value is not None:
            auth_type = desired_auth_type
            if auth_type == McpConnectionAuthType.BEARER:
                token = str((auth_payload or {}).get("token") or (auth_payload or {}).get("bearerToken") or "").strip()
                if token:
                    connection.credentials = {"token": token}
            elif auth_type == McpConnectionAuthType.HEADER:
                header_name = str((auth_payload or {}).get("headerName") or "").strip()
                header_value = str((auth_payload or {}).get("headerValue") or "").strip()
                if header_name and header_value:
                    connection.credentials = {"header_name": header_name, "header_value": header_value}
            elif auth_type == McpConnectionAuthType.NONE:
                connection.credentials = {}
            connection.save(update_fields=["credentials_encrypted", "credentials_key_version", "credentials_last_rotated_at", "credential_error_count", "updated_at"])

        _log_mcp_audit(
            business=business,
            connection=connection,
            actor=request.user,
            action=McpConnectionAuditAction.UPDATED,
            description="MCP connection updated.",
            metadata={"fields": sorted(list(updates.keys()))},
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
        tested_at = timezone.now().isoformat()
        with tenant_context(business.id):
            metadata = dict(connection.metadata or {})
            metadata["tool_cache"] = {
                "tested_at": tested_at,
                "error": str(exc)[:500],
                "tool_count": 0,
            }
            connection.metadata = metadata
            connection.save(update_fields=["metadata", "updated_at"])
            _log_mcp_audit(
                business=business,
                connection=connection,
                actor=request.user,
                action=McpConnectionAuditAction.UPDATED,
                description="MCP connection test failed.",
                metadata={"error": str(exc)[:500]},
            )
        return JsonResponse({"error": "MCP_TEST_FAILED", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

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
        metadata["tool_cache"] = {
            "tested_at": tested_at,
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
