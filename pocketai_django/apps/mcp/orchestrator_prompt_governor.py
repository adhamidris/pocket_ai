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
from .types import ToolExecutionContext


logger = logging.getLogger(__name__)


class McpPromptGovernorMixin:


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

    def _truncate_large_fields(
        self,
        obj: Any,
        max_total: int,
        max_field: int = 10000,
        *,
        max_list_items: int | None = None,
    ) -> Any:
        """Recursively truncate large string fields (and optionally list lengths)."""
        if isinstance(obj, str):
            return obj[:max_field] + "..." if len(obj) > max_field else obj
        if isinstance(obj, dict):
            return {k: self._truncate_large_fields(v, max_total, max_field, max_list_items=max_list_items) for k, v in obj.items()}
        if isinstance(obj, list):
            items = obj[:max_list_items] if isinstance(max_list_items, int) and max_list_items >= 0 else obj
            return [self._truncate_large_fields(item, max_total, max_field, max_list_items=max_list_items) for item in items]
        return obj

    def _compact_knowledge_tool_payload_for_prompt(
        self,
        tool_name: str,
        payload: Mapping[str, object],
        *,
        max_snippets: int,
        snippet_content_chars: int,
        max_rows: int,
        max_contributions: int,
        max_cells: int = 12,
        max_cells_exact: int = 60,
    ) -> dict[str, object]:
        """Prompt compaction for high-volume knowledge and table evidence tools."""
        normalized_name = (tool_name or payload.get("tool") or "").strip()
        compact: dict[str, object] = {"tool": normalized_name or payload.get("tool") or tool_name}
        status = payload.get("status")
        if status is not None:
            compact["status"] = status
        for key in ("error", "error_code", "hint"):
            if key not in payload:
                continue
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set, dict)) and not value:
                continue
            compact[key] = value

        budget = payload.get("budget")
        if isinstance(budget, Mapping) and budget and normalized_name != "search_knowledge":
            # Budget telemetry is useful for read continuations and blocked-tool payloads.
            # Search success payloads keep model-facing control data in pagination/read hints.
            compact["budget"] = dict(budget)
        for guidance_key in ("budget_guidance", "search_repeat_guidance"):
            guidance = payload.get(guidance_key)
            if isinstance(guidance, Mapping) and guidance:
                compact[guidance_key] = dict(guidance)

        if normalized_name == "mcp_search_tools":
            raw_results = payload.get("results")
            results_out: list[dict[str, object]] = []
            if isinstance(raw_results, list):
                for result in raw_results[: max(1, max_snippets)]:
                    if not isinstance(result, Mapping):
                        continue
                    tool_id = result.get("tool_id")
                    if not isinstance(tool_id, str) or not tool_id.strip():
                        continue
                    entry: dict[str, object] = {"tool_id": tool_id.strip()}
                    for key, limit in (
                        ("connection_name", 120),
                        ("remote_tool", 160),
                        ("description", 420),
                    ):
                        value = result.get(key)
                        if not isinstance(value, str) or not value.strip():
                            continue
                        entry[key] = self._clip_text(value.strip(), limit)
                    required_args = result.get("required_args")
                    required_out: list[dict[str, str]] = []
                    if isinstance(required_args, list):
                        for arg in required_args[:12]:
                            if not isinstance(arg, Mapping):
                                continue
                            name = arg.get("name")
                            if not isinstance(name, str) or not name.strip():
                                continue
                            arg_entry: dict[str, str] = {"name": name.strip()}
                            type_hint = arg.get("type")
                            if isinstance(type_hint, str) and type_hint.strip():
                                arg_entry["type"] = self._clip_text(type_hint.strip(), 48)
                            required_out.append(arg_entry)
                    if required_out:
                        entry["required_args"] = required_out
                    results_out.append(entry)

            compact["results"] = results_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "mcp_call_tool":
            tool_id = payload.get("tool_id")
            if isinstance(tool_id, str) and tool_id.strip():
                compact["tool_id"] = tool_id.strip()
            artifact_id = payload.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id.strip():
                compact["artifact_id"] = artifact_id.strip()
            prompt_view = payload.get("prompt_view")
            if isinstance(prompt_view, Mapping) and prompt_view:
                compact["prompt_view"] = self._compact_action_payload_for_prompt(
                    prompt_view,
                    max_string_chars=1200,
                    max_keys=24,
                    max_list_items=10,
                    max_nested_keys=12,
                )
            missing_fields = payload.get("missing_fields")
            if isinstance(missing_fields, list):
                compact["missing_fields"] = [str(field) for field in missing_fields if str(field).strip()][:24]
            type_errors = payload.get("type_errors")
            if isinstance(type_errors, list):
                errors_out: list[dict[str, str]] = []
                for err in type_errors[:24]:
                    if not isinstance(err, Mapping):
                        continue
                    field = err.get("field")
                    expected = err.get("expected")
                    received = err.get("received")
                    if not isinstance(field, str) or not field.strip():
                        continue
                    out: dict[str, str] = {"field": field.strip()}
                    if isinstance(expected, str) and expected.strip():
                        out["expected"] = self._clip_text(expected.strip(), 80)
                    if isinstance(received, str) and received.strip():
                        out["received"] = self._clip_text(received.strip(), 80)
                    errors_out.append(out)
                if errors_out:
                    compact["type_errors"] = errors_out

            is_error = payload.get("is_error")
            if isinstance(is_error, bool):
                compact["is_error"] = is_error
            remote = payload.get("remote")
            if isinstance(remote, Mapping):
                remote_out: dict[str, object] = {}
                for key in ("connection_id", "connection_name", "tool"):
                    value = remote.get(key)
                    if isinstance(value, str) and value.strip():
                        remote_out[key] = value.strip()
                if remote_out:
                    compact["remote"] = remote_out

            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                compact["text"] = self._clip_text(text.strip(), int(snippet_content_chars))

            content_in = payload.get("content")
            content_out: list[dict[str, object]] = []
            if isinstance(content_in, list):
                for item in content_in[:6]:
                    if not isinstance(item, Mapping):
                        continue
                    item_type = item.get("type")
                    if not isinstance(item_type, str) or not item_type.strip():
                        continue
                    entry: dict[str, object] = {"type": item_type.strip()}
                    if item_type == "text" and isinstance(item.get("text"), str) and item.get("text").strip():
                        entry["text"] = self._clip_text(item.get("text").strip(), int(snippet_content_chars))
                    elif item_type == "resource_link":
                        uri = item.get("uri")
                        if isinstance(uri, str) and uri.strip():
                            entry["uri"] = uri.strip()
                        name = item.get("name")
                        if isinstance(name, str) and name.strip():
                            entry["name"] = self._clip_text(name.strip(), 200)
                    elif item_type == "structured" and item.get("data") is not None:
                        try:
                            blob = json.dumps(item.get("data"), ensure_ascii=False, default=str)
                        except Exception:
                            blob = str(item.get("data"))
                        entry["data"] = self._clip_text(blob, 2000)
                    content_out.append(entry)
            if content_out:
                compact["content"] = content_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name.startswith("mcp_"):
            artifact_id = payload.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id.strip():
                compact["artifact_id"] = artifact_id.strip()
            prompt_view = payload.get("prompt_view")
            if isinstance(prompt_view, Mapping) and prompt_view:
                compact["prompt_view"] = self._compact_action_payload_for_prompt(
                    prompt_view,
                    max_string_chars=1200,
                    max_keys=24,
                    max_list_items=10,
                    max_nested_keys=12,
                )
            is_error = payload.get("is_error")
            if isinstance(is_error, bool):
                compact["is_error"] = is_error
            remote = payload.get("remote")
            if isinstance(remote, Mapping):
                remote_out: dict[str, object] = {}
                for key in ("connection_id", "connection_name", "tool"):
                    value = remote.get(key)
                    if isinstance(value, str) and value.strip():
                        remote_out[key] = value.strip()
                if remote_out:
                    compact["remote"] = remote_out

            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                compact["text"] = self._clip_text(text.strip(), int(snippet_content_chars))

            content_in = payload.get("content")
            content_out: list[dict[str, object]] = []
            if isinstance(content_in, list):
                for item in content_in[:6]:
                    if not isinstance(item, Mapping):
                        continue
                    item_type = item.get("type")
                    if not isinstance(item_type, str) or not item_type.strip():
                        continue
                    entry: dict[str, object] = {"type": item_type.strip()}
                    if item_type == "text" and isinstance(item.get("text"), str) and item.get("text").strip():
                        entry["text"] = self._clip_text(item.get("text").strip(), int(snippet_content_chars))
                    elif item_type == "resource_link":
                        uri = item.get("uri")
                        if isinstance(uri, str) and uri.strip():
                            entry["uri"] = uri.strip()
                        name = item.get("name")
                        if isinstance(name, str) and name.strip():
                            entry["name"] = self._clip_text(name.strip(), 200)
                    elif item_type == "structured" and item.get("data") is not None:
                        try:
                            blob = json.dumps(item.get("data"), ensure_ascii=False, default=str)
                        except Exception:
                            blob = str(item.get("data"))
                        entry["data"] = self._clip_text(blob, 2000)
                    content_out.append(entry)
            if content_out:
                compact["content"] = content_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "search_conversation_files":
            raw_snippets = payload.get("snippets")
            snippets_out: list[dict[str, object]] = []
            preview_chars = max(200, min(900, int(snippet_content_chars)))
            if isinstance(raw_snippets, list):
                for entry in raw_snippets[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    out: dict[str, object] = {}
                    snippet_id = entry.get("id")
                    if isinstance(snippet_id, str) and snippet_id.strip():
                        out["id"] = snippet_id.strip()
                    file_meta = entry.get("file")
                    if isinstance(file_meta, Mapping):
                        file_out: dict[str, object] = {}
                        for key in ("id", "filename", "page_count"):
                            value = file_meta.get(key)
                            if value is None:
                                continue
                            if isinstance(value, str) and not value.strip():
                                continue
                            file_out[key] = value
                        if file_out:
                            out["file"] = file_out
                    preview = entry.get("preview")
                    if isinstance(preview, str) and preview.strip():
                        out["preview"] = self._clip_text(preview.strip(), preview_chars)
                    read_hint = entry.get("read_hint") or entry.get("readHint")
                    if isinstance(read_hint, Mapping):
                        ids = read_hint.get("ids")
                        if isinstance(ids, list):
                            out["read_hint"] = {"ids": [str(v) for v in ids if str(v).strip()][:12]}
                    if out:
                        snippets_out.append(out)
            compact["snippets"] = snippets_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "read_conversation_file":
            raw_chunks = payload.get("chunks")
            chunks_out: list[dict[str, object]] = []
            content_chars = max(400, min(2400, int(snippet_content_chars)))
            if isinstance(raw_chunks, list):
                for entry in raw_chunks[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    out: dict[str, object] = {}
                    chunk_id = entry.get("id")
                    if isinstance(chunk_id, str) and chunk_id.strip():
                        out["id"] = chunk_id.strip()
                    file_meta = entry.get("file")
                    if isinstance(file_meta, Mapping):
                        file_out: dict[str, object] = {}
                        for key in ("id", "filename", "page_count"):
                            value = file_meta.get(key)
                            if value is None:
                                continue
                            if isinstance(value, str) and not value.strip():
                                continue
                            file_out[key] = value
                        if file_out:
                            out["file"] = file_out
                    content = entry.get("content")
                    if isinstance(content, str) and content.strip():
                        out["content"] = self._clip_text(content.strip(), content_chars)
                    if out:
                        chunks_out.append(out)
            compact["chunks"] = chunks_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name in {"pdf_generate", "pdf_merge", "pdf_extract_pages"}:
            artifact = payload.get("artifact")
            if isinstance(artifact, Mapping):
                artifact_out: dict[str, object] = {}
                file_id = artifact.get("file_id") or artifact.get("fileId") or artifact.get("id")
                filename = artifact.get("filename")
                if file_id is not None:
                    artifact_out["file_id"] = str(file_id)
                if isinstance(filename, str) and filename.strip():
                    artifact_out["filename"] = self._clip_text(filename.strip(), 180)
                if artifact_out:
                    compact["artifact"] = artifact_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "pdf_extract_text":
            file_meta = payload.get("file")
            if isinstance(file_meta, Mapping):
                file_out: dict[str, object] = {}
                for key in ("id", "filename", "page_count"):
                    value = file_meta.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    file_out[key] = value
                if file_out:
                    compact["file"] = file_out
            text_value = payload.get("text")
            if isinstance(text_value, str) and text_value.strip():
                max_text = max(2000, min(15000, int(snippet_content_chars) * 10))
                compact["text"] = self._clip_text(text_value.strip(), max_text)
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "search_knowledge":
            raw_diagnostics = payload.get("diagnostics")
            compact_diagnostics: dict[str, object] = {}
            if isinstance(raw_diagnostics, Mapping):
                for key in (
                    "reason",
                    "intent_clarification_question",
                    "assistant_guidance",
                    "scope_summary",
                    "conflict_detected",
                    "no_result_reason",
                ):
                    value = raw_diagnostics.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str):
                        if not value.strip():
                            continue
                        compact_diagnostics[key] = self._clip_text(value.strip(), 260)
                        continue
                    compact_diagnostics[key] = value

            if compact_diagnostics:
                compact["diagnostics"] = compact_diagnostics

            # Preserve control-plane fields so the LLM can plan coverage and paginate.
            # Keep pagination fields under `pagination`; top-level duplicates are
            # intentionally omitted from the compact public/search payload.
            total_found = payload.get("total_found")
            total_found_int: int | None = None
            if isinstance(total_found, (int, float)) and int(total_found) >= 0:
                total_found_int = int(total_found)
            elif isinstance(total_found, str) and total_found.strip().isdigit():
                total_found_int = int(total_found.strip())

            has_more = bool(payload.get("has_more")) if "has_more" in payload else None

            next_cursor = payload.get("next_cursor")

            read_budget = payload.get("read_budget")
            if not isinstance(read_budget, Mapping) or not read_budget:
                legacy_budget = payload.get("read_budget_hint")
                if isinstance(legacy_budget, Mapping):
                    read_budget = {
                        "suggested_chars": legacy_budget.get("total_suggested_max_chars"),
                        "max_chars": legacy_budget.get("max_chars_allowed"),
                    }
            if isinstance(read_budget, Mapping) and read_budget:
                hint_out: dict[str, object] = {}
                for key in ("suggested_chars", "max_chars"):
                    value = read_budget.get(key)
                    if value is None:
                        continue
                    try:
                        hint_out[key] = int(value)
                    except (TypeError, ValueError):
                        continue
                if hint_out:
                    compact["read_budget"] = hint_out

            completeness = payload.get("completeness")
            pagination_out: dict[str, object] = {}
            pagination_in = payload.get("pagination")
            if isinstance(pagination_in, Mapping) and pagination_in:
                for key in ("shown", "total", "has_more", "next_cursor", "message"):
                    value = pagination_in.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    pagination_out[key] = (
                        self._clip_text(value.strip(), 420)
                        if key == "message" and isinstance(value, str)
                        else self._clip_text(value.strip(), 240)
                        if key == "next_cursor" and isinstance(value, str)
                        else value
                    )
            if isinstance(completeness, Mapping) and completeness:
                for source_key, target_key in (
                    ("shown", "shown"),
                    ("refs_total_found", "total"),
                    ("total_found", "total"),
                    ("has_more", "has_more"),
                    ("message", "message"),
                ):
                    if target_key in pagination_out:
                        continue
                    value = completeness.get(source_key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    pagination_out[target_key] = (
                        self._clip_text(value.strip(), 420)
                        if source_key == "message" and isinstance(value, str)
                        else value
                    )
            if "total" not in pagination_out and total_found_int is not None:
                pagination_out["total"] = total_found_int
            if "has_more" not in pagination_out and has_more is not None:
                pagination_out["has_more"] = has_more
            if isinstance(next_cursor, str) and next_cursor.strip():
                pagination_out.setdefault("next_cursor", self._clip_text(next_cursor.strip(), 240))
            if pagination_out:
                compact["pagination"] = pagination_out

            for key in ("query", "intent", "match_policy"):
                if key not in payload:
                    continue
                value = payload.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, (list, tuple, set, dict)) and not value:
                    continue
                compact[key] = value
            raw_results = payload.get("refs")
            if not isinstance(raw_results, list):
                raw_results = payload.get("results")
            refs_out: list[dict[str, object]] = []
            if isinstance(raw_results, list):
                for result in raw_results[: max(1, max_snippets)]:
                    if not isinstance(result, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    for key in (
                        "id",
                        "document",
                        "kind",
                        "preview",
                        "read_chars",
                    ):
                        if key not in result:
                            continue
                        value = result.get(key)
                        if value is None:
                            continue
                        if isinstance(value, str) and not value.strip():
                            continue
                        if key == "preview" and isinstance(value, str) and value.strip():
                            entry[key] = self._clip_text(value.strip(), int(snippet_content_chars))
                            continue
                        entry[key] = value
                    if "document" not in entry:
                        label = result.get("label") or result.get("title")
                        if isinstance(label, str) and label.strip():
                            entry["document"] = self._clip_text(label.strip(), 180)
                    if "read_chars" not in entry:
                        read_hint = result.get("read_hint")
                        if isinstance(read_hint, Mapping):
                            value = read_hint.get("suggested_max_chars")
                            if value not in (None, ""):
                                entry["read_chars"] = value
                    if entry:
                        refs_out.append(entry)
            if refs_out:
                compact["refs"] = refs_out
                return compact
            raw_snippets = payload.get("snippets")
            snippets_out: list[dict[str, object]] = []
            search_content_chars = max(200, min(600, int(snippet_content_chars)))
            if isinstance(raw_snippets, list):
                for entry in raw_snippets[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    snippets_out.append(
                        self._compact_snippet_for_prompt(
                            entry,
                            include_content=True,
                            content_chars=search_content_chars,
                        )
                    )
            compact["snippets"] = snippets_out
            return compact



        if normalized_name == "read_knowledge":
            # Agentic contract (Phase 2): evidence is a list of canonical payloads.
            evidence_list = payload.get("evidence")
            if isinstance(evidence_list, list):
                budget_in = compact.pop("budget", None)
                if isinstance(budget_in, Mapping):
                    budget_out: dict[str, object] = {}
                    for key in ("warning", "next_action"):
                        value = budget_in.get(key)
                        if isinstance(value, str) and value.strip():
                            budget_out[key] = self._clip_text(value.strip(), 260)
                    if budget_out:
                        compact["budget"] = budget_out

                evidence_out: list[dict[str, object]] = []
                for entry in evidence_list[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    out_entry: dict[str, object] = {}
                    entry_id = entry.get("id")
                    if isinstance(entry_id, str) and entry_id.strip():
                        out_entry["id"] = entry_id.strip()
                    title = entry.get("title")
                    if isinstance(title, str) and title.strip():
                        out_entry["document"] = self._clip_text(title.strip(), 180)
                    kind = entry.get("kind")
                    if isinstance(kind, str) and kind.strip():
                        out_entry["kind"] = kind.strip()
                    if bool(entry.get("truncated")):
                        out_entry["truncated"] = True
                    if entry.get("complete") is False:
                        out_entry["complete"] = False
                    for key in ("artifact_id", "cursor_used", "next_cursor"):
                        if key in entry and entry.get(key) not in {None, ""}:
                            out_entry[key] = entry.get(key)
                    payload_obj = entry.get("payload")
                    if isinstance(payload_obj, Mapping) and payload_obj:
                        payload_type = str(payload_obj.get("type") or entry.get("type") or "").strip()
                        if payload_type == "table":
                            columns = payload_obj.get("columns")
                            rows = payload_obj.get("rows")
                            if isinstance(columns, list):
                                out_entry["columns"] = list(columns)
                            if isinstance(rows, list):
                                out_entry["rows"] = list(rows)

                            selection_mode = str(payload_obj.get("selection_mode") or "").strip()
                            is_exact_row = selection_mode == "row_ref"
                            if not is_exact_row:
                                for key in ("row_offset", "rows_shown", "total_rows", "next_row_start"):
                                    value = payload_obj.get(key)
                                    if value not in (None, ""):
                                        out_entry[key] = value
                                if bool(entry.get("more_rows_available")):
                                    out_entry["more_rows_available"] = True
                        elif payload_type == "text":
                            text_value = payload_obj.get("text")
                            if isinstance(text_value, str):
                                # Do not truncate canonical text here; read_knowledge is already bounded by max_chars.
                                out_entry["text"] = text_value
                        else:
                            out_entry["payload"] = dict(payload_obj)
                    if out_entry:
                        evidence_out.append(out_entry)

                compact["evidence"] = evidence_out

                for key in ("read", "deferred", "errors"):
                    value = payload.get(key)
                    if isinstance(value, list) and value:
                        compact[key] = value[:24]

                compact["prompt_compact"] = True
                return compact

            engine = str(payload.get("engine") or "").strip()
            if engine:
                compact["engine"] = engine
            for key in ("total_matches", "truncated", "throttle_notice"):
                if key not in payload:
                    continue
                value = payload.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, (list, tuple, set, dict)) and not value:
                    continue
                compact[key] = value

            diagnostics_in = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
            diagnostics_out: dict[str, object] = {}
            requested_identifier = (
                diagnostics_in.get("requested_identifier")
                if isinstance(diagnostics_in.get("requested_identifier"), Mapping)
                else None
            )
            if requested_identifier:
                requested_out: dict[str, object] = {}
                column = requested_identifier.get("column")
                if isinstance(column, str) and column.strip():
                    requested_out["column"] = column.strip()
                values = requested_identifier.get("values")
                if isinstance(values, list) and values:
                    requested_out["values"] = [str(item) for item in values[:6] if str(item).strip()]
                policy = requested_identifier.get("policy")
                if isinstance(policy, str) and policy.strip():
                    requested_out["policy"] = policy.strip()
                if requested_out:
                    diagnostics_out["requested_identifier"] = requested_out
            matched_identifiers = diagnostics_in.get("matched_identifiers")
            if isinstance(matched_identifiers, list) and matched_identifiers:
                diagnostics_out["matched_identifiers"] = [str(item) for item in matched_identifiers[:12] if str(item).strip()]
            match_policy = diagnostics_in.get("match_policy")
            if isinstance(match_policy, str) and match_policy.strip():
                diagnostics_out["match_policy"] = match_policy.strip()

            if engine == "text_page":
                for key in ("page", "mode", "mode_downgraded", "token_budget"):
                    value = diagnostics_in.get(key)
                    if value is None or value == "":
                        continue
                    diagnostics_out[key] = value
            elif engine == "table_preview":
                for key in (
                    "sheet_name",
                    "match_column",
                    "match_value",
                    "match_values",
                    "query",
                    "columns",
                    "mode",
                    "match_count",
                    "total",
                    "display_total",
                ):
                    value = diagnostics_in.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    if isinstance(value, (list, tuple, set, dict)) and not value:
                        continue
                    diagnostics_out[key] = value
            elif engine in {"file_dataset", "db_preview"}:
                for key in (
                    "sheet_name",
                    "sheet_index",
                    "query",
                    "filters",
                    "select_columns",
                    "sort_by",
                    "sort_direction",
                    "offset",
                    "limit",
                    "match_count",
                    "total_matches",
                    "scanned_rows",
                ):
                    value = diagnostics_in.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    if isinstance(value, (list, tuple, set, dict)) and not value:
                        continue
                    diagnostics_out[key] = value
                dataset = diagnostics_in.get("dataset") if isinstance(diagnostics_in.get("dataset"), Mapping) else None
                if dataset:
                    dataset_out: dict[str, object] = {}
                    for key in ("row_count", "sheet_row_count", "preview_rows_indexed", "sheet_count", "suggested_keys"):
                        value = dataset.get(key)
                        if value is None:
                            continue
                        if isinstance(value, str) and not value.strip():
                            continue
                        if isinstance(value, (list, tuple, set, dict)) and not value:
                            continue
                        dataset_out[key] = value
                    if dataset_out:
                        diagnostics_out["dataset"] = dataset_out

            if diagnostics_out:
                compact["diagnostics"] = diagnostics_out

            evidence_in = payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {}
            evidence_out: dict[str, object] = {"snippets": [], "rows": []}
            if engine == "text_page":
                raw_snippets = evidence_in.get("snippets")
                snippets_out: list[dict[str, object]] = []
                if isinstance(raw_snippets, list):
                    for entry in raw_snippets[: max(1, max_snippets)]:
                        if not isinstance(entry, Mapping):
                            continue
                        snippets_out.append(
                            self._compact_snippet_for_prompt(
                                entry,
                                include_content=True,
                                content_chars=snippet_content_chars,
                            )
                        )
                evidence_out["snippets"] = snippets_out
            else:
                cell_cap = max(1, int(max_cells))
                if engine in {"table_preview", "file_dataset", "db_preview"}:
                    total_matches = payload.get("total_matches")
                    if not isinstance(total_matches, int):
                        total_matches = None
                    requested_identifier = (
                        diagnostics_in.get("requested_identifier")
                        if isinstance(diagnostics_in.get("requested_identifier"), Mapping)
                        else None
                    )
                    policy = str(requested_identifier.get("policy") or "").strip().lower() if requested_identifier else ""
                    values = requested_identifier.get("values") if requested_identifier else None
                    has_values = isinstance(values, list) and any(str(item).strip() for item in values)
                    status_value = str(payload.get("status") or "ok").strip().lower()
                    if (
                        status_value == "ok"
                        and total_matches is not None
                        and total_matches <= max(1, int(max_rows))
                        and policy in {"eq", "in"}
                        and has_values
                    ):
                        cell_cap = max(cell_cap, int(max_cells_exact))
                raw_rows = evidence_in.get("rows")
                rows_out: list[dict[str, object]] = []
                if isinstance(raw_rows, list):
                    for row in raw_rows[: max(1, max_rows)]:
                        if not isinstance(row, Mapping):
                            continue
                        row_payload: dict[str, object] = {}
                        if "row_index" in row and row.get("row_index") not in {None, ""}:
                            row_payload["row_index"] = row.get("row_index")
                        for key in ("table_order_index", "sheet_name", "row_total", "row_total_display", "contribution_count"):
                            if key in row and row.get(key) not in {None, ""}:
                                row_payload[key] = row.get(key)
                        cells = row.get("cells")
                        if isinstance(cells, list) and cells:
                            row_payload["cells"] = [
                                {"column": cell.get("column"), "value": cell.get("value")}
                                for cell in cells[: max(1, cell_cap)]
                                if isinstance(cell, Mapping)
                            ]
                        contributions = row.get("contributions")
                        if isinstance(contributions, list) and contributions:
                            row_payload["contributions"] = [
                                {"column": entry.get("column"), "display": entry.get("display"), "value": entry.get("value")}
                                for entry in contributions[: max(1, max_contributions)]
                                if isinstance(entry, Mapping)
                            ]
                        if row_payload:
                            rows_out.append(row_payload)
                evidence_out["rows"] = rows_out

                aggregate_result = evidence_in.get("aggregate_result") if isinstance(evidence_in.get("aggregate_result"), Mapping) else None
                if aggregate_result:
                    evidence_out["aggregate_result"] = dict(aggregate_result)
                if evidence_in.get("total") is not None:
                    evidence_out["total"] = evidence_in.get("total")
                if evidence_in.get("display_total") not in (None, ""):
                    evidence_out["display_total"] = evidence_in.get("display_total")

            compact["evidence"] = evidence_out
            compact["prompt_compact"] = True
            return compact


        # Handle email tools to ensure results reach the LLM
        if normalized_name == "email_search":
            for key in ("provider", "email_account_id", "query", "result_size_estimate", "next_page_token"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            raw_results = payload.get("results")
            results_out: list[dict[str, object]] = []
            if isinstance(raw_results, list):
                for result in raw_results[: max(1, max_snippets)]:
                    if not isinstance(result, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    for key in ("message_id", "thread_id", "snippet", "subject", "from", "to", "date"):
                        value = result.get(key)
                        if value is not None and value != "":
                            if key == "snippet":
                                entry[key] = self._clip_text(str(value), 200)
                            else:
                                entry[key] = value
                    if entry:
                        results_out.append(entry)
            compact["results"] = results_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "email_get_message":
            for key in ("provider", "email_account_id", "message_id", "thread_id", "snippet", "labels"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            headers = payload.get("headers")
            if isinstance(headers, Mapping):
                compact["headers"] = dict(headers)
            body_text = payload.get("body_text")
            if isinstance(body_text, str) and body_text.strip():
                compact["body_text"] = self._clip_text(body_text.strip(), int(snippet_content_chars) * 2)
            if payload.get("body_truncated"):
                compact["body_truncated"] = True
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "email_get_thread":
            for key in ("provider", "email_account_id", "thread_id", "message_count", "truncated"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            raw_messages = payload.get("messages")
            messages_out: list[dict[str, object]] = []
            if isinstance(raw_messages, list):
                for msg in raw_messages[: max(1, max_snippets)]:
                    if not isinstance(msg, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    for key in ("message_id", "thread_id", "snippet", "labels"):
                        value = msg.get(key)
                        if value is not None and value != "":
                            entry[key] = value
                    headers = msg.get("headers")
                    if isinstance(headers, Mapping):
                        entry["headers"] = dict(headers)
                    body_text = msg.get("body_text")
                    if isinstance(body_text, str) and body_text.strip():
                        entry["body_text"] = self._clip_text(body_text.strip(), int(snippet_content_chars))
                    if msg.get("body_truncated"):
                        entry["body_truncated"] = True
                    if entry:
                        messages_out.append(entry)
            compact["messages"] = messages_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name in ("email_create_draft", "email_send_draft"):
            for key in ("provider", "email_account_id", "draft_id", "message_id", "thread_id"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            compact["prompt_compact"] = True
            return compact

        action_value = payload.get("action")
        action_payload = payload.get("payload")
        if action_value is not None or action_payload is not None:
            if action_value is not None:
                compact["action"] = action_value
            if isinstance(action_payload, Mapping):
                compact["payload"] = self._compact_action_payload_for_prompt(action_payload)
            elif isinstance(action_payload, str) and action_payload.strip():
                compact["payload"] = self._clip_text(action_payload.strip(), 800)
            compact["prompt_compact"] = True
            return compact

        raw_snippets = payload.get("snippets")
        snippets_out = []
        if isinstance(raw_snippets, list):
            for entry in raw_snippets[: max(1, max_snippets)]:
                if not isinstance(entry, Mapping):
                    continue
                snippets_out.append(
                    self._compact_snippet_for_prompt(
                        entry,
                        include_content=False,
                        content_chars=snippet_content_chars,
                    )
                )
        if snippets_out:
            compact["snippets"] = snippets_out

        scalar_limit = 12
        for key, value in payload.items():
            if key in {
                "tool",
                "status",
                "error",
                "error_code",
                "hint",
                "snippets",
                "rows",
                "results",
                "action",
                "payload",
            }:
                continue
            if len(compact) >= scalar_limit:
                break
            if value is None:
                continue
            if isinstance(value, (int, float, bool)):
                compact[key] = value
            elif isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    compact[key] = self._clip_text(trimmed, 200)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                items: list[object] = []
                for item in value[:6]:
                    if item is None:
                        continue
                    if isinstance(item, (int, float, bool)):
                        items.append(item)
                    elif isinstance(item, str):
                        trimmed = item.strip()
                        if trimmed:
                            items.append(self._clip_text(trimmed, 120))
                if items:
                    compact[key] = items
        compact["prompt_compact"] = True
        return compact

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