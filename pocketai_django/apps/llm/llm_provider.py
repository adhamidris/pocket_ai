from __future__ import annotations

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
import time
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Protocol

from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    httpx = None

try:  # optional dependency for accurate token estimates
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover - optional
    tiktoken = None

from apps.llm.ai_prompt_builder import PromptBundle
from apps.rag.rag_logging import structured_log
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span, Status, StatusCode

# Optional flag to enable token estimation logs (guarded by DEBUG level as well).
LOG_TOKEN_ESTIMATE = os.getenv("LLM_LOG_TOKEN_ESTIMATE", "").strip().lower() in {"1", "true", "yes"}
# Optional flag to force debug payload logging even if DEBUG level is off.
LOG_DEBUG_PAYLOADS = os.getenv("LLM_DEBUG_PAYLOADS", "").strip().lower() in {"1", "true", "yes"}
# Optional HTTP timeout overrides (seconds) when using httpx client.
HTTP_TIMEOUT_CONNECT = os.getenv("LLM_HTTP_TIMEOUT_CONNECT")
HTTP_TIMEOUT_READ = os.getenv("LLM_HTTP_TIMEOUT_READ")


logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

_MCP_PROVIDER_SINGLETON: BaseMcpProvider | None = None
_MCP_PROVIDER_LOCK = Lock()


def _format_trace_id(value: int) -> str:
    """Return a hex-encoded trace id for debug logs."""

    return f"{value:032x}"


def _format_span_id(value: int) -> str:
    """Return a hex-encoded span id for debug logs."""

    return f"{value:016x}"


def _log_span_debug(label: str, span: Span | None) -> None:
    """
    Emit a lightweight debug log with the active span identifiers.

    Useful when confirming that instrumentation blocks are running and that
    spans are parented correctly without having to open Jaeger for every test.
    """

    if not span or not logger.isEnabledFor(logging.DEBUG):
        return
    ctx = span.get_span_context()
    logger.debug(
        "%s trace_id=%s span_id=%s",
        label,
        _format_trace_id(ctx.trace_id),
        _format_span_id(ctx.span_id),
    )


