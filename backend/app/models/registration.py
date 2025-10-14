"""Database models for the registration flow domain."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from typing import TYPE_CHECKING, Optional

from app.models.base import Base, CreatedAtMixin, PrimaryKeyMixin, UpdatedAtMixin

if TYPE_CHECKING:
    from .agents_runtime import (
        AgentHourlyStatus,
        AgentKnowledgeAccess,
        AgentKpi,
        AgentMetricDaily,
        AgentPublicLinkToken,
        AgentRuntimeProfile,
        AgentSession,
        AgentStatusLog,
    )
    from .analytics import (
        CaseFact,
        ConversationFact,
        CustomerMetricDaily as BusinessCustomerMetricDaily,
        MetricRefreshJob,
    )
    from .automation import AutomationRule, AutomationRun
    from .business_config import (
        BusinessIntegration,
        BusinessIntegrationKey,
        BusinessSettings,
        TeamInvite,
        TeamRolePermission,
    )
    from .cases import (
        Case,
        CaseAssignment,
        CaseDocument,
        CaseFollowUp,
        CaseHistoryEvent,
        CaseMetricDaily,
        CaseNote,
        CaseSuggestedAction,
        CaseSlaTracker,
        Escalation,
        SlaBreach,
    )
    from .conversations import ChatVisitor, Conversation, ConversationMetricDaily
    from .customers import (
        Customer,
        CustomerActivityEvent,
        CustomerConversationStats,
        CustomerHealthScore,
        CustomerMetricSnapshot,
        CustomerNote,
        CustomerTag,
    )
    from .knowledge_extras import (
        KnowledgeAuditEvent,
        KnowledgeChunk,
        KnowledgeCollection,
        KnowledgeCollectionLink,
        KnowledgeEmbedding,
        KnowledgeIngestionJob,
        KnowledgeParseResult,
        KnowledgePermission,
    )
    from .security import (
        AuditLog,
        DataRetentionJob,
        DataSubjectRequest,
        EventBusOutbox,
    )
    from .storage import StorageAsset


class RegistrationStep(enum.Enum):
    BUSINESS_PROFILE = "business_profile"
    AGENT_SETUP = "agent_setup"
    KNOWLEDGE_UPLOADS = "knowledge_uploads"
    COMPLETED = "completed"


class MembershipRole(enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    AGENT = "agent"


class AgentRole(enum.Enum):
    SALES = "sales"
    SUPPORT = "support"
    RESEARCH = "research"
    SUCCESS = "success"
    MARKETING = "marketing"


class AgentTone(enum.Enum):
    FRIENDLY = "friendly"
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    FORMAL = "formal"
    EMPATHETIC = "empathetic"
    PLAYFUL = "playful"


class AgentTrait(enum.Enum):
    CONCISE = "concise"
    DETAILED = "detailed"
    CURIOUS = "curious"
    PATIENT = "patient"
    PROACTIVE = "proactive"
    DIRECT = "direct"
    CREATIVE = "creative"


class AgentStatus(enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


class EscalationRule(enum.Enum):
    NEVER = "never"
    ON_FALLBACK = "on_fallback"
    ON_NEGATIVE_SENTIMENT = "on_negative_sentiment"
    ON_HIGH_VALUE = "on_high_value"
    ALWAYS = "always"


class KnowledgeSource(enum.Enum):
    FILE = "file"
    URL = "url"
    TEXT = "text"


class KnowledgeStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class User(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Text, nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="password"
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(first_name) BETWEEN 1 AND 80",
            name="ck_users_first_name_length",
        ),
        CheckConstraint(
            "auth_provider IN ('password','google')",
            name="ck_users_auth_provider_allowed",
        ),
    )

    registration_sessions: Mapped[list["RegistrationSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    businesses: Mapped[list["Business"]] = relationship(back_populates="creator")
    memberships: Mapped[list["UserBusinessMembership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    created_agents: Mapped[list["Agent"]] = relationship(back_populates="created_by")
    created_knowledge_items: Mapped[list["KnowledgeItem"]] = relationship(
        back_populates="created_by"
    )


class Industry(CreatedAtMixin, Base):
    __tablename__ = "industries"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    search_terms: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default=text("ARRAY[]::text[]")
    )

    niches: Mapped[list["IndustryNiche"]] = relationship(
        back_populates="industry", cascade="all, delete-orphan"
    )
    businesses: Mapped[list["Business"]] = relationship("Business", back_populates="industry")

    __table_args__ = (
        CheckConstraint(
            "code ~ '^industry:[a-z0-9-]{2,50}$'",
            name="ck_industries_code_format",
        ),
        CheckConstraint(
            "char_length(label) BETWEEN 2 AND 120",
            name="ck_industries_label_length",
        ),
    )


class IndustryNiche(CreatedAtMixin, Base):
    __tablename__ = "industry_niches"

    code: Mapped[str] = mapped_column(String(96), primary_key=True)
    industry_code: Mapped[str] = mapped_column(
        ForeignKey("industries.code", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    search_terms: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default=text("ARRAY[]::text[]")
    )

    industry: Mapped[Industry] = relationship(back_populates="niches")
    business_links: Mapped[list["BusinessNiche"]] = relationship(
        back_populates="niche", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "code ~ '^niche:[a-z0-9-]{2,100}$'",
            name="ck_industry_niches_code_format",
        ),
        CheckConstraint(
            "char_length(label) BETWEEN 2 AND 120",
            name="ck_industry_niches_label_length",
        ),
    )


class Business(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "businesses"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    industry_code: Mapped[str] = mapped_column(
        ForeignKey("industries.code", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_name: Mapped[str] = mapped_column(String(80), nullable=False)

    industry: Mapped["Industry"] = relationship("Industry", back_populates="businesses")
    creator: Mapped[User] = relationship(back_populates="businesses")
    niches: Mapped[list["BusinessNiche"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["UserBusinessMembership"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    agents: Mapped[list["Agent"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    knowledge_items: Mapped[list["KnowledgeItem"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    registration_sessions: Mapped[list["RegistrationSession"]] = relationship(
        back_populates="business"
    )
    settings: Mapped[Optional["BusinessSettings"]] = relationship(
        back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    integrations: Mapped[list["BusinessIntegration"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    invites: Mapped[list["TeamInvite"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    role_permissions: Mapped[list["TeamRolePermission"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    customers: Mapped[list["Customer"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    customer_tags: Mapped[list["CustomerTag"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    customer_activity_events: Mapped[list["CustomerActivityEvent"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    customer_health_scores: Mapped[list["CustomerHealthScore"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    customer_metrics: Mapped[list["BusinessCustomerMetricDaily"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    chat_visitors: Mapped[list["ChatVisitor"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    conversation_metrics: Mapped[list["ConversationMetricDaily"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    cases: Mapped[list["Case"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    case_metrics: Mapped[list["CaseMetricDaily"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    agent_metrics: Mapped[list["AgentMetricDaily"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    storage_assets: Mapped[list["StorageAsset"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    knowledge_collections: Mapped[list["KnowledgeCollection"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    knowledge_audit_events: Mapped[list["KnowledgeAuditEvent"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    automation_rules: Mapped[list["AutomationRule"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    automation_runs: Mapped[list["AutomationRun"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    metric_refresh_jobs: Mapped[list["MetricRefreshJob"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    conversation_facts: Mapped[list["ConversationFact"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    case_facts: Mapped[list["CaseFact"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    data_subject_requests: Mapped[list["DataSubjectRequest"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    data_retention_jobs: Mapped[list["DataRetentionJob"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    event_bus_outbox_entries: Mapped[list["EventBusOutbox"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 2 AND 120",
            name="ck_businesses_name_length",
        ),
        CheckConstraint(
            "industry_code ~ '^industry:[a-z0-9-]{2,50}$'",
            name="ck_businesses_industry_code_format",
        ),
    )


class BusinessNiche(Base):
    __tablename__ = "business_niches"

    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    niche_code: Mapped[str] = mapped_column(
        ForeignKey("industry_niches.code", ondelete="RESTRICT"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    business: Mapped[Business] = relationship(back_populates="niches")
    niche: Mapped[IndustryNiche] = relationship(back_populates="business_links")

    __table_args__ = (
        CheckConstraint(
            "niche_code ~ '^niche:[a-z0-9-]{2,50}$'",
            name="ck_business_niches_code_format",
        ),
    )


class RegistrationSession(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "registration_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True
    )
    current_step: Mapped[RegistrationStep] = mapped_column(
        Enum(RegistrationStep, name="registration_step_enum", create_type=False),
        nullable=False,
    )
    state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("now() + interval '7 days'")
    )
    steps_completed: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    total_steps: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("4")
    )

    user: Mapped[User] = relationship(back_populates="registration_sessions")
    business: Mapped[Business | None] = relationship(back_populates="registration_sessions")

    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_registration_sessions_expiry_after_created",
        ),
        CheckConstraint(
            "steps_completed BETWEEN 0 AND total_steps",
            name="ck_registration_sessions_steps_range",
        ),
    )


class UserBusinessMembership(Base):
    __tablename__ = "user_business_memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role_enum", create_type=False),
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    business: Mapped[Business] = relationship(back_populates="memberships")


class Agent(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agents"

    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[AgentRole] = mapped_column(
        Enum(AgentRole, name="agent_role_enum", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    tone: Mapped[AgentTone] = mapped_column(
        Enum(AgentTone, name="agent_tone_enum", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    escalation_rule: Mapped[EscalationRule] = mapped_column(
        Enum(EscalationRule, name="escalation_rule_enum", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        server_default=AgentStatus.DRAFT.value,
    )
    public_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    default_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    welcome_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_link_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_name: Mapped[str] = mapped_column(String(80), nullable=False)

    business: Mapped[Business] = relationship(back_populates="agents")
    created_by: Mapped[User] = relationship(back_populates="created_agents")
    traits: Mapped[list["AgentTraitLink"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    kpis: Mapped[list["AgentKpi"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    knowledge_access: Mapped[list["AgentKnowledgeAccess"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    status_logs: Mapped[list["AgentStatusLog"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        foreign_keys="AgentStatusLog.agent_id",
    )
    hourly_statuses: Mapped[list["AgentHourlyStatus"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    public_link_tokens: Mapped[list["AgentPublicLinkToken"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    runtime_profiles: Mapped[list["AgentRuntimeProfile"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["AgentSession"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    metric_snapshots: Mapped[list["AgentMetricDaily"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 2 AND 80",
            name="ck_agents_name_length",
        ),
        CheckConstraint(
            "public_slug IS NULL OR char_length(public_slug) BETWEEN 3 AND 120",
            name="ck_agents_public_slug_length",
        ),
        CheckConstraint(
            "default_language IS NULL OR char_length(default_language) BETWEEN 2 AND 16",
            name="ck_agents_default_language_length",
        ),
        UniqueConstraint(
            "business_id", "public_slug", name="uq_agents_business_public_slug"
        ),
    )


class AgentTraitLink(Base):
    __tablename__ = "agent_traits"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    trait_code: Mapped[AgentTrait] = mapped_column(
        Enum(AgentTrait, name="agent_trait_enum", create_type=False),
        primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    agent: Mapped[Agent] = relationship(back_populates="traits")


class KnowledgeItem(PrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_items"

    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[KnowledgeSource] = mapped_column(
        Enum(KnowledgeSource, name="knowledge_source_enum", create_type=False),
        nullable=False,
    )
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        server_default=KnowledgeStatus.PENDING.value,
    )
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_uid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_name: Mapped[str] = mapped_column(String(80), nullable=False)

    business: Mapped[Business] = relationship(back_populates="knowledge_items")
    created_by: Mapped[User] = relationship(back_populates="created_knowledge_items")
    file: Mapped[KnowledgeItemFile | None] = relationship(
        back_populates="knowledge_item", uselist=False, cascade="all, delete-orphan"
    )
    url: Mapped[KnowledgeItemUrl | None] = relationship(
        back_populates="knowledge_item", uselist=False, cascade="all, delete-orphan"
    )
    text_content: Mapped[KnowledgeItemText | None] = relationship(
        back_populates="knowledge_item", uselist=False, cascade="all, delete-orphan"
    )
    ingestion_jobs: Mapped[list["KnowledgeIngestionJob"]] = relationship(
        back_populates="knowledge_item", cascade="all, delete-orphan"
    )
    parse_result: Mapped[Optional["KnowledgeParseResult"]] = relationship(
        back_populates="knowledge_item", uselist=False, cascade="all, delete-orphan"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="knowledge_item", cascade="all, delete-orphan"
    )
    permissions: Mapped[list["KnowledgePermission"]] = relationship(
        back_populates="knowledge_item", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["KnowledgeAuditEvent"]] = relationship(
        back_populates="knowledge_item", cascade="all, delete-orphan"
    )
    collection_links: Mapped[list["KnowledgeCollectionLink"]] = relationship(
        back_populates="knowledge_item", cascade="all, delete-orphan"
    )
    agent_access: Mapped[list["AgentKnowledgeAccess"]] = relationship(
        back_populates="knowledge_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "display_name IS NULL OR char_length(display_name) <= 120",
            name="ck_knowledge_items_display_name_length",
        ),
        CheckConstraint(
            "language IS NULL OR char_length(language) BETWEEN 2 AND 32",
            name="ck_knowledge_items_language_length",
        ),
        UniqueConstraint(
            "business_id", "source_uid", name="uq_knowledge_items_source_uid"
        ),
    )


class KnowledgeItemFile(Base):
    __tablename__ = "knowledge_item_files"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)

    knowledge_item: Mapped[KnowledgeItem] = relationship(back_populates="file")

    __table_args__ = (
        CheckConstraint(
            "size_bytes BETWEEN 1 AND 20971520",
            name="ck_knowledge_item_files_size_range",
        ),
        CheckConstraint(
            "content_type IS NULL OR lower(content_type) = ANY (ARRAY['application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document','application/vnd.ms-excel','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.google-apps.document','application/vnd.google-apps.spreadsheet'])",
            name="ck_knowledge_item_files_content_type_allowed",
        ),
    )


class KnowledgeItemUrl(Base):
    __tablename__ = "knowledge_item_urls"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)

    knowledge_item: Mapped[KnowledgeItem] = relationship(back_populates="url")

    __table_args__ = (
        CheckConstraint(
            "url LIKE 'https://%'",
            name="ck_knowledge_item_urls_https_only",
        ),
    )


class KnowledgeItemText(Base):
    __tablename__ = "knowledge_item_texts"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    text_content: Mapped[str] = mapped_column(Text, nullable=False)

    knowledge_item: Mapped[KnowledgeItem] = relationship(back_populates="text_content")

    __table_args__ = (
        CheckConstraint(
            "char_length(text_content) BETWEEN 1 AND 200000",
            name="ck_knowledge_item_texts_length",
        ),
    )


Index("uq_users_email_lower", func.lower(User.email), unique=True)
Index("ix_industry_niches_industry_code", IndustryNiche.industry_code)
Index("ix_businesses_industry_code", Business.industry_code)
Index("ix_business_niches_business_id", BusinessNiche.business_id)
Index("ix_registration_sessions_user_id", RegistrationSession.user_id)
Index("ix_user_business_memberships_business_role", UserBusinessMembership.business_id, UserBusinessMembership.role)
Index("ix_agents_business_id", Agent.business_id)
Index("ix_knowledge_items_business_status", KnowledgeItem.business_id, KnowledgeItem.status)

__all__ = [
    "Agent",
    "AgentStatus",
    "AgentRole",
    "AgentTone",
    "AgentTrait",
    "AgentTraitLink",
    "Business",
    "BusinessNiche",
    "Industry",
    "IndustryNiche",
    "KnowledgeItem",
    "KnowledgeItemFile",
    "KnowledgeItemText",
    "KnowledgeItemUrl",
    "KnowledgeSource",
    "KnowledgeStatus",
    "MembershipRole",
    "RegistrationSession",
    "RegistrationStep",
    "User",
    "UserBusinessMembership",
]
