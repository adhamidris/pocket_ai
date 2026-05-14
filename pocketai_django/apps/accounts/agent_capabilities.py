from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from apps.accounts.action_controls import list_action_settings
from apps.accounts.models import (
    AgentProfile,
    KnowledgeStatus,
    McpConnectionApprovalMode,
    McpConnectionStatus,
    McpToolOperationType,
)
from apps.knowledge.models import KnowledgeUpload
from apps.integrations.models import (
    AgentEmailAccountPolicyOverride,
    EmailAccount,
    EmailAccountStatus,
    EmailSendMode,
    IntegrationAccount,
    IntegrationAccountStatus,
)
from apps.mcp.connectors import _infer_operation_type_from_tool_name
from apps.mcp.models import (
    AgentMcpToolSetting,
    McpConnection,
    McpConnectionAgentOptOut,
    McpConnectionToolSetting,
)


@dataclass(frozen=True)
class AgentCapabilityItem:
    category: str
    key: str
    label: str
    availability: str
    execution_policy: str
    source: str
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentCapabilityGroup:
    category: str
    label: str
    items: Sequence[AgentCapabilityItem]


@dataclass(frozen=True)
class AgentCapabilitySummary:
    knowledge_scope: str
    knowledge_document_count: int
    native_action_total: int
    native_action_enabled: int
    native_integration_total: int
    native_integration_enabled: int
    native_tool_total: int
    native_tool_enabled: int
    mcp_connection_total: int
    mcp_connection_enabled: int
    mcp_connection_opted_out: int
    mcp_tool_total: int
    mcp_tool_auto: int
    mcp_tool_approval_required: int
    restrictions_total: int
    default_approval_mode: str | None


@dataclass(frozen=True)
class AgentCapabilityGraph:
    summary: AgentCapabilitySummary
    groups: Sequence[AgentCapabilityGroup]


def _execution_policy_for_tool(*, approval_mode: str, operation_type: str) -> str:
    if approval_mode == McpConnectionApprovalMode.AUTO:
        return "auto"
    if approval_mode == McpConnectionApprovalMode.APPROVE_ALL:
        return "approve_all"
    if approval_mode == McpConnectionApprovalMode.APPROVE_WRITES:
        if operation_type == McpToolOperationType.READ:
            return "auto"
        return "approve_writes"
    return "blocked"


