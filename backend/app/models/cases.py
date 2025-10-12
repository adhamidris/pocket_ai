"""Case management and escalation models."""

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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin


class CaseStatus(enum.Enum):
    OPEN = "open"
    PENDING_CUSTOMER = "pending_customer"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    ARCHIVED = "archived"


class CasePriority(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class CaseType(enum.Enum):
    INQUIRY = "inquiry"
    REQUEST = "request"
    COMPLAINT = "complaint"
    ESCALATION = "escalation"
    OTHER = "other"


class CaseLinkTargetType(enum.Enum):
    CONVERSATION = "conversation"
    KNOWLEDGE = "knowledge"
    EXTERNAL = "external"


class CaseHistoryEventType(enum.Enum):
    STATUS_CHANGED = "status_changed"
    PRIORITY_CHANGED = "priority_changed"
    NOTE_ADDED = "note_added"
    ASSIGNEE_CHANGED = "assignee_changed"
    ESCALATED = "escalated"
    FIELD_UPDATED = "field_updated"
    CUSTOMER_MESSAGE = "customer_message"
    AGENT_MESSAGE = "agent_message"


class CaseSuggestedActionStatus(enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class EscalationStatus(enum.Enum):
    PENDING_REVIEW = "pending_review"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class EscalationTrigger(enum.Enum):
    RULE = "rule"
    MANUAL = "manual"
    NEGATIVE_SENTIMENT = "negative_sentiment"
    SLA_BREACH = "sla_breach"


class SlaMetricType(enum.Enum):
    FIRST_RESPONSE = "first_response"
    RESOLUTION = "resolution"


class CaseFollowUpStatus(enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELED = "canceled"


class Case(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "cases"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    origin_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    primary_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[CasePriority] = mapped_column(
        Enum(CasePriority, name="case_priority_enum", create_type=False), nullable=False
    )
    case_type: Mapped[CaseType] = mapped_column(
        Enum(CaseType, name="case_type_enum", create_type=False), nullable=False
    )
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, name="case_status_enum", create_type=False), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_sentiment_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    customer_priority_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    open_duration_minutes: Mapped[int | None] = mapped_column(nullable=True)

    business: Mapped["Business"] = relationship(back_populates="cases")
    customer: Mapped[Optional["Customer"]] = relationship(back_populates="cases")
    origin_conversation: Mapped[Optional["Conversation"]] = relationship(
        "Conversation",
        foreign_keys="Case.origin_conversation_id",
        primaryjoin="Case.origin_conversation_id == Conversation.id",
    )
    primary_agent: Mapped[Optional["Agent"]] = relationship()
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="case",
        foreign_keys="Conversation.case_id",
        primaryjoin="Case.id == Conversation.case_id",
    )
    assignments: Mapped[list["CaseAssignment"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    history_events: Mapped[list["CaseHistoryEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    notes: Mapped[list["CaseNote"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    documents: Mapped[list["CaseDocument"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    suggested_actions: Mapped[list["CaseSuggestedAction"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    links: Mapped[list["CaseLink"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    follow_ups: Mapped[list["CaseFollowUp"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    sla_trackers: Mapped[list["CaseSlaTracker"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_cases_business_status", business_id, status),
        Index("ix_cases_business_priority", business_id, priority),
        CheckConstraint(
            "latest_sentiment_score IS NULL OR (latest_sentiment_score >= -100 AND latest_sentiment_score <= 100)",
            name="ck_cases_sentiment_range",
        ),
        CheckConstraint(
            "customer_priority_score IS NULL OR (customer_priority_score >= 0 AND customer_priority_score <= 100)",
            name="ck_cases_priority_score_range",
        ),
        CheckConstraint(
            "open_duration_minutes IS NULL OR open_duration_minutes >= 0",
            name="ck_cases_open_duration_positive",
        ),
    )


class CaseAssignment(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "case_assignments"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case: Mapped[Case] = relationship(back_populates="assignments")

    __table_args__ = (
        CheckConstraint(
            "assigned_agent_id IS NOT NULL OR assigned_user_id IS NOT NULL",
            name="ck_case_assignments_actor_present",
        ),
    )


class CaseHistoryEvent(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "case_history_events"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[CaseHistoryEventType] = mapped_column(
        Enum(
            CaseHistoryEventType,
            name="case_history_event_type_enum",
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
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    case: Mapped[Case] = relationship(back_populates="history_events")

    __table_args__ = (
        Index("ix_case_history_events_case", case_id, text("created_at DESC")),
    )


class CaseNote(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "case_notes"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    is_internal: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    body: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[Case] = relationship(back_populates="notes")
    author_user: Mapped[Optional["User"]] = relationship(foreign_keys=[author_user_id])
    author_agent: Mapped[Optional["Agent"]] = relationship(foreign_keys=[author_agent_id])


class CaseDocument(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "case_documents"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    storage_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("storage_assets.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    case: Mapped[Case] = relationship(back_populates="documents")
    storage_asset: Mapped["StorageAsset"] = relationship()


class CaseSuggestedAction(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "case_suggested_actions"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    suggestion_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[CaseSuggestedActionStatus] = mapped_column(
        Enum(
            CaseSuggestedActionStatus,
            name="case_suggested_action_status_enum",
            create_type=False,
        ),
        nullable=False,
        server_default=CaseSuggestedActionStatus.PENDING.value,
    )
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case: Mapped[Case] = relationship(back_populates="suggested_actions")


class Escalation(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "escalations"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    escalated_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[EscalationStatus] = mapped_column(
        Enum(EscalationStatus, name="escalation_status_enum", create_type=False), nullable=False
    )
    trigger: Mapped[EscalationTrigger] = mapped_column(
        Enum(EscalationTrigger, name="escalation_trigger_enum", create_type=False), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case: Mapped[Case] = relationship(back_populates="escalations")

    __table_args__ = (
        Index("ix_escalations_case", case_id),
        Index("ix_escalations_status", status),
    )


class CaseLink(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "case_links"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[CaseLinkTargetType] = mapped_column(
        Enum(CaseLinkTargetType, name="case_link_target_type_enum", create_type=False),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    case: Mapped[Case] = relationship(back_populates="links")

    __table_args__ = (
        CheckConstraint(
            "(target_id IS NOT NULL) OR (external_url IS NOT NULL)",
            name="ck_case_links_target_or_url",
        ),
    )


class CaseFollowUp(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "case_follow_ups"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[CaseFollowUpStatus] = mapped_column(
        Enum(CaseFollowUpStatus, name="case_follow_up_status_enum", create_type=False),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    case: Mapped[Case] = relationship(back_populates="follow_ups")

    __table_args__ = (
        CheckConstraint("due_at > created_at", name="ck_case_follow_ups_due_after_created"),
    )


class CaseSlaTracker(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "case_sla_trackers"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[SlaMetricType] = mapped_column(
        Enum(SlaMetricType, name="sla_metric_type_enum", create_type=False), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    met_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    breached_flag: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    case: Mapped[Case] = relationship(back_populates="sla_trackers")

    __table_args__ = (
        UniqueConstraint("case_id", "metric", name="uq_case_sla_trackers_case_metric"),
        CheckConstraint("due_at > created_at", name="ck_case_sla_trackers_due_after_created"),
    )


class SlaBreach(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sla_breaches"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[SlaMetricType] = mapped_column(
        Enum(SlaMetricType, name="sla_metric_type_enum", create_type=False), nullable=False
    )
    breached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_over_seconds: Mapped[int] = mapped_column(nullable=False)
    auto_acknowledged: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )

    case: Mapped[Case] = relationship()

    __table_args__ = (
        Index("ix_sla_breaches_case", case_id),
        CheckConstraint(
            "duration_over_seconds >= 0",
            name="ck_sla_breaches_duration_positive",
        ),
    )


class CaseMetricDaily(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "case_metrics_daily"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_cases: Mapped[int] = mapped_column(nullable=False)
    urgent_cases: Mapped[int] = mapped_column(nullable=False)
    avg_time_open_minutes: Mapped[int | None] = mapped_column(nullable=True)

    business: Mapped["Business"] = relationship(back_populates="case_metrics")

    __table_args__ = (
        UniqueConstraint("business_id", "metric_date", name="uq_case_metrics_daily_date"),
        CheckConstraint(
            "open_cases >= 0 AND urgent_cases >= 0",
            name="ck_case_metrics_daily_non_negative",
        ),
    )


__all__ = [
    "Case",
    "CaseAssignment",
    "CaseDocument",
    "CaseFollowUp",
    "CaseFollowUpStatus",
    "CaseHistoryEvent",
    "CaseHistoryEventType",
    "CaseLink",
    "CaseLinkTargetType",
    "CaseMetricDaily",
    "CaseNote",
    "CasePriority",
    "CaseStatus",
    "CaseSuggestedAction",
    "CaseSuggestedActionStatus",
    "CaseSlaTracker",
    "CaseType",
    "Escalation",
    "EscalationStatus",
    "EscalationTrigger",
    "SlaBreach",
    "SlaMetricType",
]
