from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from django.contrib.auth import get_user_model
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
    AgentWorkflow,
    Conversation,
    ConversationChannel,
    ConversationExtraction,
    ConversationExtractionType,
    ConversationMessage,
    ConversationSender,
    ConversationStatus,
    ConversationFeedback,
)


INTERNAL_AGENT_NOTIFICATION_PURPOSE = "agent_notification_surface"
INTERNAL_CANONICAL_CONVERSATION_TYPES = {
    "canonical_main_primary",
    "canonical_department_primary",
}


def _is_internal_agent_notification_surface(conversation: Conversation) -> bool:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
    purpose = str(metadata.get("purpose") or "").strip().lower()
    meta_type = str(metadata.get("type") or "").strip().lower()
    source = str(metadata.get("source") or "").strip().lower()
    return purpose == INTERNAL_AGENT_NOTIFICATION_PURPOSE or meta_type in INTERNAL_CANONICAL_CONVERSATION_TYPES or source == "agent_run"


class PortalNotFoundError(Exception):
    """Raised when portal business/agent/session cannot be found."""


class PortalValidationError(ValueError):
    """Raised when incoming payload fails validation."""


class PortalAuthorizationError(PermissionError):
    """Raised when an authenticated user cannot access a portal resource."""


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
    session_type: str = "chat"
    workflow_id: uuid.UUID | None = None
    workflow_name: str = ""


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
    conversation_id: uuid.UUID
    session_token: str
    title: str
    started_at: datetime
    last_activity_at: datetime
    status: str
    message_count: int
    preview: str
    session_type: str = "chat"
    workflow_id: uuid.UUID | None = None
    workflow_name: str = ""


class ChatPortalService:
    """High-level orchestration for chat session lifecycle."""

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
            .select_related("workflow")
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
        filters: dict[str, object] = {
            "business_profile": business,
            "agent_profile": agent,
        }
        if not getattr(owner_user, "is_staff", False):
            business_profiles = getattr(owner_user, "business_profiles", None)
            has_business_access = bool(
                getattr(owner_user, "id", None) == getattr(business, "user_id", None)
                or (business_profiles is not None and business_profiles.filter(id=business.id).exists())
            )
            if not has_business_access:
                filters["owner_user"] = owner_user
        candidate_limit = max(limit, min(max(limit * 3, limit + 25), 300))
        conversations = (
            Conversation.objects.filter(**filters)
            .exclude(metadata__has_key="anchor_conversation_id")
            .exclude(metadata__has_key="anchorConversationId")
            .select_related("workflow")
            .annotate(message_count=Count("messages"))
            .prefetch_related(
                Prefetch(
                    "messages",
                    queryset=ConversationMessage.objects.filter(
                        sender=ConversationSender.CUSTOMER
                    ).order_by("sent_at", "created_at")[:1],
                    to_attr="first_customer_messages",
                ),
                Prefetch(
                    "agent_workflows",
                    queryset=AgentWorkflow.objects.only("id", "name", "conversation_id"),
                    to_attr="linked_workflows",
                ),
            )
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

    def _classify_conversation_session(self, conversation: Conversation) -> tuple[str, uuid.UUID | None, str]:
        metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
        has_prefetched_workflows = hasattr(conversation, "linked_workflows")
        linked_workflows = list(getattr(conversation, "linked_workflows", []) or [])
        linked_workflow = linked_workflows[0] if linked_workflows else None
        direct_workflow = getattr(conversation, "workflow", None)
        if linked_workflow is None and direct_workflow is not None:
            linked_workflow = direct_workflow
        if linked_workflow is None and not has_prefetched_workflows:
            linked_workflow = conversation.agent_workflows.only("id", "name", "conversation_id").first()
        meta_type = str(metadata.get("type") or metadata.get("purpose") or metadata.get("source") or "").strip().lower()
        workflow_id = getattr(linked_workflow, "id", None)
        workflow_name = (
            (getattr(linked_workflow, "name", "") or "")
            or str(metadata.get("workflow_name") or metadata.get("workflowName") or "").strip()
        )
        is_task_thread = bool(
            linked_workflow
            or getattr(conversation, "workflow_id", None)
            or meta_type == "workflow_thread"
            or meta_type == "workflow_agent_session"
            or str(metadata.get("workflow_id") or "").strip()
        )
        return ("task" if is_task_thread else "chat", workflow_id, workflow_name)

    def _build_session_summaries(
        self,
        conversations: Iterable[Conversation],
        *,
        limit: int | None = None,
    ) -> tuple[PortalSessionSummary, ...]:
        summaries: list[PortalSessionSummary] = []
        for conv in conversations:
            if _is_internal_agent_notification_surface(conv):
                continue
            first_messages = getattr(conv, "first_customer_messages", [])
            first_msg = first_messages[0] if first_messages else None
            session_type, workflow_id, workflow_name = self._classify_conversation_session(conv)
            is_task_thread = session_type == "task"

            if first_msg:
                title = self._generate_session_title(first_msg.body)
                preview = (first_msg.body or "")[:100]
            elif is_task_thread and workflow_name:
                title = workflow_name
                preview = "Task thread"
            elif is_task_thread:
                title = "Task thread"
                preview = ""
            else:
                title = "New conversation"
                preview = ""

            summaries.append(
                PortalSessionSummary(
                    conversation_id=conv.id,
                    session_token=conv.session_token,
                    title=title,
                    started_at=conv.started_at,
                    last_activity_at=conv.last_activity_at,
                    status=conv.status,
                    message_count=getattr(conv, "message_count", 0),
                    preview=preview.strip(),
                    session_type=session_type,
                    workflow_id=workflow_id,
                    workflow_name=workflow_name,
                )
            )
            if limit is not None and len(summaries) >= limit:
                break
        return tuple(summaries)

    # ------------------------------------------------------------------
    # Internal helpers


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
        owner_user = self._resolve_owner_user(business=business, metadata=incoming_metadata)
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
                owner_user=owner_user,
                metadata=incoming_metadata,
                expires_at=expires_at,
            )
            # self._ensure_welcome_message(conversation) # Disabled to support empty state
            conversation.refresh_from_db()
            return conversation

        updated_metadata = {**(conversation.metadata or {}), **incoming_metadata}
        update_fields: list[str] = ["last_activity_at"]
        if conversation.owner_user_id is None and owner_user is not None:
            conversation.owner_user = owner_user
            update_fields.append("owner_user")
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

    def _resolve_owner_user(self, *, business: BusinessProfile, metadata: dict) -> object:
        actor_user_id = (
            metadata.get("actor_user_id")
            or metadata.get("actorUserId")
            or metadata.get("owner_user_id")
            or metadata.get("ownerUserId")
        )
        if actor_user_id:
            user_model = get_user_model()
            owner_user = user_model.objects.filter(id=actor_user_id).first()
            if owner_user is not None:
                if getattr(owner_user, "is_staff", False):
                    return owner_user
                if owner_user.business_profiles.filter(id=business.id).exists():
                    return owner_user
        return business.user

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
        session_type, workflow_id, workflow_name = self._classify_conversation_session(conversation)
        return PortalSessionState(
            conversation_id=conversation.id,
            session_token=conversation.session_token,
            status=conversation.status,
            started_at=conversation.started_at,
            expires_at=conversation.expires_at,
            session_type=session_type,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
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
