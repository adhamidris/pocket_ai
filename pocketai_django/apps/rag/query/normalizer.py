from __future__ import annotations

import re
import unicodedata
from typing import Sequence

from django.conf import settings

from apps.rag.contracts import QueryTraits


class QueryNormalizer:
    _TOKEN_SPLIT = re.compile(r"[^\w]+", flags=re.UNICODE)
    _SPACE_PATTERN = re.compile(r"\s+")
    _IDENTIFIER_PATTERN = re.compile(r"[a-z0-9][a-z0-9_\\-]{2,}")
    _ARABIC_DIGIT_TRANSLATION = str.maketrans(
        "٠١٢٣٤٥٦٧٨٩" + "۰۱۲۳۴۵۶۷۸۹",
        "0123456789" * 2,
    )
    _ALIAS_FILLER_BASE = {
        "the",
        "a",
        "an",
        "of",
        "for",
        "on",
        "in",
        "about",
        "info",
        "information",
        "details",
        "overview",
        "summary",
        "feature",
        "features",
        "benefit",
        "benefits",
        "requirement",
        "requirements",
        "eligibility",
        "eligible",
        "compare",
        "comparison",
        "help",
        "find",
        "looking",
        "search",
        "show",
        "give",
        "get",
        "need",
        "want",
        "please",
        "tell",
        "list",
        "listing",
        "provide",
        "latest",
        "new",
        "any",
        "some",
        "with",
        "and",
        "to",
    }

    @classmethod
    def _alias_filler_tokens(cls) -> set[str]:
        configured = getattr(settings, "RAG_ALIAS_FILLER_TOKENS", None)
        tokens: set[str] = set(cls._ALIAS_FILLER_BASE)
        if isinstance(configured, (list, tuple, set)):
            tokens.update(str(item).strip().lower() for item in configured if str(item).strip())
        return tokens

    @classmethod
    def normalize(cls, query: str, *, filler_tokens: Sequence[str] | None = None) -> QueryTraits:
        original = (query or "").strip()
        normalized_source = cls._normalize_query_text(original)
        lowered = normalized_source.lower()
        collapsed = cls._SPACE_PATTERN.sub(" ", lowered).strip()
        tokens = tuple(token for token in cls._TOKEN_SPLIT.split(collapsed) if token)
        alias_candidates = cls._alias_candidates(original, tokens, filler_tokens=filler_tokens)
        has_digits = any(ch.isdigit() for ch in collapsed)
        has_dashes = "-" in collapsed
        has_underscores = "_" in collapsed
        is_identifier_like = cls._is_identifier_like(
            alias_candidates,
            tokens,
            has_digits=has_digits,
            has_dashes=has_dashes,
            has_underscores=has_underscores,
        )
        normalized = collapsed or normalized_source or original
        return QueryTraits(
            original=original,
            normalized=normalized,
            tokens=tokens,
            alias_candidates=alias_candidates,
            token_count=len(tokens),
            has_digits=has_digits,
            has_dashes=has_dashes,
            has_underscores=has_underscores,
            is_identifier_like=is_identifier_like,
        )

    @classmethod
    def _alias_candidates(
        cls,
        original: str,
        tokens: Sequence[str],
        *,
        filler_tokens: Sequence[str] | None = None,
    ) -> tuple[str, ...]:
        ordered: dict[str, None] = {}
        raw_candidates = [original]
        raw_candidates.extend(tokens)

        def _add_candidate(value: str) -> None:
            canonical = cls._canonical_alias(value)
            if not canonical:
                return
            for variant in cls._alias_variants(canonical):
                if variant and variant not in ordered:
                    ordered[variant] = None

        for candidate in raw_candidates:
            _add_candidate(candidate)

        fillers = set(filler_tokens) if filler_tokens else cls._alias_filler_tokens()
        filtered_tokens = [token for token in tokens if token and token not in fillers]
        for n in (2, 3):
            for idx in range(len(filtered_tokens) - n + 1):
                window = filtered_tokens[idx : idx + n]
                _add_candidate(" ".join(window))
        return tuple(ordered.keys())

    @classmethod
    def _normalize_query_text(cls, value: str) -> str:
        if not value:
            return ""
        text = unicodedata.normalize("NFKC", value)
        text = text.translate(cls._ARABIC_DIGIT_TRANSLATION)
        text = text.replace("\u0640", "")  # tatweel
        text = text.replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
        return text

    @classmethod
    def _canonical_alias(cls, value: str) -> str:
        if not value:
            return ""
        lowered = cls._normalize_query_text(value).strip().lower()
        if not lowered:
            return ""
        sanitized = re.sub(r"[^\w\-\s]", "", lowered, flags=re.UNICODE)
        sanitized = sanitized.replace("_", "-")
        sanitized = cls._SPACE_PATTERN.sub("-", sanitized)
        sanitized = re.sub(r"-{2,}", "-", sanitized)
        return sanitized.strip("-")

    @staticmethod
    def _alias_variants(value: str) -> tuple[str, ...]:
        variants: dict[str, None] = {}
        for form in (value, value.replace("-", "_"), value.replace("-", ""), value.replace("_", ""), value.replace("_", "-")):
            if form and form not in variants:
                variants[form] = None
        return tuple(variants.keys())

    @classmethod
    def _is_identifier_like(
        cls,
        alias_candidates: Sequence[str],
        tokens: Sequence[str],
        *,
        has_digits: bool,
        has_dashes: bool,
        has_underscores: bool,
    ) -> bool:
        if not tokens:
            return False
        # Strong signal: digit-bearing IDs with separators (e.g., TRIP-101, INV_123).
        if has_digits and (has_dashes or has_underscores):
            return True
        if len(tokens) <= 3 and (has_digits or has_dashes or has_underscores):
            return True
        for candidate in alias_candidates:
            # Avoid flagging plain multiword names that normalize into hyphenated forms (e.g., "Grand Luxor").
            if candidate and cls._IDENTIFIER_PATTERN.fullmatch(candidate) and any(ch.isdigit() for ch in candidate):
                return True
        return any(
            token and cls._IDENTIFIER_PATTERN.fullmatch(token) and any(ch.isdigit() for ch in token)
            for token in tokens
        )
