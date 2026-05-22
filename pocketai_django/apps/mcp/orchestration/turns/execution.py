from __future__ import annotations

import logging
import time
from typing import Callable, Mapping, Sequence

from django.utils import timezone

from apps.accounts.feature_flags import FeatureFlagService
from apps.conversations.models import Conversation, ConversationMessage
from apps.rag.observability.logging import structured_log

from core.otel import otel_trace

from ... import prompts
from ...runtime.portal_block_stream import PORTAL_BLOCK_TOOL_NAME, _PortalBlockStream
from ...runtime.rag_observability import compact_retrieval_observability
from ...remote_client import McpRemoteError
from ...text.sanitizer import sanitize_text, sanitize_with_diagnostics
from ...types import ToolConstraintError, ToolExecutionContext
from ..responses.helpers import INLINE_RESPONSE_BLOCK_PATTERN
from .streaming.answer_emit import _emit_final_answer as _emit_final_answer_impl
from .streaming.callbacks import (
    _emit_tokens as _emit_tokens_impl,
)
from .streaming.stream_filters import _filter_dsml_stream as _filter_dsml_stream_impl
from .streaming.stream_logging import _log_stream_mismatch as _log_stream_mismatch_impl
from .streaming.stream_buffer import _flush_stream_buffer as _flush_stream_buffer_impl
from .streaming.portal_stream import _on_stream_tool_call_delta as _on_stream_tool_call_delta_impl
from .streaming.stream_handlers import (
    _answer_stream_chunk as _answer_stream_chunk_impl,
    _first_stream_chunk as _first_stream_chunk_impl,
)
from .streaming.status import _status_event as _status_event_impl
from .streaming.stream_state import _TurnStreamState
from .tools.email import McpTurnEmailToolsMixin
from .tools.email_auto_send import McpTurnEmailAutoSendMixin
from .tools.gateway import McpTurnGatewayToolsMixin
from .tools.internal import McpTurnInternalToolsMixin
from .tools.native_policy import McpTurnNativePolicyMixin
from .tools.phone import McpTurnPhoneToolsMixin
from .tools.preparation import McpTurnToolPreparationMixin
from .tools.results import McpTurnToolResultsMixin
from .tools.trace import McpTurnToolTraceMixin
from .tools.ui import (
    _split_portal_tool_calls as _split_portal_tool_calls_impl,
)
from .finalization import McpTurnFinalizationMixin
from .initial_pass import McpTurnInitialPassMixin
from .setup import McpTurnSetupMixin
from .verification import McpTurnVerificationMixin


TRACER = otel_trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


