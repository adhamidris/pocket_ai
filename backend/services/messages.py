"""Service helpers for working with conversation messages."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversations import (
    Conversation,
    ConversationMessage,
    ConversationMessageChannel,
    ConversationMessageType,
    ConversationMessageVisibility,
    ConversationStatus,
    ConversationStatusLog,
    MessageAttachment,
)
from app.models.customers import Customer, CustomerActivityEvent, CustomerActivityEventType
from app.models.storage import StorageAsset
from app.repositories.conversations import ConversationsRepository
from app.repositories.messages import MessagesRepository
from app.services.errors import ServiceNotFoundError, ServiceValidationError


MessageCursor = str


@dataclass(slots=True, frozen=True)
class MessageAttachmentDTO:
    id: uuid.UUID
    storage_asset_id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    caption: str | None


@dataclass(slots=True, frozen=True)
class ConversationMessageDTO:
    id: uuid.UUID
    conversation_id: uuid.UUID
    message_type: ConversationMessageType
    visibility: ConversationMessageVisibility
    channel: ConversationMessageChannel
    body: str | None
    payload: dict | None
    sent_at: datetime
    author_agent_id: uuid.UUID | None
    author_user_id: uuid.UUID | None
    author_customer_id: uuid.UUID | None
    attachments: tuple[MessageAttachmentDTO, ...]


@dataclass(slots=True, frozen=True)
class MessageAttachmentInput:
    storage_asset_id: uuid.UUID
    caption: str | None = None


@dataclass(slots=True, frozen=True)
class AddMessageInput:
    business_id: uuid.UUID
    conversation_id: uuid.UUID
    message_type: ConversationMessageType
    visibility: ConversationMessageVisibility
    channel: ConversationMessageChannel
    body: str | None = None
    payload: dict | None = None
    author_agent_id: uuid.UUID | None = None
    author_user_id: uuid.UUID | None = None
    author_customer_id: uuid.UUID | None = None
    sent_at: datetime | None = None
    attachments: Sequence[MessageAttachmentInput] = ()


@dataclass(slots=True, frozen=True)
class AddMessageResult:
    message: ConversationMessageDTO
    conversation_status: ConversationStatus


@dataclass(slots=True, frozen=True)
class ListMessagesInput:
    business_id: uuid.UUID
    conversation_id: uuid.UUID
    limit: int = 50
    cursor: MessageCursor | None = None


@dataclass(slots=True, frozen=True)
class ListMessagesResult:
    items: tuple[ConversationMessageDTO, ...]
    next_cursor: MessageCursor | None
    has_more: bool


class MessagesService:
    """Encapsulates message persistence and enrichment logic."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.conversations_repo = ConversationsRepository(session)
        self.messages_repo = MessagesRepository(session)

    # ------------------------------------------------------------------
    # Public API

    def add_message(self, input_data: AddMessageInput) -> AddMessageResult:
        conversation = self._get_conversation(input_data.conversation_id, input_data.business_id)

        sent_at = input_data.sent_at or datetime.now(timezone.utc)
        message = ConversationMessage(
            conversation_id=conversation.id,
            author_agent_id=input_data.author_agent_id,
            author_user_id=input_data.author_user_id,
            author_customer_id=input_data.author_customer_id,
            message_type=input_data.message_type,
            visibility=input_data.visibility,
            channel=input_data.channel,
            body=input_data.body,
            payload_json=input_data.payload,
            sent_at=sent_at,
        )
        self.session.add(message)
        self.session.flush()

        attachments = []
        for attachment in input_data.attachments:
            asset = self._get_storage_asset(attachment.storage_asset_id)
            msg_attachment = MessageAttachment(
                message_id=message.id,
                storage_asset_id=asset.id,
                caption=attachment.caption,
            )
            self.session.add(msg_attachment)
            attachments.append(msg_attachment)
        self.session.flush()

        self._after_message_persisted(
            conversation=conversation,
            message=message,
            sent_at=sent_at,
            author_agent_id=input_data.author_agent_id,
        )

        dto = self._to_message_dto(message)
        return AddMessageResult(message=dto, conversation_status=conversation.status)

    def list_messages(self, input_data: ListMessagesInput) -> ListMessagesResult:
        if input_data.limit <= 0 or input_data.limit > 200:
            raise ServiceValidationError("Limit must be between 1 and 200")

        conversation = self._get_conversation(input_data.conversation_id, input_data.business_id)

        try:
            page = self.messages_repo.list_messages(
                conversation_id=conversation.id,
                limit=input_data.limit,
                cursor=input_data.cursor,
            )
        except ValueError as exc:
            raise ServiceValidationError("Invalid pagination cursor") from exc

        items = tuple(self._to_message_dto(row) for row in page.items)

        return ListMessagesResult(items=items, next_cursor=page.next_cursor, has_more=page.has_more)

    # ------------------------------------------------------------------
    # Internal helpers

    def _get_conversation(self, conversation_id: uuid.UUID, business_id: uuid.UUID) -> Conversation:
        conversation = self.conversations_repo.get_conversation(
            business_id=business_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise ServiceNotFoundError("Conversation not found")
        return conversation

    def _get_storage_asset(self, asset_id: uuid.UUID) -> StorageAsset:
        stmt = select(StorageAsset).where(StorageAsset.id == asset_id)
        asset = self.session.execute(stmt).scalar_one_or_none()
        if asset is None:
            raise ServiceNotFoundError("Attachment asset not found", details={"asset_id": str(asset_id)})
        return asset

    def _after_message_persisted(
        self,
        *,
        conversation: Conversation,
        message: ConversationMessage,
        sent_at: datetime,
        author_agent_id: uuid.UUID | None,
    ) -> None:
        # Conversation lifecycle updates
        previous_status = conversation.status
        if previous_status == ConversationStatus.NEW and message.message_type in {
            ConversationMessageType.CUSTOMER,
            ConversationMessageType.AGENT,
        }:
            conversation.status = ConversationStatus.LIVE
            self.session.add(
                ConversationStatusLog(
                    conversation_id=conversation.id,
                    from_status=previous_status,
                    to_status=ConversationStatus.LIVE,
                    actor_agent_id=author_agent_id,
                )
            )

        if (
            message.message_type == ConversationMessageType.AGENT
            and conversation.first_response_at is None
        ):
            first_customer_at = self.messages_repo.get_first_customer_message_sent_at(conversation.id)
            if first_customer_at is not None:
                conversation.first_response_at = sent_at
                latency_seconds = int((sent_at - first_customer_at).total_seconds())
                conversation.first_response_latency_seconds = max(latency_seconds, 0)

        if message.message_type == ConversationMessageType.CUSTOMER and conversation.customer_id:
            customer = self.session.get(Customer, conversation.customer_id)
            if customer:
                customer.last_contact_at = sent_at
                self.session.add(customer)
            self._append_customer_activity(
                business_id=conversation.business_id,
                customer_id=conversation.customer_id,
                conversation_id=conversation.id,
                occurred_at=sent_at,
                event_type=self._resolve_customer_activity_type(conversation.id),
            )

    def _resolve_customer_activity_type(self, conversation_id: uuid.UUID) -> CustomerActivityEventType:
        existing = self.messages_repo.count_customer_messages(conversation_id)
        if existing == 1:
            return CustomerActivityEventType.CONVERSATION_STARTED
        return CustomerActivityEventType.CUSTOM

    def _append_customer_activity(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        occurred_at: datetime,
        event_type: CustomerActivityEventType,
    ) -> None:
        self.session.add(
            CustomerActivityEvent(
                business_id=business_id,
                customer_id=customer_id,
                conversation_id=conversation_id,
                event_type=event_type,
                occurred_at=occurred_at,
            )
        )

    def _to_message_dto(self, message: ConversationMessage) -> ConversationMessageDTO:
        attachments = self._load_attachments(message.id)
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

    def _load_attachments(self, message_id: uuid.UUID) -> tuple[MessageAttachmentDTO, ...]:
        attachments_map = self.messages_repo.load_attachments_for_messages((message_id,))
        rows = attachments_map.get(message_id, ())
        if not rows:
            return ()
        return tuple(
            MessageAttachmentDTO(
                id=attachment.id,
                storage_asset_id=attachment.storage_asset_id,
                filename=asset.filename,
                content_type=asset.content_type,
                size_bytes=asset.size_bytes,
                caption=attachment.caption,
            )
            for attachment, asset in rows
        )


__all__ = [
    "AddMessageInput",
    "AddMessageResult",
    "ConversationMessageDTO",
    "ListMessagesInput",
    "ListMessagesResult",
    "MessageAttachmentDTO",
    "MessageAttachmentInput",
    "MessagesService",
]
