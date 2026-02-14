from __future__ import annotations

import re
import uuid
from typing import Any, Literal, Mapping, TypedDict

from django.utils import timezone

from .rich_blocks import rich_blocks_from_text

ContentBlockType = Literal[
    "text",
    "paragraph",
    "heading",
    "list",
    "list_item",
    "quote",
    "code_block",
    "reasoning",
    "tool_use",
    "tool_result",
    "table",
    "kv",
]


class ContentBlock(TypedDict):
    block_id: str
    type: ContentBlockType
    created_at: str
    payload: dict[str, Any]


def new_block_id() -> str:
    return f"blk_{uuid.uuid4().hex}"


def make_text_block(text: str, *, block_id: str | None = None, created_at: str | None = None) -> ContentBlock:
    return {
        "block_id": block_id or new_block_id(),
        "type": "text",
        "created_at": created_at or timezone.now().isoformat(),
        "payload": {"text": text},
    }


def make_structured_block(
    block_type: ContentBlockType,
    payload: Mapping[str, object] | None = None,
    *,
    block_id: str | None = None,
    created_at: str | None = None,
) -> ContentBlock:
    if block_type == "text":
        raise ValueError("Use make_text_block for text blocks.")
    return {
        "block_id": block_id or new_block_id(),
        "type": block_type,
        "created_at": created_at or timezone.now().isoformat(),
        "payload": dict(payload or {}),
    }


def content_blocks_from_response_blocks(response_blocks: object | None) -> list[dict[str, object]]:
    """
    Convert normalized `response_blocks` (table/text/kv) into portal `content_blocks`.

    The MCP/RAG orchestrators sometimes return structured blocks alongside prose
    so the UI can render reliable tables/rows without markdown heuristics.
    """

    if not response_blocks:
        return []
    if not isinstance(response_blocks, list):
        if isinstance(response_blocks, tuple):
            response_blocks = list(response_blocks)
        else:
            return []

    out: list[dict[str, object]] = []
    for raw in response_blocks:
        if not isinstance(raw, Mapping):
            continue
        block_type = str(raw.get("type") or "").strip().lower()
        if block_type == "text":
            lines_raw = raw.get("body_md") or raw.get("body") or raw.get("lines") or raw.get("text")
            lines: list[str] = []
            if isinstance(lines_raw, str):
                stripped = lines_raw.strip()
                if stripped:
                    lines = [stripped]
            elif isinstance(lines_raw, list):
                for entry in lines_raw:
                    if isinstance(entry, str) and entry.strip():
                        lines.append(entry.strip())
            if not lines:
                continue
            heading = str(raw.get("heading") or raw.get("title") or "").strip()
            text = "\n".join(lines).strip()
            if heading:
                text = f"### {heading}\n\n{text}" if text else f"### {heading}"
            out.extend(rich_blocks_from_text(text))
            continue
        if block_type == "table":
            payload = dict(raw)
            payload.pop("type", None)
            out.append(dict(make_structured_block("table", payload)))
            continue
        if block_type == "kv":
            payload = dict(raw)
            payload.pop("type", None)
            out.append(dict(make_structured_block("kv", payload)))
            continue
    return out


