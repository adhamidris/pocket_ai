"""
LLM provider abstraction layer for orchestrator services.

This module provides two provider interfaces and their implementations:

1. **BaseLLMProvider** (legacy mode):
   - Used by `AiOrchestratorService` for structured JSON responses
   - Implementations: `OpenAIChatProvider`, `DeepSeekChatProvider`
   - Returns parsed JSON with `response_text`, `actions`, `extractions`
   - Loaded via `load_default_provider()`

2. **BaseMcpProvider** (MCP mode):
   - Used by `McpOrchestratorService` for tool-calling workflows
   - Implementations: `OpenAIToolsProvider`, `DeepSeekToolsProvider`
   - Returns raw Chat Completions format with `tool_calls` support
   - Loaded via `load_mcp_provider()`

Architecture:
    The dual-provider design allows the orchestrator to switch between legacy
    (structured JSON) and MCP (tool-calling) modes based on feature flags.
    Both modes support streaming via `on_stream_delta` callbacks for SSE delivery
    to the chat portal.

Streaming:
    Providers support Server-Sent Events (SSE) streaming for real-time response
    delivery. The `_consume_chat_completion_stream` function assembles streaming
    responses into a complete payload, while `_ResponseTextExtractor` incrementally
    parses JSON to extract the `response_text` field for immediate streaming.

Related modules:
    - ai_orchestrator.py: Uses BaseLLMProvider via load_default_provider()
    - mcp/orchestrator.py: Uses BaseMcpProvider via load_mcp_provider()
    - api/chat_portal.py: Selects provider based on business feature flags
    - ai_prompt_builder.py: Provides PromptBundle structure for legacy providers
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
import time
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

from apps.services.ai_prompt_builder import PromptBundle
from apps.services.rag_logging import structured_log

# Optional flag to enable token estimation logs (guarded by DEBUG level as well).
# Used for cost tracking and debugging large prompts.
LOG_TOKEN_ESTIMATE = os.getenv("LLM_LOG_TOKEN_ESTIMATE", "").strip().lower() in {"1", "true", "yes"}
# Optional flag to force debug payload logging even if DEBUG level is off.
# Useful for troubleshooting provider API issues without changing log levels.
LOG_DEBUG_PAYLOADS = os.getenv("LLM_DEBUG_PAYLOADS", "").strip().lower() in {"1", "true", "yes"}
# Optional HTTP timeout overrides (seconds) when using httpx client.
# Separate connect/read timeouts allow fine-tuning for slow networks or large responses.
HTTP_TIMEOUT_CONNECT = os.getenv("LLM_HTTP_TIMEOUT_CONNECT")
HTTP_TIMEOUT_READ = os.getenv("LLM_HTTP_TIMEOUT_READ")


logger = logging.getLogger(__name__)


def _select_encoder(model_name: str | None):
    """
    Select the appropriate tiktoken encoder for token estimation.

    Args:
        model_name: Optional model identifier (e.g., "gpt-4", "gpt-3.5-turbo").
            When None, falls back to cl100k_base (used by GPT-4 and most modern models).

    Returns:
        tiktoken.Encoding instance if available, None otherwise.

    Why:
        Different models use different tokenization schemes. We try model-specific
        encoding first for accuracy, then fall back to cl100k_base (the most common)
        if the model isn't recognized. This ensures token estimates are reasonably
        accurate even when tiktoken doesn't have the exact model.
    """
    if not tiktoken:
        return None
    try:
        # Try model-specific encoding first for best accuracy.
        return tiktoken.encoding_for_model(model_name) if model_name else tiktoken.get_encoding("cl100k_base")
    except Exception:
        try:
            # Fallback to cl100k_base (GPT-4 tokenizer) which works for most models.
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def _message_char_stats(messages: Iterable[Mapping[str, object]], model: str | None = None) -> tuple[int, int]:
    """
    Estimate prompt size in characters and tokens for logging and cost tracking.

    Args:
        messages: Iterable of message dicts (typically from Chat Completions API format).
        model: Optional model name for selecting the correct tokenizer.

    Returns:
        Tuple of (total_chars, total_tokens). Tokens are estimated using tiktoken
        when available, otherwise approximated as chars/4 (rough heuristic).

    Why:
        Token counts are needed for cost estimation and debugging large prompts.
        The chars/4 fallback is a conservative estimate (most tokens are 1-4 chars)
        that works reasonably well when tiktoken isn't available.

    Edge cases:
        - Handles both string content and list-based content (multi-modal messages)
        - Gracefully handles encoding errors by falling back to char count
        - Returns (0, 0) for empty message sets
    """
    total_chars = 0
    total_tokens = 0
    encoder = _select_encoder(model)
    for message in messages:
        content = message.get("content")
        # Handle multi-modal content (list of parts with text/images)
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
                                # Encoding failed; fall back to char estimate
                                pass
        elif isinstance(content, str):
            total_chars += len(content)
            if encoder:
                try:
                    total_tokens += len(encoder.encode(content))
                except Exception:
                    # Encoding failed; fall back to char estimate
                    pass
    # Conservative fallback: assume ~4 chars per token (works for most languages)
    if not encoder:
        total_tokens = max(1, total_chars // 4) if total_chars else 0
    return total_chars, total_tokens


def _estimate_text_tokens(text: str, model: str | None = None) -> int:
    """
    Estimate token count for a single text string.

    Args:
        text: The text to estimate tokens for.
        model: Optional model name for selecting the correct tokenizer.

    Returns:
        Estimated token count. Uses tiktoken when available, otherwise chars/4.

    Why:
        Used for logging output token counts in streaming responses where we
        don't have usage metadata. Helps track costs and response sizes.
    """
    encoder = _select_encoder(model)
    if not text:
        return 0
    if encoder:
        try:
            return len(encoder.encode(text))
        except Exception:
            # Fall back to char estimate if encoding fails
            pass
    # Conservative fallback: ~4 chars per token
    return max(1, len(text) // 4)


def _log_usage(label: str, model: str | None, usage: Mapping[str, object] | None) -> None:
    """
    Log token usage statistics for cost tracking and monitoring.

    Args:
        label: Provider name (e.g., "OpenAIChat", "DeepSeekTools").
        model: Model identifier (e.g., "gpt-4o-mini").
        usage: Usage dict from API response with prompt_tokens, completion_tokens, total_tokens.

    Why:
        Token usage is critical for cost tracking. This structured log is consumed
        by monitoring systems to track spend per provider/model. Only logs when
        usage data is available (non-streaming responses typically include this).
    """
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


class PromptGenerationError(RuntimeError):
    """
    Exception raised when the LLM provider fails to generate a response.

    This is the standard error type for all provider failures (network errors,
    API errors, parsing errors). The orchestrator catches this to trigger
    fallback strategies (e.g., heuristics, cached responses).

    Why:
        Using a specific exception type allows the orchestrator to distinguish
        between provider failures and other runtime errors, enabling graceful
        degradation when LLM calls fail.
    """


class BaseLLMProvider(Protocol):
    """
    Protocol interface for legacy orchestrator providers.

    Used by `AiOrchestratorService` to generate structured JSON responses with
    `response_text`, `actions`, and `extractions` fields. Implementations must
    return parsed JSON matching the orchestrator's expected schema.

    Implementations:
        - OpenAIChatProvider: Direct HTTP API calls to OpenAI
        - DeepSeekChatProvider: OpenAI-compatible API with streaming support
        - StubLLMProvider: No-op provider for testing

    Related:
        - Loaded via `load_default_provider()` in chat_portal.py
        - Used by `AiOrchestratorService.generate()` in ai_orchestrator.py
        - PromptBundle structure defined in ai_prompt_builder.py
    """

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
        """
        Generate a structured JSON response from a prompt bundle.

        Args:
            bundle: PromptBundle containing system/user prompts, transcript, knowledge snippets.
            on_stream_delta: Optional callback for streaming text deltas (for SSE delivery).

        Returns:
            Dict with keys: `response_text` (str), `actions` (list), `extractions` (list).
            Must be valid JSON matching the orchestrator's schema.

        Raises:
            PromptGenerationError: When the provider fails to generate a response.
        """
        ...


class BaseMcpProvider(Protocol):
    """
    Protocol interface for MCP (Model Context Protocol) orchestrator providers.

    Used by `McpOrchestratorService` for tool-calling workflows. Unlike BaseLLMProvider,
    this interface works with raw Chat Completions API format and supports tool_calls
    for dynamic function invocation during conversations.

    Key differences from BaseLLMProvider:
        - Accepts raw messages (not PromptBundle) for flexibility
        - Supports tool definitions and tool_calls in responses
        - Returns raw API format so orchestrator can inspect tool_calls
        - Used for multi-turn tool loops (search -> read -> aggregate -> actions)

    Implementations:
        - OpenAIToolsProvider: OpenAI Chat Completions with tool calling
        - DeepSeekToolsProvider: DeepSeek API with tool calling support

    Related:
        - Loaded via `load_mcp_provider()` in chat_portal.py
        - Used by `McpOrchestratorService.chat()` in mcp/orchestrator.py
        - Tool definitions in mcp/tools.py
    """

    def chat(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        tools: Iterable[Mapping[str, object]] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:  # pragma: no cover - interface only
        """
        Generate a chat completion with optional tool calling.

        Args:
            messages: Chat message history (system, user, assistant, tool messages).
            tools: Optional tool definitions for function calling.
            on_stream_delta: Optional callback for streaming text deltas.
            response_format: Optional response format constraints (e.g., JSON schema).

        Returns:
            Chat Completions API response format with `choices[0].message` containing:
            - `content`: Assistant text response (if any)
            - `tool_calls`: List of tool invocation requests (if any)
            The orchestrator inspects tool_calls to dispatch function calls.

        Raises:
            PromptGenerationError: When the provider fails to generate a response.
        """
        ...


def _emit_stream_chunks(emit: Callable[[str], None], text: str, chunk_size: int = 64) -> None:
    """
    Emit a long string in word-boundary chunks for SSE streaming.

    Helper to emit a long string in smaller chunks so the chat portal can
    surface incremental deltas even when the underlying provider call is
    non-streaming.

    Args:
        emit: Callback function to emit each chunk.
        text: The full text to chunk and emit.
        chunk_size: Target chunk size in characters (default 64).

    Why:
        The chat portal expects incremental SSE events for real-time UX.
        When providers return full text (e.g., planner-only passes), we need
        to simulate streaming by chunking the response. Word boundaries prevent
        mid-word splits that would break rendering.

    Used by:
        - Orchestrators when providers return full text but portal expects SSE
        - Planner-only passes that generate complete responses at once
    """
    # Clean and validate input text
    clean = (text or "").strip()
    if not clean:
        # Empty text: nothing to emit
        return
    words = clean.split()
    if not words:
        # Single word or whitespace-only: emit as-is (no chunking needed)
        emit(clean)
        return
    # Accumulate words into chunks that respect chunk_size limit
    current: list[str] = []  # Current chunk being built
    current_len = 0  # Length of current chunk (for efficient size checking)
    for word in words:
        if not current:
            # First word always starts a new chunk (even if it exceeds chunk_size)
            # This ensures we always emit at least one chunk, even for very long words
            current.append(word)
            current_len = len(word)
            continue
        # Check if adding this word would exceed chunk size
        # +1 accounts for the space separator between words
        projected = current_len + 1 + len(word)  # +1 for space
        if projected <= chunk_size:
            # Word fits: add to current chunk
            current.append(word)
            current_len = projected
        else:
            # Word doesn't fit: emit current chunk and start a new one with this word
            # This ensures we never split words mid-word (preserves word boundaries)
            emit(" ".join(current))
            current = [word]
            current_len = len(word)
    # Emit final chunk if any words remain (last chunk may be smaller than chunk_size)
    if current:
        emit(" ".join(current))


@dataclass
class StubLLMProvider:
    """
    No-op provider for testing and fallback scenarios.

    Default no-op provider so the orchestrator can be tested without a real model.
    The stub simply raises PromptGenerationError to signal that a fallback strategy
    (heuristics) should be used.

    Why:
        Allows the orchestrator to run in environments without LLM configuration
        (e.g., CI tests, development). The orchestrator catches PromptGenerationError
        and falls back to rule-based responses when no provider is available.

    Used by:
        - `load_default_provider()` when no API keys are configured
        - Test suites that want to verify orchestrator error handling
    """

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
        """
        Always raises PromptGenerationError to trigger fallback strategies.

        Args:
            bundle: Ignored (no-op provider).
            on_stream_delta: Ignored (no-op provider).

        Raises:
            PromptGenerationError: Always raised to signal provider unavailability.
        """
        raise PromptGenerationError("LLM provider not configured")


class OpenAIChatProvider:
    """
    Minimal OpenAI Chat Completions client for legacy orchestrator.

    Uses the HTTP API directly (urllib) so we avoid strict SDK version coupling.
    Responses are enforced to JSON via response_format and mapped to the orchestrator
    schema. Streaming is supported via on_stream_delta to feed portal SSE.

    Design decisions:
        - Direct HTTP avoids SDK dependency hell and version conflicts
        - JSON schema enforcement ensures consistent response format
        - Streaming support enables real-time SSE delivery to chat portal
        - Low temperature (0.3) for consistent, deterministic responses

    Related:
        - Implements BaseLLMProvider for AiOrchestratorService
        - Used when OPENAI_API_KEY is configured and LLM_PROVIDER=openai
        - Response schema matches ai_orchestrator.py expectations
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
        """
        Initialize OpenAI provider with configuration.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var).
            model: Model identifier (defaults to OPENAI_MODEL or "gpt-4o-mini").
            base_url: API base URL (defaults to OPENAI_BASE_URL or official endpoint).
                Allows proxying through services like Azure OpenAI.
            timeout: HTTP request timeout in seconds (default 60.0).
            temperature: Sampling temperature (default 0.3 for consistency).
            top_p: Nucleus sampling parameter (default 0.9).

        Raises:
            PromptGenerationError: If api_key is not configured.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise PromptGenerationError("OPENAI_API_KEY is not configured.")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        # Strip trailing slash to avoid double slashes in URL construction
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p

    def generate(self, bundle: PromptBundle, *, on_stream_delta: Callable[[str], None] | None = None) -> Mapping[str, Any]:
        """
        Generate structured JSON response from a prompt bundle.

        Args:
            bundle: PromptBundle containing system/user prompts, transcript, knowledge.
            on_stream_delta: Optional callback for streaming text deltas (SSE delivery).

        Returns:
            Dict with `response_text`, `actions`, `extractions` matching orchestrator schema.

        Raises:
            PromptGenerationError: On API errors, network failures, or invalid JSON.

        Design:
            - Enforces JSON response format via response_format schema
            - Supports streaming for real-time portal updates
            - Assembles streaming responses into complete payload
            - Logs token usage and payloads for debugging
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        streaming = bool(on_stream_delta)
        # Build messages: system prompt + user prompt (transcript/knowledge embedded in prompts)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt(bundle)},
                {"role": "user", "content": self._user_payload(bundle)},
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            # Enforce JSON output matching orchestrator schema
            "response_format": self._response_schema(),
        }
        if streaming:
            # Enable SSE streaming when callback is provided
            payload["stream"] = True
        # Log token estimates for cost tracking (only when explicitly enabled)
        if LOG_TOKEN_ESTIMATE and logger.isEnabledFor(logging.DEBUG):
            try:
                char_count, token_est = _message_char_stats(payload.get("messages") or [], self.model)
                logger.debug("LLM request model=%s chars=%s tokens≈%s", self.model, char_count, token_est)
            except Exception:  # pragma: no cover - best effort
                logger.debug("Failed to estimate tokens for LLM request.")
        # Log full payloads for debugging API issues (guarded by flag or DEBUG level)
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
                # urllib doesn't always expose status code directly, default to 200
                status_code = getattr(resp, "status", 200)
                if streaming:
                    # Assemble streaming SSE events into complete payload
                    # _consume_chat_completion_stream parses SSE format and emits deltas
                    data = _consume_chat_completion_stream(resp, on_stream_delta)
                    raw_body = None  # Streaming responses don't have a raw body
                else:
                    # Read complete response for non-streaming requests
                    # Non-streaming responses include usage metadata for cost tracking
                    raw_body = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            # HTTP error responses (4xx, 5xx): extract error details from response body
            # This includes API errors like rate limits, invalid API keys, etc.
            detail = exc.read().decode("utf-8", errors="ignore")
            raise PromptGenerationError(
                f"OpenAI error ({exc.code}): {detail.strip()[:200]}"
            ) from exc
        except urllib_error.URLError as exc:
            # Network errors (timeout, connection refused, DNS failures, etc.)
            # These are infrastructure issues, not API errors
            raise PromptGenerationError(f"OpenAI request failed: {exc}") from exc

        # Defensive check: HTTPError should catch 4xx/5xx, but some edge cases might slip through
        # This ensures we always raise an error for bad status codes
        if status_code >= 400:
            raise PromptGenerationError(f"OpenAI error ({status_code}): {raw_body[:200] if raw_body else status_code}")

        if streaming:
            # Extract content from assembled streaming payload
            try:
                content = self._extract_content(data)
            except Exception as exc:
                raise PromptGenerationError("OpenAI streaming response missing content.") from exc
            # Log assembled payload for debugging
            if logger.isEnabledFor(logging.DEBUG):
                try:
                    logger.debug("OpenAI stream assembled payload: %s", json.dumps(data, ensure_ascii=False))
                except Exception:  # pragma: no cover - log best effort
                    logger.debug("Failed to serialize OpenAI stream payload.")
            # Estimate output tokens (streaming responses don't include usage metadata)
            if LOG_TOKEN_ESTIMATE and logger.isEnabledFor(logging.DEBUG):
                try:
                    out_tokens = _estimate_text_tokens(content, self.model) if content else 0
                    if out_tokens:
                        logger.debug("OpenAI stream tokens≈%s model=%s", out_tokens, self.model)
                except Exception:
                    logger.debug("Failed to log streaming token estimate.")
        else:
            # Parse non-streaming JSON response
            try:
                data = json.loads(raw_body)
            except ValueError as exc:
                raise PromptGenerationError("OpenAI response was not valid JSON.") from exc

            # Log usage stats for cost tracking (non-streaming includes usage metadata)
            _log_usage("OpenAIChat", self.model, data.get("usage") if isinstance(data, Mapping) else None)
            structured_log(
                "llm",
                "raw_response",
                raw_body,
                context={"provider": "OpenAIChat", "model": self.model},
                logger_obj=logger,
            )

            content = self._extract_content(data)
        # Parse JSON content (enforced by response_format, but validate anyway)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise PromptGenerationError("OpenAI response did not return valid JSON output.") from exc

    def _system_prompt(self, bundle: PromptBundle) -> str:
        """
        Build system prompt with JSON schema enforcement hint.

        Args:
            bundle: PromptBundle containing the base system prompt.

        Returns:
            System prompt with JSON schema enforcement instructions appended.

        Why:
            Even with response_format, models sometimes include markdown code blocks.
            The explicit hint reduces parsing errors and ensures clean JSON output.
        """
        schema_hint = (
            "You must reply with JSON matching the schema provided. "
            "Never include Markdown or prose outside of the JSON object."
        )
        return f"{bundle.system_prompt}\n\n{schema_hint}"

    def _user_payload(self, bundle: PromptBundle) -> str:
        """
        Extract and clean user prompt from bundle.

        Args:
            bundle: PromptBundle containing the user prompt.

        Returns:
            Stripped user prompt text.

        Why:
            Stripping whitespace prevents empty prompts and ensures clean API calls.
        """
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
    DeepSeek API adapter for legacy orchestrator (OpenAI-compatible).

    Uses the official OpenAI SDK with DeepSeek's base URL so we can opt into
    real streaming. The provider still expects JSON output matching the
    orchestrator schema, but `response_text` deltas are surfaced via the
    `on_stream_delta` callback whenever streaming is enabled.

    Why inherit from OpenAIChatProvider:
        - Shares system/user prompt building logic
        - Reuses response schema enforcement
        - Differentiates only in HTTP client (OpenAI SDK vs urllib)

    Design decisions:
        - Uses OpenAI SDK for better streaming support (httpx-based)
        - _ResponseTextExtractor incrementally parses JSON to stream response_text
        - Falls back to plain text if JSON parsing fails (graceful degradation)

    Related:
        - Implements BaseLLMProvider for AiOrchestratorService
        - Used when DEEPSEEK_API_KEY is configured and LLM_PROVIDER=deepseek
        - Response schema matches ai_orchestrator.py expectations
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
        """
        Generate structured JSON response from a prompt bundle using DeepSeek API.

        Args:
            bundle: PromptBundle containing system/user prompts, transcript, knowledge snippets.
            on_stream_delta: Optional callback for streaming text deltas (for SSE delivery).

        Returns:
            Dict with keys: `response_text` (str), `actions` (list), `extractions` (list).
            Must be valid JSON matching the orchestrator's schema.

        Raises:
            PromptGenerationError: When the provider fails to generate a response.

        Design:
            - Routes to streaming or blocking generation based on callback presence
            - Uses OpenAI SDK for better streaming support (httpx-based)
            - Logs request/response payloads for debugging
            - Parses JSON with graceful fallback to plain text if parsing fails
            - Returns structured format expected by AiOrchestratorService

        Related:
            - Called by AiOrchestratorService.generate() in ai_orchestrator.py
            - Response schema matches OpenAIChatProvider._response_schema()
            - Streaming uses _ResponseTextExtractor for incremental JSON parsing
        """
        # Build messages array: system prompt + user prompt (transcript/knowledge embedded)
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
        # Log request for debugging (only when enabled via LOG_DEBUG_PAYLOADS or DEBUG level)
        self._log_pretty("DeepSeek request payload", request_payload)
        # Route to streaming or blocking based on callback presence
        if on_stream_delta:
            # Streaming mode: uses _ResponseTextExtractor to parse JSON incrementally
            content = self._generate_streaming(messages, on_stream_delta)
        else:
            # Blocking mode: waits for complete response (used for planner-only passes)
            content = self._generate_blocking(messages)
        # Log raw response for debugging
        self._log_pretty("DeepSeek raw response", content)
        # Parse JSON payload (with fallback to plain text if JSON parsing fails)
        parsed = self._parse_payload(content)
        # Log parsed payload for debugging
        self._log_pretty("DeepSeek parsed payload", parsed)
        return parsed

    def _generate_blocking(self, messages: list[Mapping[str, str]]) -> str:
        """
        Generate non-streaming response from DeepSeek API.

        Args:
            messages: Chat message history (system, user, assistant).

        Returns:
            Raw text content from the response (may be JSON string).

        Raises:
            PromptGenerationError: On API errors or network failures.

        Why:
            Used when streaming is disabled (e.g., planner-only passes that
            don't need real-time UX). Non-streaming responses include usage
            metadata for cost tracking.
        """
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
        # Log usage stats for cost tracking (non-streaming includes usage metadata)
        try:
            _log_usage("DeepSeekChat", self.model, getattr(response, "usage", None))
        except Exception:
            pass
        return self._stringify_message_content(getattr(response.choices[0], "message", None))

    def _generate_streaming(self, messages: list[Mapping[str, str]], on_stream_delta: Callable[[str], None]) -> str:
        """
        Generate streaming response and extract response_text incrementally.

        Uses _ResponseTextExtractor to parse JSON on-the-fly and stream only the
        response_text field, providing real-time UX even though the full JSON
        isn't complete yet.

        Why:
            DeepSeek streams JSON character-by-character. We can't wait for complete
            JSON to parse it, so we use the extractor to stream response_text
            incrementally while still assembling the full JSON for final parsing.
        """
        # Extract response_text field incrementally for real-time streaming
        # The extractor uses a state machine to parse JSON character-by-character,
        # allowing us to stream response_text immediately even though the full JSON
        # isn't complete yet. This provides real-time UX for the chat portal.
        extractor = _ResponseTextExtractor(on_stream_delta)
        assembled: list[str] = []  # Accumulate full JSON for final parsing
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                stream=True,
            )
            for chunk in stream:
                # Extract text delta from OpenAI SDK chunk object
                delta_text = self._stringify_message_content(getattr(chunk.choices[0], "delta", None))
                if not delta_text:
                    continue
                # Assemble full JSON for final parsing (needed to extract actions/extractions)
                assembled.append(delta_text)
                # Extract and stream response_text incrementally (for real-time UX)
                # The extractor parses JSON on-the-fly and emits response_text as it's found
                extractor.feed(delta_text)
        except Exception as exc:
            raise PromptGenerationError(f"DeepSeek streaming request failed: {exc}") from exc
        finally:
            # Flush any remaining buffered text
            extractor.flush()

        content = "".join(assembled).strip()
        if not content:
            raise PromptGenerationError("DeepSeek response was empty.")
        return content

    @staticmethod
    def _stringify_message_content(payload: Any) -> str:
        """
        Extract text content from OpenAI SDK message/delta objects.

        Handles both string content and list-based multi-modal content (text/images).
        Works with both message objects (complete) and delta objects (streaming).

        Args:
            payload: Message or delta object from OpenAI SDK, or raw content.

        Returns:
            Extracted text content as string, empty string if none found.

        Why:
            OpenAI SDK returns different structures for messages vs deltas, and
            content can be a string or list of parts. This normalizes to a single
            string for consistent processing.
        """
        if payload is None:
            return ""
        content = getattr(payload, "content", payload)
        # Handle multi-modal content (list of parts with text/images)
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
        """
        Parse JSON payload with graceful fallback to plain text.

        Why:
            Sometimes models return plain text instead of JSON (e.g., when they
            can't follow the schema). We fall back gracefully rather than failing,
            ensuring the conversation continues even with imperfect responses.
        """
        content = content.strip()
        if not content:
            raise PromptGenerationError("DeepSeek response was empty.")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Graceful fallback: treat as plain text response
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

    def _system_prompt(self, bundle: PromptBundle) -> str:
        """
        Build system prompt with JSON schema enforcement hint.

        Inherited from OpenAIChatProvider. See parent class for details.
        """
        schema_hint = (
            "You must reply with JSON matching the schema provided. "
            "Never include Markdown or prose outside of the JSON object."
        )
        return f"{bundle.system_prompt}\n\n{schema_hint}"

    def _user_payload(self, bundle: PromptBundle) -> str:
        """
        Extract and clean user prompt from bundle.

        Inherited from OpenAIChatProvider. See parent class for details.
        """
        return bundle.user_prompt.strip()

    @staticmethod
    def _response_schema() -> Mapping[str, Any]:
        """
        Return JSON schema enforced by response_format parameter.

        Inherited from OpenAIChatProvider. See parent class for details.
        """
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
    def _log_pretty(label: str, payload: Any) -> None:
        """
        Log payload with pretty-printed JSON formatting for readability.

        Args:
            label: Log stage label (e.g., "DeepSeek request payload").
            payload: Payload to log (dict, string, or any JSON-serializable object).

        Why:
            DeepSeek provider logs request/response payloads for debugging.
            Pretty-printing JSON makes logs more readable than compact format.
            Falls back to string representation if JSON serialization fails.
        """
        try:
            if isinstance(payload, str):
                payload = payload.strip()
                if payload:
                    try:
                        # Try to parse and re-format as pretty JSON
                        as_json = json.loads(payload)
                    except json.JSONDecodeError:
                        # Not JSON, log as-is
                        formatted = payload
                    else:
                        formatted = json.dumps(as_json, indent=2, ensure_ascii=False)
                else:
                    formatted = ""
            else:
                formatted = json.dumps(payload, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            # Fallback to string representation if JSON fails
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
    Incrementally parses JSON to stream the `response_text` field in real-time.

    The extractor implements a state machine that scans for the `"response_text"` key,
    then decodes the string value character-by-character while respecting JSON escape
    sequences (\\n, \\t, \\uXXXX, etc.). As soon as new characters are available,
    they are emitted via the provided callback for SSE delivery.

    Why this exists:
        When providers stream JSON responses, we can't wait for the complete JSON to
        parse it. This extractor allows us to stream the `response_text` field
        incrementally, providing real-time UX even when the full JSON isn't complete.

    State machine:
        - search: Looking for the "response_text" key
        - post_key: Found the key, waiting for colon
        - seek_quote: Waiting for opening quote of string value
        - in_string: Parsing the string value (handles escapes, unicode)
        - done: String value complete

    Edge cases handled:
        - JSON escape sequences (\\n, \\t, \\", \\\\, etc.)
        - Unicode escapes (\\uXXXX)
        - Whitespace between key and value
        - Partial chunks that split across escape sequences

    Used by:
        - DeepSeekChatProvider._generate_streaming() for real-time text streaming
    """

    TARGET = '"response_text"'

    def __init__(self, emit: Callable[[str], None]) -> None:
        """
        Initialize the extractor with a callback for emitting text deltas.

        Args:
            emit: Callback function that receives text chunks as they're extracted.
        """
        self.emit = emit
        self._key_index = 0  # Current position in TARGET key being matched
        # State machine: search -> post_key -> seek_quote -> in_string -> done
        self._state = "search"
        self._in_string = False  # Whether we're currently parsing a string value
        self._escape = False  # Whether next char is escaped
        self._unicode_digits: list[str] | None = None  # Accumulating unicode escape digits
        self._buffer: list[str] = []  # Accumulated text to emit
        self._completed = False  # Whether extraction is complete

    def feed(self, chunk: str) -> None:
        """
        Process a chunk of JSON text and extract response_text incrementally.

        Args:
            chunk: Next chunk of JSON text from streaming response.

        Why:
            Streaming responses arrive in chunks. We process each character to
            maintain state machine state and extract response_text as soon as
            it's available. Flushes buffer after each chunk to emit text immediately.
        """
        if self._completed or not chunk:
            return
        for ch in chunk:
            if self._completed:
                break
            self._consume(ch)
        # Flush buffer after each chunk to emit text immediately
        self._flush()

    def flush(self) -> None:
        """
        Public method to flush any remaining buffered text.

        Should be called when stream ends to ensure all extracted text is emitted.
        """
        self._flush()

    def _consume(self, ch: str) -> None:
        """
        Process a single character through the state machine.

        Routes to appropriate handler based on current state (searching for key,
        parsing string value, etc.). Implements the core state machine logic.

        Args:
            ch: Single character to process.
        """
        if self._completed:
            return
        if self._in_string:
            # We're inside the string value, handle escapes and unicode
            self._consume_string(ch)
            return
        if self._state == "search":
            # Looking for the "response_text" key
            self._scan_key(ch)
            return
        if self._state == "post_key":
            # Found the key, waiting for colon (JSON format: "response_text": "value")
            if ch == ":":
                # Colon found: transition to seeking the opening quote of the string value
                self._state = "seek_quote"
            elif ch in " \t\r\n":
                # Whitespace is allowed between key and colon (JSON allows whitespace)
                return
            else:
                # Not a colon: this wasn't actually the "response_text" key, reset and search again
                # This handles cases where we matched a partial key (e.g., "response_text_other")
                self._reset()
                self._scan_key(ch)
            return
        if self._state == "seek_quote":
            # Waiting for opening quote of string value (JSON format: "response_text": "value")
            if ch == '"':
                # Opening quote found: we're now inside the string value, start extracting
                self._in_string = True
            elif ch in " \t\r\n":
                # Whitespace is allowed before quote (JSON allows whitespace after colon)
                return
            else:
                # Not a quote: this wasn't actually a string value, reset and search again
                # This handles cases where the value is not a string (e.g., number, object)
                self._reset()
                self._scan_key(ch)

    def _consume_string(self, ch: str) -> None:
        """
        Process character inside the response_text string value.

        Handles JSON escape sequences (\\n, \\t, \\", etc.) and unicode escapes
        (\\uXXXX). Accumulates decoded characters in buffer for emission.

        Args:
            ch: Character to process (may be part of escape sequence).
        """
        # Handle unicode escape sequence (\uXXXX)
        if self._unicode_digits is not None:
            self._unicode_digits.append(ch)
            if len(self._unicode_digits) == 4:
                # Complete unicode escape, decode and add to buffer
                try:
                    codepoint = int("".join(self._unicode_digits), 16)
                    self._buffer.append(chr(codepoint))
                except ValueError:
                    # Invalid hex, skip
                    pass
                self._unicode_digits = None
            return
        # Handle escape sequences (\n, \t, \", etc.)
        if self._escape:
            self._escape = False
            if ch == "u":
                # Start of unicode escape
                self._unicode_digits = []
                return
            # Map escape sequences to actual characters
            self._buffer.append(self._escape_map(ch))
            return
        if ch == "\\":
            # Next character is escaped
            self._escape = True
            return
        if ch == '"':
            # Closing quote, string value complete
            self._in_string = False
            self._completed = True
            return
        # Regular character, add to buffer
        self._buffer.append(ch)

    def _scan_key(self, ch: str) -> None:
        """
        Scan for the "response_text" key character-by-character.

        Implements simple pattern matching with reset on mismatch. If we find
        a partial match (first character matches), we continue from position 1
        to handle overlapping patterns.

        Args:
            ch: Character to check against target key.
        """
        target = self.TARGET
        if ch == target[self._key_index]:
            # Character matches, advance
            self._key_index += 1
            if self._key_index == len(target):
                # Complete match found
                self._state = "post_key"
        else:
            # Mismatch: reset to start, or position 1 if first char matches
            self._key_index = 1 if ch == target[0] else 0

    def _reset(self) -> None:
        """
        Reset state machine to search state.

        Called when we lose track of the key pattern (e.g., unexpected character).
        """
        self._key_index = 0
        self._state = "search"

    def _flush(self) -> None:
        """
        Emit accumulated buffer contents via callback and clear buffer.

        Why:
            We buffer characters to emit them in chunks rather than one-by-one,
            reducing callback overhead. Flush is called after each input chunk
            to ensure real-time streaming.
        """
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
        """
        Map JSON escape sequence characters to actual characters.

        Args:
            ch: Escape sequence character (e.g., 'n' for \\n, 't' for \\t).

        Returns:
            Decoded character, or original if not a recognized escape.

        Why:
            JSON allows escape sequences like \\n (newline), \\t (tab), etc.
            This maps them to their actual character values for proper display.
        """
        mapping = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",  # Backspace
            "f": "\f",  # Form feed
            "n": "\n",  # Newline
            "r": "\r",  # Carriage return
            "t": "\t",  # Tab
        }
        return mapping.get(ch, ch)

    # NOTE: The following methods appear to be orphaned/duplicate code.
    # They duplicate functionality from OpenAIChatProvider and may be legacy code.
    # Keeping them for now to avoid breaking changes, but they may be removed in future cleanup.
    def _system_prompt(self, bundle: PromptBundle) -> str:
        """
        Build system prompt with JSON schema enforcement hint.

        NOTE: This method appears to be duplicate/orphaned code.
        See OpenAIChatProvider._system_prompt() for the canonical implementation.
        """
        schema_hint = (
            "You must reply with JSON matching the schema provided. "
            "Never include Markdown or prose outside of the JSON object."
        )
        return f"{bundle.system_prompt}\n\n{schema_hint}"

    def _user_payload(self, bundle: PromptBundle) -> str:
        """
        Extract and clean user prompt from bundle.

        NOTE: This method appears to be duplicate/orphaned code.
        See OpenAIChatProvider._user_payload() for the canonical implementation.
        """
        return bundle.user_prompt.strip()

    @staticmethod
    def _response_schema() -> Mapping[str, Any]:
        """
        Return JSON schema enforced by response_format parameter.

        NOTE: This method appears to be duplicate/orphaned code.
        See OpenAIChatProvider._response_schema() for the canonical implementation.
        """
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
        """
        Extract text content from Chat Completions API response.

        Handles both string content and list-based multi-modal content (text/images).
        Returns empty string if no content is found.

        Why:
            API responses can have content as string or list of parts. This normalizes
            to a single string for JSON parsing.

        NOTE: This method is defined in _ResponseTextExtractor class but is actually
        used by OpenAIChatProvider. It's placed here due to historical code organization.
        Consider moving to OpenAIChatProvider or a shared utility module in future cleanup.
        """
        choices = payload.get("choices") or []
        if not choices:
            raise PromptGenerationError("OpenAI response did not include choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        # Handle multi-modal content (list of parts)
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
        return str(content or "").strip()


def _emit_stream_chunks(callback: Callable[[str], None], text: str, *, chunk_size: int = 64) -> None:
    """
    Emit a text payload to a streaming callback in word-safe chunks.

    Mirrors the SSE chunking strategy used by the chat portal so the MCP
    providers can surface incremental deltas even when the underlying HTTP
    response is non-streaming.

    Args:
        callback: Callback function to emit each chunk (same as `emit` parameter in duplicate function).
        text: The full text to chunk and emit.
        chunk_size: Target chunk size in characters (default 64).

    Why:
        The chat portal expects incremental SSE events for real-time UX.
        When providers return full text (e.g., planner-only passes), we need
        to simulate streaming by chunking the response. Word boundaries prevent
        mid-word splits that would break rendering.

    NOTE: This function is a duplicate of the one at line 327, with a slightly
    different signature (uses `callback` instead of `emit`, and `*` before chunk_size).
    Both serve the same purpose. Consider consolidating in future cleanup.

    Used by:
        - MCP providers when non-streaming responses need to be chunked for SSE
        - Orchestrators when providers return full text but portal expects SSE
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

    Converts httpx's iterator-based streaming API to the file-like interface
    expected by _iter_sse_events(). This allows reuse of SSE parsing logic
    across different HTTP clients (urllib vs httpx).

    Why:
        httpx.iter_lines() returns an iterator, but _iter_sse_events() expects
        a file-like object with readline(). This adapter bridges the gap.
    """

    def __init__(self, iterator: Iterable[bytes] | Iterable[str]) -> None:
        """
        Initialize adapter with an iterator from httpx.iter_lines().

        Args:
            iterator: Iterator yielding bytes or strings (from httpx response).
        """
        self._iterator = iter(iterator)

    def readline(self) -> bytes:
        """
        Read next line from iterator, ensuring newline delimiter.

        Returns:
            Bytes with trailing newline, or empty bytes if iterator exhausted.

        Why:
            httpx.iter_lines() yields lines without trailing newlines, but SSE
            parser needs newlines to detect blank lines (event boundaries).
            We add newlines to preserve SSE format compatibility.
        """
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
    Yield decoded payload strings from a Server-Sent Events (SSE) stream.

    The OpenAI-compatible APIs send newline-delimited `data:` entries followed by
    a blank line. Each yielded string corresponds to the bytes after `data:`
    (with `[DONE]` filtered out).

    Args:
        stream: File-like object with readline() method returning bytes.

    Yields:
        Decoded UTF-8 strings from `data:` lines. Empty strings are skipped.
        Stops when `[DONE]` is encountered or stream ends.

    SSE format:
        data: {"id":"chatcmpl-123","object":"chat.completion.chunk",...}\n
        data: {"id":"chatcmpl-123","object":"chat.completion.chunk",...}\n
        \n
        [blank line signals end of event]

    Why:
        SSE is the standard format for streaming HTTP responses. This parser
        extracts the JSON payloads from the SSE envelope so we can process
        them as regular JSON objects.
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
    """
    Merge incremental tool call delta into accumulated state.

    Tool calls arrive incrementally in streaming responses. This function merges
    each delta (partial tool call data) into the accumulated state for that tool
    call index.

    Args:
        store: Dict mapping tool call index to accumulated state.
        delta: Partial tool call data from streaming response.

    Why:
        Streaming tool calls arrive in chunks (e.g., function name first, then
        arguments character-by-character). We accumulate these into complete
        tool call objects for the orchestrator to execute.

    Example:
        Delta 1: {"index": 0, "function": {"name": "search"}}
        Delta 2: {"index": 0, "function": {"arguments": "{\"query\":\""}}
        Delta 3: {"index": 0, "function": {"arguments": "hello"}}
        Result: Complete tool call with name="search", arguments='{"query":"hello"}'
    """
    try:
        idx = int(delta.get("index", 0))
    except (TypeError, ValueError):
        idx = 0
    # Initialize state for this tool call index if not exists
    state = store.setdefault(
        idx,
        {"id": None, "type": None, "function": {"name": None, "arguments": ""}},
    )
    # Merge tool call ID (if present in delta)
    identifier = delta.get("id")
    if isinstance(identifier, str) and identifier:
        state["id"] = identifier
    # Merge tool call type (if present in delta)
    tool_type = delta.get("type")
    if isinstance(tool_type, str) and tool_type:
        state["type"] = tool_type
    # Merge function name and arguments (accumulated incrementally)
    function_block = state.setdefault("function", {"name": None, "arguments": ""})
    func_delta = delta.get("function") if isinstance(delta.get("function"), Mapping) else {}
    func_name = func_delta.get("name")
    if isinstance(func_name, str) and func_name:
        function_block["name"] = func_name
    # Append arguments (they arrive character-by-character in streaming)
    func_args = func_delta.get("arguments")
    if isinstance(func_args, str) and func_args:
        existing = function_block.get("arguments") or ""
        function_block["arguments"] = f"{existing}{func_args}"


def _collapse_stream_tool_calls(store: dict[int, dict[str, object]]) -> list[dict[str, object]]:
    """
    Convert accumulated tool call state into Chat Completions format.

    Args:
        store: Dict mapping tool call index to accumulated state (from _merge_stream_tool_call).

    Returns:
        List of tool call objects in Chat Completions API format, sorted by index.

    Why:
        After merging all streaming deltas, we need to convert the accumulated
        state into the standard tool_calls format expected by the orchestrator.
        Sorting by index ensures tool calls are in the order the model intended.

    Used by:
        _consume_chat_completion_stream() to assemble final tool_calls array.
    """
    collapsed: list[dict[str, object]] = []
    # Sort by index to preserve model's intended order
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

    Parses Server-Sent Events (SSE) stream and assembles the complete response
    while emitting text deltas via callback for real-time portal updates.

    Args:
        stream: File-like object with readline() method (from urllib or httpx).
        on_stream_delta: Optional callback for streaming text deltas (SSE delivery).

    Returns:
        Dict matching Chat Completions API format: {"choices": [{"message": {...}}]}
        The message contains either `content` (text) or `tool_calls` (function invocations).

    Why:
        Streaming responses arrive as SSE events (data: {...}\n\n). This function:
        1. Parses SSE events into JSON chunks
        2. Assembles deltas into complete message
        3. Handles tool_calls that arrive incrementally
        4. Emits text deltas immediately for UX

    Edge cases:
        - Handles both string and list-based content (multi-modal)
        - Merges incremental tool_calls from multiple chunks
        - Captures final message payload if sent in last event
        - Logs timing metrics (first delta, total elapsed)

    Used by:
        - OpenAIChatProvider.generate() for streaming responses
        - OpenAIToolsProvider.chat() for MCP streaming
        - DeepSeekToolsProvider.chat() for MCP streaming
    """

    # Accumulate streaming deltas into complete message
    text_parts: list[str] = []  # Text content deltas (assembled into final content)
    tool_calls: dict[int, dict[str, object]] = {}  # Tool call deltas keyed by index (merged incrementally)
    role: str | None = None  # Assistant role (set from first delta, if present)
    finish_reason: str | None = None  # Why the stream ended ("stop", "tool_calls", "length", etc.)
    # Some providers send complete message in final SSE event instead of deltas
    last_message_content: str | None = None  # Fallback: complete content from final event
    last_message_tool_calls: list[dict[str, object]] | None = None  # Fallback: complete tool_calls from final event

    def _normalize_delta(chunk: str) -> str:
        """
        Normalize delta chunk to empty string if None/empty.

        Helper to ensure consistent handling of empty deltas in streaming responses.
        """
        return chunk or ""

    # Track timing metrics for performance monitoring
    start_first = time.monotonic()  # When we started processing the stream
    first_delta_at: float | None = None  # When we received the first content delta (measures time-to-first-token)

    # Parse SSE events from stream (each event is a JSON payload after "data:" prefix)
    for payload in _iter_sse_events(stream):
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            # Skip malformed JSON (some providers send metadata events we ignore)
            continue
        choices = data.get("choices") or []
        if not choices:
            continue
        choice = choices[0]  # Most providers only send one choice per event
        delta = choice.get("delta") or {}  # Incremental content update
        message_block = choice.get("message") or {}  # Complete message (some providers send this in final event)
        finish = choice.get("finish_reason")
        if isinstance(finish, str):
            finish_reason = finish  # "stop", "tool_calls", "length", etc.
        # Role is typically only set in first delta, preserve it across events
        role = delta.get("role") or role

        # Handle text content deltas (can be string or list of multi-modal parts)
        content_block = delta.get("content")
        if isinstance(content_block, list):
            # Multi-modal content: extract text from each part (images, text, etc.)
            for chunk in content_block:
                if not isinstance(chunk, Mapping):
                    continue
                text = _normalize_delta(chunk.get("text") or "")
                if not text:
                    continue
                # Track time-to-first-token for latency monitoring
                if first_delta_at is None:
                    first_delta_at = time.monotonic()
                text_parts.append(text)
                # Emit delta immediately for real-time UX (SSE delivery to chat portal)
                if on_stream_delta:
                    try:
                        on_stream_delta(text)
                    except Exception:  # pragma: no cover - safeguard user callbacks
                        logger.exception("Streaming callback failed while emitting delta chunk.")
        elif isinstance(content_block, str) and content_block:
            # Simple string content delta
            normalized = _normalize_delta(content_block)
            text_parts.append(normalized)
            if first_delta_at is None:
                first_delta_at = time.monotonic()
            if on_stream_delta:
                try:
                    on_stream_delta(normalized)
                except Exception:  # pragma: no cover - safeguard user callbacks
                    logger.exception("Streaming callback failed while emitting delta chunk.")

        # Handle tool call deltas (arrive incrementally: name first, then arguments character-by-character)
        for tool_delta in delta.get("tool_calls") or []:
            if isinstance(tool_delta, Mapping):
                # Merge this delta into accumulated tool call state (by index)
                _merge_stream_tool_call(tool_calls, tool_delta)

        # Capture any full message payload sent on streaming frames (some providers
        # emit the final message in the last SSE event instead of deltas).
        # This is a fallback for providers that don't stream deltas properly.
        if isinstance(message_block, Mapping):
            msg_content = message_block.get("content")
            if isinstance(msg_content, list):
                # Multi-modal: extract text from all parts
                joined = "".join(part.get("text", "") for part in msg_content if isinstance(part, Mapping)).strip()
                if joined:
                    last_message_content = joined
            elif isinstance(msg_content, str):
                # Simple string content
                stripped = msg_content.strip()
                if stripped:
                    last_message_content = stripped
            msg_tools = message_block.get("tool_calls")
            if isinstance(msg_tools, list) and msg_tools:
                # Complete tool_calls array from final event
                last_message_tool_calls = msg_tools

    # Assemble final text from streaming deltas
    assembled_text = "".join(text_parts).strip()
    # Fallback: some providers send complete message in last SSE event instead of deltas
    # (e.g., when streaming is enabled but provider doesn't support incremental deltas)
    if not assembled_text and last_message_content:
        assembled_text = last_message_content

    # Determine message type: tool_calls take precedence over text content
    # This is critical for MCP orchestrator: it needs to know if model wants to call tools
    # Priority: tool_calls (if finish_reason indicates tools OR if we have tools but no text) > last_message_tool_calls > text content
    if (finish_reason == "tool_calls" or (tool_calls and not assembled_text)) and tool_calls:
        # Model wants to call tools: return tool_calls for orchestrator to execute
        # The orchestrator will dispatch these and add tool results to conversation
        message = {
            "role": role or "assistant",
            "tool_calls": _collapse_stream_tool_calls(tool_calls),
        }
    elif not assembled_text and last_message_tool_calls:
        # Fallback: use tool_calls from last message block if no text was streamed
        # (handles edge case where provider sends tool_calls in final event, not deltas)
        message = {
            "role": role or "assistant",
            "tool_calls": last_message_tool_calls,
        }
    else:
        # Normal text response: return assembled content
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

    return {"choices": [{"message": message}]}


class OpenAIToolsProvider(BaseMcpProvider):
    """
    OpenAI tool-calling provider for MCP orchestrator.

    Implements BaseMcpProvider.chat so MCP mode can stream deltas and return
    assistant/tool_calls payloads compatible with the orchestrator.

    Key differences from OpenAIChatProvider:
        - Accepts raw messages (not PromptBundle) for flexibility
        - Supports tool definitions and tool_calls in responses
        - Returns raw API format so orchestrator can inspect tool_calls
        - Uses httpx when available for better streaming support
        - Falls back to urllib if httpx is not installed

    Design decisions:
        - Uses httpx for better streaming (when available)
        - Supports separate connect/read timeouts via env vars
        - Returns raw Chat Completions format for tool_calls inspection
        - Parses structured JSON only when no tool_calls are present

    Related:
        - Implements BaseMcpProvider for McpOrchestratorService
        - Used when OPENAI_API_KEY is configured and MCP mode is enabled
        - Tool definitions come from mcp/tools.py
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
        """
        Initialize OpenAI tools provider with configuration.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var).
            model: Model identifier (defaults to OPENAI_MODEL or "gpt-4o-mini").
            base_url: API base URL (defaults to OPENAI_BASE_URL or official endpoint).
                Allows proxying through services like Azure OpenAI.
            timeout: HTTP request timeout in seconds (default 60.0).
            temperature: Sampling temperature (default 0.3 for consistency).
            top_p: Nucleus sampling parameter (default 0.9).

        Raises:
            PromptGenerationError: If api_key is not configured.

        Design:
            - Uses httpx when available for better streaming support
            - Falls back to urllib if httpx is not installed
            - Supports separate connect/read timeouts via env vars for fine-tuning
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise PromptGenerationError("OPENAI_API_KEY is not configured for OpenAIToolsProvider.")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
        # Configure timeout: use env vars for separate connect/read timeouts if available
        timeout_cfg = self.timeout
        if httpx and (HTTP_TIMEOUT_CONNECT or HTTP_TIMEOUT_READ):
            try:
                connect = float(HTTP_TIMEOUT_CONNECT) if HTTP_TIMEOUT_CONNECT else None
                read = float(HTTP_TIMEOUT_READ) if HTTP_TIMEOUT_READ else None
                # Separate timeouts allow fine-tuning for slow networks or large responses
                timeout_cfg = httpx.Timeout(timeout=self.timeout, connect=connect or self.timeout, read=read or self.timeout)
            except Exception:
                timeout_cfg = self.timeout
        # Prefer httpx for better streaming, fall back to urllib if not available
        self._http_client = httpx.Client(base_url=self.base_url, timeout=timeout_cfg) if httpx else None

    def chat(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        tools: Iterable[Mapping[str, object]] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        """
        Generate chat completion with optional tool calling for MCP orchestrator.

        Args:
            messages: Chat message history (system, user, assistant, tool messages).
            tools: Optional tool definitions for function calling.
            on_stream_delta: Optional callback for streaming text deltas (SSE delivery).
            response_format: Optional response format constraints (used for planner-only passes).

        Returns:
            Chat Completions API response format. If tool_calls are present, returns
            raw format for orchestrator to dispatch. Otherwise, parses structured JSON
            with response_text, actions, and extractions.

        Raises:
            PromptGenerationError: On API errors, network failures, or invalid JSON.

        Design:
            - Supports both streaming and non-streaming modes
            - Returns raw API format when tool_calls are present (orchestrator handles execution)
            - Parses structured JSON only for final assistant responses
            - Uses httpx when available, falls back to urllib
        """
        start_time = time.monotonic()
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
            # This is used by the orchestrator's planner-only pass after streaming.
            # 
            # Why: After streaming completes, the orchestrator may do a planner-only
            # pass to extract actions/extractions. This pass uses non-streaming mode
            # with response_format to ensure structured JSON output.
            payload["response_format"] = OpenAIChatProvider._response_schema()
        if tools:
            # Enable tool calling for MCP workflows (search, read, aggregate, etc.)
            # Tools are defined in mcp/tools.py and passed by the orchestrator
            payload["tools"] = list(tools)
            # "auto" lets the model decide when to call tools vs respond with text
            # The orchestrator inspects tool_calls in the response to dispatch function calls
            payload["tool_choice"] = "auto"  # Let model decide when to call tools
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
        elapsed_ms = None
        start_time = time.monotonic()
        raw_body: str | None = None
        # Use httpx if available (better streaming support), otherwise fall back to urllib
        # This allows the provider to work even when httpx is not installed
        if self._http_client:
            try:
                if streaming:
                    # httpx streaming: use stream() context manager for SSE parsing
                    with self._http_client.stream(
                        "POST",
                        "/v1/chat/completions",
                        json=payload,
                        headers=headers,
                        timeout=self.timeout,
                    ) as resp:
                        status_code = resp.status_code
                        if status_code >= 400:
                            detail = resp.text[:200]
                            raise PromptGenerationError(f"OpenAI tools error ({status_code}): {detail}")
                        # Use _HttpxLineStream adapter to convert httpx iterator to file-like interface
                        data = _consume_chat_completion_stream(_HttpxLineStream(resp.iter_lines()), on_stream_delta)
                else:
                    # httpx non-streaming: simple POST request
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
                # httpx-specific HTTP errors (network issues, timeouts, etc.)
                raise PromptGenerationError(f"OpenAI tools request failed: {exc}") from exc
        else:
            # Fallback to urllib when httpx is not available
            # urllib is part of the standard library, so it's always available
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
                        # urllib streaming: response object is already file-like, can use directly
                        data = _consume_chat_completion_stream(resp, on_stream_delta)
                        raw_body = None
                    else:
                        # urllib non-streaming: read complete response
                        raw_body = resp.read().decode("utf-8")
                        status_code = getattr(resp, "status", 200)
            except urllib_error.HTTPError as exc:
                # urllib HTTP errors (4xx, 5xx status codes)
                detail = exc.read().decode("utf-8", errors="ignore")
                raise PromptGenerationError(
                    f"OpenAI tools error ({exc.code}): {detail.strip()[:200]}"
                ) from exc
            except urllib_error.URLError as exc:
                # urllib network errors (timeout, connection refused, DNS failures, etc.)
                raise PromptGenerationError(f"OpenAI tools request failed: {exc}") from exc
            if not streaming and status_code >= 400:
                # Defensive check: urllib HTTPError should catch this, but verify anyway
                raise PromptGenerationError(f"OpenAI tools error ({status_code}): {raw_body[:200] if raw_body else status_code}")
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
        # The orchestrator will execute tools and add tool results to the conversation.
        # 
        # Why return raw format: The orchestrator needs to inspect tool_calls
        # to determine which functions to call. It will execute them and add
        # tool results as tool messages, then continue the conversation loop.
        if tool_calls:
            return data

        # Final assistant turn: parse structured JSON from the message content.
        # This happens when the model has finished tool calls and is providing
        # the final answer with actions/extractions.
        # 
        # Flow: Model calls tools -> Orchestrator executes -> Model responds with
        # final answer containing response_text, actions, and extractions
        content = message.get("content")
        if isinstance(content, list):
            # Multi-modal content: extract text from all parts
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
        else:
            # Simple string content
            text = str(content or "").strip()
        if not text:
            # Empty response: return empty structure (orchestrator handles gracefully)
            return {"role": "assistant", "content": "", "actions": [], "extractions": []}

        try:
            # Parse structured JSON (enforced by response_format in non-streaming mode)
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: treat as plain text answer without actions/extractions.
            # This handles cases where the model doesn't follow the JSON schema
            # (e.g., when response_format isn't enforced or model ignores it)
            parsed = {"response_text": text, "actions": [], "extractions": []}

        response_text = str(parsed.get("response_text") or "").strip()

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
        """
        Initialize DeepSeek tools provider with configuration.

        Args:
            api_key: DeepSeek API key (defaults to DEEPSEEK_API_KEY env var).
            model: Model identifier (defaults to DEEPSEEK_MODEL or "deepseek-chat").
            base_url: API base URL (defaults to DEEPSEEK_BASE_URL or official endpoint).
            timeout: HTTP request timeout in seconds (default 60.0).
            temperature: Sampling temperature (default 0.3 for consistency).
            top_p: Nucleus sampling parameter (default 0.9).

        Raises:
            PromptGenerationError: If api_key is not configured.

        Design:
            - Uses httpx when available for better streaming support
            - Falls back to urllib if httpx is not installed
            - Supports separate connect/read timeouts via env vars for fine-tuning
        """
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise PromptGenerationError("DEEPSEEK_API_KEY is not configured for DeepSeekToolsProvider.")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
        # Configure timeout: use env vars for separate connect/read timeouts if available
        timeout_cfg = self.timeout
        if httpx and (HTTP_TIMEOUT_CONNECT or HTTP_TIMEOUT_READ):
            try:
                connect = float(HTTP_TIMEOUT_CONNECT) if HTTP_TIMEOUT_CONNECT else None
                read = float(HTTP_TIMEOUT_READ) if HTTP_TIMEOUT_READ else None
                # Separate timeouts allow fine-tuning for slow networks or large responses
                timeout_cfg = httpx.Timeout(timeout=self.timeout, connect=connect or self.timeout, read=read or self.timeout)
            except Exception:
                timeout_cfg = self.timeout
        # Prefer httpx for better streaming, fall back to urllib if not available
        self._http_client = httpx.Client(base_url=self.base_url, timeout=timeout_cfg) if httpx else None

    def chat(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        tools: Iterable[Mapping[str, object]] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
        response_format: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        """
        Generate chat completion with optional tool calling for MCP orchestrator.

        Args:
            messages: Chat message history (system, user, assistant, tool messages).
            tools: Optional tool definitions for function calling.
            on_stream_delta: Optional callback for streaming text deltas (SSE delivery).
            response_format: Optional response format constraints.

        Returns:
            Chat Completions API response format. If tool_calls are present, returns
            raw format for orchestrator to dispatch. Otherwise, parses structured JSON
            with response_text, actions, and extractions.

        Raises:
            PromptGenerationError: On API errors, network failures, or invalid JSON.

        Design:
            - Supports both streaming and non-streaming modes
            - Includes fallback retry logic for empty streaming responses (DeepSeek quirk)
            - Returns raw API format when tool_calls are present
            - Parses structured JSON only for final assistant responses
            - Uses httpx when available, falls back to urllib
        """
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
        elapsed_ms = None
        start_time = time.monotonic()
        raw_body: str | None = None
        # Use httpx if available (better streaming support), otherwise fall back to urllib
        # This allows the provider to work even when httpx is not installed
        if self._http_client:
            try:
                if streaming:
                    # httpx streaming: use stream() context manager for SSE parsing
                    with self._http_client.stream(
                        "POST",
                        "/v1/chat/completions",
                        json=payload,
                        headers=headers,
                        timeout=self.timeout,
                    ) as resp:
                        status_code = resp.status_code
                        if status_code >= 400:
                            detail = resp.text[:200]
                            raise PromptGenerationError(f"DeepSeek tools error ({status_code}): {detail}")
                        # Use _HttpxLineStream adapter to convert httpx iterator to file-like interface
                        data = _consume_chat_completion_stream(_HttpxLineStream(resp.iter_lines()), on_stream_delta)
                else:
                    # httpx non-streaming: simple POST request
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
                # httpx-specific HTTP errors (network issues, timeouts, etc.)
                raise PromptGenerationError(f"DeepSeek tools request failed: {exc}") from exc
        else:
            # Fallback to urllib when httpx is not available
            # urllib is part of the standard library, so it's always available
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
                        # urllib streaming: response object is already file-like, can use directly
                        data = _consume_chat_completion_stream(resp, on_stream_delta)
                        raw_body = None
                    else:
                        # urllib non-streaming: read complete response
                        raw_body = resp.read().decode("utf-8")
                        status_code = getattr(resp, "status", 200)
            except urllib_error.HTTPError as exc:
                # urllib HTTP errors (4xx, 5xx status codes)
                detail = exc.read().decode("utf-8", errors="ignore")
                raise PromptGenerationError(
                    f"DeepSeek tools error ({exc.code}): {detail.strip()[:200]}"
                ) from exc
            except urllib_error.URLError as exc:
                # urllib network errors (timeout, connection refused, DNS failures, etc.)
                raise PromptGenerationError(f"DeepSeek tools request failed: {exc}") from exc
            if not streaming and status_code >= 400:
                # Defensive check: urllib HTTPError should catch this, but verify anyway
                raise PromptGenerationError(f"DeepSeek tools error ({status_code}): {raw_body[:200] if raw_body else status_code}")
            elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if streaming:
            # In streaming mode we return the assembled assistant message so the
            # orchestrator can inspect tool_calls or final content.
            # 
            # DeepSeek-specific fallback: Sometimes DeepSeek streaming returns empty
            # content even when the model generated text. This defensive retry issues
            # a single non-streaming completion to recover the text, preventing
            # "(no content)" replies that confuse users.
            # 
            # Why this exists: DeepSeek's streaming API has a known quirk where
            # streaming responses can be empty even when the model generated content.
            # This retry ensures we always return content when available, maintaining
            # UX consistency. The retry only happens once to avoid infinite loops.
            try:
                if isinstance(data, Mapping):
                    choices = data.get("choices") or []
                    if choices:
                        message = choices[0].get("message") or {}
                        if isinstance(message, Mapping):
                            tool_calls = message.get("tool_calls") or []
                            content = message.get("content")
                            # Check if we have either text content or tool calls
                            # If neither, trigger retry to recover missing content
                            has_text = isinstance(content, str) and bool(content.strip())
                            if not has_text and not tool_calls:
                                structured_log(
                                    "llm",
                                    "warning",
                                    "DeepSeek MCP stream produced empty content; retrying once with non-stream completion.",
                                    logger_obj=logger,
                                    level=logging.WARNING,
                                )
                                # Build a non-streaming payload copy.
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

    Selects between OpenAIToolsProvider and DeepSeekToolsProvider based on:
    1. MCP_PROVIDER or LLM_PROVIDER env var (explicit preference)
    2. Available API keys (fallback to configured provider)
    3. Provider initialization success (graceful degradation)

    Returns:
        BaseMcpProvider instance if available, None otherwise.

    Why:
        MCP mode requires tool-calling support, so we use different providers
        than legacy mode. This function allows the orchestrator to select
        the best available provider without hardcoding.

    Used by:
        - chat_portal.py when MCP mode is enabled (business feature flag)
        - McpOrchestratorService.__init__() receives the provider

    Related:
        - load_default_provider() for legacy mode providers
        - Feature flags in chat_portal.py determine which mode to use
    """

    # Check for explicit provider preference (MCP_PROVIDER takes precedence over LLM_PROVIDER)
    preferred = (os.getenv("MCP_PROVIDER") or os.getenv("LLM_PROVIDER") or "").strip().lower()

    def _try(cls):
        """
        Attempt to instantiate a provider class, logging failures gracefully.

        Args:
            cls: Provider class to instantiate.

        Returns:
            Provider instance if successful, None if initialization fails.

        Why:
            Allows trying multiple providers in order without failing completely
            if one is misconfigured. Logs warnings for debugging but continues
            to next provider in fallback chain. This enables graceful degradation
            when API keys are missing or misconfigured.
        """
        try:
            return cls()
        except PromptGenerationError as exc:
            # Log but don't fail - allows fallback to next provider
            # This is critical: we want to try all available providers before giving up
            structured_log(
                "llm",
                "provider.disabled",
                {"provider": cls.__name__, "error": str(exc)},
                level=logging.WARNING,
            )
            return None

    # Determine provider priority order based on preference and available API keys
    order: list[type[BaseMcpProvider]] = []
    if preferred == "deepseek":
        # Explicit preference for DeepSeek: try it first, fallback to OpenAI
        order = [DeepSeekToolsProvider, OpenAIToolsProvider]
    elif preferred == "openai":
        # Explicit preference for OpenAI: try it first, fallback to DeepSeek
        order = [OpenAIToolsProvider, DeepSeekToolsProvider]
    else:
        # No explicit preference: use available API keys to determine order
        # OpenAI is preferred by default if both are configured (more stable tool calling)
        if os.getenv("OPENAI_API_KEY"):
            order.append(OpenAIToolsProvider)
        if os.getenv("DEEPSEEK_API_KEY"):
            order.append(DeepSeekToolsProvider)

    # Try each provider in priority order until one succeeds
    # This allows automatic fallback if the preferred provider is misconfigured
    for provider_cls in order:
        provider = _try(provider_cls)
        if provider:
            return provider
    # No provider available: orchestrator will use fallback strategies (heuristics)
    return None


def load_default_provider() -> BaseLLMProvider | None:
    """
    Instantiate the default legacy provider based on environment configuration.

    Selects between OpenAIChatProvider, DeepSeekChatProvider, and StubLLMProvider
    based on:
    1. LLM_PROVIDER env var (explicit preference: "openai" or "deepseek")
    2. Available API keys (fallback to configured provider)
    3. Provider initialization success (returns None if all fail)

    Returns:
        BaseLLMProvider instance if available, None otherwise (triggers fallback).

    Why:
        Legacy orchestrator needs structured JSON responses. This function allows
        the orchestrator to select the best available provider without hardcoding.

    Used by:
        - chat_portal.py when legacy mode is enabled (default)
        - AiOrchestratorService.__init__() receives the provider

    Related:
        - load_mcp_provider() for MCP mode providers
        - Feature flags in chat_portal.py determine which mode to use
    """

    # Check for explicit provider preference (legacy mode uses LLM_PROVIDER only)
    preferred = (os.getenv("LLM_PROVIDER") or "").strip().lower()

    def _try(cls):
        """
        Attempt to instantiate a provider class, logging failures gracefully.

        Args:
            cls: Provider class to instantiate.

        Returns:
            Provider instance if successful, None if initialization fails.

        Why:
            Allows trying multiple providers in order without failing completely
            if one is misconfigured. Logs warnings for debugging but continues
            to next provider in fallback chain. This enables graceful degradation
            when API keys are missing or misconfigured.
        """
        try:
            return cls()
        except PromptGenerationError as exc:
            # Log but don't fail - allows fallback to next provider
            # This is critical: we want to try all available providers before giving up
            structured_log(
                "llm",
                "provider.disabled",
                {"provider": cls.__name__, "error": str(exc)},
                level=logging.WARNING,
            )
            return None

    # Determine provider priority order based on preference and available API keys
    order: list[type] = []
    if preferred == "deepseek":
        # Explicit preference for DeepSeek: try it first, fallback to OpenAI
        order = [DeepSeekChatProvider, OpenAIChatProvider]
    elif preferred == "openai":
        # Explicit preference for OpenAI: try it first, fallback to DeepSeek
        order = [OpenAIChatProvider, DeepSeekChatProvider]
    else:
        # No explicit preference: use available API keys to determine order
        # OpenAI is preferred by default if both are configured (more stable JSON responses)
        if os.getenv("OPENAI_API_KEY"):
            order.append(OpenAIChatProvider)
        if os.getenv("DEEPSEEK_API_KEY"):
            order.append(DeepSeekChatProvider)

    # Try each provider in priority order until one succeeds
    # This allows automatic fallback if the preferred provider is misconfigured
    for provider_cls in order:
        provider = _try(provider_cls)
        if provider:
            return provider
    # No provider available: orchestrator will use fallback strategies (heuristics)
    # The orchestrator catches None and falls back to rule-based responses
    return None
