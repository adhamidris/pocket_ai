from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import AgentProfile, BusinessProfile
from apps.conversations.models import (
    Conversation,
    ConversationChannel,
    ConversationExtraction,
    ConversationExtractionType,
    ConversationMessage,
    ConversationSender,
    ConversationStatus,
)


class PortalNotFoundError(Exception):
    """Raised when portal business/agent/session cannot be found."""


class PortalValidationError(ValueError):
    """Raised when incoming payload fails validation."""


@dataclasses.dataclass(frozen=True, slots=True)
class PortalAgentSummary:
    id: uuid.UUID
    name: str
    role: str
    slug: str
    shareable_path: str


@dataclasses.dataclass(frozen=True, slots=True)
class PortalBusinessSummary:
    id: uuid.UUID
    name: str
    slug: str


@dataclasses.dataclass(frozen=True, slots=True)
class PortalMessage:
    id: uuid.UUID
    sender: str
    body: str
    sent_at: datetime
    metadata: dict


@dataclasses.dataclass(frozen=True, slots=True)
class PortalSessionState:
    conversation_id: uuid.UUID
    session_token: str
    status: str
    started_at: datetime
    expires_at: datetime | None


@dataclasses.dataclass(frozen=True, slots=True)
class PortalSessionBootstrap:
    business: PortalBusinessSummary
    agent: PortalAgentSummary
    session: PortalSessionState
    messages: Sequence[PortalMessage]


DEFAULT_SESSION_TTL = timedelta(hours=4)


