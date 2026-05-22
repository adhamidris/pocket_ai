from __future__ import annotations

import hashlib
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import (
    McpConnectionApprovalMode,
    McpConnectionAuditAction,
    McpToolOperationType,
)
from apps.agent_runs.models import (
    AgentRun,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunStatus,
)
from apps.api.portal_chat.activity_snapshots import _append_agent_run_event
from apps.api.portal_chat.request_context import (
    _json_error,
    _parse_json_body,
    _resolve_request_conversation,
    _service,
)
from apps.api.portal_chat.serializers import (
    _apply_portal_tool_approval_state,
    _serialize_tool_approval,
    _session_to_dict,
)
from apps.conversations.models import (
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
from apps.conversations.portal_service.auth import can_access_conversation
from apps.mcp.models import (
    AgentMcpToolSetting,
    McpConnectionAuditEvent,
)
from apps.rag.observability.logging import structured_log
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


@require_POST
def portal_tool_approval(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    approval_id = (payload.get("approval_id") or payload.get("approvalId") or "").strip()
    decision_raw = payload.get("decision") or payload.get("action") or payload.get("status") or ""
    decision = str(decision_raw).strip().lower()
    remember_raw = payload.get("remember") or payload.get("always_allow") or payload.get("alwaysAllow") or False
    remember = False
    if isinstance(remember_raw, str):
        remember = remember_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        remember = bool(remember_raw)
    if not approval_id or not decision:
        return _json_error("validation_error", "conversation_id/session_token, approval_id, and decision are required.")

    if decision in {"approve", "approved", "allow"}:
        next_status = ConversationToolApprovalStatus.APPROVED
    elif decision in {"deny", "denied", "reject"}:
        next_status = ConversationToolApprovalStatus.DENIED
    else:
        return _json_error("validation_error", "decision must be approve or deny.")

    try:
        approval_uuid = uuid.UUID(approval_id)
    except (TypeError, ValueError):
        return _json_error("validation_error", "approval_id is invalid.")

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

    if remember and not getattr(settings, "PORTAL_ALLOW_MCP_TOOL_PREFERENCES", False):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return _json_error("auth_required", "Authentication is required.", status=401)

    approval: ConversationToolApproval | None = None
    now = timezone.now()
    business_id = getattr(conversation, "business_profile_id", None)
    preference_saved = False
    with transaction.atomic():
        with tenant_context(business_id):
            approval = ConversationToolApproval.objects.select_for_update().filter(
                id=approval_uuid,
                conversation=conversation,
            ).first()
            if not approval:
                return _json_error("not_found", "Approval not found.", status=404)
            if approval.status != ConversationToolApprovalStatus.PENDING:
                try:
                    age_ms = None
                    if approval.requested_at:
                        age_ms = int((now - approval.requested_at).total_seconds() * 1000)
                    structured_log(
                        "portal",
                        "approval.tool.decision",
                        {
                            "status": approval.status,
                            "decision": decision,
                            "already_resolved": True,
                            "tool_name": approval.tool_name,
                            "remote_tool_name": approval.remote_tool_name,
                            "connection_id": str(approval.connection_id or ""),
                            "age_ms": age_ms,
                        },
                        context={
                            "business": business_id,
                            "conversation": conversation.id,
                            "approval": str(approval.id),
                        },
                        level=logging.INFO,
                    )
                except Exception:
                    pass
                return JsonResponse({"session": _session_to_dict(session), "approval": _serialize_tool_approval(approval)})
            if approval.expires_at and approval.expires_at <= now:
                approval.status = ConversationToolApprovalStatus.EXPIRED
                approval.resolved_at = now
                approval.save(update_fields=["status", "resolved_at", "updated_at"])
                try:
                    age_ms = None
                    if approval.requested_at:
                        age_ms = int((now - approval.requested_at).total_seconds() * 1000)
                    structured_log(
                        "portal",
                        "approval.tool.decision",
                        {
                            "status": approval.status,
                            "decision": decision,
                            "expired": True,
                            "tool_name": approval.tool_name,
                            "remote_tool_name": approval.remote_tool_name,
                            "connection_id": str(approval.connection_id or ""),
                            "age_ms": age_ms,
                        },
                        context={
                            "business": business_id,
                            "conversation": conversation.id,
                            "approval": str(approval.id),
                        },
                        level=logging.INFO,
                    )
                except Exception:
                    pass
                return JsonResponse({"session": _session_to_dict(session), "approval": _serialize_tool_approval(approval)})
            approval.status = next_status
            approval.resolved_at = now
            approval.save(update_fields=["status", "resolved_at", "updated_at"])

    if approval and approval.connection_id:
        try:
            with tenant_context(business_id):
                McpConnectionAuditEvent.objects.create(
                    business_profile=conversation.business_profile,
                    connection=approval.connection,
                    connection_id_snapshot=approval.connection_id,
                    actor_user=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
                    action=McpConnectionAuditAction.TOOL_APPROVED
                    if approval.status == ConversationToolApprovalStatus.APPROVED
                    else McpConnectionAuditAction.TOOL_DENIED,
                    description=f"Tool {approval.tool_name} {approval.status} via portal.",
                    metadata={
                        "approval_id": str(approval.id),
                        "conversation_id": str(conversation.id),
                        "tool_name": approval.tool_name,
                        "remote_tool_name": approval.remote_tool_name,
                        "tool_call_id": approval.tool_call_id,
                    },
                )
        except Exception:  # pragma: no cover - audit should never block
            logger.exception("mcp_tool_approval_audit_failed approval=%s", approval.id)

    if (
        approval
        and approval.status == ConversationToolApprovalStatus.APPROVED
        and remember
        and approval.connection_id
        and approval.remote_tool_name
    ):
        allow_persist = False
        if getattr(settings, "PORTAL_ALLOW_MCP_TOOL_PREFERENCES", False):
            allow_persist = True
        elif getattr(request, "user", None) and request.user.is_authenticated:
            allow_persist = can_access_conversation(request.user, conversation)
        if allow_persist and conversation.agent_profile_id:
            try:
                operation_value = str((approval.metadata or {}).get("operation_type") or "").strip().lower()
                operation_type = (
                    operation_value
                    if operation_value in {McpToolOperationType.READ, McpToolOperationType.WRITE, McpToolOperationType.UNKNOWN}
                    else McpToolOperationType.UNKNOWN
                )
                with tenant_context(business_id):
                    AgentMcpToolSetting.objects.update_or_create(
                        agent_profile_id=conversation.agent_profile_id,
                        connection_id=approval.connection_id,
                        tool_name=approval.remote_tool_name,
                        defaults={
                            "approval_mode": McpConnectionApprovalMode.AUTO,
                            "operation_type": operation_type,
                        },
                    )
                preference_saved = True
            except Exception:  # pragma: no cover - best effort only
                logger.exception("portal_tool_preference_save_failed approval=%s", approval.id)

    # If this approval unblocks a sub-agent run, resume/cancel it.
    if approval:
        actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        if actor_user:
            actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
        else:
            actor_snapshot = {
                "type": "portal_session",
                "session_hash": hashlib.sha256(conversation.session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
            }
        with tenant_context(business_id):
            waiting_runs = list(
                AgentRun.objects.filter(conversation_id=conversation.id, status=AgentRunStatus.WAITING_APPROVAL)
                .order_by("-updated_at")[:15]
            )
            for run in waiting_runs:
                meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
                pending_id = str(meta.get("pending_approval_id") or "").strip()
                if pending_id and pending_id != str(approval.id):
                    continue

                decision_value = "approve" if approval.status == ConversationToolApprovalStatus.APPROVED else "deny"
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

                if approval.status == ConversationToolApprovalStatus.APPROVED:
                    AgentRun.objects.filter(id=run.id).update(
                        status=AgentRunStatus.QUEUED,
                        run_after=timezone.now(),
                        lease_expires_at=None,
                        error_detail="",
                        metadata=next_meta,
                        updated_at=timezone.now(),
                    )
                else:
                    AgentRun.objects.filter(id=run.id).update(
                        status=AgentRunStatus.CANCELLED,
                        finished_at=timezone.now(),
                        lease_expires_at=None,
                        run_after=None,
                        error_detail="denied",
                        metadata=next_meta,
                        updated_at=timezone.now(),
                    )

    if approval:
        try:
            latency_ms = None
            if approval.requested_at and approval.resolved_at:
                latency_ms = int((approval.resolved_at - approval.requested_at).total_seconds() * 1000)
            structured_log(
                "portal",
                "approval.tool.decision",
                {
                    "status": approval.status,
                    "decision": decision,
                    "remember": remember,
                    "preference_saved": preference_saved,
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                    "connection_id": str(approval.connection_id or ""),
                    "operation_type": str((approval.metadata or {}).get("operation_type") or ""),
                    "latency_ms": latency_ms,
                },
                context={
                    "business": business_id,
                    "conversation": conversation.id,
                    "approval": str(approval.id),
                },
                level=logging.INFO,
            )
        except Exception:  # pragma: no cover - observability must not block portal responses
            pass

    # Patch any persisted in-flight assistant message blocks so refresh reflects the latest decision.
    # Without this, visitors can see stale "pending" CTAs after a deny/expire.
    if approval and business_id:
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
                    if meta_out.get("portal_turn_state") == "waiting_approval":
                        meta_out["portal_turn_state"] = "approval_resolved"
                    ConversationMessage.objects.filter(id=msg.id).update(
                        content_blocks=updated_blocks,
                        metadata=meta_out,
                    )
        except Exception:  # pragma: no cover - best effort only
            logger.exception("portal tool approval message patch failed approval=%s", getattr(approval, "id", None))

    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "approval": _serialize_tool_approval(approval),
            "preferenceSaved": preference_saved,
        }
    )