def _select_encoder(model_name: str | None):
    if not tiktoken:
        return None
    try:
        return tiktoken.encoding_for_model(model_name) if model_name else tiktoken.get_encoding("cl100k_base")
    except Exception:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def _message_char_stats(messages: Iterable[Mapping[str, object]], model: str | None = None) -> tuple[int, int]:
    """
    Estimate prompt size/tokens so we can log payloads. Falls back to chars/4 when tiktoken is unavailable.
    """

    total_chars = 0
    total_tokens = 0
    encoder = _select_encoder(model)
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, Mapping):
                    text = part.get("text")
                    if isinstance(text, str):
                        total_chars += len(text)
                        if encoder:
                            try:
                                total_tokens += len(encoder.encode(text))
                            except Exception:
                                pass
        elif isinstance(content, str):
            total_chars += len(content)
            if encoder:
                try:
                    total_tokens += len(encoder.encode(content))
                except Exception:
                    pass
    if not encoder:
        total_tokens = max(1, total_chars // 4) if total_chars else 0
    return total_chars, total_tokens


def _estimate_text_tokens(text: str, model: str | None = None) -> int:
    encoder = _select_encoder(model)
    if not text:
        return 0
    if encoder:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _log_usage(label: str, model: str | None, usage: Mapping[str, object] | None) -> None:
    if not usage:
        return
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    structured_log(
        "llm",
        "usage",
        {
            "provider": label,
            "model": model,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
        logger_obj=logger,
    )


def _coerce_usage_mapping(usage: object | None) -> Mapping[str, object] | None:
    if not usage:
        return None
    if isinstance(usage, Mapping):
        return usage
    for attr in ("model_dump", "to_dict", "dict"):
        method = getattr(usage, attr, None)
        if callable(method):
            try:
                value = method()
            except Exception:
                continue
            if isinstance(value, Mapping):
                return value
    raw = getattr(usage, "__dict__", None)
    if isinstance(raw, Mapping):
        return raw
    return None


def _normalize_usage_payload(
    usage: object | None,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, object] | None:
    usage_map = _coerce_usage_mapping(usage)
    if not usage_map:
        return None
    prompt = usage_map.get("prompt_tokens")
    completion = usage_map.get("completion_tokens")
    total = usage_map.get("total_tokens")
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
    payload: dict[str, object] = {
        "prompt_tokens": prompt_val,
        "completion_tokens": completion_val,
        "total_tokens": total_val,
    }
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    return payload


class PromptGenerationError(RuntimeError):
    """Raised when the LLM provider fails to respond."""


class BaseLLMProvider(Protocol):
    """Interface for future provider implementations (OpenAI, Azure, etc.)."""

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
        ...


class BaseMcpProvider(Protocol):
    """
    Tool-calling provider interface used by the MCP orchestrator.

    Implementations should call the underlying chat-completions API with the
    supplied messages and tool definitions, then return the parsed assistant
    payload (content + tool_calls metadata).
    """

    def chat(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        tools: Iterable[Mapping[str, object]] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
        on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:  # pragma: no cover - interface only
        ...


@dataclass
class StubLLMProvider:
    """
    Default no-op provider so the orchestrator can be tested without a real model.

    The stub simply raises PromptGenerationError to signal that a fallback strategy
    (heuristics) should be used.
    """

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
        raise PromptGenerationError("LLM provider not configured")


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

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
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

            try:
                with urllib_request.urlopen(request, timeout=self.timeout) as resp:
                    status_code = getattr(resp, "status", 200)
                    if streaming:
                        data = _consume_chat_completion_stream(resp, on_stream_delta)
                        raw_body = None
                    else:
                        raw_body = resp.read().decode("utf-8")
            except urllib_error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, detail))
                raise PromptGenerationError(
                    f"OpenAI error ({exc.code}): {detail.strip()[:200]}"
                ) from exc
            except urllib_error.URLError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise PromptGenerationError(f"OpenAI request failed: {exc}") from exc

            if status_code >= 400:
                span.set_status(Status(StatusCode.ERROR, str(status_code)))
                raise PromptGenerationError(f"OpenAI error ({status_code}): {raw_body[:200] if raw_body else status_code}")

            if streaming:
                usage_payload = None
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

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
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
                content, usage_payload = self._generate_streaming(messages, on_stream_delta)
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
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                stream=False,
            )
        except Exception as exc:
            raise PromptGenerationError(f"DeepSeek request failed: {exc}") from exc
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
    ) -> tuple[str, dict[str, object] | None]:
        extractor = _ResponseTextExtractor(on_stream_delta)
        assembled: list[str] = []
        usage_payload = None
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
                if usage_payload is None:
                    usage_payload = _normalize_usage_payload(
                        getattr(chunk, "usage", None),
                        provider="DeepSeekChat",
                        model=self.model,
                    )
                delta_text = self._stringify_message_content(getattr(chunk.choices[0], "delta", None))
                if not delta_text:
                    continue
                assembled.append(delta_text)
                extractor.feed(delta_text)
        except Exception as exc:
            raise PromptGenerationError(f"DeepSeek streaming request failed: {exc}") from exc
        finally:
            extractor.flush()

        content = "".join(assembled).strip()
        if not content:
            raise PromptGenerationError("DeepSeek response was empty.")
        return content, usage_payload

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
        try:
            return json.loads(content)
        except json.JSONDecodeError:
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

