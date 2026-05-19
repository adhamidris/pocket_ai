from __future__ import annotations

import math
import re
from typing import Mapping, Sequence

from apps.knowledge.ingestion.signals import _column_numeric_signal
from apps.knowledge.tables.docx_tables.schema import IngestionDocxSchemaMixin


class IngestionDocxSparseSeriesMixin:

    @staticmethod
    def _docx_value_looks_like_period_label(value: str) -> bool:
        sample = IngestionDocxSchemaMixin._docx_canonicalize_header_label(str(value or "").strip())
        if not sample:
            return False
        lowered = sample.lower()
        if re.fullmatch(r"(?:19|20)\d{2}", lowered):
            return True
        if re.fullmatch(r"\d{1,2}/\d{2,4}", lowered):
            return True
        if re.fullmatch(r"(?:q[1-4]|[1-4]q)(?:\s*(?:fy)?\s*(?:19|20)?\d{2,4})?", lowered):
            return True
        if re.fullmatch(
            r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s\-]+(?:19|20)\d{2}",
            lowered,
        ):
            return True
        if (
            re.search(r"\b(?:year|years|quarter|quarters|month|months|ended|ending)\b", lowered)
            and re.search(r"(?:19|20)\d{2}", lowered)
        ):
            return True
        return False

    @staticmethod
    def _docx_normalize_sparse_series_columns(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        span_by_key: Mapping[int, Mapping[str, int]],
    ) -> tuple[list[list[int | None]], dict[int, str], dict[int, dict[str, int]], dict[str, int]]:
        if not key_grid:
            return [], dict(text_by_key), dict(span_by_key), {"merged_columns": 0}

        row_count = len(key_grid)
        column_count = max((len(row) for row in key_grid), default=0)
        if row_count < 2 or column_count < 4:
            return list(map(list, key_grid)), dict(text_by_key), {int(key): dict(value) for key, value in span_by_key.items()}, {"merged_columns": 0}

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

        series_row_idx: int | None = None
        for candidate_row_idx in range(min(2, row_count)):
            row_values = [_value_at(candidate_row_idx, col_idx) for col_idx in range(column_count)]
            non_empty_values = [value for value in row_values if value]
            if len(non_empty_values) < 3:
                continue
            period_like_count = sum(
                1 for value in non_empty_values if IngestionDocxSparseSeriesMixin._docx_value_looks_like_period_label(value)
            )
            if period_like_count < max(2, int(math.ceil(len(non_empty_values) * 0.6))):
                continue
            duplicate_pairs = 0
            for col_idx in range(1, column_count - 1):
                current_value = IngestionDocxSchemaMixin._docx_canonicalize_header_label(row_values[col_idx])
                next_value = IngestionDocxSchemaMixin._docx_canonicalize_header_label(row_values[col_idx + 1])
                if (
                    current_value
                    and next_value
                    and current_value == next_value
                    and IngestionDocxSparseSeriesMixin._docx_value_looks_like_period_label(current_value)
                ):
                    duplicate_pairs += 1
            if duplicate_pairs >= 2:
                series_row_idx = candidate_row_idx
                break

        if series_row_idx is None:
            return list(map(list, key_grid)), dict(text_by_key), {int(key): dict(value) for key, value in span_by_key.items()}, {"merged_columns": 0}

        actions = ["keep"] * column_count
        merge_targets: dict[int, int] = {}
        data_row_count = max(1, row_count - (series_row_idx + 1))

        for col_idx in range(1, column_count - 1):
            current_label = IngestionDocxSchemaMixin._docx_canonicalize_header_label(_value_at(series_row_idx, col_idx))
            next_label = IngestionDocxSchemaMixin._docx_canonicalize_header_label(_value_at(series_row_idx, col_idx + 1))
            if (
                not current_label
                or not next_label
                or current_label != next_label
                or not IngestionDocxSparseSeriesMixin._docx_value_looks_like_period_label(current_label)
            ):
                continue

            left_values = [_value_at(row_idx, col_idx) for row_idx in range(series_row_idx + 1, row_count)]
            right_values = [_value_at(row_idx, col_idx + 1) for row_idx in range(series_row_idx + 1, row_count)]
            left_non_empty = sum(1 for value in left_values if value)
            right_non_empty = sum(1 for value in right_values if value)
            if left_non_empty == right_non_empty:
                continue

            if left_non_empty < right_non_empty:
                sparse_idx, data_idx = col_idx, col_idx + 1
                sparse_non_empty, data_non_empty = left_non_empty, right_non_empty
            else:
                sparse_idx, data_idx = col_idx + 1, col_idx
                sparse_non_empty, data_non_empty = right_non_empty, left_non_empty

            if sparse_non_empty > 1:
                continue
            if data_non_empty < max(2, int(math.ceil(data_row_count * 0.5))):
                continue
            actions[sparse_idx] = "merge"
            merge_targets[sparse_idx] = data_idx

        if not merge_targets:
            return list(map(list, key_grid)), dict(text_by_key), {int(key): dict(value) for key, value in span_by_key.items()}, {"merged_columns": 0}

        groups: dict[int, list[int]] = {}
        for col_idx, action in enumerate(actions):
            if action == "merge":
                target_idx = merge_targets.get(col_idx)
                if target_idx is None:
                    continue
                groups.setdefault(target_idx, []).append(col_idx)
                groups[target_idx].append(target_idx)
                continue
            groups.setdefault(col_idx, []).append(col_idx)

        ordered_targets = [col_idx for col_idx, action in enumerate(actions) if action == "keep"]
        new_key_grid: list[list[int | None]] = []
        new_text_by_key: dict[int, str] = {}
        positions_by_key: dict[int, list[tuple[int, int]]] = {}
        next_synthetic_key = -1

        for row_idx in range(row_count):
            new_row: list[int | None] = []
            for out_col_idx, target_idx in enumerate(ordered_targets):
                source_indices = sorted(set(groups.get(target_idx, [target_idx])))
                parts: list[str] = []
                source_keys: list[int] = []
                for source_idx in source_indices:
                    if source_idx >= len(key_grid[row_idx]):
                        continue
                    key = key_grid[row_idx][source_idx]
                    if key is None:
                        continue
                    source_keys.append(int(key))
                    value = str(text_by_key.get(key, "")).strip()
                    if value:
                        parts.append(value)

                combined = IngestionDocxSchemaMixin._docx_combine_cell_texts(parts)
                if row_idx == series_row_idx:
                    combined = IngestionDocxSchemaMixin._docx_canonicalize_header_label(combined)
                if not combined:
                    new_row.append(None)
                    continue

                if len(source_keys) == 1 and combined == str(text_by_key.get(source_keys[0], "")).strip():
                    key_to_use = source_keys[0]
                else:
                    key_to_use = next_synthetic_key
                    next_synthetic_key -= 1
                new_text_by_key[key_to_use] = combined
                positions_by_key.setdefault(key_to_use, []).append((row_idx, out_col_idx))
                new_row.append(key_to_use)
            new_key_grid.append(new_row)

        new_span_by_key: dict[int, dict[str, int]] = {}
        for key, positions in positions_by_key.items():
            row_start = min(pos[0] for pos in positions)
            row_end = max(pos[0] for pos in positions)
            col_start = min(pos[1] for pos in positions)
            col_end = max(pos[1] for pos in positions)
            prior_span = span_by_key.get(key) or {}
            new_span_by_key[key] = {
                "row_start": row_start,
                "row_end": row_end,
                "col_start": col_start,
                "col_end": col_end,
                "row_span": int(prior_span.get("row_span") or ((row_end - row_start) + 1)),
                "column_span": int(prior_span.get("column_span") or ((col_end - col_start) + 1)),
            }

        return new_key_grid, new_text_by_key, new_span_by_key, {
            "merged_columns": len(merge_targets),
        }

    @staticmethod
    def _docx_collapse_helper_columns(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        span_by_key: Mapping[int, Mapping[str, int]],
        header_rows: Sequence[int],
    ) -> tuple[list[list[int | None]], dict[int, str], dict[int, dict[str, int]], dict[str, int]]:
        if not key_grid:
            return [], dict(text_by_key), dict(span_by_key), {"removed_columns": 0, "merged_columns": 0}

        row_count = len(key_grid)
        column_count = max((len(row) for row in key_grid), default=0)
        if column_count <= 0:
            return list(map(list, key_grid)), dict(text_by_key), dict(span_by_key), {"removed_columns": 0, "merged_columns": 0}

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

        column_profiles: list[dict[str, Any]] = []
        for col_idx in range(column_count):
            all_values = [_value_at(row_idx, col_idx) for row_idx in range(row_count)]
            non_empty_values = [value for value in all_values if value]
            header_values = [_value_at(row_idx, col_idx) for row_idx in range(row_count) if row_idx in header_row_set]
            header_values = [value for value in header_values if value]
            data_values = [_value_at(row_idx, col_idx) for row_idx in range(row_count) if row_idx not in header_row_set]
            data_values = [value for value in data_values if value]
            header_fingerprint = tuple(
                re.sub(r"\s+", " ", value).strip().lower()
                for value in header_values
                if value.strip()
            )
            column_profiles.append(
                {
                    "all_values": non_empty_values,
                    "header_values": header_values,
                    "data_values": data_values,
                    "header_fingerprint": header_fingerprint,
                    "helper_only_data": bool(data_values)
                    and all(IngestionDocxSchemaMixin._docx_cell_is_helper_token(value) for value in data_values),
                    "substantive_data": any(
                        value
                        and not IngestionDocxSchemaMixin._docx_cell_is_helper_token(value)
                        and (
                            _column_numeric_signal(value)
                            or re.search(r"[A-Za-z\u0600-\u06FF]", value)
                        )
                        for value in data_values
                    ),
                }
            )

        actions = ["keep"] * column_count
        merge_targets: dict[int, int] = {}

        for col_idx, profile in enumerate(column_profiles):
            all_values = profile["all_values"]
            data_values = profile["data_values"]
            header_fingerprint = profile["header_fingerprint"]

            if not all_values:
                actions[col_idx] = "drop"
                continue

            if not data_values and not header_fingerprint:
                actions[col_idx] = "drop"
                continue

            if not data_values and header_fingerprint:
                prev_fingerprint = column_profiles[col_idx - 1]["header_fingerprint"] if col_idx > 0 else ()
                next_fingerprint = column_profiles[col_idx + 1]["header_fingerprint"] if col_idx + 1 < column_count else ()
                if header_fingerprint == prev_fingerprint or header_fingerprint == next_fingerprint:
                    actions[col_idx] = "drop"
                    continue

            if not profile["helper_only_data"]:
                continue

            helper_values = {value for value in data_values if value}
            prefer_right = helper_values <= {"$", "€", "£", "¥", "₹", "(", "["}
            prefer_left = helper_values <= {")", "]"}

            target_idx: int | None = None
            candidate_indices: list[int] = []
            if prefer_left and col_idx > 0:
                candidate_indices.append(col_idx - 1)
            if prefer_right and col_idx + 1 < column_count:
                candidate_indices.append(col_idx + 1)
            if not candidate_indices:
                if col_idx + 1 < column_count:
                    candidate_indices.append(col_idx + 1)
                if col_idx > 0:
                    candidate_indices.append(col_idx - 1)

            for candidate_idx in candidate_indices:
                if actions[candidate_idx] == "drop":
                    continue
                if column_profiles[candidate_idx]["substantive_data"]:
                    target_idx = candidate_idx
                    break
            if target_idx is None:
                continue
            actions[col_idx] = "merge"
            merge_targets[col_idx] = target_idx

        groups: dict[int, list[int]] = {}
        for col_idx, action in enumerate(actions):
            if action == "drop":
                continue
            if action == "merge":
                target_idx = merge_targets.get(col_idx)
                if target_idx is None or actions[target_idx] == "drop":
                    actions[col_idx] = "drop"
                    continue
                groups.setdefault(target_idx, []).append(col_idx)
                continue
            groups.setdefault(col_idx, []).append(col_idx)

        for target_idx, source_indices in list(groups.items()):
            unique_sources = sorted(set(source_indices + [target_idx]))
            groups[target_idx] = unique_sources

        ordered_targets = [col_idx for col_idx, action in enumerate(actions) if action == "keep"]
        if len(ordered_targets) == column_count and not any(action != "keep" for action in actions):
            return (
                [list(row) for row in key_grid],
                dict(text_by_key),
                {int(key): dict(value) for key, value in span_by_key.items()},
                {"removed_columns": 0, "merged_columns": 0},
            )

        new_key_grid: list[list[int | None]] = []
        new_text_by_key: dict[int, str] = {}
        positions_by_key: dict[int, list[tuple[int, int]]] = {}
        next_synthetic_key = -1

        for row_idx in range(row_count):
            new_row: list[int | None] = []
            for out_col_idx, target_idx in enumerate(ordered_targets):
                source_indices = groups.get(target_idx, [target_idx])
                parts: list[str] = []
                source_keys: list[int] = []
                for source_idx in source_indices:
                    if source_idx >= len(key_grid[row_idx]):
                        continue
                    key = key_grid[row_idx][source_idx]
                    if key is None:
                        continue
                    value = str(text_by_key.get(key, "")).strip()
                    if value:
                        parts.append(value)
                    source_keys.append(int(key))

                combined = IngestionDocxSchemaMixin._docx_combine_cell_texts(parts)
                if row_idx in header_row_set:
                    combined = IngestionDocxSchemaMixin._docx_canonicalize_header_label(combined)
                if not combined:
                    new_row.append(None)
                    continue

                if len(source_keys) == 1 and combined == str(text_by_key.get(source_keys[0], "")).strip():
                    key_to_use = source_keys[0]
                else:
                    key_to_use = next_synthetic_key
                    next_synthetic_key -= 1
                new_text_by_key[key_to_use] = combined
                positions_by_key.setdefault(key_to_use, []).append((row_idx, out_col_idx))
                new_row.append(key_to_use)
            new_key_grid.append(new_row)

        new_span_by_key: dict[int, dict[str, int]] = {}
        for key, positions in positions_by_key.items():
            if key in span_by_key and key >= 0:
                prior_span = span_by_key.get(key) or {}
                row_start = min(pos[0] for pos in positions)
                row_end = max(pos[0] for pos in positions)
                col_start = min(pos[1] for pos in positions)
                col_end = max(pos[1] for pos in positions)
                new_span_by_key[key] = {
                    "row_start": row_start,
                    "row_end": row_end,
                    "col_start": col_start,
                    "col_end": col_end,
                    "row_span": int(prior_span.get("row_span") or ((row_end - row_start) + 1)),
                    "column_span": int(prior_span.get("column_span") or ((col_end - col_start) + 1)),
                }
                continue
            row_start = min(pos[0] for pos in positions)
            row_end = max(pos[0] for pos in positions)
            col_start = min(pos[1] for pos in positions)
            col_end = max(pos[1] for pos in positions)
            new_span_by_key[key] = {
                "row_start": row_start,
                "row_end": row_end,
                "col_start": col_start,
                "col_end": col_end,
                "row_span": (row_end - row_start) + 1,
                "column_span": (col_end - col_start) + 1,
            }

        removed_columns = sum(1 for action in actions if action == "drop")
        merged_columns = sum(1 for action in actions if action == "merge")
        return new_key_grid, new_text_by_key, new_span_by_key, {
            "removed_columns": removed_columns,
            "merged_columns": merged_columns,
        }
