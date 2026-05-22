from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Callable, Mapping

from apps.conversations.models import Conversation

from .... import tools as mcp_tools
from ....types import ToolExecutionContext


logger = logging.getLogger(__name__)


class McpTurnEmailAutoSendMixin:
    def _maybe_send_created_email_draft(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
        created_email_draft: Mapping[str, str] | None,
        email_send_requested: bool,
        user_message: str,
        stream_state: object,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_tool_approval: bool,
        emit_final_answer: Callable[[str], None],
        status_event: Callable[[str, str | None], None],
    ) -> dict[str, object] | None:
        if not created_email_draft or email_send_requested:
            return None

        lowered = (user_message or "").strip().lower()
        send_intent = False
        if lowered:
            if re.search(r"\b(send|reply|respond|forward)\b", lowered):
                send_intent = True
            elif ("email" in lowered or "e-mail" in lowered) and not re.search(r"\bdraft\b", lowered):
                send_intent = True

        if not send_intent:
            return None

        draft_id = str(created_email_draft.get("draft_id") or "").strip()
        account_id = str(created_email_draft.get("email_account_id") or "").strip()
        send_args: dict[str, object] = {"draft_id": draft_id}
        if account_id:
            send_args["email_account_id"] = account_id
        email_account = self._resolve_email_account_for_tool_call(
            conversation=conversation,
            arguments=send_args,
        )
        if not email_account or not draft_id:
            return None

        approval_needed, approval_reason = self._email_send_requires_approval(
            conversation=conversation,
            email_account=email_account,
            draft_id=draft_id,
        )
        if approval_needed:
            approved, _, approval_result = self._maybe_request_email_tool_approval(
                conversation=conversation,
                tool_name="email_send_draft",
                tool_call_id="",
                tool_event_id=str(uuid.uuid4()),
                arguments=send_args,
                reason=approval_reason,
                on_tool_event=on_tool_event,
                wait_for_approval=wait_for_tool_approval,
            )
            if not approved:
                status_value = ""
                if isinstance(approval_result, Mapping):
                    status_value = str(approval_result.get("status") or "").strip().lower()
                if status_value != "pending_approval":
                    self._clear_pending_email_draft(
                        conversation=conversation,
                        email_account_id=getattr(email_account, "id", None),
                        draft_id=draft_id,
                    )
                    response_text = "Okay — I won't send it."
                else:
                    response_text = (
                        "I need your approval before I can send this email. "
                        "Please approve the pending request to continue."
                    )
                return self._email_draft_gate_response(
                    conversation=conversation,
                    tool_context=tool_context,
                    stream_state=stream_state,
                    response_text=response_text,
                    emit_final_answer=emit_final_answer,
                    status_event=status_event,
                )

        send_event_id = str(uuid.uuid4())
        send_started_payload = {
            "event_id": send_event_id,
            "phase": "started",
            "status": "running",
            "tool_call_id": "",
            "tool_name": "email_send_draft",
            "kind": "email",
        }
        email_input = self._email_tool_event_input("email_send_draft", send_args)
        if email_input:
            send_started_payload["input"] = email_input
        if on_tool_event:
            try:
                on_tool_event(send_started_payload)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal email send start callback failed")

        call_start = time.perf_counter()
        send_tool_result = mcp_tools.execute_tool(
            "email_send_draft",
            send_args,
            conversation=conversation,
            context=tool_context,
        )
        call_duration_ms = (time.perf_counter() - call_start) * 1000.0
        finish_payload = dict(send_started_payload)
        finish_payload["phase"] = "finished"
        finish_payload["duration_ms"] = int(call_duration_ms) if call_duration_ms is not None else 0
        if isinstance(send_tool_result, Mapping):
            finish_payload["status"] = str(send_tool_result.get("status") or "") or "ok"
            finish_payload["output"] = self._email_tool_event_output(
                "email_send_draft",
                send_tool_result,
            )
        else:
            finish_payload["status"] = "ok"
        if on_tool_event:
            try:
                on_tool_event(finish_payload)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal email send finish callback failed")

        response_text = "Email sent."
        if isinstance(send_tool_result, Mapping):
            self._record_email_send_audit(
                conversation=conversation,
                email_account=email_account,
                tool_result=send_tool_result,
            )
            status_value = str(send_tool_result.get("status") or "").strip().lower()
            if status_value == "ok":
                resolved_draft_id = str(
                    send_tool_result.get("draft_id") or send_tool_result.get("draftId") or draft_id
                ).strip()
                self._clear_pending_email_draft(
                    conversation=conversation,
                    email_account_id=getattr(email_account, "id", None),
                    draft_id=resolved_draft_id,
                )
            else:
                response_text = "I couldn't send that email draft."
        else:
            response_text = "I couldn't send that email draft."

        return self._email_draft_gate_response(
            conversation=conversation,
            tool_context=tool_context,
            stream_state=stream_state,
            response_text=response_text,
            emit_final_answer=emit_final_answer,
            status_event=status_event,
        )

    def _email_draft_gate_response(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
        stream_state: object,
        response_text: str,
        emit_final_answer: Callable[[str], None],
        status_event: Callable[[str, str | None], None],
    ) -> dict[str, object]:
        emit_final_answer(response_text)
        status_event("answer_finalized", "Answer ready")
        status_event("stream_complete", "")
        self._log_turn_metrics(conversation, tool_context)
        normalized_assistant = {
            "role": "assistant",
            "content": response_text,
            "actions": [],
            "extractions": [],
            "placeholder_response": None,
        }
        response_blocks = self._extract_response_blocks(normalized_assistant)
        return {
            "assistant_message": normalized_assistant,
            "tool_context": tool_context,
            "streamed_chunks": tuple(getattr(stream_state, "answer_chunks", ())),
            "clean_answer_text": response_text,
            "dropped_sentences": tuple(),
            "llm_strategy": "mcp_tools_email_draft_gate",
            "response_blocks": response_blocks,
        }
