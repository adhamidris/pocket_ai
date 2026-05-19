"""
Signed cursor helpers for agentic read_knowledge pagination.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Mapping

from django.conf import settings

_AGENTIC_READ_CURSOR_V2_SALT = b"mcp.read_cursor.v2"
_AGENTIC_READ_CURSOR_V2_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign_agentic_read_cursor_v2(payload: Mapping[str, object]) -> str:
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    body = _b64url_encode(json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    sig = hmac.new(secret, _AGENTIC_READ_CURSOR_V2_SALT + body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _verify_agentic_read_cursor_v2(cursor: str) -> dict[str, object]:
    raw = (cursor or "").strip()
    if not raw or "." not in raw:
        raise ValueError("invalid cursor")
    body_b64, sig_b64 = raw.split(".", 1)
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    expected = hmac.new(secret, _AGENTIC_READ_CURSOR_V2_SALT + body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(sig_b64)
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if not hmac.compare_digest(expected, provided):
        raise ValueError("invalid cursor")
    try:
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid cursor")
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        raise ValueError("invalid cursor")
    if exp and exp <= int(time.time()):
        raise ValueError("expired cursor")
    return payload