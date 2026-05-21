from __future__ import annotations

import re
from typing import Iterable

from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.decision.scope_metadata import ScopeMetadataMixin
from apps.rag.decision.scope_summary import ScopeSummaryMixin
from apps.rag.query.normalizer import QueryNormalizer
from apps.rag.lexicon.text_utils import is_plural_candidate, singularize


class SearchAutoScopeMixin(ScopeSummaryMixin, ScopeMetadataMixin):
    @classmethod
    def _scope_key_value_pairs(cls, content: str, *, max_pairs: int = 12) -> tuple[tuple[str, str], ...]:
        text = str(content or "")
        if not text:
            return tuple()
        pairs: list[tuple[str, str]] = []
        window = text[:1600]
        for match in re.finditer(r"([^\n:;|]{1,72})\s*:\s*([^;\n|]{1,220})", window):
            key = cls._normalize_topic_value(match.group(1))
            value = cls._normalize_scope_category_value(
                match.group(2),
                max_chars=120,
            )
            if not key or not value:
                continue
            pairs.append((key, value))
            if len(pairs) >= max_pairs:
                break
        return tuple(pairs)

    @staticmethod
    def _scope_upload_identity(chunk: KnowledgeUploadChunk) -> str:
        upload_id = getattr(chunk, "upload_id", None)
        if upload_id:
            return str(upload_id)
        upload = getattr(chunk, "upload", None)
        if upload is not None and getattr(upload, "id", None):
            return str(upload.id)
        return ""

    @classmethod
    def _scope_label_stop_tokens(cls) -> set[str]:
        return {
            "and",
            "or",
            "for",
            "from",
            "to",
            "in",
            "on",
            "with",
            "by",
            "the",
            "a",
            "an",
            "و",
            "او",
            "أو",
            "من",
            "في",
            "على",
            "الى",
            "إلى",
            "عن",
            "ال",
            "en",
            "ar",
            "fr",
            "de",
            "es",
            "it",
            "pt",
            "ru",
            "tr",
            "zh",
            "ja",
        }

    @staticmethod
    def _canonical_scope_token(value: object) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return ""
        if is_plural_candidate(token):
            token = singularize(token)
        return token.strip()

    @classmethod
    def _canonical_scope_token_set(cls, values: Iterable[object]) -> set[str]:
        canonical: set[str] = set()
        for raw in values:
            token = cls._canonical_scope_token(raw)
            if token:
                canonical.add(token)
        return canonical

    @classmethod
    def _normalize_scope_category_value(
        cls,
        value: object,
        *,
        max_chars: int = 96,
    ) -> str:
        """
        Normalize candidate scope labels into a stable, user-facing category key.
        """
        cleaned = cls._clean_auto_evidence_label(value, max_chars=max_chars)
        if not cleaned:
            return ""

        normalized = cls._normalize_topic_value(cleaned)
        if not normalized:
            return ""

        normalized = re.sub(r"\b(?:table|sheet|tab)\s*\d*\b", " ", normalized)
        normalized = re.sub(r"\b([a-z]{3,}s)(?:en|ar|fr|de|es)\b", r"\1", normalized)
        normalized = re.sub(r"[^0-9a-z\u0600-\u06FF]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return ""

        stop_tokens = cls._scope_label_stop_tokens()
        tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if token]
        selected: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            lowered = token.strip().lower()
            if not lowered:
                continue
            if lowered in stop_tokens:
                continue
            if len(lowered) == 1 and not lowered.isdigit():
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            selected.append(lowered)
            if len(selected) >= 10:
                break

        if not selected:
            return normalized
        return " ".join(selected)

    def _scope_is_specific_category(
        self,
        label: str,
        *,
        business_profile,
        query_tokens: set[str],
        filler_tokens: set[str],
    ) -> bool:
        normalized = self._normalize_topic_value(label)
        if not normalized:
            return False
        raw_tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if token]
        if not raw_tokens:
            return False
        canonical_filler_tokens = self._canonical_scope_token_set(filler_tokens)
        canonical_generic_tokens = self._canonical_scope_token_set(
            self._scope_generic_tokens_for_business(business_profile),
        )
        canonical_query_tokens = self._canonical_scope_token_set(query_tokens)

        tokens: list[str] = []
        for token in raw_tokens:
            canonical = self._canonical_scope_token(token)
            if not canonical or canonical in canonical_filler_tokens:
                continue
            tokens.append(canonical)
        if not tokens:
            return False
        non_generic = [token for token in tokens if token not in canonical_generic_tokens]
        if not non_generic:
            return False
        if len(non_generic) <= 2 and all(token in canonical_query_tokens for token in non_generic):
            return False
        return True

