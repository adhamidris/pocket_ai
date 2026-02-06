from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any, Mapping

from django.http import HttpRequest


@dataclass(frozen=True)
class TwilioConfig:
    account_sid: str
    auth_token: str
    webhook_base_url: str
    default_from_number: str


def load_twilio_config(
    *,
    require_from_number: bool = True,
    overrides: Mapping[str, Any] | None = None,
    allow_env_fallback: bool = True,
) -> TwilioConfig:
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

    account_sid = _value(key="account_sid", env_key="TWILIO_ACCOUNT_SID")
    auth_token = _value(key="auth_token", env_key="TWILIO_AUTH_TOKEN")
    webhook_base_url = _value(key="webhook_base_url", env_key="TWILIO_WEBHOOK_BASE_URL").rstrip("/")
    default_from_number = _value(
        key="from_number",
        env_key="TWILIO_FROM_NUMBER",
        fallback_keys=("default_from_number",),
    )

    missing: list[str] = []
    if not account_sid:
        missing.append("TWILIO_ACCOUNT_SID")
    if not auth_token:
        missing.append("TWILIO_AUTH_TOKEN")
    if not webhook_base_url:
        missing.append("TWILIO_WEBHOOK_BASE_URL")
    if require_from_number and not default_from_number:
        missing.append("TWILIO_FROM_NUMBER")
    if missing:
        raise ValueError(f"Missing Twilio config env vars: {', '.join(missing)}")

    return TwilioConfig(
        account_sid=account_sid,
        auth_token=auth_token,
        webhook_base_url=webhook_base_url,
        default_from_number=default_from_number,
    )


def build_twilio_signature(*, url: str, params: Mapping[str, str], auth_token: str) -> str:
    """
    Implement Twilio's request signing algorithm.

    See: https://www.twilio.com/docs/usage/security#validating-requests
    """

    data = url
    for key in sorted(params.keys()):
        data += f"{key}{params[key]}"
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def request_url_for_validation(request: HttpRequest, *, webhook_base_url: str) -> str:
    """
    Build the exact URL Twilio should have used when calling this endpoint.

    Important: Twilio signature validation is extremely sensitive to URL
    mismatches (scheme/host/path/trailing slashes/query encoding). This helper
    assumes `webhook_base_url` matches what was configured in Twilio.
    """

    return f"{webhook_base_url}{request.get_full_path()}"


def validate_twilio_request(request: HttpRequest, *, auth_token: str, webhook_base_url: str) -> bool:
    signature = (request.headers.get("X-Twilio-Signature") or request.META.get("HTTP_X_TWILIO_SIGNATURE") or "").strip()
    if not signature:
        return False

    url = request_url_for_validation(request, webhook_base_url=webhook_base_url)
    # Twilio docs: include POST form fields for POST requests, sorted by name.
    params: dict[str, str] = {}
    if request.method.upper() == "POST":
        for key, value in request.POST.items():
            params[str(key)] = str(value)

    expected = build_twilio_signature(url=url, params=params, auth_token=auth_token)
    return hmac.compare_digest(expected, signature)
