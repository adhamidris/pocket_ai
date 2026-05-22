from __future__ import annotations

import logging
from typing import Callable, Iterable, Mapping, Sequence

from django.conf import settings

from apps.conversations.models import Conversation
from apps.rag.observability.logging import structured_log

from ... import prompts
from .payload_compaction import McpPromptPayloadCompactionMixin


logger = logging.getLogger(__name__)


class McpPromptGovernorMixin(McpPromptPayloadCompactionMixin):

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
