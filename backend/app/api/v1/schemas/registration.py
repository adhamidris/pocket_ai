"""API-layer schemas for registration endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(token.capitalize() for token in tail)


class CamelModel(BaseModel):
    """Base model that uses camelCase aliases."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


class StartRegistrationRequest(CamelModel):
    first_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8, max_length=256)


class StartRegistrationUser(CamelModel):
    id: UUID
    email: EmailStr
    first_name: str


class StartRegistrationResponse(CamelModel):
    registration_id: UUID
    user: StartRegistrationUser
    next_step: str
    session: SessionProgress | None = None


class BusinessProfileRequest(CamelModel):
    business_name: str = Field(min_length=2, max_length=120)
    industry: str = Field(min_length=2, max_length=120)
    specify_industry: str | None = Field(default=None, max_length=120)
    line_of_business: list[str] = Field(default_factory=list, max_length=10)
    line_of_business_custom: list[str] = Field(default_factory=list, max_length=10)
    country: str | None = Field(default=None, max_length=120)
    website: HttpUrl | None = None


class BusinessSummary(CamelModel):
    id: UUID
    name: str
    industry_code: str


class SessionProgress(CamelModel):
    id: UUID
    current_step: str
    steps_completed: int
    total_steps: int


class BusinessProfileResponse(CamelModel):
    business: BusinessSummary
    niches: list[str]
    session: SessionProgress


class AgentConfigRequest(CamelModel):
    agent_name: str | None = Field(default=None, max_length=80)
    agent_title: str | None = Field(default=None, max_length=120)
    agent_tone: str | None = Field(default=None, max_length=120)
    agent_traits: list[str] = Field(default_factory=list, max_length=6)
    agent_escalation: str | None = Field(default=None, max_length=120)


class AgentSummary(CamelModel):
    id: UUID
    name: str
    role: str
    tone: str
    traits: list[str]
    escalation_rule: str


class AgentConfigResponse(CamelModel):
    agent: AgentSummary | None
    session: SessionProgress


class UploadLinksModel(CamelModel):
    links: dict[str, list[HttpUrl]]
    language: str | None = Field(default=None, max_length=16)


class UploadLinksResponse(CamelModel):
    created: dict[str, int]
    duplicates: int
    session: SessionProgress


class CompletionResponse(CamelModel):
    session: SessionProgress
    progress: dict[str, object]
