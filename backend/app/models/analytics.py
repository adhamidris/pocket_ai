"""Analytics fact and snapshot tables."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin


class MetricRefreshJobStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MetricRefreshJob(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "metric_refresh_jobs"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[MetricRefreshJobStatus] = mapped_column(
        Enum(
            MetricRefreshJobStatus,
            name="metric_refresh_job_status_enum",
            create_type=False,
        ),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)

    business: Mapped["Business"] = relationship(back_populates="metric_refresh_jobs")

    __table_args__ = ()


class ConversationFact(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "conversation_facts"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_count: Mapped[int] = mapped_column(nullable=False)
    customer_messages: Mapped[int] = mapped_column(nullable=False)
    agent_messages: Mapped[int] = mapped_column(nullable=False)
    first_response_seconds: Mapped[int | None] = mapped_column(nullable=True)
    resolution_seconds: Mapped[int | None] = mapped_column(nullable=True)
    csat_score: Mapped[float | None] = mapped_column(nullable=True)

    business: Mapped["Business"] = relationship(back_populates="conversation_facts")

    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_conversation_facts_conversation"),
        CheckConstraint(
            "message_count >= 0 AND customer_messages >= 0 AND agent_messages >= 0",
            name="ck_conversation_facts_counts",
        ),
    )


class CaseFact(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "case_facts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    escalation_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    first_response_seconds: Mapped[int | None] = mapped_column(nullable=True)
    resolution_seconds: Mapped[int | None] = mapped_column(nullable=True)
    customer_satisfaction: Mapped[float | None] = mapped_column(nullable=True)

    business: Mapped["Business"] = relationship(back_populates="case_facts")

    __table_args__ = (
        UniqueConstraint("case_id", name="uq_case_facts_case"),
        CheckConstraint(
            "reopened_count >= 0 AND escalation_count >= 0",
            name="ck_case_facts_counts",
        ),
    )


class BusinessCustomerMetricDaily(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "customer_metrics_daily"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    new_customers: Mapped[int] = mapped_column(nullable=False)
    satisfied_customers: Mapped[int] = mapped_column(nullable=False)
    expansion_opportunities: Mapped[int] = mapped_column(nullable=False)

    business: Mapped["Business"] = relationship(back_populates="customer_metrics")

    __table_args__ = (
        UniqueConstraint(
            "business_id", "metric_date", name="uq_customer_metrics_daily_date"
        ),
        CheckConstraint(
            "new_customers >= 0 AND satisfied_customers >= 0 AND expansion_opportunities >= 0",
            name="ck_customer_metrics_daily_counts",
        ),
    )


__all__ = [
    "BusinessCustomerMetricDaily",
    "CaseFact",
    "ConversationFact",
    "MetricRefreshJob",
    "MetricRefreshJobStatus",
]