def _operation_type_for_tool(
    *,
    tool_name: str,
    agent_setting: AgentMcpToolSetting | None,
    connection_setting: McpConnectionToolSetting | None,
) -> str:
    for candidate in (
        getattr(agent_setting, "operation_type", None),
        getattr(connection_setting, "operation_type", None),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return str(_infer_operation_type_from_tool_name(tool_name))


def _tool_source(
    *,
    agent_setting: AgentMcpToolSetting | None,
    connection_setting: McpConnectionToolSetting | None,
    agent_default_mode: str,
) -> str:
    if agent_setting and getattr(agent_setting, "approval_mode", None):
        return "agent_override"
    if connection_setting and getattr(connection_setting, "approval_mode", None):
        return "connection_override"
    if agent_default_mode:
        return "agent_default"
    return "connection_default"


def _tool_reason(*, execution_policy: str, connection_name: str, operation_type: str) -> str:
    if execution_policy == "auto":
        return f"Available automatically through {connection_name}."
    if execution_policy == "approve_all":
        return f"Requires approval for all operations from {connection_name}."
    if execution_policy == "approve_writes":
        if operation_type == McpToolOperationType.WRITE:
            return f"Requires approval before write operations on {connection_name}."
        return f"Requires approval before using this tool on {connection_name}."
    return f"Blocked for this agent on {connection_name}."


def _effective_email_send_mode_for_agent(*, agent: AgentProfile, email_account: EmailAccount) -> tuple[str, str]:
    send_mode = str(getattr(email_account, "send_mode", "") or "").strip() or str(EmailSendMode.DRAFT_APPROVAL)
    override = AgentEmailAccountPolicyOverride.objects.filter(
        agent_profile=agent,
        email_account=email_account,
    ).first()
    if override:
        override_mode = str(getattr(override, "send_mode", "") or "").strip()
        if override_mode:
            return override_mode, "agent_override"
    return send_mode, "default"


def _native_account_reason(*, account_label: str, source: str, send_mode: str | None = None) -> str:
    if send_mode:
        mode_label = "auto-send" if str(send_mode).strip().lower() == EmailSendMode.AUTO_SEND else "draft + approval"
        if source == "agent_override":
            return f"{account_label} is connected. Outbound email uses an agent-specific {mode_label} policy."
        return f"{account_label} is connected. Outbound email uses {mode_label}."
    return f"{account_label} is connected and available to this agent."


def _native_tool_reason(
    *,
    account_label: str,
    tool_label: str,
    enabled: bool,
    operation_type: str,
    is_email_send_tool: bool = False,
    email_send_mode: str | None = None,
) -> str:
    if not enabled:
        return f"{tool_label} is disabled for {account_label}."
    if is_email_send_tool:
        if str(email_send_mode or "").strip().lower() == EmailSendMode.AUTO_SEND:
            return f"{tool_label} can run automatically for {account_label}."
        return f"{tool_label} requires approval before sending from {account_label}."
    if operation_type == McpToolOperationType.WRITE:
        return f"{tool_label} is available as a write operation through {account_label}."
    return f"{tool_label} is available through {account_label}."


def resolve_agent_capabilities(agent: AgentProfile) -> AgentCapabilityGraph:
    business = agent.business_profile
    from apps.mcp import tools as mcp_tools

    active_documents_qs = (
        KnowledgeUpload.objects.filter(business_profile=business, is_active=True)
        .exclude(status=KnowledgeStatus.ARCHIVED)
        .only("id")
    )
    business_document_count = active_documents_qs.count()
    selected_document_ids = tuple(agent.allowed_documents.filter(id__in=active_documents_qs).values_list("id", flat=True))

    if business_document_count == 0:
        knowledge_scope = "none"
        knowledge_count = 0
        knowledge_availability = "disabled"
        knowledge_source = "system"
        knowledge_reason = "No active knowledge documents are available for this business."
    elif selected_document_ids:
        knowledge_scope = "selected"
        knowledge_count = len(selected_document_ids)
        knowledge_availability = "restricted"
        knowledge_source = "agent_override"
        knowledge_reason = "This agent is limited to a selected subset of business knowledge."
    else:
        knowledge_scope = "all"
        knowledge_count = business_document_count
        knowledge_availability = "enabled"
        knowledge_source = "default"
        knowledge_reason = "This agent can use all active business knowledge."

    knowledge_group = AgentCapabilityGroup(
        category="knowledge",
        label="Knowledge",
        items=(
            AgentCapabilityItem(
                category="knowledge",
                key="knowledge.read",
                label="Knowledge access",
                availability=knowledge_availability,
                execution_policy="auto" if knowledge_availability != "disabled" else "blocked",
                source=knowledge_source,
                reason=knowledge_reason,
                metadata={
                    "scope": knowledge_scope,
                    "documentCount": knowledge_count,
                    "businessDocumentCount": business_document_count,
                },
            ),
        ),
    )

    native_items: list[AgentCapabilityItem] = []
    native_enabled = 0
    for action in list_action_settings(agent):
        is_override = agent.action_permissions.filter(action_key=action.key).exists()
        if action.enabled:
            native_enabled += 1
        native_items.append(
            AgentCapabilityItem(
                category="native_action",
                key=action.key,
                label=action.label,
                availability="enabled" if action.enabled else "disabled",
                execution_policy="auto" if action.enabled else "blocked",
                source="agent_override" if is_override else "default",
                reason=(
                    "Enabled via the internal native action registry."
                    if action.enabled
                    else "Disabled for this agent via native action permission override."
                ),
                metadata={"description": action.description},
            )
        )

    native_group = AgentCapabilityGroup(
        category="native_action",
        label="Native actions",
        items=tuple(native_items),
    )

    connected_email_accounts = list(
        EmailAccount.objects.filter(
            business_profile=business,
            user=agent.user,
            status=EmailAccountStatus.CONNECTED,
        )
        .only("id", "provider", "email_address", "status", "send_mode", "metadata")
        .order_by("provider", "email_address")
    )
    connected_native_accounts = list(
        IntegrationAccount.objects.filter(
            business_profile=business,
            user=agent.user,
            status=IntegrationAccountStatus.CONNECTED,
        )
        .only("id", "integration_type", "provider", "account_identifier", "status", "metadata")
        .order_by("integration_type", "account_identifier")
    )

    native_connection_items: list[AgentCapabilityItem] = []
    native_tool_items: list[AgentCapabilityItem] = []
    native_tool_total = 0
    native_tool_enabled = 0

    for account in connected_email_accounts:
        provider = str(getattr(account, "provider", "") or "").strip().lower()
        account_label = str(getattr(account, "email_address", "") or "").strip() or provider or "Email account"
        send_mode, send_mode_source = _effective_email_send_mode_for_agent(agent=agent, email_account=account)
        email_tools = mcp_tools.get_email_integration_tools_for_provider(provider)
        enabled_map = mcp_tools.get_email_tool_enabled_map_for_account(
            account,
            tool_names=[str(item.get("toolName") or "").strip() for item in email_tools],
        )

        native_connection_items.append(
            AgentCapabilityItem(
                category="native_integration",
                key=f"native_integration:email:{account.id}",
                label=account_label,
                availability="enabled",
                execution_policy="auto" if str(send_mode).strip().lower() == EmailSendMode.AUTO_SEND else "approve_writes",
                source=send_mode_source,
                reason=_native_account_reason(account_label=account_label, source=send_mode_source, send_mode=send_mode),
                metadata={
                    "accountId": str(account.id),
                    "accountKind": "email",
                    "provider": provider,
                    "toolCount": len(email_tools),
                    "sendMode": send_mode,
                },
            )
        )

        for row in email_tools:
            tool_name = str(row.get("toolName") or "").strip()
            if not tool_name:
                continue
            native_tool_total += 1
            enabled = bool(enabled_map.get(tool_name, True))
            if enabled:
                native_tool_enabled += 1
            operation_type = str(row.get("operationType") or McpToolOperationType.UNKNOWN).strip() or McpToolOperationType.UNKNOWN
            is_email_send_tool = tool_name == "email_send_draft"
            execution_policy = "blocked"
            availability = "disabled"
            if enabled:
                availability = "enabled"
                if is_email_send_tool and str(send_mode).strip().lower() != EmailSendMode.AUTO_SEND:
                    execution_policy = "approve_writes"
                    availability = "restricted"
                else:
                    execution_policy = "auto"

            native_tool_items.append(
                AgentCapabilityItem(
                    category="native_tool",
                    key=f"native_tool:email:{account.id}:{tool_name}",
                    label=str(row.get("label") or tool_name),
                    availability=availability,
                    execution_policy=execution_policy,
                    source=send_mode_source if is_email_send_tool else "default",
                    reason=_native_tool_reason(
                        account_label=account_label,
                        tool_label=str(row.get("label") or tool_name),
                        enabled=enabled,
                        operation_type=operation_type,
                        is_email_send_tool=is_email_send_tool,
                        email_send_mode=send_mode,
                    ),
                    metadata={
                        "accountId": str(account.id),
                        "accountKind": "email",
                        "accountLabel": account_label,
                        "provider": provider,
                        "toolName": tool_name,
                        "operationType": operation_type,
                        "sendMode": send_mode,
                    },
                )
            )

    for account in connected_native_accounts:
        integration_type = str(getattr(account, "integration_type", "") or "").strip()
        account_label = str(getattr(account, "account_identifier", "") or "").strip() or integration_type or "Integration account"
        native_tools = mcp_tools.get_native_integration_tools_for_type(integration_type)
        enabled_map = mcp_tools.get_native_tool_enabled_map_for_account(
            account,
            tool_names=[str(item.get("toolName") or "").strip() for item in native_tools],
        )

        native_connection_items.append(
            AgentCapabilityItem(
                category="native_integration",
                key=f"native_integration:{integration_type}:{account.id}",
                label=account_label,
                availability="enabled",
                execution_policy="auto",
                source="default",
                reason=_native_account_reason(account_label=account_label, source="default"),
                metadata={
                    "accountId": str(account.id),
                    "accountKind": "native",
                    "integrationType": integration_type,
                    "provider": str(getattr(account, "provider", "") or "").strip(),
                    "toolCount": len(native_tools),
                },
            )
        )

        for row in native_tools:
            tool_name = str(row.get("toolName") or "").strip()
            if not tool_name:
                continue
            native_tool_total += 1
            enabled = bool(enabled_map.get(tool_name, True))
            if enabled:
                native_tool_enabled += 1
            operation_type = str(row.get("operationType") or McpToolOperationType.UNKNOWN).strip() or McpToolOperationType.UNKNOWN
            native_tool_items.append(
                AgentCapabilityItem(
                    category="native_tool",
                    key=f"native_tool:{integration_type}:{account.id}:{tool_name}",
                    label=str(row.get("label") or tool_name),
                    availability="enabled" if enabled else "disabled",
                    execution_policy="auto" if enabled else "blocked",
                    source="default",
                    reason=_native_tool_reason(
                        account_label=account_label,
                        tool_label=str(row.get("label") or tool_name),
                        enabled=enabled,
                        operation_type=operation_type,
                    ),
                    metadata={
                        "accountId": str(account.id),
                        "accountKind": "native",
                        "accountLabel": account_label,
                        "integrationType": integration_type,
                        "provider": str(getattr(account, "provider", "") or "").strip(),
                        "toolName": tool_name,
                        "operationType": operation_type,
                    },
                )
            )

    all_connections = list(
        McpConnection.objects.filter(business_profile=business)
        .only("id", "name", "status", "default_approval_mode", "marketplace_key", "metadata")
        .order_by("name")
    )
    opted_out_ids = set(
        McpConnectionAgentOptOut.objects.filter(agent_profile=agent, connection__business_profile=business).values_list(
            "connection_id",
            flat=True,
        )
    )
    enabled_connection_ids = [connection.id for connection in all_connections if connection.status == McpConnectionStatus.ENABLED]

    connection_settings = {
        (setting.connection_id, setting.tool_name): setting
        for setting in McpConnectionToolSetting.objects.filter(connection_id__in=enabled_connection_ids)
    }
    agent_tool_settings = {
        (setting.connection_id, setting.tool_name): setting
        for setting in AgentMcpToolSetting.objects.filter(agent_profile=agent, connection_id__in=enabled_connection_ids)
    }

    agent_default_mode = str(getattr(agent, "mcp_default_approval_mode", "") or "").strip()

    connection_items: list[AgentCapabilityItem] = []
    tool_items: list[AgentCapabilityItem] = []
    enabled_connections = 0
    opted_out_connections = 0
    total_tools = 0
    auto_tools = 0
    approval_required_tools = 0

    for connection in all_connections:
        connection_id = connection.id
        is_enabled_connection = connection.status == McpConnectionStatus.ENABLED
        is_opted_out = connection_id in opted_out_ids
        tool_cache = (
            connection.metadata.get("tool_cache")
            if isinstance(getattr(connection, "metadata", None), Mapping)
            and isinstance(connection.metadata.get("tool_cache"), Mapping)
            else {}
        )
        cached_tools = tool_cache.get("tools") if isinstance(tool_cache.get("tools"), list) else []

        if not is_enabled_connection:
            availability = "disabled"
            execution_policy = "blocked"
            source = "system"
            reason = "This MCP connection is disabled at the business level."
        elif is_opted_out:
            availability = "disabled"
            execution_policy = "blocked"
            source = "agent_override"
            reason = "This agent is explicitly opted out of this MCP connection."
            opted_out_connections += 1
        else:
            availability = "enabled"
            execution_policy = agent_default_mode or connection.default_approval_mode
            source = "agent_default" if agent_default_mode else "connection_default"
            reason = "This agent inherits access to this MCP connection."
            enabled_connections += 1

        connection_items.append(
            AgentCapabilityItem(
                category="mcp_connection",
                key=f"mcp_connection:{connection_id}",
                label=connection.name,
                availability=availability,
                execution_policy=execution_policy,
                source=source,
                reason=reason,
                metadata={
                    "connectionId": str(connection_id),
                    "marketplaceKey": connection.marketplace_key or "",
                    "toolCount": len(cached_tools),
                    "status": connection.status,
                    "defaultApprovalMode": connection.default_approval_mode,
                },
            )
        )

        if not is_enabled_connection or is_opted_out:
            continue

        for raw_tool in cached_tools:
            if not isinstance(raw_tool, Mapping):
                continue
            tool_name = str(raw_tool.get("name") or "").strip()
            if not tool_name:
                continue
            total_tools += 1
            agent_setting = agent_tool_settings.get((connection_id, tool_name))
            connection_setting = connection_settings.get((connection_id, tool_name))
            operation_type = _operation_type_for_tool(
                tool_name=tool_name,
                agent_setting=agent_setting,
                connection_setting=connection_setting,
            )
            approval_mode = (
                str(getattr(agent_setting, "approval_mode", "") or "").strip()
                or str(getattr(connection_setting, "approval_mode", "") or "").strip()
                or agent_default_mode
                or connection.default_approval_mode
            )
            execution_policy = _execution_policy_for_tool(
                approval_mode=approval_mode,
                operation_type=operation_type,
            )
            if execution_policy == "auto":
                auto_tools += 1
            else:
                approval_required_tools += 1
            tool_items.append(
                AgentCapabilityItem(
                    category="mcp_tool",
                    key=f"mcp_tool:{connection_id}:{tool_name}",
                    label=str(raw_tool.get("title") or tool_name),
                    availability="enabled" if execution_policy == "auto" else "restricted",
                    execution_policy=execution_policy,
                    source=_tool_source(
                        agent_setting=agent_setting,
                        connection_setting=connection_setting,
                        agent_default_mode=agent_default_mode,
                    ),
                    reason=_tool_reason(
                        execution_policy=execution_policy,
                        connection_name=connection.name,
                        operation_type=operation_type,
                    ),
                    metadata={
                        "connectionId": str(connection_id),
                        "connectionName": connection.name,
                        "toolName": tool_name,
                        "operationType": operation_type,
                        "approvalMode": approval_mode,
                    },
                )
            )

    restrictions_total = 0
    if knowledge_scope in {"selected", "none"}:
        restrictions_total += 1
    restrictions_total += sum(1 for item in native_items if item.availability == "disabled")
    restrictions_total += sum(1 for item in native_tool_items if item.availability in {"disabled", "restricted"})
    restrictions_total += opted_out_connections
    restrictions_total += approval_required_tools
    restrictions_total += sum(1 for item in connection_items if item.availability == "disabled" and item.source == "system")

    summary = AgentCapabilitySummary(
        knowledge_scope=knowledge_scope,
        knowledge_document_count=knowledge_count,
        native_action_total=len(native_items),
        native_action_enabled=native_enabled,
        native_integration_total=len(native_connection_items),
        native_integration_enabled=len(native_connection_items),
        native_tool_total=native_tool_total,
        native_tool_enabled=native_tool_enabled,
        mcp_connection_total=len(all_connections),
        mcp_connection_enabled=enabled_connections,
        mcp_connection_opted_out=opted_out_connections,
        mcp_tool_total=total_tools,
        mcp_tool_auto=auto_tools,
        mcp_tool_approval_required=approval_required_tools,
        restrictions_total=restrictions_total,
        default_approval_mode=agent_default_mode or None,
    )

    groups = (
        knowledge_group,
        native_group,
        AgentCapabilityGroup(
            category="native_integration",
            label="Native integrations",
            items=tuple(native_connection_items),
        ),
        AgentCapabilityGroup(
            category="native_tool",
            label="Native tools",
            items=tuple(native_tool_items),
        ),
        AgentCapabilityGroup(
            category="mcp_connection",
            label="MCP connections",
            items=tuple(connection_items),
        ),
        AgentCapabilityGroup(
            category="mcp_tool",
            label="MCP tools",
            items=tuple(tool_items),
        ),
    )

    return AgentCapabilityGraph(summary=summary, groups=groups)
