from __future__ import annotations

import json
from typing import Iterable, Mapping, Sequence

from django.conf import settings


class McpPromptSizingMixin:

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
