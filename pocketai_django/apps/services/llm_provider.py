from __future__ import annotations

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from urllib import error as urllib_error
from urllib import request as urllib_request

from apps.services.ai_prompt_builder import PromptBundle


logger = logging.getLogger(__name__)


class PromptGenerationError(RuntimeError):
    """Raised when the LLM provider fails to respond."""


class BaseLLMProvider(Protocol):
    """Interface for future provider implementations (OpenAI, Azure, etc.)."""

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
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
                raw_body = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise PromptGenerationError(
                f"OpenAI error ({exc.code}): {detail.strip()[:200]}"
            ) from exc
        except urllib_error.URLError as exc:
            raise PromptGenerationError(f"OpenAI request failed: {exc}") from exc

        if status_code >= 400:
            raise PromptGenerationError(f"OpenAI error ({status_code}): {raw_body[:200]}")

        try:
            data = json.loads(raw_body)
        except ValueError as exc:
            raise PromptGenerationError("OpenAI response was not valid JSON.") from exc

        content = self._extract_content(data)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise PromptGenerationError("OpenAI response did not return valid JSON output.") from exc


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
        if on_stream_delta:
            content = self._generate_streaming(messages, on_stream_delta)
        else:
            content = self._generate_blocking(messages)
        return self._parse_payload(content)

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

    def _system_prompt(self, bundle: PromptBundle) -> str:
        schema_hint = (
            "You must reply with JSON matching the schema provided. "
            "Never include Markdown or prose outside of the JSON object."
        )
        return f"{bundle.system_prompt}\n\n{schema_hint}"

    def _user_payload(self, bundle: PromptBundle) -> str:
        lines: list[str] = [bundle.user_prompt.strip(), "", "Conversation transcript:"]
        for turn in bundle.transcript:
            sender = turn.get("sender", "unknown")
            content = turn.get("content", "")
            lines.append(f"- {sender}: {content}")
        lines.append("")
        lines.append("Knowledge snippets:")
        if bundle.knowledge_snippets:
            for snippet in bundle.knowledge_snippets:
                lines.append(f"- {snippet.get('title')}: {snippet.get('summary')}")
        else:
            lines.append("- (none available)")
        lines.append("")
        lines.append("Action catalog (responders may choose any subset):")
        for action in bundle.actions_catalog:
            status = "enabled" if action.get("enabled") else "disabled"
            lines.append(f"- {action.get('key')} ({status}): {action.get('description')}")
        lines.append("")
        lines.append(
            "Return JSON with fields: response_text, actions[], extractions[], "
            "where each action has 'action' + 'payload', and extractions have 'type' + 'payload'."
        )
        return "\n".join(lines)

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
        lines: list[str] = [bundle.user_prompt.strip(), "", "Conversation transcript:"]
        for turn in bundle.transcript:
            sender = turn.get("sender", "unknown")
            content = turn.get("content", "")
            lines.append(f"- {sender}: {content}")
        lines.append("")
        lines.append("Knowledge snippets:")
        if bundle.knowledge_snippets:
            for snippet in bundle.knowledge_snippets:
                lines.append(f"- {snippet.get('title')}: {snippet.get('summary')}")
        else:
            lines.append("- (none available)")
        lines.append("")
        lines.append("Action catalog (responders may choose any subset):")
        for action in bundle.actions_catalog:
            status = "enabled" if action.get("enabled") else "disabled"
            lines.append(f"- {action.get('key')} ({status}): {action.get('description')}")
        lines.append("")
        lines.append(
            "Return JSON with fields: response_text, actions[], extractions[], "
            "where each action has 'action' + 'payload', and extractions have 'type' + 'payload'."
        )
        return "\n".join(lines)

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
