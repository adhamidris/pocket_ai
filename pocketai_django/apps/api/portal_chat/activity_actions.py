from __future__ import annotations

import hashlib
import json
import logging
import uuid

from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.agent_runs.models import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunCheckpointKind,
    AgentRunCheckpointStatus,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunStatus,
)
from apps.api.portal_chat.activity_snapshots import (
    _append_agent_run_event,
    _create_portal_manual_agentic_task_run,
    _is_runnable_agentic_task,
    _serialize_agent_request_for_portal,
    _serialize_agent_run_checkpoint_for_portal,
    _serialize_agent_run_for_portal,
    _serialize_agentic_task_for_portal,
)
from apps.api.portal_chat.request_context import (
    _json_error,
    _parse_json_body,
    _resolve_request_conversation,
    _service,
)
from apps.api.portal_chat.serializers import (
    _apply_portal_tool_approval_state,
    _session_to_dict,
)
from apps.agentic_tasks.models import AgenticTask, AgenticTaskStatus
from apps.agentic_tasks.processing import ensure_task_conversation
from apps.agentic_tasks.scheduling import CronScheduleError, compute_next_agentic_task_schedule_at
from apps.conversations.models import (
    AgentRequest,
    AgentRequestStatus,
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryVisibility,
)
from apps.conversations.portal import (
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalValidationError,
)
from apps.rag.observability.logging import structured_log
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


def _patch_agentic_task_approval_blocks(
    *,
    conversation: Conversation,
    message_id: str,
    block_id: str,
    agentic_task_id: str,
    status: str,
    agentic_task_payload: dict[str, object] | None = None,
) -> None:
    qs = ConversationMessage.objects.filter(conversation=conversation, sender=ConversationSender.AI)
    if message_id:
        qs = qs.filter(id=message_id)
    else:
        qs = qs.order_by("-created_at")[:8]
    for message in list(qs):
        blocks = message.content_blocks if isinstance(getattr(message, "content_blocks", None), list) else []
        changed = False
        updated_blocks: list[dict[str, object]] = []
        for block in blocks:
            if not isinstance(block, dict):
                updated_blocks.append(block)
                continue
            block_type = str(block.get("type") or "").strip().lower()
            current_block_id = str(block.get("block_id") or block.get("blockId") or "").strip()
            payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}
            current_task_id = str(
                payload.get("agenticTaskId")
                or payload.get("agentic_task_id")
                or (payload.get("agenticTask") if isinstance(payload.get("agenticTask"), dict) else {}).get("id")
                or ""
            ).strip()
            if block_type == "agentic_task_approval" and current_task_id == agentic_task_id and (not block_id or current_block_id == block_id):
                next_payload = dict(payload)
                next_payload["status"] = status
                if agentic_task_payload:
                    next_payload["agenticTask"] = dict(agentic_task_payload)
                next_block = dict(block)
                next_block["payload"] = next_payload
                updated_blocks.append(next_block)
                changed = True
                continue
            if block_type in {"tool_use", "tool_result"}:
                next_payload = dict(payload)
                patched_payload = False
                for output_key in ("output_preview", "outputPreview", "output"):
                    output = next_payload.get(output_key)
                    if not isinstance(output, dict):
                        continue
                    task_payload = output.get("agentic_task") if isinstance(output.get("agentic_task"), dict) else output.get("agenticTask")
                    if not isinstance(task_payload, dict):
                        continue
                    if str(task_payload.get("id") or "").strip() != agentic_task_id:
                        continue
                    next_output = dict(output)
                    next_output["approval_status"] = status
                    next_output["approvalStatus"] = status
                    if agentic_task_payload:
                        next_output["agentic_task"] = dict(agentic_task_payload)
                        next_output["agenticTask"] = dict(agentic_task_payload)
                    next_payload[output_key] = next_output
                    patched_payload = True
                if patched_payload and (not block_id or current_block_id == block_id):
                    next_block = dict(block)
                    next_block["payload"] = next_payload
                    updated_blocks.append(next_block)
                    changed = True
                    continue
                if patched_payload and block_id and current_block_id != block_id:
                    next_block = dict(block)
                    next_block["payload"] = next_payload
                    updated_blocks.append(next_block)
                    changed = True
                    continue
            else:
                updated_blocks.append(block)
                continue
            updated_blocks.append(block)
        if changed:
            message.content_blocks = updated_blocks
            message.save(update_fields=["content_blocks"])