def ensure_assistant_text_blocks(
    body: str,
    *,
    existing_blocks: object | None = None,
    force_regenerate_text: bool = False,
) -> list[dict[str, object]]:
    """
    Ensure the assistant message has properly formatted text blocks.

    Strategy:
    - Keep non-text blocks (tool_use, tool_result, reasoning, table, kv) from existing_blocks
    - If existing_blocks has a valid structure with both text and non-text blocks,
      preserve it as-is to maintain correct ordering (e.g., pre-approval text → tool → post-approval text)
    - Only regenerate text blocks from body if existing_blocks are missing or invalid
    - If `force_regenerate_text` is True, rebuild text blocks from `body` and merge
      them back into the existing block timeline without reordering tool/reasoning blocks
    """
    blocks = _coerce_block_list(existing_blocks)
    body_value = body.strip()

    # Types that should be preserved (not regenerated from body text)
    non_text_block_types = {"tool_use", "tool_result", "reasoning", "table", "kv"}

    # Check if existing blocks have a valid structure
    has_rich_text = any(isinstance(block, Mapping) and _is_rich_text_block(block) for block in blocks)
    has_plain_text = any(isinstance(block, Mapping) and _is_text_block(block) for block in blocks)
    has_malformed_rich_text = has_rich_text and _has_inline_embedded_lists(
        extract_text_from_content_blocks(blocks)
    )

    # Preserve existing blocks when they already include any text content.
    #
    # We only regenerate from `body` when the message has no text blocks yet (e.g., tool-only
    # messages during approval flows, or legacy rows missing content_blocks). Regenerating
    # from `body` can be lossy because `body` is a plain-text fallback and may not preserve
    # rich inline marks (bold/italic/code/link).
    if not force_regenerate_text and blocks and (has_rich_text or has_plain_text) and not has_malformed_rich_text:
        return blocks

    # Otherwise, regenerate text blocks from body
    non_text_blocks = [
        block for block in blocks
        if isinstance(block, Mapping) and str(block.get("type") or "").strip().lower() in non_text_block_types
    ]

    if not body_value:
        if has_malformed_rich_text:
            body_value = extract_text_from_content_blocks(blocks).strip()
    if not body_value:
        # No body text, just return non-text blocks
        return non_text_blocks if non_text_blocks else blocks

    # Generate fresh text blocks from body (this applies our markdown formatting fixes)
    rich_blocks = rich_blocks_from_text(body_value)

    if not rich_blocks and not non_text_blocks:
        # Fallback: return existing blocks if we couldn't generate anything
        return blocks

    # Merge rebuilt text into the existing timeline order. This avoids tools-first
    # reordering when we regenerate text for already-streamed messages.
    return _merge_rebuilt_text_blocks(blocks, rich_blocks)


def normalize_assistant_content_blocks(existing_blocks: object | None) -> list[dict[str, object]]:
    """
    Normalize malformed markdown artifacts inside rich-text runs while preserving
    surrounding non-text blocks and their order.
    """
    blocks = _coerce_block_list(existing_blocks)
    if not blocks:
        return []

    normalized: list[dict[str, object]] = []
    current_text_run: list[dict[str, object]] = []
    changed = False

    def _flush_text_run() -> None:
        nonlocal changed
        if not current_text_run:
            return
        repaired = _repair_rich_text_run(current_text_run)
        if repaired != current_text_run:
            changed = True
        normalized.extend(repaired)
        current_text_run.clear()

    for block in blocks:
        block_type = str(block.get("type") or "").strip().lower()
        if block_type == "text" or _is_rich_text_block(block):
            current_text_run.append(block)
            continue
        _flush_text_run()
        normalized.append(block)

    _flush_text_run()
    return normalized if changed else blocks


def extract_text_from_content_blocks(value: object | None) -> str:
    blocks = _coerce_block_list(value)
    if not blocks:
        return ""
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        payload = block.get("payload")
        payload_map = payload if isinstance(payload, Mapping) else {}
        text = _inline_nodes_to_markdown(payload_map.get("content"))
        if block_type == "heading":
            level = payload_map.get("level")
            prefix = "#" * level + " " if isinstance(level, int) and 1 <= level <= 6 else ""
            heading_text = f"{prefix}{text}".strip()
            if heading_text:
                lines.append(heading_text)
            continue
        if block_type == "paragraph" and text:
            lines.append(text)
            continue
        if block_type == "list_item" and text:
            lines.append(f"- {text}")
            continue
        if block_type == "quote" and text:
            lines.append(f"> {text}")
            continue
        if block_type == "code_block":
            code_value = payload_map.get("code")
            if isinstance(code_value, str) and code_value.strip():
                lang = str(payload_map.get("language") or "").strip()
                lines.append(f"```{lang}\n{code_value.strip()}\n```")
            continue
        # Ignore list container/table/kv/tool blocks for plain text fallback.
    return "\n".join(line for line in lines if line).strip()


def _coerce_block_list(value: object | None) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, object]] = []
    for entry in value:
        if isinstance(entry, dict):
            blocks.append(entry)
    return blocks


def _inline_nodes_text(nodes: object | None) -> str:
    if not isinstance(nodes, list):
        return ""
    parts: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        text_value = node.get("text")
        if isinstance(text_value, str) and text_value:
            parts.append(text_value)
    return "".join(parts)


