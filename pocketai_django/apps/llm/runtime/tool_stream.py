from __future__ import annotations

import json
import logging
import time
from typing import Callable, Mapping

from apps.llm.runtime.sse import _iter_sse_events
from apps.rag.rag_logging import structured_log

logger = logging.getLogger(__name__)


def _merge_stream_tool_call(
    store: dict[int, dict[str, object]],
    delta: Mapping[str, object],
) -> tuple[dict[str, object], bool, str]:
    try:
        idx = int(delta.get("index", 0))
    except (TypeError, ValueError):
        idx = 0
    state = store.setdefault(
        idx,
        {"id": None, "type": None, "function": {"name": None, "arguments": ""}, "index": idx},
    )
    identifier = delta.get("id")
    if isinstance(identifier, str) and identifier:
        state["id"] = identifier
    tool_type = delta.get("type")
    if isinstance(tool_type, str) and tool_type:
        state["type"] = tool_type
    function_block = state.setdefault("function", {"name": None, "arguments": ""})
    previous_name = function_block.get("name")
    func_delta = delta.get("function") if isinstance(delta.get("function"), Mapping) else {}
    func_name = func_delta.get("name")
    if isinstance(func_name, str) and func_name:
        function_block["name"] = func_name
    func_args = func_delta.get("arguments")
    args_delta = ""
    if isinstance(func_args, str) and func_args:
        existing = function_block.get("arguments") or ""
        function_block["arguments"] = f"{existing}{func_args}"
        args_delta = func_args
    name_now = function_block.get("name")
    fired = bool(name_now and name_now != previous_name)
    return state, fired, args_delta


def _collapse_stream_tool_calls(store: dict[int, dict[str, object]]) -> list[dict[str, object]]:
    collapsed: list[dict[str, object]] = []
    for idx in sorted(store.keys()):
        entry = store[idx]
        func = entry.get("function") if isinstance(entry.get("function"), Mapping) else {}
        collapsed.append(
            {
                "id": entry.get("id"),
                "type": entry.get("type") or "function",
                "function": {
                    "name": func.get("name"),
                    "arguments": func.get("arguments") or "",
                },
            }
        )
    return collapsed


