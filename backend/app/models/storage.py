"""Shared storage-related models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin


class StorageAsset(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "storage_assets"

    business_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    business: Mapped[Optional["Business"]] = relationship(back_populates="storage_assets")
    created_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[created_by_user_id])
    created_by_agent: Mapped[Optional["Agent"]] = relationship(foreign_keys=[created_by_agent_id])

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_storage_assets_size_positive"),
        CheckConstraint(
            "checksum_sha256 IS NULL OR char_length(checksum_sha256) = 64",
            name="ck_storage_assets_checksum_hex_length",
        ),
    )


Index("ix_storage_assets_business", StorageAsset.business_id)
Index(
    "uq_storage_assets_path",
    StorageAsset.storage_path,
    unique=True,
)

__all__ = ["StorageAsset"]
