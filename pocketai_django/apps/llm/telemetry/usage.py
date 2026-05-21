from __future__ import annotations

import logging
from typing import Iterable, Mapping

from apps.rag.observability.logging import structured_log
from core.otel import Span

try:  # optional dependency for accurate token estimates
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover - optional
    tiktoken = None

logger = logging.getLogger(__name__)


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
