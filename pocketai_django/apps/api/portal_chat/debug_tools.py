from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping

from django.conf import settings
from django.http import HttpRequest

from apps.rag.contracts import StreamingTurnContext


def _portal_debug_tool_trace_enabled(request: HttpRequest, payload: Mapping[str, object], metadata: Mapping[str, object]) -> bool:
    """
    Gate portal tool-trace/search debug payloads behind:
    - a server-side enable setting (or DEBUG), and
    - optional shared-token verification (if configured).
    """

    enabled = bool(getattr(settings, "PORTAL_DEBUG_TOOL_TRACE", False) or getattr(settings, "DEBUG", False))
    if not enabled:
        return False
    return True


def _clip_debug_text(value: object, *, limit: int = 480) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return f"{text[: max(0, limit - 1)].rstrip()}…"
    return text


def _json_safe_debug(value: object, *, depth: int = 3, string_limit: int = 240, list_limit: int = 12) -> object:
    def _is_numeric_metric(v: object) -> bool:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            candidate = v.strip()
            if not candidate:
                return False
            try:
                float(candidate)
            except ValueError:
                return False
            return True
        return False

    if value is None:
        return None
    if depth <= 0:
        return _clip_debug_text(value, limit=string_limit)
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return _clip_debug_text(value, limit=string_limit)
        return value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= list_limit:
                out["…"] = f"+{max(0, len(value) - list_limit)} more keys"
                break
            key_str = str(key or "").strip() or f"key_{idx}"
            lowered = key_str.lower()
            if any(token in lowered for token in ("password", "secret", "api_key", "apikey")):
                out[key_str] = "[REDACTED]"
                continue
            if "token" in lowered:
                safe_token_metrics = {
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "max_tokens",
                    "max_context_tokens",
                    "max_input_tokens",
                    "response_token_reserve",
                    "tokens_est",
                    "tokens_est_before",
                    "tokens_est_after",
                    "token_budget",
                }
                if lowered not in safe_token_metrics or not _is_numeric_metric(item):
                    out[key_str] = "[REDACTED]"
                    continue
            out[key_str] = _json_safe_debug(item, depth=depth - 1, string_limit=string_limit, list_limit=list_limit)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out_list: list[object] = []
        for item in items[:list_limit]:
            out_list.append(_json_safe_debug(item, depth=depth - 1, string_limit=string_limit, list_limit=list_limit))
        if len(items) > list_limit:
            out_list.append(f"…(+{len(items) - list_limit} more)")
        return out_list
    return _clip_debug_text(value, limit=string_limit)


def _json_debug_exact(value: object, *, depth: int = 10) -> object:
    """
    Preserve exact debug payload values without clipping/redaction.

    Used only for explicit portal debug I/O mirrors (developer-facing),
    where we need to inspect the exact tool-call request/response boundary
    as seen by the model.
    """

    if value is None:
        return None
    if depth <= 0:
        return value if isinstance(value, (str, int, float, bool)) else str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in value.items():
            out[str(key)] = _json_debug_exact(item, depth=depth - 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_json_debug_exact(item, depth=depth - 1) for item in value]
    return str(value)


