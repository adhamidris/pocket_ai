from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from django.db import transaction
from django.db.models import Count, Prefetch
from django.utils import timezone
from django.utils.text import slugify

from pocketai.language import metadata_ui_language, normalize_language_code

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
)
from apps.knowledge.models import KnowledgeFeedbackCase
from apps.conversations.content_blocks import ensure_assistant_text_blocks
from apps.conversations.models import (
    Conversation,
    ConversationChannel,
    ConversationExtraction,
    ConversationExtractionType,
    ConversationMessage,
    ConversationSender,
    ConversationStatus,
    ConversationFeedback,
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
    content_blocks: list[dict[str, object]]


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


DEFAULT_SESSION_TTL: timedelta | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class PortalSessionSummary:
    """Lightweight session info for session history list."""
    session_token: str
    title: str
    started_at: datetime
    last_activity_at: datetime
    status: str
    message_count: int
    preview: str


class ChatPortalService:
    """High-level orchestration for public chat portal lifecycle."""

    def __init__(self, *, session_ttl: timedelta | None = None) -> None:
        self.session_ttl = session_ttl if session_ttl is not None else DEFAULT_SESSION_TTL

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
        return self._serialize_session(conversation_obj)

    def record_csat(self, *, session_token: str, score: int, comment: str | None = None) -> PortalSessionState:
        if score < 1 or score > 5:
            raise PortalValidationError("Score must be between 1 and 5")
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=None,
            include_messages=False,
        )
        conversation.csat_score = score
        conversation.csat_comment = (comment or "").strip()
        conversation.csat_recorded_at = timezone.now()
        if conversation.status == ConversationStatus.LIVE:
            conversation.status = ConversationStatus.RESOLVED
        conversation.save(update_fields=["csat_score", "csat_comment", "csat_recorded_at", "status", "last_activity_at", "closed_at"])
        return self._serialize_session(conversation)

    def record_feedback(
        self,
        *,
        session_token: str,
        feedback_type: str,
        message_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> ConversationFeedback:
        if feedback_type not in ConversationFeedback.FeedbackType.values:
            raise PortalValidationError("Unsupported feedback_type")
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=None,
            include_messages=False,
        )
        message = None
        if message_id:
            message = conversation.messages.filter(id=message_id).first()
            if message is None:
                raise PortalNotFoundError("Message not found")
        feedback = ConversationFeedback.objects.create(
            conversation=conversation,
            message=message,
            feedback_type=feedback_type,
            payload=payload or {},
        )
        if feedback_type == ConversationFeedback.FeedbackType.NOT_FOUND_INCORRECT:
            self._promote_feedback_case(conversation=conversation, feedback=feedback)
        return feedback

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
        
        summaries: list[PortalSessionSummary] = []
        for conv in conversations:
            # Generate title from first customer message
            first_messages = getattr(conv, "first_customer_messages", [])
            first_msg = first_messages[0] if first_messages else None
            
            if first_msg:
                title = self._generate_session_title(first_msg.body)
                preview = (first_msg.body or "")[:100]
            else:
                title = "New conversation"
                preview = ""
            
            summaries.append(PortalSessionSummary(
                session_token=conv.session_token,
                title=title,
                started_at=conv.started_at,
                last_activity_at=conv.last_activity_at,
                status=conv.status,
                message_count=conv.message_count,
                preview=preview.strip(),
            ))
        
        return tuple(summaries)

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

    def _generate_session_title(self, first_message: str, max_length: int = 50) -> str:
        """
        Generate a meaningful session title from the first customer message.
        
        Truncates at word boundary and adds ellipsis if needed.
        """
        text = (first_message or "").strip()
        if not text:
            return "New conversation"
        
        # Remove newlines and extra whitespace
        text = " ".join(text.split())
        
        if len(text) <= max_length:
            return text
        
        # Truncate at word boundary
        truncated = text[:max_length]
        last_space = truncated.rfind(" ")
        if last_space > max_length // 2:
            truncated = truncated[:last_space]
        
        return truncated.rstrip(".,!?;:") + "..."

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

    def _get_active_conversation_by_token(self, session_token: str, *, include_messages: bool = False) -> Conversation:
        if not session_token:
            raise PortalNotFoundError("Session token is required")
        queryset = self._conversation_queryset(include_messages=include_messages)
        conversation = queryset.filter(session_token=session_token).first()
        if conversation is None:
            raise PortalNotFoundError("Conversation not found")
        return conversation

    def _conversation_queryset(self, *, include_messages: bool):
        queryset = (
            Conversation.objects.select_related("business_profile", "agent_profile")
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

    def _promote_feedback_case(self, *, conversation: Conversation, feedback: ConversationFeedback) -> None:
        payload = feedback.payload or {}
        query_text = (payload.get("query_text") or "").strip()
        if not query_text:
            latest_customer = (
                conversation.messages.filter(sender=ConversationSender.CUSTOMER)
                .order_by("-sent_at", "-created_at")
                .first()
            )
            query_text = (latest_customer.body if latest_customer else "").strip()
        if not query_text:
            return
        expected_behavior = (
            payload.get("expected_behavior")
            or KnowledgeFeedbackCase.BehaviorChoices.ALIAS
        )
        metadata_snapshot = conversation.metadata if isinstance(conversation.metadata, dict) else {}
        metadata_payload = {"source": "portal_feedback"}
        route = metadata_snapshot.get("knowledge_route")
        diagnostics = metadata_snapshot.get("knowledge_last_diagnostics")
        if isinstance(route, dict):
            metadata_payload["search_route"] = route
        if isinstance(diagnostics, dict):
            metadata_payload["retrieval_diagnostics"] = diagnostics
        KnowledgeFeedbackCase.objects.get_or_create(
            conversation_feedback=feedback,
            defaults={
                "business_profile": conversation.business_profile,
                "query_text": query_text,
                "expected_behavior": expected_behavior,
                "expected_entities": payload.get("expected_entities") or [],
                "expected_aliases": payload.get("expected_aliases") or [],
                "notes": payload.get("notes") or "",
                "is_active": bool(payload.get("auto_promote", True)),
                "metadata": metadata_payload,
            },
        )

    def _get_or_create_conversation(
        self,
        *,
        business: BusinessProfile,
        agent: AgentProfile,
        existing_session_token: str | None,
        metadata: dict,
    ) -> Conversation:
        incoming_metadata = dict(metadata or {})
        now = timezone.now()
        conversation = None
        if existing_session_token:
            conversation = Conversation.objects.filter(
                business_profile=business,
                session_token=existing_session_token,
            ).first()
            # Session cookies can outlive DB resets or tenant cleanup. Treat a stale
            # token as a cache miss and create a fresh conversation instead of 404ing.

        if conversation is None:
            expires_at = now + self.session_ttl if self.session_ttl else None
            conversation = Conversation.objects.create(
                business_profile=business,
                agent_profile=agent,
                metadata=incoming_metadata,
                expires_at=expires_at,
            )
            # self._ensure_welcome_message(conversation) # Disabled to support empty state
            conversation.refresh_from_db()
            return conversation

        updated_metadata = {**(conversation.metadata or {}), **incoming_metadata}
        update_fields: list[str] = ["last_activity_at"]
        if updated_metadata != conversation.metadata:
            conversation.metadata = updated_metadata
            update_fields.append("metadata")
        if self.session_ttl:
            next_expires = now + self.session_ttl
            if conversation.expires_at != next_expires:
                conversation.expires_at = next_expires
                update_fields.append("expires_at")
        elif conversation.expires_at is not None:
            conversation.expires_at = None
            update_fields.append("expires_at")
        conversation.save(update_fields=update_fields)
        return conversation

    def _ensure_welcome_message(self, conversation: Conversation) -> None:
        agent_name = conversation.agent_profile.name if conversation.agent_profile else "Pocket AI"
        ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.AI,
            body=f"Hi, I'm {agent_name}. How can I help today?",
            metadata={"type": "welcome"},
        )

    def _touch_conversation_after_message(self, conversation: Conversation, message: ConversationMessage, *, metadata_updated: bool = False) -> None:
        update_fields: list[str] = ["last_activity_at"]
        if message.sender == ConversationSender.CUSTOMER:
            if conversation.first_customer_message_at is None:
                conversation.first_customer_message_at = message.sent_at
                update_fields.append("first_customer_message_at")
            if conversation.status in {
                ConversationStatus.NEW,
                ConversationStatus.RESOLVED,
                ConversationStatus.CLOSED,
                ConversationStatus.EXPIRED,
            }:
                conversation.status = ConversationStatus.LIVE
                conversation.closed_at = None
                update_fields.extend(["status", "closed_at"])
        if message.sender == ConversationSender.AI and conversation.first_ai_message_at is None:
            conversation.first_ai_message_at = message.sent_at
            update_fields.append("first_ai_message_at")
        now = timezone.now()
        if self.session_ttl:
            next_expires = now + self.session_ttl
            if conversation.expires_at != next_expires:
                conversation.expires_at = next_expires
                update_fields.append("expires_at")
        elif conversation.expires_at is not None:
            conversation.expires_at = None
            update_fields.append("expires_at")
        if metadata_updated:
            update_fields.append("metadata")
        conversation.save(update_fields=list(dict.fromkeys(update_fields)))

    def _capture_ui_language_preference(self, *, conversation: Conversation, language: str | None) -> bool:
        normalized = normalize_language_code(language)
        if not normalized:
            return False
        convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
        if convo_meta.get("ui_language") == normalized:
            return False
        updated_meta = dict(convo_meta)
        updated_meta["ui_language"] = normalized
        conversation.metadata = updated_meta
        return True

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
