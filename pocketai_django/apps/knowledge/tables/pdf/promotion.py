from __future__ import annotations

import re
from collections import Counter
from typing import Mapping, Sequence

from apps.knowledge.ingestion.contracts import TablePayload, TableRowPayload
from apps.knowledge.tables.pdf.page_chrome import IngestionPdfPageChromeMixin
from apps.knowledge.tables.pdf.promotion_gate import IngestionPdfPromotionGateMixin
from apps.knowledge.tables.pdf.promotion_restore import IngestionPdfPromotionRestoreMixin


class IngestionPdfTablePromotionMixin(
    IngestionPdfPageChromeMixin,
    IngestionPdfPromotionGateMixin,
    IngestionPdfPromotionRestoreMixin,
):


    def _pdf_table_readable_rows(self, table: TablePayload) -> list[TableRowPayload]:
        readable: list[TableRowPayload] = []
        for row in table.rows or []:
            meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            row_type = str(meta.get("row_type") or "").strip().lower()
            if row_type in {"header", "section_header"}:
                continue
            readable.append(row)
        return readable

    def _normalize_table_signature_text(self, text: str) -> str:
        cleaned = self._sanitize_text(text or "").strip().lower()
        if not cleaned:
            return ""
        cleaned = re.sub(r"[_\.\-]{2,}", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:240]

    def _table_row_signature(self, table: TablePayload, row_offset: int = 0) -> str:
        readable_rows = self._pdf_table_readable_rows(table)
        if row_offset < 0 or row_offset >= len(readable_rows):
            return ""
        row = readable_rows[row_offset]
        values: list[str] = []
        for cell in row.cells or []:
            raw = str(cell.raw_text or "").strip()
            if raw:
                values.append(raw)
            if len(values) >= 3:
                break
        return self._normalize_table_signature_text(" | ".join(values))

    def _table_row_prefix_signature(
        self,
        table: TablePayload,
        row_offset: int = 0,
        *,
        max_tokens: int = 8,
    ) -> str:
        readable_rows = self._pdf_table_readable_rows(table)
        if row_offset < 0 or row_offset >= len(readable_rows):
            return ""
        row = readable_rows[row_offset]
        values: list[str] = []
        for cell in row.cells or []:
            raw = str(cell.raw_text or "").strip()
            if raw:
                values.append(raw)
            if len(values) >= 2:
                break
        if not values:
            return ""
        text = self._normalize_table_signature_text(" ".join(values))
        if not text:
            return ""
        tokens = [token for token in text.split() if len(token) > 1 and token not in {"|"}]
        return " ".join(tokens[:max_tokens]).strip()

    def _table_effective_column_count(self, table: TablePayload) -> int:
        occupied: set[int] = set()
        for row in self._pdf_table_readable_rows(table):
            for cell in row.cells or []:
                if str(cell.raw_text or "").strip():
                    occupied.add(int(cell.column_index))
        if occupied:
            return len(occupied)
        return max(0, len(table.column_schema or []))

    def _build_pdf_table_recurrence_stats(self, tables: Sequence[TablePayload]) -> dict[str, Counter[str]]:
        first_rows = Counter()
        second_rows = Counter()
        first_row_prefixes = Counter()
        second_row_prefixes = Counter()
        for table in tables:
            first_sig = self._table_row_signature(table, 0)
            second_sig = self._table_row_signature(table, 1)
            first_prefix = self._table_row_prefix_signature(table, 0)
            second_prefix = self._table_row_prefix_signature(table, 1)
            if first_sig:
                first_rows[first_sig] += 1
            if second_sig:
                second_rows[second_sig] += 1
            if first_prefix:
                first_row_prefixes[first_prefix] += 1
            if second_prefix:
                second_row_prefixes[second_prefix] += 1
        return {
            "first_rows": first_rows,
            "second_rows": second_rows,
            "first_row_prefixes": first_row_prefixes,
            "second_row_prefixes": second_row_prefixes,
        }
