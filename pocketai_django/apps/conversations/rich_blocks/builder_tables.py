from __future__ import annotations

import copy
from typing import Mapping

from .block_schema import apply_block_ops
from .table_markdown import _parse_markdown_table_divider, _parse_markdown_table_row, _parse_partial_markdown_table_row

TABLE_CELL_LIMIT = 12
TABLE_ROW_LIMIT = 60


class RichBlockTableMixin:
    def _close_active_table(self) -> None:
        self.active_table_id = None
        self.active_table_partial_row_index = None
        self.active_table_partial_cells = []

    def _table_block(self) -> dict[str, object] | None:
        if not self.active_table_id:
            return None
        block = self.blocks_by_id.get(self.active_table_id)
        return block if isinstance(block, dict) else None

    def _build_table_payload(
        self,
        *,
        header_cells: list[str],
        alignments: list[str] | None = None,
        rows: list[list[str]] | None = None,
    ) -> dict[str, object]:
        columns: list[dict[str, object]] = []
        for idx, cell in enumerate(header_cells[:TABLE_CELL_LIMIT]):
            column: dict[str, object] = {
                "key": f"col_{idx}",
                "label": cell or f"Column {idx + 1}",
            }
            if alignments and idx < len(alignments) and alignments[idx] in {"left", "center", "right"}:
                column["align"] = alignments[idx]
            columns.append(column)
        normalized_rows = [{"cells": row[: len(columns)]} for row in (rows or [])[:TABLE_ROW_LIMIT] if row]
        return {
            "columns": columns,
            "rows": normalized_rows,
        }

    def _start_table_from_lines(self, header_line: str, divider_line: str) -> list[dict[str, object]]:
        header_cells = _parse_markdown_table_row(header_line)
        alignments = _parse_markdown_table_divider(divider_line)
        if not header_cells or not alignments:
            return []
        column_count = min(len(header_cells), len(alignments), TABLE_CELL_LIMIT)
        header_cells = header_cells[:column_count]
        alignments = alignments[:column_count]
        self._close_active_table()
        table_block = self._start_block(
            "table",
            self._build_table_payload(header_cells=header_cells, alignments=alignments, rows=[]),
        )
        self.active_table_id = str(table_block.get("block_id") or "")
        return [{"type": "block_start", "payload": {"block": copy.deepcopy(table_block)}}]

    def _append_table_row(self, line: str) -> list[dict[str, object]]:
        table_block = self._table_block()
        if not table_block:
            return []
        payload = table_block.get("payload")
        payload_out: dict[str, object] = dict(payload) if isinstance(payload, Mapping) else {}
        columns = payload_out.get("columns")
        column_count = len(columns) if isinstance(columns, list) else 0
        if column_count <= 1:
            return []
        row_cells = _parse_markdown_table_row(line, expected_columns=column_count)
        if not row_cells:
            return []
        rows_value = payload_out.get("rows")
        rows: list[dict[str, object]] = list(rows_value) if isinstance(rows_value, list) else []
        if len(rows) >= TABLE_ROW_LIMIT:
            return []
        row_payload = {"cells": row_cells}
        rows.append(row_payload)
        payload_out["rows"] = rows
        table_block["payload"] = payload_out
        return [{
            "type": "block_delta",
            "payload": {
                "block_id": str(table_block.get("block_id") or ""),
                "ops": [{"op": "append_table_row", "cells": row_cells}],
            },
        }]

    def _sync_partial_table_row(self, line: str, *, finalize: bool) -> list[dict[str, object]]:
        table_block = self._table_block()
        if not table_block:
            return []
        payload = table_block.get("payload")
        payload_out: dict[str, object] = dict(payload) if isinstance(payload, Mapping) else {}
        columns = payload_out.get("columns")
        column_count = len(columns) if isinstance(columns, list) else 0
        if column_count <= 1:
            return []
        partial_cells = _parse_partial_markdown_table_row(
            line,
            expected_columns=column_count,
            pad_to_expected=finalize,
        )
        if not partial_cells:
            if finalize:
                self.active_table_partial_row_index = None
                self.active_table_partial_cells = []
            return []

        rows_value = payload_out.get("rows")
        rows: list[dict[str, object]] = list(rows_value) if isinstance(rows_value, list) else []
        row_index = self.active_table_partial_row_index
        if row_index is None:
            row_index = len(rows)
            if row_index >= TABLE_ROW_LIMIT:
                return []

        previous_cells = list(self.active_table_partial_cells)
        ops: list[dict[str, object]] = []
        row_complete = bool(finalize and len(partial_cells) >= column_count)
        for cell_index, cell_text in enumerate(partial_cells[:TABLE_CELL_LIMIT]):
            previous_text = previous_cells[cell_index] if cell_index < len(previous_cells) else None
            if previous_text == cell_text:
                continue
            ops.append(
                {
                    "op": "set_table_cell_text",
                    "row_index": row_index,
                    "cell_index": cell_index,
                    "text": cell_text,
                }
            )

        if not ops:
            if row_complete and row_index is not None and previous_cells:
                complete_cell_index = min(len(previous_cells), column_count, TABLE_CELL_LIMIT) - 1
                if complete_cell_index >= 0:
                    ops.append(
                        {
                            "op": "set_table_cell_text",
                            "row_index": row_index,
                            "cell_index": complete_cell_index,
                            "text": previous_cells[complete_cell_index],
                            "row_complete": True,
                        }
                    )
            if ops:
                apply_block_ops(table_block, ops)
                self.active_table_partial_row_index = None
                self.active_table_partial_cells = []
                return [
                    {
                        "type": "block_delta",
                        "payload": {
                            "block_id": str(table_block.get("block_id") or ""),
                            "ops": ops,
                        },
                    }
                ]
            if finalize:
                self.active_table_partial_row_index = None
                self.active_table_partial_cells = []
            return []

        if row_complete:
            ops[-1]["row_complete"] = True

        apply_block_ops(table_block, ops)
        self.active_table_partial_row_index = None if finalize else row_index
        self.active_table_partial_cells = [] if finalize else list(partial_cells[:TABLE_CELL_LIMIT])
        return [
            {
                "type": "block_delta",
                "payload": {
                    "block_id": str(table_block.get("block_id") or ""),
                    "ops": ops,
                },
            }
        ]

    def _flush_pending_table_header(self) -> list[dict[str, object]]:
        if not self.pending_table_header_line:
            return []
        line = self.pending_table_header_line
        self.pending_table_header_line = None
        return self._process_line(line, line_ended=True)