def _agentic_task_block_payload(agentic_task: AgenticTask) -> dict[str, object]:
    instructions = agentic_task.instructions if isinstance(getattr(agentic_task, "instructions", None), dict) else {}
    return {
        "id": str(agentic_task.id),
        "agentId": str(agentic_task.agent_profile_id),
        "agentName": getattr(getattr(agentic_task, "agent_profile", None), "name", "") or "",
        "name": agentic_task.name,
        "description": agentic_task.description or "",
        "status": agentic_task.status,
        "visibility": agentic_task.visibility,
        "scheduleEnabled": bool(agentic_task.schedule_enabled),
        "scheduleConfig": agentic_task.schedule_config if isinstance(agentic_task.schedule_config, dict) else {},
        "goal": str(instructions.get("goal") or ""),
        "instructions": dict(instructions),
    }


def _agentic_task_has_cron(agentic_task: AgenticTask) -> bool:
    schedule_config = agentic_task.schedule_config if isinstance(agentic_task.schedule_config, dict) else {}
    return bool(str(schedule_config.get("cron") or "").strip())


@require_POST
def portal_agent_run_user_input(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    run_id_raw = (payload.get("run_id") or payload.get("runId") or "").strip()
    message = str(payload.get("message") or "").strip()
    extra = payload.get("payload")
    extra_payload = dict(extra) if isinstance(extra, dict) else {}

    if not run_id_raw:
        return _json_error("validation_error", "conversation_id/session_token and run_id are required.")
    if not message and not extra_payload:
        return _json_error("validation_error", "message or payload is required.")

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "run_id is invalid.")

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    if actor_user:
        actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
    else:
        actor_snapshot = {
            "type": "portal_session",
            "session_hash": hashlib.sha256(conversation.session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
        }

    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        run = AgentRun.objects.filter(id=run_uuid, conversation_id=conversation.id).first()
        if run is None:
            return _json_error("not_found", "Run not found.", status=404)

        if run.status not in {AgentRunStatus.WAITING_USER, AgentRunStatus.PAUSED, AgentRunStatus.WAITING_EXTERNAL}:
            return _json_error("run_not_waiting_user", "Run is not waiting for user input.", status=409)

        _append_agent_run_event(
            run,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.PROGRESS,
            label="User input received",
            payload={"message": message, "payload": extra_payload} if extra_payload else {"message": message},
        )
        MemoryItem.objects.create(
            business_profile=run.business_profile,
            scope=MemoryScope.RUN,
            agent_profile=run.agent_profile,
            agentic_task=run.agentic_task,
            run=run,
            conversation=run.conversation,
            kind=MemoryKind.STATE_NOTE,
            key="user_input",
            content=message[:4000],
            payload={"actor": actor_snapshot, "payload": extra_payload},
            visibility=MemoryVisibility.PRIVATE,
            created_by=actor_user,
        )
        if run.agentic_task_id:
            MemoryItem.objects.create(
                business_profile=run.business_profile,
                scope=MemoryScope.TASK,
                agent_profile=run.agent_profile,
                agentic_task=run.agentic_task,
                run=run,
                conversation=run.conversation,
                kind=MemoryKind.STATE_NOTE,
                key="user_input",
                content=message[:4000],
                payload={"actor": actor_snapshot, "payload": extra_payload, "source_run_id": str(run.id)},
                visibility=MemoryVisibility.SHARED,
                created_by=actor_user,
            )

        next_meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        next_meta = dict(next_meta)
        next_meta.pop("pending_user_input", None)

        AgentRun.objects.filter(id=run.id).update(
            status=AgentRunStatus.QUEUED,
            run_after=timezone.now(),
            lease_expires_at=None,
            error_detail="",
            metadata=next_meta,
            updated_at=timezone.now(),
        )
        run.refresh_from_db()

    try:
        if message:
                service.append_message(
                    session_token=conversation.session_token,
                    sender=ConversationSender.CUSTOMER,
                    body=message,
                metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "user_input"},
                conversation=conversation,
            )
    except Exception:  # pragma: no cover - chat transcript should not block execution
        logger.exception("portal_agent_run_user_input_message_failed run=%s", run_uuid)

    # Also append into the run's isolated execution conversation so the run can continue
    # with a true session transcript (no restart / resume hacks).
    try:
        execution_conversation = None
        if run and run.execution_conversation_id and business_id:
            with tenant_context(business_id):
                execution_conversation = Conversation.objects.filter(
                    id=run.execution_conversation_id,
                    business_profile_id=business_id,
                ).first()
        if execution_conversation:
            body = message
            if not body and extra_payload:
                body = "User input payload:\n" + json.dumps(extra_payload, ensure_ascii=False)
            if body:
                service.append_message(
                    session_token=execution_conversation.session_token,
                    sender=ConversationSender.CUSTOMER,
                    body=body,
                    metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "user_input"},
                    conversation=execution_conversation,
                )
    except Exception:  # pragma: no cover - best effort only
        logger.exception("portal_agent_run_user_input_execution_message_failed run=%s", run_uuid)

    return JsonResponse({"session": _session_to_dict(session), "run": _serialize_agent_run_for_portal(run)}, status=200)


