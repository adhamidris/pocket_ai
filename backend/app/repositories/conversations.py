"""Repository helpers encapsulating conversation queries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.models.conversations import (
    Conversation,
    ConversationMessage,
    ConversationMessageType,
    ConversationStatus,
    MessageAttachment,
)
from app.repositories.base import BaseRepository

Cursor = str


@dataclass(slots=True, frozen=True)
class ConversationListFilters:
    """Filters used when listing conversations."""

    business_id: uuid.UUID
    statuses: Sequence[ConversationStatus] | None = None
    primary_agent_ids: Sequence[uuid.UUID] | None = None
    customer_ids: Sequence[uuid.UUID] | None = None
    search: str | None = None


@dataclass(slots=True, frozen=True)
class ConversationListPage:
    """Paginated result set for conversations."""

    items: tuple[Conversation, ...]
    has_next: bool
    next_cursor: Cursor | None
    total: int
    latest_message_types: dict[uuid.UUID, ConversationMessageType]


class ConversationsRepository(BaseRepository):
    """Encapsulates complex read patterns for conversations."""

    def list_conversations(
        self,
        filters: ConversationListFilters,
        *,
        limit: int,
        cursor: Cursor | None = None,
        include_total: bool = True,
    ) -> ConversationListPage:
        stmt = self._build_list_query(filters)
        stmt = stmt.order_by(Conversation.created_at.desc(), Conversation.id.desc())
        stmt = self._apply_cursor(stmt, cursor)

        with self._with_timeout():
            rows = self.session.execute(stmt.limit(limit + 1)).scalars().all()

        has_next = len(rows) > limit
        if has_next:
            rows = rows[:limit]

        conversation_ids = [row.id for row in rows]
        latest_types = self._load_latest_message_types(conversation_ids) if conversation_ids else {}

        next_cursor = None
        if has_next and rows:
            last = rows[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)

        total = 0
        if include_total:
            total = self.count_conversations(filters)

        return ConversationListPage(
            items=tuple(rows),
            has_next=has_next,
            next_cursor=next_cursor,
            total=total,
            latest_message_types=latest_types,
        )

    def count_conversations(self, filters: ConversationListFilters) -> int:
        stmt = self._build_list_query(filters)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        with self._with_timeout():
            return self.session.execute(count_stmt).scalar_one()

    def get_conversation_with_details(
        self,
        *,
        business_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.business_id == business_id,
            )
            .options(
                selectinload(Conversation.messages)
                .selectinload(ConversationMessage.attachments)
                .selectinload(MessageAttachment.storage_asset),
                selectinload(Conversation.participants),
                selectinload(Conversation.summary),
                selectinload(Conversation.status_log),
                selectinload(Conversation.turn_snapshots),
            )
        )
        with self._with_timeout():
            return self.session.execute(stmt).scalar_one_or_none()

    def get_conversation(
        self,
        *,
        business_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.business_id == business_id,
        )
        with self._with_timeout():
            return self.session.execute(stmt).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Internal helpers

    def _build_list_query(self, filters: ConversationListFilters):
        stmt = select(Conversation).where(Conversation.business_id == filters.business_id)
        if filters.statuses:
            stmt = stmt.where(Conversation.status.in_(tuple(filters.statuses)))
        if filters.primary_agent_ids:
            stmt = stmt.where(Conversation.primary_agent_id.in_(tuple(filters.primary_agent_ids)))
        if filters.customer_ids:
            stmt = stmt.where(Conversation.customer_id.in_(tuple(filters.customer_ids)))
        if filters.search:
            search = filters.search.strip()
            try:
                search_uuid = uuid.UUID(search)
            except ValueError:
                search_uuid = None
            if search_uuid:
                stmt = stmt.where(Conversation.id == search_uuid)
        return stmt

    def _apply_cursor(self, stmt, cursor: Cursor | None):
        if not cursor:
            return stmt
        created_at, entity_id = self._decode_cursor(cursor)
        return stmt.where(
            or_(
                Conversation.created_at < created_at,
                and_(
                    Conversation.created_at == created_at,
                    Conversation.id < entity_id,
                ),
            )
        )

    def _load_latest_message_types(
        self, conversation_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, ConversationMessageType]:
        ids = tuple(conversation_ids)
        if not ids:
            return {}
        stmt = (
            select(
                ConversationMessage.conversation_id,
                ConversationMessage.message_type,
                ConversationMessage.sent_at,
            )
            .where(ConversationMessage.conversation_id.in_(ids))
            .order_by(
                ConversationMessage.conversation_id,
                ConversationMessage.sent_at.desc(),
                ConversationMessage.id.desc(),
            )
        )
        with self._with_timeout():
            rows = self.session.execute(stmt).all()

        latest: dict[uuid.UUID, ConversationMessageType] = {}
        for conversation_id, message_type, _sent_at in rows:
            if conversation_id not in latest:
                latest[conversation_id] = message_type
        return latest

    def _encode_cursor(self, created_at: datetime, entity_id: uuid.UUID) -> Cursor:
        created_at_aware = self._ensure_aware(created_at)
        return f"{created_at_aware.isoformat()}|{entity_id}"

    def _decode_cursor(self, cursor: str) -> tuple[datetime, uuid.UUID]:
        created_raw, entity_raw = cursor.split("|", 1)
        created_at = datetime.fromisoformat(created_raw)
        created_at_aware = self._ensure_aware(created_at)
        entity_id = uuid.UUID(entity_raw)
        return created_at_aware, entity_id

    def _ensure_aware(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = [
    "ConversationListFilters",
    "ConversationListPage",
    "ConversationsRepository",
]
