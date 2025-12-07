from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import AgentProfile, BusinessProfile, KnowledgeFeedbackCase, _normalize_identifier_token
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
        conversation: Conversation | None = None,
    ) -> PortalMessage:
        conversation = self._resolve_conversation(
            session_token=session_token,
            conversation=conversation,
            include_messages=False,
        )
        if not body.strip():
            raise PortalValidationError("Message body cannot be empty")
        metadata = metadata or {}
        with transaction.atomic():
            message = ConversationMessage.objects.create(
                conversation=conversation,
                sender=sender,
                body=body.strip(),
                metadata=metadata,
            )
            metadata_updated = False
            if sender == ConversationSender.CUSTOMER:
                metadata_updated = self._capture_customer_identifiers(
                    conversation=conversation,
                    body=body,
                    message_metadata=metadata,
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
        if metadata is not None:
            message.metadata = metadata
            updated_fields.append("metadata")
        if updated_fields:
            message.save(update_fields=updated_fields)
        return self._serialize_message(message)

    def list_messages(self, *, session_token: str, limit: int | None = None) -> Sequence[PortalMessage]:
        conversation = self._get_active_conversation_by_token(session_token)
        qs = conversation.messages.all()
        if limit:
            qs = qs.order_by("sent_at", "created_at")[:limit]
        return tuple(self._serialize_messages(qs))

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
        if not conversation.is_active:
            raise PortalNotFoundError("Conversation is no longer active")
        return conversation

    def _conversation_queryset(self, *, include_messages: bool):
        queryset = (
            Conversation.objects.select_related("business_profile", "agent_profile", "case")
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

    def _touch_conversation_after_message(self, conversation: Conversation, message: ConversationMessage, *, metadata_updated: bool = False) -> None:
        needs_save = False
        if message.sender == ConversationSender.CUSTOMER and conversation.first_customer_message_at is None:
            conversation.first_customer_message_at = message.sent_at
            needs_save = True
            if conversation.status == ConversationStatus.NEW:
                conversation.status = ConversationStatus.LIVE
        if message.sender == ConversationSender.AI and conversation.first_ai_message_at is None:
            conversation.first_ai_message_at = message.sent_at
            needs_save = True
        update_fields = ["last_activity_at"]
        if metadata_updated:
            update_fields.append("metadata")
        if needs_save:
            update_fields.extend(["first_customer_message_at", "first_ai_message_at", "status"])
        conversation.save(update_fields=update_fields)

    def _capture_customer_identifiers(self, *, conversation, body: str, message_metadata: dict | None) -> bool:
        """
        Extract lightweight identifiers (email/phone/id) from the message and merge into conversation metadata.
        """

        convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
        existing_identifiers = convo_meta.get("customer_identifiers") or convo_meta.get("identifiers") or {}
        if not isinstance(existing_identifiers, dict):
            existing_identifiers = {}
        identifiers = dict(existing_identifiers)
        locked_identifier = convo_meta.get("locked_identifier") if isinstance(convo_meta, dict) else None
        captured_new: list[tuple[str, str]] = []

        def _set_identifier(key: str, value: str) -> None:
            nonlocal identifiers
            normalized_key = _normalize_identifier_token(key) or key
            clean_value = (value or "").strip()
            if not normalized_key or not clean_value:
                return
            if locked_identifier and isinstance(locked_identifier, dict):
                locked_key = locked_identifier.get("key")
                locked_value = locked_identifier.get("value")
                if locked_key == normalized_key and locked_value and locked_value != clean_value:
                    # Ignore conflicting values for the locked key; keep the original lock.
                    return
            if normalized_key not in identifiers:
                identifiers[normalized_key] = clean_value
                captured_new.append((normalized_key, clean_value))

        # Merge identifiers passed explicitly via message metadata.
        meta_identifiers = None
        if isinstance(message_metadata, dict):
            meta_identifiers = message_metadata.get("customer_identifiers") or message_metadata.get("identifiers")
        if isinstance(meta_identifiers, dict):
            for key, value in meta_identifiers.items():
                _set_identifier(str(key), str(value))

        text = body or ""
        # Email addresses
        for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
            _set_identifier("email", email)

        # Phone numbers (loose match; strip non-digits to validate length).
        for phone_raw in re.findall(r"(\+?\d[\d\s\-\(\)]{7,})", text):
            digits = re.sub(r"\D", "", phone_raw)
            if 10 <= len(digits) <= 15:
                _set_identifier("phone", phone_raw)

        # Labeled IDs (ticket/case/customer/account/user/order).
        for match in re.finditer(r"(?i)(ticket|case|order|customer|account|user|external)\s*(?:id|number|no|#)?\s*[:\-]?\s*([A-Za-z0-9\-_]+)", text):
            label = (match.group(1) or "").lower()
            value = match.group(2) or ""
            if not value:
                continue
            if label in {"ticket", "case", "order", "external"}:
                _set_identifier("external_id", value)
            elif label in {"customer", "account", "user"}:
                _set_identifier("customer_id", value)

        if identifiers == existing_identifiers:
            return False

        updated_meta = dict(convo_meta)
        updated_meta["customer_identifiers"] = identifiers
        if not locked_identifier and captured_new:
            lock_key, lock_value = captured_new[0]
            updated_meta["locked_identifier"] = {
                "key": lock_key,
                "value": lock_value,
                "locked_at": timezone.now().isoformat(),
            }
        elif locked_identifier:
            updated_meta["locked_identifier"] = locked_identifier
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
