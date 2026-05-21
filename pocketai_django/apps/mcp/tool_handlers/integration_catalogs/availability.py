from __future__ import annotations

from collections import defaultdict
from typing import Mapping

from apps.accounts.models import (
    EmailAccountStatus,
    IntegrationAccountStatus,
)
from apps.conversations.models import Conversation
from apps.integrations.models import EmailAccount, IntegrationAccount

from ..native_integration import (
    _conversation_actor_user_uuid,
    _integration_error,
    _resolve_integration_account_for_tool,
)
from .preferences import (
    is_email_tool_enabled_for_account,
    is_native_tool_enabled_for_account,
)
from .registry import (
    EMAIL_INTEGRATION_TOOL_REGISTRY,
    NATIVE_INTEGRATION_TOOL_REGISTRY,
    get_native_integration_tool_metadata,
)


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
