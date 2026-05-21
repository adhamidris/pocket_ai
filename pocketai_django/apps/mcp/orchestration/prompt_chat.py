from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Iterable, Mapping, Sequence

from apps.conversations.models import Conversation
from apps.llm.llm_provider import PromptGenerationError
from apps.rag.observability.logging import structured_log

from ..types import ToolExecutionContext
from .prompt_governor import McpPromptGovernorMixin


logger = logging.getLogger(__name__)


class McpPromptChatMixin(McpPromptGovernorMixin):

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
