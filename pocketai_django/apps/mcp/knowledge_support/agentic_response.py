"""
Agentic search response conversion for MCP knowledge tools.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from typing import Mapping, Sequence

from django.conf import settings

from apps.conversations.models import Conversation
from apps.knowledge.privacy import sha256_hex
from apps.rag.rag_logging import structured_log

from ..runtime.search_cursor import _store_search_cursor_handle
from ..tool_definitions import (
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
)
from ..runtime.tool_runtime_helpers import _has_prompt_evidence
from ..types import ToolExecutionContext


logger = logging.getLogger(__name__)


def _convert_to_agentic_search_response(
    legacy_payload: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext | None = None,
) -> dict[str, object]:
    """
    Convert legacy search_knowledge response to agentic format.

    Phase 1 (EvidenceRefs): return pointers only (optionally with short previews).

    The agentic format returns a compact list of "refs" the model can read via
    read_knowledge(). This intentionally avoids duplicating facts in multiple
    representations (table row + text restatement) and keeps the prompt small.
    """
    snippets = legacy_payload.get("snippets", [])
    refs: list[dict[str, object]] = []
    agentic_read_v2_enabled = (
        bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
        and bool(getattr(settings, "MCP_AGENTIC_READ_V2_ENABLED", False))
    )
    preview_full_enabled = bool(getattr(settings, "MCP_AGENTIC_SEARCH_PREVIEWS_ENABLED", False))
    preview_hybrid_enabled = bool(getattr(settings, "MCP_AGENTIC_SEARCH_PREVIEWS_HYBRID_ENABLED", False))
    try:
        preview_chars_cap = int(getattr(settings, "MCP_PROMPT_SNIPPET_CONTENT_CHARS", 1200) or 1200)
    except (TypeError, ValueError):
        preview_chars_cap = 1200
    preview_chars_cap = max(0, preview_chars_cap)
    hybrid_preview_max_items = 10
    hybrid_preview_chars_cap = min(preview_chars_cap, 400) if preview_chars_cap else 0
    previews_attached = 0

    def _preview_text(snippet: Mapping[str, object], *, max_chars: int) -> tuple[str, bool]:
        if max_chars <= 0:
            return "", False
        raw = snippet.get("summary") or snippet.get("content") or ""
        if not isinstance(raw, str):
            raw = str(raw or "")
        text = raw.strip()
        if not text:
            return "", False
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars].rstrip() + "…", True

    def _is_table_direct(snippet: Mapping[str, object]) -> bool:
        stage = str(snippet.get("search_stage") or "").strip().lower()
        source = str(snippet.get("source") or "").strip().lower()
        return stage in {"table_direct", "table_blended"} or source == "table_direct"

    def _looks_like_chunk_label(value: object) -> bool:
        text = str(value or "").strip().lower()
        return bool(text and "chunk " in text)

    def _strip_locator_suffix(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+[–-]\s+chunk\s+\d+\s*$", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s+[–-]\s+table\s+preview\s*$", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s+[–-]\s+table\s+\d+\s*$", "", text, flags=re.IGNORECASE).strip()
        return text

    def _document_name(snippet: Mapping[str, object], *, fallback_title: object) -> str:
        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        for candidate in (
            snippet.get("document"),
            snippet.get("document_name"),
            snippet.get("file_name"),
            snippet.get("source_file"),
            diagnostics.get("document_name"),
            diagnostics.get("file_name"),
            fallback_title,
            diagnostics.get("table_title"),
        ):
            text = _strip_locator_suffix(candidate)
            if text and text.lower() not in {"file upload", "table_direct", "table direct"}:
                return text[:180]
        return "Knowledge"

    def _anchor_key(snippet: Mapping[str, object]) -> str:
        """Canonical dedupe key for EvidenceRefs (avoid duplicates across search stages)."""
        diagnostics = snippet.get("source_diagnostics") if isinstance(snippet.get("source_diagnostics"), Mapping) else {}
        table_id = diagnostics.get("table_id")
        # Preserve row_index=0 (0 is valid but falsy).
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        if table_id and row_index is not None:
            return f"table:{table_id}:{row_index}"
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        upload_id = str(snippet.get("upload_id") or "").strip()
        if upload_id:
            return f"upload:{upload_id}"
        return f"fallback:{sha256_hex(json.dumps(dict(snippet), sort_keys=True, default=str)[:800])}"

    def _evidence_group_key(snippet: Mapping[str, object]) -> str:
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        if evidence_group_id:
            return f"evidence:{evidence_group_id}"
        return _anchor_key(snippet)

    def _representation(snippet: Mapping[str, object]) -> str:
        raw = str(snippet.get("representation") or "").strip().lower()
        if raw in {"text", "table", "json"}:
            return raw
        if bool(snippet.get("is_table_chunk")):
            return "table"
        if str(snippet.get("entity_type") or "").strip():
            return "json"
        return "text"

    def _evidence_tokens(text: str) -> set[str]:
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", (text or "").lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return set()
        return {
            token
            for token in normalized.split(" ")
            if token and (len(token) >= 3 or token.isdigit())
        }

    def _evidence_conflict(snippets_for_group: Sequence[Mapping[str, object]]) -> bool:
        if len(snippets_for_group) < 2:
            return False
        by_representation: dict[str, Mapping[str, object]] = {}
        for snippet in snippets_for_group:
            by_representation.setdefault(_representation(snippet), snippet)
        if len(by_representation) < 2:
            return False
        reps = list(by_representation.keys())
        for i, left_rep in enumerate(reps):
            for right_rep in reps[i + 1 :]:
                left = by_representation[left_rep]
                right = by_representation[right_rep]
                left_text = str(left.get("content") or left.get("summary") or "")
                right_text = str(right.get("content") or right.get("summary") or "")
                left_tokens = _evidence_tokens(left_text)
                right_tokens = _evidence_tokens(right_text)
                if min(len(left_tokens), len(right_tokens)) < 4:
                    continue
                union = left_tokens | right_tokens
                if not union:
                    continue
                overlap = len(left_tokens & right_tokens) / len(union)
                if overlap < 0.25:
                    return True
        return False

    def _coerce_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _snippet_row_index(snippet: Mapping[str, object]) -> int | None:
        diagnostics_local = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        row_index_local = diagnostics_local.get("row_index")
        if row_index_local is None:
            row_index_local = diagnostics_local.get("table_row_index")
        return _coerce_int(row_index_local)

    def _snippet_char_estimate(snippet: Mapping[str, object]) -> int:
        diagnostics_local = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        content = snippet.get("content") or ""
        summary = snippet.get("summary") or ""
        char_estimate_local = len(content) if content else len(summary) * 3
        limit_hint = 0
        for key in ("inline_char_limit", "page_char_limit"):
            try:
                limit_hint = max(limit_hint, int(diagnostics_local.get(key) or 0))
            except (TypeError, ValueError):
                continue
        if limit_hint:
            char_estimate_local = max(
                char_estimate_local,
                min(limit_hint, int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)),
            )
        if bool(snippet.get("is_table_chunk")) and _snippet_row_index(snippet) is None:
            row_count = (
                diagnostics_local.get("table_total_rows")
                or diagnostics_local.get("table_row_count")
                or snippet.get("row_count")
            )
            column_count = diagnostics_local.get("table_column_count") or snippet.get("column_count")
            if row_count and column_count:
                try:
                    table_size_estimate = int(row_count) * int(column_count) * 25 + int(row_count) * 32
                    char_estimate_local = max(char_estimate_local, table_size_estimate)
                except (TypeError, ValueError):
                    pass
        return max(0, int(char_estimate_local))

    def _suggest_max_chars_for_estimate(char_estimate: object, *, max_chars_allowed: int) -> int:
        """
        Convert a rough char estimate into a safe `max_chars` suggestion for read_knowledge.

        We intentionally add headroom so the model doesn't under-allocate and get truncated.
        """
        try:
            estimate = int(char_estimate or 0)
        except (TypeError, ValueError):
            estimate = 0
        max_chars_allowed_int = max(1, int(max_chars_allowed))

        if estimate <= 0:
            return min(int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT), max_chars_allowed_int)

        # +20% headroom + a small constant for JSON/table framing overhead.
        suggested = int(estimate * 1.2) + 200
        suggested = max(500, suggested)
        return min(max_chars_allowed_int, suggested)

    def _suggest_table_read_chars(
        *,
        base_suggested: int,
        row_count: object,
        column_count: object,
        char_estimate_local: int,
        row_index: object,
        max_chars_allowed: int,
    ) -> int:
        rows = _coerce_int(row_count) or 0
        cols = _coerce_int(column_count) or 0
        # Prefer explicit column counts when available; otherwise use a conservative default.
        cols = cols if cols > 0 else 5
        row_index_int = _coerce_int(row_index)
        if row_index_int is not None and row_index_int >= 0:
            estimated_row_payload_chars = max(
                int(char_estimate_local),
                int(char_estimate_local + cols * 22 + 180),
            )
            tuned = _suggest_max_chars_for_estimate(
                estimated_row_payload_chars,
                max_chars_allowed=max_chars_allowed,
            )
            return max(500, min(int(max_chars_allowed), int(tuned)))
        if rows <= 0:
            return int(base_suggested)
        estimated_table_chars = max(
            int(char_estimate_local),
            int(rows * max(80, cols * 22) + 400),
        )
        tuned = _suggest_max_chars_for_estimate(
            estimated_table_chars,
            max_chars_allowed=max_chars_allowed,
        )
        if rows >= 25:
            tuned = max(tuned, 1800)
        if rows >= 40:
            tuned = max(tuned, 2800)
        if rows >= 80:
            tuned = max(tuned, 4500)
        return max(500, min(int(max_chars_allowed), int(tuned)))

    # Evidence planner (Phase 1): convert search snippets into lightweight refs.
    # Dedupe by canonical anchors so the model doesn't see the same fact twice, but
    # do not drop entire categories of evidence based on search stage (agentic mode
    # can page/iterate if it needs more).
    raw_snippets: list[Mapping[str, object]] = [
        snippet for snippet in snippets if isinstance(snippet, Mapping)
    ]
    table_direct_present = any(_is_table_direct(snippet) for snippet in raw_snippets)
    candidate_snippets = raw_snippets

    seen_evidence_keys: set[str] = set()
    planned_snippets: list[Mapping[str, object]] = []
    evidence_variants: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    conflict_keys: set[str] = set()
    for snippet in candidate_snippets:
        key = _evidence_group_key(snippet)
        evidence_variants[key].append(snippet)
        if key in seen_evidence_keys:
            continue
        seen_evidence_keys.add(key)
        planned_snippets.append(snippet)
    for key, group_snippets in evidence_variants.items():
        if _evidence_conflict(group_snippets):
            conflict_keys.add(key)

    def _canonical_table_id(raw_table_id: object) -> str:
        value = str(raw_table_id or "").strip()
        if not value:
            return ""
        try:
            return str(uuid.UUID(value))
        except (TypeError, ValueError):
            return ""

    table_row_ref_counts: Counter[str] = Counter()
    table_row_hit_scores: dict[str, dict[int, float]] = defaultdict(dict)
    table_estimated_rows: dict[str, int] = {}
    table_estimated_columns: dict[str, int] = {}
    for snippet in planned_snippets:
        if not bool(snippet.get("is_table_chunk")):
            continue
        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        table_id = diagnostics.get("table_id")
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        if not table_id or row_index is None:
            continue
        canonical_table_id = _canonical_table_id(table_id)
        if not canonical_table_id:
            continue
        row_index_int = _coerce_int(row_index)
        if row_index_int is None or row_index_int < 0:
            continue
        table_row_ref_counts[canonical_table_id] += 1

        score_val = 0.0
        try:
            raw_score = snippet.get("confidence_score")
            if raw_score is not None:
                score_val = float(raw_score)
        except (TypeError, ValueError):
            score_val = 0.0
        prior = table_row_hit_scores[canonical_table_id].get(int(row_index_int))
        if prior is None or score_val > float(prior):
            table_row_hit_scores[canonical_table_id][int(row_index_int)] = float(score_val)

        est_rows = _coerce_int(
            diagnostics.get("table_total_rows")
            or diagnostics.get("table_row_count")
            or snippet.get("row_count")
        )
        if est_rows is not None and est_rows > 0:
            table_estimated_rows[canonical_table_id] = max(
                int(table_estimated_rows.get(canonical_table_id, 0)),
                int(est_rows),
            )
        est_cols = _coerce_int(diagnostics.get("table_column_count") or snippet.get("column_count"))
        if est_cols is not None and est_cols > 0:
            table_estimated_columns[canonical_table_id] = max(
                int(table_estimated_columns.get(canonical_table_id, 0)),
                int(est_cols),
            )
    tables_with_multiple_row_refs = {
        table_id
        for table_id, count in table_row_ref_counts.items()
        if int(count) >= 2
    }

    # Table-row promotion is intentionally disabled on the active path.
    # We keep distinct row refs visible to the model instead of collapsing
    # semantically different row hits into one table-level winner.
    promoted_table_candidates: set[str] = set()
    promote_table_context = False
    promoted_table_ids: set[str] = set()
    precomputed_table_anchor_manifests: dict[str, dict[str, object]] = {}

    def _build_multi_anchor_manifest(table_id: str) -> dict[str, object] | None:
        row_scores = table_row_hit_scores.get(table_id)
        if not isinstance(row_scores, Mapping) or not row_scores:
            return None
        row_items: list[tuple[int, float]] = []
        for raw_index, raw_score in row_scores.items():
            try:
                idx = int(raw_index)
            except (TypeError, ValueError):
                continue
            if idx < 0:
                continue
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            row_items.append((idx, score))
        if not row_items:
            return None
        row_items.sort(key=lambda item: item[0])

        clusters: list[list[tuple[int, float]]] = []
        current: list[tuple[int, float]] = []
        prev_idx: int | None = None
        for idx, score in row_items:
            if prev_idx is not None and current and (idx - prev_idx) >= 4:
                clusters.append(current)
                current = []
            current.append((idx, score))
            prev_idx = idx
        if current:
            clusters.append(current)

        anchor_candidates: list[tuple[float, int]] = []
        for cluster in clusters:
            # Highest score wins; tie-breaker is the lowest row index.
            best_idx, best_score = max(cluster, key=lambda item: (float(item[1]), -int(item[0])))
            anchor_candidates.append((float(best_score), int(best_idx)))
        anchor_candidates.sort(key=lambda item: (-float(item[0]), int(item[1])))

        anchor_rows = [int(idx) for _score, idx in anchor_candidates[:2]]
        if not anchor_rows:
            return None
        matched_row_index = int(anchor_rows[0])

        manifest: dict[str, object] = {
            "ref_id": str(table_id),
            "table_id": str(table_id),
            "matched_row_index": matched_row_index,
            "anchors": [{"row_index": int(idx)} for idx in anchor_rows],
        }
        est_rows = _coerce_int(table_estimated_rows.get(table_id))
        if est_rows is not None and est_rows > 0:
            manifest["estimated_rows"] = int(est_rows)
        est_cols = _coerce_int(table_estimated_columns.get(table_id))
        if est_cols is not None and est_cols > 0:
            manifest["estimated_columns"] = int(est_cols)
        return manifest

    for table_id in promoted_table_candidates:
        manifest = _build_multi_anchor_manifest(str(table_id))
        if isinstance(manifest, Mapping):
            precomputed_table_anchor_manifests[str(table_id)] = dict(manifest)

    promoted_table_anchor_manifests: dict[str, dict[str, object]] = {}

    # Preserve the service-provided RAG order. The retriever has already fused
    # table-direct, lexical, vector, alias, and context signals; re-sorting here
    # by confidence can promote generic anchors over exact table rows.
    for snippet in planned_snippets:

        # Determine type
        is_table = bool(snippet.get("is_table_chunk"))
        content_type = "table" if is_table else "text"
        representation = _representation(snippet)
        evidence_type = str(snippet.get("evidence_type") or "").strip().lower()

        # Get IDs
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        upload_id = str(snippet.get("upload_id") or "")
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        evidence_key = _evidence_group_key(snippet)

        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        char_estimate = _snippet_char_estimate(snippet)

        row_count = diagnostics.get("table_total_rows") or diagnostics.get("table_row_count") or snippet.get("row_count")
        column_count = diagnostics.get("table_column_count") or snippet.get("column_count")
        table_id = diagnostics.get("table_id")
        # Preserve row_index=0 (0 is valid but falsy).
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        canonical_table_id = _canonical_table_id(table_id) if (content_type == "table" and table_id) else ""
        if (
            content_type == "table"
            and canonical_table_id
            and row_index is None
            and canonical_table_id in tables_with_multiple_row_refs
        ):
            continue
        # Strict one-ref-per-promoted-table: skip chunk-only table refs when the table
        # is already eligible for promotion via row hits (we'll emit the table ref instead).
        if (
            content_type == "table"
            and canonical_table_id
            and canonical_table_id in promoted_table_candidates
            and row_index is None
        ):
            continue

        suggested_max_chars = _suggest_max_chars_for_estimate(
            char_estimate,
            max_chars_allowed=int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX),
        )
        if content_type == "table":
            suggested_max_chars = _suggest_table_read_chars(
                base_suggested=suggested_max_chars,
                row_count=row_count,
                column_count=column_count,
                char_estimate_local=char_estimate,
                row_index=row_index,
                max_chars_allowed=int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX),
            )

        # EvidenceRef fields
        entity_name = snippet.get("entity_name")
        title = snippet.get("title") or snippet.get("public_label") or "Untitled"
        label_parts: list[str] = []
        if isinstance(entity_name, str) and entity_name.strip():
            label_parts.append(entity_name.strip())
        if isinstance(title, str) and title.strip():
            label_parts.append(title.strip())
        label = " — ".join(label_parts) if label_parts else str(title or "Untitled")
        if isinstance(label, str) and len(label) > 240:
            label = f"{label[:240].rstrip()}…"

        kind = "text_anchor"
        promote_table_ref = bool(
            content_type == "table"
            and table_id
            and row_index is not None
            and promote_table_context
            and canonical_table_id in promoted_table_candidates
        )
        if content_type == "table":
            kind = "table_row" if (diagnostics.get("table_id") and row_index is not None) else "table_chunk"
            if promote_table_ref:
                kind = "table_chunk"

        coverage_hint: dict[str, object] = {}
        if evidence_group_id:
            coverage_hint["evidence_group_id"] = evidence_group_id
        if content_type == "table":
            if table_id:
                coverage_hint["table_id"] = str(table_id)
            if row_index is not None:
                coverage_hint["row_index"] = row_index
            if row_count is not None:
                coverage_hint["estimated_rows"] = row_count
            if column_count is not None:
                coverage_hint["estimated_columns"] = column_count
            page_number = snippet.get("page_number")
            if page_number is not None:
                try:
                    coverage_hint["page"] = int(page_number)
                except (TypeError, ValueError):
                    pass
        else:
            page_number = snippet.get("page_number")
            if page_number is not None:
                try:
                    coverage_hint["page"] = int(page_number)
                except (TypeError, ValueError):
                    pass
            else:
                chunk_index = snippet.get("chunk_index")
                if isinstance(chunk_index, int):
                    coverage_hint["offset"] = chunk_index

        ref_id = chunk_id
        if promote_table_ref and canonical_table_id:
            promoted_title = ""
            for candidate in (
                diagnostics.get("table_title"),
                diagnostics.get("section_heading"),
            ):
                candidate_text = str(candidate or "").strip()
                if candidate_text:
                    promoted_title = candidate_text
                    break
            if promoted_title:
                label_parts = []
                if isinstance(entity_name, str) and entity_name.strip():
                    normalized_entity_name = entity_name.strip()
                    if normalized_entity_name.lower() != promoted_title.lower():
                        label_parts.append(normalized_entity_name)
                label_parts.append(promoted_title)
                label = " — ".join(label_parts) if label_parts else promoted_title
            elif _looks_like_chunk_label(title):
                order_index = _coerce_int(diagnostics.get("table_order_index"))
                fallback_title = f"Table {order_index}" if order_index is not None and order_index >= 0 else "Table"
                label_parts = []
                if isinstance(entity_name, str) and entity_name.strip():
                    label_parts.append(entity_name.strip())
                label_parts.append(fallback_title)
                label = " — ".join(label_parts)
            if canonical_table_id not in promoted_table_anchor_manifests:
                manifest = precomputed_table_anchor_manifests.get(canonical_table_id)
                if isinstance(manifest, Mapping):
                    promoted_table_anchor_manifests[canonical_table_id] = dict(manifest)
                else:
                    matched_row_index = _coerce_int(row_index)
                    if matched_row_index is not None and matched_row_index >= 0:
                        promoted_table_anchor_manifests[canonical_table_id] = {
                            "ref_id": canonical_table_id,
                            "table_id": canonical_table_id,
                            "matched_row_index": int(matched_row_index),
                        }
            if canonical_table_id in promoted_table_ids:
                continue
            promoted_table_ids.add(canonical_table_id)
            ref_id = canonical_table_id
        if not ref_id:
            continue
        why: list[str] = []
        stage = str(snippet.get("search_stage") or "").strip().lower()
        if stage:
            why.append(f"search_stage:{stage}")
        if _is_table_direct(snippet):
            why.append("match:table_direct")
        if kind.startswith("table"):
            if promote_table_ref:
                why.append("kind:table_context")
            else:
                why.append("kind:table")
        else:
            why.append("kind:text")
        document = _document_name(snippet, fallback_title=title)
        ref_item: dict[str, object] = {
            "id": ref_id,
            "label": label,
            "document": document,
            "kind": kind,
            "read_chars": suggested_max_chars,
        }
        include_preview = False
        preview_cap = preview_chars_cap
        if preview_full_enabled and preview_chars_cap:
            include_preview = True
        elif (not preview_full_enabled) and preview_hybrid_enabled and hybrid_preview_chars_cap:
            # Hybrid mode: only attach previews to the top-ranked refs.
            include_preview = len(refs) < hybrid_preview_max_items
            preview_cap = hybrid_preview_chars_cap
        if include_preview:
            preview, preview_truncated = _preview_text(snippet, max_chars=preview_cap)
            if preview:
                ref_item["preview"] = preview
                if preview_truncated:
                    ref_item["preview_truncated"] = True
                previews_attached += 1
        conflict_flag = bool(diagnostics.get("evidence_conflict")) or (evidence_key in conflict_keys)
        if conflict_flag:
            why.append("evidence_conflict")
        if promote_table_ref and isinstance(coverage_hint, dict):
            matched_row_index: int | None = None
            anchor_row_indexes: list[int] = []
            if canonical_table_id:
                manifest = precomputed_table_anchor_manifests.get(canonical_table_id)
                if isinstance(manifest, Mapping):
                    matched_row_index = _coerce_int(manifest.get("matched_row_index"))
                    raw_anchors = manifest.get("anchors")
                    if isinstance(raw_anchors, list):
                        for entry in raw_anchors:
                            parsed = _coerce_int(entry.get("row_index")) if isinstance(entry, Mapping) else _coerce_int(entry)
                            if parsed is None or parsed < 0:
                                continue
                            anchor_row_indexes.append(int(parsed))
            if matched_row_index is None:
                matched_row_index = _coerce_int(coverage_hint.get("row_index"))
            if matched_row_index is not None:
                coverage_hint["matched_row_index"] = int(matched_row_index)
            if anchor_row_indexes:
                seen_anchor_rows: set[int] = set()
                coverage_hint["anchor_row_indexes"] = [
                    row_index_value
                    for row_index_value in anchor_row_indexes
                    if not (row_index_value in seen_anchor_rows or seen_anchor_rows.add(row_index_value))
                ][:5]
            coverage_hint.pop("row_index", None)
        refs.append(ref_item)

    if context is not None and promoted_table_anchor_manifests:
        table_manifest_cache = getattr(context, "table_row_anchor_manifests", None)
        if isinstance(table_manifest_cache, dict):
            for table_ref_id, manifest in promoted_table_anchor_manifests.items():
                table_manifest_cache[str(table_ref_id)] = dict(manifest)
            while len(table_manifest_cache) > 100:
                oldest_key = next(iter(table_manifest_cache))
                table_manifest_cache.pop(oldest_key, None)

    # Build agentic response
    status = legacy_payload.get("status", "ok")
    total_found = legacy_payload.get("completeness", {}).get("total_found", len(refs))

    agentic_response: dict[str, object] = {
        "tool": "search_knowledge",
        "status": status if refs else "empty",
        "refs": refs,
    }

    # Provide a compact read budget so the LLM can plan max_chars for read_knowledge.
    if refs:
        total_suggested = 0
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            try:
                total_suggested += int(ref.get("read_chars") or 0)
            except (TypeError, ValueError):
                continue
        max_chars_allowed = int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)
        agentic_response["read_budget"] = {
            "suggested_chars": min(total_suggested, max_chars_allowed),
            "max_chars": max_chars_allowed,
        }

    # NOTE: In agentic mode, we may dedupe/collapse a legacy "page" of N snippets
    # down to fewer refs (e.g., 10 legacy snippets -> 3 refs). The legacy
    # completeness metadata refers to snippet paging, not ref paging, which is
    # confusing in debugging. Keep both counts and make `shown` consistent with
    # the actual returned refs.
    completeness_in = legacy_payload.get("completeness")
    completeness_out: dict[str, object] = {}
    if isinstance(completeness_in, Mapping) and completeness_in:
        completeness_out = dict(completeness_in)

    legacy_shown = _coerce_int(completeness_out.get("shown"))
    if legacy_shown is None:
        legacy_shown = len(raw_snippets)
    legacy_already_seen = _coerce_int(completeness_out.get("already_seen"))
    if legacy_already_seen is None:
        legacy_already_seen = 0

    planned_already_seen = legacy_already_seen
    if context is not None:
        planned_already_seen = 0
        for snippet in planned_snippets:
            if not isinstance(snippet, Mapping):
                continue
            chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
            if chunk_id and context.is_chunk_seen(chunk_id):
                planned_already_seen += 1

    completeness_out.setdefault("total_found", total_found)
    # Make agentic completeness consistent with what we actually returned.
    completeness_out["shown"] = len(refs)
    completeness_out["already_seen"] = planned_already_seen
    # Preserve the legacy counts for debug visibility.
    completeness_out["legacy_shown"] = legacy_shown
    completeness_out["legacy_already_seen"] = legacy_already_seen
    completeness_out["planned_unique_snippets"] = len(planned_snippets)
    completeness_out["raw_snippet_count"] = len(raw_snippets)

    if "has_more" not in completeness_out and "has_more" in legacy_payload:
        completeness_out["has_more"] = bool(legacy_payload.get("has_more"))

    if completeness_out:
        agentic_response["completeness"] = completeness_out

    # Pagination hints (cursor-based "next page" support). Keep these in one
    # canonical object so the public tool payload does not duplicate control
    # fields at the top level.
    pagination_out: dict[str, object] = {
        "shown": len(refs),
        "total": total_found,
    }
    if "has_more" in completeness_out:
        pagination_out["has_more"] = bool(completeness_out.get("has_more"))
    next_cursor = legacy_payload.get("next_cursor")
    if isinstance(next_cursor, str) and next_cursor.strip():
        pagination_out["next_cursor"] = (
            _store_search_cursor_handle(context, conversation, next_cursor.strip()) or next_cursor.strip()
        )
    agentic_response["pagination"] = pagination_out

    # Log the conversion for debugging
    structured_log(
        "mcp",
        "search.agentic_conversion",
        {
            "limit_requested": _coerce_int(legacy_payload.get("limit")) or 0,
            "legacy_snippet_count": len(snippets),
            "legacy_completeness_shown": _coerce_int(completeness_in.get("shown")) if isinstance(completeness_in, Mapping) else None,
            "agentic_ref_count": len(refs),
            "total_found": total_found,
            "agentic_read_v2_enabled": agentic_read_v2_enabled,
            "table_direct_present": table_direct_present,
            "table_context_promote_enabled": promote_table_context,
            "table_context_promote_candidates": len(promoted_table_candidates),
            "table_context_promoted_refs": len(promoted_table_ids),
            "planner_dropped": max(0, len(raw_snippets) - len(planned_snippets)),
            "planned_unique_snippets": len(planned_snippets),
            "evidence_conflict_groups": len(conflict_keys),
            "previews_full_enabled": preview_full_enabled,
            "previews_hybrid_enabled": preview_hybrid_enabled,
            "previews_attached_count": previews_attached,
            "table_anchor_manifests_cached": len(promoted_table_anchor_manifests),
            "rank_preserved": True,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )

    return agentic_response
