from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.retry import (
    PromptGenerationError,
    _ProviderRequestError,
    _call_with_retry,
    _provider_request_error_from_exception,
    _status_is_retryable,
)
from apps.llm.streaming import _ResponseTextExtractor, _consume_chat_completion_stream
from apps.llm.usage import (
    _estimate_text_tokens,
    _log_usage,
    _message_char_stats,
    _normalize_usage_payload,
)
from apps.rag.rag_logging import structured_log
from core.otel import otel_trace, Status, StatusCode

LOG_TOKEN_ESTIMATE = os.getenv("LLM_LOG_TOKEN_ESTIMATE", "").strip().lower() in {"1", "true", "yes"}
LOG_DEBUG_PAYLOADS = os.getenv("LLM_DEBUG_PAYLOADS", "").strip().lower() in {"1", "true", "yes"}

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)


class OpenAIChatProvider:
    """
    Minimal OpenAI Chat Completions client tailored for the orchestrator.

    Uses the HTTP API directly so we avoid strict SDK version coupling. Responses
    are enforced to JSON (via response_format) and mapped to the orchestrator schema.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.25,
        top_p: float = 0.9,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise PromptGenerationError("OPENAI_API_KEY is not configured.")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p

    def generate(
        self,
        bundle: PromptBundle,
        *,
        on_stream_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        streaming = bool(on_stream_delta)
        with TRACER.start_as_current_span("llm.openai.chat") as span:
            if span.is_recording():
                span.set_attribute("llm.provider", "OpenAIChat")
                span.set_attribute("llm.model", self.model)
                span.set_attribute("llm.streaming", streaming)
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self._system_prompt(bundle)},
                    {"role": "user", "content": self._user_payload(bundle)},
                ],
                "temperature": self.temperature,
                "top_p": self.top_p,
                "response_format": self._response_schema(),
            }
            if streaming:
                payload["stream"] = True
                payload["stream_options"] = {"include_usage": True}
            if LOG_TOKEN_ESTIMATE and logger.isEnabledFor(logging.DEBUG):
                try:
                    char_count, token_est = _message_char_stats(payload.get("messages") or [], self.model)
                    logger.debug("LLM request model=%s chars=%s tokens≈%s", self.model, char_count, token_est)
                except Exception:  # pragma: no cover - best effort
                    logger.debug("Failed to estimate tokens for LLM request.")
            if LOG_DEBUG_PAYLOADS or logger.isEnabledFor(logging.DEBUG):
                try:
                    logger.debug("LLM request payload: %s", json.dumps(payload, ensure_ascii=False))
                except Exception:  # pragma: no cover - log best effort
                    logger.debug("Failed to serialize LLM payload for logging.")

            body = json.dumps(payload).encode("utf-8")
            request = urllib_request.Request(
                f"{self.base_url}/v1/chat/completions",
                data=body,
                headers=headers,
                method="POST",
            )

            emitted_output = False

            def _stream_delta(chunk: str) -> None:
                nonlocal emitted_output
                if chunk:
                    emitted_output = True
                if on_stream_delta:
                    on_stream_delta(chunk)

            def _reasoning_delta(chunk: str) -> None:
                nonlocal emitted_output
                if chunk:
                    emitted_output = True
                if on_reasoning_delta:
                    on_reasoning_delta(chunk)

            def _request_once() -> tuple[int, str | None, Mapping[str, Any] | None]:
                try:
                    with urllib_request.urlopen(request, timeout=self.timeout) as resp:
                        status_code_local = getattr(resp, "status", 200)
                        if streaming:
                            data_local = _consume_chat_completion_stream(
                                resp,
                                _stream_delta,
                                on_reasoning_delta=_reasoning_delta if on_reasoning_delta else None,
                                should_cancel=should_cancel,
                            )
                            return status_code_local, None, data_local
                        return status_code_local, resp.read().decode("utf-8"), None
                except urllib_error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="ignore")
                    headers_obj = getattr(exc, "headers", None)
                    retry_after = None
                    if hasattr(headers_obj, "get"):
                        retry_after = headers_obj.get("Retry-After") or headers_obj.get("retry-after")
                    raise _ProviderRequestError(
                        f"OpenAI error ({exc.code}): {detail.strip()[:200]}",
                        status_code=exc.code,
                        retry_after=str(retry_after) if retry_after else None,
                        retryable=_status_is_retryable(exc.code),
                    ) from exc
                except urllib_error.URLError as exc:
                    raise _ProviderRequestError(
                        f"OpenAI request failed: {exc}",
                        retryable=True,
                    ) from exc

            try:
                status_code, raw_body, data = _call_with_retry(
                    _request_once,
                    provider="OpenAIChat",
                    model=self.model,
                    operation="chat.completions",
                    can_retry=(lambda: not emitted_output) if streaming else None,
                )
            except PromptGenerationError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

            if status_code >= 400:
                span.set_status(Status(StatusCode.ERROR, str(status_code)))
                raise PromptGenerationError(f"OpenAI error ({status_code}): {raw_body[:200] if raw_body else status_code}")

            if streaming:
                usage_payload = None
                if should_cancel and should_cancel():
                    usage_payload = _normalize_usage_payload(
                        data.get("usage") if isinstance(data, Mapping) else None,
                        provider="OpenAIChat",
                        model=self.model,
                    )
                    payload_out: dict[str, Any] = {"response_text": "", "actions": [], "extractions": []}
                    if usage_payload:
                        payload_out["llm_usage"] = usage_payload
                    return payload_out
                try:
                    content = self._extract_content(data)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, "stream_missing_content"))
                    raise PromptGenerationError("OpenAI streaming response missing content.") from exc
                usage_payload = _normalize_usage_payload(
                    data.get("usage") if isinstance(data, Mapping) else None,
                    provider="OpenAIChat",
                    model=self.model,
                )
                if logger.isEnabledFor(logging.DEBUG):
                    try:
                        logger.debug("OpenAI stream assembled payload: %s", json.dumps(data, ensure_ascii=False))
                    except Exception:  # pragma: no cover - log best effort
                        logger.debug("Failed to serialize OpenAI stream payload.")
                if LOG_TOKEN_ESTIMATE and logger.isEnabledFor(logging.DEBUG):
                    try:
                        out_tokens = _estimate_text_tokens(content, self.model) if content else 0
                        if out_tokens:
                            logger.debug("OpenAI stream tokens≈%s model=%s", out_tokens, self.model)
                    except Exception:
                        logger.debug("Failed to log streaming token estimate.")
            else:
                usage_payload = None
                try:
                    data = json.loads(raw_body)
                except ValueError as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, "invalid_json"))
                    raise PromptGenerationError("OpenAI response was not valid JSON.") from exc

                _log_usage("OpenAIChat", self.model, data.get("usage") if isinstance(data, Mapping) else None)
                usage_payload = _normalize_usage_payload(
                    data.get("usage") if isinstance(data, Mapping) else None,
                    provider="OpenAIChat",
                    model=self.model,
                )
                structured_log(
                    "llm",
                    "raw_response",
                    raw_body,
                    context={"provider": "OpenAIChat", "model": self.model},
                    logger_obj=logger,
                )

                content = self._extract_content(data)
            try:
                parsed = json.loads(content)
                if span.is_recording() and isinstance(parsed, Mapping):
                    span.set_attribute("llm.response_chars", len(content or ""))
                if usage_payload and isinstance(parsed, dict):
                    parsed["llm_usage"] = usage_payload
                return parsed
            except json.JSONDecodeError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, "invalid_response_json"))
                raise PromptGenerationError("OpenAI response did not return valid JSON output.") from exc

    def _system_prompt(self, bundle: PromptBundle) -> str:
        schema_hint = (
            "You must reply with JSON matching the schema provided. "
            "Never include Markdown or prose outside of the JSON object."
        )
        return f"{bundle.system_prompt}\n\n{schema_hint}"

    def _user_payload(self, bundle: PromptBundle) -> str:
        return bundle.user_prompt.strip()

    @staticmethod
    def _response_schema() -> Mapping[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "ai_orchestration_response",
                "schema": {
                    "type": "object",
                    "properties": {
                        "response_text": {"type": "string"},
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["action", "payload"],
                            },
                        },
                        "extractions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["type", "payload"],
                            },
                        },
                    },
                    "required": ["response_text", "actions", "extractions"],
                },
            },
        }

    @staticmethod
    def _extract_content(payload: Mapping[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise PromptGenerationError("OpenAI response did not include choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
        return str(content or "").strip()


class DeepSeekChatProvider(OpenAIChatProvider):
    """
    DeepSeek API adapter (compatible with OpenAI-style chat completions).

    Uses the official OpenAI SDK with DeepSeek's base URL so we can opt into
    real streaming. The provider still expects JSON output matching the
    orchestrator schema, but `response_text` deltas are surfaced via the
    `on_stream_delta` callback whenever streaming is enabled.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.25,
        top_p: float = 0.9,
    ) -> None:
        key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise PromptGenerationError("DEEPSEEK_API_KEY is not configured.")
        model_name = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        base = (base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise PromptGenerationError("Install the `openai` package to use DeepSeek streaming.") from exc

        self.api_key = key
        self.model = model_name
        self.base_url = base
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)

    def generate(
        self,
        bundle: PromptBundle,
        *,
        on_stream_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        streaming = bool(on_stream_delta)
        with TRACER.start_as_current_span("llm.deepseek.chat") as span:
            if span.is_recording():
                span.set_attribute("llm.provider", "DeepSeekChat")
                span.set_attribute("llm.model", self.model)
                span.set_attribute("llm.streaming", streaming)
            messages = [
                {"role": "system", "content": self._system_prompt(bundle)},
                {"role": "user", "content": self._user_payload(bundle)},
            ]
            request_payload = {
                "model": self.model,
                "temperature": self.temperature,
                "top_p": self.top_p,
                "stream": streaming,
                "messages": messages,
            }
            self._log_pretty("DeepSeek request payload", request_payload)
            usage_payload = None
            if streaming:
                content, usage_payload = self._generate_streaming(
                    messages,
                    on_stream_delta,
                    on_reasoning_delta=on_reasoning_delta,
                    should_cancel=should_cancel,
                )
            else:
                content, usage_payload = self._generate_blocking(messages)
            self._log_pretty("DeepSeek raw response", content)
            parsed = self._parse_payload(content)
            if usage_payload and isinstance(parsed, dict):
                parsed["llm_usage"] = usage_payload
            self._log_pretty("DeepSeek parsed payload", parsed)
            if span.is_recording():
                span.set_attribute("llm.response_chars", len(content or ""))
            return parsed

    def _generate_blocking(self, messages: list[Mapping[str, str]]) -> tuple[str, dict[str, object] | None]:
        def _request_once() -> Any:
            try:
                return self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    stream=False,
                )
            except Exception as exc:
                raise _provider_request_error_from_exception("DeepSeek request failed", exc) from exc

        response = _call_with_retry(
            _request_once,
            provider="DeepSeekChat",
            model=self.model,
            operation="chat.completions",
        )
        usage_payload = _normalize_usage_payload(
            getattr(response, "usage", None),
            provider="DeepSeekChat",
            model=self.model,
        )
        try:
            _log_usage("DeepSeekChat", self.model, getattr(response, "usage", None))
        except Exception:
            pass
        content = self._stringify_message_content(getattr(response.choices[0], "message", None))
        return content, usage_payload

    def _generate_streaming(
        self,
        messages: list[Mapping[str, str]],
        on_stream_delta: Callable[[str], None],
        *,
        on_reasoning_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        emitted_output = False

        def _request_once() -> tuple[str, dict[str, object] | None]:
            nonlocal emitted_output
            streamed_response_text_parts: list[str] = []
            assembled: list[str] = []
            usage_payload: dict[str, object] | None = None

            def _emit_response_text(chunk: str) -> None:
                nonlocal emitted_output
                if not chunk:
                    return
                emitted_output = True
                streamed_response_text_parts.append(chunk)
                on_stream_delta(chunk)

            extractor = _ResponseTextExtractor(_emit_response_text)
            try:
                stream = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                for chunk in stream:
                    if should_cancel and should_cancel():
                        break
                    if usage_payload is None:
                        usage_payload = _normalize_usage_payload(
                            getattr(chunk, "usage", None),
                            provider="DeepSeekChat",
                            model=self.model,
                        )
                    reasoning_delta = getattr(getattr(chunk.choices[0], "delta", None), "reasoning_content", None)
                    if isinstance(reasoning_delta, str) and reasoning_delta:
                        emitted_output = True
                        if on_reasoning_delta:
                            try:
                                on_reasoning_delta(reasoning_delta)
                            except Exception:  # pragma: no cover - safeguard user callbacks
                                logger.exception("Streaming callback failed while emitting reasoning delta chunk.")
                    delta_text = self._stringify_message_content(getattr(chunk.choices[0], "delta", None))
                    if not delta_text:
                        continue
                    assembled.append(delta_text)
                    extractor.feed(delta_text)
            except Exception as exc:
                raise _provider_request_error_from_exception("DeepSeek streaming request failed", exc) from exc
            finally:
                extractor.flush()

            if should_cancel and should_cancel():
                return (
                    json.dumps(
                        {
                            "response_text": "".join(streamed_response_text_parts),
                            "actions": [],
                            "extractions": [],
                        },
                        ensure_ascii=False,
                    ),
                    usage_payload,
                )

            content = "".join(assembled).strip()
            if not content:
                raise _ProviderRequestError("DeepSeek response was empty.", retryable=True)
            return content, usage_payload

        return _call_with_retry(
            _request_once,
            provider="DeepSeekChat",
            model=self.model,
            operation="chat.completions.stream",
            can_retry=lambda: not emitted_output,
        )

    @staticmethod
    def _stringify_message_content(payload: Any) -> str:
        if payload is None:
            return ""
        content = getattr(payload, "content", payload)
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                text = getattr(part, "text", None)
                if text:
                    parts.append(str(text))
            return "".join(parts)
        return str(content or "")

    @staticmethod
    def _parse_payload(content: str) -> Mapping[str, Any]:
        content = content.strip()
        if not content:
            raise PromptGenerationError("DeepSeek response was empty.")

        def _strip_code_fences(value: str) -> str:
            """
            DeepSeek sometimes wraps JSON in Markdown fences:
            ```json
            {...}
            ```
            """

            if not value.startswith("```"):
                return value
            match = re.match(r"^```[a-zA-Z0-9_-]*\n(?P<body>.*)\n```$", value, flags=re.DOTALL)
            if not match:
                return value
            return str(match.group("body") or "").strip()

        def _extract_json_object(value: str) -> str | None:
            start = value.find("{")
            end = value.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            return value[start : end + 1].strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            cleaned = _strip_code_fences(content)
            if cleaned and cleaned != content:
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass

            extracted = _extract_json_object(cleaned)
            if extracted and extracted != cleaned:
                try:
                    return json.loads(extracted)
                except json.JSONDecodeError:
                    pass

            structured_log(
                "llm",
                "warning",
                "DeepSeek returned non-JSON content; using text fallback.",
                level=logging.WARNING,
            )
            return {
                "response_text": content,
                "actions": [],
                "extractions": [],
            }

    @staticmethod
    def _log_pretty(label: str, payload: Any) -> None:
        try:
            if isinstance(payload, str):
                payload = payload.strip()
                if payload:
                    try:
                        as_json = json.loads(payload)
                    except json.JSONDecodeError:
                        formatted = payload
                    else:
                        formatted = json.dumps(as_json, indent=2, ensure_ascii=False)
                else:
                    formatted = ""
            else:
                formatted = json.dumps(payload, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            formatted = str(payload)
        stage = label.lower().replace(" ", "_")
        structured_log("llm", stage, formatted, logger_obj=logger)

    def _system_prompt(self, bundle: PromptBundle) -> str:
        schema_hint = (
            "You must reply with JSON matching the schema provided. "
            "Never include Markdown or prose outside of the JSON object."
        )
        return f"{bundle.system_prompt}\n\n{schema_hint}"

    def _user_payload(self, bundle: PromptBundle) -> str:
        return bundle.user_prompt.strip()

    @staticmethod
    def _response_schema() -> Mapping[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "ai_orchestration_response",
                "schema": {
                    "type": "object",
                    "properties": {
                        "response_text": {"type": "string"},
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["action", "payload"],
                            },
                        },
                        "extractions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["type", "payload"],
                            },
                        },
                    },
                    "required": ["response_text", "actions", "extractions"],
                },
            },
        }
