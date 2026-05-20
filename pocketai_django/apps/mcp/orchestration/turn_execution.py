from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
import uuid
from typing import Callable, Mapping, Sequence

from django.conf import settings
from django.utils import timezone

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import McpConnectionApprovalMode, McpToolOperationType
from apps.conversations.models import Conversation, ConversationMessage
from apps.rag.rag_logging import structured_log

from core.otel import otel_trace

from .. import prompts, tools as mcp_tools
from ..runtime.budget_guidance import search_budget_exceeded_payload
from ..connectors import (
    get_tool_approval_requirement,
    list_enabled_mcp_connections_for_agent,
    list_remote_tool_descriptors,
)
from ..runtime.portal_block_stream import PORTAL_BLOCK_TOOL_NAME, _PortalBlockStream
from ..runtime.rag_observability import compact_retrieval_observability
from ..text.redaction import redact_tool_input_payload
from ..remote_client import McpRemoteError
from ..runtime.tool_artifacts import build_prompt_view_for_remote_tool_result, store_remote_tool_output_artifact
from ..text.sanitizer import sanitize_text, sanitize_with_diagnostics
from ..types import ToolConstraintError, ToolExecutionContext
from .response_helpers import INLINE_RESPONSE_BLOCK_PATTERN
from .turn_answer_emit import _emit_final_answer as _emit_final_answer_impl
from .turn_callbacks import (
    _emit_tokens as _emit_tokens_impl,
)
from .turn_tool_ui import (
    _split_portal_tool_calls as _split_portal_tool_calls_impl,
    _tool_spinner_text as _tool_spinner_text_impl,
)
from .turn_stream_filters import _filter_dsml_stream as _filter_dsml_stream_impl
from .turn_stream_logging import _log_stream_mismatch as _log_stream_mismatch_impl
from .turn_stream_buffer import _flush_stream_buffer as _flush_stream_buffer_impl
from .turn_portal_stream import _on_stream_tool_call_delta as _on_stream_tool_call_delta_impl
from .turn_stream_handlers import (
    _answer_stream_chunk as _answer_stream_chunk_impl,
    _first_stream_chunk as _first_stream_chunk_impl,
)
from .turn_status import _status_event as _status_event_impl


TRACER = otel_trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

