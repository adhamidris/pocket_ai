from __future__ import annotations

import logging
import re
import uuid
from typing import Mapping

from apps.conversations.models import Conversation
from apps.rag.observability.logging import structured_log

from ..knowledge_support.read_guards import _is_uuid_ref_id, _scope_invalid_read_retry_limit
from ..types import ToolExecutionContext
from .engine import _agentic_read_v2_handler


logger = logging.getLogger(__name__)


def _read_knowledge_agentic_wrapper(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Agentic read_knowledge contract.

    Supported interface:
      read_knowledge(refs=[{id,cursor?}...], max_chars=...)

    This wrapper rejects legacy knobs in agentic mode to keep the contract small and predictable.
    """

    def _has_value(key: str) -> bool:
        value = arguments.get(key)
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    # Accept both "refs" (new) and "items" (compat alias) to reduce brittleness.
    raw_refs = arguments.get("refs")
    if not isinstance(raw_refs, list):
        raw_refs = arguments.get("items")

    if not isinstance(raw_refs, list) or not raw_refs:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_refs",
            "error_code": "missing_refs",
            "evidence": [],
            "hint": "refs[] is required (use ids/cursors from search_knowledge/read_knowledge).",
        }

    # Reject legacy parameters (besides UI-only metadata) to keep the contract tight.
    # NOTE: `mode` is deprecated (kept only as a no-op compat field). The backend
    # chooses the correct representation (tables -> table_rows, text -> excerpts)
    # and paging strategy.
    allowed = {"refs", "items", "max_chars", "mode", "__ui"}
    extra = [key for key in arguments.keys() if key not in allowed and _has_value(str(key))]
    if extra:
        return {
            "tool": "read_knowledge",
            "status": "constraint_error",
            "error": "unsupported_parameters",
            "error_code": "unsupported_parameters",
            "evidence": [],
            "unsupported_fields": extra,
            "hint": "Unsupported parameters for read_knowledge. Use only refs[] + max_chars (+ optional __ui).",
        }

    # Normalize "refs" into the internal "items" shape used by the read engine.
    items: list[dict[str, object]] = []
    seen_item_keys: set[tuple[str, str | None, int | None, int | None]] = set()
    unresolved_invalid_ids: list[str] = []
    unresolved_invalid_errors: list[dict[str, object]] = []
    for entry in raw_refs:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or entry.get("ref") or "").strip()
        if not item_id:
            continue
        cursor = entry.get("cursor")
        cursor_value = cursor.strip() if isinstance(cursor, str) and cursor.strip() else None
        row_start_raw = entry.get("row_start")
        if row_start_raw is None:
            row_start_raw = entry.get("start_row")
        row_limit_raw = entry.get("row_limit")
        if row_limit_raw is None:
            row_limit_raw = entry.get("max_rows")

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
        if _is_uuid_ref_id(item_id):
            canonical_id = str(uuid.UUID(item_id))
            item_key = (canonical_id, cursor_value, row_start_value, row_limit_value)
            if item_key in seen_item_keys:
                continue
            seen_item_keys.add(item_key)
            out: dict[str, object] = {"id": canonical_id}
            if cursor_value:
                out["cursor"] = cursor_value
            if row_start_value is not None:
                out["row_start"] = row_start_value
            if row_limit_value is not None:
                out["row_limit"] = row_limit_value
            items.append(out)
            continue

        unresolved_invalid_ids.append(item_id)
        unresolved_invalid_errors.append(
            {
                "id": item_id,
                "error_code": "invalid_id",
                "hint": "id must be a valid UUID from search_knowledge results.",
            }
        )

    retry_tracker = getattr(context, "invalid_read_ref_attempts", None)
    if not isinstance(retry_tracker, dict):
        retry_tracker = {}
        context.invalid_read_ref_attempts = retry_tracker
    retry_limit = _scope_invalid_read_retry_limit()
    retry_limit_hit = False
    for unresolved_id in unresolved_invalid_ids:
        tracker_key = re.sub(r"\s+", " ", str(unresolved_id or "").strip().lower())[:160]
        if not tracker_key:
            continue
        attempt_count = int(retry_tracker.get(tracker_key, 0) or 0) + 1
        retry_tracker[tracker_key] = attempt_count
        if attempt_count >= retry_limit:
            retry_limit_hit = True

    if not items:
        if retry_limit_hit:
            structured_log(
                "mcp",
                "read_knowledge.invalid_refs",
                {
                    "stage": "retry_limit_blocked",
                    "invalid_ref_count": len(unresolved_invalid_ids),
                    "repaired_ref_count": 0,
                    "retry_limit": retry_limit,
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )
            return {
                "tool": "read_knowledge",
                "status": "blocked",
                "error": "invalid_ref_retry_limit",
                "error_code": "invalid_ref_retry_limit",
                "evidence": [],
                "errors": unresolved_invalid_errors,
                "budget": context.budget_snapshot(),
                "hint": (
                    "Repeated non-UUID refs were blocked to prevent retry loops. "
                    "Use UUID refs returned by search_knowledge/read_knowledge only."
                ),
            }
        structured_log(
            "mcp",
            "read_knowledge.invalid_refs",
            {
                "stage": "invalid_refs_rejected",
                "invalid_ref_count": len(unresolved_invalid_ids),
                "repaired_ref_count": 0,
            },
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "invalid_refs",
            "error_code": "invalid_refs",
            "evidence": [],
            "errors": unresolved_invalid_errors,
            "budget": context.budget_snapshot(),
            "hint": (
                "refs[] must contain UUID ids from search_knowledge results. "
                "If refs came from a category label, run search_knowledge first and use the returned UUID refs."
            ),
        }

    # Pass through to the agentic read engine.
    engine_args: dict[str, object] = {
        "items": items,
        "max_chars": arguments.get("max_chars"),
    }
    engine_result = _agentic_read_v2_handler(engine_args, conversation, context)
    if unresolved_invalid_errors:
        patched_result = dict(engine_result)
        existing_errors = patched_result.get("errors")
        merged_errors = (
            [dict(item) for item in existing_errors if isinstance(item, Mapping)]
            if isinstance(existing_errors, list)
            else []
        )
        merged_errors.extend(unresolved_invalid_errors)
        patched_result["errors"] = merged_errors
        hint_text = str(patched_result.get("hint") or "").strip()
        reminder = "Ignored non-UUID refs and continued with valid UUID refs."
        patched_result["hint"] = f"{hint_text} {reminder}".strip() if hint_text else reminder
        return patched_result
    return engine_result
