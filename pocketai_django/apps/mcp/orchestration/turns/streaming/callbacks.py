from __future__ import annotations

import logging
from collections.abc import Callable


def _append_chunk(
    chunk: str,
    target: list[str],
    *,
    on_response_text_delta: Callable[[str], None] | None,
    logger_obj: logging.Logger,
) -> None:
    if not chunk:
        return
    target.append(chunk)
    if on_response_text_delta:
        try:
            on_response_text_delta(chunk)
        except Exception:  # pragma: no cover - defensive
            logger_obj.exception("on_response_text_delta callback failed")

def _emit_tokens(
    text: str,
    *,
    streaming_mode: str,
    first_pass_streamed_chunks: list[str],
    answer_streamed_chunks: list[str],
    on_response_text_delta: Callable[[str], None] | None,
    logger_obj: logging.Logger,
) -> None:
    if not text:
        return
    target = first_pass_streamed_chunks if streaming_mode == "initial" else answer_streamed_chunks
    _append_chunk(
        text,
        target,
        on_response_text_delta=on_response_text_delta,
        logger_obj=logger_obj,
    )
