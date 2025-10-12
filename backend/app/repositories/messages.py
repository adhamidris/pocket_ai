"""Repository helpers for conversation message queries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import and_, func, or_, select

from app.models.conversations import (
    ConversationMessage,
    ConversationMessageType,
    MessageAttachment,
)
from app.models.storage import StorageAsset
from app.repositories.base import BaseRepository

MessageCursor = str


@dataclass(slots=True, frozen=True)
class MessageListPage:
    """Paginated list result for conversation messages."""

    items: tuple[ConversationMessage, ...]
    next_cursor: MessageCursor | None
    has_more: bool


class MessagesRepository(BaseRepository):
    """Specialised queries for conversation messages and attachments."""

    def list_messages(
        self,
        *,
        conversation_id: uuid.UUID,
        limit: int,
        cursor: MessageCursor | None = None,
    ) -> MessageListPage:
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sent_at.asc(), ConversationMessage.id.asc())
        )
        stmt = self._apply_cursor(stmt, cursor)

        with self._with_timeout():
            rows = self.session.execute(stmt.limit(limit + 1)).scalars().all()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = self._encode_cursor(last.sent_at, last.id)

        return MessageListPage(items=tuple(rows), next_cursor=next_cursor, has_more=has_more)

    def load_attachments_for_messages(
        self, message_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[tuple[MessageAttachment, StorageAsset], ...]]:
        ids = tuple(message_ids)
        if not ids:
            return {}

        stmt = (
            select(MessageAttachment, StorageAsset)
            .join(StorageAsset, MessageAttachment.storage_asset_id == StorageAsset.id)
            .where(MessageAttachment.message_id.in_(ids))
            .order_by(MessageAttachment.message_id.asc(), MessageAttachment.created_at.asc(), MessageAttachment.id.asc())
        )
        with self._with_timeout():
            rows = self.session.execute(stmt).all()

        grouped: dict[uuid.UUID, list[tuple[MessageAttachment, StorageAsset]]] = {}
        for attachment, asset in rows:
            grouped.setdefault(attachment.message_id, []).append((attachment, asset))

        return {
            message_id: tuple(items)
            for message_id, items in grouped.items()
        }

    def get_first_customer_message_sent_at(self, conversation_id: uuid.UUID) -> datetime | None:
        stmt = select(func.min(ConversationMessage.sent_at)).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.message_type == ConversationMessageType.CUSTOMER,
        )
        with self._with_timeout():
            return self.session.execute(stmt).scalar_one_or_none()

    def count_customer_messages(self, conversation_id: uuid.UUID) -> int:
        stmt = select(func.count(ConversationMessage.id)).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.message_type == ConversationMessageType.CUSTOMER,
        )
        with self._with_timeout():
            return self.session.execute(stmt).scalar_one()

    # ------------------------------------------------------------------
    # Internal helpers

    def _apply_cursor(self, stmt, cursor: MessageCursor | None):
        if not cursor:
            return stmt
        sent_at, message_id = self._decode_cursor(cursor)
        return stmt.where(
            or_(
                ConversationMessage.sent_at > sent_at,
                and_(
                    ConversationMessage.sent_at == sent_at,
                    ConversationMessage.id > message_id,
                ),
            )
        )

    def _encode_cursor(self, sent_at: datetime, message_id: uuid.UUID) -> MessageCursor:
        sent_at_aware = self._ensure_aware(sent_at)
        return f"{sent_at_aware.isoformat()}|{message_id}"

    def _decode_cursor(self, cursor: str) -> tuple[datetime, uuid.UUID]:
        sent_raw, message_raw = cursor.split("|", 1)
        sent_at = datetime.fromisoformat(sent_raw)
        sent_at_aware = self._ensure_aware(sent_at)
        message_id = uuid.UUID(message_raw)
        return sent_at_aware, message_id

    def _ensure_aware(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = [
    "MessageListPage",
    "MessagesRepository",
]
