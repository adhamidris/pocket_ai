"""Customer relationship management models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin


class CustomerLifecycleStage(enum.Enum):
    LEAD = "lead"
    PROSPECT = "prospect"
    ACTIVE = "active"
    CHURN_RISK = "churn_risk"
    FORMER = "former"


class CustomerContactMethodType(enum.Enum):
    EMAIL = "email"
    PHONE = "phone"
    SOCIAL = "social"
    MESSENGER = "messenger"
    OTHER = "other"


class CustomerNoteVisibility(enum.Enum):
    INTERNAL = "internal"
    SHARED = "shared"


class CustomerActivityEventType(enum.Enum):
    CONVERSATION_STARTED = "conversation_started"
    CONVERSATION_CLOSED = "conversation_closed"
    CASE_OPENED = "case_opened"
    CASE_RESOLVED = "case_resolved"
    ESCALATION_RAISED = "escalation_raised"
    NOTE_ADDED = "note_added"
    TAG_UPDATED = "tag_updated"
    CUSTOM = "custom"


class Customer(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "customers"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    primary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    lifecycle_stage: Mapped[CustomerLifecycleStage] = mapped_column(
        Enum(
            CustomerLifecycleStage,
            name="customer_lifecycle_stage_enum",
            create_type=False,
        ),
        nullable=False,
        default=CustomerLifecycleStage.LEAD,
    )
    satisfaction_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    persona_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)),
        nullable=False,
        default=list,
        server_default=text("ARRAY[]::text[]"),
    )
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    business: Mapped["Business"] = relationship(back_populates="customers")
    contact_methods: Mapped[list["CustomerContactMethod"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    tags: Mapped[list["CustomerTagLink"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    notes: Mapped[list["CustomerNote"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    activities: Mapped[list["CustomerActivityEvent"]] = relationship(
        "CustomerActivityEvent",
        back_populates="customer",
        cascade="all, delete-orphan",
        # Disambiguate: there are two FKs to customers on the event table.
        foreign_keys="CustomerActivityEvent.customer_id",
        primaryjoin="Customer.id == CustomerActivityEvent.customer_id",
    )
    health_scores: Mapped[list["CustomerHealthScore"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    metric_snapshots: Mapped[list["CustomerMetricSnapshot"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    conversation_stats: Mapped[list["CustomerConversationStats"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    chat_visitors: Mapped[list["ChatVisitor"]] = relationship(back_populates="customer")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="customer")
    cases: Mapped[list["Case"]] = relationship(back_populates="customer")

    __table_args__ = (
        Index("ix_customers_business_stage", business_id, lifecycle_stage),
        CheckConstraint(
            "primary_email IS NULL OR position('@' in primary_email) > 1",
            name="ck_customers_primary_email_format",
        ),
        CheckConstraint(
            "country IS NULL OR char_length(country) = 2",
            name="ck_customers_country_code",
        ),
        CheckConstraint(
            "satisfaction_score IS NULL OR (satisfaction_score >= 0 AND satisfaction_score <= 100)",
            name="ck_customers_satisfaction_score_range",
        ),
        UniqueConstraint(
            "business_id",
            "external_id",
            name="uq_customers_business_external_id",
        ),
    )


class CustomerContactMethod(CreatedAtMixin, Base):
    __tablename__ = "customer_contact_methods"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True
    )
    method_type: Mapped[CustomerContactMethodType] = mapped_column(
        Enum(
            CustomerContactMethodType,
            name="customer_contact_method_type_enum",
            create_type=False,
        ),
        primary_key=True,
    )
    value: Mapped[str] = mapped_column(String(255), primary_key=True)
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    customer: Mapped[Customer] = relationship(back_populates="contact_methods")


class CustomerTag(CreatedAtMixin, Base):
    __tablename__ = "customer_tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, primary_key=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)

    business: Mapped["Business"] = relationship(back_populates="customer_tags")
    links: Mapped[list["CustomerTagLink"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "label", name="uq_customer_tags_business_label"),
    )


class CustomerTagLink(CreatedAtMixin, Base):
    __tablename__ = "customer_tag_links"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_tags.id", ondelete="CASCADE"), primary_key=True
    )

    customer: Mapped[Customer] = relationship(back_populates="tags")
    tag: Mapped[CustomerTag] = relationship(back_populates="links")


class CustomerNote(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "customer_notes"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[CustomerNoteVisibility] = mapped_column(
        Enum(CustomerNoteVisibility, name="customer_note_visibility_enum", create_type=False),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pinned: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    customer: Mapped[Customer] = relationship(back_populates="notes")
    author_user: Mapped[Optional["User"]] = relationship(foreign_keys=[author_user_id])
    author_agent: Mapped[Optional["Agent"]] = relationship(foreign_keys=[author_agent_id])


class CustomerActivityEvent(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "customer_activity_events"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[CustomerActivityEventType] = mapped_column(
        Enum(
            CustomerActivityEventType,
            name="customer_activity_event_type_enum",
            create_type=False,
        ),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    actor_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="activities", foreign_keys=[customer_id])
    business: Mapped["Business"] = relationship(back_populates="customer_activity_events")

    __table_args__ = (
        Index(
            "ix_customer_activity_events_customer",
            customer_id,
            text("occurred_at DESC"),
        ),
        Index(
            "ix_customer_activity_events_business",
            business_id,
            text("occurred_at DESC"),
        ),
    )


class CustomerHealthScore(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "customer_health_scores"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    trend: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    driver_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="health_scores")
    business: Mapped["Business"] = relationship(back_populates="customer_health_scores")

    __table_args__ = (
        UniqueConstraint(
            "customer_id", "period_start", name="uq_customer_health_scores_period"
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_customer_health_scores_range",
        ),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_customer_health_scores_period_range",
        ),
    )


class CustomerMetricSnapshot(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "customer_metric_snapshots"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="metric_snapshots")

    __table_args__ = (
        UniqueConstraint(
            "customer_id", "metric", "period_start", name="uq_customer_metric_period"
        ),
    )


class CustomerConversationStats(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "customer_conversation_stats"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conversations_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    total_duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    satisfaction_avg: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="conversation_stats")

    __table_args__ = (
        CheckConstraint(
            "conversations_count >= 0", name="ck_customer_conv_stats_count_positive"
        ),
        CheckConstraint(
            "total_duration_minutes >= 0", name="ck_customer_conv_stats_duration_positive"
        ),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_customer_conv_stats_period_range",
        ),
        Index(
            "ix_customer_conversation_stats_customer_period",
            customer_id,
            period_start,
        ),
    )


__all__ = [
    "Customer",
    "CustomerActivityEvent",
    "CustomerActivityEventType",
    "CustomerContactMethod",
    "CustomerContactMethodType",
    "CustomerConversationStats",
    "CustomerHealthScore",
    "CustomerLifecycleStage",
    "CustomerMetricSnapshot",
    "CustomerNote",
    "CustomerNoteVisibility",
    "CustomerTag",
    "CustomerTagLink",
]
