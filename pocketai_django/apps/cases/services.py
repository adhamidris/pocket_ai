from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.accounts.models import AgentProfile, BusinessProfile, User
from apps.cases.models import (
    Case,
    CaseDocumentLink,
    CaseHistoryEntry,
    CaseMessage,
    CasePriority,
    CaseStatus,
)
from apps.customers.models import Customer, CustomerNote


class CaseServiceError(Exception):
    """Base error for case service operations."""


@dataclass(frozen=True)
class CaseSummary:
    id: uuid.UUID
    case_number: str
    title: str
    description: str
    priority: str
    status: str
    customer_id: uuid.UUID | None
    customer_name: str
    customer_email: str | None
    customer_initials: str
    agent_id: uuid.UUID | None
    agent_name: str | None
    channel: str | None
    started_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    last_message_at: datetime | None


@dataclass(frozen=True)
class CaseMetrics:
    open_total: int
    urgent_open: int
    urgent_delta_hint: str
    average_open_hours: float | None


@dataclass(frozen=True)
class CaseHistoryItem:
    id: uuid.UUID
    summary: str
    source: str
    occurred_at: datetime
    session_reference: str | None
    metadata: dict


@dataclass(frozen=True)
class CaseMessageItem:
    id: uuid.UUID
    sender: str
    sender_display_name: str
    content: str
    content_type: str
    sent_at: datetime
    session_reference: str | None
    metadata: dict


@dataclass(frozen=True)
class CaseDocumentItem:
    id: uuid.UUID
    name: str
    document_url: str | None
    knowledge_upload_id: uuid.UUID | None
    knowledge_upload_name: str | None
    captured_at: datetime
    metadata: dict


@dataclass(frozen=True)
class CaseNoteItem:
    id: uuid.UUID
    author_type: str
    content: str
    is_pinned: bool
    created_at: datetime
    metadata: dict


@dataclass(frozen=True)
class CaseDetail:
    summary: CaseSummary
    description: str
    ai_diagnosis: str
    ai_actions_taken: str
    ai_suggested_actions: Sequence[str]
    metadata: dict
    history: Sequence[CaseHistoryItem]
    messages: Sequence[CaseMessageItem]
    notes: Sequence[CaseNoteItem]
    documents: Sequence[CaseDocumentItem]


@dataclass(frozen=True)
class CaseListResult:
    items: Sequence[CaseSummary]
    total_count: int
    filters_applied: dict
    metrics: CaseMetrics


def _customer_initials(name: str) -> str:
    tokens = [token for token in name.split() if token]
    if not tokens:
        return "CU"
    if len(tokens) == 1:
        return tokens[0][:2].upper()
    return (tokens[0][0] + tokens[-1][0]).upper()


def _customer_display(case: Case) -> tuple[uuid.UUID | None, str, str | None, str]:
    if case.customer:
        name = case.customer.display_name
        email = case.customer.primary_email or None
        initials = _customer_initials(name)
        return case.customer.id, name, email, initials
    snapshot = case.customer_snapshot or {}
    name = snapshot.get("display_name") or snapshot.get("name") or "Unattributed"
    email = snapshot.get("email") or snapshot.get("primary_email")
    initials = snapshot.get("initials") or _customer_initials(name)
    return None, name, email, initials


def _agent_display(case: Case) -> tuple[uuid.UUID | None, str | None]:
    if case.agent_profile:
        return case.agent_profile.id, case.agent_profile.name
    return None, None


def _serialize_case(case: Case, last_message_at: datetime | None = None) -> CaseSummary:
    customer_id, customer_name, customer_email, customer_initials = _customer_display(case)
    agent_id, agent_name = _agent_display(case)
    channel = (case.metadata or {}).get("channel")
    return CaseSummary(
        id=case.id,
        case_number=case.case_number,
        title=case.title,
        description=case.description,
        priority=case.priority,
        status=case.status,
        customer_id=customer_id,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_initials=customer_initials,
        agent_id=agent_id,
        agent_name=agent_name,
        channel=channel,
        started_at=case.started_at,
        updated_at=case.updated_at,
        closed_at=case.closed_at,
        last_message_at=last_message_at,
    )


