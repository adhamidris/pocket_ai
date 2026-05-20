from __future__ import annotations

import logging
import os
from threading import Lock

from apps.llm.chat_providers import DeepSeekChatProvider, OpenAIChatProvider
from apps.llm.interfaces import BaseLLMProvider, BaseMcpProvider
from apps.llm.retry import PromptGenerationError
from apps.llm.tool_providers import DeepSeekToolsProvider, OpenAIToolsProvider
from apps.rag.rag_logging import structured_log


_MCP_PROVIDER_SINGLETON: BaseMcpProvider | None = None
_MCP_PROVIDER_LOCK = Lock()



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
