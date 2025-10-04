"""Pydantic schemas for the multi-step registration flow."""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator


RegistrationId = UUID
BusinessId = UUID
UserId = UUID
AgentId = UUID
KnowledgeItemId = UUID

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
INDUSTRY_CODE_PATTERN = re.compile(r"^industry:[a-z0-9-]{2,50}$")
NICHE_CODE_PATTERN = re.compile(r"^niche:[a-z0-9-]{2,50}$")
LANGUAGE_CODE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")


class BaseSchemaModel(BaseModel):
    """Base class that forbids unexpected properties."""

    model_config = ConfigDict(extra="forbid")


class IdempotentRequest(BaseSchemaModel):
    """Mixin to surface an optional idempotency key."""

    idempotency_key: Annotated[str | None, Field(
        default=None, max_length=64, pattern=IDEMPOTENCY_KEY_PATTERN.pattern
    )]

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
            msg = "idempotency_key must match ^[A-Za-z0-9_.:-]{1,64}$"
            raise ValueError(msg)
        return value


class RegistrationStep(Enum):
    BUSINESS_PROFILE = "business_profile"
    AGENT_SETUP = "agent_setup"
    KNOWLEDGE_UPLOADS = "knowledge_uploads"


class RegisterEmailRequest(IdempotentRequest):
    first_name: Annotated[str, Field(min_length=1, max_length=80)]
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=128)]
    password_confirmation: Annotated[str, Field(min_length=8, max_length=128)]
    accept_terms: bool

    @model_validator(mode="after")
    def _passwords_match(self) -> "RegisterEmailRequest":
        if self.password != self.password_confirmation:
            raise ValueError("password_confirmation must match password")
        return self


class OAuthProvider(Enum):
    GOOGLE = "google"


class RegisterOAuthRequest(IdempotentRequest):
    provider: Literal[OAuthProvider.GOOGLE.value]
    id_token: Annotated[str, Field(min_length=10, max_length=4096)]
    first_name: Annotated[str | None, Field(default=None, min_length=1, max_length=80)]


class RegisterStep1Response(BaseSchemaModel):
    registration_id: RegistrationId
    user_id: UserId
    requires_email_verification: bool
    next_step: Literal[RegistrationStep.BUSINESS_PROFILE.value]


class BusinessProfileRequest(IdempotentRequest):
    registration_id: RegistrationId
    business_name: Annotated[str, Field(min_length=2, max_length=120)]
    industry_code: str
    niche_codes: Annotated[list[str] | None, Field(default=None, min_length=1, max_length=5)]

    @field_validator("industry_code")
    @classmethod
    def _validate_industry_code(cls, value: str) -> str:
        if not INDUSTRY_CODE_PATTERN.fullmatch(value):
            raise ValueError("industry_code must match industry:[a-z0-9-]{2,50}")
        return value

    @field_validator("niche_codes")
    @classmethod
    def _validate_niche_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for code in value:
            if not NICHE_CODE_PATTERN.fullmatch(code):
                raise ValueError("niche_codes entries must match niche:[a-z0-9-]{2,50}")
        return value


class BusinessProfileResponse(BaseSchemaModel):
    business_id: BusinessId
    next_step: Literal[RegistrationStep.AGENT_SETUP.value]


class AgentRole(str, Enum):
    SALES = "sales"
    SUPPORT = "support"
    RESEARCH = "research"
    SUCCESS = "success"
    MARKETING = "marketing"


class AgentTone(str, Enum):
    FRIENDLY = "friendly"
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    FORMAL = "formal"
    EMPATHETIC = "empathetic"
    PLAYFUL = "playful"


class AgentTrait(str, Enum):
    CONCISE = "concise"
    DETAILED = "detailed"
    CURIOUS = "curious"
    PATIENT = "patient"
    PROACTIVE = "proactive"
    DIRECT = "direct"
    CREATIVE = "creative"


class EscalationRule(str, Enum):
    NEVER = "never"
    ON_FALLBACK = "on_fallback"
    ON_NEGATIVE_SENTIMENT = "on_negative_sentiment"
    ON_HIGH_VALUE = "on_high_value"
    ALWAYS = "always"


class AgentSetupRequest(IdempotentRequest):
    registration_id: RegistrationId
    business_id: BusinessId
    agent_name: Annotated[str, Field(min_length=2, max_length=80)]
    role: AgentRole
    tone: AgentTone
    traits: Annotated[list[AgentTrait], Field(default_factory=list, max_length=6)]
    escalation_rule: EscalationRule

    @model_validator(mode="after")
    def _ensure_unique_traits(self) -> "AgentSetupRequest":
        if len(self.traits) != len(set(self.traits)):
            raise ValueError("traits must contain unique values")
        return self


