from __future__ import annotations

import logging
import uuid
from typing import Mapping

from apps.agent_runs.models import (
    AgentRun,
    AgentRunCheckpointKind,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunStatus,
)
from apps.conversations.content_blocks import ensure_assistant_text_blocks
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender


logger = logging.getLogger(__name__)


class AgentRunOutcomeEventMixin:
    def _emit_run_outcome_events(
        self,
        *,
        run: AgentRun,
        next_status: str,
        terminal_error_detail: str,
        external_request_id: str,
        external_request_tool: str,
        pause_event_type: str | None,
        pause_payload: Mapping[str, object] | None,
        turn,
        now,
        next_metadata: Mapping[str, object],
        anchor_conversation: Conversation | None,
        response_text_value: str,
        completion_index: int | None,
        approval_id: str,
        approval_preview: Mapping[str, object] | None,
        pause_state: Mapping[str, object],
    ) -> None:
        if next_status == AgentRunStatus.COMPLETED:
            self._append_event(
                run,
                stream=AgentRunEventStream.SYSTEM,
                event_type=AgentRunEventType.RESULT,
                label="Completed",
                payload={"status": AgentRunStatus.COMPLETED},
            )
            self._post_completion_followup(
                run=run,
                next_metadata=next_metadata,
                anchor_conversation=anchor_conversation,
                response_text_value=response_text_value,
                completion_index=completion_index,
                now=now,
            )
        elif next_status == AgentRunStatus.FAILED:
            self._append_event(
                run,
                stream=AgentRunEventStream.SYSTEM,
                event_type=AgentRunEventType.ERROR,
                label="Incomplete",
                payload={"error": terminal_error_detail or "Run did not complete."},
            )
        elif next_status == AgentRunStatus.WAITING_EXTERNAL and external_request_id:
            self._emit_waiting_external(
                run=run,
                external_request_id=external_request_id,
                external_request_tool=external_request_tool,
                now=now,
            )
        elif pause_event_type and pause_payload is not None:
            self._emit_waiting_user_or_approval(
                run=run,
                next_status=next_status,
                pause_event_type=pause_event_type,
                pause_payload=pause_payload,
                turn=turn,
                now=now,
                next_metadata=next_metadata,
                anchor_conversation=anchor_conversation,
                approval_id=approval_id,
                approval_preview=approval_preview,
                pause_state=pause_state,
            )

        if next_status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
            self._resume_parent_if_child_finished(run, now=now)

    def _post_completion_followup(
        self,
        *,
        run: AgentRun,
        next_metadata: Mapping[str, object],
        anchor_conversation: Conversation | None,
        response_text_value: str,
        completion_index: int | None,
        now,
    ) -> None:
        followup_meta = next_metadata if isinstance(next_metadata, Mapping) else {}
        if getattr(run, "agentic_task_id", None):
            return
        delegate_intent = str(followup_meta.get("delegate_intent") or "").strip().lower()
        followup_requested = bool(followup_meta.get("followup_requested") or delegate_intent == "explicit")
        followup_mode = str(followup_meta.get("followup_mode") or "").strip().lower() or "handoff"
        if followup_mode not in {"handoff", "supervisor"}:
            followup_mode = "handoff"
        if not (followup_requested and anchor_conversation is not None):
            return
        response_text = str(response_text_value or "").strip()
        if not response_text:
            return
        handoff_text = response_text
        if len(handoff_text) > 6000:
            handoff_text = handoff_text[:5999].rstrip() + "..."
        already_handoff = ConversationMessage.objects.filter(
            conversation_id=anchor_conversation.id,
            metadata__agent_run_id=str(run.id),
            metadata__type="run_handoff",
            metadata__completion_index=completion_index,
        ).exists()
        if already_handoff:
            return
        ConversationMessage.objects.create(
            conversation=anchor_conversation,
            sender=ConversationSender.AI,
            body=handoff_text,
            metadata={
                "source": "agent_run",
                "agent_run_id": str(run.id),
                "type": "run_handoff",
                "run_source": run.source,
                "followup_mode": followup_mode,
                "completion_index": completion_index,
            },
            content_blocks=ensure_assistant_text_blocks(handoff_text),
        )
        Conversation.objects.filter(id=anchor_conversation.id).update(last_activity_at=now)

    def _emit_waiting_external(
        self,
        *,
        run: AgentRun,
        external_request_id: str,
        external_request_tool: str,
        now,
    ) -> None:
        checkpoint = self._upsert_open_checkpoint(
            run=run,
            kind=AgentRunCheckpointKind.EXTERNAL,
            title="Waiting for external work",
            prompt="This run is waiting for an external action to finish.",
            payload={"external_request_id": external_request_id, "tool": external_request_tool},
            now=now,
        )
        next_metadata = dict(run.metadata or {}) if isinstance(getattr(run, "metadata", None), Mapping) else {}
        next_metadata["pending_checkpoint_id"] = str(checkpoint.id)
        AgentRun.objects.filter(id=run.id).update(metadata=next_metadata, updated_at=now)
        self._append_event(
            run,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Waiting for agent response",
            payload={"agent_request_id": external_request_id},
        )

    def _emit_waiting_user_or_approval(
        self,
        *,
        run: AgentRun,
        next_status: str,
        pause_event_type: str,
        pause_payload: Mapping[str, object],
        turn,
        now,
        next_metadata: Mapping[str, object],
        anchor_conversation: Conversation | None,
        approval_id: str,
        approval_preview: Mapping[str, object] | None,
        pause_state: Mapping[str, object],
    ) -> None:
        self._append_event(
            run,
            stream=AgentRunEventStream.SYSTEM,
            event_type=pause_event_type,
            label="Needs approval" if next_status == AgentRunStatus.WAITING_APPROVAL else "Needs user input",
            payload=dict(pause_payload),
        )

        prompt_text = self._pause_prompt_text(next_status=next_status, pause_payload=pause_payload, turn=turn)
        checkpoint_kind = (
            AgentRunCheckpointKind.APPROVAL
            if next_status == AgentRunStatus.WAITING_APPROVAL
            else AgentRunCheckpointKind.USER_INPUT
        )
        checkpoint = self._upsert_open_checkpoint(
            run=run,
            kind=checkpoint_kind,
            title="Approval needed" if checkpoint_kind == AgentRunCheckpointKind.APPROVAL else "Input needed",
            prompt=prompt_text,
            payload={**dict(pause_payload), **({"approval_preview": approval_preview} if approval_preview else {})},
            now=now,
        )
        next_metadata = dict(next_metadata)
        next_metadata["pending_checkpoint_id"] = str(checkpoint.id)
        AgentRun.objects.filter(id=run.id).update(metadata=next_metadata, updated_at=now)

        self._post_pause_followup(
            run=run,
            next_status=next_status,
            next_metadata=next_metadata,
            anchor_conversation=anchor_conversation,
            pause_payload=pause_payload,
            prompt_text=prompt_text,
            approval_id=approval_id,
            approval_preview=approval_preview,
            pause_state=pause_state,
            now=now,
        )

    def _pause_prompt_text(self, *, next_status: str, pause_payload: Mapping[str, object], turn) -> str:
        prompt_text = str(getattr(turn, "response_text", "") or "").strip()
        if prompt_text:
            return prompt_text
        if next_status == AgentRunStatus.WAITING_APPROVAL:
            tool_label = str(pause_payload.get("tool_name") or "").strip()
            tool_norm = tool_label.strip().lower()
            if tool_norm in {"initiate_phone_call", "phone_call"}:
                return "This task wants to place a phone call. Please review the details and approve or reject."
            return (
                "This task needs your approval to continue."
                + (f" Tool: {tool_label}." if tool_label else "")
                + " Please approve or deny from the sub-agent card."
            )

        raw_questions = pause_payload.get("questions")
        questions = [str(q).strip() for q in raw_questions if str(q or "").strip()] if isinstance(raw_questions, list) else []
        if questions:
            bullets = "\n".join([f"- {q}" for q in questions[:6]])
            return "I need a bit more info to continue:\n" f"{bullets}\n\n" "Please reply from the sub-agent card."
        return "I need a bit more info to continue. Please reply from the sub-agent card."

    def _post_pause_followup(
        self,
        *,
        run: AgentRun,
        next_status: str,
        next_metadata: Mapping[str, object],
        anchor_conversation: Conversation | None,
        pause_payload: Mapping[str, object],
        prompt_text: str,
        approval_id: str,
        approval_preview: Mapping[str, object] | None,
        pause_state: Mapping[str, object],
        now,
    ) -> None:
        followup_meta = next_metadata if isinstance(next_metadata, Mapping) else {}
        if getattr(run, "agentic_task_id", None):
            return
        delegate_intent = str(followup_meta.get("delegate_intent") or "").strip().lower()
        followup_requested = bool(followup_meta.get("followup_requested") or delegate_intent == "explicit")
        tool_name_value = str(pause_payload.get("tool_name") or "").strip().lower()
        force_followup = bool(
            approval_id
            and next_status == AgentRunStatus.WAITING_APPROVAL
            and tool_name_value in {"initiate_phone_call", "phone_call"}
        )
        should_post_followup = bool(anchor_conversation is not None and (followup_requested or force_followup))
        if should_post_followup and approval_id:
            already_posted = ConversationMessage.objects.filter(
                conversation_id=anchor_conversation.id,
                metadata__agent_run_id=str(run.id),
                metadata__pending_approval_id=approval_id,
                metadata__type="needs_approval",
            ).exists()
            if already_posted:
                should_post_followup = False
        if not should_post_followup or anchor_conversation is None:
            return

        message_meta = {
            "source": "agent_run",
            "agent_run_id": str(run.id),
            "type": "needs_approval" if next_status == AgentRunStatus.WAITING_APPROVAL else "needs_user",
        }
        if approval_id:
            message_meta["pending_approval_id"] = approval_id
        if approval_preview:
            message_meta["approval_preview"] = approval_preview

        content_blocks = ensure_assistant_text_blocks(prompt_text)
        if force_followup:
            self._append_phone_approval_block(
                content_blocks=content_blocks,
                pause_state=pause_state,
                approval_id=approval_id,
                run=run,
                tool_name_value=tool_name_value,
            )

        ConversationMessage.objects.create(
            conversation=anchor_conversation,
            sender=ConversationSender.AI,
            body=prompt_text,
            metadata=message_meta,
            content_blocks=content_blocks,
        )
        Conversation.objects.filter(id=anchor_conversation.id).update(last_activity_at=now)

    def _append_phone_approval_block(
        self,
        *,
        content_blocks: list[dict[str, object]],
        pause_state: Mapping[str, object],
        approval_id: str,
        run: AgentRun,
        tool_name_value: str,
    ) -> None:
        try:
            from apps.conversations.content_blocks import make_structured_block

            approval_event_data = pause_state.get("approval_event") or {}
            if not isinstance(approval_event_data, Mapping):
                return
            event_id_value = str(approval_event_data.get("event_id") or approval_event_data.get("eventId") or "").strip()
            tool_call_id_value = str(
                approval_event_data.get("tool_call_id") or approval_event_data.get("toolCallId") or ""
            ).strip()
            kind_value = str(approval_event_data.get("kind") or "phone").strip()
            status_value = str(approval_event_data.get("status") or "pending_approval").strip()
            input_payload = approval_event_data.get("input")
            safe_input = dict(input_payload) if isinstance(input_payload, Mapping) else {}
            approval_payload = approval_event_data.get("approval")
            safe_approval = dict(approval_payload) if isinstance(approval_payload, Mapping) else {}
            remote_payload = approval_event_data.get("remote")
            safe_remote = dict(remote_payload) if isinstance(remote_payload, Mapping) else {}
            payload_out: dict[str, object] = {
                "event_id": event_id_value or f"evt_{uuid.uuid4().hex[:12]}",
                "phase": "approval_requested",
                "status": status_value or "pending_approval",
                "tool_call_id": tool_call_id_value,
                "tool_name": tool_name_value or "initiate_phone_call",
                "kind": kind_value or "phone",
                "input": safe_input,
                "approval": safe_approval,
                "approval_id": approval_id,
                "run_id": str(run.id),
            }
            if safe_remote:
                payload_out["remote"] = safe_remote
            content_blocks.append(make_structured_block("tool_use", payload_out))
        except Exception:  # pragma: no cover - best effort UI card
            logger.exception("agent_run_portal_call_approval_block_failed run=%s", run.id)
