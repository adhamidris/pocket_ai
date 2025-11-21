from __future__ import annotations

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from urllib import error as urllib_error
from urllib import request as urllib_request

try:  # optional dependency for accurate token estimates
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover - optional
    tiktoken = None

from apps.services.ai_prompt_builder import PromptBundle


logger = logging.getLogger(__name__)


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
    logger.info("%s usage model=%s prompt=%s completion=%s total=%s", label, model, prompt, completion, total)


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
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:  # pragma: no cover - interface only
        ...


def _emit_stream_chunks(emit: Callable[[str], None], text: str, chunk_size: int = 64) -> None:
    """
    Helper to emit a long string in smaller chunks so the chat portal can
    surface incremental deltas even when the underlying provider call is
    non-streaming.
    """

    clean = (text or "").strip()
    if not clean:
        return
    words = clean.split()
    if not words:
        emit(clean)
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
            emit(" ".join(current))
            current = [word]
            current_len = len(word)
    if current:
        emit(" ".join(current))


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
        temperature: float = 0.3,
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
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        streaming = bool(on_stream_delta)
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
        try:
            logger.info("LLM request payload: %s", json.dumps(payload, ensure_ascii=False))
        except Exception:  # pragma: no cover - log best effort
            logger.warning("Failed to serialize LLM payload for logging.")

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
            raise PromptGenerationError(
                f"OpenAI error ({exc.code}): {detail.strip()[:200]}"
            ) from exc
        except urllib_error.URLError as exc:
            raise PromptGenerationError(f"OpenAI request failed: {exc}") from exc

        if status_code >= 400:
            raise PromptGenerationError(f"OpenAI error ({status_code}): {raw_body[:200] if raw_body else status_code}")

        if streaming:
            try:
                content = self._extract_content(data)
            except Exception as exc:
                raise PromptGenerationError("OpenAI streaming response missing content.") from exc
            try:
                logger.debug("OpenAI stream assembled payload: %s", json.dumps(data, ensure_ascii=False))
            except Exception:  # pragma: no cover - log best effort
                logger.debug("Failed to serialize OpenAI stream payload.")
            try:
                out_tokens = _estimate_text_tokens(content, self.model) if content else 0
                if out_tokens:
                    logger.info("OpenAI stream response model=%s tokens≈%s", self.model, out_tokens)
            except Exception:
                logger.debug("Failed to log streaming token estimate.")
        else:
            try:
                data = json.loads(raw_body)
            except ValueError as exc:
                raise PromptGenerationError("OpenAI response was not valid JSON.") from exc

            _log_usage("OpenAIChat", self.model, data.get("usage") if isinstance(data, Mapping) else None)
            logger.info("LLM raw response: %s", raw_body)

            content = self._extract_content(data)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
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
        temperature: float = 0.3,
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
        messages = [
            {"role": "system", "content": self._system_prompt(bundle)},
            {"role": "user", "content": self._user_payload(bundle)},
        ]
        request_payload = {
            "model": self.model,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": bool(on_stream_delta),
            "messages": messages,
        }
        self._log_pretty("DeepSeek request payload", request_payload)
        if on_stream_delta:
            content = self._generate_streaming(messages, on_stream_delta)
        else:
            content = self._generate_blocking(messages)
        self._log_pretty("DeepSeek raw response", content)
        parsed = self._parse_payload(content)
        self._log_pretty("DeepSeek parsed payload", parsed)
        return parsed

    def _generate_blocking(self, messages: list[Mapping[str, str]]) -> str:
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
        try:
            _log_usage("DeepSeekChat", self.model, getattr(response, "usage", None))
        except Exception:
            pass
        return self._stringify_message_content(getattr(response.choices[0], "message", None))

    def _generate_streaming(self, messages: list[Mapping[str, str]], on_stream_delta: Callable[[str], None]) -> str:
        extractor = _ResponseTextExtractor(on_stream_delta)
        assembled: list[str] = []
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                stream=True,
            )
            for chunk in stream:
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
        return content

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
            logger.warning("DeepSeek returned non-JSON content; using text fallback.")
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
        logger.info("%s:\n%s", label, formatted)

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
) -> None:
    try:
        idx = int(delta.get("index", 0))
    except (TypeError, ValueError):
        idx = 0
    state = store.setdefault(
        idx,
        {"id": None, "type": None, "function": {"name": None, "arguments": ""}},
    )
    identifier = delta.get("id")
    if isinstance(identifier, str) and identifier:
        state["id"] = identifier
    tool_type = delta.get("type")
    if isinstance(tool_type, str) and tool_type:
        state["type"] = tool_type
    function_block = state.setdefault("function", {"name": None, "arguments": ""})
    func_delta = delta.get("function") if isinstance(delta.get("function"), Mapping) else {}
    func_name = func_delta.get("name")
    if isinstance(func_name, str) and func_name:
        function_block["name"] = func_name
    func_args = func_delta.get("arguments")
    if isinstance(func_args, str) and func_args:
        existing = function_block.get("arguments") or ""
        function_block["arguments"] = f"{existing}{func_args}"


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


