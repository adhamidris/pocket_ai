from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from django.db.models import Count, Q

from apps.accounts.models import BusinessProfile
from apps.cases.models import Case, CaseStatus
from apps.customers.models import (
    Customer,
    CustomerActivity,
    CustomerContactPoint,
    CustomerNote,
)


@dataclass(frozen=True)
class CustomerSummary:
    id: uuid.UUID
    display_name: str
    email: str | None
    state: str
    last_interaction_at: datetime | None
    total_cases: int
    open_cases: int


@dataclass(frozen=True)
class CustomerListResult:
    items: Sequence[CustomerSummary]
    total_count: int


@dataclass(frozen=True)
class CustomerContactItem:
    contact_type: str
    label: str
    value: str
    is_primary: bool


@dataclass(frozen=True)
class CustomerCaseLink:
    id: uuid.UUID
    case_number: str
    status: str
    started_at: datetime


@dataclass(frozen=True)
class CustomerActivityItem:
    id: uuid.UUID
    subject: str
    actor_type: str
    activity_type: str
    description: str
    occurred_at: datetime
    case_number: str | None


@dataclass(frozen=True)
class CustomerNoteItem:
    id: uuid.UUID
    author_type: str
    content: str
    is_pinned: bool
    created_at: datetime


@dataclass(frozen=True)
class CustomerDetail:
    summary: CustomerSummary
    primary_phone: str | None
    primary_address: dict
    first_seen_at: datetime
    tags: Sequence[str]
    contacts: Sequence[CustomerContactItem]
    stats: dict
    cases_open: Sequence[CustomerCaseLink]
    cases_closed: Sequence[CustomerCaseLink]
    notes: Sequence[CustomerNoteItem]
    activity: Sequence[CustomerActivityItem]


def list_customers(
    *,
    business_profile: BusinessProfile,
    limit: int = 25,
) -> CustomerListResult:
    """
    Return the most recent customers for a business with basic case metrics.
    """

    base_qs = (
        Customer.objects.filter(business_profile=business_profile)
        .annotate(
            total_cases=Count("cases", distinct=True),
            open_cases=Count("cases", filter=Q(cases__status=CaseStatus.OPEN), distinct=True),
        )
        .order_by(
            "-last_interaction_at",
            "-created_at",
        )
    )
    customers = list(base_qs[: max(1, limit)])
    summaries = tuple(
        CustomerSummary(
            id=customer.id,
            display_name=customer.display_name,
            email=customer.primary_email or None,
            state=customer.record_state,
            last_interaction_at=customer.last_interaction_at,
            total_cases=getattr(customer, "total_cases", 0),
            open_cases=getattr(customer, "open_cases", 0),
        )
        for customer in customers
    )
    total = Customer.objects.filter(business_profile=business_profile).count()
    return CustomerListResult(items=summaries, total_count=total)


def get_customer_detail(
    *,
    business_profile: BusinessProfile,
    customer_id: uuid.UUID,
) -> CustomerDetail:
    """
    Hydrate a customer profile with contact methods, activity, notes, and case stats.
    """

    customer = (
        Customer.objects.filter(business_profile=business_profile, id=customer_id)
        .prefetch_related("contact_points")
        .annotate(
            total_cases=Count("cases", distinct=True),
            open_cases=Count("cases", filter=Q(cases__status=CaseStatus.OPEN), distinct=True),
        )
        .first()
    )
    if customer is None:
        raise Customer.DoesNotExist

    summary = CustomerSummary(
        id=customer.id,
        display_name=customer.display_name,
        email=customer.primary_email or None,
        state=customer.record_state,
        last_interaction_at=customer.last_interaction_at,
        total_cases=getattr(customer, "total_cases", 0),
        open_cases=getattr(customer, "open_cases", 0),
    )

    contacts = tuple(
        CustomerContactItem(
            contact_type=contact.contact_type,
            label=contact.label,
            value=contact.value,
            is_primary=contact.is_primary,
        )
        for contact in customer.contact_points.all()
        if contact.value
    )

    cases_qs = Case.objects.filter(business_profile=business_profile, customer=customer).order_by("-started_at")
    open_cases = tuple(
        CustomerCaseLink(
            id=case.id,
            case_number=case.case_number,
            status=case.status,
            started_at=case.started_at,
        )
        for case in cases_qs.filter(status=CaseStatus.OPEN)[:5]
    )
    closed_cases = tuple(
        CustomerCaseLink(
            id=case.id,
            case_number=case.case_number,
            status=case.status,
            started_at=case.started_at,
        )
        for case in cases_qs.filter(status=CaseStatus.CLOSED)[:5]
    )

    activity_items = tuple(
        CustomerActivityItem(
            id=activity.id,
            subject=activity.subject,
            actor_type=activity.actor_type,
            activity_type=activity.activity_type,
            description=activity.description or "",
            occurred_at=activity.occurred_at,
            case_number=activity.case.case_number if activity.case else None,
        )
        for activity in CustomerActivity.objects.filter(
            business_profile=business_profile,
            customer=customer,
        )
        .select_related("case")
        .order_by("-occurred_at")[:10]
    )

    note_items = tuple(
        CustomerNoteItem(
            id=note.id,
            author_type=note.author_type,
            content=note.content,
            is_pinned=note.is_pinned,
            created_at=note.created_at,
        )
        for note in CustomerNote.objects.filter(customer=customer).order_by("-created_at")[:10]
    )

    stats = {
        "total_cases": summary.total_cases,
        "open_cases": summary.open_cases,
        "closed_cases": max(summary.total_cases - summary.open_cases, 0),
        "first_seen_at": customer.first_seen_at,
        "last_interaction_at": customer.last_interaction_at,
    }

    return CustomerDetail(
        summary=summary,
        primary_phone=customer.primary_phone or None,
        primary_address=customer.primary_address or {},
        first_seen_at=customer.first_seen_at,
        tags=tuple(customer.tags or []),
        contacts=contacts,
        stats=stats,
        cases_open=open_cases,
        cases_closed=closed_cases,
        notes=note_items,
        activity=activity_items,
    )
