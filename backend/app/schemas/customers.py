"""Pydantic schemas for Customer 360 domain."""

from __future__ import annotations

from datetime import datetime, date
from typing import Annotated, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.cases import CasePriority, CaseStatus
from app.models.customers import (
    CustomerActivityEventType,
    CustomerLifecycleStage,
    CustomerNoteVisibility,
    CustomerContactMethodType,
)


BusinessId = UUID
CustomerId = UUID
CustomerTagId = UUID
CaseId = UUID
ConversationId = UUID
UserId = UUID
AgentId = UUID


class BaseSchemaModel(BaseModel):
    """Base Pydantic base enforcing no extra attributes."""

    model_config = ConfigDict(extra="forbid")


class PaginatedResponse(BaseSchemaModel):
    """Common pagination envelope."""

    total: int
    items: Sequence[BaseModel]


class CustomerListItem(BaseSchemaModel):
    id: CustomerId
    full_name: Annotated[str, Field(min_length=1, max_length=160)]
    primary_email: Annotated[EmailStr | None, Field(default=None)]
    conversations_count: int
    satisfaction_score: Annotated[float | None, Field(default=None, ge=0, le=100)]
    last_contact_at: datetime | None
    lifecycle_stage: CustomerLifecycleStage


class CustomerStats(BaseSchemaModel):
    conversations_total: int
    conversations_last_30_days: int
    csat_average: Annotated[float | None, Field(default=None, ge=0, le=100)]
    csat_trend: Annotated[float | None, Field(default=None, ge=-100, le=100)]
    expansion_opportunities: int


class CustomerContactMethod(BaseSchemaModel):
    type: CustomerContactMethodType
    value: Annotated[str, Field(min_length=2, max_length=255)]
    is_primary: bool


class CustomerTag(BaseSchemaModel):
    id: CustomerTagId
    label: Annotated[str, Field(min_length=1, max_length=64)]
    color: Annotated[str | None, Field(default=None, pattern=r"^[A-Fa-f0-9]{6}$")]


class CustomerNote(BaseSchemaModel):
    id: UUID
    customer_id: CustomerId
    author_user_id: UserId | None
    author_agent_id: AgentId | None
    visibility: CustomerNoteVisibility
    body: Annotated[str, Field(min_length=1, max_length=4000)]
    pinned: bool
    created_at: datetime
    updated_at: datetime


class CustomerActivityEvent(BaseSchemaModel):
    id: UUID
    customer_id: CustomerId
    event_type: CustomerActivityEventType
    occurred_at: datetime
    actor_user_id: UserId | None
    actor_agent_id: AgentId | None
    actor_customer_id: CustomerId | None
    case_id: CaseId | None
    conversation_id: ConversationId | None
    details: dict | None


class CustomerCaseLink(BaseSchemaModel):
    id: CaseId
    title: str
    status: CaseStatus
    priority: CasePriority
    opened_at: datetime


class CustomerDetail(BaseSchemaModel):
    id: CustomerId
    business_id: BusinessId
    full_name: Annotated[str, Field(min_length=1, max_length=160)]
    primary_email: Annotated[EmailStr | None, Field(default=None)]
    primary_phone: Annotated[str | None, Field(default=None, max_length=64)]
    country: Annotated[str | None, Field(default=None, min_length=2, max_length=2)]
    lifecycle_stage: CustomerLifecycleStage
    satisfaction_score: Annotated[float | None, Field(default=None, ge=0, le=100)]
    persona_tags: list[str]
    last_contact_at: datetime | None
    created_at: datetime
    updated_at: datetime
    stats: CustomerStats
    contacts: Sequence[CustomerContactMethod]
    tags: Sequence[CustomerTag]
    cases_open: Sequence[CustomerCaseLink]
    cases_resolved: Sequence[CustomerCaseLink]
    notes: Sequence[CustomerNote]
    activity: Sequence[CustomerActivityEvent]


class CustomerCreateRequest(BaseSchemaModel):
    full_name: Annotated[str, Field(min_length=1, max_length=160)]
    primary_email: Annotated[EmailStr | None, Field(default=None)]
    primary_phone: Annotated[str | None, Field(default=None, max_length=64)]
    country: Annotated[str | None, Field(default=None, min_length=2, max_length=2)]
    lifecycle_stage: CustomerLifecycleStage
    satisfaction_score: Annotated[float | None, Field(default=None, ge=0, le=100)]
    persona_tags: Annotated[list[str], Field(default_factory=list, max_length=20)]
    contact_methods: Annotated[list[CustomerContactMethod], Field(default_factory=list, max_length=10)]
    tag_ids: Annotated[list[CustomerTagId], Field(default_factory=list, max_length=30)]


