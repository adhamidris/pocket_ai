from __future__ import annotations

import copy
import csv
import gzip
import json
import logging
import math
import re
import time
import uuid
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from django.conf import settings
from django.core.cache import cache
from django.db.models.functions import Length

from apps.conversations.models import Conversation, ConversationFile, ConversationFileChunk
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
)
from apps.rag.contracts import KNOWLEDGE_READ_STATE_FULL, KnowledgeSnippet
from apps.rag.knowledge_search import KnowledgeSearchService
from apps.rag.observability.logging import structured_log

from ..runtime.agentic_read_cursor import (
    _AGENTIC_READ_CURSOR_V2_TTL_SECONDS,
    _sign_agentic_read_cursor_v2,
    _verify_agentic_read_cursor_v2,
)
from ..knowledge_support.read_guards import _build_ingestion_warnings
from ..knowledge_support.result_helpers import (
    _apply_seen_row_filter,
    _budget_allows_full_page,
    _detect_full_page_intent,
    _mark_snippets_as_seen,
)
from ..tool_definitions import (
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
)
from ..runtime.tool_runtime_helpers import _read_cache_key, _record_knowledge_audit_event_once
from ..types import CharacterBudgetExceeded, ToolExecutionContext
from .anchor_manifest import _load_table_anchor_manifest as _load_table_anchor_manifest_for_context
from .artifact_output import _attach_artifact_for_item as _attach_artifact_for_item_impl
from .artifact_segments import _read_artifact_segment as _read_artifact_segment_impl
from .chunk_windows import _read_chunk_window_segment as _read_chunk_window_segment_impl
from .cursors import (
    _cursor_payload_base as _cursor_payload_base_for_conversation,
    _decode_cursor as _decode_cursor_for_conversation,
    _resolve_cursor_from_handle as _resolve_cursor_from_context,
    _store_cursor_handle as _store_cursor_handle_for_context,
)
from .section_blocks import (
    _load_ordered_page_blocks as _load_ordered_page_blocks_with_cache,
    _resolve_text_section_span as _resolve_text_section_span_with_cache,
    _upload_title,
)
from .response_building import (
    _build_response as _build_response_impl,
    _payload_len_with_budget,
)
from .table_anchors import (
    _read_exact_table_row,
    _read_tabular_rows_with_anchor as _read_tabular_rows_with_anchor_impl,
)
from .target_resolution import (
    _enforce_access as _enforce_access_impl,
    _is_dataset_upload,
    _resolve_target as _resolve_target_impl,
)
from .text_segments import (
    _read_page_blocks_segment as _read_page_blocks_segment_impl,
    _read_section_span_segment as _read_section_span_segment_impl,
)
from .table_rows import _read_tabular_rows_segment_facts


