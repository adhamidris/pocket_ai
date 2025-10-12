"""Automation and workflow models."""

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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin


class AutomationRuleStatus(enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DRAFT = "draft"


class AutomationActionType(enum.Enum):
    ASSIGN_AGENT = "assign_agent"
    ESCALATE = "escalate"
    SEND_EMAIL = "send_email"
    POST_WEBHOOK = "post_webhook"


class AutomationTargetType(enum.Enum):
    CASE = "case"
    CONVERSATION = "conversation"


class AutomationRule(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "automation_rules"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AutomationRuleStatus] = mapped_column(
        Enum(AutomationRuleStatus, name="automation_rule_status_enum", create_type=False),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    conditions_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action_type: Mapped[AutomationActionType] = mapped_column(
        Enum(AutomationActionType, name="automation_action_type_enum", create_type=False),
        nullable=False,
    )
    action_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    business: Mapped["Business"] = relationship(back_populates="automation_rules")
    runs: Mapped[list["AutomationRun"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_automation_rules_business_status", business_id, status),
    )


class AutomationRunStatus(enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class AutomationRun(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "automation_runs"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[AutomationTargetType] = mapped_column(
        Enum(AutomationTargetType, name="automation_target_type_enum", create_type=False),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[AutomationRunStatus] = mapped_column(
        Enum(AutomationRunStatus, name="automation_run_status_enum", create_type=False),
        nullable=False,
    )
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    rule: Mapped[AutomationRule] = relationship(back_populates="runs")
    business: Mapped["Business"] = relationship(back_populates="automation_runs")

    __table_args__ = (
        Index("ix_automation_runs_business_target", business_id, target_type),
    )


__all__ = [
    "AutomationActionType",
    "AutomationRule",
    "AutomationRuleStatus",
    "AutomationRun",
    "AutomationRunStatus",
    "AutomationTargetType",
]
