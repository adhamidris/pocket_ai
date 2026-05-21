from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Iterable, Sequence

from django.db import transaction
from django.db.models import Count, Prefetch, Q

from pocketai.language import metadata_ui_language

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
)
from apps.assistants.models import CustomAssistant
from apps.conversations.content_blocks import ensure_assistant_text_blocks
from apps.conversations.models import (
    Conversation,
    ConversationChannel,
    ConversationExtraction,
    ConversationExtractionType,
    ConversationMessage,
    ConversationSender,
    ConversationStatus,
)
from apps.conversations.portal_service.feedback import PortalFeedbackMixin
from apps.conversations.portal_service.lookup import PortalLookupMixin
from apps.conversations.portal_service.serialization import PortalSerializationMixin
from apps.conversations.portal_service.lifecycle import PortalConversationLifecycleMixin
from apps.conversations.portal_service.session_summaries import PortalSessionSummaryMixin
from apps.conversations.portal_service.types import (
    DEFAULT_SESSION_TTL,
    PortalAgentSummary,
    PortalAuthorizationError,
    PortalBusinessSummary,
    PortalMessage,
    PortalNotFoundError,
    PortalSessionBootstrap,
    PortalSessionState,
    PortalSessionSummary,
    PortalValidationError,
)


INTERNAL_AGENT_NOTIFICATION_PURPOSE = "agent_notification_surface"
INTERNAL_CANONICAL_CONVERSATION_TYPES = {
    "canonical_main_primary",
}


def _is_internal_agent_notification_surface(conversation: Conversation) -> bool:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
    purpose = str(metadata.get("purpose") or "").strip().lower()
    meta_type = str(metadata.get("type") or "").strip().lower()
    source = str(metadata.get("source") or "").strip().lower()
    return purpose == INTERNAL_AGENT_NOTIFICATION_PURPOSE or meta_type in INTERNAL_CANONICAL_CONVERSATION_TYPES or source == "agent_run"


