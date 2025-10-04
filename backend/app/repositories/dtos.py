"""Domain-safe DTOs emitted from repository functions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.registration import (
    AgentRole,
    AgentTone,
    AgentTrait,
    EscalationRule,
    KnowledgeSource,
    KnowledgeStatus,
    MembershipRole,
    RegistrationStep,
)


@dataclass(slots=True, frozen=True)
class RegistrationSessionRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    business_id: uuid.UUID | None
    current_step: RegistrationStep
    state: dict[str, Any] | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    steps_completed: int
    total_steps: int


@dataclass(slots=True, frozen=True)
class UserRecord:
    id: uuid.UUID
    email: str
    first_name: str
    password_hash: str | None
    auth_provider: str
    email_verified: bool
    created_at: datetime


@dataclass(slots=True, frozen=True)
class BusinessRecord:
    id: uuid.UUID
    name: str
    industry_code: str
    created_by_user_id: uuid.UUID
    created_by_user_name: str
    created_at: datetime


@dataclass(slots=True, frozen=True)
class BusinessNicheRecord:
    business_id: uuid.UUID
    niche_code: str
    added_at: datetime


@dataclass(slots=True, frozen=True)
class MembershipRecord:
    user_id: uuid.UUID
    business_id: uuid.UUID
    role: MembershipRole
    joined_at: datetime


@dataclass(slots=True, frozen=True)
class AgentRecord:
    id: uuid.UUID
    business_id: uuid.UUID
    name: str
    role: AgentRole
    tone: AgentTone
    escalation_rule: EscalationRule
    created_by_user_id: uuid.UUID
    created_by_user_name: str
    created_at: datetime


@dataclass(slots=True, frozen=True)
class AgentTraitRecord:
    agent_id: uuid.UUID
    trait_code: AgentTrait
    added_at: datetime


@dataclass(slots=True, frozen=True)
class AgentWithTraitsRecord:
    agent: AgentRecord
    traits: list[AgentTraitRecord]


@dataclass(slots=True, frozen=True)
class KnowledgeItemRecord:
    id: uuid.UUID
    business_id: uuid.UUID
    source_type: KnowledgeSource
    status: KnowledgeStatus
    display_name: str | None
    language: str | None
    created_by_user_id: uuid.UUID
    created_by_user_name: str
    created_at: datetime


@dataclass(slots=True, frozen=True)
class KnowledgeItemFileRecord:
    knowledge_item_id: uuid.UUID
    filename: str
    content_type: str | None
    storage_path: str
    size_bytes: int
    checksum_sha256: str | None


@dataclass(slots=True, frozen=True)
class KnowledgeItemUrlRecord:
    knowledge_item_id: uuid.UUID
    url: str


@dataclass(slots=True, frozen=True)
class KnowledgeItemTextRecord:
    knowledge_item_id: uuid.UUID
    text_content: str


@dataclass(slots=True, frozen=True)
class KnowledgeItemWithDetailRecord:
    item: KnowledgeItemRecord
    file: KnowledgeItemFileRecord | None
    url: KnowledgeItemUrlRecord | None
    text: KnowledgeItemTextRecord | None


__all__ = [
    "AgentRecord",
    "AgentTraitRecord",
    "AgentWithTraitsRecord",
    "BusinessNicheRecord",
    "BusinessRecord",
    "KnowledgeItemFileRecord",
    "KnowledgeItemRecord",
    "KnowledgeItemTextRecord",
    "KnowledgeItemUrlRecord",
    "KnowledgeItemWithDetailRecord",
    "MembershipRecord",
    "RegistrationSessionRecord",
    "UserRecord",
]

