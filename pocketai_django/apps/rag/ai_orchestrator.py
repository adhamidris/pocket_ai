from __future__ import annotations

from collections import OrderedDict

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
from datetime import datetime
from enum import Enum
from types import SimpleNamespace
import re
import unicodedata
from contextvars import ContextVar
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.accounts.models import (
    AgentProfile,
    KnowledgeAlias,
    KnowledgeStatus,
    KnowledgeVisibility,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadIssue,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
)
from apps.cases.models import Case, CaseHistoryEntry, CasePriority, CaseStatus
from apps.conversations.models import (
    Conversation,
    ConversationExtraction,
    ConversationExtractionType,
    ConversationSender,
    ConversationStatus,
    ConversationMessage
)
from apps.customers.models import Customer, CustomerRecordOrigin
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
from apps.conversations.response_blocks import normalize_response_blocks
from core.metrics import latency_monitor
from opentelemetry import trace as otel_trace

try:  # optional dependency
    from sentence_transformers import CrossEncoder
except ImportError:  # pragma: no cover - dependency not installed by default
    CrossEncoder = None


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

# NOTE (legacy orchestrator):
# This module implements the original ledger-based orchestrator used before the
# MCP-style tool-calling path was introduced. The MCP orchestrator lives in
# apps.mcp.orchestrator.McpOrchestratorService and is now the primary path for new
# traffic. This module is retained for backward compatibility and as a fallback
# whenever MCP is disabled at the environment or business level.


class ActionType(str, Enum):
    CREATE_CASE = "create_case"
    UPDATE_CASE_STATUS = "update_case_status"
    UPDATE_CASE_DETAILS = "update_case_details"
    ADD_CASE_HISTORY = "add_case_history"
    FLAG_ESCALATION = "flag_escalation"
    CREATE_CUSTOMER = "create_customer"
    UPDATE_CUSTOMER = "update_customer"
    CREATE_LEAD = "create_lead"
    CREATE_APPOINTMENT = "create_appointment"
    READ_KNOWLEDGE = "read_knowledge"