class ChatPortalService(
    PortalSerializationMixin,
    PortalSessionSummaryMixin,
    PortalConversationLifecycleMixin,
    PortalLookupMixin,
    PortalFeedbackMixin,
):
    """High-level orchestration for chat session lifecycle."""

    _is_internal_agent_notification_surface = staticmethod(_is_internal_agent_notification_surface)

    def __init__(self, *, session_ttl: timedelta | None = None) -> None:
        self.session_ttl = session_ttl if session_ttl is not None else DEFAULT_SESSION_TTL

    # ------------------------------------------------------------------
    # Public API

    def resolve_handle(self, business_slug: str, agent_slug: str) -> tuple[BusinessProfile, AgentProfile]:
        business = self._get_business_by_slug(business_slug)
        agent = (
            AgentProfile.objects.filter(business_profile=business, slug__iexact=agent_slug)
            .select_related("business_profile")
            .first()
        )
        if agent is None:
            raise PortalNotFoundError("Agent handle not found")

        return business, agent

    def bootstrap_session(
        self,
        *,
        business_slug: str,
        agent_slug: str,
        existing_session_token: str | None = None,
        metadata: dict | None = None,
    ) -> PortalSessionBootstrap:
        business, agent = self.resolve_handle(business_slug, agent_slug)
        conversation = self._get_or_create_conversation(
            business=business,
            agent=agent,
            existing_session_token=existing_session_token,
            metadata=metadata or {},
        )
        messages_qs = conversation.messages.all().order_by("sent_at", "created_at")
        # Filter out legacy welcome messages so the UI starts empty
        messages_list = [
            msg for msg in messages_qs
            if not (isinstance(msg.metadata, dict) and msg.metadata.get("type") == "welcome")
        ]
        messages = tuple(self._serialize_messages(messages_list))
        return PortalSessionBootstrap(
            business=self._serialize_business(business),
            agent=self._serialize_agent(agent),
            session=self._serialize_session(conversation),
            messages=messages,
        )

    def append_message(
        self,
        *,
        session_token: str,
        sender: ConversationSender,
        body: str,
        metadata: dict | None = None,
        content_blocks: list[dict[str, object]] | None = None,
        conversation: Conversation | None = None,
        message_id: uuid.UUID | None = None,
    ) -> PortalMessage:
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=conversation,
            include_messages=False,
        )
        if not body.strip():
            raise PortalValidationError("Message body cannot be empty")
        metadata = dict(metadata or {})
        selected_ui_language = metadata_ui_language(metadata)
        with transaction.atomic():
            create_kwargs = {
                "conversation": conversation,
                "sender": sender,
                "body": body.strip(),
                "metadata": metadata,
            }
            if content_blocks is not None:
                create_kwargs["content_blocks"] = [dict(item) for item in content_blocks if isinstance(item, dict)]
            elif sender == ConversationSender.AI:
                create_kwargs["content_blocks"] = ensure_assistant_text_blocks(body.strip())
            if message_id:
                create_kwargs["id"] = message_id
            message = ConversationMessage.objects.create(**create_kwargs)
            metadata_updated = self._capture_ui_language_preference(
                conversation=conversation,
                language=selected_ui_language,
            )
            self._touch_conversation_after_message(conversation, message, metadata_updated=metadata_updated)
        return self._serialize_message(message)

    def update_message(
        self,
        *,
        session_token: str,
        message_id: uuid.UUID,
        body: str | None = None,
        metadata: dict | None = None,
        content_blocks: list[dict[str, object]] | None = None,
        conversation: Conversation | None = None,
    ) -> PortalMessage:
        if not message_id:
            raise PortalValidationError("message_id is required")
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=conversation,
            include_messages=False,
        )
        message = conversation.messages.filter(id=message_id).first()
        if message is None:
            raise PortalNotFoundError("Message not found")

        updated_fields: list[str] = []
        if body is not None:
            clean_body = body.strip()
            if not clean_body:
                raise PortalValidationError("Message body cannot be empty")
            message.body = clean_body
            updated_fields.append("body")
            if content_blocks is None and message.sender == ConversationSender.AI:
                next_blocks = ensure_assistant_text_blocks(clean_body, existing_blocks=getattr(message, "content_blocks", None))
                if next_blocks != (message.content_blocks or []):
                    message.content_blocks = next_blocks
                    updated_fields.append("content_blocks")
        if metadata is not None:
            message.metadata = metadata
            updated_fields.append("metadata")
        if content_blocks is not None:
            message.content_blocks = [dict(item) for item in content_blocks if isinstance(item, dict)]
            updated_fields.append("content_blocks")
        if updated_fields:
            message.save(update_fields=updated_fields)
        return self._serialize_message(message)

    def list_messages(self, *, session_token: str, limit: int | None = None) -> Sequence[PortalMessage]:
        conversation = self._get_active_conversation_by_token(session_token)
        return self.list_messages_for_conversation(conversation=conversation, limit=limit)

    def list_messages_for_conversation(
        self,
        *,
        conversation: Conversation,
        limit: int | None = None,
    ) -> Sequence[PortalMessage]:
        qs = conversation.messages.all()
        if limit:
            qs = qs.order_by("sent_at", "created_at")[:limit]
        
        # Filter out legacy welcome messages
        messages_list = [
            msg for msg in qs
            if not (isinstance(msg.metadata, dict) and msg.metadata.get("type") == "welcome")
        ]
        return tuple(self._serialize_messages(messages_list))

    def get_session_state(self, *, session_token: str, conversation: Conversation | None = None) -> PortalSessionState:
        conversation_obj = self._resolve_conversation(
            session_token=session_token,
            conversation=conversation,
            include_messages=False,
        )
        return self.get_session_state_for_conversation(conversation_obj)

    def get_session_state_for_conversation(self, conversation: Conversation) -> PortalSessionState:
        return self._serialize_session(conversation)


    def get_conversation(self, *, session_token: str, include_messages: bool = True) -> Conversation:
        """Expose the active conversation for downstream orchestration logic."""

        return self._get_active_conversation_by_token(session_token, include_messages=include_messages)

    def store_extractions(
        self,
        *,
        session_token: str,
        items: Iterable[tuple[ConversationExtractionType, dict]],
        conversation: Conversation | None = None,
    ) -> Sequence[ConversationExtraction]:
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=conversation,
            include_messages=False,
        )
        created: list[ConversationExtraction] = []
        with transaction.atomic():
            for extraction_type, payload in items:
                created.append(
                    ConversationExtraction.objects.create(
                        conversation=conversation,
                        extraction_type=extraction_type,
                        payload=payload or {},
                    )
                )
        return tuple(created)

    def list_sessions(
        self,
        *,
        business_slug: str,
        agent_slug: str,
        session_tokens: Sequence[str],
        limit: int = 50,
    ) -> Sequence[PortalSessionSummary]:
        """
        List session summaries for given session tokens.
        
        Used by the frontend to display session history in the sidebar.
        Only returns sessions that belong to the specified business/agent.
        """
        if not session_tokens:
            return ()
        
        # Validate business/agent exist
        business, agent = self.resolve_handle(business_slug, agent_slug)
        
        # Fetch conversations matching the tokens
        conversations = (
            Conversation.objects
            .filter(
                session_token__in=session_tokens,
                business_profile=business,
                agent_profile=agent,
            )
            .select_related("custom_assistant")
            .annotate(message_count=Count("messages"))
            .prefetch_related(
                Prefetch(
                    "messages",
                    queryset=ConversationMessage.objects.filter(
                        sender=ConversationSender.CUSTOMER
                    ).order_by("sent_at", "created_at")[:1],
                    to_attr="first_customer_messages",
                )
            )
            .order_by("-started_at")[:limit]
        )
        
        return self._build_session_summaries(conversations)

    def list_owned_sessions(
        self,
        *,
        owner_user: object,
        business_slug: str,
        agent_slug: str,
        limit: int = 50,
    ) -> Sequence[PortalSessionSummary]:
        business, agent = self.resolve_handle(business_slug, agent_slug)
        queryset = Conversation.objects.filter(business_profile=business).filter(
            Q(agent_profile=agent)
            | Q(custom_assistant__business_profile=business)
        )
        if not getattr(owner_user, "is_staff", False):
            business_profiles = getattr(owner_user, "business_profiles", None)
            has_business_access = bool(
                getattr(owner_user, "id", None) == getattr(business, "user_id", None)
                or (business_profiles is not None and business_profiles.filter(id=business.id).exists())
            )
            if not has_business_access:
                queryset = queryset.filter(
                    Q(owner_user=owner_user)
                    | Q(custom_assistant__created_by=owner_user)
                )
        candidate_limit = max(limit, min(max(limit * 3, limit + 25), 300))
        conversations = (
            queryset
            .exclude(metadata__has_key="anchor_conversation_id")
            .exclude(metadata__has_key="anchorConversationId")
            .select_related("custom_assistant", "custom_assistant__agent_profile")
            .annotate(message_count=Count("messages", distinct=True))
            .prefetch_related(
                Prefetch(
                    "messages",
                    queryset=ConversationMessage.objects.filter(
                        sender=ConversationSender.CUSTOMER
                    ).order_by("sent_at", "created_at")[:1],
                    to_attr="first_customer_messages",
                ),
            )
            .distinct()
            .order_by("-last_activity_at", "-started_at")[:candidate_limit]
        )
        return self._build_session_summaries(conversations, limit=limit)

    def create_new_session(
        self,
        *,
        business_slug: str,
        agent_slug: str,
        metadata: dict | None = None,
    ) -> PortalSessionBootstrap:
        """
        Create a completely new session (no existing token).
        
        This is used when the user clicks "New Chat" to start a fresh conversation.
        """
        business, agent = self.resolve_handle(business_slug, agent_slug)
        conversation = self._get_or_create_conversation(
            business=business,
            agent=agent,
            existing_session_token=None,  # Force new session
            metadata=metadata or {},
        )
        return PortalSessionBootstrap(
            business=self._serialize_business(business),
            agent=self._serialize_agent(agent),
            session=self._serialize_session(conversation),
            messages=(),  # New session has no messages
        )

    def create_owned_session(
        self,
        *,
        owner_user: object,
        business_slug: str,
        agent_slug: str,
        metadata: dict | None = None,
    ) -> PortalSessionBootstrap:
        payload = dict(metadata or {})
        if getattr(owner_user, "id", None) and "actor_user_id" not in payload and "actorUserId" not in payload:
            payload["actor_user_id"] = str(owner_user.id)
        return self.create_new_session(
            business_slug=business_slug,
            agent_slug=agent_slug,
            metadata=payload,
        )

    def create_owned_custom_assistant_session(
        self,
        *,
        owner_user: object,
        business_slug: str,
        agent_slug: str,
        custom_assistant_id: uuid.UUID | str,
        metadata: dict | None = None,
        title: str = "",
    ) -> PortalSessionBootstrap:
        business, _agent = self.resolve_handle(business_slug, agent_slug)
        try:
            custom_assistant_uuid = custom_assistant_id if isinstance(custom_assistant_id, uuid.UUID) else uuid.UUID(str(custom_assistant_id))
        except (TypeError, ValueError):
            raise PortalValidationError("custom_assistant_id must be a valid UUID.")

        assistant = (
            CustomAssistant.objects.filter(id=custom_assistant_uuid, business_profile=business)
            .select_related("agent_profile", "business_profile")
            .first()
        )
        if assistant is None:
            raise PortalNotFoundError("Custom Assistant not found.")

        payload = dict(metadata or {})
        if getattr(owner_user, "id", None) and "actor_user_id" not in payload and "actorUserId" not in payload:
            payload["actor_user_id"] = str(owner_user.id)
        payload.update(
            {
                "type": "custom_assistant_session",
                "custom_assistant_id": str(assistant.id),
                "custom_assistant_name": assistant.name,
                "custom_assistant_agent_name": assistant.agent_profile.name,
            }
        )
        owner = owner_user if getattr(owner_user, "id", None) else assistant.created_by or business.user
        conversation = Conversation.objects.create(
            business_profile=business,
            agent_profile=assistant.agent_profile,
            custom_assistant=assistant,
            owner_user=owner,
            channel=ConversationChannel.API,
            status=ConversationStatus.LIVE,
            metadata=payload,
            summary=(title or "New session").strip()[:255],
        )
        return PortalSessionBootstrap(
            business=self._serialize_business(business),
            agent=self._serialize_agent(assistant.agent_profile),
            session=self._serialize_session(conversation),
            messages=(),
        )
