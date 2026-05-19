"""
Read throttling and diagnostic warning helpers for MCP knowledge tools.
"""

from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from django.conf import settings

from apps.rag.knowledge_payloads import diagnostic_warning_payload, issue_warning_payloads
from apps.rag.knowledge_search import KnowledgeSearchService

from ..types import ToolExecutionContext


def _maybe_throttle_full_page(
    context: ToolExecutionContext,
    business_profile,
    service: KnowledgeSearchService,
) -> dict[str, object] | None:
    """
    Determine if a full-page request should be downgraded to an excerpt.
    """

    limit = context.char_budget_per_turn
    if limit is not None and limit > 0:
        remaining = max(0, limit - context.characters_used)
        inline_cap = service.inline_char_limit_for_business(business_profile)
        threshold = max(1200, int(inline_cap * 0.75))
        if remaining < threshold:
            return {
                "reason": "char_budget_low",
                "remaining_characters": remaining,
                "threshold": threshold,
            }
    page_limit = context.max_chunk_pages_per_turn
    if page_limit is not None and page_limit > 0:
        throttle_floor = max(1, int(page_limit * 0.5))
        if context.chunk_pages_used > throttle_floor:
            return {
                "reason": "page_window_throttle",
                "used_pages": context.chunk_pages_used,
                "page_limit": page_limit,
            }
    return None


def _extract_no_result_reason(diagnostics: Mapping[str, object] | None) -> str:
    valid_reasons = {"not_found", "not_applicable_to_segment", "insufficient_evidence"}
    diag = diagnostics or {}
    reason = str(diag.get("no_result_reason") or "").strip().lower()
    if reason in valid_reasons:
        return reason
    contract = diag.get("auto_decision_contract")
    if isinstance(contract, Mapping):
        contract_reason = str(contract.get("no_result_reason") or "").strip().lower()
        if contract_reason in valid_reasons:
            return contract_reason
    return ""


def _extract_conflict_detected(diagnostics: Mapping[str, object] | None) -> bool:
    diag = diagnostics or {}
    if bool(diag.get("conflict_detected")):
        return True
    contract = diag.get("auto_decision_contract")
    if isinstance(contract, Mapping):
        return bool(contract.get("conflict_detected"))
    return False


def _is_uuid_ref_id(value: object) -> bool:
    try:
        uuid.UUID(str(value or "").strip())
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _scope_invalid_read_retry_limit() -> int:
    try:
        configured = int(getattr(settings, "MCP_INVALID_READ_REF_RETRY_LIMIT", 2) or 2)
    except (TypeError, ValueError):
        configured = 2
    return max(1, min(5, configured))


def _build_ingestion_warnings(
    snippet_payloads: Sequence[Mapping[str, object]],
    knowledge_reads: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """
    Derive ingestion warnings from snippet diagnostics, mirroring the legacy path.
    """

    if not knowledge_reads:
        return []
    relevant_ids: set[str] = set()
    for read in knowledge_reads:
        identifier = read.get("id")
        if identifier:
            relevant_ids.add(str(identifier))
    if not relevant_ids:
        return []

    warnings: list[dict[str, object]] = []
    for entry in snippet_payloads:
        entry_id = str(entry.get("id") or "")
        if entry_id not in relevant_ids:
            continue
        label = entry.get("public_label") or entry.get("title") or "Knowledge source"
        upload_id = str(entry.get("upload_id") or entry_id)
        # Issue-derived warnings
        warnings.extend(
            issue_warning_payloads(
                entry.get("issues"),
                label=label,  # type: ignore[arg-type]
                upload_id=upload_id,
            )
        )
        diagnostics = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
        partial_index = bool(entry.get("partial_index"))
        truncation_note_val = entry.get("truncation_note")
        truncation_note = truncation_note_val if isinstance(truncation_note_val, str) else None
        diag_warning = diagnostic_warning_payload(
            label=label,  # type: ignore[arg-type]
            upload_id=upload_id,
            diagnostics=diagnostics,
            partial_index=partial_index,
            truncation_note=truncation_note,
        )
        if diag_warning:
            warnings.append(diag_warning)  # type: ignore[arg-type]
    return warnings
