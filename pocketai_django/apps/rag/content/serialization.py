from __future__ import annotations

import logging
from typing import Any, Mapping

from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
)
from apps.rag.content.issue_text import ContentIssueTextMixin
from apps.rag.content.table_serialization import ContentTableSerializationMixin


logger = logging.getLogger(__name__)


class ContentSerializationMixin(ContentIssueTextMixin, ContentTableSerializationMixin):
    @staticmethod
    def _structured_exports(
        upload: KnowledgeUpload,
        *,
        max_tables: int = 3,
        max_pages: int | None = 50,
    ) -> dict[str, tuple[Mapping[str, object], ...]]:
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

    @classmethod
    def _trim_content(cls, content: str, *, max_chars: int) -> str:
        text, _ = cls._trim_with_flag(content, max_chars=max_chars)
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
