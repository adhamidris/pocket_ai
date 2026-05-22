from __future__ import annotations

from collections.abc import Callable

from apps.conversations.models import Conversation

from ....text.sanitizer import extract_sentences, sanitize_with_diagnostics


def _sanitize_stream_segment(
    text: str,
    *,
    conversation: Conversation,
    default_filter_level: str,
    stage: str,
    stream_dropped: list[str],
    filter_override: str | None = None,
) -> str:
    if not text:
        return ""
    effective_filter = filter_override or default_filter_level
    cleaned, dropped = sanitize_with_diagnostics(
        text,
        conversation=conversation,
        stage=stage,
        filter_level=effective_filter,
    )
    if dropped:
        stream_dropped.extend(dropped)
        if cleaned.strip() == text.strip():
            return ""
    return cleaned

def _flush_stream_buffer(
    stream_buffer: str,
    *,
    conversation: Conversation,
    default_filter_level: str,
    stage: str,
    stream_dropped: list[str],
    emit_tokens: Callable[[str], None],
    filter_override: str | None = None,
    flush_remainder: bool = False,
) -> str:
    pending = stream_buffer
    if not pending:
        return stream_buffer
    sentences, remainder = extract_sentences(pending)
    stream_buffer = remainder
    for sentence, sep in sentences:
        clean_segment = _sanitize_stream_segment(
            f"{sentence}{sep}",
            conversation=conversation,
            default_filter_level=default_filter_level,
            stage=stage,
            stream_dropped=stream_dropped,
            filter_override=filter_override,
        )
        if clean_segment:
            emit_tokens(clean_segment)
    if flush_remainder and stream_buffer:
        trailing_clean = _sanitize_stream_segment(
            stream_buffer,
            conversation=conversation,
            default_filter_level=default_filter_level,
            stage=stage,
            stream_dropped=stream_dropped,
            filter_override=filter_override,
        )
        stream_buffer = ""
        if trailing_clean:
            emit_tokens(trailing_clean)
    return stream_buffer