@require_POST
def portal_agentic_task_manual_run(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    agentic_task_id_raw = str(payload.get("agenticTaskId") or payload.get("agentic_task_id") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not agentic_task_id_raw:
        return _json_error("validation_error", "agentic_task_id is required.")
    try:
        agentic_task_uuid = uuid.UUID(agentic_task_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "agentic_task_id is invalid.")

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    business_id = getattr(conversation, "business_profile_id", None)
    agent_profile_id = getattr(conversation, "agent_profile_id", None)
    if not business_id or not agent_profile_id:
        return _json_error("validation_error", "The current portal session is not linked to an agent.")

    with tenant_context(business_id):
        agentic_task = (
            AgenticTask.objects.select_related("agent_profile", "business_profile", "created_by")
            .filter(
                id=agentic_task_uuid,
                business_profile_id=business_id,
                agent_profile_id=agent_profile_id,
            )
            .first()
        )
        if agentic_task is None:
            return _json_error("not_found", "Agentic Task not found.", status=404)
        if not _is_runnable_agentic_task(agentic_task):
            return _json_error("validation_error", "Agentic Task cannot be run.")
        task_conversation = ensure_task_conversation(agentic_task)
        if message:
            service.append_message(
                session_token=task_conversation.session_token,
                sender=ConversationSender.CUSTOMER,
                body=message,
                metadata={"source": "agentic_task_panel", "agentic_task_id": str(agentic_task.id), "type": "user_message"},
                conversation=task_conversation,
            )
        run = _create_portal_manual_agentic_task_run(
            agentic_task=agentic_task,
            created_by=actor_user,
            portal_conversation=conversation,
        )
        if message:
            _append_agent_run_event(
                run,
                stream=AgentRunEventStream.EXECUTED,
                event_type=AgentRunEventType.PROGRESS,
                label="User message received",
                payload={"message": message, "agentic_task_id": str(agentic_task.id)},
            )
            if run.status in {AgentRunStatus.WAITING_USER, AgentRunStatus.PAUSED, AgentRunStatus.WAITING_EXTERNAL}:
                MemoryItem.objects.create(
                    business_profile=run.business_profile,
                    scope=MemoryScope.TASK,
                    agent_profile=run.agent_profile,
                    agentic_task=run.agentic_task,
                    run=run,
                    conversation=task_conversation,
                    kind=MemoryKind.STATE_NOTE,
                    key="user_input",
                    content=message[:4000],
                    payload={"source": "agentic_task_panel", "source_run_id": str(run.id)},
                    visibility=MemoryVisibility.SHARED,
                    created_by=actor_user,
                )
                next_meta = dict(run.metadata or {}) if isinstance(run.metadata, dict) else {}
                next_meta.pop("pending_user_input", None)
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.QUEUED,
                    run_after=timezone.now(),
                    lease_expires_at=None,
                    error_detail="",
                    metadata=next_meta,
                    updated_at=timezone.now(),
                )
                run.refresh_from_db()
        AgenticTask.objects.filter(id=agentic_task.id).update(last_triggered_at=timezone.now(), updated_at=timezone.now())
        agentic_task.refresh_from_db()
        task_messages = list(task_conversation.messages.order_by("-sent_at", "-created_at")[:40])
        recent_runs = list(
            AgentRun.objects.select_related("agentic_task")
            .filter(agentic_task=agentic_task, business_profile_id=business_id, agent_profile_id=agent_profile_id)
            .exclude(id=run.id)
            .order_by("-created_at")[:5]
        )

    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "agenticTask": _serialize_agentic_task_for_portal(agentic_task, latest_run=run, recent_runs=recent_runs, messages=list(reversed(task_messages))),
            "run": _serialize_agent_run_for_portal(run),
        },
        status=201,
    )


