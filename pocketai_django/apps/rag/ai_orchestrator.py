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
from enum import Enum
from types import SimpleNamespace
import re
import unicodedata
from contextvars import ContextVar
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence
from zoneinfo import ZoneInfo

from apps.rag.text_utils import is_plural_candidate, singularize

from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.accounts.models import (
    AgentProfile,
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
from apps.conversations.models import (
    Conversation,
    ConversationExtraction,
    ConversationExtractionType,
    ConversationSender,
    ConversationMessage
)
from apps.llm.ai_prompt_builder import PromptBuilder, PromptBundle
from apps.rag.embeddings import build_embedding_service, EmbeddingProviderError
from apps.accounts.feature_flags import FeatureFlagService, FeatureState
from apps.knowledge.knowledge_access import (
    CUSTOMER_VISIBILITY_POLICY_KEY,
    apply_customer_visible_chunks,
    apply_customer_visible_uploads,
)
from apps.llm.llm_provider import BaseLLMProvider, PromptGenerationError
from apps.rag.quality_monitor import QualityMonitor
from apps.rag.rag_logging import rag_log
from apps.rag.table_semantics import normalize_column_name
from apps.rag.query_classifier import QueryClassifier, QueryClassification, QueryIntent
from apps.rag.intent_fallback import IntentFallbackService
from apps.rag.tenant_lexicon import TenantLexiconService
from apps.rag.retrieval_strategies import StrategyRouter, RetrievalContext, RetrievalHints
from apps.conversations.response_blocks import normalize_response_blocks
from core.metrics import latency_monitor
from core.tenancy import tenant_context
from core.otel import otel_trace


logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

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


def _rag_log(
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)

# NOTE (legacy orchestrator):
# This module implements the original ledger-based orchestrator used before the
# MCP-style tool-calling path was introduced. The MCP orchestrator lives in
# apps.mcp.orchestrator.McpOrchestratorService and is now the primary path for new
# traffic. This module is retained for backward compatibility and as a fallback
# whenever MCP is disabled at the environment or business level.


class ActionType(str, Enum):
    READ_KNOWLEDGE = "read_knowledge"


@dataclasses.dataclass(frozen=True)
class ActionDescriptor:
    key: ActionType
    label: str
    description: str
    default_enabled: bool = True


ACTION_REGISTRY: dict[ActionType, ActionDescriptor] = {
    ActionType.READ_KNOWLEDGE: ActionDescriptor(
        key=ActionType.READ_KNOWLEDGE,
        label="Read Knowledge Document",
        description="Request the full content of one or more knowledge uploads by ID (payload.knowledge_ids[]).",
    ),
}


@dataclasses.dataclass(frozen=True)
class KnowledgeSnippet:
    id: uuid.UUID
    title: str
    summary: str
    source: str
    content: str | None = None
    content_mode: str | None = None
    public_label: str | None = None
    structured_tables: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    issues: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    page_summaries: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    read_state: str = "summary"
    topic_hints: Sequence[str] = dataclasses.field(default_factory=tuple)
    is_pinned: bool = False
    supplemental_sections: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)

    # NEW: carry chunk identity alongside upload
    upload_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    chunk_index: int | None = None
    entity_type: str | None = None
    entity_name: str | None = None
    entity_business: str | None = None
    is_table_chunk: bool = False
    table_id: str | None = None
    evidence_group_id: str | None = None
    evidence_type: str | None = None
    representation: str | None = None
    aliases: Sequence[str] = dataclasses.field(default_factory=tuple)
    search_stage: str | None = None
    confidence_score: float | None = None
    truncated: bool = False
    source_diagnostics: Mapping[str, object] = dataclasses.field(default_factory=dict)
    partial_index: bool = False
    structured_table_count: int = 0
    issue_count: int = 0
    structured_table_hint: str | None = None
    page_number: int | None = None
    page_mode: str | None = None


@dataclasses.dataclass(frozen=True)
class KnowledgeSearchResult:
    snippets: tuple[KnowledgeSnippet, ...]
    status: str
    diagnostics: Mapping[str, object] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class QueryTraits:
    original: str
    normalized: str
    tokens: tuple[str, ...]
    alias_candidates: tuple[str, ...]
    token_count: int
    has_digits: bool
    has_dashes: bool
    has_underscores: bool
    is_identifier_like: bool


@dataclasses.dataclass(frozen=True)
class AliasSearchResult:
    hits: tuple["ChunkResult", ...]
    diagnostics: Mapping[str, object] = dataclasses.field(default_factory=dict)
    short_circuit: bool = False


@dataclasses.dataclass(frozen=True)
class HybridSearchResult:
    hits: tuple["ChunkResult", ...]
    query_vector: list[float] | None
    diagnostics: Mapping[str, object] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ChunkResult:
    chunk: KnowledgeUploadChunk
    source_stage: str
    vector_distance: float | None = None
    lexical_score: float = 0.0
    alias_confidence: float = 0.0
    recency_score: float = 0.0
    rerank_score: float = 0.0
    diagnostics: dict[str, object] = dataclasses.field(default_factory=dict)

    @property
    def chunk_id(self) -> uuid.UUID:
        return self.chunk.id


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


@dataclasses.dataclass(frozen=True)
class PlannedAction:
    action: ActionType
    payload: dict


@dataclasses.dataclass(frozen=True)
class ExtractionPlan:
    extraction_type: ConversationExtractionType
    payload: dict


@dataclasses.dataclass(frozen=True)
class AiOrchestratorPlan:
    response_text: str
    citations: Sequence[KnowledgeSnippet]
    planned_actions: Sequence[PlannedAction]
    extractions: Sequence[ExtractionPlan]
    diagnostics: dict
    ingestion_warnings: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    response_blocks: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)


@dataclasses.dataclass(frozen=True)
class StreamingTurnContext:
    conversation: Conversation
    response_text: str
    planned_actions: Sequence[PlannedAction]
    extractions: Sequence[ExtractionPlan]
    resolved_citations: Sequence[KnowledgeSnippet]
    knowledge_payload: Sequence[Mapping[str, object]]
    knowledge_reads: Sequence[Mapping[str, object]]
    knowledge_status: str | None
    knowledge_diagnostics: Mapping[str, object]
    knowledge_loading: bool
    placeholder_response: str | None
    prompt_bundle: PromptBundle | None
    tool_trace: Sequence[Mapping[str, object]]
    cached_snippet_count: int
    llm_source: str
    streamed_chunks: Sequence[str]
    llm_usage: Mapping[str, object] | None = None
    plan: AiOrchestratorPlan | None = None
    tool_context: object | None = None
    response_blocks: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)


@dataclasses.dataclass(frozen=True)
class ActionExecutionResult:
    action: ActionType
    status: str
    metadata: dict
    error: str | None = None


class ActionExecutionError(Exception):
    """Raised when an action cannot be executed."""


MAX_INLINE_KNOWLEDGE_CHARS = 12000
KNOWLEDGE_READ_STATE_SUMMARY = "summary"
KNOWLEDGE_READ_STATE_PREVIEW = "preview"
KNOWLEDGE_READ_STATE_FULL = "full"
RECENT_SNIPPET_TURN_WINDOW = 4
LEDGER_LOG_LIMIT = 8
SCOPE_CATEGORY_MAX_DEFAULT = 40
SCOPE_TOP_CATEGORY_MAX_DEFAULT = 4
# Global topic buckets must stay domain-agnostic for multi-tenant use.
TOPIC_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "pricing": ("fee", "fees", "charge", "charges", "price", "prices", "cost", "costs", "pricing", "quote", "quotes"),
    "limits": ("limit", "limits", "cap", "caps", "maximum", "max", "ceiling", "quota", "threshold"),
    "benefits": ("benefit", "benefits", "perk", "perks", "feature", "features", "advantage", "advantages"),
    "eligibility": ("eligibility", "eligible", "qualify", "qualification", "qualifications", "requirement", "requirements", "criteria"),
    "documents": ("document", "documents", "paperwork", "proof", "attachment", "attachments", "id", "identification"),
    "timeline": ("timeline", "processing time", "turnaround", "how long", "timeframe", "sla"),
    "support": ("support", "contact", "phone", "email", "help desk", "representative"),
    "metrics": ("metric", "metrics", "rate", "rates", "percentage", "percent", "ratio", "score", "scores"),
    "restrictions": ("restriction", "restrictions", "blackout", "exclusion", "not covered", "blocked"),
}