logger = logging.getLogger(__name__)


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _knowledge_service() -> KnowledgeSearchService:
    return KnowledgeSearchService()

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
    raw_items = arguments.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_items",
            "error_code": "missing_items",
            "evidence": [],
            "hint": "refs[] is required (use ids/cursors from search_knowledge/read_knowledge).",
        }

    # Per-call output cap: stay under MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS minus margin.
    try:
        raw_prompt_output_limit = getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", 12000)
        prompt_output_limit = int(raw_prompt_output_limit if raw_prompt_output_limit is not None else 12000)
    except (TypeError, ValueError):
        prompt_output_limit = 12000
    try:
        raw_safety_margin = (
            getattr(settings, "MCP_READ_KNOWLEDGE_MAX_CHARS_MARGIN", None)
            or getattr(settings, "MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", 800)
        )
        safety_margin = int(raw_safety_margin if raw_safety_margin is not None else 800)
    except (TypeError, ValueError):
        safety_margin = 800
    safe_prompt_limit = max(200, prompt_output_limit - max(0, safety_margin))

    remaining_turn_budget: int | None = None
    if context.char_budget_per_turn is not None:
        remaining_turn_budget = max(0, int(context.char_budget_per_turn) - int(context.characters_used or 0))

    max_chars_allowed = max(200, min(20000, safe_prompt_limit))
    if remaining_turn_budget is not None:
        max_chars_allowed = max(0, min(max_chars_allowed, remaining_turn_budget))
    if max_chars_allowed < 200:
        return {
            "tool": "read_knowledge",
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "evidence": [],
            "hint": "Prompt budget exceeded for this turn. Answer from collected evidence or ask a narrower question.",
        }

    raw_max_chars = arguments.get("max_chars")
    try:
        requested_max_chars = int(raw_max_chars)
    except (TypeError, ValueError):
        requested_max_chars = None
    if requested_max_chars is None:
        max_chars = max_chars_allowed
    else:
        max_chars = max(200, requested_max_chars)
        if max_chars > max_chars_allowed:
            return {
                "tool": "read_knowledge",
                "status": "constraint_error",
                "error": "max_chars_exceeded",
                "error_code": "max_chars_exceeded",
                "evidence": [],
                "requested_max_chars": max_chars,
                "max_chars_allowed": max_chars_allowed,
                "hint": (
                    f"max_chars is too high for this chat (requested {max_chars}, max {max_chars_allowed}). "
                    f"Retry with max_chars <= {max_chars_allowed}, or read fewer items."
                ),
            }

    try:
        raw_overflow_margin = int(getattr(settings, "MCP_READ_KNOWLEDGE_OVERFLOW_MARGIN", 200) or 0)
    except (TypeError, ValueError):
        raw_overflow_margin = 200
    overflow_margin = max(0, min(int(raw_overflow_margin), max(0, int(max_chars_allowed) - int(max_chars))))
    overflow_remaining = int(overflow_margin)

    # The backend chooses the correct representation and paging strategy.
    # `mode` is intentionally not part of the public agentic contract.
    mode = "auto"

    # Dedup items by (id,cursor) while preserving order.
    ordered_items: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str | None, int | None, int | None]] = set()
    for entry in raw_items:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or "").strip()
        if not item_id:
            continue
        cursor = entry.get("cursor")
        cursor_str = str(cursor).strip() if isinstance(cursor, str) and cursor.strip() else None
        row_start_raw = entry.get("row_start")
        row_limit_raw = entry.get("row_limit")
        row_start_value: int | None = None
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start_value = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start_value = None
        row_limit_value: int | None = None
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit_value = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit_value = None
        if row_limit_value is not None:
            row_limit_value = max(1, min(200, int(row_limit_value)))

        key = (item_id, cursor_str, row_start_value, row_limit_value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out_item: dict[str, object] = {"id": item_id, "cursor": cursor_str}
        if row_start_value is not None:
            out_item["row_start"] = row_start_value
        if row_limit_value is not None:
            out_item["row_limit"] = row_limit_value
        ordered_items.append(out_item)

    # ── Repeat-read detection (loop prevention) ──────────────────────────
    # Allow cursor-continuation reads (same ID + cursor = new page of content).
    # Block non-cursor re-reads of the same ref within a single turn.
    already_read_ids: set[str] = getattr(context, "read_ref_ids_this_turn", None) or set()
    filtered_items: list[dict[str, object]] = []
    blocked_items: list[dict[str, object]] = []
    for item in ordered_items:
        item_id = str(item["id"])
        has_cursor = bool(item.get("cursor"))
        has_row_start = item.get("row_start") is not None
        if item_id in already_read_ids and not has_cursor and not has_row_start:
            blocked_items.append({"id": item_id, "error": "Already read this turn. Answer from available evidence."})
        else:
            filtered_items.append(item)

    if not filtered_items:
        # ALL refs were already read this turn (non-cursor)
        return {
            "tool": "read_knowledge",
            "status": "already_read",
            "error_code": "already_read",
            "evidence": [],
            "blocked": blocked_items,
            "hint": "All requested refs were already read this turn. Answer from the evidence you have.",
        }

    if blocked_items:
        # Some refs blocked, continue with the rest
        ordered_items = filtered_items
    # ── End repeat-read detection ────────────────────────────────────────

    if not ordered_items:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_items",
            "error_code": "missing_items",
            "evidence": [],
            "hint": "items[] must contain at least one {id} from search_knowledge results.",
        }

    # NOTE: Some unit tests pass a lightweight conversation stub (SimpleNamespace)
    # instead of a real BusinessProfile instance. Avoid blowing up inside ORM
    # lookups by normalizing to a UUID early and returning a structured error
    # when missing/invalid.
    business_uuid: uuid.UUID | None = None
    raw_business_id = getattr(conversation, "business_profile_id", None)
    try:
        if raw_business_id is not None:
            business_uuid = uuid.UUID(str(raw_business_id))
    except (TypeError, ValueError, AttributeError):
        business_uuid = None
    business = getattr(conversation, "business_profile", None)
    upload_block_cache: dict[str, list[dict[str, object]]] = {}

    def _load_ordered_page_blocks(upload_id: str) -> list[dict[str, object]]:
        return _load_ordered_page_blocks_with_cache(upload_id, upload_block_cache=upload_block_cache)

    def _resolve_text_section_span(
        *,
        upload_id: str,
        chunk_record: KnowledgeUploadChunk | None,
        chunk_meta: Mapping[str, object],
        fallback_page_number: int,
    ) -> dict[str, int] | None:
        return _resolve_text_section_span_with_cache(
            upload_id=upload_id,
            upload_block_cache=upload_block_cache,
            chunk_record=chunk_record,
            chunk_meta=chunk_meta,
            fallback_page_number=fallback_page_number,
        )

    def _cursor_payload_base(*, item_id: str, kind: str) -> dict[str, object]:
        return _cursor_payload_base_for_conversation(conversation=conversation, item_id=item_id, kind=kind)

    def _decode_cursor(item_id: str, cursor: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        return _decode_cursor_for_conversation(conversation, item_id, cursor)

    def _resolve_cursor_from_handle(cursor_token: str | None) -> str | None:
        return _resolve_cursor_from_context(context, cursor_token)

    def _store_cursor_handle(cursor_signed: str | None) -> str | None:
        return _store_cursor_handle_for_context(context, cursor_signed)

    def _load_table_anchor_manifest(*, ref_id: str, table_id: str | None = None) -> dict[str, object] | None:
        return _load_table_anchor_manifest_for_context(context, ref_id=ref_id, table_id=table_id)

    def _read_page_blocks_segment(
        *,
        item_id: str,
        upload: KnowledgeUpload,
        upload_id: str,
        page_number: int,
        start_order: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
    ) -> tuple[str, dict[str, object] | None, bool]:
        return _read_page_blocks_segment_impl(
            item_id=item_id,
            cursor_payload_base=_cursor_payload_base,
            upload_id=upload_id,
            page_number=page_number,
            start_order=start_order,
            start_offset=start_offset,
            budget_chars=budget_chars,
            prepend_sep=prepend_sep,
        )

    def _read_section_span_segment(
        *,
        item_id: str,
        upload_id: str,
        start_page_number: int,
        start_block_order: int,
        end_page_number: int,
        end_block_order: int,
        current_page_number: int,
        current_block_order: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
    ) -> tuple[str, dict[str, object] | None, bool]:
        return _read_section_span_segment_impl(
            load_ordered_page_blocks=_load_ordered_page_blocks,
            cursor_payload_base=_cursor_payload_base,
            item_id=item_id,
            upload_id=upload_id,
            start_page_number=start_page_number,
            start_block_order=start_block_order,
            end_page_number=end_page_number,
            end_block_order=end_block_order,
            current_page_number=current_page_number,
            current_block_order=current_block_order,
            start_offset=start_offset,
            budget_chars=budget_chars,
            prepend_sep=prepend_sep,
        )

    def _read_chunk_window_segment(
        *,
        item_id: str,
        upload_id: str,
        start_index: int,
        end_index: int,
        current_index: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
        business_profile,
    ) -> tuple[str, dict[str, object] | None, bool]:
        return _read_chunk_window_segment_impl(
            cursor_payload_base=_cursor_payload_base,
            item_id=item_id,
            upload_id=upload_id,
            start_index=start_index,
            end_index=end_index,
            current_index=current_index,
            start_offset=start_offset,
            budget_chars=budget_chars,
            prepend_sep=prepend_sep,
            business_profile=business_profile,
        )

    def _read_artifact_segment(
        *,
        item_id: str,
        artifact_id: str,
        start_offset: int,
        budget_chars: int,
    ) -> tuple[str, dict[str, object] | None, bool, dict[str, object] | None]:
        return _read_artifact_segment_impl(
            conversation=conversation,
            cursor_payload_base=_cursor_payload_base,
            item_id=item_id,
            artifact_id=artifact_id,
            start_offset=start_offset,
            budget_chars=budget_chars,
        )

    def _read_tabular_rows_with_anchor(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        start_row_index: int,
        budget_chars: int,
        max_rows: int | None,
        business_profile,
        use_anchor: bool,
    ) -> tuple[dict[str, object], dict[str, object] | None, bool, bool, bool]:
        return _read_tabular_rows_with_anchor_impl(
            load_table_anchor_manifest=_load_table_anchor_manifest,
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=start_row_index,
            budget_chars=budget_chars,
            max_rows=max_rows,
            business_profile=business_profile,
            use_anchor=use_anchor,
        )

    def _resolve_target(item_id: str) -> tuple[
        KnowledgeUploadChunk | None,
        KnowledgeUpload | None,
        KnowledgeUploadTable | None,
        KnowledgeUploadTableRow | None,
        dict[str, object] | None,
    ]:
        return _resolve_target_impl(item_id, conversation=conversation)

    def _enforce_access(upload_id: str, *, item_id: str) -> dict[str, object] | None:
        return _enforce_access_impl(
            upload_id,
            item_id=item_id,
            conversation=conversation,
            context=context,
        )

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
            kind = str(cursor_payload.get("kind") or "")
            prepend_sep = bool(cursor_payload.get("prepend_sep"))
            if kind == "section_span":
                start_page_number = int(cursor_payload.get("start_page_number") or 1)
                start_block_order = int(cursor_payload.get("start_block_order") or 0)
                end_page_number = int(cursor_payload.get("end_page_number") or start_page_number)
                end_block_order = int(cursor_payload.get("end_block_order") or start_block_order)
                current_page_number = int(cursor_payload.get("current_page_number") or start_page_number)
                current_block_order = int(cursor_payload.get("current_block_order") or start_block_order)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_section_span_segment(
                    item_id=item_id,
                    upload_id=upload_id,
                    start_page_number=start_page_number,
                    start_block_order=start_block_order,
                    end_page_number=end_page_number,
                    end_block_order=end_block_order,
                    current_page_number=current_page_number,
                    current_block_order=current_block_order,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "page_blocks":
                page_number = int(cursor_payload.get("page_number") or 1)
                block_order = int(cursor_payload.get("block_order") or 0)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_page_blocks_segment(
                    item_id=item_id,
                    upload=upload,  # type: ignore[arg-type]
                    upload_id=upload_id,
                    page_number=page_number,
                    start_order=block_order,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "table_rows":
                payload_type = "table"
                evidence_kind = "table_rows"
                table_id = str(cursor_payload.get("table_id") or "").strip()
                raw_row_index = cursor_payload.get("row_index")
                try:
                    start_row_index = int(raw_row_index) if raw_row_index is not None else 0
                except (TypeError, ValueError):
                    start_row_index = 0
                effective_start_row = row_start if row_start is not None else start_row_index
                table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
                    item_id=item_id,
                    upload_id=upload_id,
                    table_id=table_id,
                    start_row_index=effective_start_row,
                    budget_chars=per_item_budget,
                    max_rows=row_limit,
                    business_profile=business,
                    selection_mode="row_range",
                )
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif kind == "chunk_window":
                start_index = int(cursor_payload.get("chunk_start") or 0)
                end_index = int(cursor_payload.get("chunk_end") or start_index)
                chunk_index = int(cursor_payload.get("chunk_index") or start_index)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_chunk_window_segment(
                    item_id=item_id,
                    upload_id=upload_id,
                    start_index=start_index,
                    end_index=end_index,
                    current_index=chunk_index,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                    business_profile=business,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "artifact":
                artifact_id = str(cursor_payload.get("artifact_id") or "").strip()
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete, artifact_meta = _read_artifact_segment(
                    item_id=item_id,
                    artifact_id=artifact_id,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                )
                if artifact_meta and artifact_meta.get("error_code"):
                    errors.append(dict(artifact_meta))
                    read.append({"id": item_id, "status": "error"})
                    continue
                if artifact_meta:
                    title_override = artifact_meta.get("title")
                    if isinstance(title_override, str) and title_override.strip():
                        title = title_override.strip()
                    type_override = artifact_meta.get("type")
                    if isinstance(type_override, str) and type_override.strip():
                        payload_type = type_override.strip()
                        evidence_kind = "table_rows" if payload_type == "table" else "text_excerpt"
                if payload_type == "table":
                    # Artifact segments currently stream text; tables should not route here.
                    payload = {"type": "table", "columns": [], "rows": []}
                else:
                    payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            else:
                errors.append({"id": item_id, "error_code": "invalid_cursor", "hint": "Unknown cursor kind."})
                read.append({"id": item_id, "status": "error"})
                continue
        else:
            # New read: decide best source.
            chunk_meta = chunk_record.metadata if chunk_record and isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
            is_table_chunk = bool(chunk_meta.get("is_table_chunk") or chunk_meta.get("table_chunk_role"))
            table_role = str(chunk_meta.get("table_chunk_role") or "").strip().lower()
            table_id = str(chunk_meta.get("table_id") or "").strip()

            if row_record is not None:
                payload_type = "table"
                evidence_kind = "table_rows"
                table_obj = getattr(row_record, "table", None)
                table_id = str(getattr(table_obj, "id", "") or "").strip()
                row_index_raw = getattr(row_record, "row_index", 0)
                try:
                    row_index = int(row_index_raw) if row_index_raw is not None else 0
                except (TypeError, ValueError):
                    row_index = 0

                table_title = str(getattr(table_obj, "title", "") or "").strip() or str(
                    getattr(table_obj, "section_heading", "") or ""
                ).strip()
                if not table_title:
                    order_index = getattr(table_obj, "order_index", None)
                    table_title = f"Table {order_index}" if order_index else "Table"
                title = table_title

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                    table_payload["upgraded_from_ref"] = "table_row"
                    payload = table_payload
                else:
                    table_payload, complete = _read_exact_table_row(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        db_row_index=row_index,
                        budget_chars=per_item_budget,
                        business_profile=business,
                    )
                    payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif table_record is not None:
                payload_type = "table"
                evidence_kind = "table_rows"
                table_id = str(getattr(table_record, "id", "") or "").strip()
                table_title = str(getattr(table_record, "title", "") or "").strip() or str(getattr(table_record, "section_heading", "") or "").strip()
                if not table_title:
                    order_index = getattr(table_record, "order_index", None)
                    table_title = f"Table {order_index}" if order_index else "Table"
                title = table_title

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                else:
                    table_anchor_manifest_lookups += 1
                    table_payload, _cursor_out, complete, anchor_used, fallback_used = _read_tabular_rows_with_anchor(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=0,
                        budget_chars=per_item_budget,
                        max_rows=None,
                        business_profile=business,
                        use_anchor=True,
                    )
                    if anchor_used:
                        table_anchor_manifest_hits += 1
                    if fallback_used:
                        table_anchor_fallback_reads += 1
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif upload_record is not None and chunk_record is None:
                page_number = 1
                has_page_blocks = False
                try:
                    from apps.knowledge.models import KnowledgeUploadPageBlock
                    has_page_blocks = KnowledgeUploadPageBlock.objects.filter(
                        upload_id=upload_id,
                        page__page_number=page_number,
                    ).exclude(text="").exists()
                except Exception:
                    has_page_blocks = False

                if has_page_blocks:
                    content_text, cursor_out, complete = _read_page_blocks_segment(
                        item_id=item_id,
                        upload=upload,  # type: ignore[arg-type]
                        upload_id=upload_id,
                        page_number=page_number,
                        start_order=0,
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                else:
                    errors.append(
                        {
                            "id": item_id,
                            "error_code": "not_found",
                            "hint": "No readable content found for that id.",
                        }
                    )
                    read.append({"id": item_id, "status": "error"})
                    continue
            elif mode != "excerpt" and is_table_chunk and table_id and (mode == "table_rows" or table_role not in {"row"}):
                payload_type = "table"
                evidence_kind = "table_rows"
                # Resolve table title for friendlier output.
                try:
                    table_obj = (
                        KnowledgeUploadTable.objects.filter(id=table_id)
                        .only("id", "title", "section_heading", "order_index")
                        .first()
                    )
                    if table_obj:
                        table_title = (table_obj.title or table_obj.section_heading or "").strip()
                        if not table_title:
                            table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                        title = table_title
                except Exception:
                    pass

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                else:
                    table_anchor_manifest_lookups += 1
                    table_payload, _cursor_out, complete, anchor_used, fallback_used = _read_tabular_rows_with_anchor(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=0,
                        budget_chars=per_item_budget,
                        max_rows=None,
                        business_profile=business,
                        use_anchor=True,
                    )
                    if anchor_used:
                        table_anchor_manifest_hits += 1
                    if fallback_used:
                        table_anchor_fallback_reads += 1
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif mode != "excerpt" and is_table_chunk and table_id and table_role == "row":
                payload_type = "table"
                evidence_kind = "table_rows"
                # Resolve table title for friendlier output (row-chunk reads should still
                # look like "Table X", not just the upload title).
                try:
                    table_obj = (
                        KnowledgeUploadTable.objects.filter(id=table_id)
                        .only("id", "title", "section_heading", "order_index")
                        .first()
                    )
                    if table_obj:
                        table_title = (table_obj.title or table_obj.section_heading or "").strip()
                        if not table_title:
                            table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                        title = table_title
                except Exception:
                    pass

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                    table_payload["upgraded_from_ref"] = "table_row"
                    payload = table_payload
                else:
                    row_index_raw = chunk_meta.get("table_row_index")
                    try:
                        row_index_value = int(row_index_raw) if row_index_raw is not None else 0
                    except (TypeError, ValueError):
                        row_index_value = 0

                    table_payload, complete = _read_exact_table_row(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        db_row_index=row_index_value,
                        budget_chars=per_item_budget,
                        business_profile=business,
                    )
                    payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            else:
                # Prefer page blocks when a page can be resolved; fall back to a chunk window.
                page_number = 1
                if chunk_record:
                    meta_page = chunk_meta.get("table_page_number") or chunk_meta.get("chunk_page") or chunk_meta.get("page_number")
                    if meta_page:
                        try:
                            parsed_page = int(meta_page)
                            if parsed_page >= 1:
                                page_number = parsed_page
                        except (TypeError, ValueError):
                            pass

                section_span = _resolve_text_section_span(
                    upload_id=upload_id,
                    chunk_record=chunk_record,
                    chunk_meta=chunk_meta,
                    fallback_page_number=page_number,
                )
                if section_span is not None:
                    content_text, cursor_out, complete = _read_section_span_segment(
                        item_id=item_id,
                        upload_id=upload_id,
                        start_page_number=int(section_span["start_page_number"]),
                        start_block_order=int(section_span["start_block_order"]),
                        end_page_number=int(section_span["end_page_number"]),
                        end_block_order=int(section_span["end_block_order"]),
                        current_page_number=int(section_span["start_page_number"]),
                        current_block_order=int(section_span["start_block_order"]),
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                else:
                    has_page_blocks = False
                    try:
                        from apps.knowledge.models import KnowledgeUploadPageBlock

                        has_page_blocks = KnowledgeUploadPageBlock.objects.filter(
                            upload_id=upload_id,
                            page__page_number=page_number,
                        ).exclude(text="").exists()
                    except Exception:
                        has_page_blocks = False

                # If page blocks exist for the resolved page, use them.
                if section_span is None and has_page_blocks:
                    content_text, cursor_out, complete = _read_page_blocks_segment(
                        item_id=item_id,
                        upload=upload,  # type: ignore[arg-type]
                        upload_id=upload_id,
                        page_number=page_number,
                        start_order=0,
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                elif section_span is None and chunk_record and chunk_record.chunk_index is not None:
                    neighbor = 1
                    start_index = max(0, int(chunk_record.chunk_index) - neighbor)
                    end_index = int(chunk_record.chunk_index) + neighbor
                    content_text, cursor_out, complete = _read_chunk_window_segment(
                        item_id=item_id,
                        upload_id=upload_id,
                        start_index=start_index,
                        end_index=end_index,
                        current_index=start_index,
                        start_offset=0,
                        budget_chars=per_item_budget,
                        business_profile=business,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                elif section_span is None:
                    errors.append({"id": item_id, "error_code": "not_found", "hint": "No readable content found for that id."})
                    read.append({"id": item_id, "status": "error"})
                    continue

        # If we couldn't read anything for this item, mark as deferred rather than returning empty content.
        has_payload = False
        if payload_type == "table":
            rows_value = payload.get("rows")
            has_payload = isinstance(rows_value, list) and bool(rows_value)
        else:
            text_value = payload.get("text")
            has_payload = isinstance(text_value, str) and bool(text_value)
        if not has_payload:
            if not complete:
                # Budget was too small to fit even one row/block — data exists but didn't fit.
                deferred.append(
                    {
                        "id": item_id,
                        "reason": "budget_too_small",
                        "hint": "Budget too small to fit content. Retry with a higher max_chars (at least the suggested_max_chars from search).",
                    }
                )
            else:
                deferred.append(
                    {
                        "id": item_id,
                        "reason": "empty",
                        "hint": "No readable content was available for this item.",
                    }
                )
            read.append({"id": item_id, "status": "deferred"})
            continue

        successfully_read_ids.add(item_id)

        try:
            if payload_type == "table":
                item_chars = len(json.dumps(payload, ensure_ascii=False, default=str))
            else:
                item_chars = len(str(payload.get("text") or ""))
        except Exception:
            item_chars = 0

        overflow_used = max(0, item_chars - max(0, int(remaining_chars)))
        if overflow_used:
            overflow_remaining = max(0, int(overflow_remaining) - int(overflow_used))
        remaining_chars = max(0, remaining_chars - item_chars)
        total_chars += item_chars

        table_more_rows_available = bool(
            payload_type == "table"
            and isinstance(payload.get("next_row_start"), (int, float))
            and not bool(next_cursor)
        )

        evidence_entry: dict[str, object] = {
            "id": item_id,
            "document_id": upload_id,
            "title": title,
            "type": payload_type,
            "kind": evidence_kind,
            "payload": payload,
            "chars": item_chars,
            "complete": bool(complete and not next_cursor),
            # Back-compat: orchestrator coverage ledger expects "truncated" on items.
            # For paged table reads, "more rows available" is not the same as a risky
            # truncation; keep completion false but avoid overstating truncation.
            "truncated": bool((not complete or bool(next_cursor)) and not table_more_rows_available),
        }
        if table_more_rows_available:
            evidence_entry["more_rows_available"] = True
        if isinstance(evidence_coverage_hint, Mapping) and evidence_coverage_hint:
            evidence_entry["coverage_hint"] = dict(evidence_coverage_hint)
        if cursor_used:
            evidence_entry["cursor_used"] = cursor_used
        next_cursor_handle = _store_cursor_handle(next_cursor) if next_cursor else None
        if next_cursor_handle:
            evidence_entry["next_cursor"] = next_cursor_handle
        contents.append(evidence_entry)
        context.add_read_evidence(evidence_entry)

        is_truncated = not complete or bool(next_cursor_handle)
        read_entry: dict[str, object] = {
            "id": item_id,
            "status": "full" if not is_truncated else ("more_available" if table_more_rows_available else "truncated"),
            "chars": item_chars,
        }
        if is_truncated:
            # Build informative hint so the LLM can decide whether to
            # follow up.  Include row labels already fetched so it can
            # judge if the answer is already complete.
            hint_parts: list[str] = []
            if payload_type == "table":
                next_row_start = payload.get("next_row_start")
                if isinstance(next_row_start, (int, float)):
                    if table_more_rows_available:
                        hint_parts.append(f"More rows available with row_start={int(next_row_start)} if needed.")
                    else:
                        hint_parts.append(f"Continue with row_start={int(next_row_start)}.")
                hint_parts.append(f"Max chars allowed: {int(max_chars_allowed)}.")
            else:
                hint_parts.append(
                    f"Only use next_cursor if you still need data not yet returned. "
                    f"Max chars allowed: {int(max_chars_allowed)}."
                )
            read_entry["hint"] = " ".join(hint_parts)
        read.append(read_entry)
        continue

    # ---------------------------------------------------------------------
    # Phase 4: Artifact fallback when the *final* tool payload would exceed the
    # prompt tool-output cap. This prevents orchestrator-side truncation and
    # enables deterministic paging via `kind=artifact` cursors.
    # ---------------------------------------------------------------------

    try:
        artifact_retention_days = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_RETENTION_DAYS", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS", 30)
            or 30
        )
    except (TypeError, ValueError):
        artifact_retention_days = 30
    artifact_retention_days = max(1, min(365, artifact_retention_days))
    try:
        artifact_max_per_conversation = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_MAX_PER_CONVERSATION", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION", 200)
            or 200
        )
    except (TypeError, ValueError):
        artifact_max_per_conversation = 200
    artifact_max_per_conversation = max(0, min(5000, artifact_max_per_conversation))

    output_limit = max(0, int(prompt_output_limit))

    PROMPT_VIEW_INLINE_MIN_CHARS = 200
    PROMPT_VIEW_INLINE_MAX_CHARS = 1600
    if output_limit:
        # Reserve room for JSON framing + cursors + budget metadata when the prompt cap is small.
        PROMPT_VIEW_INLINE_MAX_CHARS = min(PROMPT_VIEW_INLINE_MAX_CHARS, max(PROMPT_VIEW_INLINE_MIN_CHARS, output_limit // 2))

    def _build_response(*, total_chars_value: int, hint: str | None = None) -> dict[str, object]:
        return _build_response_impl(
            contents=contents,
            read=read,
            deferred=deferred,
            errors=errors,
            max_chars=max_chars,
            max_chars_allowed=max_chars_allowed,
            context=context,
            total_chars_value=total_chars_value,
            hint=hint,
        )

    def _attach_artifact_for_item(item: dict[str, object], trace: dict[str, object]) -> bool:
        return _attach_artifact_for_item_impl(
            item,
            trace,
            conversation=conversation,
            cursor_payload_base=_cursor_payload_base,
            resolve_cursor_from_handle=_resolve_cursor_from_handle,
            store_cursor_handle=_store_cursor_handle,
            prompt_view_inline_min_chars=PROMPT_VIEW_INLINE_MIN_CHARS,
            prompt_view_inline_max_chars=PROMPT_VIEW_INLINE_MAX_CHARS,
            artifact_retention_days=artifact_retention_days,
            artifact_max_per_conversation=artifact_max_per_conversation,
        )

    # Start with the raw v2 output; if it exceeds the prompt cap once budgets are added,
    # convert the largest content entries to artifacts until it fits.
    total_chars_final = int(total_chars)
    response_hint: str | None = None
    response_candidate = _build_response(total_chars_value=total_chars_final)
    if output_limit and _payload_len_with_budget(response_candidate) > output_limit:
        read_by_id: dict[str, dict[str, object]] = {}
        for entry in read:
            if isinstance(entry, Mapping) and entry.get("id"):
                read_by_id[str(entry.get("id"))] = entry  # type: ignore[assignment]

        # Convert biggest items first (best shrink per artifact).
        contents_sorted = sorted(
            (item for item in contents if isinstance(item, Mapping)),
            key=lambda item: int(item.get("chars") or 0),
            reverse=True,
        )
        converted_any = False
        for item in contents_sorted:
            item_id = str(item.get("id") or "").strip()
            trace = read_by_id.get(item_id)
            if not trace or not isinstance(item, dict):
                continue
            if not _attach_artifact_for_item(item, trace):
                continue
            converted_any = True
            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = _build_response(total_chars_value=total_chars_final)
            if _payload_len_with_budget(response_candidate) <= output_limit:
                break

        if converted_any:
            response_hint = (
                "Some content was stored as an artifact to fit prompt limits. "
                "Use next_cursor to continue reading until complete."
            )

    response_candidate = _build_response(total_chars_value=total_chars_final, hint=response_hint)
    if output_limit and response_hint and _payload_len_with_budget(response_candidate) > output_limit:
        # Hints are nice-to-have; when the prompt cap is very small, prefer staying under
        # the hard tool-output limit over including extra explanatory text.
        response_hint = None
        response_candidate = _build_response(total_chars_value=total_chars_final)

    # If we're still above the prompt cap (e.g., cursor/budget overhead), clip artifact previews
    # until the JSON payload fits. This doesn't lose evidence because the full text is stored
    # in the artifact and `next_cursor` continues deterministically.
    if output_limit:
        guard_loops = 0
        payload_chars = _payload_len_with_budget(response_candidate)
        while payload_chars > output_limit and guard_loops < 20:
            guard_loops += 1
            artifact_streams: list[tuple[dict[str, object], str, int]] = []
            for item in contents:
                if not isinstance(item, dict):
                    continue
                payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                if str(payload_obj.get("type") or "") != "text" or not isinstance(payload_obj.get("text"), str):
                    continue
                artifact_id_direct = item.get("artifact_id")
                if isinstance(artifact_id_direct, str) and artifact_id_direct.strip():
                    artifact_streams.append((item, artifact_id_direct.strip(), 0))
                    continue
                cursor_used_local = item.get("cursor_used")
                if not (isinstance(cursor_used_local, str) and cursor_used_local.strip()):
                    continue
                try:
                    used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
                except ValueError:
                    continue
                if str(used_payload.get("kind") or "") != "artifact":
                    continue
                artifact_id = str(used_payload.get("artifact_id") or "").strip()
                if not artifact_id:
                    continue
                try:
                    base_offset = int(used_payload.get("char_offset") or 0)
                except (TypeError, ValueError):
                    base_offset = 0
                artifact_streams.append((item, artifact_id, max(0, base_offset)))

            if not artifact_streams:
                break
            target, target_artifact_id, base_offset = max(
                artifact_streams,
                key=lambda it: len(str(((it[0].get("payload") or {}) if isinstance(it[0].get("payload"), Mapping) else {}).get("text") or "")),
            )
            current_payload = target.get("payload") if isinstance(target.get("payload"), Mapping) else {}
            current_text = current_payload.get("text")
            if not isinstance(current_text, str) or len(current_text) <= PROMPT_VIEW_INLINE_MIN_CHARS:
                break

            excess = max(1, payload_chars - output_limit)
            new_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, len(current_text) - excess - 25)
            if new_len >= len(current_text):
                new_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, len(current_text) - 25)
            clipped_text = current_text[:new_len]
            current_payload = dict(current_payload)
            current_payload["text"] = clipped_text
            target["payload"] = current_payload
            target["chars"] = len(clipped_text)

            item_id = str(target.get("id") or "").strip()
            if item_id and target_artifact_id:
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="artifact"),
                    "artifact_id": target_artifact_id,
                    "char_offset": int(base_offset + len(clipped_text)),
                }
                cursor_signed = _sign_agentic_read_cursor_v2(cursor_next)
                target["next_cursor"] = _store_cursor_handle(cursor_signed) or cursor_signed
                target["complete"] = False
                target["truncated"] = True

            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = _build_response(total_chars_value=total_chars_final, hint=response_hint)
            payload_chars = _payload_len_with_budget(response_candidate)

    response = response_candidate

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
