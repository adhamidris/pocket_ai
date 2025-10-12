"""Pydantic schemas for public chat portal endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.conversations import (
    ChatChannel,
    ChatPresenceStatus,
    ChatVisitorType,
    ConversationMessageChannel,
    ConversationMessageType,
    ConversationMessageVisibility,
    ConversationStatus,
)


VisitorId = UUID
ConversationId = UUID
AgentId = UUID
CustomerId = UUID
MessageId = UUID
StorageAssetId = UUID


class BaseSchemaModel(BaseModel):
    """Base model forbidding unexpected payload properties."""

    model_config = ConfigDict(extra="forbid")


class ChatAgentPreview(BaseSchemaModel):
    """Lightweight agent data exposed to the public portal."""

    id: AgentId
    name: Annotated[str, Field(min_length=1, max_length=160)]
    role: Annotated[str, Field(min_length=1, max_length=120)]
    avatar_url: HttpUrl | None = None
    business_name: Annotated[str | None, Field(default=None, max_length=160)]


class ChatSessionCreateRequest(BaseSchemaModel):
    """Initial handshake payload provided by the portal widget."""

    agent_handle: Annotated[str, Field(min_length=1, max_length=160)]
    channel: ChatChannel = ChatChannel.WEB_WIDGET
    locale: Annotated[str | None, Field(default=None, min_length=2, max_length=16)]
    landing_page: Annotated[str | None, Field(default=None, max_length=255)]
    fingerprint_hash: Annotated[str | None, Field(default=None, max_length=120)]
    utm: dict | None = None
    existing_session_token: Annotated[str | None, Field(default=None, min_length=8, max_length=120)]


class ChatSessionState(BaseSchemaModel):
    """Session metadata returned to the portal client."""

    session_token: Annotated[str, Field(min_length=8, max_length=120)]
    visitor_id: VisitorId
    visitor_type: ChatVisitorType
    conversation_id: ConversationId | None
    conversation_status: ConversationStatus
    started_at: datetime | None = None
    expires_at: datetime | None = None


class ChatSessionCreateResponse(BaseSchemaModel):
    """Handshake response bundling session context and any seeded messages."""

    session: ChatSessionState
    agent: ChatAgentPreview
    messages: Sequence["ChatMessage"]


class ChatMessageAttachmentDescriptor(BaseSchemaModel):
    """Attachment metadata exposed to the portal."""

    id: UUID
    storage_asset_id: StorageAssetId
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    content_type: Annotated[str | None, Field(default=None, max_length=120)]
    size_bytes: Annotated[int, Field(ge=1)]
    download_url: HttpUrl | None = None
    caption: Annotated[str | None, Field(default=None, max_length=255)]
    metadata: dict | None = None


class ChatMessageAuthor(BaseSchemaModel):
    """Identifies who sent a message."""

    agent_id: AgentId | None = None
    customer_id: CustomerId | None = None
    user_display_name: Annotated[str | None, Field(default=None, max_length=160)]


class ChatMessage(BaseSchemaModel):
    """Full message payload returned to the portal."""

    id: MessageId
    conversation_id: ConversationId
    message_type: ConversationMessageType
    visibility: ConversationMessageVisibility
    channel: ConversationMessageChannel
    body: Annotated[str | None, Field(default=None, min_length=1)]
    payload: dict | None = None
    sent_at: datetime
    author: ChatMessageAuthor
    attachments: Sequence[ChatMessageAttachmentDescriptor] = Field(default_factory=tuple)


class ChatMessageAttachmentUpload(BaseSchemaModel):
    """Attachment reference supplied when sending a message."""

    storage_asset_id: StorageAssetId
    caption: Annotated[str | None, Field(default=None, max_length=255)]


class ChatMessageSendRequest(BaseSchemaModel):
    """Visitor-authored message submission."""

    session_token: Annotated[str, Field(min_length=8, max_length=120)]
    body: Annotated[str | None, Field(default=None, min_length=1)]
    payload: dict | None = None
    channel: ConversationMessageChannel = ConversationMessageChannel.TEXT
    attachments: Sequence[ChatMessageAttachmentUpload] = Field(default_factory=tuple)


class ChatMessageSendResponse(BaseSchemaModel):
    """Response after persisting a visitor message (echo + new messages)."""

    message: ChatMessage
    follow_up_messages: Sequence[ChatMessage] = Field(default_factory=tuple)


class ChatMessagesListResponse(BaseSchemaModel):
    """Paginated transcript result for lazy loading."""

    messages: Sequence[ChatMessage]
    next_cursor: Annotated[str | None, Field(default=None, max_length=120)]
    has_more: bool


class ChatPresencePingRequest(BaseSchemaModel):
    """Signal that a visitor is still active."""

    session_token: Annotated[str, Field(min_length=8, max_length=120)]
    status: ChatPresenceStatus
    pinged_at: datetime


class ChatCsatSubmissionRequest(BaseSchemaModel):
    """Portal-facing CSAT capture."""

    session_token: Annotated[str, Field(min_length=8, max_length=120)]
    score: Annotated[float, Field(ge=0, le=100)]
    comment: Annotated[str | None, Field(default=None, max_length=2000)]


class ChatSessionHeartbeatResponse(BaseSchemaModel):
    """Lightweight heartbeat response used to refresh token expiry client-side."""

    session: ChatSessionState
    conversation_status: ConversationStatus
    new_messages: Sequence[ChatMessage] = Field(default_factory=tuple)


__all__ = [
    "AgentId",
    "ChatAgentPreview",
    "ChatChannel",
    "ChatCsatSubmissionRequest",
    "ChatMessage",
    "ChatMessageAttachmentDescriptor",
    "ChatMessageAttachmentUpload",
    "ChatMessageAuthor",
    "ChatMessageSendRequest",
    "ChatMessageSendResponse",
    "ChatMessagesListResponse",
    "ChatPresencePingRequest",
    "ChatSessionCreateRequest",
    "ChatSessionCreateResponse",
    "ChatSessionHeartbeatResponse",
    "ChatSessionState",
    "ChatVisitorType",
    "ConversationId",
    "ConversationMessageChannel",
    "ConversationMessageType",
    "ConversationMessageVisibility",
    "ConversationStatus",
    "CustomerId",
    "MessageId",
    "StorageAssetId",
    "VisitorId",
]
