"""Security, audit, and compliance models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin


class AuditAction(enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ACCESSED = "accessed"
    PERMISSION_CHANGED = "permission_changed"


class AuditLog(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"

    business_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action_enum", create_type=False), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    business: Mapped[Optional["Business"]] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_business", business_id, text("created_at DESC")),
    )


class DataSubjectRequestStatus(enum.Enum):
    RECEIVED = "received"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class DataSubjectRequest(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "data_subject_requests"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    requester_email: Mapped[str] = mapped_column(String(255), nullable=False)
    request_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DataSubjectRequestStatus] = mapped_column(
        Enum(
            DataSubjectRequestStatus,
            name="data_subject_request_status_enum",
            create_type=False,
        ),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    business: Mapped["Business"] = relationship(back_populates="data_subject_requests")


class DataRetentionJobStatus(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DataRetentionJob(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "data_retention_jobs"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    retention_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DataRetentionJobStatus] = mapped_column(
        Enum(DataRetentionJobStatus, name="data_retention_job_status_enum", create_type=False),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_records: Mapped[int | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    business: Mapped["Business"] = relationship(back_populates="data_retention_jobs")

    __table_args__ = (
        CheckConstraint(
            "deleted_records IS NULL OR deleted_records >= 0",
            name="ck_data_retention_jobs_deleted_records_positive",
        ),
    )


class EventBusOutboxStatus(enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class EventBusOutbox(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "event_bus_outbox"

    business_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[EventBusOutboxStatus] = mapped_column(
        Enum(EventBusOutboxStatus, name="event_bus_outbox_status_enum", create_type=False),
        nullable=False,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")

    business: Mapped[Optional["Business"]] = relationship(back_populates="event_bus_outbox_entries")

    __table_args__ = (
        Index("ix_event_bus_outbox_status", status, next_attempt_at),
    )


__all__ = [
    "AuditAction",
    "AuditLog",
    "DataRetentionJob",
    "DataRetentionJobStatus",
    "DataSubjectRequest",
    "DataSubjectRequestStatus",
    "EventBusOutbox",
    "EventBusOutboxStatus",
]
