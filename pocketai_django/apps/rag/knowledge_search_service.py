from __future__ import annotations

from collections import OrderedDict

from django.conf import settings

import logging
import uuid
from typing import Mapping

from apps.knowledge.models import (
    KnowledgeUploadChunk,
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
from apps.rag.query_signals import QuerySignalMixin
from apps.rag.contracts import (
    MAX_INLINE_KNOWLEDGE_CHARS,
)
from apps.rag.intent_fallback import IntentFallbackService
from apps.rag.tenant_lexicon import TenantLexiconService
from apps.rag.retrieval_strategies import StrategyRouter
from apps.rag.search_cache import KnowledgeSearchCacheMixin
from apps.rag.search_config import SearchConfigMixin
from apps.rag.search_pipeline import SearchPipelineMixin
from apps.rag.table_context import TableContextMixin


logger = logging.getLogger(__name__)

# NOTE:
# Import this service through apps.rag.knowledge_search. That module is the
# stable public surface for callers while the implementation stays modular.


class KnowledgeSearchService(
    SearchPipelineMixin,
    RerankingMixin,
    AliasRetrievalMixin,
    SearchConfigMixin,
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
