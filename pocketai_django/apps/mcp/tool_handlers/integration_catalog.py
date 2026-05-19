"""
Native/email integration tool catalog and enablement policy helpers.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from apps.accounts.models import (
    EmailAccountProvider,
    EmailAccountStatus,
    IntegrationAccountStatus,
    IntegrationType,
    McpToolOperationType,
)
from apps.conversations.models import Conversation
from apps.integrations.models import EmailAccount, IntegrationAccount

from .native_integration import (
    _conversation_actor_user_uuid,
    _integration_error,
    _resolve_integration_account_for_tool,
)


# Native integration tool policy metadata consumed by the orchestrator.
NATIVE_INTEGRATION_TOOL_REGISTRY: dict[str, dict[str, object]] = {
    # Google Calendar
    "calendar_list_events": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "calendar_get_event": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "calendar_create_event": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "calendar_update_event": {
        "integration_type": IntegrationType.GOOGLE_CALENDAR,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # Google Drive
    "drive_search_files": {
        "integration_type": IntegrationType.GOOGLE_DRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "drive_get_file": {
        "integration_type": IntegrationType.GOOGLE_DRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "drive_list_files": {
        "integration_type": IntegrationType.GOOGLE_DRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # OneDrive
    "onedrive_search_files": {
        "integration_type": IntegrationType.ONEDRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "onedrive_get_file": {
        "integration_type": IntegrationType.ONEDRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "onedrive_list_files": {
        "integration_type": IntegrationType.ONEDRIVE,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # Slack
    "slack_list_channels": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "slack_read_channel": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "slack_send_message": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "slack_search_messages": {
        "integration_type": IntegrationType.SLACK,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    # HubSpot
    "hubspot_search_contacts": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "hubspot_get_contact": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "hubspot_create_contact": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "hubspot_search_deals": {
        "integration_type": IntegrationType.HUBSPOT,
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
}

EMAIL_PROVIDER_INTEGRATION_TYPES: dict[str, str] = {
    EmailAccountProvider.GOOGLE: "google_email",
    EmailAccountProvider.MICROSOFT: "microsoft_email",
}

EMAIL_INTEGRATION_TOOL_REGISTRY: dict[str, dict[str, object]] = {
    "email_search": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_get_message": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_get_thread": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.READ,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_create_draft": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
    "email_send_draft": {
        "providers": [EmailAccountProvider.GOOGLE, EmailAccountProvider.MICROSOFT],
        "operation_type": McpToolOperationType.WRITE,
        "requires_connected_account": True,
        "requires_actor_user_binding": True,
    },
}

_NATIVE_TOOL_PRESENTATION: dict[str, tuple[str, str]] = {
    "calendar_list_events": ("List calendar events", "Read upcoming events from the connected calendar."),
    "calendar_get_event": ("Get event details", "Read details for a specific calendar event."),
    "calendar_create_event": ("Create calendar event", "Create a new event in the connected calendar."),
    "calendar_update_event": ("Update calendar event", "Modify an existing calendar event."),
    "drive_search_files": ("Search Drive files", "Search files in the connected Google Drive account."),
    "drive_get_file": ("Get Drive file", "Read content/metadata for a specific Google Drive file."),
    "drive_list_files": ("List Drive files", "List files from the connected Google Drive account."),
    "onedrive_search_files": ("Search OneDrive files", "Search files in the connected OneDrive account."),
    "onedrive_get_file": ("Get OneDrive file", "Read content/metadata for a specific OneDrive file."),
    "onedrive_list_files": ("List OneDrive files", "List files from the connected OneDrive account."),
    "slack_list_channels": ("List Slack channels", "Read available channels in the connected Slack workspace."),
    "slack_read_channel": ("Read Slack channel", "Read messages from a selected Slack channel."),
    "slack_send_message": ("Send Slack message", "Send a message to a Slack channel."),
    "slack_search_messages": ("Search Slack messages", "Search workspace messages in Slack."),
    "hubspot_search_contacts": ("Search HubSpot contacts", "Search contacts in the connected HubSpot workspace."),
    "hubspot_get_contact": ("Get HubSpot contact", "Read a specific HubSpot contact."),
    "hubspot_create_contact": ("Create HubSpot contact", "Create a new contact in HubSpot."),
    "hubspot_search_deals": ("Search HubSpot deals", "Search deals in HubSpot."),
    "email_search": ("Search email", "Search messages in the connected mailbox."),
    "email_get_message": ("Get email message", "Read a specific email message."),
    "email_get_thread": ("Get email thread", "Read a full conversation thread."),
    "email_create_draft": ("Create email draft", "Create a draft email in the connected mailbox."),
    "email_send_draft": ("Send email draft", "Send an existing draft email from the connected mailbox."),
}


def _native_tool_label(tool_name: str) -> str:
    value = str(tool_name or "").strip()
    if not value:
        return "Tool"
    preset = _NATIVE_TOOL_PRESENTATION.get(value)
    if preset:
        return preset[0]
    return value.replace("_", " ").strip().title()


def _native_tool_description(tool_name: str, *, operation_type: str) -> str:
    value = str(tool_name or "").strip()
    preset = _NATIVE_TOOL_PRESENTATION.get(value)
    if preset:
        return preset[1]
    op = str(operation_type or "").strip().lower()
    if op == McpToolOperationType.WRITE:
        return "Write operation for this connected integration."
    if op == McpToolOperationType.READ:
        return "Read operation for this connected integration."
    return "Native integration operation."


def _coerce_preference_bool(value: object, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _tool_preferences_from_metadata(metadata_obj: object) -> dict[str, bool]:
    metadata = metadata_obj if isinstance(metadata_obj, Mapping) else {}
    raw = metadata.get("tool_settings")
    if raw is None:
        raw = metadata.get("toolSettings")
    if not isinstance(raw, Mapping):
        return {}
    preferences: dict[str, bool] = {}
    for tool_name, payload in raw.items():
        normalized_name = str(tool_name or "").strip()
        if not normalized_name:
            continue
        if isinstance(payload, Mapping):
            enabled = _coerce_preference_bool(payload.get("enabled"), default=True)
        else:
            enabled = _coerce_preference_bool(payload, default=True)
        preferences[normalized_name] = enabled
    return preferences


def _native_tool_preferences_from_account(account: IntegrationAccount | None) -> dict[str, bool]:
    if account is None:
        return {}
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    return _tool_preferences_from_metadata(metadata)


def get_native_tool_enabled_map_for_account(
    account: IntegrationAccount,
    *,
    tool_names: Iterable[str] | None = None,
) -> dict[str, bool]:
    preferences = _native_tool_preferences_from_account(account)
    names = [str(name).strip() for name in (tool_names or preferences.keys()) if str(name or "").strip()]
    return {name: bool(preferences.get(name, True)) for name in names}


def is_native_tool_enabled_for_account(*, account: IntegrationAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    preferences = _native_tool_preferences_from_account(account)
    return bool(preferences.get(normalized_name, True))


def _email_tool_preferences_from_account(account: EmailAccount | None) -> dict[str, bool]:
    if account is None:
        return {}
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    return _tool_preferences_from_metadata(metadata)


def get_email_tool_enabled_map_for_account(
    account: EmailAccount,
    *,
    tool_names: Iterable[str] | None = None,
) -> dict[str, bool]:
    preferences = _email_tool_preferences_from_account(account)
    names = [str(name).strip() for name in (tool_names or preferences.keys()) if str(name or "").strip()]
    return {name: bool(preferences.get(name, True)) for name in names}


def is_email_tool_enabled_for_account(*, account: EmailAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    preferences = _email_tool_preferences_from_account(account)
    return bool(preferences.get(normalized_name, True))


def get_native_integration_tools_for_type(integration_type: str) -> list[dict[str, object]]:
    normalized_type = str(integration_type or "").strip()
    tools: list[dict[str, object]] = []
    for tool_name, meta in NATIVE_INTEGRATION_TOOL_REGISTRY.items():
        if not isinstance(meta, Mapping):
            continue
        if str(meta.get("integration_type") or "").strip() != normalized_type:
            continue
        operation_type = str(meta.get("operation_type") or McpToolOperationType.UNKNOWN).strip() or McpToolOperationType.UNKNOWN
        tools.append(
            {
                "toolName": str(tool_name),
                "label": _native_tool_label(str(tool_name)),
                "description": _native_tool_description(str(tool_name), operation_type=operation_type),
                "integrationType": normalized_type,
                "operationType": operation_type,
                "requiresConnectedAccount": bool(meta.get("requires_connected_account", True)),
                "requiresActorUserBinding": bool(meta.get("requires_actor_user_binding", True)),
            }
        )
    tools.sort(key=lambda item: str(item.get("label") or item.get("toolName") or ""))
    return tools


def get_native_integration_tool_metadata(tool_name: str) -> dict[str, object] | None:
    meta = NATIVE_INTEGRATION_TOOL_REGISTRY.get(str(tool_name or "").strip())
    if not isinstance(meta, Mapping):
        return None
    return dict(meta)


def get_native_integration_tool_registry() -> dict[str, dict[str, object]]:
    return {name: dict(meta) for name, meta in NATIVE_INTEGRATION_TOOL_REGISTRY.items()}


def get_native_integration_tool_names() -> set[str]:
    return set(NATIVE_INTEGRATION_TOOL_REGISTRY.keys())


def get_email_integration_tool_names() -> set[str]:
    return set(EMAIL_INTEGRATION_TOOL_REGISTRY.keys())


def get_email_integration_type_for_provider(provider: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    return EMAIL_PROVIDER_INTEGRATION_TYPES.get(normalized_provider, "")


def get_email_integration_tools_for_provider(provider: str) -> list[dict[str, object]]:
    normalized_provider = str(provider or "").strip().lower()
    integration_type = get_email_integration_type_for_provider(normalized_provider)
    if not integration_type:
        return []
    tools: list[dict[str, object]] = []
    for tool_name, meta in EMAIL_INTEGRATION_TOOL_REGISTRY.items():
        if not isinstance(meta, Mapping):
            continue
        providers_raw = meta.get("providers")
        providers = {
            str(value or "").strip().lower()
            for value in (providers_raw if isinstance(providers_raw, (list, tuple, set)) else [])
            if str(value or "").strip()
        }
        if providers and normalized_provider not in providers:
            continue
        operation_type = str(meta.get("operation_type") or McpToolOperationType.UNKNOWN).strip() or McpToolOperationType.UNKNOWN
        tools.append(
            {
                "toolName": str(tool_name),
                "label": _native_tool_label(str(tool_name)),
                "description": _native_tool_description(str(tool_name), operation_type=operation_type),
                "integrationType": integration_type,
                "provider": normalized_provider,
                "operationType": operation_type,
                "requiresConnectedAccount": bool(meta.get("requires_connected_account", True)),
                "requiresActorUserBinding": bool(meta.get("requires_actor_user_binding", True)),
            }
        )
    tools.sort(key=lambda item: str(item.get("label") or item.get("toolName") or ""))
    return tools


def list_connected_native_integration_types(*, conversation: Conversation) -> set[str]:
    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    if not actor_user_uuid:
        return set()
    business_id = getattr(conversation, "business_profile_id", None)
    supported_types = {
        str(meta.get("integration_type") or "").strip()
        for meta in NATIVE_INTEGRATION_TOOL_REGISTRY.values()
        if str(meta.get("integration_type") or "").strip()
    }
    if not supported_types:
        return set()
    types = IntegrationAccount.objects.filter(
        business_profile_id=business_id,
        user_id=actor_user_uuid,
        status=IntegrationAccountStatus.CONNECTED,
        integration_type__in=list(supported_types),
    ).values_list("integration_type", flat=True)
    return {str(value) for value in types if str(value).strip()}


def list_enabled_native_integration_tool_names(
    *,
    conversation: Conversation,
    registry: Mapping[str, Mapping[str, object]] | None = None,
) -> set[str]:
    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    if not actor_user_uuid:
        return set()
    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return set()

    source_registry = registry if isinstance(registry, Mapping) else NATIVE_INTEGRATION_TOOL_REGISTRY
    integration_types = {
        str(meta.get("integration_type") or "").strip()
        for meta in source_registry.values()
        if isinstance(meta, Mapping) and str(meta.get("integration_type") or "").strip()
    }
    if not integration_types:
        return set()

    connected_accounts = IntegrationAccount.objects.filter(
        business_profile_id=business_id,
        user_id=actor_user_uuid,
        status=IntegrationAccountStatus.CONNECTED,
        integration_type__in=list(integration_types),
    ).order_by("-updated_at")
    account_by_type: dict[str, IntegrationAccount] = {}
    for account in connected_accounts:
        integration_type = str(getattr(account, "integration_type", "") or "").strip()
        if not integration_type or integration_type in account_by_type:
            continue
        account_by_type[integration_type] = account

    enabled_tools: set[str] = set()
    for tool_name, meta in source_registry.items():
        if not isinstance(meta, Mapping):
            continue
        normalized_tool = str(tool_name or "").strip()
        if not normalized_tool:
            continue
        integration_type = str(meta.get("integration_type") or "").strip()
        requires_connected_account = bool(meta.get("requires_connected_account", True))
        account = account_by_type.get(integration_type)
        if requires_connected_account and account is None:
            continue
        if account is not None and not is_native_tool_enabled_for_account(account=account, tool_name=normalized_tool):
            continue
        enabled_tools.add(normalized_tool)

    return enabled_tools


def list_enabled_email_tool_names(
    *,
    conversation: Conversation,
    registry: Mapping[str, Mapping[str, object]] | None = None,
) -> set[str]:
    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    if not actor_user_uuid:
        return set()
    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return set()

    source_registry = registry if isinstance(registry, Mapping) else EMAIL_INTEGRATION_TOOL_REGISTRY
    supported_providers: set[str] = set()
    for meta in source_registry.values():
        if not isinstance(meta, Mapping):
            continue
        providers_raw = meta.get("providers")
        if not isinstance(providers_raw, (list, tuple, set)):
            continue
        for provider in providers_raw:
            normalized_provider = str(provider or "").strip().lower()
            if normalized_provider:
                supported_providers.add(normalized_provider)
    if not supported_providers:
        return set()

    connected_accounts = EmailAccount.objects.filter(
        business_profile_id=business_id,
        user_id=actor_user_uuid,
        status=EmailAccountStatus.CONNECTED,
        provider__in=list(supported_providers),
    ).order_by("-updated_at")
    accounts_by_provider: dict[str, list[EmailAccount]] = defaultdict(list)
    for account in connected_accounts:
        provider = str(getattr(account, "provider", "") or "").strip().lower()
        if not provider:
            continue
        accounts_by_provider[provider].append(account)

    enabled_tools: set[str] = set()
    for tool_name, meta in source_registry.items():
        if not isinstance(meta, Mapping):
            continue
        normalized_tool = str(tool_name or "").strip()
        if not normalized_tool:
            continue
        providers_raw = meta.get("providers")
        providers = [
            str(provider or "").strip().lower()
            for provider in (providers_raw if isinstance(providers_raw, (list, tuple, set)) else [])
            if str(provider or "").strip()
        ]
        requires_connected_account = bool(meta.get("requires_connected_account", True))
        candidate_accounts: list[EmailAccount] = []
        for provider in providers:
            candidate_accounts.extend(accounts_by_provider.get(provider, []))
        if requires_connected_account and not candidate_accounts:
            continue
        if candidate_accounts and not any(
            is_email_tool_enabled_for_account(account=account, tool_name=normalized_tool)
            for account in candidate_accounts
        ):
            continue
        enabled_tools.add(normalized_tool)

    return enabled_tools


def is_native_integration_tool_enabled_for_conversation(
    *,
    tool_name: str,
    conversation: Conversation,
) -> bool:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return False
    meta = NATIVE_INTEGRATION_TOOL_REGISTRY.get(normalized_tool)
    if not isinstance(meta, Mapping):
        return False
    enabled = list_enabled_native_integration_tool_names(
        conversation=conversation,
        registry={normalized_tool: dict(meta)},
    )
    return normalized_tool in enabled


def resolve_native_integration_account_for_tool(
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    conversation: Conversation,
) -> tuple[IntegrationAccount | None, Mapping[str, object] | None]:
    meta = get_native_integration_tool_metadata(tool_name)
    if not isinstance(meta, Mapping):
        return None, _integration_error(
            str(tool_name or "unknown_tool"),
            error_code="unsupported_tool",
            hint="Unsupported native integration tool.",
        )
    integration_type = str(meta.get("integration_type") or "").strip()
    if not integration_type:
        return None, _integration_error(
            str(tool_name or "unknown_tool"),
            error_code="unsupported_tool",
            hint="Missing integration type metadata.",
        )
    requires_actor_user_binding = bool(meta.get("requires_actor_user_binding", True))
    return _resolve_integration_account_for_tool(
        integration_type=integration_type,
        tool=str(tool_name or "unknown_tool"),
        arguments=arguments,
        conversation=conversation,
        requires_actor_user_binding=requires_actor_user_binding,
    )