class McpTurnExecutionMixin:

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

        with TRACER.start_as_current_span("portal.mcp.turn_setup") as setup_span:
            if setup_span.is_recording():
                setup_span.set_attribute("conversation.id", str(conversation.id))
                setup_span.set_attribute("business.id", str(conversation.business_profile_id))
            # Pre-fetch MCP connections once (reused later for tool catalog).
            all_remote_connections = list_enabled_mcp_connections_for_agent(self.agent)
            has_mcp_connections = bool(all_remote_connections)
            model_id = getattr(self.provider, "model", None) if self.provider else None
            messages = prompts.build_messages(
                conversation=conversation,
                user_message=user_message,
                model_id=model_id,
                has_mcp_connections=has_mcp_connections,
            )
            filter_level = self._filter_level_for_conversation(conversation)
            initial_stream_filter_level = filter_level
            char_turn_limit = self._char_budget_per_turn(conversation.business_profile)
            char_minute_limit = self._char_budget_per_minute(conversation.business_profile)
            minute_reserver = self._build_minute_budget_reserver(conversation.business_profile, char_minute_limit)
            tool_context = ToolExecutionContext(
                max_chunk_reads_per_turn=self.max_chunk_reads_per_turn,
                max_chunk_pages_per_turn=self.max_chunk_pages_per_turn,
                char_budget_per_turn=char_turn_limit,
                char_budget_per_minute=char_minute_limit,
                minute_budget_reserver=minute_reserver,
                latest_user_message=user_message,
            )
            # Load seen items from previous turns (for "are there more?" follow-ups)
            self._hydrate_seen_items(conversation, tool_context)

        feature_state = FeatureFlagService.snapshot(conversation.business_profile)
        new_contract_enabled = bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
        rag_agentic_enabled = bool(getattr(feature_state, "rag_agentic_mode", False)) and new_contract_enabled

        disable_tools_for_turn = self._is_low_intent_message(user_message)

        normalized_tool_allowlist: set[str] | None = None
        if allowed_tools is not None:
            normalized_tool_allowlist = {str(value).strip() for value in allowed_tools if str(value or "").strip()}

        native_registry = mcp_tools.get_native_integration_tool_registry()
        native_tool_names_all = set(native_registry.keys())
        available_native_tool_names = self._available_native_integration_tool_names(
            conversation=conversation,
            registry=native_registry,
        )
        email_registry = mcp_tools.EMAIL_INTEGRATION_TOOL_REGISTRY
        email_tool_names_all = mcp_tools.get_email_integration_tool_names()
        available_email_tool_names = self._available_email_integration_tool_names(
            conversation=conversation,
            registry=email_registry,
        )

        internal_tool_defs: list[Mapping[str, object]] = list(mcp_tools.get_tool_definitions())
        # Gateway tools are exposed only when at least one MCP connection is enabled.
        gateway_enabled = bool(all_remote_connections)
        if gateway_enabled:
            internal_tool_defs.extend(mcp_tools.GATEWAY_TOOL_DEFINITIONS)
        if not portal_emit_blocks_enabled:
            internal_tool_defs = [
                tool_def
                for tool_def in internal_tool_defs
                if self._tool_schema_name(tool_def) != PORTAL_BLOCK_TOOL_NAME
            ]
        enable_user_input_tool = not wait_for_tool_approval
        convo_meta = getattr(conversation, "metadata", None)
        convo_meta_map = convo_meta if isinstance(convo_meta, Mapping) else {}
        convo_source = str(convo_meta_map.get("source") or "").strip().lower()
        is_agent_run_conversation = convo_source == "agent_run"
        if is_agent_run_conversation:
            enable_user_input_tool = True
        if not enable_user_input_tool:
            internal_tool_defs = [
                tool_def
                for tool_def in internal_tool_defs
                if self._tool_schema_name(tool_def) != "request_user_input"
            ]
        if rag_agentic_enabled:
            allowed = {
                "search_knowledge",
                "read_knowledge",
                "search_conversation_files",
                "read_conversation_file",
                "pdf_generate",
                "pdf_merge",
                "pdf_extract_pages",
                "pdf_extract_text",
                "request_user_input",
                "start_agent_run",
                "list_agent_runs",
                "get_agent_run",
                "continue_agent_run",
                "list_tasks",
                "draft_task",
                "update_task",
                "request_task_activation",
                "pause_task",
                "search_memory",
                "save_memory",
                "forget_memory",
                PORTAL_BLOCK_TOOL_NAME,
                "initiate_phone_call",
            }
            if gateway_enabled:
                allowed.update({"mcp_search_tools", "mcp_call_tool"})
            allowed.update(available_native_tool_names)
            allowed.update(available_email_tool_names)
            internal_tool_defs = [
                tool_def for tool_def in internal_tool_defs if self._tool_schema_name(tool_def) in allowed
            ]

        # Native integration tools are exposed only when connected for the current actor + tenant.
        internal_tool_defs = [
            tool_def
            for tool_def in internal_tool_defs
            if (
                self._tool_schema_name(tool_def) not in native_tool_names_all
                or self._tool_schema_name(tool_def) in available_native_tool_names
            )
        ]
        internal_tool_defs = [
            tool_def
            for tool_def in internal_tool_defs
            if (
                self._tool_schema_name(tool_def) not in email_tool_names_all
                or self._tool_schema_name(tool_def) in available_email_tool_names
            )
        ]

        if normalized_tool_allowlist is not None:
            internal_tool_defs = [
                tool_def for tool_def in internal_tool_defs if self._tool_schema_name(tool_def) in normalized_tool_allowlist
            ]

        # Background runs must not be able to spawn more background runs.
        if is_agent_run_conversation:
            forbidden = {
                "start_agent_run",
                "continue_agent_run",
                "list_agent_runs",
                "get_agent_run",
                "draft_task",
                "update_task",
                "request_task_activation",
                "pause_task",
            }
            if normalized_tool_allowlist is not None:
                normalized_tool_allowlist -= forbidden
            internal_tool_defs = [
                tool_def for tool_def in internal_tool_defs if self._tool_schema_name(tool_def) not in forbidden
            ]

        # External MCP connections (per-agent) extend the tool catalog.
        # all_remote_connections was pre-fetched in turn_setup above.
        remote_tool_defs: list[dict[str, Any]] = []
        self._remote_tool_registry = {}
        if not disable_tools_for_turn:
            remote_descriptors = list_remote_tool_descriptors(all_remote_connections)
            if normalized_tool_allowlist is not None:
                remote_descriptors = [desc for desc in remote_descriptors if desc.safe_name in normalized_tool_allowlist]
            gateway_catalog: dict[str, dict[str, object]] = {}
            remote_registry: dict[str, tuple[object, str]] = {}
            default_arg_keys_by_connection: dict[str, list[str]] = {}
            for desc in remote_descriptors:
                tool_id = desc.safe_name
                remote_registry[tool_id] = (desc.connection, desc.remote_name)
                connection_id = str(getattr(desc.connection, "id", "") or "")
                default_arg_keys = default_arg_keys_by_connection.get(connection_id)
                if default_arg_keys is None:
                    creds = getattr(desc.connection, "credentials", None) or {}
                    setup_fields = creds.get("setup_fields") if isinstance(creds, Mapping) else None
                    if isinstance(setup_fields, Mapping):
                        default_arg_keys = sorted(
                            {
                                str(key).strip()
                                for key, value in setup_fields.items()
                                if str(key).strip() and isinstance(value, str) and value.strip()
                            }
                        )
                    else:
                        default_arg_keys = []
                    default_arg_keys_by_connection[connection_id] = default_arg_keys
                gateway_catalog[tool_id] = {
                    "connection_id": connection_id,
                    "connection_name": str(getattr(desc.connection, "name", "") or ""),
                    "remote_tool": desc.remote_name,
                    "description": desc.description,
                    "input_schema": dict(desc.input_schema) if isinstance(desc.input_schema, Mapping) else None,
                    "default_arg_keys": list(default_arg_keys),
                }
            tool_context.mcp_gateway_catalog = gateway_catalog
            self._remote_tool_registry = remote_registry

        if remote_tool_defs:
            self.tool_definitions = tuple([*internal_tool_defs, *remote_tool_defs])
        else:
            self.tool_definitions = tuple(internal_tool_defs)

        if not self.provider:
            raise RuntimeError("MCP provider is not configured.")

        preplan_enabled = (not disable_tools_for_turn) and self._preplan_enabled_for_business(conversation.business_profile)
        verification_enabled = self._verification_enabled_for_business(conversation.business_profile)
        verification_blocks_streaming = verification_enabled and self._verification_blocks_streaming_for_business(
            conversation.business_profile
        )
        streaming_allowed = not verification_blocks_streaming

        transcript = list(messages)
        tool_phase_assistant_message: dict[str, object] | None = None
        final_assistant_message: dict[str, object] | None = None
        first_pass_streamed_chunks: list[str] = []
        answer_streamed_chunks: list[str] = []
        streaming_mode = "initial"
        single_pass_candidate: str | None = None
        first_stream_tool_calls: list[Mapping[str, object]] = []
        first_stream_message: dict[str, object] | None = None
        stream_buffer = ""
        stream_dropped: list[str] = []
        initial_stream_started = False
        final_answer_started = False
        inline_response_blocks_detected = False
        dsml_skip_line = False
        preplan_payload: dict[str, object] | None = None
        provider_name = (os.getenv("MCP_PROVIDER") or "").strip().lower()
        tool_definitions_for_model = self.tool_definitions
        if provider_name == "deepseek":
            # DeepSeek reliably streams plain text + tool calls, but tool-call-based JSON
            # deltas (like portal_emit_blocks) are more fragile. Prefer server-parsed
            # rich blocks for the portal UI to avoid content loss.
            tool_definitions_for_model = tuple(
                tool_def
                for tool_def in tool_definitions_for_model
                if self._tool_schema_name(tool_def) != PORTAL_BLOCK_TOOL_NAME
            )
            # Ensure subsequent helper methods (like _exclude_tool_schemas) operate on
            # the same filtered tool list for the rest of this turn.
            self.tool_definitions = tool_definitions_for_model

        portal_only_tools = [
            tool_def for tool_def in tool_definitions_for_model if self._tool_schema_name(tool_def) == PORTAL_BLOCK_TOOL_NAME
        ]
        if not portal_only_tools:
            portal_only_tools = []
        initial_tools: Iterable[Mapping[str, object]] | None = None if disable_tools_for_turn else tool_definitions_for_model
        if disable_tools_for_turn and portal_only_tools and on_block_event:
            initial_tools = portal_only_tools

        if preplan_enabled:
            recent_history = [
                entry for entry in transcript if entry.get("role") in {"user", "assistant"}
            ]
            preplan_message = self._run_preplan(
                conversation=conversation,
                user_message=user_message,
                recent_history=recent_history[-6:],
                tool_context=tool_context,
            )
            if preplan_message:
                preplan_payload = self._parse_preplan_payload(preplan_message)
            if preplan_payload:
                tool_context.preplan = dict(preplan_payload)
                route = str(preplan_payload.get("route") or "").strip()
                search_query = str(preplan_payload.get("search_query") or "").strip()
                planned_tools = preplan_payload.get("tools") or []
                tool_list = [t for t in planned_tools if isinstance(t, str) and t.strip()]
                if set(tool_list) == {"search_knowledge"}:
                    initial_tools = self._include_tool_schemas({"search_knowledge"})



        def _status_event(code: str, label: str | None = None, meta: Mapping[str, object] | None = None) -> None:
            _status_event_impl(
                code,
                on_status_change=on_status_change,
                label=label,
                meta=meta,
            )

        def _mark_answer_started(label: str | None = "Responding…") -> None:
            nonlocal final_answer_started
            if final_answer_started:
                _status_event("responding", label)
                return
            final_answer_started = True
            _status_event("answer_started", label)
            _status_event("responding", label)

        portal_block_stream = _PortalBlockStream(on_block_event)

        def _split_portal_tool_calls(
            tool_calls: Sequence[Mapping[str, object]],
        ) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
            return _split_portal_tool_calls_impl(tool_calls, tool_name_for_call=self._tool_name)

        def _tool_spinner_text(arguments: Mapping[str, object] | None) -> str:
            return _tool_spinner_text_impl(arguments, clip_text=self._clip_text)

        def _on_stream_tool_call_delta(tool_call: Mapping[str, object] | None) -> None:
            _on_stream_tool_call_delta_impl(tool_call, portal_block_stream=portal_block_stream)

        _status_event("thinking", "Thinking…")

        def _emit_tokens(text: str) -> None:
            _emit_tokens_impl(
                text,
                streaming_mode=streaming_mode,
                first_pass_streamed_chunks=first_pass_streamed_chunks,
                answer_streamed_chunks=answer_streamed_chunks,
                on_response_text_delta=on_response_text_delta,
                logger_obj=logger,
            )

        def _emit_final_answer(text: str) -> None:
            nonlocal streaming_mode
            streaming_mode = _emit_final_answer_impl(
                text,
                mark_answer_started=_mark_answer_started,
                answer_streamed_chunks=answer_streamed_chunks,
                on_response_text_delta=on_response_text_delta,
                logger_obj=logger,
            )

        def _flush_stream_buffer(
            stage: str,
            *,
            filter_override: str | None = None,
            flush_remainder: bool = False,
        ) -> None:
            nonlocal stream_buffer
            stream_buffer = _flush_stream_buffer_impl(
                stream_buffer,
                conversation=conversation,
                default_filter_level=filter_level,
                stage=stage,
                stream_dropped=stream_dropped,
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
            nonlocal dsml_skip_line, inline_response_blocks_detected, initial_stream_started
            dsml_skip_line, inline_response_blocks_detected, initial_stream_started = _first_stream_chunk_impl(
                chunk,
                dsml_skip_line=dsml_skip_line,
                inline_response_blocks_detected=inline_response_blocks_detected,
                initial_stream_started=initial_stream_started,
                filter_dsml_stream=lambda value, skip_line: _filter_dsml_stream_impl(value, skip_line=skip_line),
                status_event=_status_event,
                emit_tokens=_emit_tokens,
            )

        def _answer_stream_chunk(chunk: str) -> None:
            nonlocal stream_buffer, dsml_skip_line, inline_response_blocks_detected
            stream_buffer, dsml_skip_line, inline_response_blocks_detected = _answer_stream_chunk_impl(
                chunk,
                stream_buffer=stream_buffer,
                dsml_skip_line=dsml_skip_line,
                inline_response_blocks_detected=inline_response_blocks_detected,
                final_answer_started=final_answer_started,
                filter_dsml_stream=lambda value, skip_line: _filter_dsml_stream_impl(value, skip_line=skip_line),
                mark_answer_started=_mark_answer_started,
                emit_tokens=_emit_tokens,
                response_block_pattern=INLINE_RESPONSE_BLOCK_PATTERN,
            )

        first_stream_message: dict[str, object]
        first_stream_tool_calls: list[Mapping[str, object]]

        # Limit the initial payload so the provider only sees the guardrails and
        # the latest transcript entries needed for intent selection.
        primary_messages = prompts.limit_messages_for_stage(transcript, stage="initial_pass")
        self._log_prompt("primary", conversation=conversation, messages=primary_messages)
        with TRACER.start_as_current_span("portal.mcp.initial_pass") as initial_span:
            if initial_span.is_recording():
                initial_span.set_attribute("mcp.message_count", len(primary_messages))
                initial_span.set_attribute("mcp.tools_enabled", bool(initial_tools))
            first_payload = self._chat_with_context_governor(
                conversation=conversation,
                stage="initial_pass",
                messages=primary_messages,
                tools=initial_tools,
                on_stream_delta=_first_stream_chunk if streaming_allowed else None,
                on_tool_call_start=None,
                on_tool_call_delta=_on_stream_tool_call_delta,
                tool_context=tool_context,
                on_reasoning_event=on_reasoning_event,
                reasoning_label="Initial pass",
                should_cancel=should_cancel,
            )
        first_message = self._coerce_assistant_message(first_payload)
        first_stream_message = dict(first_message or {})
        first_stream_tool_calls_raw = list(first_stream_message.get("tool_calls") or [])
        first_stream_tool_calls, portal_tool_calls = _split_portal_tool_calls(first_stream_tool_calls_raw)
        if portal_tool_calls:
            portal_block_stream.ingest_tool_calls(portal_tool_calls)
        if on_tool_decision:
            try:
                on_tool_decision("used" if first_stream_tool_calls else "no_tools")
            except Exception:  # pragma: no cover - defensive
                logger.exception("on_tool_decision callback failed")
        first_content_raw = ""
        if first_stream_message:
            first_content_raw = str(first_stream_message.get("content") or "").strip()
        pending_assistant = None
        if first_stream_tool_calls:
            # Defer appending until we process the tool call in the loop; initial
            # stream chunks stay buffered alongside the first pass message.
            pending_assistant = first_stream_message
        else:
            # No tools; keep streamed assistant content in transcript.
            transcript.append(
                {
                    "role": "assistant",
                    "content": first_content_raw,
                }
            )

        # If we got tool calls, execute them and allow an iterative tool loop
        # (including additional model turns with tools) before the final-answer pass.
        if first_stream_tool_calls:
            assistant_message = pending_assistant or {}
            if streaming_allowed:
                # All subsequent streamed content belongs to the tool-loop answer path.
                # Keep it in the final-answer buffer so tail reconciliation can compare
                # against what was already emitted to the visitor.
                streaming_mode = "final"
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
                        tool_name = self._tool_name(tool_call)
                        raw_arguments = self._tool_arguments(tool_call)
                        arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
                        llm_requested_tool_name = str(tool_name)
                        llm_requested_arguments = (
                            copy.deepcopy(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
                        )
                        # Strip UI-only hints from tool arguments so they never leak into tool execution.
                        # Spinner UX is driven by backend status/tool events (single source of truth).
                        arguments.pop("__ui", None)
                        arguments.pop("spinner_text", None)
                        tool_call_id = str(tool_call.get("id") or "").strip()
                        tool_event_id = tool_call_id or str(uuid.uuid4())

                        # Cross-turn continuity: if the model invents non-UUID ref IDs on
                        # follow-up read_knowledge turns, repair from persisted search refs.
                        if tool_name == "read_knowledge":
                            arguments = self._repair_read_knowledge_refs_from_context(arguments, tool_context)

                        policy_tool_result = None
                        missing_fields = self._missing_required_fields(tool_name, arguments)
                        if missing_fields:
                            policy_tool_result = self._missing_required_payload(tool_name, missing_fields)

                        if tool_name == "search_knowledge" and not policy_tool_result:
                            remaining_searches = self._search_budget_remaining(tool_context)
                            if remaining_searches == 0:
                                policy_tool_result = search_budget_exceeded_payload(
                                    tool_context,
                                    reason="orchestrator_policy",
                                )

                        ui_spinner_text = _tool_spinner_text(raw_arguments)

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
                                        requested_tool_id = str(arguments.get("tool_id") or "").strip()
                                        raw_inner_args = arguments.get("arguments")
                                        inner_args = raw_inner_args if isinstance(raw_inner_args, Mapping) else None
                                        if inner_args is not None:
                                            # Guard reserved UI keys from leaking into remote MCP arguments.
                                            inner_args = dict(inner_args)
                                            inner_args.pop("__ui", None)
                                            inner_args.pop("spinner_text", None)
                                        if not requested_tool_id or inner_args is None:
                                            call_origin = "validation"
                                            tool_result = {
                                                "tool": "mcp_call_tool",
                                                "status": "error",
                                                "error": "invalid_arguments",
                                                "error_code": "invalid_arguments",
                                                "output": None,
                                                "hint": "Provide tool_id and arguments (object) from mcp_search_tools results.",
                                            }
                                        else:
                                            remote_entry = self._remote_tool_registry.get(requested_tool_id)
                                            catalog_entry = (
                                                tool_context.mcp_gateway_catalog.get(requested_tool_id)
                                                if isinstance(getattr(tool_context, "mcp_gateway_catalog", None), Mapping)
                                                else None
                                            )
                                            input_schema = (
                                                catalog_entry.get("input_schema")
                                                if isinstance(catalog_entry, Mapping) and isinstance(catalog_entry.get("input_schema"), Mapping)
                                                else None
                                            )
                                            effective_inner_args = inner_args
                                            defaults_applied: list[str] = []
                                            if remote_entry:
                                                try:
                                                    effective_inner_args, defaults_applied = self._apply_mcp_setup_defaults(
                                                        inner_args,
                                                        connection=remote_entry[0],
                                                        input_schema=input_schema,
                                                    )
                                                except Exception:  # pragma: no cover - defensive
                                                    effective_inner_args = inner_args
                                                    defaults_applied = []
                                            missing_fields, type_errors = self._validate_gateway_tool_arguments(
                                                effective_inner_args, input_schema
                                            )
                                            if not remote_entry:
                                                call_origin = "validation"
                                                tool_result = {
                                                    "tool": "mcp_call_tool",
                                                    "status": "error",
                                                    "error": "unknown_tool_id",
                                                    "error_code": "unknown_tool_id",
                                                    "tool_id": requested_tool_id,
                                                    "output": None,
                                                    "hint": "Call mcp_search_tools to get a valid tool_id for this agent.",
                                                }
                                            elif missing_fields or type_errors:
                                                call_origin = "validation"
                                                remote_meta = None
                                                if isinstance(catalog_entry, Mapping):
                                                    remote_meta = {
                                                        "connection_id": str(catalog_entry.get("connection_id") or "").strip() or None,
                                                        "connection_name": str(catalog_entry.get("connection_name") or "").strip() or None,
                                                        "tool": str(catalog_entry.get("remote_tool") or "").strip() or None,
                                                    }
                                                tool_result = {
                                                    "tool": "mcp_call_tool",
                                                    "status": "error",
                                                    "error": "validation_failed",
                                                    "error_code": "validation_failed",
                                                    "tool_id": requested_tool_id,
                                                    "missing_fields": missing_fields,
                                                    "type_errors": type_errors,
                                                    "output": None,
                                                    **({"remote": remote_meta} if remote_meta else {}),
                                                    "hint": "Fix the tool arguments and retry mcp_call_tool. Use mcp_search_tools results[].required_args as a guide.",
                                                }
                                            else:
                                                connection, remote_tool_name = remote_entry
                                                tool_name_for_remote = requested_tool_id
                                                approval_requirement = get_tool_approval_requirement(
                                                    connection,
                                                    remote_tool_name,
                                                    agent=getattr(conversation, "agent_profile", None),
                                                )
                                                skip_remote_execution = False
                                                if approval_requirement.get("requires_approval"):
                                                    approved, _, approval_result = self._maybe_request_tool_approval(
                                                        conversation=conversation,
                                                        connection=connection,
                                                        tool_name=tool_name_for_remote,
                                                        remote_tool_name=remote_tool_name,
                                                        tool_call_id=tool_call_id,
                                                        tool_event_id=tool_event_id,
                                                        arguments=inner_args,
                                                        approval_requirement=approval_requirement,
                                                        on_tool_event=on_tool_event,
                                                        wait_for_approval=wait_for_tool_approval,
                                                    )
                                                    if not approved:
                                                        tool_result = approval_result
                                                        call_origin = "policy"
                                                        skip_remote_execution = True
                                                if not skip_remote_execution:
                                                    call_start = time.perf_counter()
                                                    remote_event_id = tool_event_id
                                                    sensitive_keys = self._mcp_setup_fields_for_connection(connection).keys()
                                                    redacted_input = redact_tool_input_payload(inner_args, sensitive_keys=sensitive_keys)
                                                    if not isinstance(redacted_input, Mapping):
                                                        redacted_input = {}
                                                    remote_event_payload = {
                                                        "event_id": remote_event_id,
                                                        "phase": "started",
                                                        "status": "running",
                                                        "tool_call_id": tool_call_id,
                                                        "tool_name": tool_name_for_remote,
                                                        "kind": "mcp_remote",
                                                        "remote": {
                                                            "connection_id": str(getattr(connection, "id", "") or ""),
                                                            "connection_name": str(getattr(connection, "name", "") or ""),
                                                            "endpoint_url": str(getattr(connection, "server_url", "") or ""),
                                                            "remote_tool": remote_tool_name,
                                                        },
                                                        "input": dict(redacted_input),
                                                    }
                                                    if defaults_applied:
                                                        remote_event_payload["defaults_applied"] = list(defaults_applied)
                                                    if on_tool_event:
                                                        try:
                                                            on_tool_event(remote_event_payload)
                                                        except Exception:  # pragma: no cover - UI callback must not break tools
                                                            logger.exception("mcp portal tool event start callback failed")
                                                    tool_result = self._execute_remote_mcp_tool(
                                                        tool_name=tool_name_for_remote,
                                                        remote_tool_name=remote_tool_name,
                                                        connection=connection,
                                                        arguments=effective_inner_args,
                                                        conversation=conversation,
                                                        idempotency_key=self._mcp_idempotency_key(
                                                            conversation_id=conversation.id,
                                                            event_id=tool_event_id,
                                                        ),
                                                        operation_type=str(approval_requirement.get("operation_type") or ""),
                                                    )
                                    else:
                                        remote_entry = self._remote_tool_registry.get(tool_name)
                                        skip_remote_execution = False
                                        if remote_entry:
                                            connection, remote_tool_name = remote_entry
                                            approval_requirement = get_tool_approval_requirement(
                                                connection,
                                                remote_tool_name,
                                                agent=getattr(conversation, "agent_profile", None),
                                            )
                                            if approval_requirement.get("requires_approval"):
                                                approved, _, approval_result = self._maybe_request_tool_approval(
                                                    conversation=conversation,
                                                    connection=connection,
                                                    tool_name=tool_name,
                                                    remote_tool_name=remote_tool_name,
                                                    tool_call_id=tool_call_id,
                                                    tool_event_id=tool_event_id,
                                                    arguments=arguments,
                                                    approval_requirement=approval_requirement,
                                                    on_tool_event=on_tool_event,
                                                    wait_for_approval=wait_for_tool_approval,
                                                )
                                                if not approved:
                                                    tool_result = approval_result
                                                    call_origin = "policy"
                                                    skip_remote_execution = True
                                            if not skip_remote_execution:
                                                call_start = time.perf_counter()
                                                catalog_entry = (
                                                    tool_context.mcp_gateway_catalog.get(tool_name)
                                                    if isinstance(getattr(tool_context, "mcp_gateway_catalog", None), Mapping)
                                                    else None
                                                )
                                                input_schema = (
                                                    catalog_entry.get("input_schema")
                                                    if isinstance(catalog_entry, Mapping) and isinstance(catalog_entry.get("input_schema"), Mapping)
                                                    else None
                                                )
                                                effective_remote_args, defaults_applied = self._apply_mcp_setup_defaults(
                                                    arguments,
                                                    connection=connection,
                                                    input_schema=input_schema,
                                                )
                                                remote_event_id = tool_event_id
                                                sensitive_keys = self._mcp_setup_fields_for_connection(connection).keys()
                                                redacted_input = redact_tool_input_payload(arguments, sensitive_keys=sensitive_keys)
                                                if not isinstance(redacted_input, Mapping):
                                                    redacted_input = {}
                                                remote_event_payload = {
                                                    "event_id": remote_event_id,
                                                    "phase": "started",
                                                    "status": "running",
                                                    "tool_call_id": tool_call_id,
                                                    "tool_name": tool_name,
                                                    "kind": "mcp_remote",
                                                    "remote": {
                                                        "connection_id": str(getattr(connection, "id", "") or ""),
                                                        "connection_name": str(getattr(connection, "name", "") or ""),
                                                        "endpoint_url": str(getattr(connection, "server_url", "") or ""),
                                                        "remote_tool": remote_tool_name,
                                                    },
                                                    "input": dict(redacted_input),
                                                }
                                                if ui_spinner_text:
                                                    remote_event_payload["spinner_text"] = ui_spinner_text
                                                if defaults_applied:
                                                    remote_event_payload["defaults_applied"] = list(defaults_applied)
                                                if on_tool_event:
                                                    try:
                                                        on_tool_event(remote_event_payload)
                                                    except Exception:  # pragma: no cover - UI callback must not break tools
                                                        logger.exception("mcp portal tool event start callback failed")
                                                tool_result = self._execute_remote_mcp_tool(
                                                    tool_name=tool_name,
                                                    remote_tool_name=remote_tool_name,
                                                    connection=connection,
                                                    arguments=effective_remote_args,
                                                    conversation=conversation,
                                                    idempotency_key=self._mcp_idempotency_key(
                                                        conversation_id=conversation.id,
                                                        event_id=tool_event_id,
                                                    ),
                                                    operation_type=str(approval_requirement.get("operation_type") or ""),
                                                )
                                        else:
                                            call_start = time.perf_counter()
                                            is_email_tool = self._is_email_tool(tool_name)
                                            effective_arguments = (
                                                self._sanitize_email_tool_arguments(tool_name, arguments)
                                                if is_email_tool
                                                else arguments
                                            )
                                            internal_event_payload = {
                                                "event_id": tool_event_id,
                                                "phase": "started",
                                                "status": "running",
                                                "tool_call_id": tool_call_id,
                                                "tool_name": tool_name,
                                                "kind": "email" if is_email_tool else "mcp_internal",
                                            }
                                            if ui_spinner_text:
                                                internal_event_payload["spinner_text"] = ui_spinner_text
                                            # Keep internal tool inputs minimal; portal UI should render
                                            # user-facing results via dedicated blocks (attachments, etc.)
                                            # rather than surfacing full tool arguments.
                                            if is_email_tool:
                                                email_input = self._email_tool_event_input(tool_name, effective_arguments)
                                                if email_input:
                                                    internal_event_payload["input"] = email_input
                                            elif tool_name in {"mcp_search_tools", "search_knowledge", "search_conversation_files"}:
                                                query_text = ""
                                                if isinstance(effective_arguments, Mapping):
                                                    query_values = effective_arguments.get("queries")
                                                    if isinstance(query_values, list):
                                                        for value in query_values:
                                                            query_text = str(value or "").strip()
                                                            if query_text:
                                                                break
                                                    if not query_text:
                                                        query_value = effective_arguments.get("query")
                                                        if isinstance(query_value, str):
                                                            query_text = query_value.strip()
                                                        elif query_value is not None:
                                                            query_text = str(query_value).strip()
                                                if query_text:
                                                    internal_event_payload["input"] = {
                                                        "query": self._clip_text(query_text, 280),
                                                    }
                                            elif tool_name == "request_user_input":
                                                prompt_value = (
                                                    effective_arguments.get("prompt") if isinstance(effective_arguments, Mapping) else None
                                                )
                                                prompt_text = str(prompt_value).strip() if prompt_value is not None else ""
                                                raw_questions = (
                                                    effective_arguments.get("questions") if isinstance(effective_arguments, Mapping) else None
                                                )
                                                questions: list[str] = []
                                                if isinstance(raw_questions, list):
                                                    for q in raw_questions:
                                                        if not isinstance(q, str):
                                                            continue
                                                        qt = q.strip()
                                                        if qt:
                                                            questions.append(self._clip_text(qt, 200))
                                                if prompt_text or questions:
                                                    payload: dict[str, object] = {}
                                                    if prompt_text:
                                                        payload["prompt"] = self._clip_text(prompt_text, 280)
                                                    if questions:
                                                        payload["questions"] = questions[:5]
                                                    internal_event_payload["input"] = payload
                                            if on_tool_event:
                                                try:
                                                    on_tool_event(internal_event_payload)
                                                except Exception:  # pragma: no cover - UI callback must not break tools
                                                    logger.exception("mcp portal tool event start callback failed")
                                            tool_result = None
                                            if tool_name in native_tool_names_all:
                                                native_tool_policy = self._resolve_native_integration_policy(
                                                    conversation=conversation,
                                                    tool_name=tool_name,
                                                    arguments=effective_arguments,
                                                )
                                                decision = str(native_tool_policy.get("decision") or "").strip()
                                                if decision == "deny":
                                                    tool_result = (
                                                        dict(native_tool_policy.get("error_payload") or {})
                                                        if isinstance(native_tool_policy.get("error_payload"), Mapping)
                                                        else {
                                                            "tool": tool_name,
                                                            "status": "error",
                                                            "error": str(native_tool_policy.get("reason_code") or "not_connected"),
                                                            "error_code": str(native_tool_policy.get("reason_code") or "not_connected"),
                                                            "hint": "Integration tool call is blocked by policy.",
                                                        }
                                                    )
                                                    call_origin = "policy"
                                                elif decision == "allow_with_confirmation":
                                                    approval_requirement = {
                                                        "requires_approval": True,
                                                        "approval_mode": native_tool_policy.get("approval_mode")
                                                        or self._effective_tool_approval_mode(conversation=conversation),
                                                        "operation_type": native_tool_policy.get("operation_type")
                                                        or McpToolOperationType.UNKNOWN,
                                                        "reason": native_tool_policy.get("reason") or "native_integration_write",
                                                    }
                                                    approved, _, approval_result = self._maybe_request_tool_approval(
                                                        conversation=conversation,
                                                        connection=None,
                                                        tool_name=tool_name,
                                                        remote_tool_name="",
                                                        tool_call_id=tool_call_id,
                                                        tool_event_id=tool_event_id,
                                                        arguments=effective_arguments,
                                                        approval_requirement=approval_requirement,
                                                        on_tool_event=on_tool_event,
                                                        wait_for_approval=wait_for_tool_approval,
                                                    )
                                                    if not approved:
                                                        tool_result = dict(approval_result) if isinstance(approval_result, Mapping) else {}
                                                        tool_result["tool"] = tool_name
                                                        tool_result["error"] = "approval_required"
                                                        tool_result["error_code"] = "approval_required"
                                                        if not str(tool_result.get("hint") or "").strip():
                                                            tool_result["hint"] = (
                                                                "User approval is required before this integration action can run."
                                                            )
                                                        call_origin = "policy"

                                                resolved_account_id = str(
                                                    native_tool_policy.get("resolved_integration_account_id") or ""
                                                ).strip()
                                                if tool_result is None and resolved_account_id:
                                                    effective_arguments = dict(effective_arguments)
                                                    effective_arguments["integration_account_id"] = resolved_account_id

                                            if tool_result is None and tool_name == "email_send_draft":
                                                draft_id = str(
                                                    effective_arguments.get("draft_id") or effective_arguments.get("draftId") or ""
                                                ).strip()
                                                email_account = self._resolve_email_account_for_tool_call(
                                                    conversation=conversation,
                                                    arguments=effective_arguments,
                                                )
                                                if self._looks_like_placeholder_draft_id(draft_id) or not draft_id:
                                                    pending = self._pending_email_draft_for_conversation(
                                                        conversation=conversation,
                                                        email_account_id=getattr(email_account, "id", None) if email_account else None,
                                                    )
                                                    if pending:
                                                        effective_arguments = dict(effective_arguments)
                                                        effective_arguments.pop("draftId", None)
                                                        effective_arguments["draft_id"] = pending["draft_id"]
                                                        draft_id = pending["draft_id"]
                                                approval_needed = False
                                                approval_reason = "draft_plus_approval_default"
                                                if email_account and draft_id:
                                                    approval_needed, approval_reason = self._email_send_requires_approval(
                                                        conversation=conversation,
                                                        email_account=email_account,
                                                        draft_id=draft_id,
                                                    )
                                                if approval_needed:
                                                    approved, _, approval_result = self._maybe_request_email_tool_approval(
                                                        conversation=conversation,
                                                        tool_name=tool_name,
                                                        tool_call_id=tool_call_id,
                                                        tool_event_id=tool_event_id,
                                                        arguments=effective_arguments,
                                                        reason=approval_reason,
                                                        on_tool_event=on_tool_event,
                                                        wait_for_approval=wait_for_tool_approval,
                                                    )
                                                    if not approved:
                                                        tool_result = approval_result
                                                        call_origin = "policy"
                                                if tool_result is None:
                                                    tool_result = mcp_tools.execute_tool(
                                                        tool_name,
                                                        effective_arguments,
                                                        conversation=conversation,
                                                        context=tool_context,
                                                    )
                                                    if email_account and isinstance(tool_result, Mapping):
                                                        self._record_email_send_audit(
                                                            conversation=conversation,
                                                            email_account=email_account,
                                                            tool_result=tool_result,
                                                        )
                                                        status_value = str(tool_result.get("status") or "").strip().lower()
                                                        if status_value == "ok":
                                                            resolved_draft_id = str(
                                                                tool_result.get("draft_id")
                                                                or tool_result.get("draftId")
                                                                or draft_id
                                                            ).strip()
                                                            self._clear_pending_email_draft(
                                                                conversation=conversation,
                                                                email_account_id=getattr(email_account, "id", None),
                                                                draft_id=resolved_draft_id,
                                                            )
                                            elif tool_result is None and tool_name == "initiate_phone_call":
                                                dedupe_payload = self._phone_tool_input_payload(effective_arguments)
                                                dedupe_signature = self._tool_signature(tool_name, dedupe_payload)
                                                if dedupe_signature in seen_phone_call_signatures:
                                                    tool_result = self._duplicate_phone_call_payload(tool_name)
                                                    call_origin = "policy"
                                                else:
                                                    seen_phone_call_signatures.add(dedupe_signature)
                                                    approved, _, approval_result = self._maybe_request_phone_tool_approval(
                                                        conversation=conversation,
                                                        tool_name=tool_name,
                                                        tool_call_id=tool_call_id,
                                                        tool_event_id=tool_event_id,
                                                        arguments=effective_arguments,
                                                        on_tool_event=on_tool_event,
                                                        wait_for_approval=wait_for_tool_approval,
                                                    )
                                                    if not approved:
                                                        tool_result = approval_result
                                                        call_origin = "policy"
                                                    if tool_result is None:
                                                        tool_result = mcp_tools.execute_tool(
                                                            tool_name,
                                                            effective_arguments,
                                                            conversation=conversation,
                                                            context=tool_context,
                                                        )
                                            elif tool_result is None:
                                                general_override_mode = self._tool_approval_override_for_tool(
                                                    conversation=conversation,
                                                    tool_name=tool_name,
                                                )
                                                if (
                                                    general_override_mode == "confirm"
                                                    and tool_name not in native_tool_names_all
                                                ):
                                                    approval_requirement = {
                                                        "requires_approval": True,
                                                        "approval_mode": McpConnectionApprovalMode.APPROVE_ALL,
                                                        "operation_type": McpToolOperationType.UNKNOWN,
                                                        "reason": "controls_override_confirm",
                                                    }
                                                    approved, _, approval_result = self._maybe_request_tool_approval(
                                                        conversation=conversation,
                                                        connection=None,
                                                        tool_name=tool_name,
                                                        remote_tool_name="",
                                                        tool_call_id=tool_call_id,
                                                        tool_event_id=tool_event_id,
                                                        arguments=effective_arguments,
                                                        approval_requirement=approval_requirement,
                                                        on_tool_event=on_tool_event,
                                                        wait_for_approval=wait_for_tool_approval,
                                                    )
                                                    if not approved:
                                                        tool_result = approval_result
                                                        call_origin = "policy"

                                                if tool_result is None:
                                                    tool_result = mcp_tools.execute_tool(
                                                        tool_name,
                                                        effective_arguments,
                                                        conversation=conversation,
                                                        context=tool_context,
                                                    )
                                                if tool_name == "email_create_draft" and isinstance(tool_result, Mapping):
                                                    status_value = str(tool_result.get("status") or "").strip().lower()
                                                    if status_value == "ok":
                                                        account_id = self._try_parse_uuid(
                                                            str(tool_result.get("email_account_id") or "").strip()
                                                        )
                                                        draft_id = str(tool_result.get("draft_id") or "").strip()
                                                        if account_id and draft_id:
                                                            created_email_draft = {
                                                                "draft_id": draft_id,
                                                                "email_account_id": str(account_id),
                                                            }
                                                            draft_preview = self._email_tool_event_input(
                                                                "email_create_draft",
                                                                effective_arguments,
                                                            )
                                                            self._set_pending_email_draft(
                                                                conversation=conversation,
                                                                email_account_id=account_id,
                                                                provider=str(tool_result.get("provider") or ""),
                                                                draft_id=draft_id,
                                                                message_id=str(tool_result.get("message_id") or "").strip(),
                                                                thread_id=str(tool_result.get("thread_id") or "").strip(),
                                                                preview=draft_preview
                                                                if isinstance(draft_preview, Mapping)
                                                                else None,
                                                            )
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
                                    if call_start is not None:
                                        call_duration_ms = (time.perf_counter() - call_start) * 1000.0
                                    if remote_event_id and remote_event_payload and isinstance(tool_result, Mapping):
                                        # Phase 1: Store full external MCP tool outputs out-of-band (tenant-scoped)
                                        # and feed only a compact prompt_view + artifact_id back into the LLM loop.
                                        try:
                                            prompt_view = build_prompt_view_for_remote_tool_result(tool_result)
                                            artifact_id = store_remote_tool_output_artifact(
                                                conversation=conversation,
                                                tool_call_id=tool_call_id,
                                                tool_event_id=tool_event_id,
                                                invoked_tool=tool_name,
                                                remote_event_payload=remote_event_payload,
                                                tool_result=tool_result,
                                            )
                                            remote_safe: dict[str, object] = {}
                                            remote_meta = (
                                                remote_event_payload.get("remote")
                                                if isinstance(remote_event_payload.get("remote"), Mapping)
                                                else None
                                            )
                                            if remote_meta:
                                                connection_name = remote_meta.get("connection_name")
                                                remote_tool = remote_meta.get("remote_tool")
                                                if connection_name:
                                                    remote_safe["connection_name"] = str(connection_name)[:240]
                                                if remote_tool:
                                                    remote_safe["tool"] = str(remote_tool)[:240]
                                            tool_id_for_model = str(remote_event_payload.get("tool_name") or "").strip()
                                            tool_result = {
                                                "tool": tool_name,
                                                "status": tool_result.get("status"),
                                                "error_code": tool_result.get("error_code"),
                                                "error": tool_result.get("error"),
                                                "hint": tool_result.get("hint"),
                                                "is_error": bool(tool_result.get("is_error")),
                                                "tool_id": tool_id_for_model,
                                                **({"artifact_id": artifact_id} if artifact_id else {}),
                                                **({"remote": remote_safe} if remote_safe else {}),
                                                "prompt_view": prompt_view,
                                                "prompt_compact": True,
                                            }
                                        except Exception:  # pragma: no cover - must never break tool loop
                                            logger.exception("mcp tool output isolation failed")
                                            tool_result = {
                                                "tool": tool_name,
                                                "status": tool_result.get("status"),
                                                "error_code": tool_result.get("error_code"),
                                                "error": tool_result.get("error"),
                                                "hint": tool_result.get("hint"),
                                                "is_error": bool(tool_result.get("is_error")),
                                                "truncated": True,
                                                "prompt_compact": True,
                                            }
                                    if remote_event_id and remote_event_payload and on_tool_event:
                                        try:
                                            finish_payload = dict(remote_event_payload)
                                            finish_payload["phase"] = "finished"
                                            finish_payload["duration_ms"] = (
                                                int(call_duration_ms) if call_duration_ms is not None else 0
                                            )
                                            if isinstance(tool_result, Mapping):
                                                finish_payload["status"] = str(tool_result.get("status") or "") or "ok"
                                                finish_payload["output"] = dict(tool_result)
                                            on_tool_event(finish_payload)
                                        except Exception:  # pragma: no cover - UI callback must not break tools
                                            logger.exception("mcp portal tool event finish callback failed")
                                    if internal_event_payload and on_tool_event:
                                        try:
                                            finish_payload = dict(internal_event_payload)
                                            finish_payload["phase"] = "finished"
                                            finish_payload["duration_ms"] = (
                                                int(call_duration_ms) if call_duration_ms is not None else 0
                                            )
                                            if isinstance(tool_result, Mapping):
                                                finish_payload["status"] = str(tool_result.get("status") or "") or "ok"
                                                if self._is_email_tool(tool_name):
                                                    finish_payload["output"] = self._email_tool_event_output(tool_name, tool_result)
                                                else:
                                                    finish_payload["output"] = self._compact_tool_payload_for_prompt(
                                                        tool_name,
                                                        tool_result,
                                                        **self._prompt_compaction_limits(),
                                                    )
                                            on_tool_event(finish_payload)
                                        except Exception:  # pragma: no cover - UI callback must not break tools
                                            logger.exception("mcp portal tool event finish callback failed")
                        if tool_name == "search_knowledge" and isinstance(tool_result, Mapping):
                            snippets = tool_result.get("snippets")
                            if isinstance(snippets, list) and snippets:
                                table_only_workflow = self._search_result_is_table(tool_result)

                        trace_index: int | None = None
                        if isinstance(tool_result, Mapping):
                            # Layer 2: tool responses carry budget telemetry instead of mid-loop
                            # injected system messages.

                            if tool_name in {"search_knowledge", "read_knowledge"}:
                                if bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True)):
                                    tool_result = dict(tool_result)
                                    tool_result["budget"] = tool_context.budget_snapshot()

                            diagnostics = (
                                tool_result.get("diagnostics")
                                if isinstance(tool_result.get("diagnostics"), Mapping)
                                else {}
                            )
                            engine_tool = tool_result.get("engine_tool") or diagnostics.get("engine_tool")
                            mode = tool_result.get("mode") or diagnostics.get("mode")
                            page = tool_result.get("page") or diagnostics.get("page")
                            token_budget = tool_result.get("token_budget") or diagnostics.get("token_budget")

                            trace_arguments = arguments
                            if self._is_email_tool(tool_name):
                                trace_arguments = self._email_tool_trace_arguments(tool_name, arguments)

                            output_summary: dict[str, object] | None = None
                            try:
                                output_summary = self._tool_trace_output_summary(tool_name, tool_result)
                            except Exception:  # pragma: no cover - must never break tool loop
                                logger.exception("mcp tool trace output summary failed")

                            tool_context.add_tool_trace(
                                {
                                    "tool": tool_name,
                                    "arguments": trace_arguments,
                                    "llm_request": {
                                        "tool": llm_requested_tool_name,
                                        "arguments": llm_requested_arguments,
                                    },
                                    "result_keys": sorted(tool_result.keys()),
                                    "status": tool_result.get("status"),
                                    "error_code": tool_result.get("error_code"),
                                    "hint": tool_result.get("hint"),
                                    "engine": tool_result.get("engine"),
                                    "engine_tool": engine_tool,
                                    "mode": mode,
                                    "page": page,
                                    "token_budget": token_budget,
                                    "throttle_notice": bool(tool_result.get("throttle_notice")),
                                    "duration_ms": int(call_duration_ms) if call_duration_ms is not None else 0,
                                    "origin": call_origin,
                                    "cache_hit": cache_hit,
                                    "output_summary": output_summary,
                                }
                            )
                            trace_index = len(tool_context.tool_trace) - 1

                            if self._is_knowledge_tool(tool_name):
                                self._record_knowledge_outputs(tool_context, tool_result)
                        tool_status_value = ""
                        if isinstance(tool_result, Mapping):
                            tool_status_value = str(tool_result.get("status") or "").strip().lower()
                        iteration_executed_tools.append((tool_name, tool_status_value))
                        limits = self._prompt_compaction_limits()
                        prompt_tool_result: object
                        if isinstance(tool_result, Mapping):
                            prompt_tool_result = self._compact_tool_payload_for_prompt(
                                tool_name,
                                tool_result,
                                **limits,
                            )
                        else:
                            prompt_tool_result = {
                                "tool": tool_name,
                                "result": self._clip_text(tool_result, 2000) if tool_result is not None else None,
                                "prompt_compact": True,
                            }

                        raw_tool_json = json.dumps(prompt_tool_result, ensure_ascii=False)
                        truncated_tool_json = self._truncate_tool_message_for_prompt(tool_name, raw_tool_json)
                        # Observability: track when we had to truncate a *knowledge* tool message before it
                        # hit the LLM prompt (this should trend to ~0 in agentic-v2 mode).
                        try:
                            tool_output_limit = self._tool_output_max_chars()
                        except Exception:
                            tool_output_limit = 0
                        if (
                            tool_output_limit
                            and self._is_knowledge_tool(tool_name)
                            and isinstance(raw_tool_json, str)
                            and len(raw_tool_json) > tool_output_limit
                        ):
                            structured_log(
                                "mcp",
                                "prompt.tool_output_truncated",
                                {
                                    "tool": tool_name,
                                    "raw_chars": len(raw_tool_json),
                                    "limit": int(tool_output_limit),
                                },
                                context={
                                    "conversation": conversation.id,
                                    "business": conversation.business_profile_id,
                                },
                                logger_obj=logger,
                                level=logging.WARNING,
                            )

                        if (
                            trace_index is not None
                            and isinstance(getattr(tool_context, "tool_trace", None), list)
                            and 0 <= trace_index < len(tool_context.tool_trace)
                            and isinstance(tool_context.tool_trace[trace_index], dict)
                        ):
                            try:
                                tool_context.tool_trace[trace_index]["prompt_compaction"] = {
                                    "raw_chars": int(len(raw_tool_json) if isinstance(raw_tool_json, str) else 0),
                                    "truncated_chars": int(
                                        len(truncated_tool_json) if isinstance(truncated_tool_json, str) else 0
                                    ),
                                    "limit_chars": int(tool_output_limit or 0),
                                    "truncated": bool(
                                        isinstance(raw_tool_json, str)
                                        and isinstance(truncated_tool_json, str)
                                        and raw_tool_json != truncated_tool_json
                                    ),
                                }
                                tool_context.tool_trace[trace_index]["llm_response"] = {
                                    "tool_call_id": str(tool_call.get("id") or ""),
                                    "content": truncated_tool_json,
                                }
                            except Exception:  # pragma: no cover - must never break tool loop
                                logger.exception("mcp tool trace prompt compaction patch failed")

                        transcript.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id"),
                                "name": tool_name,
                                "content": truncated_tool_json,
                            }
                        )

                        if created_email_draft and not email_send_requested:
                            lowered = (user_message or "").strip().lower()
                            send_intent = False
                            if lowered:
                                if re.search(r"\b(send|reply|respond|forward)\b", lowered):
                                    send_intent = True
                                elif ("email" in lowered or "e-mail" in lowered) and not re.search(r"\bdraft\b", lowered):
                                    send_intent = True

                            if send_intent:
                                draft_id = str(created_email_draft.get("draft_id") or "").strip()
                                account_id = str(created_email_draft.get("email_account_id") or "").strip()
                                send_args: dict[str, object] = {"draft_id": draft_id}
                                if account_id:
                                    send_args["email_account_id"] = account_id
                                email_account = self._resolve_email_account_for_tool_call(
                                    conversation=conversation,
                                    arguments=send_args,
                                )
                                if email_account and draft_id:
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
                                            _emit_final_answer(response_text)
                                            _status_event("answer_finalized", "Answer ready")
                                            _status_event("stream_complete", "")
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
                                                "streamed_chunks": tuple(answer_streamed_chunks),
                                                "clean_answer_text": response_text,
                                                "dropped_sentences": tuple(),
                                                "llm_strategy": "mcp_tools_email_draft_gate",
                                                "response_blocks": response_blocks,
                                            }

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
                                                send_tool_result.get("draft_id")
                                                or send_tool_result.get("draftId")
                                                or draft_id
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

                                    _emit_final_answer(response_text)
                                    _status_event("answer_finalized", "Answer ready")
                                    _status_event("stream_complete", "")
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
                                        "streamed_chunks": tuple(answer_streamed_chunks),
                                        "clean_answer_text": response_text,
                                        "dropped_sentences": tuple(),
                                        "llm_strategy": "mcp_tools_email_draft_gate",
                                        "response_blocks": response_blocks,
                                    }

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
            single_pass_text = first_content_raw or "".join(first_pass_streamed_chunks).strip()
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
                verification_message = self._run_verification(
                    conversation=conversation,
                    user_message=user_message,
                    draft_answer=clean_single,
                    tool_context=tool_context,
                )
                verification_payload = (
                    self._parse_verification_payload(verification_message)
                    if verification_message
                    else None
                )
                if verification_payload:
                    tool_context.verification = dict(verification_payload)
                elif verification_message:
                    raw_verification = str(verification_message.get("content") or "").strip()
                    if raw_verification:
                        tool_context.verification = {
                            "verdict": "parse_error",
                            "missing_points": [],
                            "final_response": "",
                            "notes": self._clip_text(raw_verification, 320),
                        }
                if getattr(tool_context, "verification", None):
                    snapshot = dict(getattr(tool_context, "verification") or {})
                    structured_log(
                        "mcp",
                        "verification.result",
                        {
                            "verdict": snapshot.get("verdict"),
                            "missing_points": len(snapshot.get("missing_points") or ()),
                            "override": bool(str(snapshot.get("final_response") or "").strip()),
                        },
                        context={
                            "conversation": conversation.id,
                            "business": conversation.business_profile_id,
                        },
                        logger_obj=logger,
                    )
                    verdict = snapshot.get("verdict")
                    override = str(snapshot.get("final_response") or "").strip()
                    if verdict in {"needs_clarification", "unsupported"} and override:
                        clean_single = override

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
            streaming_mode = "final"
            if streaming_allowed:
                answer_streamed_chunks[:] = list(first_pass_streamed_chunks)
                _log_stream_mismatch(answer_streamed_chunks, clean_single, stage="single_pass")
            else:
                answer_streamed_chunks.clear()
                _emit_final_answer(clean_single)
            _status_event("stream_complete", "")
            response_blocks = self._extract_response_blocks(normalized_assistant)
            clean_single = str(normalized_assistant.get("content") or clean_single)
            return {
                "assistant_message": normalized_assistant,
                "tool_context": tool_context,
                "streamed_chunks": tuple(answer_streamed_chunks),
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
        if not answer_text_raw and answer_streamed_chunks:
            answer_text_raw = "".join(answer_streamed_chunks).strip()

        _mark_answer_started()
        _status_event("answer_finalized", "Answer ready")

        unmet_read_required_count = 0
        read_required_reasons: set[str] = set()
        table_results_present = False
        for entry in getattr(tool_context, "knowledge_results", []):
            if not isinstance(entry, Mapping):
                continue
            if entry.get("read_required"):
                unmet_read_required_count += 1
                reasons = entry.get("read_required_reasons")
                if isinstance(reasons, list):
                    for reason in reasons:
                        if isinstance(reason, str) and reason:
                            read_required_reasons.add(reason)
            if entry.get("search_stage") in {"table_direct", "table_blended"}:
                table_results_present = True
        no_reads = not getattr(tool_context, "knowledge_reads", [])
        if no_reads and unmet_read_required_count:
            structured_log(
                "mcp",
                "read_required.unfulfilled",
                {
                    "read_required_count": unmet_read_required_count,
                    "table_results_present": table_results_present,
                    "reasons": sorted(read_required_reasons),
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )

        clean_answer_text, dropped_sentences = sanitize_with_diagnostics(
            answer_text_raw,
            conversation=conversation,
            stage="tool_loop_final",
            filter_level=filter_level,
        )
        if not clean_answer_text and answer_text_raw:
            clean_answer_text = answer_text_raw.strip()
        if not clean_answer_text and answer_streamed_chunks:
            clean_answer_text = "".join(answer_streamed_chunks).strip()
        if verification_blocks_streaming:
            verification_message = self._run_verification(
                conversation=conversation,
                user_message=user_message,
                draft_answer=clean_answer_text,
                tool_context=tool_context,
            )
            verification_payload = (
                self._parse_verification_payload(verification_message)
                if verification_message
                else None
            )
            if verification_payload:
                tool_context.verification = dict(verification_payload)
            elif verification_message:
                raw_verification = str(verification_message.get("content") or "").strip()
                if raw_verification:
                    tool_context.verification = {
                        "verdict": "parse_error",
                        "missing_points": [],
                        "final_response": "",
                        "notes": self._clip_text(raw_verification, 320),
                    }
            if getattr(tool_context, "verification", None):
                snapshot = dict(getattr(tool_context, "verification") or {})
                structured_log(
                    "mcp",
                    "verification.result",
                    {
                        "verdict": snapshot.get("verdict"),
                        "missing_points": len(snapshot.get("missing_points") or ()),
                        "override": bool(str(snapshot.get("final_response") or "").strip()),
                    },
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                    logger_obj=logger,
                )
                verdict = snapshot.get("verdict")
                override = str(snapshot.get("final_response") or "").strip()
                if verdict in {"needs_clarification", "unsupported"} and override:
                    clean_answer_text = override
        all_dropped = stream_dropped + dropped_sentences
        normalized_assistant_msg = dict(final_assistant_message or {})
        normalized_assistant_msg["content"] = clean_answer_text
        response_blocks = self._extract_response_blocks(normalized_assistant_msg)
        clean_answer_text = str(normalized_assistant_msg.get("content") or clean_answer_text)
        if streaming_allowed:
            _log_stream_mismatch(answer_streamed_chunks, clean_answer_text, stage="tool_loop_final")
        else:
            answer_streamed_chunks.clear()
            _emit_final_answer(clean_answer_text)
        _status_event("stream_complete", "")

        self._log_turn_metrics(conversation, tool_context)
        self._persist_seen_items(conversation, tool_context)
        return {
            "assistant_message": normalized_assistant_msg,
            "tool_context": tool_context,
            "streamed_chunks": tuple(answer_streamed_chunks),
            "clean_answer_text": clean_answer_text,
            "dropped_sentences": tuple(all_dropped),
            "llm_strategy": "mcp_tools_stream_loop",
            "response_blocks": response_blocks,
        }
