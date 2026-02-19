from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from django.db.models import Avg, Count, ExpressionWrapper, F, Max, Prefetch, Q
from django.db.models import DurationField as DjangoDurationField
from django.utils.translation import gettext as _

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
)
from apps.knowledge.models import (
    KnowledgeUpload,
)
from apps.cases.models import CasePriority, CaseStatus


ROLE_LABELS = {
    "support": "Support Agent",
    "success": "Customer Success",
    "sales": "Sales Associate",
    "marketing": "Growth & Marketing",
    "research": "Technical Specialist",
    "billing": "Billing Assistant",
}

TONE_LABELS = {
    "friendly": "Friendly",
    "professional": "Professional",
    "empathetic": "Empathetic",
    "casual": "Conversational",
    "playful": "Playful",
    "formal": "Formal",
}


class AgentListValidationError(ValueError):
    """
    Raised when incoming query parameters for agent listing are invalid.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class AgentListItem:
    id: uuid.UUID
    name: str
    role: str
    tone: str | None
    status: str
    public_slug: str
    created_at: datetime
    updated_at: datetime
    conversations: int
    open_cases: int
    closed_cases: int
    escalations: int
    last_active_at: datetime | None
    average_handle_seconds: float | None


@dataclass(frozen=True)
class AgentListResult:
    items: Sequence[AgentListItem]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class AgentStats:
    total_cases: int
    open_cases: int
    closed_cases: int
    escalations: int
    last_active_at: datetime | None
    average_handle_seconds: float | None


@dataclass(frozen=True)
class AgentKnowledgeItem:
    id: uuid.UUID
    name: str
    status: str
    source_type: str
    last_synced_at: datetime | None


@dataclass(frozen=True)
class AgentDetail:
    summary: AgentListItem
    shareable_path: str
    traits: Sequence[str]
    escalation_rule: str | None
    selected_kpis: Sequence[str]
    custom_kpis: Sequence[str]
    allow_custom_kpi_weighting: bool
    knowledge_mode: str
    knowledge_documents: Sequence[AgentKnowledgeItem]
    stats: AgentStats


def display_role_label(role: str | None) -> str:
    label = ROLE_LABELS.get((role or "").lower(), ROLE_LABELS["support"])
    return _(label)


def display_tone_label(tone: str | None) -> str | None:
    if not tone:
        return None
    label = TONE_LABELS.get(tone.lower(), tone.title())
    return _(label)


def agent_identifier(agent_id: uuid.UUID) -> str:
    token = str(agent_id).split("-")[0].upper()
    return f"AG-{token}"


def initials_from_name(name: str, fallback: str = "AI") -> str:
    tokens = [token for token in (name or "").split() if token]
    if not tokens:
        return fallback
    if len(tokens) == 1:
        return tokens[0][:2].upper()
    return (tokens[0][0] + tokens[-1][0]).upper()


def _duration_seconds(value: timedelta | None) -> float | None:
    return value.total_seconds() if isinstance(value, timedelta) else None


def list_agents(
    *,
    business_profile: BusinessProfile,
    q_name: str | None = None,
    role: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "created_at",
    order: str = "desc",
) -> AgentListResult:
    """
    Fetch a paginated list of agents for a business with lightweight fields and case metrics.
    """

    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise AgentListValidationError("limit must be an integer", field="limit") from exc
    if limit < 1 or limit > 100:
        raise AgentListValidationError("limit must be between 1 and 100", field="limit")

    try:
        offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise AgentListValidationError("offset must be an integer", field="offset") from exc
    if offset < 0 or offset > 10_000:
        raise AgentListValidationError("offset must be between 0 and 10000", field="offset")

    sort_map = {"created_at": "created_at", "name": "name", "updated_at": "updated_at"}
    sort_field = sort_map.get((sort_by or "created_at").lower())
    if not sort_field:
        raise AgentListValidationError("Unsupported sort_by value", field="sort_by")

    if not order:
        order = "desc"
    if order not in {"asc", "desc"}:
        raise AgentListValidationError("order must be 'asc' or 'desc'", field="order")

    base_qs = AgentProfile.objects.filter(business_profile=business_profile)
    if q_name:
        base_qs = base_qs.filter(name__icontains=q_name.strip())
    if role:
        base_qs = base_qs.filter(role__iexact=role.strip())

    total = base_qs.count()

    duration_expr = ExpressionWrapper(
        F("cases__closed_at") - F("cases__started_at"),
        output_field=DjangoDurationField(),
    )
    annotated = base_qs.annotate(
        total_cases=Count("cases", distinct=True),
        open_cases=Count("cases", filter=Q(cases__status=CaseStatus.OPEN), distinct=True),
        closed_cases=Count("cases", filter=Q(cases__status=CaseStatus.CLOSED), distinct=True),
        escalations=Count(
            "cases",
            filter=Q(cases__priority=CasePriority.CRITICAL),
            distinct=True,
        ),
        last_case_at=Max("cases__updated_at"),
        avg_handle=Avg(
            duration_expr,
            filter=Q(cases__status=CaseStatus.CLOSED),
        ),
    )

    ordering = f"-{sort_field}" if order == "desc" else sort_field
    rows = list(annotated.order_by(ordering)[offset : offset + limit])

    items = tuple(
        AgentListItem(
            id=row.id,
            name=row.name,
            role=row.role or "",
            tone=row.tone or None,
            status=row.status,
            public_slug=row.slug or "",
            created_at=row.created_at,
            updated_at=row.updated_at,
            conversations=getattr(row, "total_cases", 0) or 0,
            open_cases=getattr(row, "open_cases", 0) or 0,
            closed_cases=getattr(row, "closed_cases", 0) or 0,
            escalations=getattr(row, "escalations", 0) or 0,
            last_active_at=getattr(row, "last_case_at", None),
            average_handle_seconds=_duration_seconds(getattr(row, "avg_handle", None)),
        )
        for row in rows
    )

    return AgentListResult(items=items, total=total, limit=limit, offset=offset)


def get_agent_detail(
    *,
    business_profile: BusinessProfile,
    agent_id: uuid.UUID,
) -> AgentDetail:
    """
    Load a single agent profile with persona, KPI, and knowledge metadata.
    """

    duration_expr = ExpressionWrapper(
        F("cases__closed_at") - F("cases__started_at"),
        output_field=DjangoDurationField(),
    )
    agent = (
        AgentProfile.objects.filter(business_profile=business_profile, id=agent_id)
        .select_related("business_profile")
        .prefetch_related(
            Prefetch(
                "allowed_documents",
                queryset=KnowledgeUpload.objects.filter(business_profile=business_profile).only(
                    "id",
                    "display_name",
                    "status",
                    "source_type",
                    "updated_at",
                    "last_ingested_at",
                    "last_synced_at",
                ),
            ),
        )
        .annotate(
            total_cases=Count("cases", distinct=True),
            open_cases=Count("cases", filter=Q(cases__status=CaseStatus.OPEN), distinct=True),
            closed_cases=Count("cases", filter=Q(cases__status=CaseStatus.CLOSED), distinct=True),
            escalations=Count(
                "cases",
                filter=Q(cases__priority=CasePriority.CRITICAL),
                distinct=True,
            ),
            last_case_at=Max("cases__updated_at"),
            avg_handle=Avg(
                duration_expr,
                filter=Q(cases__status=CaseStatus.CLOSED),
            ),
        )
        .first()
    )
    if agent is None:
        raise AgentProfile.DoesNotExist

    summary = AgentListItem(
        id=agent.id,
        name=agent.name,
        role=agent.role or "",
        tone=agent.tone or None,
        status=agent.status,
        public_slug=agent.slug or "",
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        conversations=getattr(agent, "total_cases", 0) or 0,
        open_cases=getattr(agent, "open_cases", 0) or 0,
        closed_cases=getattr(agent, "closed_cases", 0) or 0,
        escalations=getattr(agent, "escalations", 0) or 0,
        last_active_at=getattr(agent, "last_case_at", None),
        average_handle_seconds=_duration_seconds(getattr(agent, "avg_handle", None)),
    )

    documents = tuple(
        AgentKnowledgeItem(
            id=doc.id,
            name=doc.display_name or "Knowledge Item",
            status=doc.status,
            source_type=doc.source_type,
            last_synced_at=doc.last_synced_at or doc.last_ingested_at or doc.updated_at,
        )
        for doc in agent.allowed_documents.all()
    )

    stats = AgentStats(
        total_cases=summary.conversations,
        open_cases=summary.open_cases,
        closed_cases=summary.closed_cases,
        escalations=summary.escalations,
        last_active_at=summary.last_active_at,
        average_handle_seconds=summary.average_handle_seconds,
    )

    return AgentDetail(
        summary=summary,
        shareable_path=agent.shareable_path,
        traits=tuple(agent.traits or []),
        escalation_rule=agent.escalation_rule or None,
        selected_kpis=tuple(agent.selected_kpis or []),
        custom_kpis=tuple(agent.custom_kpis or []),
        allow_custom_kpi_weighting=bool(agent.allow_custom_kpi_weighting),
        knowledge_mode="select" if documents else "all",
        knowledge_documents=documents,
        stats=stats,
    )
