"""
Background AgentRun MCP tool handlers.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Mapping

from django.conf import settings
from django.core.cache import cache

from apps.agent_runs.models import AgentRun, AgentRunEvent, AgentRunStatus
from apps.conversations.models import Conversation
from core.tenancy import tenant_context

from ..types import ToolExecutionContext


def _request_user_input_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    prompt = str(arguments.get("prompt") or arguments.get("question") or "").strip()
    raw_questions = arguments.get("questions")
    questions: list[str] = []
    if isinstance(raw_questions, list):
        for item in raw_questions:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                questions.append(text)
    if not questions and prompt:
        questions = [prompt]
    if not questions:
        return {
            "tool": "request_user_input",
            "status": "error",
            "error_code": "validation_failed",
            "error": "missing_prompt",
            "hint": "Provide prompt or questions for request_user_input.",
        }

    schema = arguments.get("schema")
    schema_payload = dict(schema) if isinstance(schema, Mapping) else {}
    return {
        "tool": "request_user_input",
        "status": "needs_user",
        "questions": questions[:10],
        "schema": schema_payload,
        "hint": "Awaiting user input. Ask the user, then resume after they reply.",
    }


def _start_agent_run_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context

    convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    convo_source = str(convo_meta.get("source") or "").strip().lower()
    parent_run_id_raw = str(convo_meta.get("agent_run_id") or convo_meta.get("agentRunId") or "").strip()

    agent_profile = getattr(conversation, "agent_profile", None)
    if not agent_profile:
        return {
            "tool": "start_agent_run",
            "status": "error",
            "error_code": "missing_agent_profile",
            "error": "missing_agent_profile",
            "hint": "Conversation must be linked to an agent_profile to create runs.",
        }

    goal = str(arguments.get("goal") or "").strip()
    if not goal:
        return {
            "tool": "start_agent_run",
            "status": "error",
            "error_code": "validation_failed",
            "error": "missing_goal",
            "hint": "Provide goal for start_agent_run.",
        }

    title = str(arguments.get("title") or "").strip()
    if not title:
        title = (goal[:200].strip() or "Background run").rstrip()

    followup_mode = str(arguments.get("followup_mode") or "").strip().lower() or "handoff"
    if followup_mode not in {"handoff", "supervisor"}:
        followup_mode = "handoff"

    actor_raw = str(convo_meta.get("actor_user_id") or convo_meta.get("actorUserId") or "").strip()
    actor_id: uuid.UUID | None = None
    if actor_raw:
        try:
            actor_id = uuid.UUID(actor_raw)
        except (TypeError, ValueError):
            actor_id = None

    business_owner_id = getattr(getattr(conversation, "business_profile", None), "user_id", None)
    agent_user_id = getattr(agent_profile, "user_id", None)

    if not actor_id:
        return {
            "tool": "start_agent_run",
            "status": "error",
            "error_code": "missing_actor_user",
            "error": "missing_actor_user",
            "hint": "Authenticated actor_user_id is required to start background runs.",
        }

    if actor_id not in {business_owner_id, agent_user_id}:
        return {
            "tool": "start_agent_run",
            "status": "error",
            "error_code": "forbidden",
            "error": "forbidden",
            "hint": "Actor is not permitted to start runs for this business.",
        }

    success_raw = arguments.get("success_criteria")
    success_criteria: list[str] = []
    if isinstance(success_raw, list):
        for item in success_raw[:20]:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                success_criteria.append(text[:280])
    constraints = arguments.get("constraints")
    constraints_payload = dict(constraints) if isinstance(constraints, Mapping) else {}
    output_schema = arguments.get("output_schema")
    output_schema_payload = dict(output_schema) if isinstance(output_schema, Mapping) else {}
    approval = arguments.get("approval")
    approval_payload = dict(approval) if isinstance(approval, Mapping) else {}
    metadata = arguments.get("metadata")
    metadata_payload = dict(metadata) if isinstance(metadata, Mapping) else {}

    delegate_mode_enabled = bool(convo_meta.get("delegate_mode") or convo_meta.get("delegateMode"))
    explicit_delegate = False
    trigger_message_id = ""
    last_customer_body = ""
    try:
        last_customer = (
            conversation.messages.filter(sender="customer")
            .order_by("-sent_at", "-created_at")
            .values("id", "body")
            .first()
        )
    except Exception:  # pragma: no cover - best effort only
        last_customer = None
    if isinstance(last_customer, Mapping):
        trigger_message_id = str(last_customer.get("id") or "").strip()
        last_customer_body = str(last_customer.get("body") or "").strip()
    if last_customer_body:
        needle = last_customer_body.lower()
        tokens = (
            "delegate",
            "delegat",
            "subagent",
            "background agent",
            "specialist agent",
            "background",
            "in the background",
            "run this in background",
            "offload",
            "hand off",
            "spawn",
        )
        explicit_delegate = any(t in needle for t in tokens)
    delegate_intent = "explicit" if explicit_delegate else "implicit"
    followup_requested = bool(explicit_delegate or delegate_mode_enabled)

    visibility = str(arguments.get("visibility") or "initiator").strip().lower()
    if visibility not in {"initiator", "managers", "workspace"}:
        visibility = "initiator"

    from django.db import transaction
    from django.db.models import Max
    from django.utils import timezone as django_timezone

    from apps.agent_runs.models import (
        AgentRun,
        AgentRunCheckpoint,
        AgentRunCheckpointKind,
        AgentRunCheckpointStatus,
        AgentRunEvent,
        AgentRunEventStream,
        AgentRunEventType,
        AgentRunSource,
        AgentRunStatus,
    )
    from apps.conversations.models import (
        Conversation,
        ConversationChannel,
    )
    from apps.conversations.instruction_contracts import normalize_workflow_instructions

    run_snapshot = normalize_workflow_instructions(
        {
            "version": 1,
            "goal": goal[:6000],
            "success_criteria": success_criteria[:10],
            **({"constraints": constraints_payload} if constraints_payload else {}),
            **({"output_schema": output_schema_payload} if output_schema_payload else {}),
            **({"approval": approval_payload} if approval_payload else {}),
            "visibility": visibility,
            "metadata": metadata_payload,
        }
    )

    plan = arguments.get("plan")
    plan_payload = dict(plan) if isinstance(plan, Mapping) else {}

    now = django_timezone.now()
    with transaction.atomic():
        parent_run = None
        if convo_source == "agent_run" and parent_run_id_raw:
            try:
                parent_run_uuid = uuid.UUID(parent_run_id_raw)
            except (TypeError, ValueError):
                parent_run_uuid = None
            if parent_run_uuid:
                parent_run = AgentRun.objects.select_for_update().filter(
                    id=parent_run_uuid,
                    business_profile_id=conversation.business_profile_id,
                ).first()

        existing_run = None
        if trigger_message_id and parent_run is None:
            # Dedup by trigger_message_id AND title to allow multiple distinct runs
            # from the same user message while preventing true duplicates.
            existing_run = (
                AgentRun.objects.filter(
                    business_profile_id=conversation.business_profile_id,
                    conversation_id=conversation.id,
                    created_by_id=actor_id,
                    source=AgentRunSource.CHAT,
                    title=title,  # Different titles = different runs
                )
                .exclude(status__in={AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED})
                .filter(metadata__trigger_message_id=trigger_message_id)
                .order_by("-created_at")
                .first()
            )

        if existing_run is not None:
            next_meta = dict(existing_run.metadata or {}) if isinstance(getattr(existing_run, "metadata", None), dict) else {}
            changed = False
            if trigger_message_id and next_meta.get("trigger_message_id") != trigger_message_id:
                next_meta["trigger_message_id"] = trigger_message_id
                changed = True
            if delegate_intent == "explicit" and next_meta.get("delegate_intent") != "explicit":
                next_meta["delegate_intent"] = "explicit"
                changed = True
            if followup_requested and not bool(next_meta.get("followup_requested")):
                next_meta["followup_requested"] = True
                changed = True
            if next_meta.get("followup_mode") != followup_mode:
                next_meta["followup_mode"] = followup_mode
                changed = True
            if changed:
                AgentRun.objects.filter(id=existing_run.id).update(metadata=next_meta, updated_at=now)
                existing_run.metadata = next_meta
            return {
                "tool": "start_agent_run",
                "status": "ok",
                "run_id": str(existing_run.id),
                "deduped": True,
                "run": {
                    "id": str(existing_run.id),
                    "title": existing_run.title,
                    "status": existing_run.status,
                    "source": existing_run.source,
                    "visibility": existing_run.visibility,
                },
                "hint": "Background run already queued for this message. Watch the Activity panel for progress.",
            }

        run = AgentRun.objects.create(
            business_profile_id=conversation.business_profile_id,
            agent_profile_id=agent_profile.id,
            conversation_id=parent_run.conversation_id if parent_run is not None else conversation.id,
            created_by_id=actor_id,
            automation=parent_run.automation if parent_run is not None else None,
            parent_run=parent_run,
            delegated_by_agent_id=agent_profile.id if parent_run is not None else None,
            run_snapshot=run_snapshot,
            title=title[:200],
            source=AgentRunSource.DELEGATION if parent_run is not None else AgentRunSource.CHAT,
            status=AgentRunStatus.QUEUED,
            visibility=visibility,
            plan=plan_payload,
            metadata={
                **metadata_payload,
                "source": "mcp_tool",
                "trigger_message_id": trigger_message_id,
                "delegate_intent": delegate_intent,
                "followup_requested": followup_requested,
                "followup_mode": followup_mode,
            },
            run_after=now,
        )
        exec_metadata: dict[str, object] = {
            "source": "agent_run",
            "agent_run_id": str(run.id),
        }
        if parent_run is None:
            exec_metadata["anchor_conversation_id"] = str(conversation.id)
        elif parent_run.conversation_id:
            exec_metadata["anchor_conversation_id"] = str(parent_run.conversation_id)
            exec_metadata["parent_run_id"] = str(parent_run.id)
        if actor_id:
            exec_metadata["actor_user_id"] = str(actor_id)
        execution_conversation = Conversation.objects.create(
            business_profile_id=conversation.business_profile_id,
            agent_profile_id=agent_profile.id,
            channel=ConversationChannel.API,
            metadata=exec_metadata,
        )
        run.execution_conversation = execution_conversation
        run.metadata = {
            **(run.metadata or {}),
            "execution_conversation_id": str(execution_conversation.id),
        }
        run.save(update_fields=["execution_conversation", "metadata", "updated_at"])
        AgentRunEvent.objects.create(
            run=run,
            sequence_index=1,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Queued",
            payload={"status": AgentRunStatus.QUEUED},
        )
        if parent_run is not None:
            checkpoint = AgentRunCheckpoint.objects.create(
                business_profile=parent_run.business_profile,
                automation=parent_run.automation,
                run=parent_run,
                conversation=parent_run.conversation,
                child_run=run,
                kind=AgentRunCheckpointKind.CHILD_RUN,
                status=AgentRunCheckpointStatus.OPEN,
                title="Waiting for delegated run",
                prompt=f"Waiting for delegated run: {run.title}",
                payload={"child_run_id": str(run.id), "title": run.title},
                expires_at=now + timedelta(hours=24),
            )
            parent_meta = dict(parent_run.metadata or {}) if isinstance(getattr(parent_run, "metadata", None), dict) else {}
            parent_meta["pending_child_run_id"] = str(run.id)
            parent_meta["pending_checkpoint_id"] = str(checkpoint.id)
            AgentRun.objects.filter(id=parent_run.id).update(
                status=AgentRunStatus.WAITING_CHILD,
                lease_expires_at=None,
                run_after=None,
                metadata=parent_meta,
                updated_at=now,
            )
            next_index = (AgentRunEvent.objects.filter(run=parent_run).aggregate(Max("sequence_index")).get("sequence_index__max") or 0) + 1
            AgentRunEvent.objects.create(
                run=parent_run,
                sequence_index=next_index,
                stream=AgentRunEventStream.SYSTEM,
                event_type=AgentRunEventType.NEEDS_CHILD,
                label="Waiting for delegated run",
                payload={"child_run_id": str(run.id), "checkpoint_id": str(checkpoint.id)},
            )

    return {
        "tool": "start_agent_run",
        "status": "ok",
        "run_id": str(run.id),
        "run": {
            "id": str(run.id),
            "title": run.title,
            "status": run.status,
            "source": run.source,
            "visibility": run.visibility,
        },
        "hint": "Delegated run queued; the parent run will resume when it finishes." if run.parent_run_id else "Background run queued. Watch the Activity panel for progress.",
    }


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


def _continue_agent_run_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """Continue an existing agent run with a follow-up message."""
    del context
    from django.utils import timezone
    from apps.conversations.models import ConversationMessage, ConversationSender

    run_id_raw = str(arguments.get("run_id") or "").strip()
    message = str(arguments.get("message") or "").strip()

    if not run_id_raw:
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "missing_run_id",
            "error": "run_id is required.",
        }

    if not message:
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "missing_message",
            "error": "message is required.",
        }

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "invalid_run_id",
            "error": "run_id is not a valid UUID.",
        }

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return {
            "tool": "continue_agent_run",
            "status": "error",
            "error_code": "missing_business",
            "error": "Conversation missing business_profile_id.",
        }

    # States that can be continued
    continuable_states = {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.WAITING_USER,
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.WAITING_EXTERNAL,
        AgentRunStatus.PAUSED,
    }

    with tenant_context(business_id):
        run = AgentRun.objects.filter(
            id=run_uuid,
            conversation_id=conversation.id,
        ).first()

        if not run:
            return {
                "tool": "continue_agent_run",
                "status": "error",
                "error_code": "not_found",
                "error": f"Run {run_id_raw} not found in this conversation.",
            }

        if run.status not in continuable_states:
            return {
                "tool": "continue_agent_run",
                "status": "error",
                "error_code": "not_continuable",
                "error": f"Run is currently '{run.status}' and cannot be continued. Wait for it to complete or pause.",
                "hint": "Runs can only be continued when completed, failed, waiting, or paused.",
            }

        meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        next_spec_snapshot = None

        execution_conversation = None
        if run.execution_conversation_id:
            execution_conversation = Conversation.objects.filter(
                id=run.execution_conversation_id,
                business_profile_id=business_id,
            ).first()

        if execution_conversation is None:
            return {
                "tool": "continue_agent_run",
                "status": "error",
                "error_code": "no_execution_conversation",
                "error": "Run has no execution conversation to continue. The run may not have started yet.",
                "hint": "Wait for the run to start processing before continuing it.",
            }

        now = timezone.now()

        # Append the follow-up message to the execution conversation
        ConversationMessage.objects.create(
            conversation=execution_conversation,
            sender=ConversationSender.CUSTOMER,
            body=message,
            metadata={
                "source": "agent_run_continuation",
                "agent_run_id": str(run.id),
                "from_conversation_id": str(conversation.id),
                "type": "orchestrator_followup",
            },
        )
        Conversation.objects.filter(id=execution_conversation.id).update(last_activity_at=now)

        # Update run metadata to track continuation
        next_meta = dict(meta)
        continuations = next_meta.get("continuations") or []
        if not isinstance(continuations, list):
            continuations = []
        continuations.append({
            "at": now.isoformat(),
            "from_status": run.status,
            "message_preview": message[:200],
        })
        next_meta["continuations"] = continuations[-10:]  # Keep last 10
        next_meta.pop("pending_approval_id", None)
        next_meta.pop("pending_user_input", None)
        next_meta.pop("pending_tool_call", None)

        # Re-queue the run
        update_fields = {
            "status": AgentRunStatus.QUEUED,
            "run_after": now,
            "lease_expires_at": None,
            "finished_at": None,
            "error_detail": "",
            "metadata": next_meta,
            "updated_at": now,
        }
        if next_spec_snapshot is not None:
            update_fields["run_snapshot"] = next_spec_snapshot

        AgentRun.objects.filter(id=run.id).update(**update_fields)

        # Log the continuation event
        from apps.agent_runs.models import AgentRunEventStream, AgentRunEventType
        from django.db.models import Max

        next_index = (
            AgentRunEvent.objects.filter(run_id=run.id).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
        )
        AgentRunEvent.objects.create(
            run_id=run.id,
            sequence_index=int(next_index) + 1,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Continued by orchestrator",
            payload={
                "from_status": run.status,
                "message_preview": message[:200],
                "from_conversation_id": str(conversation.id),
            },
        )

    return {
        "tool": "continue_agent_run",
        "status": "ok",
        "run_id": str(run.id),
        "previous_status": run.status,
        "new_status": AgentRunStatus.QUEUED,
        "message_appended": True,
        "hint": "Run re-queued with your follow-up message. It will continue with full conversation history.",
    }
