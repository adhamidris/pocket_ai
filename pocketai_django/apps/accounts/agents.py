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
from apps.conversations.models import ConversationStatus


ROLE_LABELS = {
    "support": "Support Agent",
    "success": "Customer Success",
    "sales": "Sales Associate",
    "marketing": "Growth & Marketing",
    "research": "Technical Specialist",
    "billing": "Billing Assistant",
}

AGENT_TYPE_LABELS = {
    AgentProfile.AgentTypeChoices.MAIN: "Main Agent",
    AgentProfile.AgentTypeChoices.DEPARTMENT_LEAD: "Department Lead",
    AgentProfile.AgentTypeChoices.SPECIALIST: "Specialist",
    AgentProfile.AgentTypeChoices.BACKGROUND: "Background Agent",
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
    status: str
    role: str
    agent_type: str
    department_id: uuid.UUID | None
    department_name: str
    manager_agent_id: uuid.UUID | None
    can_manage_tasks: bool
    can_manage_departments: bool
    tone: str | None
    public_slug: str
    created_at: datetime
    updated_at: datetime
    conversations: int
    active_conversations: int
    completed_conversations: int
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
    total_conversations: int
    active_conversations: int
    completed_conversations: int
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
    raw = (role or "").strip()
    if not raw:
        return _("General")
    label = ROLE_LABELS.get(raw.lower(), raw)
    return _(label)


def display_agent_type_label(agent_type: str | None) -> str:
    label = AGENT_TYPE_LABELS.get(agent_type or "", "Specialist")
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
    Fetch a paginated list of agents for a business with lightweight fields and conversation metrics.
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

    base_qs = AgentProfile.objects.select_related("department", "manager_agent").filter(business_profile=business_profile)
    if q_name:
        base_qs = base_qs.filter(name__icontains=q_name.strip())
    if role:
        base_qs = base_qs.filter(role__iexact=role.strip())

    total = base_qs.count()

    duration_expr = ExpressionWrapper(
        F("conversations__closed_at") - F("conversations__started_at"),
        output_field=DjangoDurationField(),
    )
    annotated = base_qs.annotate(
        total_conversations=Count("conversations", distinct=True),
        open_conversations=Count(
            "conversations",
            filter=Q(conversations__status__in=[
                ConversationStatus.NEW,
                ConversationStatus.LIVE,
                ConversationStatus.ESCALATED,
            ]),
            distinct=True,
        ),
        closed_conversations=Count(
            "conversations",
            filter=Q(conversations__status__in=[
                ConversationStatus.RESOLVED,
                ConversationStatus.CLOSED,
                ConversationStatus.EXPIRED,
            ]),
            distinct=True,
        ),
        escalations=Count(
            "conversations",
            filter=Q(conversations__status=ConversationStatus.ESCALATED),
            distinct=True,
        ),
        last_conversation_at=Max("conversations__last_activity_at"),
        avg_handle=Avg(
            duration_expr,
            filter=Q(conversations__closed_at__isnull=False),
        ),
    )

    ordering = f"-{sort_field}" if order == "desc" else sort_field
    rows = list(annotated.order_by(ordering)[offset : offset + limit])

    items = tuple(
        AgentListItem(
            id=row.id,
            name=row.name,
            status=getattr(row, "status", "active"),
            role=row.role or "",
            agent_type=getattr(row, "agent_type", AgentProfile.AgentTypeChoices.SPECIALIST),
            department_id=getattr(row, "department_id", None),
            department_name=getattr(getattr(row, "department", None), "name", "") or "",
            manager_agent_id=getattr(row, "manager_agent_id", None),
            can_manage_tasks=bool(getattr(row, "can_manage_tasks", False)),
            can_manage_departments=bool(getattr(row, "can_manage_departments", False)),
            tone=row.tone or None,
            public_slug=row.slug or "",
            created_at=row.created_at,
            updated_at=row.updated_at,
            conversations=getattr(row, "total_conversations", 0) or 0,
            active_conversations=getattr(row, "open_conversations", 0) or 0,
            completed_conversations=getattr(row, "closed_conversations", 0) or 0,
            escalations=getattr(row, "escalations", 0) or 0,
            last_active_at=getattr(row, "last_conversation_at", None),
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

    duration_expr = ExpressionWrapper(F("conversations__closed_at") - F("conversations__started_at"), output_field=DjangoDurationField())
    agent = (
        AgentProfile.objects.filter(business_profile=business_profile, id=agent_id)
        .select_related("business_profile", "department", "manager_agent")
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
            total_conversations=Count("conversations", distinct=True),
            open_conversations=Count(
                "conversations",
                filter=Q(conversations__status__in=[
                    ConversationStatus.NEW,
                    ConversationStatus.LIVE,
                    ConversationStatus.ESCALATED,
                ]),
                distinct=True,
            ),
            closed_conversations=Count(
                "conversations",
                filter=Q(conversations__status__in=[
                    ConversationStatus.RESOLVED,
                    ConversationStatus.CLOSED,
                    ConversationStatus.EXPIRED,
                ]),
                distinct=True,
            ),
            escalations=Count(
                "conversations",
                filter=Q(conversations__status=ConversationStatus.ESCALATED),
                distinct=True,
            ),
            last_conversation_at=Max("conversations__last_activity_at"),
            avg_handle=Avg(
                duration_expr,
                filter=Q(conversations__closed_at__isnull=False),
            ),
        )
        .first()
    )
    if agent is None:
        raise AgentProfile.DoesNotExist

    summary = AgentListItem(
        id=agent.id,
        name=agent.name,
        status=getattr(agent, "status", "active"),
        role=agent.role or "",
        agent_type=getattr(agent, "agent_type", AgentProfile.AgentTypeChoices.SPECIALIST),
        department_id=getattr(agent, "department_id", None),
        department_name=getattr(getattr(agent, "department", None), "name", "") or "",
        manager_agent_id=getattr(agent, "manager_agent_id", None),
        can_manage_tasks=bool(getattr(agent, "can_manage_tasks", False)),
        can_manage_departments=bool(getattr(agent, "can_manage_departments", False)),
        tone=agent.tone or None,
        public_slug=agent.slug or "",
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        conversations=getattr(agent, "total_conversations", 0) or 0,
        active_conversations=getattr(agent, "open_conversations", 0) or 0,
        completed_conversations=getattr(agent, "closed_conversations", 0) or 0,
        escalations=getattr(agent, "escalations", 0) or 0,
        last_active_at=getattr(agent, "last_conversation_at", None),
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
        total_conversations=summary.conversations,
        active_conversations=summary.active_conversations,
        completed_conversations=summary.completed_conversations,
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