@require_POST
def portal_agentic_task_approval(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    agentic_task_id_raw = str(payload.get("agenticTaskId") or payload.get("agentic_task_id") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    message_id = str(payload.get("messageId") or payload.get("message_id") or "").strip()
    block_id = str(payload.get("blockId") or payload.get("block_id") or "").strip()
    if action not in {"approve", "reject", "edit"}:
        return _json_error("validation_error", "Invalid Agentic Task approval action.")
    try:
        agentic_task_uuid = uuid.UUID(agentic_task_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "agentic_task_id is invalid.")

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    business_id = getattr(conversation, "business_profile_id", None)
    agent_profile_id = getattr(conversation, "agent_profile_id", None)
    with tenant_context(business_id):
        agentic_task = (
            AgenticTask.objects.select_related("agent_profile", "business_profile", "created_by")
            .filter(
                id=agentic_task_uuid,
                business_profile_id=business_id,
                agent_profile_id=agent_profile_id,
                status=AgenticTaskStatus.DRAFT,
            )
            .first()
        )
        if agentic_task is None:
            return _json_error("not_found", "Draft Agentic Task not found.", status=404)

        edits = payload.get("edits") if isinstance(payload.get("edits"), dict) else {}
        if action in {"approve", "edit"} and edits:
            updates: list[str] = []
            if "name" in edits:
                name = str(edits.get("name") or "").strip()
                if not name:
                    return _json_error("validation_error", "Task name is required.")
                agentic_task.name = name[:160]
                updates.append("name")
            if "goal" in edits:
                goal = str(edits.get("goal") or "").strip()
                if not goal:
                    return _json_error("validation_error", "Task goal is required.")
                instructions = dict(agentic_task.instructions or {}) if isinstance(agentic_task.instructions, dict) else {}
                instructions["goal"] = goal[:6000]
                agentic_task.instructions = instructions
                updates.append("instructions")
            if "scheduleEnabled" in edits or "schedule_enabled" in edits:
                agentic_task.schedule_enabled = bool(edits.get("scheduleEnabled") if "scheduleEnabled" in edits else edits.get("schedule_enabled"))
                updates.append("schedule_enabled")
            if "scheduleConfig" in edits or "schedule_config" in edits:
                schedule_config = edits.get("scheduleConfig") if "scheduleConfig" in edits else edits.get("schedule_config")
                agentic_task.schedule_config = dict(schedule_config or {}) if isinstance(schedule_config, dict) else {}
                updates.append("schedule_config")
            if updates:
                agentic_task.save(update_fields=sorted(set([*updates, "updated_at"])))

        if action == "reject":
            snapshot = _agentic_task_block_payload(agentic_task)
            agentic_task.delete()
            metadata = dict(conversation.metadata or {}) if isinstance(conversation.metadata, dict) else {}
            if str(metadata.get("pending_agentic_task_activation_id") or "") == agentic_task_id_raw:
                metadata.pop("pending_agentic_task_activation_id", None)
                conversation.metadata = metadata
                conversation.save(update_fields=["metadata", "last_activity_at"])
            _patch_agentic_task_approval_blocks(
                conversation=conversation,
                message_id=message_id,
                block_id=block_id,
                agentic_task_id=agentic_task_id_raw,
                status="rejected",
                agentic_task_payload=snapshot,
            )
            return JsonResponse({"session": _session_to_dict(session), "status": "rejected", "agenticTask": snapshot}, status=200)

        if action == "edit":
            agentic_task.refresh_from_db()
            snapshot = _agentic_task_block_payload(agentic_task)
            _patch_agentic_task_approval_blocks(
                conversation=conversation,
                message_id=message_id,
                block_id=block_id,
                agentic_task_id=agentic_task_id_raw,
                status="pending",
                agentic_task_payload=snapshot,
            )
            return JsonResponse({"session": _session_to_dict(session), "status": "pending", "agenticTask": snapshot}, status=200)

        next_trigger_at = None
        if agentic_task.schedule_enabled and not _agentic_task_has_cron(agentic_task):
            agentic_task.schedule_enabled = False
            agentic_task.schedule_config = {}
        if agentic_task.schedule_enabled:
            try:
                next_trigger_at = compute_next_agentic_task_schedule_at(
                    "cron",
                    dict(agentic_task.schedule_config or {}),
                    after=timezone.now(),
                )
            except CronScheduleError as exc:
                return _json_error("validation_error", str(exc))
        ensure_task_conversation(agentic_task)
        metadata = dict(agentic_task.metadata or {}) if isinstance(agentic_task.metadata, dict) else {}
        metadata["activated_from_chat_approval"] = True
        metadata["activated_by_user_id"] = str(getattr(actor_user, "id", "") or "") if actor_user else ""
        agentic_task.status = AgenticTaskStatus.ACTIVE
        agentic_task.next_trigger_at = next_trigger_at
        agentic_task.metadata = metadata
        agentic_task.save(update_fields=["status", "schedule_enabled", "schedule_config", "next_trigger_at", "metadata", "updated_at"])
        metadata = dict(conversation.metadata or {}) if isinstance(conversation.metadata, dict) else {}
        if str(metadata.get("pending_agentic_task_activation_id") or "") == agentic_task_id_raw:
            metadata.pop("pending_agentic_task_activation_id", None)
            conversation.metadata = metadata
            conversation.save(update_fields=["metadata", "last_activity_at"])
        agentic_task.refresh_from_db()
        snapshot = _agentic_task_block_payload(agentic_task)
        _patch_agentic_task_approval_blocks(
            conversation=conversation,
            message_id=message_id,
            block_id=block_id,
            agentic_task_id=agentic_task_id_raw,
            status="approved",
            agentic_task_payload=snapshot,
        )

    return JsonResponse({"session": _session_to_dict(session), "status": "approved", "agenticTask": snapshot}, status=200)


@require_POST
def portal_agent_run_checkpoint_resolve(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    checkpoint_id_raw = str(payload.get("checkpoint_id") or payload.get("checkpointId") or "").strip()
    action = str(payload.get("action") or payload.get("decision") or "resolve").strip().lower()
    message = str(payload.get("message") or payload.get("note") or "").strip()
    extra_payload = dict(payload.get("payload") or {}) if isinstance(payload.get("payload"), dict) else {}
    if not checkpoint_id_raw:
        return _json_error("validation_error", "checkpoint_id is required.")
    if action not in {"approve", "deny", "reply", "resolve", "cancel", "expire"}:
        return _json_error("validation_error", "Invalid checkpoint action.")
    try:
        checkpoint_uuid = uuid.UUID(checkpoint_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "checkpoint_id is invalid.")

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        checkpoint = (
            AgentRunCheckpoint.objects.select_related("run", "agentic_task")
            .filter(id=checkpoint_uuid, business_profile_id=business_id)
            .filter(Q(run__agent_profile_id=conversation.agent_profile_id) | Q(agentic_task__agent_profile_id=conversation.agent_profile_id))
            .first()
        )
        if checkpoint is None:
            return _json_error("not_found", "Checkpoint not found.", status=404)
        if checkpoint.status != AgentRunCheckpointStatus.OPEN:
            return _json_error("checkpoint_closed", "Checkpoint is already resolved.", status=409)
        run = checkpoint.run
        now = timezone.now()
        resolution = {"action": action, "message": message, "payload": extra_payload}
        checkpoint.status = AgentRunCheckpointStatus.EXPIRED if action == "expire" else (
            AgentRunCheckpointStatus.CANCELLED if action == "cancel" else AgentRunCheckpointStatus.RESOLVED
        )
        checkpoint.resolution = resolution
        checkpoint.resolved_at = now
        checkpoint.resolved_by = actor_user
        checkpoint.save(update_fields=["status", "resolution", "resolved_at", "resolved_by", "updated_at"])

        _append_agent_run_event(
            run,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.CANCELLED if action in {"deny", "cancel"} else AgentRunEventType.PROGRESS,
            label="Checkpoint resolved",
            payload={"checkpoint_id": str(checkpoint.id), **resolution},
        )
        MemoryItem.objects.create(
            business_profile=run.business_profile,
            scope=MemoryScope.RUN,
            agent_profile=run.agent_profile,
            agentic_task=run.agentic_task,
            run=run,
            conversation=run.conversation,
            kind=MemoryKind.DECISION if checkpoint.kind == AgentRunCheckpointKind.APPROVAL else MemoryKind.STATE_NOTE,
            key=f"checkpoint_{checkpoint.kind}",
            content=message[:4000] or action,
            payload={"checkpoint_id": str(checkpoint.id), **resolution},
            visibility=MemoryVisibility.PRIVATE,
            created_by=actor_user,
        )
        next_meta = dict(run.metadata or {}) if isinstance(getattr(run, "metadata", None), dict) else {}
        next_meta.pop("pending_checkpoint_id", None)
        next_meta.pop("pending_approval_id", None)
        next_meta.pop("pending_user_input", None)
        next_meta.pop("pending_child_run_id", None)
        if action in {"deny", "cancel"}:
            AgentRun.objects.filter(id=run.id).update(
                status=AgentRunStatus.CANCELLED,
                finished_at=now,
                lease_expires_at=None,
                run_after=None,
                error_detail=message or action,
                metadata=next_meta,
                updated_at=now,
            )
        elif action == "expire":
            AgentRun.objects.filter(id=run.id).update(
                status=AgentRunStatus.PAUSED,
                lease_expires_at=None,
                run_after=None,
                error_detail=message or "checkpoint expired",
                metadata=next_meta,
                updated_at=now,
            )
        else:
            execution_conversation = None
            if run.execution_conversation_id:
                execution_conversation = Conversation.objects.filter(id=run.execution_conversation_id, business_profile_id=business_id).first()
            if execution_conversation and (message or extra_payload):
                ConversationMessage.objects.create(
                    conversation=execution_conversation,
                    sender=ConversationSender.CUSTOMER,
                    body=message or "Checkpoint response:\n" + json.dumps(extra_payload, ensure_ascii=False),
                    metadata={"source": "agent_run_checkpoint", "agent_run_id": str(run.id), "checkpoint_id": str(checkpoint.id)},
                )
                Conversation.objects.filter(id=execution_conversation.id).update(last_activity_at=now)
            AgentRun.objects.filter(id=run.id).update(
                status=AgentRunStatus.QUEUED,
                run_after=now,
                lease_expires_at=None,
                finished_at=None,
                error_detail="",
                metadata=next_meta,
                updated_at=now,
            )
        run.refresh_from_db()
        checkpoint.refresh_from_db()

    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "checkpoint": _serialize_agent_run_checkpoint_for_portal(checkpoint),
            "run": _serialize_agent_run_for_portal(run),
        },
        status=200,
    )


@require_POST
def portal_agent_run_approval(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    run_id_raw = (payload.get("run_id") or payload.get("runId") or "").strip()
    approval_id_raw = (payload.get("approval_id") or payload.get("approvalId") or "").strip()
    decision_raw = payload.get("decision") or payload.get("action") or payload.get("status") or ""
    decision = str(decision_raw).strip().lower()

    if not run_id_raw or not decision:
        return _json_error("validation_error", "conversation_id/session_token, run_id, and decision are required.")

    if decision in {"approve", "approved", "allow"}:
        next_status = ConversationToolApprovalStatus.APPROVED
        decision_value = "approve"
    elif decision in {"deny", "denied", "reject"}:
        next_status = ConversationToolApprovalStatus.DENIED
        decision_value = "deny"
    else:
        return _json_error("validation_error", "decision must be approve or deny.")

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "run_id is invalid.")

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    if actor_user:
        actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
    else:
        actor_snapshot = {
            "type": "portal_session",
            "session_hash": hashlib.sha256(conversation.session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
        }

    run: AgentRun | None = None
    approval: ConversationToolApproval | None = None
    business_id = getattr(conversation, "business_profile_id", None)
    with transaction.atomic():
        with tenant_context(business_id):
            run = AgentRun.objects.select_for_update().filter(id=run_uuid, conversation_id=conversation.id).first()
            if run is None:
                return _json_error("not_found", "Run not found.", status=404)

            if run.status not in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.PAUSED}:
                return _json_error("run_not_waiting_approval", "Run is not waiting for approval.", status=409)

            meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
            pending_id = str(meta.get("pending_approval_id") or "").strip()
            if not pending_id:
                return _json_error("missing_pending_approval", "Run has no pending approval to resolve.", status=409)
            if approval_id_raw and approval_id_raw != pending_id:
                return _json_error("approval_mismatch", "approval_id does not match the run's pending approval.", status=409)

            try:
                approval_uuid = uuid.UUID(pending_id)
            except (TypeError, ValueError):
                return _json_error("approval_invalid", "Run pending approval ID is invalid.", status=409)

            approval = ConversationToolApproval.objects.select_for_update().filter(
                id=approval_uuid,
                conversation__business_profile_id=business_id,
            ).first()
            if not approval:
                return _json_error("not_found", "Approval not found.", status=404)

            execution_uuid = run.execution_conversation_id
            if execution_uuid and approval.conversation_id != execution_uuid:
                return _json_error("approval_mismatch", "Approval does not belong to this run.", status=409)

            now = timezone.now()
            if approval.status == ConversationToolApprovalStatus.PENDING:
                approval.status = next_status
                approval.resolved_at = now
                approval.save(update_fields=["status", "resolved_at", "updated_at"])

            _append_agent_run_event(
                run,
                stream=AgentRunEventStream.EXECUTED,
                event_type=AgentRunEventType.PROGRESS,
                label="Approved" if decision_value == "approve" else "Denied",
                payload={
                    "decision": decision_value,
                    "approval_id": str(approval.id),
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                },
            )
            MemoryItem.objects.create(
                business_profile=run.business_profile,
                scope=MemoryScope.RUN,
                agent_profile=run.agent_profile,
                agentic_task=run.agentic_task,
                run=run,
                conversation=run.conversation,
                kind=MemoryKind.DECISION,
                key="tool_approval",
                content=decision_value,
                payload={
                    "decision": decision_value,
                    "approval_id": str(approval.id),
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                    "actor": actor_snapshot,
                },
                created_by=actor_user,
            )
            if run.agentic_task_id:
                MemoryItem.objects.create(
                    business_profile=run.business_profile,
                    scope=MemoryScope.TASK,
                    agent_profile=run.agent_profile,
                    agentic_task=run.agentic_task,
                    run=run,
                    conversation=run.conversation,
                    kind=MemoryKind.DECISION,
                    key="tool_approval",
                    content=decision_value,
                    payload={
                        "decision": decision_value,
                        "approval_id": str(approval.id),
                        "tool_name": approval.tool_name,
                        "remote_tool_name": approval.remote_tool_name,
                        "actor": actor_snapshot,
                        "source_run_id": str(run.id),
                    },
                    visibility=MemoryVisibility.SHARED,
                    created_by=actor_user,
                )

            next_meta = dict(meta)
            next_meta.pop("pending_approval_id", None)
            # NOTE: pending_tool_call is preserved - worker will execute it directly and clear it

            if decision_value == "approve":
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.QUEUED,
                    run_after=now,
                    lease_expires_at=None,
                    error_detail="",
                    metadata=next_meta,
                    updated_at=now,
                )
            else:
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.CANCELLED,
                    finished_at=now,
                    lease_expires_at=None,
                    run_after=None,
                    error_detail="denied",
                    metadata=next_meta,
                    updated_at=now,
                )
            run.refresh_from_db()

    assert run is not None
    if approval is not None:
        try:
            latency_ms = None
            if approval.requested_at and approval.resolved_at:
                latency_ms = int((approval.resolved_at - approval.requested_at).total_seconds() * 1000)
            structured_log(
                "portal",
                "approval.run.decision",
                {
                    "decision": decision_value,
                    "approval_status": approval.status,
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                    "latency_ms": latency_ms,
                    "actor_type": str(actor_snapshot.get("type") or ""),
                },
                context={
                    "business": business_id,
                    "conversation": conversation.id,
                    "run": run.id,
                    "approval": str(approval.id),
                },
                level=logging.INFO,
            )
        except Exception:  # pragma: no cover - observability must not block portal responses
            pass

    # Patch any persisted in-flight portal message blocks so refresh reflects the latest decision.
    # This mirrors `portal_tool_approval` behavior for chat-thread approval cards created by runs.
    if approval is not None and business_id:
        try:
            approval_id_str = str(approval.id)
            with tenant_context(business_id):
                base_qs = (
                    ConversationMessage.objects.filter(conversation_id=conversation.id, sender=ConversationSender.AI)
                    .order_by("-sent_at", "-created_at")
                )
                candidates = list(base_qs.filter(metadata__pending_approval_id=approval_id_str)[:6])
                if not candidates:
                    candidates = list(base_qs[:30])
                for msg in candidates:
                    blocks_raw = msg.content_blocks if isinstance(getattr(msg, "content_blocks", None), list) else []
                    updated_blocks, mutated = _apply_portal_tool_approval_state(blocks_raw, approval=approval)
                    if not mutated:
                        continue
                    meta_in = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
                    meta_out = dict(meta_in)
                    if str(meta_out.get("pending_approval_id") or "").strip() == approval_id_str:
                        meta_out.pop("pending_approval_id", None)
                    ConversationMessage.objects.filter(id=msg.id).update(
                        content_blocks=updated_blocks,
                        metadata=meta_out,
                    )
        except Exception:  # pragma: no cover - best effort only
            logger.exception("portal run approval message patch failed approval=%s", getattr(approval, "id", None))

    return JsonResponse({"session": _session_to_dict(session), "run": _serialize_agent_run_for_portal(run)}, status=200)


