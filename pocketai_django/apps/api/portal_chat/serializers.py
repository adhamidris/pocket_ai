from __future__ import annotations

import logging
from typing import Mapping

from apps.conversations.content_blocks import extract_text_from_content_blocks
from apps.conversations.models import ConversationToolApproval, PortalTurn, PortalTurnStatus
from apps.conversations.portal import (
    PortalAgentSummary,
    PortalBusinessSummary,
    PortalMessage,
    PortalSessionBootstrap,
    PortalSessionState,
)
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


def _business_to_dict(summary: PortalBusinessSummary) -> dict:
    return {"id": str(summary.id), "name": summary.name, "slug": summary.slug}


def _agent_to_dict(summary: PortalAgentSummary) -> dict:
    return {
        "id": str(summary.id),
        "name": summary.name,
        "role": summary.role,
        "slug": summary.slug,
        "shareable_path": summary.shareable_path,
    }


def _session_to_dict(session: PortalSessionState) -> dict:
    return {
        "conversation_id": str(session.conversation_id),
        "session_token": session.session_token,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
        "session_type": getattr(session, "session_type", "chat"),
        "custom_assistant_id": str(session.custom_assistant_id) if getattr(session, "custom_assistant_id", None) else None,
        "custom_assistant_name": getattr(session, "custom_assistant_name", ""),
    }


def _portal_turn_to_dict(turn: PortalTurn) -> dict[str, object]:
    return {
        "id": str(turn.id),
        "status": turn.status,
        "last_event_seq": int(turn.last_event_seq or 0),
        "message_id": str(turn.message_id) if getattr(turn, "message_id", None) else None,
        "started_at": turn.started_at.isoformat() if getattr(turn, "started_at", None) else None,
        "finalized_at": turn.finalized_at.isoformat() if getattr(turn, "finalized_at", None) else None,
    }


def _serialize_tool_approval(approval: ConversationToolApproval) -> dict[str, object]:
    return {
        "id": str(approval.id),
        "status": approval.status,
        "tool_name": approval.tool_name,
        "remote_tool_name": approval.remote_tool_name,
        "tool_call_id": approval.tool_call_id,
        "event_id": approval.event_id,
        "requested_at": approval.requested_at.isoformat() if approval.requested_at else None,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "metadata": approval.metadata or {},
    }