class _ResponseTextExtractor:
    """
    Incrementally parses the JSON string to stream the `response_text` field.

    The extractor scans for the `"response_text"` key, then decodes the string
    value while respecting JSON escape sequences and unicode escapes. As soon as
    new characters are available, they are emitted via the provided callback.
    """

    TARGET = '"response_text"'

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self._key_index = 0
        self._state = "search"  # search -> post_key -> seek_quote -> in_string -> done
        self._in_string = False
        self._escape = False
        self._unicode_digits: list[str] | None = None
        self._buffer: list[str] = []
        self._completed = False

    def feed(self, chunk: str) -> None:
        if self._completed or not chunk:
            return
        for ch in chunk:
            if self._completed:
                break
            self._consume(ch)
        self._flush()

    def flush(self) -> None:
        self._flush()

    def _consume(self, ch: str) -> None:
        if self._completed:
            return
        if self._in_string:
            self._consume_string(ch)
            return
        if self._state == "search":
            self._scan_key(ch)
            return
        if self._state == "post_key":
            if ch == ":":
                self._state = "seek_quote"
            elif ch in " \t\r\n":
                return
            else:
                self._reset()
                self._scan_key(ch)
            return
        if self._state == "seek_quote":
            if ch == '"':
                self._in_string = True
            elif ch in " \t\r\n":
                return
            else:
                self._reset()
                self._scan_key(ch)

    def _consume_string(self, ch: str) -> None:
        if self._unicode_digits is not None:
            self._unicode_digits.append(ch)
            if len(self._unicode_digits) == 4:
                try:
                    codepoint = int("".join(self._unicode_digits), 16)
                    self._buffer.append(chr(codepoint))
                except ValueError:
                    pass
                self._unicode_digits = None
            return
        if self._escape:
            self._escape = False
            if ch == "u":
                self._unicode_digits = []
                return
            self._buffer.append(self._escape_map(ch))
            return
        if ch == "\\":
            self._escape = True
            return
        if ch == '"':
            self._in_string = False
            self._completed = True
            return
        self._buffer.append(ch)

    def _scan_key(self, ch: str) -> None:
        target = self.TARGET
        if ch == target[self._key_index]:
            self._key_index += 1
            if self._key_index == len(target):
                self._state = "post_key"
        else:
            self._key_index = 1 if ch == target[0] else 0

    def _reset(self) -> None:
        self._key_index = 0
        self._state = "search"

    def _flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        try:
            self.emit(text)
        except Exception:  # pragma: no cover - safeguard user callbacks
            logger.exception("Streaming callback failed while emitting response_text delta.")

    @staticmethod
    def _escape_map(ch: str) -> str:
        mapping = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        return mapping.get(ch, ch)

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


def _emit_stream_chunks(callback: Callable[[str], None], text: str, *, chunk_size: int = 64) -> None:
    """
    Emit a text payload to a streaming callback in word-safe chunks.

    Mirrors the SSE chunking strategy used by the chat portal so the MCP
    providers can surface incremental deltas even when the underlying HTTP
    response is non-streaming.
    """

    clean = (text or "").strip()
    if not clean:
        return
    words = clean.split()
    if not words:
        return

    current: list[str] = []
    current_len = 0
    for word in words:
        if not current:
            current.append(word)
            current_len = len(word)
            continue
        projected = current_len + 1 + len(word)
        if projected <= chunk_size:
            current.append(word)
            current_len = projected
        else:
            try:
                callback(" ".join(current))
            except Exception:  # pragma: no cover - safeguard user callbacks
                logger.exception("Streaming callback failed while emitting chunk.")
            current = [word]
            current_len = len(word)

    if current:
        try:
            callback(" ".join(current))
        except Exception:  # pragma: no cover - safeguard user callbacks
            logger.exception("Streaming callback failed while emitting final chunk.")


class _HttpxLineStream:
    """
    Adapter to present httpx.iter_lines() as a file-like object with readline().
    """

    def __init__(self, iterator: Iterable[bytes] | Iterable[str]) -> None:
        self._iterator = iter(iterator)

    def readline(self) -> bytes:
        try:
            line = next(self._iterator)
        except StopIteration:
            return b""
        if isinstance(line, str):
            # httpx.iter_lines() yields strings without trailing newlines; preserve
            # blank lines by emitting a newline so the SSE parser can flush buffers.
            return (line + "\n").encode("utf-8")
        # For byte lines, also ensure a newline delimiter so blank lines are honored.
        return line if line.endswith(b"\n") else line + b"\n"


def _iter_sse_events(stream) -> Iterable[str]:
    """
    Yield decoded payload strings from a server-sent events stream.

    The OpenAI-compatible APIs send newline-delimited `data:` entries followed by
    a blank line. Each yielded string corresponds to the bytes after `data:`
    (with `[DONE]` filtered out).
    """

    buffer: list[str] = []
    while True:
        raw_line = stream.readline()
        if not raw_line:
            if buffer:
                yield "\n".join(buffer)
            break
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            value = value.rstrip("\r\n")
            if value == "[DONE]":
                if buffer:
                    yield "\n".join(buffer)
                    buffer.clear()
                break
            if value:
                buffer.append(value)
            continue
        if not line.strip():
            if buffer:
                yield "\n".join(buffer)
                buffer.clear()
            continue


