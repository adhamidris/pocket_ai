from __future__ import annotations

import dataclasses
import logging
import uuid
from enum import Enum
from types import SimpleNamespace
import re
from typing import Callable, Iterable, Mapping, Sequence

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.accounts.models import (
    AgentProfile,
    KnowledgeStatus,
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
from apps.services.ai_prompt_builder import PromptBuilder, PromptBundle
from apps.services.embeddings import build_embedding_service, EmbeddingProviderError
from apps.services.llm_provider import BaseLLMProvider, PromptGenerationError


logger = logging.getLogger(__name__)


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
    public_label: str | None = None
    structured_tables: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    issues: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    page_summaries: Sequence[Mapping[str, object]] = dataclasses.field(default_factory=tuple)
    read_state: str = "summary"
    topic_hints: Sequence[str] = dataclasses.field(default_factory=tuple)
    is_pinned: bool = False


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


@dataclasses.dataclass(frozen=True)
class ActionExecutionResult:
    action: ActionType
    status: str
    metadata: dict
    error: str | None = None


class ActionExecutionError(Exception):
    """Raised when an action cannot be executed."""


MAX_INLINE_KNOWLEDGE_CHARS = 60000
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

    def search(self, *, business_profile, query: str, limit: int = 3) -> Sequence[KnowledgeSnippet]:
        normalized_query = (query or "").strip()
        if normalized_query:
            chunk_snippets = self._search_chunks(business_profile, normalized_query, limit=limit)
            if chunk_snippets:
                return tuple(chunk_snippets)
        return tuple(self._fallback_snippets(business_profile=business_profile, limit=limit))

    def _search_chunks(self, business_profile, query: str, limit: int) -> Sequence[KnowledgeSnippet]:
        hits = self._chunk_hits(business_profile, query, limit=limit * 3)
        if not hits:
            return tuple()

        snippets: list[KnowledgeSnippet] = []
        seen_uploads: set[uuid.UUID] = set()
        for chunk in hits:
            upload = chunk.upload
            if upload.id in seen_uploads:
                continue
            snippets.append(self._chunk_to_snippet(chunk))
            seen_uploads.add(upload.id)
            if len(snippets) >= limit:
                break
        return tuple(snippets)

    def _chunk_hits(self, business_profile, query: str, limit: int) -> Sequence[KnowledgeUploadChunk]:
        base_qs = (
            KnowledgeUploadChunk.objects.filter(
                upload__business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .select_related("upload")
            .order_by("-upload__updated_at")
        )
        # Embedding search
        if self.embedding_service:
            candidates = list(base_qs.exclude(embedding__isnull=True)[:400])
            if candidates:
                try:
                    query_vector = self.embedding_service.embed_text(query)
                except EmbeddingProviderError as exc:
                    logger.warning("Query embedding failed: %s", exc)
                else:
                    if query_vector:
                        scored: list[tuple[float, KnowledgeUploadChunk]] = []
                        for chunk in candidates:
                            vector = chunk.embedding
                            if not isinstance(vector, list):
                                continue
                            score = self._cosine_similarity(query_vector, vector)
                            scored.append((score, chunk))
                        scored.sort(key=lambda item: item[0], reverse=True)
                        hits = [chunk for score, chunk in scored[:limit] if score > 0]
                        if hits:
                            return tuple(hits)
        # Keyword fallback
        keyword_hits = (
            base_qs.filter(content__icontains=query)
            .order_by("-upload__updated_at")[:limit]
        )
        return tuple(keyword_hits)

    def _fallback_snippets(self, *, business_profile, limit: int) -> Sequence[KnowledgeSnippet]:
        qs = (
            KnowledgeUpload.objects.filter(
                business_profile=business_profile,
                status=KnowledgeStatus.ACTIVE,
            )
            .order_by("-updated_at")[:limit]
        )
        snippets: list[KnowledgeSnippet] = []
        for upload in qs:
            label = self._public_label(upload)
            structured = self._structured_exports(upload)
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
                )
            )
        if not snippets:
            logger.warning("Knowledge load returned no snippets for business=%s", business_profile.id)
        return tuple(snippets)

    def _chunk_to_snippet(self, chunk: KnowledgeUploadChunk) -> KnowledgeSnippet:
        upload = chunk.upload
        label = self._public_label(upload)
        summary = self._summarize_chunk(chunk)
        content = self._trim_content(chunk.content, max_chars=1200)
        structured = self._structured_exports(upload)
        return KnowledgeSnippet(
            id=upload.id,
            title=label,
            summary=summary,
            source=upload.source_name or upload.source_type,
            content=content,
            public_label=label,
            structured_tables=structured["tables"],
            issues=structured["issues"],
            page_summaries=structured["pages"],
            read_state=KNOWLEDGE_READ_STATE_PREVIEW if content else KNOWLEDGE_READ_STATE_SUMMARY,
            topic_hints=self._topic_hints(upload),
            is_pinned=self._is_pinned(upload),
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
            queryset=KnowledgeUploadTable.objects.order_by("order_index").select_related("page").prefetch_related(
                Prefetch(
                    "rows",
                    queryset=KnowledgeUploadTableRow.objects.order_by("row_index").prefetch_related(
                        Prefetch(
                            "cells",
                            queryset=KnowledgeUploadTableCell.objects.order_by("column_index"),
                        )
                    ),
                )
            ),
        )
        issue_prefetch = Prefetch(
            "issues",
            queryset=KnowledgeUploadIssue.objects.order_by("-created_at").select_related("page", "table", "table_row", "table_cell"),
        )
        qs = (
            KnowledgeUpload.objects.filter(
                business_profile=business_profile,
                status=KnowledgeStatus.ACTIVE,
                id__in=normalized,
            )
            .select_related("text_detail")
            .prefetch_related(table_prefetch, issue_prefetch)
            .order_by("-updated_at")
        )
        snippets: list[KnowledgeSnippet] = []
        limit = max_chars if max_chars is not None else MAX_INLINE_KNOWLEDGE_CHARS
        for upload in qs:
            label = self._public_label(upload)
            raw_content = self._extract_content(upload)
            content = self._trim_content(raw_content, max_chars=limit) if raw_content else ""
            structured = self._structured_exports(upload)
            table_text = self._render_structured_tables_text(upload)
            issue_text = self._render_issue_text(upload)
            supplemental_sections = [content]
            if table_text:
                supplemental_sections.append(table_text)
            if issue_text:
                supplemental_sections.append(issue_text)
            combined_content = "\n\n".join(section for section in supplemental_sections if section)
            snippets.append(
                KnowledgeSnippet(
                    id=upload.id,
                    title=label,
                    summary=self._summarize_upload(upload),
                    source=upload.source_name or upload.source_type,
                    content=combined_content,
                    public_label=label,
                    structured_tables=structured["tables"],
                    issues=structured["issues"],
                    page_summaries=structured["pages"],
                    read_state=KNOWLEDGE_READ_STATE_FULL if combined_content else KNOWLEDGE_READ_STATE_SUMMARY,
                    topic_hints=self._topic_hints(upload),
                    is_pinned=self._is_pinned(upload),
                )
            )
        return tuple(snippets)

    @staticmethod
    def _structured_exports(upload: KnowledgeUpload, *, max_tables: int = 3) -> dict[str, tuple[Mapping[str, object], ...]]:
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
            "pages": _normalize_list(pages, 10),
        }

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
        text = (content or "").strip()
        if not text or max_chars <= 0:
            return text
        if len(text) <= max_chars:
            return text
        logger.info("Trimming knowledge content from %s to %s chars", len(text), max_chars)
        trimmed = text[:max_chars].rstrip()
        return f"{trimmed}\n\n[Content truncated]"

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


