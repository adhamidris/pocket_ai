from __future__ import annotations

from collections import OrderedDict, Counter

from django.conf import settings
from django.core.cache import cache
from django.db.models.expressions import F
from pgvector.django import CosineDistance  # if using cosine
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, TrigramSimilarity

import time
import hashlib
import dataclasses
import logging
import uuid
import math
from datetime import datetime
from types import SimpleNamespace
import re
from contextvars import ContextVar
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence
from zoneinfo import ZoneInfo

from apps.rag.text_utils import is_plural_candidate, singularize

from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.accounts.models import (
    KnowledgeStatus,
    KnowledgeVisibility,
)
from apps.knowledge.models import (
    KnowledgeAlias,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadIssue,
    KnowledgeTableColumn,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
    KnowledgeUploadShadowChunk,
)
from apps.rag.embeddings import build_embedding_service, EmbeddingProviderError
from apps.rag.content_serialization import ContentSerializationMixin
from apps.rag.evidence_grouping import EvidenceGroupingMixin
from apps.rag.metadata_helpers import KnowledgeMetadataMixin
from apps.rag.ranking_features import RankingFeatureMixin
from apps.rag.reranking import RerankingMixin
from apps.accounts.feature_flags import FeatureFlagService, FeatureState
from apps.knowledge.knowledge_access import (
    apply_customer_visible_chunks,
    apply_customer_visible_uploads,
)
from apps.rag.quality_monitor import QualityMonitor
from apps.rag.rag_logging import rag_log
from apps.rag.query_normalizer import QueryNormalizer
from apps.rag.contracts import (
    MAX_INLINE_KNOWLEDGE_CHARS,
    AliasSearchResult,
    ChunkResult,
    HybridSearchResult,
    KnowledgeSearchResult,
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
    KNOWLEDGE_READ_STATE_SUMMARY,
    QueryTraits,
)
from apps.rag.intent_fallback import IntentFallbackService
from apps.rag.tenant_lexicon import TenantLexiconService
from apps.rag.retrieval_strategies import StrategyRouter, RetrievalContext, RetrievalHints
from apps.rag.search_cache import KnowledgeSearchCacheMixin
from apps.rag.table_context import TableContextMixin
from core.metrics import latency_monitor
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


SCOPE_CATEGORY_MAX_DEFAULT = 40
SCOPE_TOP_CATEGORY_MAX_DEFAULT = 4


