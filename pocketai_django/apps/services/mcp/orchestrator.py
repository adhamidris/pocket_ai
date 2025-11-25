"""
MCP-style orchestrator skeleton.

The goal is to keep this implementation self-contained so we can experiment
with standard tool-calling workflows without disturbing the legacy
AiOrchestratorService. Later phases will flesh out the orchestration loop,
tool dispatch, and plan construction logic.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Callable, Iterable, Mapping, Sequence

from django.conf import settings

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationExtractionType
from apps.services.llm_provider import PromptGenerationError, _emit_stream_chunks
from apps.services.ai_orchestrator import (
    AiOrchestratorPlan,
    PlannedAction,
    ExtractionPlan,
    KnowledgeSnippet,
    ActionType,
    StreamingTurnContext,
)
from apps.services.rag_logging import structured_log

from . import prompts, tools
from .sanitizer import extract_sentences, is_investigative_filler_with_level, sanitize_with_diagnostics
from django.core.cache import cache

from .types import (
    BaseMcpProvider,
    ToolExecutionContext,
    ToolConstraintError,
    ChunkReadBudgetExceeded,
    ChunkPageBudgetExceeded,
    CharacterBudgetExceeded,
)
from .identifier_registry import IdentifierGuardrail


logger = logging.getLogger(__name__)


class McpOrchestratorService:
    """
    Placeholder MCP orchestrator.

    This class mirrors the public API of AiOrchestratorService so the chat
    portal can swap between implementations via a feature flag. Real behavior
    will be implemented in later phases of the migration plan.
    """

    def __init__(self, *, agent: AgentProfile, provider: BaseMcpProvider | None) -> None:
        self.agent = agent
        self.provider = provider
        self.tool_definitions = tools.TOOL_DEFINITIONS
        self.max_tool_iterations = int(getattr(settings, "MCP_MAX_TOOL_ITERATIONS", 10))
        self.business_override_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
        default_chunk_reads = max(1, int(getattr(settings, "RAG_MAX_CHUNK_READS_PER_TURN", 3)))
        self.max_chunk_reads_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_reads_per_turn", default_chunk_reads)),
        )
        default_page_windows = max(1, int(getattr(settings, "RAG_MAX_CHUNK_PAGES_PER_TURN", 3)))
        self.max_chunk_pages_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_pages_per_turn", default_page_windows)),
        )
        self.default_char_budget_per_turn = max(
            4000,
            int(getattr(settings, "RAG_MAX_CHAR_BUDGET_PER_TURN", 48000)),
        )
        self.default_char_budget_per_minute = max(
            4000,
            int(getattr(settings, "RAG_MAX_CHAR_BUDGET_PER_MINUTE", 64000)),
        )
        self.char_budget_window_seconds = max(30, int(getattr(settings, "RAG_CHAR_BUDGET_WINDOW_SECONDS", 60)))

    def _execute_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """
        Build the orchestration plan for the latest customer message.

        Splits the turn into an internal tool loop (no user-facing streaming)
        followed by a final answer pass that streams only customer-facing text.
        """

        messages = prompts.build_messages(conversation=conversation, user_message=user_message)
        filter_level = self._filter_level_for_conversation(conversation)
        char_turn_limit = self._char_budget_per_turn(conversation.business_profile)
        char_minute_limit = self._char_budget_per_minute(conversation.business_profile)
        minute_reserver = self._build_minute_budget_reserver(conversation.business_profile, char_minute_limit)
        tool_context = ToolExecutionContext(
            max_chunk_reads_per_turn=self.max_chunk_reads_per_turn,
            max_chunk_pages_per_turn=self.max_chunk_pages_per_turn,
            char_budget_per_turn=char_turn_limit,
            char_budget_per_minute=char_minute_limit,
            minute_budget_reserver=minute_reserver,
        )
        tool_context.identifier_gate = IdentifierGuardrail.from_conversation(conversation)

        if not self.provider:
            raise RuntimeError("MCP provider is not configured.")

        transcript = list(messages)
        tool_phase_assistant_message: dict[str, object] | None = None
        final_assistant_message: dict[str, object] | None = None
        placeholder_sent = False
        answer_streamed_chunks: list[str] = []
        single_pass_candidate: str | None = None
        first_stream_tool_calls: list[Mapping[str, object]] = []
        first_stream_message: dict[str, object] | None = None
        stream_buffer = ""
        stream_dropped: list[str] = []

        if on_status_change:
            on_status_change({"code": "thinking", "label": "Thinking…"})

        def _emit_tokens(text: str) -> None:
            if not text:
                return
            for token in re.findall(r"\S+\s*|\s+", text, flags=re.MULTILINE):
                if not token:
                    continue
                answer_streamed_chunks.append(token)
                if on_response_text_delta:
                    try:
                        on_response_text_delta(token)
                    except Exception:  # pragma: no cover - defensive
                        logger.exception("on_response_text_delta callback failed")

        def _emit_sentence(text: str) -> None:
            if not text:
                return
            _emit_tokens(text)

        def _flush_stream_buffer(stage: str) -> None:
            nonlocal stream_buffer
            trailing = stream_buffer
            if not trailing:
                return
            trailing_stripped = trailing.strip()
            if trailing_stripped and is_investigative_filler_with_level(trailing_stripped, filter_level=filter_level):
                stream_dropped.append(trailing_stripped)
                structured_log(
                    "mcp",
                    "sanitizer.dropped_sentence",
                    {
                        "stage": stage,
                        "text": trailing_stripped[:200],
                    },
                    indent=1,
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                    logger_obj=logger,
                )
            else:
                _emit_tokens(trailing)
            stream_buffer = ""

        # Phase 1: streaming tool-enabled call. If tool_calls appear, we will
        # fall back to the full tool loop + final-answer path. If no tool_calls
        # and we have content, we can keep this streamed text and skip the
        # second content call.
        def _first_stream_chunk(chunk: str) -> None:
            nonlocal stream_buffer
            if not chunk:
                return
            stream_buffer = f"{stream_buffer}{chunk}"
            while True:
                match = re.search(r"(.+?[.!?])([\\s]|$)", stream_buffer)
                if match:
                    sentence = match.group(1)
                    remainder = stream_buffer[match.end(1):]
                    stripped = sentence.strip()
                    if is_investigative_filler_with_level(stripped, filter_level=filter_level):
                        stream_dropped.append(stripped)
                        structured_log(
                            "mcp",
                            "sanitizer.dropped_sentence",
                            {
                                "stage": "streaming_tools",
                                "text": stripped[:200],
                            },
                            indent=1,
                            context={
                                "conversation": conversation.id,
                                "business": conversation.business_profile_id,
                            },
                            logger_obj=logger,
                        )
                    else:
                        _emit_sentence(sentence + (match.group(2) or ""))
                    stream_buffer = remainder
                    continue
                if is_investigative_filler_with_level(stream_buffer.strip(), filter_level=filter_level):
                    break
                words = stream_buffer.split(" ")
                if len(words) > 1:
                    emit_part = " ".join(words[:-1]) + " "
                    stream_buffer = words[-1]
                    _emit_tokens(emit_part)
                    continue
                break

        self._log_prompt("primary", conversation=conversation, messages=transcript)
        first_payload = self.provider.chat(
            transcript,
            tools=self.tool_definitions,
            on_stream_delta=_first_stream_chunk,
        )
        first_message = self._coerce_assistant_message(first_payload)
        first_stream_message = dict(first_message or {})
        first_stream_tool_calls = list(first_stream_message.get("tool_calls") or [])
        first_content_raw = ""
        if first_stream_message:
            first_content_raw = str(first_stream_message.get("content") or "").strip()
        pending_assistant = None
        if first_stream_tool_calls:
            # Defer appending until we process the tool call in the loop.
            pending_assistant = first_stream_message
            # We don't want to surface the streamed text from the tool-call turn.
            answer_streamed_chunks.clear()
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
            # Seed transcript with the assistant message containing tool_calls.
            transcript.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": first_stream_tool_calls,
                }
            )
            pending_assistant = None

            for _ in range(self.max_tool_iterations):
                # Execute each tool_call and append tool results.
                current_tool_calls = list(assistant_message.get("tool_calls") or [])
                if not current_tool_calls:
                    break
                for tool_call in current_tool_calls:
                    tool_name = self._tool_name(tool_call)
                    arguments = self._tool_arguments(tool_call)
                    if self._is_knowledge_tool(tool_name):
                        if on_status_change:
                            if tool_name == "search_knowledge":
                                raw_query = arguments.get("query")
                                query = str(raw_query).strip() if raw_query is not None else ""
                                label = f"Searching: {query[:80]}" if query else "Searching knowledge…"
                                on_status_change({"code": "searching_knowledge", "label": label})
                            elif tool_name == "read_document":
                                raw_id = arguments.get("document_id")
                                doc_id = str(raw_id).strip() if raw_id is not None else ""
                                short_id = f"{doc_id[:8]}…" if doc_id else ""
                                base_label = "Reading document"
                                label = f"Reading: {short_id}" if short_id else base_label
                                on_status_change({"code": "reading_document", "label": label})
                    if on_placeholder_response and not placeholder_sent:
                        placeholder_text = str(assistant_message.get("content") or "").strip()
                        if placeholder_text:
                            on_placeholder_response(placeholder_text)
                            placeholder_sent = True

                    try:
                        tool_result = tools.execute_tool(
                            tool_name,
                            arguments,
                            conversation=conversation,
                            context=tool_context,
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
                    tool_context.add_tool_trace(
                        {
                            "tool": tool_name,
                            "arguments": arguments,
                            "result_keys": sorted(tool_result.keys()),
                            "status": tool_result.get("status"),
                            "error_code": tool_result.get("error_code"),
                            "hint": tool_result.get("hint"),
                            "mode": tool_result.get("mode"),
                            "page": tool_result.get("page"),
                            "token_budget": tool_result.get("token_budget"),
                            "throttle_notice": bool(tool_result.get("throttle_notice")),
                        }
                    )
                    if self._is_knowledge_tool(tool_name):
                        self._record_knowledge_outputs(tool_context, tool_result)
                        if tool_name == "read_document" and on_status_change:
                            snippets = tool_result.get("snippets") if isinstance(tool_result, Mapping) else None
                            if isinstance(snippets, list) and snippets:
                                first = snippets[0]
                                if isinstance(first, Mapping):
                                    label_source = (
                                        first.get("public_label")
                                        or first.get("title")
                                        or first.get("source")
                                    )
                                    if isinstance(label_source, str) and label_source.strip():
                                        on_status_change(
                                            {
                                                "code": "reading_document",
                                                "label": f"Reading: {label_source.strip()[:80]}",
                                            }
                                        )
                    transcript.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "name": tool_name,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )

                # Ask the model again with tools enabled to see if more tool_calls are needed.
                payload = self.provider.chat(
                    transcript,
                    tools=self.tool_definitions,
                    on_stream_delta=None,
                )
                assistant_message = self._coerce_assistant_message(payload)
                next_tool_calls = list(assistant_message.get("tool_calls") or [])
                # Append the assistant turn (empty content if tools present).
                transcript.append(
                    {
                        "role": "assistant",
                        "content": "" if next_tool_calls else assistant_message.get("content"),
                        **({"tool_calls": next_tool_calls} if next_tool_calls else {}),
                    }
                )
                if not next_tool_calls:
                    tool_phase_assistant_message = assistant_message
                    raw_content = assistant_message.get("content")
                    if isinstance(raw_content, str) and raw_content.strip():
                        single_pass_candidate = raw_content.strip()
                    break
                first_stream_tool_calls = next_tool_calls
            else:
                raise RuntimeError("MCP tool loop exceeded iteration limit.")
            # Reset streaming buffers for the final-answer pass.
            answer_streamed_chunks.clear()
            stream_buffer = ""
            stream_dropped = []
        # No tool calls from the first streaming pass: take single-pass fast path.
        else:
            tool_phase_assistant_message = first_stream_message
            _flush_stream_buffer("streaming_tools")
            single_pass_text = "".join(answer_streamed_chunks).strip() or first_content_raw
            if on_status_change:
                on_status_change({"code": "responding", "label": "Responding…"})
            clean_single, dropped_single = sanitize_with_diagnostics(
                single_pass_text,
                conversation=conversation,
                stage="single_pass_stream",
                filter_level=filter_level,
            )
            if not clean_single:
                clean_single = single_pass_text

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
            if on_status_change:
                on_status_change({"code": "stream_complete", "label": ""})
            self._log_turn_metrics(conversation, tool_context)
            del on_status_change, on_placeholder_response
            normalized_assistant = dict(tool_phase_assistant_message or {"role": "assistant"})
            normalized_assistant["content"] = clean_single
            return {
                "assistant_message": normalized_assistant,
                "tool_context": tool_context,
                "streamed_chunks": tuple(answer_streamed_chunks),
                "clean_answer_text": clean_single,
                "dropped_sentences": tuple(dropped_single),
                "llm_strategy": "mcp_tools_stream_single_pass",
            }

        def _answer_stream_chunk(chunk: str) -> None:
            nonlocal stream_buffer
            if not chunk:
                return
            stream_buffer = f"{stream_buffer}{chunk}"
            while True:
                # If we have a full sentence, process it.
                match = re.search(r"(.+?[.!?])([\s]|$)", stream_buffer)
                if match:
                    sentence = match.group(1)
                    remainder = stream_buffer[match.end(1):]
                    stripped = sentence.strip()
                    if is_investigative_filler_with_level(stripped, filter_level=filter_level):
                        stream_dropped.append(stripped)
                        structured_log(
                            "mcp",
                            "sanitizer.dropped_sentence",
                            {
                                "stage": "streaming_answer",
                                "text": stripped[:200],
                            },
                            indent=1,
                            context={
                                "conversation": conversation.id,
                                "business": conversation.business_profile_id,
                            },
                            logger_obj=logger,
                        )
                    else:
                        _emit_sentence(sentence + (match.group(2) or ""))
                    stream_buffer = remainder
                    continue

                # No full sentence yet; stream word-by-word if it's not a filler prefix.
                if is_investigative_filler_with_level(stream_buffer.strip(), filter_level=filter_level):
                    break
                words = stream_buffer.split(" ")
                if len(words) > 1:
                    emit_part = " ".join(words[:-1]) + " "
                    stream_buffer = words[-1]
                    _emit_tokens(emit_part)
                    continue
                break

        # If identifier gating blocked retrieval and nothing was read, respond deterministically.
        identifier_filters = getattr(tool_context, "identifier_filters", []) or []
        identifier_blocks = [f for f in identifier_filters if isinstance(f, Mapping) and f.get("status") == "identifier_required"]
        if identifier_blocks and not getattr(tool_context, "knowledge_reads", []):
            requirement = identifier_blocks[0]
            required_keys = requirement.get("required_keys") or ()
            match_policy = requirement.get("match_policy") or "or"
            hint = requirement.get("hint")
            requirement_text = self._identifier_requirement_message(required_keys, match_policy, hint)
            _emit_tokens(requirement_text)
            final_assistant_message = {
                "role": "assistant",
                "content": requirement_text,
                "actions": [],
                "extractions": [],
                "placeholder_response": None,
            }
            if on_status_change:
                on_status_change({"code": "stream_complete", "label": ""})
            self._log_turn_metrics(conversation, tool_context)
            del on_status_change, on_placeholder_response
            return {
                "assistant_message": final_assistant_message,
                "tool_context": tool_context,
                "streamed_chunks": tuple(answer_streamed_chunks),
                "clean_answer_text": requirement_text,
                "dropped_sentences": tuple(),
                "llm_strategy": "mcp_tools_stream_only",
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

        final_messages = prompts.build_final_answer_messages(
            conversation=conversation,
            user_message=user_message,
            tool_context_note=self._planner_tool_note(tool_context),
            coverage_ledger=tuple(getattr(tool_context, "coverage_ledger", ())),
            tool_trace=tuple(getattr(tool_context, "tool_trace", ())),
            assistant_draft=tool_phase_assistant_message,
            identifier_filters=tuple(getattr(tool_context, "identifier_filters", ())),
        )
        use_response_format = True
        if self.provider.__class__.__name__ == "DeepSeekToolsProvider":
            use_response_format = False
        self._log_prompt("final", conversation=conversation, messages=final_messages)
        try:
            final_payload = self.provider.chat(
                final_messages,
                tools=None,
                on_stream_delta=_answer_stream_chunk,
                response_format=self._final_response_schema() if use_response_format else None,
            )
        except PromptGenerationError as exc:
            if "response_format" in str(exc).lower():
                structured_log(
                    "mcp",
                    "final_response_format_unsupported",
                    {"provider": self.provider.__class__.__name__, "error": str(exc)},
                    context={"conversation": conversation.id},
                    level=logging.WARNING,
                )
                final_payload = self.provider.chat(
                    final_messages,
                    tools=None,
                    on_stream_delta=_answer_stream_chunk,
                    response_format=None,
                )
            else:
                raise
        final_assistant_message = self._coerce_assistant_message(final_payload)
        trailing = stream_buffer
        if trailing:
            trailing_stripped = trailing.strip()
            if trailing_stripped and is_investigative_filler_with_level(trailing_stripped, filter_level=filter_level):
                stream_dropped.append(trailing_stripped)
                structured_log(
                    "mcp",
                    "sanitizer.dropped_sentence",
                    {
                        "stage": "streaming_answer",
                        "text": trailing_stripped[:200],
                    },
                    indent=1,
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                )
            else:
                _emit_tokens(trailing)

        answer_text_raw = ""
        if final_assistant_message is not None:
            answer_text_raw = str(final_assistant_message.get("content") or "").strip()

        if on_status_change:
            on_status_change({"code": "stream_complete", "label": ""})

        unmet_read_required = False
        table_results_present = False
        for entry in getattr(tool_context, "knowledge_results", []):
            if not isinstance(entry, Mapping):
                continue
            if entry.get("read_required"):
                unmet_read_required = True
            if entry.get("search_stage") in {"table_direct", "table_blended"}:
                table_results_present = True
            if unmet_read_required and table_results_present:
                break
        no_reads = not getattr(tool_context, "knowledge_reads", [])
        if no_reads and (unmet_read_required or table_results_present):
            # Enforce read-before-answer for table/identifier hits
            final_assistant_message = {
                "role": "assistant",
                "content": "",
                "actions": [],
                "extractions": [],
                "placeholder_response": "Need to read the recommended document/page before answering. Use read_hint (doc_id + page + mode).",
            }
            answer_text_raw = ""

        clean_answer_text, dropped_sentences = sanitize_with_diagnostics(
            answer_text_raw,
            conversation=conversation,
            stage="final_answer",
            filter_level=filter_level,
        )
        if not clean_answer_text and answer_text_raw:
            clean_answer_text = answer_text_raw.strip()
        all_dropped = stream_dropped + dropped_sentences
        normalized_assistant_msg = dict(final_assistant_message or {})
        normalized_assistant_msg["content"] = clean_answer_text

        def _record_chunk(chunk: str) -> None:
            if not chunk:
                return
            answer_streamed_chunks.append(chunk)
            if on_response_text_delta:
                try:
                    on_response_text_delta(chunk)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("on_response_text_delta callback failed")

        if not answer_streamed_chunks:
            _emit_tokens(clean_answer_text)

        self._log_turn_metrics(conversation, tool_context)
        del on_status_change, on_placeholder_response
        return {
            "assistant_message": normalized_assistant_msg,
            "tool_context": tool_context,
            "streamed_chunks": tuple(answer_streamed_chunks),
            "clean_answer_text": clean_answer_text,
            "dropped_sentences": tuple(all_dropped),
        }

    @staticmethod
    def _filter_level_for_conversation(conversation) -> str:
        """
        Map agent tone to a filter level for filler suppression.

        - professional/formal: stricter (drops investigative narration)
        - friendly (default): light (drops only hard guardrails)
        - free/casual: same as friendly for now; still enforces hard guardrails
        """
        tone = getattr(getattr(conversation, "agent_profile", None), "tone", None)
        if not tone:
            return "friendly"
        tone_norm = str(tone).strip().lower()
        if tone_norm in {"professional", "formal"}:
            return "professional"
        if tone_norm in {"free", "freeflow", "freeflowing", "free-flowing", "casual"}:
            return "friendly"
        return "friendly"

    def stream_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_stream_complete: Callable[[], None] | None = None,
    ) -> StreamingTurnContext:
        result = self._execute_turn(
            conversation=conversation,
            user_message=user_message,
            on_response_text_delta=on_response_text_delta,
            on_status_change=on_status_change,
            on_placeholder_response=on_placeholder_response,
        )
        streamed_chunks = tuple(result.get("streamed_chunks") or ())
        clean_answer_text = str(result.get("clean_answer_text") or "")
        tool_context = result.get("tool_context")
        assistant_message = result.get("assistant_message") or {}
        if not streamed_chunks and clean_answer_text:
            reconstructed: list[str] = []
            _emit_stream_chunks(reconstructed.append, clean_answer_text)
            streamed_chunks = tuple(reconstructed)
        strategy = str(result.get("llm_strategy") or "mcp_tools_stream_only")
        diagnostics: dict[str, object] = {
            "llm_strategy": strategy,
            "sanitized_sentences": {
                "count": len(result.get("dropped_sentences") or ()),
                "examples": list(result.get("dropped_sentences") or ())[:3],
            },
        }
        if tool_context:
            diagnostics["tool_trace"] = list(getattr(tool_context, "tool_trace", ()))
            diagnostics["coverage_ledger"] = list(getattr(tool_context, "coverage_ledger", ()))
            diagnostics["knowledge_reads"] = list(getattr(tool_context, "knowledge_reads", ()))
            diagnostics["knowledge_results"] = list(getattr(tool_context, "knowledge_results", ()))
            diagnostics["identifier_checks"] = list(getattr(tool_context, "identifier_checks", ()))
            diagnostics["identifier_filters"] = list(getattr(tool_context, "identifier_filters", ()))
            if getattr(tool_context, "identifier_hashes", None):
                diagnostics["identifier_hashes"] = dict(getattr(tool_context, "identifier_hashes"))
            gate = getattr(tool_context, "identifier_gate", None)
            if gate:
                try:
                    diagnostics["identifier_gate"] = gate.snapshot()  # type: ignore[attr-defined]
                except Exception:
                    diagnostics["identifier_gate"] = None
        llm_source = "provider"
        if diagnostics.get("llm_strategy"):
            llm_source = str(diagnostics.get("llm_strategy"))
        if on_stream_complete:
            try:
                on_stream_complete()
            except Exception:  # pragma: no cover - defensive
                pass
        return StreamingTurnContext(
            conversation=conversation,
            response_text=clean_answer_text,
            planned_actions=tuple(),
            extractions=tuple(),
            resolved_citations=tuple(self._build_citations(tool_context)) if tool_context else tuple(),
            knowledge_payload=tuple(),
            knowledge_reads=tuple(getattr(tool_context, "knowledge_reads", ())) if tool_context else tuple(),
            knowledge_status=None,
            knowledge_diagnostics=diagnostics,
            knowledge_loading=False,
            placeholder_response=None,
            prompt_bundle=None,
            tool_trace=tuple(getattr(tool_context, "tool_trace", ())) if tool_context else tuple(),
            cached_snippet_count=len(getattr(tool_context, "knowledge_results", ()) or []) if tool_context else 0,
            llm_source=llm_source,
            streamed_chunks=streamed_chunks,
            plan=None,
            tool_context=tool_context,
        )

    def finalize_turn(self, context: StreamingTurnContext) -> AiOrchestratorPlan:
        return context.plan or AiOrchestratorPlan(
            response_text=context.response_text,
            citations=tuple(context.resolved_citations),
            planned_actions=tuple(context.planned_actions),
            extractions=tuple(context.extractions),
            diagnostics=dict(context.knowledge_diagnostics),
            ingestion_warnings=tuple(),
        )

    def run_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_stream_complete: Callable[[], None] | None = None,
    ) -> AiOrchestratorPlan:
        context = self.stream_turn(
            conversation=conversation,
            user_message=user_message,
            on_response_text_delta=on_response_text_delta,
            on_status_change=on_status_change,
            on_placeholder_response=on_placeholder_response,
            on_stream_complete=on_stream_complete,
        )
        return self.finalize_turn(context)

    def _build_plan_from_assistant(
        self,
        *,
        conversation: Conversation,
        assistant_message: Mapping[str, object],
        tool_context: ToolExecutionContext,
        sanitized_dropped: Iterable[str] | None = None,
    ) -> AiOrchestratorPlan:
        response_text = str(assistant_message.get("content") or "").strip()
        planned_actions = self._extract_planned_actions(conversation, assistant_message)
        extractions = self._extract_extractions(assistant_message)
        dropped_list = list(sanitized_dropped or [])
        diagnostics = {
            "llm_strategy": "mcp_tools_stream_planner",
            "knowledge_reads": getattr(tool_context, "knowledge_reads", []),
            "tool_trace": getattr(tool_context, "tool_trace", []),
            "placeholder_response": assistant_message.get("placeholder_response"),
            "coverage_ledger": getattr(tool_context, "coverage_ledger", []),
            "sanitized_sentences": {
                "count": len(dropped_list),
                "examples": dropped_list[:3],
            },
        }
        if getattr(tool_context, "identifier_gate", None):
            snapshot = None
            try:
                snapshot = tool_context.identifier_gate.snapshot()  # type: ignore[attr-defined]
            except Exception:
                snapshot = None
            if snapshot:
                diagnostics["identifier_gate"] = snapshot
        identifier_checks = getattr(tool_context, "identifier_checks", None)
        if identifier_checks:
            diagnostics["identifier_checks"] = list(identifier_checks)
        identifier_filters = getattr(tool_context, "identifier_filters", None)
        if identifier_filters:
            diagnostics["identifier_filters"] = list(identifier_filters)
        if getattr(tool_context, "identifier_hashes", None):
            diagnostics["identifier_hashes"] = dict(tool_context.identifier_hashes)
        ingestion_warnings = tuple(tool_context.ingestion_warnings)
        return AiOrchestratorPlan(
            response_text=response_text,
            citations=self._build_citations(tool_context),
            planned_actions=planned_actions,
            extractions=extractions,
            diagnostics=diagnostics,
            ingestion_warnings=ingestion_warnings,
        )

    def _extract_planned_actions(
        self,
        conversation: Conversation,
        assistant_message: Mapping[str, object],
    ) -> tuple[PlannedAction, ...]:
        actions_payload = assistant_message.get("actions") or []
        planned: list[PlannedAction] = []
        for item in actions_payload:
            if not isinstance(item, Mapping):
                continue
            action_name = item.get("action")
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
            if not action_name:
                continue
            try:
                action_type = ActionType(action_name)
            except Exception:
                continue
            planned.append(PlannedAction(action=action_type, payload=dict(payload)))
        return tuple(planned)

    def _extract_extractions(self, assistant_message: Mapping[str, object]) -> tuple[ExtractionPlan, ...]:
        extraction_payload = assistant_message.get("extractions") or []
        extractions: list[ExtractionPlan] = []
        for item in extraction_payload:
            if not isinstance(item, Mapping):
                continue
            extraction_type = item.get("type")
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
            try:
                extraction_enum = ConversationExtractionType(extraction_type)
            except Exception:
                continue
            extractions.append(ExtractionPlan(extraction_type=extraction_enum, payload=dict(payload)))
        return tuple(extractions)

    def _build_citations(self, tool_context: ToolExecutionContext) -> tuple[KnowledgeSnippet, ...]:
        citations: list[KnowledgeSnippet] = []
        for entry in getattr(tool_context, "knowledge_results", []):
            try:
                snippet = KnowledgeSnippet(
                    id=uuid.UUID(entry.get("id")) if entry.get("id") else uuid.uuid4(),
                    title=entry.get("title") or "Knowledge",
                    summary=entry.get("summary") or entry.get("content") or "",
                    source=entry.get("source") or "",
                    content=entry.get("content"),
                    content_mode=entry.get("content_mode"),
                    public_label=entry.get("public_label"),
                    structured_tables=entry.get("structuredTables") or (),
                    issues=entry.get("issues") or (),
                    page_summaries=entry.get("pageSummaries") or (),
                    read_state=entry.get("read_state") or "summary",
                    topic_hints=entry.get("topic_hints") or (),
                    is_pinned=bool(entry.get("pin")),
                    supplemental_sections=entry.get("supplemental_sections") or (),
                    upload_id=uuid.UUID(entry["upload_id"]) if entry.get("upload_id") else None,
                    chunk_id=uuid.UUID(entry["chunk_id"]) if entry.get("chunk_id") else None,
                    chunk_index=entry.get("chunk_index"),
                    entity_type=entry.get("entity_type"),
                    entity_name=entry.get("entity_name"),
                    entity_business=entry.get("entity_business"),
                    is_table_chunk=bool(entry.get("is_table_chunk")),
                    aliases=entry.get("aliases") or (),
                    search_stage=entry.get("search_stage"),
                    confidence_score=entry.get("confidence_score"),
                    truncated=bool(entry.get("truncated")),
                    source_diagnostics=entry.get("source_diagnostics") or {},
                    partial_index=bool(entry.get("partial_index")),
                    structured_table_count=entry.get("structured_table_count") or 0,
                    issue_count=entry.get("issue_count") or 0,
                    structured_table_hint=entry.get("structured_table_hint"),
                    page_number=entry.get("page_number"),
                    page_mode=entry.get("page_mode"),
                )
            except Exception:
                continue
            citations.append(snippet)
        return tuple(citations)

    def _run_planner(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        answer_text: str,
        tool_context: ToolExecutionContext | None = None,
        on_status_change: Callable[[str], None] | None = None,
    ) -> dict[str, object] | None:
        """
        Second, non-streaming pass that asks the MCP provider to propose
        actions and extractions in structured JSON form. The streamed
        `answer_text` is treated as the final assistant reply shown to the
        visitor; the planner focuses on backend intents only.
        """

        if not self.provider:
            raise PromptGenerationError("MCP provider is not configured for planning.")

        if on_status_change:
            on_status_change({"code": "planning_actions", "label": "Planning follow-up actions…" })

        planner_messages = prompts.build_planner_messages(
            conversation=conversation,
            user_message=user_message,
            answer_text=answer_text,
            tool_context_note=self._planner_tool_note(tool_context),
            tool_trace=tuple(tool_context.tool_trace),
            coverage_ledger=tuple(tool_context.coverage_ledger),
        )
        self._log_prompt("planner", conversation=conversation, messages=planner_messages)
        payload = self.provider.chat(planner_messages, tools=None, on_stream_delta=None)
        if not isinstance(payload, dict):
            return None
        return self._coerce_assistant_message(payload)

    @staticmethod
    def _merge_planner_into_assistant(
        assistant_message: Mapping[str, object],
        planner_message: Mapping[str, object] | None,
    ) -> Mapping[str, object]:
        """
        Combine the streamed assistant content with planner-produced metadata.

        The assistant `content` (answer text) comes from the streaming pass,
        while `actions`/`extractions` are injected from the planner payload.
        """

        if not planner_message:
            return assistant_message
        merged: dict[str, object] = dict(assistant_message)
        merged.pop("placeholder_response", None)
        if isinstance(planner_message.get("actions"), list):
            merged["actions"] = planner_message.get("actions")
        if isinstance(planner_message.get("extractions"), list):
            merged["extractions"] = planner_message.get("extractions")
        return merged

    def run_planner_only(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        answer_text: str,
        tool_context: ToolExecutionContext | None = None,
        on_status_change: Callable[[str], None] | None = None,
    ) -> AiOrchestratorPlan | None:
        """
        Run planner-only pass using the already streamed answer and tool context.
        """
        if not self.provider:
            return None
        planner_payload: dict[str, object] | None = None
        try:
            planner_payload = self._run_planner(
                conversation=conversation,
                user_message=user_message,
                answer_text=answer_text,
                tool_context=tool_context,
                on_status_change=on_status_change,
            )
        except PromptGenerationError:
            planner_payload = None
        assistant_message = {
            "role": "assistant",
            "content": answer_text,
        }
        merged_assistant = self._merge_planner_into_assistant(assistant_message, planner_payload)
        return self._build_plan_from_assistant(
            conversation=conversation,
            assistant_message=merged_assistant,
            tool_context=tool_context or ToolExecutionContext(),
            sanitized_dropped=(),
        )

    @staticmethod
    def _final_response_schema() -> Mapping[str, object]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "final_response",
                "schema": {
                    "type": "object",
                    "properties": {
                        "response_text": {"type": "string"},
                        "actions": {"type": "array", "items": {"type": "object"}},
                        "extractions": {"type": "array", "items": {"type": "object"}},
                        "placeholder_response": {"type": "string"},
                    },
                    "required": ["response_text"],
                    "additionalProperties": True,
                },
                "strict": False,
            },
        }

    @staticmethod
    def _is_knowledge_tool(name: str) -> bool:
        return name in {"search_knowledge", "read_document"}

    @staticmethod
    def _record_knowledge_outputs(context: ToolExecutionContext, tool_result: Mapping[str, object]) -> None:
        snippets = tool_result.get("snippets") if isinstance(tool_result, Mapping) else None
        if isinstance(snippets, list):
            for entry in snippets:
                if isinstance(entry, Mapping):
                    context.add_knowledge_result(entry)
                    coverage_entry = {
                        "id": entry.get("id"),
                        "title": entry.get("title") or entry.get("public_label") or "Knowledge",
                        "read_state": entry.get("read_state"),
                        "coverage": entry.get("coverage") if isinstance(entry.get("coverage"), (list, tuple)) else (),
                        "search_stage": entry.get("search_stage"),
                        "chunk_id": entry.get("chunk_id"),
                        "upload_id": entry.get("upload_id"),
                    }
                    context.add_coverage_entry(coverage_entry)
        reads = tool_result.get("knowledge_reads") if isinstance(tool_result, Mapping) else None
        if isinstance(reads, list):
            for read in reads:
                if isinstance(read, Mapping):
                    context.add_knowledge_read(read)
        warnings = tool_result.get("ingestion_warnings") if isinstance(tool_result, Mapping) else None
        if isinstance(warnings, list):
            for warning in warnings:
                if isinstance(warning, Mapping):
                    context.add_ingestion_warning(warning)

    @staticmethod
    def _coerce_assistant_message(payload: dict | None) -> dict[str, object]:
        """
        Normalize the provider payload into an assistant-style message dict.

        Supports both OpenAI-style response envelopes and simplified dictionaries.
        """

        if not payload:
            return {}
        if "choices" in payload:
            choices = payload.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                if isinstance(message, dict):
                    msg = dict(message)
                    msg.pop("placeholder_response", None)
                    return msg
        message = payload.get("message")
        if isinstance(message, dict):
            msg = dict(message)
            msg.pop("placeholder_response", None)
            return msg
        return payload

    @staticmethod
    def _planner_tool_note(tool_context: ToolExecutionContext | None) -> str | None:
        if not tool_context:
            return None

        lines: list[str] = []
        reads = getattr(tool_context, "knowledge_reads", [])
        if isinstance(reads, list) and reads:
            display: list[str] = []
            for entry in reads[:6]:
                if not isinstance(entry, Mapping):
                    continue
                label = entry.get("label") or "Knowledge"
                page = entry.get("page")
                mode = entry.get("mode")
                parts = [str(label)]
                if page:
                    parts.append(f"p{page}")
                if mode:
                    parts.append(str(mode))
                display.append(" ".join(parts))
            if display:
                lines.append("Knowledge reads: " + "; ".join(display))

        trace = getattr(tool_context, "tool_trace", [])
        if isinstance(trace, list) and trace:
            constraint = [t for t in trace if isinstance(t, Mapping) and t.get("status") == "constraint_error"]
            throttled = [t for t in trace if isinstance(t, Mapping) and t.get("throttle_notice")]
            if constraint:
                lines.append("Constraint errors: %s (ask for a narrower page/identifier or continue with existing snippets)" % len(constraint))
            if throttled:
                lines.append("Throttled reads: %s (budget low; avoid wide reads and stick to precise pages)" % len(throttled))

        warnings = getattr(tool_context, "ingestion_warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.append(f"Ingestion warnings: {len(warnings)} (content may be partial; avoid guessing missing details)")

        coverage = getattr(tool_context, "coverage_ledger", [])
        if isinstance(coverage, list) and coverage:
            display: list[str] = []
            for entry in coverage[:6]:
                if not isinstance(entry, Mapping):
                    continue
                label = entry.get("label") or entry.get("title") or "Knowledge"
                state = entry.get("read_state") or "summary"
                topics = entry.get("coverage") or ()
                topics_display = ", ".join(topics[:3]) if isinstance(topics, (list, tuple)) else ""
                parts = [str(label), f"state={state}"]
                if topics_display:
                    parts.append(f"topics={topics_display}")
                display.append(" ".join(parts))
            if display:
                lines.append("Coverage ledger: " + "; ".join(display))

        return "\n".join(lines) if lines else None

    @staticmethod
    def _log_turn_metrics(conversation: Conversation, context: ToolExecutionContext) -> None:
        structured_log(
            "mcp",
            "turn.metrics",
            {
                "tools": len(context.tool_trace),
                "knowledge_reads": len(context.knowledge_reads),
                "chunk_reads": context.chunk_reads_used,
                "chunk_pages": context.chunk_pages_used,
                "characters": context.characters_used,
            },
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            logger_obj=logger,
        )

    @staticmethod
    def _message_previews(messages: Sequence[Mapping[str, object]], limit: int = 10) -> list[dict[str, object]]:
        previews: list[dict[str, object]] = []
        for entry in list(messages)[:limit]:
            role = entry.get("role") or "system"
            content = entry.get("content")
            text = ""
            if isinstance(content, list):
                fragments: list[str] = []
                for part in content:
                    if isinstance(part, Mapping):
                        snippet = part.get("text")
                        if isinstance(snippet, str) and snippet.strip():
                            fragments.append(snippet.strip())
                text = " ".join(fragments)
            elif isinstance(content, str):
                text = content
            previews.append(
                {
                    "role": role,
                    "chars": len(text),
                    "preview": text[:200],
                }
            )
        return previews

    def _log_prompt(
        self,
        stage: str,
        *,
        conversation: Conversation,
        messages: Sequence[Mapping[str, object]],
    ) -> None:
        structured_log(
            "mcp",
            f"prompt.{stage}",
            {"messages": self._message_previews(messages)},
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )

    def _business_override(self, business_profile, key: str, default: int | float) -> int | float:
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if not isinstance(overrides, Mapping):
            return default
        value = overrides.get(key)
        if isinstance(value, (int, float)):
            try:
                return type(default)(value)
            except (TypeError, ValueError):
                return default
        return default

    def _char_budget_per_turn(self, business_profile) -> int | None:
        limit = int(self._business_override(business_profile, "char_budget_per_turn", self.default_char_budget_per_turn))
        return limit if limit > 0 else None

    def _char_budget_per_minute(self, business_profile) -> int | None:
        limit = int(
            self._business_override(business_profile, "char_budget_per_minute", self.default_char_budget_per_minute)
        )
        return limit if limit > 0 else None

    def _build_minute_budget_reserver(self, business_profile, limit: int | None):
        if not limit:
            return None
        window = self.char_budget_window_seconds
        cache_key = f"rag:char_minute:{business_profile.id}"

        def _reserve(count: int) -> None:
            if count <= 0:
                return
            current = cache.get(cache_key)
            if current is None:
                if count > limit:
                    raise CharacterBudgetExceeded(
                        f"Per-minute character budget exceeded (requested {count}, max {limit})."
                    )
                cache.set(cache_key, count, timeout=window)
                return
            new_total = int(current) + count
            if new_total > limit:
                raise CharacterBudgetExceeded(
                    f"Per-minute character budget exceeded (requested {new_total}, max {limit})."
                )
            cache.set(cache_key, new_total, timeout=window)

        return _reserve

    @staticmethod
    def _constraint_error_payload(tool_name: str, exc: ToolConstraintError) -> Mapping[str, object]:
        if isinstance(exc, CharacterBudgetExceeded):
            code = "char_budget_exceeded"
            hint = "Character budget exhausted; continue with existing excerpts or respond."
        elif isinstance(exc, ChunkPageBudgetExceeded):
            code = "page_budget_exceeded"
            hint = "Page window budget exhausted; summarize what you already have."
        elif isinstance(exc, ChunkReadBudgetExceeded):
            code = "chunk_read_budget_exceeded"
            hint = "Chunk read budget exhausted; proceed without additional reads."
        else:
            code = "constraint_violation"
            hint = "Constraint violated."
        return {
            "tool": tool_name,
            "status": "constraint_error",
            "error": str(exc),
            "error_code": code,
            "hint": hint,
            "llm_hint": hint,
            "snippets": [],
        }

    @staticmethod
    def _identifier_requirement_message(required_keys: Iterable[str], match_policy: str, hint: str | None) -> str:
        keys = [str(k).strip() for k in required_keys if str(k).strip()]
        if not keys:
            return hint or "I need a verified identifier to continue. Please share the identifier requested for this record."
        keys_text = ", ".join(keys)
        if match_policy == "and" and len(keys) > 1:
            base = f"I need all of these identifiers to continue: {keys_text}."
        else:
            base = f"I need one of these identifiers to continue: {keys_text}."
        if hint:
            return f"{base} {hint}"
        return base

    @staticmethod
    def _tool_name(tool_call: Mapping[str, object]) -> str:
        func = tool_call.get("function")
        if isinstance(func, dict):
            name = func.get("name")
            if isinstance(name, str):
                return name
        value = tool_call.get("name")
        if isinstance(value, str):
            return value
        raise ValueError("Tool call did not include a function name.")

    @staticmethod
    def _tool_arguments(tool_call: Mapping[str, object]) -> dict[str, object]:
        func = tool_call.get("function")
        raw_args = None
        if isinstance(func, dict):
            raw_args = func.get("arguments")
        if raw_args is None:
            raw_args = tool_call.get("arguments")

        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
