from __future__ import annotations

import uuid
from typing import Callable, Iterable, Mapping, Sequence

from apps.conversations.models import Conversation, ConversationExtractionType
from apps.conversations.response_blocks import normalize_response_blocks
from apps.llm.llm_provider import PromptGenerationError
from apps.rag.contracts import (
    ActionType,
    AiOrchestratorPlan,
    ExtractionPlan,
    KnowledgeSnippet,
    PlannedAction,
    StreamingTurnContext,
)

from core.otel import otel_trace

from ... import prompts
from ...types import ToolExecutionContext


TRACER = otel_trace.get_tracer(__name__)


class McpPlanningMixin:


    def finalize_turn(self, context: StreamingTurnContext) -> AiOrchestratorPlan:
        return context.plan or AiOrchestratorPlan(
            response_text=context.response_text,
            citations=tuple(context.resolved_citations),
            planned_actions=tuple(context.planned_actions),
            extractions=tuple(context.extractions),
            diagnostics=dict(context.knowledge_diagnostics),
            ingestion_warnings=tuple(),
            response_blocks=tuple(context.response_blocks),
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
        on_spinner_update: Callable[[str], None] | None = None,
    ) -> AiOrchestratorPlan:
        context = self.stream_turn(
            conversation=conversation,
            user_message=user_message,
            on_response_text_delta=on_response_text_delta,
            on_status_change=on_status_change,
            on_placeholder_response=on_placeholder_response,
            on_stream_complete=on_stream_complete,
            on_spinner_update=on_spinner_update,
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
            "knowledge_results": getattr(tool_context, "knowledge_results", []),
            "tool_trace": getattr(tool_context, "tool_trace", []),
            "placeholder_response": assistant_message.get("placeholder_thinking")
            or assistant_message.get("placeholder_response"),
            "coverage_ledger": getattr(tool_context, "coverage_ledger", []),
            "retrieval_candidates": getattr(tool_context, "retrieval_candidates", []),
            "model_visible_refs": getattr(tool_context, "model_visible_refs", []),
            "read_evidence": getattr(tool_context, "read_evidence", []),
            "sanitized_sentences": {
                "count": len(dropped_list),
                "examples": dropped_list[:3],
            },
        }
        block_source = None
        for key in ("response_blocks", "responseBlocks", "response_blocks_json"):
            if isinstance(assistant_message, Mapping) and key in assistant_message:
                candidate = assistant_message.get(key)
                if candidate is not None:
                    block_source = candidate
                    break
        response_blocks = normalize_response_blocks(block_source)
        ingestion_warnings = tuple(tool_context.ingestion_warnings)
        return AiOrchestratorPlan(
            response_text=response_text,
            citations=self._build_citations(tool_context),
            planned_actions=planned_actions,
            extractions=extractions,
            diagnostics=diagnostics,
            ingestion_warnings=ingestion_warnings,
            response_blocks=response_blocks,
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
        try:
            total_entries = len(extraction_payload)
        except TypeError:
            total_entries = 0
        anomalies = {"non_mapping": 0, "unknown_type": 0}
        extractions: list[ExtractionPlan] = []
        with TRACER.start_as_current_span("portal.mcp.validate_extractions") as span:
            for item in extraction_payload:
                if not isinstance(item, Mapping):
                    anomalies["non_mapping"] += 1
                    continue
                extraction_type = item.get("type")
                payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                try:
                    extraction_enum = ConversationExtractionType(extraction_type)
                except Exception:
                    anomalies["unknown_type"] += 1
                    continue
                extractions.append(ExtractionPlan(extraction_type=extraction_enum, payload=dict(payload)))
            if span.is_recording():
                span.set_attribute("extractions.total", total_entries)
                span.set_attribute("extractions.valid", len(extractions))
                span.set_attribute("extractions.anomaly.non_mapping", anomalies["non_mapping"])
                span.set_attribute("extractions.anomaly.unknown_type", anomalies["unknown_type"])
        return tuple(extractions)

    def _build_citations(self, tool_context: ToolExecutionContext) -> tuple[KnowledgeSnippet, ...]:
        citations: list[KnowledgeSnippet] = []
        for entry in getattr(tool_context, "knowledge_results", []):
            if entry.get("suppress_in_prompt"):
                continue
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
        Second, non-streaming postflight pass that asks the MCP provider to
        propose backend actions/extractions AND run a lightweight verification
        check. The streamed `answer_text` is treated as the final assistant
        reply shown to the visitor; this pass is for backend intents +
        diagnostics only.
        """

        if not self.provider:
            raise PromptGenerationError("MCP provider is not configured for planning.")

        if on_status_change:
            on_status_change({"code": "planning_actions", "label": "Planning follow-up actions…" })

        evidence_note = self._evidence_summary_note(tool_context)
        planner_messages = prompts.build_planner_messages(
            conversation=conversation,
            user_message=user_message,
            answer_text=answer_text,
            tool_context_note=self._planner_tool_note(tool_context),
            tool_trace=tuple(tool_context.tool_trace),
            coverage_ledger=tuple(tool_context.coverage_ledger),
            evidence_note=evidence_note,
        )
        self._log_prompt("postflight", conversation=conversation, messages=planner_messages)
        payload = self._chat_with_context_governor(
            conversation=conversation,
            stage="postflight",
            messages=planner_messages,
            tools=None,
            on_stream_delta=None,
            response_format=self._final_response_schema(),
            tool_context=tool_context,
        )
        if not isinstance(payload, dict):
            return None
        return self._coerce_assistant_message(payload)

    def _run_preplan(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        recent_history: Sequence[Mapping[str, object]] | None = None,
        tool_context: ToolExecutionContext | None = None,
    ) -> dict[str, object] | None:
        if not self.provider:
            return None
        preplan_messages = prompts.build_preplan_messages(
            conversation=conversation,
            user_message=user_message,
            recent_history=recent_history,
        )
        self._log_prompt("preplan", conversation=conversation, messages=preplan_messages)
        payload = self._chat_with_context_governor(
            conversation=conversation,
            stage="preplan",
            messages=preplan_messages,
            tools=None,
            on_stream_delta=None,
            response_format=self._final_response_schema(),
            tool_context=tool_context,
        )
        if not isinstance(payload, dict):
            return None
        return self._coerce_assistant_message(payload)

    def _run_verification(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        draft_answer: str,
        tool_context: ToolExecutionContext | None = None,
    ) -> dict[str, object] | None:
        if not self.provider:
            return None
        evidence_note = self._evidence_summary_note(tool_context)
        verification_messages = prompts.build_verification_messages(
            conversation=conversation,
            user_message=user_message,
            draft_answer=draft_answer,
            evidence_note=evidence_note,
        )
        self._log_prompt("verification", conversation=conversation, messages=verification_messages)
        payload = self._chat_with_context_governor(
            conversation=conversation,
            stage="verification",
            messages=verification_messages,
            tools=None,
            on_stream_delta=None,
            response_format=self._final_response_schema(),
            tool_context=tool_context,
        )
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
        merged.pop("placeholder_thinking", None)
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
        tool_ctx = tool_context or ToolExecutionContext()
        planner_payload: dict[str, object] | None = None
        try:
            planner_payload = self._run_planner(
                conversation=conversation,
                user_message=user_message,
                answer_text=answer_text,
                tool_context=tool_ctx,
                on_status_change=on_status_change,
            )
        except PromptGenerationError:
            planner_payload = None
        if planner_payload:
            verification_payload = self._parse_verification_payload(planner_payload)
            if verification_payload:
                tool_ctx.verification = dict(verification_payload)
            else:
                raw_postflight = str(planner_payload.get("content") or "").strip()
                if raw_postflight:
                    tool_ctx.verification = {
                        "verdict": "parse_error",
                        "missing_points": [],
                        "final_response": "",
                        "notes": self._clip_text(raw_postflight, 320),
                    }
        assistant_message = {
            "role": "assistant",
            "content": answer_text,
        }
        merged_assistant = self._merge_planner_into_assistant(assistant_message, planner_payload)
        return self._build_plan_from_assistant(
            conversation=conversation,
            assistant_message=merged_assistant,
            tool_context=tool_ctx,
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
                        # Optional structured UI blocks (preferred over markdown tables).
                        "response_blocks": {"type": "array", "items": {"type": "object"}},
                        "actions": {"type": "array", "items": {"type": "object"}},
                        "extractions": {"type": "array", "items": {"type": "object"}},
                        "placeholder_response": {"type": "string"},
                        "placeholder_thinking": {"type": "string"},
                    },
                    "required": ["response_text"],
                    "additionalProperties": True,
                },
                "strict": False,
            },
        }

    @staticmethod
    def _parse_json_blob(text: str) -> Mapping[str, object] | None:
        """
        Parse a JSON object from a model response.

        Models sometimes wrap JSON in markdown fences or add extra prose.
        This helper is intentionally tolerant so preplan/verification payloads
        can still be recovered.
        """

        if not text:
            return None

        candidate = str(text).strip()
        if not candidate:
            return None

        # Unwrap ```json ... ``` fences when present.
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
        if fence_match:
            candidate = fence_match.group(1).strip()

        def _try_parse(blob: str) -> Mapping[str, object] | None:
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, Mapping) else None

        parsed = _try_parse(candidate)
        if parsed:
            return parsed

        # Best-effort: extract the first {...} region and try again.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = _try_parse(candidate[start : end + 1])
            if parsed:
                return parsed

        return None

    def _parse_preplan_payload(self, message: Mapping[str, object]) -> dict[str, object] | None:
        raw = str(message.get("content") or "").strip()
        parsed = self._parse_json_blob(raw)
        if not parsed:
            return None
        route = str(parsed.get("route") or "").strip().lower()
        if route and route not in {"search", "read", "answer"}:
            route = ""
        tools = parsed.get("tools")
        tool_list: list[str] = []
        if isinstance(tools, list):
            for entry in tools:
                if isinstance(entry, str) and entry.strip():
                    tool_list.append(entry.strip())
        return {
            "route": route,
            "search_query": str(parsed.get("search_query") or "").strip(),
            "tools": tool_list,
            "clarifying_question": str(parsed.get("clarifying_question") or "").strip(),
            "notes": str(parsed.get("notes") or "").strip(),
        }

    def _parse_verification_payload(self, message: Mapping[str, object]) -> dict[str, object] | None:
        raw = str(message.get("content") or "").strip()
        parsed = self._parse_json_blob(raw)
        if not parsed:
            return None
        verdict = str(parsed.get("verdict") or "").strip().lower()
        if verdict and verdict not in {"supported", "needs_clarification", "unsupported"}:
            verdict = ""
        missing_points = parsed.get("missing_points")
        missing: list[str] = []
        if isinstance(missing_points, list):
            for entry in missing_points:
                if isinstance(entry, str) and entry.strip():
                    missing.append(entry.strip())
        return {
            "verdict": verdict,
            "missing_points": missing,
            "final_response": str(parsed.get("final_response") or "").strip(),
            "notes": str(parsed.get("notes") or "").strip(),
        }
