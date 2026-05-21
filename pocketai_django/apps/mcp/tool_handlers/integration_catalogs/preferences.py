from __future__ import annotations

from typing import Iterable, Mapping

from apps.integrations.models import EmailAccount, IntegrationAccount


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
