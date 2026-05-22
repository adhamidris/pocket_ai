from __future__ import annotations

from collections.abc import Callable

from apps.llm.llm_provider import _emit_stream_chunks

from .callbacks import _append_chunk


def _emit_final_answer(
    text: str,
    *,
    mark_answer_started: Callable[[], None],
    answer_streamed_chunks: list[str],
    on_response_text_delta: Callable[[str], None] | None,
    logger_obj,
) -> str:
    if not text:
        return "final"
    mark_answer_started()
    _emit_stream_chunks(
        lambda chunk: _append_chunk(
            chunk,
            answer_streamed_chunks,
            on_response_text_delta=on_response_text_delta,
            logger_obj=logger_obj,
        ),
        text,
    )
    return "final"
