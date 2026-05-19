"""
Active MCP portal orchestrator.

This module owns the portal tool-calling runtime, including planning,
tool dispatch, streaming events, approvals, and response assembly.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from typing import Callable, Mapping

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import AgentProfile
from apps.conversations.models import (
    Conversation,
)
from apps.llm.llm_provider import _emit_stream_chunks
from apps.rag.contracts import StreamingTurnContext
from apps.rag.rag_logging import structured_log

from . import tools as mcp_tools
from .orchestrator_approval_policy import McpApprovalPolicyMixin
from .orchestrator_knowledge_context import McpKnowledgeContextMixin
from .orchestrator_planning import McpPlanningMixin
from .orchestrator_prompt_governor import McpPromptGovernorMixin
from .orchestrator_response_helpers import McpResponseHelpersMixin
from .orchestrator_remote_tools import McpRemoteToolsMixin
from .orchestrator_runtime_controls import McpRuntimeControlsMixin
from .orchestrator_tool_schema import McpToolSchemaMixin
from .orchestrator_turn_execution import McpTurnExecutionMixin

from .types import (
    BaseMcpProvider,
)


logger = logging.getLogger(__name__)


class McpOrchestratorService(
    McpApprovalPolicyMixin,
    McpKnowledgeContextMixin,
    McpPlanningMixin,
    McpPromptGovernorMixin,
    McpResponseHelpersMixin,
    McpRemoteToolsMixin,
    McpRuntimeControlsMixin,
    McpToolSchemaMixin,
    McpTurnExecutionMixin,
):
    """
    Primary portal orchestration service.

    The public API is intentionally small because portal turns construct this
    service directly and consume its streaming turn result.
    """

    def __init__(self, *, agent: AgentProfile, provider: BaseMcpProvider | None) -> None:
        self.agent = agent
        self.provider = provider
        self.tool_definitions = mcp_tools.get_tool_definitions()
        self._remote_tool_registry: dict[str, tuple[object, str]] = {}
        self._tool_approval_overrides_cache: dict[str, dict[str, str]] = {}
        self.max_tool_iterations = int(getattr(settings, "MCP_MAX_TOOL_ITERATIONS", 10))
        self.business_override_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
        # Increased from 3 to 8 to allow richer context for table-heavy documents
        default_chunk_reads = max(1, int(getattr(settings, "RAG_MAX_CHUNK_READS_PER_TURN", 8)))
        self.max_chunk_reads_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_reads_per_turn", default_chunk_reads)),
        )
        # Increased from 3 to 8 to allow reading more pages per turn
        default_page_windows = max(1, int(getattr(settings, "RAG_MAX_CHUNK_PAGES_PER_TURN", 8)))
        self.max_chunk_pages_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_pages_per_turn", default_page_windows)),
        )
        # Increased from 48000 to 64000 to accommodate richer table content
        self.default_char_budget_per_turn = max(
            4000,
            int(getattr(settings, "RAG_MAX_CHAR_BUDGET_PER_TURN", 64000)),
        )
        self.default_char_budget_per_minute = max(
            4000,
            int(getattr(settings, "RAG_MAX_CHAR_BUDGET_PER_MINUTE", 64000)),
        )
        self.char_budget_window_seconds = max(30, int(getattr(settings, "RAG_CHAR_BUDGET_WINDOW_SECONDS", 60)))

    def _deepseek_reasoner_tool_loop_enabled(self) -> bool:
        """
        DeepSeek thinking-mode tool loops require assistant messages to include
        `reasoning_content` when continuing a tool call chain.
        """

        provider = self.provider
        if not provider:
            return False
        if "deepseek" not in provider.__class__.__name__.lower():
            return False
        model = getattr(provider, "model", None)
        if not isinstance(model, str):
            return False
        return "deepseek-reasoner" in model.lower()

    _LOW_INTENT_PATTERNS = (
        re.compile(r"^(hi|hello|hey|hola|hallo|مرحبا|السلام عليكم|as-salamu alaykum)\\b", re.IGNORECASE),
        re.compile(r"^(good\\s+(morning|evening|afternoon|day|night))\\b", re.IGNORECASE),
        re.compile(r"^(thanks|thank you|gracias|shukran|شكرا)\\b", re.IGNORECASE),
        re.compile(r"^(test|testing)\\b", re.IGNORECASE),
    )
    _LOW_INTENT_SIMPLE = {
        "hi",
        "hello",
        "hey",
        "hola",
        "مرحبا",
        "salam",
        "salaam",
        "as-salamu alaykum",
        "thanks",
        "thank you",
        "gracias",
        "شكرا",
        "test",
        "testing",
    }

    @classmethod
    def _is_low_intent_message(cls, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        if len(normalized) > 80:
            return False
        lowered = normalized.lower()
        if any(ch.isdigit() for ch in lowered):
            return False
        if "@" in lowered:
            return False
        if lowered in cls._LOW_INTENT_SIMPLE:
            return True
        return any(pattern.match(lowered) for pattern in cls._LOW_INTENT_PATTERNS)


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
        user_metadata: Mapping[str, object] | None = None,
        allowed_tools: set[str] | None = None,
        wait_for_tool_approval: bool = True,
        portal_emit_blocks_enabled: bool = True,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_stream_complete: Callable[[], None] | None = None,
        on_spinner_update: Callable[[str], None] | None = None,
        on_tool_event: Callable[[Mapping[str, object]], None] | None = None,
        on_tool_decision: Callable[[str], None] | None = None,
        on_block_event: Callable[[Mapping[str, object]], None] | None = None,
        on_reasoning_event: Callable[[Mapping[str, object]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> StreamingTurnContext:
        turn_start = time.perf_counter()
        result = self._execute_turn(
            conversation=conversation,
            user_message=user_message,
            user_metadata=user_metadata,
            allowed_tools=allowed_tools,
            wait_for_tool_approval=wait_for_tool_approval,
            portal_emit_blocks_enabled=portal_emit_blocks_enabled,
            on_response_text_delta=on_response_text_delta,
            on_status_change=on_status_change,
            on_placeholder_response=on_placeholder_response,
            on_spinner_update=on_spinner_update,
            on_tool_event=on_tool_event,
            on_tool_decision=on_tool_decision,
            on_block_event=on_block_event,
            on_reasoning_event=on_reasoning_event,
            should_cancel=should_cancel,
        )
        turn_duration_ms = int((time.perf_counter() - turn_start) * 1000.0)
        streamed_chunks = tuple(result.get("streamed_chunks") or ())
        clean_answer_text = str(result.get("clean_answer_text") or "")
        tool_context = result.get("tool_context")
        assistant_message = result.get("assistant_message") or {}
        response_blocks = tuple(result.get("response_blocks") or ())
        if not streamed_chunks and clean_answer_text:
            reconstructed: list[str] = []
            logger.debug(
                "mcp.stream_turn.fallback_emit",
                extra={
                    "conversation_id": str(conversation.id),
                    "strategy": str(result.get("llm_strategy") or ""),
                },
            )
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
            trace_entries = list(getattr(tool_context, "tool_trace", ()))
            diagnostics["tool_trace"] = trace_entries
            diagnostics["coverage_ledger"] = list(getattr(tool_context, "coverage_ledger", ()))
            diagnostics["knowledge_reads"] = list(getattr(tool_context, "knowledge_reads", ()))
            diagnostics["knowledge_results"] = list(getattr(tool_context, "knowledge_results", ()))
            diagnostics["retrieval_candidates"] = list(getattr(tool_context, "retrieval_candidates", ()))
            diagnostics["model_visible_refs"] = list(getattr(tool_context, "model_visible_refs", ()))
            diagnostics["read_evidence"] = list(getattr(tool_context, "read_evidence", ()))
            if getattr(tool_context, "preplan", None):
                diagnostics["preplan"] = dict(getattr(tool_context, "preplan") or {})
            if getattr(tool_context, "verification", None):
                diagnostics["verification"] = dict(getattr(tool_context, "verification") or {})
            if trace_entries:
                tool_metrics: dict[str, dict[str, float | int]] = {}
                for entry in trace_entries:
                    tool_name = entry.get("tool")
                    if not tool_name:
                        continue
                    bucket = tool_metrics.setdefault(
                        tool_name,
                        {"count": 0, "cache_hits": 0, "total_ms": 0.0},
                    )
                    bucket["count"] += 1
                    if entry.get("cache_hit"):
                        bucket["cache_hits"] += 1
                    duration = entry.get("duration_ms")
                    if isinstance(duration, (int, float)):
                        bucket["total_ms"] += max(0.0, float(duration))
                diagnostics["tool_metrics"] = [
                    {
                        "tool": name,
                        "count": stats["count"],
                        "cache_hits": stats["cache_hits"],
                        "total_ms": round(stats["total_ms"], 2),
                    }
                    for name, stats in tool_metrics.items()
                ]

            tool_calls = len(trace_entries)
            tool_total_ms = 0.0
            tool_errors = 0
            throttle_hits = 0
            error_code_counts: dict[str, int] = {}
            for entry in trace_entries:
                if not isinstance(entry, Mapping):
                    continue
                duration = entry.get("duration_ms")
                if isinstance(duration, (int, float)):
                    tool_total_ms += max(0.0, float(duration))
                status_value = str(entry.get("status") or "").strip().lower()
                if status_value in {"error", "constraint_error"}:
                    tool_errors += 1
                if entry.get("throttle_notice"):
                    throttle_hits += 1
                error_code = entry.get("error_code")
                if error_code:
                    key = str(error_code)
                    error_code_counts[key] = error_code_counts.get(key, 0) + 1

            warn_ms = int(getattr(settings, "MCP_SLO_TURN_WARN_MS", 15000) or 0)
            warn_tools = int(getattr(settings, "MCP_SLO_TOOL_CALLS_WARN", 6) or 0)
            slow_turn = bool(warn_ms and turn_duration_ms >= warn_ms)
            noisy_tools = bool(warn_tools and tool_calls >= warn_tools)
            structured_log(
                "mcp",
                "turn.summary",
                {
                    "llm_strategy": strategy,
                    "duration_ms": turn_duration_ms,
                    "answer_chars": len(clean_answer_text),
                    "streamed_chunks": len(streamed_chunks),
                    "tools": tool_calls,
                    "tool_total_ms": round(tool_total_ms, 2),
                    "tool_errors": tool_errors,
                    "throttle_hits": throttle_hits,
                    "error_codes": error_code_counts,
                    "char_budget_turn": tool_context.char_budget_per_turn,
                    "char_budget_minute": tool_context.char_budget_per_minute,
                    "char_used": tool_context.characters_used,
                    "chunk_reads_used": tool_context.chunk_reads_used,
                    "chunk_pages_used": tool_context.chunk_pages_used,
                    "llm_prompt_tokens": int(tool_context.llm_usage.get("prompt_tokens", 0) or 0),
                    "llm_completion_tokens": int(tool_context.llm_usage.get("completion_tokens", 0) or 0),
                    "llm_total_tokens": int(tool_context.llm_usage.get("total_tokens", 0) or 0),
                    "slo": "slow" if slow_turn else None,
                    "slo_warn_ms": warn_ms if slow_turn else None,
                    "slo_tool_calls_warn": warn_tools if noisy_tools else None,
                },
                context={
                    "business": conversation.business_profile_id,
                    "conversation": conversation.id,
                },
                logger_obj=logger,
                level=logging.WARNING if slow_turn or noisy_tools or tool_errors else logging.INFO,
            )
        llm_source = "provider"
        if diagnostics.get("llm_strategy"):
            llm_source = str(diagnostics.get("llm_strategy"))
        if on_stream_complete:
            try:
                on_stream_complete()
            except Exception:  # pragma: no cover - defensive
                pass

        if getattr(settings, "MCP_COMPACTION_ENABLED", True):
            # Phase 6: enqueue durable background work (no inline threads).
            try:
                from apps.conversations.compaction_service import ContextCompactionService
                from apps.conversations.maintenance_job_processing import enqueue_compaction_job

                compaction_service = ContextCompactionService()
                if compaction_service.should_compact(conversation):
                    # If the conversation isn't safe yet (pending approvals/active runs),
                    # schedule the first attempt a bit later to avoid worker thrash.
                    run_after = timezone.now()
                    safe_to_compact = compaction_service.is_safe_to_compact(conversation)
                    if not safe_to_compact:
                        try:
                            delay_seconds = float(getattr(settings, "MCP_COMPACTION_UNSAFE_BACKOFF_SECONDS", 60.0) or 60.0)
                        except (TypeError, ValueError):
                            delay_seconds = 60.0
                        run_after = timezone.now() + timedelta(seconds=max(1.0, delay_seconds))
                    job = enqueue_compaction_job(conversation, run_after=run_after)
                    try:
                        delay_s = max(0.0, float((run_after - timezone.now()).total_seconds()))
                    except Exception:
                        delay_s = 0.0
                    structured_log(
                        "mcp",
                        "compaction.enqueue",
                        {
                            "enqueued": bool(job),
                            "job_id": str(getattr(job, "id", "") or "") if job else None,
                            "safe_to_compact": bool(safe_to_compact),
                            "delay_seconds": round(delay_s, 2) if delay_s else 0,
                        },
                        context={
                            "business": conversation.business_profile_id,
                            "conversation": conversation.id,
                        },
                        logger_obj=logger,
                        level=logging.INFO,
                    )
            except Exception:  # pragma: no cover - defensive
                logger.exception("mcp.compaction.trigger_failed", extra={"conversation_id": str(conversation.id)})
        llm_usage = None
        if tool_context and isinstance(getattr(tool_context, "llm_usage", None), Mapping):
            usage_totals = dict(tool_context.llm_usage)
            entries = list(getattr(tool_context, "llm_usage_entries", []) or [])
            if usage_totals.get("total_tokens") or entries:
                llm_usage = {
                    "prompt_tokens": int(usage_totals.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(usage_totals.get("completion_tokens", 0) or 0),
                    "total_tokens": int(usage_totals.get("total_tokens", 0) or 0),
                    "calls": entries,
                }
        visible_knowledge: tuple[dict[str, object], ...] = tuple()
        if tool_context:
            entries = []
            for item in getattr(tool_context, "knowledge_results", []):
                if not isinstance(item, Mapping):
                    continue
                if item.get("suppress_in_prompt"):
                    continue
                entries.append(dict(item))
            visible_knowledge = tuple(entries)

        return StreamingTurnContext(
            conversation=conversation,
            response_text=clean_answer_text,
            planned_actions=tuple(),
            extractions=tuple(),
            resolved_citations=tuple(self._build_citations(tool_context)) if tool_context else tuple(),
            knowledge_payload=visible_knowledge,
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
            llm_usage=llm_usage,
            plan=None,
            tool_context=tool_context,
            response_blocks=response_blocks,
        )
