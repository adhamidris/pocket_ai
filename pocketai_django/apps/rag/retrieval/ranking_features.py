from __future__ import annotations

from collections import Counter
import math
import re
from typing import Any, Mapping, Sequence

from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.contracts import QueryTraits


DOCUMENT_NAME_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "a",
        "all",
        "an",
        "and",
        "any",
        "amount",
        "amounts",
        "by",
        "charge",
        "charges",
        "cost",
        "costs",
        "every",
        "fee",
        "fees",
        "for",
        "from",
        "give",
        "in",
        "list",
        "me",
        "of",
        "on",
        "or",
        "per",
        "price",
        "prices",
        "pricing",
        "rate",
        "rates",
        "schedule",
        "show",
        "table",
        "tables",
        "the",
        "their",
        "to",
        "value",
        "values",
        "what",
        "which",
        "with",
    }
)


class RankingFeatureMixin:
    def _text_quality_penalty(self, metadata: Mapping[str, Any]) -> float:
        if not metadata:
            return 0.0
        try:
            token_count = int(metadata.get("chunk_quality_tokens") or 0)
        except (TypeError, ValueError):
            token_count = 0
        score = None
        try:
            raw_score = metadata.get("chunk_quality_score")
            if raw_score is not None:
                score = float(raw_score)
                if not math.isfinite(score):
                    score = None
        except (TypeError, ValueError):
            score = None
        heading_only = bool(metadata.get("chunk_heading_only"))
        penalty = 0.0
        if score is not None and score < self.chunk_quality_low_score:
            scale = (self.chunk_quality_low_score - score) / max(self.chunk_quality_low_score, 0.001)
            penalty = max(penalty, min(self.text_chunk_penalty_max, scale * self.text_chunk_penalty_max))
        if token_count and token_count < self.chunk_quality_min_tokens:
            penalty = max(penalty, min(self.text_chunk_penalty_max, 0.2))
        if heading_only:
            penalty = max(penalty, min(self.text_chunk_penalty_max, 0.25))
        return penalty

    @staticmethod
    def _section_label_candidates(metadata: Mapping[str, Any]) -> tuple[str, ...]:
        if not metadata:
            return tuple()
        ordered: list[str] = []
        seen: set[str] = set()

        def _push(value: object) -> None:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if not text:
                return
            lowered = text.lower()
            if lowered in seen:
                return
            seen.add(lowered)
            ordered.append(text)

        _push(metadata.get("section_heading"))
        for key in ("section_headings", "heading_path"):
            raw = metadata.get(key)
            if isinstance(raw, (list, tuple, set)):
                for item in raw:
                    _push(item)
        return tuple(ordered[:8])

    def _section_context_boost(
        self,
        metadata: Mapping[str, Any],
        *,
        query_tokens: Sequence[str],
        query_text: str,
        focus_terms: Sequence[str],
    ) -> float:
        labels = self._section_label_candidates(metadata)
        if not labels:
            return 0.0
        best = 0.0
        normalized_query_text = (query_text or "").strip()
        normalized_tokens = tuple(str(token).strip().lower() for token in query_tokens if str(token).strip())
        normalized_focus_terms = tuple(str(term).strip().lower() for term in focus_terms if str(term).strip())
        for label in labels:
            lexical = self._lexical_score_text(label, normalized_tokens)
            phrase = self._exact_phrase_boost(label, normalized_query_text, max_boost=0.16)
            focus_lexical = 0.0
            focus_phrase = 0.0
            for term in normalized_focus_terms[:6]:
                term_tokens = tuple(part for part in term.split() if part)
                if term_tokens:
                    focus_lexical = max(focus_lexical, self._lexical_score_text(label, term_tokens))
                focus_phrase = max(focus_phrase, self._exact_phrase_boost(label, term, max_boost=0.12))
            label_score = min(0.3, (lexical * 0.12) + phrase + (focus_lexical * 0.14) + focus_phrase)
            best = max(best, label_score)
        return best

    @staticmethod
    def _lexical_overlap_score(chunk: KnowledgeUploadChunk, tokens: tuple[str, ...]) -> float:
        if not tokens:
            return 0.0
        text = (chunk.content or "").lower()
        if not text:
            return 0.0
        matches = sum(1 for token in tokens if token and token in text)
        if not matches:
            return 0.0
        return matches / len(tokens)

    @staticmethod
    def _entity_bonus(chunk: KnowledgeUploadChunk, tokens: tuple[str, ...], query_text: str) -> float:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        entity_name = (metadata.get("entity_name") or "").strip().lower()
        if not entity_name:
            return 0.0
        if query_text and entity_name in query_text.lower():
            return 0.5
        if tokens and any(token == entity_name or token in entity_name for token in tokens if token):
            return 0.4
        return 0.0

    @staticmethod
    def _alias_bonus(chunk: KnowledgeUploadChunk, tokens: tuple[str, ...], query_text: str) -> float:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        alias_blob = (metadata.get("alias_string") or "").lower()
        if not alias_blob:
            return 0.0
        bonus = 0.0
        if query_text:
            query_normalized = query_text.lower().replace(" ", "-")
            if query_normalized and query_normalized in alias_blob:
                bonus += 0.4
        if tokens and any(token in alias_blob for token in tokens if token):
            bonus += 0.2
        return min(bonus, 0.6)

    @staticmethod
    def _lexical_score_text(text: str, tokens: tuple[str, ...]) -> float:
        if not text or not tokens:
            return 0.0
        lowered = text.lower()
        matches = sum(1 for token in tokens if token and token.lower() in lowered)
        if not matches:
            return 0.0
        return matches / len(tokens)

    def _document_name_relevance_tokens(self, traits: QueryTraits) -> tuple[str, ...]:
        generic_tokens = DOCUMENT_NAME_GENERIC_TOKENS | set(self.table_query_keywords)
        relevance_tokens: list[str] = []
        seen: set[str] = set()
        for token in traits.tokens:
            normalized = str(token or "").strip().lower()
            if len(normalized) < 2 or normalized in generic_tokens or normalized in seen:
                continue
            seen.add(normalized)
            relevance_tokens.append(normalized)
        return tuple(relevance_tokens)

    @staticmethod
    def _normalized_match_text(text: str) -> str:
        cleaned = re.sub(r"[^\w%$ ]+", " ", (text or "").lower(), flags=re.UNICODE)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _exact_phrase_boost(
        self,
        text: str,
        query_text: str,
        *,
        max_boost: float = 0.45,
    ) -> float:
        normalized_text = self._normalized_match_text(text)
        normalized_query = self._normalized_match_text(query_text)
        if not normalized_text or not normalized_query:
            return 0.0
        query_terms = [term for term in normalized_query.split(" ") if term]
        if len(query_terms) < 2:
            return 0.0
        if len(normalized_query) < 10:
            return 0.0
        if normalized_query in normalized_text:
            return max_boost
        ngrams: list[str] = []
        max_n = min(4, len(query_terms))
        for n in range(max_n, 1, -1):
            for idx in range(0, len(query_terms) - n + 1):
                phrase = " ".join(query_terms[idx : idx + n]).strip()
                if len(phrase) >= 10:
                    ngrams.append(phrase)
        for phrase in ngrams:
            if phrase in normalized_text:
                return max_boost * 0.8
        return 0.0

    def _token_proximity_boost(
        self,
        text: str,
        tokens: Sequence[str],
        *,
        max_boost: float = 0.25,
    ) -> float:
        lowered = (text or "").lower()
        if not lowered or not tokens:
            return 0.0
        significant_tokens = tuple(
            token.lower()
            for token in tokens
            if token and len(token) >= self.table_specific_min_length
        )
        if len(significant_tokens) < 2:
            return 0.0
        positions: list[tuple[int, str]] = []
        for token in significant_tokens[:10]:
            cursor = lowered.find(token)
            while cursor != -1:
                positions.append((cursor, token))
                cursor = lowered.find(token, cursor + len(token))
                if len(positions) >= 200:
                    break
            if len(positions) >= 200:
                break
        if len(positions) < 2:
            return 0.0
        positions.sort(key=lambda item: item[0])
        left = 0
        token_counter: Counter[str] = Counter()
        best_unique = 0
        best_span = None
        window_chars = 260
        for right, (pos, token) in enumerate(positions):
            token_counter[token] += 1
            while left <= right and (pos - positions[left][0]) > window_chars:
                left_token = positions[left][1]
                token_counter[left_token] -= 1
                if token_counter[left_token] <= 0:
                    token_counter.pop(left_token, None)
                left += 1
            unique = len(token_counter)
            span = pos - positions[left][0] if left <= right else 0
            if unique > best_unique or (unique == best_unique and (best_span is None or span < best_span)):
                best_unique = unique
                best_span = span
        if best_unique <= 1:
            return 0.0
        unique_ratio = best_unique / max(1, len(set(significant_tokens[:10])))
        span_factor = 1.0
        if best_span is not None and best_span > 0:
            span_factor = min(1.0, 180.0 / float(best_span))
        return max_boost * unique_ratio * span_factor

    def _best_text_evidence_span(
        self,
        text: str,
        *,
        query_text: str,
        tokens: Sequence[str],
        max_chars: int = 280,
    ) -> str:
        raw_text = (text or "").strip()
        if not raw_text:
            return ""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return ""
        normalized_query = self._normalized_match_text(query_text)
        significant_tokens = tuple(
            token.lower()
            for token in tokens
            if token and len(token) >= self.table_specific_min_length
        )
        if not normalized_query and not significant_tokens:
            return ""

        def _line_score(candidate: str) -> float:
            lower = candidate.lower()
            lexical = self._lexical_score_text(lower, tuple(significant_tokens))
            phrase = self._exact_phrase_boost(lower, normalized_query, max_boost=1.0) if normalized_query else 0.0
            proximity = self._token_proximity_boost(lower, significant_tokens, max_boost=0.6)
            return lexical + phrase + proximity

        best_text = ""
        best_score = 0.0

        for idx, line in enumerate(lines):
            score = _line_score(line)
            if score > best_score:
                best_score = score
                best_text = line
            if idx + 1 < len(lines):
                paired = f"{line} {lines[idx + 1]}".strip()
                pair_score = _line_score(paired)
                if pair_score > best_score:
                    best_score = pair_score
                    best_text = paired

        if best_score <= 0.0:
            return ""
        evidence = re.sub(r"\s+", " ", best_text).strip()
        if len(evidence) > max_chars:
            evidence = evidence[:max_chars].rstrip() + "…"
        return evidence
