from __future__ import annotations

from collections import OrderedDict, Counter

from django.conf import settings
from django.core.cache import cache

import time
import logging
import uuid
import math
from typing import Any, Mapping, MutableMapping, Sequence

from django.db import connection, transaction
from django.db.utils import DatabaseError

from apps.accounts.models import (
    KnowledgeStatus,
    KnowledgeVisibility,
)
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeTableColumn,
    KnowledgeUploadTableRow,
)
from apps.rag.auto_decision import (
    SCOPE_CATEGORY_MAX_DEFAULT,
    SCOPE_TOP_CATEGORY_MAX_DEFAULT,
    SearchAutoDecisionMixin,
)
from apps.rag.alias_retrieval import AliasRetrievalMixin
from apps.rag.embeddings import build_embedding_service
from apps.rag.auto_scoring import SearchAutoScoringMixin
from apps.rag.auto_scope import SearchAutoScopeMixin
from apps.rag.candidate_retrieval import CandidateRetrievalMixin
from apps.rag.content_reading import ContentReadingMixin
from apps.rag.content_serialization import ContentSerializationMixin
from apps.rag.evidence_grouping import EvidenceGroupingMixin
from apps.rag.metadata_helpers import KnowledgeMetadataMixin
from apps.rag.ranking_features import RankingFeatureMixin
from apps.rag.reranking import RerankingMixin
from apps.rag.snippet_builder import SnippetBuilderMixin
from apps.rag.snippet_selection import SnippetSelectionMixin
from apps.rag.table_expansion import TableExpansionMixin
from apps.rag.table_snippets import TableSnippetMixin
from apps.rag.search_observability import SearchObservabilityMixin
from apps.accounts.feature_flags import FeatureFlagService
from apps.rag.rag_logging import rag_log
from apps.rag.query_normalizer import QueryNormalizer
from apps.rag.query_signals import QuerySignalMixin
from apps.rag.contracts import (
    MAX_INLINE_KNOWLEDGE_CHARS,
    AliasSearchResult,
    KnowledgeSearchResult,
    KnowledgeSnippet,
    QueryTraits,
)
from apps.rag.intent_fallback import IntentFallbackService
from apps.rag.tenant_lexicon import TenantLexiconService
from apps.rag.retrieval_strategies import StrategyRouter, RetrievalContext, RetrievalHints
from apps.rag.search_cache import KnowledgeSearchCacheMixin
from apps.rag.table_context import TableContextMixin
from core.tenancy import tenant_context
from core.otel import otel_trace


logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

def _rag_log(
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)

# NOTE:
# This module still contains the active knowledge search implementation. Import
# it through apps.rag.knowledge_search so this file can keep shrinking without
# changing callers.