def _merge_stream_tool_call(
    store: dict[int, dict[str, object]],
    delta: Mapping[str, object],
) -> tuple[dict[str, object], bool]:
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
    if isinstance(func_args, str) and func_args:
        existing = function_block.get("arguments") or ""
        function_block["arguments"] = f"{existing}{func_args}"
    name_now = function_block.get("name")
    fired = bool(name_now and name_now != previous_name)
    return state, fired


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
    on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
) -> dict[str, object]:
    """
    Assemble a chat-completions style payload from a streaming HTTP response.

    The returned structure mirrors the non-streaming API response so callers can
    reuse the same parsing logic. When `on_stream_delta` is provided, partial
    assistant content is emitted as soon as it is available.
    """

    text_parts: list[str] = []
    tool_calls: dict[int, dict[str, object]] = {}
    role: str | None = None
    finish_reason: str | None = None
    last_message_content: str | None = None
    last_message_tool_calls: list[dict[str, object]] | None = None
    last_message_payload: dict[str, object] | None = None
    usage_payload: Mapping[str, object] | None = None
    model_name: str | None = None

    def _normalize_delta(chunk: str) -> str:
        return chunk or ""

    start_first = time.monotonic()
    first_delta_at: float | None = None

    for payload in _iter_sse_events(stream):
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

        for tool_delta in delta.get("tool_calls") or []:
            if isinstance(tool_delta, Mapping):
                state, fired = _merge_stream_tool_call(tool_calls, tool_delta)
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
            msg_tools = message_block.get("tool_calls")
            if isinstance(msg_tools, list) and msg_tools:
                last_message_tool_calls = msg_tools

    assembled_text = "".join(text_parts).strip()
    if not assembled_text and last_message_content:
        assembled_text = last_message_content

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
        on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
        response_format: Mapping[str, object] | None = None,
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

                data: dict[str, Any]
                raw_body: str | None = None
                start_time = time.monotonic()
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
                                status_code = resp.status_code
                                if status_code >= 400:
                                    try:
                                        detail = resp.read().decode("utf-8", errors="ignore")[:200]
                                    except Exception:
                                        detail = ""
                                    raise PromptGenerationError(f"OpenAI tools error ({status_code}): {detail}")
                                data = _consume_chat_completion_stream(
                                    _HttpxLineStream(resp.iter_lines()),
                                    on_stream_delta,
                                    on_tool_call_start=on_tool_call_start,
                                )
                        else:
                            resp = self._http_client.post(
                                "/v1/chat/completions",
                                json=payload,
                                headers=headers,
                                timeout=self.timeout,
                            )
                            status_code = resp.status_code
                            raw_body = resp.text
                            if status_code >= 400:
                                raise PromptGenerationError(f"OpenAI tools error ({status_code}): {raw_body[:200]}")
                        elapsed_ms = int((time.monotonic() - start_time) * 1000)
                    except httpx.HTTPError as exc:
                        raise PromptGenerationError(f"OpenAI tools request failed: {exc}") from exc
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
                            if streaming:
                                data = _consume_chat_completion_stream(
                                    resp,
                                    on_stream_delta,
                                    on_tool_call_start=on_tool_call_start,
                                )
                                raw_body = None
                            else:
                                raw_body = resp.read().decode("utf-8")
                                status_code = getattr(resp, "status", 200)
                    except urllib_error.HTTPError as exc:
                        detail = exc.read().decode("utf-8", errors="ignore")
                        raise PromptGenerationError(
                            f"OpenAI tools error ({exc.code}): {detail.strip()[:200]}"
                        ) from exc
                    except urllib_error.URLError as exc:
                        raise PromptGenerationError(f"OpenAI tools request failed: {exc}") from exc
                    if not streaming and status_code >= 400:
                        raise PromptGenerationError(
                            f"OpenAI tools error ({status_code}): {raw_body[:200] if raw_body else status_code}"
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


class DeepSeekToolsProvider(BaseMcpProvider):
    """
    DeepSeek tools-capable provider for the MCP orchestrator.

    Uses the HTTP Chat Completions API (OpenAI-compatible) with tool calling
    enabled. Behavior mirrors OpenAIToolsProvider so the orchestrator can treat
    providers interchangeably.
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
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise PromptGenerationError("DEEPSEEK_API_KEY is not configured for DeepSeekToolsProvider.")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
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
        on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        streaming = bool(on_stream_delta)
        with TRACER.start_as_current_span("llm.deepseek.tools") as span:
            if span.is_recording():
                span.set_attribute("llm.provider", "DeepSeekTools")
                span.set_attribute("llm.model", self.model)
                span.set_attribute("llm.streaming", streaming)
            _log_span_debug("DeepSeekToolsProvider.chat", span)

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
                    "temperature": self.temperature,
                    "top_p": self.top_p,
                    "stream": streaming,
                }
                if streaming:
                    payload["stream_options"] = {"include_usage": True}
                if tools:
                    payload["tools"] = list(tools)
                    payload["tool_choice"] = "auto"
                # Note: DeepSeek has disabled response_format support ("This response_format type
                # is unavailable now"). Skip response_format entirely and rely on prompt instructions
                # for JSON output. The verification/planner prompts already request JSON format.

                # Compact summary at INFO; heavy logs only when enabled.
                if LOG_TOKEN_ESTIMATE and logger.isEnabledFor(logging.DEBUG):
                    try:
                        char_count, token_est = _message_char_stats(payload.get("messages") or [], self.model)
                        logger.debug(
                            "DeepSeek MCP request model=%s tools=%s messages=%s chars=%s tokens≈%s",
                            self.model,
                            [t.get("function", {}).get("name") for t in (tools or [])],
                            len(payload.get("messages") or []),
                            char_count,
                            token_est,
                        )
                    except Exception:  # pragma: no cover - best effort
                        logger.debug("Failed to estimate tokens for DeepSeek MCP request.")
                else:
                    structured_log(
                        "llm",
                        "request",
                        {
                            "provider": "DeepSeekTools",
                            "model": self.model,
                            "tools": [t.get("function", {}).get("name") for t in (tools or [])],
                            "message_count": len(payload.get("messages") or []),
                            "streaming": streaming,
                        },
                    )
                if LOG_DEBUG_PAYLOADS or logger.isEnabledFor(logging.DEBUG):
                    try:
                        logger.debug("DeepSeek MCP request payload: %s", json.dumps(payload, ensure_ascii=False))
                    except Exception:  # pragma: no cover - log best effort
                        logger.debug("Failed to serialize DeepSeek MCP payload for logging.")

                data: dict[str, Any]
                raw_body: str | None = None
                start_time = time.monotonic()
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
                                status_code = resp.status_code
                                if status_code >= 400:
                                    try:
                                        detail = resp.read().decode("utf-8", errors="ignore")[:200]
                                    except Exception:
                                        detail = ""
                                    raise PromptGenerationError(f"DeepSeek tools error ({status_code}): {detail}")
                                data = _consume_chat_completion_stream(
                                    _HttpxLineStream(resp.iter_lines()),
                                    on_stream_delta,
                                    on_tool_call_start=on_tool_call_start,
                                )
                        else:
                            resp = self._http_client.post(
                                "/v1/chat/completions",
                                json=payload,
                                headers=headers,
                                timeout=self.timeout,
                            )
                            status_code = resp.status_code
                            raw_body = resp.text
                            if status_code >= 400:
                                raise PromptGenerationError(f"DeepSeek tools error ({status_code}): {raw_body[:200]}")
                        elapsed_ms = int((time.monotonic() - start_time) * 1000)
                    except httpx.HTTPError as exc:
                        raise PromptGenerationError(f"DeepSeek tools request failed: {exc}") from exc
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
                            if streaming:
                                data = _consume_chat_completion_stream(
                                    resp,
                                    on_stream_delta,
                                    on_tool_call_start=on_tool_call_start,
                                )
                                raw_body = None
                            else:
                                raw_body = resp.read().decode("utf-8")
                                status_code = getattr(resp, "status", 200)
                    except urllib_error.HTTPError as exc:
                        detail = exc.read().decode("utf-8", errors="ignore")
                        raise PromptGenerationError(
                            f"DeepSeek tools error ({exc.code}): {detail.strip()[:200]}"
                        ) from exc
                    except urllib_error.URLError as exc:
                        raise PromptGenerationError(f"DeepSeek tools request failed: {exc}") from exc
                    if not streaming and status_code >= 400:
                        raise PromptGenerationError(
                            f"DeepSeek tools error ({status_code}): {raw_body[:200] if raw_body else status_code}"
                        )
                    elapsed_ms = int((time.monotonic() - start_time) * 1000)

                if streaming:
                    # In streaming mode we return the assembled assistant message so the
                    # orchestrator can inspect tool_calls or final content.
                    # Defensive fallback: if the assembled payload has no content and no
                    # tool_calls we retry once without streaming.
                    try:
                        if isinstance(data, Mapping):
                            choices = data.get("choices") or []
                            if choices:
                                message = choices[0].get("message") or {}
                                if isinstance(message, Mapping):
                                    tool_calls = message.get("tool_calls") or []
                                    content = message.get("content")
                                    has_text = isinstance(content, str) and bool(content.strip())
                                    if not has_text and not tool_calls:
                                        structured_log(
                                            "llm",
                                            "warning",
                                            "DeepSeek MCP stream produced empty content; retrying once with non-stream completion.",
                                            logger_obj=logger,
                                            level=logging.WARNING,
                                        )
                                        retry_payload = dict(payload)
                                        retry_payload["stream"] = False
                                        if self._http_client:
                                            try:
                                                resp = self._http_client.post(
                                                    "/v1/chat/completions",
                                                    json=retry_payload,
                                                    headers=headers,
                                                    timeout=self.timeout,
                                                )
                                                status_code = resp.status_code
                                                raw_body = resp.text
                                                if status_code >= 400:
                                                    raise PromptGenerationError(
                                                        f"DeepSeek tools error ({status_code}): {raw_body[:200]}"
                                                    )
                                                data = json.loads(raw_body)
                                                _log_usage(
                                                    "DeepSeekTools",
                                                    self.model,
                                                    data.get("usage") if isinstance(data, Mapping) else None,
                                                )
                                            except httpx.HTTPError as exc:
                                                raise PromptGenerationError(
                                                    f"DeepSeek tools request failed (retry): {exc}"
                                                ) from exc
                                        else:
                                            body = json.dumps(retry_payload).encode("utf-8")
                                            request = urllib_request.Request(
                                                f"{self.base_url}/v1/chat/completions",
                                                data=body,
                                                headers=headers,
                                                method="POST",
                                            )
                                            try:
                                                with urllib_request.urlopen(request, timeout=self.timeout) as resp:
                                                    raw_body = resp.read().decode("utf-8")
                                                    status_code = getattr(resp, "status", 200)
                                            except urllib_error.HTTPError as exc:
                                                detail = exc.read().decode("utf-8", errors="ignore")
                                                raise PromptGenerationError(
                                                    f"DeepSeek tools error ({exc.code}): {detail.strip()[:200]}"
                                                ) from exc
                                            except urllib_error.URLError as exc:
                                                raise PromptGenerationError(
                                                    f"DeepSeek tools request failed (retry): {exc}"
                                                ) from exc
                                            if status_code >= 400:
                                                raise PromptGenerationError(
                                                    f"DeepSeek tools error ({status_code}): {raw_body[:200] if raw_body else status_code}"
                                                )
                                            data = json.loads(raw_body)
                                            _log_usage(
                                                "DeepSeekTools",
                                                self.model,
                                                data.get("usage") if isinstance(data, Mapping) else None,
                                            )
                    except Exception:  # pragma: no cover - best effort; fall back to original data
                        logger.exception("DeepSeek MCP fallback to non-streaming completion failed.")

                    if logger.isEnabledFor(logging.DEBUG):
                        try:
                            logger.debug("DeepSeek MCP stream assembled payload: %s", json.dumps(data, ensure_ascii=False))
                        except Exception:  # pragma: no cover - log best effort
                            logger.debug("Failed to serialize streamed DeepSeek payload for logging.")
                    if LOG_TOKEN_ESTIMATE and logger.isEnabledFor(logging.DEBUG):
                        try:
                            message = (data.get("choices") or [{}])[0].get("message") if isinstance(data, Mapping) else {}
                            content = ""
                            if isinstance(message, Mapping):
                                raw_content = message.get("content")
                                if isinstance(raw_content, str):
                                    content = raw_content
                            out_tokens = _estimate_text_tokens(content, self.model) if content else 0
                            if out_tokens:
                                logger.debug("DeepSeek MCP stream tokens≈%s model=%s", out_tokens, self.model)
                        except Exception:
                            logger.debug("Failed to log streaming token estimate.")
                    _record_latency()
                    return data

                try:
                    data = json.loads(raw_body)
                except ValueError as exc:
                    raise PromptGenerationError("DeepSeek tools response was not valid JSON.") from exc
                logger.debug("DeepSeek MCP raw response: %s", raw_body)

                _log_usage("DeepSeekTools", self.model, data.get("usage") if isinstance(data, Mapping) else None)
                choices = data.get("choices") or []
                if not choices:
                    raise PromptGenerationError("DeepSeek tools response did not include choices.")
                message = choices[0].get("message") or {}
                tool_calls = message.get("tool_calls") or []

                if tool_calls:
                    _record_latency()
                    return data

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
                    _record_latency()
                    return {"role": "assistant", "content": text, "actions": [], "extractions": []}

                response_text = str(parsed.get("response_text") or "").strip()

                if elapsed_ms is not None:
                    structured_log(
                        "llm",
                        "latency",
                        {
                            "provider": "deepseek_tools",
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
                }
            except Exception as exc:
                if span and span.is_recording():
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise


def load_mcp_provider() -> BaseMcpProvider | None:
    """
    Instantiate the default MCP provider based on environment configuration.

    Mirrors load_default_provider but targets tool-calling implementations.
    """

    global _MCP_PROVIDER_SINGLETON

    with _MCP_PROVIDER_LOCK:
        if _MCP_PROVIDER_SINGLETON is not None:
            return _MCP_PROVIDER_SINGLETON

        preferred = (os.getenv("MCP_PROVIDER") or "").strip().lower()

        def _try(cls):
            try:
                return cls()
            except PromptGenerationError as exc:
                structured_log(
                    "llm",
                    "provider.disabled",
                    {"provider": cls.__name__, "error": str(exc)},
                    level=logging.WARNING,
                )
                return None

        order: list[type[BaseMcpProvider]] = []
        if preferred == "deepseek":
            order = [DeepSeekToolsProvider, OpenAIToolsProvider]
        elif preferred == "openai":
            order = [OpenAIToolsProvider, DeepSeekToolsProvider]
        else:
            if os.getenv("OPENAI_API_KEY"):
                order.append(OpenAIToolsProvider)
            if os.getenv("DEEPSEEK_API_KEY"):
                order.append(DeepSeekToolsProvider)

        for provider_cls in order:
            provider = _try(provider_cls)
            if provider:
                _MCP_PROVIDER_SINGLETON = provider
                return provider
    return None


def load_default_provider() -> BaseLLMProvider | None:
    """Instantiate the default provider based on environment configuration."""

    preferred = (os.getenv("LLM_PROVIDER") or "").strip().lower()

    def _try(cls):
        try:
            return cls()
        except PromptGenerationError as exc:
            structured_log(
                "llm",
                "provider.disabled",
                {"provider": cls.__name__, "error": str(exc)},
                level=logging.WARNING,
            )
            return None

    order: list[type] = []
    if preferred == "deepseek":
        order = [DeepSeekChatProvider, OpenAIChatProvider]
    elif preferred == "openai":
        order = [OpenAIChatProvider, DeepSeekChatProvider]
    else:
        # Default preference: OpenAI if configured, else DeepSeek.
        if os.getenv("OPENAI_API_KEY"):
            order.append(OpenAIChatProvider)
        if os.getenv("DEEPSEEK_API_KEY"):
            order.append(DeepSeekChatProvider)

    for provider_cls in order:
        provider = _try(provider_cls)
        if provider:
            return provider
    return None
