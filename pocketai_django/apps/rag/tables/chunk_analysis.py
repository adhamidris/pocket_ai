from __future__ import annotations

import math
import re

from apps.knowledge.models import KnowledgeUploadChunk


class TableChunkAnalysisMixin:

    def _table_chunk_match_info(
        self,
        chunk: KnowledgeUploadChunk,
        *,
        query_tokens: set[str],
        specific_tokens: set[str],
    ) -> dict[str, object]:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        header_tokens = self._table_header_tokens(metadata.get("table_id"))
        header_matches = header_tokens & query_tokens if query_tokens else set()
        specific_header_matches = header_tokens & specific_tokens if specific_tokens else set()
        content = (chunk.content or "").lower()
        specific_content_matches = (
            {token for token in specific_tokens if token and token in content} if specific_tokens else set()
        )
        specific_matches = specific_header_matches | specific_content_matches
        specific_token_count = len(specific_tokens)
        specific_match_count = len(specific_matches)
        specific_match_ratio = (
            (specific_match_count / specific_token_count) if specific_token_count else 0.0
        )
        if specific_token_count <= 2:
            required_count = 1
        elif specific_token_count <= 4:
            required_count = max(1, min(2, self.table_specific_min_match_count))
        else:
            required_count = max(2, self.table_specific_min_match_count)
        if specific_token_count:
            required_count = min(required_count, specific_token_count)
            specific_match_strong = bool(
                specific_match_count >= required_count
                and (
                    specific_match_ratio >= self.table_specific_min_match_ratio
                    or specific_match_count >= (required_count + 1)
                )
            )
        else:
            specific_match_strong = False
        return {
            "header_match": bool(header_matches),
            "header_match_tokens": tuple(sorted(header_matches))[:5],
            "specific_match": bool(specific_matches),
            "specific_match_tokens": tuple(sorted(specific_matches))[:5],
            "specific_match_count": specific_match_count,
            "specific_token_count": specific_token_count,
            "specific_match_ratio": round(specific_match_ratio, 4),
            "specific_match_strong": specific_match_strong,
        }

    def _table_structural_row_info(self, chunk: KnowledgeUploadChunk) -> dict[str, object]:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        if not metadata.get("is_table_chunk"):
            return {
                "structural_row": False,
                "structural_scope_echo_count": 0,
                "structural_scope_echo_ratio": 0.0,
                "structural_pair_count": 0,
                "structural_pair_echo_count": 0,
                "structural_pair_echo_ratio": 0.0,
            }

        def _looks_numeric(sample: str) -> bool:
            text = str(sample or "").strip()
            if not text:
                return False
            lowered = text.lower()
            return bool(
                re.search(r"\d", text)
                or "%" in text
                or any(token in lowered for token in ("egp", "usd", "eur", "gbp"))
            )

        def _normalize_label(sample: str) -> str:
            return re.sub(r"[^\w]+", "", str(sample or "").strip().lower())

        scope_columns_raw = metadata.get("table_row_scope_dimension_columns") or []
        scope_columns = [
            str(value or "").strip()
            for value in scope_columns_raw
            if str(value or "").strip()
        ]
        content = str(chunk.content or "").lower()
        scope_echo_count = 0
        scope_numeric_count = 0
        for label in scope_columns:
            marker = f"{label.lower()}:"
            idx = content.find(marker)
            if idx == -1:
                continue
            tail = content[idx + len(marker):].splitlines()[0].strip()
            if tail == label.lower():
                scope_echo_count += 1
            if _looks_numeric(tail):
                scope_numeric_count += 1
        scope_echo_ratio = (
            float(scope_echo_count) / float(len(scope_columns))
            if scope_columns
            else 0.0
        )

        pair_count = 0
        pair_echo_count = 0
        pair_numeric_count = 0
        for segment in re.split(r"[;\n]+", str(chunk.content or "")):
            if ":" not in segment:
                continue
            key, value = segment.split(":", 1)
            normalized_key = _normalize_label(key)
            normalized_value = _normalize_label(value)
            if not normalized_key or not normalized_value:
                continue
            pair_count += 1
            if normalized_key == normalized_value:
                pair_echo_count += 1
            if _looks_numeric(value):
                pair_numeric_count += 1
        pair_echo_ratio = (float(pair_echo_count) / float(pair_count)) if pair_count else 0.0

        explicit = metadata.get("table_row_is_structural_context")
        if explicit is None:
            has_fee_value = bool(metadata.get("table_row_signal_has_fee_value"))
            scope_structural = bool(
                len(scope_columns) >= 3
                and scope_echo_count >= max(2, int(math.ceil(len(scope_columns) * 0.6)))
                and scope_numeric_count == 0
                and not has_fee_value
            )
            pair_structural = bool(
                pair_count >= 3
                and pair_echo_count >= max(2, int(math.ceil(pair_count * 0.5)))
                and pair_numeric_count == 0
                and not has_fee_value
            )
            explicit = bool(scope_structural or pair_structural)

        return {
            "structural_row": bool(explicit),
            "structural_scope_echo_count": int(scope_echo_count),
            "structural_scope_numeric_count": int(scope_numeric_count),
            "structural_scope_echo_ratio": round(float(scope_echo_ratio), 4),
            "structural_pair_count": int(pair_count),
            "structural_pair_echo_count": int(pair_echo_count),
            "structural_pair_numeric_count": int(pair_numeric_count),
            "structural_pair_echo_ratio": round(float(pair_echo_ratio), 4),
        }
