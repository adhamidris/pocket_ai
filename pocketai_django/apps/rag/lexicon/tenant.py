from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from typing import Mapping, Sequence

from django.conf import settings
from django.core.cache import cache
from django.db.models import Prefetch

from apps.accounts.models import BusinessProfile
from apps.knowledge.models import KnowledgeLexiconSynonym, KnowledgeLexiconTerm

logger = logging.getLogger(__name__)

_TOKEN_SPLIT = re.compile(r"[^\w]+", flags=re.UNICODE)
_SPACE_PATTERN = re.compile(r"\s+")
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_ARABIC_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩" + "۰۱۲۳۴۵۶۷۸۹",
    "0123456789" * 2,
)
_ARABIC_CHAR_TRANSLATION = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
        "ک": "ك",
        "ی": "ي",
        "ۀ": "ه",
        "ہ": "ه",
    }
)


def normalize_lexicon_text(value: str) -> str:
    """
    Canonical text normalization for lexicon comparisons.

    Keeps unicode letters, normalizes spacing, and applies Arabic-friendly
    canonicalization (digits, diacritics, and common glyph variants).
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    translated = unicodedata.normalize("NFKC", raw).translate(_ARABIC_DIGIT_TRANSLATION).casefold()
    if _ARABIC_CHAR_RE.search(translated):
        translated = _ARABIC_DIACRITICS_RE.sub("", translated)
        translated = translated.replace("ـ", "")
        translated = translated.translate(_ARABIC_CHAR_TRANSLATION)
    collapsed = _SPACE_PATTERN.sub(" ", translated).strip()
    tokens = [token for token in _TOKEN_SPLIT.split(collapsed) if token]
    normalized = " ".join(tokens)
    return normalized[:255]


def tokenize_lexicon_text(value: str, *, max_tokens: int = 128, dedupe: bool = False) -> tuple[str, ...]:
    normalized = normalize_lexicon_text(value)
    if not normalized:
        return tuple()
    tokens: list[str] = []
    seen: set[str] = set()
    for token in normalized.split(" "):
        item = token.strip()
        if not item:
            continue
        if dedupe and item in seen:
            continue
        seen.add(item)
        tokens.append(item)
        if len(tokens) >= max_tokens:
            break
    return tuple(tokens)


def normalize_language_code(value: str) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return "und"
    parts = [part for part in raw.split("-") if part]
    if not parts:
        return "und"
    return "-".join(part[:8] for part in parts)


def _validate_confidence(value: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence_score must be a float between 0 and 1.") from exc
    if score < 0.0 or score > 1.0:
        raise ValueError("confidence_score must be between 0 and 1.")
    return score


class TenantLexiconService:
    """
    Tenant-scoped lexicon persistence and snapshot helpers.

    Phase 2 foundation:
    - Persist canonical entity/attribute terms per tenant.
    - Persist tenant synonym forms with language and confidence.
    - Build a cached runtime snapshot for classifier/rewriter consumption.
    """

    cache_key_prefix = "rag:tenant-lexicon:v1"

    def __init__(self, *, cache_ttl: int | None = None) -> None:
        configured_ttl = (
            cache_ttl if cache_ttl is not None else int(getattr(settings, "RAG_TENANT_LEXICON_CACHE_TTL", 300))
        )
        self.cache_ttl = max(0, int(configured_ttl))

    @staticmethod
    def _business_id(business_profile: BusinessProfile) -> str:
        business_id = getattr(business_profile, "id", None)
        if business_id is None:
            raise ValueError("business_profile must be persisted before lexicon operations.")
        return str(business_id)

    @staticmethod
    def _term_type(term_type: str) -> str:
        normalized = str(term_type or "").strip().lower()
        valid = {choice for choice, _ in KnowledgeLexiconTerm.TermType.choices}
        if normalized not in valid:
            raise ValueError(f"Unsupported term_type '{term_type}'. Expected one of: {sorted(valid)}")
        return normalized

    def _cache_key(self, business_profile: BusinessProfile, *, include_inactive: bool) -> str:
        business_id = self._business_id(business_profile)
        return f"{self.cache_key_prefix}:{business_id}:{1 if include_inactive else 0}"

    def invalidate_cache(self, business_profile: BusinessProfile) -> None:
        cache.delete(self._cache_key(business_profile, include_inactive=False))
        cache.delete(self._cache_key(business_profile, include_inactive=True))

    def upsert_term(
        self,
        *,
        business_profile: BusinessProfile,
        term_type: str,
        canonical_text: str,
        language_code: str = "und",
        confidence_score: float = 0.7,
        source: str = "manual",
        metadata: Mapping[str, object] | None = None,
        is_active: bool = True,
        synonyms: Sequence[str] | None = None,
    ) -> KnowledgeLexiconTerm:
        self._business_id(business_profile)
        normalized = normalize_lexicon_text(canonical_text)
        if not normalized:
            raise ValueError("canonical_text must contain at least one valid token.")

        normalized_language = normalize_language_code(language_code)
        term_kind = self._term_type(term_type)
        score = _validate_confidence(confidence_score)

        term, _ = KnowledgeLexiconTerm.objects.update_or_create(
            business_profile=business_profile,
            term_type=term_kind,
            language_code=normalized_language,
            canonical_normalized=normalized,
            defaults={
                "canonical_text": str(canonical_text or "").strip()[:255],
                "confidence_score": score,
                "source": str(source or "manual").strip()[:40],
                "is_active": bool(is_active),
                "metadata": dict(metadata or {}),
            },
        )

        for synonym in synonyms or ():
            self._upsert_synonym(
                business_profile=business_profile,
                term=term,
                synonym_text=synonym,
                language_code=normalized_language,
                confidence_score=score,
                source=source,
                metadata=None,
                is_active=is_active,
                invalidate=False,
            )

        self.invalidate_cache(business_profile)
        return term

    def _upsert_synonym(
        self,
        *,
        business_profile: BusinessProfile,
        term: KnowledgeLexiconTerm,
        synonym_text: str,
        language_code: str = "und",
        confidence_score: float = 0.7,
        source: str = "manual",
        metadata: Mapping[str, object] | None = None,
        is_active: bool = True,
        invalidate: bool = True,
    ) -> KnowledgeLexiconSynonym:
        self._business_id(business_profile)
        if term.business_profile_id != business_profile.id:
            raise ValueError("term and business_profile must belong to the same tenant.")

        normalized = normalize_lexicon_text(synonym_text)
        if not normalized:
            raise ValueError("synonym_text must contain at least one valid token.")
        if normalized == term.canonical_normalized:
            raise ValueError("synonym_text cannot be identical to canonical term.")

        score = _validate_confidence(confidence_score)
        normalized_language = normalize_language_code(language_code)
        synonym, _ = KnowledgeLexiconSynonym.objects.update_or_create(
            term=term,
            language_code=normalized_language,
            synonym_normalized=normalized,
            defaults={
                "business_profile": business_profile,
                "synonym_text": str(synonym_text or "").strip()[:255],
                "confidence_score": score,
                "source": str(source or "manual").strip()[:40],
                "is_active": bool(is_active),
                "metadata": dict(metadata or {}),
            },
        )
        if invalidate:
            self.invalidate_cache(business_profile)
        return synonym

    def upsert_synonym(
        self,
        *,
        business_profile: BusinessProfile,
        term: KnowledgeLexiconTerm,
        synonym_text: str,
        language_code: str = "und",
        confidence_score: float = 0.7,
        source: str = "manual",
        metadata: Mapping[str, object] | None = None,
        is_active: bool = True,
    ) -> KnowledgeLexiconSynonym:
        return self._upsert_synonym(
            business_profile=business_profile,
            term=term,
            synonym_text=synonym_text,
            language_code=language_code,
            confidence_score=confidence_score,
            source=source,
            metadata=metadata,
            is_active=is_active,
            invalidate=True,
        )

    def get_snapshot(
        self,
        *,
        business_profile: BusinessProfile,
        include_inactive: bool = False,
        use_cache: bool = True,
    ) -> dict[str, object]:
        self._business_id(business_profile)
        cache_key = self._cache_key(business_profile, include_inactive=include_inactive)
        if use_cache:
            cached = cache.get(cache_key)
            if isinstance(cached, dict):
                return cached

        terms_qs = KnowledgeLexiconTerm.objects.filter(business_profile=business_profile).order_by(
            "term_type",
            "language_code",
            "canonical_normalized",
        )
        if not include_inactive:
            terms_qs = terms_qs.filter(is_active=True)

        synonyms_qs = KnowledgeLexiconSynonym.objects.filter(
            business_profile=business_profile,
            term__business_profile=business_profile,
        ).order_by(
            "synonym_normalized"
        )
        if not include_inactive:
            synonyms_qs = synonyms_qs.filter(is_active=True)

        terms = terms_qs.prefetch_related(Prefetch("synonyms", queryset=synonyms_qs, to_attr="_lex_synonyms"))

        by_type_language: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        term_count = 0
        synonym_count = 0
        isolation_filtered_synonyms = 0

        for term in terms:
            term_count += 1
            lang = normalize_language_code(term.language_code)
            by_type_language[term.term_type][lang].add(term.canonical_normalized)
            for synonym in getattr(term, "_lex_synonyms", []):
                if synonym.term_id != term.id or synonym.business_profile_id != business_profile.id:
                    isolation_filtered_synonyms += 1
                    continue
                synonym_count += 1
                syn_lang = normalize_language_code(synonym.language_code or lang)
                by_type_language[term.term_type][syn_lang].add(synonym.synonym_normalized)

        def _flatten(term_kind: str) -> list[str]:
            tokens: set[str] = set()
            for values in by_type_language.get(term_kind, {}).values():
                tokens.update(values)
            return sorted(tokens)

        frozen_by_type_language: dict[str, dict[str, tuple[str, ...]]] = {}
        all_languages: set[str] = set()
        for term_kind, language_map in by_type_language.items():
            frozen_by_type_language[term_kind] = {}
            for lang, values in language_map.items():
                all_languages.add(lang)
                frozen_by_type_language[term_kind][lang] = tuple(sorted(values))

        payload: dict[str, object] = {
            "business_profile_id": str(business_profile.id),
            "term_count": int(term_count),
            "synonym_count": int(synonym_count),
            "isolation_filtered_synonyms": int(isolation_filtered_synonyms),
            "languages": tuple(sorted(all_languages)),
            "entity_terms": tuple(_flatten(KnowledgeLexiconTerm.TermType.ENTITY)),
            "attribute_terms": tuple(_flatten(KnowledgeLexiconTerm.TermType.ATTRIBUTE)),
            "by_type_language": frozen_by_type_language,
        }
        if isolation_filtered_synonyms:
            logger.warning(
                "tenant_lexicon.isolation_filtered business=%s filtered_synonyms=%s",
                business_profile.id,
                isolation_filtered_synonyms,
            )
        if use_cache and self.cache_ttl > 0:
            cache.set(cache_key, payload, self.cache_ttl)
        return payload
