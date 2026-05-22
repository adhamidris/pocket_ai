from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
import uuid
from typing import Mapping

from django.conf import settings
from django.utils import timezone

from core.tenancy import tenant_context

from apps.agent_runs.execution.conversation_transcript import AgentRunConversationTranscriptMixin
from apps.agent_runs.execution.outcome_events import AgentRunOutcomeEventMixin
from apps.agent_runs.execution.audit import (
    _build_approval_preview,
    _build_email_approval_preview,
    _clip_text,
    _recursive_contains_force_final,
    _sanitize_approval_meta,
    _sanitize_remote_meta,
    _summarize_tool_result,
    sanitize_tool_event_for_audit,
)
from apps.agent_runs.execution.results import AgentRunProcessResult
from apps.agent_runs.execution.run_prompt import build_seed_prompt
from apps.agent_runs.execution.streaming import looks_like_machine_contract
from apps.agent_runs.models import (
    AgentRun,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
)
from apps.conversations.models import ConversationToolApproval, ConversationToolApprovalStatus
from apps.mcp.text.sanitizer import has_dsml_markup, strip_dsml_markup
from apps.rag.observability.logging import structured_log


logger = logging.getLogger(__name__)


class AgentRunExecutorMixin(AgentRunConversationTranscriptMixin, AgentRunOutcomeEventMixin):

    def _execute_run(self, run: AgentRun) -> AgentRunProcessResult:
        """
        Minimal executor: run one MCP turn using the run's goal and snapshot.

        This is intentionally conservative:
        - Uses the agent's MCP orchestrator (tool loop) for deterministic server-side tool execution.
        - Persists tool/status events as AgentRunEvents.
        - Stores the final response in AgentRun.result.
        """

        from apps.llm.llm_provider import load_mcp_provider
        from apps.mcp.orchestrator import McpOrchestratorService
        from apps.conversations.content_blocks import content_blocks_from_response_blocks, ensure_assistant_text_blocks
        from apps.conversations.models import ConversationSender

        business_id = run.business_profile_id
        if not business_id:
            raise RuntimeError("run missing business_profile_id")

        provider = load_mcp_provider()
        if provider is None:
            raise RuntimeError("MCP provider is not configured.")

        started = time.monotonic()
        spec = dict(run.run_snapshot or {}) if isinstance(run.run_snapshot, dict) else {}
        goal = str(spec.get("goal") or spec.get("name") or run.title or "").strip()
        if not goal:
            raise RuntimeError("run has no goal/title to execute")

        allowed_tools: set[str] | None = None

        timeout_seconds = None
        constraints = spec.get("constraints")
        if isinstance(constraints, Mapping):
            raw_timeout = constraints.get("timeout_seconds")
            try:
                timeout_seconds = int(raw_timeout) if raw_timeout is not None else None
            except (TypeError, ValueError):
                timeout_seconds = None
        if timeout_seconds is None:
            timeout_seconds = 300
        timeout_seconds = max(10, min(int(timeout_seconds), 1800))

        success_criteria = spec.get("success_criteria") or []
        if not isinstance(success_criteria, list):
            success_criteria = [str(success_criteria)]
        criteria_lines = [str(item).strip() for item in success_criteria if str(item or "").strip()]
        criteria_text = "\n".join([f"- {line}" for line in criteria_lines])
        conversation_summary = f"Agent run\nGoal: {goal}".strip()
        if criteria_text:
            conversation_summary = f"{conversation_summary}\nSuccess criteria:\n{criteria_text}".strip()
        conversation_summary = _clip_text(conversation_summary, 1400)

        with tenant_context(business_id):
            run_metadata = run.metadata if isinstance(getattr(run, "metadata", None), Mapping) else {}

            anchor_conversation = run.conversation

            execution_conversation, run_metadata = self._resolve_execution_conversation(
                run=run,
                business_id=business_id,
                run_metadata=run_metadata,
                anchor_conversation=anchor_conversation,
                conversation_summary=conversation_summary,
            )

            orchestrator = McpOrchestratorService(agent=run.agent_profile, provider=provider)

            pause_state: dict[str, object] = {"approval_event": None, "user_input_event": None, "external_request_event": None}
            latest_email_draft_preview: dict[str, object] | None = None

            def _deadline_exceeded() -> bool:
                return (time.monotonic() - started) >= float(timeout_seconds or 300)

            def _should_cancel() -> bool:
                if _deadline_exceeded():
                    return True
                # Allow external cancellation (best effort; cheap check).
                status_now = (
                    AgentRun.objects.filter(id=run.id).values_list("status", flat=True).first()
                )
                return status_now == AgentRunStatus.CANCELLED

            def _on_status_change(state: object) -> None:
                if not state:
                    return
                if isinstance(state, str):
                    code = state.strip()
                    if code:
                        self._append_event(
                            run,
                            stream=AgentRunEventStream.SYSTEM,
                            event_type=AgentRunEventType.PROGRESS,
                            label=code,
                            payload={"code": code},
                        )
                    return
                if isinstance(state, Mapping):
                    code = state.get("code") or state.get("state")
                    label = state.get("label")
                    meta = state.get("meta") if isinstance(state.get("meta"), Mapping) else None
                    code_str = str(code).strip() if isinstance(code, str) else ""
                    label_str = str(label).strip() if isinstance(label, str) else ""
                    payload: dict[str, object] = {}
                    if code_str:
                        payload["code"] = code_str
                    if label_str:
                        payload["label"] = label_str
                    if meta:
                        payload["meta"] = dict(meta)
                    if payload:
                        self._append_event(
                            run,
                            stream=AgentRunEventStream.SYSTEM,
                            event_type=AgentRunEventType.PROGRESS,
                            label=label_str or code_str or "status",
                            payload=payload,
                        )

            visible_stream_buffer: list[str] = []
            suppress_machine_contract_stream = False


            def _flush_visible_assistant_text(*, force: bool = False) -> None:
                nonlocal suppress_machine_contract_stream
                if not visible_stream_buffer:
                    return
                raw = "".join(visible_stream_buffer)
                if looks_like_machine_contract(raw):
                    visible_stream_buffer.clear()
                    suppress_machine_contract_stream = True
                    return
                if not force and len(raw) < 180 and not re.search(r"[\n.!?]\s*$", raw):
                    return
                visible_stream_buffer.clear()
                text = raw.strip()
                if not text or looks_like_machine_contract(text):
                    return
                self._append_event(
                    run,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=AgentRunEventType.PROGRESS,
                    label="Assistant",
                    payload={"kind": "assistant_message", "text": _clip_text(text, 2400)},
                )

            def _on_response_text_delta(chunk: str) -> None:
                nonlocal suppress_machine_contract_stream
                text = str(chunk or "")
                if not text:
                    return
                if suppress_machine_contract_stream:
                    return
                visible_stream_buffer.append(text)

            def _on_tool_event(event: Mapping[str, object] | None) -> None:
                nonlocal latest_email_draft_preview
                if not event:
                    return
                _flush_visible_assistant_text(force=True)
                phase = str(event.get("phase") or "").strip().lower()
                tool_name = str(event.get("tool_name") or "").strip()
                status_value = str(event.get("status") or "").strip().lower()
                if phase == "approval_requested":
                    pause_state["approval_event"] = dict(event)
                if tool_name == "email_create_draft" and phase == "finished":
                    input_payload = event.get("input") if isinstance(event.get("input"), Mapping) else None
                    output_payload = event.get("output") if isinstance(event.get("output"), Mapping) else None
                    preview = _build_email_approval_preview(input_payload or {}) if input_payload else None
                    if isinstance(preview, dict):
                        draft_id = ""
                        if isinstance(output_payload, Mapping):
                            draft_id = str(output_payload.get("draft_id") or output_payload.get("draftId") or "").strip()
                        if draft_id:
                            preview["draft_id"] = draft_id
                        latest_email_draft_preview = preview
                if tool_name == "request_user_input" and phase in {"started", "finished"}:
                    pause_state["user_input_event"] = dict(event)
                label = f"{phase}:{tool_name}" if tool_name else (phase or "tool_event")
                if tool_name == "initiate_phone_call" and phase == "finished":
                    output_payload = event.get("output") if isinstance(event.get("output"), Mapping) else {}
                    call_session_id = ""
                    if isinstance(output_payload, Mapping):
                        call_session_id = str(
                            output_payload.get("call_session_id") or output_payload.get("callSessionId") or ""
                        ).strip()
                    if status_value == "needs_external" or call_session_id:
                        pause_state["external_request_event"] = dict(event)
                self._append_event(
                    run,
                    stream=AgentRunEventStream.EXECUTED,
                    event_type=AgentRunEventType.PROGRESS,
                    label=label[:240],
                    payload=sanitize_tool_event_for_audit(event),
                )

                # Persist compact tool context into the execution transcript so resumed runs
                # continue from prior tool outcomes instead of re-running searches.
                if phase == "finished":
                    input_payload = event.get("input") if isinstance(event.get("input"), Mapping) else None
                    tool_args = dict(input_payload) if isinstance(input_payload, Mapping) else {}

                    output_payload = event.get("output") if isinstance(event.get("output"), Mapping) else None
                    tool_output = dict(output_payload) if isinstance(output_payload, Mapping) else {}
                    if not tool_output:
                        tool_output = _summarize_tool_result(tool_name, {"status": status_value})

                    extraction_result = dict(output_payload) if isinstance(output_payload, Mapping) else {}
                    if status_value and "status" not in extraction_result:
                        extraction_result["status"] = status_value
                    if not extraction_result:
                        extraction_result = {"status": status_value or "ok"}

                    try:
                        from apps.conversations.content_blocks import make_tool_result_block, make_tool_use_block
                        from apps.conversations.models import ConversationSender

                        event_id_value = str(event.get("event_id") or event.get("eventId") or "").strip()
                        if not event_id_value:
                            event_id_value = f"evt_{uuid.uuid4().hex[:12]}"
                        tool_call_id_value = str(event.get("tool_call_id") or event.get("toolCallId") or "").strip()
                        duration_ms = event.get("duration_ms")
                        try:
                            duration_ms_int = int(duration_ms) if duration_ms is not None else 0
                        except (TypeError, ValueError):
                            duration_ms_int = 0

                        remote_payload = _sanitize_remote_meta(event.get("remote"))

                        tool_use_block = make_tool_use_block(
                            event_id=event_id_value,
                            tool_name=tool_name or "tool",
                            tool_call_id=tool_call_id_value,
                            arguments=tool_args,
                            status=status_value or "ok",
                            duration_ms=duration_ms_int,
                            remote=remote_payload,
                        )
                        tool_result_block = make_tool_result_block(
                            event_id=event_id_value,
                            tool_name=tool_name or "tool",
                            output=tool_output,
                            status=status_value or "ok",
                            duration_ms=duration_ms_int,
                        )

                        summary_parts: list[str] = [f"Tool result ({tool_name or 'tool'}) [{event_id_value}]"]
                        if tool_args:
                            summary_parts.append(
                                "Input: " + _clip_text(json.dumps(tool_args, ensure_ascii=False), 1200)
                            )
                        if tool_output:
                            summary_parts.append(
                                "Output: " + _clip_text(json.dumps(tool_output, ensure_ascii=False), 2000)
                            )
                        summary_text = _clip_text("\n".join(summary_parts), 3200)

                        self._append_execution_message(
                            execution_conversation=execution_conversation,
                            sender=ConversationSender.AI,
                            body=summary_text,
                            metadata={
                                "source": "agent_run",
                                "agent_run_id": str(run.id),
                                "type": "tool_result",
                                "tool_name": tool_name,
                                "tool_event_id": event_id_value,
                            },
                            content_blocks=[tool_use_block, tool_result_block],
                        )
                    except Exception:  # pragma: no cover - best effort only
                        logger.exception("agent_run_tool_transcript_append_failed run=%s tool=%s", run.id, tool_name)

                    if getattr(settings, "MCP_RUN_MEMORY_ENABLED", True) and tool_name:
                        try:
                            from apps.conversations.memory_extraction import MemoryExtractionService

                            extractor = MemoryExtractionService()
                            extractor.extract_from_tool_result(
                                run=run,
                                tool_name=tool_name,
                                arguments=tool_args,
                                result=extraction_result,
                                user=run.created_by if getattr(run, "created_by_id", None) else None,
                            )
                        except Exception:  # pragma: no cover - best effort only
                            logger.exception("agent_run_memory_extraction_failed run=%s tool=%s", run.id, tool_name)

            metadata_snapshot = run_metadata if isinstance(run_metadata, Mapping) else {}
            workflow_runtime_context = self._build_workflow_runtime_context(run)
            seed_prompt = build_seed_prompt(
                goal=goal,
                criteria_lines=criteria_lines,
                constraints=spec.get("constraints"),
                metadata_snapshot=metadata_snapshot,
                workflow_runtime_context=workflow_runtime_context,
            )

            self._ensure_execution_seed_prompt(
                run=run,
                execution_conversation=execution_conversation,
                seed_prompt=seed_prompt,
            )
            run_metadata, metadata_snapshot = self._feed_external_inputs(
                run=run,
                execution_conversation=execution_conversation,
                run_metadata=run_metadata,
                metadata_snapshot=metadata_snapshot,
            )

            # Execute pending tool call if resuming from approval
            pending_tool_call = run_metadata.get("pending_tool_call")
            pending_tool_executed = False
            if pending_tool_call and isinstance(pending_tool_call, Mapping):
                tool_executed = self._execute_pending_tool_call(
                    run=run,
                    pending_tool_call=pending_tool_call,
                    execution_conversation=execution_conversation,
                    orchestrator=orchestrator,
                    on_tool_event=_on_tool_event,
                )
                if tool_executed:
                    pending_tool_executed = True
                    # Clear the pending tool call from metadata
                    next_meta = dict(run_metadata)
                    next_meta.pop("pending_tool_call", None)
                    next_meta.pop("pending_approval_id", None)
                    AgentRun.objects.filter(id=run.id).update(metadata=next_meta, updated_at=timezone.now())
                    run.metadata = next_meta
                    run_metadata = next_meta

            # If a pending tool was executed, update the user message to indicate continuation
            if pending_tool_executed:
                turn_user_message = "The approved tool has been executed. Continue with the workflow."
                self._append_execution_message(
                    execution_conversation=execution_conversation,
                    sender=ConversationSender.CUSTOMER,
                    body=turn_user_message,
                    metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "post_approval_continue"},
                )
            else:
                last_exec = (
                    execution_conversation.messages.order_by("-sent_at", "-created_at").only("sender", "body").first()
                )
                if last_exec and last_exec.sender == ConversationSender.CUSTOMER:
                    turn_user_message = str(last_exec.body or "").strip()
                else:
                    turn_user_message = "Continue."
                    self._append_execution_message(
                        execution_conversation=execution_conversation,
                        sender=ConversationSender.CUSTOMER,
                        body=turn_user_message,
                        metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "continue"},
                    )

            turn = orchestrator.stream_turn(
                conversation=execution_conversation,
                user_message=turn_user_message,
                on_response_text_delta=_on_response_text_delta,
                on_status_change=_on_status_change,
                on_tool_event=_on_tool_event,
                should_cancel=_should_cancel,
                allowed_tools=allowed_tools,
                wait_for_tool_approval=False,
            )
            # Assistant text is only run progress when a later tool event proves
            # it was pre-tool/intermediate narration. Any remaining buffered
            # text at turn end is the final response and is persisted below via
            # run.result / the assistant transcript message.
            visible_stream_buffer.clear()

            raw_response_text_value = str(getattr(turn, "response_text", "") or "").strip()
            response_text_value = raw_response_text_value
            malformed_final_reason = ""
            if run.source in {AgentRunSource.TASK, AgentRunSource.SCHEDULE}:
                if has_dsml_markup(raw_response_text_value):
                    stripped = strip_dsml_markup(raw_response_text_value).strip()
                    response_text_value = stripped
                    malformed_final_reason = "final response contained internal DSML/tool-call markup"
                    if not stripped:
                        malformed_final_reason = "final response contained only internal DSML/tool-call markup"
            if response_text_value and not malformed_final_reason:
                response_blocks = list(getattr(turn, "response_blocks", None) or ())
                blocks = content_blocks_from_response_blocks(response_blocks)
                if not blocks:
                    blocks = ensure_assistant_text_blocks(response_text_value)
                self._append_execution_message(
                    execution_conversation=execution_conversation,
                    sender=ConversationSender.AI,
                    body=response_text_value,
                    metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "assistant"},
                    content_blocks=blocks,
                )

            if _deadline_exceeded():
                raise RuntimeError("timeout: run exceeded configured timeout_seconds")

            approval_event = pause_state.get("approval_event") if isinstance(pause_state, dict) else None
            user_input_event = pause_state.get("user_input_event") if isinstance(pause_state, dict) else None
            external_request_event = pause_state.get("external_request_event") if isinstance(pause_state, dict) else None

            next_status = AgentRunStatus.COMPLETED
            pause_event_type: str | None = None
            pause_payload: dict[str, object] | None = None

            approval_payload: dict[str, object] | None = None
            approval_id = ""
            if isinstance(approval_event, Mapping):
                approval_payload = (
                    approval_event.get("approval") if isinstance(approval_event.get("approval"), Mapping) else None
                )
                approval_id = str(approval_payload.get("id") if approval_payload else "" or "").strip()
            if not approval_id:
                tool_trace = list(getattr(turn, "tool_trace", None) or ())
                for entry in tool_trace:
                    if not isinstance(entry, Mapping):
                        continue
                    tool_name_value = str(entry.get("tool") or entry.get("tool_name") or "").strip().lower()
                    status_value = str(entry.get("status") or "").strip().lower()
                    if tool_name_value != "initiate_phone_call":
                        continue
                    if status_value not in {"pending", "pending_approval"}:
                        continue
                    try:
                        with tenant_context(business_id):
                            approval = (
                                ConversationToolApproval.objects.filter(
                                    conversation_id=execution_conversation.id,
                                    tool_name="initiate_phone_call",
                                    status=ConversationToolApprovalStatus.PENDING,
                                )
                                .order_by("-requested_at")
                                .first()
                            )
                        if approval:
                            approval_payload = {
                                "id": str(approval.id),
                                "status": approval.status,
                                "operation_type": str((approval.metadata or {}).get("operation_type") or ""),
                                "reason": str((approval.metadata or {}).get("reason") or ""),
                            }
                            approval_event = {
                                "phase": "approval_requested",
                                "status": approval.status,
                                "tool_name": "initiate_phone_call",
                                "approval": approval_payload,
                            }
                            pause_state["approval_event"] = dict(approval_event)
                            approval_id = str(approval.id)
                            break
                    except Exception:  # pragma: no cover - best effort only
                        logger.exception("agent_run approval fallback lookup failed run=%s", run.id)
                    break

            user_input_payload: dict[str, object] | None = None
            if isinstance(user_input_event, Mapping):
                user_input_payload = (
                    user_input_event.get("input") if isinstance(user_input_event.get("input"), Mapping) else None
                )

            external_request_id = ""
            external_request_payload: dict[str, object] | None = None
            external_request_tool = ""
            if isinstance(external_request_event, Mapping):
                external_request_tool = str(external_request_event.get("tool_name") or "").strip().lower()
                output = external_request_event.get("output") if isinstance(external_request_event.get("output"), Mapping) else None
                if isinstance(output, Mapping):
                    external_request_id = str(
                        output.get("agent_request_id")
                        or output.get("call_session_id")
                        or output.get("callSessionId")
                        or ""
                    ).strip()
                    request_payload = output.get("request") if isinstance(output.get("request"), Mapping) else None
                    if not external_request_id and isinstance(request_payload, Mapping):
                        external_request_id = str(request_payload.get("id") or "").strip()
                        external_request_payload = dict(request_payload)
                    elif isinstance(request_payload, Mapping):
                        external_request_payload = dict(request_payload)

            approval_preview: dict[str, object] | None = None
            pending_tool_call: Mapping[str, object] | None = None
            if approval_id:
                next_status = AgentRunStatus.WAITING_APPROVAL
                pause_event_type = AgentRunEventType.NEEDS_APPROVAL
                pause_payload = {
                    "approval": _sanitize_approval_meta(approval_payload) or {},
                    "tool_name": str(approval_event.get("tool_name") or "") if isinstance(approval_event, Mapping) else "",
                    "remote": dict(approval_event.get("remote") or {}) if isinstance(approval_event, Mapping) and isinstance(approval_event.get("remote"), Mapping) else {},
                }
            elif user_input_payload:
                next_status = AgentRunStatus.WAITING_USER
                pause_event_type = AgentRunEventType.NEEDS_USER
                pause_payload = {
                    "questions": list(user_input_payload.get("questions") or ())
                    if isinstance(user_input_payload.get("questions"), list)
                    else [],
                    "prompt": str(user_input_payload.get("prompt") or "").strip(),
                    "schema": dict(user_input_payload.get("schema") or {})
                    if isinstance(user_input_payload.get("schema"), Mapping)
                    else {},
                }
            elif external_request_id:
                next_status = AgentRunStatus.WAITING_EXTERNAL

            now = timezone.now()
            tool_trace = list(turn.tool_trace or ())
            forced_final_trace = self._forced_final_trace(tool_trace)
            terminal_error_detail = ""
            if next_status == AgentRunStatus.COMPLETED and forced_final_trace:
                next_status = AgentRunStatus.FAILED
                reason = str(forced_final_trace.get("reason") or "tool_loop_forced_final").strip()
                next_tools = forced_final_trace.get("next_tools")
                next_tools_list = [str(item) for item in next_tools] if isinstance(next_tools, list) else []
                terminal_error_detail = (
                    "Run stopped before completing because the tool loop reached a safety limit."
                    + (f" Reason: {reason}." if reason else "")
                    + (f" Pending tools: {', '.join(next_tools_list[:6])}." if next_tools_list else "")
                )
            llm_usage_payload = dict(turn.llm_usage or {}) if getattr(turn, "llm_usage", None) else None
            if next_status == AgentRunStatus.COMPLETED and _recursive_contains_force_final(llm_usage_payload):
                next_status = AgentRunStatus.FAILED
                terminal_error_detail = "Run stopped before completing because the tool loop reached a forced-final stage."
            if next_status == AgentRunStatus.COMPLETED and malformed_final_reason:
                next_status = AgentRunStatus.FAILED
                terminal_error_detail = (
                    "Run finished with malformed internal tool-call markup instead of a safe user-facing result. "
                    f"Reason: {malformed_final_reason}."
                )
                response_text_value = ""
            base_result = {
                "response_text": response_text_value,
                **({"raw_response_text": raw_response_text_value} if malformed_final_reason and raw_response_text_value else {}),
                "response_blocks": list(turn.response_blocks or ()),
                "planned_actions": [dataclasses.asdict(a) for a in (turn.planned_actions or ())] if turn.planned_actions else [],
                "extractions": [dataclasses.asdict(e) for e in (turn.extractions or ())] if turn.extractions else [],
                "llm_usage": llm_usage_payload,
                "tool_trace": tool_trace,
            }

            next_metadata = dict(run.metadata or {}) if isinstance(getattr(run, "metadata", None), dict) else {}
            if malformed_final_reason:
                next_metadata["malformed_final_output"] = {
                    "reason": malformed_final_reason,
                    "raw_length": len(raw_response_text_value),
                }
            if next_status == AgentRunStatus.WAITING_APPROVAL and approval_id:
                next_metadata["pending_approval_id"] = approval_id
                # Store the full pending tool call for direct execution on resume
                approval_event_data = pause_state.get("approval_event") or {}
                tool_result_output = approval_event_data.get("output") if isinstance(approval_event_data.get("output"), Mapping) else {}
                pending_tool_call = tool_result_output.get("pending_tool_call")
                if pending_tool_call and isinstance(pending_tool_call, Mapping):
                    next_metadata["pending_tool_call"] = dict(pending_tool_call)
                approval_preview = _build_approval_preview(
                    pause_payload.get("tool_name") if isinstance(pause_payload, dict) else "",
                    approval_event_data if isinstance(approval_event_data, Mapping) else None,
                    pending_tool_call if isinstance(pending_tool_call, Mapping) else None,
                )
                if (
                    not approval_preview
                    and isinstance(approval_payload, Mapping)
                    and isinstance(approval_payload.get("preview"), Mapping)
                ):
                    preview_payload = dict(approval_payload.get("preview") or {})
                    tool_name_value = ""
                    if isinstance(pause_payload, dict):
                        tool_name_value = str(pause_payload.get("tool_name") or "").strip().lower()
                    if tool_name_value == "email_send_draft":
                        approval_preview = _build_email_approval_preview(preview_payload) or preview_payload
                    else:
                        approval_preview = preview_payload
                if not approval_preview and isinstance(pause_payload, dict):
                    tool_name_value = str(pause_payload.get("tool_name") or "").strip().lower()
                    if tool_name_value == "email_send_draft":
                        fallback = latest_email_draft_preview or next_metadata.get("last_email_draft_preview")
                        if isinstance(fallback, Mapping):
                            draft_id = ""
                            if isinstance(pending_tool_call, Mapping):
                                args = pending_tool_call.get("arguments")
                                if isinstance(args, Mapping):
                                    draft_id = str(args.get("draft_id") or args.get("draftId") or "").strip()
                            if not draft_id or str(fallback.get("draft_id") or "").strip() == draft_id:
                                approval_preview = dict(fallback)
            run_report = self._build_run_report(
                run=run,
                next_status=next_status,
                response_text=response_text_value,
                tool_trace=tool_trace,
                pause_payload=pause_payload,
                approval_preview=approval_preview,
            )
            base_result["run_report"] = run_report
            if next_status == AgentRunStatus.WAITING_USER and isinstance(pause_payload, dict):
                next_metadata["pending_user_input"] = dict(pause_payload)
            if next_status == AgentRunStatus.WAITING_EXTERNAL and external_request_id:
                if external_request_tool == "initiate_phone_call":
                    next_metadata["pending_call_session_id"] = external_request_id
                else:
                    next_metadata["pending_agent_request_id"] = external_request_id
                    if external_request_payload:
                        next_metadata["pending_agent_request"] = external_request_payload
            if latest_email_draft_preview:
                next_metadata["last_email_draft_preview"] = latest_email_draft_preview

            completion_index: int | None = None
            if next_status == AgentRunStatus.COMPLETED:
                try:
                    completion_index = int(next_metadata.get("completion_count") or 0) + 1
                except (TypeError, ValueError):
                    completion_index = 1
                next_metadata["completion_count"] = completion_index

            update_fields: dict[str, object] = {
                "status": next_status,
                "lease_expires_at": None,
                "run_after": None,
                "error_detail": terminal_error_detail,
                "result": base_result,
                "metadata": next_metadata,
                "updated_at": now,
            }
            if next_status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}:
                update_fields["finished_at"] = now

            updated = AgentRun.objects.filter(id=run.id, status=AgentRunStatus.RUNNING).update(**update_fields)
            if not updated:
                status_now = AgentRun.objects.filter(id=run.id).values_list("status", flat=True).first() or ""
                return AgentRunProcessResult(run_id=str(run.id), status=str(status_now) or "unknown")
            run.status = next_status
            run.result = base_result
            run.metadata = next_metadata
            run.error_detail = terminal_error_detail

            if next_status == AgentRunStatus.WAITING_APPROVAL and approval_id:
                try:
                    tool_name_value = ""
                    remote_tool_name_value = ""
                    if isinstance(pause_payload, Mapping):
                        tool_name_value = str(pause_payload.get("tool_name") or "").strip()
                        remote = pause_payload.get("remote") if isinstance(pause_payload.get("remote"), Mapping) else {}
                        remote_tool_name_value = str(remote.get("tool") or remote.get("tool_name") or "").strip()
                    structured_log(
                        "mcp",
                        "agent_run.waiting_approval",
                        {
                            "tool_name": tool_name_value,
                            "remote_tool_name": remote_tool_name_value,
                        },
                        context={
                            "business": business_id,
                            "run": run.id,
                            "conversation": getattr(execution_conversation, "id", None),
                            "approval": approval_id,
                        },
                        level=logging.INFO,
                    )
                except Exception:  # pragma: no cover - observability must not block agent run processing
                    pass

            report_state: dict[str, object] = {}
            if run.source in {AgentRunSource.TASK, AgentRunSource.SCHEDULE}:
                report_state = self._persist_run_report(
                    run=run,
                    report=run_report,
                    next_status=next_status,
                    now=now,
                )
                if report_state:
                    next_metadata = dict(next_metadata)
                    next_metadata["run_report_state"] = report_state
                    AgentRun.objects.filter(id=run.id).update(metadata=next_metadata, result={**base_result, "run_report_state": report_state}, updated_at=now)

            self._emit_run_outcome_events(
                run=run,
                next_status=next_status,
                terminal_error_detail=terminal_error_detail,
                external_request_id=external_request_id,
                external_request_tool=external_request_tool,
                pause_event_type=pause_event_type,
                pause_payload=pause_payload,
                turn=turn,
                now=now,
                next_metadata=next_metadata,
                anchor_conversation=anchor_conversation,
                response_text_value=response_text_value,
                completion_index=completion_index,
                approval_id=approval_id,
                approval_preview=approval_preview,
                pause_state=pause_state,
            )

        return AgentRunProcessResult(run_id=str(run.id), status=next_status)
