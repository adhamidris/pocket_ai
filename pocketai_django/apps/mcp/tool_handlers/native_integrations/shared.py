from __future__ import annotations

import logging
import uuid
from typing import Mapping

from apps.accounts.models import IntegrationAccountStatus
from apps.conversations.models import Conversation
from apps.integrations.accounts.native import ensure_fresh_integration_credentials
from apps.integrations.models import IntegrationAccount


logger = logging.getLogger(__name__)


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


# ═══════════════════════════════════════════════════════════════════════════════
# Native Integration tool helpers and handlers
# ═══════════════════════════════════════════════════════════════════════════════


def _integration_error(tool: str, *, error_code: str, hint: str) -> Mapping[str, object]:
    return {
        "tool": tool,
        "status": "error",
        "error": error_code,
        "error_code": error_code,
        "hint": hint,
    }


def _conversation_actor_user_uuid(conversation: Conversation) -> uuid.UUID | None:
    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    actor_user_id = None
    if isinstance(meta, Mapping):
        actor_user_id = meta.get("actor_user_id") or meta.get("actorUserId") or meta.get("user_id") or meta.get("userId")
    if not actor_user_id:
        return None
    try:
        return uuid.UUID(str(actor_user_id))
    except (TypeError, ValueError):
        return None


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


def _is_native_tool_enabled_for_account(*, account: IntegrationAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    preferences = _tool_preferences_from_metadata(metadata)
    return bool(preferences.get(normalized_name, True))


def _resolve_integration_account_for_tool(
    *,
    integration_type: str,
    tool: str,
    arguments: Mapping[str, object],
    conversation: Conversation,
    requires_actor_user_binding: bool = True,
) -> tuple[IntegrationAccount | None, Mapping[str, object] | None]:
    """
    Resolve the IntegrationAccount to use for a tool call.

    Preferred: explicit integration_account_id argument.
    Fallback: conversation metadata actor_user_id.
    """

    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    business_id = getattr(conversation, "business_profile_id", None)
    raw_account_id = _coerce_str(arguments.get("integration_account_id") or arguments.get("integrationAccountId")).strip()
    if raw_account_id:
        try:
            account_uuid = uuid.UUID(raw_account_id)
        except (TypeError, ValueError):
            return None, _integration_error(tool, error_code="validation_error", hint="integration_account_id must be a valid UUID.")
        account = IntegrationAccount.objects.filter(
            id=account_uuid,
            integration_type=integration_type,
            business_profile_id=business_id,
        ).first()
        if not account:
            return None, _integration_error(
                tool,
                error_code="not_connected",
                hint=f"{integration_type} is not connected for this workspace/user.",
            )
        if account.status != IntegrationAccountStatus.CONNECTED:
            return None, _integration_error(
                tool,
                error_code="not_connected",
                hint=f"{integration_type} is not connected for this workspace/user.",
            )
        if actor_user_uuid and account.user_id != actor_user_uuid:
            return None, _integration_error(
                tool,
                error_code="account_mismatch",
                hint="Connected account belongs to a different user in this workspace.",
            )
        if requires_actor_user_binding and not actor_user_uuid:
            return None, _integration_error(
                tool,
                error_code="account_mismatch",
                hint="Missing actor-user binding for this integration tool call.",
            )
        if not _is_native_tool_enabled_for_account(account=account, tool_name=tool):
            return None, _integration_error(
                tool,
                error_code="tool_disabled",
                hint="This integration tool is disabled in Integrations settings.",
            )
        return account, None

    if actor_user_uuid:
        account = (
            IntegrationAccount.objects.filter(
                business_profile_id=business_id,
                user_id=actor_user_uuid,
                integration_type=integration_type,
                status=IntegrationAccountStatus.CONNECTED,
            )
            .order_by("-updated_at")
            .first()
        )
        if account:
            if not _is_native_tool_enabled_for_account(account=account, tool_name=tool):
                return None, _integration_error(
                    tool,
                    error_code="tool_disabled",
                    hint="This integration tool is disabled in Integrations settings.",
                )
            return account, None
        return None, _integration_error(
            tool,
            error_code="not_connected",
            hint=f"No connected {integration_type} account found for this user. Connect it from Integrations.",
        )

    if requires_actor_user_binding:
        return None, _integration_error(
            tool,
            error_code="account_mismatch",
            hint="Missing actor-user binding for this integration tool call.",
        )

    return None, _integration_error(
        tool,
        error_code="not_connected",
        hint=f"No connected {integration_type} account found. Connect it from Integrations.",
    )


def _get_integration_access_token(account: IntegrationAccount, tool: str) -> tuple[str | None, Mapping[str, object] | None]:
    """Refresh credentials and extract access_token. Returns (token, error)."""
    try:
        refreshed = ensure_fresh_integration_credentials(account)
    except Exception:
        logger.exception("integration.oauth_refresh_failed tool=%s account=%s", tool, getattr(account, "id", None))
        return None, _integration_error(
            tool,
            error_code="token_expired",
            hint="Integration OAuth refresh failed. Reconnect the integration and try again.",
        )
    creds = refreshed.credentials or {}
    access_token = str(creds.get("access_token") or "").strip()
    if not access_token:
        return None, _integration_error(
            tool,
            error_code="token_expired",
            hint="Integration account is missing an access token. Reconnect and try again.",
        )
    return access_token, None
