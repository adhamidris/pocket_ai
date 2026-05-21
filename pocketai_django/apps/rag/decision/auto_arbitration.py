from __future__ import annotations

import re
from typing import Mapping, Sequence

from apps.rag.contracts import ChunkResult


class AutoArbitrationMixin:

    @staticmethod
    def _clean_auto_evidence_label(value: object, *, max_chars: int = 72) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" \n\r\t-:|,.;")
        if not text:
            return ""
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
        return text

    def _auto_result_strength(self, hit: ChunkResult) -> float:
        values = [
            self._safe_float(hit.rerank_score),
            self._safe_float(hit.lexical_score),
            self._safe_float(hit.alias_confidence),
            self._safe_float(hit.recency_score),
        ]
        if isinstance(hit.vector_distance, (int, float)):
            values.append(1.0 - float(hit.vector_distance))
        return max(values) if values else 0.0

    def _table_auto_evidence_label(self, hit: ChunkResult, *, query_tokens: Sequence[str]) -> str:
        metadata = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
        diagnostics = hit.diagnostics if isinstance(hit.diagnostics, dict) else {}

        specific_tokens_raw = diagnostics.get("specific_match_tokens")
        header_tokens_raw = diagnostics.get("header_match_tokens")
        token_pool: list[str] = []
        for source in (specific_tokens_raw, header_tokens_raw):
            if isinstance(source, (list, tuple)):
                token_pool.extend(str(item).strip().lower() for item in source if str(item).strip())
        if token_pool:
            preferred: list[str] = []
            token_set = set(token_pool)
            for token in query_tokens:
                lowered = str(token).strip().lower()
                if lowered and lowered in token_set and lowered not in preferred:
                    preferred.append(lowered)
            if not preferred:
                preferred = [token for token in token_pool if token]
            label = " ".join(preferred[:3])
            cleaned = self._clean_auto_evidence_label(label)
            if cleaned:
                return cleaned

        for key in ("row_label", "table_title", "section_heading", "sheet_name"):
            cleaned = self._clean_auto_evidence_label(metadata.get(key))
            if cleaned:
                return cleaned

        content = str(hit.chunk.content or "")
        if content:
            for raw_line in content.splitlines():
                line = self._clean_auto_evidence_label(raw_line)
                if not line:
                    continue
                if line.startswith("["):
                    continue
                if ":" in line:
                    left = self._clean_auto_evidence_label(line.split(":", 1)[0])
                    if left:
                        return left
                return line
        return "table records"

    def _text_auto_evidence_label(self, hit: ChunkResult, *, query_tokens: Sequence[str]) -> str:
        metadata = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
        content = str(hit.chunk.content or "")
        lowered = content.lower()
        normalized_query_tokens = [str(token).strip().lower() for token in query_tokens if str(token).strip()]

        for token in normalized_query_tokens:
            idx = lowered.find(token)
            if idx < 0:
                continue
            start = max(0, idx - 28)
            end = min(len(content), idx + len(token) + 38)
            excerpt = self._clean_auto_evidence_label(content[start:end])
            if excerpt:
                return excerpt

        for key in ("section_heading", "entity_name", "display_name", "title"):
            cleaned = self._clean_auto_evidence_label(metadata.get(key))
            if cleaned:
                return cleaned

        if content:
            for raw_line in content.splitlines():
                line = self._clean_auto_evidence_label(raw_line)
                if line:
                    return line
        return "document text"

    def _build_auto_ambiguity_clarification_question(
        self,
        *,
        hits: Sequence[ChunkResult],
        query_tokens: Sequence[str],
    ) -> tuple[str, str, str]:
        table_hits = [hit for hit in hits if self._chunk_index_type(hit.chunk) == "table"]
        text_hits = [hit for hit in hits if self._chunk_index_type(hit.chunk) != "table"]

        table_label = ""
        text_label = ""
        if table_hits:
            best_table = max(table_hits, key=self._auto_result_strength)
            table_label = self._table_auto_evidence_label(best_table, query_tokens=query_tokens)
        if text_hits:
            best_text = max(text_hits, key=self._auto_result_strength)
            text_label = self._text_auto_evidence_label(best_text, query_tokens=query_tokens)

        if table_label and text_label:
            question = (
                f'I found table evidence around "{table_label}" and text evidence around "{text_label}". '
                "Do you want table-only, text-only, or both?"
            )
            return question, table_label, text_label

        question = (
            "I found relevant evidence in both table data and document text. "
            "Do you want table-only, text-only, or both?"
        )
        return question, table_label, text_label

    def _arbitrate_auto_mode(
        self,
        *,
        scoring_diagnostics: Mapping[str, object] | None = None,
        table_intent_hint: bool = False,
    ) -> dict[str, object]:
        scoring = scoring_diagnostics or {}
        table_score = self._clamp_unit(self._safe_float(scoring.get("auto_table_score")))
        text_score = self._clamp_unit(self._safe_float(scoring.get("auto_text_score")))
        margin = abs(table_score - text_score)

        table_hits = max(0, int(self._safe_float(scoring.get("auto_score_table_hits"))))
        text_hits = max(0, int(self._safe_float(scoring.get("auto_score_text_hits"))))

        both_present = table_hits > 0 and text_hits > 0
        table_strong = table_score >= self.auto_mode_min_score
        text_strong = text_score >= self.auto_mode_min_score
        ambiguous = bool(
            both_present
            and table_strong
            and text_strong
            and margin < self.auto_mode_margin_threshold
        )

        if ambiguous:
            decision = "tie_fallback_to_hint"
            resolved_table_intent = bool(table_intent_hint)
            reason = "score_margin_ambiguous_fallback_to_hint"
            needs_clarification = False
        elif table_score > text_score and margin >= self.auto_mode_margin_threshold:
            decision = "table"
            resolved_table_intent = True
            reason = "score_margin_table"
            needs_clarification = False
        elif text_score > table_score and margin >= self.auto_mode_margin_threshold:
            decision = "text"
            resolved_table_intent = False
            reason = "score_margin_text"
            needs_clarification = False
        else:
            decision = "table_hint" if table_intent_hint else "text_hint"
            resolved_table_intent = bool(table_intent_hint)
            reason = "insufficient_signal_fallback_to_hint"
            needs_clarification = False

        return {
            "auto_arbitration_version": "v1",
            "auto_arbitration_margin_threshold": round(self.auto_mode_margin_threshold, 6),
            "auto_arbitration_min_score": round(self.auto_mode_min_score, 6),
            "auto_arbitration_decision": decision,
            "auto_arbitration_reason": reason,
            "auto_arbitration_needs_clarification": needs_clarification,
            "auto_arbitration_table_intent": resolved_table_intent,
        }
