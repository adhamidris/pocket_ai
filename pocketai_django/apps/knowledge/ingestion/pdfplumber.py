from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import (
    IssuePayload,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
from apps.knowledge.tables.detection import TableDetector

try:  # pragma: no cover - optional dependency
    import pdfplumber
except ImportError:  # pragma: no cover - optional dependency
    pdfplumber = None  # type: ignore


# PdfPlumber table extraction (optional dependency)
PDFPLUMBER_DEFAULT_TABLE_SETTINGS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "lines",
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
        },
    ),
    (
        "lines_text",
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "min_words_horizontal": 1,
            "text_y_tolerance": 2,
        },
    ),
    (
        "text_lines",
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "min_words_vertical": 1,
            "text_x_tolerance": 2,
        },
    ),
    (
        "text",
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
        },
    ),
)


class PdfPlumberTableExtractor:
    def __init__(self, *, table_settings: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        self.table_settings = list(table_settings)

    @staticmethod
    def _cell_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _row_non_numeric_ratio(row: Sequence[str]) -> float:
        total = 0
        non_numeric = 0
        for cell in row:
            text = cell.strip()
            if not text:
                continue
            total += 1
            if not re.search(r"\d", text):
                non_numeric += 1
        if total == 0:
            return 0.0
        return non_numeric / total

    def _infer_header_index(self, rows: Sequence[list[str]]) -> int | None:
        if not rows:
            return None
        first = rows[0]
        second = rows[1] if len(rows) > 1 else []
        first_ratio = self._row_non_numeric_ratio(first)
        second_ratio = self._row_non_numeric_ratio(second)
        if first_ratio >= 0.6 and (second_ratio <= 0.5 or first_ratio >= second_ratio):
            return 0
        return None

    def _build_table_payload(
        self,
        *,
        rows: list[list[str]],
        page_number: int,
        order_index: int,
        extractor_label: str,
    ) -> TablePayload | None:
        cleaned_rows = [row for row in rows if any(cell.strip() for cell in row)]
        if len(cleaned_rows) < 2:
            return None
        max_cols = max(len(row) for row in cleaned_rows)
        header_idx = self._infer_header_index(cleaned_rows)
        if header_idx is not None:
            header_row = cleaned_rows[header_idx]
            schema = [
                TableDetector._normalize_header_cell(cell, idx)
                for idx, cell in enumerate(header_row)
            ]
            if len(schema) < max_cols:
                schema.extend([f"column_{idx+1}" for idx in range(len(schema), max_cols)])
        else:
            schema = [f"column_{idx+1}" for idx in range(max_cols)]

        table_rows: list[TableRowPayload] = []
        next_row_idx = 0
        if header_idx is not None:
            header_cells_payload: list[TableCellPayload] = []
            for col_idx in range(max_cols):
                raw = header_row[col_idx] if col_idx < len(header_row) else ""
                header_cells_payload.append(
                    TableCellPayload(
                        row_index=0,
                        column_index=col_idx,
                        column_key=schema[col_idx],
                        raw_text=raw,
                        normalized_value=TableDetector._normalize_cell_value(raw),
                        bbox={},
                        confidence=None,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=0,
                    page_number=page_number,
                    bbox={},
                    raw_text=" | ".join(header_row),
                    metadata={"row_type": "header"},
                    cells=header_cells_payload,
                )
            )
            next_row_idx = 1

        for row in cleaned_rows[1 if header_idx is not None else 0 :]:
            cells_payload: list[TableCellPayload] = []
            for col_idx in range(max_cols):
                raw = row[col_idx] if col_idx < len(row) else ""
                cells_payload.append(
                    TableCellPayload(
                        row_index=next_row_idx,
                        column_index=col_idx,
                        column_key=schema[col_idx],
                        raw_text=raw,
                        normalized_value=TableDetector._normalize_cell_value(raw),
                        bbox={},
                        confidence=None,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=next_row_idx,
                    page_number=page_number,
                    bbox={},
                    raw_text=" | ".join(row),
                    metadata={"row_type": "data"},
                    cells=cells_payload,
                )
            )
            next_row_idx += 1

        return TablePayload(
            order_index=order_index,
            title=f"Table {order_index}",
            section_heading="",
            page_number=page_number,
            bbox={},
            column_schema=schema,
            data_dictionary={},
            metadata={"detected_via": "pdfplumber", "extractor": extractor_label},
            rows=table_rows,
        )

    def extract_candidates(
        self,
        path: Path,
    ) -> tuple[dict[str, list[TablePayload]], list[IssuePayload], dict[str, Any]]:
        if pdfplumber is None:
            return (
                {},
                [
                    IssuePayload(
                        code="pdfplumber_missing",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="pdfplumber is not installed; skipping PDF table extraction.",
                    )
                ],
                {},
            )

        candidates: dict[str, list[TablePayload]] = {}
        issues: list[IssuePayload] = []
        page_counts: dict[int, dict[str, int]] = {}

        try:
            with pdfplumber.open(path) as pdf:
                for label, settings in self.table_settings:
                    order_index = 0
                    tables_for_label: list[TablePayload] = []
                    for page_number, page in enumerate(pdf.pages, start=1):
                        try:
                            extracted = page.extract_tables(table_settings=dict(settings)) or []
                        except Exception as exc:
                            issues.append(
                                IssuePayload(
                                    code="pdfplumber_page_failed",
                                    severity=KnowledgeIssueSeverity.WARNING.value,
                                    description=f"pdfplumber failed on page {page_number}: {exc}",
                                    page_number=page_number,
                                )
                            )
                            continue
                        if not extracted:
                            continue
                        for raw_table in extracted:
                            normalized_rows = [
                                [self._cell_text(cell) for cell in row]
                                for row in (raw_table or [])
                                if row
                            ]
                            if not normalized_rows:
                                continue
                            order_index += 1
                            payload = self._build_table_payload(
                                rows=normalized_rows,
                                page_number=page_number,
                                order_index=order_index,
                                extractor_label=label,
                            )
                            if payload:
                                tables_for_label.append(payload)
                        page_counts.setdefault(page_number, {})[label] = page_counts.get(page_number, {}).get(label, 0) + len(extracted)
                    if tables_for_label:
                        candidates[f"pdfplumber:{label}"] = tables_for_label
        except Exception as exc:
            issues.append(
                IssuePayload(
                    code="pdfplumber_failed",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description=f"pdfplumber extraction failed: {exc}",
                )
            )

        metadata = {"page_counts": page_counts}
        return candidates, issues, metadata