def _normalize_portal_content_blocks(blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    """
    Normalize portal `content_blocks` ordering for consistent UX on refresh.

    The portal streams tool cards as they arrive. When the assistant text is persisted
    after a tool approval request, we want the content blocks to render in the same
    order after a refresh (e.g. lead-in text followed by an approval card).

    Current normalization:
    - Move "initiate_phone_call" tool cards to the end of the message (stable),
      unless they have child blocks.
    """

    if not isinstance(blocks, list) or not blocks:
        return blocks

    tool_names = {"initiate_phone_call", "phone_call"}
    call_block_ids: list[str] = []
    child_parent_ids: set[str] = set()

    for entry in blocks:
        if not isinstance(entry, Mapping):
            continue
        parent_id = str(entry.get("parent_block_id") or entry.get("parentBlockId") or "").strip()
        if parent_id:
            child_parent_ids.add(parent_id)

        if str(entry.get("type") or "").strip().lower() != "tool_use":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").strip().lower()
        if tool_name in tool_names:
            block_id = str(entry.get("block_id") or entry.get("blockId") or "").strip()
            if block_id:
                call_block_ids.append(block_id)

    if not call_block_ids:
        return blocks

    movable_ids = {block_id for block_id in call_block_ids if block_id and block_id not in child_parent_ids}
    if not movable_ids:
        return blocks

    head: list[dict[str, object]] = []
    tail: list[dict[str, object]] = []
    for entry in blocks:
        block_id = str(entry.get("block_id") or entry.get("blockId") or "").strip() if isinstance(entry, Mapping) else ""
        if block_id and block_id in movable_ids:
            tail.append(entry)
        else:
            head.append(entry)
    return [*head, *tail]


def _canonicalize_portal_message_blocks(
    *,
    body: str,
    blocks: object | None,
) -> list[dict[str, object]]:
    del body  # Body is fallback text; canonical source-of-truth is persisted content_blocks.
    if not isinstance(blocks, list):
        return []
    canonical: list[dict[str, object]] = []
    for entry in blocks:
        if isinstance(entry, Mapping):
            canonical.append(dict(entry))
    return canonical


def _canonicalize_turn_event_payload(
    event_type: str,
    payload_obj: object | None,
) -> dict[str, object]:
    payload = payload_obj if isinstance(payload_obj, dict) else {}
    if str(event_type or "").strip().lower() != "turn_persisted":
        return payload

    # Preserve turn_persisted payload as streamed/persisted source-of-truth.
    # Only coerce camelCase alias when present for compatibility.
    blocks = payload.get("content_blocks")
    if blocks is None and "contentBlocks" in payload:
        payload["content_blocks"] = _canonicalize_portal_message_blocks(body="", blocks=payload.get("contentBlocks"))
        payload.pop("contentBlocks", None)
    elif blocks is not None:
        payload["content_blocks"] = _canonicalize_portal_message_blocks(body="", blocks=blocks)
    return payload


def _safe_canonicalize_blocks(
    *,
    body: str,
    blocks: object | None,
) -> list[dict[str, object]]:
    """Guarded wrapper: returns raw blocks on failure instead of crashing."""
    try:
        return _canonicalize_portal_message_blocks(body=body, blocks=blocks)
    except Exception:
        logger.exception("_canonicalize_portal_message_blocks failed; using raw blocks")
        return list(blocks) if isinstance(blocks, list) else []


def _safe_canonicalize_turn_event(
    event_type: str,
    payload_obj: object | None,
) -> dict[str, object]:
    """Guarded wrapper: returns original payload on failure instead of crashing the SSE generator."""
    try:
        return _canonicalize_turn_event_payload(event_type, payload_obj)
    except Exception:
        logger.exception("_canonicalize_turn_event_payload failed; passing through raw payload")
        return payload_obj if isinstance(payload_obj, dict) else {}


def _apply_portal_tool_approval_state(
    blocks: list[dict[str, object]],
    *,
    approval: ConversationToolApproval,
    phase: str = "approval_resolved",
) -> tuple[list[dict[str, object]], bool]:
    """
    Update persisted portal content blocks when a tool approval resolves.

    Without this, the UI can regress on refresh (e.g., denied approvals show CTAs again)
    because the last persisted message still contains `approval_requested` payloads.
    """

    if not isinstance(blocks, list) or not blocks:
        return blocks, False

    approval_id = str(getattr(approval, "id", "") or "").strip()
    if not approval_id:
        return blocks, False
    approval_event_id = str(getattr(approval, "event_id", "") or "").strip()
    approval_tool_call_id = str(getattr(approval, "tool_call_id", "") or "").strip()

    status = str(getattr(approval, "status", "") or "").strip().lower()
    if not status:
        return blocks, False

    mutated = False
    resolved_at = approval.resolved_at.isoformat() if approval.resolved_at else None
    expires_at = approval.expires_at.isoformat() if approval.expires_at else None

    for entry in blocks:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "").strip().lower() != "tool_use":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue

        match_id = str(payload.get("approval_id") or payload.get("approvalId") or "").strip()
        nested = payload.get("approval")
        nested_id = str(nested.get("id") or "").strip() if isinstance(nested, Mapping) else ""
        payload_event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
        payload_tool_call_id = str(payload.get("tool_call_id") or payload.get("toolCallId") or "").strip()
        matches = match_id == approval_id or nested_id == approval_id
        if not matches and approval_event_id and payload_event_id and payload_event_id == approval_event_id:
            matches = True
        if not matches and approval_tool_call_id and payload_tool_call_id and payload_tool_call_id == approval_tool_call_id:
            matches = True
        if not matches:
            continue

        payload["approval_id"] = approval_id
        payload["phase"] = phase
        payload["status"] = status

        approval_payload: dict[str, object] = dict(nested) if isinstance(nested, Mapping) else {}
        approval_payload["id"] = approval_id
        approval_payload["status"] = status
        if resolved_at:
            approval_payload["resolved_at"] = resolved_at
        if expires_at:
            approval_payload["expires_at"] = expires_at
        payload["approval"] = approval_payload
        entry["payload"] = payload
        mutated = True

    if mutated:
        blocks = _normalize_portal_content_blocks(blocks)
    return blocks, mutated


def _clip_portal_text(value: str, limit: int) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"



def _message_to_dict(message: PortalMessage) -> dict:
    body = message.body if isinstance(message.body, str) else str(message.body or "")
    content_blocks = _safe_canonicalize_blocks(body=body, blocks=message.content_blocks)
    if not body and content_blocks:
        body = extract_text_from_content_blocks(content_blocks)
    return {
        "id": str(message.id),
        "sender": message.sender,
        "body": body,
        "sent_at": message.sent_at.isoformat(),
        "metadata": message.metadata,
        "content_blocks": content_blocks,
    }


def _bootstrap_to_dict(result: PortalSessionBootstrap) -> dict:
    payload = {
        "business": _business_to_dict(result.business),
        "agent": _agent_to_dict(result.agent),
        "session": _session_to_dict(result.session),
        "messages": [_message_to_dict(msg) for msg in result.messages],
    }
    try:
        # If a portal turn is currently streaming (e.g., waiting for tool approval),
        # expose it so the frontend can resume by replaying the turn event log.
        with tenant_context(result.business.id):
            active_turn = (
                PortalTurn.objects.filter(conversation_id=result.session.conversation_id)
                .filter(status__in={PortalTurnStatus.STREAMING, PortalTurnStatus.WAITING_APPROVAL})
                .order_by("-started_at")
                .first()
            )
        payload["active_turn"] = _portal_turn_to_dict(active_turn) if active_turn else None
    except Exception:  # pragma: no cover - best effort only
        payload["active_turn"] = None
    payload["capabilities"] = {"agentWorkforceEnabled": True}
    return payload
