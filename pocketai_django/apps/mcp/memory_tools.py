"""
Memory MCP tool handlers.
"""

from __future__ import annotations

import uuid
from typing import Mapping

from apps.conversations.models import Conversation

from .types import ToolExecutionContext


def _search_memory_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from django.db.models import Q
    from apps.conversations.models import MemoryItem, MemoryStatus, MemoryVisibility

    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"tool": "search_memory", "status": "error", "error_code": "validation_failed", "error": "query is required."}
    limit = max(1, min(int(arguments.get("limit") or 10), 20))
    qs = MemoryItem.objects.filter(business_profile_id=conversation.business_profile_id, status=MemoryStatus.ACTIVE).filter(
        Q(visibility=MemoryVisibility.SHARED) | Q(agent_profile_id=conversation.agent_profile_id)
    )
    scope = str(arguments.get("scope") or "").strip().lower()
    if scope:
        qs = qs.filter(scope=scope)
    qs = qs.filter(Q(content__icontains=query) | Q(key__icontains=query)).order_by("-updated_at")
    return {
        "tool": "search_memory",
        "status": "ok",
        "memory": [
            {
                "id": str(item.id),
                "scope": item.scope,
                "kind": item.kind,
                "key": item.key,
                "content": item.content[:1200],
                "visibility": item.visibility,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in qs[:limit]
        ],
    }


def _save_memory_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.conversations.models import (
        MemoryAuditAction,
        MemoryAuditEvent,
        MemoryItem,
        MemoryKind,
        MemoryScope,
        MemorySensitivity,
        MemoryStatus,
        MemoryVisibility,
    )

    content = str(arguments.get("content") or "").strip()
    if not content:
        return {"tool": "save_memory", "status": "error", "error_code": "validation_failed", "error": "content is required."}
    kind = str(arguments.get("kind") or MemoryKind.FACT).strip().lower()
    sensitivity = str(arguments.get("sensitivity") or MemorySensitivity.NORMAL).strip().lower()
    status = MemoryStatus.PENDING_REVIEW if sensitivity in {MemorySensitivity.SENSITIVE, MemorySensitivity.SECRET} or kind == MemoryKind.INSTRUCTION else MemoryStatus.ACTIVE
    item = MemoryItem.objects.create(
        business_profile_id=conversation.business_profile_id,
        agent_profile_id=conversation.agent_profile_id,
        conversation=conversation if str(arguments.get("scope") or "") == MemoryScope.CONVERSATION else None,
        scope=str(arguments.get("scope") or MemoryScope.AGENT).strip().lower(),
        kind=kind,
        key=str(arguments.get("key") or "")[:160],
        content=content[:8000],
        sensitivity=sensitivity,
        visibility=str(arguments.get("visibility") or MemoryVisibility.SHARED).strip().lower(),
        status=status,
        source_type="mcp_tool",
    )
    MemoryAuditEvent.objects.create(
        memory_item=item,
        business_profile_id=conversation.business_profile_id,
        actor_user=getattr(conversation, "owner_user", None) or getattr(conversation.agent_profile, "user", None),
        action=MemoryAuditAction.CREATED,
        after={
            "scope": item.scope,
            "kind": item.kind,
            "key": item.key,
            "content": item.content,
            "visibility": item.visibility,
            "sensitivity": item.sensitivity,
            "status": item.status,
        },
        metadata={"source": "save_memory_tool"},
    )
    return {"tool": "save_memory", "status": "ok", "memory_id": str(item.id), "review_required": status == MemoryStatus.PENDING_REVIEW}


def _forget_memory_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.conversations.models import MemoryAuditAction, MemoryAuditEvent, MemoryItem, MemoryStatus

    try:
        memory_id = uuid.UUID(str(arguments.get("memory_id") or arguments.get("memoryId") or ""))
    except (TypeError, ValueError):
        return {"tool": "forget_memory", "status": "error", "error_code": "validation_failed", "error": "memory_id must be a UUID."}
    item = MemoryItem.objects.filter(id=memory_id, business_profile_id=conversation.business_profile_id).first()
    if item is None:
        return {"tool": "forget_memory", "status": "error", "error_code": "not_found", "error": "Memory item not found."}
    before = {"status": item.status, "content": item.content, "scope": item.scope, "kind": item.kind, "key": item.key}
    item.status = MemoryStatus.ARCHIVED
    item.save(update_fields=["status", "updated_at"])
    MemoryAuditEvent.objects.create(
        memory_item=item,
        business_profile_id=conversation.business_profile_id,
        actor_user=getattr(conversation, "owner_user", None) or getattr(conversation.agent_profile, "user", None),
        action=MemoryAuditAction.ARCHIVED,
        before=before,
        after={"status": item.status, "content": item.content, "scope": item.scope, "kind": item.kind, "key": item.key},
        metadata={"source": "forget_memory_tool"},
    )
    return {"tool": "forget_memory", "status": "ok", "memory_id": str(item.id)}