def _serialize_tool_trace_entry(entry: Mapping[str, object]) -> dict[str, object]:
    tool = str(entry.get("tool") or "").strip()
    arguments = entry.get("arguments")
    args_out: dict[str, object] | None = None
    if isinstance(arguments, Mapping) and arguments:
        allowed_keys = {
            "query",
            "queries",
            "limit",
            "document_id",
            "ids",
            "items",
            "page",
            "pages",
            "offset",
            "mode",
            "neighbor_window",
            "chunk_neighbor",
            "token_budget",
            "max_chars",
            "columns",
            "filters",
            "operation",
        }
        filtered: dict[str, object] = {}
        for key, value in arguments.items():
            key_str = str(key)
            if key_str not in allowed_keys:
                continue
            if key_str == "items" and isinstance(value, list):
                # Avoid dumping raw signed cursors into the portal debug panel.
                safe_items: list[dict[str, object]] = []
                for item in value[:12]:
                    if not isinstance(item, Mapping):
                        continue
                    item_id = str(item.get("id") or "").strip()
                    cursor = item.get("cursor")
                    cursor_fp = None
                    if isinstance(cursor, str) and cursor.strip():
                        digest = hashlib.sha256(cursor.strip().encode("utf-8")).hexdigest()
                        cursor_fp = {"len": len(cursor.strip()), "sha256_10": digest[:10]}
                    safe_entry: dict[str, object] = {}
                    if item_id:
                        safe_entry["id"] = item_id
                    if cursor_fp:
                        safe_entry["cursor"] = cursor_fp
                    if safe_entry:
                        safe_items.append(safe_entry)
                filtered[key_str] = safe_items
            else:
                filtered[key_str] = value
        if not filtered:
            # Fall back to a bounded view of whatever was provided (still redacts tokens).
            filtered = dict(list(arguments.items())[:12])
        args_out = _json_safe_debug(filtered, depth=3, string_limit=240, list_limit=12)  # type: ignore[assignment]

    out: dict[str, object] = {
        "tool": tool or None,
        "status": entry.get("status"),
        "error_code": entry.get("error_code"),
        "duration_ms": entry.get("duration_ms"),
        "cache_hit": entry.get("cache_hit"),
        "origin": entry.get("origin"),
    }
    if args_out:
        out["arguments"] = args_out
    output_summary = entry.get("output_summary")
    if isinstance(output_summary, Mapping) and output_summary:
        out["output_summary"] = _json_safe_debug(output_summary, depth=4, string_limit=240, list_limit=16)  # type: ignore[arg-type]
    prompt_compaction = entry.get("prompt_compaction")
    if isinstance(prompt_compaction, Mapping) and prompt_compaction:
        out["prompt_compaction"] = _json_safe_debug(prompt_compaction, depth=3, string_limit=180, list_limit=12)  # type: ignore[arg-type]
    hint = entry.get("hint")
    if hint:
        out["hint"] = _clip_debug_text(hint, limit=240)
    engine = entry.get("engine")
    if engine:
        out["engine"] = _clip_debug_text(engine, limit=80)

    llm_request = entry.get("llm_request")
    if isinstance(llm_request, Mapping) and llm_request:
        out["llm_request"] = _json_debug_exact(llm_request, depth=10)  # type: ignore[arg-type]

    llm_response = entry.get("llm_response")
    if isinstance(llm_response, Mapping) and llm_response:
        llm_response_out = _json_debug_exact(llm_response, depth=6)
        if isinstance(llm_response_out, dict):
            content_value = llm_response_out.get("content")
            if isinstance(content_value, str):
                try:
                    llm_response_out["content_json"] = json.loads(content_value)
                except Exception:
                    pass
        out["llm_response"] = llm_response_out  # type: ignore[assignment]

    return {k: v for k, v in out.items() if v is not None and v != ""}


def _serialize_llm_usage(usage: Mapping[str, object] | None) -> dict[str, object] | None:
    if not usage:
        return None
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
    out: dict[str, object] = {
        "prompt_tokens": prompt_val,
        "completion_tokens": completion_val,
        "total_tokens": total_val,
    }
    calls_raw = usage.get("calls")
    if isinstance(calls_raw, (list, tuple)):
        calls: list[dict[str, object]] = []
        for entry in calls_raw:
            if not isinstance(entry, Mapping):
                continue
            call_prompt = entry.get("prompt_tokens")
            call_completion = entry.get("completion_tokens")
            call_total = entry.get("total_tokens")
            try:
                call_prompt_val = int(call_prompt) if call_prompt is not None else 0
            except (TypeError, ValueError):
                call_prompt_val = 0
            try:
                call_completion_val = int(call_completion) if call_completion is not None else 0
            except (TypeError, ValueError):
                call_completion_val = 0
            try:
                call_total_val = int(call_total) if call_total is not None else 0
            except (TypeError, ValueError):
                call_total_val = 0
            if not call_total_val and (call_prompt_val or call_completion_val):
                call_total_val = call_prompt_val + call_completion_val
            call_entry: dict[str, object] = {
                "prompt_tokens": call_prompt_val,
                "completion_tokens": call_completion_val,
                "total_tokens": call_total_val,
            }
            stage = entry.get("stage")
            if stage:
                call_entry["stage"] = stage
            model = entry.get("model")
            if model:
                call_entry["model"] = model
            provider = entry.get("provider")
            if provider:
                call_entry["provider"] = provider
            calls.append(call_entry)
        if calls:
            out["calls"] = calls
    provider = usage.get("provider")
    if provider:
        out["provider"] = provider
    model = usage.get("model")
    if model:
        out["model"] = model
    return out


def _serialize_context_budget() -> dict[str, object] | None:
    max_context = getattr(settings, "MCP_MAX_CONTEXT_TOKENS", None)
    max_input = getattr(settings, "MCP_MAX_INPUT_TOKENS", None)
    reserve = getattr(settings, "MCP_RESPONSE_TOKEN_RESERVE", None)
    try:
        max_context_val = int(max_context) if max_context is not None else 0
    except (TypeError, ValueError):
        max_context_val = 0
    try:
        max_input_val = int(max_input) if max_input is not None else 0
    except (TypeError, ValueError):
        max_input_val = 0
    try:
        reserve_val = int(reserve) if reserve is not None else 0
    except (TypeError, ValueError):
        reserve_val = 0

    max_context_val = max(0, max_context_val)
    max_input_val = max(0, max_input_val)
    reserve_val = max(0, reserve_val)
    if not max_context_val and not max_input_val and not reserve_val:
        return None
    return {
        "max_context_tokens": max_context_val,
        "max_input_tokens": max_input_val,
        "response_token_reserve": reserve_val,
    }