class CustomerUpdateRequest(BaseSchemaModel):
    full_name: Annotated[str | None, Field(default=None, min_length=1, max_length=160)]
    primary_email: Annotated[EmailStr | None, Field(default=None)]
    primary_phone: Annotated[str | None, Field(default=None, max_length=64)]
    country: Annotated[str | None, Field(default=None, min_length=2, max_length=2)]
    lifecycle_stage: CustomerLifecycleStage | None
    satisfaction_score: Annotated[float | None, Field(default=None, ge=0, le=100)]
    persona_tags: Annotated[list[str] | None, Field(default=None, max_length=20)]
    contact_methods: Annotated[list[CustomerContactMethod] | None, Field(default=None, max_length=10)]
    tag_ids: Annotated[list[CustomerTagId] | None, Field(default=None, max_length=30)]


class CustomerNoteCreateRequest(BaseSchemaModel):
    body: Annotated[str, Field(min_length=1, max_length=4000)]
    visibility: CustomerNoteVisibility
    pinned: bool = False


class CustomerNoteUpdateRequest(BaseSchemaModel):
    body: Annotated[str | None, Field(default=None, min_length=1, max_length=4000)]
    visibility: CustomerNoteVisibility | None = None
    pinned: bool | None = None


class CustomerTagCreateRequest(BaseSchemaModel):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    color: Annotated[str | None, Field(default=None, pattern=r"^[A-Fa-f0-9]{6}$")]


class CustomerTagResponse(CustomerTagCreateRequest):
    id: CustomerTagId


class CustomerImportRow(BaseSchemaModel):
    full_name: Annotated[str, Field(min_length=1, max_length=160)]
    email: Annotated[EmailStr | None, Field(default=None)]
    phone: Annotated[str | None, Field(default=None, max_length=64)]
    country: Annotated[str | None, Field(default=None, min_length=2, max_length=2)]
    lifecycle_stage: CustomerLifecycleStage | None = None
    tags: Annotated[list[str], Field(default_factory=list)]


class CustomerImportRequest(BaseSchemaModel):
    rows: Annotated[list[CustomerImportRow], Field(min_length=1, max_length=1000)]
    skip_duplicates: bool = True


class CustomerImportResult(BaseSchemaModel):
    imported_count: int
    skipped_count: int
    errors: list[str]


class CustomersListResponse(BaseSchemaModel):
    items: Sequence[CustomerListItem]
    total: int
    has_next: bool
    next_cursor: str | None


class CustomerDetailResponse(BaseSchemaModel):
    customer: CustomerDetail


class CustomerActivityListResponse(BaseSchemaModel):
    items: Sequence[CustomerActivityEvent]
    total: int
    has_next: bool
    next_cursor: str | None


class CustomerNoteListResponse(BaseSchemaModel):
    items: Sequence[CustomerNote]
    total: int
    has_next: bool
    next_cursor: str | None


class CustomersFilterParams(BaseSchemaModel):
    search: Annotated[str | None, Field(default=None, max_length=120)]
    lifecycle_stage: CustomerLifecycleStage | None = None
    tags: Annotated[list[str] | None, Field(default=None, max_length=10)]
    date_from: date | None = None
    date_to: date | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 25
    cursor: Annotated[str | None, Field(default=None, max_length=120)] = None


__all__ = [
    "CustomerActivityEvent",
    "CustomerActivityEventType",
    "CustomerActivityListResponse",
    "CustomerCreateRequest",
    "CustomerDetail",
    "CustomerDetailResponse",
    "CustomerImportRequest",
    "CustomerImportResult",
    "CustomerLifecycleStage",
    "CustomerListItem",
    "CustomerNote",
    "CustomerNoteCreateRequest",
    "CustomerNoteListResponse",
    "CustomerNoteUpdateRequest",
    "CustomerTag",
    "CustomerTagCreateRequest",
    "CustomerTagResponse",
    "CustomerUpdateRequest",
    "CustomersFilterParams",
    "CustomersListResponse",
]
