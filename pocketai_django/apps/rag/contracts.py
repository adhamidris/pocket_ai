from __future__ import annotations

import dataclasses
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from apps.conversations.models import Conversation, ConversationExtractionType
    from apps.knowledge.models import KnowledgeUploadChunk
    from apps.llm.ai_prompt_builder import PromptBundle


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
    chunk: "KnowledgeUploadChunk"
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


@dataclasses.dataclass(frozen=True)
class PlannedAction:
    action: ActionType
    payload: dict


@dataclasses.dataclass(frozen=True)
class ExtractionPlan:
    extraction_type: "ConversationExtractionType"
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
    conversation: "Conversation"
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
    prompt_bundle: "PromptBundle | None"
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
