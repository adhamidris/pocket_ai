from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Callable, Mapping

from django.utils import timezone

from apps.conversations.models import (
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
)

from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


class McpPhoneApprovalMixin:

    def _phone_tool_input_payload(self, arguments: Mapping[str, object]) -> dict[str, object]:
        phone_number = str(arguments.get("phone_number") or arguments.get("phoneNumber") or "").strip()
        objective = str(arguments.get("objective") or "").strip()
        call_type = str(arguments.get("call_type") or arguments.get("callType") or "").strip()
        language = str(arguments.get("language") or "").strip()
        max_duration = arguments.get("max_duration_minutes") or arguments.get("maxDurationMinutes")
        try:
            max_duration_value = int(max_duration) if max_duration is not None else None
        except (TypeError, ValueError):
            max_duration_value = None

        payload: dict[str, object] = {}
        if phone_number:
            payload["phone_number"] = phone_number
        if objective:
            payload["objective"] = self._clip_text(objective, 600)
        if call_type:
            payload["call_type"] = self._clip_text(call_type, 80)
        if language:
            payload["language"] = self._clip_text(language, 40)
        if max_duration_value:
            payload["max_duration_minutes"] = max_duration_value

        context_items = arguments.get("context_items") or arguments.get("contextItems") or []
        context_lines: list[str] = []
        if isinstance(context_items, list):
            for item in context_items[:8]:
                line = ""
                if isinstance(item, Mapping):
                    title = str(item.get("title") or item.get("label") or item.get("name") or "").strip()
                    value = item.get("value") or item.get("content") or item.get("text") or item.get("summary") or item.get("note")
                    value_text = str(value).strip() if value is not None else ""

                    if title and value_text:
                        line = f"{title}: {value_text}"
                    elif value_text:
                        line = value_text
                    elif title:
                        line = title
                    else:
                        parts: list[str] = []
                        for key, val in list(item.items())[:3]:
                            key_text = str(key).strip()
                            val_text = str(val).strip() if val is not None else ""
                            if key_text and val_text:
                                parts.append(f"{key_text}: {val_text}")
                        line = "; ".join(parts).strip()
                        if not line:
                            try:
                                line = json.dumps(item, ensure_ascii=False)
                            except Exception:
                                line = str(item)
                elif isinstance(item, str):
                    line = item.strip()
                elif item is not None:
                    line = str(item).strip()
                if line:
                    context_lines.append(self._clip_text(line, 220))
        if context_lines:
            payload["context_items"] = context_lines
        return payload

    def _phone_tool_approval_preview(
        self,
        *,
        arguments: Mapping[str, object],
        conversation: Conversation,
    ) -> dict[str, object] | None:
        payload = self._phone_tool_input_payload(arguments)
        if not payload:
            return None

        fields: list[dict[str, str]] = []
        phone_number = str(payload.get("phone_number") or "").strip()
        if phone_number:
            fields.append({"label": "To", "value": self._clip_text(phone_number, 80)})
        objective = str(payload.get("objective") or "").strip()
        if objective:
            fields.append({"label": "Objective", "value": self._clip_text(objective, 360)})
        call_type = str(payload.get("call_type") or "").strip()
        if call_type:
            fields.append({"label": "Type", "value": self._clip_text(call_type, 80)})
        language = str(payload.get("language") or "").strip()
        if language:
            fields.append({"label": "Language", "value": self._clip_text(language, 40)})
        max_duration = payload.get("max_duration_minutes")
        if isinstance(max_duration, int) and max_duration:
            fields.append({"label": "Max duration", "value": f"{max_duration} min"})

        context_lines: list[str] = []
        context_items = payload.get("context_items")
        if isinstance(context_items, list):
            for item in context_items[:8]:
                line = str(item or "").strip()
                if line:
                    context_lines.append(self._clip_text(line, 220))

        summary = str(getattr(conversation, "summary", "") or "").strip()
        if summary:
            context_lines.append(f"Summary: {self._clip_text(summary, 600)}")
        elif not context_lines:
            try:
                business_id = getattr(conversation, "business_profile_id", None)
                with tenant_context(business_id):
                    messages = list(
                        ConversationMessage.objects.filter(conversation_id=conversation.id)
                        .order_by("-sent_at", "-created_at")
                        .only("sender", "body")[:4]
                    )
                for msg in reversed(messages):
                    body = str(getattr(msg, "body", "") or "").strip()
                    if not body:
                        continue
                    sender = "Customer" if msg.sender == ConversationSender.CUSTOMER else "Agent"
                    context_lines.append(f"{sender}: {self._clip_text(body, 160)}")
            except Exception:
                context_lines = context_lines or []

        preview: dict[str, object] = {"type": "phone_call", "title": "Phone call", "fields": fields}
        if context_lines:
            preview["body"] = self._clip_text("\n".join(context_lines), 1400)
        if not fields and not context_lines:
            return None
        return preview

    def _maybe_request_phone_tool_approval(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        tool_call_id: str,
        tool_event_id: str,
        arguments: Mapping[str, object],
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_approval: bool = True,
    ) -> tuple[bool, ConversationToolApproval | None, Mapping[str, object] | None]:
        expires_at = timezone.now() + timedelta(seconds=self._tool_approval_timeout_seconds())
        business_id = getattr(conversation, "business_profile_id", None)

        redacted_input = self._phone_tool_input_payload(arguments)
        redacted_input_dict = dict(redacted_input) if isinstance(redacted_input, Mapping) else {}

        existing_approved: ConversationToolApproval | None = None
        if self._phone_tool_approval_reuse_enabled():
            with tenant_context(business_id):
                approved_candidates = list(
                    ConversationToolApproval.objects.filter(
                        conversation=conversation,
                        tool_name=tool_name,
                        remote_tool_name="",
                        status=ConversationToolApprovalStatus.APPROVED,
                    )
                    .order_by("-resolved_at")[:10]
                )
            for candidate in approved_candidates:
                candidate_input = getattr(candidate, "input_payload", None)
                if isinstance(candidate_input, Mapping) and dict(candidate_input) == redacted_input_dict:
                    existing_approved = candidate
                    break

        if existing_approved:
            approval_payload = {
                "id": str(existing_approved.id),
                "status": ConversationToolApprovalStatus.APPROVED,
                "operation_type": "write",
                "reason": "phone_call",
                "expires_at": existing_approved.expires_at.isoformat() if existing_approved.expires_at else None,
            }
            resolve_event = {
                "event_id": tool_event_id,
                "phase": "approval_resolved",
                "status": ConversationToolApprovalStatus.APPROVED,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "kind": "phone",
                "approval": approval_payload,
                "input": redacted_input_dict,
            }
            if on_tool_event:
                try:
                    on_tool_event(resolve_event)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal phone approval resolve callback failed")
            return True, existing_approved, None

        with tenant_context(business_id):
            existing = None
            if tool_call_id:
                existing = ConversationToolApproval.objects.filter(
                    conversation=conversation,
                    tool_call_id=tool_call_id,
                    status=ConversationToolApprovalStatus.PENDING,
                ).first()
            approval = existing or ConversationToolApproval.objects.create(
                conversation=conversation,
                connection=None,
                tool_name=tool_name,
                remote_tool_name="",
                tool_call_id=tool_call_id or "",
                event_id=tool_event_id or "",
                status=ConversationToolApprovalStatus.PENDING,
                expires_at=expires_at,
                input_payload=redacted_input_dict,
                metadata={
                    "approval_mode": "phone_call",
                    "operation_type": "write",
                    "reason": "phone_call",
                },
            )

        preview_payload = self._phone_tool_approval_preview(arguments=arguments, conversation=conversation)
        if preview_payload and isinstance(getattr(approval, "metadata", None), Mapping):
            try:
                updated_meta = dict(approval.metadata or {})
                updated_meta["preview"] = preview_payload
                with tenant_context(business_id):
                    ConversationToolApproval.objects.filter(id=approval.id).update(
                        metadata=updated_meta,
                        updated_at=timezone.now(),
                    )
                approval.metadata = updated_meta
            except Exception:  # pragma: no cover - best effort only
                logger.exception("mcp phone approval preview persist failed approval=%s", getattr(approval, "id", None))

        approval_payload = {
            "id": str(approval.id),
            "status": approval.status,
            "operation_type": "write",
            "reason": "phone_call",
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        }
        if preview_payload:
            approval_payload["preview"] = preview_payload

        pending_tool_call_data = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
            "approval_id": str(approval.id),
            "connection_id": None,
            "remote_tool_name": "",
            "event_id": tool_event_id,
        }
        request_event = {
            "event_id": tool_event_id,
            "phase": "approval_requested",
            "status": "pending_approval",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": "phone",
            "input": redacted_input_dict,
            "approval": approval_payload,
            "output": {"pending_tool_call": pending_tool_call_data},
        }
        if on_tool_event:
            try:
                on_tool_event(request_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal phone approval request callback failed")

        if not wait_for_approval:
            tool_result = {
                "tool": tool_name,
                "status": "pending_approval",
                "error_code": "pending_approval",
                "error": "Awaiting user approval.",
                "hint": "Ask the user to approve or deny the phone call, then retry.",
                "approval": approval_payload,
                "input": redacted_input_dict,
                "pending_tool_call": pending_tool_call_data,
            }
            return False, approval, tool_result

        approval = self._wait_for_tool_approval(approval=approval, conversation=conversation)
        status_value = approval.status if approval else ConversationToolApprovalStatus.DENIED
        approval_payload["status"] = status_value
        resolve_event = {
            "event_id": tool_event_id,
            "phase": "approval_resolved",
            "status": status_value,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": "phone",
            "approval": approval_payload,
        }
        if status_value != ConversationToolApprovalStatus.APPROVED:
            tool_result = self._approval_blocked_payload(tool_name, status_value)
            resolve_event["output"] = dict(tool_result)
            if on_tool_event:
                try:
                    on_tool_event(resolve_event)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal phone approval resolve callback failed")
            return False, approval, dict(tool_result)

        if on_tool_event:
            try:
                on_tool_event(resolve_event)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal phone approval resolve callback failed")
        return True, approval, None
