from __future__ import annotations

import math
import re
from typing import Any, Mapping

from apps.knowledge.ingestion.contracts import TablePayload
from apps.knowledge.ingestion.signals import (
    _TABLE_NUMERIC_SIGNAL_TOKEN_RE,
    _TABLE_ROW_VALUE_KEYWORD_RE,
    _column_numeric_signal,
)


class IngestionTablePostprocessSignalsMixin:

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
