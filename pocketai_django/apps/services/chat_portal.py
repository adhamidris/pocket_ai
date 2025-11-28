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
    """
    High-level orchestration for public chat portal lifecycle.
    
    Responsibilities:
        - Session management (bootstrap, resume, expiry)
        - Message persistence (customer + AI messages)
        - Identifier extraction from customer messages
        - CSAT and feedback recording
        - Extraction storage for downstream analytics
        
    Thread safety:
        Methods are designed to be called from multiple threads (e.g., stream_send
        worker threads). Uses Django transactions for atomicity.
    """

    def __init__(self, *, session_ttl: timedelta | None = None) -> None:
        self.session_ttl = session_ttl or DEFAULT_SESSION_TTL

    # ------------------------------------------------------------------
    # Public API

    def resolve_handle(self, business_slug: str, agent_slug: str) -> tuple[BusinessProfile, AgentProfile]:
        """
        Resolve friendly URL slugs to business + agent entities.
        
        Used by the portal widget to validate the chat URL before bootstrapping.
        Matches step 1.1 in docs/llm_conversation_backend_flow.md.
        
        Args:
            business_slug: URL-friendly business identifier (e.g., "my-store")
            agent_slug: URL-friendly agent identifier (e.g., "pocket-agent")
            
        Returns:
            Tuple of (BusinessProfile, AgentProfile) for the matched entities.
            
        Raises:
            PortalNotFoundError: If business or agent not found, or agent slug mismatch.
        """
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
        """
        Create or resume a portal session (step 1.2 in flow doc).
        
        Flow:
            1. Resolve business/agent from slugs
            2. Reuse existing conversation if session_token is valid and active
            3. Otherwise create new conversation with welcome message
            4. Return session snapshot + message history for widget initialization
            
        Why session tokens:
            - Allows widget refresh without losing conversation state
            - TTL-based expiry (default 4 hours) prevents stale sessions
            - Token is opaque to client (security through obscurity)
            
        Args:
            business_slug: URL-friendly business identifier
            agent_slug: URL-friendly agent identifier
            existing_session_token: Optional token from previous bootstrap (for resume)
            metadata: Optional per-session metadata (identifier hints, etc.)
            
        Returns:
            PortalSessionBootstrap with business/agent/session + message history
        """
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
        """
        Persist a message (customer or AI) to the conversation.
        
        Used by:
            - stream_send: Persists customer message before LLM turn (step 2.2 in flow doc)
            - finalize_stream_context: Persists AI message after streaming completes
            
        Side effects:
            - Extracts identifiers (email, phone, IDs) from customer messages
            - Updates conversation timestamps and status
            - Locks first identifier found (prevents cross-customer data leakage)
            
        Args:
            session_token: Active session token
            sender: CUSTOMER or AI
            body: Message text (trimmed before storage)
            metadata: Optional message metadata (citations, actions, diagnostics)
            
        Returns:
            Serialized PortalMessage with id, sender, body, sent_at, metadata
            
        Raises:
            PortalNotFoundError: If session_token is invalid or conversation inactive
            PortalValidationError: If body is empty
        """
        conversation = self._get_active_conversation_by_token(session_token)
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
            # Extract identifiers from customer messages for knowledge gating
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
    ) -> PortalMessage:
        if not message_id:
            raise PortalValidationError("message_id is required")
        conversation = self._get_active_conversation_by_token(session_token)
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
        conversation = self._get_active_conversation_by_token(session_token)
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

    def get_conversation(self, *, session_token: str) -> Conversation:
        """Expose the active conversation for downstream orchestration logic."""

        return self._get_active_conversation_by_token(session_token)

    def store_extractions(
        self,
        *,
        session_token: str,
        items: Iterable[tuple[ConversationExtractionType, dict]],
    ) -> Sequence[ConversationExtraction]:
        """
        Store structured entity extractions from the planner pass.
        
        Used by finalize_stream_context after planner completes (step 5.3 in flow doc).
        Extractions are used for:
            - Analytics (what entities were mentioned in conversations)
            - Downstream workflows (CRM integration, reporting)
            - Training data (improve entity recognition)
            
        Args:
            session_token: Active session token
            items: Iterable of (extraction_type, payload) tuples from planner
            
        Returns:
            Sequence of created ConversationExtraction records
        """
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
        """
        Resolve business by URL-friendly slug.
        
        Used by resolve_handle to map portal URLs (e.g., /chat/my-store/agent)
        to BusinessProfile entities. Normalizes slug for case-insensitive matching.
        
        Why slug normalization:
            - Slugs may have inconsistent casing from URL encoding
            - Django slugify ensures consistent format
            - Case-insensitive matching handles variations
        
        Args:
            slug_value: URL-friendly business identifier (e.g., "my-store")
            
        Returns:
            BusinessProfile with matching slug
            
        Raises:
            PortalNotFoundError: If slug is empty or business not found
        """
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
        """
        Load active conversation by session token with eager loading.
        
        Used by bootstrap_session and get_conversation to resume existing
        sessions. Eagerly loads related objects (business, agent, case, messages)
        to avoid N+1 queries.
        
        Why eager loading:
            - Conversation is accessed frequently (every message)
            - Related objects (business, agent) are always needed
            - Messages are needed for transcript assembly
            - Prefetch reduces database round-trips
        
        Args:
            session_token: Opaque session identifier from portal client
            
        Returns:
            Active Conversation with related objects loaded
            
        Raises:
            PortalNotFoundError: If token is empty, conversation not found, or conversation is inactive
        """
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
        
        Why identifier extraction:
            - Enables knowledge gating (restrict search/read to customer's own data)
            - Supports identifier-based routing (e.g., "check my account")
            - First identifier found becomes "locked" (prevents cross-customer leakage)
            
        Patterns detected:
            - Email addresses (regex)
            - Phone numbers (10-15 digits, flexible formatting)
            - Labeled IDs (ticket/case/order/customer/account/user IDs)
            
        Returns:
            True if conversation metadata was updated, False otherwise
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
        # Lock the first identifier found to prevent cross-customer data leakage
        # Why locking:
        #   - Once we identify a customer (e.g., email), all subsequent searches
        #     must be scoped to that customer's data only
        #   - Prevents showing other customers' account statements, tickets, etc.
        #   - Lock persists for the conversation lifetime
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