class AgentSetupResponse(BaseSchemaModel):
    agent_id: AgentId
    next_step: Literal[RegistrationStep.KNOWLEDGE_UPLOADS.value]


class FieldInputType(str, Enum):
    FILE = "file"
    STRING = "string"
    URL = "url"
    TEXT = "text"
    SELECT = "select"


class FieldDescriptor(BaseSchemaModel):
    key: Annotated[str, Field(min_length=1, max_length=64)]
    type: FieldInputType
    required: bool
    constraints: dict[str, Any] | None = None


class KnowledgeSourceType(str, Enum):
    FILE = "file"
    URL = "url"
    TEXT = "text"


class KnowledgeSourceDefinition(BaseSchemaModel):
    source_type: KnowledgeSourceType
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    accepts: Sequence[FieldDescriptor]


class KnowledgeSourceCatalogResponse(BaseSchemaModel):
    sources: Sequence[KnowledgeSourceDefinition]


class KnowledgeUploadStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class KnowledgeUploadBase(IdempotentRequest):
    registration_id: RegistrationId
    business_id: BusinessId
    display_name: Annotated[str | None, Field(default=None, min_length=1, max_length=120)]
    language: Annotated[str | None, Field(default=None, pattern=LANGUAGE_CODE_PATTERN.pattern)]


class KnowledgeUploadFileRequest(KnowledgeUploadBase):
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    content_type: Annotated[str | None, Field(default=None, max_length=100)]


class KnowledgeUploadFromUrlRequest(KnowledgeUploadBase):
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def _require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("url must use https scheme")
        return value


class KnowledgeUploadFromTextRequest(KnowledgeUploadBase):
    text: Annotated[str, Field(min_length=1, max_length=200_000)]


class KnowledgeItemResponse(BaseSchemaModel):
    knowledge_item_id: KnowledgeItemId
    source_type: KnowledgeSourceType
    status: KnowledgeUploadStatus
    created_at: Annotated[str, Field(description="ISO8601 UTC timestamp")]


class IndustryItem(BaseSchemaModel):
    code: str
    label: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        if not INDUSTRY_CODE_PATTERN.fullmatch(value):
            raise ValueError("code must match industry:[a-z0-9-]{2,50}")
        return value


class ListIndustriesResponse(BaseSchemaModel):
    items: Sequence[IndustryItem]


class ListNichesQuery(BaseSchemaModel):
    industry_code: str

    @field_validator("industry_code")
    @classmethod
    def _validate_industry(cls, value: str) -> str:
        if not INDUSTRY_CODE_PATTERN.fullmatch(value):
            raise ValueError("industry_code must match industry:[a-z0-9-]{2,50}")
        return value


class NicheItem(BaseSchemaModel):
    code: str
    label: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        if not NICHE_CODE_PATTERN.fullmatch(value):
            raise ValueError("code must match niche:[a-z0-9-]{2,50}")
        return value


class ListNichesResponse(BaseSchemaModel):
    items: Sequence[NicheItem]


class ListAgentChoicesResponse(BaseSchemaModel):
    roles: Sequence[AgentRole]
    tones: Sequence[AgentTone]
    traits: Sequence[AgentTrait]
    escalation_rules: Sequence[EscalationRule]


class ApiErrorResponse(BaseSchemaModel):
    code: Annotated[str, Field(min_length=1, max_length=64)]
    message: Annotated[str, Field(min_length=1, max_length=256)]
    details: dict[str, Any] | None = None


__all__ = [
    "AgentId",
    "AgentRole",
    "AgentSetupRequest",
    "AgentSetupResponse",
    "AgentTone",
    "AgentTrait",
    "ApiErrorResponse",
    "BusinessId",
    "BusinessProfileRequest",
    "BusinessProfileResponse",
    "FieldDescriptor",
    "FieldInputType",
    "BaseSchemaModel",
    "IdempotentRequest",
    "IndustryItem",
    "KnowledgeItemId",
    "KnowledgeItemResponse",
    "KnowledgeSourceCatalogResponse",
    "KnowledgeSourceDefinition",
    "KnowledgeSourceType",
    "KnowledgeUploadBase",
    "KnowledgeUploadFileRequest",
    "KnowledgeUploadFromTextRequest",
    "KnowledgeUploadFromUrlRequest",
    "KnowledgeUploadStatus",
    "ListAgentChoicesResponse",
    "ListIndustriesResponse",
    "ListNichesQuery",
    "ListNichesResponse",
    "NicheItem",
    "OAuthProvider",
    "RegistrationId",
    "RegistrationStep",
    "RegisterEmailRequest",
    "RegisterOAuthRequest",
    "RegisterStep1Response",
    "UserId",
]
