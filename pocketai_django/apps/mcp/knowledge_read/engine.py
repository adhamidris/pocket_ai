from __future__ import annotations

import logging
from typing import Mapping

from apps.conversations.models import Conversation
from apps.rag.observability.logging import structured_log

from ..types import CharacterBudgetExceeded, ToolExecutionContext
from .artifacts.fitting import fit_read_response_to_prompt_limit
from .cursor_reads import read_from_cursor
from .document_reads import read_new_document_source
from .evidence import append_read_evidence
from .preflight import prepare_agentic_read_request
from .segments.section_blocks import (
    _upload_title,
)
from .response_building import (
    _payload_len_with_budget,
)
from .runtime_context import KnowledgeReadRuntimeContext
from .table_reading.anchors import _read_exact_table_row
from .table_reading.new_reads import read_new_table_source
from .target_resolution import (
    _is_dataset_upload,
)
from .table_reading.rows import _read_tabular_rows_segment_facts


logger = logging.getLogger(__name__)


def _agentic_read_v2_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Agentic Read V2 engine for refs-first retrieval.

    Internal interface (called by read_knowledge wrapper):
      - items=[{id,cursor?}...], max_chars=...

    Public interface (enforced by read_knowledge):
      - refs=[{id,cursor?}...], max_chars=...

    The tool selects the retrieval strategy internally (page blocks vs table rows vs
    chunk-window fallback) and returns deterministic continuation cursors when the
    requested content doesn't fit.
    """
    preflight = prepare_agentic_read_request(arguments=arguments, context=context)
    if preflight.response is not None:
        return preflight.response

    ordered_items = preflight.ordered_items
    prompt_output_limit = preflight.prompt_output_limit
    max_chars_allowed = preflight.max_chars_allowed
    max_chars = preflight.max_chars
    overflow_margin = preflight.overflow_margin
    overflow_remaining = preflight.overflow_remaining
    mode = preflight.mode

    business = getattr(conversation, "business_profile", None)
    runtime_context = KnowledgeReadRuntimeContext(conversation=conversation, context=context)
    _resolve_text_section_span = runtime_context.resolve_text_section_span
    _cursor_payload_base = runtime_context.cursor_payload_base
    _decode_cursor = runtime_context.decode_cursor
    _resolve_cursor_from_handle = runtime_context.resolve_cursor_from_handle
    _store_cursor_handle = runtime_context.store_cursor_handle
    _read_page_blocks_segment = runtime_context.read_page_blocks_segment
    _read_section_span_segment = runtime_context.read_section_span_segment
    _read_chunk_window_segment = runtime_context.read_chunk_window_segment
    _read_artifact_segment = runtime_context.read_artifact_segment
    _read_tabular_rows_with_anchor = runtime_context.read_tabular_rows_with_anchor
    _resolve_target = runtime_context.resolve_target
    _enforce_access = runtime_context.enforce_access

    contents: list[dict[str, object]] = []
    read: list[dict[str, object]] = []
    deferred: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []

    successfully_read_ids: set[str] = set()
    table_anchor_manifest_lookups = 0
    table_anchor_manifest_hits = 0
    table_anchor_fallback_reads = 0

    remaining_chars = max(0, int(max_chars))
    total_chars = 0

    for idx, entry in enumerate(ordered_items):
        item_id = str(entry.get("id") or "").strip()
        cursor_in = entry.get("cursor")
        row_start: int | None = None
        row_limit: int | None = None
        row_start_raw = entry.get("row_start")
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start = None
        row_limit_raw = entry.get("row_limit")
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit = None
        if row_limit is not None:
            row_limit = max(1, min(200, int(row_limit)))
        cursor_in_resolved = _resolve_cursor_from_handle(cursor_in if isinstance(cursor_in, str) else None)
        effective_remaining_chars = max(0, int(remaining_chars) + int(overflow_remaining))
        if effective_remaining_chars < 200:
            deferred.append(
                {
                    "id": item_id,
                    "reason": "not_enough_remaining_chars",
                    "hint": "Not enough remaining chars in this call; retry with a higher max_chars or fewer items.",
                }
            )
            continue

        # Greedy packing by priority: let higher-ranked refs consume the remaining
        # budget instead of forcing an equal-share slice that can starve the first
        # ref and create avoidable follow-up reads.
        per_item_budget = max(200, effective_remaining_chars)

        cursor_payload: dict[str, object] | None = None
        if isinstance(cursor_in_resolved, str) and cursor_in_resolved.strip():
            cursor_payload, cursor_error = _decode_cursor(item_id, cursor_in_resolved)
            if cursor_error:
                errors.append(cursor_error)
                read.append({"id": item_id, "status": "error"})
                continue

        chunk_record, upload_record, table_record, row_record, resolve_error = _resolve_target(item_id)
        if resolve_error:
            errors.append(resolve_error)
            read.append({"id": item_id, "status": "error"})
            continue

        upload = (
            upload_record
            or getattr(chunk_record, "upload", None)
            or getattr(table_record, "upload", None)
            or getattr(getattr(row_record, "table", None), "upload", None)
        )
        upload_id = str(getattr(upload, "id", "") or "")
        if not upload_id:
            errors.append({"id": item_id, "error_code": "not_found", "hint": "Upload not found."})
            read.append({"id": item_id, "status": "error"})
            continue

        access_error = _enforce_access(upload_id, item_id=item_id)
        if access_error:
            errors.append(access_error)
            read.append({"id": item_id, "status": "error"})
            continue

        # Block dataset/spreadsheet reads through read_knowledge (not supported in agentic KB tools).
        if _is_dataset_upload(upload) and (chunk_record is None or bool((chunk_record.metadata or {}).get("is_table_chunk"))):
            errors.append(
                {
                    "id": item_id,
                    "error_code": "wrong_tool_for_table",
                    "hint": "This item is a structured dataset/spreadsheet table and is not supported by this tool.",
                }
            )
            read.append({"id": item_id, "status": "error"})
            continue

        # Track for follow-up context.
        try:
            context.track_document_read(upload_id, title=_upload_title(upload))
        except Exception:
            pass

        payload: dict[str, object] = {"type": "text", "text": ""}
        payload_type = "text"
        evidence_kind = "text_excerpt"
        title = _upload_title(upload)
        cursor_used = (
            cursor_in_resolved
            if isinstance(cursor_in_resolved, str) and cursor_in_resolved.strip()
            else None
        )
        next_cursor: str | None = None
        complete = True
        evidence_coverage_hint: dict[str, object] | None = None

        # Strategy selection (AUTO):
        if cursor_payload:
            cursor_result = read_from_cursor(
                item_id=item_id,
                upload=upload,
                upload_id=upload_id,
                cursor_payload=cursor_payload,
                row_start=row_start,
                row_limit=row_limit,
                per_item_budget=per_item_budget,
                business=business,
                title=title,
                errors=errors,
                read=read,
                read_section_span_segment=_read_section_span_segment,
                read_page_blocks_segment=_read_page_blocks_segment,
                read_tabular_rows_segment_facts=_read_tabular_rows_segment_facts,
                read_chunk_window_segment=_read_chunk_window_segment,
                read_artifact_segment=_read_artifact_segment,
            )
            if cursor_result is None:
                continue
            payload = cursor_result.payload
            payload_type = cursor_result.payload_type
            evidence_kind = cursor_result.evidence_kind
            title = cursor_result.title
            next_cursor = cursor_result.next_cursor
            complete = cursor_result.complete
        else:
            # New read: decide best source.
            chunk_meta = chunk_record.metadata if chunk_record and isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
            table_result = read_new_table_source(
                item_id=item_id,
                upload_id=upload_id,
                row_record=row_record,
                table_record=table_record,
                chunk_meta=chunk_meta,
                mode=mode,
                row_start=row_start,
                row_limit=row_limit,
                per_item_budget=per_item_budget,
                business=business,
                title=title,
                read_tabular_rows_segment_facts=_read_tabular_rows_segment_facts,
                read_exact_table_row=_read_exact_table_row,
                read_tabular_rows_with_anchor=_read_tabular_rows_with_anchor,
            )
            if table_result is not None:
                payload = table_result.payload
                payload_type = table_result.payload_type
                evidence_kind = table_result.evidence_kind
                title = table_result.title
                next_cursor = table_result.next_cursor
                complete = table_result.complete
                table_anchor_manifest_lookups += table_result.anchor_manifest_lookups
                table_anchor_manifest_hits += table_result.anchor_manifest_hits
                table_anchor_fallback_reads += table_result.anchor_fallback_reads
            else:
                document_result = read_new_document_source(
                    item_id=item_id,
                    upload=upload,
                    upload_id=upload_id,
                    upload_record=upload_record,
                    chunk_record=chunk_record,
                    chunk_meta=chunk_meta,
                    per_item_budget=per_item_budget,
                    business=business,
                    errors=errors,
                    read=read,
                    resolve_text_section_span=_resolve_text_section_span,
                    read_page_blocks_segment=_read_page_blocks_segment,
                    read_section_span_segment=_read_section_span_segment,
                    read_chunk_window_segment=_read_chunk_window_segment,
                )
                if document_result is None:
                    continue
                payload = document_result.payload
                next_cursor = document_result.next_cursor
                complete = document_result.complete

        evidence_result = append_read_evidence(
            context=context,
            contents=contents,
            read=read,
            deferred=deferred,
            item_id=item_id,
            upload_id=upload_id,
            title=title,
            payload_type=payload_type,
            evidence_kind=evidence_kind,
            payload=payload,
            complete=complete,
            cursor_used=cursor_used,
            next_cursor=next_cursor,
            evidence_coverage_hint=evidence_coverage_hint,
            max_chars_allowed=max_chars_allowed,
            remaining_chars=remaining_chars,
            overflow_remaining=overflow_remaining,
            store_cursor_handle=_store_cursor_handle,
        )
        if not evidence_result.content_added:
            continue

        successfully_read_ids.add(item_id)
        remaining_chars = evidence_result.remaining_chars
        overflow_remaining = evidence_result.overflow_remaining
        total_chars += evidence_result.item_chars
        continue

    output_limit = max(0, int(prompt_output_limit))
    response, total_chars_final = fit_read_response_to_prompt_limit(
        conversation=conversation,
        context=context,
        contents=contents,
        read=read,
        deferred=deferred,
        errors=errors,
        max_chars=max_chars,
        max_chars_allowed=max_chars_allowed,
        prompt_output_limit=prompt_output_limit,
        total_chars=total_chars,
        cursor_payload_base=_cursor_payload_base,
        resolve_cursor_from_handle=_resolve_cursor_from_handle,
        store_cursor_handle=_store_cursor_handle,
    )

    try:
        artifact_items = sum(1 for entry in read if isinstance(entry, Mapping) and str(entry.get("status") or "") == "artifact")
        truncated_items = sum(1 for entry in read if isinstance(entry, Mapping) and str(entry.get("status") or "") in {"truncated", "partial"})
        response_chars = _payload_len_with_budget(response)
        structured_log(
            "mcp",
            "read_knowledge.agentic_v2",
            {
                "items": len(ordered_items),
                "contents": len(contents),
                "truncated_items": int(truncated_items),
                "artifact_items": int(artifact_items),
                "deferred": len(deferred),
                "errors": len(errors),
                "total_chars": int(total_chars_final),
                "max_chars": int(max_chars),
                "overflow_margin": int(overflow_margin),
                "overflow_used": int(max(0, int(overflow_margin) - int(overflow_remaining))),
                "output_chars": int(response_chars),
                "output_limit": int(output_limit or 0),
                "table_anchor_manifest_lookups": int(table_anchor_manifest_lookups),
                "table_anchor_manifest_hits": int(table_anchor_manifest_hits),
                "table_anchor_fallback_reads": int(table_anchor_fallback_reads),
            },
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
        )
    except Exception:
        # Logging must never break the tool boundary.
        pass

    try:
        context.reserve_characters(int(total_chars_final))
    except CharacterBudgetExceeded as exc:
        return {
            "tool": "read_knowledge",
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "evidence": [],
            "throttle_notice": {"type": "prompt_budget", "message": str(exc)},
            "hint": "Prompt budget exceeded. Ask a narrower question or request fewer items.",
        }

    # Record only refs that actually returned content for repeat-read detection.
    # Deferred/errored refs must remain retryable within the same turn.
    for ref_id in successfully_read_ids:
        context.read_ref_ids_this_turn.add(ref_id)

    return response
