"""Core conversation management business logic."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.conversations import (
    Conversation,
    ConversationEndReason,
    ConversationMessage,
    ConversationMessageType,
    ConversationParticipantType,
    ConversationSource,
    ConversationStatus,
    ConversationStatusLog,
)
from app.models.customers import CustomerActivityEvent, CustomerActivityEventType
from app.repositories.conversations import (
    ConversationListFilters,
    ConversationsRepository,
)
from app.services.errors import ServiceNotFoundError, ServiceValidationError
from app.services.messages import ConversationMessageDTO, MessageAttachmentDTO


Cursor = str


@dataclass(slots=True, frozen=True)
class ConversationParticipantDTO:
    id: uuid.UUID
    participant_type: ConversationParticipantType
    participant_id: uuid.UUID | None
    joined_at: datetime
    left_at: datetime | None


@dataclass(slots=True, frozen=True)
class ConversationStatusLogDTO:
    id: uuid.UUID
    from_status: ConversationStatus | None
    to_status: ConversationStatus
    actor_user_id: uuid.UUID | None
    actor_agent_id: uuid.UUID | None
    reason: str | None
    created_at: datetime


@dataclass(slots=True, frozen=True)
class ConversationTurnSnapshotDTO:
    id: uuid.UUID
    message_id: uuid.UUID | None
    model: str
    temperature: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int | None
    prompt_content: str | None
    completion_content: str | None
    metadata: dict | None
    created_at: datetime


@dataclass(slots=True, frozen=True)
class ConversationSummaryDTO:
    id: uuid.UUID
    ai_overview: str | None
    key_points: dict | None
    actions_taken: dict | None
    suggested_actions: dict | None
    last_generated_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class ConversationListItemDTO:
    id: uuid.UUID
    business_id: uuid.UUID
    visitor_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    case_id: uuid.UUID | None
    primary_agent_id: uuid.UUID | None
    source: ConversationSource
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime
    latest_message_type: ConversationMessageType | None
    csat_score: float | None


@dataclass(slots=True, frozen=True)
class ListConversationsInput:
    business_id: uuid.UUID
    statuses: Sequence[ConversationStatus] | None = None
    primary_agent_ids: Sequence[uuid.UUID] | None = None
    customer_ids: Sequence[uuid.UUID] | None = None
    limit: int = 50
    cursor: Cursor | None = None
    search: str | None = None


@dataclass(slots=True, frozen=True)
class ListConversationsResult:
    items: tuple[ConversationListItemDTO, ...]
    total: int
    next_cursor: Cursor | None
    has_next: bool


@dataclass(slots=True, frozen=True)
class GetConversationDetailInput:
    business_id: uuid.UUID
    conversation_id: uuid.UUID


@dataclass(slots=True, frozen=True)
class ConversationDetailDTO:
    id: uuid.UUID
    business_id: uuid.UUID
    visitor_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    case_id: uuid.UUID | None
    primary_agent_id: uuid.UUID | None
    status: ConversationStatus
    source: ConversationSource
    end_reason: ConversationEndReason | None
    first_response_at: datetime | None
    first_response_latency_seconds: int | None
    resolution_time_seconds: int | None
    closed_at: datetime | None
    csat_score: float | None
    csat_comment: str | None
    satisfaction_recorded_at: datetime | None
    runtime_profile_version: int | None
    created_at: datetime
    updated_at: datetime
    messages: tuple[ConversationMessageDTO, ...]
    participants: tuple[ConversationParticipantDTO, ...]
    summary: ConversationSummaryDTO | None
    status_log: tuple[ConversationStatusLogDTO, ...]
    turn_snapshots: tuple[ConversationTurnSnapshotDTO, ...]


@dataclass(slots=True, frozen=True)
class ConversationDetailResult:
    conversation: ConversationDetailDTO


@dataclass(slots=True, frozen=True)
class UpdateConversationStatusInput:
    business_id: uuid.UUID
    conversation_id: uuid.UUID
    status: ConversationStatus
    end_reason: ConversationEndReason | None = None
    actor_user_id: uuid.UUID | None = None
    actor_agent_id: uuid.UUID | None = None
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class UpdateConversationStatusResult:
    conversation_id: uuid.UUID
    previous_status: ConversationStatus
    new_status: ConversationStatus


@dataclass(slots=True, frozen=True)
class UpdateConversationAssignmentInput:
    business_id: uuid.UUID
    conversation_id: uuid.UUID
    primary_agent_id: uuid.UUID | None


@dataclass(slots=True, frozen=True)
class UpdateConversationAssignmentResult:
    conversation_id: uuid.UUID
    primary_agent_id: uuid.UUID | None


@dataclass(slots=True, frozen=True)
class RecordConversationCsatInput:
    business_id: uuid.UUID
    conversation_id: uuid.UUID
    score: float | None
    comment: str | None = None
    recorded_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class RecordConversationCsatResult:
    conversation_id: uuid.UUID
    score: float | None
    recorded_at: datetime | None


class ConversationsService:
    """Business operations for listing, inspecting and mutating conversations."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ConversationsRepository(session)

    # ------------------------------------------------------------------
    # Listing & retrieval

    def list_conversations(self, input_data: ListConversationsInput) -> ListConversationsResult:
        if input_data.limit <= 0 or input_data.limit > 200:
            raise ServiceValidationError("Limit must be between 1 and 200")

        filters = ConversationListFilters(
            business_id=input_data.business_id,
            statuses=input_data.statuses,
            primary_agent_ids=input_data.primary_agent_ids,
            customer_ids=input_data.customer_ids,
            search=input_data.search,
        )

        try:
            page = self.repository.list_conversations(
                filters,
                limit=input_data.limit,
                cursor=input_data.cursor,
                include_total=True,
            )
        except ValueError as exc:
            raise ServiceValidationError("Invalid pagination cursor") from exc

        items = tuple(
            ConversationListItemDTO(
                id=model.id,
                business_id=model.business_id,
                visitor_id=model.visitor_id,
                customer_id=model.customer_id,
                case_id=model.case_id,
                primary_agent_id=model.primary_agent_id,
                source=model.source,
                status=model.status,
                created_at=model.created_at,
                updated_at=model.updated_at,
                latest_message_type=page.latest_message_types.get(model.id),
                csat_score=model.csat_score,
            )
            for model in page.items
        )

        return ListConversationsResult(
            items=items,
            total=page.total,
            next_cursor=page.next_cursor,
            has_next=page.has_next,
        )

    def get_conversation_detail(self, input_data: GetConversationDetailInput) -> ConversationDetailResult:
        conversation = self.repository.get_conversation_with_details(
            business_id=input_data.business_id,
            conversation_id=input_data.conversation_id,
        )
        if conversation is None:
            raise ServiceNotFoundError("Conversation not found")

        dto = self._to_detail_dto(conversation)
        return ConversationDetailResult(conversation=dto)

    # ------------------------------------------------------------------
    # Mutations

    def update_status(self, input_data: UpdateConversationStatusInput) -> UpdateConversationStatusResult:
        conversation = self._get_conversation(input_data.conversation_id, input_data.business_id)
        previous_status = conversation.status
        if previous_status == input_data.status and conversation.end_reason == input_data.end_reason:
            return UpdateConversationStatusResult(
                conversation_id=conversation.id,
                previous_status=previous_status,
                new_status=conversation.status,
            )

        conversation.status = input_data.status
        conversation.end_reason = input_data.end_reason
        now = datetime.now(timezone.utc)
        if input_data.status in {ConversationStatus.RESOLVED, ConversationStatus.CLOSED_WITHOUT_RESOLUTION}:
            if conversation.closed_at is None:
                conversation.closed_at = now
            if conversation.resolution_time_seconds is None and conversation.created_at:
                conversation.resolution_time_seconds = max(int((now - conversation.created_at).total_seconds()), 0)

        self.session.add(
            ConversationStatusLog(
                conversation_id=conversation.id,
                from_status=previous_status,
                to_status=input_data.status,
                actor_user_id=input_data.actor_user_id,
                actor_agent_id=input_data.actor_agent_id,
                reason=input_data.reason,
            )
        )

        if (
            input_data.status == ConversationStatus.RESOLVED
            and conversation.customer_id
        ):
            self.session.add(
                CustomerActivityEvent(
                    business_id=conversation.business_id,
                    customer_id=conversation.customer_id,
                    conversation_id=conversation.id,
                    event_type=CustomerActivityEventType.CONVERSATION_CLOSED,
                    occurred_at=now,
                )
            )

        return UpdateConversationStatusResult(
            conversation_id=conversation.id,
            previous_status=previous_status,
            new_status=conversation.status,
        )

    def update_primary_agent(self, input_data: UpdateConversationAssignmentInput) -> UpdateConversationAssignmentResult:
        conversation = self._get_conversation(input_data.conversation_id, input_data.business_id)
        conversation.primary_agent_id = input_data.primary_agent_id
        return UpdateConversationAssignmentResult(
            conversation_id=conversation.id,
            primary_agent_id=conversation.primary_agent_id,
        )

    def record_csat(self, input_data: RecordConversationCsatInput) -> RecordConversationCsatResult:
        conversation = self._get_conversation(input_data.conversation_id, input_data.business_id)
        if input_data.score is not None and not (0 <= input_data.score <= 100):
            raise ServiceValidationError("CSAT score must be between 0 and 100")

        conversation.csat_score = input_data.score
        conversation.csat_comment = input_data.comment
        recorded_at = input_data.recorded_at or datetime.now(timezone.utc)
        conversation.satisfaction_recorded_at = recorded_at

        return RecordConversationCsatResult(
            conversation_id=conversation.id,
            score=conversation.csat_score,
            recorded_at=recorded_at,
        )

    # ------------------------------------------------------------------
    # Helpers

    def _to_detail_dto(self, conversation: Conversation) -> ConversationDetailDTO:
        messages = tuple(
            self._message_to_dto(message)
            for message in sorted(
                conversation.messages,
                key=lambda m: (m.sent_at, m.id),
            )
        )

        participants = tuple(
            ConversationParticipantDTO(
                id=participant.id,
                participant_type=participant.participant_type,
                participant_id=participant.participant_id,
                joined_at=participant.joined_at,
                left_at=participant.left_at,
            )
            for participant in sorted(
                conversation.participants,
                key=lambda p: (p.joined_at, p.id),
            )
        )

        summary = None
        if conversation.summary:
            summary = ConversationSummaryDTO(
                id=conversation.summary.id,
                ai_overview=conversation.summary.ai_overview,
                key_points=conversation.summary.key_points_json,
                actions_taken=conversation.summary.actions_taken_json,
                suggested_actions=conversation.summary.suggested_actions_json,
                last_generated_at=conversation.summary.last_generated_at,
                created_at=conversation.summary.created_at,
                updated_at=conversation.summary.updated_at,
            )

        status_log = tuple(
            ConversationStatusLogDTO(
                id=log.id,
                from_status=log.from_status,
                to_status=log.to_status,
                actor_user_id=log.actor_user_id,
                actor_agent_id=log.actor_agent_id,
                reason=log.reason,
                created_at=log.created_at,
            )
            for log in sorted(conversation.status_log, key=lambda l: (l.created_at, l.id))
        )

        turn_snapshots = tuple(
            ConversationTurnSnapshotDTO(
                id=snapshot.id,
                message_id=snapshot.message_id,
                model=snapshot.model,
                temperature=snapshot.temperature,
                prompt_tokens=snapshot.prompt_tokens,
                completion_tokens=snapshot.completion_tokens,
                latency_ms=snapshot.latency_ms,
                prompt_content=snapshot.prompt_content,
                completion_content=snapshot.completion_content,
                metadata=snapshot.metadata_json,
                created_at=snapshot.created_at,
            )
            for snapshot in sorted(conversation.turn_snapshots, key=lambda s: (s.created_at, s.id))
        )

        return ConversationDetailDTO(
            id=conversation.id,
            business_id=conversation.business_id,
            visitor_id=conversation.visitor_id,
            customer_id=conversation.customer_id,
            case_id=conversation.case_id,
            primary_agent_id=conversation.primary_agent_id,
            status=conversation.status,
            source=conversation.source,
            end_reason=conversation.end_reason,
            first_response_at=conversation.first_response_at,
            first_response_latency_seconds=conversation.first_response_latency_seconds,
            resolution_time_seconds=conversation.resolution_time_seconds,
            closed_at=conversation.closed_at,
            csat_score=conversation.csat_score,
            csat_comment=conversation.csat_comment,
            satisfaction_recorded_at=conversation.satisfaction_recorded_at,
            runtime_profile_version=conversation.runtime_profile_version,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            messages=messages,
            participants=participants,
            summary=summary,
            status_log=status_log,
            turn_snapshots=turn_snapshots,
        )

    def _message_to_dto(self, message: ConversationMessage) -> ConversationMessageDTO:
        attachments = tuple(
            MessageAttachmentDTO(
                id=attachment.id,
                storage_asset_id=attachment.storage_asset_id,
                filename=attachment.storage_asset.filename if attachment.storage_asset else "",
                content_type=attachment.storage_asset.content_type if attachment.storage_asset else None,
                size_bytes=attachment.storage_asset.size_bytes if attachment.storage_asset else 0,
                caption=attachment.caption,
            )
            for attachment in message.attachments
        )
        return ConversationMessageDTO(
            id=message.id,
            conversation_id=message.conversation_id,
            message_type=message.message_type,
            visibility=message.visibility,
            channel=message.channel,
            body=message.body,
            payload=message.payload_json,
            sent_at=message.sent_at,
            author_agent_id=message.author_agent_id,
            author_user_id=message.author_user_id,
            author_customer_id=message.author_customer_id,
            attachments=attachments,
        )

    def _get_conversation(self, conversation_id: uuid.UUID, business_id: uuid.UUID) -> Conversation:
        conversation = self.repository.get_conversation(
            business_id=business_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise ServiceNotFoundError("Conversation not found")
        return conversation


__all__ = [
    "ConversationDetailDTO",
    "ConversationDetailResult",
    "ConversationListItemDTO",
    "ConversationsService",
    "GetConversationDetailInput",
    "ListConversationsInput",
    "ListConversationsResult",
    "RecordConversationCsatInput",
    "RecordConversationCsatResult",
    "UpdateConversationAssignmentInput",
    "UpdateConversationAssignmentResult",
    "UpdateConversationStatusInput",
    "UpdateConversationStatusResult",
]
