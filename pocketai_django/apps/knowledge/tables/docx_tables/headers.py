from __future__ import annotations

import math
import re
from typing import Mapping, Sequence

from apps.knowledge.ingestion.signals import _column_numeric_signal
from apps.knowledge.tables.docx_tables.schema import IngestionDocxSchemaMixin
from apps.knowledge.tables.docx_tables.sparse_series import IngestionDocxSparseSeriesMixin


class IngestionDocxHeaderRowsMixin:

    @staticmethod
    def _docx_row_has_financial_data_signal(values: Sequence[str]) -> bool:
        for value in values:
            sample = str(value or "").strip()
            if not sample:
                continue
            if "$" in sample or "%" in sample:
                return True
            if re.search(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b", sample):
                return True
            if re.search(r"\(\s*\d", sample):
                return True
        return False

    @staticmethod
    def _docx_row_looks_like_header_band(values: Sequence[str]) -> bool:
        non_empty = [
            IngestionDocxSchemaMixin._docx_canonicalize_header_label(str(value or "").strip())
            for value in values
            if str(value or "").strip()
        ]
        if not non_empty:
            return False
        if IngestionDocxHeaderRowsMixin._docx_row_has_financial_data_signal(non_empty):
            return False

        short_cell_ratio = sum(
            1
            for value in non_empty
            if len(re.findall(r"\w+", value)) <= 4 and len(value) <= 40
        ) / float(max(1, len(non_empty)))
        if short_cell_ratio < 0.6:
            return False

        lowered = [value.lower() for value in non_empty]
        period_or_header_terms = sum(
            1
            for value in lowered
            if re.search(
                r"\b(year|years|ended|ending|quarter|quarters|fiscal|period|periods|date|dates|record|payment|declaration|month|months|june|march|september|december|thereafter)\b",
                value,
            )
        )
        explicit_year_cells = sum(
            1
            for value in non_empty
            if re.fullmatch(r"(?:19|20)\d{2}", value)
        )
        period_label_cells = sum(
            1
            for value in non_empty
            if IngestionDocxSparseSeriesMixin._docx_value_looks_like_period_label(value)
        )
        if explicit_year_cells >= max(1, len(non_empty) // 2):
            return True
        if period_label_cells >= max(2, int(math.ceil(len(non_empty) * 0.6))):
            return True
        if len(non_empty) == 1 and period_or_header_terms > 0:
            return True
        return period_or_header_terms > 0

    @staticmethod
    def _docx_detect_header_rows(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
    ) -> list[int]:
        row_count = len(key_grid)
        if row_count <= 1:
            return []

        def _row_features(row_keys: Sequence[int | None]) -> dict[str, float]:
            values = [
                str(text_by_key.get(key, "")).strip()
                for key in row_keys
                if key is not None and str(text_by_key.get(key, "")).strip()
            ]
            if not values:
                return {"non_empty": 0.0, "alpha_ratio": 0.0, "numeric_ratio": 0.0}
            alpha_count = sum(1 for value in values if re.search(r"[A-Za-z\u0600-\u06FF]", value))
            numeric_count = sum(1 for value in values if _column_numeric_signal(value))
            total = max(1, len(values))
            return {
                "non_empty": float(len(values)),
                "alpha_ratio": float(alpha_count) / total,
                "numeric_ratio": float(numeric_count) / total,
            }

        probe_rows = min(4, row_count)
        header_rows: list[int] = []
        for row_idx in range(probe_rows):
            values = [
                str(text_by_key.get(key, "")).strip()
                for key in key_grid[row_idx]
                if key is not None and str(text_by_key.get(key, "")).strip()
            ]
            stats = _row_features(key_grid[row_idx])
            header_band = IngestionDocxHeaderRowsMixin._docx_row_looks_like_header_band(values)
            if stats["non_empty"] <= 0:
                if row_idx == 0:
                    continue
                break
            looks_header = stats["alpha_ratio"] >= 0.5 and stats["numeric_ratio"] <= 0.5
            if row_idx == 0:
                if looks_header or header_band or stats["numeric_ratio"] < 0.8:
                    header_rows.append(row_idx)
                continue
            multi_value_header = looks_header and len(values) >= 2
            if (multi_value_header or header_band) and header_rows:
                header_rows.append(row_idx)
                continue
            break

        if len(header_rows) >= row_count:
            header_rows = header_rows[: max(1, row_count - 1)]
        if header_rows:
            next_idx = header_rows[-1] + 1
            if next_idx < row_count - 1 and next_idx not in header_rows:
                next_values = [
                    str(text_by_key.get(key, "")).strip()
                    for key in key_grid[next_idx]
                    if key is not None and str(text_by_key.get(key, "")).strip()
                ]
                if (
                    len(next_values) == 1
                    and IngestionDocxSparseSeriesMixin._docx_value_looks_like_period_label(next_values[0])
                    and not IngestionDocxHeaderRowsMixin._docx_row_has_financial_data_signal(next_values)
                ):
                    following_stats = _row_features(key_grid[next_idx + 1])
                    if following_stats["non_empty"] >= 1:
                        header_rows.append(next_idx)
        return header_rows

    @staticmethod
    def _docx_detect_section_rows(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        header_row_set: set[int],
    ) -> list[int]:
        row_count = len(key_grid)
        if row_count <= 2:
            return []

        def _non_empty_values(row_idx: int) -> list[str]:
            return [
                str(text_by_key.get(key, "")).strip()
                for key in key_grid[row_idx]
                if key is not None and str(text_by_key.get(key, "")).strip()
            ]

        section_rows: list[int] = []
        for row_idx in range(row_count):
            if row_idx in header_row_set:
                continue
            values = _non_empty_values(row_idx)
            repeated_label, _ = IngestionDocxSchemaMixin._docx_repeated_row_label_info(values)
            if len(values) != 1 and not repeated_label:
                continue
            label = repeated_label or values[0]
            normalized = label.strip().lower()
            if not normalized:
                continue
            if re.search(r"\d", normalized) and not IngestionDocxSparseSeriesMixin._docx_value_looks_like_period_label(label):
                continue
            if normalized in {"total", "subtotal", "totals"}:
                continue
            if len(re.findall(r"\w+", label)) > 10 or len(label) > 80:
                continue
            prev_counts = [
                len(_non_empty_values(candidate))
                for candidate in range(max(0, row_idx - 2), row_idx)
                if candidate not in header_row_set
            ]
            next_counts = [
                len(_non_empty_values(candidate))
                for candidate in range(row_idx + 1, min(row_count, row_idx + 3))
                if candidate not in header_row_set
            ]
            if not next_counts:
                continue
            prev_supports_section = max(prev_counts, default=0) >= 2 or not prev_counts
            if prev_supports_section and max(next_counts, default=0) >= 2:
                section_rows.append(row_idx)
        return section_rows
