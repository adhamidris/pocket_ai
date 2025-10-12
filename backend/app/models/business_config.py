"""Business configuration and integration related models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin
from .registration import MembershipRole


class BusinessSettings(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "business_settings"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    default_response_sla_minutes: Mapped[int | None] = mapped_column(nullable=True)
    default_resolution_sla_minutes: Mapped[int | None] = mapped_column(nullable=True)
    chatportal_settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    business: Mapped["Business"] = relationship(back_populates="settings")

    __table_args__ = (
        UniqueConstraint("business_id", name="uq_business_settings_business"),
        CheckConstraint(
            "default_response_sla_minutes IS NULL OR default_response_sla_minutes > 0",
            name="ck_business_settings_response_sla_positive",
        ),
        CheckConstraint(
            "default_resolution_sla_minutes IS NULL OR default_resolution_sla_minutes > 0",
            name="ck_business_settings_resolution_sla_positive",
        ),
    )


class IntegrationStatus(enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class BusinessIntegration(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "business_integrations"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    integration_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(
            IntegrationStatus,
            name="integration_status_enum",
            create_type=False,
        ),
        nullable=False,
        default=IntegrationStatus.INACTIVE,
    )
    config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    business: Mapped["Business"] = relationship(back_populates="integrations")
    api_keys: Mapped[list["BusinessIntegrationKey"]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_business_integrations_business_type", business_id, integration_type),
    )


class BusinessIntegrationKey(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "business_integration_keys"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    hashed_key: Mapped[str] = mapped_column(String(120), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    integration: Mapped[BusinessIntegration] = relationship(back_populates="api_keys")

    __table_args__ = (
        Index("ix_business_integration_keys_integration", integration_id),
    )


class InviteStatus(enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class TeamInvite(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "team_invites"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(
            MembershipRole,
            name="membership_role_enum",
            create_type=False,
        ),
        nullable=False,
    )
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, name="invite_status_enum", create_type=False), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    business: Mapped["Business"] = relationship(back_populates="invites")
    invited_by_user: Mapped["User"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_team_invites_expires_after_created",
        ),
        Index("ix_team_invites_business_email", business_id, email),
    )


class PermissionCode(enum.Enum):
    VIEW_DASHBOARD = "view_dashboard"
    MANAGE_AGENTS = "manage_agents"
    MANAGE_KNOWLEDGE = "manage_knowledge"
    MANAGE_CASES = "manage_cases"
    MANAGE_CUSTOMERS = "manage_customers"
    MANAGE_INTEGRATIONS = "manage_integrations"


class TeamRolePermission(CreatedAtMixin, Base):
    __tablename__ = "team_role_permissions"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[MembershipRole] = mapped_column(
        Enum(
            MembershipRole,
            name="membership_role_enum",
            create_type=False,
        ),
        primary_key=True,
    )
    permission: Mapped[PermissionCode] = mapped_column(
        Enum(PermissionCode, name="permission_code_enum", create_type=False), primary_key=True
    )

    business: Mapped["Business"] = relationship(back_populates="role_permissions")

    __table_args__ = (
        Index("ix_team_role_permissions_business_role", business_id, role),
    )


__all__ = [
    "BusinessIntegration",
    "BusinessIntegrationKey",
    "BusinessSettings",
    "IntegrationStatus",
    "InviteStatus",
    "PermissionCode",
    "TeamInvite",
    "TeamRolePermission",
]