def _consume_chat_completion_stream(stream, on_stream_delta: Callable[[str], None] | None) -> dict[str, object]:
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

    def _normalize_delta(chunk: str) -> str:
        return chunk or ""

    for payload in _iter_sse_events(stream):
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        choices = data.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}
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
                text_parts.append(text)
                if on_stream_delta:
                    try:
                        on_stream_delta(text)
                    except Exception:  # pragma: no cover - safeguard user callbacks
                        logger.exception("Streaming callback failed while emitting delta chunk.")
        elif isinstance(content_block, str) and content_block:
            normalized = _normalize_delta(content_block)
            text_parts.append(normalized)
            if on_stream_delta:
                try:
                    on_stream_delta(normalized)
                except Exception:  # pragma: no cover - safeguard user callbacks
                    logger.exception("Streaming callback failed while emitting delta chunk.")

        for tool_delta in delta.get("tool_calls") or []:
            if isinstance(tool_delta, Mapping):
                _merge_stream_tool_call(tool_calls, tool_delta)

    assembled_text = "".join(text_parts).strip()
    if (finish_reason == "tool_calls" or (tool_calls and not assembled_text)) and tool_calls:
        message = {
            "role": role or "assistant",
            "tool_calls": _collapse_stream_tool_calls(tool_calls),
        }
    else:
        message = {"role": role or "assistant", "content": assembled_text}

    return {"choices": [{"message": message}]}


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
        self.temperature = temperature
        self.top_p = top_p

    def chat(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        tools: Iterable[Mapping[str, object]] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        streaming = bool(on_stream_delta)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(msg) for msg in messages],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": streaming,
        }
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
                logger.warning("Invalid OPENAI_MAX_TOKENS value: %s", max_tokens_env)

        # Log a compact summary at INFO and full payload only at DEBUG.
        char_count, token_est = _message_char_stats(payload.get("messages") or [], self.model)
        logger.info(
            "MCP LLM request model=%s tools=%s messages=%s chars=%s tokens≈%s",
            self.model,
            [t.get("function", {}).get("name") for t in (tools or [])],
            len(payload.get("messages") or []),
            char_count,
            token_est,
        )
        try:
            logger.debug("MCP LLM request payload: %s", json.dumps(payload, ensure_ascii=False))
        except Exception:  # pragma: no cover - log best effort
            logger.debug("Failed to serialize MCP payload for logging.")

        body = json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )

        data: dict[str, Any]
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as resp:
                if streaming:
                    data = _consume_chat_completion_stream(resp, on_stream_delta)
                    raw_body = None
                else:
                    raw_body = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise PromptGenerationError(
                f"OpenAI tools error ({exc.code}): {detail.strip()[:200]}"
            ) from exc
        except urllib_error.URLError as exc:
            raise PromptGenerationError(f"OpenAI tools request failed: {exc}") from exc

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
                    logger.info("MCP LLM stream response model=%s tokens≈%s", self.model, out_tokens)
            except Exception:
                logger.debug("Failed to log streaming token estimate.")
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
            return data

        # Final assistant turn: parse structured JSON from the message content.
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
        else:
            text = str(content or "").strip()
        if not text:
            return {"role": "assistant", "content": "", "actions": [], "extractions": []}

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: treat as plain text answer without actions/extractions.
            return {"role": "assistant", "content": text, "actions": [], "extractions": []}

        response_text = str(parsed.get("response_text") or "").strip()

        return {
            "role": "assistant",
            "content": response_text,
            "actions": parsed.get("actions") or [],
            "extractions": parsed.get("extractions") or [],
            "placeholder_response": parsed.get("placeholder_response"),
        }


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
        self.temperature = temperature
        self.top_p = top_p

    def chat(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        tools: Iterable[Mapping[str, object]] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        streaming = bool(on_stream_delta)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(msg) for msg in messages],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": streaming,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format

        # Compact summary at INFO; full payload at DEBUG for troubleshooting.
        char_count, token_est = _message_char_stats(payload.get("messages") or [], self.model)
        logger.info(
            "DeepSeek MCP request model=%s tools=%s messages=%s chars=%s tokens≈%s",
            self.model,
            [t.get("function", {}).get("name") for t in (tools or [])],
            len(payload.get("messages") or []),
            char_count,
            token_est,
        )
        try:
            logger.debug("DeepSeek MCP request payload: %s", json.dumps(payload, ensure_ascii=False))
        except Exception:  # pragma: no cover - log best effort
            logger.debug("Failed to serialize DeepSeek MCP payload for logging.")

        body = json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )

        data: dict[str, Any]
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as resp:
                if streaming:
                    data = _consume_chat_completion_stream(resp, on_stream_delta)
                    raw_body = None
                else:
                    raw_body = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise PromptGenerationError(
                f"DeepSeek tools error ({exc.code}): {detail.strip()[:200]}"
            ) from exc
        except urllib_error.URLError as exc:
            raise PromptGenerationError(f"DeepSeek tools request failed: {exc}") from exc

        if streaming:
            # In streaming mode we return the assembled assistant message so the
            # orchestrator can inspect tool_calls or final content.
            try:
                logger.debug("DeepSeek MCP stream assembled payload: %s", json.dumps(data, ensure_ascii=False))
            except Exception:  # pragma: no cover - log best effort
                logger.debug("Failed to serialize streamed DeepSeek payload for logging.")
            try:
                message = (data.get("choices") or [{}])[0].get("message") if isinstance(data, Mapping) else {}
                content = ""
                if isinstance(message, Mapping):
                    raw_content = message.get("content")
                    if isinstance(raw_content, str):
                        content = raw_content
                out_tokens = _estimate_text_tokens(content, self.model) if content else 0
                if out_tokens:
                    logger.info("DeepSeek MCP stream response model=%s tokens≈%s", self.model, out_tokens)
            except Exception:
                logger.debug("Failed to log streaming token estimate.")
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
            # Let the orchestrator inspect tool_calls directly.
            return data

        content = message.get("content")
        if isinstance(content, list):
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
        else:
            text = str(content or "").strip()
        if not text:
            return {"role": "assistant", "content": "", "actions": [], "extractions": []}

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"role": "assistant", "content": text, "actions": [], "extractions": []}

        response_text = str(parsed.get("response_text") or "").strip()

        return {
            "role": "assistant",
            "content": response_text,
            "actions": parsed.get("actions") or [],
            "extractions": parsed.get("extractions") or [],
            "placeholder_response": parsed.get("placeholder_response"),
        }


