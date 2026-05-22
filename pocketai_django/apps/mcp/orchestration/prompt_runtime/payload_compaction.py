from __future__ import annotations

import json
from typing import Mapping, Sequence

from django.conf import settings

from apps.conversations.models import Conversation

from ... import prompts
from .sizing import McpPromptSizingMixin
from .compaction.tools import McpPromptToolCompactionMixin


class McpPromptPayloadCompactionMixin(McpPromptSizingMixin, McpPromptToolCompactionMixin):

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
