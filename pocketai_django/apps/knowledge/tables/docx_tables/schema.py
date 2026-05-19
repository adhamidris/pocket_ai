from __future__ import annotations

import re
from typing import Mapping, Sequence

from apps.knowledge.ingestion.signals import _column_numeric_signal
from apps.knowledge.tables.detection import TableDetector


class IngestionDocxSchemaMixin:

    @staticmethod
    def _docx_cell_is_helper_token(value: str) -> bool:
        sample = str(value or "").strip()
        if not sample:
            return False
        if re.fullmatch(r"[$€£¥₹]", sample):
            return True
        if sample in {"(", ")", "[", "]"}:
            return True
        return False

    @staticmethod
    def _docx_combine_cell_texts(parts: Sequence[str]) -> str:
        cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
        if not cleaned:
            return ""
        combined = cleaned[0]
        currency_tokens = {"$", "€", "£", "¥", "₹"}
        for part in cleaned[1:]:
            if not combined:
                combined = part
                continue
            if part in {")", "]", "%"}:
                combined = combined.rstrip() + part
                continue
            if part in {"(", "["}:
                combined = combined.rstrip() + part
                continue
            if combined.endswith(tuple(currency_tokens)) or combined.endswith(("(", "[")):
                combined = combined.rstrip() + part
                continue
            combined = combined.rstrip() + " " + part
        return combined.strip()

    @staticmethod
    def _docx_collapse_repeated_sequence(parts: Sequence[str]) -> list[str]:
        items = [str(part or "").strip() for part in parts if str(part or "").strip()]
        size = len(items)
        if size <= 1:
            return items
        for chunk_size in range(1, (size // 2) + 1):
            if size % chunk_size != 0:
                continue
            chunk = items[:chunk_size]
            if chunk * (size // chunk_size) == items:
                return chunk
        return items

    @staticmethod
    def _docx_canonicalize_header_label(label: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(label or "").strip())
        if not cleaned:
            return ""
        tokens = cleaned.split(" ")
        collapsed = IngestionDocxSchemaMixin._docx_collapse_repeated_sequence(tokens)
        return " ".join(collapsed).strip()

    @staticmethod
    def _docx_repeated_row_label_info(values: Sequence[str]) -> tuple[str, int | None]:
        normalized: list[tuple[int, str, str]] = []
        for idx, value in enumerate(values):
            raw = str(value or "").strip()
            if not raw:
                continue
            canonical = IngestionDocxSchemaMixin._docx_canonicalize_header_label(raw)
            if not canonical:
                continue
            normalized.append((idx, canonical, canonical.strip().lower()))
        if len(normalized) < 2:
            return "", None
        lowered = {entry[2] for entry in normalized if entry[2]}
        if len(lowered) != 1:
            return "", None
        first_idx, first_label, _ = normalized[0]
        return first_label, first_idx

    @staticmethod
    def _docx_normalize_column_key(label: str, index: int) -> str:
        normalized = TableDetector._normalize_header_cell(label, index)
        parts = [part for part in str(normalized or "").split("_") if part]
        collapsed = IngestionDocxSchemaMixin._docx_collapse_repeated_sequence(parts)
        if collapsed:
            normalized = "_".join(collapsed)
        return normalized or f"column_{index + 1}"

    @staticmethod
    def _docx_column_key_looks_period_like(key: str) -> bool:
        sample = str(key or "").strip().lower()
        if not sample or sample.startswith("column_"):
            return False
        if re.fullmatch(r"(?:19|20)\d{2}", sample):
            return True
        if re.fullmatch(r"\d{1,2}_\d{2,4}", sample):
            return True
        if re.fullmatch(r"(?:q[1-4]|[1-4]q)(?:_(?:fy)?(?:19|20)?\d{2,4})?", sample):
            return True
        if "thereafter" in sample:
            return True
        return bool(
            re.search(r"(?:19|20)\d{2}", sample)
            and re.search(
                r"\b(?:year|years|quarter|quarters|month|months|ended|ending|june|march|september|december)\b",
                sample.replace("_", " "),
            )
        )

    @staticmethod
    def _docx_column_key_is_generic(key: str) -> bool:
        return bool(re.fullmatch(r"column_\d+", str(key or "").strip().lower()))

    @classmethod
    def _docx_refine_grouped_column_schema(
        cls,
        column_schema: Sequence[str],
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        header_rows: Sequence[int],
    ) -> list[str]:
        schema = list(column_schema or [])
        if not schema or not key_grid:
            return schema

        row_count = len(key_grid)
        header_row_set = set(header_rows)

        def _value_at(row_idx: int, col_idx: int) -> str:
            if row_idx >= row_count:
                return ""
            row = key_grid[row_idx]
            if col_idx >= len(row):
                return ""
            key = row[col_idx]
            if key is None:
                return ""
            return str(text_by_key.get(key, "")).strip()

        profiles: list[dict[str, Any]] = []
        for col_idx, key in enumerate(schema):
            data_values = [
                _value_at(row_idx, col_idx)
                for row_idx in range(row_count)
                if row_idx not in header_row_set and _value_at(row_idx, col_idx)
            ]
            helper_only = bool(data_values) and all(cls._docx_cell_is_helper_token(value) for value in data_values)
            substantive = any(
                value
                and not cls._docx_cell_is_helper_token(value)
                and (_column_numeric_signal(value) or re.search(r"[A-Za-z\u0600-\u06FF]", value))
                for value in data_values
            )
            profiles.append(
                {
                    "key": str(key or "").strip(),
                    "data_count": len(data_values),
                    "helper_only": helper_only,
                    "substantive": substantive,
                    "period_like": cls._docx_column_key_looks_period_like(str(key or "").strip()),
                    "generic": cls._docx_column_key_is_generic(str(key or "").strip()),
                }
            )

        refined = list(schema)
        for col_idx, profile in enumerate(profiles):
            key = profile["key"]
            if not key or not profile["period_like"] or profile["data_count"] <= 0:
                continue
            if col_idx + 1 >= len(profiles):
                continue
            right = profiles[col_idx + 1]
            right_key = str(right["key"] or "").strip()
            if (
                right_key
                and not right["period_like"]
                and not right["generic"]
                and not right["helper_only"]
                and right["data_count"] == 0
            ):
                refined[col_idx] = cls._docx_normalize_column_key(f"{right_key} {key}", col_idx)

        for col_idx, profile in enumerate(profiles[:-1]):
            key = str(refined[col_idx] or "").strip()
            next_key = str(refined[col_idx + 1] or "").strip()
            if not key or key != next_key or not cls._docx_column_key_looks_period_like(key):
                continue
            next_profile = profiles[col_idx + 1]
            if profile["helper_only"] and next_profile["substantive"]:
                refined[col_idx] = cls._docx_normalize_column_key(f"helper {key}", col_idx)
            elif next_profile["helper_only"] and profile["substantive"]:
                refined[col_idx + 1] = cls._docx_normalize_column_key(f"helper {key}", col_idx + 1)

        return refined
