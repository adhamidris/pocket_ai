from __future__ import annotations

from typing import Any, Mapping

from apps.knowledge.ingestion.contracts import TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.tables.semantic.column_roles import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_QUALIFIER,
    role_lookup_by_index,
)


class IngestionTablePostprocessRowsMixin:

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
