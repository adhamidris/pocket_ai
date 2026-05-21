from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


class PortalFileTokenError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PortalFileTokenPayload:
    file_id: uuid.UUID
    business_id: uuid.UUID
    exp: int


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def sign_portal_file_token(*, file_id: uuid.UUID, business_id: uuid.UUID, ttl: timedelta) -> str:
    if ttl.total_seconds() <= 0:
        raise PortalFileTokenError("ttl must be positive")
    exp = int((timezone.now() + ttl).timestamp())
    payload = {"file_id": str(file_id), "business_id": str(business_id), "exp": exp}
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def verify_portal_file_token(token: str) -> PortalFileTokenPayload:
    raw = (token or "").strip()
    if not raw or "." not in raw:
        raise PortalFileTokenError("invalid token")
    body_b64, sig_b64 = raw.split(".", 1)
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    expected = hmac.new(secret, body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(sig_b64)
    except Exception as exc:
        raise PortalFileTokenError("invalid token") from exc
    if not hmac.compare_digest(expected, provided):
        raise PortalFileTokenError("invalid token")
    try:
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except Exception as exc:
        raise PortalFileTokenError("invalid token") from exc
    file_id_raw = payload.get("file_id")
    business_id_raw = payload.get("business_id")
    exp_raw = payload.get("exp")
    try:
        file_id = uuid.UUID(str(file_id_raw))
        business_id = uuid.UUID(str(business_id_raw))
        exp = int(exp_raw)
    except Exception as exc:
        raise PortalFileTokenError("invalid token") from exc
    now_ts = int(timezone.now().timestamp())
    if exp <= now_ts:
        raise PortalFileTokenError("expired token")
    return PortalFileTokenPayload(file_id=file_id, business_id=business_id, exp=exp)
