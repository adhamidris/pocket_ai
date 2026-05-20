from __future__ import annotations

import copy
from typing import Mapping

from ..runtime.budget_guidance import build_repeat_search_guidance
from ..types import ToolExecutionContext
from .duplicate_detection import (
    _duplicate_result_diagnostics,
    _response_result_fingerprint,
)


def _finalize_search_history(
    *,
    final_response: object,
    context: ToolExecutionContext,
    new_contract_enabled: bool,
    result_fingerprint_top_k: int,
    duplicate_intent_diagnostics: dict[str, object] | None,
    intent_text: str,
    intent_embedding: list[float] | None,
) -> object:
    result_fingerprint = ""
    result_top_ids: list[str] = []
    if isinstance(final_response, Mapping):
        result_fingerprint, result_top_ids = _response_result_fingerprint(
            final_response,
            top_k=result_fingerprint_top_k,
        )

    duplicate_result_diagnostics: dict[str, object] | None = None
    if new_contract_enabled and result_fingerprint:
        duplicate_result_diagnostics = _duplicate_result_diagnostics(
            history=getattr(context, "search_history", None) or [],
            result_fingerprint=result_fingerprint,
            result_top_ids=result_top_ids,
            result_fingerprint_top_k=result_fingerprint_top_k,
        )

    if isinstance(final_response, dict):
        diagnostics = final_response.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        if duplicate_intent_diagnostics:
            diagnostics.update(duplicate_intent_diagnostics)
            try:
                similarity_value = duplicate_intent_diagnostics.get("duplicate_intent_similarity")
                similarity_float = float(similarity_value) if similarity_value is not None else None
            except (TypeError, ValueError):
                similarity_float = None
            final_response["search_repeat_guidance"] = build_repeat_search_guidance(
                context,
                similarity=similarity_float,
            )
        if duplicate_result_diagnostics:
            diagnostics.update(duplicate_result_diagnostics)
        if diagnostics:
            final_response["diagnostics"] = diagnostics

    try:
        history_entry = {
            "intent": intent_text,
            "embedding": intent_embedding,
            "response": copy.deepcopy(final_response),
            "result_fingerprint": result_fingerprint,
            "result_top_ids": list(result_top_ids[:result_fingerprint_top_k]),
        }
        context.search_history.append(history_entry)
        if len(context.search_history) > 25:
            context.search_history = context.search_history[-25:]
    except Exception:
        pass

    return final_response
