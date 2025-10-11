"""Security helpers: JWT validation, role checks, CAPTCHA verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence
from uuid import UUID

from app.core.settings import Settings


class TokenVerificationError(RuntimeError):
    """Raised when an access token fails validation."""


@dataclass(slots=True, frozen=True)
class AccessTokenClaims:
    """Structured representation of a decoded JWT."""

    subject: UUID
    issuer: str
    audience: str
    issued_at: datetime
    expires_at: datetime
    scopes: tuple[str, ...]
    roles: tuple[str, ...]
    business_roles: Mapping[UUID, tuple[str, ...]]
    email: str | None


@dataclass(slots=True, frozen=True)
class AuthenticatedUser:
    """Authenticated principal extracted from an access token."""

    user_id: UUID
    email: str | None
    roles: tuple[str, ...]
    business_roles: Mapping[UUID, tuple[str, ...]]
    scopes: tuple[str, ...]
    token: str

    def has_business_role(self, business_id: UUID, required: Sequence[str]) -> bool:
        roles = {role.lower() for role in self.business_roles.get(business_id, ())}
        return any(role.lower() in roles for role in required)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def verify_jwt(token: str, settings: Settings) -> AccessTokenClaims:
    """Verify an HS256 JWT using application settings."""

    if not token:
        raise TokenVerificationError("Token is empty")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenVerificationError("Token must have header, payload, signature")
    header_b64, payload_b64, signature_b64 = parts
    header = _decode_segment(header_b64)
    if header.get("alg") != "HS256":
        raise TokenVerificationError("Unsupported JWT algorithm")
    secret = settings.JWT_SECRET.encode("utf-8")
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = hmac.new(secret, msg=signing_input, digestmod=hashlib.sha256).digest()
    signature = _base64url_decode(signature_b64)
    if not hmac.compare_digest(expected_sig, signature):
        raise TokenVerificationError("Invalid token signature")

    payload = _decode_segment(payload_b64)
    now = datetime.now(timezone.utc)
    skew = timedelta(seconds=settings.JWT_CLOCK_SKEW_SECONDS)

    try:
        subject = UUID(str(payload["sub"]))
    except Exception as exc:  # pragma: no cover - defensive guard
        raise TokenVerificationError("Token is missing a valid subject") from exc

    exp = _coerce_timestamp(payload.get("exp"))
    if exp is None or exp < now - skew:
        raise TokenVerificationError("Token expired")

    nbf = _coerce_timestamp(payload.get("nbf"))
    if nbf and nbf > now + skew:
        raise TokenVerificationError("Token not yet valid")

    iat = _coerce_timestamp(payload.get("iat")) or now
    if iat > now + skew:
        raise TokenVerificationError("Token issued in the future")

    issuer = payload.get("iss")
    if issuer != settings.JWT_ISSUER:
        raise TokenVerificationError("Invalid issuer")

    audience = payload.get("aud")
    if audience != settings.JWT_AUDIENCE:
        raise TokenVerificationError("Invalid audience")

    scopes = _normalize_string_list(payload.get("scope") or payload.get("scopes"))
    roles = _normalize_string_list(payload.get("roles"))
    business_roles = _normalize_business_roles(payload.get("biz_roles") or payload.get("business_roles"))
    email = payload.get("email")

    return AccessTokenClaims(
        subject=subject,
        issuer=issuer,
        audience=audience,
        issued_at=iat,
        expires_at=exp,
        scopes=scopes,
        roles=roles,
        business_roles=business_roles,
        email=email,
    )


def generate_access_token(
    *,
    subject: UUID,
    settings: Settings,
    email: str | None = None,
    scopes: Sequence[str] | None = None,
    roles: Sequence[str] | None = None,
    business_roles: Mapping[UUID, Sequence[str]] | None = None,
    expires_in: int | None = None,
) -> str:
    """Create an HS256 JWT aligned with the verifier expectations."""

    now = datetime.now(timezone.utc)
    expiry_seconds = expires_in or settings.JWT_ACCESS_TTL_SECONDS
    expires_at = now + timedelta(seconds=max(expiry_seconds, 1))

    payload: dict[str, object] = {
        "iss": settings.JWT_ISSUER,
        "sub": str(subject),
        "aud": settings.JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    if email:
        payload["email"] = email

    if scopes:
        scope_values = [scope.strip() for scope in scopes if scope.strip()]
        if scope_values:
            payload["scope"] = " ".join(scope_values)

    if roles:
        role_values = [role.strip() for role in roles if role.strip()]
        if role_values:
            payload["roles"] = role_values

    if business_roles:
        encoded_roles: dict[str, list[str]] = {}
        for business_id, assigned_roles in business_roles.items():
            role_list = [role.strip() for role in assigned_roles if role.strip()]
            if role_list:
                encoded_roles[str(business_id)] = role_list
        if encoded_roles:
            payload["biz_roles"] = encoded_roles

    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        _base64url_encode(json.dumps(component, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        for component in (header, payload)
    )
    signature = hmac.new(
        settings.JWT_SECRET.encode("utf-8"),
        msg=signing_input.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    token = f"{signing_input}.{_base64url_encode(signature)}"
    return token


def build_authenticated_user(token: str, claims: AccessTokenClaims) -> AuthenticatedUser:
    """Create an authenticated user wrapper from claims."""

    return AuthenticatedUser(
        user_id=claims.subject,
        email=claims.email,
        roles=claims.roles,
        business_roles=claims.business_roles,
        scopes=claims.scopes,
        token=token,
    )


def validate_captcha(token: str | None, remote_ip: str | None, settings: Settings) -> bool:
    """Validate CAPTCHA token using shared-secret HMAC (placeholder until provider integration)."""

    if not settings.CAPTCHA_SECRET:
        return True
    if not token:
        return False
    message = (remote_ip or "").encode("utf-8")
    digest = hmac.new(settings.CAPTCHA_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, token)


def _decode_segment(segment: str) -> dict[str, object]:
    try:
        data = _base64url_decode(segment)
        return json.loads(data.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive guard
        raise TokenVerificationError("Malformed token segment") from exc


def _base64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _coerce_timestamp(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str) and value.isdigit():
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    return None


def _normalize_string_list(value: object | None) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        items = [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    seen: list[str] = []
    for item in items:
        lower = item.lower()
        if lower not in seen:
            seen.append(lower)
    return tuple(seen)


def _normalize_business_roles(value: object | None) -> Mapping[UUID, tuple[str, ...]]:
    result: dict[UUID, tuple[str, ...]] = {}
    if not isinstance(value, Mapping):
        return result
    for key, raw_roles in value.items():
        try:
            business_id = UUID(str(key))
        except Exception:  # pragma: no cover - defensive guard
            continue
        roles = _normalize_string_list(raw_roles)
        if roles:
            result[business_id] = roles
    return result


__all__ = [
    "AccessTokenClaims",
    "AuthenticatedUser",
    "generate_access_token",
    "TokenVerificationError",
    "build_authenticated_user",
    "validate_captcha",
    "verify_jwt",
]
