from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from apps.llm.prompts.builder import PromptBundle
from apps.llm.runtime.retry import PromptGenerationError


class BaseLLMProvider(Protocol):
    """Interface for future provider implementations (OpenAI, Azure, etc.)."""

    def generate(
        self,
        bundle: PromptBundle,
        *,
        on_stream_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
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
        on_reasoning_delta: Callable[[str], None] | None = None,
        on_tool_call_start: Callable[[Mapping[str, object]], None] | None = None,
        on_tool_call_delta: Callable[[Mapping[str, object]], None] | None = None,
        response_format: Mapping[str, object] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:  # pragma: no cover - interface only
        ...


@dataclass
class StubLLMProvider:
    """
    Default no-op provider so the orchestrator can be tested without a real model.

    The stub simply raises PromptGenerationError to signal that a fallback strategy
    (heuristics) should be used.
    """

    def generate(
        self,
        bundle: PromptBundle,
        *,
        on_stream_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        raise PromptGenerationError("LLM provider not configured")