def _inline_nodes_to_markdown(nodes: object | None) -> str:
    """Reconstruct markdown from inline nodes: bold, italic, code, link."""
    if not isinstance(nodes, list):
        return ""
    parts: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_type = str(node.get("type") or "").strip().lower()
        text_value = node.get("text")
        if not isinstance(text_value, str) or not text_value:
            continue
        if node_type == "bold":
            parts.append(f"**{text_value}**")
        elif node_type == "italic":
            parts.append(f"*{text_value}*")
        elif node_type == "code":
            parts.append(f"`{text_value}`")
        elif node_type == "link":
            href = str(node.get("href") or "").strip()
            if href:
                parts.append(f"[{text_value}]({href})")
            else:
                parts.append(text_value)
        else:
            parts.append(text_value)
    return "".join(parts)


def _is_text_block(block: Mapping[str, object]) -> bool:
    if str(block.get("type") or "").strip().lower() != "text":
        return False
    payload = block.get("payload")
    if not isinstance(payload, Mapping):
        return False
    return isinstance(payload.get("text"), str)


def _is_rich_text_block(block: Mapping[str, object]) -> bool:
    block_type = str(block.get("type") or "").strip().lower()
    return block_type in {"paragraph", "heading", "list", "list_item", "quote", "code_block"}


