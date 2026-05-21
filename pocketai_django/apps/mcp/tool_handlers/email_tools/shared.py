from __future__ import annotations

import uuid
from typing import Mapping

from apps.accounts.models import EmailAccountStatus
from apps.conversations.models import Conversation
from apps.integrations.models import EmailAccount


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


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


def _email_tool_preferences_from_account(account: EmailAccount | None) -> dict[str, bool]:
    if account is None:
        return {}
    metadata = account.metadata if isinstance(getattr(account, "metadata", None), Mapping) else {}
    return _tool_preferences_from_metadata(metadata)


def _is_email_tool_enabled_for_account(*, account: EmailAccount, tool_name: str) -> bool:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return False
    preferences = _email_tool_preferences_from_account(account)
    return bool(preferences.get(normalized_name, True))


def _conversation_actor_user_uuid(conversation: Conversation) -> uuid.UUID | None:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    actor_user_id = metadata.get("actor_user_id") or metadata.get("actorUserId") or metadata.get("user_id") or metadata.get("userId")
    if not actor_user_id:
        return None
    try:
        return uuid.UUID(str(actor_user_id))
    except (TypeError, ValueError):
        return None


def _email_error(tool: str, *, error_code: str, hint: str) -> Mapping[str, object]:
    return {
        "tool": tool,
        "status": "error",
        "error": error_code,
        "error_code": error_code,
        "hint": hint,
    }


def _resolve_email_account_for_tool(
    *,
    tool: str,
    arguments: Mapping[str, object],
    conversation: Conversation,
) -> tuple[EmailAccount | None, Mapping[str, object] | None]:
    """
    Resolve the EmailAccount to use for a tool call.

    Preferred:
    - explicit email_account_id argument
    Fallback (for dashboard chat sessions):
    - conversation.metadata.actor_user_id (or user_id)
    """

    actor_user_uuid = _conversation_actor_user_uuid(conversation)
    raw_account_id = _coerce_str(arguments.get("email_account_id") or arguments.get("emailAccountId")).strip()
    if raw_account_id:
        try:
            account_uuid = uuid.UUID(raw_account_id)
        except (TypeError, ValueError):
            return None, _email_error(tool, error_code="validation_error", hint="email_account_id must be a valid UUID.")
        account = EmailAccount.objects.filter(
            id=account_uuid,
            business_profile_id=getattr(conversation, "business_profile_id", None),
        ).first()
        if not account:
            return None, _email_error(tool, error_code="email_account_not_found", hint="Email account not found.")
        if account.status != EmailAccountStatus.CONNECTED:
            return None, _email_error(tool, error_code="email_not_connected", hint="Email account is not connected.")
        if actor_user_uuid and account.user_id != actor_user_uuid:
            return None, _email_error(
                tool,
                error_code="account_mismatch",
                hint="Connected account belongs to a different user in this workspace.",
            )
        if not _is_email_tool_enabled_for_account(account=account, tool_name=tool):
            return None, _email_error(
                tool,
                error_code="tool_disabled",
                hint="This integration tool is disabled in Integrations settings.",
            )
        return account, None

    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    actor_user_id = None
    if isinstance(meta, Mapping):
        actor_user_id = meta.get("actor_user_id") or meta.get("actorUserId") or meta.get("user_id") or meta.get("userId")
    if actor_user_id:
        try:
            user_uuid = uuid.UUID(str(actor_user_id))
        except (TypeError, ValueError):
            user_uuid = None
        if user_uuid:
            account = EmailAccount.objects.filter(
                business_profile_id=getattr(conversation, "business_profile_id", None),
                user_id=user_uuid,
            ).first()
            if account and account.status == EmailAccountStatus.CONNECTED:
                if not _is_email_tool_enabled_for_account(account=account, tool_name=tool):
                    return None, _email_error(
                        tool,
                        error_code="tool_disabled",
                        hint="This integration tool is disabled in Integrations settings.",
                    )
                return account, None

    return None, _email_error(
        tool,
        error_code="email_not_connected",
        hint="No connected email account found. Connect Gmail/Microsoft via OAuth first.",
    )
