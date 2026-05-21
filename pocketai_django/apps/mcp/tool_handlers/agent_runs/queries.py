from __future__ import annotations

import uuid
from typing import Mapping

from django.conf import settings
from django.core.cache import cache

from apps.agent_runs.models import AgentRun, AgentRunEvent, AgentRunStatus
from apps.conversations.models import Conversation
from core.tenancy import tenant_context

from ...types import ToolExecutionContext


def _list_agent_runs_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """List agent runs for the current conversation."""
    del context

    status_filter = str(arguments.get("status_filter") or "all").strip().lower()
    limit = min(20, max(1, int(arguments.get("limit") or 10)))
    refresh = bool(arguments.get("refresh"))

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return {
            "tool": "list_agent_runs",
            "status": "error",
            "error_code": "missing_business",
            "error": "Conversation missing business_profile_id.",
        }

    # Map status filter to actual statuses
    status_mapping = {
        "all": None,
        "active": [AgentRunStatus.QUEUED, AgentRunStatus.RUNNING],
        "waiting": [AgentRunStatus.WAITING_USER, AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_EXTERNAL, AgentRunStatus.PAUSED],
        "completed": [AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED],
    }
    statuses = status_mapping.get(status_filter)

    try:
        cache_ttl = int(getattr(settings, "MCP_LIST_AGENT_RUNS_CACHE_TTL_SECONDS", 5) or 5)
    except (TypeError, ValueError):
        cache_ttl = 5
    cache_ttl = max(0, min(cache_ttl, 60))
    cache_key = f"mcp:list_agent_runs:{business_id}:{conversation.id}:{status_filter}:{limit}"

    if not refresh and cache_ttl > 0:
        cached = cache.get(cache_key)
        if isinstance(cached, Mapping):
            return dict(cached)

    with tenant_context(business_id):
        qs = AgentRun.objects.filter(conversation_id=conversation.id).order_by("-created_at")
        if statuses:
            qs = qs.filter(status__in=statuses)
        runs = list(qs[:limit])

    run_summaries = []
    for run in runs:
        result_payload = run.result if isinstance(getattr(run, "result", None), dict) else {}
        response_text = str(result_payload.get("response_text") or "").strip()
        # Truncate for summary
        if len(response_text) > 500:
            response_text = response_text[:497] + "..."

        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        pending_approval_id = str(meta.get("pending_approval_id") or "").strip()
        pending_user_input = bool(meta.get("pending_user_input"))

        run_summaries.append({
            "id": str(run.id),
            "title": run.title or "",
            "status": run.status,
            "source": run.source,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "response_preview": response_text or None,
            "waiting_for": (
                "approval" if pending_approval_id else
                "user_input" if pending_user_input else
                None
            ),
        })

    response = {
        "tool": "list_agent_runs",
        "status": "ok",
        "runs": run_summaries,
        "count": len(run_summaries),
        "filter": status_filter,
    }
    if cache_ttl > 0:
        cache.set(cache_key, dict(response), timeout=cache_ttl)
    return response


def _get_agent_run_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """Get detailed status of a specific agent run."""
    del context

    run_id_raw = str(arguments.get("run_id") or "").strip()
    include_events = bool(arguments.get("include_events"))

    if not run_id_raw:
        return {
            "tool": "get_agent_run",
            "status": "error",
            "error_code": "missing_run_id",
            "error": "run_id is required.",
        }

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "get_agent_run",
            "status": "error",
            "error_code": "invalid_run_id",
            "error": "run_id is not a valid UUID.",
        }

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return {
            "tool": "get_agent_run",
            "status": "error",
            "error_code": "missing_business",
            "error": "Conversation missing business_profile_id.",
        }

    with tenant_context(business_id):
        run = AgentRun.objects.filter(
            id=run_uuid,
            conversation_id=conversation.id,
        ).first()

        if not run:
            return {
                "tool": "get_agent_run",
                "status": "error",
                "error_code": "not_found",
                "error": f"Run {run_id_raw} not found in this conversation.",
            }

        result_payload = run.result if isinstance(getattr(run, "result", None), dict) else {}
        response_text = str(result_payload.get("response_text") or "").strip()
        # Allow longer text for detailed view so LLM can use the full result
        if len(response_text) > 8000:
            response_text = response_text[:7997] + "..."

        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        pending_approval_id = str(meta.get("pending_approval_id") or "").strip()
        pending_user_input = meta.get("pending_user_input") if isinstance(meta.get("pending_user_input"), dict) else None

        run_detail: dict[str, object] = {
            "id": str(run.id),
            "title": run.title or "",
            "status": run.status,
            "source": run.source,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "attempt_count": run.attempt_count,
            "error_detail": run.error_detail or None,
            "response_text": response_text or None,
        }

        # Add waiting context if applicable
        if pending_approval_id:
            run_detail["waiting_for"] = "approval"
            run_detail["pending_approval_id"] = pending_approval_id
        elif pending_user_input:
            run_detail["waiting_for"] = "user_input"
            questions = pending_user_input.get("questions") if isinstance(pending_user_input, dict) else []
            if isinstance(questions, list):
                run_detail["pending_questions"] = [str(q)[:200] for q in questions[:5]]

        # Include recent events if requested
        if include_events:
            events = list(
                AgentRunEvent.objects.filter(run_id=run.id)
                .order_by("-sequence_index")[:15]
            )
            run_detail["recent_events"] = [
                {
                    "sequence": event.sequence_index,
                    "stream": event.stream,
                    "type": event.event_type,
                    "label": event.label,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
                for event in reversed(events)
            ]

    return {
        "tool": "get_agent_run",
        "status": "ok",
        "run": run_detail,
    }

