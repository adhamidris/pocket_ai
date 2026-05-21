from __future__ import annotations

from typing import Iterable

from django.utils.text import slugify

from apps.accounts.constants import DEFAULT_ASSISTANT_ROLE
from apps.accounts.models import AgentProfile, BusinessProfile
from apps.conversations.content_blocks import ensure_assistant_text_blocks
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender
from apps.conversations.portal_service.types import (
    PortalAgentSummary,
    PortalBusinessSummary,
    PortalMessage,
    PortalSessionState,
)


class PortalSerializationMixin:
    def _serialize_agent(self, agent: AgentProfile) -> PortalAgentSummary:
        return PortalAgentSummary(
            id=agent.id,
            name=agent.name,
            role=DEFAULT_ASSISTANT_ROLE,
            slug=agent.slug,
            shareable_path=agent.shareable_path,
        )

    def _serialize_business(self, business: BusinessProfile) -> PortalBusinessSummary:
        return PortalBusinessSummary(
            id=business.id,
            name=business.name,
            slug=business.slug or slugify(business.name),
        )

    def _serialize_session(self, conversation: Conversation) -> PortalSessionState:
        session_type, custom_assistant_id, custom_assistant_name, _custom_assistant_agent_name = self._classify_conversation_session(conversation)
        return PortalSessionState(
            conversation_id=conversation.id,
            session_token=conversation.session_token,
            status=conversation.status,
            started_at=conversation.started_at,
            expires_at=conversation.expires_at,
            session_type=session_type,
            custom_assistant_id=custom_assistant_id,
            custom_assistant_name=custom_assistant_name,
        )

    def _serialize_message(self, message: ConversationMessage) -> PortalMessage:
        content_blocks = message.content_blocks if isinstance(getattr(message, "content_blocks", None), list) else []
        if message.sender == ConversationSender.AI:
            content_blocks = ensure_assistant_text_blocks(message.body or "", existing_blocks=content_blocks)
        return PortalMessage(
            id=message.id,
            sender=message.sender,
            body=message.body,
            sent_at=message.sent_at,
            metadata=message.metadata or {},
            content_blocks=content_blocks,
        )

    def _serialize_messages(self, messages: Iterable[ConversationMessage]) -> Iterable[PortalMessage]:
        for message in messages:
            yield self._serialize_message(message)
