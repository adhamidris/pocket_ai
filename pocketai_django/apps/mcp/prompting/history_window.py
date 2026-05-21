from __future__ import annotations

from typing import Mapping, Sequence

from django.conf import settings

STAGE_HISTORY_DEFAULTS: Mapping[str, int] = {
    "initial_pass": 6,
    "tool_iteration": 6,
    "planner": 6,
    "postflight": 6,
}

def _strip_incomplete_tool_chains(entries: list[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """
    Strip assistant messages with tool_calls that are missing their corresponding
    tool responses from the end of the message list.

    This handles the case where limit_messages_for_stage can't fix broken
    tool_call/response chains by going backwards (e.g., when tool responses
    simply don't exist in the message list).
    """
    if not entries:
        return entries

    # Work backwards from the end, tracking which tool_call_ids need responses
    # and removing incomplete chains
    result = list(entries)

    while result:
        # Check if the current messages have incomplete tool chains
        pending_ids: set[str] = set()
        for entry in result:
            role = entry.get("role")
            if role == "assistant":
                tool_calls = entry.get("tool_calls")
                if isinstance(tool_calls, Sequence):
                    for tool_call in tool_calls:
                        if isinstance(tool_call, Mapping):
                            tool_id = str(tool_call.get("id") or "").strip()
                            if tool_id:
                                pending_ids.add(tool_id)
            elif role == "tool":
                tool_call_id = str(entry.get("tool_call_id") or "").strip()
                pending_ids.discard(tool_call_id)

        if not pending_ids:
            # All tool_calls have responses, we're done
            break

        # Find and remove the last assistant message with incomplete tool_calls
        # and any trailing tool responses that belong to it
        removed_any = False
        for i in range(len(result) - 1, -1, -1):
            entry = result[i]
            if entry.get("role") == "assistant" and entry.get("tool_calls"):
                tool_calls = entry.get("tool_calls")
                if isinstance(tool_calls, Sequence):
                    has_incomplete = False
                    for tool_call in tool_calls:
                        if isinstance(tool_call, Mapping):
                            tool_id = str(tool_call.get("id") or "").strip()
                            if tool_id in pending_ids:
                                has_incomplete = True
                                break
                    if has_incomplete:
                        # Remove this assistant message and any following tool messages
                        result = result[:i]
                        removed_any = True
                        break

        if not removed_any:
            # Safety: avoid infinite loop if we can't make progress
            break

    return result


def _history_requires_tool_anchor(entries: Sequence[Mapping[str, object]]) -> bool:
    """
    Detect whether any tool response in the trimmed history is missing its
    preceding assistant message (LLM APIs require the assistant message that
    declared the tool call to appear immediately before the tool response).

    Also detect if any assistant message with tool_calls is missing its
    corresponding tool responses (OpenAI API requires all tool_calls to have
    matching tool response messages).
    """

    pending_ids: set[str] = set()
    for entry in entries:
        role = entry.get("role")
        if role == "assistant":
            tool_calls = entry.get("tool_calls")
            if isinstance(tool_calls, Sequence):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, Mapping):
                        continue
                    tool_id = str(tool_call.get("id") or "").strip()
                    if tool_id:
                        pending_ids.add(tool_id)
        elif role == "tool":
            tool_call_id = str(entry.get("tool_call_id") or "").strip()
            if tool_call_id and tool_call_id not in pending_ids:
                # Tool response without its assistant message
                return True
            # Remove the tool_call_id from pending since we found its response
            pending_ids.discard(tool_call_id)

    # If there are still pending tool_call_ids, it means there are assistant
    # messages with tool_calls but their responses were cut off
    return len(pending_ids) > 0


def limit_messages_for_stage(
    messages: Sequence[Mapping[str, object]],
    *,
    stage: str,
    history_limit: int | None = None,
) -> list[Mapping[str, object]]:
    """
    Trim transcripts so each LLM call only receives the recent context it needs.

    System messages are preserved while non-system entries are windowed based on
    the provided history_limit (or a stage-specific default).
    """

    stage_key = (stage or "").strip().lower()
    default_limit = STAGE_HISTORY_DEFAULTS.get(stage_key, 12)
    override_setting = {
        "initial_pass": "MCP_STAGE_HISTORY_INITIAL_PASS",
        "tool_iteration": "MCP_STAGE_HISTORY_TOOL_ITERATION",
        "planner": "MCP_STAGE_HISTORY_PLANNER",
        "postflight": "MCP_STAGE_HISTORY_POSTFLIGHT",
    }.get(stage_key)
    if history_limit is not None:
        effective_limit = history_limit
    else:
        effective_limit = default_limit
        if override_setting:
            override_value = getattr(settings, override_setting, None)
            if override_value is not None:
                try:
                    effective_limit = int(override_value)
                except (TypeError, ValueError):
                    effective_limit = default_limit
    system_entries: list[Mapping[str, object]] = []
    other_entries: list[Mapping[str, object]] = []
    for entry in messages:
        role = entry.get("role")
        if role == "system":
            system_entries.append(entry)
        else:
            other_entries.append(entry)
    if not effective_limit or effective_limit <= 0:
        return [*system_entries, *other_entries]

    start_index = max(0, len(other_entries) - effective_limit)
    trimmed_history = other_entries[start_index:]
    while start_index > 0 and _history_requires_tool_anchor(trimmed_history):
        start_index -= 1
        trimmed_history = other_entries[start_index:]

    # If we've included all messages but still have broken tool_call/response
    # chains (e.g., assistant message with tool_calls but no responses),
    # strip incomplete tool call chains from the end.
    trimmed_history = _strip_incomplete_tool_chains(list(trimmed_history))

    last_user_entry = None
    for entry in reversed(other_entries):
        if entry.get("role") == "user" and isinstance(entry.get("content"), str) and entry.get("content", "").strip():
            last_user_entry = entry
            break
    if last_user_entry is not None and not any(entry is last_user_entry for entry in trimmed_history):
        trimmed_history = [last_user_entry, *trimmed_history]
    return [*system_entries, *trimmed_history]