class KnowledgeSearchService(
    RerankingMixin,
    AliasRetrievalMixin,
    SearchAutoScoringMixin,
    SearchAutoScopeMixin,
    CandidateRetrievalMixin,
    QuerySignalMixin,
    SearchAutoDecisionMixin,
    RankingFeatureMixin,
    SnippetSelectionMixin,
    SnippetBuilderMixin,
    EvidenceGroupingMixin,
    SearchObservabilityMixin,
    TableContextMixin,
    TableExpansionMixin,
    TableSnippetMixin,
    KnowledgeSearchCacheMixin,
    KnowledgeMetadataMixin,
    ContentReadingMixin,
    ContentSerializationMixin,
):
    """Chunk-aware RAG search leveraging extracted knowledge uploads."""

    def __init__(self) -> None:
        self.embedding_service = build_embedding_service()
        self.max_chunks_per_upload = max(
            1,
            int(getattr(settings, "RAG_MAX_CHUNKS_PER_UPLOAD", 2)),
        )
        self.search_snippet_limit = max(1, int(getattr(settings, "RAG_MAX_SNIPPETS_PER_SEARCH", 3)))
        self.alias_chunks_per_upload_default = max(
            1,
            int(getattr(settings, "RAG_ALIAS_MAX_CHUNKS_PER_UPLOAD", self.max_chunks_per_upload)),
        )
        self.ann_chunks_per_upload_default = max(
            1,
            int(getattr(settings, "RAG_ANN_MAX_CHUNKS_PER_UPLOAD", self.max_chunks_per_upload)),
        )
        self.search_preview_char_limit = max(200, int(getattr(settings, "RAG_SEARCH_PREVIEW_CHAR_LIMIT", 800)))
        self.token_gate_fallback = max(
            0,
            int(getattr(settings, "RAG_TOKEN_GATE_FALLBACK", 6)),
        )
        self.rerank_pool = max(10, int(getattr(settings, "RAG_RERANK_POOL", 60)))
        self.mmr_lambda = float(getattr(settings, "RAG_MMR_LAMBDA", 0.7))
        self.mmr_preserve_head = max(0, int(getattr(settings, "RAG_MMR_PRESERVE_HEAD", 3)))
        self.entity_neighbor_min = max(1, int(getattr(settings, "RAG_ENTITY_NEIGHBOR_MIN", 2)))
        self.vector_distance_ceiling = float(getattr(settings, "RAG_VECTOR_DISTANCE_CEILING", 0.5))
        self.short_query_ann_multiplier = float(getattr(settings, "RAG_SHORT_QUERY_ANN_MULTIPLIER", 3.0))
        self.read_ready_threshold = max(200, int(getattr(settings, "RAG_READY_CHAR_THRESHOLD", 900)))
        self.table_ready_threshold = max(200, int(getattr(settings, "RAG_READY_TABLE_THRESHOLD", 600)))
        self.alias_result_cap = max(1, int(getattr(settings, "RAG_ALIAS_RESULTS_LIMIT", 4)))
        self.alias_neighbor_window = max(1, int(getattr(settings, "RAG_ALIAS_NEIGHBOR_WINDOW", 1)))
        self.alias_cache_ttl = max(60, int(getattr(settings, "RAG_ALIAS_CACHE_TTL", 900)))
        self.inline_char_limit_default = max(
            200,
            int(getattr(settings, "RAG_MAX_INLINE_KNOWLEDGE_CHARS", MAX_INLINE_KNOWLEDGE_CHARS)),
        )
        self.chunk_neighbor_window_default = max(0, int(getattr(settings, "RAG_CHUNK_NEIGHBOR_WINDOW", 1)))
        self.page_char_limit_default = max(500, int(getattr(settings, "RAG_PAGE_CHAR_LIMIT", 6000)))
        self.page_summary_cache_limit = max(32, int(getattr(settings, "RAG_PAGE_SUMMARY_CACHE_SIZE", 128)))
        self.alias_fts_limit = max(5, int(getattr(settings, "RAG_ALIAS_FTS_LIMIT", 20)))
        self.alias_fts_threshold = float(getattr(settings, "RAG_ALIAS_FTS_THRESHOLD", 0.25))
        self.query_vector_cache_ttl = max(60, int(getattr(settings, "RAG_QUERY_VECTOR_CACHE_TTL", 300)))
        self.query_vector_cache_max_bytes = max(1024, int(getattr(settings, "RAG_QUERY_VECTOR_CACHE_MAX_BYTES", 16384)))
        self.ivfflat_probes = max(1, int(getattr(settings, "RAG_IVFFLAT_PROBES", 8)))
        self.result_cache_ttl = max(60, int(getattr(settings, "RAG_RESULT_CACHE_TTL", 900)))
        self.result_cache_enabled = bool(getattr(settings, "RAG_RESULT_CACHE_ENABLED", True))
        self.session_cache_limit = max(8, int(getattr(settings, "RAG_SESSION_CACHE_LIMIT", 64)))
        self.rerank_weights = {
            "vector": float(getattr(settings, "RAG_WEIGHT_VECTOR", 1.0)),
            "lexical": float(getattr(settings, "RAG_WEIGHT_LEXICAL", 0.8)),
            "alias": float(getattr(settings, "RAG_WEIGHT_ALIAS", 1.2)),
            "entity": float(getattr(settings, "RAG_WEIGHT_ENTITY", 0.4)),
            "recency": float(getattr(settings, "RAG_WEIGHT_RECENCY", 0.25)),
            "document_name": float(getattr(settings, "RAG_WEIGHT_DOCUMENT_NAME", 0.35)),
        }
        self.recency_decay_days = float(getattr(settings, "RAG_RECENCY_DECAY_DAYS", 90))
        self.recency_min_floor = float(getattr(settings, "RAG_RECENCY_MIN_FLOOR", 0.05))
        self.recency_bonus_fresh = float(getattr(settings, "RAG_RECENCY_BONUS_FRESH", 0.15))
        self.evidence_grouping_enabled = bool(getattr(settings, "RAG_EVIDENCE_GROUPING_ENABLED", True))
        self.evidence_conflict_min_overlap = float(
            getattr(settings, "RAG_EVIDENCE_CONFLICT_MIN_OVERLAP", 0.25)
        )
        if not (0.0 <= self.evidence_conflict_min_overlap <= 1.0):
            self.evidence_conflict_min_overlap = 0.25
        self.business_override_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
        self.window_cache_limit = max(32, int(getattr(settings, "RAG_NEIGHBOR_WINDOW_CACHE_SIZE", 128)))
        self._window_cache: OrderedDict[tuple[uuid.UUID, uuid.UUID, int, int], list[KnowledgeUploadChunk]] = OrderedDict()
        self.structured_count_cache_limit = 256
        self._structured_count_cache: OrderedDict[tuple[uuid.UUID, uuid.UUID], tuple[int, int]] = OrderedDict()
        self.table_result_cap = max(3, int(getattr(settings, "RAG_TABLE_RESULT_LIMIT", 12)))
        self.table_similarity_threshold = float(getattr(settings, "RAG_TABLE_SIMILARITY_THRESHOLD", 0.3))
        self.table_column_cache_limit = max(8, int(getattr(settings, "RAG_TABLE_COLUMN_CACHE_SIZE", 32)))
        self.table_column_sample_limit = max(25, int(getattr(settings, "RAG_TABLE_COLUMN_SAMPLE", 200)))
        self._table_column_cache: OrderedDict[tuple[uuid.UUID, str], set[str]] = OrderedDict()
        self.table_rerank_floor = float(getattr(settings, "RAG_TABLE_RERANK_FLOOR", 0.35))
        self.chunk_quality_min_tokens = max(1, int(getattr(settings, "RAG_CHUNK_MIN_TOKENS", 20)))
        self.chunk_quality_low_score = float(getattr(settings, "RAG_CHUNK_LOW_QUALITY_SCORE", 0.45))
        if not (0.0 <= self.chunk_quality_low_score <= 1.0):
            self.chunk_quality_low_score = 0.45
        self.text_chunk_penalty_max = float(getattr(settings, "RAG_TEXT_CHUNK_PENALTY_MAX", 0.35))
        if self.text_chunk_penalty_max <= 0.0:
            self.text_chunk_penalty_max = 0.35
        self.table_quality_threshold = float(getattr(settings, "RAG_TABLE_QUALITY_THRESHOLD", 0.5))
        if not (0.0 < self.table_quality_threshold <= 1.0):
            self.table_quality_threshold = 0.5
        self.table_residual_penalty = float(getattr(settings, "RAG_TABLE_RESIDUAL_PENALTY", 0.12))
        if self.table_residual_penalty < 0.0:
            self.table_residual_penalty = 0.0
        self.table_residual_table_intent_penalty = float(
            getattr(settings, "RAG_TABLE_RESIDUAL_TABLE_INTENT_PENALTY", 0.28)
        )
        if self.table_residual_table_intent_penalty < 0.0:
            self.table_residual_table_intent_penalty = 0.0
        self.table_residual_rescue_enabled = bool(
            getattr(settings, "RAG_TABLE_RESIDUAL_RESCUE_ENABLED", True)
        )
        self.table_residual_rescue_bonus = float(
            getattr(settings, "RAG_TABLE_RESIDUAL_RESCUE_BONUS", 0.18)
        )
        if self.table_residual_rescue_bonus < 0.0:
            self.table_residual_rescue_bonus = 0.0
        self.table_residual_rescue_phrase_min = float(
            getattr(settings, "RAG_TABLE_RESIDUAL_RESCUE_PHRASE_MIN", 0.24)
        )
        if self.table_residual_rescue_phrase_min < 0.0:
            self.table_residual_rescue_phrase_min = 0.0
        self.table_residual_rescue_lexical_min = float(
            getattr(settings, "RAG_TABLE_RESIDUAL_RESCUE_LEXICAL_MIN", 0.42)
        )
        if self.table_residual_rescue_lexical_min < 0.0:
            self.table_residual_rescue_lexical_min = 0.0
        self.table_residual_rescue_max_results = max(
            1,
            int(getattr(settings, "RAG_TABLE_RESIDUAL_RESCUE_MAX_RESULTS", 1)),
        )
        self.table_vector_floor = float(getattr(settings, "RAG_TABLE_VECTOR_FLOOR", 0.45))
        self.table_chunk_sample_limit = max(3, int(getattr(settings, "RAG_TABLE_CHUNK_SAMPLE", 6)))
        self.table_header_match_bonus = float(getattr(settings, "RAG_TABLE_HEADER_MATCH_BONUS", 0.12))
        self.table_specific_miss_penalty = float(getattr(settings, "RAG_TABLE_SPECIFIC_MISS_PENALTY", 0.25))
        self.table_specific_min_length = max(
            2,
            int(getattr(settings, "RAG_TABLE_SPECIFIC_MIN_LENGTH", 4)),
        )
        self.table_specific_min_match_count = max(
            1,
            int(getattr(settings, "RAG_TABLE_SPECIFIC_MIN_MATCH_COUNT", 2)),
        )
        self.table_specific_min_match_ratio = float(
            getattr(settings, "RAG_TABLE_SPECIFIC_MIN_MATCH_RATIO", 0.34)
        )
        if not (0.0 <= self.table_specific_min_match_ratio <= 1.0):
            self.table_specific_min_match_ratio = 0.34
        self.table_generic_df_threshold = float(getattr(settings, "RAG_TABLE_GENERIC_TOKEN_DF", 0.35))
        self.table_generic_topk = max(0, int(getattr(settings, "RAG_TABLE_GENERIC_TOKEN_TOPK", 40)))
        self.table_generic_min_tables = max(1, int(getattr(settings, "RAG_TABLE_GENERIC_MIN_TABLES", 2)))
        self.table_dominant_min_tables = max(1, int(getattr(settings, "RAG_TABLE_DOMINANT_MIN_TABLES", 2)))
        self.table_dominant_upload_ratio = float(getattr(settings, "RAG_TABLE_DOMINANT_UPLOAD_RATIO", 0.35))
        self.table_row_label_sample_limit = max(50, int(getattr(settings, "RAG_TABLE_ROW_LABEL_SAMPLE_LIMIT", 200)))
        self.table_context_cache_limit = max(32, int(getattr(settings, "RAG_TABLE_CONTEXT_CACHE_SIZE", 128)))
        # Hierarchical table retrieval: when parent/preview chunks are found, expand to row chunks
        self.table_row_expansion_limit = max(5, int(getattr(settings, "RAG_TABLE_ROW_EXPANSION_LIMIT", 20)))
        self.table_row_expansion_max_parent_context = max(1, int(getattr(settings, "RAG_TABLE_ROW_EXPANSION_MAX_PARENT_CONTEXT", 2)))

        # Parallel table search: run table search alongside vector search (not as fallback)
        # This fixes semantic collisions where vector search confidently returns wrong results
        # (e.g., "Withdraw Bills for Collection" matching ATM withdrawal content)
        self.parallel_table_search_enabled = str(
            getattr(settings, "RAG_PARALLEL_TABLE_SEARCH_ENABLED", "true")
        ).lower() in {"1", "true", "yes"}
        self.parallel_table_min_ratio = float(
            getattr(settings, "RAG_PARALLEL_TABLE_MIN_RATIO", 0.25)
        )  # Min table upload ratio to trigger parallel search
        self.parallel_table_rrf_k = int(
            getattr(settings, "RAG_PARALLEL_TABLE_RRF_K", 60)
        )  # RRF K parameter for rank fusion
        try:
            self.coverage_diversification_candidate_multiplier = float(
                getattr(settings, "RAG_COVERAGE_DIVERSIFICATION_CANDIDATE_MULTIPLIER", 2.0)
            )
        except (TypeError, ValueError):
            self.coverage_diversification_candidate_multiplier = 2.0
        if not (1.0 <= self.coverage_diversification_candidate_multiplier <= 4.0):
            self.coverage_diversification_candidate_multiplier = 2.0
        self.table_header_token_cache_limit = max(
            32,
            int(getattr(settings, "RAG_TABLE_HEADER_TOKEN_CACHE", 256)),
        )
        self._table_header_token_cache: OrderedDict[str, set[str]] = OrderedDict()
        self._table_generic_token_cache: OrderedDict[tuple[uuid.UUID, str], set[str]] = OrderedDict()
        self._table_context_cache: OrderedDict[tuple[uuid.UUID, str], dict[str, object]] = OrderedDict()
        self._table_row_label_cache: OrderedDict[tuple[uuid.UUID, str], set[str]] = OrderedDict()
        self.table_column_hint_base = {
            "name",
            "title",
            "plan",
            "brand",
            "company",
            "product",
            "clinic",
            "doctor",
            "provider",
            "program",
            "category",
        }
        # Keep this list structural and domain-agnostic.
        # Business/industry terms should not globally force table behavior.
        self.table_query_keywords = {
            "table",
            "tables",
            "column",
            "columns",
            "row",
            "rows",
            "sheet",
            "spreadsheet",
            "excel",
            "csv",
            "tsv",
            "tab",
            "tabular",
            "grid",
            "dataset",
            "field",
            "fields",
            "schema",
        }
        # Formats we should NOT treat as queryable tables (documents are read-only evidence, not datasets).
        # Default is ("pdf", "docx") but can be overridden via RAG_NON_QUERYABLE_TABLE_FORMATS setting.
        # Set to empty list [] to enable table-aware retrieval for all formats including PDF.
        default_non_queryable_formats: tuple[str, ...] = ("pdf", "docx")
        formats_setting = getattr(settings, "RAG_NON_QUERYABLE_TABLE_FORMATS", None)
        if isinstance(formats_setting, (list, tuple, set)):
            # Allow empty list to mean "no formats excluded" (all formats queryable)
            cleaned: list[str] = []
            for entry in formats_setting:
                token = str(entry or "").strip().lower()
                if token:
                    cleaned.append(token)
            default_non_queryable_formats = tuple(cleaned)  # Can be empty tuple
        self.non_queryable_table_formats: set[str] = set(default_non_queryable_formats)
        # Enhanced embedding provider log
        provider_name = type(self.embedding_service).__name__ if self.embedding_service else "None"
        model_name = getattr(self.embedding_service, "model", "unknown")
        logger.info("🧮 EMBEDDING PROVIDER %s | Model: %s", provider_name, model_name)
        self._page_summary_cache: OrderedDict[tuple[uuid.UUID, uuid.UUID], dict[int, Mapping[str, object]]] = OrderedDict()
        self._table_presence_cache: OrderedDict[tuple[uuid.UUID, str], bool] = OrderedDict()
        
        # Strategy router for intent-aware retrieval (Phase 3)
        self.strategy_router = StrategyRouter(self)
        self.tenant_lexicon_service = TenantLexiconService()
        self.intent_fallback_service = IntentFallbackService()
        self._tenant_lexicon_tables_ready_cache: bool | None = None
        self.intent_llm_fallback_threshold = float(
            getattr(settings, "RAG_INTENT_LLM_FALLBACK_THRESHOLD", 0.62)
        )
        if not (0.0 <= self.intent_llm_fallback_threshold <= 1.0):
            self.intent_llm_fallback_threshold = 0.62
        self.intent_clarification_threshold = float(
            getattr(settings, "RAG_INTENT_CLARIFICATION_THRESHOLD", 0.45)
        )
        if not (0.0 <= self.intent_clarification_threshold <= 1.0):
            self.intent_clarification_threshold = 0.45
        if self.intent_clarification_threshold > self.intent_llm_fallback_threshold:
            self.intent_clarification_threshold = self.intent_llm_fallback_threshold
        self.auto_mode_margin_threshold = float(
            getattr(settings, "RAG_AUTO_MODE_MARGIN_THRESHOLD", 0.12)
        )
        if not (0.0 <= self.auto_mode_margin_threshold <= 1.0):
            self.auto_mode_margin_threshold = 0.12
        self.auto_mode_min_score = float(
            getattr(settings, "RAG_AUTO_MODE_MIN_SCORE", 0.35)
        )
        if not (0.0 <= self.auto_mode_min_score <= 1.0):
            self.auto_mode_min_score = 0.35
        self.scope_category_max = max(
            SCOPE_TOP_CATEGORY_MAX_DEFAULT,
            int(getattr(settings, "RAG_SCOPE_CATEGORY_MAX", SCOPE_CATEGORY_MAX_DEFAULT) or SCOPE_CATEGORY_MAX_DEFAULT),
        )
        self.scope_top_category_max = max(
            1,
            int(getattr(settings, "RAG_SCOPE_TOP_CATEGORY_MAX", SCOPE_TOP_CATEGORY_MAX_DEFAULT) or SCOPE_TOP_CATEGORY_MAX_DEFAULT),
        )
        if self.scope_top_category_max > self.scope_category_max:
            self.scope_top_category_max = self.scope_category_max
        self.scope_category_ref_max = max(
            1,
            min(
                8,
                int(getattr(settings, "RAG_SCOPE_CATEGORY_REF_MAX", 4) or 4),
            ),
        )
        logger.info("🎯 Strategy router initialized with intent-aware retrieval strategies")

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

    @staticmethod
    def _normalized_scope_values(value: object) -> set[str]:
        if isinstance(value, Mapping):
            iterable = tuple(value.keys())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            iterable = value
        else:
            iterable = (value,)
        normalized: set[str] = set()
        for raw in iterable:
            cleaned = KnowledgeSearchService._normalize_topic_value(str(raw or ""))
            if cleaned:
                normalized.add(cleaned)
        return normalized

    def _scope_metadata_keysets_for_business(self, business_profile) -> dict[str, set[str]]:
        empty = {
            "scope_generic_tokens": set(),
            "scope_column_keys": set(),
            "known_segment_keys": set(),
            "preferred_value_keys": set(),
        }
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return empty

        raw_version = cache.get(f"table_profile:ver:{business_id}")
        try:
            version = max(0, int(raw_version or 0))
        except (TypeError, ValueError):
            version = 0
        cache_key = f"rag_scope_keys:{business_id}:v{version}"
        cached = cache.get(cache_key)
        if isinstance(cached, Mapping):
            return {
                "scope_generic_tokens": {
                    str(token).strip().lower()
                    for token in (cached.get("scope_generic_tokens") or ())
                    if str(token).strip()
                },
                "scope_column_keys": {
                    str(token).strip().lower()
                    for token in (cached.get("scope_column_keys") or ())
                    if str(token).strip()
                },
                "known_segment_keys": {
                    str(token).strip().lower()
                    for token in (cached.get("known_segment_keys") or ())
                    if str(token).strip()
                },
                "preferred_value_keys": {
                    str(token).strip().lower()
                    for token in (cached.get("preferred_value_keys") or ())
                    if str(token).strip()
                },
            }

        row_scope_labels: set[str] = set()
        rows = KnowledgeUploadTableRow.objects.filter(
            table__upload__business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL).order_by("-updated_at").values_list(
            "metadata",
            flat=True,
        )[: self.table_row_label_sample_limit]
        for metadata in rows:
            if not isinstance(metadata, Mapping):
                continue
            for key in (
                "scope_dimension_columns",
                "inferred_scope_columns",
                "table_row_scope_dimension_columns",
                "table_row_inferred_scope_columns",
            ):
                row_scope_labels.update(self._normalized_scope_values(metadata.get(key)))

        column_labels = {
            self._normalize_topic_value(column)
            for column in self._table_columns_for_business(business_profile)
            if self._normalize_topic_value(column)
        }
        column_rows = KnowledgeTableColumn.objects.filter(
            business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL).order_by("-updated_at").values_list(
            "column_name",
            "column_normalized",
        )[: self.table_column_sample_limit]
        for column_name, column_normalized in column_rows:
            for raw in (column_normalized, column_name):
                normalized = self._normalize_topic_value(raw)
                if normalized:
                    column_labels.add(normalized)

        scope_column_keys = set(row_scope_labels)
        known_segment_keys = {
            self._normalize_segment_key(label)
            for label in row_scope_labels
            if self._normalize_segment_key(label)
        }

        preferred_value_keys = {
            label for label in column_labels if label and label not in scope_column_keys
        }
        if len(preferred_value_keys) > 64:
            preferred_value_keys = set(sorted(preferred_value_keys)[:64])

        phrase_pool = set(column_labels) | set(scope_column_keys)
        token_counts: Counter[str] = Counter()
        for phrase in phrase_pool:
            tokens = {
                token
                for token in QueryNormalizer._TOKEN_SPLIT.split(phrase)
                if token and len(token) > 1 and not token.isdigit()
            }
            for token in tokens:
                token_counts[token] += 1

        min_frequency = (
            max(2, int(round(len(phrase_pool) * self.table_generic_df_threshold)))
            if phrase_pool
            else 2
        )
        generic_tokens = {token for token, count in token_counts.items() if count >= min_frequency}
        for label in scope_column_keys:
            for token in QueryNormalizer._TOKEN_SPLIT.split(label):
                cleaned = token.strip().lower()
                if cleaned and len(cleaned) > 1 and not cleaned.isdigit():
                    generic_tokens.add(cleaned)

        payload = {
            "scope_generic_tokens": sorted(generic_tokens),
            "scope_column_keys": sorted(scope_column_keys),
            "known_segment_keys": sorted(known_segment_keys),
            "preferred_value_keys": sorted(preferred_value_keys),
        }
        cache.set(cache_key, payload, timeout=900)
        return {
            "scope_generic_tokens": set(payload["scope_generic_tokens"]),
            "scope_column_keys": set(payload["scope_column_keys"]),
            "known_segment_keys": set(payload["known_segment_keys"]),
            "preferred_value_keys": set(payload["preferred_value_keys"]),
        }

    def _scope_generic_tokens_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("scope_generic_tokens") or set())

    def _scope_column_keys_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("scope_column_keys") or set())

    def _known_segment_keys_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("known_segment_keys") or set())

    def _preferred_value_keys_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("preferred_value_keys") or set())



    def analyze_query(self, query: str, business_profile=None) -> QueryTraits:
        filler_tokens = self._filler_tokens_for_business(business_profile) if business_profile else None
        return QueryNormalizer.normalize(query, filler_tokens=filler_tokens)

    def search(
        self,
        *,
        business_profile,
        query: str,
        limit: int | None = None,
        traits: QueryTraits | None = None,
        alias_result: AliasSearchResult | None = None,
        session_cache: MutableMapping[str, object] | None = None,
        identifier_filter: Mapping[str, str] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> KnowledgeSearchResult:
        """
        Entry point for retrieval planning and execution.

        Architectural ownership:
        - Query analysis / rewriting signals live here.
        - Retrieval strategy selection lives here.
        - Hybrid candidate generation lives here.
        - Table routing / blending / fallback lives here.
        - Fusion and reranking live here.

        MCP should treat this method as the retrieval source of truth and remain
        a thin tool-contract layer around it. If we ever need explicit batched
        query search, add that entry point here rather than rebuilding retrieval
        planning in `apps.mcp.tools`.
        """

        traits = traits or self.analyze_query(query, business_profile=business_profile)
        business_id = getattr(business_profile, "id", None) if business_profile else None
        with tenant_context(business_id):
            with TRACER.start_as_current_span("knowledge.search") as span:
                if span.is_recording():
                    span.set_attribute("knowledge.query", traits.original or query)
                    span.set_attribute("knowledge.query_tokens", traits.token_count)
                    if business_profile and getattr(business_profile, "id", None):
                        span.set_attribute("knowledge.business_id", str(business_profile.id))
                start = time.perf_counter()
                statement_timeout_ms = int(getattr(settings, "RAG_DB_STATEMENT_TIMEOUT_MS", 0) or 0)
                lock_timeout_ms = int(getattr(settings, "RAG_DB_LOCK_TIMEOUT_MS", 0) or 0)

                def _run_search() -> KnowledgeSearchResult:
                    return self._search_inner(
                        business_profile=business_profile,
                        query=query,
                        limit=limit,
                        traits=traits,
                        alias_result=alias_result,
                        session_cache=session_cache,
                        identifier_filter=identifier_filter,
                        allowed_upload_ids=allowed_upload_ids,
                        allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                        session_context=session_context,
                    )

                def _apply_db_timeouts() -> None:
                    if statement_timeout_ms <= 0 and lock_timeout_ms <= 0:
                        return
                    with connection.cursor() as cursor:
                        if lock_timeout_ms > 0:
                            cursor.execute("SET LOCAL lock_timeout = %s", [lock_timeout_ms])
                        if statement_timeout_ms > 0:
                            cursor.execute("SET LOCAL statement_timeout = %s", [statement_timeout_ms])

                try:
                    if statement_timeout_ms > 0 or lock_timeout_ms > 0:
                        # Use SET LOCAL (transaction-scoped) so we don't leak timeouts across pooled connections.
                        with transaction.atomic():
                            _apply_db_timeouts()
                            result = _run_search()
                    else:
                        result = _run_search()
                except DatabaseError as exc:
                    duration_ms = int((time.perf_counter() - start) * 1000.0)
                    message = str(exc)
                    lowered = message.lower()
                    timeout_reason = None
                    if "statement timeout" in lowered or "canceling statement" in lowered:
                        timeout_reason = "statement_timeout"
                    elif "lock timeout" in lowered:
                        timeout_reason = "lock_timeout"

                    diagnostics = {
                        "original_query": traits.original,
                        "normalized_query": traits.normalized,
                        "token_count": traits.token_count,
                        "total_duration_ms": duration_ms,
                        "error_code": "db_timeout" if timeout_reason else "db_error",
                        "db_timeout_reason": timeout_reason,
                        "db_statement_timeout_ms": statement_timeout_ms if statement_timeout_ms > 0 else None,
                        "db_lock_timeout_ms": lock_timeout_ms if lock_timeout_ms > 0 else None,
                        "error": message[:500],
                    }
                    result = KnowledgeSearchResult(snippets=tuple(), status="not_found", diagnostics=diagnostics)
                if span.is_recording():
                    span.set_attribute("knowledge.status", result.status)
                    span.set_attribute("knowledge.snippet_count", len(result.snippets))
                    diagnostics = result.diagnostics or {}
                    snippet_limit = diagnostics.get("snippet_limit")
                    if isinstance(snippet_limit, int):
                        span.set_attribute("knowledge.limit", snippet_limit)
                    duration_ms = diagnostics.get("total_duration_ms")
                    if isinstance(duration_ms, (int, float)):
                        span.set_attribute("knowledge.duration_ms", duration_ms)
                return result

    def _search_inner(
        self,
        *,
        business_profile,
        query: str,
        limit: int | None = None,
        traits: QueryTraits | None = None,
        alias_result: AliasSearchResult | None = None,
        session_cache: MutableMapping[str, object] | None = None,
        identifier_filter: Mapping[str, str] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> KnowledgeSearchResult:
        """
        Core retrieval pipeline.

        This method intentionally owns the "search brain" for knowledge lookup:
        classification, strategy routing, candidate generation, table-aware
        branching, fusion, reranking, and fallback arbitration all happen here.

        Keep MCP out of these decisions. MCP should pass queries in, then shape
        the returned evidence into refs and read contracts without re-ranking or
        re-planning the retrieval result set.
        """
        traits = traits or self.analyze_query(query, business_profile=business_profile)
        overall_start = time.perf_counter()
        feature_state = FeatureFlagService.snapshot(business_profile)
        request_id = uuid.uuid4()
        limit = self._snippet_limit_for_business(business_profile, limit)
        alias_chunk_cap = self._effective_chunk_cap(business_profile, "alias")
        ann_chunk_cap = self._effective_chunk_cap(business_profile, "ann")
        vector_ceiling = self._vector_ceiling_for_business(business_profile)
        table_context_start = time.perf_counter()
        table_context = self._table_query_context(
            business_profile,
            traits,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        table_context_ms = int((time.perf_counter() - table_context_start) * 1000.0)
        
        # Retrieval strategy selection belongs in RAG, not in MCP. MCP may pass
        # one or more query strings, but the retrieval policy for a given query
        # must stay centralized in this layer.
        # Execute retrieval strategy based on classified intent (Phase 3)
        classification = table_context.get("query_classification")
        strategy_result = None
        if classification and not classification.requires_clarification:
            retrieval_context = RetrievalContext(
                business_profile=business_profile,
                query=query,
                traits=traits,
                classification=classification,
                table_context=table_context,
                limit=limit,
                alias_result=alias_result,
                session_cache=session_cache,
                identifier_filter=identifier_filter,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                feature_state=feature_state,
                request_id=uuid.uuid4(),
            )
            strategy_result = self.strategy_router.execute(retrieval_context)
            _rag_log(
                "strategy.executed",
                {
                    "strategy": strategy_result.diagnostics.get("strategy"),
                    "intent": classification.intent.value,
                    "confidence": round(classification.confidence, 2),
                    "effective_limit": strategy_result.effective_limit,
                    "hints": strategy_result.hints.to_dict(),
                },
                indent=1,
                context={"business": business_profile.id if business_profile else None},
            )
        elif classification and classification.requires_clarification:
            _rag_log(
                "strategy.skipped_clarification",
                {
                    "intent": classification.intent.value,
                    "confidence": round(classification.confidence, 2),
                    "question": classification.clarification_question,
                },
                indent=1,
                context={"business": business_profile.id if business_profile else None},
            )

        # Apply strategy effective_limit when available (safety cap: never exceed 4x base)
        if strategy_result and strategy_result.effective_limit:
            limit = min(strategy_result.effective_limit, limit * 4)
        section_focus_terms: tuple[str, ...] = tuple()
        if classification and isinstance(classification.retrieval_hints, Mapping):
            raw_terms = classification.retrieval_hints.get("section_focus_terms") or ()
            if isinstance(raw_terms, (list, tuple, set)):
                section_focus_terms = tuple(
                    str(item).strip().lower()
                    for item in raw_terms
                    if str(item).strip()
                )[:8]
        prefer_section_context = bool(
            classification.prefers_section_context() if classification else False
        )
        modality_bias = str(
            classification.modality_bias() if classification else "mixed"
        ).strip().lower()
        if strategy_result and strategy_result.hints.prefer_section_context:
            prefer_section_context = True
        if strategy_result:
            strategy_modality_bias = str(strategy_result.hints.modality_bias or "").strip().lower()
            if strategy_modality_bias in {"table", "text", "mixed"}:
                modality_bias = strategy_modality_bias
        if prefer_section_context or section_focus_terms:
            table_context = dict(table_context)
            table_context["prefer_section_context"] = prefer_section_context
            table_context["section_focus_terms"] = list(section_focus_terms)
        if modality_bias in {"table", "text", "mixed"}:
            table_context = dict(table_context)
            table_context["modality_bias"] = modality_bias

        table_presence_start = time.perf_counter()
        tables_available = self._business_has_tables(
            business_profile,
            cached_columns=table_context.get("available_columns"),
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        table_presence_ms = int((time.perf_counter() - table_presence_start) * 1000.0)
        alias_blocked = False
        if alias_result is None:
            with TRACER.start_as_current_span("knowledge.alias_lookup") as alias_span:
                alias_result = self.search_by_alias(
                    business_profile=business_profile,
                    traits=traits,
                    limit=self.alias_result_cap,
                    feature_state=feature_state,
                    allowed_upload_ids=allowed_upload_ids,
                    allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                )
                if alias_span.is_recording():
                    alias_span.set_attribute("knowledge.alias_candidates", len(traits.alias_candidates))
                    alias_span.set_attribute("knowledge.alias_short_circuit", bool(alias_result.short_circuit))
        elif alias_result.short_circuit and not traits.is_identifier_like:
            alias_blocked = True
            alias_result = AliasSearchResult(
                hits=alias_result.hits,
                diagnostics=dict(alias_result.diagnostics or {}),
                short_circuit=False,
            )
        # Agentic RAG: do not surface "needs clarification" contracts. Always proceed best-effort.
        auto_decision_contract = self._derive_auto_decision_contract(requires_clarification=False)
        diagnostics: dict[str, object] = {
            "original_query": traits.original,
            "normalized_query": traits.normalized,
            "token_count": traits.token_count,
            "identifier_like": traits.is_identifier_like,
            "has_digits": traits.has_digits,
            "has_dashes": traits.has_dashes,
            "has_underscores": traits.has_underscores,
            "alias_candidate_count": len(traits.alias_candidates),
            "feature_flags": feature_state.as_dict(),
            "request_id": str(request_id),
            "tabular_intent": table_context["has_intent"],
            "tabular_columns_matched": sorted(table_context["matched_columns"])[:5],
            "tabular_columns_token_match": sorted(table_context.get("matched_columns_tokens") or ())[:5],
            "tabular_columns_specific": sorted(table_context.get("matched_columns_specific") or ())[:5],
            "tabular_row_label_matches": sorted(table_context.get("matched_row_labels") or ())[:5],
            "tabular_specific_tokens": sorted(table_context.get("specific_tokens") or ())[:5],
            "snippet_limit": limit,
            "alias_chunks_per_upload": alias_chunk_cap,
            "ann_chunks_per_upload": ann_chunk_cap,
            "vector_distance_ceiling": vector_ceiling,
            "tables_available": tables_available,
            "table_context_ms": table_context_ms,
            "table_presence_ms": table_presence_ms,
            "tabular_columns_hint": sorted(table_context.get("semantic_columns") or ())[:5],
            "tabular_table_dominant": bool(table_context.get("table_dominant")),
            "tabular_table_upload_ratio": table_context.get("table_upload_ratio"),
            "tabular_table_count": table_context.get("table_count"),
            "tabular_table_uploads": table_context.get("table_uploads"),
            "tabular_allow_generic": bool(table_context.get("allow_generic")),
            "tabular_comprehensive_intent": bool(table_context.get("comprehensive_intent")),
            "tabular_prefer_section_context": bool(table_context.get("prefer_section_context")),
            "tabular_section_focus_terms": list(table_context.get("section_focus_terms") or ())[:6],
            "document_continuity_allowed": bool(
                session_context.get("document_continuity_allowed") if session_context else False
            ),
            "document_continuity_reason": (
                session_context.get("document_continuity_reason") if session_context else None
            ),
            "query_rewrite_strategy": (
                session_context.get("query_rewrite_strategy") if session_context else None
            ),
            "query_rewrite_confidence": (
                session_context.get("query_rewrite_confidence") if session_context else None
            ),
            "intent_name": classification.intent.value if classification else None,
            "intent_confidence": round(classification.confidence, 3) if classification else None,
            "intent_source": classification.source if classification else None,
            "intent_fallback_used": bool(classification.fallback_used) if classification else False,
            "intent_fallback_attempted": bool(table_context.get("intent_fallback_attempted")),
            "intent_fallback_applied": bool(table_context.get("intent_fallback_applied")),
            # Keep the classifier signal for debugging, but do not turn it into a blocking clarification.
            "intent_requires_clarification": False,
            "intent_clarification_question": "",
            "intent_classifier_requires_clarification": bool(classification.requires_clarification) if classification else False,
            "intent_classifier_clarification_question": (
                classification.clarification_question if classification else ""
            ),
            "tenant_lexicon_entity_terms_count": int(table_context.get("tenant_lexicon_entity_terms_count") or 0),
            "tenant_lexicon_attribute_terms_count": int(table_context.get("tenant_lexicon_attribute_terms_count") or 0),
            "alias_short_circuit_blocked": alias_blocked,
            "table_reason": None,
            "chunk_candidate_count": 0,
            "auto_decision_contract": auto_decision_contract,
        }
        if alias_result.diagnostics:
            diagnostics.update(dict(alias_result.diagnostics))
        diagnostics["alias_stage"] = diagnostics.get("stage")
        diagnostics["alias_hits"] = len(alias_result.hits)
        alias_duration_ms = None
        if alias_result and alias_result.diagnostics:
            try:
                alias_duration_ms = int(alias_result.diagnostics.get("duration_ms"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                alias_duration_ms = None
        if alias_duration_ms is not None:
            diagnostics["alias_duration_ms"] = alias_duration_ms
        
        # Add strategy pattern diagnostics (Phase 3)
        if strategy_result:
            diagnostics["strategy_name"] = strategy_result.diagnostics.get("strategy")
            diagnostics["strategy_intent"] = strategy_result.diagnostics.get("intent")
            diagnostics["strategy_confidence"] = strategy_result.diagnostics.get("confidence")
            diagnostics["strategy_effective_limit"] = strategy_result.effective_limit
            diagnostics["strategy_multiplier"] = strategy_result.hints.snippet_limit_multiplier
            diagnostics["strategy_diversify_tables"] = strategy_result.hints.diversify_tables
            diagnostics["strategy_comprehensive"] = strategy_result.hints.comprehensive_intent
            diagnostics["strategy_prefer_section_context"] = strategy_result.hints.prefer_section_context
            diagnostics["strategy_modality_bias"] = strategy_result.hints.modality_bias
            diagnostics["strategy_applied_limit"] = limit

        # Agentic RAG should not block on "clarification". Keep the signal for diagnostics,
        # but continue retrieval and answer best-effort with available evidence.
        if classification and classification.requires_clarification and not traits.is_identifier_like:
            diagnostics.setdefault("clarification_suggested", True)
            diagnostics.setdefault("clarification_reason", "low_intent_confidence")
            if classification.clarification_question:
                diagnostics.setdefault(
                    "clarification_question",
                    classification.clarification_question,
                )
        
        cache_key = self._result_cache_key(
            business_profile=business_profile,
            traits=traits,
            limit=limit,
            alias_result=alias_result,
            table_context=table_context,
            feature_state=feature_state,
            identifier_filter=identifier_filter,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        cached_result = None
        if session_cache is not None:
            cached_result = self._session_cache_get(session_cache, cache_key)
            if cached_result:
                cached_status = str(cached_result.status or "").strip().lower() or "not_found"
                if cached_status == "needs_clarification":
                    # Ignore stale clarification cache entries; agentic mode should proceed best-effort.
                    cached_result = None
                else:
                    cached_diag = dict(cached_result.diagnostics or {})
                    cached_diag["cache_hit"] = True
                    cached_diag["cache_scope"] = "session"
                    cached_diag["request_id"] = str(request_id)
                    cached_diag["total_duration_ms"] = self._duration_ms(overall_start)
                    snippets = cached_result.snippets[:limit]
                    snippets, collapse_diag = self._collapse_snippets_by_evidence_group(
                        snippets,
                        query_text=traits.normalized or traits.original or query,
                        tokens=traits.tokens,
                        limit=limit,
                    )
                    cached_diag.update(collapse_diag)
                    cached_diag["snippet_count"] = len(snippets)
                    result_obj = KnowledgeSearchResult(
                        snippets=snippets,
                        status=cached_status,
                        diagnostics=cached_diag,
                    )
                    self._record_retrieval_event(
                        business_profile=business_profile,
                        traits=traits,
                        alias_result=alias_result,
                        result=result_obj,
                        feature_state=feature_state,
                    )
                    self._log_search_summary(
                        business_profile=business_profile,
                        request_id=request_id,
                        result=result_obj,
                    )
                    return result_obj
        cached_result = self._result_cache_get(cache_key)
        if cached_result:
            cached_status = str(cached_result.status or "").strip().lower() or "not_found"
            if cached_status != "needs_clarification":
                cached_diag = dict(cached_result.diagnostics or {})
                cached_diag["cache_hit"] = True
                cached_diag["cache_scope"] = "business"
                cached_diag["request_id"] = str(request_id)
                cached_diag["total_duration_ms"] = self._duration_ms(overall_start)
                snippets = cached_result.snippets[:limit]
                snippets, collapse_diag = self._collapse_snippets_by_evidence_group(
                    snippets,
                    query_text=traits.normalized or traits.original or query,
                    tokens=traits.tokens,
                    limit=limit,
                )
                cached_diag.update(collapse_diag)
                cached_diag["snippet_count"] = len(snippets)
                result_obj = KnowledgeSearchResult(
                    snippets=snippets,
                    status=cached_status,
                    diagnostics=cached_diag,
                )
                self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
                self._record_retrieval_event(
                    business_profile=business_profile,
                    traits=traits,
                    alias_result=alias_result,
                    result=result_obj,
                    feature_state=feature_state,
                )
                self._log_search_summary(
                    business_profile=business_profile,
                    request_id=request_id,
                    result=result_obj,
                )
                return result_obj
        if alias_result.short_circuit and alias_result.hits:
            chunk_ids = [hit.chunk_id for hit in alias_result.hits[: max(limit, self.alias_result_cap)]]
            neighbor = max(1, self.alias_neighbor_window)
            snippets = tuple(
                self.load_chunk_contents(
                    business_profile=business_profile,
                    chunk_ids=chunk_ids,
                    neighbor=neighbor,
            )
            )[:limit]
            snippets, collapse_diag = self._collapse_snippets_by_evidence_group(
                snippets,
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
                limit=limit,
            )
            diagnostics["path"] = "alias_exact"
            diagnostics["alias_stage"] = diagnostics.get("alias_stage") or alias_result.diagnostics.get("stage")
            diagnostics.update(collapse_diag)
            _rag_log(
                "alias.short_circuit",
                {
                    "query": traits.normalized,
                    "hits": len(snippets),
                    "neighbor": neighbor,
                },
                indent=1,
                context={
                    "business": business_profile.id,
                    "request": diagnostics.get("request_id"),
                },
            )
            status = "ok" if snippets else "not_found"
            status, snippets, diagnostics = self._apply_phase6_semantics(
                status=status,
                snippets=snippets,
                diagnostics=diagnostics,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
                table_blocked=False,
            )
            diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
            diagnostics["snippet_count"] = len(snippets)
            result_obj = KnowledgeSearchResult(snippets=snippets, status=status, diagnostics=diagnostics)
            self._result_cache_set(cache_key, result_obj, limit=limit)
            self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
            self._record_retrieval_event(
                business_profile=business_profile,
                traits=traits,
                alias_result=alias_result,
                result=result_obj,
                feature_state=feature_state,
            )
            self._log_search_summary(
                business_profile=business_profile,
                request_id=request_id,
                result=result_obj,
            )
            return result_obj

        chunk_hits = self._chunk_hits(
            business_profile,
            traits=traits,
            limit=max(limit * 3, self.max_chunks_per_upload * limit),
            alias_result=alias_result,
            feature_state=feature_state,
            diagnostics=diagnostics,
            vector_ceiling=vector_ceiling,
            table_context=table_context,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            session_context=session_context,
        )
        _rag_log(
            "table.search_decision",
            {
                "query": traits.normalized,
                "tables_available": tables_available,
                "has_intent": table_context["has_intent"],
                "chunk_hits": len(chunk_hits),
            },
            indent=1,
            context={
                "business": business_profile.id,
                "request": diagnostics.get("request_id"),
            },
        )
        diagnostics["chunk_candidate_count_raw"] = len(chunk_hits)
        table_intent = bool(table_context.get("has_intent"))

        # Hierarchical table retrieval: expand parent/preview chunks to row chunks for table-intent queries.
        # Row chunks contain actual answer data (e.g., "EGP 500") while parent chunks often have OCR noise.
        # This enables: table discovery → row expansion → answer from rows (parents for context only).
        # EXCEPTION: For comprehensive queries ("list all cards", "every product"), keep preview chunks
        # as they contain the full table structure needed for enumeration/comparison answers.
        comprehensive_intent = bool(table_context.get("comprehensive_intent"))
        table_signal_header = bool(
            table_context.get("matched_columns_query")
            or table_context.get("matched_columns_tokens")
        )
        table_signal_specific = bool(
            table_context.get("matched_columns_specific")
            or table_context.get("matched_row_labels")
        )
        table_signal_from_hits = any(
            bool((hit.diagnostics or {}).get("specific_match_strong"))
            or bool((hit.diagnostics or {}).get("specific_match"))
            or bool((hit.diagnostics or {}).get("header_match"))
            for hit in chunk_hits[: self.table_chunk_sample_limit]
        )
        table_signal_direct_stage = any(
            str(getattr(hit, "source_stage", "") or "").strip().lower() in {"table_direct", "table_blended"}
            for hit in chunk_hits[: self.table_chunk_sample_limit]
        )
        table_row_expansion_relevant = bool(
            table_signal_specific
            or table_signal_header
            or table_signal_from_hits
            or table_signal_direct_stage
        )
        if table_intent and not comprehensive_intent and chunk_hits and table_row_expansion_relevant:
            expanded_rows = self._expand_table_rows(
                business_profile,
                chunk_hits,
                max_rows_per_table=self.table_row_expansion_limit,
                query_tokens=traits.tokens,
            )
            if expanded_rows:
                chunk_hits, merge_diagnostics = self._merge_expanded_table_hits(
                    chunk_hits,
                    expanded_rows,
                    query_tokens=traits.tokens,
                )
                diagnostics["table_row_expansion"] = merge_diagnostics.get("expanded_rows", 0)
                diagnostics["table_row_expansion_relevant"] = merge_diagnostics.get("relevant_rows", 0)
                diagnostics["table_row_expansion_supplemental"] = merge_diagnostics.get("supplemental_rows", 0)
                diagnostics["table_parent_suppressed"] = merge_diagnostics.get("parent_chunks_suppressed", 0)
                diagnostics["table_parent_limited"] = merge_diagnostics.get("parent_chunks_limited", 0)
                _rag_log(
                    "table.row_expansion",
                    {
                        "expanded_rows": merge_diagnostics.get("expanded_rows", 0),
                        "relevant_rows": merge_diagnostics.get("relevant_rows", 0),
                        "supplemental_rows": merge_diagnostics.get("supplemental_rows", 0),
                        "parent_chunks_suppressed": merge_diagnostics.get("parent_chunks_suppressed", 0),
                        "parent_chunks_limited": merge_diagnostics.get("parent_chunks_limited", 0),
                        "total_after": len(chunk_hits),
                    },
                    indent=1,
                    context={
                        "business": business_profile.id,
                        "request": diagnostics.get("request_id"),
                    },
                )
        elif table_intent and not comprehensive_intent and chunk_hits:
            diagnostics["table_row_expansion_skipped"] = "query_signal_not_specific"

        # Always suppress legacy PDF "json_entity" chunks (historically created from per-row table entities).
        # These chunks tend to be low-context ("Table_1: ...") and can dominate retrieval even for normal Q&A.
        if chunk_hits:
            before = len(chunk_hits)
            chunk_hits = tuple(hit for hit in chunk_hits if not self._is_legacy_pdf_table_entity_chunk(hit.chunk))
            removed = before - len(chunk_hits)
            if removed:
                diagnostics["pdf_table_entity_chunks_filtered"] = removed
                _rag_log(
                    "pdf.table_entities.filtered",
                    {"removed": removed, "remaining": len(chunk_hits)},
                    indent=1,
                    context={
                        "business": business_profile.id,
                        "request": diagnostics.get("request_id"),
                    },
                )

        # For non-table queries, prefer page/text chunks and suppress extracted doc-table preview chunks.
        # This prevents PDFs-with-tables from dominating retrieval unless the query is actually table-critical.
        if chunk_hits and not table_intent:
            before = len(chunk_hits)
            chunk_hits = tuple(hit for hit in chunk_hits if not self._is_doc_table_preview_chunk(hit.chunk))
            removed = before - len(chunk_hits)
            if removed:
                diagnostics["doc_table_chunks_filtered"] = removed
                _rag_log(
                    "table.preview.filtered",
                    {"removed": removed, "remaining": len(chunk_hits)},
                    indent=1,
                    context={
                        "business": business_profile.id,
                        "request": diagnostics.get("request_id"),
                    },
                )
        filler_tokens = self._filler_tokens_for_business(business_profile)
        postclip_scope_summary = self._build_scope_summary_from_candidates(
            chunk_hits,
            business_profile=business_profile,
            query_tokens=traits.tokens,
            filler_tokens=filler_tokens,
        )
        preclip_scope_summary = self._normalize_scope_summary(
            diagnostics.get("scope_summary_preclip"),
        )
        scope_summary = preclip_scope_summary or postclip_scope_summary
        diagnostics["scope_summary"] = scope_summary
        diagnostics["scope_summary_source"] = "preclip_fused" if preclip_scope_summary else "postclip_hits"
        if preclip_scope_summary:
            diagnostics["scope_summary_postclip"] = postclip_scope_summary
        _rag_log(
            "scope.summary",
            {
                "total_matches": scope_summary.get("total_matches"),
                "distinct_docs": scope_summary.get("distinct_docs"),
                "categories": len(scope_summary.get("category_counts") or {}),
                "is_broad_scope": scope_summary.get("is_broad_scope"),
                "source": diagnostics.get("scope_summary_source"),
            },
            indent=1,
            context={
                "business": business_profile.id,
                "request": diagnostics.get("request_id"),
            },
        )
        auto_score_diag = self._score_auto_mode_candidates(
            chunk_hits,
            query_tokens=traits.tokens,
            specific_tokens=tuple(table_context.get("specific_tokens") or ()),
        )
        diagnostics.update(auto_score_diag)
        auto_arbitration_diag = self._arbitrate_auto_mode(
            scoring_diagnostics=diagnostics,
            table_intent_hint=table_intent,
        )
        diagnostics.update(auto_arbitration_diag)
        table_intent = bool(auto_arbitration_diag.get("auto_arbitration_table_intent", table_intent))

        # NOTE: `auto_arbitration_needs_clarification` is intentionally ignored in agentic mode.
        # We proceed with the best-effort route and allow later fusion (e.g. parallel table search)
        # to reconcile table/text evidence without pausing the conversation.

        chunk_hits, _context_hits, route_diag = self._route_chunk_hits(
            chunk_hits,
            table_intent=table_intent,
            table_context=table_context,
        )
        diagnostics.update(route_diag)
        diagnostics["auto_decision_contract"] = self._derive_auto_decision_contract(
            route_diagnostics=route_diag,
            scoring_diagnostics=diagnostics,
            requires_clarification=False,
            scope_summary=scope_summary,
        )
        diagnostics["chunk_candidate_count"] = len(chunk_hits)
        table_snippets: tuple[KnowledgeSnippet, ...] = tuple()
        table_reason: str | None = None
        should_run_table = False
        is_parallel_table_search = False  # NEW: Track if this is parallel (not fallback) search
        table_duration_ms: int | None = None
        matched_columns_query = table_context.get("matched_columns_query")
        matched_columns_tokens = table_context.get("matched_columns_tokens")
        has_header_match = bool(matched_columns_query or matched_columns_tokens) if isinstance(matched_columns_query, set) else False
        specific_tokens = set(table_context.get("specific_tokens") or ())
        matched_columns_specific = table_context.get("matched_columns_specific")
        matched_row_labels = set(table_context.get("matched_row_labels") or ())
        allow_generic = bool(table_context.get("allow_generic"))
        has_specific_match = bool(matched_columns_specific or matched_row_labels) if specific_tokens else True
        table_blocked = bool(table_intent and specific_tokens and not has_specific_match and not allow_generic)
        has_table_chunk = any(
            bool((hit.chunk.metadata or {}).get("is_table_chunk"))
            for hit in chunk_hits[: self.table_chunk_sample_limit]
        )
        has_text_chunk = any(
            not bool((hit.chunk.metadata or {}).get("is_table_chunk"))
            for hit in chunk_hits[: self.table_chunk_sample_limit]
        )

        # Parallel table search is a retrieval concern. Keep the decision and the
        # resulting fusion here so MCP does not grow a second retrieval planner.
        # NEW: Parallel table search - run table search alongside vector search, not as fallback
        # This fixes semantic collisions where vector search returns confident but wrong results
        # (e.g., "Withdraw Bills for Collection" matching ATM withdrawal content)
        table_upload_ratio = float(table_context.get("table_upload_ratio") or 0)
        should_run_parallel_table = (
            self.parallel_table_search_enabled
            and tables_available
            and table_intent
            and not table_blocked
            and chunk_hits  # We have vector results (parallel mode, not fallback)
            and table_upload_ratio >= self.parallel_table_min_ratio
            and (has_text_chunk or bool(_context_hits))
            # Keep parallel fusion for mixed candidates; table-only flows use table_direct/blended.
        )

        if should_run_parallel_table:
            should_run_table = True
            is_parallel_table_search = True
            table_reason = "parallel_multi_strategy"
            _rag_log(
                "parallel_table.triggered",
                {
                    "query": traits.normalized,
                    "table_upload_ratio": round(table_upload_ratio, 2),
                    "min_ratio": self.parallel_table_min_ratio,
                    "chunk_hits": len(chunk_hits),
                },
                indent=2,
                context={"business": business_profile.id},
            )
        elif tables_available and not table_blocked:
            # Original fallback logic (kept for cases where parallel is disabled or ratio too low)
            if not chunk_hits:
                should_run_table = True
                table_reason = "no_chunk_candidates"
            elif table_intent and self._chunk_hits_are_weak(chunk_hits, traits):
                should_run_table = True
                table_reason = "weak_chunk_candidates"
            elif table_intent and allow_generic and not has_table_chunk:
                should_run_table = True
                table_reason = "schema_context"
            elif table_intent and self._query_has_entity_tokens(business_profile, traits):
                should_run_table = True
                table_reason = "entity_query_parallel"
            elif table_intent and has_header_match:
                should_run_table = True
                table_reason = "header_match"
        elif table_blocked:
            diagnostics["table_reason"] = "specific_tokens_missing"

        if should_run_table:
            _rag_log(
                "table.search_run",
                {
                    "query": traits.normalized,
                    "reason": table_reason,
                },
                indent=2,
                context={
                    "business": business_profile.id,
                    "request": diagnostics.get("request_id"),
                },
            )
            table_start = time.perf_counter()
            comprehensive_flag = bool(table_context.get("comprehensive_intent"))
            _rag_log(
                "table.search_call",
                {
                    "query": traits.normalized or traits.original,
                    "comprehensive_intent_passed": comprehensive_flag,
                    "table_context_comprehensive": table_context.get("comprehensive_intent"),
                    "table_reason": table_reason,
                    "limit": limit,
                },
                indent=2,
                context={"business": business_profile.id},
            )
            table_snippets = self._table_search_snippets(
                business_profile=business_profile,
                query_text=traits.normalized or traits.original,
                limit=limit,
                matched_columns=table_context["matched_columns"],
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                comprehensive_intent=comprehensive_flag,
            )
            table_duration_ms = int((time.perf_counter() - table_start) * 1000)
            diagnostics["table_duration_ms"] = table_duration_ms

        if table_snippets:
            # Fusion ownership stays in RAG. MCP should not try to reconcile
            # table/vector candidates again after this point.
            # NEW: For parallel table search, use RRF fusion to merge vector + table results
            if is_parallel_table_search and chunk_hits:
                diagnostics["path"] = "parallel_rrf"
                diagnostics["reason"] = "parallel_multi_strategy"
                diagnostics["table_reason"] = table_reason

                # Convert chunk hits to snippets for RRF fusion.
                vector_snippets = list(
                    self._search_chunks(
                        chunk_hits,
                        limit=limit * 2,  # Get more candidates for fusion
                        business_profile=business_profile,
                        pathway="hybrid",
                        query=traits.normalized or traits.original or query,
                    )
                )

                # RRF fusion of table + vector results
                rrf_merged = self._rrf_fusion_snippets(
                    vector_snippets=vector_snippets,
                    table_snippets=list(table_snippets),
                    k=self.parallel_table_rrf_k,
                )

                if strategy_result and strategy_result.hints.diversify_tables and len(rrf_merged) > 1:
                    blended, coverage_diag = self._diversify_table_snippets_with_diagnostics(
                        rrf_merged,
                        limit=limit,
                    )
                    diagnostics.update(coverage_diag)
                    diagnostics["strategy_diversified"] = bool(
                        coverage_diag.get("coverage_diversification_applied")
                    )
                else:
                    blended = list(rrf_merged[:limit])

                blended, collapse_diag = self._collapse_snippets_by_evidence_group(
                    blended,
                    query_text=traits.normalized or traits.original or query,
                    tokens=traits.tokens,
                    limit=limit,
                )
                diagnostics.update(collapse_diag)

                diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
                diagnostics["snippet_count"] = len(blended)
                diagnostics["rrf_vector_count"] = len(vector_snippets)
                diagnostics["rrf_table_count"] = len(table_snippets)
                diagnostics["rrf_merged_count"] = len(rrf_merged)

                status = "ok" if blended else "not_found"
                status, blended_snippets, diagnostics = self._apply_phase6_semantics(
                    status=status,
                    snippets=tuple(blended),
                    diagnostics=diagnostics,
                    business_profile=business_profile,
                    traits=traits,
                    table_context=table_context,
                    table_blocked=table_blocked,
                )
                result_obj = KnowledgeSearchResult(
                    snippets=blended_snippets,
                    status=status,
                    diagnostics=diagnostics,
                )
                self._result_cache_set(cache_key, result_obj, limit=limit)
                self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
                self._record_retrieval_event(
                    business_profile=business_profile,
                    traits=traits,
                    alias_result=alias_result,
                    result=result_obj,
                    feature_state=feature_state,
                )
                self._log_search_summary(
                    business_profile=business_profile,
                    request_id=request_id,
                    result=result_obj,
                )
                return result_obj

            # Original blending logic (for fallback table search, not parallel)
            diagnostics["path"] = "table_direct" if not chunk_hits else "table_blended"
            diagnostics["reason"] = table_reason or diagnostics.get("reason") or "table_search"
            if table_reason:
                diagnostics["table_reason"] = table_reason
            diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
            diagnostics["snippet_count"] = len(table_snippets)
            diversify_requested = bool(strategy_result and strategy_result.hints.diversify_tables)
            candidate_limit = limit
            if diversify_requested:
                candidate_limit = max(
                    limit,
                    int(math.ceil(limit * self.coverage_diversification_candidate_multiplier)),
                )
            blended_candidates: list[KnowledgeSnippet] = list(table_snippets)
            remaining = max(0, candidate_limit - len(blended_candidates))
            if chunk_hits and remaining:
                blended_candidates.extend(
                    self._search_chunks(
                        chunk_hits,
                        limit=remaining,
                        business_profile=business_profile,
                        pathway="hybrid",
                        query=traits.normalized or traits.original or query,  # NEW: For query-aware row sampling
                    )
                )
            if diversify_requested and len(blended_candidates) > 1:
                blended, coverage_diag = self._diversify_table_snippets_with_diagnostics(
                    blended_candidates,
                    limit=limit,
                )
                diagnostics.update(coverage_diag)
                diagnostics["strategy_diversified"] = bool(
                    coverage_diag.get("coverage_diversification_applied")
                )
            else:
                blended = list(blended_candidates[:limit])

            blended, collapse_diag = self._collapse_snippets_by_evidence_group(
                blended,
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
                limit=limit,
            )
            diagnostics.update(collapse_diag)

            status = "ok" if blended else "not_found"
            status, blended_snippets, diagnostics = self._apply_phase6_semantics(
                status=status,
                snippets=tuple(blended),
                diagnostics=diagnostics,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
                table_blocked=table_blocked,
            )
            diagnostics["snippet_count"] = len(blended_snippets)
            result_obj = KnowledgeSearchResult(snippets=blended_snippets, status=status, diagnostics=diagnostics)
            self._result_cache_set(cache_key, result_obj, limit=limit)
            self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
            self._record_retrieval_event(
                business_profile=business_profile,
                traits=traits,
                alias_result=alias_result,
                result=result_obj,
                feature_state=feature_state,
            )
            self._log_search_summary(
                business_profile=business_profile,
                request_id=request_id,
                result=result_obj,
            )
            return result_obj

        chunk_snippet_limit = limit
        diversify_requested = bool(strategy_result and strategy_result.hints.diversify_tables)
        if diversify_requested:
            chunk_snippet_limit = max(
                limit,
                int(math.ceil(limit * self.coverage_diversification_candidate_multiplier)),
            )
        snippets = tuple(
            self._search_chunks(
                chunk_hits,
                limit=chunk_snippet_limit,
                business_profile=business_profile,
                pathway="hybrid",
                query=traits.normalized or traits.original or query,  # NEW: For query-aware row sampling
            )
        )
        if snippets:
            diagnostics["path"] = diagnostics.get("path") or "hybrid"
            diagnostics.setdefault("table_reason", table_reason)
            if diversify_requested and len(snippets) > 1:
                diversified_snippets, coverage_diag = self._diversify_table_snippets_with_diagnostics(
                    snippets,
                    limit=limit,
                )
                diagnostics.update(coverage_diag)
                diagnostics["strategy_diversified"] = bool(
                    coverage_diag.get("coverage_diversification_applied")
                )
                snippets = tuple(diversified_snippets)
            snippets, collapse_diag = self._collapse_snippets_by_evidence_group(
                snippets,
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
                limit=limit,
            )
            diagnostics.update(collapse_diag)
            diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
            status, snippets, diagnostics = self._apply_phase6_semantics(
                status="ok",
                snippets=snippets,
                diagnostics=diagnostics,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
                table_blocked=table_blocked,
            )
            diagnostics["snippet_count"] = len(snippets)
            result_obj = KnowledgeSearchResult(snippets=snippets, status=status, diagnostics=diagnostics)
            self._result_cache_set(cache_key, result_obj, limit=limit)
            self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
            self._record_retrieval_event(
                business_profile=business_profile,
                traits=traits,
                alias_result=alias_result,
                result=result_obj,
                feature_state=feature_state,
            )
            self._log_search_summary(
                business_profile=business_profile,
                request_id=request_id,
                result=result_obj,
            )
            return result_obj

        fallback = tuple(
            self._fallback_snippets(
                business_profile=business_profile,
                limit=limit,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
        )
        fallback, collapse_diag = self._collapse_snippets_by_evidence_group(
            fallback,
            query_text=traits.normalized or traits.original or query,
            tokens=traits.tokens,
            limit=limit,
        )
        diagnostics["path"] = "fallback"
        diagnostics["reason"] = "fallback_used"
        status = "ok" if fallback else "not_found"
        status, fallback, diagnostics = self._apply_phase6_semantics(
            status=status,
            snippets=fallback,
            diagnostics=diagnostics,
            business_profile=business_profile,
            traits=traits,
            table_context=table_context,
            table_blocked=table_blocked,
        )
        diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
        diagnostics.setdefault("table_reason", table_reason)
        diagnostics.update(collapse_diag)
        diagnostics["snippet_count"] = len(fallback)
        result_obj = KnowledgeSearchResult(snippets=fallback, status=status, diagnostics=diagnostics)
        self._result_cache_set(cache_key, result_obj, limit=limit)
        self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
        self._record_retrieval_event(
            business_profile=business_profile,
            traits=traits,
            alias_result=alias_result,
            result=result_obj,
            feature_state=feature_state,
        )
        self._log_search_summary(
            business_profile=business_profile,
            request_id=request_id,
            result=result_obj,
        )
        return result_obj
