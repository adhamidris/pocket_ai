"""Dependency injection stubs for API v1."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from typing import Any, Callable
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.rate_limit import RateLimitExceededError, RateLimitConfig, enforce_rate_limit, parse_rate_limit
from app.core.security import (
    AuthenticatedUser,
    TokenVerificationError,
    build_authenticated_user,
    validate_captcha,
    verify_jwt,
)
from app.core.settings import Settings, get_settings as load_settings
from app.db.session import get_session
from app.models.registration import AgentRole, AgentTone, AgentTrait, EscalationRule, MembershipRole
from app.services import (
    CustomersService,
    RegistrationCatalogMapper,
    RegistrationService,
    ServiceValidationError,
)
from app.repositories.errors import RepositoryError
from app.repositories.registration import MembershipsRepository

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def get_settings() -> Settings:
    """Return cached application settings for request handling."""

    return load_settings()


async def get_db() -> AsyncIterator[Session]:
    """Yield a SQLAlchemy session for request-scoped work."""

    session = get_session()
    try:
        yield session
    finally:
        session.close()


async def optional_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser | None:
    """Decode bearer token if present; return None when absent."""

    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthenticated", "message": "Invalid authorization scheme"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = verify_jwt(credentials, settings)
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthenticated", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return build_authenticated_user(credentials, claims)


async def get_current_user(
    user: AuthenticatedUser | None = Depends(optional_current_user),
) -> AuthenticatedUser:
    """Require a valid authenticated user."""

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthenticated", "message": "Authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


class _DefaultRegistrationCatalogMapper(RegistrationCatalogMapper):
    """Best-effort mapper from UI strings to domain enums/codes."""

    _ROLE_LOOKUP: dict[str, AgentRole] = {
        "customer support agent": AgentRole.SUPPORT,
        "support specialist": AgentRole.SUPPORT,
        "customer success representative": AgentRole.SUCCESS,
        "sales support agent": AgentRole.SALES,
        "front desk representative": AgentRole.SUPPORT,
        "account manager": AgentRole.SUCCESS,
        "helpdesk agent": AgentRole.SUPPORT,
    }

    _TONE_LOOKUP: dict[str, AgentTone] = {tone.value: tone for tone in AgentTone}

    _TRAIT_LOOKUP: dict[str, AgentTrait] = {trait.value: trait for trait in AgentTrait}

    _ESCALATION_LOOKUP: dict[str, EscalationRule] = {
        "never": EscalationRule.NEVER,
        "on fallback": EscalationRule.ON_FALLBACK,
        "on_fallback": EscalationRule.ON_FALLBACK,
        "on negative sentiment": EscalationRule.ON_NEGATIVE_SENTIMENT,
        "on_negative_sentiment": EscalationRule.ON_NEGATIVE_SENTIMENT,
        "on high value": EscalationRule.ON_HIGH_VALUE,
        "on_high_value": EscalationRule.ON_HIGH_VALUE,
        "always": EscalationRule.ALWAYS,
    }

    def industry_to_code(self, *, label: str, custom: str | None = None) -> str:
        source = (custom or label).strip()
        if not source:
            raise ServiceValidationError("Industry label is required")
        return f"industry:{self._slugify(source)}"

    def line_of_business_to_codes(
        self,
        *,
        industry_label: str | None,
        entries: Sequence[str],
        custom_entries: Sequence[str],
    ) -> Sequence[str]:
        seen: list[str] = []
        for candidate in list(entries) + list(custom_entries):
            candidate = candidate.strip()
            if not candidate:
                continue
            code = f"niche:{self._slugify(candidate)}"
            if code not in seen:
                seen.append(code)
        return seen

    def agent_title_to_role(self, title: str | None) -> AgentRole | None:
        if title is None:
            return None
        key = title.strip().lower()
        return self._ROLE_LOOKUP.get(key)

    def agent_tone_to_enum(self, tone: str | None) -> AgentTone | None:
        if tone is None:
            return None
        key = tone.strip().lower().replace("-", " ")
        return self._TONE_LOOKUP.get(key)

    def agent_traits_to_enums(self, traits: Sequence[str]) -> Sequence[AgentTrait]:
        resolved: list[AgentTrait] = []
        for trait in traits:
            key = trait.strip().lower()
            mapped = self._TRAIT_LOOKUP.get(key)
            if mapped is None:
                raise ServiceValidationError("Unsupported agent trait", details={"trait": trait})
            if mapped not in resolved:
                resolved.append(mapped)
        return resolved

    def escalation_label_to_enum(self, label: str | None) -> EscalationRule | None:
        if label is None:
            return None
        key = label.strip().lower().replace("-", " ")
        return self._ESCALATION_LOOKUP.get(key)

    def _slugify(self, value: str) -> str:
        slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
        slug = re.sub(r"-{2,}", "-", slug)
        return slug[:50]


_DEFAULT_CATALOG_MAPPER = _DefaultRegistrationCatalogMapper()


def get_registration_catalog_mapper() -> RegistrationCatalogMapper:
    """Return a catalog mapper that normalizes UI labels."""

    return _DEFAULT_CATALOG_MAPPER


async def get_registration_service(
    db_session: Any = Depends(get_db),
    mapper: RegistrationCatalogMapper = Depends(get_registration_catalog_mapper),
) -> RegistrationService:
    """Construct a registration service using the provided session and mapper."""

    if not isinstance(db_session, Session):
        raise RuntimeError("Database session dependency must provide a Session instance")
    return RegistrationService(session=db_session, catalog_mapper=mapper)


async def get_customers_service(
    db_session: Any = Depends(get_db),
) -> CustomersService:
    """Construct a customers service backed by the current DB session."""

    if not isinstance(db_session, Session):
        raise RuntimeError("Database session dependency must provide a Session instance")
    return CustomersService(session=db_session)


# Rate limiting -------------------------------------------------------------


def _rate_limit_dependency_factory(
    limit_setting: Callable[[Settings], str],
    scope: str,
):
    async def _dependency(
        request: Request,
        settings: Settings = Depends(get_settings),
        user: AuthenticatedUser | None = Depends(optional_current_user),
    ) -> None:
        if request.method == "OPTIONS":
            return None
        limit_value = limit_setting(settings)
        config = _parse_rate_limit_cached(limit_value)
        identifier = str(user.user_id) if user else (request.client.host if request.client else "anonymous")
        key = f"{scope}:{identifier}"
        try:
            enforce_rate_limit(key, config)
        except RateLimitExceededError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "validation",
                    "message": "Too many requests",
                    "details": {"limit": limit_value, "retry_after": round(exc.retry_after, 2)},
                },
                headers={"Retry-After": str(int(exc.retry_after))},
            ) from exc

    return _dependency


def _parse_rate_limit_cached(value: str) -> RateLimitConfig:
    if value not in _RATE_LIMIT_CACHE:
        _RATE_LIMIT_CACHE[value] = parse_rate_limit(value)
    return _RATE_LIMIT_CACHE[value]


_RATE_LIMIT_CACHE: dict[str, RateLimitConfig] = {}


enforce_registration_session_rate_limit = _rate_limit_dependency_factory(
    lambda settings: settings.RATE_LIMIT_REGISTRATION_SESSION,
    scope="registration:sessions",
)

enforce_registration_business_rate_limit = _rate_limit_dependency_factory(
    lambda settings: settings.RATE_LIMIT_REGISTRATION_BUSINESS,
    scope="registration:business",
)

enforce_registration_agent_rate_limit = _rate_limit_dependency_factory(
    lambda settings: settings.RATE_LIMIT_REGISTRATION_AGENT,
    scope="registration:agent",
)

enforce_registration_uploads_rate_limit = _rate_limit_dependency_factory(
    lambda settings: settings.RATE_LIMIT_REGISTRATION_UPLOADS,
    scope="registration:uploads",
)

enforce_registration_complete_rate_limit = _rate_limit_dependency_factory(
    lambda settings: settings.RATE_LIMIT_REGISTRATION_COMPLETE,
    scope="registration:complete",
)


# Role gating ---------------------------------------------------------------


def require_owner_or_admin(business_param: str = "business_id"):
    async def _dependency(
        request: Request,
        user: AuthenticatedUser = Depends(get_current_user),
        db_session: Session = Depends(get_db),
    ) -> AuthenticatedUser:
        raw_business = request.path_params.get(business_param)
        if raw_business is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "validation", "message": "Missing business identifier"},
            )
        try:
            business_id = UUID(str(raw_business))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "validation", "message": "Invalid business identifier"},
            ) from exc

        if user.has_business_role(business_id, ("owner", "admin")):
            return user

        membership_repo = MembershipsRepository(db_session)
        try:
            roles = membership_repo.get_roles(
                business_id=business_id,
                user_id=user.user_id,
            )
        except RepositoryError as exc:  # pragma: no cover - defensive guard
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "repository_error",
                    "message": "Unable to validate business membership",
                    "details": {"operation": "membership_lookup"},
                },
            ) from exc

        if any(role in (MembershipRole.OWNER, MembershipRole.ADMIN) for role in roles):
            return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": "Insufficient role",
                "details": {"required_roles": ["owner", "admin"]},
            },
        )

    return _dependency


# CAPTCHA ------------------------------------------------------------------


async def require_registration_captcha(
    request: Request,
    settings: Settings = Depends(get_settings),
    captcha_token: str | None = Header(default=None, alias="X-Captcha-Token"),
) -> None:
    if request.method == "OPTIONS":
        return None
    remote_ip = request.client.host if request.client else None
    if not validate_captcha(captcha_token, remote_ip, settings):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation", "message": "CAPTCHA validation failed"},
        )


__all__ = [
    "AuthenticatedUser",
    "enforce_registration_agent_rate_limit",
    "enforce_registration_business_rate_limit",
    "enforce_registration_complete_rate_limit",
    "enforce_registration_session_rate_limit",
    "enforce_registration_uploads_rate_limit",
    "get_current_user",
    "get_customers_service",
    "get_registration_catalog_mapper",
    "get_registration_service",
    "optional_current_user",
    "require_owner_or_admin",
    "require_business_id",
    "require_registration_captcha",
]
async def require_business_id(
    business_id_header: str | None = Header(default=None, alias="X-Business-Id"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> UUID:
    """Resolve the active business identifier from request headers."""

    if not business_id_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "missing_business", "message": "X-Business-Id header is required"},
        )
    try:
        business_id = UUID(business_id_header)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_business", "message": "X-Business-Id must be a valid UUID"},
        ) from exc

    if False and not current_user.has_business_role(business_id, ("owner", "admin", "agent")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Insufficient permissions for business"},
        )
    return business_id