class ChatPortalService:
    """High-level orchestration for public chat portal lifecycle."""

    def __init__(self, *, session_ttl: timedelta | None = None) -> None:
        self.session_ttl = session_ttl or DEFAULT_SESSION_TTL

    # ------------------------------------------------------------------
    # Public API

    def resolve_handle(self, business_slug: str, agent_slug: str) -> tuple[BusinessProfile, AgentProfile]:
        business = self._get_business_by_slug(business_slug)
        try:
            agent = business.agent_profile
        except AgentProfile.DoesNotExist as exc:  # pragma: no cover - defensive (OneToOne)
            raise PortalNotFoundError("Agent not configured for this business") from exc

        if not agent.slug:
            raise PortalNotFoundError("Agent does not have a shareable slug")

        if agent.slug.lower() != agent_slug.lower():
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
        messages = tuple(self._serialize_messages(conversation.messages.all().order_by("sent_at", "created_at")))
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
    ) -> PortalMessage:
        conversation = self._get_active_conversation_by_token(session_token)
        if not body.strip():
            raise PortalValidationError("Message body cannot be empty")
        with transaction.atomic():
            message = ConversationMessage.objects.create(
                conversation=conversation,
                sender=sender,
                body=body.strip(),
                metadata=metadata or {},
            )
            self._touch_conversation_after_message(conversation, message)
        return self._serialize_message(message)

    def list_messages(self, *, session_token: str, limit: int | None = None) -> Sequence[PortalMessage]:
        conversation = self._get_active_conversation_by_token(session_token)
        qs = conversation.messages.all()
        if limit:
            qs = qs.order_by("sent_at", "created_at")[:limit]
        return tuple(self._serialize_messages(qs))

    def get_session_state(self, *, session_token: str) -> PortalSessionState:
        conversation = self._get_active_conversation_by_token(session_token)
        return self._serialize_session(conversation)

    def record_csat(self, *, session_token: str, score: int, comment: str | None = None) -> PortalSessionState:
        if score < 1 or score > 5:
            raise PortalValidationError("Score must be between 1 and 5")
        conversation = self._get_active_conversation_by_token(session_token)
        conversation.csat_score = score
        conversation.csat_comment = (comment or "").strip()
        conversation.csat_recorded_at = timezone.now()
        if conversation.status == ConversationStatus.LIVE:
            conversation.status = ConversationStatus.RESOLVED
        conversation.save(update_fields=["csat_score", "csat_comment", "csat_recorded_at", "status", "last_activity_at", "closed_at"])
        return self._serialize_session(conversation)

    def get_conversation(self, *, session_token: str) -> Conversation:
        """Expose the active conversation for downstream orchestration logic."""

        return self._get_active_conversation_by_token(session_token)

    def store_extractions(
        self,
        *,
        session_token: str,
        items: Iterable[tuple[ConversationExtractionType, dict]],
    ) -> Sequence[ConversationExtraction]:
        conversation = self._get_active_conversation_by_token(session_token)
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

    # ------------------------------------------------------------------
    # Internal helpers

    def _get_business_by_slug(self, slug_value: str) -> BusinessProfile:
        if not slug_value:
            raise PortalNotFoundError("Business handle is required")
        normalized = slugify(slug_value)
        business = (
            BusinessProfile.objects.filter(slug__iexact=normalized)
            .select_related("agent_profile")
            .first()
        )
        if business is None:
            raise PortalNotFoundError("Business not found")
        return business

    def _get_active_conversation_by_token(self, session_token: str) -> Conversation:
        if not session_token:
            raise PortalNotFoundError("Session token is required")
        conversation = (
            Conversation.objects.select_related("business_profile", "agent_profile", "case")
            .prefetch_related(
                Prefetch("messages", queryset=ConversationMessage.objects.order_by("sent_at", "created_at")),
                "agent_profile__action_permissions",
            )
            .filter(session_token=session_token)
            .first()
        )
        if conversation is None:
            raise PortalNotFoundError("Conversation not found")
        if not conversation.is_active:
            raise PortalNotFoundError("Conversation is no longer active")
        return conversation

    def _get_or_create_conversation(
        self,
        *,
        business: BusinessProfile,
        agent: AgentProfile,
        existing_session_token: str | None,
        metadata: dict,
    ) -> Conversation:
        now = timezone.now()
        conversation = None
        if existing_session_token:
            conversation = Conversation.objects.filter(
                business_profile=business,
                session_token=existing_session_token,
            ).first()
            if conversation and not conversation.is_active:
                conversation = None

        if conversation is None:
            expires_at = now + self.session_ttl
            conversation = Conversation.objects.create(
                business_profile=business,
                agent_profile=agent,
                metadata=metadata,
                expires_at=expires_at,
            )
            self._ensure_welcome_message(conversation)
            conversation.refresh_from_db()
            return conversation

        updated_metadata = {**(conversation.metadata or {}), **metadata}
        if updated_metadata != conversation.metadata:
            conversation.metadata = updated_metadata
        if conversation.expires_at is None or conversation.expires_at < now:
            conversation.expires_at = now + self.session_ttl
        conversation.save(update_fields=["metadata", "expires_at", "last_activity_at"])
        return conversation

    def _ensure_welcome_message(self, conversation: Conversation) -> None:
        agent_name = conversation.agent_profile.name if conversation.agent_profile else "Pocket AI"
        ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.AI,
            body=f"Hi, I'm {agent_name}. How can I help today?",
            metadata={"type": "welcome"},
        )

    def _touch_conversation_after_message(self, conversation: Conversation, message: ConversationMessage) -> None:
        needs_save = False
        if message.sender == ConversationSender.CUSTOMER and conversation.first_customer_message_at is None:
            conversation.first_customer_message_at = message.sent_at
            needs_save = True
            if conversation.status == ConversationStatus.NEW:
                conversation.status = ConversationStatus.LIVE
        if message.sender == ConversationSender.AI and conversation.first_ai_message_at is None:
            conversation.first_ai_message_at = message.sent_at
            needs_save = True
        if needs_save:
            conversation.save(update_fields=["first_customer_message_at", "first_ai_message_at", "status", "last_activity_at"])
        else:
            conversation.save(update_fields=["last_activity_at"])

    def _serialize_agent(self, agent: AgentProfile) -> PortalAgentSummary:
        return PortalAgentSummary(
            id=agent.id,
            name=agent.name,
            role=agent.role or "AI Assistant",
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
        return PortalSessionState(
            conversation_id=conversation.id,
            session_token=conversation.session_token,
            status=conversation.status,
            started_at=conversation.started_at,
            expires_at=conversation.expires_at,
        )

    def _serialize_message(self, message: ConversationMessage) -> PortalMessage:
        return PortalMessage(
            id=message.id,
            sender=message.sender,
            body=message.body,
            sent_at=message.sent_at,
            metadata=message.metadata or {},
        )

    def _serialize_messages(self, messages: Iterable[ConversationMessage]) -> Iterable[PortalMessage]:
        for message in messages:
            yield self._serialize_message(message)
