"""
Agent knowledge-scope helpers for MCP knowledge tools.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from apps.accounts.models import AgentProfile, KnowledgeStatus
from apps.conversations.models import Conversation
from apps.knowledge.access.visibility import apply_customer_visible_uploads

from ..types import ToolExecutionContext


@dataclass(frozen=True, slots=True)
class AgentKnowledgeScope:
    mode: str  # all|documents
    explicit_upload_ids: frozenset[str] = frozenset()

    @property
    def restricted(self) -> bool:
        return self.mode != "all"


_AGENT_SCOPE_SENTINEL: object = object()


def _agent_knowledge_scope(conversation: Conversation, context: ToolExecutionContext) -> AgentKnowledgeScope:
    cached = getattr(context, "_agent_knowledge_scope", _AGENT_SCOPE_SENTINEL)
    if isinstance(cached, AgentKnowledgeScope):
        return cached

    agent_id = getattr(conversation, "agent_profile_id", None)
    if not agent_id:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    agent = AgentProfile.objects.filter(
        id=agent_id,
        business_profile=conversation.business_profile,
    ).first()
    if agent is None:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    has_doc_rules = agent.allowed_documents.filter(business_profile=conversation.business_profile).exists()
    if not has_doc_rules:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    explicit_upload_ids: set[str] = set()
    if has_doc_rules:
        explicit_upload_ids.update(
            str(value)
            for value in apply_customer_visible_uploads(
                agent.allowed_documents.filter(
                    business_profile=conversation.business_profile,
                    status=KnowledgeStatus.ACTIVE,
                )
            ).values_list("id", flat=True)
        )

    scope = AgentKnowledgeScope(
        mode="documents",
        explicit_upload_ids=frozenset(explicit_upload_ids),
    )
    context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
    return scope


def _agent_scope_allows_upload(
    *,
    scope: AgentKnowledgeScope,
    conversation: Conversation,
    upload_id: uuid.UUID,
) -> bool:
    if not scope.restricted:
        return True
    return str(upload_id) in scope.explicit_upload_ids


def _apply_agent_scope_to_upload_queryset(queryset, scope: AgentKnowledgeScope):
    if not scope.restricted:
        return queryset
    if not scope.explicit_upload_ids:
        return queryset.none()
    return queryset.filter(id__in=list(scope.explicit_upload_ids)).distinct()


def _scope_upload_ids_to_uuids(scope: Iterable[str] | None) -> list[uuid.UUID] | None:
    if scope is None:
        return None
    ids: list[uuid.UUID] = []
    for value in scope:
        try:
            ids.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return ids
