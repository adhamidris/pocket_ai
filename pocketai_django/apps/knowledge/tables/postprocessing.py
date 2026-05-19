from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.azure_di import AzureDocumentIntelligenceExtractor
from apps.knowledge.ingestion.contracts import IssuePayload, TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.ingestion.signals import (
    _TABLE_NUMERIC_SIGNAL_TOKEN_RE,
    _TABLE_ROW_VALUE_KEYWORD_RE,
    _column_numeric_signal,
)
from apps.knowledge.models import KnowledgeUploadTable
from apps.knowledge.tables.column_roles import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    role_lookup_by_index,
)


class IngestionTablePostprocessingMixin:


    def _table_row_label_set(self, table: TablePayload) -> set[str]:
        labels: list[str] = []
        for row in table.rows:
            if (row.metadata or {}).get("row_type") == "header":
                continue
            first_cell = None
            for cell in row.cells:
                if cell.column_index == 0:
                    first_cell = cell
                    break
            if not first_cell and row.cells:
                first_cell = row.cells[0]
            raw = str(getattr(first_cell, "raw_text", "") or "").strip() if first_cell else str(row.raw_text or "")
            if not raw:
                continue
            normalized = self._normalize_ocr_text(raw).lower()
            normalized = re.sub(r"\s+", " ", normalized).strip()
            if len(normalized) < 3 or not re.search(r"[a-z]", normalized):
                continue
            labels.append(normalized)
            if len(labels) >= self.table_postprocess_row_limit:
                break
        return set(labels)

    def _table_descriptor_continuation_tokens(self) -> set[str]:
        # Generic continuation cues for wrapped descriptor labels.
        return {
            "and", "or", "of", "in", "for", "to", "from", "by", "with",
            "without", "on", "at", "via", "through", "the", "a", "an",
            "company", "group", "department", "division", "exchange", "branch",
            "unit", "office", "region", "country", "city",
        }

    @staticmethod
    def _looks_like_tier_descriptor(value: str) -> bool:
        sample = str(value or "").strip().lower()
        if not sample:
            return False
        if not re.search(r"\d", sample):
            return False
        if sample.startswith("from "):
            return True
        if sample.startswith("up to ") or sample.startswith("upto "):
            return True
        if sample.startswith("less than ") or sample.startswith("more than "):
            return True
        if sample.startswith("below ") or sample.startswith("above "):
            return True
        if sample.endswith("+") or " - " in sample or " – " in sample:
            return True
        return False

    @staticmethod
    def _value_fragment_connector_tokens() -> set[str]:
        return {
            "and", "or", "with", "without", "minimum", "maximum", "max", "min",
            "no", "up", "to", "from", "per", "each", "equivalent",
        }

    @staticmethod
    def _is_complete_value_state(value: str) -> bool:
        sample = str(value or "").strip().lower()
        if not sample:
            return False
        normalized = re.sub(r"\s+", " ", sample)
        return normalized in {
            "free",
            "no fee",
            "no fees",
            "waived",
            "n/a",
            "na",
        }

    @staticmethod
    def _fragment_has_numeric_signal(value: str) -> bool:
        sample = str(value or "").strip().lower()
        if not sample:
            return False
        if re.search(r"\d", sample):
            return True
        if "%" in sample:
            return True
        return bool(
            re.search(
                r"\b(?:usd|eur|gbp|jpy|chf|aud|cad|cny|inr|sar|aed|egp|qar|kwd|omr|bhd|try|zar)\b",
                sample,
            )
        )

    @staticmethod
    def _table_row_has_value_keyword(value: str) -> bool:
        sample = str(value or "").strip()
        if not sample:
            return False
        return bool(_TABLE_ROW_VALUE_KEYWORD_RE.search(sample))

    def _table_row_signal_score(
        self,
        *,
        value_by_label: Mapping[str, str],
        inferred_scope_columns: Sequence[str],
        observed_value_columns: Sequence[str],
        fee_value: str,
    ) -> tuple[float, dict[str, Any]]:
        values = [self._table_cell_text(value) for value in value_by_label.values()]
        values = [value for value in values if value]
        pair_count = len(values)
        numeric_value_count = sum(1 for value in values if _column_numeric_signal(value))
        # Numeric-dense single-cell rows (fee formulas / min-max / multiple values) are meaningful
        # even if only one column is populated. Count numeric-like tokens so such rows don't get
        # suppressed by the row-signal filter.
        currency_signal_token_count = sum(len(_TABLE_NUMERIC_SIGNAL_TOKEN_RE.findall(value)) for value in values)
        keyword_value_count = sum(1 for value in values if self._table_row_has_value_keyword(value))
        scope_column_count = len(list(inferred_scope_columns or []))
        observed_value_column_count = len(list(observed_value_columns or []))
        has_fee_value = bool(self._table_cell_text(fee_value))

        score = 0.0
        if numeric_value_count > 0:
            score += 1.25
        if keyword_value_count > 0:
            score += 0.75
        if has_fee_value:
            score += 1.0
        if scope_column_count > 0:
            score += 0.6
        if observed_value_column_count >= 2:
            score += 0.35
        if pair_count >= 3:
            score += 0.4
        if pair_count >= 5:
            score += 0.3

        diagnostics = {
            "pair_count": int(pair_count),
            "numeric_value_count": int(numeric_value_count),
            "currency_signal_token_count": int(currency_signal_token_count),
            "keyword_value_count": int(keyword_value_count),
            "scope_column_count": int(scope_column_count),
            "observed_value_column_count": int(observed_value_column_count),
            "has_fee_value": has_fee_value,
            "score": round(float(score), 3),
        }
        return score, diagnostics

    def _table_row_structural_context_profile(
        self,
        *,
        value_by_label: Mapping[str, str],
        scope_dimension_columns: Sequence[str],
        fee_value: str,
        row_signal_diag: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_scope_labels = [
            self._table_cell_text(label)
            for label in scope_dimension_columns or []
            if self._table_cell_text(label)
        ]
        normalized_scope_labels = list(dict.fromkeys(normalized_scope_labels))
        normalized_lookup = {
            self._table_cell_text(label): self._table_cell_text(value)
            for label, value in (value_by_label or {}).items()
            if self._table_cell_text(label)
        }

        def _norm(value: str) -> str:
            return re.sub(r"\s+", " ", str(value or "").strip()).lower()

        scope_echo_count = 0
        scope_numeric_count = 0
        for label in normalized_scope_labels:
            value = normalized_lookup.get(label, "")
            if value and _norm(value) == _norm(label):
                scope_echo_count += 1
            if value and _column_numeric_signal(value):
                scope_numeric_count += 1

        scope_label_count = len(normalized_scope_labels)
        scope_echo_ratio = (
            float(scope_echo_count) / float(scope_label_count)
            if scope_label_count > 0
            else 0.0
        )
        has_fee_value = bool(self._table_cell_text(fee_value)) or bool(row_signal_diag.get("has_fee_value"))
        is_structural = bool(
            scope_label_count >= 3
            and scope_echo_count >= max(2, int(math.ceil(scope_label_count * 0.6)))
            and scope_numeric_count == 0
            and not has_fee_value
        )
        return {
            "is_structural_context": is_structural,
            "scope_label_count": int(scope_label_count),
            "scope_echo_count": int(scope_echo_count),
            "scope_numeric_count": int(scope_numeric_count),
            "scope_echo_ratio": round(float(scope_echo_ratio), 4),
        }

    def _table_allows_collapsed_native_text_row_override(self, table: KnowledgeUploadTable) -> bool:
        table_meta = getattr(table, "metadata", None)
        if not isinstance(table_meta, Mapping):
            return False
        promotion_class = str(table_meta.get("promotion_class") or "").strip().lower()
        return promotion_class in {
            "collapsed_factual_table",
            "collapsed_uniform_value_matrix",
            "coherent_native_text_table",
        }

    def _collapsed_native_text_row_override_applies(
        self,
        *,
        table: KnowledgeUploadTable,
        value_by_label: Mapping[str, str],
        row_signal_diag: Mapping[str, Any],
        structural_row_diag: Mapping[str, Any],
    ) -> bool:
        if not self._table_allows_collapsed_native_text_row_override(table):
            return False
        if bool(structural_row_diag.get("is_structural_context")):
            return False
        values = [self._table_cell_text(value) for value in value_by_label.values()]
        values = [value for value in values if value]
        if len(values) != 1:
            return False
        text = values[0]
        word_count = len(re.findall(r"[A-Za-z0-9\u0600-\u06FF%]+", text))
        if word_count < 3 or word_count > 24:
            return False
        numeric_signal_tokens = int(row_signal_diag.get("currency_signal_token_count") or 0)
        keyword_count = int(row_signal_diag.get("keyword_value_count") or 0)
        has_fee_value = bool(row_signal_diag.get("has_fee_value"))
        if numeric_signal_tokens <= 0 and keyword_count <= 0 and not has_fee_value:
            return False
        return True

    def _is_prefix_value_fragment(self, value: str) -> bool:
        sample = self._table_cell_text(value)
        if not sample:
            return False
        if self._is_complete_value_state(sample):
            return False
        if self._fragment_has_numeric_signal(sample):
            return False
        tokens = [tok for tok in re.split(r"[^a-z0-9]+", sample.lower()) if tok]
        if not tokens or len(tokens) > 5:
            return False
        if sample.startswith("("):
            return True
        first = tokens[0]
        return first in self._value_fragment_connector_tokens()

    def _is_suffix_value_fragment(self, value: str) -> bool:
        sample = self._table_cell_text(value)
        if not sample:
            return False
        tokens = [tok for tok in re.split(r"[^a-z0-9]+", sample.lower()) if tok]
        if not tokens or len(tokens) > 8:
            return False
        if self._fragment_has_numeric_signal(sample) and len(tokens) > 4:
            return False
        return tokens[0] in self._value_fragment_connector_tokens()

    def _numeric_fragments_compatible(self, values: Sequence[str]) -> bool:
        normalized = [self._table_cell_text(v).lower() for v in values if self._table_cell_text(v)]
        if not normalized:
            return False
        uniq = list(dict.fromkeys(normalized))
        if len(uniq) <= 1:
            return True
        # Allow near-equivalent numeric fragments (one containing the other).
        for left in uniq:
            for right in uniq:
                if left == right:
                    continue
                if left in right or right in left:
                    continue
                return False
        return True

    def _row_cell_lookup(self, row: TableRowPayload) -> dict[int, TableCellPayload]:
        lookup: dict[int, TableCellPayload] = {}
        for cell in row.cells or []:
            try:
                idx = int(cell.column_index)
            except (TypeError, ValueError):
                continue
            lookup[idx] = cell
        return lookup

    def _rewrite_row_cell_text(
        self,
        *,
        row: TableRowPayload,
        column_index: int,
        new_value: str,
        metadata_patch: Mapping[str, Any] | None = None,
    ) -> TableRowPayload:
        new_cells: list[TableCellPayload] = []
        changed = False
        for cell in row.cells or []:
            if int(getattr(cell, "column_index", -1)) != column_index:
                new_cells.append(cell)
                continue
            changed = True
            cell_meta = dict(cell.metadata or {})
            if metadata_patch:
                cell_meta.update(dict(metadata_patch))
            new_cells.append(
                TableCellPayload(
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    column_key=cell.column_key,
                    raw_text=new_value,
                    normalized_value=cell.normalized_value,
                    bbox=cell.bbox,
                    confidence=cell.confidence,
                    metadata=cell_meta,
                )
            )
        if not changed:
            return row
        ordered_cells = sorted(
            new_cells,
            key=lambda c: int(getattr(c, "column_index", 0)),
        )
        row_text = " | ".join(
            str(cell.raw_text or "").strip()
            for cell in ordered_cells
            if str(cell.raw_text or "").strip()
        ).strip()
        row_meta = dict(row.metadata or {})
        return TableRowPayload(
            row_index=row.row_index,
            page_number=row.page_number,
            bbox=row.bbox,
            raw_text=row_text or row.raw_text,
            metadata=row_meta,
            cells=new_cells,
        )

    def _table_contextual_column_indices(self, table: TablePayload) -> set[int]:
        column_map = self._table_column_map_for_payload(table)
        if not column_map:
            return {0}
        role_payloads = self._infer_table_column_roles(
            table_rows=list(table.rows or []),
            column_map=column_map,
            cached_roles=(table.data_dictionary or {}).get("column_roles"),
        )
        role_lookup = role_lookup_by_index(role_payloads)
        contextual = {
            int(idx)
            for idx, payload in role_lookup.items()
            if str(payload.get("role") or "").strip() in {COLUMN_ROLE_DESCRIPTOR, COLUMN_ROLE_QUALIFIER}
        }
        if contextual:
            return contextual
        # Conservative fallback: only the leading columns are likely to carry
        # row labels. Value columns can legitimately use row spans too, and
        # duplicating those would change the facts.
        return {0}

    def _replace_or_add_row_cell(
        self,
        *,
        row: TableRowPayload,
        column_index: int,
        column_key: str,
        new_value: str,
        metadata_patch: Mapping[str, Any],
    ) -> TableRowPayload:
        new_cells: list[TableCellPayload] = []
        replaced = False
        for cell in row.cells or []:
            try:
                idx = int(cell.column_index)
            except (TypeError, ValueError):
                idx = -1
            if idx != column_index:
                new_cells.append(cell)
                continue
            replaced = True
            cell_meta = dict(cell.metadata or {})
            cell_meta.update(dict(metadata_patch or {}))
            new_cells.append(
                TableCellPayload(
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    column_key=cell.column_key or column_key,
                    raw_text=new_value,
                    normalized_value=cell.normalized_value,
                    bbox=cell.bbox,
                    confidence=cell.confidence,
                    metadata=cell_meta,
                )
            )
        if not replaced:
            new_cells.append(
                TableCellPayload(
                    row_index=row.row_index,
                    column_index=column_index,
                    column_key=column_key,
                    raw_text=new_value,
                    normalized_value={},
                    bbox={},
                    confidence=None,
                    metadata=dict(metadata_patch or {}),
                )
            )
        ordered_cells = sorted(
            new_cells,
            key=lambda c: int(getattr(c, "column_index", 0) or 0),
        )
        row_text = " | ".join(
            str(cell.raw_text or "").strip()
            for cell in ordered_cells
            if str(cell.raw_text or "").strip()
        ).strip()
        row_meta = dict(row.metadata or {})
        row_meta["merged_parent_labels_carried_down"] = int(row_meta.get("merged_parent_labels_carried_down") or 0) + 1
        return TableRowPayload(
            row_index=row.row_index,
            page_number=row.page_number,
            bbox=row.bbox,
            raw_text=row_text or row.raw_text,
            metadata=row_meta,
            cells=ordered_cells,
        )

    def _carry_down_merged_parent_labels(self, table: TablePayload) -> tuple[TablePayload, int]:
        rows = sorted(list(table.rows or []), key=lambda row: int(getattr(row, "row_index", 0)))
        if len(rows) < 2:
            return table, 0
        contextual_indices = self._table_contextual_column_indices(table)
        if not contextual_indices:
            return table, 0

        schema = [str(col or "").strip() or f"column_{idx + 1}" for idx, col in enumerate(table.column_schema or [])]
        updated_rows: dict[int, TableRowPayload] = {int(row.row_index): row for row in rows}
        filled_cells = 0

        for position, row in enumerate(rows):
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            if str(row_meta.get("row_type") or "").strip().lower() in {"header", "section_header"}:
                continue
            effective_row = updated_rows.get(int(row.row_index), row)
            for col_idx, cell in sorted(self._row_cell_lookup(effective_row).items()):
                if col_idx not in contextual_indices:
                    continue
                parent_value = self._table_cell_text(cell.raw_text or "")
                if not parent_value:
                    continue
                try:
                    row_span = int((cell.metadata or {}).get("row_span") or 1)
                except (TypeError, ValueError):
                    row_span = 1
                if row_span <= 1:
                    continue
                for offset in range(1, row_span):
                    target_position = position + offset
                    if target_position >= len(rows):
                        break
                    target_original = rows[target_position]
                    target_row = updated_rows.get(int(target_original.row_index), target_original)
                    target_meta = target_row.metadata if isinstance(target_row.metadata, Mapping) else {}
                    if str(target_meta.get("row_type") or "").strip().lower() in {"header", "section_header"}:
                        continue
                    target_lookup = self._row_cell_lookup(target_row)
                    target_cell = target_lookup.get(col_idx)
                    if target_cell is not None and self._table_cell_text(target_cell.raw_text or ""):
                        continue
                    column_key = schema[col_idx] if 0 <= col_idx < len(schema) else f"column_{col_idx + 1}"
                    target_row = self._replace_or_add_row_cell(
                        row=target_row,
                        column_index=col_idx,
                        column_key=column_key,
                        new_value=parent_value,
                        metadata_patch={
                            "merged_parent_label_carried_down": True,
                            "merged_parent_source_row": int(row.row_index),
                            "merged_parent_source_column": int(col_idx),
                            "merged_parent_source_row_span": int(row_span),
                        },
                    )
                    updated_rows[int(target_row.row_index)] = target_row
                    filled_cells += 1

        if filled_cells <= 0:
            return table, 0

        table_meta = dict(table.metadata or {})
        table_meta["merged_parent_label_cells_carried_down"] = int(
            table_meta.get("merged_parent_label_cells_carried_down") or 0
        ) + filled_cells
        rebuilt_rows = [updated_rows.get(int(row.row_index), row) for row in rows]
        return (
            TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=table_meta,
                rows=rebuilt_rows,
            ),
            filled_cells,
        )

    def _stitch_table_row_continuations(self, table: TablePayload) -> tuple[TablePayload, int]:
        rows = list(table.rows or [])
        if len(rows) < 3:
            return table, 0

        column_map = self._table_column_map_for_payload(table)
        if not column_map:
            return table, 0
        role_payloads = self._infer_table_column_roles(
            table_rows=rows,
            column_map=column_map,
            cached_roles=(table.data_dictionary or {}).get("column_roles"),
        )
        role_lookup = role_lookup_by_index(role_payloads)
        descriptor_indices = sorted(
            idx
            for idx, payload in role_lookup.items()
            if str(payload.get("role") or "") == COLUMN_ROLE_DESCRIPTOR
        )
        descriptor_idx = descriptor_indices[0] if descriptor_indices else 0

        row_order = sorted(rows, key=lambda row: int(getattr(row, "row_index", 0)))
        data_rows = [
            row
            for row in row_order
            if str((row.metadata or {}).get("row_type") or "").strip().lower() != "header"
        ]
        if len(data_rows) < 2:
            return table, 0

        continuation_tokens = self._table_descriptor_continuation_tokens()
        row_by_index = {int(row.row_index): row for row in rows}
        updated_rows = dict(row_by_index)
        stitched_pairs = 0

        for pos in range(1, len(data_rows)):
            prev_row = updated_rows.get(int(data_rows[pos - 1].row_index)) or data_rows[pos - 1]
            curr_row = updated_rows.get(int(data_rows[pos].row_index)) or data_rows[pos]

            prev_cells = self._row_cell_lookup(prev_row)
            curr_cells = self._row_cell_lookup(curr_row)
            prev_desc_cell = prev_cells.get(descriptor_idx)
            curr_desc_cell = curr_cells.get(descriptor_idx)
            if prev_desc_cell is None or curr_desc_cell is None:
                continue

            prev_desc = self._table_cell_text(prev_desc_cell.raw_text or "")
            curr_desc = self._table_cell_text(curr_desc_cell.raw_text or "")
            if not prev_desc or not curr_desc:
                continue
            if prev_desc.lower() == curr_desc.lower():
                continue

            try:
                prev_span = int((prev_desc_cell.metadata or {}).get("row_span") or 1)
                curr_span = int((curr_desc_cell.metadata or {}).get("row_span") or 1)
            except (TypeError, ValueError):
                prev_span = 1
                curr_span = 1
            if prev_span > 1 or curr_span > 1:
                continue

            prev_words = prev_desc.split()
            curr_words = curr_desc.split()
            if len(prev_words) < 3 or len(curr_words) > 8:
                continue
            if re.search(r"[.!?:;]\s*$", prev_desc):
                continue
            if self._looks_like_tier_descriptor(prev_desc) and self._looks_like_tier_descriptor(curr_desc):
                continue

            first_curr_token = re.sub(r"[^a-z0-9]+", "", curr_words[0].lower())
            continuation_cue = bool(first_curr_token and first_curr_token in continuation_tokens)
            if not continuation_cue and not curr_desc[:1].islower():
                continue

            prev_non_descriptor = {
                idx for idx, cell in prev_cells.items()
                if idx != descriptor_idx and str(cell.raw_text or "").strip()
            }
            curr_non_descriptor = {
                idx for idx, cell in curr_cells.items()
                if idx != descriptor_idx and str(cell.raw_text or "").strip()
            }
            if not prev_non_descriptor or not curr_non_descriptor:
                continue
            if not (prev_non_descriptor & curr_non_descriptor):
                continue

            combined = self._table_cell_text(f"{prev_desc} {curr_desc}")
            if not combined or len(combined) <= max(len(prev_desc), len(curr_desc)):
                continue
            if len(combined) > 220:
                continue

            row_patch = {
                "descriptor_continuation_stitched": True,
                "descriptor_continuation_anchor_row": int(prev_row.row_index),
            }
            updated_prev = self._rewrite_row_cell_text(
                row=prev_row,
                column_index=descriptor_idx,
                new_value=combined,
                metadata_patch=row_patch,
            )
            updated_curr = self._rewrite_row_cell_text(
                row=curr_row,
                column_index=descriptor_idx,
                new_value=combined,
                metadata_patch=row_patch,
            )
            prev_meta = dict(updated_prev.metadata or {})
            curr_meta = dict(updated_curr.metadata or {})
            prev_meta.update(row_patch)
            curr_meta.update(row_patch)
            updated_prev = TableRowPayload(
                row_index=updated_prev.row_index,
                page_number=updated_prev.page_number,
                bbox=updated_prev.bbox,
                raw_text=updated_prev.raw_text,
                metadata=prev_meta,
                cells=updated_prev.cells,
            )
            updated_curr = TableRowPayload(
                row_index=updated_curr.row_index,
                page_number=updated_curr.page_number,
                bbox=updated_curr.bbox,
                raw_text=updated_curr.raw_text,
                metadata=curr_meta,
                cells=updated_curr.cells,
            )

            updated_rows[int(updated_prev.row_index)] = updated_prev
            updated_rows[int(updated_curr.row_index)] = updated_curr
            stitched_pairs += 1

        if stitched_pairs <= 0:
            return table, 0

        rebuilt_rows = [
            updated_rows.get(int(row.row_index), row)
            for row in row_order
        ]
        table_meta = dict(table.metadata or {})
        table_meta["row_continuation_stitched_pairs"] = stitched_pairs
        return (
            TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=table_meta,
                rows=rebuilt_rows,
            ),
            stitched_pairs,
        )

    def _stitch_scope_value_fragments(self, table: TablePayload) -> tuple[TablePayload, int]:
        rows = list(table.rows or [])
        if len(rows) < 2:
            return table, 0

        column_map = self._table_column_map_for_payload(table)
        if not column_map:
            return table, 0
        role_payloads = self._infer_table_column_roles(
            table_rows=rows,
            column_map=column_map,
            cached_roles=(table.data_dictionary or {}).get("column_roles"),
        )
        role_lookup = role_lookup_by_index(role_payloads)

        scope_indices: set[int] = set()
        for idx, payload in role_lookup.items():
            role = str(payload.get("role") or "").strip()
            if role == COLUMN_ROLE_SCOPE_DIMENSION:
                scope_indices.add(int(idx))
                continue
            # In noisy OCR tables, true scope columns can be temporarily
            # classified as qualifier; include non-contextual candidates and
            # let row-level fragment gates decide whether stitching applies.
            if role in {COLUMN_ROLE_QUALIFIER, ""}:
                scope_indices.add(int(idx))
        scope_order = sorted(scope_indices)
        if len(scope_order) < 2:
            return table, 0

        row_order = sorted(rows, key=lambda row: int(getattr(row, "row_index", 0)))
        updated_rows = {int(row.row_index): row for row in row_order}
        stitched_cells = 0

        for row in row_order:
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            if str(row_meta.get("row_type") or "").strip().lower() == "header":
                continue
            effective_row = updated_rows.get(int(row.row_index), row)
            lookup = self._row_cell_lookup(effective_row)

            fragments: list[tuple[int, str]] = []
            numeric_values: list[str] = []
            prefix_indices: set[int] = set()
            suffix_indices: set[int] = set()
            for idx in scope_order:
                cell = lookup.get(idx)
                value = self._table_cell_text(cell.raw_text if cell else "")
                if not value:
                    continue
                fragments.append((idx, value))
                if self._fragment_has_numeric_signal(value):
                    numeric_values.append(value)
                if self._is_prefix_value_fragment(value):
                    prefix_indices.add(idx)
                if self._is_suffix_value_fragment(value):
                    suffix_indices.add(idx)

            if len(fragments) < 2:
                continue
            if not prefix_indices or not numeric_values:
                continue
            if not self._numeric_fragments_compatible(numeric_values):
                continue

            ordered_unique_parts: list[str] = []
            seen_parts: set[str] = set()
            for _idx, value in fragments:
                key = value.lower()
                if key in seen_parts:
                    continue
                ordered_unique_parts.append(value)
                seen_parts.add(key)
            if len(ordered_unique_parts) < 2:
                continue
            merged_value = self._table_cell_text(" ".join(ordered_unique_parts))
            if not merged_value:
                continue
            if len(merged_value) > 260:
                continue

            replace_indices = set(prefix_indices) | set(suffix_indices)
            # When multiple prefix fragments exist, propagate full value across
            # all visible scope fragments in the row for consistent semantics.
            if len(prefix_indices) >= 2:
                replace_indices.update(idx for idx, _value in fragments)
            if not replace_indices:
                continue

            sorted_replace = sorted(replace_indices)
            anchor_idx = sorted_replace[0] if sorted_replace else None
            contiguous = bool(
                sorted_replace
                and all((sorted_replace[i] - sorted_replace[i - 1]) == 1 for i in range(1, len(sorted_replace)))
            )
            anchor_span = len(sorted_replace) if contiguous and len(sorted_replace) > 1 else 1

            updated_row = effective_row
            row_rewrites = 0
            for idx in sorted(replace_indices):
                existing_cell = self._row_cell_lookup(updated_row).get(idx)
                if existing_cell is None:
                    continue
                existing_value = self._table_cell_text(existing_cell.raw_text or "")
                if not existing_value or existing_value.lower() == merged_value.lower():
                    continue
                updated_row = self._rewrite_row_cell_text(
                    row=updated_row,
                    column_index=idx,
                    new_value=merged_value,
                    metadata_patch={
                        "value_fragment_stitched": True,
                        "value_fragment_stitch_source_row": int(row.row_index),
                        # Reset stale extraction spans on rewritten fragments.
                        # If rewritten indices are contiguous, encode one explicit
                        # span anchor so scope refresh can infer the full range.
                        "column_span": anchor_span if idx == anchor_idx else 1,
                    },
                )
                row_rewrites += 1

            if row_rewrites > 0:
                new_meta = dict(updated_row.metadata or {})
                new_meta.update(
                    {
                        "value_fragment_stitched": True,
                        "value_fragment_stitch_cells": row_rewrites,
                    }
                )
                updated_rows[int(updated_row.row_index)] = TableRowPayload(
                    row_index=updated_row.row_index,
                    page_number=updated_row.page_number,
                    bbox=updated_row.bbox,
                    raw_text=updated_row.raw_text,
                    metadata=new_meta,
                    cells=updated_row.cells,
                )
                stitched_cells += row_rewrites

        if stitched_cells <= 0:
            return table, 0

        rebuilt_rows = [updated_rows.get(int(row.row_index), row) for row in row_order]
        table_meta = dict(table.metadata or {})
        table_meta["value_fragment_stitched_cells"] = stitched_cells
        return (
            TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=table_meta,
                rows=rebuilt_rows,
            ),
            stitched_cells,
        )

    def _refresh_table_scope_annotations(self, table: TablePayload) -> TablePayload:
        if not table.rows or len(table.column_schema or []) < 3:
            return table
        header_rows: set[int] = {
            int(row.row_index)
            for row in (table.rows or [])
            if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"
        }
        annotator = AzureDocumentIntelligenceExtractor(endpoint=None, key=None)
        annotated_rows = annotator._annotate_row_applicability(
            table_rows=table.rows or [],
            column_schema=table.column_schema or [],
            header_rows=header_rows,
        )
        table_meta = dict(table.metadata or {})
        table_meta["scope_postprocess_refreshed"] = True
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=table.column_schema,
            data_dictionary=table.data_dictionary,
            metadata=table_meta,
            rows=annotated_rows,
        )

    def _build_table_profile(self, tables: Sequence[TablePayload]) -> dict[str, Any] | None:
        if not tables:
            return None
        column_limit = max(16, int(getattr(settings, "RAG_TABLE_PROFILE_COLUMN_LIMIT", 256)))
        token_limit = max(32, int(getattr(settings, "RAG_TABLE_PROFILE_ROW_LABEL_TOKEN_LIMIT", 800)))
        label_limit = max(8, int(getattr(settings, "RAG_TABLE_PROFILE_ROW_LABEL_SAMPLE_LIMIT", 60)))

        columns: set[str] = set()
        row_label_tokens: set[str] = set()
        row_label_samples: list[str] = []
        token_split = re.compile(r"[^\w]+", flags=re.UNICODE)

        for table in tables:
            schema = table.column_schema or []
            for col in schema:
                lowered = str(col or "").strip().lower()
                if lowered:
                    columns.add(lowered)
                    if len(columns) >= column_limit:
                        break
            if len(columns) >= column_limit:
                columns = set(sorted(columns)[:column_limit])

            labels = self._table_row_label_set(table)
            for label in labels:
                if label and len(row_label_samples) < label_limit:
                    row_label_samples.append(label)
                for token in token_split.split(label):
                    cleaned = token.strip().lower()
                    if not cleaned or cleaned.isdigit():
                        continue
                    row_label_tokens.add(cleaned)
                    if len(row_label_tokens) >= token_limit:
                        break
                if len(row_label_tokens) >= token_limit:
                    break

            if len(columns) >= column_limit and len(row_label_tokens) >= token_limit:
                break

        profile: dict[str, Any] = {
            "version": 1,
            "generated_at": timezone.now().isoformat(),
            "table_count": len(tables),
            "columns": sorted(columns)[:column_limit],
            "row_label_tokens": sorted(row_label_tokens)[:token_limit],
            "row_label_samples": row_label_samples[:label_limit],
        }
        return profile

    def _table_schema_is_generic(self, table: TablePayload) -> bool:
        if not table.column_schema:
            return True
        assessment = self._assess_table_quality(table)
        signals = assessment.get("signals") or {}
        if signals.get("nonsense_columns"):
            return True
        header_confidence = float(signals.get("header_confidence") or 0.0)
        if header_confidence < 0.3:
            return True
        return False

    def _table_dedupe_heading_key(self, table: TablePayload) -> str:
        meta = table.metadata if isinstance(table.metadata, Mapping) else {}
        candidates = [
            str(table.section_heading or ""),
            str(meta.get("derived_section_heading") or ""),
            str(table.title or ""),
        ]
        for candidate in candidates:
            normalized = self._normalize_evidence_phrase(candidate)
            if normalized:
                return normalized
        return ""

    def _table_region_overlap_ratio(self, left: TablePayload, right: TablePayload) -> float:
        left_bbox = self._normalize_bbox(left.bbox)
        right_bbox = self._normalize_bbox(right.bbox)
        if not left_bbox or not right_bbox:
            return 0.0

        ix0 = max(left_bbox["x0"], right_bbox["x0"])
        iy0 = max(left_bbox["y0"], right_bbox["y0"])
        ix1 = min(left_bbox["x1"], right_bbox["x1"])
        iy1 = min(left_bbox["y1"], right_bbox["y1"])
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0

        intersection = (ix1 - ix0) * (iy1 - iy0)
        union = self._bbox_area(left_bbox) + self._bbox_area(right_bbox) - intersection
        if union <= 0.0:
            return 0.0
        return intersection / union

    def _tables_are_duplicate_candidates(
        self,
        left: TablePayload,
        right: TablePayload,
        *,
        left_labels: set[str],
        right_labels: set[str],
    ) -> tuple[bool, dict[str, Any]]:
        overlap = 0.0
        if left_labels and right_labels:
            overlap = len(left_labels & right_labels) / max(1, len(left_labels | right_labels))
        if overlap < self.table_dedupe_min_overlap:
            return False, {"overlap": round(overlap, 3), "reason": "label_overlap_below_threshold"}

        left_heading = self._table_dedupe_heading_key(left)
        right_heading = self._table_dedupe_heading_key(right)
        headings_match = bool(left_heading and right_heading and left_heading == right_heading)
        region_overlap = self._table_region_overlap_ratio(left, right)
        same_region = region_overlap >= 0.3

        is_duplicate = headings_match or same_region
        reason = "heading_match" if headings_match else ("region_overlap" if same_region else "distinct_region_or_heading")
        return is_duplicate, {
            "overlap": round(overlap, 3),
            "region_overlap": round(region_overlap, 3),
            "headings_match": headings_match,
            "reason": reason,
        }

    def _apply_schema_override(
        self,
        table: TablePayload,
        schema: Sequence[str],
        *,
        inferred_from: int | None = None,
    ) -> TablePayload:
        normalized_schema = [str(col or "").strip() or f"column_{idx+1}" for idx, col in enumerate(schema)]
        new_rows: list[TableRowPayload] = []
        for row in table.rows:
            row_meta = dict(row.metadata or {})
            if row_meta.get("row_type") == "header":
                row_meta["row_type"] = "data"
                row_meta["header_inferred"] = True
            new_cells: list[TableCellPayload] = []
            for cell in row.cells:
                col_key = normalized_schema[cell.column_index] if cell.column_index < len(normalized_schema) else f"column_{cell.column_index+1}"
                new_cells.append(
                    TableCellPayload(
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                        column_key=col_key,
                        raw_text=cell.raw_text,
                        normalized_value=cell.normalized_value,
                        bbox=cell.bbox,
                        confidence=cell.confidence,
                        metadata=cell.metadata,
                    )
                )
            new_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row_meta,
                    cells=new_cells,
                )
            )
        table_meta = dict(table.metadata or {})
        table_meta["header_inferred"] = True
        if inferred_from is not None:
            table_meta["header_inferred_from"] = inferred_from
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=normalized_schema,
            data_dictionary=table.data_dictionary,
            metadata=table_meta,
            rows=new_rows,
        )

    def _postprocess_tables(
        self,
        tables: Sequence[TablePayload],
    ) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        if not tables:
            return [], [], {}
        grouped: dict[int | None, list[TablePayload]] = {}
        for table in tables:
            grouped.setdefault(table.page_number, []).append(table)
        issues: list[IssuePayload] = []
        meta = {
            "deduped_tables": 0,
            "header_inferred": 0,
            "embedded_header_rows_promoted": 0,
            "merged_parent_label_cells_carried_down": 0,
            "row_continuation_stitched_pairs": 0,
            "value_fragment_stitched_cells": 0,
            "scope_refreshed_tables": 0,
        }
        processed: list[TablePayload] = []

        def _jaccard(a: set[str], b: set[str]) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / max(1, len(a | b))

        for page_number, page_tables in grouped.items():
            page_tables = sorted(page_tables, key=lambda t: t.order_index)
            promoted_tables: list[TablePayload] = []
            for table in page_tables:
                promoted_table, promoted_headers = self._promote_embedded_header_rows(table)
                if promoted_headers > 0:
                    meta["embedded_header_rows_promoted"] += promoted_headers
                    issues.append(
                        IssuePayload(
                            code="table_embedded_header_promoted",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Header-like table row promoted to schema/header metadata before indexing.",
                            page_number=page_number,
                            table_order_index=table.order_index,
                            details={
                                "promoted_rows": promoted_headers,
                                "source_row": (promoted_table.metadata or {}).get("embedded_header_source_row"),
                                "renamed_columns": (promoted_table.metadata or {}).get("embedded_header_renamed_columns"),
                            },
                        )
                    )
                promoted_tables.append(promoted_table)
            page_tables = promoted_tables

            carried_tables: list[TablePayload] = []
            for table in page_tables:
                carried_table, carried_cells = self._carry_down_merged_parent_labels(table)
                if carried_cells > 0:
                    meta["merged_parent_label_cells_carried_down"] += carried_cells
                    issues.append(
                        IssuePayload(
                            code="table_merged_parent_labels_carried_down",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Merged parent row labels were carried down into child rows.",
                            page_number=page_number,
                            table_order_index=table.order_index,
                            details={"carried_cells": carried_cells},
                        )
                    )
                carried_tables.append(carried_table)
            page_tables = carried_tables

            stitched_tables: list[TablePayload] = []
            for table in page_tables:
                stitched_table, stitched_pairs = self._stitch_table_row_continuations(table)
                if stitched_pairs > 0:
                    meta["row_continuation_stitched_pairs"] += stitched_pairs
                    issues.append(
                        IssuePayload(
                            code="table_row_continuation_stitched",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Descriptor continuation text stitched across adjacent rows.",
                            page_number=page_number,
                            table_order_index=table.order_index,
                            details={"stitched_pairs": stitched_pairs},
                        )
                    )
                stitched_tables.append(stitched_table)
            page_tables = stitched_tables

            value_stitched_tables: list[TablePayload] = []
            for table in page_tables:
                value_table, stitched_cells = self._stitch_scope_value_fragments(table)
                if stitched_cells > 0:
                    meta["value_fragment_stitched_cells"] += stitched_cells
                value_stitched_tables.append(value_table)
            page_tables = value_stitched_tables

            if self.table_dedupe_enabled:
                deduped: list[TablePayload] = []
                dedupe_labels: list[set[str]] = []
                dedupe_quality: list[float] = []
                for table in page_tables:
                    labels = self._table_row_label_set(table)
                    quality = float(self._assess_table_quality(table).get("quality_score") or 0.0)
                    merged = False
                    if labels:
                        for idx, existing in enumerate(deduped):
                            if len(existing.column_schema) != len(table.column_schema):
                                continue
                            is_duplicate, dedupe_diag = self._tables_are_duplicate_candidates(
                                existing,
                                table,
                                left_labels=dedupe_labels[idx],
                                right_labels=labels,
                            )
                            if is_duplicate:
                                meta["deduped_tables"] += 1
                                if quality > dedupe_quality[idx]:
                                    deduped[idx] = table
                                    dedupe_labels[idx] = labels
                                    dedupe_quality[idx] = quality
                                issues.append(
                                    IssuePayload(
                                        code="table_duplicate_suppressed",
                                        severity=KnowledgeIssueSeverity.INFO.value,
                                        description="Duplicate table suppressed based on row-label overlap.",
                                        page_number=page_number,
                                        table_order_index=table.order_index,
                                        details=dedupe_diag,
                                    )
                                )
                                merged = True
                                break
                    if not merged:
                        deduped.append(table)
                        dedupe_labels.append(labels)
                        dedupe_quality.append(quality)
                page_tables = deduped

            prev_schema: list[str] | None = None
            prev_labels: set[str] | None = None
            prev_order_index: int | None = None
            for table in page_tables:
                labels = self._table_row_label_set(table)
                is_generic = self._table_schema_is_generic(table)
                if (
                    self.table_header_propagation_enabled
                    and is_generic
                    and prev_schema
                    and labels
                    and prev_labels
                    and len(prev_schema) == len(table.column_schema)
                ):
                    overlap = _jaccard(labels, prev_labels)
                    if overlap >= self.table_header_propagation_min_overlap:
                        table = self._apply_schema_override(table, prev_schema, inferred_from=prev_order_index)
                        meta["header_inferred"] += 1
                        issues.append(
                            IssuePayload(
                                code="table_header_inferred",
                                severity=KnowledgeIssueSeverity.INFO.value,
                                description="Table headers inferred from adjacent table on same page.",
                                page_number=page_number,
                                table_order_index=table.order_index,
                                details={"overlap": round(overlap, 3), "source_table": prev_order_index},
                            )
                        )
                if not is_generic and labels:
                    prev_schema = list(table.column_schema)
                    prev_labels = labels
                    prev_order_index = table.order_index
                table = self._refresh_table_scope_annotations(table)
                meta["scope_refreshed_tables"] += 1
                processed.append(table)

        return processed, issues, meta
