"""Extended knowledge base models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin


class KnowledgeCollectionVisibility(enum.Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    INTERNAL = "internal"


class KnowledgeCollection(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "knowledge_collections"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[KnowledgeCollectionVisibility] = mapped_column(
        Enum(
            KnowledgeCollectionVisibility,
            name="knowledge_collection_visibility_enum",
            create_type=False,
        ),
        nullable=False,
    )
    default_access_level: Mapped[str | None] = mapped_column(String(32), nullable=True)

    business: Mapped["Business"] = relationship(back_populates="knowledge_collections")
    items: Mapped[list["KnowledgeCollectionLink"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "business_id", "label", name="uq_knowledge_collections_business_label"
        ),
    )


class KnowledgeCollectionLink(CreatedAtMixin, Base):
    __tablename__ = "knowledge_collection_links"

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), primary_key=True
    )

    collection: Mapped[KnowledgeCollection] = relationship(back_populates="items")
    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="collection_links")


class KnowledgeIngestionJobType(enum.Enum):
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"


class KnowledgeIngestionJobStatus(enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class KnowledgeIngestionJob(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "knowledge_ingestion_jobs"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[KnowledgeIngestionJobType] = mapped_column(
        Enum(
            KnowledgeIngestionJobType,
            name="knowledge_ingestion_job_type_enum",
            create_type=False,
        ),
        nullable=False,
    )
    status: Mapped[KnowledgeIngestionJobStatus] = mapped_column(
        Enum(
            KnowledgeIngestionJobStatus,
            name="knowledge_ingestion_job_status_enum",
            create_type=False,
        ),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="ingestion_jobs")

    __table_args__ = (
        Index(
            "ix_knowledge_ingestion_jobs_item_status",
            knowledge_item_id,
            status,
            text("created_at DESC"),
        ),
    )


class KnowledgeParseResult(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_parse_results"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    sections_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    token_count: Mapped[int | None] = mapped_column(nullable=True)

    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="parse_result")


class KnowledgeChunk(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_chunks"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(nullable=False)
    text_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="chunks")
    embedding: Mapped[Optional["KnowledgeEmbedding"]] = relationship(
        back_populates="knowledge_chunk", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "knowledge_item_id", "sequence_no", name="uq_knowledge_chunks_sequence"
        ),
    )


class KnowledgeEmbedding(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_embeddings"

    knowledge_chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False
    )
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    dimensions: Mapped[int] = mapped_column(nullable=False)
    vector_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    knowledge_chunk: Mapped[KnowledgeChunk] = relationship(back_populates="embedding")

    __table_args__ = (
        Index("ix_knowledge_embeddings_chunk", knowledge_chunk_id),
    )


class KnowledgePermissionScope(enum.Enum):
    BUSINESS = "business"
    AGENT = "agent"
    TEAM = "team"


class KnowledgePermissionType(enum.Enum):
    READ = "read"
    ANNOTATE = "annotate"
    MANAGE = "manage"


class KnowledgePermission(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_permissions"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[KnowledgePermissionScope] = mapped_column(
        Enum(
            KnowledgePermissionScope,
            name="knowledge_permission_scope_enum",
            create_type=False,
        ),
        nullable=False,
    )
    grantee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    permission: Mapped[KnowledgePermissionType] = mapped_column(
        Enum(
            KnowledgePermissionType,
            name="knowledge_permission_type_enum",
            create_type=False,
        ),
        nullable=False,
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="permissions")

    __table_args__ = (
        Index(
            "ix_knowledge_permissions_item_scope",
            knowledge_item_id,
            scope,
            permission,
        ),
    )


class KnowledgeAuditEvent(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_audit_events"

    knowledge_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    knowledge_item: Mapped[Optional["KnowledgeItem"]] = relationship(back_populates="audit_events")
    business: Mapped["Business"] = relationship(back_populates="knowledge_audit_events")


__all__ = [
    "KnowledgeAuditEvent",
    "KnowledgeChunk",
    "KnowledgeCollection",
    "KnowledgeCollectionLink",
    "KnowledgeCollectionVisibility",
    "KnowledgeEmbedding",
    "KnowledgeIngestionJob",
    "KnowledgeIngestionJobStatus",
    "KnowledgeIngestionJobType",
    "KnowledgeParseResult",
    "KnowledgePermission",
    "KnowledgePermissionScope",
    "KnowledgePermissionType",
]
