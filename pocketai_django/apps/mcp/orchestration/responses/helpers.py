from __future__ import annotations

import json
import logging
import re
from typing import Mapping, MutableMapping, Sequence

from apps.conversations.models import Conversation, ConversationSender
from apps.conversations.response_blocks import normalize_response_blocks
from apps.rag.observability.logging import structured_log

from ...types import ToolExecutionContext


logger = logging.getLogger(__name__)

INLINE_RESPONSE_BLOCK_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:[-*+]\s*)?[\"'`]?response(?:_|\s)?blocks[\"'`]?\s*:?",
    re.IGNORECASE,
)


class McpResponseHelpersMixin:

    @staticmethod
    def _extract_response_blocks(source: Mapping[str, object] | None) -> tuple[dict[str, object], ...]:
        if not isinstance(source, Mapping):
            return tuple()
        block_source = None
        for key in ("response_blocks", "responseBlocks", "response_blocks_json"):
            if key in source and source.get(key) is not None:
                block_source = source.get(key)
                break
        if isinstance(source, MutableMapping):
            if block_source is None:
                inline = McpResponseHelpersMixin._extract_inline_response_blocks(source)
                if inline is not None:
                    block_source = inline
            else:
                # Even when structured blocks exist, strip any inline duplicates from the visible content.
                McpResponseHelpersMixin._extract_inline_response_blocks(source)
        return normalize_response_blocks(block_source)

    @staticmethod
    def _extract_inline_response_blocks(message: MutableMapping[str, object]) -> object | None:
        content = message.get("content")
        if not isinstance(content, str):
            return None
        match = None
        for candidate in INLINE_RESPONSE_BLOCK_PATTERN.finditer(content):
            match = candidate
        if not match:
            return None
        prefix = content[: match.start()]
        suffix = content[match.end():]
        block_source = McpResponseHelpersMixin._parse_inline_block_payload(suffix)
        if block_source is None:
            return None
        message["content"] = prefix.rstrip()
        return block_source

    @staticmethod
    def _parse_inline_block_payload(text: str) -> object | None:
        remainder = text.lstrip()
        if remainder.startswith(":"):
            remainder = remainder[1:].lstrip()
        if remainder.startswith("```"):
            remainder = remainder[3:].lstrip()
            if remainder.lower().startswith("json"):
                remainder = remainder[4:].lstrip()
            fence_end = remainder.find("```")
            snippet = remainder if fence_end < 0 else remainder[:fence_end]
        else:
            snippet = remainder
        snippet = snippet.strip()
        if not snippet:
            return None
        decoder = json.JSONDecoder()
        try:
            parsed, _ = decoder.raw_decode(snippet)
        except ValueError:
            return None
        return parsed


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
                    msg.pop("placeholder_thinking", None)
                    return msg
        message = payload.get("message")
        if isinstance(message, dict):
            msg = dict(message)
            msg.pop("placeholder_response", None)
            msg.pop("placeholder_thinking", None)
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
                if entry.get("suppress_in_prompt"):
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

    def _build_task_summary_note(
        self,
        conversation: Conversation,
        user_message: str,
        context: ToolExecutionContext | None = None,
        *,
        max_anchor_chars: int = 500,
    ) -> str | None:
        """
        Build a pinned task summary for tool-iteration calls.

        Captures the latest user question and the most recent "anchor" customer
        request (long/rich message) so retries don't lose constraints.
        """

        current = (user_message or "").strip()
        if not current:
            return None

        anchor_text: str | None = None
        try:
            recent_messages = list(conversation.messages.order_by("-sent_at", "-created_at")[:20])
        except Exception:
            recent_messages = []

        for entry in recent_messages:
            try:
                if entry.sender != ConversationSender.CUSTOMER:
                    continue
            except Exception:
                continue
            body = (entry.body or "").strip()
            if not body or body == current:
                continue
            lower = body.lower()
            is_anchor = len(body) >= 80 or "\n" in body or "product" in lower or "store" in lower
            if is_anchor:
                anchor_text = body
                break

        def _trim(text: str) -> str:
            trimmed = text.replace("\n", " ").strip()
            if len(trimmed) > max_anchor_chars:
                return trimmed[:max_anchor_chars].rstrip() + "…"
            return trimmed

        lines: list[str] = []
        if anchor_text:
            lines.append(f"Anchor request: {_trim(anchor_text)}")
        lines.append(f"Current question: {_trim(current)}")

        if context:
            pass

        bullets = "\n".join(f"- {line}" for line in lines if line)
        return (
            "Task summary (internal; keep these constraints stable unless the visitor changes them):\n"
            f"{bullets}"
        )

    @staticmethod
    def _tool_loop_note(tool_context: ToolExecutionContext | None) -> str | None:
        """
        Compact ledger of tools executed this turn to ground retries.
        """
        if not tool_context:
            return None
        trace = getattr(tool_context, "tool_trace", [])
        if not isinstance(trace, list) or not trace:
            return None

        def _clean(value: object, limit: int = 120) -> str:
            if value is None:
                return ""
            text = str(value).replace("\n", " ").strip()
            if len(text) > limit:
                return text[:limit].rstrip() + "…"
            return text

        def _clean_list(value: object, *, limit_items: int = 4, per_item: int = 60) -> str:
            if isinstance(value, (list, tuple)):
                items = [_clean(item, per_item) for item in value[:limit_items] if item is not None]
                suffix = "…" if len(value) > limit_items else ""
                return ", ".join(items) + suffix
            return _clean(value)

        lines: list[str] = [
            "Tool ledger this turn (internal; do not repeat identical tools unless the visitor adds a new constraint):"
        ]
        for entry in trace[-6:]:
            if not isinstance(entry, Mapping):
                continue
            tool_name = str(entry.get("tool") or "tool")
            status = str(entry.get("status") or entry.get("error_code") or "").strip()
            args = entry.get("arguments") if isinstance(entry.get("arguments"), Mapping) else {}

            if tool_name == "search_knowledge":
                query = args.get("query") or args.get("queries") or ""
                lines.append(f"- search_knowledge(query={_clean_list(query)}) -> {status or 'done'}")
                continue

            if tool_name == "read_knowledge":
                raw_refs = args.get("refs")
                if not isinstance(raw_refs, list):
                    raw_refs = args.get("items")
                refs: list[str] = []
                if isinstance(raw_refs, list):
                    for ref in raw_refs:
                        if not isinstance(ref, Mapping):
                            continue
                        ref_id = ref.get("id") or ref.get("ref")
                        if isinstance(ref_id, str) and ref_id.strip():
                            refs.append(ref_id.strip())
                mode = args.get("mode") or ""
                max_chars = args.get("max_chars")
                parts: list[str] = []
                if refs:
                    parts.append(f"refs={_clean_list(refs, limit_items=5, per_item=40)}")
                if mode:
                    parts.append(f"mode={_clean(mode, 20)}")
                if max_chars is not None:
                    parts.append(f"max_chars={_clean(max_chars, 10)}")
                detail = ", ".join(parts)
                lines.append(f"- read_knowledge({detail}) -> {status or 'done'}")
                continue

            lines.append(f"- {tool_name} -> {status or 'done'}")

        return "\n".join(lines)

    @staticmethod
    def _evidence_summary_note(tool_context: ToolExecutionContext | None) -> str | None:
        """
        Compact evidence summary to survive prompt budget trimming.

        The context governor may drop tool payloads (and even the transcript) to
        fit token limits, which can cause the model to re-run expensive tools.
        This note keeps the best snippet summaries "sticky" as system context so
        the model can answer without repeating search_knowledge.
        """

        if not tool_context:
            return None
        results = getattr(tool_context, "knowledge_results", None) or []
        if not isinstance(results, list) or not results:
            return None

        def _clean(value: object, limit: int) -> str:
            if value is None:
                return ""
            text = str(value).replace("\n", " ").strip()
            if not text:
                return ""
            if len(text) > limit:
                return text[:limit].rstrip() + "…"
            return text

        seen: set[tuple[str, str]] = set()
        summaries: list[str] = []
        for entry in results:
            if not isinstance(entry, Mapping):
                continue
            chunk_id = str(entry.get("chunk_id") or entry.get("id") or "").strip()
            upload_id = str(entry.get("upload_id") or "").strip()
            key = (chunk_id, upload_id)
            if key in seen:
                continue
            seen.add(key)
            summary = entry.get("summary") or entry.get("content") or ""
            summary_text = _clean(summary, 360)
            if not summary_text:
                continue
            read_hint = entry.get("read_hint") if isinstance(entry.get("read_hint"), Mapping) else {}
            doc_id = str(read_hint.get("document_id") or "").strip()
            page = read_hint.get("page")
            mode = str(read_hint.get("mode") or "").strip()
            hint_bits: list[str] = []
            if doc_id:
                hint_bits.append(f"document_id={doc_id[:40]}")
            if page:
                hint_bits.append(f"page={page}")
            if mode:
                hint_bits.append(f"mode={mode}")
            if hint_bits:
                summary_text = f"{summary_text} (read_hint: {', '.join(hint_bits)})"
            summaries.append(summary_text)
            if len(summaries) >= 4:
                break

        if not summaries:
            return None

        lines = [
            "Evidence summary (system-only): Ground the answer in this evidence; do NOT re-run search_knowledge with new queries just to double-check.",
            "If factual detail is still missing, do one targeted read_knowledge call with existing ref IDs/cursors before finalizing.",
            "If you need more results, prefer paging with next_cursor (if available) instead of repeating the same search.",
            "If you need more detail, use read_knowledge with the ref IDs (and cursors if provided). Never invent IDs/cursors.",
            "Do NOT include document names/IDs/pages in the user-facing answer.",
        ]
        for idx, summary in enumerate(summaries, start=1):
            lines.append(f"{idx}. {summary}")
        return "\n".join(lines)

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
