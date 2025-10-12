"""Chat portal conversation models."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Optional

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


class ChatVisitorType(enum.Enum):
    ANONYMOUS = "anonymous"
    KNOWN = "known"


class ChatChannel(enum.Enum):
    WEB_WIDGET = "web_widget"
    WHATSAPP = "whatsapp"
    MESSENGER = "messenger"
    API = "api"
    EMAIL = "email"
    OTHER = "other"


class ChatPresenceStatus(enum.Enum):
    LIVE = "live"
    IDLE = "idle"
    OFFLINE = "offline"


class ConversationSource(enum.Enum):
    WEB = "web"
    MOBILE = "mobile"
    API = "api"
    INTEGRATION = "integration"


class ConversationStatus(enum.Enum):
    NEW = "new"
    LIVE = "live"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    CLOSED_WITHOUT_RESOLUTION = "closed_without_resolution"
    EXPIRED = "expired"


class ConversationEndReason(enum.Enum):
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    CUSTOMER_LEFT = "customer_left"
    AGENT_ENDED = "agent_ended"
    TIMEOUT = "timeout"
    OTHER = "other"


class ConversationParticipantType(enum.Enum):
    AGENT = "agent"
    TEAMMATE = "teammate"
    CUSTOMER = "customer"
    SYSTEM = "system"


class ConversationMessageType(enum.Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    SYSTEM = "system"
    TOOL = "tool"
    INTERNAL = "internal"


class ConversationMessageVisibility(enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"


class ConversationMessageChannel(enum.Enum):
    TEXT = "text"
    FILE = "file"
    ACTION = "action"


class ChatVisitor(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "chat_visitors"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    visitor_type: Mapped[ChatVisitorType] = mapped_column(
        Enum(ChatVisitorType, name="chat_visitor_type_enum", create_type=False),
        nullable=False,
        default=ChatVisitorType.ANONYMOUS,
    )
    session_token: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[ChatChannel] = mapped_column(
        Enum(ChatChannel, name="chat_channel_enum", create_type=False), nullable=False
    )
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    landing_page: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_session_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_session_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    welcome_template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    business: Mapped["Business"] = relationship(back_populates="chat_visitors")
    customer: Mapped[Optional["Customer"]] = relationship(back_populates="chat_visitors")
    fingerprints: Mapped[list["ChatDeviceFingerprint"]] = relationship(
        back_populates="visitor", cascade="all, delete-orphan"
    )
    presence_pings: Mapped[list["ChatPresencePing"]] = relationship(
        back_populates="visitor", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="visitor", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "business_id", "session_token", name="uq_chat_visitors_business_session"
        ),
        Index("ix_chat_visitors_business", business_id),
    )


class ChatDeviceFingerprint(CreatedAtMixin, Base):
    __tablename__ = "chat_device_fingerprints"

    visitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_visitors.id", ondelete="CASCADE"), primary_key=True
    )
    fingerprint_hash: Mapped[str] = mapped_column(String(120), primary_key=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    visitor: Mapped[ChatVisitor] = relationship(back_populates="fingerprints")


class ChatPresencePing(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "chat_presence_pings"

    visitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_visitors.id", ondelete="CASCADE"), nullable=False
    )
    pinged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ChatPresenceStatus] = mapped_column(
        Enum(ChatPresenceStatus, name="chat_presence_status_enum", create_type=False),
        nullable=False,
    )

    visitor: Mapped[ChatVisitor] = relationship(back_populates="presence_pings")

    __table_args__ = (
        Index(
            "ix_chat_presence_pings_visitor",
            visitor_id,
            text("pinged_at DESC"),
        ),
    )

class Conversation(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "conversations"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_visitors.id", ondelete="SET NULL"), nullable=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True
    )
    primary_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[ConversationSource] = mapped_column(
        Enum(ConversationSource, name="conversation_source_enum", create_type=False),
        nullable=False,
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status_enum", create_type=False),
        nullable=False,
    )
    end_reason: Mapped[ConversationEndReason | None] = mapped_column(
        Enum(ConversationEndReason, name="conversation_end_reason_enum", create_type=False),
        nullable=True,
    )
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_latency_seconds: Mapped[int | None] = mapped_column(nullable=True)
    resolution_time_seconds: Mapped[int | None] = mapped_column(nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    csat_score: Mapped[float | None] = mapped_column(nullable=True)
    csat_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    satisfaction_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    runtime_profile_version: Mapped[int | None] = mapped_column(nullable=True)

    business: Mapped["Business"] = relationship(back_populates="conversations")
    visitor: Mapped[ChatVisitor | None] = relationship(back_populates="conversations")
    customer: Mapped[Optional["Customer"]] = relationship(back_populates="conversations")
    case: Mapped[Optional["Case"]] = relationship(
        "Case",
        back_populates="conversations",
        foreign_keys="Conversation.case_id",
        primaryjoin="Conversation.case_id == Case.id",
    )
    participants: Mapped[list["ConversationParticipant"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    turn_snapshots: Mapped[list["ConversationTurnSnapshot"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    summary: Mapped[Optional["ConversationSummary"]] = relationship(
        back_populates="conversation", uselist=False, cascade="all, delete-orphan"
    )
    status_log: Mapped[list["ConversationStatusLog"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_conversations_business_status", business_id, status),
        Index(
            "ix_conversations_business_updated",
            business_id,
            text("updated_at DESC"),
        ),
        Index("ix_conversations_customer", customer_id),
        Index("ix_conversations_case", case_id),
        CheckConstraint(
            "csat_score IS NULL OR (csat_score >= 0 AND csat_score <= 100)",
            name="ck_conversations_csat_range",
        ),
        CheckConstraint(
            "first_response_latency_seconds IS NULL OR first_response_latency_seconds >= 0",
            name="ck_conversations_first_response_latency",
        ),
        CheckConstraint(
            "resolution_time_seconds IS NULL OR resolution_time_seconds >= 0",
            name="ck_conversations_resolution_time",
        ),
    )


class ConversationParticipant(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "conversation_participants"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    participant_type: Mapped[ConversationParticipantType] = mapped_column(
        Enum(
            ConversationParticipantType,
            name="conversation_participant_type_enum",
            create_type=False,
        ),
        nullable=False,
    )
    participant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="participants")

    __table_args__ = (
        Index(
            "ix_conversation_participants_conversation",
            conversation_id,
            participant_type,
        ),
    )


class ConversationMessage(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "conversation_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    author_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    message_type: Mapped[ConversationMessageType] = mapped_column(
        Enum(ConversationMessageType, name="conversation_message_type_enum", create_type=False),
        nullable=False,
    )
    visibility: Mapped[ConversationMessageVisibility] = mapped_column(
        Enum(
            ConversationMessageVisibility,
            name="conversation_message_visibility_enum",
            create_type=False,
        ),
        nullable=False,
    )
    channel: Mapped[ConversationMessageChannel] = mapped_column(
        Enum(
            ConversationMessageChannel,
            name="conversation_message_channel_enum",
            create_type=False,
        ),
        nullable=False,
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    attachments: Mapped[list["MessageAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_conversation_messages_conversation", conversation_id, sent_at),
        CheckConstraint(
            "body IS NOT NULL OR payload_json IS NOT NULL",
            name="ck_conversation_messages_body_or_payload",
        ),
    )


class MessageAttachment(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "message_attachments"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False
    )
    storage_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("storage_assets.id", ondelete="CASCADE"), nullable=False
    )
    caption: Mapped[str | None] = mapped_column(String(255), nullable=True)

    message: Mapped[ConversationMessage] = relationship(back_populates="attachments")
    storage_asset: Mapped["StorageAsset"] = relationship()


class ConversationTurnSnapshot(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "conversation_turn_snapshots"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    temperature: Mapped[float | None] = mapped_column(nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    prompt_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="turn_snapshots")
    message: Mapped[ConversationMessage | None] = relationship()


class ConversationSummary(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "conversation_summaries"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    ai_overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actions_taken_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    suggested_actions_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="summary")

    __table_args__ = (
        UniqueConstraint(
            "conversation_id", name="uq_conversation_summaries_conversation"
        ),
    )


class ConversationStatusLog(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "conversation_status_logs"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[ConversationStatus | None] = mapped_column(
        Enum(ConversationStatus, name="conversation_status_enum", create_type=False),
        nullable=True,
    )
    to_status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status_enum", create_type=False),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="status_log")

    __table_args__ = (
        Index(
            "ix_conversation_status_logs_conversation",
            conversation_id,
            text("created_at DESC"),
        ),
    )


class ConversationMetricDaily(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "conversation_metrics_daily"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    live_conversations: Mapped[int] = mapped_column(nullable=False)
    new_conversations: Mapped[int] = mapped_column(nullable=False)
    escalations: Mapped[int] = mapped_column(nullable=False)
    avg_first_response_seconds: Mapped[int | None] = mapped_column(nullable=True)
    avg_resolution_seconds: Mapped[int | None] = mapped_column(nullable=True)
    csat_average: Mapped[float | None] = mapped_column(nullable=True)

    business: Mapped["Business"] = relationship(back_populates="conversation_metrics")

    __table_args__ = (
        UniqueConstraint(
            "business_id", "metric_date", name="uq_conversation_metrics_daily_date"
        ),
        CheckConstraint(
            "live_conversations >= 0 AND new_conversations >= 0 AND escalations >= 0",
            name="ck_conversation_metrics_daily_positive_counts",
        ),
    )


__all__ = [
    "ChatChannel",
    "ChatDeviceFingerprint",
    "ChatPresencePing",
    "ChatPresenceStatus",
    "ChatVisitor",
    "ChatVisitorType",
    "Conversation",
    "ConversationEndReason",
    "ConversationMessage",
    "ConversationMessageChannel",
    "ConversationMessageType",
    "ConversationMessageVisibility",
    "ConversationMetricDaily",
    "ConversationParticipant",
    "ConversationParticipantType",
    "ConversationSource",
    "ConversationStatus",
    "ConversationStatusLog",
    "ConversationSummary",
    "ConversationTurnSnapshot",
    "MessageAttachment",
]
