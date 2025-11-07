"""Session lifecycle utilities for chat visitors and conversations."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversations import (
    ChatChannel,
    ChatDeviceFingerprint,
    ChatVisitor,
    ChatVisitorType,
    Conversation,
    ConversationParticipant,
    ConversationParticipantType,
    ConversationSource,
    ConversationStatus,
    ConversationStatusLog,
)
from app.models.customers import (
    Customer,
    CustomerActivityEvent,
    CustomerActivityEventType,
    CustomerLifecycleStage,
)
from app.models.registration import Agent
from app.core.settings import get_settings
from app.utils.chat import (
    compute_session_expiration,
    default_session_ttl,
    generate_session_token,
    is_session_expired,
    resolve_welcome_template,
)
from app.services.errors import ServiceNotFoundError, ServiceValidationError


@dataclass(slots=True, frozen=True)
class ChatSessionDTO:
    session_token: str
    visitor_id: uuid.UUID
    visitor_type: ChatVisitorType
    conversation_id: uuid.UUID
    conversation_status: ConversationStatus
    customer_id: uuid.UUID | None
    started_at: datetime | None
    expires_at: datetime | None
    welcome_template_key: str | None


@dataclass(slots=True, frozen=True)
class CreateChatSessionInput:
    business_id: uuid.UUID
    agent_id: uuid.UUID
    channel: ChatChannel = ChatChannel.WEB_WIDGET
    locale: str | None = None
    landing_page: str | None = None
    fingerprint_hash: str | None = None
    utm: dict | None = None
    existing_session_token: str | None = None
    customer_id: uuid.UUID | None = None
    auto_create_customer: bool = True
    welcome_template_key: str | None = None


@dataclass(slots=True, frozen=True)
class CreateChatSessionResult:
    session: ChatSessionDTO
    visitor_created: bool
    conversation_created: bool
    customer_created: bool


class ChatSessionsService:
    """Handles visitor sessions, TTL refresh, and conversation bootstrap."""

    def __init__(
        self,
        session: Session,
        *,
        session_ttl: timedelta | None = None,
        default_welcome_template: str | None = None,
    ) -> None:
        self.session = session
        settings = get_settings()
        self.session_ttl = session_ttl or default_session_ttl()
        self.default_welcome_template_key = (
            default_welcome_template
            if default_welcome_template is not None
            else settings.CHAT_DEFAULT_WELCOME_TEMPLATE_KEY
        )

    # ------------------------------------------------------------------
    # Public API

    def create_or_refresh_session(self, input_data: CreateChatSessionInput) -> CreateChatSessionResult:
        agent = self._get_agent(input_data.agent_id, input_data.business_id)
        now = datetime.now(timezone.utc)

        visitor = None
        visitor_created = False
        customer_created = False

        if input_data.existing_session_token:
            visitor = self._find_active_visitor(
                business_id=input_data.business_id,
                session_token=input_data.existing_session_token,
                now=now,
            )

        resolved_welcome_key: str | None = None

        if visitor is None:
            resolved_welcome_key = resolve_welcome_template(
                input_data.welcome_template_key,
                default_key=self.default_welcome_template_key,
            )
            visitor = self._create_visitor(
                business_id=input_data.business_id,
                channel=input_data.channel,
                locale=input_data.locale,
                landing_page=input_data.landing_page,
                utm=input_data.utm,
                welcome_key=resolved_welcome_key,
                now=now,
            )
            visitor_created = True
        else:
            if input_data.welcome_template_key:
                resolved_welcome_key = resolve_welcome_template(input_data.welcome_template_key)
            self._refresh_visitor(
                visitor=visitor,
                channel=input_data.channel,
                locale=input_data.locale,
                landing_page=input_data.landing_page,
                utm=input_data.utm,
                welcome_key=resolved_welcome_key,
                now=now,
            )

        if input_data.customer_id is not None:
            self._attach_existing_customer(
                visitor=visitor,
                business_id=input_data.business_id,
                customer_id=input_data.customer_id,
            )
        elif visitor.customer_id is None and input_data.auto_create_customer:
            customer = self._create_placeholder_customer(
                business_id=input_data.business_id,
                locale=input_data.locale,
                now=now,
            )
            visitor.customer_id = customer.id
            customer_created = True

        if input_data.fingerprint_hash:
            self._record_fingerprint(visitor, input_data.fingerprint_hash, now)

        conversation, conversation_created = self._ensure_active_conversation(
            visitor=visitor,
            agent=agent,
            channel=input_data.channel,
            now=now,
        )

        if conversation_created and visitor.customer_id:
            self._record_conversation_start_activity(
                business_id=input_data.business_id,
                customer_id=visitor.customer_id,
                conversation_id=conversation.id,
                occurred_at=now,
            )

        self.session.flush()

        session_dto = ChatSessionDTO(
            session_token=visitor.session_token,
            visitor_id=visitor.id,
            visitor_type=visitor.visitor_type,
            conversation_id=conversation.id,
            conversation_status=conversation.status,
            customer_id=visitor.customer_id,
            started_at=visitor.current_session_started_at,
            expires_at=visitor.current_session_expires_at,
            welcome_template_key=visitor.welcome_template_key,
        )

        return CreateChatSessionResult(
            session=session_dto,
            visitor_created=visitor_created,
            conversation_created=conversation_created,
            customer_created=customer_created,
        )

    # ------------------------------------------------------------------
    # Internal helpers

    def _get_agent(self, agent_id: uuid.UUID, business_id: uuid.UUID) -> Agent:
        stmt = select(Agent).where(Agent.id == agent_id, Agent.business_id == business_id)
        agent = self.session.execute(stmt).scalar_one_or_none()
        if agent is None:
            raise ServiceNotFoundError("Agent not found")
        return agent

    def _find_active_visitor(
        self,
        *,
        business_id: uuid.UUID,
        session_token: str,
        now: datetime,
    ) -> ChatVisitor | None:
        stmt = select(ChatVisitor).where(
            ChatVisitor.business_id == business_id,
            ChatVisitor.session_token == session_token,
        )
        visitor = self.session.execute(stmt).scalar_one_or_none()
        if visitor is None:
            return None
        if is_session_expired(visitor.current_session_expires_at, now):
            return None
        visitor.last_seen_at = now
        visitor.current_session_expires_at = compute_session_expiration(now, self.session_ttl)
        return visitor

    def _create_visitor(
        self,
        *,
        business_id: uuid.UUID,
        channel: ChatChannel,
        locale: str | None,
        landing_page: str | None,
        utm: dict | None,
        welcome_key: str | None,
        now: datetime,
    ) -> ChatVisitor:
        session_token = self._generate_session_token()
        expires_at = compute_session_expiration(now, self.session_ttl)
        visitor = ChatVisitor(
            business_id=business_id,
            visitor_type=ChatVisitorType.ANONYMOUS,
            session_token=session_token,
            channel=channel,
            locale=locale,
            landing_page=landing_page,
            utm_json=utm,
            first_seen_at=now,
            last_seen_at=now,
            current_session_started_at=now,
            current_session_expires_at=expires_at,
            welcome_template_key=welcome_key,
        )
        self.session.add(visitor)
        self.session.flush()
        return visitor

    def _refresh_visitor(
        self,
        *,
        visitor: ChatVisitor,
        channel: ChatChannel,
        locale: str | None,
        landing_page: str | None,
        utm: dict | None,
        welcome_key: str | None,
        now: datetime,
    ) -> None:
        visitor.channel = channel
        visitor.last_seen_at = now
        visitor.current_session_expires_at = compute_session_expiration(now, self.session_ttl)
        if visitor.current_session_started_at is None:
            visitor.current_session_started_at = now
        if locale:
            visitor.locale = locale
        if landing_page:
            visitor.landing_page = landing_page
        if utm:
            visitor.utm_json = {**visitor.utm_json, **utm} if visitor.utm_json else utm
        if welcome_key:
            visitor.welcome_template_key = welcome_key

    def _attach_existing_customer(
        self,
        *,
        visitor: ChatVisitor,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
    ) -> None:
        stmt = select(Customer).where(Customer.id == customer_id, Customer.business_id == business_id)
        customer = self.session.execute(stmt).scalar_one_or_none()
        if customer is None:
            raise ServiceNotFoundError("Customer not found")
        visitor.customer_id = customer.id

    def _create_placeholder_customer(
        self,
        *,
        business_id: uuid.UUID,
        locale: str | None,
        now: datetime,
    ) -> Customer:
        label = f"Guest {secrets.token_hex(4).upper()}"
        customer = Customer(
            business_id=business_id,
            full_name=label,
            primary_email=None,
            primary_phone=None,
            country=None,
            lifecycle_stage=CustomerLifecycleStage.LEAD,
            persona_tags=[],
            last_contact_at=None,
        )
        self.session.add(customer)
        self.session.flush()
        return customer

    def _record_fingerprint(
        self,
        visitor: ChatVisitor,
        fingerprint_hash: str,
        now: datetime,
    ) -> None:
        stmt = select(ChatDeviceFingerprint).where(
            ChatDeviceFingerprint.visitor_id == visitor.id,
            ChatDeviceFingerprint.fingerprint_hash == fingerprint_hash,
        )
        existing = self.session.execute(stmt).scalar_one_or_none()
        if existing is None:
            self.session.add(
                ChatDeviceFingerprint(
                    visitor_id=visitor.id,
                    fingerprint_hash=fingerprint_hash,
                    detected_at=now,
                )
            )

    def _ensure_active_conversation(
        self,
        *,
        visitor: ChatVisitor,
        agent: Agent,
        channel: ChatChannel,
        now: datetime,
    ) -> tuple[Conversation, bool]:
        active_stmt = select(Conversation).where(
            Conversation.business_id == visitor.business_id,
            Conversation.visitor_id == visitor.id,
            Conversation.status.in_([ConversationStatus.NEW, ConversationStatus.LIVE]),
        ).order_by(Conversation.created_at.desc(), Conversation.id.desc())
        conversation = self.session.execute(active_stmt.limit(1)).scalar_one_or_none()
        if conversation:
            if conversation.primary_agent_id is None:
                conversation.primary_agent_id = agent.id
            if conversation.customer_id is None and visitor.customer_id:
                conversation.customer_id = visitor.customer_id
            return conversation, False

        source = self._map_channel_to_source(channel)
        conversation = Conversation(
            business_id=visitor.business_id,
            visitor_id=visitor.id,
            customer_id=visitor.customer_id,
            primary_agent_id=agent.id,
            source=source,
            status=ConversationStatus.NEW,
        )
        self.session.add(conversation)
        self.session.flush()

        self.session.add(
            ConversationParticipant(
                conversation_id=conversation.id,
                participant_type=ConversationParticipantType.AGENT,
                participant_id=agent.id,
                joined_at=now,
            )
        )
        if visitor.customer_id:
            self.session.add(
                ConversationParticipant(
                    conversation_id=conversation.id,
                    participant_type=ConversationParticipantType.CUSTOMER,
                    participant_id=visitor.customer_id,
                    joined_at=now,
                )
            )

        self.session.add(
            ConversationStatusLog(
                conversation_id=conversation.id,
                from_status=None,
                to_status=ConversationStatus.NEW,
                actor_agent_id=agent.id,
            )
        )

        return conversation, True

    def _record_conversation_start_activity(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        occurred_at: datetime,
    ) -> None:
        self.session.add(
            CustomerActivityEvent(
                business_id=business_id,
                customer_id=customer_id,
                conversation_id=conversation_id,
                event_type=CustomerActivityEventType.CONVERSATION_STARTED,
                occurred_at=occurred_at,
            )
        )

    def _map_channel_to_source(self, channel: ChatChannel) -> ConversationSource:
        if channel == ChatChannel.WEB_WIDGET:
            return ConversationSource.WEB
        if channel == ChatChannel.API:
            return ConversationSource.API
        if channel in {ChatChannel.WHATSAPP, ChatChannel.MESSENGER, ChatChannel.EMAIL, ChatChannel.OTHER}:
            return ConversationSource.INTEGRATION
        return ConversationSource.MOBILE

    def _generate_session_token(self) -> str:
        return generate_session_token()


__all__ = [
    "ChatSessionDTO",
    "ChatSessionsService",
    "CreateChatSessionInput",
    "CreateChatSessionResult",
]
