"""API-layer schemas for Customer 360 endpoints."""

from __future__ import annotations

from datetime import datetime, date
from typing import Literal, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(token.capitalize() for token in tail)


class CamelModel(BaseModel):
    """Base model that maps snake_case fields to camelCase aliases."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


class CustomerListItem(CamelModel):
    id: UUID
    full_name: str
    primary_email: EmailStr | None = None
    conversations_count: int
    satisfaction_score: float | None = Field(default=None, ge=0, le=100)
    last_contact_at: datetime | None = None
    lifecycle_stage: str


class CustomerStats(CamelModel):
    conversations_total: int
    conversations_last_30_days: int
    csat_average: float | None = Field(default=None, ge=0, le=100)
    csat_trend: float | None = Field(default=None, ge=-100, le=100)
    expansion_opportunities: int


class CustomerContactMethod(CamelModel):
    type: Literal["email", "phone", "social", "messenger", "other"]
    value: str = Field(min_length=2, max_length=255)
    is_primary: bool = False


class CustomerTag(CamelModel):
    id: UUID
    label: str
    color: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{6}$")


class CustomerNote(CamelModel):
    id: UUID
    customer_id: UUID
    author_user_id: UUID | None = None
    author_agent_id: UUID | None = None
    visibility: Literal["internal", "shared"]
    body: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


class CustomerActivityEvent(CamelModel):
    id: UUID
    customer_id: UUID
    event_type: str
    occurred_at: datetime
    actor_user_id: UUID | None = None
    actor_agent_id: UUID | None = None
    actor_customer_id: UUID | None = None
    case_id: UUID | None = None
    conversation_id: UUID | None = None
    details: dict | None = None


class CustomerCaseLink(CamelModel):
    id: UUID
    title: str
    status: str
    priority: str
    opened_at: datetime


class CustomerDetail(CamelModel):
    id: UUID
    business_id: UUID
    full_name: str
    primary_email: EmailStr | None = None
    primary_phone: str | None = None
    country: str | None = None
    lifecycle_stage: str
    satisfaction_score: float | None = Field(default=None, ge=0, le=100)
    persona_tags: list[str]
    last_contact_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    stats: CustomerStats
    contacts: list[CustomerContactMethod]
    tags: list[CustomerTag]
    cases_open: list[CustomerCaseLink]
    cases_resolved: list[CustomerCaseLink]
    notes: list[CustomerNote]
    activity: list[CustomerActivityEvent]


class CustomersListResponse(CamelModel):
    items: list[CustomerListItem]
    total: int
    has_next: bool
    next_cursor: str | None = None


class CustomerDetailResponse(CamelModel):
    customer: CustomerDetail


class CustomerActivityListResponse(CamelModel):
    items: list[CustomerActivityEvent]
    total: int
    has_next: bool
    next_cursor: str | None = None


class CustomerNoteListResponse(CamelModel):
    items: list[CustomerNote]
    total: int
    has_next: bool
    next_cursor: str | None = None


class CustomersListQuery(CamelModel):
    search: str | None = Field(default=None, max_length=120)
    lifecycle_stage: str | None = None
    tags: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=120)


class CustomerCreateRequest(CamelModel):
    full_name: str = Field(min_length=1, max_length=160)
    primary_email: EmailStr | None = None
    primary_phone: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    lifecycle_stage: str
    satisfaction_score: float | None = Field(default=None, ge=0, le=100)
    persona_tags: list[str] = Field(default_factory=list, max_length=20)
    contact_methods: list[CustomerContactMethod] = Field(default_factory=list, max_length=10)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=30)


class CustomerUpdateRequest(CamelModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    primary_email: EmailStr | None = None
    primary_phone: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    lifecycle_stage: str | None = None
    satisfaction_score: float | None = Field(default=None, ge=0, le=100)
    persona_tags: list[str] | None = Field(default=None, max_length=20)
    contact_methods: list[CustomerContactMethod] | None = Field(default=None, max_length=10)
    tag_ids: list[UUID] | None = Field(default=None, max_length=30)


class CustomerNoteCreateRequest(CamelModel):
    body: str = Field(min_length=1, max_length=4000)
    visibility: Literal["internal", "shared"]
    pinned: bool = False


class CustomerNoteUpdateRequest(CamelModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    visibility: Literal["internal", "shared"] | None = None
    pinned: bool | None = None


class CustomerImportRow(CamelModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    lifecycle_stage: str | None = None
    tags: list[str] = Field(default_factory=list)


class CustomerImportRequest(CamelModel):
    rows: list[CustomerImportRow] = Field(min_length=1, max_length=1000)
    skip_duplicates: bool = True


class CustomerImportResult(CamelModel):
    imported_count: int
    skipped_count: int
    errors: list[str]


class CustomerTagCreateRequest(CamelModel):
    label: str = Field(min_length=1, max_length=64)
    color: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{6}$")


class CustomerTagAssignmentRequest(CamelModel):
    tag_ids: Sequence[UUID]


class CustomerTagsResponse(CamelModel):
    items: list[CustomerTag]


__all__ = [
    "CamelModel",
    "CustomerActivityEvent",
    "CustomerActivityListResponse",
    "CustomerCaseLink",
    "CustomerContactMethod",
    "CustomerCreateRequest",
    "CustomerDetail",
    "CustomerDetailResponse",
    "CustomerImportRequest",
    "CustomerImportResult",
    "CustomerImportRow",
    "CustomerListItem",
    "CustomerNote",
    "CustomerNoteCreateRequest",
    "CustomerNoteListResponse",
    "CustomerNoteUpdateRequest",
    "CustomerStats",
    "CustomerTag",
    "CustomerTagAssignmentRequest",
    "CustomerTagCreateRequest",
    "CustomerTagsResponse",
    "CustomerUpdateRequest",
    "CustomersListQuery",
    "CustomersListResponse",
]