def _repair_rich_text_run(run_blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    markdown = extract_text_from_content_blocks(run_blocks).strip()
    if not markdown or not _has_inline_embedded_lists(markdown):
        return run_blocks
    rebuilt = rich_blocks_from_text(markdown)
    return rebuilt or run_blocks


def _has_inline_embedded_lists(text: str) -> bool:
    if not text:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        numbered = list(re.finditer(r"(\d+)\.\s+", stripped))
        if len(numbered) >= 2 and re.search(r"\S\s+\d+\.\s+\S", stripped):
            return True
        bullets = list(re.finditer(r"\s[-*+]\s+\S", stripped))
        if len(bullets) >= 2:
            first = bullets[0]
            before = stripped[: first.start()].strip()
            if before and not re.match(r"^[-*+]\s+", before):
                return True
    return False


def _merge_rebuilt_text_blocks(
    existing_blocks: list[dict[str, object]],
    rebuilt_text_blocks: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not rebuilt_text_blocks:
        return existing_blocks
    if not existing_blocks:
        return rebuilt_text_blocks

    def _is_textual(block: Mapping[str, object]) -> bool:
        block_type = str(block.get("type") or "").strip().lower()
        return block_type == "text" or _is_rich_text_block(block)

    # Insert rebuilt text at the start of the *last* text run so tool/reasoning
    # blocks remain before the final assistant answer in tool-loop turns.
    insert_at: int | None = None
    in_text_run = False
    for idx, block in enumerate(existing_blocks):
        if not isinstance(block, Mapping):
            continue
        is_text = _is_textual(block)
        if is_text:
            if not in_text_run:
                insert_at = idx
                in_text_run = True
            continue
        in_text_run = False

    merged: list[dict[str, object]] = []
    inserted = False
    for idx, block in enumerate(existing_blocks):
        if not isinstance(block, Mapping):
            continue
        if insert_at is not None and idx == insert_at and not inserted:
            merged.extend(rebuilt_text_blocks)
            inserted = True
        is_text = _is_textual(block)
        if is_text:
            continue
        merged.append(block)

    if not inserted:
        # No prior text blocks existed (e.g. tool-only message): append rebuilt text.
        # If there were text blocks but no insertion anchor, this still guarantees one text run.
        merged.extend(rebuilt_text_blocks)
    return merged


def make_tool_use_block(
    *,
    event_id: str,
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    status: str = "finished",
    duration_ms: int = 0,
    remote: dict[str, Any] | None = None,
    block_id: str | None = None,
    created_at: str | None = None,
) -> ContentBlock:
    """Create a tool_use content block."""
    payload: dict[str, Any] = {
        "event_id": event_id,
        "phase": "finished",
        "status": status,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "duration_ms": duration_ms,
        "input": arguments,
    }
    if remote:
        payload["remote"] = remote
        payload["kind"] = "mcp_remote"
    else:
        payload["kind"] = "tool"
    return {
        "block_id": block_id or new_block_id(),
        "type": "tool_use",
        "created_at": created_at or timezone.now().isoformat(),
        "payload": payload,
    }


def make_tool_result_block(
    *,
    event_id: str,
    tool_name: str,
    output: dict[str, Any],
    status: str = "ok",
    duration_ms: int = 0,
    block_id: str | None = None,
    created_at: str | None = None,
) -> ContentBlock:
    """Create a tool_result content block."""
    return {
        "block_id": block_id or new_block_id(),
        "type": "tool_result",
        "created_at": created_at or timezone.now().isoformat(),
        "payload": {
            "event_id": event_id,
            "phase": "finished",
            "status": status,
            "tool_name": tool_name,
            "duration_ms": duration_ms,
            "output": output,
        },
    }


def reconstruct_tool_messages_from_content_blocks(
    content_blocks: list[dict[str, Any]] | None,
    message_body: str = "",
    *,
    max_output_chars: int = 25000,
) -> list[dict[str, object]]:
    """
    Convert content_blocks containing tool_use/tool_result into
    proper LLM message format (assistant with tool_calls + tool responses).

    This function reconstructs the tool call history from stored content_blocks
    so that the LLM can see the full context of what tools were executed and
    their results when resuming a conversation (e.g., after approval flows).

    Following the pattern from build_cached_table_messages():
    - Creates assistant message with tool_calls array
    - Creates separate tool response messages with tool_call_id

    Args:
        content_blocks: List of content block dictionaries from ConversationMessage
        message_body: The message body text (used as content if no tool blocks found)
        max_output_chars: Maximum characters for tool output JSON (default 25000)

    Returns:
        List of messages in LLM format:
        - [assistant_with_tool_calls, tool_response_1, tool_response_2, ...]
        - Empty list if no tool blocks found
    """
    import json

    blocks = _coerce_block_list(content_blocks)
    if not blocks:
        return []

    # Collect tool_use and tool_result blocks, grouped by event_id
    tool_uses: dict[str, dict[str, Any]] = {}  # event_id -> payload
    tool_results: dict[str, dict[str, Any]] = {}  # event_id -> payload

    for block in blocks:
        block_type = str(block.get("type") or "").strip().lower()
        payload = block.get("payload")
        if not isinstance(payload, Mapping):
            continue

        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            continue

        if block_type == "tool_use":
            tool_uses[event_id] = dict(payload)
        elif block_type == "tool_result":
            tool_results[event_id] = dict(payload)

    if not tool_uses:
        # No tool_use blocks found, return empty
        return []

    # Build tool_calls array for assistant message
    tool_calls: list[dict[str, object]] = []
    for event_id, use_payload in tool_uses.items():
        tool_name = str(use_payload.get("tool_name") or "").strip()
        tool_call_id = str(use_payload.get("tool_call_id") or event_id).strip()
        arguments = use_payload.get("input") or {}

        if not tool_name:
            continue

        # Serialize arguments to JSON string
        try:
            args_json = json.dumps(arguments, ensure_ascii=False)
        except (TypeError, ValueError):
            args_json = "{}"

        tool_calls.append({
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": args_json,
            },
        })

    if not tool_calls:
        return []

    # Build assistant message with tool_calls
    messages: list[dict[str, object]] = []
    assistant_message: dict[str, object] = {
        "role": "assistant",
        "content": "",
        "tool_calls": tool_calls,
    }
    messages.append(assistant_message)

    # Build tool response messages (one per tool_use, in same order)
    for event_id, use_payload in tool_uses.items():
        tool_name = str(use_payload.get("tool_name") or "").strip()
        tool_call_id = str(use_payload.get("tool_call_id") or event_id).strip()

        if not tool_name:
            continue

        # Get the corresponding result
        result_payload = tool_results.get(event_id, {})
        output = result_payload.get("output") or {}
        status = str(result_payload.get("status") or "ok").strip()

        # Serialize output to JSON string, respecting max_output_chars
        try:
            output_json = json.dumps(output, ensure_ascii=False)
            if len(output_json) > max_output_chars:
                # Truncate and add indicator
                output_json = output_json[:max_output_chars] + "...[truncated]"
        except (TypeError, ValueError):
            output_json = json.dumps({"status": status, "error": "serialization_failed"})

        tool_message: dict[str, object] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": output_json,
        }
        messages.append(tool_message)

    return messages
