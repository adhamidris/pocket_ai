"""Pydantic schemas describing structured AI message payloads.

These models define the JSON envelope persisted to `ConversationMessage.payload_json`
for each AGENT turn. They are provider-agnostic, versioned, and align to existing
domain enums (customers, cases, knowledge, escalations).

Usage:
- Validate LLM structured output (function/tool/JSON mode) via these models.
- Downstream workers can map fields to create/update Customers/Cases/Escalations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.cases import (
    CaseLinkTargetType,
    CasePriority,
    CaseStatus,
    CaseType,
    EscalationTrigger,
)
from app.models.customers import CustomerContactMethodType


# -------------------------
# Base & common type aliases
# -------------------------

BusinessId = UUID
ConversationId = UUID
AgentId = UUID
CustomerId = UUID
CaseId = UUID
KnowledgeItemId = UUID
KnowledgeChunkId = UUID


class BaseSchemaModel(BaseModel):
    """Base Pydantic configuration shared by AI runtime schemas."""

    model_config = ConfigDict(extra="forbid")


# -------------------------
# Metrics / Meta
# -------------------------

class AiMetrics(BaseSchemaModel):
    prompt_tokens: Annotated[int | None, Field(default=None, ge=0)]
    completion_tokens: Annotated[int | None, Field(default=None, ge=0)]
    latency_ms: Annotated[int | None, Field(default=None, ge=0)]


AiIntent = Literal["support_issue", "billing_question", "pre_sales", "faq", "other"]


class AiMeta(BaseSchemaModel):
    intent: AiIntent
    confidence: Annotated[float, Field(ge=0.0, le=1.0, description="Model confidence 0–1")]
    language: Annotated[str, Field(min_length=2, max_length=16)]
    metrics: Annotated[AiMetrics | None, Field(default=None)]


# -------------------------
# Customer capture
# -------------------------

class AiContactMethod(BaseSchemaModel):
    type: CustomerContactMethodType
    value: Annotated[str, Field(min_length=2, max_length=255)]
    is_primary: bool = False


class AiCustomerCapture(BaseSchemaModel):
    full_name: Annotated[str | None, Field(default=None, min_length=1, max_length=160)]
    primary_email: Annotated[str | None, Field(default=None, max_length=255)]
    primary_phone: Annotated[str | None, Field(default=None, max_length=64)]
    country: Annotated[str | None, Field(default=None, min_length=2, max_length=2)]
    consent_opt_in: bool | None = None
    contact_methods: Sequence[AiContactMethod] = Field(default_factory=tuple)


# -------------------------
# Case payload
# -------------------------

AiCaseAction = Literal["none", "create", "update"]


class AiCaseLink(BaseSchemaModel):
    target_type: CaseLinkTargetType
    target_id: UUID | None = None
    external_url: Annotated[str | None, Field(default=None, max_length=255)]
    metadata_json: dict | None = None


class AiCasePayload(BaseSchemaModel):
    action: AiCaseAction = "none"
    title: Annotated[str | None, Field(default=None, max_length=200)]
    description: Annotated[str | None, Field(default=None)]
    priority: CasePriority | None = None
    case_type: CaseType | None = None
    status: CaseStatus | None = None
    links: Sequence[AiCaseLink] = Field(default_factory=tuple)


# -------------------------
# Appointment (stub-ready)
# -------------------------

AiAppointmentLocation = Literal["virtual", "phone", "in_person"]


class AiAppointmentProposal(BaseSchemaModel):
    proposed: bool = False
    summary: Annotated[str | None, Field(default=None, max_length=200)]
    duration_minutes: Annotated[int | None, Field(default=30, ge=5, le=480)]
    customer_timezone: Annotated[str | None, Field(default=None, min_length=2, max_length=40)]
    candidate_slots_utc: Sequence[datetime] = Field(default_factory=tuple)
    location: AiAppointmentLocation | None = None
    notes: Annotated[str | None, Field(default=None, max_length=1000)]


# -------------------------
# RAG (retrieval usage & citations)
# -------------------------

class AiCitation(BaseSchemaModel):
    knowledge_item_id: KnowledgeItemId | None = None
    chunk_id: KnowledgeChunkId | None = None
    excerpt: Annotated[str | None, Field(default=None)]
    collection_label: Annotated[str | None, Field(default=None, max_length=120)]


class AiRagPayload(BaseSchemaModel):
    used: bool = False
    query: Annotated[str | None, Field(default=None, max_length=400)]
    top_k: Annotated[int | None, Field(default=None, ge=0)]
    citations: Sequence[AiCitation] = Field(default_factory=tuple)


# -------------------------
# Escalation decision
# -------------------------

class AiEscalationPayload(BaseSchemaModel):
    flagged: bool = False
    trigger: EscalationTrigger | None = None
    reason: Annotated[str | None, Field(default=None, max_length=2000)]


# -------------------------
# Root message payload
# -------------------------

class AiMessagePayload(BaseSchemaModel):
    """Versioned envelope stored in ConversationMessage.payload_json."""

    version: Literal["1.0"] = "1.0"
    meta: AiMeta
    customer_capture: AiCustomerCapture | None = None
    case: AiCasePayload | None = None
    appointment_proposal: AiAppointmentProposal | None = None
    rag: AiRagPayload | None = None
    escalation: AiEscalationPayload | None = None


__all__ = [
    "AiAppointmentLocation",
    "AiAppointmentProposal",
    "AiCaseAction",
    "AiCaseLink",
    "AiCasePayload",
    "AiCitation",
    "AiContactMethod",
    "AiCustomerCapture",
    "AiEscalationPayload",
    "AiIntent",
    "AiMessagePayload",
    "AiMeta",
    "AiMetrics",
    "AiRagPayload",
    "AgentId",
    "BusinessId",
    "CaseId",
    "ConversationId",
    "CustomerId",
    "KnowledgeChunkId",
    "KnowledgeItemId",
]