def _compute_metrics(business_profile: BusinessProfile) -> CaseMetrics:
    base_qs = Case.objects.filter(business_profile=business_profile)
    open_total = base_qs.filter(status=CaseStatus.OPEN).count()
    urgent_open = base_qs.filter(
        status=CaseStatus.OPEN,
        priority__in=(CasePriority.HIGH, CasePriority.CRITICAL),
    ).count()

    open_cases = base_qs.filter(status=CaseStatus.OPEN).only("started_at")
    open_durations: list[timedelta] = []
    now = timezone.now()
    for case in open_cases:
        open_durations.append(now - case.started_at)
    average_open_hours = None
    if open_durations:
        average_open_hours = sum((dur.total_seconds() for dur in open_durations)) / len(open_durations) / 3600

    urgent_hint = "No urgent cases" if urgent_open == 0 else "Monitor escalation queue"

    return CaseMetrics(
        open_total=open_total,
        urgent_open=urgent_open,
        urgent_delta_hint=urgent_hint,
        average_open_hours=round(average_open_hours, 2) if average_open_hours is not None else None,
    )


def list_cases(
    *,
    business_profile: BusinessProfile,
    status: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    agent_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CaseListResult:
    """
    Fetch cases for a business with optional filters and derived metrics.
    """

    qs = Case.objects.filter(business_profile=business_profile).select_related(
        "business_profile",
        "agent_profile",
        "customer",
    )

    if status and status.lower() != "all":
        qs = qs.filter(status=status.lower())
    if priority and priority.lower() != "all":
        qs = qs.filter(priority=priority.lower())
    if agent_id:
        qs = qs.filter(agent_profile_id=agent_id)
    if customer_id:
        qs = qs.filter(customer_id=customer_id)
    if search:
        qs = qs.filter(
            Q(case_number__icontains=search)
            | Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(customer__display_name__icontains=search)
        )

    qs = qs.annotate(last_message_at=Max("messages__sent_at")).order_by("-started_at")
    total_count = qs.count()
    paginated = list(qs[offset : offset + limit])

    summaries = [
        _serialize_case(case, getattr(case, "last_message_at", None)) for case in paginated
    ]

    metrics = _compute_metrics(business_profile)
    filters_applied = {
        "status": status,
        "priority": priority,
        "search": search,
        "agent_id": str(agent_id) if agent_id else None,
        "customer_id": str(customer_id) if customer_id else None,
        "limit": limit,
        "offset": offset,
    }

    return CaseListResult(
        items=summaries,
        total_count=total_count,
        filters_applied=filters_applied,
        metrics=metrics,
    )


def get_case_detail(*, business_profile: BusinessProfile, case_id: uuid.UUID) -> CaseDetail:
    """
    Retrieve a case with related history, chat transcript, notes, and documents.
    """

    case = (
        Case.objects.filter(business_profile=business_profile, id=case_id)
        .select_related("business_profile", "agent_profile", "customer")
        .get()
    )

    history_qs = case.history_entries.order_by("-occurred_at")
    messages_qs = case.messages.order_by("sent_at")
    documents_qs = case.document_links.select_related("knowledge_upload").order_by("-captured_at")
    notes_qs = case.customer_notes.order_by("-created_at")

    history = [
        CaseHistoryItem(
            id=entry.id,
            summary=entry.summary,
            source=entry.source,
            occurred_at=entry.occurred_at,
            session_reference=entry.session_reference,
            metadata=entry.metadata,
        )
        for entry in history_qs
    ]

    messages = [
        CaseMessageItem(
            id=message.id,
            sender=message.sender,
            sender_display_name=message.sender_display_name,
            content=message.content,
            content_type=message.content_type,
            sent_at=message.sent_at,
            session_reference=message.session_reference,
            metadata=message.metadata,
        )
        for message in messages_qs
    ]

    documents: list[CaseDocumentItem] = []
    for link in documents_qs:
        upload = link.knowledge_upload
        documents.append(
            CaseDocumentItem(
                id=link.id,
                name=link.name or (upload.display_name if upload else "") or "Attachment",
                document_url=link.document_url or (upload.legacy_url if upload else None),
                knowledge_upload_id=upload.id if upload else None,
                knowledge_upload_name=upload.display_name if upload else None,
                captured_at=link.captured_at,
                metadata=link.metadata,
            )
        )

    notes = [
        CaseNoteItem(
            id=note.id,
            author_type=note.author_type,
            content=note.content,
            is_pinned=note.is_pinned,
            created_at=note.created_at,
            metadata=note.metadata,
        )
        for note in notes_qs
    ]

    summary = _serialize_case(case)
    return CaseDetail(
        summary=summary,
        description=case.description,
        ai_diagnosis=case.ai_diagnosis,
        ai_actions_taken=case.ai_actions_taken,
        ai_suggested_actions=tuple(case.ai_suggested_actions or []),
        metadata=case.metadata or {},
        history=tuple(history),
        messages=tuple(messages),
        notes=tuple(notes),
        documents=tuple(documents),
    )


def create_case(
    *,
    business_profile: BusinessProfile,
    title: str,
    description: str,
    priority: str = CasePriority.MEDIUM,
    status: str = CaseStatus.OPEN,
    created_by: User | None = None,
    agent_profile: AgentProfile | None = None,
    customer: Customer | None = None,
    customer_snapshot: dict | None = None,
    ai_diagnosis: str = "",
    ai_actions_taken: str = "",
    ai_suggested_actions: Sequence[str] | None = None,
    metadata: dict | None = None,
) -> Case:
    """
    Manually create a case record so it can be wired into AI pipelines later.
    """

    if priority not in dict(CasePriority.choices):
        raise CaseServiceError(f"Unsupported priority '{priority}'.")
    if status not in dict(CaseStatus.choices):
        raise CaseServiceError(f"Unsupported status '{status}'.")

    case = Case.objects.create(
        business_profile=business_profile,
        agent_profile=agent_profile,
        customer=customer,
        customer_snapshot=customer_snapshot or {},
        title=title,
        description=description,
        priority=priority,
        status=status,
        ai_diagnosis=ai_diagnosis,
        ai_actions_taken=ai_actions_taken,
        ai_suggested_actions=list(ai_suggested_actions or []),
        metadata=metadata or {},
        started_at=timezone.now(),
    )

    if created_by:
        CaseHistoryEntry.objects.create(
            case=case,
            summary=f"Case created manually by {created_by.get_full_name() or created_by.email}",
            source="agent",
            metadata={"created_by": str(created_by.id)},
        )

    return case


def update_case(
    *,
    business_profile: BusinessProfile,
    case_id: uuid.UUID,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    ai_diagnosis: str | None = None,
    ai_actions_taken: str | None = None,
    ai_suggested_actions: Sequence[str] | None = None,
    metadata: dict | None = None,
) -> Case:
    """
    Update mutable fields on a case. Returns the refreshed instance.
    """

    try:
        case = Case.objects.get(business_profile=business_profile, id=case_id)
    except Case.DoesNotExist as exc:
        raise CaseServiceError("Case not found.") from exc

    if priority:
        if priority not in dict(CasePriority.choices):
            raise CaseServiceError(f"Unsupported priority '{priority}'.")
        case.priority = priority
    if status:
        if status not in dict(CaseStatus.choices):
            raise CaseServiceError(f"Unsupported status '{status}'.")
        case.status = status
    if title is not None:
        case.title = title
    if description is not None:
        case.description = description
    if ai_diagnosis is not None:
        case.ai_diagnosis = ai_diagnosis
    if ai_actions_taken is not None:
        case.ai_actions_taken = ai_actions_taken
    if ai_suggested_actions is not None:
        case.ai_suggested_actions = list(ai_suggested_actions)
    if metadata is not None:
        case.metadata = metadata

    case.save()
    return case


def add_history_entry(
    *,
    case: Case,
    summary: str,
    source: str = "system",
    session_reference: str | None = None,
    metadata: dict | None = None,
) -> CaseHistoryEntry:
    return CaseHistoryEntry.objects.create(
        case=case,
        summary=summary,
        source=source,
        session_reference=session_reference or "",
        metadata=metadata or {},
        occurred_at=timezone.now(),
    )


def add_case_message(
    *,
    case: Case,
    sender: str,
    content: str,
    sender_display_name: str = "",
    session_reference: str | None = None,
    content_type: str = "text",
    metadata: dict | None = None,
) -> CaseMessage:
    return CaseMessage.objects.create(
        case=case,
        sender=sender,
        sender_display_name=sender_display_name,
        content=content,
        content_type=content_type,
        session_reference=session_reference or "",
        metadata=metadata or {},
        sent_at=timezone.now(),
    )


def add_case_note(
    *,
    case: Case,
    customer: Customer,
    author_type: str,
    content: str,
    is_pinned: bool = False,
    metadata: dict | None = None,
) -> CustomerNote:
    return CustomerNote.objects.create(
        customer=customer,
        case=case,
        author_type=author_type,
        content=content,
        is_pinned=is_pinned,
        metadata=metadata or {},
    )


def bulk_attach_documents(
    *,
    case: Case,
    documents: Iterable[CaseDocumentLink],
) -> None:
    """
    Provide a hook for future ingestion jobs to attach documents in bulk.
    """

    with transaction.atomic():
        CaseDocumentLink.objects.bulk_create(documents)
