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
from django.db import models
from django.db.models import Prefetch
from django.db.models.functions import Length

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.conversations.models import Conversation, ConversationFile, ConversationFileChunk
from apps.knowledge.knowledge_access import apply_customer_visible_chunks, apply_customer_visible_uploads
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
)
from apps.rag.contracts import KNOWLEDGE_READ_STATE_FULL, KnowledgeSnippet
from apps.rag.knowledge_search import KnowledgeSearchService
from apps.rag.rag_logging import structured_log

from ..agentic_read_cursor import (
    _AGENTIC_READ_CURSOR_V2_TTL_SECONDS,
    _sign_agentic_read_cursor_v2,
    _verify_agentic_read_cursor_v2,
)
from ..knowledge_identifier_helpers import _normalize_column_name
from ..knowledge_read_guards import _build_ingestion_warnings
from ..knowledge_result_helpers import (
    _apply_seen_row_filter,
    _budget_allows_full_page,
    _detect_full_page_intent,
    _mark_rows_as_seen,
    _mark_snippets_as_seen,
)
from ..knowledge_scope import _agent_knowledge_scope, _agent_scope_allows_upload
from ..models import McpToolOutputArtifact
from ..tool_artifacts import store_local_tool_output_artifact
from ..tool_definitions import (
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
)
from ..tool_runtime_helpers import _read_cache_key, _record_knowledge_audit_event_once
from ..types import CharacterBudgetExceeded, ToolExecutionContext


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
    _heading_month_tokens = {
        "jan",
        "january",
        "feb",
        "february",
        "mar",
        "march",
        "apr",
        "april",
        "may",
        "jun",
        "june",
        "jul",
        "july",
        "aug",
        "august",
        "sep",
        "sept",
        "september",
        "oct",
        "october",
        "nov",
        "november",
        "dec",
        "december",
        "ongoing",
        "present",
    }

    def _upload_title(upload: KnowledgeUpload | None) -> str:
        if not upload:
            return "Untitled"
        title = (
            getattr(upload, "display_name", None)
            or getattr(upload, "filename", None)
            or getattr(upload, "source_name", None)
            or getattr(upload, "external_reference", None)
            or getattr(upload, "slug", None)
            or str(getattr(upload, "id", "") or "")
        )
        title_text = str(title or "").strip() or "Untitled"
        return title_text

    def _collapse_ws(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _parse_block_anchor(value: object) -> tuple[int, int] | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        match = re.search(r"p(?P<page>\d+)-b(?P<block>\d+)", raw)
        if not match:
            return None
        try:
            return int(match.group("page")), int(match.group("block"))
        except (TypeError, ValueError):
            return None

    def _load_ordered_page_blocks(upload_id: str) -> list[dict[str, object]]:
        cached = upload_block_cache.get(upload_id)
        if cached is not None:
            return cached
        try:
            from apps.knowledge.models import KnowledgeUploadPageBlock

            rows = list(
                KnowledgeUploadPageBlock.objects.filter(upload_id=upload_id)
                .exclude(text="")
                .order_by("page__page_number", "order_index")
                .values(
                    "page__page_number",
                    "order_index",
                    "text",
                    "section_heading",
                    "heading_path",
                )
            )
        except Exception:
            rows = []
        normalized: list[dict[str, object]] = []
        for row in rows:
            try:
                page_number = int(row.get("page__page_number") or 0)
                order_index = int(row.get("order_index") or 0)
            except (TypeError, ValueError):
                continue
            normalized.append(
                {
                    "page_number": page_number,
                    "order_index": order_index,
                    "text": str(row.get("text") or ""),
                    "section_heading": str(row.get("section_heading") or ""),
                    "heading_path": list(row.get("heading_path") or []),
                }
            )
        upload_block_cache[upload_id] = normalized
        return normalized

    def _classify_heading_block(block: Mapping[str, object]) -> dict[str, object] | None:
        raw_text = str(block.get("text") or "")
        text = _collapse_ws(raw_text)
        if not text:
            return None

        normalized_path = [_collapse_ws(item) for item in (block.get("heading_path") or []) if _collapse_ws(item)]
        explicit_heading = _collapse_ws(block.get("section_heading") or "")
        if normalized_path or explicit_heading:
            label = normalized_path[-1] if normalized_path else explicit_heading
            return {
                "page_number": int(block.get("page_number") or 0),
                "order_index": int(block.get("order_index") or 0),
                "label": label,
                "level": max(1, len(normalized_path) or 1),
                "signature": "|".join(item.lower() for item in (normalized_path or [label]) if item),
                "heuristic": False,
            }

        if len(text) > 180:
            return None
        stripped = text.lstrip()
        if not stripped:
            return None
        if stripped.startswith(("●", "•", "-", "–", "—", "➔", "*")):
            return None
        if stripped[0].islower():
            return None
        if text[-1:] in {".", ";", "?", "!"}:
            return None

        tokens = re.findall(r"[A-Za-z0-9&/+'().-]+", text)
        if not tokens or len(tokens) > 22:
            return None

        lower_tokens = [token.lower() for token in tokens]
        has_digits = any(ch.isdigit() for ch in text)
        has_month = any(token in _heading_month_tokens for token in lower_tokens)
        pipe_count = text.count("|")
        comma_count = text.count(",")
        colon_count = text.count(":")

        level = 0
        if len(tokens) <= 6 and not has_digits and not has_month and pipe_count == 0 and comma_count <= 1 and colon_count == 0:
            level = 1
        elif len(tokens) <= 12 and not has_digits and not has_month and comma_count <= 1 and colon_count == 0:
            level = 1
        elif len(tokens) <= 20 and (has_digits or has_month or pipe_count > 0 or comma_count > 0) and colon_count <= 1:
            level = 2

        if level <= 0:
            return None

        return {
            "page_number": int(block.get("page_number") or 0),
            "order_index": int(block.get("order_index") or 0),
            "label": text,
            "level": level,
            "signature": text.lower(),
            "heuristic": True,
        }

    def _resolve_text_section_span(
        *,
        upload_id: str,
        chunk_record: KnowledgeUploadChunk | None,
        chunk_meta: Mapping[str, object],
        fallback_page_number: int,
    ) -> dict[str, int] | None:
        blocks = _load_ordered_page_blocks(upload_id)
        if not blocks:
            return None

        block_index_by_key = {
            (int(block["page_number"]), int(block["order_index"])): idx
            for idx, block in enumerate(blocks)
        }

        raw_block_anchors = chunk_meta.get("block_anchors")
        parsed_anchors: list[tuple[int, int]] = []
        if isinstance(raw_block_anchors, list):
            for entry in raw_block_anchors:
                parsed = _parse_block_anchor(entry)
                if parsed is not None:
                    parsed_anchors.append(parsed)
        parsed_anchors = [anchor for anchor in parsed_anchors if anchor in block_index_by_key]
        parsed_anchors.sort()

        if parsed_anchors:
            anchor_start_key = parsed_anchors[0]
            anchor_end_key = parsed_anchors[-1]
        else:
            canonical_anchor = _parse_block_anchor(chunk_meta.get("canonical_anchor_id"))
            if canonical_anchor and canonical_anchor in block_index_by_key:
                anchor_start_key = canonical_anchor
                anchor_end_key = canonical_anchor
            else:
                anchor_start_key = (int(fallback_page_number), 0)
                anchor_end_key = (int(fallback_page_number), 0)
                if anchor_start_key not in block_index_by_key:
                    return None

        anchor_start_idx = block_index_by_key.get(anchor_start_key)
        anchor_end_idx = block_index_by_key.get(anchor_end_key)
        if anchor_start_idx is None or anchor_end_idx is None:
            return None
        if anchor_end_idx < anchor_start_idx:
            anchor_start_idx, anchor_end_idx = anchor_end_idx, anchor_start_idx

        headings: list[dict[str, object]] = []
        heading_index_by_pos: dict[tuple[int, int], int] = {}
        for idx, block in enumerate(blocks):
            candidate = _classify_heading_block(block)
            if candidate is None:
                continue
            candidate["idx"] = idx
            headings.append(candidate)
            heading_index_by_pos[(int(candidate["page_number"]), int(candidate["order_index"]))] = idx

        if not headings:
            return None

        anchor_major: list[dict[str, object]] = [
            heading
            for heading in headings
            if anchor_start_idx <= int(heading["idx"]) <= anchor_end_idx and int(heading["level"]) == 1
        ]
        if anchor_major:
            start_heading = anchor_major[-1]
        else:
            prior_major = [
                heading
                for heading in headings
                if int(heading["idx"]) <= anchor_start_idx and int(heading["level"]) == 1
            ]
            if prior_major:
                start_heading = prior_major[-1]
            else:
                anchor_any = [
                    heading
                    for heading in headings
                    if anchor_start_idx <= int(heading["idx"]) <= anchor_end_idx
                ]
                if anchor_any:
                    start_heading = anchor_any[-1]
                else:
                    prior_any = [heading for heading in headings if int(heading["idx"]) <= anchor_start_idx]
                    if not prior_any:
                        return None
                    start_heading = prior_any[-1]

        start_idx = int(start_heading["idx"])
        start_level = int(start_heading["level"])
        end_idx = len(blocks) - 1
        start_signature = str(start_heading.get("signature") or "")
        for heading in headings:
            idx = int(heading["idx"])
            if idx <= start_idx:
                continue
            level = int(heading["level"])
            signature = str(heading.get("signature") or "")
            if level <= start_level and signature != start_signature:
                end_idx = idx - 1
                break

        if end_idx < start_idx:
            return None

        start_block = blocks[start_idx]
        end_block = blocks[end_idx]
        return {
            "start_page_number": int(start_block["page_number"]),
            "start_block_order": int(start_block["order_index"]),
            "end_page_number": int(end_block["page_number"]),
            "end_block_order": int(end_block["order_index"]),
        }

    def _cursor_payload_base(*, item_id: str, kind: str) -> dict[str, object]:
        exp = int(time.time()) + _AGENTIC_READ_CURSOR_V2_TTL_SECONDS
        return {
            "v": 2,
            "exp": exp,
            "conversation_id": str(conversation.id),
            "business_id": str(conversation.business_profile_id),
            "item_id": item_id,
            "kind": kind,
        }

    def _decode_cursor(item_id: str, cursor: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        try:
            payload = _verify_agentic_read_cursor_v2(cursor)
        except ValueError as exc:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": str(exc) or "Invalid cursor."}
        if str(payload.get("conversation_id") or "") != str(conversation.id):
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different conversation."}
        if str(payload.get("business_id") or "") != str(conversation.business_profile_id):
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different business."}
        if str(payload.get("item_id") or "") != item_id:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor does not match the requested id."}
        if int(payload.get("v") or 0) != 2:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Unsupported cursor version."}
        return payload, None

    def _resolve_cursor_from_handle(cursor_token: str | None) -> str | None:
        token = str(cursor_token or "").strip()
        if not token:
            return None
        cache_value = getattr(context, "read_cursor_handles", None)
        if isinstance(cache_value, dict):
            mapped = cache_value.get(token)
            if isinstance(mapped, str) and mapped.strip():
                return mapped.strip()
        return token

    def _store_cursor_handle(cursor_signed: str | None) -> str | None:
        token = str(cursor_signed or "").strip()
        if not token:
            return None
        cache_value = getattr(context, "read_cursor_handles", None)
        reverse_cache_value = getattr(context, "read_cursor_reverse_handles", None)
        if not isinstance(cache_value, dict) or not isinstance(reverse_cache_value, dict):
            return token

        existing_handle = reverse_cache_value.get(token)
        if isinstance(existing_handle, str) and existing_handle.strip():
            cached_token = cache_value.get(existing_handle.strip())
            if isinstance(cached_token, str) and cached_token == token:
                return existing_handle.strip()

        handle = f"c_{uuid.uuid4().hex[:20]}"
        cache_value[handle] = token
        reverse_cache_value[token] = handle

        while len(cache_value) > 500:
            oldest_handle = next(iter(cache_value))
            oldest_cursor = cache_value.pop(oldest_handle, None)
            if isinstance(oldest_cursor, str):
                reverse_cache_value.pop(oldest_cursor, None)
        while len(reverse_cache_value) > 500:
            oldest_cursor_key = next(iter(reverse_cache_value))
            oldest_handle_value = reverse_cache_value.pop(oldest_cursor_key, None)
            if isinstance(oldest_handle_value, str):
                cache_value.pop(oldest_handle_value, None)

        return handle

    def _load_table_anchor_manifest(*, ref_id: str, table_id: str | None = None) -> dict[str, object] | None:
        cache_value = getattr(context, "table_row_anchor_manifests", None)
        if not isinstance(cache_value, dict):
            return None
        candidate_keys: list[str] = []
        ref_key = str(ref_id or "").strip()
        table_key = str(table_id or "").strip()
        if ref_key:
            candidate_keys.append(ref_key)
        if table_key and table_key not in candidate_keys:
            candidate_keys.append(table_key)
        for key in candidate_keys:
            raw_manifest = cache_value.get(key)
            if not isinstance(raw_manifest, Mapping):
                continue
            anchors: list[int] = []
            raw_anchors = raw_manifest.get("anchors")
            if isinstance(raw_anchors, list):
                for entry in raw_anchors:
                    if isinstance(entry, Mapping):
                        parsed = _coerce_int(entry.get("row_index"))
                    else:
                        parsed = _coerce_int(entry)
                    if parsed is None or parsed < 0:
                        continue
                    anchors.append(int(parsed))
            # Deduplicate anchors while preserving order.
            if anchors:
                seen_rows: set[int] = set()
                anchors = [row for row in anchors if (row not in seen_rows and not seen_rows.add(row))]

            matched_row_index = _coerce_int(raw_manifest.get("matched_row_index"))
            if matched_row_index is None:
                matched_row_index = -1
            if matched_row_index < 0:
                if anchors:
                    matched_row_index = int(anchors[0])
                else:
                    continue

            # Ensure anchors includes matched_row_index, and keep it first.
            if matched_row_index in anchors:
                anchors = [matched_row_index] + [row for row in anchors if row != matched_row_index]
            else:
                anchors = [matched_row_index] + anchors
            anchors = anchors[:5]

            out: dict[str, object] = {
                "ref_id": key,
                "table_id": table_key or str(raw_manifest.get("table_id") or "").strip(),
                "matched_row_index": int(matched_row_index),
            }
            if anchors:
                out["anchors"] = list(anchors)

            try:
                estimated_rows = int(raw_manifest.get("estimated_rows") or 0)
            except (TypeError, ValueError):
                estimated_rows = 0
            if estimated_rows > 0:
                out["estimated_rows"] = int(estimated_rows)

            try:
                estimated_columns = int(raw_manifest.get("estimated_columns") or 0)
            except (TypeError, ValueError):
                estimated_columns = 0
            if estimated_columns > 0:
                out["estimated_columns"] = int(estimated_columns)

            return out
        return None

    def _resolve_target(item_id: str) -> tuple[
        KnowledgeUploadChunk | None,
        KnowledgeUpload | None,
        KnowledgeUploadTable | None,
        KnowledgeUploadTableRow | None,
        dict[str, object] | None,
    ]:
        if business_uuid is None:
            return (
                None,
                None,
                None,
                None,
                {
                    "id": item_id,
                    "error_code": "invalid_business_profile",
                    "hint": "conversation.business_profile_id must be a UUID.",
                },
            )
        try:
            identifier = uuid.UUID(item_id)
        except (TypeError, ValueError):
            return None, None, None, None, {"id": item_id, "error_code": "invalid_id", "hint": "id must be a valid UUID from search_knowledge results."}

        chunk_record = (
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    id=identifier,
                    business_profile_id=business_uuid,
                    upload__status=KnowledgeStatus.ACTIVE,
                )
            )
            .select_related("upload")
            .first()
        )
        if chunk_record:
            upload = getattr(chunk_record, "upload", None)
            return chunk_record, upload, None, None, None

        upload_record = apply_customer_visible_uploads(
            KnowledgeUpload.objects.filter(
                id=identifier,
                business_profile_id=business_uuid,
                status=KnowledgeStatus.ACTIVE,
            )
        ).first()
        if upload_record:
            return None, upload_record, None, None, None

        table_record = (
            KnowledgeUploadTable.objects.filter(
                id=identifier,
                upload__business_profile_id=business_uuid,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
            .select_related("upload")
            .only(
                "id",
                "upload_id",
                "title",
                "section_heading",
                "order_index",
                "upload__id",
                "upload__display_name",
                "upload__source_name",
                "upload__external_reference",
                "upload__slug",
            )
            .first()
        )
        if table_record:
            return None, None, table_record, None, None

        row_record = (
            KnowledgeUploadTableRow.objects.filter(
                id=identifier,
                table__upload__business_profile_id=business_uuid,
                table__upload__status=KnowledgeStatus.ACTIVE,
            )
            .exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
            .select_related("table", "table__upload")
            .only(
                "id",
                "row_index",
                "table__id",
                "table__title",
                "table__section_heading",
                "table__order_index",
                "table__upload_id",
                "table__upload__id",
                "table__upload__display_name",
                "table__upload__source_name",
                "table__upload__external_reference",
                "table__upload__slug",
            )
            .first()
        )
        if row_record:
            return None, None, None, row_record, None

        return None, None, None, None, {"id": item_id, "error_code": "not_found", "hint": "Document not found for this business."}

    agent_scope = _agent_knowledge_scope(conversation, context)

    def _enforce_access(upload_id: str, *, item_id: str) -> dict[str, object] | None:
        if upload_id and not _agent_scope_allows_upload(scope=agent_scope, conversation=conversation, upload_id=upload_id):
            return {"id": item_id, "error_code": "forbidden_document", "hint": "This agent is not permitted to access that document."}
        return None

    def _is_dataset_upload(upload: KnowledgeUpload | None) -> bool:
        if not upload:
            return False
        try:
            ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
        except Exception:
            ingestion_meta = {}
        dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, Mapping) else None
        dataset_enabled = bool(isinstance(dataset_meta, Mapping) and dataset_meta.get("enabled"))
        format_hint = str(ingestion_meta.get("format") or "").strip().lower()
        native_tabular = format_hint in {"csv", "tsv", "xls", "xlsx", "jsonl"}
        if dataset_enabled or native_tabular:
            return True
        # Legacy heuristic: uploads that have tables but no pages are usually spreadsheets/datasets.
        try:
            if upload.tables.exists() and not upload.pages.exists():
                return True
        except Exception:
            pass
        return False

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
        try:
            from apps.knowledge.models import (
                KnowledgeUploadPage,
                KnowledgeUploadPageBlock,
            )
        except Exception:
            return "", None, True

        page_obj = (
            KnowledgeUploadPage.objects.filter(upload_id=upload_id, page_number=page_number)
            .only("id")
            .first()
        )
        if not page_obj:
            return "", None, True

        blocks_qs = (
            KnowledgeUploadPageBlock.objects.filter(page_id=page_obj.id)
            .exclude(text="")
            .order_by("order_index")
            .values_list("order_index", "text")
        )

        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        # Only prepend a separator when resuming at an element boundary.
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        started = False
        for order_index, text in blocks_qs.iterator():  # type: ignore[attr-defined]
            try:
                order_int = int(order_index)
            except (TypeError, ValueError):
                continue
            if order_int < int(start_order):
                continue
            raw_text = str(text or "")
            if not raw_text:
                continue

            chunk_text = raw_text
            offset = 0
            if not started:
                started = True
                offset = max(0, int(start_offset))
                if offset:
                    chunk_text = chunk_text[offset:]
            separator = "\n\n" if (out_parts or prepend_sep) else ""
            # If we can't fit the separator + at least one character, stop and continue on next call.
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="page_blocks"),
                    "upload_id": upload_id,
                    "page_number": int(page_number),
                    "block_order": int(order_int),
                    "char_offset": int(offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(chunk_text) <= remaining:
                out_parts.append(chunk_text)
                remaining -= len(chunk_text)
                # Continue to next block
                continue

            # Partial block
            out_parts.append(chunk_text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="page_blocks"),
                "upload_id": upload_id,
                "page_number": int(page_number),
                "block_order": int(order_int),
                "char_offset": int(offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete

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
        blocks = _load_ordered_page_blocks(upload_id)
        if not blocks:
            return "", None, True

        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        started = False
        current_page = int(current_page_number)
        current_order = int(current_block_order)
        for block in blocks:
            try:
                page_int = int(block.get("page_number") or 0)
                order_int = int(block.get("order_index") or 0)
            except (TypeError, ValueError):
                continue
            if (page_int, order_int) < (int(start_page_number), int(start_block_order)):
                continue
            if (page_int, order_int) > (int(end_page_number), int(end_block_order)):
                continue
            if (page_int, order_int) < (current_page, current_order):
                continue
            raw_text = str(block.get("text") or "")
            if not raw_text:
                continue

            chunk_text = raw_text
            offset = 0
            if not started:
                started = True
                offset = max(0, int(start_offset))
                if offset:
                    chunk_text = chunk_text[offset:]

            separator = "\n\n" if (out_parts or prepend_sep) else ""
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="section_span"),
                    "upload_id": upload_id,
                    "start_page_number": int(start_page_number),
                    "start_block_order": int(start_block_order),
                    "end_page_number": int(end_page_number),
                    "end_block_order": int(end_block_order),
                    "current_page_number": int(page_int),
                    "current_block_order": int(order_int),
                    "char_offset": int(offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(chunk_text) <= remaining:
                out_parts.append(chunk_text)
                remaining -= len(chunk_text)
                continue

            out_parts.append(chunk_text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="section_span"),
                "upload_id": upload_id,
                "start_page_number": int(start_page_number),
                "start_block_order": int(start_block_order),
                "end_page_number": int(end_page_number),
                "end_block_order": int(end_block_order),
                "current_page_number": int(page_int),
                "current_block_order": int(order_int),
                "char_offset": int(offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete


    def _read_tabular_rows_segment_facts(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        start_row_index: int = 0,
        budget_chars: int,
        max_rows: int | None = None,
        business_profile,
        selection_mode: str | None = None,
    ) -> tuple[dict[str, object], dict[str, object] | None, bool]:
        """
        Facts-first table read for the LLM.

        Contract:
        - `start_row_index` is a 0-based row offset within the visible table body
          (header/separator rows excluded by ingestion metadata).
        - Returns only the table facts + paging signals needed to continue via
          `row_start` + `row_limit` (no table cursors).
        """

        budget_limit = max(0, int(budget_chars))
        complete = True

        payload: dict[str, object] = {
            "type": "table",
            "table_id": str(table_id),
            "columns": [],
            "rows": [],
            "row_offset": 0,
            "rows_shown": 0,
            "total_rows": 0,
        }
        if isinstance(selection_mode, str) and selection_mode.strip():
            payload["selection_mode"] = selection_mode.strip()

        try:
            table_uuid = uuid.UUID(str(table_id))
        except (TypeError, ValueError):
            return payload, None, True

        table = (
            KnowledgeUploadTable.objects.filter(
                id=table_uuid,
                upload_id=upload_id,
                upload__business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .only("id", "order_index", "title", "section_heading", "column_schema")
            .first()
        )
        if not table:
            return payload, None, True

        def _dedupe_column_labels(raw_columns: Sequence[str]) -> list[str]:
            deduped: list[str] = []
            seen: dict[str, int] = {}
            for index, raw_column in enumerate(raw_columns):
                label = str(raw_column or "").strip() or f"column_{index + 1}"
                key = label.lower()
                count = seen.get(key, 0) + 1
                seen[key] = count
                deduped.append(label if count == 1 else f"{label}_{count}")
            return deduped

        # Column labels: prefer column_schema if it looks like a list of strings.
        raw_schema = table.column_schema if isinstance(getattr(table, "column_schema", None), list) else []
        columns: list[str] = []
        for entry in raw_schema:
            if isinstance(entry, str) and entry.strip():
                columns.append(entry.strip())
            elif isinstance(entry, Mapping):
                for key in ("column", "name", "label", "key", "column_key"):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip():
                        columns.append(value.strip())
                        break
        columns = columns[:200]

        # Visible rows are non-header rows only (in ingestion metadata).
        non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
            metadata__row_type__in=["header", "section_header"]
        )

        # If schema is missing/empty, infer columns from the first visible row's cells.
        if not columns:
            row_obj = (
                table.rows.filter(non_header_q)
                .order_by("row_index")
                .only("id", "row_index")
                .first()
            )
            if row_obj:
                cell_qs = (
                    KnowledgeUploadTableCell.objects.filter(row_id=row_obj.id)
                    .order_by("column_index")
                    .values_list("column_index", "column_key")
                )
                inferred: list[tuple[int, str]] = []
                for col_idx, col_key in cell_qs:
                    try:
                        idx = int(col_idx)
                    except (TypeError, ValueError):
                        continue
                    label = str(col_key or "").strip() or f"column_{idx + 1}"
                    inferred.append((idx, label))
                inferred.sort(key=lambda item: item[0])
                columns = [label for _idx, label in inferred][:200]

        columns = _dedupe_column_labels(columns[:200])
        payload["columns"] = columns

        try:
            start_row = max(0, int(start_row_index))
        except (TypeError, ValueError):
            start_row = 0
        payload["row_offset"] = start_row

        try:
            total_rows = int(table.rows.filter(non_header_q).count())
        except Exception:
            total_rows = 0
        payload["total_rows"] = total_rows

        # Bound reads: even for "list everything", never scan unbounded rows in one call.
        try:
            scan_cap = int(max(50, min(500, int(budget_chars) // 30 or 50)))
        except Exception:
            scan_cap = 200
        limit_rows = scan_cap
        if isinstance(max_rows, int) and max_rows > 0:
            limit_rows = min(limit_rows, int(max_rows))
        limit_rows = max(1, int(limit_rows))

        row_qs = (
            table.rows.filter(non_header_q)
            .order_by("row_index")
            .only("id", "row_index", "metadata")
        )
        row_qs = row_qs[start_row : start_row + limit_rows]
        row_qs = row_qs.prefetch_related(
            Prefetch(
                "cells",
                queryset=KnowledgeUploadTableCell.objects.order_by("column_index").only(
                    "row_id",
                    "column_index",
                    "column_key",
                    "raw_text",
                ),
            )
        )

        def _normalize_cell_text(value: str) -> str:
            normalized = str(value or "").strip().lower()
            normalized = re.sub(r"\\s+", " ", normalized)
            return normalized

        def _is_uniform_section_separator(values: Sequence[str]) -> tuple[bool, str]:
            non_empty = [str(cell or "").strip() for cell in values if str(cell or "").strip()]
            if len(non_empty) < 4:
                return False, ""
            normalized = [_normalize_cell_text(cell) for cell in non_empty]
            first = normalized[0] if normalized else ""
            if not first:
                return False, ""
            if any(cell != first for cell in normalized[1:]):
                return False, ""
            return True, non_empty[0]

        scope_overlay_reasons = {
            "scope_explicit_span",
            "scope_repeated_value_span",
            "scope_sparse_expansion",
            "scope_edge_completion",
        }
        column_index_lookup: dict[str, int] = {}
        for idx, label in enumerate(columns):
            normalized = _normalize_column_name(label)
            if normalized and normalized not in column_index_lookup:
                column_index_lookup[normalized] = idx
            lowered = str(label or "").strip().lower()
            if lowered and lowered not in column_index_lookup:
                column_index_lookup[lowered] = idx

        index_offset: int | None = None
        rows_out: list[list[str]] = []

        def _payload_size(candidate_rows: Sequence[Sequence[str]]) -> int:
            candidate: dict[str, object] = {
                "type": "table",
                "table_id": str(table_id),
                "columns": list(columns),
                "rows": [list(row) for row in candidate_rows],
                "row_offset": int(start_row),
                "rows_shown": int(len(candidate_rows)),
                "total_rows": int(total_rows),
            }
            if isinstance(selection_mode, str) and selection_mode.strip():
                candidate["selection_mode"] = selection_mode.strip()
            try:
                return len(json.dumps(candidate, ensure_ascii=False, default=str))
            except Exception:
                return 0

        for row in row_qs:
            cell_lookup: dict[int, str] = {}
            for cell in row.cells.all():  # type: ignore[attr-defined]
                try:
                    idx = int(getattr(cell, "column_index", None) or 0)
                except (TypeError, ValueError):
                    continue
                text = str(getattr(cell, "raw_text", "") or "")
                cell_lookup[idx] = text

            if index_offset is None:
                keys = list(cell_lookup.keys())
                if 0 in cell_lookup:
                    index_offset = 0
                elif 1 in cell_lookup:
                    index_offset = 1
                elif keys:
                    index_offset = min(keys)
                else:
                    index_offset = 0

            values: list[str] = []
            for col_idx in range(len(columns)):
                values.append(str(cell_lookup.get(int(col_idx) + int(index_offset or 0), "")))

            # Collapse broad-span note rows (same text duplicated across many columns) to:
            #   [note_text, "", "", ...]
            is_uniform, uniform_label = _is_uniform_section_separator(values)
            if is_uniform and uniform_label:
                values = [uniform_label] + [""] * max(0, len(columns) - 1)

            row_meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
            inferred_scope_columns: list[str] = []
            raw_applies_to = row_meta.get("inferred_scope_columns")
            if isinstance(raw_applies_to, (list, tuple)):
                for scope_entry in raw_applies_to:
                    label = str(scope_entry or "").strip()
                    if label:
                        inferred_scope_columns.append(label)
            scope_reason = str(row_meta.get("scope_reason") or "").strip()
            scope_reason_key = scope_reason.lower()
            fee_value = str(row_meta.get("scope_value") or "").strip()

            effective_values = list(values)
            if (
                fee_value
                and inferred_scope_columns
                and (
                    scope_reason_key.startswith("inferred_")
                    or scope_reason_key in scope_overlay_reasons
                )
            ):
                for scope_label in inferred_scope_columns:
                    normalized_scope = _normalize_column_name(scope_label)
                    if not normalized_scope:
                        continue
                    col_pos = column_index_lookup.get(normalized_scope)
                    if col_pos is None:
                        col_pos = column_index_lookup.get(str(scope_label).strip().lower())
                    if col_pos is None or col_pos < 0 or col_pos >= len(effective_values):
                        continue
                    if str(effective_values[col_pos] or "").strip():
                        continue
                    effective_values[col_pos] = fee_value

            candidate_rows = [*rows_out, effective_values]
            if _payload_size(candidate_rows) > budget_limit:
                complete = False
                break
            rows_out = candidate_rows

        payload["rows"] = rows_out
        payload["rows_shown"] = len(rows_out)
        if (start_row + len(rows_out)) < int(total_rows or 0):
            payload["next_row_start"] = int(start_row + len(rows_out))

        return payload, None, complete

    def _table_payload_is_informative(table_payload: Mapping[str, object]) -> bool:
        rows_value = table_payload.get("rows")
        if not isinstance(rows_value, list) or not rows_value:
            return False
        for row in rows_value:
            if isinstance(row, (list, tuple)):
                if any(str(cell or "").strip() for cell in row):
                    return True
            elif str(row or "").strip():
                return True
        return False

    def _table_payload_size(table_payload: Mapping[str, object]) -> int:
        try:
            return len(json.dumps(dict(table_payload), ensure_ascii=False, default=str))
        except Exception:
            return 0

    def _merge_table_anchor_payloads(
        *,
        base_payload: Mapping[str, object],
        incoming_payload: Mapping[str, object],
        budget_chars: int,
        selection_mode: str,
    ) -> tuple[dict[str, object], int, bool]:
        merged_columns = list(base_payload.get("columns") or incoming_payload.get("columns") or [])
        if not merged_columns:
            merged_columns = list(incoming_payload.get("columns") or [])

        base_rows_raw = base_payload.get("rows")
        incoming_rows_raw = incoming_payload.get("rows")
        base_rows = [list(row) for row in base_rows_raw] if isinstance(base_rows_raw, list) else []
        incoming_rows = [list(row) for row in incoming_rows_raw] if isinstance(incoming_rows_raw, list) else []

        merged: dict[str, object] = {
            "type": "table",
            "table_id": str(base_payload.get("table_id") or incoming_payload.get("table_id") or ""),
            "columns": merged_columns,
            "rows": [],
            "row_offset": min(
                max(0, _coerce_int(base_payload.get("row_offset")) or 0),
                max(0, _coerce_int(incoming_payload.get("row_offset")) or 0),
            ),
            "rows_shown": 0,
            "total_rows": max(
                max(0, _coerce_int(base_payload.get("total_rows")) or 0),
                max(0, _coerce_int(incoming_payload.get("total_rows")) or 0),
            ),
            "selection_mode": selection_mode,
        }

        merged_rows: list[list[str]] = []
        seen_rows: set[tuple[str, ...]] = set()
        for row in [*base_rows, *incoming_rows]:
            normalized_row = tuple(str(cell or "") for cell in row)
            if normalized_row in seen_rows:
                continue
            candidate_rows = [*merged_rows, list(row)]
            candidate_payload = dict(merged)
            candidate_payload["rows"] = candidate_rows
            candidate_payload["rows_shown"] = len(candidate_rows)
            if _table_payload_size(candidate_payload) > max(0, int(budget_chars)):
                merged["rows"] = merged_rows
                merged["rows_shown"] = len(merged_rows)
                if (
                    int(merged["row_offset"]) + len(merged_rows)
                ) < int(merged.get("total_rows") or 0):
                    merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
                return merged, max(0, len(merged_rows) - len(base_rows)), True
            merged_rows = candidate_rows
            seen_rows.add(normalized_row)

        merged["rows"] = merged_rows
        merged["rows_shown"] = len(merged_rows)
        if (
            int(merged["row_offset"]) + len(merged_rows)
        ) < int(merged.get("total_rows") or 0):
            merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
        return merged, max(0, len(merged_rows) - len(base_rows)), False

    def _table_db_row_index_to_visible_row_start(
        *,
        table_id: str,
        upload_id: str,
        business_profile,
        db_row_index: int,
    ) -> int:
        """
        Convert a stored table row index into a visible `row_start` offset.

        The visible table body excludes header / section-header rows, so row refs
        and anchor reads must normalize DB row indexes before paging facts.
        """
        try:
            raw_idx = int(db_row_index)
        except (TypeError, ValueError):
            return 0
        if raw_idx < 0:
            return 0
        try:
            table_uuid = uuid.UUID(str(table_id))
        except (TypeError, ValueError):
            return max(0, raw_idx)

        non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
            metadata__row_type__in=["header", "section_header"]
        )

        base_qs = KnowledgeUploadTableRow.objects.filter(
            table_id=table_uuid,
            table__upload_id=upload_id,
            table__upload__business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        )

        target = (
            base_qs.filter(non_header_q, row_index__gte=raw_idx)
            .order_by("row_index")
            .only("row_index")
            .first()
        )
        if target is None:
            target = (
                base_qs.filter(non_header_q, row_index__lte=raw_idx)
                .order_by("-row_index")
                .only("row_index")
                .first()
            )
        if target is None:
            return 0

        try:
            target_db_idx = int(getattr(target, "row_index", 0) or 0)
        except (TypeError, ValueError):
            target_db_idx = raw_idx

        try:
            visible_before = int(
                base_qs.filter(non_header_q, row_index__lt=int(target_db_idx)).count()
            )
        except Exception:
            visible_before = max(0, raw_idx)
        return max(0, int(visible_before))

    def _read_exact_table_row(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        db_row_index: int,
        budget_chars: int,
        business_profile,
    ) -> tuple[dict[str, object], bool]:
        visible_row_start = _table_db_row_index_to_visible_row_start(
            table_id=table_id,
            upload_id=upload_id,
            business_profile=business_profile,
            db_row_index=db_row_index,
        )
        table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=visible_row_start,
            budget_chars=budget_chars,
            max_rows=1,
            business_profile=business_profile,
            selection_mode="row_ref",
        )
        return table_payload, complete

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
        anchor_used = False
        fallback_used = False
        anchor_start_row = int(start_row_index)

        if use_anchor and anchor_start_row <= 0:
            manifest = _load_table_anchor_manifest(ref_id=item_id, table_id=table_id)
            if isinstance(manifest, Mapping):
                anchor_rows: list[int] = []
                primary_anchor = _coerce_int(manifest.get("matched_row_index"))
                if primary_anchor is not None and primary_anchor >= 0:
                    anchor_rows.append(int(primary_anchor))
                raw_anchors = manifest.get("anchors")
                if isinstance(raw_anchors, list):
                    for entry in raw_anchors:
                        parsed = _coerce_int(entry)
                        if parsed is None or parsed < 0:
                            continue
                        anchor_rows.append(int(parsed))
                if anchor_rows:
                    # Deduplicate while preserving order, and cap attempts.
                    seen_rows: set[int] = set()
                    anchor_rows = [row for row in anchor_rows if (row not in seen_rows and not seen_rows.add(row))]
                    anchor_used = True
                    merged_payload: dict[str, object] | None = None
                    merged_complete = True
                    merged_anchor_hits = 0
                    for row_idx in anchor_rows[:2]:
                        visible_row_start = _table_db_row_index_to_visible_row_start(
                            table_id=table_id,
                            upload_id=upload_id,
                            business_profile=business_profile,
                            db_row_index=int(row_idx),
                        )
                        anchor_start_row = max(0, int(visible_row_start) - 1)
                        table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
                            item_id=item_id,
                            upload_id=upload_id,
                            table_id=table_id,
                            start_row_index=anchor_start_row,
                            budget_chars=budget_chars,
                            max_rows=max_rows,
                            business_profile=business_profile,
                            selection_mode="anchor_match",
                        )
                        if not _table_payload_is_informative(table_payload):
                            merged_complete = False
                            continue
                        if merged_payload is None:
                            merged_payload = dict(table_payload)
                            merged_complete = bool(complete)
                            merged_anchor_hits = 1
                            continue
                        merged_payload, added_rows, merge_budget_exhausted = _merge_table_anchor_payloads(
                            base_payload=merged_payload,
                            incoming_payload=table_payload,
                            budget_chars=budget_chars,
                            selection_mode="anchor_merge",
                        )
                        if added_rows > 0:
                            merged_anchor_hits += 1
                        merged_complete = bool(merged_complete and complete and not merge_budget_exhausted)
                    if merged_payload is not None and _table_payload_is_informative(merged_payload):
                        if merged_anchor_hits <= 1:
                            merged_payload["selection_mode"] = "anchor_match"
                        return merged_payload, None, merged_complete, anchor_used, fallback_used
                    # Anchors were tried but didn't yield useful rows; fall back.
                    fallback_used = True

        table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=max(0, int(start_row_index)),
            budget_chars=budget_chars,
            max_rows=max_rows,
            business_profile=business_profile,
            selection_mode="row_range",
        )

        return table_payload, cursor_out, complete, anchor_used, fallback_used

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
        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        # Only prepend a separator when resuming at an element boundary.
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        window_qs = (
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    upload_id=upload_id,
                    business_profile=business_profile,
                    upload__status=KnowledgeStatus.ACTIVE,
                    chunk_index__gte=int(start_index),
                    chunk_index__lte=int(end_index),
                )
            )
            .exclude(content="")
            .order_by("chunk_index")
            .values_list("chunk_index", "content")
        )

        cur_idx = int(current_index)
        offset = max(0, int(start_offset))
        started = False
        for chunk_index, content in window_qs.iterator():  # type: ignore[attr-defined]
            try:
                ci = int(chunk_index)
            except (TypeError, ValueError):
                continue
            if ci < cur_idx:
                continue
            raw = str(content or "")
            if not raw:
                continue
            text = raw
            local_offset = 0
            if not started:
                started = True
                local_offset = offset
                if local_offset:
                    text = text[local_offset:]

            separator = "\n\n" if (out_parts or prepend_sep) else ""
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="chunk_window"),
                    "upload_id": upload_id,
                    "chunk_start": int(start_index),
                    "chunk_end": int(end_index),
                    "chunk_index": int(ci),
                    "char_offset": int(local_offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(text) <= remaining:
                out_parts.append(text)
                remaining -= len(text)
                continue

            out_parts.append(text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="chunk_window"),
                "upload_id": upload_id,
                "chunk_start": int(start_index),
                "chunk_end": int(end_index),
                "chunk_index": int(ci),
                "char_offset": int(local_offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete

    def _read_artifact_segment(
        *,
        item_id: str,
        artifact_id: str,
        start_offset: int,
        budget_chars: int,
    ) -> tuple[str, dict[str, object] | None, bool, dict[str, object] | None]:
        """
        Read from a stored tool-output artifact (Phase 4 fallback).

        Artifact payload shape (response JSON):
          { "text": "...", "title": "...", "type": "text|table", "cursor_after": "opaque_or_null" }
        """

        try:
            artifact_uuid = uuid.UUID(str(artifact_id))
        except (TypeError, ValueError):
            return "", None, True, {"id": item_id, "error_code": "invalid_cursor", "hint": "Invalid artifact id."}

        artifact = (
            McpToolOutputArtifact.objects.filter(
                id=artifact_uuid,
                conversation=conversation,
                invoked_tool="read_knowledge",
            )
            .only("id", "response")
            .first()
        )
        if not artifact:
            return "", None, True, {"id": item_id, "error_code": "artifact_not_found", "hint": "Artifact not found for this conversation."}

        payload = artifact.response if isinstance(getattr(artifact, "response", None), Mapping) else {}
        full_text = payload.get("text")
        if not isinstance(full_text, str):
            full_text = str(full_text or "")
        if not full_text:
            return "", None, True, {"id": item_id, "error_code": "artifact_empty", "hint": "Artifact has no readable text."}

        title_override = payload.get("title")
        type_override = payload.get("type")

        start = max(0, int(start_offset or 0))
        remaining = max(0, int(budget_chars))
        out = full_text[start : start + remaining]
        end = start + len(out)

        cursor_after = payload.get("cursor_after")
        if end < len(full_text):
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="artifact"),
                "artifact_id": str(artifact.id),
                "char_offset": int(end),
            }
            cursor_str = _sign_agentic_read_cursor_v2(cursor_next)
            return out, {"cursor": cursor_str}, False, {"title": title_override, "type": type_override}

        if isinstance(cursor_after, str) and cursor_after.strip():
            # Once the artifact stream is consumed, resume the original knowledge cursor (if any).
            return out, {"cursor": cursor_after.strip()}, False, {"title": title_override, "type": type_override}

        return out, None, True, {"title": title_override, "type": type_override}

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

    def _response_status() -> str:
        if errors or deferred or any(str(entry.get("status") or "") in {"truncated", "partial", "artifact"} for entry in read):
            return "truncated" if contents else "error"
        return "ok"

    def _compact_read_summaries_for_prompt(entries: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        """
        Keep read summaries lean for prompt injection.

        We preserve control-flow entries (truncated/error/deferred/covered/artifact/etc.)
        and only keep "full" entries when they carry actionable metadata (hint/cursor/artifact).
        """

        compact_entries: list[dict[str, object]] = []
        for entry in entries:
            status_raw = str(entry.get("status") or "").strip()
            status_key = status_raw.lower()
            has_hint = isinstance(entry.get("hint"), str) and bool(str(entry.get("hint") or "").strip())
            has_cursor = isinstance(entry.get("next_cursor"), str) and bool(str(entry.get("next_cursor") or "").strip())
            has_artifact = isinstance(entry.get("artifact_id"), str) and bool(str(entry.get("artifact_id") or "").strip())
            keep_entry = status_key != "full" or has_hint or has_cursor or has_artifact
            if not keep_entry:
                continue

            out: dict[str, object] = {}
            item_id = entry.get("id")
            if isinstance(item_id, str) and item_id.strip():
                out["id"] = item_id.strip()
            elif item_id is not None:
                out["id"] = item_id

            if status_raw:
                out["status"] = status_raw

            chars_value = entry.get("chars")
            if isinstance(chars_value, bool):
                chars_value = None
            if isinstance(chars_value, (int, float)):
                out["chars"] = int(chars_value)
            elif isinstance(chars_value, str) and chars_value.strip():
                try:
                    out["chars"] = int(chars_value.strip())
                except (TypeError, ValueError):
                    pass

            error_code = entry.get("error_code")
            if isinstance(error_code, str) and error_code.strip():
                out["error_code"] = error_code.strip()

            if has_artifact:
                out["artifact_id"] = str(entry.get("artifact_id")).strip()
            if has_cursor:
                out["next_cursor"] = str(entry.get("next_cursor")).strip()
            if has_hint:
                out["hint"] = str(entry.get("hint")).strip()

            if out:
                compact_entries.append(out)

        return compact_entries

    def _build_response(*, total_chars_value: int, hint: str | None = None) -> dict[str, object]:
        read_out = _compact_read_summaries_for_prompt(read)
        payload: dict[str, object] = {
            "tool": "read_knowledge",
            "status": _response_status(),
            "evidence": contents,
            "deferred": deferred,
            "max_chars": int(max_chars),
            "max_chars_allowed": int(max_chars_allowed),
            "total_chars": int(total_chars_value),
            "budget": context.budget_snapshot(),
        }
        if read_out:
            payload["read"] = read_out
        if errors:
            payload["errors"] = errors
        if hint:
            payload["hint"] = hint
        elif not contents and (deferred or errors):
            payload["hint"] = (
                "No content could be read. Ensure ids/cursors come from tool results, "
                "increase max_chars (up to max_chars_allowed), or retry with fewer items."
            )
        return payload

    def _payload_len_with_budget(payload: Mapping[str, object]) -> int:
        try:
            return len(json.dumps(dict(payload), ensure_ascii=False, default=str))
        except Exception:
            return 0

    def _attach_artifact_for_item(item: dict[str, object], trace: dict[str, object]) -> bool:
        item_id = str(item.get("id") or "").strip()
        payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
        if str(payload_obj.get("type") or "") != "text":
            return False
        full_text = payload_obj.get("text")
        if not item_id or not isinstance(full_text, str) or not full_text:
            return False
        if isinstance(item.get("artifact_id"), str) and str(item.get("artifact_id") or "").strip():
            return False

        preview_len = min(len(full_text), PROMPT_VIEW_INLINE_MAX_CHARS)
        if len(full_text) >= PROMPT_VIEW_INLINE_MIN_CHARS:
            preview_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, preview_len)
        preview_text = full_text[:preview_len]
        cursor_after_raw = item.get("next_cursor")
        cursor_after = _resolve_cursor_from_handle(cursor_after_raw if isinstance(cursor_after_raw, str) else None)
        cursor_used_local = item.get("cursor_used")
        if isinstance(cursor_used_local, str) and cursor_used_local.strip():
            # Avoid nested artifacts: if this segment was already read from an artifact cursor,
            # we can always clip + continue with that cursor instead of creating a new artifact.
            try:
                used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
            except ValueError:
                used_payload = None
            if isinstance(used_payload, dict) and str(used_payload.get("kind") or "") == "artifact":
                return False

        artifact_payload: dict[str, object] = {
            "kind": "agentic_read_v2",
            "item_id": item_id,
            "title": item.get("title"),
            "type": item.get("type"),
            "text": full_text,
        }
        if isinstance(cursor_after, str) and cursor_after.strip():
            artifact_payload["cursor_after"] = cursor_after.strip()
        if isinstance(cursor_used_local, str) and cursor_used_local.strip():
            artifact_payload["cursor_used"] = cursor_used_local.strip()

        artifact_id = store_local_tool_output_artifact(
            conversation=conversation,
            invoked_tool="read_knowledge",
            request={"refs": [{"id": item_id, **({"cursor": cursor_used_local} if cursor_used_local else {})}]},
            response=artifact_payload,
            status="ok",
            is_error=False,
            retention_days=artifact_retention_days,
            max_per_conversation=artifact_max_per_conversation,
        )
        if not artifact_id:
            return False

        cursor_next = {
            **_cursor_payload_base(item_id=item_id, kind="artifact"),
            "artifact_id": artifact_id,
            "char_offset": int(preview_len),
        }
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next)

        item["artifact_id"] = artifact_id
        payload_obj = dict(payload_obj)
        payload_obj["text"] = preview_text
        item["payload"] = payload_obj
        item["chars"] = len(preview_text)
        item["next_cursor"] = _store_cursor_handle(cursor_str) or cursor_str
        item["complete"] = False
        item["truncated"] = True

        trace["status"] = "artifact"
        trace["chars"] = len(full_text)
        trace["artifact_id"] = artifact_id
        return True

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
