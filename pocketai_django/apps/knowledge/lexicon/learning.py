from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from django.conf import settings

from apps.knowledge.models import KnowledgeLexiconTerm, KnowledgeUpload
from apps.rag.lexicon.tenant import (
    TenantLexiconService,
    normalize_language_code,
    normalize_lexicon_text,
)

_COLUMN_PLACEHOLDER_RE = re.compile(r"^(?:column|col|field|value)[\s_-]?\d+$", re.IGNORECASE)
_GENERIC_TABLE_RE = re.compile(r"^table\s+\d+$", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[_\-/]+")
_SPACE_RE = re.compile(r"\s+")

_STOP_TERMS = {
    "a",
    "an",
    "and",
    "any",
    "all",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "none",
    "null",
    "n a",
    "na",
    "n/a",
    "unknown",
}

_ENTITY_SCHEMA_KEYS = {
    "tool",
    "tool_name",
    "resource",
    "resource_name",
    "name",
    "title",
    "entity",
    "entity_type",
}

_ATTRIBUTE_SCHEMA_KEYS = {
    "field",
    "field_name",
    "column",
    "column_name",
    "attribute",
    "property",
    "parameter",
    "param",
}

_ATTRIBUTE_SCHEMA_CONTAINERS = {
    "columns",
    "column_schema",
    "fields",
    "attributes",
    "headers",
}

_SCHEMA_OBJECT_KEYS = {
    "schema",
    "tool_schema",
    "input_schema",
    "output_schema",
    "parameters",
    "properties",
}


@dataclass
class _CandidateTerm:
    term_type: str
    canonical_text: str
    normalized: str
    confidence_score: float
    origins: set[str] = field(default_factory=set)
    synonyms: set[str] = field(default_factory=set)


class TenantLexiconAutoLearningService:
    """
    Phase 3 ingestion-time lexicon learning.

    Learns tenant-specific terms from extraction artifacts:
    - entities/aliases inferred during ingestion
    - table headers and table titles
    - document labels (upload/source/section headings)
    - integration/tool schema metadata when available
    """

    def __init__(self, *, lexicon_service: TenantLexiconService | None = None) -> None:
        self.lexicon_service = lexicon_service or TenantLexiconService()
        self.enabled = bool(getattr(settings, "RAG_TENANT_LEXICON_AUTO_LEARN_ENABLED", True))
        self.max_entity_terms = max(
            0, int(getattr(settings, "RAG_TENANT_LEXICON_AUTO_LEARN_MAX_ENTITY_TERMS", 40))
        )
        self.max_attribute_terms = max(
            0, int(getattr(settings, "RAG_TENANT_LEXICON_AUTO_LEARN_MAX_ATTRIBUTE_TERMS", 120))
        )
        self.max_synonyms_per_term = max(
            0, int(getattr(settings, "RAG_TENANT_LEXICON_AUTO_LEARN_MAX_SYNONYMS_PER_TERM", 8))
        )
        self.entity_scan_limit = max(
            0, int(getattr(settings, "RAG_TENANT_LEXICON_AUTO_LEARN_ENTITY_SCAN_LIMIT", 500))
        )
        self.metadata_scan_limit = max(
            0, int(getattr(settings, "RAG_TENANT_LEXICON_AUTO_LEARN_METADATA_SCAN_LIMIT", 400))
        )

    def learn_from_ingestion(
        self,
        *,
        upload: KnowledgeUpload,
        extraction: Any,
        structured_summary: Mapping[str, Any] | None = None,
        ingestion_metadata: Mapping[str, Any] | None = None,
        entity_payloads: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "term_count": 0,
                "synonym_count": 0,
                "language_code": "und",
            }

        language_code = self._resolve_language_code(
            upload=upload,
            extraction_metadata=getattr(extraction, "metadata", None),
            ingestion_metadata=ingestion_metadata,
        )
        candidates: dict[tuple[str, str], _CandidateTerm] = {}
        self._collect_document_terms(candidates=candidates, upload=upload, extraction=extraction)
        self._collect_table_terms(
            candidates=candidates,
            extraction=extraction,
            structured_summary=structured_summary,
        )
        self._collect_entity_terms(
            candidates=candidates,
            extraction=extraction,
            entity_payloads=entity_payloads,
        )
        self._collect_schema_terms(
            candidates=candidates,
            extraction_metadata=getattr(extraction, "metadata", None),
            ingestion_metadata=ingestion_metadata,
        )

        persisted_terms = 0
        persisted_synonyms = 0
        for candidate in self._rank_and_limit(candidates):
            metadata = {
                "auto_learned": True,
                "origins": sorted(candidate.origins)[:8],
                "upload_id": str(upload.id),
                "upload_source_type": upload.source_type,
            }
            synonym_values = sorted(candidate.synonyms)[: self.max_synonyms_per_term]
            self.lexicon_service.upsert_term(
                business_profile=upload.business_profile,
                term_type=candidate.term_type,
                canonical_text=candidate.canonical_text,
                language_code=language_code,
                confidence_score=candidate.confidence_score,
                source="ingestion_auto",
                metadata=metadata,
                synonyms=synonym_values,
            )
            persisted_terms += 1
            persisted_synonyms += len(synonym_values)

        return {
            "enabled": True,
            "term_count": int(persisted_terms),
            "synonym_count": int(persisted_synonyms),
            "language_code": language_code,
        }

    def _rank_and_limit(self, candidates: Mapping[tuple[str, str], _CandidateTerm]) -> list[_CandidateTerm]:
        entity_terms = sorted(
            [item for item in candidates.values() if item.term_type == KnowledgeLexiconTerm.TermType.ENTITY],
            key=lambda item: (-item.confidence_score, item.canonical_text),
        )
        attribute_terms = sorted(
            [item for item in candidates.values() if item.term_type == KnowledgeLexiconTerm.TermType.ATTRIBUTE],
            key=lambda item: (-item.confidence_score, item.canonical_text),
        )
        if self.max_entity_terms > 0:
            entity_terms = entity_terms[: self.max_entity_terms]
        if self.max_attribute_terms > 0:
            attribute_terms = attribute_terms[: self.max_attribute_terms]
        return entity_terms + attribute_terms

    def _collect_document_terms(
        self,
        *,
        candidates: dict[tuple[str, str], _CandidateTerm],
        upload: KnowledgeUpload,
        extraction: Any,
    ) -> None:
        doc_labels = [
            Path(str(upload.display_name or "")).stem,
            Path(str(upload.source_name or "")).stem,
            str(upload.category or ""),
        ]
        for label in doc_labels:
            self._add_candidate(
                candidates=candidates,
                term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                text=label,
                confidence_score=0.52,
                origin="document_label",
            )

        for page in getattr(extraction, "pages", []) or []:
            for block in getattr(page, "blocks", []) or []:
                self._add_candidate(
                    candidates=candidates,
                    term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                    text=getattr(block, "section_heading", ""),
                    confidence_score=0.56,
                    origin="page_heading",
                )
                for heading in getattr(block, "heading_path", []) or []:
                    self._add_candidate(
                        candidates=candidates,
                        term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                        text=heading,
                        confidence_score=0.56,
                        origin="page_heading_path",
                    )

    def _collect_table_terms(
        self,
        *,
        candidates: dict[tuple[str, str], _CandidateTerm],
        extraction: Any,
        structured_summary: Mapping[str, Any] | None,
    ) -> None:
        for table in getattr(extraction, "tables", []) or []:
            self._add_candidate(
                candidates=candidates,
                term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                text=getattr(table, "title", ""),
                confidence_score=0.64,
                origin="table_title",
            )
            self._add_candidate(
                candidates=candidates,
                term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                text=getattr(table, "section_heading", ""),
                confidence_score=0.6,
                origin="table_section_heading",
            )
            table_columns = getattr(table, "column_schema", [])
            if not isinstance(table_columns, Sequence) or isinstance(table_columns, (str, bytes)):
                table_columns = []
            for column in table_columns:
                self._add_candidate(
                    candidates=candidates,
                    term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
                    text=column,
                    confidence_score=0.78,
                    origin="table_column_schema",
                )

        table_summaries = []
        if isinstance(structured_summary, Mapping):
            maybe_tables = structured_summary.get("tables")
            if isinstance(maybe_tables, Sequence) and not isinstance(maybe_tables, (str, bytes)):
                table_summaries = [item for item in maybe_tables if isinstance(item, Mapping)]
        for summary in table_summaries:
            self._add_candidate(
                candidates=candidates,
                term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                text=summary.get("title"),
                confidence_score=0.6,
                origin="structured_table_title",
            )
            summary_columns = summary.get("column_schema", [])
            if not isinstance(summary_columns, Sequence) or isinstance(summary_columns, (str, bytes)):
                summary_columns = []
            for column in summary_columns:
                self._add_candidate(
                    candidates=candidates,
                    term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
                    text=column,
                    confidence_score=0.74,
                    origin="structured_table_column",
                )

    def _collect_entity_terms(
        self,
        *,
        candidates: dict[tuple[str, str], _CandidateTerm],
        extraction: Any,
        entity_payloads: Sequence[Mapping[str, Any]] | None,
    ) -> None:
        raw_entities = entity_payloads
        if raw_entities is None:
            raw_entities = getattr(extraction, "entities", None)
        if not isinstance(raw_entities, Sequence) or isinstance(raw_entities, (str, bytes)):
            return

        for entity in list(raw_entities)[: self.entity_scan_limit]:
            if not isinstance(entity, Mapping):
                continue
            entity_type = self._add_candidate(
                candidates=candidates,
                term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                text=entity.get("entity_type"),
                confidence_score=0.86,
                origin="entity_type",
            )
            entity_name = self._clean_text(entity.get("entity_name"))
            aliases = entity.get("aliases")
            if not isinstance(aliases, Sequence) or isinstance(aliases, (str, bytes)):
                aliases = []
            if entity_type:
                self._add_synonyms(entity_type, [entity_name, *aliases], origin="entity_alias")
            else:
                self._add_candidate(
                    candidates=candidates,
                    term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                    text=entity_name,
                    confidence_score=0.66,
                    origin="entity_name",
                )

            columns = entity.get("columns")
            if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
                columns = []
            for column in columns:
                self._add_candidate(
                    candidates=candidates,
                    term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
                    text=column,
                    confidence_score=0.8,
                    origin="entity_column",
                )
            attributes = entity.get("attributes")
            if isinstance(attributes, Mapping):
                for key in attributes.keys():
                    self._add_candidate(
                        candidates=candidates,
                        term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
                        text=key,
                        confidence_score=0.8,
                        origin="entity_attribute",
                    )

    def _collect_schema_terms(
        self,
        *,
        candidates: dict[tuple[str, str], _CandidateTerm],
        extraction_metadata: Any,
        ingestion_metadata: Mapping[str, Any] | None,
    ) -> None:
        scanned = 0

        def walk(node: Any, parent_key: str = "", depth: int = 0) -> None:
            nonlocal scanned
            if scanned >= self.metadata_scan_limit or depth > 8:
                return

            if isinstance(node, Mapping):
                for key, value in node.items():
                    if scanned >= self.metadata_scan_limit:
                        return
                    scanned += 1
                    key_clean = self._clean_text(key)
                    key_normalized = normalize_lexicon_text(key_clean)
                    if key_normalized in _SCHEMA_OBJECT_KEYS:
                        walk(value, parent_key=key_normalized, depth=depth + 1)
                        continue

                    if key_normalized in _ENTITY_SCHEMA_KEYS:
                        self._add_candidate(
                            candidates=candidates,
                            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
                            text=value if isinstance(value, str) else key_clean,
                            confidence_score=0.64,
                            origin="schema_entity_key",
                        )
                    if key_normalized in _ATTRIBUTE_SCHEMA_KEYS:
                        self._add_candidate(
                            candidates=candidates,
                            term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
                            text=value if isinstance(value, str) else key_clean,
                            confidence_score=0.68,
                            origin="schema_attribute_key",
                        )
                    if key_normalized in _ATTRIBUTE_SCHEMA_CONTAINERS:
                        for item in self._iter_string_values(value):
                            self._add_candidate(
                                candidates=candidates,
                                term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
                                text=item,
                                confidence_score=0.68,
                                origin="schema_attribute_container",
                            )
                    if parent_key == "properties":
                        self._add_candidate(
                            candidates=candidates,
                            term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
                            text=key_clean,
                            confidence_score=0.7,
                            origin="schema_property_key",
                        )
                    walk(value, parent_key=key_normalized or parent_key, depth=depth + 1)
                return

            if isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
                for item in list(node)[:100]:
                    if scanned >= self.metadata_scan_limit:
                        return
                    walk(item, parent_key=parent_key, depth=depth + 1)

        walk(extraction_metadata)
        walk(ingestion_metadata)

    def _add_synonyms(
        self,
        candidate: _CandidateTerm,
        values: Sequence[Any],
        *,
        origin: str,
    ) -> None:
        if self.max_synonyms_per_term <= 0:
            return
        for value in values:
            text = self._clean_text(value)
            normalized = normalize_lexicon_text(text)
            if not text or not normalized:
                continue
            if normalized == candidate.normalized:
                continue
            if not self._is_valid_term(text):
                continue
            candidate.synonyms.add(text)
            candidate.origins.add(origin)
            if len(candidate.synonyms) >= (self.max_synonyms_per_term * 3):
                break

    def _add_candidate(
        self,
        *,
        candidates: dict[tuple[str, str], _CandidateTerm],
        term_type: str,
        text: Any,
        confidence_score: float,
        origin: str,
    ) -> _CandidateTerm | None:
        cleaned = self._clean_text(text)
        if not cleaned or not self._is_valid_term(cleaned):
            return None
        normalized = normalize_lexicon_text(cleaned)
        if not normalized:
            return None
        key = (term_type, normalized)
        candidate = candidates.get(key)
        if candidate is None:
            candidate = _CandidateTerm(
                term_type=term_type,
                canonical_text=cleaned,
                normalized=normalized,
                confidence_score=max(0.0, min(1.0, float(confidence_score))),
            )
            candidates[key] = candidate
        else:
            if float(confidence_score) > candidate.confidence_score:
                candidate.confidence_score = max(0.0, min(1.0, float(confidence_score)))
        candidate.origins.add(origin)
        return candidate

    @staticmethod
    def _clean_text(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text:
            return ""
        has_path_sep = ("/" in text) or ("\\" in text)
        has_filename_suffix = bool(re.search(r"\.[a-zA-Z0-9]{2,5}$", text))
        if has_path_sep or has_filename_suffix:
            text = Path(text).stem
        text = _NON_WORD_RE.sub(" ", text)
        text = _SPACE_RE.sub(" ", text).strip(" :;,.|")
        if len(text) > 255:
            text = text[:255]
        return text

    @staticmethod
    def _iter_string_values(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            output: list[str] = []
            for key, child in value.items():
                if isinstance(child, str):
                    output.append(child)
                elif isinstance(key, str):
                    output.append(key)
            return output
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [item for item in value if isinstance(item, str)]
        return []

    @staticmethod
    def _is_valid_term(value: str) -> bool:
        normalized = normalize_lexicon_text(value)
        if not normalized:
            return False
        if len(normalized) < 2 or len(normalized) > 120:
            return False
        if normalized in _STOP_TERMS:
            return False
        if _COLUMN_PLACEHOLDER_RE.fullmatch(normalized):
            return False
        if _GENERIC_TABLE_RE.fullmatch(normalized):
            return False
        if normalized.isdigit():
            return False
        if not any(char.isalpha() for char in normalized):
            return False
        return True

    @staticmethod
    def _resolve_language_code(
        *,
        upload: KnowledgeUpload,
        extraction_metadata: Any,
        ingestion_metadata: Mapping[str, Any] | None,
    ) -> str:
        candidates: list[str] = []
        candidates.append(str(upload.language or ""))
        if isinstance(extraction_metadata, Mapping):
            candidates.extend(
                [
                    str(extraction_metadata.get("language") or ""),
                    str(extraction_metadata.get("detected_language") or ""),
                    str(extraction_metadata.get("lang") or ""),
                ]
            )
        if isinstance(ingestion_metadata, Mapping):
            candidates.extend(
                [
                    str(ingestion_metadata.get("language") or ""),
                    str(ingestion_metadata.get("detected_language") or ""),
                    str(ingestion_metadata.get("lang") or ""),
                ]
            )
        for candidate in candidates:
            normalized = normalize_language_code(candidate)
            if normalized != "und":
                return normalized
        return "und"
