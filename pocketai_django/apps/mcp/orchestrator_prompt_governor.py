from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Iterable, Mapping, Sequence

from django.conf import settings

from apps.conversations.models import Conversation
from apps.llm.llm_provider import PromptGenerationError
from apps.rag.rag_logging import structured_log

from . import prompts
from .orchestrator_prompt_tool_compaction import McpPromptToolCompactionMixin
from .types import ToolExecutionContext


logger = logging.getLogger(__name__)


class McpPromptGovernorMixin(McpPromptToolCompactionMixin):


    @staticmethod
    def _estimate_request_tokens(
        *,
        messages: Sequence[Mapping[str, object]],
        tools: Iterable[Mapping[str, object]] | None,
        response_format: Mapping[str, object] | None,
    ) -> dict[str, int]:
        def _dump_len(obj: object) -> int:
            try:
                return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))
            except Exception:
                return len(str(obj))

        message_chars = 0
        for msg in messages:
            if not isinstance(msg, Mapping):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, Mapping):
                        text = part.get("text")
                        if isinstance(text, str):
                            message_chars += len(text)
            elif isinstance(content, str):
                message_chars += len(content)
            reasoning_content = msg.get("reasoning_content")
            if isinstance(reasoning_content, str):
                message_chars += len(reasoning_content)
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes, bytearray)):
                message_chars += _dump_len(tool_calls)
            name = msg.get("name")
            if isinstance(name, str):
                message_chars += len(name)
            role = msg.get("role")
            if isinstance(role, str):
                message_chars += len(role)
            message_chars += 12

        tool_chars = _dump_len(list(tools)) if tools else 0
        response_chars = _dump_len(response_format) if response_format else 0
        total_chars = message_chars + tool_chars + response_chars
        padded_chars = int(total_chars * 1.2)
        tokens_est = (padded_chars + 3) // 4 if padded_chars else 0
        return {
            "message_chars": message_chars,
            "tool_chars": tool_chars,
            "response_format_chars": response_chars,
            "total_chars": total_chars,
            "tokens_est": tokens_est,
        }

    @staticmethod
    def _clip_text(value: object, limit: int) -> str:
        if value is None:
            return ""
        text = str(value)
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _safe_int_setting(value: object, default: int) -> int:
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def _tool_output_max_chars(self) -> int:
        default = 12000
        limit = self._safe_int_setting(getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", default), default)
        if limit <= 0:
            return 0
        # Ensure we can always return a valid JSON tool payload.
        return max(200, limit)

    def _truncate_tool_message_for_prompt(self, tool_name: str, content: str) -> str:
        limit = self._tool_output_max_chars()
        if not limit or limit <= 0:
            return content
        if not isinstance(content, str):
            content = str(content)
        if len(content) <= limit:
            return content

        try:
            parsed = json.loads(content)
        except Exception:
            parsed = None

        if isinstance(parsed, Mapping):
            base_payload: dict[str, object] = dict(parsed)
        else:
            base_payload = {"tool": tool_name, "result": parsed if parsed is not None else content}

        if "tool" not in base_payload:
            base_payload["tool"] = tool_name
        base_payload["truncated"] = True
        base_payload["prompt_compact"] = True

        def _shrink(value: object, *, max_field: int, max_list_items: int) -> object:
            if value is None:
                return None
            if isinstance(value, str):
                trimmed = value.strip()
                return self._clip_text(trimmed, max_field) if trimmed else ""
            if isinstance(value, Mapping):
                return {k: _shrink(v, max_field=max_field, max_list_items=max_list_items) for k, v in value.items()}
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return [
                    _shrink(item, max_field=max_field, max_list_items=max_list_items)
                    for item in list(value)[:max(0, max_list_items)]
                ]
            return value

        for max_list_items in (50, 20, 10, 6, 3, 1):
            for max_field in (10_000, 6_000, 3_000, 1_500, 800, 400, 200, 120):
                candidate = _shrink(base_payload, max_field=max_field, max_list_items=max_list_items)
                try:
                    blob = json.dumps(candidate, ensure_ascii=False, default=str)
                except Exception:
                    continue
                if len(blob) <= limit:
                    return blob

        status = base_payload.get("status")
        error_code = base_payload.get("error_code")
        error = base_payload.get("error")
        hint = base_payload.get("hint")
        fallback: dict[str, object] = {"tool": tool_name, "truncated": True, "prompt_compact": True}
        if status is not None:
            fallback["status"] = str(status)[:60]
        if error_code is not None:
            fallback["error_code"] = str(error_code)[:80]
        if error is not None:
            fallback["error"] = self._clip_text(error, 240)
        if hint is not None:
            fallback["hint"] = self._clip_text(hint, 240)

        preview_budget = max(0, limit - 200)
        if preview_budget:
            fallback["preview"] = self._clip_text(content, preview_budget)
        blob = json.dumps(fallback, ensure_ascii=False, default=str)
        if len(blob) <= limit:
            return blob
        minimal = json.dumps({"tool": tool_name, "truncated": True, "prompt_compact": True}, ensure_ascii=False)
        return minimal if len(minimal) <= limit else minimal[:limit]

    @staticmethod
    def _is_memory_system_message(entry: Mapping[str, object]) -> bool:
        content = entry.get("content")
        if not isinstance(content, str):
            return False
        text = content.strip()
        if not text:
            return False
        if text.startswith("Conversation memory"):
            return True
        return "<memory_summary>" in text

    def _estimate_prompt_breakdown(
        self,
        *,
        messages: Sequence[Mapping[str, object]],
        tools: Iterable[Mapping[str, object]] | None,
        response_format: Mapping[str, object] | None,
    ) -> dict[str, object]:
        total_size = self._estimate_request_tokens(messages=messages, tools=tools, response_format=response_format)
        total_chars = int(total_size.get("total_chars") or 0)
        tokens_est = int(total_size.get("tokens_est") or 0)

        system_messages: list[Mapping[str, object]] = []
        memory_messages: list[Mapping[str, object]] = []
        tool_messages: list[Mapping[str, object]] = []
        history_messages: list[Mapping[str, object]] = []

        for entry in messages:
            role = entry.get("role")
            if role == "tool":
                tool_messages.append(entry)
                continue
            if role == "system":
                if self._is_memory_system_message(entry):
                    memory_messages.append(entry)
                else:
                    system_messages.append(entry)
                continue
            history_messages.append(entry)

        system_chars = self._estimate_request_tokens(messages=system_messages, tools=None, response_format=None)["message_chars"]
        memory_chars = self._estimate_request_tokens(messages=memory_messages, tools=None, response_format=None)["message_chars"]
        history_chars = self._estimate_request_tokens(messages=history_messages, tools=None, response_format=None)["message_chars"]
        tool_output_chars = self._estimate_request_tokens(messages=tool_messages, tools=None, response_format=None)["message_chars"]

        tool_schema_chars = int(total_size.get("tool_chars") or 0)
        response_format_chars = int(total_size.get("response_format_chars") or 0)

        bucket_chars: dict[str, int] = {
            "system": int(system_chars) + int(response_format_chars),
            "memory": int(memory_chars),
            "history": int(history_chars),
            "tool_outputs": int(tool_output_chars),
            "tools": int(tool_schema_chars),
        }

        allocations = {key: 0 for key in bucket_chars}
        if tokens_est > 0 and total_chars > 0:
            raw = {key: (tokens_est * (chars / total_chars)) for key, chars in bucket_chars.items()}
            floors = {key: int(val) for key, val in raw.items()}
            remainder = tokens_est - sum(floors.values())
            allocations.update(floors)
            if remainder > 0:
                ranked = sorted(raw.items(), key=lambda kv: kv[1] - floors[kv[0]], reverse=True)
                for i in range(remainder):
                    allocations[ranked[i % len(ranked)][0]] += 1

        return {
            "total": {
                "tokens_est": tokens_est,
                "total_chars": total_chars,
                "message_chars": int(total_size.get("message_chars") or 0),
                "tool_chars": tool_schema_chars,
                "response_format_chars": response_format_chars,
            },
            "buckets": {
                "system": {"chars": bucket_chars["system"], "tokens_est": allocations["system"], "messages": len(system_messages)},
                "memory": {"chars": bucket_chars["memory"], "tokens_est": allocations["memory"], "messages": len(memory_messages)},
                "history": {"chars": bucket_chars["history"], "tokens_est": allocations["history"], "messages": len(history_messages)},
                "tool_outputs": {"chars": bucket_chars["tool_outputs"], "tokens_est": allocations["tool_outputs"], "messages": len(tool_messages)},
                "tools": {"chars": bucket_chars["tools"], "tokens_est": allocations["tools"]},
            },
        }

    def _prompt_compaction_limits(self) -> dict[str, int]:
        return {
            "max_snippets": max(
                1,
                self._safe_int_setting(getattr(settings, "MCP_PROMPT_MAX_SNIPPETS", 4), 4),
            ),
            "snippet_content_chars": max(
                200,
                self._safe_int_setting(getattr(settings, "MCP_PROMPT_SNIPPET_CONTENT_CHARS", 1200), 1200),
            ),
            "max_rows": max(
                3,
                self._safe_int_setting(getattr(settings, "MCP_PROMPT_TABLE_MAX_ROWS", 12), 12),
            ),
            "max_contributions": max(
                5,
                self._safe_int_setting(getattr(settings, "MCP_PROMPT_TABLE_MAX_CONTRIBUTIONS", 25), 25),
            ),
            "max_cells": max(
                4,
                self._safe_int_setting(getattr(settings, "MCP_PROMPT_TABLE_MAX_CELLS", 12), 12),
            ),
            "max_cells_exact": max(
                4,
                self._safe_int_setting(getattr(settings, "MCP_PROMPT_TABLE_MAX_CELLS_EXACT", 60), 60),
            ),
        }

    def _compact_action_payload_for_prompt(
        self,
        payload: Mapping[str, object],
        *,
        max_string_chars: int = 500,
        max_keys: int = 20,
        max_list_items: int = 10,
        max_nested_keys: int = 10,
    ) -> dict[str, object]:
        compact: dict[str, object] = {}
        for key, value in payload.items():
            if len(compact) >= max_keys:
                break
            if value is None:
                continue
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    compact[key] = self._clip_text(trimmed, max_string_chars)
                continue
            if isinstance(value, (int, float, bool)):
                compact[key] = value
                continue
            if isinstance(value, Mapping):
                nested: dict[str, object] = {}
                for nested_key, nested_value in value.items():
                    if len(nested) >= max_nested_keys:
                        break
                    if nested_value is None:
                        continue
                    if isinstance(nested_value, str):
                        trimmed = nested_value.strip()
                        if trimmed:
                            nested[nested_key] = self._clip_text(trimmed, max_string_chars)
                        continue
                    if isinstance(nested_value, (int, float, bool)):
                        nested[nested_key] = nested_value
                if nested:
                    compact[key] = nested
                continue
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                items: list[object] = []
                for item in value[:max_list_items]:
                    if item is None:
                        continue
                    if isinstance(item, str):
                        trimmed = item.strip()
                        if trimmed:
                            items.append(self._clip_text(trimmed, max_string_chars))
                        continue
                    if isinstance(item, (int, float, bool)):
                        items.append(item)
                        continue
                    if isinstance(item, Mapping):
                        mini: dict[str, object] = {}
                        for mini_key, mini_val in item.items():
                            if len(mini) >= 6:
                                break
                            if mini_val is None:
                                continue
                            if isinstance(mini_val, str):
                                trimmed = mini_val.strip()
                                if trimmed:
                                    mini[mini_key] = self._clip_text(trimmed, max_string_chars)
                                continue
                            if isinstance(mini_val, (int, float, bool)):
                                mini[mini_key] = mini_val
                        if mini:
                            items.append(mini)
                if items:
                    compact[key] = items
                continue
        return compact

    def _strip_optional_system_messages(self, messages: Sequence[Mapping[str, object]]) -> tuple[list[dict[str, object]], int]:
        kept: list[dict[str, object]] = []
        dropped = 0
        placeholder_reminder = getattr(prompts, "PLACEHOLDER_REMINDER", "").strip()

        for entry in messages:
            payload = dict(entry)
            if payload.get("role") != "system":
                kept.append(payload)
                continue
            content = payload.get("content")
            if not isinstance(content, str):
                kept.append(payload)
                continue
            trimmed = content.strip()
            if not trimmed:
                kept.append(payload)
                continue

            is_optional = False
            if placeholder_reminder and trimmed == placeholder_reminder:
                is_optional = True
            elif trimmed.startswith("Task summary (internal;"):
                is_optional = True
            elif trimmed.startswith("Tool ledger this turn (internal;"):
                is_optional = True
            elif "You have already acknowledged that you are checking." in trimmed:
                is_optional = True
            elif "Tools are not returning new evidence. Do NOT call tools again." in trimmed:
                is_optional = True

            if is_optional:
                dropped += 1
                continue
            kept.append(payload)

        if not any(entry.get("role") == "system" for entry in kept):
            return [dict(entry) for entry in messages], 0
        return kept, dropped

    def _compact_snippet_for_prompt(
        self,
        snippet: Mapping[str, object],
        *,
        include_content: bool,
        content_chars: int,
        summary_chars: int = 400,
    ) -> dict[str, object]:
        kept_keys = (
            "id",
            "upload_id",
            "chunk_id",
            "chunk_index",
            "page_number",
            "page_mode",
            "title",
            "public_label",
            "read_state",
            "read_required",
            "read_hint",
            "coverage",
            "search_stage",
            "structured_table_hint",
            "structured_table_count",
            "issue_count",
            "confidence_score",
            "is_table_chunk",
            "entity_type",
            "entity_name",
            "entity_business",
        )
        compact: dict[str, object] = {}
        for key in kept_keys:
            if key not in snippet:
                continue
            value = snippet.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set, dict)) and not value:
                continue
            compact[key] = value
        summary = snippet.get("summary")
        if isinstance(summary, str) and summary.strip():
            compact["summary"] = self._clip_text(summary.strip(), summary_chars)
        if include_content:
            content = snippet.get("content")
            if isinstance(content, str) and content.strip():
                compact["content"] = self._clip_text(content.strip(), content_chars)
        return compact

    def _compact_tool_payload_for_prompt(
        self,
        tool_name: str,
        payload: Mapping[str, object],
        *,
        max_snippets: int = 50,
        snippet_content_chars: int = 8000,
        max_rows: int = 100,
        max_contributions: int = 50,
        max_cells: int = 50,
        max_cells_exact: int = 100,
    ) -> dict[str, object]:
        """
        Compact tool results before they are injected into the LLM prompt.

        - Knowledge tools can return very large payloads (full page reads, table previews).
          Use the knowledge-tool compactor to preserve IDs + evidence while keeping
          messages within the prompt tool-output budget.
        - Other tools are passed through as-is, with a safety truncation for extreme cases.
        """
        normalized_name = (tool_name or payload.get("tool") or "").strip()
        if normalized_name and self._is_knowledge_tool(normalized_name):
            return self._compact_knowledge_tool_payload_for_prompt(
                tool_name,
                payload,
                max_snippets=max_snippets,
                snippet_content_chars=snippet_content_chars,
                max_rows=max_rows,
                max_contributions=max_contributions,
                max_cells=max_cells,
                max_cells_exact=max_cells_exact,
            )

        MAX_RESULT_CHARS = 80_000  # 80KB is plenty for any non-knowledge tool result

        result = dict(payload)
        if "tool" not in result:
            result["tool"] = normalized_name or tool_name

        try:
            result_json = json.dumps(result, ensure_ascii=False, default=str)
            if len(result_json) > MAX_RESULT_CHARS:
                result = self._truncate_large_fields(result, MAX_RESULT_CHARS)
        except (TypeError, ValueError):
            pass  # If serialization fails, return as-is

        return result


    def _compact_tool_messages_for_prompt(
        self,
        messages: Sequence[Mapping[str, object]],
        *,
        max_snippets: int,
        snippet_content_chars: int,
        max_rows: int,
        max_contributions: int,
        max_cells: int,
        max_cells_exact: int,
    ) -> tuple[list[dict[str, object]], int]:
        updated: list[dict[str, object]] = []
        changed = 0
        for entry in messages:
            payload = dict(entry)
            if payload.get("role") != "tool":
                updated.append(payload)
                continue
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip():
                updated.append(payload)
                continue
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                updated.append(payload)
                continue
            if not isinstance(parsed, Mapping):
                updated.append(payload)
                continue
            tool_name = str(payload.get("name") or parsed.get("tool") or "")
            compacted = self._compact_tool_payload_for_prompt(
                tool_name,
                parsed,
                max_snippets=max_snippets,
                snippet_content_chars=snippet_content_chars,
                max_rows=max_rows,
                max_contributions=max_contributions,
                max_cells=max_cells,
                max_cells_exact=max_cells_exact,
            )
            new_content = self._truncate_tool_message_for_prompt(tool_name, json.dumps(compacted, ensure_ascii=False))
            if new_content != content:
                payload["content"] = new_content
                changed += 1
            updated.append(payload)
        return updated, changed

    def _proactive_compaction_keep_last_turns(self) -> int:
        raw_value = getattr(settings, "MCP_PROACTIVE_COMPACTION_KEEP_LAST_TURNS", 3)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = 3
        return max(1, min(value, 25))

    def _trim_history_keep_last_turns(
        self,
        messages: Sequence[Mapping[str, object]],
        *,
        keep_last_turns: int,
    ) -> tuple[list[dict[str, object]], int]:
        """
        Proactively trim older transcript entries while keeping the latest N turns verbatim.

        This trims only messages *before* the most recent user message, so it
        does not break within-turn tool call chains that appear after the last
        user entry (tool_iteration stage).
        """

        if keep_last_turns <= 0:
            return [dict(entry) for entry in messages], 0

        last_user_index = None
        for idx in range(len(messages) - 1, -1, -1):
            entry = messages[idx]
            if not isinstance(entry, Mapping):
                continue
            if entry.get("role") != "user":
                continue
            content = entry.get("content")
            if isinstance(content, str) and content.strip():
                last_user_index = idx
                break
        if last_user_index is None:
            return [dict(entry) for entry in messages], 0

        protected_indices: set[int] = set()
        chat_history_indices: list[int] = []
        for idx, entry in enumerate(messages):
            if idx >= last_user_index or not isinstance(entry, Mapping):
                continue
            role = entry.get("role")
            if role == "tool":
                protected_indices.add(idx)
                continue
            if role == "assistant":
                tool_calls = entry.get("tool_calls")
                if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes, bytearray)) and tool_calls:
                    protected_indices.add(idx)
                    continue
            if role in {"user", "assistant"}:
                chat_history_indices.append(idx)

        max_history_messages = max(0, int(keep_last_turns) * 2)
        if not max_history_messages or len(chat_history_indices) <= max_history_messages:
            return [dict(entry) for entry in messages], 0

        keep_history = set(chat_history_indices[-max_history_messages:])
        keep_indices = protected_indices | keep_history
        trimmed: list[dict[str, object]] = []
        dropped = 0
        for idx, entry in enumerate(messages):
            if not isinstance(entry, Mapping):
                continue
            if entry.get("role") == "system":
                trimmed.append(dict(entry))
                continue
            if idx >= last_user_index:
                trimmed.append(dict(entry))
                continue
            if idx in keep_indices:
                trimmed.append(dict(entry))
                continue
            dropped += 1
        return trimmed, dropped

    def _replace_memory_note_for_prompt(
        self,
        *,
        conversation: Conversation,
        messages: Sequence[Mapping[str, object]],
        cap_overrides: Mapping[str, int],
    ) -> tuple[list[dict[str, object]], bool]:
        """
        Replace (or drop) the conversation memory system message for prompt budgeting.

        This does NOT persist anything; it only affects the outgoing prompt.
        """

        new_note = None
        try:
            new_note = prompts._conversation_memory_note(conversation, cap_overrides=cap_overrides)
        except Exception:
            new_note = None

        updated: list[dict[str, object]] = []
        touched = False
        for entry in messages:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("role") == "system" and self._is_memory_system_message(entry):
                touched = True
                if isinstance(new_note, str) and new_note.strip():
                    payload = dict(entry)
                    payload["content"] = new_note
                    updated.append(payload)
                continue
            updated.append(dict(entry))
        return updated, touched

    def _govern_messages_for_budget(
        self,
        *,
        conversation: Conversation,
        stage: str,
        messages: Sequence[Mapping[str, object]],
        tools: Iterable[Mapping[str, object]] | None,
        response_format: Mapping[str, object] | None,
        on_stream_delta: Callable[[str], None] | None,
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        business = conversation.business_profile
        max_input_tokens = self._max_input_tokens_for_business(business)
        limits = self._prompt_compaction_limits()
        keep_last_turns = self._proactive_compaction_keep_last_turns()

        trigger_ratio = getattr(settings, "MCP_PROACTIVE_COMPACTION_TRIGGER_RATIO", 0.9)
        target_ratio = getattr(settings, "MCP_PROACTIVE_COMPACTION_TARGET_RATIO", 0.85)
        try:
            trigger_ratio_val = float(trigger_ratio)
        except (TypeError, ValueError):
            trigger_ratio_val = 0.9
        try:
            target_ratio_val = float(target_ratio)
        except (TypeError, ValueError):
            target_ratio_val = 0.85
        trigger_ratio_val = max(0.1, min(trigger_ratio_val, 1.0))
        target_ratio_val = max(0.05, min(target_ratio_val, trigger_ratio_val))
        trigger_tokens = max(1, int(max_input_tokens * trigger_ratio_val))
        target_tokens = max(1, int(max_input_tokens * target_ratio_val))

        original_size = self._estimate_request_tokens(messages=messages, tools=tools, response_format=response_format)
        actions: list[str] = []
        governed = [dict(entry) for entry in messages]

        if original_size["tokens_est"] >= trigger_tokens:
            governed, dropped_history = self._trim_history_keep_last_turns(governed, keep_last_turns=keep_last_turns)
            if dropped_history:
                actions.append(f"history_keep_turns={keep_last_turns}")
                actions.append(f"history_dropped={dropped_history}")

            candidate_size = self._estimate_request_tokens(messages=governed, tools=tools, response_format=response_format)
            if candidate_size["tokens_est"] > target_tokens:
                actions.append("compaction=proactive")
                if any(
                    isinstance(entry, Mapping) and entry.get("role") == "system" and self._is_memory_system_message(entry)
                    for entry in governed
                ):
                    memory_profiles: list[dict[str, int]] = [
                        {"artifact_refs_max_items": 0},
                        {
                            "artifact_refs_max_items": 0,
                            "item_max_chars": 96,
                            "facts_max_items": 6,
                            "preferences_max_items": 4,
                            "open_tasks_max_items": 4,
                            "decisions_max_items": 4,
                            "summary_max_chars": 900,
                        },
                        {
                            "artifact_refs_max_items": 0,
                            "item_max_chars": 96,
                            "facts_max_items": 4,
                            "preferences_max_items": 0,
                            "open_tasks_max_items": 0,
                            "decisions_max_items": 0,
                            "summary_max_chars": 600,
                        },
                    ]
                    applied_level = 0
                    for level, profile in enumerate(memory_profiles, start=1):
                        governed_candidate, touched = self._replace_memory_note_for_prompt(
                            conversation=conversation,
                            messages=governed,
                            cap_overrides=profile,
                        )
                        if not touched:
                            break
                        governed = governed_candidate
                        applied_level = level
                        candidate_size = self._estimate_request_tokens(
                            messages=governed,
                            tools=tools,
                            response_format=response_format,
                        )
                        if candidate_size["tokens_est"] <= target_tokens:
                            break
                    if applied_level:
                        actions.append(f"memory_level={applied_level}")

        if original_size["tokens_est"] > max_input_tokens:
            governed, dropped = self._strip_optional_system_messages(governed)
            if dropped:
                actions.append(f"dropped_system={dropped}")

            governed, compacted = self._compact_tool_messages_for_prompt(governed, **limits)
            if compacted:
                actions.append(f"compacted_tools={compacted}")

            other_count = sum(1 for entry in governed if entry.get("role") != "system")
            trimmed_history_limit = None
            for history_limit in range(other_count, 0, -1):
                candidate = prompts.limit_messages_for_stage(
                    governed,
                    stage=stage,
                    history_limit=history_limit,
                )
                candidate_size = self._estimate_request_tokens(
                    messages=candidate,
                    tools=tools,
                    response_format=response_format,
                )
                if candidate_size["tokens_est"] <= max_input_tokens:
                    if history_limit != other_count:
                        trimmed_history_limit = history_limit
                    governed = [dict(entry) for entry in candidate]
                    break
            if trimmed_history_limit is not None:
                actions.append(f"history_limit={trimmed_history_limit}")

        final_size = self._estimate_request_tokens(messages=governed, tools=tools, response_format=response_format)

        if final_size["tokens_est"] > max_input_tokens:
            system_entries = [entry for entry in governed if entry.get("role") == "system"]
            last_user = None
            for entry in reversed(governed):
                if entry.get("role") == "user" and isinstance(entry.get("content"), str) and entry.get("content").strip():
                    last_user = entry
                    break
            if last_user:
                governed = [*system_entries, dict(last_user)]
                actions.append("fallback=minimal_messages")
                final_size = self._estimate_request_tokens(messages=governed, tools=tools, response_format=response_format)

        detail: dict[str, object] = {
            "stage": stage,
            "max_input_tokens": max_input_tokens,
            "tokens_est_before": original_size["tokens_est"],
            "tokens_est_after": final_size["tokens_est"],
            "total_chars_before": original_size["total_chars"],
            "total_chars_after": final_size["total_chars"],
            "message_chars_after": final_size["message_chars"],
            "tool_chars_after": final_size["tool_chars"],
            "response_chars_after": final_size["response_format_chars"],
            "messages": len(governed),
            "tools_enabled": bool(tools),
            "streaming": bool(on_stream_delta),
        }
        if actions:
            detail["actions"] = actions
        level = logging.INFO if actions or final_size["tokens_est"] >= int(max_input_tokens * 0.9) else logging.DEBUG
        structured_log(
            "mcp",
            "prompt.budget",
            detail,
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
            level=level,
        )
        telemetry: dict[str, object] = {
            "max_input_tokens": max_input_tokens,
            "tokens_est_before": original_size["tokens_est"],
            "tokens_est_after": final_size["tokens_est"],
            "actions": tuple(actions),
        }
        return governed, telemetry

    def _chat_with_context_governor(
        self,
        *,
        conversation: Conversation,
        stage: str,
        messages: Sequence[Mapping[str, object]],
        tools: Iterable[Mapping[str, object]] | None,
        on_stream_delta: Callable[[str], None] | None,
        on_reasoning_event: Callable[[Mapping[str, object]], None] | None = None,
        reasoning_label: str | None = None,
        on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
        on_tool_call_delta: Callable[[Mapping[str, object]], None] | None = None,
        response_format: Mapping[str, object] | None = None,
        tool_context: ToolExecutionContext | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        if not self.provider:
            raise PromptGenerationError("MCP provider is not configured.")

        business = conversation.business_profile
        enabled = self._context_governor_enabled_for_business(business)
        governed_messages = [dict(entry) for entry in messages]
        telemetry: dict[str, object] | None = None
        if enabled:
            governed_messages, telemetry = self._govern_messages_for_budget(
                conversation=conversation,
                stage=stage,
                messages=messages,
                tools=tools,
                response_format=response_format,
                on_stream_delta=on_stream_delta,
            )
        else:
            max_input_tokens = self._max_input_tokens_for_business(business)
            estimated = self._estimate_request_tokens(messages=governed_messages, tools=tools, response_format=response_format)
            telemetry = {
                "max_input_tokens": max_input_tokens,
                "tokens_est_before": int(estimated.get("tokens_est") or 0),
                "tokens_est_after": int(estimated.get("tokens_est") or 0),
                "actions": tuple(),
            }

        prompt_budget_index: int | None = None
        if tool_context:
            breakdown = self._estimate_prompt_breakdown(
                messages=governed_messages,
                tools=tools,
                response_format=response_format,
            )
            max_input_tokens = int((telemetry or {}).get("max_input_tokens") or 0) if telemetry else 0
            tool_output_max_chars = self._tool_output_max_chars()
            entry: dict[str, object] = {
                "stage": stage,
                "max_input_tokens": max_input_tokens,
                "tool_output_max_chars": tool_output_max_chars,
                "messages": len(governed_messages),
                "tools_enabled": bool(tools),
                "streaming": bool(on_stream_delta),
            }
            if telemetry:
                entry["tokens_est_before"] = telemetry.get("tokens_est_before")
                entry["tokens_est_after"] = telemetry.get("tokens_est_after")
                actions = telemetry.get("actions")
                if isinstance(actions, (list, tuple)) and actions:
                    entry["actions"] = list(actions)
            entry.update(breakdown)
            prompt_budget_index = tool_context.add_prompt_budget_entry(entry)
            structured_log(
                "mcp",
                "prompt.breakdown",
                entry,
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
                level=logging.DEBUG,
            )

        try:
            from apps.llm.request_dump import maybe_dump_mcp_llm_request

            bundle_path = getattr(tool_context, "llm_request_dump_path", None) if tool_context else None
            dump_path = maybe_dump_mcp_llm_request(
                stage=stage,
                conversation_id=conversation.id,
                business_id=getattr(conversation, "business_profile_id", None),
                provider=self.provider,
                messages=governed_messages,
                tools=tools,
                streaming=bool(on_stream_delta),
                bundle_path=bundle_path,
            )
            if dump_path and tool_context and bundle_path is None:
                setattr(tool_context, "llm_request_dump_path", dump_path)
        except Exception:
            pass

        try:
            call_id = f"llm_{uuid.uuid4().hex}"
            label_value = (reasoning_label or stage.replace("_", " ").strip()).strip()
            if not label_value:
                label_value = "LLM"

            reasoning_started = False
            reasoning_ended = False

            def _emit_reasoning_event(event_type: str, *, delta: str | None = None) -> None:
                if not on_reasoning_event:
                    return
                payload: dict[str, object] = {
                    "type": event_type,
                    "call_id": call_id,
                    "stage": stage,
                    "label": label_value,
                }
                if delta is not None:
                    payload["delta"] = delta
                try:
                    on_reasoning_event(payload)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("on_reasoning_event callback failed")

            def _should_skip_reasoning_end() -> bool:
                return bool(should_cancel and should_cancel())

            def _maybe_end_reasoning() -> None:
                nonlocal reasoning_ended
                if reasoning_ended or not reasoning_started:
                    return
                if _should_skip_reasoning_end():
                    return
                reasoning_ended = True
                _emit_reasoning_event("reasoning_end")

            def _on_reasoning_delta(delta: str) -> None:
                nonlocal reasoning_started
                if not delta:
                    return
                if should_cancel and should_cancel():
                    return
                reasoning_started = True
                _emit_reasoning_event("reasoning_delta", delta=delta)

            def _on_stream_delta(chunk: str) -> None:
                _maybe_end_reasoning()
                if on_stream_delta:
                    on_stream_delta(chunk)

            payload = self.provider.chat(
                governed_messages,
                tools=tools,
                on_stream_delta=_on_stream_delta if (on_stream_delta and on_reasoning_event) else on_stream_delta,
                on_reasoning_delta=_on_reasoning_delta if on_reasoning_event else None,
                on_tool_call_start=on_tool_call_start,
                on_tool_call_delta=on_tool_call_delta,
                response_format=response_format,
                should_cancel=should_cancel,
            )
            _maybe_end_reasoning()
            self._record_llm_usage(tool_context, stage, payload)
            if tool_context and prompt_budget_index is not None:
                usage = payload.get("usage") if isinstance(payload, Mapping) else None
                if isinstance(usage, Mapping):
                    patch: dict[str, object] = {
                        "usage": {
                            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                            "total_tokens": int(usage.get("total_tokens", 0) or 0),
                        }
                    }
                    model_name = payload.get("model") if isinstance(payload.get("model"), str) else None
                    provider_name = payload.get("provider") if isinstance(payload.get("provider"), str) else None
                    if model_name:
                        patch["model"] = model_name
                    if provider_name:
                        patch["provider"] = provider_name
                    tool_context.update_prompt_budget_entry(prompt_budget_index, patch)
            return payload
        except PromptGenerationError as exc:
            err = str(exc).lower()
            if not enabled or not tools:
                raise
            if "token" not in err and "context" not in err and "maximum" not in err:
                raise

            last_user = None
            for entry in reversed(messages):
                if entry.get("role") == "user":
                    content = entry.get("content")
                    if isinstance(content, str) and content.strip():
                        last_user = content.strip()
                        break
            fallback_user = last_user or "Please clarify your request."
            fallback_messages: list[Mapping[str, object]] = [
                {
                    "role": "system",
                    "content": (
                        "You cannot call tools right now due to context limits. Provide a brief high-level response "
                        "based on the user's request. Invite them to share a specific detail if they want more precision, "
                        "but do not ask a direct clarifying question. Do not mention token limits or tools."
                    ),
                },
                {"role": "user", "content": fallback_user},
            ]
            structured_log(
                "mcp",
                "prompt.budget_fallback",
                {"stage": stage, "error": str(exc)[:240]},
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
                level=logging.WARNING,
            )
            payload = self.provider.chat(
                fallback_messages,
                tools=None,
                on_stream_delta=on_stream_delta,
                on_tool_call_start=None,
                on_tool_call_delta=None,
                response_format=None,
            )
            self._record_llm_usage(tool_context, stage, payload)
            return payload

    @staticmethod
    def _record_llm_usage(
        tool_context: ToolExecutionContext | None,
        stage: str,
        payload: Mapping[str, object] | None,
    ) -> None:
        if not tool_context or not payload or not isinstance(payload, Mapping):
            return
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            return
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        total = usage.get("total_tokens")
        try:
            prompt_val = int(prompt) if prompt is not None else 0
        except (TypeError, ValueError):
            prompt_val = 0
        try:
            completion_val = int(completion) if completion is not None else 0
        except (TypeError, ValueError):
            completion_val = 0
        try:
            total_val = int(total) if total is not None else 0
        except (TypeError, ValueError):
            total_val = 0
        if not total_val and (prompt_val or completion_val):
            total_val = prompt_val + completion_val
        model_name = payload.get("model")
        provider_name = payload.get("provider")
        tool_context.record_llm_usage(
            prompt_tokens=prompt_val,
            completion_tokens=completion_val,
            total_tokens=total_val,
            stage=stage,
            model=model_name if isinstance(model_name, str) else None,
            provider=provider_name if isinstance(provider_name, str) else None,
        )
