from __future__ import annotations

from django.conf import settings
from django.db import connection

from apps.rag.contracts import QueryTraits
from apps.rag.query_normalizer import QueryNormalizer


class SearchConfigMixin:
    def _business_override(self, business_profile, key: str, default: int | float) -> int | float:
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if not isinstance(overrides, dict):
            return default
        value = overrides.get(key)
        if isinstance(value, (int, float)):
            try:
                return type(default)(value)
            except (TypeError, ValueError):
                return default
        return default

    def _tenant_lexicon_tables_ready(self) -> bool:
        cached = self._tenant_lexicon_tables_ready_cache
        if cached is not None:
            return cached
        try:
            table_names = set(connection.introspection.table_names())
        except Exception:
            self._tenant_lexicon_tables_ready_cache = False
            return False
        required = {
            "accounts_knowledge_lexicon_term",
            "accounts_knowledge_lexicon_synonym",
        }
        ready = required.issubset(table_names)
        self._tenant_lexicon_tables_ready_cache = ready
        return ready

    def _snippet_limit_for_business(self, business_profile, requested: int | None = None) -> int:
        base = requested or self.search_snippet_limit
        override = self._business_override(business_profile, "max_snippets_per_search", base)
        return max(1, int(override))

    def _effective_chunk_cap(self, business_profile, pathway: str) -> int:
        if pathway == "alias":
            default = self.alias_chunks_per_upload_default
            key = "alias_chunks_per_upload"
        else:
            default = self.ann_chunks_per_upload_default
            key = "ann_chunks_per_upload"
        override = self._business_override(business_profile, key, default)
        return max(1, int(override))

    def _alias_threshold_for_business(self, business_profile) -> float:
        return float(self._business_override(business_profile, "alias_fts_threshold", self.alias_fts_threshold))

    def _vector_ceiling_for_business(self, business_profile) -> float:
        return float(self._business_override(business_profile, "vector_distance_ceiling", self.vector_distance_ceiling))

    def _significant_token_min_length(self, business_profile) -> int:
        default = 4
        override = self._business_override(business_profile, "fts_token_min_length", default)
        return max(2, int(override))

    def _fts_condense_max_tokens(self, business_profile) -> int:
        default = 5
        override = self._business_override(business_profile, "fts_condense_max_tokens", default)
        return max(2, int(override))

    def _lexical_threshold_for_business(self, business_profile, traits: QueryTraits) -> float:
        short_default = float(getattr(settings, "RAG_LEXICAL_THRESHOLD_SHORT", 0.25))
        mid_default = float(getattr(settings, "RAG_LEXICAL_THRESHOLD_MEDIUM", 0.2))
        long_default = float(getattr(settings, "RAG_LEXICAL_THRESHOLD_LONG", 0.15))
        if traits.token_count <= 3:
            return float(self._business_override(business_profile, "lexical_threshold_short", short_default))
        if traits.token_count <= 6:
            return float(self._business_override(business_profile, "lexical_threshold_medium", mid_default))
        return float(self._business_override(business_profile, "lexical_threshold_long", long_default))

    def _filler_tokens_for_business(self, business_profile) -> set[str]:
        tokens = QueryNormalizer._alias_filler_tokens()
        if not business_profile:
            return tokens
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if isinstance(overrides, dict):
            extra = overrides.get("alias_filler_tokens")
            if isinstance(extra, (list, tuple, set)):
                tokens.update(str(item).strip().lower() for item in extra if str(item).strip())
        return tokens