class McpTurnExecutionMixin(
    McpTurnEmailAutoSendMixin,
    McpTurnEmailToolsMixin,
    McpTurnFinalizationMixin,
    McpTurnGatewayToolsMixin,
    McpTurnInternalToolsMixin,
    McpTurnNativePolicyMixin,
    McpTurnPhoneToolsMixin,
    McpTurnToolPreparationMixin,
    McpTurnToolResultsMixin,
    McpTurnToolTraceMixin,
    McpTurnVerificationMixin,
    McpTurnInitialPassMixin,
    McpTurnSetupMixin,
):

    def _execute_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        user_metadata: Mapping[str, object] | None = None,
        allowed_tools: set[str] | None = None,
        wait_for_tool_approval: bool = True,
        portal_emit_blocks_enabled: bool = True,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_spinner_update: Callable[[str], None] | None = None,
        on_tool_event: Callable[[Mapping[str, object]], None] | None = None,
        on_tool_decision: Callable[[str], None] | None = None,
        on_block_event: Callable[[Mapping[str, object]], None] | None = None,
        on_reasoning_event: Callable[[Mapping[str, object]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        """
        Build the orchestration plan for the latest customer message.

        Splits the turn into an internal tool loop (no user-facing streaming)
        followed by a final answer pass that streams only customer-facing text.
        """

        turn_setup = self._prepare_turn_setup(
            conversation=conversation,
            user_message=user_message,
        )
        all_remote_connections = turn_setup.all_remote_connections
        messages = turn_setup.messages
        filter_level = turn_setup.filter_level
        initial_stream_filter_level = turn_setup.initial_stream_filter_level
        tool_context = turn_setup.tool_context

        feature_state = FeatureFlagService.snapshot(conversation.business_profile)
        tool_catalog = self._prepare_turn_tool_catalog(
            conversation=conversation,
            user_message=user_message,
            allowed_tools=allowed_tools,
            wait_for_tool_approval=wait_for_tool_approval,
            portal_emit_blocks_enabled=portal_emit_blocks_enabled,
            all_remote_connections=all_remote_connections,
            tool_context=tool_context,
            feature_state=feature_state,
        )
        disable_tools_for_turn = tool_catalog.disable_tools_for_turn
        gateway_enabled = tool_catalog.gateway_enabled
        native_tool_names_all = tool_catalog.native_tool_names_all

        if not self.provider:
            raise RuntimeError("MCP provider is not configured.")

        transcript = list(messages)
        initial_tool_plan = self._prepare_initial_tool_plan(
            conversation=conversation,
            user_message=user_message,
            transcript=transcript,
            tool_context=tool_context,
            disable_tools_for_turn=disable_tools_for_turn,
            on_block_event=on_block_event,
        )
        verification_enabled = initial_tool_plan.verification_enabled
        verification_blocks_streaming = initial_tool_plan.verification_blocks_streaming
        streaming_allowed = initial_tool_plan.streaming_allowed
        tool_definitions_for_model = initial_tool_plan.tool_definitions_for_model
        portal_only_tools = initial_tool_plan.portal_only_tools
        initial_tools = initial_tool_plan.initial_tools

        tool_phase_assistant_message: dict[str, object] | None = None
        final_assistant_message: dict[str, object] | None = None
        stream_state = _TurnStreamState()
        single_pass_candidate: str | None = None

        def _status_event(code: str, label: str | None = None, meta: Mapping[str, object] | None = None) -> None:
            _status_event_impl(
                code,
                on_status_change=on_status_change,
                label=label,
                meta=meta,
            )

        def _mark_answer_started(label: str | None = "Responding…") -> None:
            if stream_state.final_started:
                _status_event("responding", label)
                return
            stream_state.final_started = True
            _status_event("answer_started", label)
            _status_event("responding", label)

        portal_block_stream = _PortalBlockStream(on_block_event)

        def _split_portal_tool_calls(
            tool_calls: Sequence[Mapping[str, object]],
        ) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
            return _split_portal_tool_calls_impl(tool_calls, tool_name_for_call=self._tool_name)

        def _on_stream_tool_call_delta(tool_call: Mapping[str, object] | None) -> None:
            _on_stream_tool_call_delta_impl(tool_call, portal_block_stream=portal_block_stream)

        _status_event("thinking", "Thinking…")

        def _emit_tokens(text: str) -> None:
            _emit_tokens_impl(
                text,
                streaming_mode=stream_state.mode,
                first_pass_streamed_chunks=stream_state.first_pass_chunks,
                answer_streamed_chunks=stream_state.answer_chunks,
                on_response_text_delta=on_response_text_delta,
                logger_obj=logger,
            )

        def _emit_final_answer(text: str) -> None:
            stream_state.mode = _emit_final_answer_impl(
                text,
                mark_answer_started=_mark_answer_started,
                answer_streamed_chunks=stream_state.answer_chunks,
                on_response_text_delta=on_response_text_delta,
                logger_obj=logger,
            )

        def _flush_stream_buffer(
            stage: str,
            *,
            filter_override: str | None = None,
            flush_remainder: bool = False,
        ) -> None:
            stream_state.buffer = _flush_stream_buffer_impl(
                stream_state.buffer,
                conversation=conversation,
                default_filter_level=filter_level,
                stage=stage,
                stream_dropped=stream_state.dropped,
                emit_tokens=_emit_tokens,
                filter_override=filter_override,
                flush_remainder=flush_remainder,
            )

        def _log_stream_mismatch(target: list[str], final_text: str, *, stage: str) -> None:
            _log_stream_mismatch_impl(
                target,
                final_text,
                stage=stage,
                conversation=conversation,
                logger_obj=logger,
            )

        def _first_stream_chunk(chunk: str) -> None:
            stream_state.dsml_skip_line, stream_state.inline_blocks_detected, stream_state.initial_started = _first_stream_chunk_impl(
                chunk,
                dsml_skip_line=stream_state.dsml_skip_line,
                inline_response_blocks_detected=stream_state.inline_blocks_detected,
                initial_stream_started=stream_state.initial_started,
                filter_dsml_stream=lambda value, skip_line: _filter_dsml_stream_impl(value, skip_line=skip_line),
                status_event=_status_event,
                emit_tokens=_emit_tokens,
            )

        def _answer_stream_chunk(chunk: str) -> None:
            stream_state.buffer, stream_state.dsml_skip_line, stream_state.inline_blocks_detected = _answer_stream_chunk_impl(
                chunk,
                stream_buffer=stream_state.buffer,
                dsml_skip_line=stream_state.dsml_skip_line,
                inline_response_blocks_detected=stream_state.inline_blocks_detected,
                final_answer_started=stream_state.final_started,
                filter_dsml_stream=lambda value, skip_line: _filter_dsml_stream_impl(value, skip_line=skip_line),
                mark_answer_started=_mark_answer_started,
                emit_tokens=_emit_tokens,
                response_block_pattern=INLINE_RESPONSE_BLOCK_PATTERN,
            )

        initial_pass = self._run_initial_pass(
            conversation=conversation,
            transcript=transcript,
            initial_tools=initial_tools,
            streaming_allowed=streaming_allowed,
            tool_context=tool_context,
            portal_block_stream=portal_block_stream,
            first_stream_chunk=_first_stream_chunk,
            on_stream_tool_call_delta=_on_stream_tool_call_delta,
            split_portal_tool_calls=_split_portal_tool_calls,
            on_tool_decision=on_tool_decision,
            on_reasoning_event=on_reasoning_event,
            should_cancel=should_cancel,
        )
        first_stream_message = initial_pass.first_stream_message
        first_stream_tool_calls = initial_pass.first_stream_tool_calls
        first_content_raw = initial_pass.first_content_raw
        pending_assistant = initial_pass.pending_assistant

        # If we got tool calls, execute them and allow an iterative tool loop
        # (including additional model turns with tools) before the final-answer pass.
        if first_stream_tool_calls:
            assistant_message = pending_assistant or {}
            if streaming_allowed:
                # All subsequent streamed content belongs to the tool-loop answer path.
                # Keep it in the final-answer buffer so tail reconciliation can compare
                # against what was already emitted to the visitor.
                stream_state.mode = "final"
            # Seed transcript with the assistant message containing tool_calls.
            seed_turn: dict[str, object] = {
                "role": "assistant",
                "content": "",
                "tool_calls": first_stream_tool_calls,
            }
            if self._deepseek_reasoner_tool_loop_enabled():
                reasoning = assistant_message.get("reasoning_content")
                seed_turn["reasoning_content"] = reasoning if isinstance(reasoning, str) else ""
            transcript.append(seed_turn)
            pending_assistant = None

            seen_tool_signatures: set[str] = set()
            seen_phone_call_signatures: set[str] = set()
            duplicate_loop_streak = 0
            duplicate_loop_threshold = 2
            table_only_workflow = False

            for iteration_index in range(self.max_tool_iterations):
                current_tool_calls_raw = list(assistant_message.get("tool_calls") or [])
                current_tool_calls, portal_tool_calls = _split_portal_tool_calls(current_tool_calls_raw)
                if portal_tool_calls:
                    portal_block_stream.ingest_tool_calls(portal_tool_calls)
                if not current_tool_calls:
                    break
                email_send_requested = False
                for tool_call in current_tool_calls:
                    if not isinstance(tool_call, Mapping):
                        continue
                    try:
                        email_send_requested = self._tool_name(tool_call) == "email_send_draft"
                    except Exception:
                        email_send_requested = False
                    if email_send_requested:
                        break
                created_email_draft: dict[str, str] | None = None
                # Collect document IDs for deferred structure injection to avoid
                # breaking tool_call/response ordering (OpenAI requires all tool
                # responses to immediately follow their assistant message).
                deferred_structure_doc_ids: list[str] = []
                with TRACER.start_as_current_span("portal.mcp.tool_iteration") as iter_span:
                    if iter_span.is_recording():
                        iter_span.set_attribute("mcp.iteration_index", iteration_index)
                        iter_span.set_attribute("mcp.pending_tool_calls", len(current_tool_calls))
                        iter_span.set_attribute("mcp.transcript_length", len(transcript))
                    iteration_executed_tools: list[tuple[str, str]] = []
                    # Execute each tool_call and append tool results.
                    for tool_call in current_tool_calls:
                        prepared_tool_call = self._prepare_tool_call_for_execution(
                            tool_call=tool_call,
                            tool_context=tool_context,
                        )
                        tool_name = prepared_tool_call.tool_name
                        raw_arguments = prepared_tool_call.raw_arguments
                        arguments = prepared_tool_call.arguments
                        llm_requested_tool_name = prepared_tool_call.llm_requested_tool_name
                        llm_requested_arguments = prepared_tool_call.llm_requested_arguments
                        tool_call_id = prepared_tool_call.tool_call_id
                        tool_event_id = prepared_tool_call.tool_event_id
                        policy_tool_result = prepared_tool_call.policy_tool_result
                        ui_spinner_text = prepared_tool_call.ui_spinner_text

                        # Record the signature of the tool call after any hint injection so we can
                        # detect no-progress loops.
                        seen_tool_signatures.add(self._tool_signature(tool_name, arguments))

                        call_origin = "live"
                        call_duration_ms: float | None = None
                        cache_hit = False
                        with TRACER.start_as_current_span("portal.mcp.tool_call") as tool_span:
                            if tool_span.is_recording():
                                tool_span.set_attribute("mcp.tool_name", tool_name)
                                tool_span.set_attribute("mcp.iteration_index", iteration_index)
                                tool_span.set_attribute("mcp.tool_args_keys", sorted(arguments.keys()))
                            if policy_tool_result:
                                tool_result = policy_tool_result
                                call_origin = "policy"
                            else:
                                if tool_name == "read_knowledge":
                                    raw_refs = arguments.get("refs")
                                    if not isinstance(raw_refs, list):
                                        raw_refs = arguments.get("items")
                                    refs_out: list[str] = []
                                    if isinstance(raw_refs, list):
                                        for ref in raw_refs:
                                            if not isinstance(ref, Mapping):
                                                continue
                                            ref_id = str(ref.get("id") or ref.get("ref") or "").strip()
                                            if ref_id:
                                                refs_out.append(ref_id)
                                    structured_log(
                                        "mcp",
                                        "tool.read_knowledge.request",
                                        {
                                            "refs": refs_out[:10],
                                            "refs_count": len(refs_out),
                                            "mode": arguments.get("mode"),
                                            "max_chars": arguments.get("max_chars"),
                                        },
                                        context={
                                            "conversation": conversation.id,
                                            "business": conversation.business_profile_id,
                                        },
                                        logger_obj=logger,
                                    )
                                call_start: float | None = None
                                remote_event_id: str | None = None
                                remote_event_payload: dict[str, object] | None = None
                                internal_event_payload: dict[str, object] | None = None
                                try:
                                    if gateway_enabled and tool_name == "mcp_call_tool":
                                        gateway_tool_result = self._execute_gateway_mcp_call_tool(
                                            conversation=conversation,
                                            arguments=arguments,
                                            tool_context=tool_context,
                                            tool_call_id=tool_call_id,
                                            tool_event_id=tool_event_id,
                                            on_tool_event=on_tool_event,
                                            wait_for_tool_approval=wait_for_tool_approval,
                                        )
                                        tool_result = gateway_tool_result.tool_result
                                        call_origin = gateway_tool_result.call_origin
                                        call_start = gateway_tool_result.call_start
                                        remote_event_id = gateway_tool_result.remote_event_id
                                        remote_event_payload = gateway_tool_result.remote_event_payload
                                    else:
                                        remote_entry = self._remote_tool_registry.get(tool_name)
                                        if remote_entry:
                                            remote_tool_result = self._execute_registered_remote_tool(
                                                conversation=conversation,
                                                tool_context=tool_context,
                                                tool_name=tool_name,
                                                arguments=arguments,
                                                remote_entry=remote_entry,
                                                tool_call_id=tool_call_id,
                                                tool_event_id=tool_event_id,
                                                ui_spinner_text=ui_spinner_text,
                                                on_tool_event=on_tool_event,
                                                wait_for_tool_approval=wait_for_tool_approval,
                                            )
                                            tool_result = remote_tool_result.tool_result
                                            call_origin = remote_tool_result.call_origin
                                            call_start = remote_tool_result.call_start
                                            remote_event_id = remote_tool_result.remote_event_id
                                            remote_event_payload = remote_tool_result.remote_event_payload
                                        else:
                                            call_start = time.perf_counter()
                                            prepared_internal_tool = self._prepare_internal_tool_execution(
                                                tool_name=tool_name,
                                                arguments=arguments,
                                                tool_call_id=tool_call_id,
                                                tool_event_id=tool_event_id,
                                                ui_spinner_text=ui_spinner_text,
                                                on_tool_event=on_tool_event,
                                            )
                                            effective_arguments = prepared_internal_tool.effective_arguments
                                            internal_event_payload = prepared_internal_tool.internal_event_payload
                                            tool_result = None
                                            if tool_name in native_tool_names_all:
                                                native_policy_result = self._apply_native_tool_policy(
                                                    conversation=conversation,
                                                    tool_name=tool_name,
                                                    effective_arguments=effective_arguments,
                                                    tool_call_id=tool_call_id,
                                                    tool_event_id=tool_event_id,
                                                    on_tool_event=on_tool_event,
                                                    wait_for_tool_approval=wait_for_tool_approval,
                                                )
                                                tool_result = native_policy_result.tool_result
                                                call_origin = native_policy_result.call_origin
                                                effective_arguments = native_policy_result.effective_arguments

                                            if tool_result is None and tool_name == "email_send_draft":
                                                email_send_result = self._execute_email_send_draft_tool(
                                                    conversation=conversation,
                                                    tool_context=tool_context,
                                                    tool_name=tool_name,
                                                    effective_arguments=effective_arguments,
                                                    tool_call_id=tool_call_id,
                                                    tool_event_id=tool_event_id,
                                                    on_tool_event=on_tool_event,
                                                    wait_for_tool_approval=wait_for_tool_approval,
                                                )
                                                tool_result = email_send_result.tool_result
                                                call_origin = email_send_result.call_origin
                                                effective_arguments = email_send_result.effective_arguments
                                            elif tool_result is None and tool_name == "initiate_phone_call":
                                                phone_tool_result = self._execute_phone_call_tool(
                                                    conversation=conversation,
                                                    tool_context=tool_context,
                                                    tool_name=tool_name,
                                                    effective_arguments=effective_arguments,
                                                    tool_call_id=tool_call_id,
                                                    tool_event_id=tool_event_id,
                                                    seen_phone_call_signatures=seen_phone_call_signatures,
                                                    on_tool_event=on_tool_event,
                                                    wait_for_tool_approval=wait_for_tool_approval,
                                                )
                                                tool_result = phone_tool_result.tool_result
                                                call_origin = phone_tool_result.call_origin
                                            elif tool_result is None:
                                                internal_tool_result = self._execute_internal_tool(
                                                    conversation=conversation,
                                                    tool_name=tool_name,
                                                    tool_context=tool_context,
                                                    effective_arguments=effective_arguments,
                                                    tool_call_id=tool_call_id,
                                                    tool_event_id=tool_event_id,
                                                    apply_general_override=tool_name not in native_tool_names_all,
                                                    on_tool_event=on_tool_event,
                                                    wait_for_tool_approval=wait_for_tool_approval,
                                                )
                                                tool_result = internal_tool_result.tool_result
                                                call_origin = internal_tool_result.call_origin
                                                created_email_draft = internal_tool_result.created_email_draft
                                except ToolConstraintError as exc:
                                    structured_log(
                                        "mcp",
                                        "tool.constraint_violation",
                                        {
                                            "tool": tool_name,
                                            "error": str(exc),
                                        },
                                        indent=1,
                                        context={"conversation": conversation.id},
                                        logger_obj=logger,
                                        level=logging.WARNING,
                                    )
                                    tool_result = self._constraint_error_payload(tool_name, exc)
                                except McpRemoteError as exc:
                                    structured_log(
                                        "mcp",
                                        "tool.remote_error",
                                        {"tool": tool_name, "error": str(exc)},
                                        indent=1,
                                        context={"conversation": conversation.id, "business": conversation.business_profile_id},
                                        logger_obj=logger,
                                        level=logging.WARNING,
                                    )
                                    tool_result = {
                                        "tool": tool_name,
                                        "status": "error",
                                        "error_code": "mcp_remote_error",
                                        "error": str(exc),
                                        "hint": "Verify the MCP server URL and authentication, then test the connection.",
                                    }
                                except Exception as exc:  # pragma: no cover - defensive
                                    structured_log(
                                        "mcp",
                                        "tool.unhandled_error",
                                        {"tool": tool_name, "error": str(exc)},
                                        indent=1,
                                        context={"conversation": conversation.id, "business": conversation.business_profile_id},
                                        logger_obj=logger,
                                        level=logging.ERROR,
                                    )
                                    tool_result = {
                                        "tool": tool_name,
                                        "status": "error",
                                        "error_code": "tool_failed",
                                        "error": str(exc),
                                    }
                                finally:
                                    tool_finalization = self._finalize_tool_result_after_execution(
                                        conversation=conversation,
                                        tool_name=tool_name,
                                        tool_call_id=tool_call_id,
                                        tool_event_id=tool_event_id,
                                        tool_result=tool_result,
                                        call_start=call_start,
                                        remote_event_id=remote_event_id,
                                        remote_event_payload=remote_event_payload,
                                        internal_event_payload=internal_event_payload,
                                        on_tool_event=on_tool_event,
                                    )
                                    tool_result = tool_finalization.tool_result
                                    call_duration_ms = tool_finalization.call_duration_ms
                        if tool_name == "search_knowledge" and isinstance(tool_result, Mapping):
                            snippets = tool_result.get("snippets")
                            if isinstance(snippets, list) and snippets:
                                table_only_workflow = self._search_result_is_table(tool_result)

                        tool_result = self._append_tool_result_to_trace_and_transcript(
                            conversation=conversation,
                            tool_context=tool_context,
                            transcript=transcript,
                            iteration_executed_tools=iteration_executed_tools,
                            tool_name=tool_name,
                            tool_call_id=tool_call.get("id"),
                            tool_result=tool_result,
                            arguments=arguments,
                            llm_requested_tool_name=llm_requested_tool_name,
                            llm_requested_arguments=llm_requested_arguments,
                            call_duration_ms=call_duration_ms,
                            call_origin=call_origin,
                            cache_hit=cache_hit,
                        )

                        email_draft_gate_response = self._maybe_send_created_email_draft(
                            conversation=conversation,
                            tool_context=tool_context,
                            created_email_draft=created_email_draft,
                            email_send_requested=email_send_requested,
                            user_message=user_message,
                            stream_state=stream_state,
                            on_tool_event=on_tool_event,
                            wait_for_tool_approval=wait_for_tool_approval,
                            emit_final_answer=_emit_final_answer,
                            status_event=_status_event,
                        )
                        if email_draft_gate_response is not None:
                            return email_draft_gate_response

                    loop_messages = prompts.limit_messages_for_stage(transcript, stage="tool_iteration")
                    tools_for_iteration = self.tool_definitions
                    excluded_tools: set[str] = set()
                    if table_only_workflow:
                        excluded_tools.add("read_knowledge")
                    if self._search_budget_remaining(tool_context) == 0:
                        excluded_tools.add("search_knowledge")
                    if excluded_tools:
                        tools_for_iteration = self._exclude_tool_schemas(excluded_tools)
                    payload = self._chat_with_context_governor(
                        conversation=conversation,
                        stage="tool_iteration",
                        messages=loop_messages,
                        tools=tools_for_iteration,
                        on_stream_delta=_answer_stream_chunk if streaming_allowed else None,
                        on_tool_call_start=None,
                        on_tool_call_delta=_on_stream_tool_call_delta,
                        tool_context=tool_context,
                        on_reasoning_event=on_reasoning_event,
                        reasoning_label=f"Tool step {iteration_index + 1}",
                        should_cancel=should_cancel,
                    )
                    assistant_message = self._coerce_assistant_message(payload)
                    next_tool_calls_raw = list(assistant_message.get("tool_calls") or [])
                    next_tool_calls, portal_tool_calls = _split_portal_tool_calls(next_tool_calls_raw)
                    if portal_tool_calls:
                        portal_block_stream.ingest_tool_calls(portal_tool_calls)

                    next_signatures: list[str] = []
                    if next_tool_calls:
                        for next_call in next_tool_calls:
                            next_name = self._tool_name(next_call)
                            raw_next_args = self._tool_arguments(next_call)
                            next_args = dict(raw_next_args) if isinstance(raw_next_args, Mapping) else {}
                            next_args.pop("__ui", None)
                            next_args.pop("spinner_text", None)
                            next_signatures.append(self._tool_signature(next_name, next_args))

                        if next_signatures and all(sig in seen_tool_signatures for sig in next_signatures):
                            duplicate_loop_streak += 1
                        else:
                            duplicate_loop_streak = 0

                        force_final = duplicate_loop_streak >= duplicate_loop_threshold or (
                            iteration_index >= self.max_tool_iterations - 1
                        )
                        if force_final:
                            force_final_reason = (
                                "duplicate_signatures"
                                if duplicate_loop_streak >= duplicate_loop_threshold
                                else "iteration_limit"
                            )
                            force_final_next_tools = [self._tool_name(c) for c in next_tool_calls]
                            structured_log(
                                "mcp",
                                "tool.loop.force_final",
                                {
                                    "reason": force_final_reason,
                                    "next_tools": force_final_next_tools,
                                },
                                context={
                                    "conversation": conversation.id,
                                    "business": conversation.business_profile_id,
                                },
                                logger_obj=logger,
                                level=logging.WARNING,
                            )
                            tool_context.add_tool_trace(
                                {
                                    "tool": "__orchestrator__",
                                    "status": "forced_final",
                                    "reason": force_final_reason,
                                    "next_tools": force_final_next_tools,
                                }
                            )
                            forced_payload = self._chat_with_context_governor(
                                conversation=conversation,
                                stage="force_final",
                                messages=loop_messages,
                                tools=portal_only_tools if portal_only_tools and on_block_event else None,
                                on_stream_delta=_answer_stream_chunk if streaming_allowed else None,
                                on_tool_call_delta=_on_stream_tool_call_delta,
                                tool_context=tool_context,
                            )
                            assistant_message = self._coerce_assistant_message(forced_payload)
                            forced_tool_calls_raw = list(assistant_message.get("tool_calls") or [])
                            _, portal_tool_calls = _split_portal_tool_calls(forced_tool_calls_raw)
                            if portal_tool_calls:
                                portal_block_stream.ingest_tool_calls(portal_tool_calls)
                            next_tool_calls = []
                            _mark_answer_started()
                            transcript.append(
                                {
                                    "role": "assistant",
                                    "content": assistant_message.get("content"),
                                }
                            )
                            tool_phase_assistant_message = assistant_message
                            raw_content = assistant_message.get("content")
                            if isinstance(raw_content, str) and raw_content.strip():
                                single_pass_candidate = raw_content.strip()
                            break

                    if not next_tool_calls:
                        _mark_answer_started()
                    # Append the assistant turn (empty content if tools present).
                    assistant_turn: dict[str, object] = {
                        "role": "assistant",
                        "content": assistant_message.get("content") if not next_tool_calls else "",
                        **({"tool_calls": next_tool_calls} if next_tool_calls else {}),
                    }
                    if self._deepseek_reasoner_tool_loop_enabled():
                        reasoning = assistant_message.get("reasoning_content")
                        assistant_turn["reasoning_content"] = reasoning if isinstance(reasoning, str) else ""
                    transcript.append(assistant_turn)
                    if iter_span.is_recording():
                        iter_span.set_attribute("mcp.next_tool_calls", len(next_tool_calls))
                        iter_span.set_attribute("mcp.cache_hits", len(getattr(tool_context, "knowledge_results", ())))
                        iter_span.set_attribute("mcp.message_count", len(loop_messages))
                if not next_tool_calls:
                    tool_phase_assistant_message = assistant_message
                    raw_content = assistant_message.get("content")
                    if isinstance(raw_content, str) and raw_content.strip():
                        single_pass_candidate = raw_content.strip()
                    break
                first_stream_tool_calls = next_tool_calls
            else:
                raise RuntimeError("MCP tool loop exceeded iteration limit.")
        # No tool calls from the first streaming pass: take single-pass fast path.
        else:
            tool_phase_assistant_message = first_stream_message
            _flush_stream_buffer(
                "streaming_tools",
                filter_override=initial_stream_filter_level,
                flush_remainder=False,
            )
            # Canonical assistant content must come from the provider payload, not
            # streamed deltas. Streamed deltas can be truncated at boundaries.
            single_pass_text = first_content_raw or "".join(stream_state.first_pass_chunks).strip()
            _mark_answer_started()
            with TRACER.start_as_current_span("portal.mcp.single_pass") as span:
                if span.is_recording():
                    span.set_attribute("mcp.streamed_chars", len(single_pass_text))
                clean_single, dropped_single = sanitize_with_diagnostics(
                    single_pass_text,
                    conversation=conversation,
                    stage="single_pass_stream",
                    filter_level=filter_level,
                )
            if not clean_single:
                clean_single = single_pass_text
            if verification_blocks_streaming:
                clean_single = self._apply_verification_override(
                    conversation=conversation,
                    user_message=user_message,
                    draft_answer=clean_single,
                    tool_context=tool_context,
                )

            structured_log(
                "mcp",
                "turn.single_pass",
                {
                    "strategy": "mcp_tools_stream_single_pass",
                    "content_chars": len(clean_single),
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
            )
            _status_event("answer_finalized", "Answer ready")
            self._log_turn_metrics(conversation, tool_context)
            normalized_assistant = dict(tool_phase_assistant_message or {"role": "assistant"})
            normalized_assistant["content"] = clean_single
            stream_state.mode = "final"
            if streaming_allowed:
                stream_state.answer_chunks[:] = list(stream_state.first_pass_chunks)
                _log_stream_mismatch(stream_state.answer_chunks, clean_single, stage="single_pass")
            else:
                stream_state.answer_chunks.clear()
                _emit_final_answer(clean_single)
            _status_event("stream_complete", "")
            response_blocks = self._extract_response_blocks(normalized_assistant)
            clean_single = str(normalized_assistant.get("content") or clean_single)
            return {
                "assistant_message": normalized_assistant,
                "tool_context": tool_context,
                "streamed_chunks": tuple(stream_state.answer_chunks),
                "clean_answer_text": clean_single,
                "dropped_sentences": tuple(dropped_single),
                "llm_strategy": "mcp_tools_stream_single_pass",
                "response_blocks": response_blocks,
            }

        single_pass_detected = bool(single_pass_candidate and (not tool_phase_assistant_message or not tool_phase_assistant_message.get("tool_calls")))
        if single_pass_detected:
            structured_log(
                "mcp",
                "turn.single_pass_candidate",
                {"content_chars": len(single_pass_candidate)},
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )

        final_assistant_message = tool_phase_assistant_message or {"role": "assistant"}
        _flush_stream_buffer("streaming_answer", flush_remainder=False)
        answer_text_raw = ""
        if isinstance(final_assistant_message, Mapping):
            answer_text_raw = str(final_assistant_message.get("content") or "").strip()
        else:
            final_assistant_message = {"role": "assistant", "content": ""}
        if not answer_text_raw and stream_state.answer_chunks:
            answer_text_raw = "".join(stream_state.answer_chunks).strip()

        _mark_answer_started()
        _status_event("answer_finalized", "Answer ready")

        self._log_unfulfilled_read_required(
            conversation=conversation,
            tool_context=tool_context,
        )

        clean_answer_text, dropped_sentences = sanitize_with_diagnostics(
            answer_text_raw,
            conversation=conversation,
            stage="tool_loop_final",
            filter_level=filter_level,
        )
        if not clean_answer_text and answer_text_raw:
            clean_answer_text = answer_text_raw.strip()
        if not clean_answer_text and stream_state.answer_chunks:
            clean_answer_text = "".join(stream_state.answer_chunks).strip()
        if verification_blocks_streaming:
            clean_answer_text = self._apply_verification_override(
                conversation=conversation,
                user_message=user_message,
                draft_answer=clean_answer_text,
                tool_context=tool_context,
            )
        all_dropped = stream_state.dropped + dropped_sentences
        normalized_assistant_msg = dict(final_assistant_message or {})
        normalized_assistant_msg["content"] = clean_answer_text
        response_blocks = self._extract_response_blocks(normalized_assistant_msg)
        clean_answer_text = str(normalized_assistant_msg.get("content") or clean_answer_text)
        if streaming_allowed:
            _log_stream_mismatch(stream_state.answer_chunks, clean_answer_text, stage="tool_loop_final")
        else:
            stream_state.answer_chunks.clear()
            _emit_final_answer(clean_answer_text)
        _status_event("stream_complete", "")

        self._log_turn_metrics(conversation, tool_context)
        self._persist_seen_items(conversation, tool_context)
        return {
            "assistant_message": normalized_assistant_msg,
            "tool_context": tool_context,
            "streamed_chunks": tuple(stream_state.answer_chunks),
            "clean_answer_text": clean_answer_text,
            "dropped_sentences": tuple(all_dropped),
            "llm_strategy": "mcp_tools_stream_loop",
            "response_blocks": response_blocks,
        }
