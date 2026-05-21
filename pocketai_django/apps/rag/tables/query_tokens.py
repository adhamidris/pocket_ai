from __future__ import annotations

import uuid
from typing import Sequence

from apps.rag.contracts import QueryTraits
from apps.rag.query.normalizer import QueryNormalizer
from apps.rag.tables.semantics import normalize_column_name
from apps.rag.tables.token_profiles import TableTokenProfileMixin
from apps.rag.lexicon.text_utils import is_plural_candidate, singularize


class TableQueryTokenMixin(TableTokenProfileMixin):

    @staticmethod
    def _table_tokenize(value: str) -> set[str]:
        if not value:
            return set()
        normalized = QueryNormalizer._normalize_query_text(str(value))
        normalized = normalized.replace("_", " ").replace("-", " ")
        lowered = normalized.lower()
        return {token for token in QueryNormalizer._TOKEN_SPLIT.split(lowered) if token}

    def _table_query_tokens(
        self,
        business_profile,
        traits: QueryTraits,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> tuple[set[str], set[str]]:
        tokens = {token.lower() for token in traits.tokens if token}
        filler = self._filler_tokens_for_business(business_profile)
        tokens = {token for token in tokens if token not in filler and not token.isdigit()}
        normalized_tokens: set[str] = set()
        for token in tokens:
            if is_plural_candidate(token):
                normalized_tokens.add(singularize(token))
                continue
            normalized_tokens.add(token)
        tokens = normalized_tokens
        generic = set(self.table_query_keywords)
        generic.update(
            {
                "what",
                "which",
                "how",
                "when",
                "where",
                "who",
                "whats",
                "what's",
                "is",
                "are",
                "was",
                "were",
                "do",
                "does",
                "did",
                "can",
                "could",
                "should",
                "would",
                "egp",
                "usd",
                "eur",
                "gbp",
                "aed",
                "sar",
                "qar",
                "kwd",
                "bhd",
                "omr",
                "jod",
                # Product-category words are too generic to act as "specific table" anchors.
                # Keeping them out of `specific_tokens` prevents wrong-table drift like
                # "Heya credit card" -> matching any table that has a `card_type` column.
                "card",
                "cards",
                "credit",
                "debit",
            }
        )
        generic.update(
            self._table_generic_tokens_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
        )
        specific = {
            token
            for token in tokens
            if token not in generic and len(token) >= self.table_specific_min_length
        }
        return tokens, specific

    def _column_matches_tokens(self, column: str | None, tokens: set[str]) -> bool:
        if not column or not tokens:
            return False
        normalized = normalize_column_name(column)
        source = normalized or str(column)
        column_tokens = self._table_tokenize(source)
        if column_tokens & tokens:
            return True
        condensed = (normalized or "").replace("_", "")
        if condensed and any(token in condensed for token in tokens if token):
            return True
        return False

    def _is_usable_table_query_column_match(self, column: str | None) -> bool:
        if not column:
            return False
        normalized = normalize_column_name(column)
        source = (normalized or str(column or "")).strip().lower()
        if not source:
            return False
        if len(source) <= 1:
            return False
        tokens = {token for token in self._table_tokenize(source) if token}
        if tokens and max((len(token) for token in tokens), default=0) <= 1:
            return False
        return True

    def _has_strong_row_label_signal(
        self,
        *,
        matched_row_labels: set[str],
        specific_tokens: set[str],
    ) -> bool:
        if not matched_row_labels:
            return False
        if len(matched_row_labels) >= 2:
            return True
        if not specific_tokens:
            return False
        overlap_ratio = len(matched_row_labels) / max(len(specific_tokens), 1)
        return overlap_ratio > 0.5
