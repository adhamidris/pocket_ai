"""Agent runtime and analytics models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, date

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin
from .registration import AgentStatus


class AgentKnowledgeAccessState(enum.Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    INHERIT = "inherit"


class AgentKpi(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "agent_kpis"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")

    agent: Mapped["Agent"] = relationship(back_populates="kpis")

    __table_args__ = (
        Index("ix_agent_kpis_agent", agent_id),
    )


class AgentKnowledgeAccess(CreatedAtMixin, Base):
    __tablename__ = "agent_knowledge_access"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), primary_key=True
    )
    access_state: Mapped[AgentKnowledgeAccessState] = mapped_column(
        Enum(
            AgentKnowledgeAccessState,
            name="agent_knowledge_access_state_enum",
            create_type=False,
        ),
        nullable=False,
    )
    last_updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    agent: Mapped["Agent"] = relationship(back_populates="knowledge_access")
    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="agent_access")


class AgentStatusLog(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_status_logs"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[AgentStatus | None] = mapped_column(
        Enum(AgentStatus, name="agent_status_enum", create_type=False), nullable=True
    )
    to_status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status_enum", create_type=False), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    agent: Mapped["Agent"] = relationship(back_populates="status_logs", foreign_keys=[agent_id])

    __table_args__ = (
        Index("ix_agent_status_logs_agent", agent_id, text("created_at DESC")),
    )


class AgentHourlyStatus(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_hourly_statuses"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    status_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status_enum", create_type=False), nullable=False
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="hourly_statuses")

    __table_args__ = (
        UniqueConstraint("agent_id", "status_at", name="uq_agent_hourly_status_unique"),
    )


class AgentPublicLinkToken(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_public_link_tokens"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="public_link_tokens")


class AgentRuntimeProfile(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "agent_runtime_profiles"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    tools_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")

    agent: Mapped["Agent"] = relationship(back_populates="runtime_profiles")

    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_runtime_profiles_version"),
        CheckConstraint(
            "status IN ('draft','active','deprecated')",
            name="ck_agent_runtime_profiles_status",
        ),
    )


class AgentSession(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "agent_sessions"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    runtime_profile_version: Mapped[int | None] = mapped_column(nullable=True)
    active_tool_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_tool_invocation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="sessions")
    conversation: Mapped["Conversation"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "conversation_id",
            name="uq_agent_sessions_agent_conversation",
        ),
    )


class AgentMetricDaily(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_metrics_daily"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    conversations_handled: Mapped[int] = mapped_column(nullable=False)
    cases_resolved: Mapped[int] = mapped_column(nullable=False)
    escalations: Mapped[int] = mapped_column(nullable=False)
    csat_average: Mapped[float | None] = mapped_column(nullable=True)
    avg_handle_time_seconds: Mapped[int | None] = mapped_column(nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="metric_snapshots")
    business: Mapped["Business"] = relationship(back_populates="agent_metrics")

    __table_args__ = (
        UniqueConstraint("agent_id", "metric_date", name="uq_agent_metrics_daily_date"),
        CheckConstraint(
            "conversations_handled >= 0 AND cases_resolved >= 0 AND escalations >= 0",
            name="ck_agent_metrics_daily_positive_counts",
        ),
    )


__all__ = [
    "AgentHourlyStatus",
    "AgentKnowledgeAccess",
    "AgentKnowledgeAccessState",
    "AgentKpi",
    "AgentMetricDaily",
    "AgentPublicLinkToken",
    "AgentRuntimeProfile",
    "AgentSession",
    "AgentStatus",
    "AgentStatusLog",
]
