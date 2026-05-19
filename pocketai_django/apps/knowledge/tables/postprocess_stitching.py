from __future__ import annotations

import re
from typing import Any, Mapping

from apps.knowledge.ingestion.azure_di import AzureDocumentIntelligenceExtractor
from apps.knowledge.ingestion.contracts import TablePayload, TableRowPayload
from apps.knowledge.tables.column_roles import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    role_lookup_by_index,
)


class IngestionTablePostprocessStitchingMixin:

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