def _serialize_knowledge_result(entry: Mapping[str, object]) -> dict[str, object]:
    title = entry.get("title") or entry.get("public_label") or entry.get("label") or "Knowledge"
    preview_source = (
        entry.get("summary")
        or entry.get("preview")
        or entry.get("content")
        or entry.get("text")
        or ""
    )
    out: dict[str, object] = {
        "id": entry.get("id") or entry.get("chunk_id") or entry.get("upload_id"),
        "title": _clip_debug_text(title, limit=140),
        "search_stage": entry.get("search_stage"),
        "read_state": entry.get("read_state") or entry.get("readState"),
        "document_id": entry.get("upload_id") or entry.get("document_id"),
        "chunk_id": entry.get("chunk_id"),
        "source": entry.get("source_file") or entry.get("source"),
        "is_table_chunk": entry.get("is_table_chunk"),
    }
    if preview_source:
        out["preview"] = _clip_debug_text(preview_source, limit=420)
    read_hint = entry.get("read_hint") or entry.get("readHint")
    if isinstance(read_hint, Mapping) and read_hint:
        out["read_hint"] = _json_safe_debug(read_hint, depth=2, string_limit=160, list_limit=8)
    return {k: v for k, v in out.items() if v is not None and v != "" and v != []}


def _serialize_debug_tools_payload(stream_context: StreamingTurnContext) -> dict[str, object] | None:
    tool_context = getattr(stream_context, "tool_context", None)
    tool_trace_raw = None
    knowledge_results_raw = None
    knowledge_reads_raw = None
    search_history_raw = None
    coverage_ledger_raw = None
    llm_usage_raw = getattr(stream_context, "llm_usage", None)
    prompt_budget_raw = None
    if tool_context is not None:
        tool_trace_raw = getattr(tool_context, "tool_trace", None)
        knowledge_results_raw = getattr(tool_context, "knowledge_results", None)
        knowledge_reads_raw = getattr(tool_context, "knowledge_reads", None)
        search_history_raw = getattr(tool_context, "search_history", None)
        coverage_ledger_raw = getattr(tool_context, "coverage_ledger", None)
        prompt_budget_raw = getattr(tool_context, "prompt_budget_entries", None)
    if tool_trace_raw is None:
        tool_trace_raw = getattr(stream_context, "tool_trace", None)
    if knowledge_results_raw is None:
        knowledge_results_raw = getattr(stream_context, "knowledge_payload", None)
    if knowledge_reads_raw is None:
        knowledge_reads_raw = getattr(stream_context, "knowledge_reads", None)

    tool_trace: list[dict[str, object]] = []
    if isinstance(tool_trace_raw, (list, tuple)):
        for entry in tool_trace_raw[-30:]:
            if isinstance(entry, Mapping):
                tool_trace.append(_serialize_tool_trace_entry(entry))

    knowledge_results: list[dict[str, object]] = []
    if isinstance(knowledge_results_raw, (list, tuple)):
        for entry in knowledge_results_raw[:20]:
            if isinstance(entry, Mapping):
                knowledge_results.append(_serialize_knowledge_result(entry))

    knowledge_reads: list[dict[str, object]] = []
    if isinstance(knowledge_reads_raw, (list, tuple)):
        for entry in knowledge_reads_raw[:20]:
            if isinstance(entry, Mapping):
                knowledge_reads.append(_json_safe_debug(entry, depth=2, string_limit=180, list_limit=10))  # type: ignore[arg-type]

    search_history: list[object] = []
    if isinstance(search_history_raw, (list, tuple)):
        for entry in search_history_raw[-12:]:
            if isinstance(entry, Mapping):
                search_history.append(_json_safe_debug(entry, depth=3, string_limit=220, list_limit=12))

    coverage_ledger: list[object] = []
    if isinstance(coverage_ledger_raw, (list, tuple)):
        for entry in coverage_ledger_raw[:30]:
            if isinstance(entry, Mapping):
                coverage_ledger.append(_json_safe_debug(entry, depth=2, string_limit=200, list_limit=12))

    llm_usage = _serialize_llm_usage(llm_usage_raw if isinstance(llm_usage_raw, Mapping) else None)
    context_budget = _serialize_context_budget()

    prompt_budget: list[object] = []
    if isinstance(prompt_budget_raw, (list, tuple)):
        for entry in prompt_budget_raw[-12:]:
            if isinstance(entry, Mapping):
                prompt_budget.append(_json_safe_debug(entry, depth=4, string_limit=180, list_limit=20))

    if (
        not tool_trace
        and not knowledge_results
        and not knowledge_reads
        and not search_history
        and not coverage_ledger
        and not prompt_budget
        and not llm_usage
        and not context_budget
    ):
        return None
    return {
        "tool_trace": tool_trace,
        "search_history": search_history,
        "knowledge_results": knowledge_results,
        "knowledge_reads": knowledge_reads,
        "coverage_ledger": coverage_ledger,
        "prompt_budget": prompt_budget,
        "context_budget": context_budget,
        "usage": llm_usage,
    }

