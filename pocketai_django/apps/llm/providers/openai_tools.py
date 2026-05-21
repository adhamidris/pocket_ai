from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Iterable, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    httpx = None

from apps.llm.interfaces import BaseMcpProvider
from apps.llm.runtime.retry import (
    PromptGenerationError,
    _ProviderRequestError,
    _call_with_retry,
    _provider_request_error_from_exception,
    _status_is_retryable,
)
from apps.llm.runtime.streaming import _HttpxLineStream, _consume_chat_completion_stream
from apps.llm.telemetry.usage import _estimate_text_tokens, _log_span_debug, _log_usage, _message_char_stats
from apps.rag.rag_logging import structured_log
from core.otel import Status, StatusCode, otel_trace

LOG_TOKEN_ESTIMATE = os.getenv("LLM_LOG_TOKEN_ESTIMATE", "").strip().lower() in {"1", "true", "yes"}
LOG_DEBUG_PAYLOADS = os.getenv("LLM_DEBUG_PAYLOADS", "").strip().lower() in {"1", "true", "yes"}
HTTP_TIMEOUT_CONNECT = os.getenv("LLM_HTTP_TIMEOUT_CONNECT")
HTTP_TIMEOUT_READ = os.getenv("LLM_HTTP_TIMEOUT_READ")

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)


