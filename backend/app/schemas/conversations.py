"""Pydantic schemas for internal conversation management APIs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.cases import CasePriority, CaseStatus
from app.models.conversations import (
    ChatChannel,
    ConversationEndReason,
    ConversationMessageChannel,
    ConversationMessageType,
    ConversationMessageVisibility,
    ConversationParticipantType,
    ConversationSource,
    ConversationStatus,
)


BusinessId = UUID
ConversationId = UUID
MessageId = UUID
ParticipantId = UUID
CaseId = UUID
CustomerId = UUID
AgentId = UUID
UserId = UUID
StorageAssetId = UUID


class BaseSchemaModel(BaseModel):
    """Shared base configuration across conversation schemas."""

    model_config = ConfigDict(extra="forbid")


class ConversationAttachment(BaseSchemaModel):
    id: UUID
    message_id: MessageId
    storage_asset_id: StorageAssetId
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    content_type: Annotated[str | None, Field(default=None, max_length=120)]
    size_bytes: Annotated[int, Field(ge=1)]
    caption: Annotated[str | None, Field(default=None, max_length=255)]
    metadata: dict | None = None


class ConversationMessage(BaseSchemaModel):
    id: MessageId
    conversation_id: ConversationId
    message_type: ConversationMessageType
    visibility: ConversationMessageVisibility
    channel: ConversationMessageChannel
    body: Annotated[str | None, Field(default=None, min_length=1)]
    payload: dict | None = None
    sent_at: datetime
    author_agent_id: AgentId | None = None
    author_user_id: UserId | None = None
    author_customer_id: CustomerId | None = None
    attachments: Sequence[ConversationAttachment] = Field(default_factory=tuple)


class ConversationParticipant(BaseSchemaModel):
    id: UUID
    conversation_id: ConversationId
    participant_type: ConversationParticipantType
    participant_id: ParticipantId | None
    display_name: Annotated[str | None, Field(default=None, max_length=160)]
    joined_at: datetime
    left_at: datetime | None = None


class ConversationStatusLogEntry(BaseSchemaModel):
    id: UUID
    conversation_id: ConversationId
    from_status: ConversationStatus | None
    to_status: ConversationStatus
    actor_user_id: UserId | None = None
    actor_agent_id: AgentId | None = None
    reason: Annotated[str | None, Field(default=None, max_length=4000)]
    created_at: datetime


class ConversationTurnSnapshot(BaseSchemaModel):
    id: UUID
    conversation_id: ConversationId
    message_id: MessageId | None
    model: Annotated[str, Field(min_length=1, max_length=80)]
    temperature: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int | None
    prompt_content: Annotated[str | None, Field(default=None)]
    completion_content: Annotated[str | None, Field(default=None)]
    metadata: dict | None = None
    created_at: datetime


class ConversationSummary(BaseSchemaModel):
    id: UUID
    conversation_id: ConversationId
    ai_overview: Annotated[str | None, Field(default=None)]
    key_points: dict | None = None
    actions_taken: dict | None = None
    suggested_actions: dict | None = None
    last_generated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationListItem(BaseSchemaModel):
    id: ConversationId
    business_id: BusinessId
    visitor_id: UUID | None
    customer_id: CustomerId | None
    case_id: CaseId | None
    primary_agent_id: AgentId | None
    source: ConversationSource
    status: ConversationStatus
    channel: ChatChannel
    latest_message_type: ConversationMessageType | None = None
    subject: Annotated[str | None, Field(default=None, max_length=200)]
    preview_snippet: Annotated[str | None, Field(default=None, max_length=400)]
    opened_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    csat_score: Annotated[float | None, Field(default=None, ge=0, le=100)]
    satisfaction_recorded_at: datetime | None = None
    escalation_flagged: bool = False
    unread_count: Annotated[int, Field(ge=0)] = 0


class ConversationListResponse(BaseSchemaModel):
    total: int
    items: Sequence[ConversationListItem]
    next_cursor: Annotated[str | None, Field(default=None, max_length=120)]
    has_next: bool


class ConversationDetail(BaseSchemaModel):
    id: ConversationId
    business_id: BusinessId
    visitor_id: UUID | None
    customer_id: CustomerId | None
    case_id: CaseId | None
    primary_agent_id: AgentId | None
    status: ConversationStatus
    source: ConversationSource
    channel: ChatChannel
    end_reason: ConversationEndReason | None = None
    first_response_at: datetime | None = None
    first_response_latency_seconds: int | None = None
    resolution_time_seconds: int | None = None
    closed_at: datetime | None = None
    csat_score: Annotated[float | None, Field(default=None, ge=0, le=100)]
    csat_comment: Annotated[str | None, Field(default=None, max_length=4000)]
    satisfaction_recorded_at: datetime | None = None
    runtime_profile_version: int | None = None
    created_at: datetime
    updated_at: datetime
    messages: Sequence[ConversationMessage]
    participants: Sequence[ConversationParticipant]
    summary: ConversationSummary | None = None
    status_log: Sequence[ConversationStatusLogEntry] = Field(default_factory=tuple)
    turn_snapshots: Sequence[ConversationTurnSnapshot] = Field(default_factory=tuple)


class ConversationDetailResponse(BaseSchemaModel):
    conversation: ConversationDetail


class ConversationStatusUpdateRequest(BaseSchemaModel):
    status: ConversationStatus
    end_reason: ConversationEndReason | None = None
    actor_user_id: UserId | None = None
    actor_agent_id: AgentId | None = None
    reason: Annotated[str | None, Field(default=None, max_length=4000)]


class ConversationAssignmentUpdateRequest(BaseSchemaModel):
    primary_agent_id: AgentId | None


class ConversationCsatUpdateRequest(BaseSchemaModel):
    score: Annotated[float | None, Field(default=None, ge=0, le=100)]
    comment: Annotated[str | None, Field(default=None, max_length=4000)]
    recorded_at: datetime | None = None


class ConversationExportQuery(BaseSchemaModel):
    business_id: BusinessId
    from_date: date | None = None
    to_date: date | None = None
    status: Sequence[ConversationStatus] | None = None
    primary_agent_ids: Sequence[AgentId] | None = None


class ConversationMessagesPage(BaseSchemaModel):
    messages: Sequence[ConversationMessage]
    next_cursor: Annotated[str | None, Field(default=None, max_length=120)]
    has_more: bool


class ConversationMetricsDailyPoint(BaseSchemaModel):
    metric_date: date
    live_conversations: Annotated[int, Field(ge=0)]
    new_conversations: Annotated[int, Field(ge=0)]
    escalations: Annotated[int, Field(ge=0)]
    avg_first_response_seconds: Annotated[int | None, Field(default=None, ge=0)]
    avg_resolution_seconds: Annotated[int | None, Field(default=None, ge=0)]
    csat_average: Annotated[float | None, Field(default=None, ge=0, le=100)]


class ConversationMetricsResponse(BaseSchemaModel):
    points: Sequence[ConversationMetricsDailyPoint]


class ConversationCaseLink(BaseSchemaModel):
    id: CaseId
    title: Annotated[str, Field(min_length=1, max_length=200)]
    status: CaseStatus
    priority: CasePriority
    opened_at: datetime
    resolved_at: datetime | None = None


class ConversationCustomerSnapshot(BaseSchemaModel):
    customer_id: CustomerId | None
    display_name: Annotated[str | None, Field(default=None, max_length=160)]
    primary_email: Annotated[str | None, Field(default=None, max_length=255)]
    primary_phone: Annotated[str | None, Field(default=None, max_length=64)]
    lifecycle_stage: Annotated[str | None, Field(default=None, max_length=32)]


class ConversationListFilters(BaseSchemaModel):
    statuses: Sequence[ConversationStatus] | None = None
    sources: Sequence[ConversationSource] | None = None
    agents: Sequence[AgentId] | None = None
    customers: Sequence[CustomerId] | None = None
    search: Annotated[str | None, Field(default=None, max_length=160)]
    created_from: datetime | None = None
    created_to: datetime | None = None
    limit: Annotated[int, Field(gt=0, le=200)] = 50
    cursor: Annotated[str | None, Field(default=None, max_length=120)] = None


__all__ = [
    "AgentId",
    "BusinessId",
    "CaseId",
    "ChatChannel",
    "ConversationAttachment",
    "ConversationAssignmentUpdateRequest",
    "ConversationCaseLink",
    "ConversationCsatUpdateRequest",
    "ConversationCustomerSnapshot",
    "ConversationDetail",
    "ConversationDetailResponse",
    "ConversationEndReason",
    "ConversationExportQuery",
    "ConversationId",
    "ConversationListFilters",
    "ConversationListItem",
    "ConversationListResponse",
    "ConversationMessage",
    "ConversationMessageChannel",
    "ConversationMessageType",
    "ConversationMessageVisibility",
    "ConversationMessagesPage",
    "ConversationParticipant",
    "ConversationParticipantType",
    "ConversationSource",
    "ConversationStatus",
    "ConversationStatusLogEntry",
    "ConversationStatusUpdateRequest",
    "ConversationSummary",
    "ConversationTurnSnapshot",
    "ConversationMetricsDailyPoint",
    "ConversationMetricsResponse",
    "CustomerId",
    "MessageId",
    "ParticipantId",
    "StorageAssetId",
    "UserId",
]
