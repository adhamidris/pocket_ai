from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TelnyxConfig:
    api_key: str
    account_sid: str
    application_sid: str
    webhook_base_url: str
    default_from_number: str


def load_telnyx_config(
    *,
    require_from_number: bool = True,
    overrides: Mapping[str, Any] | None = None,
    allow_env_fallback: bool = True,
) -> TelnyxConfig:
    source = dict(overrides or {})

    def _value(*, key: str, env_key: str, fallback_keys: tuple[str, ...] = ()) -> str:
        keys = (key, *fallback_keys)
        for candidate in keys:
            if candidate in source:
                text = str(source.get(candidate) or "").strip()
                if text or not allow_env_fallback:
                    return text
        if allow_env_fallback:
            return (os.getenv(env_key) or "").strip()
        return ""

    api_key = _value(key="api_key", env_key="TELNYX_API_KEY")
    account_sid = _value(key="account_sid", env_key="TELNYX_ACCOUNT_SID")
    application_sid = _value(
        key="application_sid",
        env_key="TELNYX_APPLICATION_SID",
        fallback_keys=("app_sid",),
    )
    webhook_base_url = _value(key="webhook_base_url", env_key="TELNYX_WEBHOOK_BASE_URL").rstrip("/")
    default_from_number = _value(
        key="from_number",
        env_key="TELNYX_FROM_NUMBER",
        fallback_keys=("default_from_number",),
    )

    missing: list[str] = []
    if not api_key:
        missing.append("TELNYX_API_KEY" if allow_env_fallback else "api_key")
    if not account_sid:
        missing.append("TELNYX_ACCOUNT_SID" if allow_env_fallback else "account_sid")
    if not application_sid:
        missing.append("TELNYX_APPLICATION_SID" if allow_env_fallback else "application_sid")
    if not webhook_base_url:
        missing.append("TELNYX_WEBHOOK_BASE_URL" if allow_env_fallback else "webhook_base_url")
    if require_from_number and not default_from_number:
        missing.append("TELNYX_FROM_NUMBER" if allow_env_fallback else "from_number")
    if missing:
        if allow_env_fallback:
            raise ValueError(f"Missing Telnyx config env vars: {', '.join(missing)}")
        raise ValueError(f"Missing Telnyx config values: {', '.join(missing)}")

    return TelnyxConfig(
        api_key=api_key,
        account_sid=account_sid,
        application_sid=application_sid,
        webhook_base_url=webhook_base_url,
        default_from_number=default_from_number,
    )