class KnowledgeSearchService(
    RerankingMixin,
    RankingFeatureMixin,
    EvidenceGroupingMixin,
    TableContextMixin,
    KnowledgeSearchCacheMixin,
    KnowledgeMetadataMixin,
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

    def inline_char_limit_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve the inline char budget for a business without changing legacy defaults.
        """

        base = requested if requested is not None else self.inline_char_limit_default
        override = self._business_override(business_profile, "inline_knowledge_char_limit", base)
        limit = max(200, int(override))
        return limit

    def _neighbor_window_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve the neighbor span for chunk reads so we can tune per business later.
        """

        base = requested if requested is not None else self.chunk_neighbor_window_default
        override = self._business_override(business_profile, "chunk_neighbor_window", base)
        window = max(0, int(override))
        return window

    def page_char_limit_for_business(self, business_profile, requested: int | None = None) -> int:
        base = requested if requested is not None else self.page_char_limit_default
        override = self._business_override(business_profile, "page_char_limit", base)
        return max(200, int(override))

    def _window_cache_get(
        self,
        business_id: uuid.UUID,
        upload_id: uuid.UUID,
        start: int,
        end: int,
    ) -> list[KnowledgeUploadChunk] | None:
        key = (business_id, upload_id, start, end)
        cached = self._window_cache.get(key)
        if cached is not None:
            self._window_cache.move_to_end(key)
        return cached

    def _window_cache_set(
        self,
        business_id: uuid.UUID,
        upload_id: uuid.UUID,
        start: int,
        end: int,
        chunks: list[KnowledgeUploadChunk],
    ) -> None:
        key = (business_id, upload_id, start, end)
        self._window_cache[key] = chunks
        self._window_cache.move_to_end(key)
        if len(self._window_cache) > self.window_cache_limit:
            self._window_cache.popitem(last=False)

    def _page_summary_entries(self, upload: KnowledgeUpload) -> dict[int, Mapping[str, object]]:
        cache_key = (upload.business_profile_id, upload.id)
        cached = self._page_summary_cache.get(cache_key)
        if cached is not None:
            self._page_summary_cache.move_to_end(cache_key)
            return cached
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        exports = metadata.get("structured_exports") if isinstance(metadata, dict) else None
        pages = exports.get("pages") if isinstance(exports, dict) else []
        entries: dict[int, Mapping[str, object]] = {}
        if isinstance(pages, list):
            for item in pages:
                if not isinstance(item, Mapping):
                    continue
                page_number = item.get("page_number")
                try:
                    page_index = int(page_number)
                except (TypeError, ValueError):
                    continue
                entries[page_index] = item
        self._page_summary_cache[cache_key] = entries
        self._page_summary_cache.move_to_end(cache_key)
        if len(self._page_summary_cache) > self.page_summary_cache_limit:
            self._page_summary_cache.popitem(last=False)
        return entries

    def _page_synopsis_text(self, upload: KnowledgeUpload | None, page_number: int | None, fallback: str | None = None) -> str:
        if not upload or not page_number:
            return (fallback or "").strip()
        entries = self._page_summary_entries(upload)
        entry = entries.get(page_number)
        if not entry:
            return (fallback or "").strip()
        synopsis = entry.get("synopsis")
        if isinstance(synopsis, str) and synopsis.strip():
            return synopsis.strip()
        heading = entry.get("heading") or entry.get("section_heading")
        if isinstance(heading, str) and heading.strip():
            return heading.strip()
        headings = entry.get("headings")
        if isinstance(headings, list):
            for candidate in headings:
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return (fallback or "").strip()

    def _structured_counts(self, upload: KnowledgeUpload) -> tuple[int, int]:
        cache_key = (upload.business_profile_id, upload.id)
        cached = self._structured_count_cache.get(cache_key)
        if cached:
            self._structured_count_cache.move_to_end(cache_key)
            return cached
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        exports = metadata.get("structured_exports") if isinstance(metadata, dict) else None
        tables = issues = 0
        if isinstance(exports, dict):
            tables = len(exports.get("tables") or [])
            issues = len(exports.get("issues") or [])
        else:
            try:
                tables = upload.tables.count()
            except Exception:
                tables = 0
            try:
                issues = upload.issues.count()
            except Exception:
                issues = 0
        counts = (tables, issues)
        self._structured_count_cache[cache_key] = counts
        self._structured_count_cache.move_to_end(cache_key)
        if len(self._structured_count_cache) > self.structured_count_cache_limit:
            self._structured_count_cache.popitem(last=False)
        return counts


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

    def _public_confidence_score(self, result: ChunkResult | None) -> float | None:
        """Expose one comparable public relevance signal for prompt-visible snippets.

        Public confidence should reflect retrieval relevance, not freshness or
        other helper boosts that can make scores incomparable across result
        types. Keep recency out of the public score.
        """
        if result is None:
            return None

        score_candidates: list[float] = []
        rerank_score = self._clamp_unit(self._safe_float(getattr(result, "rerank_score", None), default=0.0))
        if rerank_score > 0.0:
            score_candidates.append(rerank_score)

        lexical_score = self._clamp_unit(self._safe_float(getattr(result, "lexical_score", None), default=0.0))
        if lexical_score > 0.0:
            score_candidates.append(lexical_score)

        alias_score = self._clamp_unit(self._safe_float(getattr(result, "alias_confidence", None), default=0.0))
        if alias_score > 0.0:
            score_candidates.append(alias_score)

        vector_distance = getattr(result, "vector_distance", None)
        if isinstance(vector_distance, (int, float)):
            vector_score = self._clamp_unit(1.0 - float(vector_distance))
            if vector_score > 0.0:
                score_candidates.append(vector_score)

        return max(score_candidates) if score_candidates else 0.0

    def search_by_alias(
        self,
        *,
        business_profile,
        aliases: Sequence[str] | None = None,
        traits: QueryTraits | None = None,
        limit: int | None = None,
        feature_state: FeatureState | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> AliasSearchResult:
        traits = traits or self.analyze_query(" ".join(aliases or ()), business_profile=business_profile)
        alias_values = tuple(
            value
            for value in (aliases or traits.alias_candidates)
            if value
        )
        alias_threshold = self._alias_threshold_for_business(business_profile)
        normalized_aliases = tuple(
            self._normalize_alias_input(candidate) for candidate in alias_values if candidate
        )
        normalized_aliases = tuple(alias for alias in normalized_aliases if alias)
        if not normalized_aliases:
            return AliasSearchResult(hits=tuple(), diagnostics={"alias_candidates": 0}, short_circuit=False)
        feature_state = feature_state or FeatureFlagService.snapshot(business_profile)
        if not feature_state.alias_lookup:
            return AliasSearchResult(
                hits=tuple(),
                diagnostics={"alias_candidates": len(normalized_aliases), "stage": "alias_disabled"},
                short_circuit=False,
            )

        start = time.perf_counter()
        limit = limit or self.alias_result_cap
        hits, cache_diag = self._alias_exact_hits(
            business_profile=business_profile,
            aliases=normalized_aliases,
            limit=limit,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        diagnostics: dict[str, object] = {
            "alias_candidates": len(normalized_aliases),
            "alias_cache_hit": cache_diag.get("cache_hit", 0),
            "alias_cache_miss": cache_diag.get("cache_miss", 0),
            "alias_fts_threshold": alias_threshold,
        }
        total_cache_ops = diagnostics["alias_cache_hit"] + diagnostics["alias_cache_miss"]
        if total_cache_ops:
            diagnostics["alias_cache_hit_rate"] = round(diagnostics["alias_cache_hit"] / max(1, total_cache_ops), 3)

        if hits:
            diagnostics["stage"] = "alias_exact"
            diagnostics["duration_ms"] = int((time.perf_counter() - start) * 1000)
            latency_monitor.observe("rag.alias", diagnostics["duration_ms"], tags={"stage": diagnostics["stage"]})
            _rag_log(
                "alias.exact",
                {
                    "aliases": normalized_aliases,
                    "hits": len(hits),
                    "cache_hit": diagnostics["alias_cache_hit"],
                    "cache_miss": diagnostics["alias_cache_miss"],
                },
                indent=1,
                context={"business": business_profile.id},
            )
            return AliasSearchResult(
                hits=tuple(hits[:limit]),
                diagnostics=diagnostics,
                short_circuit=bool(traits.is_identifier_like),
            )

        fuzzy_hits = self._alias_fuzzy_hits(
            business_profile=business_profile,
            traits=traits,
            limit=self.alias_fts_limit,
            threshold=alias_threshold,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        diagnostics["stage"] = "alias_fts"
        diagnostics["identifier_tokens"] = self._identifier_like_tokens(traits)
        diagnostics["alias_fts_hits"] = len(fuzzy_hits)
        diagnostics["duration_ms"] = int((time.perf_counter() - start) * 1000)
        latency_monitor.observe("rag.alias", diagnostics["duration_ms"], tags={"stage": diagnostics["stage"]})
        _rag_log(
            "alias.fts",
            {
                "query": traits.normalized,
                "hits": len(fuzzy_hits),
                "threshold": f"{alias_threshold:.2f}",
            },
            indent=1,
            context={"business": business_profile.id},
        )
        return AliasSearchResult(
            hits=tuple(fuzzy_hits[: self.alias_fts_limit]),
            diagnostics=diagnostics,
            short_circuit=False,
        )

    def search_free_text(
        self,
        *,
        business_profile,
        query: str,
        limit: int,
        traits: QueryTraits,
        alias_candidates: Sequence[ChunkResult] | None = None,
        feature_state: FeatureState | None = None,
        table_context: Mapping[str, object] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> HybridSearchResult:
        business_id = getattr(business_profile, "id", None) if business_profile else None
        with tenant_context(business_id):
            return self._search_free_text_inner(
                business_profile=business_profile,
                query=query,
                limit=limit,
                traits=traits,
                alias_candidates=alias_candidates,
                feature_state=feature_state,
                table_context=table_context,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                session_context=session_context,
            )

    def _search_free_text_inner(
        self,
        *,
        business_profile,
        query: str,
        limit: int,
        traits: QueryTraits,
        alias_candidates: Sequence[ChunkResult] | None = None,
        feature_state: FeatureState | None = None,
        table_context: Mapping[str, object] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> HybridSearchResult:
        with TRACER.start_as_current_span("knowledge.hybrid_search") as span:
            base_qs = self._base_chunk_queryset(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            query_text = (query or "").strip() or traits.normalized or traits.original
            feature_state = feature_state or FeatureFlagService.snapshot(business_profile)
            query_vector: list[float] | None
            vector_diag: dict[str, object]
            vector_ms = 0
            vector_hits: Sequence[ChunkResult]
            backend = str(getattr(settings, "RAG_SEARCH_BACKEND", "postgres") or "postgres").strip().lower()
            azure_enabled = backend == "azure"
            azure_diag: dict[str, object] = {}
            azure_duration_ms = 0
            lexical_hits: Sequence[ChunkResult]
            lexical_ms = 0
            lexical_diag: dict[str, object] = {}

            if feature_state.hybrid_search:
                query_vector, vector_diag = self._build_query_vector(
                    business_profile=business_profile,
                    query_text=query_text.lower(),
                )
            else:
                query_vector = None
                vector_diag = {"vector_disabled": True}

            if azure_enabled:
                try:
                    from apps.rag.azure_ai_search import (
                        AzureAISearchConfig,
                        build_scope_filter,
                        search as azure_search,
                    )
                except Exception as exc:  # pragma: no cover - optional dependency
                    azure_enabled = False
                    azure_diag = {"azure_import_error": str(exc)[:200]}
                else:
                    config = AzureAISearchConfig.from_settings()
                    if not config:
                        azure_enabled = False
                        azure_diag = {"azure_configured": False}
                    else:
                        filter_expr, scope_diag = build_scope_filter(
                            business_id=business_profile.id,
                            allowed_upload_ids=allowed_upload_ids,
                            agent_explicit_upload_ids=allowed_explicit_upload_ids,
                            upload_filter_threshold=config.upload_filter_threshold,
                        )
                        top = max(limit * 8, 40)
                        if scope_diag.get("scope_filter_skipped"):
                            top = max(top, limit * 20, 200)
                        if allowed_upload_ids is not None and len(allowed_upload_ids) > config.upload_filter_threshold:
                            top = max(top, limit * 20, 200)
                        top = min(500, int(top))
                        try:
                            rows, azure_query_diag = azure_search(
                                config=config,
                                business_id=business_profile.id,
                                query_text=query_text,
                                query_vector=query_vector if feature_state.hybrid_search else None,
                                top=top,
                                filter=filter_expr,
                                semantic_enabled=config.semantic_enabled,
                                semantic_config=config.semantic_config,
                                request_timeout_s=config.request_timeout_s,
                            )
                            azure_duration_ms = int(azure_query_diag.get("duration_ms") or 0)
                            azure_diag = {
                                "retrieval_backend": "azure",
                                "azure_index": config.index_name,
                                "azure_candidates_raw": len(rows),
                                "azure_duration_ms": azure_duration_ms,
                            }
                            azure_diag.update(scope_diag)
                            azure_diag.update(azure_query_diag)
                            ordered: list[uuid.UUID] = []
                            scores: dict[uuid.UUID, float] = {}
                            meta: dict[uuid.UUID, dict[str, object]] = {}
                            for row in rows:
                                chunk_id_raw = row.get("chunk_id") if isinstance(row, Mapping) else None
                                try:
                                    chunk_id = uuid.UUID(str(chunk_id_raw))
                                except (TypeError, ValueError):
                                    continue
                                if chunk_id not in scores:
                                    ordered.append(chunk_id)
                                score_raw = row.get("score") if isinstance(row, Mapping) else None
                                try:
                                    scores[chunk_id] = float(score_raw) if score_raw is not None else 0.0
                                except (TypeError, ValueError):
                                    scores[chunk_id] = 0.0
                                meta[chunk_id] = dict(row) if isinstance(row, Mapping) else {}
                            chunk_lookup = self._fetch_chunks_by_ids(
                                business_profile=business_profile,
                                chunk_ids=ordered,
                                allowed_upload_ids=allowed_upload_ids,
                                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                            )
                            max_score = max(scores.values(), default=0.0)
                            hits: list[ChunkResult] = []
                            for chunk_id in ordered:
                                chunk = chunk_lookup.get(chunk_id)
                                if not chunk:
                                    continue
                                raw_score = scores.get(chunk_id, 0.0)
                                normalized = (raw_score / max_score) if max_score else 0.0
                                hit_diag = {
                                    "stage": "azure_search",
                                    "azure_score": raw_score,
                                    "azure_score_norm": round(normalized, 6),
                                }
                                extra = meta.get(chunk_id)
                                if extra:
                                    hit_diag["azure_upload_id"] = extra.get("upload_id")
                                    hit_diag["azure_chunk_index"] = extra.get("chunk_index")
                                    hit_diag["azure_title"] = extra.get("title")
                                    hit_diag["azure_format"] = extra.get("format")
                                    hit_diag["azure_index_type"] = extra.get("index_type")
                                    hit_diag["azure_is_table_chunk"] = extra.get("is_table_chunk")
                                hits.append(
                                    ChunkResult(
                                        chunk=chunk,
                                        source_stage="azure_search",
                                        lexical_score=min(1.0, max(0.0, float(normalized))),
                                        recency_score=self._recency_score(chunk.upload),
                                        diagnostics=hit_diag,
                                    )
                                )
                            lexical_hits = tuple(hits)
                            lexical_ms = azure_duration_ms
                            lexical_diag = {"lexical_strategy": "azure"}
                        except Exception as exc:  # pragma: no cover - external dependency
                            azure_enabled = False
                            azure_diag = {
                                "retrieval_backend": "azure_failed",
                                "azure_error": str(exc)[:250],
                            }

            if azure_enabled:
                vector_hits = tuple()
            else:
                if feature_state.hybrid_search:
                    vector_hits, vector_ms = self._vector_candidates(
                        business_id=business_profile.id,
                        base_qs=base_qs,
                        query_vector=query_vector,
                        limit=limit,
                        traits=traits,
                    )
                else:
                    vector_hits = tuple()
                lexical_hits, lexical_ms, lexical_diag = self._lexical_candidates(
                    business_profile=business_profile,
                    base_qs=base_qs,
                    traits=traits,
                    limit=limit,
                )
                lexical_diag.setdefault("retrieval_backend", "postgres")
            latency_monitor.observe("rag.vector", vector_ms, tags={"business": str(business_profile.id)})
            latency_monitor.observe("rag.lexical", lexical_ms, tags={"business": str(business_profile.id)})
            if azure_duration_ms:
                latency_monitor.observe("rag.azure", azure_duration_ms, tags={"business": str(business_profile.id)})
            merged = self._merge_candidates(
                alias_candidates or tuple(),
                vector_hits,
                lexical_hits,
            )
            reranked, rerank_ms, rerank_diag = self._rerank_candidates(
                merged,
                query_vector if self.embedding_service else None,
                traits=traits,
                feature_state=feature_state,
                table_context=table_context,
                session_context=session_context,
            )
            latency_monitor.observe(
                "rag.rerank",
                rerank_ms,
                tags={
                    "business": str(business_profile.id),
                },
            )
            diagnostics = {
                "vector_candidates": len(vector_hits),
                "vector_duration_ms": vector_ms,
                "fts_candidates": len(lexical_hits),
                "fts_duration_ms": lexical_ms,
                "rerank_duration_ms": rerank_ms,
                "hybrid_enabled": feature_state.hybrid_search,
            }
            diagnostics.update(lexical_diag)
            diagnostics.update(vector_diag)
            diagnostics.update(rerank_diag)
            if azure_diag:
                diagnostics.update(azure_diag)
            diagnostics.update(self._vector_distance_stats(vector_hits))
            diagnostics["stage"] = "hybrid"
            _rag_log(
                "hybrid.summary",
                {
                    "alias_stage": len(alias_candidates or ()),
                    "vector_candidates": len(vector_hits),
                    "fts_candidates": len(lexical_hits),
                    "hybrid_enabled": feature_state.hybrid_search,
                },
                indent=1,
                context={"business": business_profile.id},
            )
            if span.is_recording():
                span.set_attribute("knowledge.hybrid.vector_ms", vector_ms)
                span.set_attribute("knowledge.hybrid.lexical_ms", lexical_ms)
                if azure_duration_ms:
                    span.set_attribute("knowledge.hybrid.azure_ms", azure_duration_ms)
                span.set_attribute("knowledge.hybrid.rerank_ms", rerank_ms)
                span.set_attribute("knowledge.hybrid.candidates", len(reranked))
            return HybridSearchResult(
                hits=tuple(reranked),
                query_vector=query_vector,
                diagnostics=diagnostics,
            )

    @staticmethod
    def _diversify_table_snippets(
        snippets: Sequence[KnowledgeSnippet],
        *,
        limit: int,
    ) -> list[KnowledgeSnippet]:
        diversified, _diagnostics = KnowledgeSearchService._diversify_table_snippets_with_diagnostics(
            snippets,
            limit=limit,
        )
        return diversified

    @staticmethod
    def _diversify_table_snippets_with_diagnostics(
        snippets: Sequence[KnowledgeSnippet],
        *,
        limit: int,
    ) -> tuple[list[KnowledgeSnippet], dict[str, object]]:
        """Round-robin across table/document buckets before final clipping."""
        bucketed: OrderedDict[str, list[KnowledgeSnippet]] = OrderedDict()
        table_bucket_ids: set[str] = set()
        upload_bucket_ids: set[str] = set()

        for snippet in snippets:
            diagnostics = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else {}
            table_id = str(snippet.table_id or diagnostics.get("table_id") or "").strip()
            upload_id = str(snippet.upload_id or "").strip()
            if upload_id:
                upload_bucket_ids.add(upload_id)

            if table_id:
                bucket_key = f"table:{table_id}"
                table_bucket_ids.add(table_id)
            elif upload_id:
                bucket_key = f"upload:{upload_id}"
            else:
                bucket_key = f"snippet:{snippet.id}"
            bucketed.setdefault(bucket_key, []).append(snippet)

        requested_limit = max(0, int(limit or 0))
        diagnostics_out: dict[str, object] = {
            "coverage_diversification_method": "table_document_round_robin_v2",
            "coverage_diversification_input_count": len(snippets),
            "coverage_diversification_limit": requested_limit,
            "coverage_diversification_bucket_count": len(bucketed),
            "coverage_diversification_table_buckets": len(table_bucket_ids),
            "coverage_diversification_upload_buckets": len(upload_bucket_ids),
            "coverage_diversification_applied": False,
            "coverage_diversification_output_count": 0,
        }

        if requested_limit <= 0 or not snippets:
            return [], diagnostics_out

        if len(bucketed) <= 1:
            result = list(snippets)[:requested_limit]
            diagnostics_out["coverage_diversification_output_count"] = len(result)
            return result, diagnostics_out

        result: list[KnowledgeSnippet] = []
        offsets: dict[str, int] = {key: 0 for key in bucketed}
        bucket_keys = list(bucketed.keys())

        while len(result) < requested_limit:
            added = False
            for key in bucket_keys:
                offset = offsets[key]
                bucket = bucketed[key]
                if offset >= len(bucket):
                    continue
                result.append(bucket[offset])
                offsets[key] = offset + 1
                added = True
                if len(result) >= requested_limit:
                    break
            if not added:
                break

        diagnostics_out["coverage_diversification_applied"] = True
        diagnostics_out["coverage_diversification_output_count"] = len(result)
        return result, diagnostics_out

    def _search_chunks(
        self,
        hits: Sequence[ChunkResult],
        *,
        limit: int,
        business_profile,
        pathway: str,
        query: str | None = None,  # NEW: For query-aware row sampling
    ) -> Sequence[KnowledgeSnippet]:
        if not hits:
            return tuple()

        snippets: list[KnowledgeSnippet] = []
        per_upload_counts: dict[uuid.UUID, int] = {}
        query_text = (query or "").strip()
        query_tokens: tuple[str, ...] = tuple(
            token
            for token in QueryNormalizer._TOKEN_SPLIT.split(
                QueryNormalizer._normalize_query_text(query_text).lower()
            )
            if token
        )
        # LLM-driven mode: allow more chunks per upload for table diversity
        # Previously capped to 3-6, now allow full limit to pass through
        max_per_upload = max(limit, self._effective_chunk_cap(business_profile, pathway))
        for hit in hits:
            chunk = hit.chunk
            upload = chunk.upload
            current = per_upload_counts.get(upload.id, 0)
            if current >= max_per_upload:
                continue
            # NEW: Pass query to enable query-aware row sampling
            table_sample: tuple[Mapping[str, object], ...] | None = self._table_row_sample(
                chunk,
                max_columns=6,
                max_rows=1,
                query=query,
            )
            snippets.append(
                self._chunk_to_snippet(
                    chunk,
                    result=hit,
                    table_row_sample=table_sample,
                    query_text=query_text,
                    query_tokens=query_tokens,
                )
            )
            per_upload_counts[upload.id] = current + 1
            if len(snippets) >= limit:
                break
        return tuple(snippets)

    def _chunk_hits(
        self,
        business_profile,
        *,
        traits: QueryTraits,
        limit: int,
        alias_result: AliasSearchResult | None = None,
        feature_state: FeatureState | None = None,
        diagnostics: dict[str, object] | None = None,
        vector_ceiling: float | None = None,
        table_context: Mapping[str, object] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> tuple[ChunkResult, ...]:
        alias_result = alias_result or AliasSearchResult(tuple(), {})
        if alias_result.short_circuit and alias_result.hits:
            return tuple(alias_result.hits[:limit])

        alias_candidates = alias_result.hits if alias_result and not alias_result.short_circuit else tuple()
        ann_cap = self._effective_chunk_cap(business_profile, "ann")
        feature_state = feature_state or FeatureFlagService.snapshot(business_profile)
        ceiling = vector_ceiling if vector_ceiling is not None else self._vector_ceiling_for_business(business_profile)
        hybrid = self.search_free_text(
            business_profile=business_profile,
            query=traits.normalized or traits.original,
            limit=max(limit, ann_cap * 2),
            traits=traits,
            alias_candidates=alias_candidates,
            feature_state=feature_state,
            table_context=table_context,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            session_context=session_context,
        )
        if diagnostics is not None:
            diagnostics["vector_distance_ceiling"] = ceiling
            diagnostics["vector_candidates"] = hybrid.diagnostics.get("vector_candidates")
            diagnostics["fts_candidates"] = hybrid.diagnostics.get("fts_candidates")
            diagnostics["vector_duration_ms"] = hybrid.diagnostics.get("vector_duration_ms")
            diagnostics["fts_duration_ms"] = hybrid.diagnostics.get("fts_duration_ms")
            diagnostics["rerank_duration_ms"] = hybrid.diagnostics.get("rerank_duration_ms")
            diagnostics["vector_distance_mean"] = hybrid.diagnostics.get("vector_distance_mean")
            diagnostics["vector_distance_min"] = hybrid.diagnostics.get("vector_distance_min")
            diagnostics["vector_distance_max"] = hybrid.diagnostics.get("vector_distance_max")
            diagnostics["fts_threshold"] = hybrid.diagnostics.get("fts_threshold")
            diagnostics["fts_condensed_query"] = hybrid.diagnostics.get("fts_condensed_query")
            diagnostics["fts_token_filter_min_length"] = hybrid.diagnostics.get("fts_token_filter_min_length")
            diagnostics["fts_tokens_used"] = tuple(hybrid.diagnostics.get("fts_tokens_used") or ())[:5]
        candidates = list(hybrid.hits)
        if not candidates:
            return tuple()

        prioritized = self._prioritize_token_hits(
            candidates,
            traits.tokens,
            fallback=max(self.token_gate_fallback, limit * 2),
        )
        if not prioritized:
            return tuple()

        filtered = self._apply_vector_threshold(
            prioritized,
            hybrid.query_vector,
            ceiling=ceiling,
            min_keep=limit,
        )
        if diagnostics is not None:
            diagnostics["vector_candidates_post_threshold"] = len(filtered)
            diagnostics["scope_candidates_preclip"] = filtered
            diagnostics["chunk_hits_rerank_reused"] = True
            diagnostics["scope_summary_preclip"] = self._build_scope_summary_from_candidates(
                filtered,
                business_profile=business_profile,
                query_tokens=traits.tokens,
                filler_tokens=self._filler_tokens_for_business(business_profile),
            )
        comprehensive_intent = bool((table_context or {}).get("comprehensive_intent"))
        preserve_head = 0 if comprehensive_intent else min(self.mmr_preserve_head, limit, len(filtered))
        if diagnostics is not None:
            diagnostics["chunk_hits_mmr_preserve_head"] = preserve_head
            diagnostics["chunk_hits_mmr_applied"] = bool(hybrid.query_vector and len(filtered) > preserve_head)
        if preserve_head:
            head = list(filtered[:preserve_head])
            tail_candidates = filtered[preserve_head:]
            if hybrid.query_vector and tail_candidates and limit > preserve_head:
                tail = self._mmr_select(
                    tail_candidates,
                    hybrid.query_vector,
                    k=max(0, limit - preserve_head),
                    lam=self.mmr_lambda,
                )
                final = head + tail
            else:
                final = head
        else:
            final = self._mmr_select(filtered, hybrid.query_vector, k=limit, lam=self.mmr_lambda)
        return tuple(final)

    @staticmethod
    def _normalize_alias_input(value: str) -> str:
        return QueryNormalizer._canonical_alias(value) if value else ""

    def _alias_exact_hits(
        self,
        *,
        business_profile,
        aliases: Sequence[str],
        limit: int,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> tuple[list[ChunkResult], dict[str, int]]:
        if not aliases:
            return [], {"cache_hit": 0, "cache_miss": 0}
        with tenant_context(business_profile.id if business_profile else None):
            version = self._get_alias_cache_version(business_profile.id)
            ordered_ids: list[uuid.UUID] = []
            cache_hit = 0
            cache_miss = 0
            missing_aliases: list[str] = []
            for alias in aliases:
                key = self._alias_cache_key(business_profile.id, alias, version)
                cached = cache.get(key)
                if cached:
                    cache_hit += 1
                    for entry in cached:
                        try:
                            ordered_ids.append(uuid.UUID(entry["chunk_id"]))
                        except (KeyError, ValueError, TypeError):
                            continue
                else:
                    cache_miss += 1
                    missing_aliases.append(alias)

            if missing_aliases:
                if allowed_upload_ids is not None and not allowed_upload_ids:
                    return [], {"cache_hit": cache_hit, "cache_miss": cache_miss}
                alias_qs = KnowledgeAlias.objects.filter(
                    business_profile=business_profile,
                    alias_normalized__in=missing_aliases,
                )
                if allowed_upload_ids is not None:
                    alias_qs = alias_qs.filter(entity__upload_id__in=allowed_upload_ids)
                else:
                    scope_clauses: list[Q] = []
                    if allowed_explicit_upload_ids:
                        scope_clauses.append(Q(entity__upload_id__in=allowed_explicit_upload_ids))
                    if scope_clauses:
                        clause = scope_clauses[0]
                        for extra in scope_clauses[1:]:
                            clause |= extra
                        alias_qs = alias_qs.filter(clause).distinct()
                alias_qs = alias_qs.select_related("entity", "entity__upload").order_by("alias_normalized")
                alias_records = list(alias_qs)
                chunk_ids = [
                    record.entity.chunk_id
                    for record in alias_records
                    if record.entity and record.entity.chunk_id
                ]
                chunk_lookup = self._fetch_chunks_by_ids(
                    business_profile=business_profile,
                    chunk_ids=chunk_ids,
                    allowed_upload_ids=allowed_upload_ids,
                    allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                )
                payloads: dict[str, list[dict[str, str]]] = {}
                for record in alias_records:
                    entity = record.entity
                    chunk = chunk_lookup.get(entity.chunk_id) if entity else None
                    if not chunk or not chunk.upload or chunk.upload.business_profile_id != business_profile.id:
                        continue
                    if chunk.upload.visibility == KnowledgeVisibility.INTERNAL:
                        continue
                    entry = {
                        "chunk_id": str(chunk.id),
                        "entity_name": entity.entity_name,
                        "entity_type": entity.entity_type,
                        "alias": record.alias_normalized,
                    }
                    payloads.setdefault(record.alias_normalized, []).append(entry)
                    ordered_ids.append(chunk.id)
                for alias_value, payload in payloads.items():
                    self._cache_alias_payload(business_profile.id, alias_value, payload)

            if not ordered_ids:
                return [], {"cache_hit": cache_hit, "cache_miss": cache_miss}

            chunk_lookup = self._fetch_chunks_by_ids(
                business_profile=business_profile,
                chunk_ids=ordered_ids[:limit],
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            hits: list[ChunkResult] = []
            for identifier in ordered_ids:
                chunk = chunk_lookup.get(identifier)
                if not chunk:
                    continue
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="alias_exact",
                        alias_confidence=1.0,
                        recency_score=self._recency_score(chunk.upload),
                        rerank_score=1.0,
                        diagnostics={"alias": str(identifier)},
                    )
                )
                if len(hits) >= limit:
                    break
            return hits, {"cache_hit": cache_hit, "cache_miss": cache_miss}

    def _alias_fuzzy_hits(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        limit: int,
        threshold: float,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> list[ChunkResult]:
        identifier_tokens = self._identifier_like_tokens(traits)
        if not identifier_tokens:
            return []
        query_text = " ".join(identifier_tokens[:4]) or (traits.normalized or traits.original or "")
        with tenant_context(business_profile.id if business_profile else None):
            alias_qs = KnowledgeAlias.objects.filter(business_profile=business_profile)
            if allowed_upload_ids is not None:
                if not allowed_upload_ids:
                    return []
                alias_qs = alias_qs.filter(entity__upload_id__in=allowed_upload_ids)
            else:
                scope_clauses: list[Q] = []
                if allowed_explicit_upload_ids:
                    scope_clauses.append(Q(entity__upload_id__in=allowed_explicit_upload_ids))
                if scope_clauses:
                    clause = scope_clauses[0]
                    for extra in scope_clauses[1:]:
                        clause |= extra
                    alias_qs = alias_qs.filter(clause).distinct()
            alias_qs = (
                alias_qs.annotate(sim=TrigramSimilarity("alias_search_vector", query_text))
                .filter(sim__gte=threshold)
                .order_by("-sim")[: max(limit, 10)]
                .select_related("entity", "entity__upload")
            )
            alias_records = list(alias_qs)
            chunk_ids = [
                record.entity.chunk_id
                for record in alias_records
                if record.entity and record.entity.chunk_id
            ]
            chunk_lookup = self._fetch_chunks_by_ids(
                business_profile=business_profile,
                chunk_ids=chunk_ids,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            seen: set[uuid.UUID] = set()
            hits: list[ChunkResult] = []
            for record in alias_records:
                entity = record.entity
                chunk = chunk_lookup.get(entity.chunk_id) if entity else None
                if not chunk or not chunk.upload or chunk.upload.business_profile_id != business_profile.id:
                    continue
                if chunk.upload.visibility == KnowledgeVisibility.INTERNAL:
                    continue
                if chunk.id in seen:
                    continue
                seen.add(chunk.id)
                confidence = float(getattr(record, "sim", 0.0) or 0.0)
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="alias_fts",
                        alias_confidence=min(1.0, confidence),
                        recency_score=self._recency_score(chunk.upload),
                        rerank_score=min(1.0, confidence),
                        diagnostics={"alias": record.alias_normalized},
                    )
                )
                if len(hits) >= limit:
                    break
            return hits

    def _fetch_chunks_by_ids(
        self,
        *,
        business_profile,
        chunk_ids: Sequence[uuid.UUID],
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> dict[uuid.UUID, KnowledgeUploadChunk]:
        if not chunk_ids:
            return {}
        qs = KnowledgeUploadChunk.objects.filter(
            business_profile=business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
            id__in=chunk_ids,
        )
        qs = self._apply_chunk_scope(
            qs,
            business_profile=business_profile,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        qs = apply_customer_visible_chunks(qs.select_related("upload"))
        return {chunk.id: chunk for chunk in qs}

    def _base_chunk_queryset(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ):
        qs = KnowledgeUploadChunk.objects.filter(
            business_profile=business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
        ).filter(
            # NULL-safe filter: include legacy chunks missing search_tier.
            Q(metadata__search_tier__isnull=True) | ~Q(metadata__search_tier="drill_down")
        )
        qs = self._apply_chunk_scope(
            qs,
            business_profile=business_profile,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        return apply_customer_visible_chunks(qs.select_related("upload"))

    def _apply_chunk_scope(
        self,
        queryset,
        *,
        business_profile,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ):
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return queryset.none()
            return queryset.filter(upload_id__in=allowed_upload_ids)

        clauses: list[Q] = []
        if allowed_explicit_upload_ids:
            clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
        if not clauses:
            return queryset
        combined = clauses[0]
        for clause in clauses[1:]:
            combined |= clause
        return queryset.filter(combined)

    def _merge_candidates(self, *groups: Sequence[ChunkResult]) -> list[ChunkResult]:
        """
        Merge candidates from multiple search pathways, keeping the best occurrence
        of each chunk for deterministic results.

        When the same chunk appears in multiple groups, keep the one with the
        highest combined score (alias_confidence + lexical_score - vector_distance).
        This ensures consistent ranking regardless of which search path returns first.
        """
        best: dict[uuid.UUID, ChunkResult] = {}
        order: list[uuid.UUID] = []  # Preserve first-seen order for final output

        def _merge_score(hit: ChunkResult) -> float:
            # Combine available scores: higher is better
            # Note: vector_distance is lower-is-better, so we negate it
            vec_contrib = -(hit.vector_distance or 0.0) if hit.vector_distance else 0.0
            return hit.alias_confidence + hit.lexical_score + vec_contrib

        for group in groups:
            for hit in group:
                cid = hit.chunk_id
                if cid not in best:
                    order.append(cid)
                    best[cid] = hit
                else:
                    # Keep the occurrence with the higher merge score
                    existing_score = _merge_score(best[cid])
                    new_score = _merge_score(hit)
                    if new_score > existing_score:
                        best[cid] = hit

        return [best[cid] for cid in order]

    def _vector_candidates(
        self,
        *,
        business_id,
        base_qs,
        query_vector: list[float] | None,
        limit: int,
        traits: QueryTraits,
    ) -> tuple[list[ChunkResult], int]:
        if not query_vector:
            return [], 0
        adaptive_factor = self._ann_factor_for_query(traits)
        with TRACER.start_as_current_span("knowledge.vector_candidates") as span:
            if span.is_recording():
                span.set_attribute("knowledge.business_id", str(business_id))
                span.set_attribute("knowledge.vector.limit", limit)
                span.set_attribute("knowledge.vector.adaptive_factor", adaptive_factor)
                span.set_attribute("knowledge.vector_tokens", traits.token_count)
            base_k = max(limit * 10, 60)
            K = int(base_k * adaptive_factor)
            start = time.perf_counter()
            if self.ivfflat_probes:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SET ivfflat.probes = %s", [self.ivfflat_probes])
                except Exception:  # pragma: no cover - diagnostic only
                    logger.debug("Unable to set ivfflat.probes", exc_info=True)
            ann_qs = (
                base_qs.exclude(embedding__isnull=True)
                .annotate(distance=CosineDistance("embedding", query_vector))
                # Secondary sort by id for deterministic tie-breaking
                .order_by("distance", "id")[:K]
            )
            hits: list[ChunkResult] = []
            distances: list[float] = []
            for chunk in ann_qs:
                distance = getattr(chunk, "distance", None)
                dist_val = None
                if distance is not None:
                    try:
                        dist_val = float(distance)
                        distances.append(dist_val)
                    except (TypeError, ValueError):
                        dist_val = None
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="vector_ann",
                        vector_distance=dist_val,
                        recency_score=self._recency_score(chunk.upload),
                        diagnostics={"stage": "vector_ann"},
                    )
                )
            duration_ms = int((time.perf_counter() - start) * 1000)
            distance_min = min(distances) if distances else None
            distance_max = max(distances) if distances else None
            distance_avg = (sum(distances) / len(distances)) if distances else None
            if span.is_recording():
                span.set_attribute("knowledge.vector_duration_ms", duration_ms)
                if distance_min is not None:
                    span.set_attribute("knowledge.vector_distance_min", distance_min)
                if distance_avg is not None:
                    span.set_attribute("knowledge.vector_distance_mean", distance_avg)
            _rag_log(
                "vector.candidates",
                {
                    "query_tokens": traits.token_count,
                    "candidates": len(hits),
                    "d_min": f"{distance_min:.4f}" if distance_min is not None else None,
                    "d_max": f"{distance_max:.4f}" if distance_max is not None else None,
                    "d_avg": f"{distance_avg:.4f}" if distance_avg is not None else None,
                },
                indent=1,
                context={"business": business_id},
            )
            return hits, duration_ms

    @staticmethod
    def _vector_distance_stats(candidates: Sequence[ChunkResult]) -> dict[str, float]:
        distances = [
            float(hit.vector_distance)
            for hit in candidates
            if isinstance(hit.vector_distance, (int, float))
        ]
        if not distances:
            return {}
        average = sum(distances) / len(distances)
        similarities = [1.0 - distance for distance in distances]
        sim_average = sum(similarities) / len(similarities)
        clamped_scores = [max(-1.0, min(1.0, sim)) for sim in similarities]
        score_average = sum(clamped_scores) / len(clamped_scores)
        return {
            "vector_distance_min": min(distances),
            "vector_distance_max": max(distances),
            "vector_distance_mean": round(average, 5),
            "vector_similarity_min": round(min(similarities), 5),
            "vector_similarity_max": round(max(similarities), 5),
            "vector_similarity_mean": round(sim_average, 5),
            "vector_score_mean": round(score_average, 5),
        }

    def _condensed_query_for_fts(self, business_profile, traits: QueryTraits) -> str:
        tokens = list(traits.tokens or ())
        if not tokens:
            return traits.normalized or traits.original or ""
        filler = self._filler_tokens_for_business(business_profile)
        filtered = [token for token in tokens if token and token not in filler]
        min_length = self._significant_token_min_length(business_profile)
        strong = [token for token in filtered if len(token) >= min_length]
        max_tokens = self._fts_condense_max_tokens(business_profile)
        chosen: list[str] = []
        seen: set[str] = set()
        for token in strong:
            if token in seen:
                continue
            seen.add(token)
            chosen.append(token)
            if len(chosen) >= max_tokens:
                break
        if not chosen:
            fallback = filtered or tokens
            chosen = []
            seen.clear()
            for token in fallback:
                if not token:
                    continue
                if token in filler:
                    continue
                if token in seen:
                    continue
                seen.add(token)
                chosen.append(token)
                if len(chosen) >= max_tokens:
                    break
        condensed = " ".join(chosen).strip()
        return condensed or traits.normalized or traits.original or ""

    def _lexical_candidates(
        self,
        *,
        business_profile,
        base_qs,
        traits: QueryTraits,
        limit: int,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        if getattr(settings, "RAG_FTS_ENABLED", True) and connection.vendor == "postgresql":
            hits, duration_ms, diag = self._fts_candidates(
                business_profile=business_profile,
                base_qs=base_qs,
                traits=traits,
                limit=limit,
            )
            if hits:
                return hits, duration_ms, diag
            fallback_hits, fallback_ms, fallback_diag = self._trigram_candidates(
                business_profile=business_profile,
                base_qs=base_qs,
                traits=traits,
                limit=limit,
            )
            fallback_diag.update(
                {
                    "lexical_strategy": "fts+trigram",
                    "fts_fallback": True,
                    "fts_duration_ms": duration_ms,
                    "fts_candidates": diag.get("fts_candidates", 0),
                    "fts_rank_max": diag.get("fts_rank_max", 0.0),
                    "fts_error": diag.get("fts_error"),
                    "fts_config": diag.get("fts_config"),
                    "fts_search_type": diag.get("fts_search_type"),
                }
            )
            total_ms = duration_ms + fallback_ms
            return fallback_hits, total_ms, fallback_diag

        return self._trigram_candidates(
            business_profile=business_profile,
            base_qs=base_qs,
            traits=traits,
            limit=limit,
        )

    def _fts_candidates(
        self,
        *,
        business_profile,
        base_qs,
        traits: QueryTraits,
        limit: int,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        with TRACER.start_as_current_span("knowledge.lexical_candidates") as span:
            condensed_query = self._condensed_query_for_fts(business_profile, traits)
            start = time.perf_counter()
            N = max(limit * 8, 40)
            config = "simple"
            search_type = "plain"
            vector = SearchVector("content", config=config)
            token_min_length = self._significant_token_min_length(business_profile)
            filler = self._filler_tokens_for_business(business_profile)
            condensed_tokens = tuple(
                token
                for token in QueryNormalizer._TOKEN_SPLIT.split(
                    QueryNormalizer._normalize_query_text(condensed_query).lower()
                )
                if token
            )
            significant = [token for token in condensed_tokens if len(token) >= token_min_length and token not in filler]
            combined_query: SearchQuery | None = None
            for token in significant:
                token_query = SearchQuery(token, search_type=search_type, config=config)
                combined_query = token_query if combined_query is None else (combined_query | token_query)

            if combined_query is None:
                combined_query = SearchQuery(condensed_query, search_type=search_type, config=config)

            generic_anchor_tokens = set(filler)
            generic_anchor_tokens.update(self.table_query_keywords)
            generic_anchor_tokens.update(self.table_column_hint_base)
            generic_anchor_tokens.update({"card", "cards", "credit"})
            anchor_token = next(
                (token for token in significant if token not in generic_anchor_tokens),
                None,
            )
            filter_query = (
                SearchQuery(anchor_token, search_type=search_type, config=config)
                if anchor_token
                else combined_query
            )
            try:
                fts_qs = (
                    base_qs.annotate(
                        fts_vector=vector,
                        rank=SearchRank(vector, combined_query, cover_density=True),
                    )
                    # Apply @@ filter so Postgres can use the GIN index on
                    # `to_tsvector('simple', coalesce(content,''))`.
                    .filter(fts_vector=filter_query)
                    # Secondary sort by id for deterministic tie-breaking
                    .order_by("-rank", "id")[:N]
                )
                rows = [(chunk, float(getattr(chunk, "rank", 0.0) or 0.0)) for chunk in fts_qs]
            except Exception as exc:  # pragma: no cover - DB / config edge cases
                duration_ms = int((time.perf_counter() - start) * 1000)
                diag: dict[str, object] = {
                    "lexical_strategy": "fts_error",
                    "fts_config": config,
                    "fts_search_type": search_type,
                    "fts_error": str(exc)[:200],
                }
                if span.is_recording():
                    span.set_attribute("knowledge.lexical_hits", 0)
                    span.set_attribute("knowledge.lexical_duration_ms", duration_ms)
                return [], duration_ms, diag

            max_rank = max((rank for _, rank in rows), default=0.0)
            hits: list[ChunkResult] = []
            for chunk, rank in rows:
                normalized_rank = (rank / max_rank) if max_rank > 0 else 0.0
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="content_fts",
                        lexical_score=min(1.0, max(0.0, normalized_rank)),
                        recency_score=self._recency_score(chunk.upload),
                        diagnostics={
                            "stage": "content_fts",
                            "fts_rank": round(rank, 6),
                            "fts_rank_norm": round(normalized_rank, 6),
                        },
                    )
                )
            duration_ms = int((time.perf_counter() - start) * 1000)
            diag = {
                "lexical_strategy": "fts",
                "fts_config": config,
                "fts_search_type": search_type,
                "fts_condensed_query": condensed_query,
                "fts_anchor_token": anchor_token,
                "fts_candidates": len(hits),
                "fts_rank_max": round(max_rank, 6) if max_rank else 0.0,
            }
            if span.is_recording():
                span.set_attribute("knowledge.lexical_hits", len(hits))
                span.set_attribute("knowledge.lexical_duration_ms", duration_ms)
            return hits, duration_ms, diag

    def _trigram_candidates(
        self,
        *,
        business_profile,
        base_qs,
        traits: QueryTraits,
        limit: int,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        with TRACER.start_as_current_span("knowledge.lexical_candidates") as span:
            condensed_query = self._condensed_query_for_fts(business_profile, traits)
            threshold = self._lexical_threshold_for_business(business_profile, traits)
            token_min_length = self._significant_token_min_length(business_profile)
            condensed_tokens = tuple(
                token for token in QueryNormalizer._TOKEN_SPLIT.split(
                    QueryNormalizer._normalize_query_text(condensed_query).lower()
                )
                if token
            )
            filler = self._filler_tokens_for_business(business_profile)
            ordered_tokens = tuple(token for token in traits.tokens if token and token not in filler)
            token_filter = self._build_fts_token_filter(
                ordered_tokens or condensed_tokens or traits.tokens,
                min_length=token_min_length,
            )
            fts_base = base_qs.filter(token_filter) if token_filter else base_qs
            N = max(limit * 8, 40)
            start = time.perf_counter()
            fts_qs = (
                fts_base.annotate(sim=TrigramSimilarity("content", condensed_query))
                .filter(sim__gte=threshold)
                # Secondary sort by id for deterministic tie-breaking
                .order_by("-sim", "id")[:N]
            )
            hits: list[ChunkResult] = []
            for chunk in fts_qs:
                sim = getattr(chunk, "sim", 0.0) or 0.0
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="content_trigram",
                        lexical_score=float(sim),
                        recency_score=self._recency_score(chunk.upload),
                        diagnostics={"stage": "content_trigram"},
                    )
                )
            duration_ms = int((time.perf_counter() - start) * 1000)
            diag = {
                "lexical_strategy": "trigram",
                # Keep legacy `fts_*` keys for downstream diagnostics consumers.
                "fts_threshold": threshold,
                "fts_condensed_query": condensed_query,
                "fts_tokens_used": condensed_tokens[:5],
                "fts_token_filter_min_length": token_min_length,
                # Trigram-specific keys for richer observability.
                "trigram_threshold": threshold,
                "trigram_condensed_query": condensed_query,
                "trigram_tokens_used": condensed_tokens[:5],
                "trigram_token_filter_min_length": token_min_length,
            }
            if span.is_recording():
                span.set_attribute("knowledge.lexical_hits", len(hits))
                span.set_attribute("knowledge.lexical_duration_ms", duration_ms)
                span.set_attribute("knowledge.lexical_threshold", threshold)
            return hits, duration_ms, diag

    def _build_query_vector(
        self,
        *,
        business_profile,
        query_text: str,
    ) -> tuple[list[float] | None, dict[str, object]]:
        diagnostics: dict[str, object] = {"vector_cache_hit": False}
        if not self.embedding_service:
            return None, diagnostics
        model_name = getattr(self.embedding_service, "model", "local")
        qvec_version = self._get_query_cache_version(business_profile.id)
        digest_source = f"{business_profile.id}:{model_name}:{query_text}".encode("utf-8")
        cache_key = f"rag:qvec:{qvec_version}:{hashlib.sha256(digest_source).hexdigest()[:32]}"
        query_vector: list[float] | None = cache.get(cache_key)
        diagnostics["vector_cache_hit"] = query_vector is not None
        if query_vector is None:
            try:
                t0 = time.time()
                query_vector = self.embedding_service.embed_text(query_text)
                payload = list(query_vector) if isinstance(query_vector, (list, tuple)) else query_vector
                approx_size = len(payload) * 8 if isinstance(payload, list) else 0
                if approx_size <= self.query_vector_cache_max_bytes:
                    cache.set(cache_key, payload, timeout=self.query_vector_cache_ttl)
                diagnostics["vector_embed_ms"] = int((time.time() - t0) * 1000)
            except EmbeddingProviderError as exc:
                logger.warning("Query embedding failed: %s", exc)
                query_vector = None
        return query_vector, diagnostics

    def _apply_vector_threshold(
        self,
        candidates: Sequence[ChunkResult],
        query_vector: list[float] | None,
        *,
        ceiling: float | None,
        min_keep: int = 0,
    ) -> list[ChunkResult]:
        threshold = ceiling if ceiling is not None else self.vector_distance_ceiling
        if not threshold or threshold <= 0 or not query_vector:
            return list(candidates)
        filtered = [
            hit
            for hit in candidates
            if hit.vector_distance is None or hit.vector_distance <= threshold
        ]
        if not filtered:
            return list(candidates)
        if min_keep > 0 and len(filtered) < min_keep:
            seen = {hit.chunk_id for hit in filtered}
            for hit in candidates:
                if hit.chunk_id in seen:
                    continue
                filtered.append(hit)
                seen.add(hit.chunk_id)
                if len(filtered) >= min_keep:
                    break
        return filtered

    def _mmr_select(
        self,
        candidates: Sequence[ChunkResult],
        query_vector: list[float] | None,
        *,
        k: int,
        lam: float,
    ) -> list[ChunkResult]:
        if not query_vector:
            return list(candidates)[:k]
        selected: list[ChunkResult] = []
        remaining = list(candidates)
        while remaining and len(selected) < k:
            def score(hit: ChunkResult) -> tuple[float, str]:
                vector = self._chunk_embedding(hit)
                rel = self._cosine_similarity(query_vector, vector) if vector else 0.0
                diversity = 0.0
                if selected and vector:
                    sims = [
                        self._cosine_similarity(self._chunk_embedding(other), vector)
                        for other in selected
                        if self._chunk_embedding(other)
                    ]
                    diversity = max(sims) if sims else 0.0
                mmr_score = lam * rel - (1 - lam) * diversity
                # Stable tie-breaking: use chunk_id as secondary sort key
                return (mmr_score, str(hit.chunk_id))
            best = max(remaining, key=score)
            selected.append(best)
            remaining.remove(best)
        return selected

    @staticmethod
    def _chunk_embedding(hit: ChunkResult) -> Sequence[float] | None:
        embedding = getattr(hit.chunk, "embedding", None)
        return embedding if isinstance(embedding, Sequence) else None

    @staticmethod
    def _recency_score(upload: KnowledgeUpload) -> float:
        updated = getattr(upload, "updated_at", None)
        if not updated:
            return 0.0
        age_days = max(0.0, (timezone.now() - updated).total_seconds() / 86400.0)
        half_life = max(1.0, float(getattr(settings, "RAG_RECENCY_DECAY_DAYS", 90)))
        floor = float(getattr(settings, "RAG_RECENCY_MIN_FLOOR", 0.05))
        boost = float(getattr(settings, "RAG_RECENCY_BONUS_FRESH", 0.15))
        recency = 0.5 ** (age_days / half_life)
        if age_days <= 7:
            recency += boost
        return max(floor, min(1.0, recency))

    @staticmethod
    def _lexical_threshold(traits: QueryTraits) -> float:
        if traits.token_count <= 3:
            return 0.25
        if traits.token_count <= 6:
            return 0.2
        return 0.15

    @staticmethod
    def _extract_query_tokens(query: str) -> tuple[str, ...]:
        if not query:
            return tuple()
        normalized = QueryNormalizer._normalize_query_text(query).lower()
        tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if len(token) >= 3]
        seen: dict[str, None] = {}
        for token in tokens:
            if token and token not in seen:
                seen[token] = None
        return tuple(seen.keys())

    def _prioritize_token_hits(
        self,
        candidates: Sequence[ChunkResult],
        tokens: tuple[str, ...],
        fallback: int,
    ) -> list[ChunkResult]:
        if not tokens:
            return list(candidates)
        matched: list[ChunkResult] = []
        remainder: list[ChunkResult] = []
        for candidate in candidates:
            cont = self._chunk_contains_tokens(candidate.chunk, tokens)
            if cont:
                matched.append(candidate)
            else:
                remainder.append(candidate)
        if not matched:
            return list(candidates)
        tail = remainder[:fallback] if fallback else []
        return matched + tail

    @staticmethod
    def _chunk_contains_tokens(chunk: KnowledgeUploadChunk, tokens: tuple[str, ...]) -> bool:
        if not tokens:
            return True
        text = (chunk.content or "").lower()
        if not text:
            return False
        if any(token in text for token in tokens):
            return True
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        alias_blob = str(metadata.get("alias_string") or "").lower()
        if alias_blob and any(token in alias_blob for token in tokens):
            return True
        return False

    def _build_fts_token_filter(self, tokens: tuple[str, ...], *, min_length: int) -> Q | None:
        if not tokens:
            return None
        significant = [token for token in tokens if len(token) >= min_length][:3]
        if not significant:
            return None
        clause = Q()
        for token in significant:
            clause |= Q(content__icontains=token) | Q(metadata__alias_string__icontains=token)
        return clause

    def _read_state_for_content(self, content: str | None, metadata: Mapping[str, object] | None) -> str:
        if not content:
            return KNOWLEDGE_READ_STATE_SUMMARY
        target_threshold = self.read_ready_threshold
        meta = metadata or {}
        if meta.get("is_table_chunk"):
            target_threshold = self.table_ready_threshold
        if len(content) >= target_threshold:
            return KNOWLEDGE_READ_STATE_FULL
        return KNOWLEDGE_READ_STATE_SUMMARY

    def _ann_factor_for_query(self, traits: QueryTraits) -> float:
        token_count = traits.token_count
        if token_count <= 3:
            return self.short_query_ann_multiplier
        if token_count <= 6:
            return 1.5
        return 1.0

    def _effective_neighbor_window(self, chunk: KnowledgeUploadChunk, default_neighbor: int) -> int:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        window = default_neighbor
        if metadata.get("is_table_chunk"):
            window = max(window, self.entity_neighbor_min)
        if metadata.get("entity_name"):
            window = max(window, self.entity_neighbor_min + 1)
        return window

    @staticmethod
    def _fetch_entity_chunks(
        upload: KnowledgeUpload,
        entity_name: str,
        *,
        cache: dict[tuple[uuid.UUID, str], list[KnowledgeUploadChunk]],
    ) -> list[KnowledgeUploadChunk]:
        if not entity_name:
            return []
        key = (upload.id, entity_name.lower())
        if key in cache:
            return cache[key]
        qs = KnowledgeUploadChunk.objects.filter(
            upload=upload,
            metadata__entity_name__iexact=entity_name,
        ).order_by("chunk_index")
        cache[key] = list(qs)
        return cache[key]
    
    def _effective_neighbor_window(self, chunk: KnowledgeUploadChunk, default_neighbor: int) -> int:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        window = default_neighbor
        if metadata.get("is_table_chunk"):
            window = max(window, self.entity_neighbor_min)
        if metadata.get("entity_name"):
            window = max(window, self.entity_neighbor_min + 1)
        return window

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

    def _identifier_like_tokens(self, traits: QueryTraits) -> list[str]:
        tokens: list[str] = []
        pattern = QueryNormalizer._IDENTIFIER_PATTERN
        for token in traits.tokens:
            if not token:
                continue
            if pattern.fullmatch(token):
                tokens.append(token)
                continue
            if any(ch.isdigit() for ch in token) and any(sym in token for sym in ("-", "_")):
                tokens.append(token)
        if traits.alias_candidates:
            for alias in traits.alias_candidates:
                if alias and pattern.fullmatch(alias):
                    tokens.append(alias)
        return tokens

    def _query_has_entity_tokens(self, business_profile, traits: QueryTraits) -> bool:
        tokens = list(traits.tokens or ())
        if not tokens:
            return False
        filler = self._filler_tokens_for_business(business_profile)
        meaningful = [t for t in tokens if t and t not in filler]
        if len([t for t in meaningful if len(t) > 3]) >= 2:
            return True
        if self._identifier_like_tokens(traits):
            return True
        return False

    def _table_search_snippets(
        self,
        *,
        business_profile,
        query_text: str,
        limit: int,
        matched_columns: set[str],
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        comprehensive_intent: bool = False,
    ) -> tuple[KnowledgeSnippet, ...]:
        normalized_query = (query_text or "").strip()
        if not normalized_query:
            return tuple()
        row_cap = self._table_row_result_cap_for_business(business_profile, requested=limit)

        # DEBUG: Log entry into _table_search_snippets
        _rag_log(
            "table.search_snippets_entry",
            {
                "query": normalized_query[:100],
                "comprehensive_intent_received": comprehensive_intent,
                "row_cap": row_cap,
                "limit": limit,
                "matched_columns": list(matched_columns)[:10] if matched_columns else [],
            },
            indent=3,
            context={"business": business_profile.id},
        )
        total_keywords = (
            "total",
            "sum",
            "overall",
            "اجمالي",
            "إجمالي",
            "الاجمالي",
            "المجموع",
        )

        def _normalize_label(value: str | None) -> str:
            return re.sub(r"\s+", " ", (value or "").strip().lower())

        def _is_total_column(label: str | None) -> bool:
            normalized = _normalize_label(label)
            if not normalized:
                return False
            return any(keyword in normalized for keyword in total_keywords)

        def _parse_numeric(value: str | None) -> float | None:
            if not isinstance(value, str):
                return None
            text = value.strip()
            if not text:
                return None
            cleaned = re.sub(r"[^\d\-,\.]", "", text)
            cleaned = cleaned.replace(",", "")
            if not cleaned or cleaned in {"-", "."}:
                return None
            try:
                return float(cleaned)
            except ValueError:
                return None

        def _format_total(value: float | None, raw: str | None = None) -> str | None:
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            if value is None:
                return None
            rounded = round(value)
            if abs(value - rounded) < 1e-6:
                return f"{rounded:,}"
            return f"{value:,.2f}".rstrip("0").rstrip(".")

        def _is_numeric_value(value: str | None) -> bool:
            return _parse_numeric(value) is not None
        cell_qs = KnowledgeUploadTableCell.objects.filter(table__upload__business_profile=business_profile).exclude(
            table__upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        cell_qs = self._filter_queryable_table_uploads(
            cell_qs,
            format_lookup="table__upload__ingestion_metadata__format",
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return tuple()
            cell_qs = cell_qs.filter(table__upload_id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                cell_qs = cell_qs.filter(clause)
        if matched_columns:
            column_filter = Q()
            for column in matched_columns:
                column_filter |= Q(column_key__iexact=column) | Q(column_key__icontains=column)
            cell_qs = cell_qs.filter(column_filter)
        # NOTE: We intentionally do not apply per-token AND filters here.
        # Requiring every cell to contain all query tokens can drop valid rows
        # when entity names and generic terms are split across columns.
        # We rely on TrigramSimilarity over raw_text for fuzzy row matching.
        cell_qs = (
            cell_qs.annotate(sim=TrigramSimilarity("raw_text", normalized_query))
            .filter(sim__gte=self.table_similarity_threshold)
            .order_by("-sim")
            .select_related("row__table__upload")
        )
        # LLM-driven mode: always collect more candidates for table diversity
        # Let snippet limit (RAG_MAX_SNIPPETS_PER_SEARCH) be the only hard cap
        candidate_limit = row_cap * 5  # Always use higher limit
        top_cells = list(cell_qs[:candidate_limit])
        row_priority: list[uuid.UUID] = []
        cell_diag: dict[uuid.UUID, dict[str, object]] = {}
        # Track table_id for each row to enable round-robin diversification
        row_to_table: dict[uuid.UUID, uuid.UUID] = {}

        if top_cells:
            for cell in top_cells:
                row = cell.row
                if not row or not row.id or row.id in cell_diag:
                    continue
                table = getattr(row, "table", None)
                upload = getattr(table, "upload", None) if table else None
                if not upload or upload.business_profile_id != business_profile.id:
                    continue
                similarity = float(getattr(cell, "sim", 0.0) or 0.0)
                cell_diag[row.id] = {
                    "column_key": cell.column_key,
                    "value": cell.raw_text,
                    "similarity": similarity,
                    "table_id": table.id,
                }
                row_priority.append(row.id)
                row_to_table[row.id] = table.id
                # LLM-driven mode: never break early, collect all candidates
                # Table diversity will be applied after collection
        else:
            # Fallback: match on row.raw_text when no individual cell is similar enough.
            row_qs = KnowledgeUploadTableRow.objects.filter(
                table__upload__business_profile=business_profile,
            ).exclude(
                table__upload__visibility=KnowledgeVisibility.INTERNAL,
            )
            row_qs = self._filter_queryable_table_uploads(
                row_qs,
                format_lookup="table__upload__ingestion_metadata__format",
            )
            if allowed_upload_ids is not None:
                row_qs = row_qs.filter(table__upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    row_qs = row_qs.filter(clause)
            row_qs = (
                row_qs.annotate(sim=TrigramSimilarity("raw_text", normalized_query))
                .filter(sim__gte=self.table_similarity_threshold)
                .order_by("-sim")
                .select_related("table__upload")
            )
            fallback_limit = row_cap * 5 if comprehensive_intent else row_cap
            top_rows = list(row_qs[:fallback_limit])
            for row in top_rows:
                if not row or not row.id or row.id in cell_diag:
                    continue
                table = getattr(row, "table", None)
                upload = getattr(table, "upload", None) if table else None
                if not upload or upload.business_profile_id != business_profile.id:
                    continue
                similarity = float(getattr(row, "sim", 0.0) or 0.0)
                cell_diag[row.id] = {
                    "column_key": None,
                    "value": row.raw_text,
                    "similarity": similarity,
                    "table_id": table.id,
                }
                row_priority.append(row.id)
                row_to_table[row.id] = table.id
                # LLM-driven mode: never break early, collect all candidates
                # Table diversity will be applied after collection

        if not row_priority:
            _rag_log(
                "table.search_snippets_no_candidates",
                {"comprehensive_intent": comprehensive_intent, "query": normalized_query[:50]},
                indent=3,
                context={"business": business_profile.id},
            )
            return tuple()

        # DEBUG: Log collected candidates BEFORE diversification
        table_distribution_before: dict[str, int] = {}
        for rid in row_priority:
            tid = row_to_table.get(rid)
            if tid:
                key = str(tid)[:8]
                table_distribution_before[key] = table_distribution_before.get(key, 0) + 1
        _rag_log(
            "table.candidates_collected",
            {
                "comprehensive_intent": comprehensive_intent,
                "total_candidates": len(row_priority),
                "distinct_tables": len(set(row_to_table.values())),
                "table_distribution": table_distribution_before,
                "row_cap": row_cap,
            },
            indent=3,
            context={"business": business_profile.id},
        )

        # LLM-driven mode: ALWAYS apply round-robin diversification across tables
        # This ensures results are spread across multiple tables for full coverage
        if len(row_to_table) > 0:  # Always diversify, regardless of intent
            distinct_tables = set(row_to_table.values())
            _rag_log(
                "table.diversification_check",
                {
                    "comprehensive_intent": comprehensive_intent,
                    "distinct_tables_count": len(distinct_tables),
                    "will_diversify": len(distinct_tables) > 1,
                },
                indent=3,
                context={"business": business_profile.id},
            )
            if len(distinct_tables) > 1:
                # Group rows by table, preserving similarity order within each table
                rows_by_table: dict[uuid.UUID, list[uuid.UUID]] = {}
                for row_id in row_priority:
                    table_id = row_to_table.get(row_id)
                    if table_id:
                        if table_id not in rows_by_table:
                            rows_by_table[table_id] = []
                        rows_by_table[table_id].append(row_id)

                # Round-robin selection across tables
                diversified_priority: list[uuid.UUID] = []
                table_queues = {tid: list(rows) for tid, rows in rows_by_table.items()}
                table_order = list(table_queues.keys())  # Stable order

                while len(diversified_priority) < row_cap and table_queues:
                    for table_id in list(table_order):
                        if table_id not in table_queues:
                            continue
                        queue = table_queues[table_id]
                        if queue:
                            diversified_priority.append(queue.pop(0))
                            if len(diversified_priority) >= row_cap:
                                break
                        if not queue:
                            del table_queues[table_id]

                # Log diversification result with table distribution AFTER
                table_distribution_after: dict[str, int] = {}
                for rid in diversified_priority:
                    tid = row_to_table.get(rid)
                    if tid:
                        key = str(tid)[:8]
                        table_distribution_after[key] = table_distribution_after.get(key, 0) + 1
                _rag_log(
                    "table.diversification_applied",
                    {
                        "before_count": len(row_priority),
                        "after_count": len(diversified_priority),
                        "distinct_tables": len(distinct_tables),
                        "distribution_before": table_distribution_before,
                        "distribution_after": table_distribution_after,
                    },
                    indent=3,
                    context={"business": business_profile.id},
                )
                row_priority = diversified_priority
            else:
                _rag_log(
                    "table.diversification_skipped",
                    {"reason": "only_one_table", "distinct_tables": len(distinct_tables)},
                    indent=3,
                    context={"business": business_profile.id},
                )
        else:
            _rag_log(
                "table.diversification_not_applicable",
                {
                    "comprehensive_intent": comprehensive_intent,
                    "row_to_table_count": len(row_to_table),
                },
                indent=3,
                context={"business": business_profile.id},
            )
        rows = (
            KnowledgeUploadTableRow.objects.filter(id__in=row_priority)
            .select_related("table__upload__business_profile")
            .prefetch_related("cells")
        )
        row_map = {row.id: row for row in rows}
        ingestion_diag_cache: dict[uuid.UUID, dict[str, object]] = {}
        row_chunk_map: dict[tuple[str, int], KnowledgeUploadChunk] = {}
        row_keys: set[tuple[str, int]] = set()
        row_upload_ids: set[uuid.UUID] = set()
        for row in row_map.values():
            if not row or row.row_index is None:
                continue
            table = getattr(row, "table", None)
            upload_id = getattr(table, "upload_id", None) if table else None
            if not table or not upload_id:
                continue
            row_keys.add((str(table.id), int(row.row_index)))
            row_upload_ids.add(upload_id)
        if row_keys:
            table_ids = {table_id for table_id, _ in row_keys}
            row_indices = {row_index for _, row_index in row_keys}
            row_chunks = (
                KnowledgeUploadChunk.objects.filter(
                    business_profile=business_profile,
                    upload_id__in=row_upload_ids,
                    metadata__table_chunk_role="row",
                    metadata__table_id__in=list(table_ids),
                    metadata__table_row_index__in=list(row_indices),
                )
                .only("id", "chunk_index", "metadata", "upload_id")
            )
            for chunk in row_chunks:
                chunk_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
                table_id = str(chunk_meta.get("table_id") or "")
                row_index = chunk_meta.get("table_row_index")
                try:
                    row_index_int = int(row_index)
                except (TypeError, ValueError):
                    continue
                if not table_id:
                    continue
                key = (table_id, row_index_int)
                if key not in row_chunk_map:
                    row_chunk_map[key] = chunk
        snippets: list[KnowledgeSnippet] = []
        for row_id in row_priority:
            row = row_map.get(row_id)
            if not row:
                continue
            table = row.table
            upload = getattr(table, "upload", None)
            if (
                not table
                or not upload
                or upload.business_profile_id != business_profile.id
                or upload.visibility == KnowledgeVisibility.INTERNAL
            ):
                continue
            business_name = getattr(upload.business_profile, "name", None)
            cells = sorted(row.cells.all(), key=lambda c: c.column_index)
            structured: list[dict[str, str]] = []
            for cell in cells:
                column_label = cell.column_key or f"column_{cell.column_index + 1}"
                cell_value = cell.raw_text or ""
                structured.append(
                    {
                        "column": column_label,
                        "value": cell_value,
                    }
                )
            visible_structured = [entry for entry in structured if not _is_total_column(entry.get("column"))]
            if not visible_structured:
                visible_structured = structured
            summary_candidates = [
                (entry["column"], entry["value"])
                for entry in visible_structured
                if entry["value"]
            ]
            base_candidates = summary_candidates[:8]
            highlight_candidates = [
                item
                for item in summary_candidates[8:]
                if _is_numeric_value(item[1])
            ]
            display_parts: list[str] = []
            seen_parts: set[str] = set()
            for column, value in base_candidates + highlight_candidates:
                if not value:
                    continue
                text = f"{column}: {value}"
                if text in seen_parts:
                    continue
                seen_parts.add(text)
                display_parts.append(text)
            summary = "; ".join(display_parts) or (row.raw_text or "")
            content_parts = [f"{column}: {value}" for column, value in summary_candidates]
            content = "\n".join(content_parts) or (row.raw_text or summary)
            structured_table = {
                "title": table.title or table.section_heading or "Table",
                "columns": [entry["column"] for entry in visible_structured],
                "rows": [[entry["value"] for entry in visible_structured]],
                "metadata": {
                    "sheet_name": (table.metadata or {}).get("sheet_name"),
                    "row_index": row.row_index,
                    "table_order_index": table.order_index,
                },
            }
            ingestion_diag = ingestion_diag_cache.get(upload.id)
            if ingestion_diag is None:
                ingestion_diag = self._table_ingestion_diagnostics(upload)
                ingestion_diag_cache[upload.id] = ingestion_diag
            table_truncated = bool(ingestion_diag.get("table_truncated"))
            structured_table["metadata"]["table_truncated"] = table_truncated
            diag = dict(cell_diag.get(row_id, {}))
            diag.update(
                {
                    "table_id": str(table.id),
                    "row_index": row.row_index,
                    "table_truncated": table_truncated,
                    "table_total_rows": ingestion_diag.get("total_rows"),
                    "table_indexed_rows": ingestion_diag.get("indexed_rows"),
                    "table_row_cap": ingestion_diag.get("row_cap"),
                    "table_partial_tables": ingestion_diag.get("partial_tables"),
                    "truncated_rows": ingestion_diag.get("truncated_rows"),
                    "truncated_columns": ingestion_diag.get("truncated_columns"),
                    "truncated_tables": ingestion_diag.get("truncated_tables"),
                }
            )
            entity_hint = diag.get("value") or diag.get("column_key") or structured_table["title"]
            row_chunk = row_chunk_map.get((str(table.id), int(row.row_index)))
            snippet_id = row_chunk.id if row_chunk else row.id
            snippet_chunk_id = row_chunk.id if row_chunk else None
            snippet_chunk_index = row_chunk.chunk_index if row_chunk else None
            snippets.append(
                KnowledgeSnippet(
                    id=snippet_id,
                    title=structured_table["title"],
                    summary=summary or structured_table["title"],
                    source="table_direct",
                    content=content,
                    public_label=structured_table["title"],
                    structured_tables=(structured_table,),
                    issues=tuple(),
                    page_summaries=tuple(),
                    read_state=KNOWLEDGE_READ_STATE_SUMMARY,
                    topic_hints=tuple(),
                    is_pinned=False,
                    upload_id=table.upload_id,
                    chunk_id=snippet_chunk_id,
                    chunk_index=snippet_chunk_index,
                    entity_type=table.section_heading or "table_row",
                    entity_name=entity_hint,
                    entity_business=business_name,
                    is_table_chunk=True,
                    table_id=str(table.id),
                    page_number=row.page_number or (table.page.page_number if table.page else None),
                    aliases=tuple(),
                    search_stage="table_direct",
                    confidence_score=self._clamp_unit(self._safe_float(diag.get("similarity"), default=0.0)),
                    truncated=False,
                    source_diagnostics=diag,
                    partial_index=table_truncated,
                    structured_table_count=1,
                    issue_count=0,
                    structured_table_hint=diag.get("column_key"),
                )
            )
        return tuple(snippets)

    def _fallback_snippets(
        self,
        *,
        business_profile,
        limit: int,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        # Prefer top table rows as factual fallback; if none, fall back to recent uploads.
        table_rows_qs = KnowledgeUploadTableRow.objects.filter(
            table__upload__business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return tuple()
            table_rows_qs = table_rows_qs.filter(table__upload_id__in=allowed_upload_ids)
        else:
            scope_clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                scope_clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
            if scope_clauses:
                clause = scope_clauses[0]
                for extra in scope_clauses[1:]:
                    clause |= extra
                table_rows_qs = table_rows_qs.filter(clause)
        table_rows_qs = self._filter_queryable_table_uploads(
            table_rows_qs,
            format_lookup="table__upload__ingestion_metadata__format",
        )
        table_rows_qs = (
            table_rows_qs.order_by("-created_at")
            .select_related("table__upload__business_profile")
            .prefetch_related("cells")[: limit * 3]
        )
        table_rows = list(table_rows_qs)
        snippets: list[KnowledgeSnippet] = []
        for row in table_rows:
            table = row.table
            upload = getattr(table, "upload", None)
            if not upload or upload.visibility == KnowledgeVisibility.INTERNAL:
                continue
            cells = sorted(row.cells.all(), key=lambda c: c.column_index)
            structured = []
            for cell in cells:
                structured.append(
                    {"column": cell.column_key or f"column_{cell.column_index + 1}", "value": cell.raw_text}
                )
            summary_parts = [f"{entry['column']}: {entry['value']}" for entry in structured if entry["value"]]
            summary = "; ".join(summary_parts[:8]) or (row.raw_text or "")
            content = "\n".join(summary_parts) or summary
            structured_table = {
                "title": table.title or table.section_heading or "Table",
                "columns": [entry["column"] for entry in structured],
                "rows": [[entry["value"] for entry in structured]],
                "metadata": {
                    "sheet_name": (table.metadata or {}).get("sheet_name"),
                    "row_index": row.row_index,
                    "table_order_index": table.order_index,
                },
            }
            snippets.append(
                KnowledgeSnippet(
                    id=uuid.uuid4(),
                    title=structured_table["title"],
                    summary=summary or structured_table["title"],
                    source="table_fallback",
                    public_label=structured_table["title"],
                    structured_tables=(structured_table,),
                    issues=tuple(),
                    page_summaries=tuple(),
                    read_state=KNOWLEDGE_READ_STATE_SUMMARY,
                    topic_hints=tuple(),
                    is_pinned=False,
                    content=content,
                    content_mode="abstract",
                    entity_type=table.section_heading or "table_row",
                    entity_name=summary_parts[0] if summary_parts else structured_table["title"],
                    entity_business=getattr(upload.business_profile, "name", None),
                    is_table_chunk=True,
                    table_id=str(table.id),
                    aliases=tuple(),
                    search_stage="fallback",
                    confidence_score=0.0,
                    truncated=False,
                    source_diagnostics={
                        "reason": "fallback",
                        "table_id": str(table.id),
                        "row_index": row.row_index,
                    },
                    partial_index=False,
                    structured_table_count=1,
                    issue_count=0,
                    structured_table_hint=structured_table["title"],
                )
            )
            if len(snippets) >= limit:
                break
        if len(snippets) < limit:
            remaining = limit - len(snippets)
            uploads_qs = KnowledgeUpload.objects.filter(
                business_profile=business_profile,
                status=KnowledgeStatus.ACTIVE,
            )
            if allowed_upload_ids is not None:
                uploads_qs = uploads_qs.filter(id__in=allowed_upload_ids)
            else:
                clauses: list[Q] = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    uploads_qs = uploads_qs.filter(clause)
            uploads = apply_customer_visible_uploads(uploads_qs).order_by("-updated_at")[:remaining]
            for upload in uploads:
                trunc_metrics = self._truncation_metrics(upload)
                label = self._public_label(upload)
                structured = self._structured_exports(upload)
                table_count = len(structured["tables"])
                issue_count = len(structured["issues"])
                source_diag: dict[str, object] = {"reason": "fallback"}
                if trunc_metrics:
                    source_diag.update(trunc_metrics)
                snippets.append(
                    KnowledgeSnippet(
                        id=upload.id,
                        title=label,
                        summary=self._summarize_upload(upload),
                        source=upload.source_name or upload.source_type,
                        public_label=label,
                        structured_tables=structured["tables"],
                        issues=structured["issues"],
                        page_summaries=structured["pages"],
                        read_state=KNOWLEDGE_READ_STATE_SUMMARY,
                        topic_hints=self._topic_hints(upload),
                        is_pinned=self._is_pinned(upload),
                        content_mode="abstract",
                        entity_type=None,
                        entity_name=None,
                        entity_business=None,
                        is_table_chunk=False,
                        aliases=tuple(),
                        search_stage="fallback",
                        confidence_score=0.0,
                        truncated=False,
                        source_diagnostics=source_diag,
                        partial_index=bool(trunc_metrics.get("partial_index")) if trunc_metrics else False,
                        structured_table_count=table_count,
                        issue_count=issue_count,
                        structured_table_hint=None,
                    )
                )
        if not snippets:
            logger.warning("Knowledge load returned no snippets for business=%s", business_profile.id)
        return tuple(snippets)

    def _chunk_to_snippet(
        self,
        chunk: KnowledgeUploadChunk,
        *,
        result: ChunkResult | None = None,
        search_stage: str | None = None,
        content_mode: str = "abstract",
        table_row_sample: tuple[Mapping[str, object], ...] | None = None,
        query_text: str | None = None,
        query_tokens: Sequence[str] | None = None,
    ) -> KnowledgeSnippet:
        upload = chunk.upload
        label = self._public_label(upload)
        chunk_number = (chunk.chunk_index or 0) + 1 if chunk.chunk_index is not None else None
        title = f"{label} – chunk {chunk_number}" if chunk_number else label or "Document"
        summary = self._summarize_chunk(chunk)
        sample_text = ""
        evidence_span_text = ""
        
        # Only use table_row_sample for actual table chunks
        chunk_metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        is_table_chunk_flag = bool(chunk_metadata.get("is_table_chunk"))
        
        if table_row_sample and is_table_chunk_flag:
            pairs: list[str] = []
            # Preferred shape: a structured table preview (columns + rows).
            first = table_row_sample[0] if table_row_sample else None
            if isinstance(first, Mapping) and isinstance(first.get("columns"), list) and isinstance(first.get("rows"), list):
                cols = [str(c) for c in (first.get("columns") or [])]
                rows = first.get("rows") or []
                first_row = rows[0] if rows else None
                if isinstance(first_row, list):
                    for col, val in zip(cols, first_row):
                        value = str(val or "").strip()
                        label = str(col or "").strip()
                        if not value or not label:
                            continue
                        pairs.append(f"{label}: {value}")
                        if len(pairs) >= 8:
                            break
            else:
                # Backward-compatible shape: list of {row, column, value} entries.
                for entry in table_row_sample:
                    if not isinstance(entry, Mapping):
                        continue
                    col = entry.get("column") or ""
                    val = entry.get("value") or ""
                    combined = f"{col}: {val}".strip(": ")
                    if combined:
                        pairs.append(combined)
                        if len(pairs) >= 8:
                            break
            if pairs:
                sample_text = "; ".join(pairs)[:500]
        
        # For table chunks, keep a single canonical preview source.
        if sample_text and is_table_chunk_flag:
            summary = sample_text[:500]

        # For text chunks, promote a query-matching span as summary so literal
        # matches (for example exact service names) are visible in previews.
        if (not is_table_chunk_flag) and (query_text or query_tokens):
            evidence_span_text = self._best_text_evidence_span(
                chunk.content or "",
                query_text=query_text or "",
                tokens=tuple(query_tokens or ()),
                max_chars=280,
            )
            if evidence_span_text:
                summary = evidence_span_text
        
        truncated = False
        # For table chunks, always use full chunk content so the LLM sees actual cell values
        # instead of abstract summaries like "Table 1: columns...". This ensures comprehensive
        # enumeration queries get complete data in a single search.
        if content_mode == "abstract" and not is_table_chunk_flag:
            content = summary
            read_state = KNOWLEDGE_READ_STATE_SUMMARY
        elif content_mode == "abstract" and is_table_chunk_flag:
            # Table chunks: use full content from ingestion (which includes cell values)
            content, truncated = self._trim_with_flag(chunk.content, max_chars=self.search_preview_char_limit)
            read_state = KNOWLEDGE_READ_STATE_FULL if content and len(content) >= self.table_ready_threshold else KNOWLEDGE_READ_STATE_PREVIEW
        else:
            content, truncated = self._trim_with_flag(chunk.content, max_chars=self.search_preview_char_limit)
            read_state = KNOWLEDGE_READ_STATE_PREVIEW if truncated else self._read_state_for_content(content, chunk_metadata)

        entity_type = chunk_metadata.get("entity_type")
        entity_name = chunk_metadata.get("entity_name")
        entity_business = chunk_metadata.get("entity_business")
        is_table_chunk = bool(chunk_metadata.get("is_table_chunk"))
        evidence_group_id = str(chunk_metadata.get("evidence_group_id") or "").strip() or None
        evidence_type = str(chunk_metadata.get("evidence_type") or "").strip() or None
        representation = str(chunk_metadata.get("representation") or "").strip().lower() or None
        aliases = tuple(chunk_metadata.get("aliases") or ())
        table_count, issue_count = self._structured_counts(upload)
        structured_preview: tuple[Mapping[str, object], ...] = tuple(table_row_sample or ())
        table_hint = None
        if table_count and not structured_preview:
            label_name = "tables" if table_count != 1 else "table"
            table_hint = f"{table_count} structured {label_name} available via load_document"

        source_stage = search_stage or (result.source_stage if result else None)
        confidence = self._public_confidence_score(result)
        diagnostics = dict(result.diagnostics) if result else {}
        if result and result.vector_distance is not None:
            diagnostics.setdefault("vector_distance", result.vector_distance)
        if is_table_chunk:
            # Carry table provenance + quality signals so the model can ground answers correctly.
            for key in (
                "table_id",
                "table_order_index",
                "table_row_index",
                "table_page_number",
                "table_title",
                "table_quality_score",
                "table_is_decorative",
                "table_row_contract_version",
                "table_row_observed_value_columns",
                "table_row_qualifier_columns",
                "table_row_scope_dimension_columns",
                "table_row_inferred_scope_columns",
                "table_row_scope_reason",
                "table_row_scope_confidence",
                "table_row_fee_value",
                "table_row_evidence_cell_ids",
            ):
                if key in diagnostics:
                    continue
                value = chunk_metadata.get(key)
                if value is None or value == "":
                    continue
                diagnostics[key] = value
            ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
            format_hint = str(ingestion_meta.get("format") or "").strip().lower()
            if format_hint in {"pdf", "docx"}:
                diagnostics["table_read_only"] = True
        diagnostics["structured_table_count"] = table_count
        diagnostics["issue_count"] = issue_count
        trunc_metrics = self._truncation_metrics(upload)
        if trunc_metrics:
            diagnostics.update(trunc_metrics)
        if evidence_span_text:
            diagnostics["evidence_span"] = True
            diagnostics["evidence_span_chars"] = len(evidence_span_text)
        partial_flag = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False

        return KnowledgeSnippet(
            id=chunk.id,
            title=title,
            summary=summary,
            source=upload.get_source_type_display(),
            content=content,
            content_mode=content_mode,
            public_label=label,
            structured_tables=structured_preview,
            issues=tuple(),
            page_summaries=tuple(),
            read_state=read_state,
            topic_hints=tuple(),
            is_pinned=False,
            upload_id=upload.id,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            entity_type=entity_type,
            entity_name=entity_name,
            entity_business=entity_business,
            is_table_chunk=is_table_chunk,
            table_id=str(chunk_metadata.get("table_id") or "") or None,
            evidence_group_id=evidence_group_id,
            evidence_type=evidence_type,
            representation=representation,
            aliases=aliases,
            search_stage=source_stage,
            confidence_score=confidence,
            truncated=truncated,
            source_diagnostics=diagnostics,
            partial_index=partial_flag,
            structured_table_count=table_count,
            issue_count=issue_count,
            structured_table_hint=table_hint,
        )

    @staticmethod
    def _duration_ms(start: float | None) -> int:
        if start is None:
            return 0
        elapsed = (time.perf_counter() - start) * 1000
        return int(max(0.0, elapsed))

    def _rrf_fusion_snippets(
        self,
        *,
        vector_snippets: Sequence[KnowledgeSnippet],
        table_snippets: Sequence[KnowledgeSnippet],
        k: int = 60,
    ) -> list[KnowledgeSnippet]:
        """
        Merge vector and table search results using Reciprocal Rank Fusion (RRF).

        RRF formula: score(d) = Σ 1/(k + rank(d))

        This allows exact-match table results to compete fairly with semantic
        vector results, fixing cases where vector search confidently returns
        wrong results (e.g., "Withdraw Bills" matching "ATM withdrawal").

        Args:
            vector_snippets: Results from vector/hybrid search
            table_snippets: Results from table-direct search
            k: RRF constant (higher = more weight to lower-ranked items)

        Returns:
            Merged and sorted list of snippets
        """
        # Track RRF scores by snippet ID
        rrf_scores: dict[str, float] = {}
        snippet_map: dict[str, KnowledgeSnippet] = {}

        # Score vector results
        for rank, snippet in enumerate(vector_snippets, start=1):
            snippet_id = str(snippet.id)
            rrf_scores[snippet_id] = rrf_scores.get(snippet_id, 0.0) + 1.0 / (k + rank)
            if snippet_id not in snippet_map:
                snippet_map[snippet_id] = snippet

        # Score table results (table results get a small boost for exact matching)
        # The boost is implicit: table results that also appear in vector results
        # get scores from both, naturally rising to the top
        for rank, snippet in enumerate(table_snippets, start=1):
            snippet_id = str(snippet.id)
            rrf_scores[snippet_id] = rrf_scores.get(snippet_id, 0.0) + 1.0 / (k + rank)
            if snippet_id not in snippet_map:
                snippet_map[snippet_id] = snippet

        # Sort by RRF score descending
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        # Build merged result list
        merged: list[KnowledgeSnippet] = []
        for snippet_id in sorted_ids:
            snippet = snippet_map[snippet_id]
            merged.append(snippet)

        _rag_log(
            "parallel_table.rrf_fusion",
            {
                "vector_count": len(vector_snippets),
                "table_count": len(table_snippets),
                "merged_count": len(merged),
                "top_5_scores": [
                    {"id": sid[:8], "score": round(rrf_scores[sid], 4)}
                    for sid in sorted_ids[:5]
                ],
            },
            indent=2,
        )

        return merged

    def _record_retrieval_event(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        alias_result: AliasSearchResult,
        result: KnowledgeSearchResult,
        feature_state: FeatureState | None = None,
    ) -> None:
        try:
            QualityMonitor.record_retrieval_sample(
                business_profile=business_profile,
                query_type="identifier" if traits.is_identifier_like else "natural",
                alias_hit=bool(alias_result.short_circuit and alias_result.hits),
                fallback_used=result.status == "not_found" or result.diagnostics.get("path") == "fallback",
                latency_ms=result.diagnostics.get("total_duration_ms"),
                stage=result.diagnostics.get("path"),
                feature_flags=feature_state.as_dict() if feature_state else None,
                diagnostics=result.diagnostics,
                result_status=result.status,
            )
        except Exception as exc:  # pragma: no cover - monitoring must not block retrieval
            logger.warning(
                "quality.retrieval.monitor_failed business=%s error=%s",
                business_profile.id if business_profile else None,
                exc,
            )

    def _log_search_summary(
        self,
        *,
        business_profile,
        request_id: uuid.UUID,
        result: KnowledgeSearchResult,
    ) -> None:
        diagnostics = dict(result.diagnostics or {})
        query_preview = (diagnostics.get("normalized_query") or diagnostics.get("original_query") or "").replace(
            "\n",
            " ",
        )
        if len(query_preview) > 200:
            query_preview = f"{query_preview[:200]}..."
        top_score = None
        top_diag: Mapping[str, object] = {}
        top_breakdown: Mapping[str, object] = {}
        if result.snippets:
            top_score = result.snippets[0].confidence_score
            top_diag = result.snippets[0].source_diagnostics or {}
            maybe_breakdown = top_diag.get("score_breakdown") if isinstance(top_diag, Mapping) else None
            if isinstance(maybe_breakdown, Mapping):
                top_breakdown = maybe_breakdown
        _rag_log(
            "search.summary",
            {
                "stage": diagnostics.get("path") or "unknown",
                "status": result.status,
                "snippets": diagnostics.get("snippet_count") or len(result.snippets),
                "reason": diagnostics.get("reason"),
                "features": diagnostics.get("feature_flags"),
                "tokens": diagnostics.get("token_count"),
                "total_ms": diagnostics.get("total_duration_ms"),
                "alias_ms": diagnostics.get("alias_duration_ms"),
                "vector_ms": diagnostics.get("vector_duration_ms"),
                "lexical_ms": diagnostics.get("fts_duration_ms"),
                "rerank_ms": diagnostics.get("rerank_duration_ms"),
                "table_ms": diagnostics.get("table_duration_ms"),
                "table_context_ms": diagnostics.get("table_context_ms"),
                "table_presence_ms": diagnostics.get("table_presence_ms"),
                "identifier": diagnostics.get("identifier_like"),
                "alias_stage": diagnostics.get("alias_stage"),
                "chunk_candidates": diagnostics.get("chunk_candidate_count"),
                "tabular_intent": diagnostics.get("tabular_intent"),
                "tables_available": diagnostics.get("tables_available"),
                "table_reason": diagnostics.get("table_reason"),
                "vector_ceiling": diagnostics.get("vector_distance_ceiling"),
                "vector_distance_min": diagnostics.get("vector_distance_min"),
                "vector_distance_mean": diagnostics.get("vector_distance_mean"),
                "vector_distance_max": diagnostics.get("vector_distance_max"),
                "vector_similarity_min": diagnostics.get("vector_similarity_min"),
                "vector_similarity_mean": diagnostics.get("vector_similarity_mean"),
                "vector_similarity_max": diagnostics.get("vector_similarity_max"),
                "top_score": top_score,
                "top_vector": top_breakdown.get("vector"),
                "top_lexical": top_breakdown.get("lexical"),
                "top_alias": top_breakdown.get("alias"),
                "top_entity": top_breakdown.get("entity"),
                "top_recency": top_breakdown.get("recency"),
                "mmr_lambda": self.mmr_lambda,
                "w_vector": self.rerank_weights.get("vector"),
                "w_lexical": self.rerank_weights.get("lexical"),
                "w_alias": self.rerank_weights.get("alias"),
                "w_entity": self.rerank_weights.get("entity"),
                "w_recency": self.rerank_weights.get("recency"),
                "alias_threshold": diagnostics.get("alias_fts_threshold"),
                "query": query_preview,
            },
            indent=1,
            context={
                "business": business_profile.id,
                "request": request_id,
            },
        )
        feature_flags = diagnostics.get("feature_flags") if isinstance(diagnostics.get("feature_flags"), dict) else {}
        if feature_flags.get("rag_eval_logging"):
            self._log_snippet_previews(
                business_profile=business_profile,
                request_id=request_id,
                result=result,
            )
        if feature_flags.get("rag_shadow_retrieval"):
            self._log_shadow_vector_snapshot(
                business_profile=business_profile,
                request_id=request_id,
                query_text=diagnostics.get("normalized_query") or diagnostics.get("original_query") or "",
            )

    def _log_snippet_previews(
        self,
        *,
        business_profile,
        request_id: uuid.UUID,
        result: KnowledgeSearchResult,
        limit: int = 3,
    ) -> None:
        snippets = result.snippets or ()
        if not snippets:
            return
        preview_limit = max(1, min(limit, len(snippets)))
        previews: list[dict[str, object]] = []
        for snippet in snippets[:preview_limit]:
            content = (snippet.content or "").strip()
            summary = (snippet.summary or "").strip()
            preview = content or summary
            if len(preview) > 240:
                preview = f"{preview[:240]}..."
            if len(summary) > 160:
                summary = f"{summary[:160]}..."
            previews.append(
                {
                    "chunk_id": str(snippet.chunk_id) if snippet.chunk_id else None,
                    "upload_id": str(snippet.upload_id) if snippet.upload_id else None,
                    "title": snippet.title,
                    "summary": summary,
                    "preview": preview,
                    "search_stage": snippet.search_stage,
                    "is_table_chunk": bool(snippet.is_table_chunk),
                }
            )
        _rag_log(
            "search.snippets",
            {"count": preview_limit, "items": previews},
            indent=2,
            context={
                "business": business_profile.id,
                "request": request_id,
            },
        )

    def _log_shadow_vector_snapshot(
        self,
        *,
        business_profile,
        request_id: uuid.UUID,
        query_text: str,
        limit: int = 3,
    ) -> None:
        query_text = (query_text or "").strip()
        if not query_text:
            return
        query_vector, diagnostics = self._build_query_vector(
            business_profile=business_profile,
            query_text=query_text,
        )
        if not query_vector:
            return
        qs = (
            KnowledgeUploadShadowChunk.objects.filter(
                business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
            .exclude(embedding__isnull=True)
            .annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance")[: max(1, limit)]
        )
        hits: list[dict[str, object]] = []
        for chunk in qs:
            preview = (chunk.content or "").strip()
            if len(preview) > 200:
                preview = f"{preview[:200]}..."
            hits.append(
                {
                    "chunk_id": str(chunk.id),
                    "upload_id": str(chunk.upload_id),
                    "distance": float(getattr(chunk, "distance", 0.0) or 0.0),
                    "preview": preview,
                }
            )
        _rag_log(
            "shadow.vector",
            {
                "count": len(hits),
                "query": query_text[:160] + "..." if len(query_text) > 160 else query_text,
                "vector_cache_hit": diagnostics.get("vector_cache_hit"),
                "hits": hits,
            },
            indent=2,
            context={
                "business": business_profile.id,
                "request": request_id,
            },
        )

    def load_contents(
        self,
        *,
        business_profile,
        knowledge_ids: Sequence[str],
        max_chars: int | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        normalized: list[uuid.UUID] = []
        for value in knowledge_ids:
            try:
                normalized.append(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
        if not normalized:
            return tuple()
        table_prefetch = Prefetch(
            "tables",
            queryset=KnowledgeUploadTable.objects.order_by("order_index").select_related("page"),
        )
        issue_prefetch = Prefetch(
            "issues",
            queryset=KnowledgeUploadIssue.objects.order_by("-created_at").select_related("page", "table", "table_row", "table_cell"),
        )
        qs = (
            apply_customer_visible_uploads(
                KnowledgeUpload.objects.filter(
                    business_profile=business_profile,
                    status=KnowledgeStatus.ACTIVE,
                    id__in=normalized,
                )
            )
            .select_related("text_detail")
            .prefetch_related(table_prefetch, issue_prefetch)
            .order_by("-updated_at")
        )
        snippets: list[KnowledgeSnippet] = []
        limit = self.inline_char_limit_for_business(business_profile, max_chars)
        for upload in qs:
            trunc_metrics = self._truncation_metrics(upload)
            label = self._public_label(upload)
            raw_content = self._extract_content(upload)
            if raw_content:
                content, truncated = self._trim_with_flag(raw_content, max_chars=limit)
            else:
                content, truncated = ("", False)
            structured = self._structured_exports(upload)
            enriched_tables = self._serialize_structured_tables_with_rows(upload, max_tables=3, max_rows=5)
            table_count = len(enriched_tables) or len(structured["tables"])
            issue_count = len(structured["issues"])
            supplemental_sections: list[dict[str, object]] = []
            if table_count:
                supplemental_sections.append(
                    {
                        "type": "table_preview",
                        "label": f"{table_count} structured table{'s' if table_count != 1 else ''}",
                        "reference": "structured_tables",
                        "item_count": table_count,
                    }
                )
            if issue_count:
                supplemental_sections.append(
                    {
                        "type": "ingestion_issues",
                        "label": f"{issue_count} ingestion issue{'s' if issue_count != 1 else ''}",
                        "reference": "issues",
                        "item_count": issue_count,
                    }
                )
            doc_read_state = KNOWLEDGE_READ_STATE_PREVIEW if truncated else self._read_state_for_content(content, {})
            source_diag: dict[str, object] = {"load": "document", "inline_char_limit": limit}
            if trunc_metrics:
                source_diag.update(trunc_metrics)
            if truncated:
                source_diag["partial_content"] = True
            partial_flag = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False
            snippets.append(
                KnowledgeSnippet(
                    id=upload.id,
                    title=label,
                    summary=self._summarize_upload(upload),
                    source=upload.source_name or upload.source_type,
                    content=content,
                    content_mode="full_document",
                    public_label=label,
                    structured_tables=enriched_tables or structured["tables"],
                    issues=structured["issues"],
                    page_summaries=structured["pages"],
                    read_state=doc_read_state,
                    topic_hints=self._topic_hints(upload),
                    is_pinned=self._is_pinned(upload),
                    supplemental_sections=tuple(supplemental_sections),
                    entity_type=None,
                    entity_name=None,
                    entity_business=None,
                    is_table_chunk=False,
                    aliases=tuple(),
                    search_stage="load_document",
                    confidence_score=1.0,
                    truncated=truncated,
                    source_diagnostics=source_diag,
                    partial_index=partial_flag,
                    structured_table_count=table_count,
                    issue_count=issue_count,
                    structured_table_hint=None,
                )
            )
        return tuple(snippets)

    # ADD this method inside KnowledgeSearchService

    def _resolve_upload_page_chunk(
        self,
        *,
        business_profile,
        upload_id: uuid.UUID,
        page_index: int,
    ) -> KnowledgeUploadChunk | None:
        """
        Locate a chunk for the requested page index (1-based) within an upload.
        Falls back to the closest available chunk when the requested index is out
        of range.
        """

        target_index = max(0, page_index - 1)
        base_qs = apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                upload__business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
                upload_id=upload_id,
            ).select_related("upload")
        )

        try:
            chunk = base_qs.get(chunk_index=target_index)
            return chunk
        except KnowledgeUploadChunk.DoesNotExist:
            pass

        chunk = base_qs.filter(chunk_index__gte=target_index).order_by("chunk_index").first()
        if chunk:
            return chunk
        return base_qs.order_by("-chunk_index").first()

    def _get_page_text_from_blocks(
        self,
        upload: KnowledgeUpload,
        page_number: int,
        max_chars: int | None = None,
    ) -> tuple[str, bool]:
        """
        Extract actual page text from KnowledgeUploadPageBlock entries.
        Returns (text, truncated_flag).

        This is the CORRECT way to get page content, not via chunk indices.
        """
        try:
            from apps.knowledge.models import KnowledgeUploadPageBlock

            blocks = list(
                KnowledgeUploadPageBlock.objects.filter(
                    upload=upload,
                    page__page_number=page_number
                )
                .select_related("page")
                .order_by("order_index")
            )

            if not blocks:
                return ("", False)

            # Combine block texts in order
            page_parts: list[str] = []
            for block in blocks:
                if block.text:
                    page_parts.append(block.text)

            combined = "\n\n".join(page_parts).strip()

            if max_chars and len(combined) > max_chars:
                return (combined[:max_chars], True)

            return (combined, False)

        except Exception:
            # Fallback if blocks aren't available
            return ("", False)

    def _get_structured_table_content_for_page(
        self,
        upload: KnowledgeUpload,
        page_number: int,
        max_chars: int | None = None,
    ) -> tuple[str, bool, bool]:
        """
        Get structured table content for a page from table row chunks.

        Returns (content, truncated_flag, has_tables).

        This returns correctly structured key-value pairs from extracted tables,
        avoiding the column misalignment issues present in raw PageBlock text.
        """
        try:
            from apps.knowledge.models import (
                KnowledgeUploadTable,
                KnowledgeUploadChunk,
            )

            # Check if page has tables
            table_ids = list(
                KnowledgeUploadTable.objects.filter(
                    upload=upload,
                    page__page_number=page_number
                )
                .order_by("order_index")
                .values_list("id", flat=True)
            )

            if not table_ids:
                return ("", False, False)

            # Get table row chunks for these tables, ordered by table then row
            row_chunks = list(
                KnowledgeUploadChunk.objects.filter(
                    upload=upload,
                    metadata__table_id__in=[str(tid) for tid in table_ids],
                    metadata__table_chunk_role="row",
                )
                .order_by("chunk_index")
            )

            if not row_chunks:
                return ("", False, False)

            # Build structured content from row chunks
            content_parts: list[str] = []
            current_table_id: str | None = None

            for chunk in row_chunks:
                chunk_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
                table_id = chunk_meta.get("table_id")

                # Add separator between tables
                if table_id != current_table_id and current_table_id is not None:
                    content_parts.append("\n---\n")
                current_table_id = table_id

                if chunk.content:
                    content_parts.append(chunk.content.strip())

            combined = "\n\n".join(content_parts).strip()
            truncated = False

            if max_chars and len(combined) > max_chars:
                combined = combined[:max_chars]
                truncated = True

            return (combined, truncated, True)

        except Exception as exc:
            logger.warning("Failed to get structured table content: %s", exc)
            return ("", False, False)

    def load_page_window(
        self,
        *,
        business_profile,
        upload_id: uuid.UUID | None = None,
        chunk_id: uuid.UUID | None = None,
        page_index: int = 1,
        neighbor: int = 1,
        mode: str = "excerpt",
        token_budget: int | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        """
        Fetch a single chunk "page" with a tighter char budget so the LLM can
        request additional windows via repeated tool calls.
        
        NOW FIXED: Prioritizes actual page text from PageBlocks instead of
        mapping page_index to chunk_index.
        """

        inline_cap = self.inline_char_limit_for_business(business_profile)
        page_cap = self.page_char_limit_for_business(business_profile)
        normalized_mode = (mode or "excerpt").strip().lower()
        effective_limit = inline_cap if normalized_mode == "full_page" else min(page_cap, inline_cap)
        if token_budget is not None:
            try:
                approx_chars = max(200, int(token_budget) * 4)
                effective_limit = max(200, min(effective_limit, approx_chars))
            except (TypeError, ValueError):
                pass
        effective_neighbor = self._neighbor_window_for_business(business_profile, neighbor)

        # NEW LOGIC: Try to get page from blocks first if we have upload_id and page_index
        page_from_blocks = False
        upload_obj = None
        page_text = ""
        page_truncated = False
        page_source = "none"  # Track source: "structured_tables", "page_blocks", or "none"
        resolved_upload_id = upload_id
        
        # NEW: Resolve chunk_id to upload_id for PageBlocks access (Codex gap fix)
        if chunk_id is not None and upload_id is None:
            try:
                from apps.knowledge.models import KnowledgeUploadChunk
                chunk = KnowledgeUploadChunk.objects.filter(
                    id=chunk_id,
                    upload__business_profile=business_profile,
                    upload__status=KnowledgeStatus.ACTIVE
                ).select_related("upload").only("id", "upload_id", "metadata").first()
                
                if chunk:
                    resolved_upload_id = chunk.upload_id
                    # Try to get page number from chunk metadata (table_page_number or chunk_page)
                    chunk_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
                    if page_index == 1:  # Only override if caller passed default page=1
                        meta_page = chunk_meta.get("table_page_number") or chunk_meta.get("chunk_page") or chunk_meta.get("page_number")
                        if meta_page:
                            try:
                                parsed_page = int(meta_page)
                                if parsed_page >= 1:  # Clamp to valid page numbers
                                    page_index = parsed_page
                            except (TypeError, ValueError):
                                pass
            except Exception:
                pass
        
        if resolved_upload_id is not None:
            # User is requesting a specific page by number - try blocks first
            try:
                upload_obj = (
                    apply_customer_visible_uploads(
                        KnowledgeUpload.objects.filter(
                            id=resolved_upload_id,
                            business_profile=business_profile,
                            status=KnowledgeStatus.ACTIVE
                        )
                    )
                    .only("id", "ingestion_metadata", "display_name", "source_type", "summary")
                    .first()
                )
                
                if upload_obj:
                    # PRIORITY: Try structured table content first for pages with tables
                    # This avoids column misalignment issues in raw PageBlock text
                    table_content, table_truncated, has_tables = self._get_structured_table_content_for_page(
                        upload_obj,
                        page_index,
                        max_chars=effective_limit
                    )

                    if has_tables and table_content:
                        # Use structured table content - correctly formatted key-value pairs
                        page_text = table_content
                        page_truncated = table_truncated
                        page_from_blocks = True
                        page_source = "structured_tables"
                    else:
                        # Fallback to raw PageBlock text for pages without tables
                        page_text, page_truncated = self._get_page_text_from_blocks(
                            upload_obj,
                            page_index,
                            max_chars=effective_limit
                        )
                        if page_text:
                            page_from_blocks = True
                            page_source = "page_blocks"
                        else:
                            page_source = "none"
            except Exception:
                # Fall through to chunk-based approach
                pass

        # If we successfully got page from blocks, return it directly
        if page_from_blocks and upload_obj:
            synopsis = self._page_synopsis_text(upload_obj, page_index, "")
            label = getattr(upload_obj, "display_name", None) or "Document"

            # Use synopsis for excerpt mode, full text for full_page mode
            if normalized_mode != "full_page":
                content_value = synopsis or page_text[:500]
                read_state = KNOWLEDGE_READ_STATE_SUMMARY
                truncated_flag = False
            else:
                content_value = page_text
                read_state = KNOWLEDGE_READ_STATE_PREVIEW if page_truncated else KNOWLEDGE_READ_STATE_FULL
                truncated_flag = page_truncated

            diagnostics = {
                "page_request": True,
                "page_number": page_index,
                "page_mode": normalized_mode,
                "page_char_limit": effective_limit,
                "page_source": page_source,  # Track whether we used structured_tables or page_blocks
            }

            return tuple([
                KnowledgeSnippet(
                    id=upload_obj.id,
                    title=f"{label} – page {page_index}",
                    summary=synopsis or (page_text[:280] if page_text else ""),
                    source=upload_obj.get_source_type_display(),
                    content=content_value,
                    content_mode="full_page" if normalized_mode == "full_page" else "excerpt",
                    public_label=label,
                    structured_tables=tuple(),
                    issues=tuple(),
                    page_summaries=tuple(),
                    read_state=read_state,
                    topic_hints=tuple(),
                    is_pinned=False,
                    upload_id=upload_obj.id,
                    chunk_id=None,
                    chunk_index=None,
                    page_number=page_index,
                    page_mode=normalized_mode,
                    entity_type=None,
                    entity_name=None,
                    entity_business=None,
                    is_table_chunk=page_source == "structured_tables",  # Mark as table content
                    aliases=tuple(),
                    search_stage="load_page",
                    confidence_score=1.0,
                    truncated=truncated_flag,
                    source_diagnostics=diagnostics,
                    partial_index=False,
                    structured_table_count=0,
                    issue_count=0,
                    structured_table_hint=None,
                )
            ])

        # FALLBACK: Use old chunk-based approach for backwards compatibility
        target_chunk_id = chunk_id
        fallback_upload_id: uuid.UUID | None = None
        if target_chunk_id is None and upload_id is not None:
            chunk = self._resolve_upload_page_chunk(
                business_profile=business_profile,
                upload_id=upload_id,
                page_index=page_index,
            )
            if chunk:
                target_chunk_id = chunk.id
            else:
                fallback_upload_id = upload_id

        if target_chunk_id:
            snippets = self.load_chunk_contents(
                business_profile=business_profile,
                chunk_ids=[str(target_chunk_id)],
                neighbor=effective_neighbor,
                max_chars=effective_limit,
            )
        elif fallback_upload_id:
            snippets = self.load_contents(
                business_profile=business_profile,
                knowledge_ids=[str(fallback_upload_id)],
                max_chars=effective_limit,
            )
        else:
            return tuple()

        annotated: list[KnowledgeSnippet] = []
        upload_lookup: dict[uuid.UUID, KnowledgeUpload] = {}
        if normalized_mode != "full_page":
            upload_ids = {snippet.upload_id for snippet in snippets if snippet.upload_id}
            if upload_ids:
                upload_lookup = {
                    obj.id: obj
                    for obj in apply_customer_visible_uploads(
                        KnowledgeUpload.objects.filter(id__in=upload_ids)
                    ).only("id", "ingestion_metadata")
                }
        for snippet in snippets:
            actual_page = page_index
            if snippet.chunk_index is not None:
                actual_page = max(1, int(snippet.chunk_index) + 1)
            diagnostics = dict(snippet.source_diagnostics or {})
            diagnostics.update(
                {
                    "page_request": True,
                    "page_number": actual_page,
                    "page_mode": normalized_mode,
                    "page_char_limit": effective_limit,
                    "neighbor_window": effective_neighbor,
                    "page_source": "chunk_fallback",  # NEW diagnostic
                }
            )
            content_value = snippet.content
            read_state = snippet.read_state
            truncated_flag = snippet.truncated
            content_mode_value = "full_page" if normalized_mode == "full_page" else "excerpt"
            if normalized_mode != "full_page":
                upload_obj = upload_lookup.get(snippet.upload_id) if snippet.upload_id else None
                synopsis = self._page_synopsis_text(upload_obj, actual_page, snippet.summary)
                content_value = synopsis or snippet.summary
                read_state = KNOWLEDGE_READ_STATE_SUMMARY
                truncated_flag = False
            else:
                read_state = KNOWLEDGE_READ_STATE_PREVIEW if snippet.truncated else KNOWLEDGE_READ_STATE_FULL
            annotated.append(
                dataclasses.replace(
                    snippet,
                    content=content_value,
                    content_mode=content_mode_value,
                    read_state=read_state,
                    truncated=truncated_flag,
                    source_diagnostics=diagnostics,
                    page_number=actual_page,
                    page_mode=normalized_mode,
                )
            )
        return tuple(annotated)

    def load_chunk_contents(
        self,
        *,
        business_profile,
        chunk_ids: Sequence[str],
        neighbor: int = 1,
        max_chars: int | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        """
        Fetch exact chunk(s) and stitch +/- neighbor chunks from the same upload
        for minimal, focused context delivery.
        """
        normalized: list[uuid.UUID] = []
        for value in (chunk_ids or ()):
            try:
                normalized.append(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
        if not normalized:
            return tuple()

        # Pull requested chunks with their uploads
        chunks: list[KnowledgeUploadChunk] = list(
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    id__in=normalized,
                    business_profile=business_profile,
                    upload__status=KnowledgeStatus.ACTIVE,
                )
            )
            .select_related("upload")
            .order_by("upload_id", "chunk_index")
        )
        if not chunks:
            return tuple()

        # Group by upload for neighbor stitching
        by_upload: dict[uuid.UUID, list[KnowledgeUploadChunk]] = {}
        for ch in chunks:
            by_upload.setdefault(ch.upload_id, []).append(ch)

        # For each upload, fetch neighbors for the selected indices
        stitched_snippets: list[KnowledgeSnippet] = []
        limit = self.inline_char_limit_for_business(business_profile, max_chars)
        configured_neighbor = self._neighbor_window_for_business(business_profile, neighbor)

        entity_chunk_cache: dict[tuple[uuid.UUID, str], list[KnowledgeUploadChunk]] = {}
        structured_cache: dict[uuid.UUID, tuple[Mapping[str, object], ...]] = {}
        issue_cache: dict[uuid.UUID, tuple[Mapping[str, object], ...]] = {}

        for upload_id, requested in by_upload.items():
            upload = requested[0].upload  # same upload
            trunc_metrics = self._truncation_metrics(upload)
            # Determine all chunk indices we need
            request_entries: list[tuple[KnowledgeUploadChunk, int]] = []
            for ch in requested:
                if ch.chunk_index is None:
                    continue
                effective_neighbor = self._effective_neighbor_window(ch, configured_neighbor)
                request_entries.append((ch, effective_neighbor))
            if not request_entries:
                continue

            min_idx = min(max(0, ch.chunk_index - span) for ch, span in request_entries if ch.chunk_index is not None)
            max_idx = max((ch.chunk_index or 0) + span for ch, span in request_entries)
            cached_window = self._window_cache_get(upload.business_profile_id, upload.id, min_idx, max_idx)
            if cached_window is not None:
                window_chunks = cached_window
            else:
                window_chunks = list(
                    KnowledgeUploadChunk.objects.filter(
                        upload=upload,
                        chunk_index__gte=min_idx,
                        chunk_index__lte=max_idx,
                    ).order_by("chunk_index")
                )
                self._window_cache_set(upload.business_profile_id, upload.id, min_idx, max_idx, window_chunks)
            by_index = {ch.chunk_index: ch for ch in window_chunks}
            if upload.id not in structured_cache:
                structured_cache[upload.id] = tuple(self._serialize_structured_tables_with_rows(upload, max_tables=3, max_rows=5))
                issue_cache[upload.id] = self._ingestion_issue_summaries(upload)

            # Build one stitched snippet per requested chunk (not per merged range),
            # so each request id returns a focused snippet keyed by that chunk id
            for req, span in request_entries:
                idx = req.chunk_index
                start = max(0, idx - span)
                end = idx + span
                parts: list[str] = []
                for i in range(start, end + 1):
                    ch = by_index.get(i)
                    if ch and ch.content:
                        parts.append(ch.content)
                extra_parts: list[str] = []
                chunk_metadata = req.metadata if isinstance(req.metadata, dict) else {}
                entity_name = chunk_metadata.get("entity_name")
                if entity_name:
                    entity_chunks = self._fetch_entity_chunks(upload, entity_name, cache=entity_chunk_cache)
                    for extra in entity_chunks:
                        if extra.chunk_index == idx:
                            continue
                        if extra.content:
                            extra_parts.append(extra.content)
                        if len(extra_parts) >= 2:
                            break
                combined_sections = parts + extra_parts
                combined = "\n\n".join(section for section in combined_sections if section).strip()
                if combined:
                    trimmed, truncated = self._trim_with_flag(combined, max_chars=limit)
                else:
                    trimmed, truncated = ("", False)

                label = getattr(upload, "display_name", None) or "Document"
                summary = (trimmed.splitlines()[0] if trimmed else req.content or "No summary available.").strip()
                chunk_metadata = req.metadata if isinstance(req.metadata, dict) else {}
                read_state = KNOWLEDGE_READ_STATE_PREVIEW if truncated else self._read_state_for_content(trimmed, chunk_metadata)
                structured_tables = structured_cache.get(upload.id) or tuple()
                issues = issue_cache.get(upload.id) or tuple()
                partial_flag = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False
                source_diag: dict[str, object] = {"neighbor_window": span, "inline_char_limit": limit}
                if trunc_metrics:
                    source_diag.update(trunc_metrics)
                if truncated:
                    source_diag["partial_content"] = True
                snippet = KnowledgeSnippet(
                    id=req.id,  # keep id = CHUNK id so the ledger continues to reference this chunk
                    title=f"{label} – chunk {req.chunk_index}",
                    summary=summary[:280],
                    source=upload.get_source_type_display(),
                    content=trimmed,
                    content_mode="preview",
                    public_label=label,
                    structured_tables=structured_tables,
                    issues=issues,
                    page_summaries=tuple(),
                    read_state=read_state,
                    topic_hints=tuple(),
                    is_pinned=False,
                    upload_id=upload.id,
                    chunk_id=req.id,
                    chunk_index=req.chunk_index,
                    page_number=(req.chunk_index + 1) if req.chunk_index is not None else None,
                    entity_type=chunk_metadata.get("entity_type"),
                    entity_name=chunk_metadata.get("entity_name"),
                    entity_business=chunk_metadata.get("entity_business"),
                    is_table_chunk=bool(chunk_metadata.get("is_table_chunk")),
                    table_id=str(chunk_metadata.get("table_id") or "") or None,
                    aliases=tuple(chunk_metadata.get("aliases") or ()),
                    search_stage="load_chunk",
                    confidence_score=1.0,
                    truncated=truncated,
                    source_diagnostics=source_diag,
                    partial_index=partial_flag,
                    structured_table_count=len(structured_tables),
                    issue_count=len(issues),
                    structured_table_hint=None,
                )
                stitched_snippets.append(snippet)

        return tuple(stitched_snippets)


    @staticmethod
    def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _chunk_hits_are_weak(self, hits: Sequence[ChunkResult], traits: QueryTraits) -> bool:
        if not hits:
            return True
        sample = hits[: self.table_chunk_sample_limit]

        def _is_table_chunk(candidate: ChunkResult) -> bool:
            metadata = candidate.chunk.metadata if isinstance(candidate.chunk.metadata, dict) else {}
            return bool(metadata.get("is_table_chunk"))

        has_table_chunk = any(_is_table_chunk(hit) for hit in sample)
        best_rerank = max((hit.rerank_score or 0.0) for hit in sample)
        vector_distances = [hit.vector_distance for hit in sample if hit.vector_distance is not None]
        best_vector = min(vector_distances) if vector_distances else None
        if has_table_chunk and best_rerank >= self.table_rerank_floor:
            return False
        if best_rerank < self.table_rerank_floor:
            return True
        if best_vector is not None and best_vector > self.table_vector_floor:
            return True
        if not has_table_chunk and best_rerank < (self.table_rerank_floor * 1.2):
            return True
        top = hits[0]
        meta = top.chunk.metadata if isinstance(top.chunk.metadata, dict) else {}
        if meta.get("is_table_preview"):
            filler = self._filler_tokens_for_business(top.chunk.upload.business_profile)
            query_tokens = [
                t.lower() for t in traits.tokens if t and t.lower() not in filler and len(t) > 3
            ]
            text = (top.chunk.content or "").lower()
            missing = [t for t in query_tokens if t not in text]
            if query_tokens and len(missing) >= len(query_tokens) * 0.5:
                return True
        return False

    @staticmethod
    def _is_doc_table_preview_chunk(chunk: KnowledgeUploadChunk) -> bool:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        return bool(metadata.get("is_table_chunk")) and bool(metadata.get("is_table_preview"))

    @staticmethod
    def _chunk_index_type(chunk: KnowledgeUploadChunk) -> str:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        raw = str(metadata.get("index_type") or "").strip().lower()
        if raw:
            return raw
        if metadata.get("is_table_chunk"):
            return "table"
        return "text"

    @staticmethod
    def _interleave_chunk_hits(
        text_hits: Sequence[ChunkResult],
        table_hits: Sequence[ChunkResult],
    ) -> tuple[ChunkResult, ...]:
        merged: list[ChunkResult] = []
        text_idx = 0
        table_idx = 0
        take_text = True
        while text_idx < len(text_hits) or table_idx < len(table_hits):
            if take_text and text_idx < len(text_hits):
                merged.append(text_hits[text_idx])
                text_idx += 1
            elif (not take_text) and table_idx < len(table_hits):
                merged.append(table_hits[table_idx])
                table_idx += 1
            elif text_idx < len(text_hits):
                merged.append(text_hits[text_idx])
                text_idx += 1
            elif table_idx < len(table_hits):
                merged.append(table_hits[table_idx])
                table_idx += 1
            take_text = not take_text
        return tuple(merged)

    @staticmethod
    def _safe_float(value: object, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _clamp_unit(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @classmethod
    def _aggregate_auto_scores(cls, scores: Sequence[float]) -> float:
        if not scores:
            return 0.0
        ordered = sorted((max(0.0, float(score)) for score in scores), reverse=True)
        weights = (1.0, 0.75, 0.55, 0.4, 0.3)
        limited = ordered[: len(weights)]
        weighted_sum = sum(value * weights[idx] for idx, value in enumerate(limited))
        weight_total = sum(weights[: len(limited)]) or 1.0
        coverage = min(1.0, len(ordered) / 3.0)
        # Reward multiple strong corroborating hits without letting long tails dominate.
        return (weighted_sum / weight_total) * (0.75 + 0.25 * coverage)

    def _score_auto_mode_candidates(
        self,
        hits: Sequence[ChunkResult],
        *,
        query_tokens: Sequence[str] | None = None,
        specific_tokens: Sequence[str] | None = None,
    ) -> dict[str, object]:
        if not hits:
            return {
                "auto_score_version": "v2",
                "auto_score_sample_size": 0,
                "auto_score_table_hits": 0,
                "auto_score_text_hits": 0,
                "auto_table_score": 0.0,
                "auto_text_score": 0.0,
                "auto_score_margin": 0.0,
                "auto_table_signal_header_hits": 0,
                "auto_table_signal_specific_hits": 0,
                "auto_table_signal_strong_hits": 0,
                "auto_text_semantic_overlap_avg": 0.0,
            }

        normalized_query_tokens = tuple(
            str(token).strip().lower()
            for token in (query_tokens or ())
            if str(token).strip()
        )
        query_token_set = set(normalized_query_tokens)
        specific_token_set = {
            str(token).strip().lower()
            for token in (specific_tokens or ())
            if str(token).strip()
        }
        sample = tuple(hits[: max(1, int(self.table_chunk_sample_limit))])

        table_scores: list[float] = []
        text_scores: list[float] = []
        table_header_hits = 0
        table_specific_hits = 0
        table_strong_hits = 0
        text_semantic_scores: list[float] = []

        for hit in sample:
            chunk = hit.chunk
            metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            content = str(chunk.content or "")

            rerank = self._clamp_unit(self._safe_float(hit.rerank_score))
            lexical = self._clamp_unit(self._safe_float(hit.lexical_score))
            alias = self._clamp_unit(self._safe_float(hit.alias_confidence))
            recency = self._clamp_unit(self._safe_float(hit.recency_score))
            vector_distance = hit.vector_distance
            vector_signal = 0.0
            if isinstance(vector_distance, (int, float)):
                vector_signal = self._clamp_unit(1.0 - float(vector_distance))
            if lexical <= 0.0 and normalized_query_tokens:
                lexical = self._clamp_unit(self._lexical_score_text(content, normalized_query_tokens))

            # Base relevance from existing ranking signals.
            base = max(
                rerank,
                (0.45 * lexical) + (0.2 * alias) + (0.2 * recency) + (0.15 * vector_signal),
            )

            index_type = self._chunk_index_type(chunk)
            if index_type == "table":
                match_info = hit.diagnostics if isinstance(hit.diagnostics, dict) else {}
                if not {"header_match", "specific_match", "specific_match_strong"} & set(match_info.keys()):
                    if query_token_set or specific_token_set:
                        computed = self._table_chunk_match_info(
                            chunk,
                            query_tokens=query_token_set,
                            specific_tokens=specific_token_set,
                        )
                        if isinstance(hit.diagnostics, dict):
                            hit.diagnostics.update(computed)
                        match_info = computed

                header_match = bool(match_info.get("header_match"))
                specific_match = bool(match_info.get("specific_match"))
                strong_match = bool(match_info.get("specific_match_strong"))
                ratio = self._clamp_unit(self._safe_float(match_info.get("specific_match_ratio")))
                role = str(metadata.get("table_chunk_role") or "").strip().lower()
                is_preview = bool(metadata.get("is_table_preview"))

                table_boost = 0.0
                if header_match:
                    table_boost += 0.18
                    table_header_hits += 1
                if specific_match:
                    table_boost += 0.24
                    table_specific_hits += 1
                if strong_match:
                    table_boost += 0.3
                    table_strong_hits += 1
                table_boost += 0.15 * ratio
                if role == "row":
                    table_boost += 0.08
                if is_preview:
                    table_boost -= 0.05
                table_scores.append(max(0.0, base + table_boost))
            else:
                semantic_overlap = self._clamp_unit(self._lexical_score_text(content, normalized_query_tokens))
                density = self._clamp_unit(min(1.0, len(content) / 600.0))
                text_boost = (0.28 * semantic_overlap) + (0.08 * density)
                text_scores.append(max(0.0, base + text_boost))
                text_semantic_scores.append(semantic_overlap)

        table_score = round(self._aggregate_auto_scores(table_scores), 6)
        text_score = round(self._aggregate_auto_scores(text_scores), 6)
        margin = round(abs(table_score - text_score), 6)
        text_semantic_avg = round(sum(text_semantic_scores) / len(text_semantic_scores), 6) if text_semantic_scores else 0.0

        return {
            "auto_score_version": "v2",
            "auto_score_sample_size": len(sample),
            "auto_score_table_hits": len(table_scores),
            "auto_score_text_hits": len(text_scores),
            "auto_table_score": table_score,
            "auto_text_score": text_score,
            "auto_score_margin": margin,
            "auto_table_signal_header_hits": table_header_hits,
            "auto_table_signal_specific_hits": table_specific_hits,
            "auto_table_signal_strong_hits": table_strong_hits,
            "auto_text_semantic_overlap_avg": text_semantic_avg,
        }

    @staticmethod
    def _scope_key_value_pairs(content: str, *, max_pairs: int = 12) -> tuple[tuple[str, str], ...]:
        text = str(content or "")
        if not text:
            return tuple()
        pairs: list[tuple[str, str]] = []
        window = text[:1600]
        for match in re.finditer(r"([^\n:;|]{1,72})\s*:\s*([^;\n|]{1,220})", window):
            key = KnowledgeSearchService._normalize_topic_value(match.group(1))
            value = KnowledgeSearchService._normalize_scope_category_value(
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

        This is deterministic and tenant-agnostic: strip structural artifacts
        (table/sheet markers, punctuation noise), dedupe repeated tokens, and keep
        a compact lexical phrase that remains easy to match later.
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

    def _scope_categories_for_contract(
        self,
        *,
        scope_summary: Mapping[str, object] | None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        categories_map = (
            scope_summary.get("category_counts")
            if isinstance(scope_summary, Mapping)
            and isinstance(scope_summary.get("category_counts"), Mapping)
            else None
        )
        ranked_items = self._rank_scope_category_items(
            categories_map,
            max_categories=self.scope_category_max,
        )
        categories = tuple(label for label, _count in ranked_items)
        top_categories = categories[: self.scope_top_category_max]
        return categories, top_categories

    @staticmethod
    def _scope_ref_uuid_string(value: object) -> str:
        if isinstance(value, uuid.UUID):
            return str(value)
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return str(uuid.UUID(text))
        except (TypeError, ValueError):
            return ""

    def _scope_category_ref_hints_from_candidates(
        self,
        *,
        hits: Sequence[ChunkResult],
        business_profile,
        top_categories: Sequence[str],
        query_tokens: Sequence[str] | None = None,
        filler_tokens: set[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        normalized_top_categories = self._normalize_scope_category_sequence(
            top_categories,
            max_categories=self.scope_top_category_max,
        )
        if not normalized_top_categories:
            return {}

        normalized_query_tokens = {
            str(token).strip().lower()
            for token in (query_tokens or ())
            if str(token).strip()
        }
        normalized_filler_tokens = {
            str(token).strip().lower()
            for token in (filler_tokens or set())
            if str(token).strip()
        }

        category_lookup: dict[str, str] = {}
        for category in normalized_top_categories:
            normalized = self._normalize_scope_category_value(category, max_chars=96)
            if normalized:
                category_lookup[normalized] = category
        if not category_lookup:
            return {}

        per_category_refs: dict[str, list[str]] = {category: [] for category in normalized_top_categories}
        per_category_seen: dict[str, set[str]] = {category: set() for category in normalized_top_categories}
        max_refs_per_category = max(1, int(self.scope_category_ref_max))

        for hit in hits:
            label = self._scope_category_from_hit(
                hit,
                business_profile=business_profile,
                query_tokens=normalized_query_tokens,
                filler_tokens=normalized_filler_tokens,
            )
            normalized_label = self._normalize_scope_category_value(label, max_chars=96)
            canonical_label = category_lookup.get(normalized_label)
            if not canonical_label:
                continue

            ref_uuid = self._scope_ref_uuid_string(getattr(hit, "chunk_id", None))
            if not ref_uuid:
                ref_uuid = self._scope_ref_uuid_string(getattr(getattr(hit, "chunk", None), "id", None))
            if not ref_uuid:
                continue

            seen_ids = per_category_seen.get(canonical_label)
            ref_list = per_category_refs.get(canonical_label)
            if seen_ids is None or ref_list is None:
                continue
            if ref_uuid in seen_ids or len(ref_list) >= max_refs_per_category:
                continue
            seen_ids.add(ref_uuid)
            ref_list.append(ref_uuid)

            if all(len(refs) >= max_refs_per_category for refs in per_category_refs.values()):
                break

        out: dict[str, dict[str, object]] = {}
        for category in normalized_top_categories:
            refs = per_category_refs.get(category) or []
            if refs:
                out[category] = {
                    "ref_ids": refs,
                    "source": "retrieval_candidates",
                }
            else:
                out[category] = {"fallback": "scoped_search"}
        return out

    @classmethod
    def _rank_scope_category_items(
        cls,
        category_counts: Mapping[str, object] | None,
        *,
        max_categories: int,
    ) -> tuple[tuple[str, int], ...]:
        if not isinstance(category_counts, Mapping):
            return tuple()

        limit = max(1, int(max_categories))

        def _coerce_count(raw: object) -> int:
            try:
                coerced = int(raw)
            except (TypeError, ValueError):
                coerced = 1
            return coerced if coerced > 0 else 0

        merged_counts: dict[str, int] = {}
        for raw_label, raw_count in category_counts.items():
            normalized_label = cls._normalize_scope_category_value(raw_label, max_chars=96)
            if not normalized_label:
                continue
            count_value = _coerce_count(raw_count)
            if count_value <= 0:
                continue
            merged_counts[normalized_label] = merged_counts.get(normalized_label, 0) + count_value

        if not merged_counts:
            return tuple()

        ordered_items = sorted(
            merged_counts.items(),
            key=lambda item: (-int(item[1]), item[0]),
        )
        return tuple((label, int(count)) for label, count in ordered_items[:limit])

    @classmethod
    def _normalize_scope_category_sequence(
        cls,
        values: object,
        *,
        max_categories: int,
    ) -> tuple[str, ...]:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            return tuple()
        limit = max(1, int(max_categories))
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            label = cls._normalize_scope_category_value(raw, max_chars=96)
            if not label or label in seen:
                continue
            seen.add(label)
            normalized.append(label)
            if len(normalized) >= limit:
                break
        return tuple(normalized)

    def _scope_category_from_hit(
        self,
        hit: ChunkResult,
        *,
        business_profile,
        query_tokens: set[str],
        filler_tokens: set[str],
    ) -> str:
        chunk = hit.chunk
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        diagnostics = hit.diagnostics if isinstance(hit.diagnostics, dict) else {}

        metadata_candidates: list[object] = []
        for key in ("row_label", "table_title", "section_heading", "entity_name", "display_name", "title", "sheet_name"):
            metadata_candidates.append(metadata.get(key))
        for key in ("table_title", "column_key", "value"):
            metadata_candidates.append(diagnostics.get(key))

        for candidate in metadata_candidates:
            normalized = self._normalize_scope_category_value(candidate, max_chars=96)
            if self._scope_is_specific_category(
                normalized,
                business_profile=business_profile,
                query_tokens=query_tokens,
                filler_tokens=filler_tokens,
            ):
                return normalized

        content = str(chunk.content or "")
        pairs = self._scope_key_value_pairs(content)
        preferred_value_keys = self._preferred_value_keys_for_business(business_profile)
        scope_column_keys = self._scope_column_keys_for_business(business_profile)
        fallback_values: list[str] = []
        for key, value in pairs:
            if key in scope_column_keys:
                continue
            if not self._scope_is_specific_category(
                value,
                business_profile=business_profile,
                query_tokens=query_tokens,
                filler_tokens=filler_tokens,
            ):
                continue
            if key in preferred_value_keys:
                return value
            fallback_values.append(value)
        if fallback_values:
            return fallback_values[0]

        for raw_line in content.splitlines():
            line = self._normalize_scope_category_value(raw_line, max_chars=96)
            if self._scope_is_specific_category(
                line,
                business_profile=business_profile,
                query_tokens=query_tokens,
                filler_tokens=filler_tokens,
            ):
                return line
        return ""

    def _build_scope_summary_from_candidates(
        self,
        hits: Sequence[ChunkResult],
        *,
        business_profile=None,
        query_tokens: Sequence[str] | None = None,
        filler_tokens: set[str] | None = None,
    ) -> dict[str, object]:
        total_matches = len(hits)
        if not hits:
            return {
                "total_matches": 0,
                "distinct_docs": 0,
                "category_counts": {},
                "is_broad_scope": False,
            }

        normalized_query_tokens = {
            str(token).strip().lower()
            for token in (query_tokens or ())
            if str(token).strip()
        }
        normalized_filler_tokens = {
            str(token).strip().lower()
            for token in (filler_tokens or set())
            if str(token).strip()
        }
        doc_ids: set[str] = set()
        category_counts: Counter[str] = Counter()
        for hit in hits:
            doc_id = self._scope_upload_identity(hit.chunk)
            if doc_id:
                doc_ids.add(doc_id)
            label = self._scope_category_from_hit(
                hit,
                business_profile=business_profile,
                query_tokens=normalized_query_tokens,
                filler_tokens=normalized_filler_tokens,
            )
            if label:
                category_counts[label] += 1

        ranked_categories = self._rank_scope_category_items(
            category_counts,
            max_categories=self.scope_category_max,
        )
        distinct_docs = len(doc_ids)
        distinct_categories = len(category_counts)
        is_broad_scope = bool(
            total_matches >= 6
            and distinct_docs >= 2
            and distinct_categories >= 3
        )
        return {
            "total_matches": int(total_matches),
            "distinct_docs": int(distinct_docs),
            "category_counts": {label: int(count) for label, count in ranked_categories},
            "is_broad_scope": is_broad_scope,
        }

    def _normalize_scope_summary(
        self,
        value: object,
    ) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None

        def _coerce_int(raw: object) -> int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        raw_counts = value.get("category_counts")
        ranked_counts = self._rank_scope_category_items(
            raw_counts if isinstance(raw_counts, Mapping) else None,
            max_categories=self.scope_category_max,
        )
        total_matches = max(0, _coerce_int(value.get("total_matches")))
        distinct_docs = max(0, _coerce_int(value.get("distinct_docs")))
        if distinct_docs == 0 and total_matches > 0:
            distinct_docs = 1
        is_broad_scope = bool(value.get("is_broad_scope"))
        return {
            "total_matches": total_matches,
            "distinct_docs": distinct_docs,
            "category_counts": {label: int(count) for label, count in ranked_counts},
            "is_broad_scope": is_broad_scope,
        }

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
            # Agentic mode: do not ask the user to choose table-only vs text-only.
            # Fall back to the existing hint and let fusion/parallel search reconcile evidence.
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
            # Keep current behavior when scores are weak/insufficient.
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

    @staticmethod
    def _derive_auto_decision_contract(
        *,
        route_diagnostics: Mapping[str, object] | None = None,
        scoring_diagnostics: Mapping[str, object] | None = None,
        requires_clarification: bool = False,
        scope_summary: Mapping[str, object] | None = None,
        conflict_detected: bool | None = None,
        no_result_reason: str | None = None,
    ) -> dict[str, object]:
        route_data = route_diagnostics or {}
        scoring_data = scoring_diagnostics or {}

        try:
            table_hits = max(0, int(route_data.get("index_route_table_hits") or 0))
        except (TypeError, ValueError):
            table_hits = 0
        try:
            text_hits = max(0, int(route_data.get("index_route_text_hits") or 0))
        except (TypeError, ValueError):
            text_hits = 0

        scored_table = scoring_data.get("auto_table_score")
        scored_text = scoring_data.get("auto_text_score")
        table_score = float(scored_table) if isinstance(scored_table, (int, float)) else float(table_hits)
        text_score = float(scored_text) if isinstance(scored_text, (int, float)) else float(text_hits)
        if isinstance(scoring_data.get("auto_score_margin"), (int, float)):
            margin = round(float(scoring_data["auto_score_margin"]), 6)
        else:
            margin = round(abs(table_score - text_score), 6)
        route = str(route_data.get("index_route") or "").strip().lower()
        has_route = bool(route)
        used_scoring = isinstance(scored_table, (int, float)) or isinstance(scored_text, (int, float))

        if requires_clarification:
            decision = "clarification"
        elif used_scoring and margin <= 0.05 and table_score > 0.0 and text_score > 0.0:
            decision = "blended"
        elif used_scoring and table_score > text_score:
            decision = "table"
        elif used_scoring and text_score > table_score:
            decision = "text"
        elif not has_route:
            decision = "undecided"
        elif table_score > text_score:
            decision = "table"
        elif text_score > table_score:
            decision = "text"
        elif route.startswith("mixed"):
            decision = "blended"
        elif route.startswith("table"):
            decision = "table"
        elif route.startswith("text"):
            decision = "text"
        else:
            decision = "undecided"

        normalized_scope_summary = dict(scope_summary) if isinstance(scope_summary, Mapping) else None
        normalized_no_result_reason = (
            str(no_result_reason).strip().lower() if isinstance(no_result_reason, str) and no_result_reason.strip() else None
        )
        normalized_conflict = bool(conflict_detected) if isinstance(conflict_detected, bool) else False
        normalized_categories = list(
            KnowledgeSearchService._normalize_scope_category_sequence(
                scoring_data.get("categories"),
                max_categories=SCOPE_CATEGORY_MAX_DEFAULT,
            )
        )
        if not normalized_categories:
            ranked_items = KnowledgeSearchService._rank_scope_category_items(
                scope_summary.get("category_counts")
                if isinstance(scope_summary, Mapping) and isinstance(scope_summary.get("category_counts"), Mapping)
                else None,
                max_categories=SCOPE_CATEGORY_MAX_DEFAULT,
            )
            normalized_categories = [label for label, _count in ranked_items]

        normalized_top_categories = list(
            KnowledgeSearchService._normalize_scope_category_sequence(
                scoring_data.get("top_categories"),
                max_categories=SCOPE_TOP_CATEGORY_MAX_DEFAULT,
            )
        )
        if normalized_top_categories and normalized_categories:
            category_set = set(normalized_categories)
            normalized_top_categories = [label for label in normalized_top_categories if label in category_set]
        if not normalized_top_categories:
            normalized_top_categories = list(normalized_categories[:SCOPE_TOP_CATEGORY_MAX_DEFAULT])
        raw_ui_mode = (
            scoring_data.get("clarification_ui_mode")
            or route_data.get("clarification_ui_mode")
        )
        normalized_ui_mode = (
            str(raw_ui_mode).strip().lower()
            if isinstance(raw_ui_mode, str) and str(raw_ui_mode).strip()
            else None
        )
        if normalized_ui_mode not in {"text"}:
            normalized_ui_mode = None
        if requires_clarification and not normalized_ui_mode:
            normalized_ui_mode = "text"

        return {
            "table_score": table_score,
            "text_score": text_score,
            "margin": margin,
            "decision": decision,
            "needs_clarification": bool(requires_clarification),
            "scope_summary": normalized_scope_summary,
            "categories": normalized_categories,
            "top_categories": normalized_top_categories,
            "clarification_ui_mode": normalized_ui_mode,
            "conflict_detected": normalized_conflict,
            "no_result_reason": normalized_no_result_reason,
        }

    @staticmethod
    def _normalize_segment_key(value: object) -> str:
        text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
        return text.strip("_")

    @staticmethod
    def _normalize_conflict_value(value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = text.replace(",", "")
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^a-z0-9\u0600-\u06FF%./:+ -]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text in {"n/a", "na", "-", "none", "null", "not applicable"}:
            return ""
        return text

    def _segment_targets_from_context(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        table_context: Mapping[str, object] | None,
    ) -> tuple[str, ...]:
        known_segments = self._known_segment_keys_for_business(business_profile)
        allow_fallback_tokens = not known_segments
        stop_tokens = self._scope_label_stop_tokens()
        candidates: list[str] = []
        seen: set[str] = set()
        for source in (
            (table_context or {}).get("matched_columns_specific"),
            (table_context or {}).get("matched_columns_tokens"),
            (table_context or {}).get("specific_tokens"),
            traits.tokens,
        ):
            for raw in source or ():
                normalized = self._normalize_segment_key(raw)
                if not normalized:
                    continue
                if known_segments and normalized in known_segments and normalized not in seen:
                    seen.add(normalized)
                    candidates.append(normalized)
                    continue
                if (
                    allow_fallback_tokens
                    and normalized not in seen
                    and normalized not in stop_tokens
                    and len(normalized) > 1
                ):
                    seen.add(normalized)
                    candidates.append(normalized)
        return tuple(candidates)

    def _detect_conflicting_evidence(
        self,
        *,
        snippets: Sequence[KnowledgeSnippet],
        business_profile,
        traits: QueryTraits,
        table_context: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        segment_targets = self._segment_targets_from_context(
            business_profile=business_profile,
            traits=traits,
            table_context=table_context,
        )
        if not segment_targets:
            return None

        segment_keys = self._known_segment_keys_for_business(business_profile)
        conflict_groups: dict[tuple[str, str], dict[str, object]] = {}
        for snippet in snippets[:8]:
            if not snippet.is_table_chunk:
                continue
            diagnostics = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else {}
            text = str(snippet.content or snippet.summary or "").strip()
            if not text:
                continue
            pairs = self._scope_key_value_pairs(text, max_pairs=16)
            pair_map: dict[str, str] = {}
            category = ""
            for key, value in pairs:
                pair_map[key] = value
                if key in {
                    "service",
                    "services",
                    "types_of_services_fee",
                    "types of services fee",
                    "tariff",
                    "tarrif",
                    "subsection",
                    "category",
                    "product",
                    "plan",
                    "account",
                } and value and not category:
                    category = value
            if not category:
                category = str(diagnostics.get("table_title") or snippet.title or "").strip()
            category = self._normalize_topic_value(self._clean_auto_evidence_label(category, max_chars=96))
            if not category:
                continue

            selected_segment = ""
            selected_value = ""
            for segment in segment_targets:
                if segment in pair_map and pair_map[segment]:
                    selected_segment = segment
                    selected_value = pair_map[segment]
                    break
            if not selected_value:
                fee_value = str(diagnostics.get("table_row_fee_value") or "").strip()
                if fee_value and segment_targets:
                    selected_segment = segment_targets[0]
                    selected_value = fee_value
            if not selected_value:
                for key, value in pairs:
                    if self._normalize_segment_key(key) in segment_keys and value:
                        normalized_key = self._normalize_segment_key(key)
                        if normalized_key in segment_targets:
                            selected_segment = normalized_key
                            selected_value = value
                            break
            if not selected_segment or not selected_value:
                continue

            normalized_value = self._normalize_conflict_value(selected_value)
            if not normalized_value:
                continue
            group_key = (selected_segment, category)
            group = conflict_groups.setdefault(
                group_key,
                {"values": {}, "sources": set()},
            )
            values_map = group["values"]
            if isinstance(values_map, dict):
                entry = values_map.setdefault(
                    normalized_value,
                    {
                        "display_value": str(selected_value).strip(),
                        "snippet_ids": [],
                    },
                )
                snippet_ids = entry.get("snippet_ids")
                if isinstance(snippet_ids, list):
                    snippet_ids.append(str(snippet.id))
            sources = group.get("sources")
            if isinstance(sources, set):
                sources.add(str(snippet.id))

        if not conflict_groups:
            return None

        best_conflict: dict[str, object] | None = None
        for (segment, category), payload in conflict_groups.items():
            values_map = payload.get("values")
            sources = payload.get("sources")
            if not isinstance(values_map, dict):
                continue
            if len(values_map) < 2:
                continue
            display_values = [
                str((entry or {}).get("display_value") or normalized).strip()
                for normalized, entry in values_map.items()
            ]
            if len(display_values) < 2:
                continue
            candidate = {
                "segment": segment,
                "category": category,
                "values": display_values[:3],
                "source_count": len(sources) if isinstance(sources, set) else 0,
                "value_count": len(values_map),
            }
            if best_conflict is None:
                best_conflict = candidate
                continue
            if int(candidate["value_count"]) > int(best_conflict.get("value_count") or 0):
                best_conflict = candidate
                continue
            if (
                int(candidate["value_count"]) == int(best_conflict.get("value_count") or 0)
                and int(candidate["source_count"]) > int(best_conflict.get("source_count") or 0)
            ):
                best_conflict = candidate

        return best_conflict

    @staticmethod
    def _build_conflict_clarification_question(conflict: Mapping[str, object] | None) -> str:
        if not isinstance(conflict, Mapping):
            return (
                "I found conflicting values in the retrieved sources. "
                "Do you want me to list all conflicting values with sources?"
            )
        segment = str(conflict.get("segment") or "").replace("_", " ").strip()
        category = str(conflict.get("category") or "").strip()
        values = conflict.get("values")
        rendered_values: list[str] = []
        if isinstance(values, list):
            for raw in values:
                value = str(raw or "").strip()
                if value:
                    rendered_values.append(value)
                if len(rendered_values) >= 2:
                    break
        value_text = " vs ".join(rendered_values) if rendered_values else "different values"
        category_text = category or "this fee item"
        if segment:
            return (
                f"I found conflicting values for {category_text} in the {segment} segment "
                f"({value_text}). Do you want both values with sources or should I narrow by document/date?"
            )
        return (
            f"I found conflicting values for {category_text} ({value_text}). "
            "Do you want both values with sources or should I narrow by document/date?"
        )

    @staticmethod
    def _derive_no_result_reason(
        *,
        diagnostics: Mapping[str, object],
        table_blocked: bool,
    ) -> str:
        if table_blocked or str(diagnostics.get("table_reason") or "").strip().lower() == "specific_tokens_missing":
            return "not_applicable_to_segment"

        def _safe_int(value: object) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        candidate_signals = (
            _safe_int(diagnostics.get("chunk_candidate_count")),
            _safe_int(diagnostics.get("chunk_candidate_count_raw")),
            _safe_int(diagnostics.get("vector_candidates")),
            _safe_int(diagnostics.get("vector_candidates_post_threshold")),
            _safe_int(diagnostics.get("fts_candidates")),
            _safe_int(diagnostics.get("alias_hits")),
        )
        if any(value > 0 for value in candidate_signals):
            return "insufficient_evidence"

        path = str(diagnostics.get("path") or "").strip().lower()
        if path in {"hybrid", "parallel_rrf", "table_direct", "table_blended"}:
            return "insufficient_evidence"
        return "not_found"

    def _apply_phase6_semantics(
        self,
        *,
        status: str,
        snippets: Sequence[KnowledgeSnippet],
        diagnostics: Mapping[str, object],
        business_profile=None,
        traits: QueryTraits,
        table_context: Mapping[str, object] | None,
        table_blocked: bool,
    ) -> tuple[str, tuple[KnowledgeSnippet, ...], dict[str, object]]:
        updated_status = str(status or "not_found").strip().lower() or "not_found"
        updated_snippets = tuple(snippets or ())
        updated_diagnostics: dict[str, object] = dict(diagnostics or {})
        updated_diagnostics.setdefault("conflict_detected", False)
        updated_diagnostics.setdefault("no_result_reason", None)

        conflict_payload: dict[str, object] | None = None
        if (
            updated_status == "ok"
            and updated_snippets
            and not traits.is_identifier_like
        ):
            detected_conflict = self._detect_conflicting_evidence(
                snippets=updated_snippets,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
            )
            if detected_conflict:
                # Conflicts should be non-blocking in agentic RAG. Keep evidence, mark the conflict,
                # and let the assistant explain uncertainty or present both values if needed.
                conflict_payload = dict(detected_conflict)
                updated_diagnostics["conflict_detected"] = True
                updated_diagnostics["conflict_context"] = conflict_payload
                updated_diagnostics["reason"] = "conflicting_evidence"

        no_result_reason = None
        if updated_status == "not_found":
            no_result_reason = self._derive_no_result_reason(
                diagnostics=updated_diagnostics,
                table_blocked=table_blocked,
            )
            updated_diagnostics["no_result_reason"] = no_result_reason

        requires_clarification = bool(updated_status == "needs_clarification")
        scope_summary = (
            updated_diagnostics.get("scope_summary")
            if isinstance(updated_diagnostics.get("scope_summary"), Mapping)
            else None
        )
        categories: tuple[str, ...] = tuple()
        top_categories: tuple[str, ...] = tuple()
        if isinstance(scope_summary, Mapping):
            categories, top_categories = self._scope_categories_for_contract(
                scope_summary=scope_summary,
            )
        if categories and "categories" not in updated_diagnostics:
            updated_diagnostics["categories"] = list(categories)
        if top_categories and "top_categories" not in updated_diagnostics:
            updated_diagnostics["top_categories"] = list(top_categories)
        current_ui_mode = str(updated_diagnostics.get("clarification_ui_mode") or "").strip().lower()
        if requires_clarification and current_ui_mode not in {"text"}:
            updated_diagnostics["clarification_ui_mode"] = "text"
        existing_contract = updated_diagnostics.get("auto_decision_contract")
        if isinstance(existing_contract, Mapping):
            contract = dict(existing_contract)
            if requires_clarification:
                contract["decision"] = "clarification"
            contract["needs_clarification"] = requires_clarification
            contract["scope_summary"] = dict(scope_summary) if isinstance(scope_summary, Mapping) else contract.get("scope_summary")
            contract["categories"] = list(updated_diagnostics.get("categories") or contract.get("categories") or [])
            contract["top_categories"] = list(updated_diagnostics.get("top_categories") or contract.get("top_categories") or [])
            contract_ui_mode = str(updated_diagnostics.get("clarification_ui_mode") or contract.get("clarification_ui_mode") or "").strip().lower()
            contract["clarification_ui_mode"] = contract_ui_mode if contract_ui_mode in {"text"} else None
            contract["conflict_detected"] = bool(updated_diagnostics.get("conflict_detected"))
            contract["no_result_reason"] = (
                str(updated_diagnostics.get("no_result_reason")).strip().lower()
                if str(updated_diagnostics.get("no_result_reason") or "").strip()
                else None
            )
            updated_diagnostics["auto_decision_contract"] = contract
        else:
            updated_diagnostics["auto_decision_contract"] = self._derive_auto_decision_contract(
                route_diagnostics=updated_diagnostics,
                scoring_diagnostics=updated_diagnostics,
                requires_clarification=requires_clarification,
                scope_summary=scope_summary,
                conflict_detected=bool(updated_diagnostics.get("conflict_detected")),
                no_result_reason=str(updated_diagnostics.get("no_result_reason") or "") or None,
            )
        return updated_status, updated_snippets, updated_diagnostics

    def _route_chunk_hits(
        self,
        hits: Sequence[ChunkResult],
        *,
        table_intent: bool,
        table_context: Mapping[str, object] | None = None,
    ) -> tuple[tuple[ChunkResult, ...], tuple[ChunkResult, ...], dict[str, object]]:
        if not hits:
            return tuple(), tuple(), {"index_route": "empty", "index_route_table_hits": 0, "index_route_text_hits": 0}
        table_hits: list[ChunkResult] = []
        text_hits: list[ChunkResult] = []
        for hit in hits:
            index_type = self._chunk_index_type(hit.chunk)
            if index_type == "table":
                table_hits.append(hit)
            else:
                text_hits.append(hit)
        table_context = table_context or {}
        specific_tokens = set(table_context.get("specific_tokens") or ())
        query_tokens = set(table_context.get("query_tokens") or ())

        filtered_count = 0
        weak_count = 0
        strong_count = 0
        if table_hits and table_intent and specific_tokens:
            for hit in table_hits:
                match_info = self._table_chunk_match_info(
                    hit.chunk,
                    query_tokens=query_tokens,
                    specific_tokens=specific_tokens,
                )
                hit.diagnostics.update(match_info)
                if match_info.get("specific_match_strong"):
                    strong_count += 1
                elif match_info.get("specific_match"):
                    weak_count += 1
                else:
                    filtered_count += 1

        raw_bias = str(table_context.get("modality_bias") or "").strip().lower()
        if raw_bias not in {"table", "text", "mixed"}:
            raw_bias = "table" if table_intent else "text"

        if table_hits and text_hits:
            table_biased = raw_bias == "table" or (raw_bias == "mixed" and table_intent)
            if table_biased:
                primary_hits = self._interleave_chunk_hits(table_hits, text_hits)
                secondary_hits = tuple(text_hits)
                route = "mixed_primary_table_biased"
                dominant_modality = "table"
            else:
                primary_hits = self._interleave_chunk_hits(text_hits, table_hits)
                secondary_hits = tuple(table_hits)
                route = "mixed_primary_text_biased"
                dominant_modality = "text"
        elif table_hits:
            primary_hits = tuple(table_hits)
            secondary_hits = tuple()
            route = "mixed_primary_table_only"
            dominant_modality = "table"
        elif text_hits:
            primary_hits = tuple(text_hits)
            secondary_hits = tuple()
            route = "mixed_primary_text_only"
            dominant_modality = "text"
        else:
            primary_hits = tuple(hits)
            secondary_hits = tuple()
            route = "mixed_fallback_all"
            dominant_modality = "unknown"

        diagnostics = {
            "index_route": route,
            "index_route_table_hits": len(table_hits),
            "index_route_text_hits": len(text_hits),
            "index_route_modality_bias": raw_bias,
            "index_route_dominant_modality": dominant_modality,
            "index_route_mixed": bool(table_hits and text_hits),
        }
        if specific_tokens:
            diagnostics.update(
                {
                    "table_specific_filtered": filtered_count,
                    "table_specific_strong_hits": strong_count,
                    "table_specific_weak_hits": weak_count,
                }
            )
        return tuple(primary_hits), secondary_hits, diagnostics

    def _table_parent_hits(
        self,
        business_profile,
        hits: Sequence[ChunkResult],
        *,
        limit: int = 3,
    ) -> tuple[ChunkResult, ...]:
        if not hits:
            return tuple()
        table_ids: set[str] = set()
        hit_ids: set[uuid.UUID] = {hit.chunk_id for hit in hits}
        for hit in hits:
            meta = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
            if not meta.get("is_table_chunk"):
                continue
            if str(meta.get("table_chunk_role") or "") != "row":
                continue
            table_id = meta.get("table_id")
            if table_id:
                table_ids.add(str(table_id))
        if not table_ids:
            return tuple()
        parents = (
            KnowledgeUploadChunk.objects.filter(
                upload__business_profile=business_profile,
                metadata__table_id__in=list(table_ids),
                metadata__table_chunk_role="parent",
            )
            .select_related("upload")
            .order_by("chunk_index")[: max(1, int(limit))]
        )
        parent_hits: list[ChunkResult] = []
        for chunk in parents:
            if chunk.id in hit_ids:
                continue
            parent_hits.append(ChunkResult(chunk=chunk, source_stage="table_parent"))
        return tuple(parent_hits)

    def _table_hit_metadata(self, hit: ChunkResult) -> dict[str, object]:
        return hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}

    def _table_hit_table_id(self, hit: ChunkResult) -> str:
        metadata = self._table_hit_metadata(hit)
        return str(metadata.get("table_id") or "").strip()

    def _is_table_row_hit(self, hit: ChunkResult) -> bool:
        metadata = self._table_hit_metadata(hit)
        return bool(metadata.get("is_table_chunk")) and str(metadata.get("table_chunk_role") or "").strip().lower() == "row"

    def _is_table_context_hit(self, hit: ChunkResult) -> bool:
        metadata = self._table_hit_metadata(hit)
        role = str(metadata.get("table_chunk_role") or "").strip().lower()
        return bool(metadata.get("is_table_preview")) or role == "parent"

    def _query_token_overlap_fraction(
        self,
        content: str | None,
        query_tokens: Sequence[str] | None,
    ) -> float:
        normalized_tokens = tuple(
            token.lower()
            for token in (query_tokens or ())
            if isinstance(token, str) and token.strip()
        )
        text = str(content or "").lower()
        if not text or not normalized_tokens:
            return 0.0
        matches = sum(1 for token in normalized_tokens if token in text)
        return matches / len(normalized_tokens)

    def _merge_expanded_table_hits(
        self,
        chunk_hits: Sequence[ChunkResult],
        expanded_rows: Sequence[ChunkResult],
        *,
        query_tokens: Sequence[str] | None = None,
    ) -> tuple[tuple[ChunkResult, ...], dict[str, int]]:
        existing_ids = {hit.chunk_id for hit in chunk_hits}
        new_rows = [row for row in expanded_rows if row.chunk_id not in existing_ids]
        if not new_rows:
            return tuple(chunk_hits), {
                "expanded_rows": 0,
                "relevant_rows": 0,
                "supplemental_rows": 0,
                "parent_chunks_suppressed": 0,
                "parent_chunks_limited": 0,
            }

        parent_chunks = [hit for hit in chunk_hits if self._is_table_context_hit(hit)]
        non_parent_chunks = [hit for hit in chunk_hits if not self._is_table_context_hit(hit)]
        seed_row_tables = Counter(
            self._table_hit_table_id(hit)
            for hit in non_parent_chunks
            if self._is_table_row_hit(hit) and self._table_hit_table_id(hit)
        )

        relevant_rows: list[ChunkResult] = []
        supplemental_rows: list[ChunkResult] = []
        for row in new_rows:
            overlap_fraction = self._query_token_overlap_fraction(row.chunk.content, query_tokens)
            table_id = self._table_hit_table_id(row)
            if overlap_fraction >= 0.5 or (table_id and seed_row_tables.get(table_id, 0)):
                relevant_rows.append(row)
            else:
                supplemental_rows.append(row)

        def _row_priority(hit: ChunkResult) -> tuple[int, float, float, float]:
            table_id = self._table_hit_table_id(hit)
            return (
                1 if table_id and seed_row_tables.get(table_id, 0) else 0,
                self._query_token_overlap_fraction(hit.chunk.content, query_tokens),
                float(hit.rerank_score or 0.0),
                float(hit.lexical_score or 0.0),
            )

        relevant_rows.sort(key=_row_priority, reverse=True)
        supplemental_rows.sort(key=_row_priority, reverse=True)

        merged_hits = tuple(relevant_rows) + tuple(non_parent_chunks) + tuple(supplemental_rows)
        row_evidence_by_table = Counter(
            self._table_hit_table_id(hit)
            for hit in merged_hits
            if self._is_table_row_hit(hit) and self._table_hit_table_id(hit)
        )

        retained_parents: list[ChunkResult] = []
        parent_chunks_suppressed = 0
        parent_chunks_limited = 0
        for parent_hit in parent_chunks:
            table_id = self._table_hit_table_id(parent_hit)
            if table_id and row_evidence_by_table.get(table_id, 0) >= 2:
                parent_chunks_suppressed += 1
                continue
            if len(retained_parents) >= self.table_row_expansion_max_parent_context:
                parent_chunks_limited += 1
                continue
            retained_parents.append(parent_hit)

        merged_hits = merged_hits + tuple(retained_parents)
        return merged_hits, {
            "expanded_rows": len(new_rows),
            "relevant_rows": len(relevant_rows),
            "supplemental_rows": len(supplemental_rows),
            "parent_chunks_suppressed": parent_chunks_suppressed,
            "parent_chunks_limited": parent_chunks_limited,
        }

    def _expand_table_rows(
        self,
        business_profile,
        hits: Sequence[ChunkResult],
        *,
        max_rows_per_table: int | None = None,
        query_tokens: Sequence[str] | None = None,
    ) -> tuple[ChunkResult, ...]:
        """
        Hierarchical table retrieval: expand parent/preview chunks to row chunks.
        
        For any parent/preview chunks in hits, fetch associated row chunks.
        Row chunks contain actual answer data (e.g., "EGP 500") while parent chunks
        often contain OCR-corrupted markdown summaries.
        
        This enables "table discovery → row expansion → answer from rows" pattern.
        """
        if not hits:
            return tuple()
        
        max_rows = max_rows_per_table or self.table_row_expansion_limit
        table_ids: set[str] = set()
        hit_ids: set[uuid.UUID] = {hit.chunk_id for hit in hits}
        
        # Track parent/preview chunks for logging
        parent_count = 0
        preview_count = 0
        seeded_row_count = 0
        missing_table_id_count = 0
        best_seed_by_table: dict[str, ChunkResult] = {}

        def _result_strength(candidate: ChunkResult) -> float:
            values: list[float] = []
            for raw_value in (
                candidate.rerank_score,
                candidate.lexical_score,
                candidate.alias_confidence,
                candidate.recency_score,
            ):
                try:
                    values.append(float(raw_value))
                except (TypeError, ValueError):
                    continue
            return max(values) if values else 0.0
        
        for hit in hits:
            meta = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
            if not meta.get("is_table_chunk"):
                continue
            # Identify parent/preview chunks (table discovery)
            is_parent = meta.get("table_chunk_role") == "parent"
            is_preview = bool(meta.get("is_table_preview"))
            is_row = str(meta.get("table_chunk_role") or "").strip().lower() == "row"
            
            if is_parent:
                parent_count += 1
            if is_preview:
                preview_count += 1
            if is_row:
                seeded_row_count += 1
                
            if is_parent or is_preview or is_row:
                table_id = meta.get("table_id")
                if table_id:
                    table_id_str = str(table_id)
                    table_ids.add(table_id_str)
                    existing_best = best_seed_by_table.get(table_id_str)
                    if existing_best is None or _result_strength(hit) > _result_strength(existing_best):
                        best_seed_by_table[table_id_str] = hit
                elif is_parent or is_preview:
                    # CRITICAL: Parent/preview chunk without table_id
                    missing_table_id_count += 1
                    _rag_log(
                        "table.row_expansion.missing_table_id",
                        {
                            "chunk_id": str(hit.chunk_id),
                            "chunk_index": hit.chunk.chunk_index,
                            "upload_id": str(hit.chunk.upload_id),
                            "is_parent": is_parent,
                            "is_preview": is_preview,
                            "metadata_keys": list(meta.keys()),
                        },
                        indent=2,
                        context={"business": business_profile.id if business_profile else None},
                    )
        
        if not table_ids:
            # No parent/preview chunks found, or all missing table_id
            if parent_count or preview_count:
                logger.warning(
                    "table.row_expansion.no_table_ids business=%s parent_count=%s preview_count=%s missing_table_id=%s",
                    business_profile.id if business_profile else None,
                    parent_count,
                    preview_count,
                    missing_table_id_count,
                )
            return tuple()
        
        # Log discovery phase
        _rag_log(
            "table.row_expansion.discovery",
            {
                "input_hits": len(hits),
                "parent_chunks": parent_count,
                "preview_chunks": preview_count,
                "seed_row_chunks": seeded_row_count,
                "discovered_tables": len(table_ids),
                "table_ids": list(table_ids)[:5],  # Sample
                "missing_table_id_count": missing_table_id_count,
            },
            indent=2,
            context={"business": business_profile.id if business_profile else None},
        )
        
        # Fetch row chunks for discovered tables (answer extraction).
        # Prefer shard-local rows when the selected summary chunk is shard-scoped.
        row_chunks: list[KnowledgeUploadChunk] = []
        table_shard_hints: dict[str, int] = {}
        for table_id in table_ids:
            seed_hit = best_seed_by_table.get(table_id)
            seed_meta = seed_hit.chunk.metadata if seed_hit and isinstance(seed_hit.chunk.metadata, dict) else {}
            if not isinstance(seed_meta, dict):
                continue
            raw_hint = seed_meta.get("table_row_shard_index")
            try:
                if raw_hint is not None:
                    table_shard_hints[table_id] = int(raw_hint)
            except (TypeError, ValueError):
                continue

        try:
            for table_id in sorted(table_ids):
                per_table_qs = (
                    KnowledgeUploadChunk.objects.filter(
                        upload__business_profile=business_profile,
                        metadata__table_id=table_id,
                        metadata__table_chunk_role="row",
                    )
                    .select_related("upload")
                    .order_by("chunk_index")
                )
                selected_rows: list[KnowledgeUploadChunk] = []
                shard_hint = table_shard_hints.get(table_id)
                if shard_hint is not None:
                    selected_rows.extend(list(per_table_qs.filter(metadata__table_row_shard_index=shard_hint)[:max_rows]))
                    if len(selected_rows) < max_rows:
                        selected_ids = [row.id for row in selected_rows]
                        supplemental_qs = per_table_qs
                        if selected_ids:
                            supplemental_qs = supplemental_qs.exclude(id__in=selected_ids)
                        selected_rows.extend(list(supplemental_qs[: max_rows - len(selected_rows)]))
                else:
                    selected_rows.extend(list(per_table_qs[:max_rows]))
                row_chunks.extend(selected_rows)
        except Exception as exc:
            # CRITICAL: Query failed
            logger.error(
                "table.row_expansion.query_failed business=%s table_ids=%s error=%s",
                business_profile.id if business_profile else None,
                list(table_ids)[:5],
                str(exc)[:300],
            )
            return tuple()
        
        # Log query results
        row_chunk_count = len(row_chunks)
        if row_chunk_count == 0:
            # CRITICAL: No row chunks found for discovered tables
            logger.warning(
                "table.row_expansion.no_rows_found business=%s table_count=%s table_ids=%s max_rows=%s",
                business_profile.id if business_profile else None,
                len(table_ids),
                list(table_ids),
                max_rows,
            )
            # Sample query for debugging: check if ANY chunks exist for these tables
            try:
                any_chunks = (
                    KnowledgeUploadChunk.objects.filter(
                        upload__business_profile=business_profile,
                        metadata__table_id__in=list(table_ids),
                    )
                    .values_list("id", "metadata__table_chunk_role")[:5]
                )
                logger.warning(
                    "table.row_expansion.debug_sample business=%s sample_chunks=%s",
                    business_profile.id if business_profile else None,
                    list(any_chunks),
                )
            except Exception:
                pass
        
        normalized_query_tokens = tuple(
            token.lower()
            for token in (query_tokens or ())
            if isinstance(token, str) and token.strip()
        )

        expanded: list[ChunkResult] = []
        duplicate_count = 0
        for chunk in row_chunks:
            if chunk.id in hit_ids:
                duplicate_count += 1
                continue  # Already in results
            row_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            row_table_id = str(row_meta.get("table_id") or "").strip()
            seed_hit = best_seed_by_table.get(row_table_id) if row_table_id else None
            seed_meta = seed_hit.chunk.metadata if seed_hit and isinstance(seed_hit.chunk.metadata, dict) else {}
            parent_shard = seed_meta.get("table_row_shard_index") if isinstance(seed_meta, dict) else None
            row_shard = row_meta.get("table_row_shard_index") if isinstance(row_meta, dict) else None

            lexical_score = 0.0
            if normalized_query_tokens:
                lexical_score = self._lexical_overlap_score(chunk, normalized_query_tokens)

            base_lexical = float(seed_hit.lexical_score) if seed_hit is not None else 0.0
            base_alias = float(seed_hit.alias_confidence) if seed_hit is not None else 0.0
            base_recency = float(seed_hit.recency_score) if seed_hit is not None else 0.0
            base_rerank = float(seed_hit.rerank_score) if seed_hit is not None else 0.0
            vector_distance = seed_hit.vector_distance if seed_hit is not None else None
            row_diagnostics = dict(seed_hit.diagnostics) if seed_hit is not None else {}
            if seed_hit is not None:
                row_diagnostics["expanded_from_chunk_id"] = str(seed_hit.chunk_id)
                row_diagnostics["expanded_from_stage"] = str(seed_hit.source_stage)
            row_diagnostics["expanded_table_id"] = row_table_id
            if parent_shard is not None:
                row_diagnostics["expanded_parent_shard"] = parent_shard
            if row_shard is not None:
                row_diagnostics["expanded_row_shard"] = row_shard

            # Expanded rows should earn their own relevance score.
            # Scale inherited weight by lexical overlap fraction (0.0–1.0)
            # so rows matching 4/4 query tokens inherit full weight (0.3),
            # rows matching 1/4 inherit ~0.075, and rows matching 0/4 get
            # a near-zero baseline (0.05).  This prevents unrelated table
            # rows from receiving inflated scores from a parent that
            # happened to be nearby in embedding space.
            inherited_weight = 0.3
            effective_weight = max(0.05, inherited_weight * lexical_score) if lexical_score > 0 else 0.05
            expanded.append(
                ChunkResult(
                    chunk=chunk,
                    source_stage="table_row_expansion",
                    vector_distance=vector_distance,
                    lexical_score=max(base_lexical * effective_weight, lexical_score),
                    alias_confidence=base_alias * effective_weight,
                    recency_score=base_recency * effective_weight,
                    rerank_score=max(base_rerank * effective_weight, lexical_score),
                    diagnostics=row_diagnostics,
                )
            )
        
        # Log expansion results
        _rag_log(
            "table.row_expansion.result",
            {
                "discovered_tables": len(table_ids),
                "tables_with_shard_hints": len(table_shard_hints),
                "row_chunks_found": row_chunk_count,
                "expanded_rows": len(expanded),
                "duplicates_skipped": duplicate_count,
            },
            indent=2,
            context={"business": business_profile.id if business_profile else None},
        )
        
        return tuple(expanded)
