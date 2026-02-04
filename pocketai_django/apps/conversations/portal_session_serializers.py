from __future__ import annotations

from typing import Mapping

from apps.conversations.models import AgentRequest, AgentRun, AgentRunEvent, ConversationMessage


def clip_portal_text(value: str, limit: int) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def serialize_agent_run_for_portal(run: AgentRun) -> dict[str, object]:
    plan_payload = run.plan if isinstance(getattr(run, "plan", None), dict) else {}
    result_payload = run.result if isinstance(getattr(run, "result", None), dict) else {}
    response_text = ""
    if isinstance(result_payload, dict):
        response_text = str(result_payload.get("response_text") or result_payload.get("responseText") or "").strip()
    error_detail = str(getattr(run, "error_detail", "") or "").strip()
    return {
        "id": str(run.id),
        "title": run.title or "",
        "source": run.source,
        "status": run.status,
        "attemptCount": int(run.attempt_count or 0),
        "maxAttempts": int(run.max_attempts or 0),
        "runAfter": run.run_after.isoformat() if run.run_after else None,
        "leaseExpiresAt": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "updatedAt": run.updated_at.isoformat() if run.updated_at else None,
        "errorDetail": clip_portal_text(error_detail, 800) if error_detail else "",
        "plan": plan_payload,
        "result": {"responseText": clip_portal_text(response_text, 6000)} if response_text else {},
    }


def serialize_agent_run_event_for_portal(event: AgentRunEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "runId": str(event.run_id),
        "sequenceIndex": int(event.sequence_index),
        "stream": event.stream,
        "type": event.event_type,
        "label": event.label or "",
        "payload": event.payload if isinstance(getattr(event, "payload", None), dict) else {},
        "createdAt": event.created_at.isoformat() if event.created_at else None,
    }


def serialize_agent_request_for_portal(request: AgentRequest) -> dict[str, object]:
    context_refs = request.context_refs if isinstance(getattr(request, "context_refs", None), list) else []
    from_agent = getattr(request, "from_agent_profile", None)
    to_agent = getattr(request, "to_agent_profile", None)
    return {
        "id": str(request.id),
        "status": request.status,
        "subject": request.subject or "",
        "question": clip_portal_text(str(request.question or ""), 6000),
        "contextRefs": context_refs,
        "resolution": clip_portal_text(str(request.resolution or ""), 6000),
        "fromAgent": {
            "id": str(getattr(from_agent, "id", "") or ""),
            "name": str(getattr(from_agent, "name", "") or ""),
            "slug": str(getattr(from_agent, "slug", "") or ""),
        }
        if from_agent
        else {},
        "toAgent": {
            "id": str(getattr(to_agent, "id", "") or ""),
            "name": str(getattr(to_agent, "name", "") or ""),
            "slug": str(getattr(to_agent, "slug", "") or ""),
        }
        if to_agent
        else {},
        "conversationId": str(request.conversation_id) if request.conversation_id else None,
        "agentRunId": str(request.agent_run_id) if request.agent_run_id else None,
        "createdAt": request.created_at.isoformat() if request.created_at else None,
        "updatedAt": request.updated_at.isoformat() if request.updated_at else None,
        "resolvedAt": request.resolved_at.isoformat() if request.resolved_at else None,
    }


def serialize_conversation_message_for_portal(msg: ConversationMessage) -> dict[str, object]:
    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    content_blocks = msg.content_blocks if isinstance(getattr(msg, "content_blocks", None), list) else []
    return {
        "id": str(msg.id),
        "sender": msg.sender,
        "body": msg.body or "",
        "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
        "content_blocks": content_blocks,
    }