@require_POST
def portal_agent_request_update(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    request_id_raw = (payload.get("request_id") or payload.get("requestId") or "").strip()
    status_raw = payload.get("status") or payload.get("state") or payload.get("action") or ""
    status_value = str(status_raw).strip().lower().replace("-", "_").replace(" ", "_")
    resolution = str(payload.get("resolution") or payload.get("message") or payload.get("reply") or "").strip()

    if not request_id_raw:
        return _json_error("validation_error", "conversation_id/session_token and request_id are required.")

    try:
        request_uuid = uuid.UUID(request_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "request_id is invalid.")

    status_map = {
        "open": AgentRequestStatus.OPEN,
        "in_progress": AgentRequestStatus.IN_PROGRESS,
        "inprogress": AgentRequestStatus.IN_PROGRESS,
        "start": AgentRequestStatus.IN_PROGRESS,
        "started": AgentRequestStatus.IN_PROGRESS,
        "resolve": AgentRequestStatus.RESOLVED,
        "resolved": AgentRequestStatus.RESOLVED,
    }
    next_status = status_map.get(status_value)
    if not next_status:
        return _json_error("validation_error", "status must be open, in_progress, or resolved.")
    if next_status == AgentRequestStatus.RESOLVED and not resolution:
        return _json_error("validation_error", "resolution is required when resolving a request.")

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    agent_profile_id = getattr(conversation, "agent_profile_id", None)
    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    if actor_user:
        actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
    else:
        actor_snapshot = {
            "type": "portal_session",
            "session_hash": hashlib.sha256(conversation.session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
        }

    run_payload: dict[str, object] | None = None
    business_id = getattr(conversation, "business_profile_id", None)
    with transaction.atomic():
        with tenant_context(business_id):
            qs = AgentRequest.objects.select_for_update().select_related("from_agent_profile", "to_agent_profile").filter(
                id=request_uuid,
                business_profile_id=business_id,
            )
            if agent_profile_id:
                qs = qs.filter(Q(to_agent_profile_id=agent_profile_id) | Q(from_agent_profile_id=agent_profile_id))
            agent_request = qs.first()
            if agent_request is None:
                return _json_error("not_found", "Request not found.", status=404)

            now = timezone.now()
            update_fields: list[str] = ["status", "updated_at"]
            agent_request.status = next_status
            if next_status == AgentRequestStatus.RESOLVED:
                agent_request.resolution = resolution[:8000]
                agent_request.resolved_at = now
                update_fields.extend(["resolution", "resolved_at"])
            agent_request.save(update_fields=update_fields)

            if next_status == AgentRequestStatus.RESOLVED and agent_request.agent_run_id:
                run = AgentRun.objects.select_for_update().filter(id=agent_request.agent_run_id).first()
                if run and run.status in {AgentRunStatus.WAITING_EXTERNAL, AgentRunStatus.PAUSED}:
                    _append_agent_run_event(
                        run,
                        stream=AgentRunEventStream.EXECUTED,
                        event_type=AgentRunEventType.PROGRESS,
                        label="Agent response received",
                        payload={
                            "agent_request_id": str(agent_request.id),
                            "subject": agent_request.subject,
                        },
                    )
                    MemoryItem.objects.create(
                        business_profile=run.business_profile,
                        scope=MemoryScope.RUN,
                        agent_profile=run.agent_profile,
                        agentic_task=run.agentic_task,
                        run=run,
                        conversation=run.conversation,
                        kind=MemoryKind.STATE_NOTE,
                        key="agent_request",
                        content=resolution[:4000],
                        payload={
                            "agent_request_id": str(agent_request.id),
                            "subject": agent_request.subject,
                            "actor": actor_snapshot,
                        },
                        created_by=actor_user,
                    )

                    next_meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
                    next_meta = dict(next_meta)
                    inputs = next_meta.get("external_inputs")
                    if not isinstance(inputs, list):
                        inputs = []
                    inputs.append(
                        {
                            "type": "agent_request",
                            "id": str(agent_request.id),
                            "subject": agent_request.subject,
                            "resolution": resolution[:4000],
                            "at": now.isoformat(),
                        }
                    )
                    next_meta["external_inputs"] = inputs[-10:]
                    next_meta.pop("pending_agent_request_id", None)
                    next_meta.pop("pending_agent_request", None)

                    AgentRun.objects.filter(id=run.id).update(
                        status=AgentRunStatus.QUEUED,
                        run_after=now,
                        lease_expires_at=None,
                        error_detail="",
                        metadata=next_meta,
                        updated_at=now,
                    )
                    run.refresh_from_db()
                    run_payload = _serialize_agent_run_for_portal(run)

    response_payload: dict[str, object] = {
        "session": _session_to_dict(session),
        "request": _serialize_agent_request_for_portal(agent_request),
    }
    if run_payload:
        response_payload["run"] = run_payload
    return JsonResponse(response_payload, status=200)
