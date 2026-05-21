from __future__ import annotations

import uuid

from django.db.models import Prefetch
from django.utils.text import slugify

from apps.accounts.models import BusinessProfile
from apps.conversations.models import Conversation, ConversationMessage
from apps.conversations.portal_service.types import PortalNotFoundError, PortalValidationError


class PortalLookupMixin:
    def _get_business_by_slug(self, slug_value: str) -> BusinessProfile:
        if not slug_value:
            raise PortalNotFoundError("Business handle is required")
        normalized = slugify(slug_value)
        business = (
            BusinessProfile.objects.filter(slug__iexact=normalized)
            .first()
        )
        if business is None:
            raise PortalNotFoundError("Business not found")
        return business

    def _get_active_conversation_by_token(self, session_token: str, *, include_messages: bool = False) -> Conversation:
        if not session_token:
            raise PortalNotFoundError("Session token is required")
        queryset = self._conversation_queryset(include_messages=include_messages)
        conversation = queryset.filter(session_token=session_token).first()
        if conversation is None:
            raise PortalNotFoundError("Conversation not found")
        return conversation

    def get_conversation_by_id(self, conversation_id: uuid.UUID | str, *, include_messages: bool = False) -> Conversation:
        if not conversation_id:
            raise PortalNotFoundError("Conversation id is required")
        queryset = self._conversation_queryset(include_messages=include_messages)
        conversation = queryset.filter(id=conversation_id).first()
        if conversation is None:
            raise PortalNotFoundError("Conversation not found")
        return conversation

    def _conversation_queryset(self, *, include_messages: bool):
        queryset = (
            Conversation.objects.select_related("business_profile", "agent_profile", "owner_user")
            .prefetch_related("agent_profile__action_permissions")
        )
        if include_messages:
            queryset = queryset.prefetch_related(
                Prefetch("messages", queryset=ConversationMessage.objects.order_by("sent_at", "created_at"))
            )
        return queryset

    def _resolve_conversation(
        self,
        *,
        session_token: str,
        conversation: Conversation | None,
        include_messages: bool,
    ) -> Conversation:
        if conversation is not None:
            if conversation.session_token != session_token:
                raise PortalValidationError("Conversation session mismatch")
            return conversation
        return self._get_active_conversation_by_token(session_token, include_messages=include_messages)