def _consume_chat_completion_stream(
    stream,
    on_stream_delta: Callable[[str], None] | None,
    *,
    on_reasoning_delta: Callable[[str], None] | None = None,
    on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
    on_tool_call_delta: Callable[[Mapping[str, object]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """
    Assemble a chat-completions style payload from a streaming HTTP response.

    The returned structure mirrors the non-streaming API response so callers can
    reuse the same parsing logic. When `on_stream_delta` is provided, partial
    assistant content is emitted as soon as it is available.
    """

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, object]] = {}
    role: str | None = None
    finish_reason: str | None = None
    last_message_content: str | None = None
    last_message_reasoning_content: str | None = None
    last_message_tool_calls: list[dict[str, object]] | None = None
    last_message_payload: dict[str, object] | None = None
    usage_payload: Mapping[str, object] | None = None
    model_name: str | None = None

    def _normalize_delta(chunk: str) -> str:
        return chunk or ""

    start_first = time.monotonic()
    first_delta_at: float | None = None

    for payload in _iter_sse_events(stream):
        if should_cancel and should_cancel():
            break
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if not model_name:
            model_raw = data.get("model")
            if isinstance(model_raw, str) and model_raw:
                model_name = model_raw
        usage_raw = data.get("usage")
        if isinstance(usage_raw, Mapping):
            usage_payload = usage_raw
        choices = data.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}
        message_block = choice.get("message") or {}
        finish = choice.get("finish_reason")
        if isinstance(finish, str):
            finish_reason = finish
        role = delta.get("role") or role

        content_block = delta.get("content")
        if isinstance(content_block, list):
            for chunk in content_block:
                if not isinstance(chunk, Mapping):
                    continue
                text = _normalize_delta(chunk.get("text") or "")
                if not text:
                    continue
                if first_delta_at is None:
                    first_delta_at = time.monotonic()
                text_parts.append(text)
                if on_stream_delta:
                    try:
                        on_stream_delta(text)
                    except Exception:  # pragma: no cover - safeguard user callbacks
                        logger.exception("Streaming callback failed while emitting delta chunk.")
        elif isinstance(content_block, str) and content_block:
            normalized = _normalize_delta(content_block)
            text_parts.append(normalized)
            if first_delta_at is None:
                first_delta_at = time.monotonic()
            if on_stream_delta:
                try:
                    on_stream_delta(normalized)
                except Exception:  # pragma: no cover - safeguard user callbacks
                    logger.exception("Streaming callback failed while emitting delta chunk.")

        reasoning_block = delta.get("reasoning_content")
        if isinstance(reasoning_block, list):
            for chunk in reasoning_block:
                if not isinstance(chunk, Mapping):
                    continue
                text = _normalize_delta(chunk.get("text") or "")
                if text:
                    reasoning_parts.append(text)
                    if on_reasoning_delta:
                        try:
                            on_reasoning_delta(text)
                        except Exception:  # pragma: no cover - safeguard user callbacks
                            logger.exception("Streaming callback failed while emitting reasoning delta chunk.")
        elif isinstance(reasoning_block, str) and reasoning_block:
            normalized_reasoning = _normalize_delta(reasoning_block)
            reasoning_parts.append(normalized_reasoning)
            if on_reasoning_delta:
                try:
                    on_reasoning_delta(normalized_reasoning)
                except Exception:  # pragma: no cover - safeguard user callbacks
                    logger.exception("Streaming callback failed while emitting reasoning delta chunk.")

        for tool_delta in delta.get("tool_calls") or []:
            if isinstance(tool_delta, Mapping):
                state, fired, args_delta = _merge_stream_tool_call(tool_calls, tool_delta)
                if fired and on_tool_call_start:
                    try:
                        on_tool_call_start(
                            {
                                "id": state.get("id"),
                                "type": state.get("type") or "function",
                                "function": dict(state.get("function") or {}),
                                "index": state.get("index"),
                            }
                        )
                    except Exception:  # pragma: no cover - defensive
                        logger.exception("Streaming tool-call callback failed.")
                if on_tool_call_delta and (args_delta or fired):
                    try:
                        on_tool_call_delta(dict(state))
                    except Exception:  # pragma: no cover - defensive
                        logger.exception("Streaming tool-call delta callback failed.")

        # Capture any full message payload sent on streaming frames (some providers
        # emit the final message in the last SSE event).
        if isinstance(message_block, Mapping):
            last_message_payload = dict(message_block)
            msg_content = message_block.get("content")
            if isinstance(msg_content, list):
                joined = "".join(part.get("text", "") for part in msg_content if isinstance(part, Mapping)).strip()
                if joined:
                    last_message_content = joined
            elif isinstance(msg_content, str):
                stripped = msg_content.strip()
                if stripped:
                    last_message_content = stripped
            msg_reasoning = message_block.get("reasoning_content")
            if isinstance(msg_reasoning, list):
                joined_reasoning = "".join(
                    part.get("text", "") for part in msg_reasoning if isinstance(part, Mapping)
                ).strip()
                if joined_reasoning:
                    last_message_reasoning_content = joined_reasoning
            elif isinstance(msg_reasoning, str):
                stripped_reasoning = msg_reasoning.strip()
                if stripped_reasoning:
                    last_message_reasoning_content = stripped_reasoning
            msg_tools = message_block.get("tool_calls")
            if isinstance(msg_tools, list) and msg_tools:
                last_message_tool_calls = msg_tools

    assembled_text = "".join(text_parts).strip()
    if not assembled_text and last_message_content:
        assembled_text = last_message_content

    assembled_reasoning = "".join(reasoning_parts).strip()
    if not assembled_reasoning and last_message_reasoning_content:
        assembled_reasoning = last_message_reasoning_content
    force_reasoning_field = bool(model_name and "deepseek-reasoner" in model_name.lower())

    if (finish_reason == "tool_calls" or (tool_calls and not assembled_text)) and tool_calls:
        message = {
            "role": role or "assistant",
            "tool_calls": _collapse_stream_tool_calls(tool_calls),
        }
    elif not assembled_text and last_message_tool_calls:
        message = {
            "role": role or "assistant",
            "tool_calls": last_message_tool_calls,
        }
    else:
        if last_message_payload:
            message = dict(last_message_payload)
            if assembled_text:
                message["content"] = assembled_text
            else:
                message.setdefault("content", "")
        else:
            message = {"role": role or "assistant", "content": assembled_text}

    if assembled_reasoning:
        message["reasoning_content"] = assembled_reasoning
    elif force_reasoning_field and message.get("role") == "assistant":
        message.setdefault("reasoning_content", "")

    elapsed_ms = int((time.monotonic() - start_first) * 1000)
    first_ms = int((first_delta_at - start_first) * 1000) if first_delta_at else None
    structured_log(
        "llm",
        "stream.assembled",
        {
            "finish_reason": finish_reason,
            "elapsed_ms": elapsed_ms,
            "first_delta_ms": first_ms,
        },
        logger_obj=logger,
    )
    response: dict[str, object] = {"choices": [{"message": message}]}
    if usage_payload:
        response["usage"] = dict(usage_payload)
    if model_name:
        response["model"] = model_name
    return response