class KnowledgeSearchService:
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

    @staticmethod
    def _alias_cache_version_key(business_id: uuid.UUID) -> str:
        return f"rag:alias:ver:{business_id}"

    @staticmethod
    def _alias_cache_key(business_id: uuid.UUID, alias_value: str, version: int) -> str:
        return f"rag:alias:{business_id}:{version}:{alias_value}"

    @staticmethod
    def _query_cache_version_key(business_id: uuid.UUID) -> str:
        return f"rag:qvec:ver:{business_id}"

    @classmethod
    def invalidate_alias_cache(cls, business_id: uuid.UUID) -> None:
        version_key = cls._alias_cache_version_key(business_id)
        try:
            cache.incr(version_key)
        except ValueError:
            cache.set(version_key, 1, None)

    @classmethod
    def invalidate_query_cache(cls, business_id: uuid.UUID) -> None:
        version_key = cls._query_cache_version_key(business_id)
        try:
            cache.incr(version_key)
        except ValueError:
            cache.set(version_key, 1, None)

    def _get_alias_cache_version(self, business_id: uuid.UUID) -> int:
        version_key = self._alias_cache_version_key(business_id)
        version = cache.get(version_key)
        if version is None:
            cache.set(version_key, 0, None)
            return 0
        return int(version)

    def _get_query_cache_version(self, business_id: uuid.UUID) -> int:
        version_key = self._query_cache_version_key(business_id)
        version = cache.get(version_key)
        if version is None:
            cache.set(version_key, 0, None)
            return 0
        return int(version)

    @staticmethod
    def _result_cache_version_key(business_id: uuid.UUID) -> str:
        return f"rag:result:ver:{business_id}"

    @classmethod
    def invalidate_result_cache(cls, business_id: uuid.UUID) -> None:
        version_key = cls._result_cache_version_key(business_id)
        try:
            cache.incr(version_key)
        except ValueError:
            cache.set(version_key, 1, None)

    def _get_result_cache_version(self, business_id: uuid.UUID) -> int:
        version_key = self._result_cache_version_key(business_id)
        version = cache.get(version_key)
        if version is None:
            cache.set(version_key, 0, None)
            return 0
        return int(version)

    @staticmethod
    def _serialize_snippet_for_cache(snippet: KnowledgeSnippet) -> dict[str, object]:
        payload = dataclasses.asdict(snippet)
        for key, value in list(payload.items()):
            if isinstance(value, uuid.UUID):
                payload[key] = str(value)
            elif isinstance(value, tuple):
                payload[key] = list(value)
        return payload

    @staticmethod
    def _deserialize_snippet_from_cache(payload: Mapping[str, Any]) -> KnowledgeSnippet | None:
        try:
            return KnowledgeSnippet(
                id=uuid.UUID(str(payload.get("id"))),
                title=str(payload.get("title") or ""),
                summary=str(payload.get("summary") or ""),
                source=str(payload.get("source") or ""),
                content=payload.get("content"),
                content_mode=payload.get("content_mode"),
                public_label=payload.get("public_label"),
                structured_tables=tuple(payload.get("structured_tables") or ()),
                issues=tuple(payload.get("issues") or ()),
                page_summaries=tuple(payload.get("page_summaries") or ()),
                read_state=str(payload.get("read_state") or KNOWLEDGE_READ_STATE_SUMMARY),
                topic_hints=tuple(payload.get("topic_hints") or ()),
                is_pinned=bool(payload.get("is_pinned") or False),
                supplemental_sections=tuple(payload.get("supplemental_sections") or ()),
                upload_id=uuid.UUID(str(payload["upload_id"])) if payload.get("upload_id") else None,
                chunk_id=uuid.UUID(str(payload["chunk_id"])) if payload.get("chunk_id") else None,
                chunk_index=int(payload.get("chunk_index")) if payload.get("chunk_index") is not None else None,
                entity_type=payload.get("entity_type"),
                entity_name=payload.get("entity_name"),
                entity_business=payload.get("entity_business"),
                is_table_chunk=bool(payload.get("is_table_chunk") or False),
                table_id=str(payload.get("table_id") or "").strip() or None,
                evidence_group_id=str(payload.get("evidence_group_id") or "").strip() or None,
                evidence_type=str(payload.get("evidence_type") or "").strip() or None,
                representation=str(payload.get("representation") or "").strip().lower() or None,
                aliases=tuple(payload.get("aliases") or ()),
                search_stage=payload.get("search_stage"),
                confidence_score=float(payload["confidence_score"]) if payload.get("confidence_score") is not None else None,
                truncated=bool(payload.get("truncated") or False),
                source_diagnostics=payload.get("source_diagnostics") or {},
                partial_index=bool(payload.get("partial_index") or False),
                structured_table_count=int(payload.get("structured_table_count") or 0),
                issue_count=int(payload.get("issue_count") or 0),
                structured_table_hint=payload.get("structured_table_hint"),
                page_number=int(payload.get("page_number")) if payload.get("page_number") is not None else None,
                page_mode=payload.get("page_mode"),
            )
        except Exception:
            return None

    @staticmethod
    def _upload_scope_token(
        allowed_upload_ids: Sequence[uuid.UUID] | None,
        *,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> str:
        """Stable scope token used to key per-scope caches."""
        if allowed_upload_ids is None and not allowed_explicit_upload_ids:
            return "all"
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return "none"
            unique = sorted({str(value) for value in allowed_upload_ids if value})
            digest = hashlib.sha256("|".join(unique).encode("utf-8")).hexdigest()[:16]
            return f"u{len(unique)}:{digest}"

        explicit_ids = sorted({str(value) for value in (allowed_explicit_upload_ids or ()) if value})
        if not explicit_ids:
            return "all"
        fingerprint = "explicit:" + ",".join(explicit_ids)
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
        return f"d{len(explicit_ids)}:{digest}"

    def _result_cache_key(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        limit: int,
        alias_result: AliasSearchResult | None,
        table_context: Mapping[str, object],
        feature_state: FeatureState,
        identifier_filter: Mapping[str, str] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> str:
        version = self._get_result_cache_version(business_profile.id)
        qvec_version = self._get_query_cache_version(business_profile.id)
        model_name = getattr(self.embedding_service, "model", "local")
        backend = str(getattr(settings, "RAG_SEARCH_BACKEND", "postgres") or "postgres").strip().lower()
        azure_index = str(getattr(settings, "AZURE_SEARCH_INDEX_NAME", "") or "")
        azure_semantic = bool(getattr(settings, "AZURE_SEARCH_SEMANTIC_ENABLED", False))
        azure_semantic_config = str(getattr(settings, "AZURE_SEARCH_SEMANTIC_CONFIG", "") or "")
        alias_stage = ""
        if alias_result and alias_result.diagnostics:
            alias_stage = str(alias_result.diagnostics.get("stage") or "")
        classification = table_context.get("query_classification")
        intent_name = "none"
        intent_source = "none"
        intent_clarification = "0"
        if isinstance(classification, QueryClassification):
            intent_name = classification.intent.value
            intent_source = str(classification.source or "heuristic")
            intent_clarification = "1" if classification.requires_clarification else "0"
        normalized_query = (traits.normalized or traits.original or "").strip().lower()
        scope_token = self._upload_scope_token(
            allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        fingerprint = "|".join(
            [
                str(business_profile.id),
                scope_token,
                str(version),
                str(qvec_version),
                CUSTOMER_VISIBILITY_POLICY_KEY,
                model_name,
                f"backend:{backend}",
                f"azure_index:{azure_index}",
                f"azure_semantic:{int(azure_semantic)}",
                f"azure_semantic_config:{azure_semantic_config}",
                "evidence_grouping:v3",
                "section" if table_context.get("prefer_section_context") else "chunk",
                ",".join(str(term) for term in (table_context.get("section_focus_terms") or ())[:4]),
                normalized_query,
                str(limit),
                "table" if table_context.get("has_intent") else "chunk",
                "comprehensive" if table_context.get("comprehensive_intent") else "specific",
                f"intent:{intent_name}",
                f"intent_source:{intent_source}",
                f"intent_clarify:{intent_clarification}",
                "hybrid" if feature_state.hybrid_search else "lexical_only",
                "alias_on" if feature_state.alias_lookup else "alias_off",
                "alias_short" if alias_result and alias_result.short_circuit else "alias_none",
                alias_stage,
                str(identifier_filter or {}),
            ]
        )
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
        return f"rag:result:{digest}"

    def _result_cache_get(self, cache_key: str) -> KnowledgeSearchResult | None:
        if not self.result_cache_enabled:
            return None
        cached = cache.get(cache_key)
        if not isinstance(cached, Mapping):
            return None
        snippets_raw = cached.get("snippets") or []
        snippets: list[KnowledgeSnippet] = []
        for item in snippets_raw:
            if not isinstance(item, Mapping):
                continue
            resolved = self._deserialize_snippet_from_cache(item)
            if resolved:
                snippets.append(resolved)
        status = str(cached.get("status") or "ok")
        diagnostics = cached.get("diagnostics") or {}
        limit_hint = cached.get("limit")
        if isinstance(limit_hint, int):
            snippets = snippets[: max(1, limit_hint)]
        return KnowledgeSearchResult(snippets=tuple(snippets), status=status, diagnostics=diagnostics)

    def _result_cache_set(self, cache_key: str, result: KnowledgeSearchResult, *, limit: int) -> None:
        if not self.result_cache_enabled:
            return
        diag = dict(result.diagnostics or {})
        diag.pop("request_id", None)
        diag.pop("total_duration_ms", None)
        payload = {
            "status": result.status,
            "diagnostics": diag,
            "snippets": [self._serialize_snippet_for_cache(s) for s in result.snippets],
            "limit": limit,
        }
        cache.set(cache_key, payload, timeout=self.result_cache_ttl)

    def _session_cache_get(
        self,
        cache_dict: MutableMapping[str, object],
        cache_key: str,
    ) -> KnowledgeSearchResult | None:
        if not cache_dict:
            return None
        cached = cache_dict.get(cache_key)
        if not isinstance(cached, Mapping):
            return None
        snippets_raw = cached.get("snippets") or []
        snippets: list[KnowledgeSnippet] = []
        for item in snippets_raw:
            if not isinstance(item, Mapping):
                continue
            resolved = self._deserialize_snippet_from_cache(item)
            if resolved:
                snippets.append(resolved)
        if not snippets and cached.get("status") == "ok":
            return None
        status = str(cached.get("status") or "ok")
        diagnostics = cached.get("diagnostics") or {}
        limit_hint = cached.get("limit")
        if isinstance(limit_hint, int):
            snippets = snippets[: max(1, limit_hint)]
        return KnowledgeSearchResult(snippets=tuple(snippets), status=status, diagnostics=diagnostics)

    def _session_cache_set(
        self,
        cache_dict: MutableMapping[str, object] | None,
        cache_key: str,
        result: KnowledgeSearchResult,
        *,
        limit: int,
    ) -> None:
        if cache_dict is None:
            return
        diag = dict(result.diagnostics or {})
        diag.pop("request_id", None)
        diag.pop("total_duration_ms", None)
        cache_dict[cache_key] = {
            "status": result.status,
            "diagnostics": diag,
            "snippets": [self._serialize_snippet_for_cache(s) for s in result.snippets],
            "limit": limit,
        }
        while len(cache_dict) > self.session_cache_limit:
            oldest_key = next(iter(cache_dict))
            cache_dict.pop(oldest_key, None)

    def _cache_alias_payload(self, business_id: uuid.UUID, alias_value: str, payload: Sequence[Mapping[str, str]]) -> None:
        version = self._get_alias_cache_version(business_id)
        key = self._alias_cache_key(business_id, alias_value, version)
        cache.set(key, list(payload), timeout=self.alias_cache_ttl)

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
                import math

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

    def _rerank_candidates(
        self,
        candidates: Sequence[ChunkResult],
        query_vector: list[float] | None,
        *,
        traits: QueryTraits,
        feature_state: FeatureState | None = None,
        table_context: Mapping[str, object] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        if not candidates:
            return (
                [],
                0,
                {
                    "table_residual_rescue_applied": False,
                    "table_residual_rescue_count": 0,
                    "table_residual_rescue_reason": "no_candidates",
                },
            )
        start = time.perf_counter()
        rerank_diag: dict[str, object] = {
            "table_residual_rescue_applied": False,
            "table_residual_rescue_count": 0,
            "table_residual_rescue_reason": None,
        }
        continuity_allowed_for_rerank = False
        continuity_reason_for_rerank = "document_continuity_removed"
        rerank_diag.update(
            {
                "document_continuity_allowed": continuity_allowed_for_rerank,
                "document_continuity_reason": continuity_reason_for_rerank or None,
                "document_continuity_primary_present": False,
                "document_continuity_boosted_candidates": 0,
                "document_continuity_max_bonus": 0.0,
            }
        )
        top_pool = min(len(candidates), self.rerank_pool)
        scored: list[tuple[float, int, ChunkResult]] = []
        tail: list[ChunkResult] = []
        text_penalty_enabled = bool(feature_state and feature_state.rag_text_chunk_penalty)
        table_context = table_context or {}
        table_intent = bool(table_context.get("has_intent"))
        modality_bias = str(table_context.get("modality_bias") or "").strip().lower()
        if modality_bias not in {"table", "text", "mixed"}:
            modality_bias = "table" if table_intent else "text"
        prefer_section_context = bool(table_context.get("prefer_section_context"))
        section_focus_terms = tuple(
            str(item).strip().lower()
            for item in (table_context.get("section_focus_terms") or ())
            if str(item).strip()
        )
        query_tokens = set(table_context.get("query_tokens") or ())
        specific_tokens = set(table_context.get("specific_tokens") or ())
        document_name_tokens = self._document_name_relevance_tokens(traits)
        rerank_diag["document_name_boost_token_count"] = len(document_name_tokens)
        for idx, cand in enumerate(candidates):
            if idx >= top_pool:
                tail.append(cand)
                continue
            vector_score = 0.0
            if query_vector:
                if cand.vector_distance is not None:
                    try:
                        vector_score = 1.0 - float(cand.vector_distance)
                    except (TypeError, ValueError):
                        vector_score = 0.0
                else:
                    vector_score = self._cosine_similarity(query_vector, self._chunk_embedding(cand))
            lexical_score = cand.lexical_score or self._lexical_overlap_score(cand.chunk, traits.tokens)
            entity_bonus = self._entity_bonus(cand.chunk, traits.tokens, traits.normalized)
            alias_bonus = max(cand.alias_confidence, self._alias_bonus(cand.chunk, traits.tokens, traits.normalized))
            recency_score = cand.recency_score or self._recency_score(cand.chunk.upload)

            # NEW: Quality-based penalty for decorative tables (Phase 2.1 + 3.2)
            quality_penalty = 0.0
            chunk_metadata = cand.chunk.metadata if isinstance(cand.chunk.metadata, dict) else {}
            table_header_bonus = 0.0
            modality_bias_bonus = 0.0
            table_specific_penalty = 0.0
            table_residual_penalty = 0.0
            table_structural_penalty = 0.0
            is_table_residual = bool(
                chunk_metadata.get("table_residual")
                or chunk_metadata.get("content_source") == "table_residual"
                or chunk_metadata.get("region_role") == "table_residual"
            )
            
            if chunk_metadata.get("is_table_chunk"):
                # Get quality score from metadata (0.0 = garbage, 1.0 = high quality)
                quality_score_raw = chunk_metadata.get("table_quality_score")
                is_decorative = chunk_metadata.get("table_is_decorative", False)
                
                # FIXED: Defensive type checking to prevent crashes
                quality_score = None
                try:
                    if quality_score_raw is not None:
                        quality_score = float(quality_score_raw)
                        # Clamp to valid range [0.0, 1.0] and handle NaN/inf
                        import math
                        if not math.isfinite(quality_score):
                            quality_score = None
                        elif not (0.0 <= quality_score <= 1.0):
                            quality_score = max(0.0, min(1.0, quality_score))
                except (TypeError, ValueError):
                    quality_score = None
                
                if quality_score is not None and quality_score < self.table_quality_threshold:
                    # Apply a smooth linear penalty below the configured threshold.
                    # Penalty ranges from 0% (quality=threshold) to 50% (quality=0.0).
                    quality_gap = self.table_quality_threshold - quality_score
                    quality_penalty = (quality_gap / self.table_quality_threshold) * 0.5
                elif is_decorative:
                    # Fallback: if is_decorative flag is set, apply moderate penalty
                    quality_penalty = 0.30
                if table_intent:
                    match_info = self._table_chunk_match_info(
                        cand.chunk,
                        query_tokens=query_tokens,
                        specific_tokens=specific_tokens,
                    )
                    cand.diagnostics.update(match_info)
                    structural_info = self._table_structural_row_info(cand.chunk)
                    cand.diagnostics.update(structural_info)
                    numeric_table_intent = bool(table_context.get("numeric_intent"))
                    if match_info.get("header_match") and not structural_info.get("structural_row"):
                        table_header_bonus = self.table_header_match_bonus
                    if specific_tokens and not match_info.get("specific_match"):
                        table_specific_penalty = self.table_specific_miss_penalty
                    if structural_info.get("structural_row") and (specific_tokens or numeric_table_intent):
                        table_structural_penalty = min(
                            0.3,
                            max(self.table_header_match_bonus + 0.12, self.table_specific_miss_penalty * 0.8),
                        )
                if modality_bias == "table":
                    modality_bias_bonus = 0.04
                elif modality_bias == "mixed" and table_intent:
                    modality_bias_bonus = 0.02

            text_penalty = 0.0
            section_context_boost = 0.0
            if text_penalty_enabled and not chunk_metadata.get("is_table_chunk") and not chunk_metadata.get("is_dataset_card"):
                index_type = chunk_metadata.get("index_type")
                if index_type in (None, "text"):
                    text_penalty = self._text_quality_penalty(chunk_metadata)
            if prefer_section_context and not chunk_metadata.get("is_table_chunk") and not chunk_metadata.get("is_dataset_card"):
                index_type = chunk_metadata.get("index_type")
                if index_type in (None, "text"):
                    section_context_boost = self._section_context_boost(
                        chunk_metadata,
                        query_tokens=traits.tokens,
                        query_text=traits.normalized or traits.original or "",
                        focus_terms=section_focus_terms,
                    )
            if is_table_residual and not chunk_metadata.get("is_table_chunk"):
                if table_intent:
                    table_residual_penalty = self.table_residual_table_intent_penalty
                else:
                    table_residual_penalty = self.table_residual_penalty
            text_phrase_boost = 0.0
            text_proximity_boost = 0.0
            if not chunk_metadata.get("is_table_chunk"):
                if modality_bias == "text":
                    modality_bias_bonus = 0.04
                elif modality_bias == "mixed" and not table_intent:
                    modality_bias_bonus = 0.02
                text_phrase_boost = self._exact_phrase_boost(
                    cand.chunk.content or "",
                    traits.normalized or traits.original or "",
                    max_boost=0.32,
                )
                text_proximity_boost = self._token_proximity_boost(
                    cand.chunk.content or "",
                    traits.tokens,
                    max_boost=0.18,
                )

            # Document-name relevance: boost chunks from documents whose
            # display_name closely matches the query.  This is the standard IR
            # "title field boost" — a document literally named "Fees and Charges
            # Credit Cards" should rank higher for "credit card issuance fees"
            # than one named "Cheques-EN".  Works across any industry.
            document_name_boost = 0.0
            upload = getattr(cand.chunk, "upload", None)
            if upload is not None:
                doc_label = (
                    getattr(upload, "display_name", "") or
                    getattr(upload, "source_name", "") or ""
                )
                if doc_label and document_name_tokens:
                    document_name_boost = self._lexical_score_text(doc_label, document_name_tokens)

            # Document continuity boost has been removed. The previous document
            # must not influence ranking unless the user/tool explicitly scopes
            # the search elsewhere.
            document_continuity_bonus = 0.0

            combined = (
                self.rerank_weights["vector"] * vector_score
                + self.rerank_weights["lexical"] * lexical_score
                + self.rerank_weights["alias"] * alias_bonus
                + self.rerank_weights["entity"] * entity_bonus
                + self.rerank_weights["recency"] * recency_score
                + table_header_bonus
                + self.rerank_weights["document_name"] * document_name_boost
                + document_continuity_bonus
                + section_context_boost
                + modality_bias_bonus
                + text_phrase_boost
                + text_proximity_boost
                - quality_penalty  # NEW: Subtract quality penalty
                - table_specific_penalty
                - table_structural_penalty
                - text_penalty
                - table_residual_penalty
            )
            cand.diagnostics["score_breakdown"] = {
                "vector": round(vector_score, 4),
                "lexical": round(lexical_score, 4),
                "alias": round(alias_bonus, 4),
                "entity": round(entity_bonus, 4),
                "recency": round(recency_score, 4),
                "table_header_bonus": round(table_header_bonus, 4),
                "document_name_boost": round(document_name_boost, 4),
                "document_name_token_count": len(document_name_tokens),
                "document_continuity_bonus": round(document_continuity_bonus, 4),
                "section_context_boost": round(section_context_boost, 4),
                "modality_bias_bonus": round(modality_bias_bonus, 4),
                "quality_penalty": round(quality_penalty, 4),  # NEW: Include in diagnostics
                "table_specific_penalty": round(table_specific_penalty, 4),
                "table_structural_penalty": round(table_structural_penalty, 4),
                "text_penalty": round(text_penalty, 4),
                "table_residual_penalty": round(table_residual_penalty, 4),
                "text_phrase_boost": round(text_phrase_boost, 4),
                "text_proximity_boost": round(text_proximity_boost, 4),
                "table_residual_rescue_bonus": 0.0,
            }
            cand.diagnostics["table_residual_candidate"] = bool(is_table_residual and not chunk_metadata.get("is_table_chunk"))
            cand.rerank_score = combined
            scored.append((combined, -idx, cand))
        comprehensive_intent = bool(table_context.get("comprehensive_intent"))
        allow_broad_rescue = bool(table_intent and comprehensive_intent and not specific_tokens)
        if table_intent and self.table_residual_rescue_enabled and scored and (specific_tokens or allow_broad_rescue):
            canonical_candidates = []
            for item in scored:
                metadata = item[2].chunk.metadata if isinstance(item[2].chunk.metadata, dict) else {}
                if metadata.get("is_table_chunk"):
                    canonical_candidates.append(item)
            residual_candidates: list[tuple[int, float, int, ChunkResult, float, float]] = []
            if specific_tokens:
                canonical_specific_hits = sum(
                    1
                    for _, _, hit in canonical_candidates
                    if bool(hit.diagnostics.get("specific_match_strong") or hit.diagnostics.get("specific_match"))
                )
            else:
                canonical_specific_hits = 0
            best_canonical_score = max((score for score, _, _ in canonical_candidates), default=None)

            for scored_index, (base_score, order, hit) in enumerate(scored):
                hit_meta = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
                is_residual = bool(
                    (not hit_meta.get("is_table_chunk"))
                    and (
                        hit_meta.get("table_residual")
                        or hit_meta.get("content_source") == "table_residual"
                        or hit_meta.get("region_role") == "table_residual"
                    )
                )
                is_supporting = bool(
                    (not hit_meta.get("is_table_chunk"))
                    and (
                        hit_meta.get("table_annotation")
                        or hit_meta.get("content_source") == "table_annotation"
                        or hit_meta.get("region_role") == "table_annotation"
                    )
                )
                if not (is_residual or is_supporting):
                    continue
                score_breakdown = hit.diagnostics.get("score_breakdown") or {}
                phrase_boost = float(score_breakdown.get("text_phrase_boost") or 0.0)
                lexical_component = float(score_breakdown.get("lexical") or 0.0)
                phrase_min = self.table_residual_rescue_phrase_min
                if allow_broad_rescue:
                    phrase_min = 0.0
                if phrase_boost < phrase_min:
                    continue
                lexical_min = self.table_residual_rescue_lexical_min
                if allow_broad_rescue:
                    lexical_min = min(lexical_min, 0.45)
                if lexical_component < lexical_min:
                    continue
                residual_candidates.append(
                    (scored_index, base_score, order, hit, phrase_boost, lexical_component)
                )

            if canonical_specific_hits > 0:
                rerank_diag["table_residual_rescue_reason"] = "canonical_specific_match_present"
            elif not residual_candidates:
                rerank_diag["table_residual_rescue_reason"] = "no_eligible_residual"
            else:
                residual_candidates.sort(key=lambda item: (item[4], item[5], item[1]), reverse=True)
                promoted = 0
                for scored_index, base_score, order, hit, phrase_boost, lexical_component in residual_candidates:
                    if promoted >= self.table_residual_rescue_max_results:
                        break
                    boosted = base_score + self.table_residual_rescue_bonus
                    hit.rerank_score = boosted
                    score_breakdown = dict(hit.diagnostics.get("score_breakdown") or {})
                    score_breakdown["table_residual_rescue_bonus"] = round(
                        self.table_residual_rescue_bonus,
                        4,
                    )
                    hit.diagnostics["score_breakdown"] = score_breakdown
                    hit.diagnostics["table_residual_rescue"] = {
                        "applied": True,
                        "phrase_boost": round(phrase_boost, 4),
                        "lexical": round(lexical_component, 4),
                        "bonus": round(self.table_residual_rescue_bonus, 4),
                        "canonical_specific_hits": canonical_specific_hits,
                        "best_canonical_score": round(float(best_canonical_score), 4)
                        if isinstance(best_canonical_score, (int, float))
                        else None,
                    }
                    scored[scored_index] = (boosted, order, hit)
                    promoted += 1
                rerank_diag["table_residual_rescue_applied"] = bool(promoted)
                rerank_diag["table_residual_rescue_count"] = promoted
                rerank_diag["table_residual_rescue_reason"] = (
                    "promoted"
                    if promoted
                    else "residual_candidates_below_cap_or_bonus_zero"
                )
        if rerank_diag.get("table_residual_rescue_reason") is None:
            if not table_intent:
                rerank_diag["table_residual_rescue_reason"] = "not_table_intent"
            elif not specific_tokens:
                rerank_diag["table_residual_rescue_reason"] = "no_specific_tokens"
            elif not self.table_residual_rescue_enabled:
                rerank_diag["table_residual_rescue_reason"] = "disabled"
            else:
                rerank_diag["table_residual_rescue_reason"] = "not_applicable"
        # Sort by score (desc), then by original index (asc via -idx desc) for stable tie-breaking
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        reranked = [item[2] for item in scored]
        if tail:
            reranked.extend(tail)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return reranked, duration_ms, rerank_diag

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
        # Ignore overly short/boilerplate phrases to avoid accidental boosts.
        if len(normalized_query) < 10:
            return 0.0
        if normalized_query in normalized_text:
            return max_boost
        # Fallback: boost if a long n-gram from query is present.
        ngrams: list[str] = []
        max_n = min(4, len(query_terms))
        for n in range(max_n, 1, -1):
            for idx in range(0, len(query_terms) - n + 1):
                phrase = " ".join(query_terms[idx : idx + n]).strip()
                if len(phrase) >= 10:
                    ngrams.append(phrase)
        for phrase in ngrams:
            if phrase in normalized_text:
                # Slightly lower than full phrase match.
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
        best_left_pos = 0
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
                best_left_pos = positions[left][0]
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

    @staticmethod
    def _snippet_representation(snippet: KnowledgeSnippet) -> str:
        raw = str(snippet.representation or "").strip().lower()
        if raw in {"text", "table", "json"}:
            return raw
        if snippet.is_table_chunk:
            return "table"
        if snippet.entity_type:
            return "json"
        return "text"

    @staticmethod
    def _snippet_evidence_group_key(snippet: KnowledgeSnippet) -> str:
        group_id = str(snippet.evidence_group_id or "").strip()
        if group_id:
            return group_id
        if snippet.chunk_id:
            return f"chunk:{snippet.chunk_id}"
        return f"snippet:{snippet.id}"

    @staticmethod
    def _query_prefers_numeric(query_text: str, tokens: tuple[str, ...]) -> bool:
        if any(char.isdigit() for char in query_text or ""):
            return True
        lowered = (query_text or "").lower()
        if any(mark in lowered for mark in ("%", "fee", "fees", "rate", "rates", "price", "prices", "cost", "costs")):
            return True
        return any(token and any(ch.isdigit() for ch in token) for token in tokens or ())

    @staticmethod
    def _evidence_tokens(snippet: KnowledgeSnippet) -> set[str]:
        text = (snippet.content or snippet.summary or "").strip().lower()
        if not text:
            return set()
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", text)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return set()
        return {
            token
            for token in normalized.split(" ")
            if token and (len(token) >= 3 or token.isdigit())
        }

    @staticmethod
    def _evidence_overlap(tokens_a: set[str], tokens_b: set[str]) -> float:
        if not tokens_a or not tokens_b:
            return 0.0
        union = tokens_a | tokens_b
        if not union:
            return 0.0
        return len(tokens_a & tokens_b) / len(union)

    def _select_primary_evidence_snippet(
        self,
        entries: Sequence[tuple[int, KnowledgeSnippet]],
        *,
        query_text: str,
        tokens: tuple[str, ...],
    ) -> tuple[int, KnowledgeSnippet]:
        query_prefers_numeric = self._query_prefers_numeric(query_text, tokens)
        token_set = {
            str(token or "").strip().lower()
            for token in tokens
            if str(token or "").strip()
        }

        best_entry: tuple[int, KnowledgeSnippet] | None = None
        best_score = float("-inf")
        for original_index, snippet in entries:
            representation = self._snippet_representation(snippet)
            base_score = float(snippet.confidence_score or 0.0)
            bonus = 0.0

            if representation == "text":
                bonus += 0.06 if not query_prefers_numeric else 0.03
            elif representation == "table":
                bonus += 0.08 if query_prefers_numeric else -0.01
                diagnostics = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else {}
                try:
                    quality_score = diagnostics.get("table_quality_score")
                    if quality_score is not None:
                        quality_value = float(quality_score)
                        if quality_value < 0.35:
                            bonus -= 0.25
                        elif quality_value < 0.5:
                            bonus -= 0.12
                except (TypeError, ValueError):
                    pass
                if diagnostics.get("table_is_decorative"):
                    bonus -= 0.25
                if diagnostics.get("header_match") or diagnostics.get("specific_match"):
                    bonus += 0.05
            elif representation == "json":
                bonus -= 0.03

            if snippet.truncated:
                bonus -= 0.04

            if token_set:
                evidence_text = (snippet.content or snippet.summary or "").lower()
                matches = sum(1 for token in token_set if token in evidence_text)
                bonus += min(0.12, (matches / max(1, len(token_set))) * 0.12)

            total_score = base_score + bonus
            if total_score > best_score:
                best_score = total_score
                best_entry = (original_index, snippet)
            elif best_entry is not None and total_score == best_score and original_index < best_entry[0]:
                best_entry = (original_index, snippet)

        return best_entry or entries[0]

    def _collapse_snippets_by_evidence_group(
        self,
        snippets: Sequence[KnowledgeSnippet],
        *,
        query_text: str,
        tokens: tuple[str, ...],
        limit: int | None = None,
    ) -> tuple[tuple[KnowledgeSnippet, ...], dict[str, object]]:
        if not snippets:
            return tuple(), {"evidence_groups": 0, "evidence_collapsed": 0, "evidence_conflicts": 0}
        if not self.evidence_grouping_enabled:
            limited = tuple(snippets[:limit]) if isinstance(limit, int) and limit > 0 else tuple(snippets)
            return limited, {"evidence_groups": len(limited), "evidence_collapsed": 0, "evidence_conflicts": 0}

        grouped: OrderedDict[str, list[tuple[int, KnowledgeSnippet]]] = OrderedDict()
        for idx, snippet in enumerate(snippets):
            group_key = self._snippet_evidence_group_key(snippet)
            grouped.setdefault(group_key, []).append((idx, snippet))

        selected_entries: list[tuple[int, KnowledgeSnippet]] = []
        conflict_groups: list[str] = []
        collapsed_count = 0

        for group_key, entries in grouped.items():
            if len(entries) == 1:
                selected_entries.append(entries[0])
                continue

            collapsed_count += len(entries) - 1
            selected_idx, selected = self._select_primary_evidence_snippet(
                entries,
                query_text=query_text,
                tokens=tokens,
            )

            representation_buckets: dict[str, list[KnowledgeSnippet]] = {}
            for _, entry_snippet in entries:
                representation = self._snippet_representation(entry_snippet)
                representation_buckets.setdefault(representation, []).append(entry_snippet)

            has_conflict = False
            if len(representation_buckets) > 1:
                representations = list(representation_buckets.keys())
                for i, left in enumerate(representations):
                    for right in representations[i + 1 :]:
                        left_tokens = self._evidence_tokens(representation_buckets[left][0])
                        right_tokens = self._evidence_tokens(representation_buckets[right][0])
                        if min(len(left_tokens), len(right_tokens)) < 4:
                            continue
                        overlap = self._evidence_overlap(left_tokens, right_tokens)
                        if overlap < self.evidence_conflict_min_overlap:
                            has_conflict = True
                            break
                    if has_conflict:
                        break

            if has_conflict:
                conflict_groups.append(group_key)
                source_diag = dict(selected.source_diagnostics or {})
                source_diag["evidence_conflict"] = True
                source_diag["evidence_group_size"] = len(entries)
                source_diag["evidence_group_id"] = group_key
                selected = dataclasses.replace(selected, source_diagnostics=source_diag)

            selected_entries.append((selected_idx, selected))

        selected_entries.sort(key=lambda item: item[0])
        collapsed = [snippet for _, snippet in selected_entries]
        if isinstance(limit, int) and limit > 0:
            collapsed = collapsed[:limit]

        diagnostics: dict[str, object] = {
            "evidence_groups": len(grouped),
            "evidence_collapsed": collapsed_count,
            "evidence_conflicts": len(conflict_groups),
        }
        if conflict_groups:
            diagnostics["evidence_conflict_groups"] = conflict_groups[:8]
        return tuple(collapsed), diagnostics

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
                # Product-category words are too generic to act as “specific table” anchors.
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

    def _table_header_tokens(self, table_id: str | uuid.UUID | None) -> set[str]:
        if not table_id:
            return set()
        cache_key = str(table_id).strip()
        if not cache_key:
            return set()
        cached = self._table_header_token_cache.get(cache_key)
        if cached is not None:
            self._table_header_token_cache.move_to_end(cache_key)
            return cached
        parsed_table_id: uuid.UUID | None = None
        if isinstance(table_id, uuid.UUID):
            parsed_table_id = table_id
        else:
            try:
                parsed_table_id = uuid.UUID(cache_key)
            except (TypeError, ValueError, AttributeError):
                self._table_header_token_cache[cache_key] = set()
                return set()
        payload = (
            KnowledgeUploadTable.objects.filter(id=parsed_table_id)
            .values("column_schema", "title", "section_heading")
            .first()
        )
        tokens: set[str] = set()
        if payload:
            for value in payload.get("column_schema") or []:
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for value in (payload.get("title"), payload.get("section_heading")):
                if value:
                    normalized = normalize_column_name(str(value))
                    tokens.update(self._table_tokenize(normalized or str(value)))
        self._table_header_token_cache[cache_key] = tokens
        if len(self._table_header_token_cache) > self.table_header_token_cache_limit:
            self._table_header_token_cache.popitem(last=False)
        return tokens

    def _table_generic_tokens_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_generic_token_cache.get(scope_key)
        if cached is not None:
            self._table_generic_token_cache.move_to_end(scope_key)
            return cached
        qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
            upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_generic_token_cache[scope_key] = set()
                return set()
            qs = qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                qs = qs.filter(clause)
        rows = list(
            qs.order_by("-updated_at").values_list(
                "column_schema",
                "title",
                "section_heading",
                "upload__display_name",
                "upload__source_name",
                "upload__external_reference",
            )[: self.table_column_sample_limit]
        )
        total_tables = len(rows)
        if not total_tables:
            self._table_generic_token_cache[scope_key] = set()
            return set()
        required_tables = min(self.table_generic_min_tables, total_tables)
        df: Counter[str] = Counter()
        for column_schema, title, section_heading, display_name, source_name, external_reference in rows:
            tokens: set[str] = set()
            for value in (column_schema or []):
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for value in (title, section_heading, display_name, source_name, external_reference):
                if not value:
                    continue
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for token in tokens:
                if not token or token.isdigit():
                    continue
                if len(token) < self.table_specific_min_length:
                    continue
                df[token] += 1
        generic: set[str] = set()
        if total_tables:
            for token, count in df.items():
                if count < required_tables:
                    continue
                if (count / total_tables) >= self.table_generic_df_threshold:
                    generic.add(token)
        if self.table_generic_topk > 0 and df:
            # Only treat frequently-occurring tokens as “generic”.
            # The previous behavior could swallow rare but critical entity tokens (e.g., a product name)
            # when `table_generic_topk` is large relative to the number of tables.
            for token, count in df.most_common(self.table_generic_topk):
                if count < required_tables:
                    continue
                generic.add(token)
        self._table_generic_token_cache[scope_key] = generic
        if len(self._table_generic_token_cache) > self.table_header_token_cache_limit:
            self._table_generic_token_cache.popitem(last=False)
        return generic

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

    def _table_query_context(
        self,
        business_profile,
        traits: QueryTraits,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> Mapping[str, object]:
        """
        Build table query context with column/profile metadata.
        
        NEW (P0 #3): Checks Redis cache first to avoid 0.5-2s DB scan penalty.
        Falls back to DB computation if cache miss, then caches result.
        """
        def _lexicon_values(snapshot: Mapping[str, object], key: str, *, limit: int = 300) -> tuple[str, ...]:
            values = snapshot.get(key) if isinstance(snapshot, Mapping) else None
            if not isinstance(values, (list, tuple, set)):
                return tuple()
            normalized: list[str] = []
            seen: set[str] = set()
            for value in values:
                token = str(value or "").strip().lower()
                if not token or token in seen:
                    continue
                seen.add(token)
                normalized.append(token)
                if len(normalized) >= limit:
                    break
            return tuple(normalized)

        from apps.rag.table_profile_cache import get_table_profile_cache, set_table_profile_cache
        
        query_text = (traits.normalized or traits.original or "").lower()
        query_tokens, specific_tokens = self._table_query_tokens(
            business_profile,
            traits,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        tokens = set(query_tokens)
        matched_keywords = tokens & self.table_query_keywords
        
        # Try Redis cache first (< 10ms)
        business_id = getattr(business_profile, "id", None)
        cached_profile = None
        if business_id:
            try:
                cached_profile = get_table_profile_cache(business_id)
            except Exception as exc:
                # Cache failure shouldn't break retrieval
                logger.warning(
                    "table_profile_cache.get_failed business=%s error=%s",
                    business_id,
                    str(exc)[:200],
                )
        
        if cached_profile:
            # Cache hit — use precomputed data
            _rag_log(
                "table.context.cache_hit",
                {"business": business_id, "cache_keys": list(cached_profile.keys())[:10]},
                indent=2,
                context={"business": business_id},
            )
            columns = set(cached_profile.get("available_columns") or [])
            row_label_tokens = set(cached_profile.get("row_label_tokens") or [])
            table_profile = {
                "table_uploads": cached_profile.get("table_uploads", 0),
                "total_uploads": cached_profile.get("total_uploads", 0),
                "table_upload_ratio": cached_profile.get("table_upload_ratio", 0.0),
                "table_count": cached_profile.get("table_count", 0),
                "dominant": cached_profile.get("dominant", False),
            }
        else:
            # Cache miss — compute from DB (expensive: 500-2000ms)
            _rag_log(
                "table.context.cache_miss",
                {"business": business_id, "will_compute_and_cache": True},
                indent=2,
                context={"business": business_id},
            )
            
            columns = self._table_columns_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            table_profile = self._table_profile_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            row_label_tokens = self._table_row_label_tokens_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            
            # Cache the computed result for next time
            if business_id:
                try:
                    profile_to_cache = {
                        "available_columns": columns,  # Will be converted to list by set_table_profile_cache
                        "row_label_tokens": row_label_tokens,
                        **table_profile,  # table_uploads, total_uploads, etc.
                    }
                    set_table_profile_cache(
                        business_id,
                        profile_to_cache,
                    )
                except Exception as exc:
                    logger.warning(
                        "table_profile_cache.set_failed business=%s error=%s",
                        business_id,
                        str(exc)[:200],
                    )
        
        # Compute query-specific matching (this MUST be done per-query)
        hints = self._table_column_hints(business_profile)
        semantic_columns = {column for column in columns if any(hint in column for hint in hints)}
        matched_columns_query_raw = {column for column in columns if column and column in query_text}
        matched_columns_query = {
            column for column in matched_columns_query_raw if self._is_usable_table_query_column_match(column)
        }
        matched_columns_tokens = {column for column in columns if self._column_matches_tokens(column, query_tokens)}
        matched_columns_specific = {column for column in columns if self._column_matches_tokens(column, specific_tokens)}
        matched_row_labels_raw = {token for token in row_label_tokens if token in tokens}
        matched_row_labels_specific = {token for token in matched_row_labels_raw if token in specific_tokens}
        matched_row_labels = (
            matched_row_labels_specific
            if self._has_strong_row_label_signal(
                matched_row_labels=matched_row_labels_specific,
                specific_tokens=specific_tokens,
            )
            else set()
        )
        matched_columns = matched_columns_query or matched_columns_tokens or semantic_columns
        has_currency_token = bool(tokens & {"egp", "usd", "eur", "gbp", "aed", "sar", "qar", "kwd", "bhd", "omr", "jod"})
        has_percent = "%" in query_text
        numeric_table_intent = bool(traits.has_digits and (has_currency_token or has_percent))
        has_intent_pre_classification = bool(
            matched_columns_query
            or matched_columns_specific
            or matched_row_labels
            or numeric_table_intent
        )

        # PHASE 1: Use QueryClassifier for intent detection instead of legacy keyword matching.
        # This fixes the bug where "list all credit cards" was marked as non-comprehensive
        # because "credit" matched a row label token.
        #
        # The classifier uses proper linguistic analysis:
        #   - ENUMERATE intent: "list all", "show me every", "what are all the"
        #   - SPECIFIC_LOOKUP intent: "Gold card fees" (specific entity name)
        #   - COMPARE intent: "compare X vs Y"
        #   - AGGREGATE intent: "total fees", "how many cards"
        #   - EXPLORATORY intent: open-ended queries
        #
        tenant_lexicon_snapshot: Mapping[str, object] = {}
        if self._tenant_lexicon_tables_ready():
            try:
                tenant_lexicon_snapshot = self.tenant_lexicon_service.get_snapshot(
                    business_profile=business_profile,
                    use_cache=True,
                )
            except Exception as exc:  # pragma: no cover - cache/db failures should not block search
                logger.warning(
                    "tenant_lexicon.snapshot_failed business=%s error=%s",
                    business_profile.id if business_profile else None,
                    str(exc)[:240],
                )
                tenant_lexicon_snapshot = {}
        tenant_entity_terms = _lexicon_values(tenant_lexicon_snapshot, "entity_terms")
        tenant_attribute_terms = _lexicon_values(tenant_lexicon_snapshot, "attribute_terms", limit=500)

        query_classifier = QueryClassifier(known_entity_names=list(row_label_tokens)[:100])
        classification = query_classifier.classify(
            traits.original or traits.normalized or query_text,
            context={
                "tenant_id": str(getattr(business_profile, "id", "") or ""),
                "document_entities": list(row_label_tokens)[:100],
                "table_schemas": list(columns)[:50],
                "tenant_entity_terms": tenant_entity_terms,
                "tenant_attribute_terms": tenant_attribute_terms,
            }
        )

        # Do not invoke an extra LLM on the hot retrieval path for query intent.
        # The heuristic classifier is good enough for routing, and the main
        # conversational agent already has a model loop above retrieval.
        fallback_attempted = False
        fallback_applied = False
        classification.fallback_used = bool(classification.fallback_used)

        should_require_clarification = bool(
            has_intent_pre_classification
            and classification.intent == QueryIntent.EXPLORATORY
            and classification.confidence < self.intent_clarification_threshold
        )
        if should_require_clarification:
            classification.requires_clarification = True
            if classification.intent == QueryIntent.COMPARE and len(classification.entity_names) < 2:
                question = "Which two items should I compare? Please share both names or IDs."
            elif classification.intent == QueryIntent.AGGREGATE and not classification.attributes:
                question = "Which metric should I calculate (for example total count, total amount, or average value)?"
            elif classification.intent == QueryIntent.SPECIFIC_LOOKUP and not classification.entity_names:
                question = "Do you want one specific record or all matching records? Share a name or ID if specific."
            else:
                question = (
                    "Please clarify what to retrieve: a specific record (with name/ID), "
                    "a comparison, or a full list."
                )
            classification.clarification_question = question
        else:
            classification.requires_clarification = False
            classification.clarification_question = ""
        
        # comprehensive_intent is True for ENUMERATE and AGGREGATE intents
        # These require full table coverage, not just the top-matching rows
        comprehensive_intent = classification.requires_full_coverage()
        generic_intents = {QueryIntent.ENUMERATE, QueryIntent.AGGREGATE, QueryIntent.COMPARE}
        generic_column_signal = bool(
            matched_columns_tokens
            and table_profile.get("dominant")
            and classification.intent in generic_intents
            and classification.confidence >= 0.45
            and not classification.requires_clarification
        )
        # Intent is data-driven, but must be based on meaningful signals.
        # Generic fee/limit/international tokens across a tenant corpus are too loose.
        row_label_intent = bool(
            matched_row_labels
            and (
                classification.intent != QueryIntent.EXPLORATORY
                or len(matched_row_labels) >= self.table_specific_min_match_count
            )
        )
        has_intent = bool(
            matched_columns_query
            or matched_columns_specific
            or row_label_intent
            or numeric_table_intent
            or generic_column_signal
        )
        
        # Also preserve legacy detection for backward compatibility during transition
        legacy_comprehensive_keywords = {"all", "every", "everything", "list", "compare", "comparison", "full", "complete", "entire", "whole", "show"}
        legacy_has_comprehensive_keyword = bool(tokens & legacy_comprehensive_keywords)
        legacy_comprehensive_tokens = tokens & legacy_comprehensive_keywords

        # DEBUG: Log comprehensive intent detection with both old and new methods
        _rag_log(
            "table.comprehensive_detection",
            {
                "query_tokens": list(tokens)[:20],
                "classifier_intent": classification.intent.value,
                "classifier_confidence": round(classification.confidence, 2),
                "classifier_reasoning": classification.reasoning,
                "classifier_source": classification.source,
                "classifier_fallback_attempted": fallback_attempted,
                "classifier_fallback_applied": fallback_applied,
                "classifier_requires_clarification": classification.requires_clarification,
                "comprehensive_intent_result": comprehensive_intent,
                # Legacy detection (for comparison during transition)
                "legacy_comprehensive_tokens": list(legacy_comprehensive_tokens),
                "legacy_has_keyword": legacy_has_comprehensive_keyword,
                "matched_row_labels": list(matched_row_labels)[:10] if matched_row_labels else [],
                "legacy_would_be_comprehensive": legacy_has_comprehensive_keyword and not matched_row_labels,
            },
            indent=2,
            context={"business": business_profile.id if business_profile else None},
        )

        allow_generic = bool(
            table_profile.get("dominant")
            and classification.intent in generic_intents
            and classification.confidence >= 0.45
            and not classification.requires_clarification
        )
        if row_label_intent and not classification.requires_clarification:
            allow_generic = True
        return {
            "has_intent": has_intent,
            "comprehensive_intent": comprehensive_intent,
            "query_classification": classification,  # New: full classification object
            "intent_source": classification.source,
            "intent_fallback_attempted": fallback_attempted,
            "intent_fallback_applied": fallback_applied,
            "requires_clarification": classification.requires_clarification,
            "clarification_question": classification.clarification_question,
            "tenant_lexicon_entity_terms_count": len(tenant_entity_terms),
            "tenant_lexicon_attribute_terms_count": len(tenant_attribute_terms),
            "matched_columns": matched_columns,
            "matched_columns_query": matched_columns_query,
            "matched_columns_query_raw": matched_columns_query_raw,
            "matched_columns_tokens": matched_columns_tokens,
            "matched_columns_specific": matched_columns_specific,
            "matched_row_labels": matched_row_labels,
            "matched_row_labels_raw": matched_row_labels_raw,
            "row_label_intent": row_label_intent,
            "matched_keywords": matched_keywords,
            "numeric_intent": numeric_table_intent,
            "available_columns": columns,
            "semantic_columns": semantic_columns,
            "matched_column_count": len(matched_columns),
            "query_tokens": query_tokens,
            "specific_tokens": specific_tokens,
            "table_dominant": table_profile.get("dominant"),
            "table_upload_ratio": table_profile.get("table_upload_ratio"),
            "table_count": table_profile.get("table_count"),
            "table_uploads": table_profile.get("table_uploads"),
            "allow_generic": allow_generic,
            "generic_column_signal": generic_column_signal,
        }

    def _table_columns_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_column_cache.get(scope_key)
        if cached is not None:
            self._table_column_cache.move_to_end(scope_key)
            return cached
        columns: set[str] = set()
        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_column_cache[scope_key] = set()
                return set()
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
        uploads_qs = self._filter_queryable_table_uploads(
            uploads_qs,
            format_lookup="ingestion_metadata__format",
        )
        profiles = list(
            uploads_qs.order_by("-updated_at").values_list("ingestion_metadata__table_profile", flat=True)[
                : self.table_column_sample_limit
            ]
        )
        for profile in profiles:
            if not isinstance(profile, Mapping):
                continue
            for column in profile.get("columns") or []:
                lowered = str(column).strip().lower()
                if lowered:
                    columns.add(lowered)
        if not columns:
            qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
                upload__visibility=KnowledgeVisibility.INTERNAL,
            )
            if allowed_upload_ids is not None:
                qs = qs.filter(upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    qs = qs.filter(clause)
            qs = self._filter_queryable_table_uploads(qs, format_lookup="upload__ingestion_metadata__format")
            qs = qs.order_by("-updated_at").values_list("column_schema", flat=True)[: self.table_column_sample_limit]
            for schema in qs:
                if not isinstance(schema, (list, tuple)):
                    continue
                for column in schema:
                    if not column:
                        continue
                    lowered = str(column).strip().lower()
                    if lowered:
                        columns.add(lowered)
        self._table_column_cache[scope_key] = columns
        if len(self._table_column_cache) > self.table_column_cache_limit:
            self._table_column_cache.popitem(last=False)
        return columns

    def _table_profile_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> dict[str, object]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return {
                "table_uploads": 0,
                "total_uploads": 0,
                "table_upload_ratio": 0.0,
                "table_count": 0,
                "dominant": False,
            }
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_context_cache.get(scope_key)
        if cached is not None:
            self._table_context_cache.move_to_end(scope_key)
            return cached

        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                profile = {
                    "table_uploads": 0,
                    "total_uploads": 0,
                    "table_upload_ratio": 0.0,
                    "table_count": 0,
                    "dominant": False,
                }
                self._table_context_cache[scope_key] = profile
                if len(self._table_context_cache) > self.table_context_cache_limit:
                    self._table_context_cache.popitem(last=False)
                return profile
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
        uploads_qs = self._filter_queryable_table_uploads(
            uploads_qs,
            format_lookup="ingestion_metadata__format",
        )
        total_uploads = uploads_qs.count()

        table_qs = KnowledgeUploadTable.objects.filter(
            upload__business_profile=business_profile,
        ).exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            table_qs = table_qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                table_qs = table_qs.filter(clause)
        table_qs = self._filter_queryable_table_uploads(
            table_qs,
            format_lookup="upload__ingestion_metadata__format",
        )
        table_uploads = table_qs.values("upload_id").distinct().count()
        table_count = table_qs.count()
        upload_ratio = (table_uploads / total_uploads) if total_uploads else 0.0
        dominant = bool(
            table_count >= self.table_dominant_min_tables
            and upload_ratio >= self.table_dominant_upload_ratio
        )
        profile = {
            "table_uploads": table_uploads,
            "total_uploads": total_uploads,
            "table_upload_ratio": round(upload_ratio, 4),
            "table_count": table_count,
            "dominant": dominant,
        }
        self._table_context_cache[scope_key] = profile
        if len(self._table_context_cache) > self.table_context_cache_limit:
            self._table_context_cache.popitem(last=False)
        return profile

    def _table_row_label_tokens_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_row_label_cache.get(scope_key)
        if cached is not None:
            self._table_row_label_cache.move_to_end(scope_key)
            return cached
        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_row_label_cache[scope_key] = set()
                return set()
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
        uploads_qs = self._filter_queryable_table_uploads(
            uploads_qs,
            format_lookup="ingestion_metadata__format",
        )
        profiles = list(
            uploads_qs.order_by("-updated_at").values_list("ingestion_metadata__table_profile", flat=True)[
                : self.table_row_label_sample_limit
            ]
        )
        cell_qs = KnowledgeUploadTableCell.objects.filter(
            table__upload__business_profile=business_profile,
            column_index=0,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
        tokens: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, Mapping):
                continue
            for token in profile.get("row_label_tokens") or []:
                cleaned = str(token).strip().lower()
                if cleaned:
                    tokens.add(cleaned)
        if not tokens:
            if allowed_upload_ids is not None:
                cell_qs = cell_qs.filter(table__upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    cell_qs = cell_qs.filter(clause)
            cell_qs = self._filter_queryable_table_uploads(
                cell_qs,
                format_lookup="table__upload__ingestion_metadata__format",
            ).exclude(row__metadata__row_type="header")
            labels = list(
                cell_qs.order_by("-row__updated_at")
                .values_list("raw_text", flat=True)[: self.table_row_label_sample_limit]
            )
            for label in labels:
                if not label:
                    continue
                tokens.update(self._table_tokenize(str(label)))
        self._table_row_label_cache[scope_key] = tokens
        if len(self._table_row_label_cache) > self.table_context_cache_limit:
            self._table_row_label_cache.popitem(last=False)
        return tokens

    def _table_row_result_cap_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve how many table rows we can consider before final ranking.

        Requested `limit` remains the primary driver. Business overrides and default
        caps act as floors for candidate collection, not hard ceilings.
        """
        requested_cap = 0
        if requested is not None:
            try:
                requested_cap = max(1, int(requested))
            except (TypeError, ValueError):
                requested_cap = 0
        base = max(1, int(self.table_result_cap))
        if not business_profile:
            return max(base, requested_cap)
        override = self._business_override(business_profile, "table_results_limit", base)
        try:
            override_cap = max(1, int(override))
        except (TypeError, ValueError):
            override_cap = base
        return max(base, override_cap, requested_cap)

    def _table_ingestion_diagnostics(self, upload: KnowledgeUpload | None) -> dict[str, object]:
        diagnostics: dict[str, object] = {
            "table_truncated": False,
            "total_rows": None,
            "indexed_rows": None,
            "row_cap": None,
            "partial_tables": None,
            "partial_index": False,
            "truncated_rows": None,
            "truncated_columns": None,
            "truncated_tables": None,
        }
        if not upload:
            return diagnostics
        metadata = getattr(upload, "ingestion_metadata", None)
        if isinstance(metadata, Mapping):
            table_stats = metadata.get("table_stats")
            if isinstance(table_stats, Mapping):
                diagnostics["total_rows"] = table_stats.get("total_rows")
                diagnostics["indexed_rows"] = table_stats.get("indexed_rows")
                diagnostics["row_cap"] = table_stats.get("row_cap")
                diagnostics["partial_tables"] = table_stats.get("partial_tables")
                diagnostics["partial_index"] = bool(table_stats.get("partial_index"))
            table_truncation = metadata.get("table_truncation")
            if isinstance(table_truncation, Mapping):
                diagnostics["truncated_rows"] = table_truncation.get("truncated_rows")
                diagnostics["truncated_columns"] = table_truncation.get("truncated_columns")
                diagnostics["truncated_tables"] = table_truncation.get("truncated_tables")
        indexed_rows = self._coerce_int(diagnostics.get("indexed_rows"))
        total_rows = self._coerce_int(diagnostics.get("total_rows"))
        truncated_rows = self._coerce_int(diagnostics.get("truncated_rows"))
        truncated_tables = self._coerce_int(diagnostics.get("truncated_tables"))
        partial_tables = self._coerce_int(diagnostics.get("partial_tables"))
        partial_index = bool(diagnostics.get("partial_index"))
        table_truncated = (
            truncated_rows > 0
            or truncated_tables > 0
            or partial_tables > 0
            or partial_index
            or (indexed_rows and total_rows and indexed_rows < total_rows)
        )
        diagnostics["table_truncated"] = table_truncated
        return diagnostics

    def _table_column_hints(self, business_profile) -> set[str]:
        hints = set(self.table_column_hint_base)
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if isinstance(overrides, dict):
            extra = overrides.get("table_column_hints")
            if isinstance(extra, (list, tuple, set)):
                hints.update(str(item).strip().lower() for item in extra if str(item).strip())
        return hints

    def _filter_queryable_table_uploads(self, qs, *, format_lookup: str):
        """
        Keep rows where the upload format is missing/NULL or not in non-queryable formats.

        We use a positive filter (IS NULL OR NOT IN) instead of exclude(IN) because
        SQL NULL semantics can otherwise drop rows where the JSON key is absent.
        """
        if not self.non_queryable_table_formats:
            return qs
        formats = sorted(self.non_queryable_table_formats)
        return qs.filter(Q(**{f"{format_lookup}__isnull": True}) | ~Q(**{f"{format_lookup}__in": formats}))

    def _business_has_tables(
        self,
        business_profile,
        cached_columns: set[str] | None = None,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> bool:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return False
        if cached_columns is not None and cached_columns:
            return True
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_presence_cache.get(scope_key)
        if cached is not None:
            self._table_presence_cache.move_to_end(scope_key)
            return cached
        qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
            upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_presence_cache[scope_key] = False
                self._table_presence_cache.move_to_end(scope_key)
                if len(self._table_presence_cache) > self.table_column_cache_limit:
                    self._table_presence_cache.popitem(last=False)
                return False
            qs = qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                qs = qs.filter(clause)
        qs = self._filter_queryable_table_uploads(qs, format_lookup="upload__ingestion_metadata__format")
        exists = qs.exists()
        self._table_presence_cache[scope_key] = exists
        self._table_presence_cache.move_to_end(scope_key)
        if len(self._table_presence_cache) > self.table_column_cache_limit:
            self._table_presence_cache.popitem(last=False)
        return exists

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
    def _structured_exports(upload: KnowledgeUpload, *, max_tables: int = 3, max_pages: int | None = 50) -> dict[str, tuple[Mapping[str, object], ...]]:
        metadata = upload.ingestion_metadata or {}
        exports = metadata.get("structured_exports") if isinstance(metadata, dict) else None
        if not isinstance(exports, dict):
            return {"tables": tuple(), "issues": tuple(), "pages": tuple()}
        tables = exports.get("tables") or []
        issues = exports.get("issues") or []
        pages = exports.get("pages") or []
        def _normalize_list(source: Any, limit: int | None = None) -> tuple[Mapping[str, object], ...]:
            if not isinstance(source, list):
                return tuple()
            sliced = source if limit is None else source[:limit]
            normalized: list[Mapping[str, object]] = []
            for item in sliced:
                if isinstance(item, dict):
                    normalized.append(item)
            return tuple(normalized)
        return {
            "tables": _normalize_list(tables, max_tables),
            "issues": _normalize_list(issues, 10),
            "pages": _normalize_list(pages, max_pages),
        }

    @staticmethod
    def _ingestion_issue_summaries(upload: KnowledgeUpload) -> tuple[Mapping[str, object], ...]:
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        exports = metadata.get("structured_exports")
        if not isinstance(exports, dict):
            return tuple()
        issues = exports.get("issues")
        if not isinstance(issues, list):
            return tuple()
        normalized: list[Mapping[str, object]] = []
        for issue in issues:
            if isinstance(issue, dict):
                normalized.append(issue)
        return tuple(normalized)

    @staticmethod
    def _truncation_metrics(upload: KnowledgeUpload) -> dict[str, object]:
        """
        Extract high-level truncation metrics from ingestion metadata so the orchestrator
        can reason about severe truncation when deciding whether to warn the user.
        """
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        metrics: dict[str, object] = {}
        truncated_entities = metadata.get("truncated_entities")
        if isinstance(truncated_entities, int) and truncated_entities > 0:
            metrics["truncated_entities"] = truncated_entities
        table_trunc = metadata.get("table_truncation")
        if isinstance(table_trunc, dict):
            for key in ("truncated_tables", "truncated_rows", "truncated_columns"):
                value = table_trunc.get(key)
                if isinstance(value, int) and value > 0:
                    metrics[key] = value
        table_stats = metadata.get("table_stats")
        if isinstance(table_stats, dict):
            def _coerce_int(value: object) -> int:
                try:
                    return int(value) if value is not None else 0
                except (TypeError, ValueError):
                    return 0

            total_rows = _coerce_int(table_stats.get("total_rows"))
            indexed_rows = _coerce_int(table_stats.get("indexed_rows"))
            row_cap = _coerce_int(table_stats.get("row_cap"))
            source_rows = _coerce_int(table_stats.get("source_row_count"))
            partial_tables = _coerce_int(table_stats.get("partial_tables"))
            if total_rows:
                metrics["table_total_rows"] = total_rows
            if indexed_rows:
                metrics["table_indexed_rows"] = indexed_rows
            if row_cap:
                metrics["table_row_cap"] = row_cap
            if source_rows:
                metrics["table_source_rows"] = source_rows
            if partial_tables:
                metrics["table_partial_tables"] = partial_tables
            row_tier = table_stats.get("row_tier")
            if isinstance(row_tier, str) and row_tier:
                metrics["table_row_tier"] = row_tier
            if bool(table_stats.get("partial_index")) or (indexed_rows and total_rows and indexed_rows < total_rows):
                metrics["partial_index"] = True
        if metrics.get("truncated_rows") or metrics.get("table_partial_tables"):
            metrics["partial_index"] = True
        return metrics

    # apps/services/ai_orchestrator.py (inside KnowledgeSearchService)
    def _serialize_structured_tables_with_rows(
        self,
        upload,
        *,
        max_tables: int = 3,
        max_rows: int = 5,
        max_columns: int = 8,
    ):
        tables_manager = getattr(upload, "tables", None)
        if not hasattr(tables_manager, "order_by"):
            return []
        enriched = []
        table_qs = tables_manager.order_by("order_index")
        for table in table_qs[:max_tables]:
            rows_manager = getattr(table, "rows", None)
            rows_sample: list[list[str]] = []
            rows_qs = rows_manager.order_by("row_index") if hasattr(rows_manager, "order_by") else None
            if rows_qs is not None:
                limited_rows = rows_qs[:max_rows] if max_rows else rows_qs
                for row in limited_rows:
                    cells_manager = getattr(row, "cells", None)
                    cells_qs = cells_manager.order_by("column_index") if hasattr(cells_manager, "order_by") else None
                    if cells_qs is None:
                        rows_sample.append([])
                        continue
                    limited_cells = cells_qs[:max_columns] if max_columns else cells_qs
                    rows_sample.append([cell.raw_text for cell in limited_cells])
            enriched.append(
                {
                    "order_index": table.order_index,
                    "title": table.title or f"Table {table.order_index}",
                    "section_heading": table.section_heading or "",
                    "page_number": table.page.page_number if table.page else None,
                    "column_schema": list(table.column_schema or []),
                    "row_count": rows_manager.count() if hasattr(rows_manager, "count") else 0,
                    "rowsSample": rows_sample,
                    "bbox": dict(table.bbox or {}),
                    "metadata": dict(table.metadata or {}),
                }
            )
        return enriched

    def _table_row_sample(
        self,
        chunk: KnowledgeUploadChunk,
        *,
        max_columns: int = 6,
        max_rows: int = 1,
        query: str | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        """
        Build an answer-ready structured table preview for doc-table chunks.

        - Chunk-scoped: uses `metadata.table_id` to pick the correct table.
        - Row-accurate: if the chunk is a row chunk, preview that exact row.
        - Query-aware: for parent/preview chunks, pick the most relevant rows by token overlap.

        Returns a `structuredTables`-compatible payload (columns + rows + provenance metadata)
        so the LLM can answer table questions without needing an extra `read_knowledge` call.
        """
        chunk_metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        if not chunk_metadata.get("is_table_chunk"):
            return tuple()

        table_id = chunk_metadata.get("table_id")
        table = None
        if table_id:
            try:
                from apps.knowledge.models import KnowledgeUploadTable

                table = KnowledgeUploadTable.objects.filter(id=table_id).first()
            except Exception:
                table = None
        if table is None:
            upload = chunk.upload
            tables_manager = getattr(upload, "tables", None)
            if not hasattr(tables_manager, "order_by"):
                return tuple()
            try:
                table = tables_manager.order_by("order_index").first()
            except Exception:
                return tuple()
        if table is None:
            return tuple()

        # Prefer the exact row for row chunks.
        target_row_index = chunk_metadata.get("table_row_index")
        if isinstance(target_row_index, str) and target_row_index.isdigit():
            target_row_index = int(target_row_index)
        if not isinstance(target_row_index, int):
            target_row_index = None

        max_rows = max(1, int(max_rows))
        max_columns = max(2, int(max_columns))
        row_limit = max(1, min(50, max_rows * 25))

        try:
            row_qs = (
                table.rows.filter(row_index__isnull=False)
                .exclude(metadata__row_type="header")
                .order_by("row_index")
                .prefetch_related(
                    Prefetch(
                        "cells",
                        queryset=KnowledgeUploadTableCell.objects.order_by("column_index"),
                    )
                )
            )
            if target_row_index is not None:
                row_qs = row_qs.filter(row_index=target_row_index)
                selected_rows = list(row_qs[:max_rows])
            else:
                rows = list(row_qs[:row_limit])
                if not rows:
                    return tuple()
                if not query or len(rows) <= 1:
                    selected_rows = rows[:max_rows]
                else:
                    # Lightweight token overlap scoring (bounded to first `row_limit` rows).
                    query_tokens = {tok for tok in (query or "").lower().split() if tok and len(tok) >= 3}
                    scored: list[tuple[int, int]] = []
                    for idx, row in enumerate(rows):
                        score = 0
                        for cell in row.cells.all():
                            cell_text = str(cell.raw_text or "").lower()
                            for tok in query_tokens:
                                if tok in cell_text:
                                    score += 1
                        scored.append((score, idx))
                    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                    picked = [rows[idx] for score, idx in scored[:max_rows] if score > 0]
                    selected_rows = picked if picked else rows[:max_rows]

            if not selected_rows:
                return tuple()

            # Build a compact structured table payload.
            all_cells = list(selected_rows[0].cells.all()) if selected_rows else []
            ordered_columns = [
                (cell.column_index, (cell.column_key or f"column_{(cell.column_index or 0) + 1}"))
                for cell in all_cells
            ]
            ordered_columns.sort(key=lambda item: item[0])
            column_labels = [label for _, label in ordered_columns]
            if len(column_labels) > max_columns:
                column_labels = column_labels[:max_columns]

            rows_payload: list[list[str]] = []
            for row in selected_rows:
                cell_lookup = {cell.column_index: (cell.raw_text or "") for cell in row.cells.all()}
                values: list[str] = []
                for col_idx, _label in ordered_columns[: len(column_labels)]:
                    values.append(str(cell_lookup.get(col_idx, "")))
                rows_payload.append(values)

            structured_table = {
                "title": table.title or table.section_heading or f"Table {table.order_index}",
                "columns": column_labels,
                "rows": rows_payload,
                "metadata": {
                    "table_id": str(table.id),
                    "table_order_index": table.order_index,
                    "page_number": table.page.page_number if table.page else None,
                    "row_indexes": [row.row_index for row in selected_rows],
                    "chunk_role": chunk_metadata.get("table_chunk_role"),
                },
            }
            table_meta = table.metadata if isinstance(getattr(table, "metadata", None), Mapping) else {}
            if table_meta:
                quality_score = table_meta.get("quality_score")
                if isinstance(quality_score, (int, float)):
                    structured_table["metadata"]["quality_score"] = float(quality_score)
                is_decorative = table_meta.get("is_decorative")
                if isinstance(is_decorative, bool):
                    structured_table["metadata"]["is_decorative"] = is_decorative
                signals = table_meta.get("quality_signals")
                if isinstance(signals, Mapping) and signals.get("column_misalignment") is True:
                    structured_table["metadata"]["column_misalignment"] = True
            return (structured_table,)
        except Exception:
            return tuple()



    @staticmethod
    def _render_structured_tables_text(upload: KnowledgeUpload, *, max_preview_rows: int = 5) -> str:
        tables_manager = getattr(upload, "tables", None)
        if not hasattr(tables_manager, "all"):
            return ""
        tables = list(tables_manager.all())
        if not tables:
            return ""
        lines = ["[Structured Tables]"]
        for table in tables:
            page_number = table.page.page_number if table.page else None
            title = table.title or f"Table {table.order_index}"
            header = ", ".join((table.column_schema or [])[:10])
            lines.append(f"- {title} (page {page_number or 'n/a'}) columns: {header or 'unspecified'}")
            rows = list(table.rows.all()) if hasattr(table, "rows") else []
            preview_rows = rows[:max_preview_rows]
            for row in preview_rows:
                cells = list(row.cells.all()) if hasattr(row, "cells") else []
                cell_values = [cell.raw_text for cell in sorted(cells, key=lambda c: c.column_index)]
                if cell_values:
                    lines.append(f"    • {' | '.join(cell_values)}")
            if len(rows) > max_preview_rows:
                lines.append(f"    • … ({len(rows) - max_preview_rows} more rows)")
        return "\n".join(lines)

    @staticmethod
    def _render_issue_text(upload: KnowledgeUpload, *, max_issues: int = 5) -> str:
        issues_manager = getattr(upload, "issues", None)
        if not hasattr(issues_manager, "all"):
            return ""
        issues = list(issues_manager.all())[:max_issues]
        if not issues:
            return ""
        lines = ["[Ingestion Issues]"]
        for issue in issues:
            location = []
            if issue.page:
                location.append(f"page {issue.page.page_number}")
            if issue.table:
                location.append(f"table {issue.table.order_index}")
            if issue.table_row:
                location.append(f"row {issue.table_row.row_index}")
            if issue.table_cell:
                location.append(f"cell {issue.table_cell.column_index}")
            location_str = " • ".join(location)
            lines.append(f"- {issue.severity.upper()} {issue.issue_code}: {issue.description} ({location_str or 'no location'})")
        return "\n".join(lines)

    @staticmethod
    def _public_label(upload: KnowledgeUpload) -> str:
        metadata = upload.metadata or {}
        if isinstance(metadata, dict):
            integration_resource = metadata.get("integration_resource") if isinstance(metadata.get("integration_resource"), Mapping) else None
            if isinstance(integration_resource, Mapping):
                sheet_label = integration_resource.get("sheet_label")
                if isinstance(sheet_label, str) and sheet_label.strip():
                    return sheet_label.strip()
            for key in ("public_label", "customer_label", "display_label"):
                label = metadata.get(key)
                if isinstance(label, str) and label.strip():
                    return label.strip()
        return (upload.display_name or upload.source_name or upload.external_reference or "Knowledge Resource").strip()

    @staticmethod
    def _summarize_upload(upload: KnowledgeUpload) -> str:
        summary = (upload.summary or upload.description or "No summary available.").strip()
        return summary[:280]

    @staticmethod
    def _summarize_chunk(chunk: KnowledgeUploadChunk) -> str:
        text = (chunk.content or "").strip()
        if not text:
            return "No summary available."
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return "No summary available."

        first_line = lines[0]
        if len(first_line) >= 40 or len(lines) == 1:
            return (first_line or text)[:280]

        # If the first line is a short heading (common for PDF table-ish chunks),
        # include a few additional non-empty lines so the LLM has enough context
        # to answer without immediately calling read_knowledge.
        parts: list[str] = []
        for line in lines[:10]:
            if not line:
                continue
            candidate = "\n".join([*parts, line]) if parts else line
            if len(candidate) > 280:
                break
            parts.append(line)
            if len(parts) >= 6 and any(char.isdigit() for char in candidate):
                break
        snippet = "\n".join(parts) if parts else (first_line or text)
        return snippet[:280]

    @staticmethod
    def _extract_content(upload: KnowledgeUpload) -> str:
        if upload.text_detail and upload.text_detail.content:
            return upload.text_detail.content
        if upload.description:
            return upload.description
        if upload.summary:
            return upload.summary
        return ""

    @staticmethod
    def _trim_content(content: str, *, max_chars: int) -> str:
        text, _ = KnowledgeSearchService._trim_with_flag(content, max_chars=max_chars)
        return text

    @staticmethod
    def _trim_with_flag(content: str, *, max_chars: int) -> tuple[str, bool]:
        text = (content or "").strip()
        if not text or max_chars <= 0:
            return text, False
        if len(text) <= max_chars:
            return text, False
        logger.info("Trimming knowledge content from %s to %s chars", len(text), max_chars)
        trimmed = text[:max_chars].rstrip()
        return f"{trimmed}\n\n[Content truncated]", True

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



    @staticmethod
    def _is_legacy_pdf_table_entity_chunk(chunk: KnowledgeUploadChunk) -> bool:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        if metadata.get("strategy") != "json_entity":
            return False
        upload = getattr(chunk, "upload", None)
        ingestion_meta = getattr(upload, "ingestion_metadata", None) if upload else None
        if not isinstance(ingestion_meta, Mapping):
            return False
        return str(ingestion_meta.get("format") or "").strip().lower() == "pdf"

    @staticmethod
    def _is_pinned(upload: KnowledgeUpload) -> bool:
        metadata = upload.metadata if isinstance(upload.metadata, dict) else {}
        ingestion = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        tags = upload.tags if isinstance(upload.tags, list) else []
        flag_sources: list[object] = []
        flag_sources.extend(metadata.get(key) for key in ("pin", "pinned", "always_on_prompt", "alwaysOnPrompt") if metadata)
        flag_sources.extend(ingestion.get(key) for key in ("pin", "pinned") if ingestion)
        normalized_tags = {str(tag).strip().lower() for tag in tags if isinstance(tag, str)}
        if any(KnowledgeSearchService._coerce_bool(flag) for flag in flag_sources if flag is not None):
            return True
        if any(tag in {"pin", "pinned", "always-on", "always_on", "alwayson"} for tag in normalized_tags):
            return True
        return False

    @staticmethod
    def _coerce_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {"1", "true", "yes", "y", "t", "pin"}
        return False

    @staticmethod
    def _coerce_int(value: object) -> int:
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _topic_hints(cls, upload: KnowledgeUpload) -> tuple[str, ...]:
        metadata = upload.metadata if isinstance(upload.metadata, dict) else {}
        tags = upload.tags if isinstance(upload.tags, list) else []
        hints: list[str] = []
        for source in (
            metadata.get("coverage"),
            metadata.get("topics"),
            metadata.get("labels"),
            metadata.get("keywords"),
            tags,
            [upload.category] if upload.category else [],
        ):
            hints.extend(cls._normalize_topic_list(source))
        seen: set[str] = set()
        ordered: list[str] = []
        for hint in hints:
            if hint and hint not in seen:
                seen.add(hint)
                ordered.append(hint)
        return tuple(ordered)

    @staticmethod
    def _normalize_topic_list(source: object) -> list[str]:
        if source is None:
            return []
        if isinstance(source, str):
            normalized = KnowledgeSearchService._normalize_topic_value(source)
            return [normalized] if normalized else []
        if isinstance(source, (list, tuple, set)):
            result: list[str] = []
            for item in source:
                normalized = KnowledgeSearchService._normalize_topic_value(item)
                if normalized:
                    result.append(normalized)
            return result
        return []

    @staticmethod
    def _normalize_topic_value(value: object) -> str:
        if not isinstance(value, str):
            return ""
        normalized = " ".join(value.replace("_", " ").replace("/", " ").split()).strip().lower()
        return normalized


@dataclasses.dataclass(frozen=True)
class LlmPlan:
    response_text: str
    planned_actions: Sequence[PlannedAction]
    extractions: Sequence[ExtractionPlan]
    knowledge_requests: Sequence[str]
    response_blocks: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    llm_usage: Mapping[str, object] | None = None


class AiOrchestratorService:
    """
    Legacy ledger-based orchestrator.

    Coordinates knowledge retrieval, action planning, and LLM interactions using a
    precomputed knowledge ledger stored in conversation metadata. The MCP-style
    orchestrator (apps.mcp.orchestrator.McpOrchestratorService) is now the primary
    implementation; this class is retained for backward compatibility and as a
    fallback when MCP is disabled.
    """

    def __init__(self, *, agent: AgentProfile, provider: BaseLLMProvider | None = None):
        self.agent = agent
        self.knowledge_service = KnowledgeSearchService()
        self.prompt_builder = PromptBuilder(agent)
        self.provider = provider
        self._permission_cache: dict[str, bool] = {}
        self._hydrate_permissions()
        self.business_override_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
        default_snippet_budget = max(3, int(getattr(settings, "RAG_KNOWLEDGE_SNIPPET_BUDGET", 6)))
        self.knowledge_snippet_budget = max(
            3,
            int(self._business_override(agent.business_profile, "prompt_snippet_budget", default_snippet_budget)),
        )
        default_chunk_reads = max(1, int(getattr(settings, "RAG_MAX_CHUNK_READS_PER_TURN", 3)))
        self.max_chunk_reads_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_reads_per_turn", default_chunk_reads)),
        )
        self.knowledge_trace_limit = max(5, int(getattr(settings, "RAG_KNOWLEDGE_TRACE_LIMIT", 12)))
        self.alias_low_confidence_threshold = float(getattr(settings, "RAG_ALIAS_LOW_CONFIDENCE_THRESHOLD", 0.35))

    # ------------------------------------------------------------------
    # Public API

    def stream_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_stream_complete: Callable[[], None] | None = None,
        on_spinner_update: Callable[[str], None] | None = None,
        on_tool_event: Callable[[Mapping[str, object]], None] | None = None,
        on_block_event: Callable[[Mapping[str, object]], None] | None = None,
        on_reasoning_event: Callable[[Mapping[str, object]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> StreamingTurnContext:
        """
        Execute the streaming phase of an orchestration turn.

        Returns the partial context required to finalize the turn after all
        response deltas have been emitted.
        """

        query = user_message.strip()
        logger.info(
            "orchestrator turn start conversation=%s agent=%s message=%s",
            conversation.id,
            self.agent.id,
            query[:160],
        )
        metadata_snapshot = dict(conversation.metadata or {})
        # Legacy ledger metadata: retained so existing conversations and analytics
        # continue to work when this non-MCP orchestrator is used as a fallback.
        turn_index = int(metadata_snapshot.get("knowledge_turn_counter") or 0) + 1
        metadata_snapshot["knowledge_turn_counter"] = turn_index
        prompt_budget = self._business_override(
            conversation.business_profile,
            "prompt_snippet_budget",
            self.knowledge_snippet_budget,
        )
        if not isinstance(metadata_snapshot.get("knowledge_delivery_log"), list):
            metadata_snapshot["knowledge_delivery_log"] = []
        metadata_dirty = True
        raw_cache = metadata_snapshot.get("knowledge_cache") if isinstance(metadata_snapshot, dict) else {}
        cached_entries: dict[str, dict[str, object]] = {}
        if isinstance(raw_cache, dict):
            for key, value in raw_cache.items():
                if isinstance(value, dict):
                    cached_entries[str(key)] = value
        cache_dirty = False
        raw_query_cache = metadata_snapshot.get("knowledge_query_cache") if isinstance(metadata_snapshot, dict) else {}
        session_cache: dict[str, object] = {}
        if isinstance(raw_query_cache, dict):
            session_cache.update(raw_query_cache)
        tool_trace_history = list(metadata_snapshot.get("knowledge_trace") or [])
        tool_trace: list[dict[str, object]] = []
        visitor_mentions, mention_updates = self._detect_snippet_mentions(
            query=query,
            cached_entries=cached_entries,
            turn_index=turn_index,
        )
        if mention_updates:
            cache_dirty = True

        query_traits = self.knowledge_service.analyze_query(query)
        identifier_tokens = self._identifier_tokens(query_traits.tokens)
        alias_result: AliasSearchResult | None = None
        if query_traits.is_identifier_like or identifier_tokens:
            alias_start = time.perf_counter()
            alias_result = self.knowledge_service.search_by_alias(
                business_profile=conversation.business_profile,
                traits=query_traits,
                aliases=identifier_tokens or None,
            )
            alias_duration = int((time.perf_counter() - alias_start) * 1000)
            tool_trace.append(
                self._record_tool_invocation(
                    tool="search_by_identifier",
                    query=query,
                    traits=query_traits,
                    duration_ms=alias_duration,
                    result_count=len(alias_result.hits),
                    metadata=alias_result.diagnostics,
                )
            )
        else:
            alias_result = AliasSearchResult(tuple(), {})

        if on_status_change:
            label = f"Searching: {query[:80]}" if query else "Searching knowledge…"
            on_status_change({"code": "searching_knowledge", "label": label})

        search_start = time.perf_counter()
        search_result = self.knowledge_service.search(
            business_profile=conversation.business_profile,
            query=query,
            traits=query_traits,
            alias_result=alias_result,
            session_cache=session_cache,
        )
        search_duration = int((time.perf_counter() - search_start) * 1000)
        if search_result.diagnostics.get("path") != "alias_exact":
            tool_trace.append(
                self._record_tool_invocation(
                    tool="search_free_text",
                    query=query,
                    traits=query_traits,
                    duration_ms=search_duration,
                    result_count=len(search_result.snippets),
                    metadata=search_result.diagnostics,
                )
            )
        knowledge_status = search_result.status
        knowledge_diagnostics = dict(search_result.diagnostics or {})
        metadata_snapshot["knowledge_search_status"] = knowledge_status
        metadata_snapshot["knowledge_route"] = {
            "identifier_like": query_traits.is_identifier_like,
            "identifier_tokens": identifier_tokens,
            "alias_short_circuit": bool(alias_result.short_circuit and alias_result.hits),
            "search_path": knowledge_diagnostics.get("path"),
            "status": knowledge_status,
        }
        metadata_snapshot["knowledge_last_diagnostics"] = knowledge_diagnostics
        metadata_dirty = True
        citations = list(search_result.snippets)
        actions_catalog = self._actions_catalog()
        recent_messages = list(self._recent_messages(conversation))
        knowledge_payload: list[dict[str, object]] = []
        for cached_snippet in cached_entries.values():
            if not self._should_include_snippet(
                cached_snippet,
                turn_index=turn_index,
                visitor_mentions=visitor_mentions,
            ):
                continue
            prepared = self._prepare_prompt_snippet(cached_snippet)
            # For table chunks, re-sample rows based on current query to ensure
            # follow-up questions get query-optimized table data (not stale cache).
            if prepared.get("is_table_chunk") or prepared.get("needs_table_refresh"):
                prepared = self._refresh_table_sample(prepared, query)
            self._upsert_knowledge_payload(knowledge_payload, prepared)
        for snippet in citations:
            serialized = self._serialize_snippet(snippet)
            serialized["status"] = serialized.get("status") or self._determine_snippet_status(serialized)
            serialized.setdefault("coverage", [])
            self._upsert_knowledge_payload(knowledge_payload, serialized)
        if knowledge_status == "not_found" and not citations:
            placeholder = {
                "id": "knowledge:not-found",
                "title": "No matching knowledge",
                "status": "not_found",
                "reason": knowledge_diagnostics.get("reason", "no_candidates"),
                "coverage": [],
                "read": KNOWLEDGE_READ_STATE_SUMMARY,
                "content": "",
            }
            self._upsert_knowledge_payload(knowledge_payload, placeholder)
        if knowledge_diagnostics.get("path") == "fallback":
            self._upsert_knowledge_payload(
                knowledge_payload,
                self._fallback_notice_snippet("fallback", summary="Fallback snippets are being used; verify facts before responding."),
            )
        if alias_result and alias_result.hits and not alias_result.short_circuit:
            top_alias = alias_result.hits[0]
            if (top_alias.alias_confidence or 0.0) < self.alias_low_confidence_threshold:
                self._upsert_knowledge_payload(knowledge_payload, self._clarify_identifier_notice())
        self._enforce_snippet_budget(knowledge_payload, budget=prompt_budget)
        snippet_lookup: dict[str, KnowledgeSnippet] = {str(snippet.id): snippet for snippet in citations}
        loaded_content_ids: set[str] = {
            str(identifier)
            for identifier, payload in cached_entries.items()
            if isinstance(payload, dict) and payload.get("content")
        }
        knowledge_reads: list[dict[str, object]] = []
        placeholder_response: str | None = None
        knowledge_loading = False
        # Accumulates all streamed response_text chunks across iterations.
        streamed_chunks: list[str] = []
        last_iteration_streamed = False
        iteration_streamed = False
        cache_satisfied_read = False
        forced_read_once = False  # NEW: prevent repeated forced reads
        llm_usage_summary: dict[str, object] | None = None

        def _merge_llm_usage(usage: Mapping[str, object] | None, *, stage: str) -> None:
            nonlocal llm_usage_summary
            if not usage:
                return
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            total = usage.get("total_tokens")
            try:
                prompt_val = int(prompt) if prompt is not None else 0
            except (TypeError, ValueError):
                prompt_val = 0
            try:
                completion_val = int(completion) if completion is not None else 0
            except (TypeError, ValueError):
                completion_val = 0
            try:
                total_val = int(total) if total is not None else 0
            except (TypeError, ValueError):
                total_val = 0
            if not total_val and (prompt_val or completion_val):
                total_val = prompt_val + completion_val
            if llm_usage_summary is None:
                llm_usage_summary = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "calls": [],
                }
            llm_usage_summary["prompt_tokens"] = int(llm_usage_summary.get("prompt_tokens", 0)) + prompt_val
            llm_usage_summary["completion_tokens"] = int(llm_usage_summary.get("completion_tokens", 0)) + completion_val
            llm_usage_summary["total_tokens"] = int(llm_usage_summary.get("total_tokens", 0)) + total_val
            entry = {
                "prompt_tokens": prompt_val,
                "completion_tokens": completion_val,
                "total_tokens": total_val,
                "stage": stage,
            }
            model = usage.get("model")
            provider = usage.get("provider")
            if isinstance(model, str) and model:
                entry["model"] = model
            if isinstance(provider, str) and provider:
                entry["provider"] = provider
            llm_usage_summary.setdefault("calls", [])
            if isinstance(llm_usage_summary["calls"], list):
                llm_usage_summary["calls"].append(entry)



        def _emit_stream_chunk(chunk: str) -> None:
            if not chunk:
                return
            # Track for finalization / fallback streaming.
            streamed_chunks.append(chunk)
            if on_response_text_delta:
                on_response_text_delta(chunk)

        def _provider_stream_callback(chunk: str) -> None:
            nonlocal iteration_streamed
            if not chunk:
                return
            iteration_streamed = True
            _emit_stream_chunk(chunk)

        prompt_bundle: PromptBundle | None = None
        final_plan: LlmPlan | None = None
        plan_candidate: LlmPlan | None = None
        max_turns = 3
        stream_complete_notified = False

        def _notify_stream_complete_once() -> None:
            nonlocal stream_complete_notified
            if stream_complete_notified:
                return
            stream_complete_notified = True
            if on_stream_complete:
                try:
                    on_stream_complete()
                except Exception:  # pragma: no cover - defensive
                    logger.exception("stream_complete callback failed")

        for iteration_index in range(max_turns):
            self._enforce_snippet_budget(knowledge_payload, budget=prompt_budget)
            prompt_bundle = self.prompt_builder.build(
                conversation=conversation,
                knowledge_snippets=knowledge_payload,
                transcript=recent_messages,
                actions_catalog=actions_catalog,
                knowledge_log=metadata_snapshot.get("knowledge_delivery_log") or (),
            )

            iteration_streamed = False
            stream_callback = _provider_stream_callback if on_response_text_delta else None
            plan_candidate = self._invoke_llm(
                prompt_bundle,
                on_response_text_delta=stream_callback,
                on_reasoning_event=on_reasoning_event,
                reasoning_label=f"Answer draft {iteration_index + 1}",
                should_cancel=should_cancel,
            )
            if not plan_candidate:
                final_plan = None
                _notify_stream_complete_once()
                break
            _merge_llm_usage(plan_candidate.llm_usage, stage=f"iteration_{iteration_index + 1}")
            ready_ids_in_payload = {
                str(s.get("id"))
                for s in knowledge_payload
                if (
                    str(s.get("status") or "").lower() == "ready"
                    or str(s.get("read_state") or "").lower() == KNOWLEDGE_READ_STATE_FULL
                )
            }


            # Map numeric ledger indices -> true snippet UUIDs, skip unavailable/suppressed
            normalized_kids = self._coerce_knowledge_ids(plan_candidate.knowledge_requests, knowledge_payload)

            pending_requests = [
                kid for kid in normalized_kids
                if kid not in loaded_content_ids and kid not in ready_ids_in_payload
            ]

            if not pending_requests:
                requested_again = bool(normalized_kids)
                if requested_again:
                    for kid in normalized_kids:
                        entry = cached_entries.get(str(kid))
                        if not isinstance(entry, dict):
                            continue
                        usage_changed, topics, usage_label = self._mark_snippet_usage(
                            entry=entry,
                            query=query,
                            turn_index=turn_index,
                            metadata_snapshot=metadata_snapshot,
                        )
                        if usage_changed:
                            cache_dirty = True
                            metadata_dirty = True
                        prepared = self._prepare_prompt_snippet(entry)
                        self._upsert_knowledge_payload(knowledge_payload, prepared)
                        knowledge_reads.append(
                            {
                                "id": str(entry.get("id")),
                                "label": entry.get("public_label") or entry.get("title") or "Knowledge",
                                "topics": topics,
                                "usage": usage_label,
                            }
                        )

                # Preserve whether the final iteration streamed any chunks.
                last_iteration_streamed = iteration_streamed

                # Give the model one more pass with the updated ledger if it re-requested a doc
                if requested_again and not cache_satisfied_read:
                    cache_satisfied_read = True
                    if on_status_change:
                        on_status_change("responding")
                    continue

                final_plan = plan_candidate
                _notify_stream_complete_once()
                break

            # Suppress placeholder emission; rely on status updates only.
            knowledge_loading = True
            if on_status_change:
                on_status_change("reading_document")

            try:
                chunk_uuid_list = list(
                    apply_customer_visible_chunks(
                        KnowledgeUploadChunk.objects.filter(id__in=pending_requests)
                    ).values_list("id", flat=True)
                )
                upload_uuid_list = list(
                    apply_customer_visible_uploads(
                        KnowledgeUpload.objects.filter(id__in=pending_requests)
                    ).values_list("id", flat=True)
                )
                if self.max_chunk_reads_per_turn and len(chunk_uuid_list) > self.max_chunk_reads_per_turn:
                    exceeded = len(chunk_uuid_list) - self.max_chunk_reads_per_turn
                    chunk_uuid_list = chunk_uuid_list[: self.max_chunk_reads_per_turn]
                    self._upsert_knowledge_payload(knowledge_payload, self._chunk_read_budget_notice(exceeded))
                chunk_id_set = {str(x) for x in chunk_uuid_list}
                upload_id_set = {str(x) for x in upload_uuid_list}
            except Exception:
                # If detection fails, treat all as uploads (safe fallback)
                chunk_id_set = set()
                upload_id_set = set(map(str, pending_requests))

            fetched_snippets: list[KnowledgeSnippet] = []
            inline_limit = self.knowledge_service.inline_char_limit_for_business(conversation.business_profile)

            # 1) Load chunk-focused spans (with small neighbor context)
            if chunk_id_set:
                chunk_start = time.perf_counter()
                chunk_snippets = self.knowledge_service.load_chunk_contents(
                    business_profile=conversation.business_profile,
                    chunk_ids=sorted(chunk_id_set),
                    neighbor=1,  # tweakable
                    max_chars=inline_limit,
                )
                fetched_snippets.extend(chunk_snippets)
                chunk_duration = int((time.perf_counter() - chunk_start) * 1000)
                tool_trace.append(
                    self._record_tool_invocation(
                        tool="load_chunk_contents",
                        query=query,
                        traits=query_traits,
                        duration_ms=chunk_duration,
                        result_count=len(chunk_snippets),
                        metadata={"chunk_ids": list(chunk_id_set)},
                    )
                )

            # 2) Load full uploads for the rest
            remaining_upload_ids = [uid for uid in pending_requests if str(uid) in upload_id_set]
            if remaining_upload_ids:
                doc_start = time.perf_counter()
                doc_snippets = self.knowledge_service.load_contents(
                    business_profile=conversation.business_profile,
                    knowledge_ids=remaining_upload_ids,
                    max_chars=inline_limit,
                )
                fetched_snippets.extend(doc_snippets)
                doc_duration = int((time.perf_counter() - doc_start) * 1000)
                tool_trace.append(
                    self._record_tool_invocation(
                        tool="load_document_contents",
                        query=query,
                        traits=query_traits,
                        duration_ms=doc_duration,
                        result_count=len(doc_snippets),
                        metadata={"upload_ids": [str(uid) for uid in remaining_upload_ids]},
                    )
                )

            if not fetched_snippets:
                logger.info(
                    "LLM requested knowledge ids %s but nothing was fetched; inserting system notice",
                    pending_requests,
                )
                cache_dirty = (
                    self._handle_missing_knowledge(
                        identifiers=pending_requests,
                        knowledge_payload=knowledge_payload,
                        knowledge_reads=knowledge_reads,
                        loaded_content_ids=loaded_content_ids,
                        knowledge_cache=cached_entries,
                    )
                    or cache_dirty
                )
                knowledge_loading = False
                continue

            for snippet in fetched_snippets:
                key = str(snippet.id)  # NOTE: for chunks this is the chunk UUID
                loaded_content_ids.add(key)
                snippet_lookup[key] = snippet
                payload = self._serialize_snippet(snippet)
                if snippet.content:
                    payload["content"] = snippet.content
                cache_dirty = self._cache_snippet(cached_entries, payload) or cache_dirty
                entry = cached_entries.get(key, payload)
                usage_changed, topics, usage_label = self._mark_snippet_usage(
                    entry=entry,
                    query=query,
                    turn_index=turn_index,
                    metadata_snapshot=metadata_snapshot,
                )
                if usage_changed:
                    cache_dirty = True
                    metadata_dirty = True
                prepared = self._prepare_prompt_snippet(entry)
                self._upsert_knowledge_payload(knowledge_payload, prepared)
                knowledge_reads.append(
                    {
                        "id": key,
                        "label": snippet.public_label or snippet.title,
                        "topics": topics,
                        "usage": usage_label,
                    }
                )
                logger.info("Loaded knowledge snippet id=%s label=%s", key, snippet.public_label or snippet.title)

        else:
            final_plan = plan_candidate

        _notify_stream_complete_once()
        metadata_snapshot["knowledge_query_cache"] = session_cache
        metadata_snapshot["knowledge_cache"] = cached_entries
        if tool_trace:
            tool_trace_history.extend(tool_trace)
            metadata_snapshot["knowledge_trace"] = tool_trace_history[-self.knowledge_trace_limit :]
            metadata_dirty = True
        if cache_dirty or metadata_dirty:
            conversation.metadata = metadata_snapshot
            conversation.save(update_fields=["metadata"])

        llm_plan = final_plan
        resolved_citations = tuple(snippet_lookup.values()) if snippet_lookup else tuple(citations)
        response_blocks: tuple[Mapping[str, object], ...] = tuple()
        if llm_plan:
            if knowledge_loading and on_status_change:
                on_status_change("responding")
            planned_actions = [
                action for action in llm_plan.planned_actions if action.action != ActionType.READ_KNOWLEDGE
            ]
            extractions = list(llm_plan.extractions)
            response_text = (llm_plan.response_text or "").strip()
            # ensure downstream tuples stay JSON-friendly
            response_blocks = tuple(llm_plan.response_blocks)
            llm_source = "provider"
        else:
            planned_actions, extractions = self._plan_actions(conversation=conversation, user_message=query)
            response_text = (llm_plan.response_text or "").strip() if llm_plan else ""
            llm_source = "heuristic"

        if response_text and not last_iteration_streamed:
            has_ready_context = any(bool(item.get("content")) for item in knowledge_payload)

            response_text = self._dedupe_response(
                conversation,
                response_text,
                knowledge_reads,
                has_ready_context=has_ready_context,
            )
            # Provider didn't stream; emit a single synthesized chunk and record it.
            streamed_chunks = []
            _emit_stream_chunk(response_text)


        placeholder_clean = None

        return StreamingTurnContext(
            conversation=conversation,
            response_text=response_text,
            planned_actions=tuple(planned_actions),
            extractions=tuple(extractions),
            resolved_citations=resolved_citations,
            knowledge_payload=tuple(knowledge_payload),
            knowledge_reads=tuple(knowledge_reads),
            knowledge_status=knowledge_status,
            knowledge_diagnostics=dict(knowledge_diagnostics or {}),
            knowledge_loading=knowledge_loading,
            placeholder_response=placeholder_clean,
            prompt_bundle=prompt_bundle,
            tool_trace=tuple(tool_trace),
            cached_snippet_count=len(cached_entries),
            llm_source=llm_source,
            streamed_chunks=tuple(streamed_chunks),
            llm_usage=llm_usage_summary,
            response_blocks=response_blocks,
        )

    def run_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_stream_complete: Callable[[], None] | None = None,
        on_spinner_update: Callable[[str], None] | None = None,
    ) -> AiOrchestratorPlan:
        """
        Build the orchestration plan for the latest customer message.

        This method is currently a thin wrapper that executes the streaming
        phase, captures the resulting context, and then finalizes the response
        into an AiOrchestratorPlan.
        """

        context = self.stream_turn(
            conversation=conversation,
            user_message=user_message,
            on_response_text_delta=on_response_text_delta,
            on_status_change=on_status_change,
            on_placeholder_response=on_placeholder_response,
            on_stream_complete=on_stream_complete,
            on_spinner_update=on_spinner_update,
        )
        return self.finalize_turn(context)

    def finalize_turn(self, context: StreamingTurnContext) -> AiOrchestratorPlan:
        """Finalize a completed streaming turn into a persistable plan."""

        return self._finalize_streaming_context(context)

    def _finalize_streaming_context(self, context: StreamingTurnContext) -> AiOrchestratorPlan:
        response_text = context.response_text or ""
        streamed_text = "".join(context.streamed_chunks).strip()
        if streamed_text:
            response_text = streamed_text

        response_text = self._maybe_prepend_not_found_notice(
            response_text,
            knowledge_status=context.knowledge_status,
        )
        response_text = self._maybe_append_ingestion_notice(
            response_text,
            context.knowledge_payload,
            knowledge_reads=context.knowledge_reads,
            knowledge_status=context.knowledge_status,
        )

        answer_confidence, confidence_reason = self._compute_answer_confidence(
            context.resolved_citations,
            knowledge_status=context.knowledge_status,
            knowledge_diagnostics=context.knowledge_diagnostics,
            knowledge_payload=context.knowledge_payload,
            knowledge_reads=context.knowledge_reads,
        )

        response_text = self._refine_response_by_confidence(
            response_text,
            answer_confidence=answer_confidence,
            knowledge_status=context.knowledge_status,
            knowledge_payload=context.knowledge_payload,
            knowledge_reads=context.knowledge_reads,
        )
        response_text = self._enforce_table_applicability_guardrails(
            response_text,
            citations=context.resolved_citations,
        )

        ingestion_warnings = self._collect_ingestion_warnings(
            context.knowledge_payload,
            knowledge_reads=context.knowledge_reads,
        )

        response_stream_text = response_text

        diagnostics = {
            "planned_action_count": len(context.planned_actions),
            "extraction_count": len(context.extractions),
            "citations": [snippet.title for snippet in context.resolved_citations],
            "llm_strategy": context.llm_source,
            "prompt_preview": context.prompt_bundle.system_prompt[:160] if context.prompt_bundle else "",
            "transcript_messages": len(context.prompt_bundle.transcript) if context.prompt_bundle else 0,
            "knowledge_reads": list(context.knowledge_reads),
            "knowledge_loading": context.knowledge_loading,
            "response_stream_text": response_stream_text,
            "cached_snippets": context.cached_snippet_count,
            "knowledge_status": context.knowledge_status,
            "answer_confidence": answer_confidence,
            "confidence_reason": confidence_reason,
            "tool_trace": list(context.tool_trace),
        }
        if ingestion_warnings:
            diagnostics["ingestion_warnings"] = ingestion_warnings
        # placeholder_response intentionally suppressed to avoid duplicating transient messages

        self._log_plan_summary(
            conversation=context.conversation,
            source=context.llm_source,
            planned_actions=context.planned_actions,
            extractions=context.extractions,
            diagnostics=diagnostics,
        )

        return AiOrchestratorPlan(
            response_text=response_text,
            citations=tuple(context.resolved_citations),
            planned_actions=context.planned_actions,
            extractions=context.extractions,
            diagnostics=diagnostics,
            ingestion_warnings=tuple(ingestion_warnings),
            response_blocks=tuple(context.response_blocks),
        )

    def _compute_answer_confidence(
        self,
        citations: Sequence[KnowledgeSnippet],
        *,
        knowledge_status: str | None,
        knowledge_diagnostics: Mapping[str, object] | None,
        knowledge_payload: Sequence[Mapping[str, object]] | None = None,
        knowledge_reads: Sequence[Mapping[str, object]] | None = None,
    ) -> tuple[float, str]:
        """
        Derive an answer-level confidence score from snippet scores and search metadata.
        Returns (score in [0,1], reason string).
        """
        # Hard floor when knowledge lookup did not succeed.
        if knowledge_status and knowledge_status != "ok":
            return 0.1, f"status={knowledge_status}"

        if not citations:
            return 0.2, "no_citations"

        scores: list[float] = []
        for snippet in citations:
            if snippet.confidence_score is None:
                continue
            try:
                scores.append(float(snippet.confidence_score))
            except (TypeError, ValueError):
                continue

        # If we have no usable scores, fall back to a neutral baseline.
        if not scores:
            base = 0.3
            reason_parts: list[str] = ["no_snippet_scores"]
        else:
            scores.sort(reverse=True)
            top1 = scores[0]
            top_k = scores[:3]
            mean_topk = sum(top_k) / len(top_k)
            # Blend peak and average to reward both a strong best hit and a solid top set.
            base = 0.5 * top1 + 0.5 * mean_topk
            reason_parts = [f"top1={top1:.3f}", f"mean_topk={mean_topk:.3f}"]

        path = ""
        if knowledge_diagnostics:
            raw_path = knowledge_diagnostics.get("path")
            if isinstance(raw_path, str):
                path = raw_path

        reason_parts.append(f"path={path or 'unknown'}")
        reason_parts.append(f"citations={len(citations)}")

        # Route-based adjustments.
        if path == "alias_exact":
            base = min(1.0, base + 0.15)
            reason_parts.append("alias_exact_bonus")
        elif path == "fallback":
            base *= 0.5
            reason_parts.append("fallback_penalty")

        # Snippet-count adjustments (thin context should reduce confidence).
        if len(citations) == 1 and path not in {"alias_exact"}:
            base *= 0.8
            reason_parts.append("single_snippet_penalty")

        ingestion_penalty_applied = False
        if knowledge_payload:
            if self._has_ingestion_red_flags(knowledge_payload, knowledge_reads=knowledge_reads):
                base *= 0.6
                ingestion_penalty_applied = True
                reason_parts.append("truncation_penalty")

        partial_penalty = False
        fully_indexed_sources = 0
        for snippet in citations:
            diag = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else None
            partial_flag = bool(getattr(snippet, "partial_index", False))
            truncated_rows = self._coerce_int((diag or {}).get("truncated_rows")) if diag else 0
            total_rows = 0
            indexed_rows = 0
            if diag:
                total_rows = self._coerce_int(diag.get("table_total_rows") or diag.get("table_source_rows"))
                indexed_rows = self._coerce_int(diag.get("table_indexed_rows"))
                if not indexed_rows:
                    indexed_rows = self._coerce_int(diag.get("table_row_cap"))
            row_ratio = 1.0
            if total_rows > 0:
                row_ratio = indexed_rows / total_rows if indexed_rows else 0.0
            severe_truncation = truncated_rows >= 200 or (total_rows > 0 and row_ratio < 0.9)
            if partial_flag or severe_truncation:
                partial_penalty = True
            elif (total_rows > 0 and row_ratio >= 0.99 and truncated_rows == 0 and not partial_flag) or (
                not diag and not partial_flag
            ):
                fully_indexed_sources += 1

        if partial_penalty:
            base *= 0.75
            reason_parts.append("partial_index_penalty")
        elif fully_indexed_sources and not ingestion_penalty_applied:
            base = min(1.0, base + 0.05)
            reason_parts.append("full_index_bonus")

        applicability_penalty = False
        applicability_soft_penalty = False
        for snippet in citations:
            diag = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else {}
            mode = str(
                diag.get("table_row_scope_reason")
                or diag.get("table_row_applicability_mode")
                or ""
            ).strip().lower()
            scope = diag.get("table_row_inferred_scope_columns")
            if not isinstance(scope, (list, tuple)):
                scope = diag.get("table_row_applies_to_columns")
            if not mode or not isinstance(scope, (list, tuple)):
                continue
            if mode in {"ambiguous", "scope_abstain"}:
                applicability_penalty = True
                break
            if mode.startswith("inferred_") or mode in {
                "scope_repeated_value_span",
                "scope_sparse_expansion",
                "scope_edge_completion",
            }:
                applicability_soft_penalty = True

        if applicability_penalty:
            base *= 0.82
            reason_parts.append("applicability_penalty")
        elif applicability_soft_penalty:
            base *= 0.92
            reason_parts.append("applicability_soft_penalty")

        base = max(0.0, min(1.0, base))
        reason_parts.append(f"status={knowledge_status or 'unknown'}")
        return round(base, 3), ", ".join(reason_parts)

    def _refine_response_by_confidence(
        self,
        response_text: str,
        *,
        answer_confidence: float,
        knowledge_status: str | None,
        knowledge_payload: Sequence[Mapping[str, object]],
        knowledge_reads: Sequence[Mapping[str, object]],
    ) -> str:
        """
        Light-touch behavior based on confidence:
        - For low confidence, append a clarifying prompt instead of overconfident answers.
        - For high confidence with healthy ingestion, trim common hedging phrases.
        """
        text = (response_text or "").strip()
        if not text:
            return response_text

        LOW_CONFIDENCE_THRESHOLD = 0.4
        HIGH_CONFIDENCE_THRESHOLD = 0.75

        has_ingestion_flags = self._has_ingestion_red_flags(knowledge_payload, knowledge_reads=knowledge_reads)

        # Low confidence: explicitly invite the user to narrow or correct the query.
        if answer_confidence < LOW_CONFIDENCE_THRESHOLD and knowledge_status == "ok":
            if has_ingestion_flags:
                clarifier = (
                    " I can only see part of the referenced tables right now, so tell me the exact row or details you need and I’ll flag a follow-up to pull the missing data."
                )
            else:
                clarifier = (
                    " If this doesn’t fully match what you expect, please tell me the exact document name, identifier, or date you’re asking about so I can check again more precisely."
                )
            if clarifier.strip() in text:
                return text
            return f"{text.rstrip()} {clarifier}".strip()

        # High confidence and no ingestion warnings: strip obvious hedging phrases.
        if answer_confidence >= HIGH_CONFIDENCE_THRESHOLD and not has_ingestion_flags:
            return self._strip_hedging_language(text)

        return text

    def _enforce_table_applicability_guardrails(
        self,
        response_text: str,
        *,
        citations: Sequence[KnowledgeSnippet],
    ) -> str:
        """
        Prevent false single-segment assertions when cited rows indicate broader scope.
        """
        text = (response_text or "").strip()
        if not text:
            return response_text

        scope_counts: dict[tuple[str, ...], int] = {}
        for snippet in citations:
            diagnostics = (
                snippet.source_diagnostics
                if isinstance(snippet.source_diagnostics, Mapping)
                else {}
            )
            raw_scope = diagnostics.get("table_row_inferred_scope_columns")
            if not isinstance(raw_scope, (list, tuple)):
                raw_scope = diagnostics.get("table_row_applies_to_columns")
            if not isinstance(raw_scope, (list, tuple)):
                continue
            cleaned_scope: list[str] = []
            for entry in raw_scope:
                value = str(entry or "").strip()
                if value:
                    cleaned_scope.append(value)
            if len(cleaned_scope) <= 1:
                continue
            key = tuple(dict.fromkeys(cleaned_scope))
            scope_counts[key] = int(scope_counts.get(key, 0)) + 1

        if not scope_counts:
            return text

        dominant_scope = max(scope_counts.items(), key=lambda item: item[1])[0]
        lower_text = text.lower()
        if "all segment" in lower_text:
            return text

        mention_pattern = re.compile(r"\b(?:appl(?:y|ies|icable)|for)\b")
        if not mention_pattern.search(lower_text):
            return text

        mentioned_scope: list[str] = []
        for label in dominant_scope:
            token = str(label).strip().lower()
            if not token:
                continue
            variants = {token, token.replace("_", " ")}
            if any(re.search(rf"\b{re.escape(variant)}\b", lower_text) for variant in variants if variant):
                mentioned_scope.append(label)

        unique_mentions = list(dict.fromkeys(mentioned_scope))
        if len(unique_mentions) != 1:
            return text

        correction = f"Based on the cited table row, this applies to: {', '.join(dominant_scope)}."
        if correction.lower() in lower_text:
            return text
        return f"{text.rstrip()}\n\n{correction}"

    def _strip_hedging_language(self, text: str) -> str:
        sentences = self._split_sentences(text)
        if not sentences:
            return text
        hedge_prefixes = (
            "i think ",
            "i believe ",
            "it seems ",
            "it looks like ",
            "from what i can tell ",
            "from what i can see ",
        )
        hedge_words = ("maybe ", "probably ")
        cleaned: list[str] = []
        for sentence in sentences:
            s = sentence.lstrip()
            lower = s.lower()
            for prefix in hedge_prefixes:
                if lower.startswith(prefix):
                    s = s[len(prefix):].lstrip()
                    lower = s.lower()
                    break
            for word in hedge_words:
                if lower.startswith(word):
                    s = s[len(word):].lstrip()
                    break
            cleaned.append(s)
        joined = " ".join(cleaned).strip()
        return joined or text

    def _cache_snippet(self, cache: dict[str, dict[str, object]], payload: Mapping[str, object]) -> bool:
        identifier = str(payload.get("id") or "")
        if not identifier:
            return False
        snapshot = cache.get(identifier)
        data = dict(payload)
        data["id"] = identifier
        data["status"] = data.get("status") or self._determine_snippet_status(data)
        preserved_keys = {
            "coverage",
            "last_used_for",
            "last_used_at",
            "last_active_turn",
            "last_customer_reference_turn",
            "pin",
            "topic_hints",
        }
        if snapshot:
            for key in preserved_keys:
                if key in snapshot and key not in data:
                    data[key] = snapshot[key]
        if snapshot == data:
            return False
        cache[identifier] = data
        return True

    def _strip_placeholder_overlap(self, placeholder: str | None, response_text: str) -> str:
        if not placeholder or not response_text:
            return response_text
        base = placeholder.strip()
        current = response_text.strip()
        if not base or not current:
            return response_text
        lower_base = base.lower()
        lower_current = current.lower()
        if lower_current.startswith(lower_base):
            return current[len(base) :].lstrip() or current
        return response_text

    def _dedupe_response(
        self,
        conversation: Conversation,
        response_text: str,
        knowledge_reads: Sequence[Mapping[str, object]],
        *,
        has_ready_context: bool,
    ) -> str:
        text = (response_text or "").strip()
        if not text:
            return text
        last_ai = (
            conversation.messages.filter(sender=ConversationSender.AI)
            .order_by("-sent_at", "-created_at")
            .first()
        )
        if not last_ai or not last_ai.body:
            return text
        previous_sentences = {sentence.lower() for sentence in self._split_sentences(last_ai.body)}
        new_sentences = self._split_sentences(text)
        just_read = {item.get("label", "").lower() for item in knowledge_reads if item.get("label")}
        filtered: list[str] = []
        for sentence in new_sentences:
            normalized = sentence.lower()
            if normalized and normalized in previous_sentences:
                continue
            # If we just read a doc, encourage immediate answers by skipping filler such as "I'll check".
            if (just_read or has_ready_context) and (
                normalized.startswith("i'll check")
                or normalized.startswith("i will check")
                or normalized.startswith("let me check")
                or "i’ll check" in normalized
            ):
                continue
            filtered.append(sentence)
        if filtered:
            return " ".join(filtered).strip()
        return text

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        parts = re.split(r"(?<=[.!?])\s+", cleaned)
        return [part.strip() for part in parts if part and part.strip()]

    @staticmethod
    def _determine_snippet_status(snapshot: Mapping[str, object]) -> str:
        if snapshot.get("system_notice") == "missing_document":
            return "unavailable"
        state = (snapshot.get("read_state") or "").strip().lower()
        if state == KNOWLEDGE_READ_STATE_FULL:
            return "ready"
        if state == KNOWLEDGE_READ_STATE_PREVIEW:
            return "preview"
        return "summary-only"

    # REPLACE the entire _prepare_prompt_snippet function
    def _prepare_prompt_snippet(self, entry: Mapping[str, object]) -> dict[str, object]:
        payload = dict(entry)

        # Normalize status/read_state, but DO NOT overwrite the explicit read_state
        payload["status"] = payload.get("status") or self._determine_snippet_status(payload)
        payload["read_state"] = (payload.get("read_state") or KNOWLEDGE_READ_STATE_SUMMARY)

        if "truncation_note" not in payload:
            diagnostics = payload.get("source_diagnostics") if isinstance(payload.get("source_diagnostics"), Mapping) else None
            note = self._build_truncation_note_from_diagnostics(
                diagnostics,
                label=payload.get("public_label") or payload.get("title"),
                partial_index=bool(payload.get("partial_index")),
            )
            if note:
                payload["truncation_note"] = note

        # Ensure chunk metadata is preserved (pass-through if present)
        if "upload_id" in entry and entry["upload_id"]:
            payload["upload_id"] = str(entry["upload_id"])
        if "chunk_id" in entry and entry["chunk_id"]:
            payload["chunk_id"] = str(entry["chunk_id"])
        if "chunk_index" in entry and entry["chunk_index"] is not None:
            payload["chunk_index"] = int(entry["chunk_index"])

        payload.setdefault("coverage", [])
        payload["pin"] = bool(payload.get("pin"))

        # Remove transient fields that shouldn't hit the prompt
        payload.pop("last_active_turn", None)
        payload.pop("last_customer_reference_turn", None)
        return payload

    def _refresh_table_sample(
        self,
        cached_entry: dict[str, object],
        query: str,
    ) -> dict[str, object]:
        """
        For cached table chunks, re-sample rows based on the current query.

        This ensures follow-up questions get query-optimized table data instead
        of stale row samples from the original query. The cache stores the table
        reference (chunk_id, is_table_chunk) but not the query-specific rows.

        Args:
            cached_entry: The cached snippet dict (from knowledge_cache).
            query: The current user query for row relevance scoring.

        Returns:
            Updated dict with fresh structuredTables if this is a table chunk,
            otherwise the original cached_entry unchanged.
        """
        # Only refresh table chunks that need it
        if not cached_entry.get("is_table_chunk") and not cached_entry.get("needs_table_refresh"):
            return cached_entry

        chunk_id = cached_entry.get("chunk_id")
        if not chunk_id:
            return cached_entry

        # Fetch the chunk from DB
        try:
            from apps.knowledge.models import KnowledgeUploadChunk

            chunk = KnowledgeUploadChunk.objects.filter(id=chunk_id).select_related("upload").first()
            if not chunk:
                return cached_entry
        except Exception:
            return cached_entry

        # Re-sample with current query for relevance-ranked rows
        fresh_tables = self._table_row_sample(
            chunk,
            max_columns=6,
            max_rows=3,  # Allow more rows for comprehensive follow-up queries
            query=query,
        )

        # Update the cached entry with fresh data
        refreshed = dict(cached_entry)
        refreshed["structuredTables"] = list(fresh_tables)
        refreshed["needs_table_refresh"] = False
        return refreshed

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




    def _should_include_snippet(
        self,
        entry: Mapping[str, object],
        *,
        turn_index: int,
        visitor_mentions: set[str],
    ) -> bool:
        identifier = entry.get("id")
        if not identifier:
            return False
        if entry.get("pin"):
            return True
        if identifier in visitor_mentions:
            return True
        last_turn = int(entry.get("last_active_turn") or 0)
        if last_turn == 0:
            return True
        return (turn_index - last_turn) <= RECENT_SNIPPET_TURN_WINDOW

    def _detect_snippet_mentions(
        self,
        *,
        query: str,
        cached_entries: Mapping[str, dict[str, object]],
        turn_index: int,
    ) -> tuple[set[str], bool]:
        mentions: set[str] = set()
        updated = False
        normalized_query = QueryNormalizer._normalize_query_text(query).lower()
        tokenized = {token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized_query) if token}
        for identifier, entry in cached_entries.items():
            keywords = self._extract_snippet_keywords(entry)
            if not keywords:
                continue
            if self._text_mentions_keywords(normalized_query, tokenized, keywords):
                mentions.add(identifier)
                if entry.get("last_customer_reference_turn") != turn_index:
                    entry["last_customer_reference_turn"] = turn_index
                    entry["last_active_turn"] = turn_index
                    updated = True
        return mentions, updated

    @staticmethod
    def _extract_snippet_keywords(entry: Mapping[str, object]) -> set[str]:
        keywords: set[str] = set()
        for field in ("public_label", "title"):
            label = entry.get(field)
            if isinstance(label, str) and label.strip():
                normalized = QueryNormalizer._normalize_query_text(label).lower()
                for token in QueryNormalizer._TOKEN_SPLIT.split(normalized):
                    if token and len(token) >= 4:
                        keywords.add(token)
        topic_hints = entry.get("topic_hints")
        if isinstance(topic_hints, (list, tuple, set)):
            for hint in topic_hints:
                if isinstance(hint, str) and hint.strip():
                    keywords.add(hint.strip().lower())
        coverage = entry.get("coverage")
        if isinstance(coverage, (list, tuple, set)):
            for cov in coverage:
                if isinstance(cov, str) and cov.strip():
                    keywords.add(cov.strip().lower())
        return keywords

    @staticmethod
    def _text_mentions_keywords(text: str, tokens: set[str], keywords: set[str]) -> bool:
        for keyword in keywords:
            if not keyword:
                continue
            if " " in keyword:
                if keyword in text:
                    return True
            else:
                if keyword in tokens:
                    return True
        return False

    @staticmethod
    def _merge_topics(existing: Sequence[str] | None, new_topics: Sequence[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        sources = list(existing or []) + list(new_topics or [])
        for topic in sources:
            normalized = str(topic).strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        return merged

    def _infer_topics_from_text(self, text: str) -> tuple[str, ...]:
        normalized_text = QueryNormalizer._normalize_query_text(text).lower()
        tokens = {token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized_text) if token}
        matches: list[str] = []
        for topic, keywords in TOPIC_KEYWORD_MAP.items():
            if self._text_mentions_keywords(normalized_text, tokens, set(keywords)):
                matches.append(topic)
        if matches:
            ordered: list[str] = []
            seen: set[str] = set()
            for topic in matches:
                if topic not in seen:
                    seen.add(topic)
                    ordered.append(topic)
            return tuple(ordered)
        return tuple()

    @staticmethod
    def _detect_product_label(query: str, snapshot: Mapping[str, object]) -> str | None:
        label = (snapshot.get("public_label") or snapshot.get("title") or "").strip()
        if not label:
            return None
        normalized_query = QueryNormalizer._normalize_query_text(query).lower()
        normalized_label = QueryNormalizer._normalize_query_text(label).lower()
        tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized_label) if len(token) >= 4]
        if not tokens:
            return None
        for token in tokens:
            if token and token in normalized_query:
                return label
        return None

    def _mark_snippet_usage(
        self,
        *,
        entry: dict[str, object],
        query: str,
        turn_index: int,
        metadata_snapshot: dict,
    ) -> tuple[bool, list[str], str]:
        changed = False
        entry_status = entry.get("status")
        status = entry_status or self._determine_snippet_status(entry)
        if entry_status != status:
            entry["status"] = status
            changed = True
        entry["last_active_turn"] = turn_index
        entry["last_used_at"] = timezone.now().isoformat()
        topics = list(self._infer_topics_from_text(query))
        topic_hints = entry.get("topic_hints")
        if not topics and isinstance(topic_hints, (list, tuple)):
            normalized_hints = [str(hint).strip().lower() for hint in topic_hints if isinstance(hint, str) and hint.strip()]
            if normalized_hints:
                topics = [normalized_hints[0]]
        if not topics:
            topics = ["details"]
        existing = entry.get("coverage")
        existing_coverage = existing if isinstance(existing, list) else (existing or [])
        merged = self._merge_topics(existing_coverage, topics)
        if merged != existing_coverage:
            entry["coverage"] = merged
            changed = True
        product_label = self._detect_product_label(query, entry)
        topic_display = "/".join(topics)
        usage_label = topic_display
        if product_label:
            usage_label = f"{product_label} {topic_display}".strip()
        if usage_label and entry.get("last_used_for") != usage_label:
            entry["last_used_for"] = usage_label
            changed = True
        if self._append_delivery_log(
            metadata_snapshot=metadata_snapshot,
            identifier=str(entry.get("id")),
            label=entry.get("public_label") or entry.get("title") or "Knowledge",
            usage_label=usage_label,
            topics=topics,
        ):
            changed = True
        return changed, topics, usage_label

    @staticmethod
    def _append_delivery_log(
        *,
        metadata_snapshot: dict,
        identifier: str,
        label: str,
        usage_label: str,
        topics: Sequence[str],
    ) -> bool:
        if not identifier:
            return False
        log = metadata_snapshot.get("knowledge_delivery_log")
        if not isinstance(log, list):
            log = []
        entry = {
            "id": identifier,
            "label": label,
            "usage": usage_label,
            "topics": list(topics),
            "used_at": timezone.now().isoformat(),
        }
        log.append(entry)
        metadata_snapshot["knowledge_delivery_log"] = log[-LEDGER_LOG_LIMIT:]
        return True

    # ------------------------------------------------------------------
    # Internal helpers

    def _hydrate_permissions(self) -> None:
        permissions = self.agent.action_permissions.all() if hasattr(self.agent, "action_permissions") else []
        for perm in permissions:
            self._permission_cache[str(perm.action_key)] = perm.is_enabled

    def _is_enabled(self, action: ActionType) -> bool:
        if action.value in self._permission_cache:
            return self._permission_cache[action.value]
        descriptor = ACTION_REGISTRY.get(action)
        return descriptor.default_enabled if descriptor else False

    def _actions_catalog(self) -> list[Mapping[str, str]]:
        catalog: list[Mapping[str, str]] = []
        for action, descriptor in ACTION_REGISTRY.items():
            catalog.append(
                {
                    "key": action.value,
                    "label": descriptor.label,
                    "description": descriptor.description,
                    "enabled": self._is_enabled(action),
                }
            )
        return catalog

    @staticmethod
    def _recent_messages(conversation: Conversation, limit: int = 8) -> Sequence:
        qs = conversation.messages.all().order_by("-sent_at", "-created_at")[:limit]
        return tuple(reversed(tuple(qs)))

    @classmethod
    # REPLACE the entire _serialize_snippet function
    def _serialize_snippet(cls, snippet: KnowledgeSnippet) -> dict[str, object]:
        """
        Turn a KnowledgeSnippet into a stable dict for the prompt ledger/cache.
        We preserve chunk metadata so the LLM can request chunk reads.
        """
        payload: dict[str, object] = {
            "id": str(snippet.id),
            "title": snippet.title,
            "summary": snippet.summary,
            "source": snippet.source,
            "content": snippet.content or "",
            "content_mode": snippet.content_mode if snippet.content_mode else ("full" if snippet.content else None),
            "public_label": snippet.public_label or "",
            # For table chunks, don't cache query-specific row samples.
            # They'll be refreshed at query time via _refresh_table_sample().
            "structuredTables": [] if snippet.is_table_chunk else list(snippet.structured_tables or ()),
            "needs_table_refresh": bool(snippet.is_table_chunk),
            "issues": list(snippet.issues or ()),
            "pageSummaries": list(snippet.page_summaries or ()),
            "read_state": snippet.read_state or KNOWLEDGE_READ_STATE_SUMMARY,
            "topic_hints": list(snippet.topic_hints or ()),
            "is_pinned": bool(snippet.is_pinned),
            "structured_table_count": int(snippet.structured_table_count or 0),
            "issue_count": int(snippet.issue_count or 0),
            "supplemental_sections": list(snippet.supplemental_sections or ()),
            "page_number": snippet.page_number,
            "page_mode": snippet.page_mode,
        }
        if snippet.structured_table_hint:
            payload["structured_table_hint"] = snippet.structured_table_hint
        if snippet.search_stage:
            payload["search_stage"] = snippet.search_stage
        if snippet.confidence_score is not None:
            payload["confidence_score"] = float(snippet.confidence_score)
        payload["truncated"] = bool(snippet.truncated)
        payload["source_diagnostics"] = dict(snippet.source_diagnostics or {})
        if payload["source_diagnostics"].get("table_read_only"):
            payload["table_read_only"] = True
        partial_index = bool(snippet.partial_index)
        payload["partial_index"] = partial_index
        payload["aliases"] = list(snippet.aliases or ())
        truncation_note = cls._build_truncation_note_from_diagnostics(
            payload["source_diagnostics"],
            label=payload.get("public_label") or payload.get("title"),
            partial_index=partial_index,
        )
        if truncation_note:
            payload["truncation_note"] = truncation_note
        # NEW: carry chunk identity
        if snippet.upload_id:
            payload["upload_id"] = str(snippet.upload_id)
        if snippet.chunk_id:
            payload["chunk_id"] = str(snippet.chunk_id)
        if snippet.chunk_index is not None:
            payload["chunk_index"] = int(snippet.chunk_index)
        if snippet.entity_type:
            payload["entity_type"] = snippet.entity_type
        if snippet.entity_name:
            payload["entity_name"] = snippet.entity_name
        if snippet.entity_business:
            payload["entity_business"] = snippet.entity_business
        payload["is_table_chunk"] = bool(snippet.is_table_chunk)
        if snippet.table_id:
            payload["table_id"] = snippet.table_id
        if snippet.evidence_group_id:
            payload["evidence_group_id"] = snippet.evidence_group_id
        if snippet.evidence_type:
            payload["evidence_type"] = snippet.evidence_type
        if snippet.representation:
            payload["representation"] = snippet.representation
        # status will be normalized later by _determine_snippet_status
        return payload

    @staticmethod
    def _coerce_int(value: object) -> int:
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _format_count(value: int) -> str:
        if value <= 0:
            return "0"
        for threshold, suffix in ((1_000_000, "M"), (1_000, "k")):
            if value >= threshold:
                scaled = value / threshold
                scaled_str = f"{scaled:.1f}" if scaled % 1 else str(int(scaled))
                scaled_str = scaled_str.rstrip("0").rstrip(".")
                return f"{scaled_str}{suffix}"
        return f"{value:,}"

    @classmethod
    def _build_truncation_note_from_diagnostics(
        cls,
        diagnostics: Mapping[str, object] | None,
        *,
        label: str | None,
        partial_index: bool = False,
    ) -> str:
        diag = diagnostics if isinstance(diagnostics, Mapping) else {}
        label_text = (label or "This document").strip() or "This document"
        truncated_rows = cls._coerce_int(diag.get("truncated_rows"))
        truncated_tables = cls._coerce_int(diag.get("truncated_tables"))
        truncated_columns = cls._coerce_int(diag.get("truncated_columns"))
        truncated_entities = cls._coerce_int(diag.get("truncated_entities"))
        total_rows = cls._coerce_int(diag.get("table_total_rows") or diag.get("table_source_rows"))
        indexed_rows = cls._coerce_int(diag.get("table_indexed_rows"))
        row_cap = cls._coerce_int(diag.get("table_row_cap"))
        if not indexed_rows and row_cap:
            indexed_rows = row_cap
        partial_tables = cls._coerce_int(diag.get("table_partial_tables"))
        partial_flag = partial_index or bool(diag.get("partial_index"))

        note = ""
        if indexed_rows and total_rows and indexed_rows < total_rows:
            note = (
                f"{label_text} only indexes the first {cls._format_count(indexed_rows)} of ~{cls._format_count(total_rows)} rows; "
                "later rows are unknown"
            )
        elif truncated_rows and row_cap:
            note = (
                f"{label_text} stops after {cls._format_count(row_cap)} rows and skips at least "
                f"{cls._format_count(truncated_rows)} later rows"
            )
        elif truncated_rows:
            note = f"{label_text} dropped {cls._format_count(truncated_rows)} rows beyond the ingest cap"
        elif partial_flag and indexed_rows:
            note = (
                f"{label_text} is partially indexed ({cls._format_count(indexed_rows)} rows captured); "
                "request another source if you need the remaining rows"
            )
        elif partial_flag:
            note = f"{label_text} is only partially indexed; some rows or records may be missing"
        elif truncated_entities:
            note = (
                f"{label_text} only captured a subset of structured records; "
                f"{cls._format_count(truncated_entities)} records were left out"
            )
        elif truncated_columns:
            note = f"{label_text} omitted {cls._format_count(truncated_columns)} columns, so some fields are missing"
        elif truncated_tables or partial_tables:
            count = truncated_tables or partial_tables
            note = f"{label_text} skipped {cls._format_count(count)} tables or sheets due to size limits"

        note = note.strip()
        if note and not note.endswith("."):
            note = f"{note}."
        return note

    @staticmethod
    def _normalize_issue_severity(issue: Mapping[str, object]) -> str | None:
        severity = str(issue.get("severity") or "").strip().lower()
        if severity and severity not in {"warning", "error"}:
            return None
        return severity or "warning"

    @classmethod
    def _issue_warning_payloads(
        cls,
        issues: Sequence[Mapping[str, object]] | None,
        *,
        label: str,
        upload_id: str,
    ) -> list[dict[str, object]]:
        warnings: list[dict[str, object]] = []
        if not isinstance(issues, Sequence) or isinstance(issues, (str, bytes)):
            return warnings
        for issue in issues:
            if not isinstance(issue, Mapping):
                continue
            severity = cls._normalize_issue_severity(issue)
            if not severity:
                continue
            raw_code = issue.get("issue_code") or issue.get("code") or "ingestion_issue"
            code = str(raw_code).strip().lower()
            if "truncate" not in code and "missing" not in code:
                continue
            details = (
                issue.get("summary")
                or issue.get("message")
                or issue.get("details")
                or issue.get("explanation")
                or issue.get("label")
            )
            if not details:
                details = f"{label} reported ingestion issue {raw_code}."
            warnings.append(
                {
                    "upload_id": str(upload_id),
                    "label": label,
                    "type": str(raw_code) or "ingestion_issue",
                    "severity": severity,
                    "details": str(details),
                }
            )
        return warnings

    @classmethod
    def _diagnostic_warning_payload(
        cls,
        *,
        label: str,
        upload_id: str,
        diagnostics: Mapping[str, object] | None,
        partial_index: bool,
        truncation_note: str | None,
    ) -> dict[str, object] | None:
        diag = diagnostics if isinstance(diagnostics, Mapping) else {}
        note = (truncation_note or cls._build_truncation_note_from_diagnostics(diag, label=label, partial_index=partial_index)).strip()
        if not note:
            return None
        truncated_rows = cls._coerce_int(diag.get("truncated_rows"))
        truncated_entities = cls._coerce_int(diag.get("truncated_entities"))
        truncated_columns = cls._coerce_int(diag.get("truncated_columns"))
        truncated_tables = cls._coerce_int(diag.get("truncated_tables"))
        partial_tables = cls._coerce_int(diag.get("table_partial_tables"))
        indexed_rows = cls._coerce_int(diag.get("table_indexed_rows")) or cls._coerce_int(diag.get("table_row_cap"))
        total_rows = cls._coerce_int(diag.get("table_total_rows") or diag.get("table_source_rows"))

        warning_type = "ingestion_truncation"
        if truncated_rows or (indexed_rows and total_rows and indexed_rows < total_rows) or partial_index:
            warning_type = "table_rows_truncated"
        elif truncated_entities:
            warning_type = "structured_records_truncated"
        elif truncated_columns:
            warning_type = "table_columns_truncated"
        elif truncated_tables or partial_tables:
            warning_type = "table_count_truncated"

        return {
            "upload_id": str(upload_id),
            "label": label,
            "type": warning_type,
            "severity": "warning",
            "details": note,
        }

    def _collect_ingestion_warnings(
        self,
        knowledge_payload: Sequence[Mapping[str, object]],
        *,
        knowledge_reads: Sequence[Mapping[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        if not knowledge_reads:
            return []
        relevant_ids: set[str] = set()
        for read in knowledge_reads:
            identifier = read.get("id")
            if identifier:
                relevant_ids.add(str(identifier))
        if not relevant_ids:
            return []

        warnings: list[dict[str, object]] = []
        for entry in knowledge_payload:
            entry_id = str(entry.get("id") or "")
            if entry_id not in relevant_ids:
                continue
            label = entry.get("public_label") or entry.get("title") or "Knowledge source"
            upload_id = str(entry.get("upload_id") or entry_id)
            warnings.extend(
                self._issue_warning_payloads(
                    entry.get("issues"),
                    label=label,
                    upload_id=upload_id,
                )
            )
            diagnostics = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
            partial_index = bool(entry.get("partial_index"))
            trunc_note_val = entry.get("truncation_note")
            trunc_note = trunc_note_val if isinstance(trunc_note_val, str) else None
            diag_warning = self._diagnostic_warning_payload(
                label=label,
                upload_id=upload_id,
                diagnostics=diagnostics,
                partial_index=partial_index,
                truncation_note=trunc_note if isinstance(trunc_note, str) else None,
            )
            if diag_warning:
                warnings.append(diag_warning)
        return warnings


    @staticmethod
    def _upsert_knowledge_payload(payload: list[dict], snippet_payload: Mapping[str, object]) -> None:
        # Do not surface suppressed/system notices into the prompt ledger
        if snippet_payload.get("suppress_in_prompt"):
            return
        target_id = snippet_payload.get("id")
        for existing in payload:
            if existing.get("id") == target_id:
                existing.update(snippet_payload)
                break
        else:
            payload.append(dict(snippet_payload))

    def _enforce_snippet_budget(self, payload: list[dict[str, object]], *, budget: int | None = None) -> list[dict[str, object]]:
        limit = budget if budget is not None else self.knowledge_snippet_budget
        if limit <= 0 or len(payload) <= limit:
            return payload
        priorities = {
            "system_notice": 0,
            "alias_exact": 1,
            "alias_fts": 2,
            "vector_ann": 3,
            "content_fts": 4,
            "content_trigram": 4,
            "fallback": 5,
        }
        ranked = sorted(
            payload,
            key=lambda item: (
                0 if item.get("pin") else priorities.get(item.get("search_stage") or item.get("status"), 6),
                -float(item.get("confidence_score") or 0.0),
            ),
        )
        keep = {entry.get("id") for entry in ranked[:limit]}
        payload[:] = [entry for entry in payload if entry.get("id") in keep]
        return payload

    @staticmethod
    def _identifier_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
        identifier_like: list[str] = []
        for token in tokens:
            if any(ch.isdigit() for ch in token) or "-" in token or "_" in token:
                identifier_like.append(token)
        return tuple(identifier_like)

    def _record_tool_invocation(
        self,
        *,
        tool: str,
        query: str,
        traits: QueryTraits,
        duration_ms: int,
        result_count: int,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        entry = {
            "tool": tool,
            "query": query[:160],
            "token_count": traits.token_count,
            "identifier_like": traits.is_identifier_like,
            "duration_ms": duration_ms,
            "result_count": result_count,
            "timestamp": timezone.now().isoformat(),
        }
        if metadata:
            entry["metadata"] = dict(metadata)
        return entry

    @staticmethod
    def _fallback_notice_snippet(reason: str, summary: str | None = None) -> dict[str, object]:
        return {
            "id": f"notice:{reason}",
            "title": "Knowledge fallback in use",
            "summary": summary
            or "Only generic snippets were available for this query. Phrase the answer cautiously or ask for a specific identifier.",
            "status": "system_notice",
            "read_state": KNOWLEDGE_READ_STATE_SUMMARY,
            "search_stage": "system_notice",
            "confidence_score": 0.0,
            "truncated": False,
        }

    @staticmethod
    def _clarify_identifier_notice() -> dict[str, object]:
        return {
            "id": "notice:clarify-identifier",
            "title": "Need clearer identifier",
            "summary": "Similar aliases exist but none are confident matches. Ask the visitor to confirm the exact identifier or provide more digits.",
            "status": "system_notice",
            "read_state": KNOWLEDGE_READ_STATE_SUMMARY,
            "search_stage": "system_notice",
            "confidence_score": 0.0,
        }

    @staticmethod
    def _chunk_read_budget_notice(exceeded: int) -> dict[str, object]:
        return {
            "id": f"notice:chunk-budget:{exceeded}",
            "title": "Chunk read budget reached",
            "summary": "Only the first few chunk reads were executed this turn to control cost. Request fewer IDs or ask for more specific identifiers.",
            "status": "system_notice",
            "read_state": KNOWLEDGE_READ_STATE_SUMMARY,
            "search_stage": "system_notice",
        }

    def _maybe_prepend_not_found_notice(self, text: str, *, knowledge_status: str) -> str:
        if knowledge_status != "not_found":
            return text
        normalized = (text or "").lower()
        if "not found" in normalized and "knowledge" in normalized:
            return text
        notice = "I couldn’t find that identifier in the knowledge base. "
        return f"{notice}{text}".strip() if text else notice.strip()

    def _maybe_append_ingestion_notice(
        self,
        text: str,
        knowledge_payload: Sequence[Mapping[str, object]],
        knowledge_reads: Sequence[Mapping[str, object]] | None = None,
        *,
        knowledge_status: str | None = None,
    ) -> str:
        # Only surface ingestion warnings when we actually read documents this turn
        # and the search itself succeeded. This avoids leaking historical or
        # unrelated ingestion issues into otherwise confident answers.
        if knowledge_status and knowledge_status != "ok":
            return text
        if not knowledge_reads:
            return text
        if not self._has_ingestion_red_flags(knowledge_payload, knowledge_reads=knowledge_reads):
            return text
        notice = self._summarize_ingestion_truncation(knowledge_payload, knowledge_reads=knowledge_reads)
        if not notice:
            notice = "Some of the ingested files were truncated, so certain details may be missing."
        # Prepend a space when appending to an existing answer for readability.
        notice = f" {notice}".rstrip()
        return (text or "") + notice if text else notice.strip()

    @classmethod
    def _has_ingestion_red_flags(
        cls,
        knowledge_payload: Sequence[Mapping[str, object]],
        *,
        knowledge_reads: Sequence[Mapping[str, object]] | None = None,
    ) -> bool:
        # Restrict checks to documents that were actually read this turn.
        relevant_ids: set[str] = set()
        if knowledge_reads:
            for item in knowledge_reads:
                identifier = item.get("id")
                if identifier:
                    relevant_ids.add(str(identifier))
        if not relevant_ids:
            return False
        # Thresholds to treat truncation as user-visible red flags.
        ENTITY_TRUNC_THRESHOLD = 20
        ROW_TRUNC_THRESHOLD = 200
        TABLE_TRUNC_THRESHOLD = 5
        COL_TRUNC_THRESHOLD = 50
        for entry in knowledge_payload:
            entry_id = str(entry.get("id") or "")
            if entry_id not in relevant_ids:
                continue
            issues = entry.get("issues") or []
            for issue in issues:
                if not isinstance(issue, Mapping):
                    continue
                raw_code = issue.get("issue_code") or issue.get("code") or ""
                code = str(raw_code).lower()
                raw_severity = issue.get("severity")
                severity = str(raw_severity).lower() if raw_severity is not None else ""
                # Only treat truncate/missing issues as red flags when severity is warning/error.
                # For legacy issues without severity, default to warning to avoid hiding serious problems.
                if "truncate" in code or "missing" in code:
                    if not severity or severity in {"warning", "error"}:
                        return True
            # Fall back to coarse truncation metrics for severe table/JSON truncation.
            diagnostics = entry.get("source_diagnostics") or {}
            if isinstance(diagnostics, Mapping):
                truncated_entities = cls._coerce_int(diagnostics.get("truncated_entities"))
                truncated_rows = cls._coerce_int(diagnostics.get("truncated_rows"))
                truncated_tables = cls._coerce_int(diagnostics.get("truncated_tables"))
                truncated_columns = cls._coerce_int(diagnostics.get("truncated_columns"))

                if truncated_entities >= ENTITY_TRUNC_THRESHOLD:
                    return True
                if truncated_rows >= ROW_TRUNC_THRESHOLD:
                    return True
                if truncated_tables >= TABLE_TRUNC_THRESHOLD:
                    return True
                if truncated_columns >= COL_TRUNC_THRESHOLD:
                    return True
        return False

    @classmethod
    def _summarize_ingestion_truncation(
        cls,
        knowledge_payload: Sequence[Mapping[str, object]],
        *,
        knowledge_reads: Sequence[Mapping[str, object]] | None = None,
    ) -> str:
        """
        Produce a more specific ingestion notice based on which truncation signals
        are present for the documents actually read this turn.
        """
        if not knowledge_reads:
            return ""
        relevant_ids: set[str] = set()
        for item in knowledge_reads:
            identifier = item.get("id")
            if identifier:
                relevant_ids.add(str(identifier))
        if not relevant_ids:
            return ""

        entry_notes: list[str] = []
        total_entities = total_columns = total_rows = total_tables = 0
        for entry in knowledge_payload:
            entry_id = str(entry.get("id") or "")
            if entry_id not in relevant_ids:
                continue
            diagnostics = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
            partial_index = bool(entry.get("partial_index"))
            label = entry.get("public_label") or entry.get("title")
            note_val = entry.get("truncation_note")
            note = note_val if isinstance(note_val, str) else ""
            if not note:
                note = cls._build_truncation_note_from_diagnostics(
                    diagnostics,
                    label=label,
                    partial_index=partial_index,
                )
                if note and isinstance(entry, dict):
                    entry.setdefault("truncation_note", note)
            if note:
                entry_notes.append(note)
            if diagnostics:
                total_entities += cls._coerce_int(diagnostics.get("truncated_entities"))
                total_columns += cls._coerce_int(diagnostics.get("truncated_columns"))
                total_rows += cls._coerce_int(diagnostics.get("truncated_rows"))
                total_tables += cls._coerce_int(diagnostics.get("truncated_tables"))

        if entry_notes:
            if len(entry_notes) == 1:
                return entry_notes[0]
            summary = " ".join(entry_notes[:2]).strip()
            remaining = len(entry_notes) - 2
            if remaining > 0:
                plural = "sources" if remaining > 1 else "source"
                summary = f"{summary} {remaining} more {plural} also have partial coverage."
            return summary.strip()
        if total_rows or total_tables:
            return (
                "Some table rows in the referenced documents were truncated due to size limits, so details from later rows may be missing."
            )
        if total_entities:
            return (
                "Some structured records in the referenced documents were truncated due to size limits, so the answer may not reflect all records."
            )
        if total_columns:
            return (
                "Some table columns in the referenced documents were truncated due to size limits, so certain fields may be missing."
            )
        return ""



    def _handle_missing_knowledge(
        self,
        *,
        identifiers: Sequence[str],
        knowledge_payload: list[dict[str, object]],
        knowledge_reads: list[dict[str, object]],
        loaded_content_ids: set[str],
        knowledge_cache: dict[str, dict[str, object]],
    ) -> bool:
        if not identifiers:
            return False
        dirty = False
        for raw_id in identifiers:
            notice = self._missing_knowledge_notice(raw_id)
            self._upsert_knowledge_payload(knowledge_payload, notice)
            knowledge_reads.append(
                {
                    "id": notice["id"],
                    "label": notice.get("public_label") or notice.get("title") or "missing_document",
                }
            )
            loaded_content_ids.add(notice["id"])
            dirty = self._cache_snippet(knowledge_cache, notice) or dirty
        return dirty

    @staticmethod
    def _missing_knowledge_notice(identifier: str | None) -> dict[str, str]:
        clean_id = (identifier or "missing-document").strip()
        display_fragment = clean_id[:8] if clean_id else "doc"
        return {
            "id": f"missing:{display_fragment}",  # avoid numeric IDs like "1"
            "title": "Document unavailable",
            "public_label": f"Doc {display_fragment} unavailable",
            "summary": (
                "System notice: The requested knowledge resource could not be retrieved. "
                "Let the visitor know the latest document is unavailable and offer to follow up once it is restored."
            ),
            "content": (
                "System directive: Inform the visitor that the referenced document is temporarily unavailable, "
                "reassure them you will monitor for updates, and offer alternative guidance or escalation."
            ),
            "system_notice": "missing_document",
            "data_ready": False,
            "read_state": KNOWLEDGE_READ_STATE_SUMMARY,
            "status": "unavailable",
            "coverage": [],
            "suppress_in_prompt": True,  # <-- do not surface in ledger
        }

    def _invoke_llm(
        self,
        bundle: PromptBundle,
        *,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_reasoning_event: Callable[[Mapping[str, object]], None] | None = None,
        reasoning_label: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> LlmPlan | None:
        if not self.provider:
            logger.info("LLM provider is not configured; using heuristic planner.")
            return None
        try:
            logger.info("Invoking LLM provider %s", self.provider.__class__.__name__)
            call_id = f"llm_{uuid.uuid4().hex}"
            label_value = (reasoning_label or "Answer").strip() or "Answer"

            reasoning_started = False
            reasoning_ended = False

            def _emit_reasoning_event(event_type: str, *, delta: str | None = None) -> None:
                if not on_reasoning_event:
                    return
                payload: dict[str, object] = {
                    "type": event_type,
                    "call_id": call_id,
                    "stage": "legacy_llm",
                    "label": label_value,
                }
                if delta is not None:
                    payload["delta"] = delta
                try:
                    on_reasoning_event(payload)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("on_reasoning_event callback failed")

            def _should_skip_reasoning_end() -> bool:
                return bool(should_cancel and should_cancel())

            def _maybe_end_reasoning() -> None:
                nonlocal reasoning_ended
                if reasoning_ended or not reasoning_started:
                    return
                if _should_skip_reasoning_end():
                    return
                reasoning_ended = True
                _emit_reasoning_event("reasoning_end")

            def _on_reasoning_delta(delta: str) -> None:
                nonlocal reasoning_started
                if not delta:
                    return
                if should_cancel and should_cancel():
                    return
                reasoning_started = True
                _emit_reasoning_event("reasoning_delta", delta=delta)

            def _on_stream_delta(chunk: str) -> None:
                _maybe_end_reasoning()
                if on_response_text_delta:
                    on_response_text_delta(chunk)

            raw = self.provider.generate(
                bundle,
                on_stream_delta=_on_stream_delta if (on_response_text_delta and on_reasoning_event) else on_response_text_delta,
                on_reasoning_delta=_on_reasoning_delta if on_reasoning_event else None,
                should_cancel=should_cancel,
            )
            _maybe_end_reasoning()
        except PromptGenerationError as exc:
            logger.warning("LLM provider failed; falling back to heuristics: %s", exc)
            return None
        return self._parse_llm_plan(raw)

    def _log_plan_summary(
        self,
        *,
        conversation: Conversation,
        source: str,
        planned_actions: Sequence[PlannedAction],
        extractions: Sequence[ExtractionPlan],
        diagnostics: Mapping[str, object],
    ) -> None:
        action_dump = [
            {
                "action": plan.action.value,
                "payload_keys": sorted(plan.payload.keys()),
            }
            for plan in planned_actions
        ]
        extraction_dump = [
            {
                "type": extraction.extraction_type.value,
                "payload_keys": sorted(extraction.payload.keys()),
            }
            for extraction in extractions
        ]

        logger.info(
            "orchestrator plan conversation=%s source=%s actions=%s extractions=%s diagnostics=%s",
            conversation.id,
            source,
            action_dump,
            extraction_dump,
            diagnostics,
        )

    def _parse_llm_plan(self, raw_payload: Mapping[str, object]) -> LlmPlan | None:
        text = str(raw_payload.get("response_text") or "").strip()
        if not text:
            return None
        planned_actions: list[PlannedAction] = []
        knowledge_requests: list[str] = []
        for action_payload in raw_payload.get("actions", []) or []:
            key = action_payload.get("action") if isinstance(action_payload, dict) else None
            if not key:
                continue
            if key == ActionType.READ_KNOWLEDGE.value:
                payload = action_payload.get("payload") if isinstance(action_payload, dict) else None
                requested = self._extract_knowledge_ids(payload or {})
                if requested:
                    knowledge_requests.extend(requested)
                continue
            try:
                action_type = ActionType(key)
            except ValueError:
                continue
            if not self._is_enabled(action_type):
                continue
            planned_actions.append(PlannedAction(action=action_type, payload=action_payload.get("payload") or {}))

        extractions: list[ExtractionPlan] = []
        for extraction_payload in raw_payload.get("extractions", []) or []:
            if not isinstance(extraction_payload, dict):
                continue
            kind = extraction_payload.get("type")
            try:
                extraction_type = ConversationExtractionType(kind)
            except ValueError:
                continue
            extractions.append(
                ExtractionPlan(
                    extraction_type=extraction_type,
                    payload=extraction_payload.get("payload") or {},
                )
            )

        block_source = None
        for key in ("response_blocks", "responseBlocks", "response_blocks_json"):
            if key in raw_payload and raw_payload.get(key) is not None:
                block_source = raw_payload.get(key)
                break
        response_blocks = normalize_response_blocks(block_source)
        llm_usage = raw_payload.get("llm_usage")
        if not isinstance(llm_usage, Mapping):
            llm_usage = None

        return LlmPlan(
            response_text=text,
            planned_actions=planned_actions,
            extractions=extractions,
            knowledge_requests=tuple(knowledge_requests),
            response_blocks=response_blocks,
            llm_usage=llm_usage,
        )

    @staticmethod
    def _extract_knowledge_ids(payload: Mapping[str, object]) -> Sequence[str]:
        identifiers: list[str] = []
        if not payload:
            return identifiers
        raw_ids = payload.get("knowledge_ids") or payload.get("knowledge_id")
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if isinstance(raw_ids, (list, tuple, set)):
            for item in raw_ids:
                if item is None:
                    continue
                identifier = str(item).strip()
                if identifier:
                    identifiers.append(identifier)
        return identifiers

    def _coerce_knowledge_ids(self, requested: Sequence[str] | None, knowledge_payload: Sequence[dict]) -> list[str]:
        """
        Converts LLM-provided `knowledge_ids` that reference ledger indices (1-based)
        into real snippet UUIDs. Also accepts real UUIDs and filters duplicates.
        Skips entries marked unavailable or suppressed from the prompt.
        """
        ids: list[str] = []
        seen: set[str] = set()
        requested = requested or []
        # Build a clean view of the ledger as shown to the model
        visible_payload = [s for s in knowledge_payload if not s.get("suppress_in_prompt")]
        for item in requested:
            s = str(item).strip()
            # numeric index? map 1-based index -> visible payload entry id
            if s.isdigit():
                idx = int(s)
                if 1 <= idx <= len(visible_payload):
                    target = visible_payload[idx - 1]
                    kid = str(target.get("id") or "").strip()
                    status = str(target.get("status") or "").strip().lower()
                    if kid and status != "unavailable" and kid not in seen:
                        ids.append(kid)
                        seen.add(kid)
                continue
            # try UUID
            try:
                kid = str(uuid.UUID(s))
                if kid not in seen:
                    ids.append(kid)
                    seen.add(kid)
            except Exception:
                continue
        return ids

    @staticmethod
    def _is_placeholder_text(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False
        return (
            t == "reviewing knowledge"
            or t == "reviewing the document"
            or t == "reviewing docs"
            or t.startswith("reviewing ")
            or t == "loading details"
        )



    def _plan_actions(self, *, conversation: Conversation, user_message: str) -> tuple[list[PlannedAction], list[ExtractionPlan]]:
        del conversation
        plans: list[PlannedAction] = []
        extractions: list[ExtractionPlan] = []
        lower = user_message.lower()

        if "complaint" in lower:
            extractions.append(
                ExtractionPlan(
                    extraction_type=ConversationExtractionType.COMPLAINT,
                    payload={"note": user_message},
                )
            )

        return plans, extractions

    def _compose_placeholder_response(
        self,
        *,
        user_message: str,
        citations: Sequence[KnowledgeSnippet],
        planned_actions: Sequence[PlannedAction],
    ) -> str:
        lines = [
            f"Thanks for the update. I’m reviewing your note: \"{user_message[:160]}\"."
        ]
        if citations:
            cited = ", ".join(snippet.title for snippet in citations)
            lines.append(f"I’m referencing our latest resources ({cited}) to keep details accurate.")
        lines.append("I’ll follow up with any updates as soon as they’re ready—feel free to share more context meanwhile.")
        return " ".join(lines)


class ActionDispatcher:
    """Executes orchestrator planned actions if the agent has them enabled."""

    def __init__(self, *, agent: AgentProfile):
        self.agent = agent

    def execute(self, *, conversation: Conversation, planned_actions: Iterable[PlannedAction]) -> Sequence[ActionExecutionResult]:
        actions = list(planned_actions)
        results: list[ActionExecutionResult] = []
        with TRACER.start_as_current_span("portal.actions.dispatch") as span:
            if span.is_recording():
                span.set_attribute("actions.count", len(actions))
                span.set_attribute("conversation.id", str(getattr(conversation, "id", "")))
            for plan in actions:
                handler = getattr(self, f"_handle_{plan.action.value}", None)
                span_name = f"portal.actions.{plan.action.value}"
                with TRACER.start_as_current_span(span_name) as action_span:
                    if action_span.is_recording():
                        action_span.set_attribute("action.name", plan.action.value)
                        action_span.set_attribute("action.payload_keys", sorted(plan.payload.keys()))
                        action_span.set_attribute("conversation.id", str(getattr(conversation, "id", "")))
                    if handler is None:
                        logger.warning("⚠️ Action skipped: %s (no handler)", plan.action.value)
                        if action_span.is_recording():
                            action_span.set_attribute("action.status", "skipped")
                            action_span.set_attribute("action.error", "handler_missing")
                        results.append(
                            ActionExecutionResult(
                                action=plan.action,
                                status="skipped",
                                metadata={},
                                error="Handler not implemented",
                            )
                        )
                        continue
                    try:
                        metadata = handler(conversation=conversation, payload=plan.payload)
                        logger.info("✅ Action applied: %s", plan.action.value)
                        if action_span.is_recording():
                            action_span.set_attribute("action.status", "applied")
                        results.append(
                            ActionExecutionResult(
                                action=plan.action,
                                status="applied",
                                metadata=metadata,
                            )
                        )
                    except ActionExecutionError as exc:
                        logger.warning("❌ Action failed: %s (%s)", plan.action.value, str(exc)[:50])
                        if action_span.is_recording():
                            action_span.record_exception(exc)
                            action_span.set_attribute("action.status", "failed")
                        results.append(
                            ActionExecutionResult(
                                action=plan.action,
                                status="failed",
                                metadata={},
                                error=str(exc),
                            )
                        )
        return tuple(results)

    def _handle_read_knowledge(self, *, conversation: Conversation, payload: dict) -> dict:  # pragma: no cover - safeguard
        requested = payload.get("knowledge_ids") or payload.get("knowledge_id") or []
        if isinstance(requested, str):
            requested = [requested]
        return {"requested_ids": requested, "status": "handled_upstream"}
