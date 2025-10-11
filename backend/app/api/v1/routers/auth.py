"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.schemas.auth import PasswordLoginRequest, PasswordLoginResponse
from app.api.v1.deps import get_db, get_settings
from app.core.security import generate_access_token
from app.core.settings import Settings
from app.repositories.registration import UsersRepository


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=PasswordLoginResponse, summary="Authenticate with email and password")
def login_with_password(
    payload: PasswordLoginRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PasswordLoginResponse:
    """Validate credentials and return an access token."""

    email_normalized = payload.email.strip().lower()
    user = UsersRepository(session).find_by_email(email_lower=email_normalized)
    if user is None or not _password_matches(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthenticated", "message": "Invalid email or password"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = generate_access_token(
        subject=user.id,
        settings=settings,
        email=user.email,
        scopes=("registration:write",),
        roles=(),
        business_roles=None,
    )

    return PasswordLoginResponse(
        access_token=access_token,
        token=access_token,
        token_type="Bearer",
        expires_in=settings.JWT_ACCESS_TTL_SECONDS,
        user_id=user.id,
        email=user.email,
        first_name=user.first_name,
    )


def _password_matches(candidate: str, stored_hash: str | None) -> bool:
    if stored_hash is None:
        return False
    # TODO: replace with secure hash comparison once password hashing is introduced
    return candidate == stored_hash


__all__ = ["router"]