class AiOrchestratorService:
    """
    Coordinates knowledge retrieval, action planning, and future LLM interactions.

    The current implementation uses heuristic planning to keep the pipeline testable
    until the prompt builder + provider integration is ready.
    """

    def __init__(self, *, agent: AgentProfile, provider: BaseLLMProvider | None = None):
        self.agent = agent
        self.knowledge_service = KnowledgeSearchService()
        self.prompt_builder = PromptBuilder(agent)
        self.provider = provider
        self._permission_cache: dict[str, bool] = {}
        self._hydrate_permissions()

    # ------------------------------------------------------------------
    # Public API

    def run_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
    ) -> AiOrchestratorPlan:
        """
        Build the orchestration plan for the latest customer message.

        Returns the AI response text placeholder, citations, action plans, and any
        extracted entities (lead, appointment, etc.) that should be persisted.
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
        turn_index = int(metadata_snapshot.get("knowledge_turn_counter") or 0) + 1
        metadata_snapshot["knowledge_turn_counter"] = turn_index
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
        visitor_mentions, mention_updates = self._detect_snippet_mentions(
            query=query,
            cached_entries=cached_entries,
            turn_index=turn_index,
        )
        if mention_updates:
            cache_dirty = True

        citations = list(
            self.knowledge_service.search(
                business_profile=conversation.business_profile,
                query=query,
            )
        )
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
        snippet_lookup: dict[str, KnowledgeSnippet] = {str(snippet.id): snippet for snippet in citations}
        loaded_content_ids: set[str] = {
            str(identifier)
            for identifier, payload in cached_entries.items()
            if isinstance(payload, dict) and payload.get("content")
        }
        knowledge_reads: list[dict[str, object]] = []
        placeholder_response: str | None = None
        knowledge_loading = False
        placeholder_added_to_prompt = False
        streamed_chunks: list[str] = []
        active_iteration_chunks: list[str] = []
        last_iteration_streamed = False
        iteration_streamed = False
        cache_satisfied_read = False

        def _emit_stream_chunk(chunk: str) -> None:
            if not chunk:
                return
            if on_response_text_delta:
                on_response_text_delta(chunk)

        def _provider_stream_callback(chunk: str) -> None:
            nonlocal iteration_streamed, active_iteration_chunks
            if not chunk:
                return
            iteration_streamed = True
            active_iteration_chunks.append(chunk)
            _emit_stream_chunk(chunk)

        def _remember_placeholder_for_prompt(text: str | None) -> None:
            nonlocal placeholder_added_to_prompt, recent_messages
            if placeholder_added_to_prompt or not text:
                return
            # 1) Persist a separate placeholder message so it never disappears
            placeholder_msg = ConversationMessage.objects.create(
                conversation=conversation,
                sender=ConversationSender.AI,
                body=text.strip(),
                metadata={"placeholder": True},
            )
            # 2) Also inject into this turn's in-memory transcript so the next LLM pass "remembers" it
            recent_messages.append(placeholder_msg)
            placeholder_added_to_prompt = True

        prompt_bundle: PromptBundle | None = None
        final_plan: LlmPlan | None = None
        plan_candidate: LlmPlan | None = None
        max_turns = 3

        for _ in range(max_turns):
            active_iteration_chunks = []
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
                break
            ready_ids_in_payload = {
                str(s.get("id"))
                for s in knowledge_payload
                if s.get("content") or str(s.get("status") or "").lower() == "ready"
            }
            pending_requests = [
                kid for kid in plan_candidate.knowledge_requests
                if kid not in loaded_content_ids and kid not in ready_ids_in_payload
            ]
            if not pending_requests:
                final_plan = plan_candidate
                requested_again = bool(getattr(plan_candidate, "knowledge_requests", None))
                if requested_again:
                    cache_satisfied_read = True
                    if on_status_change:
                        on_status_change("reading_document")
                last_iteration_streamed = iteration_streamed
                if iteration_streamed:
                    streamed_chunks = list(active_iteration_chunks)
                else:
                    streamed_chunks = []
                if requested_again and on_status_change:
                    on_status_change("responding")
                break

            if plan_candidate.response_text:
                placeholder_response = plan_candidate.response_text
                _remember_placeholder_for_prompt(placeholder_response)
            if on_placeholder_response and placeholder_response:
                on_placeholder_response(placeholder_response.strip())

            knowledge_loading = True
            if on_status_change:
                on_status_change("reading_document")

            fetched = self.knowledge_service.load_contents(
                business_profile=conversation.business_profile,
                knowledge_ids=pending_requests,
            )
            if not fetched:
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
            for snippet in fetched:
                key = str(snippet.id)
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

        metadata_snapshot["knowledge_cache"] = cached_entries
        if cache_dirty or metadata_dirty:
            conversation.metadata = metadata_snapshot
            conversation.save(update_fields=["metadata"])

        llm_plan = final_plan
        resolved_citations = tuple(snippet_lookup.values()) if snippet_lookup else tuple(citations)
        if llm_plan:
            if knowledge_loading and on_status_change:
                on_status_change("responding")
            planned_actions = [
                action for action in llm_plan.planned_actions if action.action != ActionType.READ_KNOWLEDGE
            ]
            extractions = list(llm_plan.extractions)
            response_text = (llm_plan.response_text or "").strip()
            if knowledge_loading and not response_text and placeholder_response:
                response_text = placeholder_response.strip()
            llm_source = "provider"
        else:
            planned_actions, extractions = self._plan_actions(conversation=conversation, user_message=query)
            response_text = self._compose_placeholder_response(
                user_message=query,
                citations=resolved_citations,
                planned_actions=planned_actions,
            )
            llm_source = "heuristic"

        if response_text and not last_iteration_streamed:
            has_ready_context = any(bool(item.get("content")) for item in knowledge_payload)

            # CHANGED: only strip overlap when there was no placeholder committed
            response_text = self._strip_placeholder_overlap(placeholder_response, response_text)


            response_text = self._dedupe_response(
                conversation,
                response_text,
                knowledge_reads,
                has_ready_context=has_ready_context,
            )
            streamed_chunks = [response_text]
            _emit_stream_chunk(response_text)

        streamed_text = "".join(streamed_chunks).strip()
        if streamed_text:
            response_text = streamed_text

        response_stream_text = response_text

        diagnostics = {
            "planned_action_count": len(planned_actions),
            "extraction_count": len(extractions),
            "citations": [snippet.title for snippet in resolved_citations],
            "llm_strategy": llm_source,
            "prompt_preview": prompt_bundle.system_prompt[:160] if prompt_bundle else "",
            "transcript_messages": len(prompt_bundle.transcript) if prompt_bundle else 0,
            "knowledge_reads": knowledge_reads,
            "knowledge_loading": knowledge_loading,
            "response_stream_text": response_stream_text,
            "cached_snippets": len(cached_entries),
        }
        if placeholder_response:
            diagnostics["placeholder_response"] = placeholder_response.strip()

        self._log_plan_summary(
            conversation=conversation,
            source=llm_source,
            planned_actions=planned_actions,
            extractions=extractions,
            diagnostics=diagnostics,
        )

        return AiOrchestratorPlan(
            response_text=response_text,
            citations=resolved_citations,
            planned_actions=planned_actions,
            extractions=extractions,
            diagnostics=diagnostics,
        )

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
        state = snapshot.get("read_state") or ""
        if (state == KNOWLEDGE_READ_STATE_FULL) or snapshot.get("content"):
            return "ready"
        if state == KNOWLEDGE_READ_STATE_PREVIEW:
            return "preview"
        return "summary-only"

    def _prepare_prompt_snippet(self, entry: Mapping[str, object]) -> dict[str, object]:
        payload = dict(entry)
        payload["status"] = payload.get("status") or self._determine_snippet_status(payload)
        payload.setdefault("coverage", [])

        if payload.get("content"):
            payload["read_state"] = KNOWLEDGE_READ_STATE_FULL
            payload["status"] = "ready"
        else:
            payload["read_state"] = payload.get("read_state") or KNOWLEDGE_READ_STATE_SUMMARY

        payload["pin"] = bool(payload.get("pin"))
        payload.pop("last_active_turn", None)
        payload.pop("last_customer_reference_turn", None)
        return payload

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
        normalized_query = (query or "").lower()
        tokenized = set(re.split(r"[^a-z0-9]+", normalized_query))
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
                for token in re.split(r"[^a-z0-9]+", label.lower()):
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
        normalized_text = (text or "").lower()
        tokens = set(re.split(r"[^a-z0-9]+", normalized_text))
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
        normalized_query = (query or "").lower()
        tokens = [token for token in re.split(r"[^a-z0-9]+", label.lower()) if len(token) >= 4]
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

    @staticmethod
    def _serialize_snippet(snippet: KnowledgeSnippet) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": str(snippet.id),
            "title": snippet.title,
            "summary": snippet.summary,
            "source": snippet.source,
            "data_ready": bool(snippet.content),
            "read_state": snippet.read_state,
        }
        if snippet.public_label:
            payload["public_label"] = snippet.public_label
        if snippet.content:
            payload["content"] = snippet.content
        if snippet.structured_tables:
            payload["structuredTables"] = [dict(table) for table in snippet.structured_tables]
        if snippet.issues:
            payload["issues"] = [dict(issue) for issue in snippet.issues]
        if snippet.page_summaries:
            payload["pageSummaries"] = [dict(page) for page in snippet.page_summaries]
        if snippet.topic_hints:
            payload["topic_hints"] = list(snippet.topic_hints)
        if snippet.is_pinned:
            payload["pin"] = True
        return payload

    @staticmethod
    def _upsert_knowledge_payload(payload: list[dict], snippet_payload: Mapping[str, object]) -> None:
        target_id = snippet_payload.get("id")
        for existing in payload:
            if existing.get("id") == target_id:
                existing.update(snippet_payload)
                break
        else:
            payload.append(dict(snippet_payload))

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
            "id": clean_id or "missing-document",
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

        return LlmPlan(
            response_text=text,
            planned_actions=planned_actions,
            extractions=extractions,
            knowledge_requests=tuple(knowledge_requests),
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
        results: list[ActionExecutionResult] = []
        for plan in planned_actions:
            handler = getattr(self, f"_handle_{plan.action.value}", None)
            if handler is None:
                logger.warning("action_dispatcher skipped action %s: no handler", plan.action)
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
                results.append(
                    ActionExecutionResult(
                        action=plan.action,
                        status="applied",
                        metadata=metadata,
                    )
                )
            except ActionExecutionError as exc:
                logger.warning("action_dispatcher failed %s | error=%s | payload=%s", plan.action, exc, plan.payload)
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