def load_mcp_provider() -> BaseMcpProvider | None:
    """
    Instantiate the default MCP provider based on environment configuration.

    Mirrors load_default_provider but targets tool-calling implementations.
    """

    preferred = (os.getenv("MCP_PROVIDER") or os.getenv("LLM_PROVIDER") or "").strip().lower()

    def _try(cls):
        try:
            return cls()
        except PromptGenerationError as exc:
            logger.warning("%s provider disabled: %s", cls.__name__, exc)
            return None

    order: list[type[BaseMcpProvider]] = []
    if preferred == "deepseek":
        order = [DeepSeekToolsProvider, OpenAIToolsProvider]
    elif preferred == "openai":
        order = [OpenAIToolsProvider, DeepSeekToolsProvider]
    else:
        # Default preference: OpenAI if configured, else DeepSeek.
        if os.getenv("OPENAI_API_KEY"):
            order.append(OpenAIToolsProvider)
        if os.getenv("DEEPSEEK_API_KEY"):
            order.append(DeepSeekToolsProvider)

    for provider_cls in order:
        provider = _try(provider_cls)
        if provider:
            return provider
    return None


def load_default_provider() -> BaseLLMProvider | None:
    """Instantiate the default provider based on environment configuration."""

    preferred = (os.getenv("LLM_PROVIDER") or "").strip().lower()

    def _try(cls):
        try:
            return cls()
        except PromptGenerationError as exc:
            logger.warning("%s provider disabled: %s", cls.__name__, exc)
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