class OpenAIToolsProvider(BaseMcpProvider):
    """
    Placeholder OpenAI provider for MCP tool-calling.

    Later phases will implement the full chat-completions loop with tool
    definitions, streaming, and structured response parsing.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.3,
        top_p: float = 0.9,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise PromptGenerationError("OPENAI_API_KEY is not configured for OpenAIToolsProvider.")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
        self.timeout = timeout
        # Temperature from env var, fallback to parameter default
        env_temp = os.getenv("LLM_TEMPERATURE")
        self.temperature = float(env_temp) if env_temp else temperature
        self.top_p = top_p
        timeout_cfg = self.timeout
        if httpx and (HTTP_TIMEOUT_CONNECT or HTTP_TIMEOUT_READ):
            try:
                connect = float(HTTP_TIMEOUT_CONNECT) if HTTP_TIMEOUT_CONNECT else None
                read = float(HTTP_TIMEOUT_READ) if HTTP_TIMEOUT_READ else None
                timeout_cfg = httpx.Timeout(timeout=self.timeout, connect=connect or self.timeout, read=read or self.timeout)
            except Exception:
                timeout_cfg = self.timeout
        self._http_client = httpx.Client(base_url=self.base_url, timeout=timeout_cfg) if httpx else None

    def chat(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        tools: Iterable[Mapping[str, object]] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
        on_tool_call_delta: Callable[[Mapping[str, object]], None] | None = None,
        response_format: Mapping[str, object] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        streaming = bool(on_stream_delta)
        with TRACER.start_as_current_span("llm.openai.tools") as span:
            if span.is_recording():
                span.set_attribute("llm.provider", "OpenAITools")
                span.set_attribute("llm.model", self.model)
                span.set_attribute("llm.streaming", streaming)
            _log_span_debug("OpenAIToolsProvider.chat", span)

            elapsed_ms: int | None = None

            def _record_latency() -> None:
                if span and span.is_recording() and elapsed_ms is not None:
                    span.set_attribute("llm.duration_ms", elapsed_ms)

            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                payload: dict[str, Any] = {
                    "model": self.model,
                    "messages": [dict(msg) for msg in messages],
                    "stream": streaming,
                }
                if streaming:
                    payload["stream_options"] = {"include_usage": True}
                # Newer models (o1, o3, gpt-5) don't support temperature/top_p
                model_lower = self.model.lower()
                skip_sampling_params = any(
                    model_lower.startswith(prefix)
                    for prefix in ("o1", "o3", "gpt-5")
                )
                if not skip_sampling_params:
                    payload["temperature"] = self.temperature
                    payload["top_p"] = self.top_p
                if not streaming:
                    # For non-streaming planning calls we request structured JSON content
                    # so the provider can return actions/extractions alongside text.
                    payload["response_format"] = OpenAIChatProvider._response_schema()
                if tools:
                    payload["tools"] = list(tools)
                    payload["tool_choice"] = "auto"
                max_tokens_env = os.getenv("OPENAI_MAX_TOKENS")
                if max_tokens_env:
                    try:
                        payload["max_tokens"] = max(1, int(max_tokens_env))
                    except (TypeError, ValueError):
                        structured_log(
                            "llm",
                            "warning",
                            f"Invalid OPENAI_MAX_TOKENS value: {max_tokens_env}",
                            level=logging.WARNING,
                        )

                # Log a compact summary at INFO; heavy details only when enabled.
                if LOG_TOKEN_ESTIMATE and logger.isEnabledFor(logging.DEBUG):
                    try:
                        char_count, token_est = _message_char_stats(payload.get("messages") or [], self.model)
                        logger.debug(
                            "MCP LLM request model=%s tools=%s messages=%s chars=%s tokens≈%s",
                            self.model,
                            [t.get("function", {}).get("name") for t in (tools or [])],
                            len(payload.get("messages") or []),
                            char_count,
                            token_est,
                        )
                    except Exception:  # pragma: no cover - best effort
                        logger.debug("Failed to estimate tokens for MCP request.")
                else:
                    structured_log(
                        "llm",
                        "request",
                        {
                            "provider": "OpenAITools",
                            "model": self.model,
                            "tools": [t.get("function", {}).get("name") for t in (tools or [])],
                            "message_count": len(payload.get("messages") or []),
                            "streaming": streaming,
                        },
                    )
                if LOG_DEBUG_PAYLOADS or logger.isEnabledFor(logging.DEBUG):
                    try:
                        logger.debug("MCP LLM request payload: %s", json.dumps(payload, ensure_ascii=False))
                    except Exception:  # pragma: no cover - log best effort
                        logger.debug("Failed to serialize MCP payload for logging.")

                emitted_output = False

                def _wrapped_stream_delta(chunk: str) -> None:
                    nonlocal emitted_output
                    if chunk:
                        emitted_output = True
                    if on_stream_delta:
                        on_stream_delta(chunk)

                def _wrapped_reasoning_delta(chunk: str) -> None:
                    nonlocal emitted_output
                    if chunk:
                        emitted_output = True
                    if on_reasoning_delta:
                        on_reasoning_delta(chunk)

                def _wrapped_tool_call_start(payload_chunk: Mapping[str, object]) -> None:
                    nonlocal emitted_output
                    emitted_output = True
                    if on_tool_call_start:
                        on_tool_call_start(payload_chunk)

                def _wrapped_tool_call_delta(payload_chunk: Mapping[str, object]) -> None:
                    nonlocal emitted_output
                    emitted_output = True
                    if on_tool_call_delta:
                        on_tool_call_delta(payload_chunk)

                def _request_once() -> tuple[dict[str, Any], str | None, int]:
                    data_local: dict[str, Any] = {}
                    raw_body_local: str | None = None
                    status_code_local = 200
                    if self._http_client:
                        try:
                            if streaming:
                                with self._http_client.stream(
                                    "POST",
                                    "/v1/chat/completions",
                                    json=payload,
                                    headers=headers,
                                    timeout=self.timeout,
                                ) as resp:
                                    status_code_local = resp.status_code
                                    if status_code_local >= 400:
                                        try:
                                            detail = resp.read().decode("utf-8", errors="ignore")[:200]
                                        except Exception:
                                            detail = ""
                                        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                                        raise _ProviderRequestError(
                                            f"OpenAI tools error ({status_code_local}): {detail}",
                                            status_code=status_code_local,
                                            retry_after=retry_after,
                                            retryable=_status_is_retryable(status_code_local),
                                        )
                                    data_local = _consume_chat_completion_stream(
                                        _HttpxLineStream(resp.iter_lines()),
                                        _wrapped_stream_delta,
                                        on_reasoning_delta=_wrapped_reasoning_delta if on_reasoning_delta else None,
                                        on_tool_call_start=_wrapped_tool_call_start if on_tool_call_start else None,
                                        on_tool_call_delta=_wrapped_tool_call_delta if on_tool_call_delta else None,
                                        should_cancel=should_cancel,
                                    )
                            else:
                                resp = self._http_client.post(
                                    "/v1/chat/completions",
                                    json=payload,
                                    headers=headers,
                                    timeout=self.timeout,
                                )
                                status_code_local = resp.status_code
                                raw_body_local = resp.text
                                if status_code_local >= 400:
                                    retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                                    raise _ProviderRequestError(
                                        f"OpenAI tools error ({status_code_local}): {raw_body_local[:200] if raw_body_local else ''}",
                                        status_code=status_code_local,
                                        retry_after=retry_after,
                                        retryable=_status_is_retryable(status_code_local),
                                    )
                        except httpx.HTTPError as exc:
                            raise _provider_request_error_from_exception("OpenAI tools request failed", exc) from exc
                    else:
                        body = json.dumps(payload).encode("utf-8")
                        request = urllib_request.Request(
                            f"{self.base_url}/v1/chat/completions",
                            data=body,
                            headers=headers,
                            method="POST",
                        )
                        try:
                            with urllib_request.urlopen(request, timeout=self.timeout) as resp:
                                status_code_local = getattr(resp, "status", 200)
                                if streaming:
                                    data_local = _consume_chat_completion_stream(
                                        resp,
                                        _wrapped_stream_delta,
                                        on_reasoning_delta=_wrapped_reasoning_delta if on_reasoning_delta else None,
                                        on_tool_call_start=_wrapped_tool_call_start if on_tool_call_start else None,
                                        on_tool_call_delta=_wrapped_tool_call_delta if on_tool_call_delta else None,
                                        should_cancel=should_cancel,
                                    )
                                else:
                                    raw_body_local = resp.read().decode("utf-8")
                        except urllib_error.HTTPError as exc:
                            detail = exc.read().decode("utf-8", errors="ignore")
                            headers_obj = getattr(exc, "headers", None)
                            retry_after = None
                            if hasattr(headers_obj, "get"):
                                retry_after = headers_obj.get("Retry-After") or headers_obj.get("retry-after")
                            raise _ProviderRequestError(
                                f"OpenAI tools error ({exc.code}): {detail.strip()[:200]}",
                                status_code=exc.code,
                                retry_after=str(retry_after) if retry_after else None,
                                retryable=_status_is_retryable(exc.code),
                            ) from exc
                        except urllib_error.URLError as exc:
                            raise _ProviderRequestError(
                                f"OpenAI tools request failed: {exc}",
                                retryable=True,
                            ) from exc

                    if not streaming and status_code_local >= 400:
                        raise _ProviderRequestError(
                            f"OpenAI tools error ({status_code_local}): {raw_body_local[:200] if raw_body_local else status_code_local}",
                            status_code=status_code_local,
                            retryable=_status_is_retryable(status_code_local),
                        )
                    return data_local, raw_body_local, status_code_local

                start_time = time.monotonic()
                data, raw_body, _status_code = _call_with_retry(
                    _request_once,
                    provider="OpenAITools",
                    model=self.model,
                    operation="chat.completions.tools",
                    can_retry=(lambda: not emitted_output) if streaming else None,
                )
                elapsed_ms = int((time.monotonic() - start_time) * 1000)

                if streaming:
                    # In streaming mode we return the assembled assistant message so the
                    # orchestrator can inspect tool_calls or final content.
                    try:
                        logger.debug("MCP LLM stream assembled payload: %s", json.dumps(data, ensure_ascii=False))
                    except Exception:  # pragma: no cover - log best effort
                        logger.debug("Failed to serialize streamed MCP payload for logging.")
                    try:
                        message = (data.get("choices") or [{}])[0].get("message") if isinstance(data, Mapping) else {}
                        content = ""
                        if isinstance(message, Mapping):
                            raw_content = message.get("content")
                            if isinstance(raw_content, str):
                                content = raw_content
                        out_tokens = _estimate_text_tokens(content, self.model) if content else 0
                        if out_tokens:
                            structured_log(
                                "llm",
                                "stream.tokens",
                                {"model": self.model, "tokens": out_tokens},
                            )
                    except Exception:
                        logger.debug("Failed to log streaming token estimate.")
                    _record_latency()
                    return data

                try:
                    data = json.loads(raw_body)
                except ValueError as exc:
                    raise PromptGenerationError("OpenAI tools response was not valid JSON.") from exc
                logger.debug("MCP LLM raw response: %s", raw_body)

                _log_usage("OpenAITools", self.model, data.get("usage") if isinstance(data, Mapping) else None)
                choices = data.get("choices") or []
                if not choices:
                    raise PromptGenerationError("OpenAI tools response did not include choices.")
                message = choices[0].get("message") or {}
                tool_calls = message.get("tool_calls") or []

                # If the model is requesting tool invocations, return the raw
                # Chat Completions envelope so the orchestrator can dispatch calls.
                if tool_calls:
                    _record_latency()
                    return data

                # Final assistant turn: parse structured JSON from the message content.
                content = message.get("content")
                if isinstance(content, list):
                    text = "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
                else:
                    text = str(content or "").strip()
                if not text:
                    _record_latency()
                    return {"role": "assistant", "content": "", "actions": [], "extractions": []}

                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    # Fallback: treat as plain text answer without actions/extractions.
                    parsed = {"response_text": text, "actions": [], "extractions": []}

                response_text = str(parsed.get("response_text") or "").strip()
                response_blocks = None
                for key in ("response_blocks", "responseBlocks", "response_blocks_json"):
                    if key in parsed and parsed.get(key) is not None:
                        response_blocks = parsed.get(key)
                        break

                if elapsed_ms is not None:
                    structured_log(
                        "llm",
                        "latency",
                        {
                            "provider": "openai_tools",
                            "model": self.model,
                            "streaming": streaming,
                            "elapsed_ms": elapsed_ms,
                        },
                    )
                _record_latency()

                return {
                    "role": "assistant",
                    "content": response_text,
                    "actions": parsed.get("actions") or [],
                    "extractions": parsed.get("extractions") or [],
                    "placeholder_response": parsed.get("placeholder_response"),
                    "placeholder_thinking": parsed.get("placeholder_thinking"),
                    "response_blocks": response_blocks,
                }
            except Exception as exc:
                if span and span.is_recording():
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
