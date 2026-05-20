from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _first_stream_chunk(
    chunk: str,
    *,
    dsml_skip_line: bool,
    inline_response_blocks_detected: bool,
    initial_stream_started: bool,
    filter_dsml_stream: Callable[[str, bool], tuple[str, bool]],
    status_event: Callable[[str, str | None], None],
    emit_tokens: Callable[[str], None],
) -> tuple[bool, bool, bool]:
    if not chunk:
        return dsml_skip_line, inline_response_blocks_detected, initial_stream_started
    chunk, dsml_skip_line = filter_dsml_stream(chunk, dsml_skip_line)
    if not chunk or inline_response_blocks_detected:
        return dsml_skip_line, inline_response_blocks_detected, initial_stream_started
    if not initial_stream_started:
        initial_stream_started = True
        status_event("responding", "Responding...")
    emit_tokens(chunk)
    return dsml_skip_line, inline_response_blocks_detected, initial_stream_started


def _answer_stream_chunk(
    chunk: str,
    *,
    stream_buffer: str,
    dsml_skip_line: bool,
    inline_response_blocks_detected: bool,
    final_answer_started: bool,
    filter_dsml_stream: Callable[[str, bool], tuple[str, bool]],
    mark_answer_started: Callable[[], None],
    emit_tokens: Callable[[str], None],
    response_block_pattern: Any,
) -> tuple[str, bool, bool]:
    if not chunk:
        return stream_buffer, dsml_skip_line, inline_response_blocks_detected
    chunk, dsml_skip_line = filter_dsml_stream(chunk, dsml_skip_line)
    if not chunk or inline_response_blocks_detected:
        return stream_buffer, dsml_skip_line, inline_response_blocks_detected
    if not final_answer_started:
        mark_answer_started()
    stream_buffer = f"{stream_buffer}{chunk}"
    block_match = response_block_pattern.search(stream_buffer)
    emit_text = stream_buffer
    if block_match:
        emit_text = stream_buffer[: block_match.start()]
        inline_response_blocks_detected = True
    stream_buffer = ""
    if emit_text:
        emit_tokens(emit_text)
    return stream_buffer, dsml_skip_line, inline_response_blocks_detected