def _is_business_text(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().lower()
    if not normalized:
        return False
    greetings = {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "hola",
        "hey there",
    }
    if normalized in greetings:
        return False
    keywords = [
        "order",
        "invoice",
        "shipment",
        "payment",
        "account",
        "refund",
        "support",
        "issue",
        "problem",
        "error",
        "login",
        "subscription",
        "appointment",
        "contract",
        "service",
        "escalate",
        "case",
        "complaint",
        "ticket",
        "transfer",
        "finance",
    ]
    if any(keyword in normalized for keyword in keywords):
        return True
    if any(ch.isdigit() for ch in normalized):
        return True
    if any(sym in normalized for sym in ("$", "€", "£")):
        return True
    return len(normalized.split()) >= 6


@dataclasses.dataclass(frozen=True)
class ActionDescriptor:
    key: ActionType
    label: str
    description: str
    default_enabled: bool = True


ACTION_REGISTRY: dict[ActionType, ActionDescriptor] = {
    ActionType.CREATE_CASE: ActionDescriptor(
        key=ActionType.CREATE_CASE,
        label="Create Case",
        description="Create a structured case with AI diagnosis and suggested actions.",
    ),
    ActionType.UPDATE_CASE_STATUS: ActionDescriptor(
        key=ActionType.UPDATE_CASE_STATUS,
        label="Update Case Status",
        description="Change the linked case lifecycle (open/resolved).",
        default_enabled=True,
    ),
    ActionType.UPDATE_CASE_DETAILS: ActionDescriptor(
        key=ActionType.UPDATE_CASE_DETAILS,
        label="Update Case Details",
        description="Refresh case title, description, or priority using new info.",
    ),
    ActionType.ADD_CASE_HISTORY: ActionDescriptor(
        key=ActionType.ADD_CASE_HISTORY,
        label="Add Case History Entry",
        description="Log significant updates to the case timeline without mutating the description.",
    ),
    ActionType.FLAG_ESCALATION: ActionDescriptor(
        key=ActionType.FLAG_ESCALATION,
        label="Flag Escalation",
        description="Escalate the conversation for human follow-up.",
        default_enabled=True,
    ),
    ActionType.CREATE_CUSTOMER: ActionDescriptor(
        key=ActionType.CREATE_CUSTOMER,
        label="Create Customer",
        description="Capture a new customer record extracted from the chat.",
    ),
    ActionType.UPDATE_CUSTOMER: ActionDescriptor(
        key=ActionType.UPDATE_CUSTOMER,
        label="Update Customer",
        description="Refresh an existing customer profile with new details.",
    ),
    ActionType.CREATE_LEAD: ActionDescriptor(
        key=ActionType.CREATE_LEAD,
        label="Create Lead",
        description="Store structured lead intents discovered in chat.",
    ),
    ActionType.CREATE_APPOINTMENT: ActionDescriptor(
        key=ActionType.CREATE_APPOINTMENT,
        label="Create Appointment",
        description="Persist appointment requests for downstream scheduling.",
    ),
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
        sanitized = re.sub(r"[^\w\\-\\s]", "", lowered, flags=re.UNICODE)
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
        if len(tokens) <= 3 and (has_digits or has_dashes or has_underscores):
            return True
        for candidate in alias_candidates:
            if candidate and cls._IDENTIFIER_PATTERN.fullmatch(candidate):
                return True
        return any(cls._IDENTIFIER_PATTERN.fullmatch(token) for token in tokens if token)


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
TOPIC_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "fees": ("fee", "fees", "charge", "charges", "pricing", "annual fee", "monthly fee", "maintenance fee"),
    "limits": ("limit", "limits", "cap", "caps", "maximum", "max", "ceiling", "spend limit", "withdrawal limit"),
    "benefits": ("benefit", "benefits", "perk", "perks", "reward", "rewards", "cashback", "cash back", "points", "miles"),
    "eligibility": ("eligibility", "eligible", "qualify", "qualification", "qualifications", "requirement", "requirements", "criteria"),
    "documents": ("document", "documents", "paperwork", "proof", "statement", "statements", "id", "identification"),
    "timeline": ("timeline", "processing time", "turnaround", "how long", "timeframe", "sla"),
    "support": ("support", "contact", "phone", "email", "help desk", "representative"),
    "apr": ("apr", "interest", "interest rate", "rate", "percentage"),
    "restrictions": ("restriction", "restrictions", "blackout", "exclusion", "not covered"),
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
        self.entity_neighbor_min = max(1, int(getattr(settings, "RAG_ENTITY_NEIGHBOR_MIN", 2)))
        self.vector_distance_ceiling = float(getattr(settings, "RAG_VECTOR_DISTANCE_CEILING", 0.5))
        self.short_query_ann_multiplier = float(getattr(settings, "RAG_SHORT_QUERY_ANN_MULTIPLIER", 3.0))
        self.read_ready_threshold = max(200, int(getattr(settings, "RAG_READY_CHAR_THRESHOLD", 900)))
        self.table_ready_threshold = max(200, int(getattr(settings, "RAG_READY_TABLE_THRESHOLD", 600)))
        self.cross_encoder_weight = float(getattr(settings, "RAG_CROSS_ENCODER_WEIGHT", 0.6))
        self.cross_encoder = self._build_cross_encoder()
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
        }
        self.recency_decay_days = float(getattr(settings, "RAG_RECENCY_DECAY_DAYS", 90))
        self.recency_min_floor = float(getattr(settings, "RAG_RECENCY_MIN_FLOOR", 0.05))
        self.recency_bonus_fresh = float(getattr(settings, "RAG_RECENCY_BONUS_FRESH", 0.15))
        self.snippet_rerank_enabled = bool(getattr(settings, "RAG_SNIPPET_RERANK_ENABLED", True))
        self.snippet_rerank_pool = max(5, int(getattr(settings, "RAG_SNIPPET_RERANK_POOL", 20)))
        self.business_override_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
        self.window_cache_limit = max(32, int(getattr(settings, "RAG_NEIGHBOR_WINDOW_CACHE_SIZE", 128)))
        self._window_cache: OrderedDict[tuple[uuid.UUID, int, int], list[KnowledgeUploadChunk]] = OrderedDict()
        self.structured_count_cache_limit = 256
        self._structured_count_cache: OrderedDict[uuid.UUID, tuple[int, int]] = OrderedDict()
        self.table_result_cap = max(3, int(getattr(settings, "RAG_TABLE_RESULT_LIMIT", 12)))
        self.table_similarity_threshold = float(getattr(settings, "RAG_TABLE_SIMILARITY_THRESHOLD", 0.3))
        self.table_column_cache_limit = max(8, int(getattr(settings, "RAG_TABLE_COLUMN_CACHE_SIZE", 32)))
        self.table_column_sample_limit = max(25, int(getattr(settings, "RAG_TABLE_COLUMN_SAMPLE", 200)))
        self._table_column_cache: OrderedDict[uuid.UUID, set[str]] = OrderedDict()
        self.table_rerank_floor = float(getattr(settings, "RAG_TABLE_RERANK_FLOOR", 0.35))
        self.table_vector_floor = float(getattr(settings, "RAG_TABLE_VECTOR_FLOOR", 0.45))
        self.table_chunk_sample_limit = max(3, int(getattr(settings, "RAG_TABLE_CHUNK_SAMPLE", 6)))
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
        self.table_query_keywords = {
            "table",
            "column",
            "row",
            "sheet",
            "spreadsheet",
            "excel",
            "csv",
            "tab",
            "grid",
        }
        logger.info("emb.provider %s model=%s", type(self.embedding_service).__name__ if self.embedding_service else None, getattr(self.embedding_service, "model", None))
        self._page_summary_cache: OrderedDict[uuid.UUID, dict[int, Mapping[str, object]]] = OrderedDict()
        self._table_presence_cache: OrderedDict[uuid.UUID, bool] = OrderedDict()

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

    def _window_cache_get(self, upload_id: uuid.UUID, start: int, end: int) -> list[KnowledgeUploadChunk] | None:
        key = (upload_id, start, end)
        cached = self._window_cache.get(key)
        if cached is not None:
            self._window_cache.move_to_end(key)
        return cached

    def _window_cache_set(self, upload_id: uuid.UUID, start: int, end: int, chunks: list[KnowledgeUploadChunk]) -> None:
        key = (upload_id, start, end)
        self._window_cache[key] = chunks
        self._window_cache.move_to_end(key)
        if len(self._window_cache) > self.window_cache_limit:
            self._window_cache.popitem(last=False)

    def _page_summary_entries(self, upload: KnowledgeUpload) -> dict[int, Mapping[str, object]]:
        cached = self._page_summary_cache.get(upload.id)
        if cached is not None:
            self._page_summary_cache.move_to_end(upload.id)
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
        self._page_summary_cache[upload.id] = entries
        self._page_summary_cache.move_to_end(upload.id)
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
        cached = self._structured_count_cache.get(upload.id)
        if cached:
            self._structured_count_cache.move_to_end(upload.id)
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
        self._structured_count_cache[upload.id] = counts
        self._structured_count_cache.move_to_end(upload.id)
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
    ) -> KnowledgeSearchResult:
        """
        Wrapper that runs knowledge retrieval and emits tracing spans for observability.
        """

        traits = traits or self.analyze_query(query, business_profile=business_profile)
        with TRACER.start_as_current_span("knowledge.search") as span:
            if span.is_recording():
                span.set_attribute("knowledge.query", traits.original or query)
                span.set_attribute("knowledge.query_tokens", traits.token_count)
                if business_profile and getattr(business_profile, "id", None):
                    span.set_attribute("knowledge.business_id", str(business_profile.id))
            result = self._search_inner(
                business_profile=business_profile,
                query=query,
                limit=limit,
                traits=traits,
                alias_result=alias_result,
                session_cache=session_cache,
                identifier_filter=identifier_filter,
            )
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
    ) -> KnowledgeSearchResult:
        traits = traits or self.analyze_query(query, business_profile=business_profile)
        overall_start = time.perf_counter()
        feature_state = FeatureFlagService.snapshot(business_profile)
        request_id = uuid.uuid4()
        limit = self._snippet_limit_for_business(business_profile, limit)
        alias_chunk_cap = self._effective_chunk_cap(business_profile, "alias")
        ann_chunk_cap = self._effective_chunk_cap(business_profile, "ann")
        vector_ceiling = self._vector_ceiling_for_business(business_profile)
        table_context = self._table_query_context(business_profile, traits)
        tables_available = self._business_has_tables(business_profile, cached_columns=table_context.get("available_columns"))
        alias_blocked = False
        if alias_result is None:
            with TRACER.start_as_current_span("knowledge.alias_lookup") as alias_span:
                alias_result = self.search_by_alias(
                    business_profile=business_profile,
                    traits=traits,
                    limit=self.alias_result_cap,
                    feature_state=feature_state,
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
            "snippet_limit": limit,
            "alias_chunks_per_upload": alias_chunk_cap,
            "ann_chunks_per_upload": ann_chunk_cap,
            "vector_distance_ceiling": vector_ceiling,
            "tables_available": tables_available,
            "tabular_columns_hint": sorted(table_context.get("semantic_columns") or ())[:5],
            "alias_short_circuit_blocked": alias_blocked,
            "table_reason": None,
            "chunk_candidate_count": 0,
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
        cache_key = self._result_cache_key(
            business_profile=business_profile,
            traits=traits,
            limit=limit,
            alias_result=alias_result,
            table_context=table_context,
            feature_state=feature_state,
            identifier_filter=identifier_filter,
        )
        cached_result = None
        if session_cache is not None:
            cached_result = self._session_cache_get(session_cache, cache_key)
            if cached_result:
                cached_diag = dict(cached_result.diagnostics or {})
                cached_diag["cache_hit"] = True
                cached_diag["cache_scope"] = "session"
                cached_diag["request_id"] = str(request_id)
                cached_diag["total_duration_ms"] = self._duration_ms(overall_start)
                snippets = cached_result.snippets[:limit]
                cached_diag["snippet_count"] = len(snippets)
                result_obj = KnowledgeSearchResult(
                    snippets=snippets,
                    status=cached_result.status,
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
            cached_diag = dict(cached_result.diagnostics or {})
            cached_diag["cache_hit"] = True
            cached_diag["cache_scope"] = "business"
            cached_diag["request_id"] = str(request_id)
            cached_diag["total_duration_ms"] = self._duration_ms(overall_start)
            snippets = cached_result.snippets[:limit]
            cached_diag["snippet_count"] = len(snippets)
            result_obj = KnowledgeSearchResult(
                snippets=snippets,
                status=cached_result.status,
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
            snippets, snippet_ms = self._snippet_rerank(
                snippets,
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
            )
            diagnostics["path"] = "alias_exact"
            diagnostics["alias_stage"] = diagnostics.get("alias_stage") or alias_result.diagnostics.get("stage")
            diagnostics["snippet_rerank_ms"] = snippet_ms
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
        diagnostics["chunk_candidate_count"] = len(chunk_hits)
        table_snippets: tuple[KnowledgeSnippet, ...] = tuple()
        table_reason: str | None = None
        should_run_table = False
        table_duration_ms: int | None = None
        has_header_match = bool(table_context.get("matched_columns"))
        if tables_available:
            if not chunk_hits:
                should_run_table = True
                table_reason = "no_chunk_candidates"
            elif self._chunk_hits_are_weak(chunk_hits, traits):
                should_run_table = True
                table_reason = "weak_chunk_candidates"
            elif self._query_has_entity_tokens(business_profile, traits):
                should_run_table = True
                table_reason = "entity_query_parallel"
            elif has_header_match:
                should_run_table = True
                table_reason = "header_match"

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
            table_snippets = self._table_search_snippets(
                business_profile=business_profile,
                query_text=traits.normalized or traits.original,
                limit=limit,
                matched_columns=table_context["matched_columns"],
            )
            table_duration_ms = int((time.perf_counter() - table_start) * 1000)
            diagnostics["table_duration_ms"] = table_duration_ms

        if table_snippets:
            diagnostics["path"] = "table_direct" if not chunk_hits else "table_blended"
            diagnostics["reason"] = table_reason or diagnostics.get("reason") or "table_search"
            if table_reason:
                diagnostics["table_reason"] = table_reason
            diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
            diagnostics["snippet_count"] = len(table_snippets)
            blended: list[KnowledgeSnippet] = list(table_snippets[:limit])
            remaining = max(0, limit - len(blended))
            if chunk_hits and remaining:
                blended.extend(
                    self._search_chunks(
                        chunk_hits,
                        limit=remaining,
                        business_profile=business_profile,
                        pathway="hybrid",
                        query=traits.normalized or traits.original or query,  # NEW: For query-aware row sampling
                    )
                )
            blended, snippet_ms = self._snippet_rerank(
                tuple(blended),
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
            )
            diagnostics["snippet_rerank_ms"] = snippet_ms
            status = "ok" if blended else "not_found"
            diagnostics["snippet_count"] = len(blended)
            result_obj = KnowledgeSearchResult(snippets=tuple(blended), status=status, diagnostics=diagnostics)
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

            if not chunk_hits:
                diagnostics.setdefault("reason", "no_candidates")
                diagnostics["path"] = diagnostics.get("path") or "not_found"
                diagnostics.setdefault("table_reason", table_reason)
                fallback = tuple(self._fallback_snippets(business_profile=business_profile, limit=limit))
                fallback, snippet_ms = self._snippet_rerank(
                    fallback,
                    query_text=traits.normalized or traits.original or query,
                    tokens=traits.tokens,
                )
                diagnostics["snippet_rerank_ms"] = snippet_ms
                status = "ok" if fallback else "not_found"
                diagnostics["reason"] = diagnostics.get("reason") or ("fallback_used" if fallback else "no_candidates")
                _rag_log(
                    "search.empty",
                    {
                        "query": traits.normalized,
                        "fallback": len(fallback),
                        "reason": diagnostics.get("reason"),
                    },
                    indent=1,
                    context={
                        "business": business_profile.id,
                        "request": diagnostics.get("request_id"),
                    },
                )
            diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
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

        snippets = tuple(
            self._search_chunks(
                chunk_hits,
                limit=limit,
                business_profile=business_profile,
                pathway="hybrid",
                query=traits.normalized or traits.original or query,  # NEW: For query-aware row sampling
            )
        )
        if snippets:
            diagnostics["path"] = diagnostics.get("path") or "hybrid"
            diagnostics.setdefault("table_reason", table_reason)
            diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
            diagnostics["snippet_count"] = len(snippets)
            result_obj = KnowledgeSearchResult(snippets=snippets, status="ok", diagnostics=diagnostics)
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

        fallback = tuple(self._fallback_snippets(business_profile=business_profile, limit=limit))
        diagnostics["path"] = "fallback"
        diagnostics["reason"] = "fallback_used"
        status = "ok" if fallback else "not_found"
        diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
        diagnostics.setdefault("table_reason", table_reason)
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

    def _build_cross_encoder(self):
        enabled = bool(getattr(settings, "RAG_ENABLE_CROSS_ENCODER", False))
        if not enabled or CrossEncoder is None:
            if enabled and CrossEncoder is None:
                logger.warning("Cross-encoder reranker requested but sentence_transformers is not installed.")
            return None
        model_name = getattr(settings, "RAG_CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        device = getattr(settings, "RAG_CROSS_ENCODER_DEVICE", None)
        try:
            return CrossEncoder(model_name, device=device)
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning("Failed to initialize cross-encoder model=%s error=%s", model_name, exc)
            return None

    def search_by_alias(
        self,
        *,
        business_profile,
        aliases: Sequence[str] | None = None,
        traits: QueryTraits | None = None,
        limit: int | None = None,
        feature_state: FeatureState | None = None,
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
                short_circuit=True,
            )

        fuzzy_hits = self._alias_fuzzy_hits(
            business_profile=business_profile,
            traits=traits,
            limit=self.alias_fts_limit,
            threshold=alias_threshold,
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
    ) -> HybridSearchResult:
        with TRACER.start_as_current_span("knowledge.hybrid_search") as span:
            base_qs = self._base_chunk_queryset(business_profile)
            query_text = (query or "").strip() or traits.normalized or traits.original
            feature_state = feature_state or FeatureFlagService.snapshot(business_profile)
            query_vector: list[float] | None
            vector_diag: dict[str, object]
            vector_ms = 0
            vector_hits: Sequence[ChunkResult]
            if feature_state.hybrid_search:
                query_vector, vector_diag = self._build_query_vector(
                    business_profile=business_profile,
                    query_text=query_text.lower(),
                )
                vector_hits, vector_ms = self._vector_candidates(
                    business_id=business_profile.id,
                    base_qs=base_qs,
                    query_vector=query_vector,
                    limit=limit,
                    traits=traits,
                )
            else:
                query_vector = None
                vector_diag = {"vector_disabled": True}
                vector_hits = tuple()
            lexical_hits, lexical_ms, lexical_diag = self._lexical_candidates(
                business_profile=business_profile,
                base_qs=base_qs,
                traits=traits,
                limit=limit,
            )
            latency_monitor.observe("rag.vector", vector_ms, tags={"business": str(business_profile.id)})
            latency_monitor.observe("rag.lexical", lexical_ms, tags={"business": str(business_profile.id)})
            merged = self._merge_candidates(
                alias_candidates or tuple(),
                vector_hits,
                lexical_hits,
            )
            reranked, rerank_ms = self._rerank_candidates(
                merged,
                query_vector if self.embedding_service else None,
                traits=traits,
            )
            latency_monitor.observe(
                "rag.rerank",
                rerank_ms,
                tags={
                    "business": str(business_profile.id),
                    "cross_encoder": bool(self.cross_encoder),
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
                span.set_attribute("knowledge.hybrid.rerank_ms", rerank_ms)
                span.set_attribute("knowledge.hybrid.candidates", len(reranked))
            return HybridSearchResult(
                hits=tuple(reranked),
                query_vector=query_vector,
                diagnostics=diagnostics,
            )

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
        max_per_upload = self._effective_chunk_cap(business_profile, pathway)
        for hit in hits:
            chunk = hit.chunk
            upload = chunk.upload
            current = per_upload_counts.get(upload.id, 0)
            if current >= max_per_upload:
                continue
            # NEW: Pass query to enable query-aware row sampling
            table_sample: tuple[Mapping[str, object], ...] | None = self._table_row_sample(chunk, max_columns=4, query=query)
            snippets.append(self._chunk_to_snippet(chunk, result=hit, table_row_sample=table_sample))
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

        reranked, rerank_ms = self._rerank_candidates(
            prioritized,
            hybrid.query_vector if self.embedding_service else None,
            traits=traits,
        )
        hybrid.diagnostics["rerank_duration_ms"] = rerank_ms
        filtered = self._apply_vector_threshold(reranked, hybrid.query_vector, ceiling=ceiling)
        if diagnostics is not None:
            diagnostics["vector_candidates_post_threshold"] = len(filtered)
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
    ) -> tuple[list[ChunkResult], dict[str, int]]:
        if not aliases:
            return [], {"cache_hit": 0, "cache_miss": 0}
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
            alias_qs = (
                KnowledgeAlias.objects.filter(
                    business_profile=business_profile,
                    alias_normalized__in=missing_aliases,
                )
                .select_related("entity__chunk__upload")
                .order_by("alias_normalized")
            )
            payloads: dict[str, list[dict[str, str]]] = {}
            for record in alias_qs:
                entity = record.entity
                chunk = entity.chunk if entity else None
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
    ) -> list[ChunkResult]:
        identifier_tokens = self._identifier_like_tokens(traits)
        if not identifier_tokens:
            return []
        query_text = " ".join(identifier_tokens[:4]) or (traits.normalized or traits.original or "")
        alias_qs = (
            KnowledgeAlias.objects.filter(business_profile=business_profile)
            .annotate(sim=TrigramSimilarity("alias_search_vector", query_text))
            .filter(sim__gte=threshold)
            .order_by("-sim")[: max(limit, 10)]
            .select_related("entity__chunk__upload")
        )
        seen: set[uuid.UUID] = set()
        hits: list[ChunkResult] = []
        for record in alias_qs:
            entity = record.entity
            chunk = entity.chunk if entity else None
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
    ) -> str:
        version = self._get_result_cache_version(business_profile.id)
        qvec_version = self._get_query_cache_version(business_profile.id)
        model_name = getattr(self.embedding_service, "model", "local")
        alias_stage = ""
        if alias_result and alias_result.diagnostics:
            alias_stage = str(alias_result.diagnostics.get("stage") or "")
        normalized_query = (traits.normalized or traits.original or "").strip().lower()
        fingerprint = "|".join(
            [
                str(business_profile.id),
                str(version),
                str(qvec_version),
                CUSTOMER_VISIBILITY_POLICY_KEY,
                model_name,
                normalized_query,
                str(limit),
                "table" if table_context.get("has_intent") else "chunk",
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
    ) -> dict[uuid.UUID, KnowledgeUploadChunk]:
        if not chunk_ids:
            return {}
        qs = apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
                id__in=chunk_ids,
            )
            .select_related("upload")
        )
        return {chunk.id: chunk for chunk in qs}

    def _base_chunk_queryset(self, business_profile):
        return apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .select_related("upload")
        )

    def _merge_candidates(self, *groups: Sequence[ChunkResult]) -> list[ChunkResult]:
        seen: set[uuid.UUID] = set()
        merged: list[ChunkResult] = []
        for group in groups:
            for hit in group:
                if hit.chunk_id in seen:
                    continue
                seen.add(hit.chunk_id)
                merged.append(hit)
        return merged

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
                .order_by("distance")[:K]
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
        strong = [(idx, token) for idx, token in enumerate(filtered) if len(token) >= min_length]
        ranked = sorted(strong, key=lambda pair: (-len(pair[1]), pair[0]))
        max_tokens = self._fts_condense_max_tokens(business_profile)
        chosen = [token for _, token in ranked[:max_tokens]]
        if not chosen:
            fallback = filtered or tokens
            chosen = fallback[:max_tokens]
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
            search_type = "websearch"
            vector = SearchVector("content", config=config)
            query = SearchQuery(condensed_query, search_type=search_type, config=config)
            try:
                fts_qs = (
                    base_qs.annotate(
                        fts_vector=vector,
                        rank=SearchRank(vector, query, cover_density=True),
                    )
                    # Apply @@ filter so Postgres can use the GIN index on
                    # `to_tsvector('simple', coalesce(content,''))`.
                    .filter(fts_vector=query)
                    .order_by("-rank")[:N]
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
            token_filter = self._build_fts_token_filter(condensed_tokens or traits.tokens, min_length=token_min_length)
            fts_base = base_qs.filter(token_filter) if token_filter else base_qs
            N = max(limit * 8, 40)
            start = time.perf_counter()
            fts_qs = (
                fts_base.annotate(sim=TrigramSimilarity("content", condensed_query))
                .filter(sim__gte=threshold)
                .order_by("-sim")[:N]
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
    ) -> list[ChunkResult]:
        threshold = ceiling if ceiling is not None else self.vector_distance_ceiling
        if not threshold or threshold <= 0 or not query_vector:
            return list(candidates)
        filtered = [
            hit
            for hit in candidates
            if hit.vector_distance is None or hit.vector_distance <= threshold
        ]
        return filtered or list(candidates)

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
            def score(hit: ChunkResult) -> float:
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
                return lam * rel - (1 - lam) * diversity
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

    def _rerank_candidates(
        self,
        candidates: Sequence[ChunkResult],
        query_vector: list[float] | None,
        *,
        traits: QueryTraits,
    ) -> tuple[list[ChunkResult], int]:
        if not candidates:
            return [], 0
        start = time.perf_counter()
        top_pool = min(len(candidates), self.rerank_pool)
        scored: list[tuple[float, int, ChunkResult]] = []
        tail: list[ChunkResult] = []
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
            
            if chunk_metadata.get("is_table_chunk"):
                # Get quality score from metadata (0.0 = garbage, 1.0 = high quality)
                quality_score_raw = chunk_metadata.get("table_quality_score")
                is_decorative = chunk_metadata.get("table_is_decorative", False)
                
                # FIXED: Defensive type checking to prevent crashes
                quality_score = None
                try:
                    if quality_score_raw is not None:
                        quality_score = float(quality_score_raw)
                except (TypeError, ValueError):
                    quality_score = None
                
                if quality_score is not None and quality_score < 0.5:
                    # Apply penalty for low quality tables
                    # Penalty ranges from 0% (quality=0.5) to 50% (quality=0.0)
                    quality_penalty = (0.5 - quality_score) * 1.0  # Max penalty of 0.5
                elif is_decorative:
                    # Fallback: if is_decorative flag is set, apply moderate penalty
                    quality_penalty = 0.30
            
            combined = (
                self.rerank_weights["vector"] * vector_score
                + self.rerank_weights["lexical"] * lexical_score
                + self.rerank_weights["alias"] * alias_bonus
                + self.rerank_weights["entity"] * entity_bonus
                + self.rerank_weights["recency"] * recency_score
                - quality_penalty  # NEW: Subtract quality penalty
            )
            cand.diagnostics["score_breakdown"] = {
                "vector": round(vector_score, 4),
                "lexical": round(lexical_score, 4),
                "alias": round(alias_bonus, 4),
                "entity": round(entity_bonus, 4),
                "recency": round(recency_score, 4),
                "quality_penalty": round(quality_penalty, 4),  # NEW: Include in diagnostics
            }
            cand.rerank_score = combined
            scored.append((combined, -idx, cand))
        if self.cross_encoder and traits.normalized:
            head = [item[2] for item in scored[: self.rerank_pool]]
            if head:
                pairs = [[traits.normalized, (hit.chunk.content or "")] for hit in head]
                try:
                    ce_scores = self.cross_encoder.predict(pairs)
                    ce_values = [float(score) for score in ce_scores]
                except Exception as exc:  # pragma: no cover - optional dependency
                    logger.warning("Cross-encoder rerank failed: %s", exc)
                else:
                    ce_lookup = {
                        hit.chunk_id: self.cross_encoder_weight * ce_values[idx]
                        for idx, hit in enumerate(head)
                        if idx < len(ce_values)
                    }
                    if ce_lookup:
                        scored = [
                            (base + ce_lookup.get(hit.chunk_id, 0.0), order, hit)
                            for base, order, hit in scored
                        ]
                        scored.sort(key=lambda item: item[0], reverse=True)
        scored.sort(key=lambda item: item[0], reverse=True)
        reranked = [item[2] for item in scored]
        if tail:
            reranked.extend(tail)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return reranked, duration_ms

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

    def _snippet_rerank(
        self,
        snippets: Sequence[KnowledgeSnippet],
        *,
        query_text: str,
        tokens: tuple[str, ...],
    ) -> tuple[tuple[KnowledgeSnippet, ...], int]:
        if not self.snippet_rerank_enabled or len(snippets) <= 1:
            return tuple(snippets), 0
        with TRACER.start_as_current_span("knowledge.snippet_rerank") as span:
            start = time.perf_counter()
            normalized_query = (query_text or "").strip()
            head = list(snippets[: self.snippet_rerank_pool])
            scores: list[tuple[float, int, KnowledgeSnippet]] = []
            ce_scores: list[float] | None = None
            if self.cross_encoder and normalized_query:
                pairs = [
                    [normalized_query, "\n".join(filter(None, [snip.summary, snip.content]))]
                    for snip in head
                ]
                try:  # pragma: no cover - optional dependency
                    raw = self.cross_encoder.predict(pairs)
                    ce_scores = [float(val) for val in raw]
                except Exception as exc:  # pragma: no cover - optional dependency
                    logger.warning("Cross-encoder snippet rerank failed: %s", exc)
                    ce_scores = None
            for idx, snip in enumerate(head):
                text = "\n".join(filter(None, [snip.summary, snip.content]))
                lexical = self._lexical_score_text(text, tokens)
                ce_score = ce_scores[idx] if ce_scores and idx < len(ce_scores) else 0.0
                score = ce_score if ce_scores else 0.0
                score += 0.25 * lexical
                scores.append((score, -idx, snip))
            scores.sort(key=lambda item: item[0], reverse=True)
            reranked = [item[2] for item in scores]
            if len(snippets) > len(head):
                reranked.extend(snippets[len(head) :])
            duration_ms = int((time.perf_counter() - start) * 1000)
            if span.is_recording():
                span.set_attribute("knowledge.snippet_rerank_head", len(head))
                span.set_attribute("knowledge.snippet_rerank_ms", duration_ms)
                span.set_attribute("knowledge.snippet_cross_encoder", bool(self.cross_encoder))
            return tuple(reranked), duration_ms

    def _table_query_context(self, business_profile, traits: QueryTraits) -> Mapping[str, object]:
        query_text = (traits.normalized or traits.original or "").lower()
        tokens = set(token.lower() for token in traits.tokens)
        matched_keywords = tokens & self.table_query_keywords
        columns = self._table_columns_for_business(business_profile)
        hints = self._table_column_hints(business_profile)
        semantic_columns = {column for column in columns if any(hint in column for hint in hints)}
        matched_columns_query = {column for column in columns if column and column in query_text}
        matched_columns = matched_columns_query or semantic_columns
        has_intent = bool(matched_keywords or matched_columns_query)
        return {
            "has_intent": has_intent,
            "matched_columns": matched_columns,
            "available_columns": columns,
            "semantic_columns": semantic_columns,
            "matched_column_count": len(matched_columns),
        }

    def _table_columns_for_business(self, business_profile) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        cached = self._table_column_cache.get(business_id)
        if cached is not None:
            self._table_column_cache.move_to_end(business_id)
            return cached
        columns: set[str] = set()
        qs = (
            KnowledgeUploadTable.objects.filter(
                upload__business_profile=business_profile
            )
            .exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
            .order_by("-updated_at")
            .values_list("column_schema", flat=True)[: self.table_column_sample_limit]
        )
        for schema in qs:
            if not isinstance(schema, (list, tuple)):
                continue
            for column in schema:
                if not column:
                    continue
                lowered = str(column).strip().lower()
                if lowered:
                    columns.add(lowered)
        self._table_column_cache[business_id] = columns
        if len(self._table_column_cache) > self.table_column_cache_limit:
            self._table_column_cache.popitem(last=False)
        return columns

    def _table_row_result_cap_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve how many table rows we should surface for a business.
        """

        base = requested if requested is not None else self.table_result_cap
        if not business_profile:
            return base
        override = self._business_override(business_profile, "table_results_limit", base)
        return max(3, int(override))

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

    def _business_has_tables(self, business_profile, cached_columns: set[str] | None = None) -> bool:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return False
        if cached_columns is not None and cached_columns:
            return True
        cached = self._table_presence_cache.get(business_id)
        if cached is not None:
            self._table_presence_cache.move_to_end(business_id)
            return cached
        exists = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exists()
        self._table_presence_cache[business_id] = exists
        self._table_presence_cache.move_to_end(business_id)
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
    ) -> tuple[KnowledgeSnippet, ...]:
        normalized_query = (query_text or "").strip()
        if not normalized_query:
            return tuple()
        row_cap = self._table_row_result_cap_for_business(business_profile)
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
        cell_qs = KnowledgeUploadTableCell.objects.filter(
            table__upload__business_profile=business_profile
        )
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
        top_cells = list(cell_qs[: row_cap * 3])
        if not top_cells:
            return tuple()
        row_priority: list[uuid.UUID] = []
        cell_diag: dict[uuid.UUID, dict[str, object]] = {}
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
            }
            row_priority.append(row.id)
            if len(row_priority) >= row_cap:
                break
        if not row_priority:
            return tuple()
        rows = (
            KnowledgeUploadTableRow.objects.filter(id__in=row_priority)
            .select_related("table__upload__business_profile")
            .prefetch_related("cells")
        )
        row_map = {row.id: row for row in rows}
        ingestion_diag_cache: dict[uuid.UUID, dict[str, object]] = {}
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
            snippets.append(
                KnowledgeSnippet(
                    id=uuid.uuid4(),
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
                    chunk_id=None,
                    chunk_index=None,
                    entity_type=table.section_heading or "table_row",
                    entity_name=entity_hint,
                    entity_business=business_name,
                    is_table_chunk=True,
                    aliases=tuple(),
                    search_stage="table_direct",
                    confidence_score=diag.get("similarity"),
                    truncated=False,
                    source_diagnostics=diag,
                    partial_index=table_truncated,
                    structured_table_count=1,
                    issue_count=0,
                    structured_table_hint=diag.get("column_key"),
                )
            )
        return tuple(snippets)

    def _fallback_snippets(self, *, business_profile, limit: int) -> Sequence[KnowledgeSnippet]:
        # Prefer top table rows as factual fallback; if none, fall back to recent uploads.
        table_rows = list(
            KnowledgeUploadTableRow.objects.filter(
                table__upload__business_profile=business_profile,
                table__upload__status=KnowledgeStatus.ACTIVE,
            )
            .order_by("-created_at")[: limit * 3]
            .select_related("table__upload__business_profile")
            .prefetch_related("cells")
        )
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
            uploads = apply_customer_visible_uploads(
                KnowledgeUpload.objects.filter(
                    business_profile=business_profile,
                    status=KnowledgeStatus.ACTIVE,
                )
            ).order_by("-updated_at")[:remaining]
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
    ) -> KnowledgeSnippet:
        upload = chunk.upload
        label = self._public_label(upload)
        chunk_number = (chunk.chunk_index or 0) + 1 if chunk.chunk_index is not None else None
        title = f"{label} – chunk {chunk_number}" if chunk_number else label or "Document"
        summary = self._summarize_chunk(chunk)
        sample_text = ""
        
        # Only use table_row_sample for actual table chunks
        chunk_metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        is_table_chunk_flag = bool(chunk_metadata.get("is_table_chunk"))
        
        if table_row_sample and is_table_chunk_flag:
            pairs = []
            for entry in table_row_sample:
                col = entry.get("column") or ""
                val = entry.get("value") or ""
                combined = f"{col}: {val}".strip(": ")
                if combined:
                    pairs.append(combined)
            if pairs:
                sample_text = "; ".join(pairs)[:500]
        
        # For table chunks, combine sample with summary instead of overwriting
        if sample_text and is_table_chunk_flag:
            if summary:
                summary = f"{summary}\n{sample_text}"[:500]
            else:
                summary = sample_text[:500]
        
        truncated = False
        if content_mode == "abstract":
            content = summary
            read_state = KNOWLEDGE_READ_STATE_SUMMARY
        else:
            content, truncated = self._trim_with_flag(chunk.content, max_chars=self.search_preview_char_limit)
            read_state = KNOWLEDGE_READ_STATE_PREVIEW if truncated else self._read_state_for_content(content, chunk_metadata)
        entity_type = chunk_metadata.get("entity_type")
        entity_name = chunk_metadata.get("entity_name")
        entity_business = chunk_metadata.get("entity_business")
        is_table_chunk = bool(chunk_metadata.get("is_table_chunk"))
        aliases = tuple(chunk_metadata.get("aliases") or ())
        table_count, issue_count = self._structured_counts(upload)
        structured_preview: tuple[Mapping[str, object], ...] = tuple(table_row_sample or ())
        table_hint = None
        if table_count and not structured_preview:
            label_name = "tables" if table_count != 1 else "table"
            table_hint = f"{table_count} structured {label_name} available via load_document"

        source_stage = search_stage or (result.source_stage if result else None)
        confidence = None
        if result:
            confidence = result.alias_confidence or result.rerank_score or result.lexical_score or 0.0
        diagnostics = dict(result.diagnostics) if result else {}
        if result and result.vector_distance is not None:
            diagnostics.setdefault("vector_distance", result.vector_distance)
        diagnostics["structured_table_count"] = table_count
        diagnostics["issue_count"] = issue_count
        trunc_metrics = self._truncation_metrics(upload)
        if trunc_metrics:
            diagnostics.update(trunc_metrics)
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
                "snippet_rerank_ms": diagnostics.get("snippet_rerank_ms"),
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
            from apps.accounts.models import KnowledgeUploadPageBlock
            
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
        
        if upload_id is not None and chunk_id is None:
            # User is requesting a specific page by number - try blocks first
            try:
                upload_obj = (
                    apply_customer_visible_uploads(
                        KnowledgeUpload.objects.filter(
                            id=upload_id,
                            business_profile=business_profile,
                            status=KnowledgeStatus.ACTIVE
                        )
                    )
                    .only("id", "ingestion_metadata", "display_name", "source_type", "summary")
                    .first()
                )
                
                if upload_obj:
                    page_text, page_truncated = self._get_page_text_from_blocks(
                        upload_obj,
                        page_index,
                        max_chars=effective_limit
                    )
                    if page_text:
                        page_from_blocks = True
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
                "page_source": "page_blocks",  # NEW diagnostic
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
                    is_table_chunk=False,
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
            cached_window = self._window_cache_get(upload.id, min_idx, max_idx)
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
                self._window_cache_set(upload.id, min_idx, max_idx, window_chunks)
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
        max_columns: int = 4,
        query: str | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        """
        Build a compact key/value sample for table chunks so search snippets
        carry identifiers without requiring a full read.
        
        NOW CHUNK-SCOPED: Uses table_id from chunk metadata to sample from
        the correct table, not the first table of the upload.
        
        NOW QUERY-AWARE: If query provided, selects most relevant row based
        on token matching instead of always returning first row.
        """
        # Only sample for table chunks
        chunk_metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        if not chunk_metadata.get("is_table_chunk"):
            return tuple()
        
        # Get table_id from chunk metadata (added during ingestion)
        table_id = chunk_metadata.get("table_id")
        if not table_id:
            # Fallback for legacy chunks without table_id - use old behavior
            upload = chunk.upload
            tables_manager = getattr(upload, "tables", None)
            if not hasattr(tables_manager, "all"):
                return tuple()
            try:
                table = tables_manager.order_by("order_index").first()
            except Exception:
                return tuple()
        else:
            # NEW: Use the specific table for this chunk
            try:
                from apps.accounts.models import KnowledgeUploadTable
                table = KnowledgeUploadTable.objects.filter(id=table_id).first()
            except Exception:
                return tuple()
        
        if not table:
            return tuple()
        
        try:
            # Get data rows (exclude headers), limit to top 50 for performance
            rows = list(
                table.rows.filter(row_index__isnull=False)
                .exclude(metadata__row_type='header')
                .order_by("row_index")[:50]  # PERF: Limit to avoid loading thousands of rows
                .prefetch_related(
                    Prefetch(
                        "cells",
                        queryset=KnowledgeUploadTableCell.objects.order_by("column_index"),
                    )
                )
            )
            
            if not rows:
                return tuple()
            
            # NEW: Query-aware row selection
            selected_row = rows[0]  # Default to first row
            
            if query and len(rows) > 1:
                # Tokenize query (simple whitespace split, lowercase)
                query_tokens = set(query.lower().split())
                
                # Score each row by token matches
                best_score = 0
                for row in rows:
                    row_score = 0
                    cells = list(row.cells.all())
                    
                    for cell in cells:
                        cell_text = str(cell.raw_text or "").lower()
                        # Count matching query tokens
                        for token in query_tokens:
                            if token in cell_text:
                                row_score += 1
                    
                    if row_score > best_score:
                        best_score = row_score
                        selected_row = row
            
            # Build sample from selected row
            cells = list(selected_row.cells.all())
            sample: list[Mapping[str, object]] = []
            for cell in cells[:max_columns]:
                sample.append(
                    {
                        "row": selected_row.row_index,
                        "column": cell.column_key or f"column_{(cell.column_index or 0) + 1}",
                        "value": cell.raw_text,
                    }
                )
            return tuple(sample)
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
        first_line = text.splitlines()[0].strip()
        snippet = first_line or text
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
    ) -> StreamingTurnContext:
        """
        Execute the streaming phase of an orchestration turn.

        Returns the partial context required to finalize the turn after all
        response deltas have been emitted.
        """

        query = user_message.strip()
        logger.info(
            "orchestrator turn start conversation=%s agent=%s case=%s message=%s",
            conversation.id,
            self.agent.id,
            conversation.case_id,
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
            self._upsert_knowledge_payload(knowledge_payload, self._prepare_prompt_snippet(cached_snippet))
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

        for _ in range(max_turns):
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
            plan_candidate = self._invoke_llm(prompt_bundle, on_response_text_delta=stream_callback)
            if not plan_candidate:
                final_plan = None
                _notify_stream_complete_once()
                break
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

            forced_full_reads = self._forced_full_table_reads(
                query=query,
                knowledge_payload=knowledge_payload,
                loaded_content_ids=loaded_content_ids,
            )
            for forced in forced_full_reads:
                if forced not in pending_requests and forced not in ready_ids_in_payload:
                    pending_requests.append(forced)

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


    def _is_table_critical_query(self, query: str) -> bool:
        normalized = QueryNormalizer._normalize_query_text(query).lower()
        tokens = {token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if token}
        fee_tokens = set(TOPIC_KEYWORD_MAP.get("fees", ()))
        limit_tokens = set(TOPIC_KEYWORD_MAP.get("limits", ()))
        apr_tokens = set(TOPIC_KEYWORD_MAP.get("apr", ()))
        return any(t in tokens for t in (fee_tokens | limit_tokens | apr_tokens))

    @staticmethod
    def _should_force_read_for_tables(knowledge_payload: Sequence[Mapping[str, object]]) -> tuple[bool, list[str]]:
        """
        Returns (should_force, candidate_ids).
        Force when we see structured table hints but the snippet is not ready/full.
        """
        ids: list[str] = []
        should = False
        for s in knowledge_payload:
            tables = s.get("structuredTables") or []
            table_count = int(s.get("structured_table_count") or 0)
            hint_present = bool(s.get("structured_table_hint"))
            status = str(s.get("status") or "").lower()
            sid = str(s.get("id") or "") if s.get("id") else ""
            has_tables = bool(tables) or table_count > 0 or hint_present
            if has_tables and status != "ready" and sid:
                should = True
                ids.append(sid)
        return should, ids[:3]  # safety: cap the count

    def _forced_full_table_reads(
        self,
        *,
        query: str,
        knowledge_payload: Sequence[Mapping[str, object]],
        loaded_content_ids: set[str],
    ) -> list[str]:
        normalized = QueryNormalizer._normalize_query_text(query).strip().lower()
        if not normalized:
            return []
        aggregate_tokens = {"all", "overall", "entire", "whole", "total", "everything"}
        aggregate_phrases = (
            "how many",
            "total number",
            "entire sheet",
            "whole sheet",
            "all rows",
            "overall count",
        )
        token_hit = any(token in aggregate_tokens for token in normalized.replace("/", " ").split())
        phrase_hit = any(phrase in normalized for phrase in aggregate_phrases)
        if not (token_hit or phrase_hit):
            return []
        token_set = {token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if token}

        forced: list[str] = []
        seen: set[str] = set()
        for entry in knowledge_payload:
            upload_id = entry.get("upload_id")
            chunk_id = entry.get("chunk_id")
            if not upload_id:
                continue
            upload_str = str(upload_id)
            if upload_str in loaded_content_ids:
                continue
            is_chunk = bool(chunk_id)
            has_tables = bool(
                entry.get("structuredTables")
                or entry.get("structured_table_count")
                or entry.get("structured_table_hint")
            )
            if not has_tables and not is_chunk:
                continue
            status = str(entry.get("status") or "").lower()
            if status == "ready" and not is_chunk:
                continue
            keywords = self._extract_snippet_keywords(entry)
            if keywords and token_set and keywords.isdisjoint(token_set):
                continue
            if upload_str in seen:
                continue
            seen.add(upload_str)
            forced.append(upload_str)
        return forced


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
            "structuredTables": list(snippet.structured_tables or ()),
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

    def _invoke_llm(self, bundle: PromptBundle, *, on_response_text_delta: Callable[[str], None] | None = None) -> LlmPlan | None:
        if not self.provider:
            logger.info("LLM provider is not configured; using heuristic planner.")
            return None
        try:
            logger.info("Invoking LLM provider %s", self.provider.__class__.__name__)
            raw = self.provider.generate(bundle, on_stream_delta=on_response_text_delta)
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

        return LlmPlan(
            response_text=text,
            planned_actions=planned_actions,
            extractions=extractions,
            knowledge_requests=tuple(knowledge_requests),
            response_blocks=response_blocks,
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
        plans: list[PlannedAction] = []
        extractions: list[ExtractionPlan] = []
        lower = user_message.lower()

        if (
            conversation.case_id is None
            and self._is_enabled(ActionType.CREATE_CASE)
            and _is_business_text(user_message)
        ):
            plans.append(
                PlannedAction(
                    action=ActionType.CREATE_CASE,
                    payload={
                        "title": self._derive_case_title(user_message),
                        "description": user_message,
                        "priority": CasePriority.HIGH if "urgent" in lower or "immediately" in lower else CasePriority.MEDIUM,
                        "ai_diagnosis": f"Initial issue reported: {user_message[:500]}",
                        "ai_actions_taken": "",
                        "ai_suggested_actions": [
                            "Review customer account",
                            "Follow up via email once resolved",
                        ],
                        "metadata": {"source": "ai_orchestrator"},
                    },
                )
            )

        if conversation.case_id and "resolved" in lower and self._is_enabled(ActionType.UPDATE_CASE_STATUS):
            plans.append(
                PlannedAction(
                    action=ActionType.UPDATE_CASE_STATUS,
                    payload={"status": CaseStatus.CLOSED},
                )
            )

        if (
            conversation.case_id
            and self._is_enabled(ActionType.ADD_CASE_HISTORY)
            and _is_business_text(user_message)
        ):
            summary = self._summarize_history(user_message)
            if summary:
                plans.append(
                    PlannedAction(
                        action=ActionType.ADD_CASE_HISTORY,
                        payload={
                            "summary": summary,
                            "source": "customer",
                            "metadata": {"auto_generated": True},
                        },
                    )
                )

        if "escalate" in lower and self._is_enabled(ActionType.FLAG_ESCALATION):
            plans.append(
                PlannedAction(
                    action=ActionType.FLAG_ESCALATION,
                    payload={"reason": "Customer requested escalation", "priority": CasePriority.CRITICAL},
                )
            )

        if "appointment" in lower and self._is_enabled(ActionType.CREATE_APPOINTMENT):
            extractions.append(
                ExtractionPlan(
                    extraction_type=ConversationExtractionType.APPOINTMENT,
                    payload={"note": user_message, "detected_at": timezone.now().isoformat()},
                )
            )

        if "lead" in lower or "pricing" in lower:
            extractions.append(
                ExtractionPlan(
                    extraction_type=ConversationExtractionType.LEAD,
                    payload={"note": user_message, "interest": "pricing" if "pricing" in lower else "general"},
                )
            )

        if "complaint" in lower:
            extractions.append(
                ExtractionPlan(
                    extraction_type=ConversationExtractionType.COMPLAINT,
                    payload={"note": user_message},
                )
            )

        return plans, extractions

    @staticmethod
    def _derive_case_title(user_message: str) -> str:
        base = user_message.split(".")[0][:80]
        return base or "Customer issue reported"

    @staticmethod
    def _infer_customer_name(user_message: str) -> str:
        tokens = user_message.split()
        hint = tokens[0].strip(",:") if tokens else "Customer"
        return hint.title() if hint else "Customer"

    @staticmethod
    def _summarize_history(user_message: str) -> str:
        snippet = " ".join(user_message.strip().split())
        return snippet[:320]

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
                        logger.warning("action_dispatcher skipped action %s: no handler", plan.action)
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
                        logger.info("action_dispatcher applied %s | payload=%s", plan.action, plan.payload)
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
                        logger.warning("action_dispatcher failed %s | error=%s | payload=%s", plan.action, exc, plan.payload)
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

    # ------------------------------------------------------------------
    # Individual action handlers

    def _handle_create_case(self, *, conversation: Conversation, payload: dict) -> dict:
        if conversation.case_id:
            return {"case_id": str(conversation.case_id), "skipped": True}
        description = (payload.get("description") or "").strip()
        if not _is_business_text(description):
            raise ActionExecutionError("Case description lacks business context; creation aborted.")
        with transaction.atomic():
            metadata_payload = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            case = Case.objects.create(
                business_profile=conversation.business_profile,
                agent_profile=conversation.agent_profile,
                customer=conversation.customer,
                title=payload.get("title") or "Customer request",
                description=payload.get("description") or "",
                priority=payload.get("priority") or CasePriority.MEDIUM,
                status=CaseStatus.OPEN,
                ai_diagnosis=payload.get("ai_diagnosis", ""),
                ai_actions_taken=payload.get("ai_actions_taken", ""),
                ai_suggested_actions=payload.get("ai_suggested_actions", []),
                metadata={"source": "ai_orchestrator", **metadata_payload},
            )
            conversation.case = case
            conversation.status = ConversationStatus.LIVE
            conversation.save(update_fields=["case", "status", "last_activity_at"])
        return {"case_id": str(case.id), "case_number": case.case_number}

    def _handle_update_case_status(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.case_id:
            raise ActionExecutionError("No linked case to update")
        status = (payload.get("status") or "").strip().lower()
        alias = {
            "resolved": CaseStatus.CLOSED,
            "close": CaseStatus.CLOSED,
            "closed": CaseStatus.CLOSED,
            "open": CaseStatus.OPEN,
            "reopen": CaseStatus.OPEN,
        }
        status = alias.get(status, status)
        if status not in CaseStatus.values:
            raise ActionExecutionError(f"Unsupported status transition: {payload.get('status')}")
        fields = ["last_activity_at"]
        conversation.case.status = status
        conversation.case.save(update_fields=["status", "updated_at"])
        if status == CaseStatus.CLOSED:
            # Preserve the live chat session so the visitor can continue chatting even after the case closes.
            fields.append("closed_at")
        conversation.save(update_fields=fields)
        return {"case_id": str(conversation.case_id), "status": status}

    def _handle_update_case_details(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.case_id:
            raise ActionExecutionError("No linked case to update")
        case = conversation.case
        fields: list[str] = []
        if payload.get("description"):
            if not payload.get("allow_description_overwrite"):
                raise ActionExecutionError("Description updates require allow_description_overwrite=true")
            if not _is_business_text(payload["description"]):
                raise ActionExecutionError("New description lacks business context")
        for attr in ("title", "ai_diagnosis", "ai_actions_taken"):
            if payload.get(attr) and getattr(case, attr) != payload[attr]:
                setattr(case, attr, payload[attr])
                fields.append(attr)
        if payload.get("description") and case.description != payload["description"]:
            case.description = payload["description"]
            fields.append("description")
        priority = payload.get("priority")
        if priority:
            normalized = priority.lower()
            alias = {
                "high": CasePriority.HIGH,
                "critical": CasePriority.CRITICAL,
                "medium": CasePriority.MEDIUM,
                "low": CasePriority.LOW,
            }
            normalized = alias.get(normalized, normalized)
            if normalized in CasePriority.values and case.priority != normalized:
                case.priority = normalized
                fields.append("priority")
        metadata = payload.get("metadata")
        if metadata and isinstance(metadata, dict):
            case.metadata = {**(case.metadata or {}), **metadata}
            fields.append("metadata")
        if not fields:
            return {"case_id": str(case.id), "updated_fields": []}
        fields.append("updated_at")
        case.save(update_fields=fields)
        return {"case_id": str(case.id), "updated_fields": fields}

    def _handle_flag_escalation(self, *, conversation: Conversation, payload: dict) -> dict:
        reason = payload.get("reason") or "Escalated by AI orchestrator"
        conversation.status = ConversationStatus.ESCALATED
        conversation.save(update_fields=["status", "last_activity_at"])
        if conversation.case_id:
            case = conversation.case
            priority = payload.get("priority") or CasePriority.CRITICAL
            case.priority = priority
            case.save(update_fields=["priority", "updated_at"])
        ConversationExtraction.objects.create(
            conversation=conversation,
            extraction_type=ConversationExtractionType.ESCALATION,
            payload={"reason": reason, "metadata": payload},
        )
        return {"reason": reason}

    def _handle_add_case_history(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.case_id:
            raise ActionExecutionError("No linked case to update")
        summary = (payload.get("summary") or payload.get("entry") or payload.get("note") or payload.get("description") or "").strip()
        if not summary:
            raise ActionExecutionError("History summary is required")
        source = payload.get("source") or "ai"
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        case = conversation.case
        if not case:
            case = Case.objects.filter(pk=conversation.case_id).first()
            if not case:
                raise ActionExecutionError("Case not found")
        entry = CaseHistoryEntry.objects.create(
            case=case,
            summary=summary[:500],
            source=source,
            session_reference=str(conversation.session_token),
            metadata={**metadata, "author": "ai_orchestrator"},
        )
        return {"history_id": str(entry.id)}

    def _handle_create_customer(self, *, conversation: Conversation, payload: dict) -> dict:
        business = conversation.business_profile
        email_raw = payload.get("primary_email") or payload.get("email")
        phone_raw = (
            payload.get("primary_phone")
            or payload.get("phone")
            or payload.get("mobile")
            or payload.get("contact_number")
        )
        email = self._normalize_email(email_raw)
        phone = self._normalize_phone(phone_raw)
        display_name = (payload.get("display_name") or "").strip() or "Web Visitor"
        refused_contact = bool(payload.get("refused_contact"))

        existing = self._match_customer(business_profile=business, email=email, phone=phone)
        if existing:
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            updated_fields: list[str] = []
            if (
                display_name
                and display_name.lower() not in {"web visitor", "customer"}
                and existing.display_name != display_name
            ):
                existing.display_name = display_name
                updated_fields.append("display_name")
            if metadata:
                existing.metadata = {**(existing.metadata or {}), **metadata}
                updated_fields.append("metadata")
            if updated_fields:
                updated_fields.append("updated_at")
                existing.save(update_fields=updated_fields)
            conversation.customer = existing
            conversation.save(update_fields=["customer", "last_activity_at"])
            self._link_customer_to_case(conversation=conversation, customer=existing)
            return {"customer_id": str(existing.id), "matched": True}

        if not email and not phone and not refused_contact:
            raise ActionExecutionError("Cannot create customer without email/phone or explicit refusal.")

        with transaction.atomic():
            customer = Customer.objects.create(
                business_profile=business,
                agent_profile=conversation.agent_profile,
                display_name=display_name,
                primary_email=email or "",
                primary_phone=phone_raw or "",
                record_origin=payload.get("record_origin") or CustomerRecordOrigin.AI_EXTRACTED,
                metadata={**(payload.get("metadata") or {}), **({"contact_refused": True} if not email and not phone else {})},
            )
            conversation.customer = customer
            conversation.save(update_fields=["customer", "last_activity_at"])
        self._link_customer_to_case(conversation=conversation, customer=customer)
        return {"customer_id": str(customer.id), "matched": False}

    def _handle_update_customer(self, *, conversation: Conversation, payload: dict) -> dict:
        if not conversation.customer_id:
            raise ActionExecutionError("No customer attached to conversation")
        customer = conversation.customer
        fields = []
        for attr in ("primary_email", "primary_phone"):
            if attr in payload and payload.get(attr):
                raise ActionExecutionError(f"Updating {attr} is not allowed")
        if payload.get("display_name") and customer.display_name != payload["display_name"]:
            customer.display_name = payload["display_name"]
            fields.append("display_name")
        if payload.get("metadata"):
            customer.metadata = {**(customer.metadata or {}), **payload["metadata"]}
            fields.append("metadata")
        if not fields:
            return {"customer_id": str(customer.id), "updated": False}
        fields.append("updated_at")
        customer.save(update_fields=fields)
        return {"customer_id": str(customer.id), "updated": True}

    def _handle_read_knowledge(self, *, conversation: Conversation, payload: dict) -> dict:  # pragma: no cover - safeguard
        requested = payload.get("knowledge_ids") or payload.get("knowledge_id") or []
        if isinstance(requested, str):
            requested = [requested]
        return {"requested_ids": requested, "status": "handled_upstream"}

    def _match_customer(self, *, business_profile, email: str | None, phone: str | None) -> Customer | None:
        qs = Customer.objects.filter(business_profile=business_profile)
        if email:
            try:
                return qs.get(primary_email__iexact=email)
            except Customer.DoesNotExist:
                pass
        if phone:
            normalized_phone = phone
            for candidate in qs.exclude(primary_phone=""):
                if self._normalize_phone(candidate.primary_phone) == normalized_phone:
                    return candidate
        return None

    @staticmethod
    def _normalize_email(value: str | None) -> str | None:
        if not value:
            return None
        email = value.strip().lower()
        return email or None

    @staticmethod
    def _normalize_phone(value: str | None) -> str | None:
        if not value:
            return None
        digits = "".join(ch for ch in value if ch.isdigit())
        return digits or None

    def _link_customer_to_case(self, *, conversation: Conversation, customer: Customer) -> None:
        if not conversation.case_id:
            return
        case = conversation.case
        if not case:
            try:
                case = Case.objects.get(pk=conversation.case_id)
            except Case.DoesNotExist:
                return
        if case.customer_id == customer.id:
            return
        case.customer = customer
        case.customer_snapshot = {
            "display_name": customer.display_name,
            "primary_email": customer.primary_email,
            "primary_phone": customer.primary_phone,
        }
        case.save(update_fields=["customer", "customer_snapshot", "updated_at"])

    def _handle_create_lead(self, *, conversation: Conversation, payload: dict) -> dict:
        extraction = ConversationExtraction.objects.create(
            conversation=conversation,
            extraction_type=ConversationExtractionType.LEAD,
            payload=payload or {},
        )
        return {"extraction_id": str(extraction.id)}

    def _handle_create_appointment(self, *, conversation: Conversation, payload: dict) -> dict:
        extraction = ConversationExtraction.objects.create(
            conversation=conversation,
            extraction_type=ConversationExtractionType.APPOINTMENT,
            payload=payload or {},
        )
        return {"extraction_id": str(extraction.id)}
