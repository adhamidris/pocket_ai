from __future__ import annotations

import logging
import os
import time
from threading import Lock

from apps.llm.chat_providers import DeepSeekChatProvider, OpenAIChatProvider
from apps.llm.interfaces import BaseLLMProvider, BaseMcpProvider, StubLLMProvider
from apps.llm.provider_factory import load_default_provider, load_mcp_provider
from apps.llm.retry import (
    DEFAULT_RETRYABLE_STATUS_CODES,
    LLM_RETRY_BASE_DELAY_SECONDS,
    LLM_RETRY_JITTER_SECONDS,
    LLM_RETRY_MAX_ATTEMPTS,
    LLM_RETRY_MAX_DELAY_SECONDS,
    LLM_RETRYABLE_STATUSES,
    PromptGenerationError,
    _ProviderRequestError,
    _call_with_retry,
    _env_float,
    _env_int,
    _extract_retry_after,
    _extract_status_code,
    _is_retryable_transport_error,
    _parse_retry_after_seconds,
    _parse_retryable_statuses,
    _provider_request_error_from_exception,
    _retry_delay_seconds,
    _status_is_retryable,
)
from apps.llm.streaming import (
    _consume_chat_completion_stream,
    _ResponseTextExtractor,
    _emit_stream_chunks,
    _iter_sse_events,
)
from apps.llm.tool_providers import DeepSeekToolsProvider, OpenAIToolsProvider
from apps.llm.usage import (
    _coerce_usage_mapping,
    _estimate_text_tokens,
    _format_span_id,
    _format_trace_id,
    _log_span_debug,
    _log_usage,
    _message_char_stats,
    _normalize_usage_payload,
    _select_encoder,
)
from apps.rag.rag_logging import structured_log

# Optional flag to enable token estimation logs (guarded by DEBUG level as well).
LOG_TOKEN_ESTIMATE = os.getenv("LLM_LOG_TOKEN_ESTIMATE", "").strip().lower() in {"1", "true", "yes"}
# Optional flag to force debug payload logging even if DEBUG level is off.
LOG_DEBUG_PAYLOADS = os.getenv("LLM_DEBUG_PAYLOADS", "").strip().lower() in {"1", "true", "yes"}
# Optional HTTP timeout overrides (seconds) when using httpx client.
HTTP_TIMEOUT_CONNECT = os.getenv("LLM_HTTP_TIMEOUT_CONNECT")
HTTP_TIMEOUT_READ = os.getenv("LLM_HTTP_TIMEOUT_READ")

logger = logging.getLogger(__name__)
