from __future__ import annotations

from datetime import datetime
from typing import Sequence
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, HttpUrl

from app.api.v1.deps import get_db, get_current_user, require_business_id
from app.core.security import AuthenticatedUser
from sqlalchemy.orm import Session

from app.models.registration import AgentRole, AgentTone, AgentStatus
from app.repositories.registration import AgentsRepository
from app.schemas.registration import ApiErrorResponse  # reuse generic error envelope

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ApiErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ApiErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ApiErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ApiErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ApiErrorResponse},
    },
)

class AgentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: AgentRole
    tone: AgentTone
    status: AgentStatus
    public_slug: str
    avatar_url: HttpUrl | None = None
    created_at: datetime

class AgentsListResponse(BaseModel):
    items: Sequence[AgentListItem]
    total: int

@router.get("", response_model=AgentsListResponse, summary="List agents for the active business")
def list_agents_endpoint(
    q_name: str | None = Query(default=None, description="Filter by name (contains)"),
    role: AgentRole | None = Query(default=None, description="Filter by role"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    sort_by: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    business_id: UUID = Depends(require_business_id),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentsListResponse:
    repo = AgentsRepository(db)
    items = repo.list_agents(
        business_id=business_id,
        q_name=q_name,
        role=role,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        order=order,
    )
    # For now, return count=offset+len(items) approximation; later we can add a proper count if needed
    return AgentsListResponse(
        items=[AgentListItem(
            id=it.id,
            name=it.name,
            role=it.role,
            tone=it.tone,
            status=it.status,
            public_slug=it.public_slug,
            avatar_url=it.avatar_url,
            created_at=it.created_at,
        ) for it in items],
        total=offset + len(items),
    )
