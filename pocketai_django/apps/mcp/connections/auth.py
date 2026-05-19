from __future__ import annotations

import logging

from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    McpConnectionAuthType,
    McpConnectionStatus,
)
from apps.accounts.oauth_helpers import OAuthFlowError, ensure_fresh_oauth_credentials
from apps.mcp.models import McpConnection


logger = logging.getLogger(__name__)


def list_enabled_mcp_connections_for_agent(agent: AgentProfile) -> list[McpConnection]:
    business = agent.business_profile
    if not business:
        return []
    with tenant_context(business.id):
        return list(
            McpConnection.objects.filter(
                business_profile=business,
                status=McpConnectionStatus.ENABLED,
            )
            .exclude(agent_opt_outs__agent_profile=agent)
            .order_by("name")
        )


def mcp_connection_auth_headers(connection: McpConnection) -> dict[str, str]:
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
