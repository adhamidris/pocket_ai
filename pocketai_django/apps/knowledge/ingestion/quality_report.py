from __future__ import annotations

from typing import Any, Mapping, Sequence

from django.utils import timezone

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import ExtractionResult
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadChunk


class IngestionQualityReportMixin:

    def _build_quality_report(
        self,
        *,
        upload: KnowledgeUpload,
        extraction: ExtractionResult,
        chunk_count: int,
        chunk_objects: Sequence[KnowledgeUploadChunk],
        structured_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {}

        def _pct(part: int, whole: int) -> float:
            if whole <= 0:
                return 0.0
            return round((float(part) / float(whole)) * 100.0, 2)

        report["generated_at"] = timezone.now().isoformat()
        report["chunk_count"] = int(chunk_count)

        table_chunk_count = 0
        entity_chunk_count = 0
        for chunk in chunk_objects:
            meta = chunk.metadata if isinstance(chunk.metadata, Mapping) else {}
            if meta.get("is_table_chunk"):
                table_chunk_count += 1
            if meta.get("index_type") == "entity":
                entity_chunk_count += 1
        text_chunk_count = max(0, chunk_count - table_chunk_count - entity_chunk_count)
        report["chunk_breakdown"] = {
            "text": text_chunk_count,
            "table": table_chunk_count,
            "entity": entity_chunk_count,
        }

        short_chunk_count = 0
        heading_only_count = 0
        low_quality_count = 0
        duplicate_count = 0
        seen_fingerprints: set[str] = set()
        for chunk in chunk_objects:
            meta = chunk.metadata if isinstance(chunk.metadata, Mapping) else {}
            if not self._segment_is_text(meta):
                continue
            try:
                token_count = int(meta.get("chunk_quality_tokens") or chunk.token_count or 0)
            except (TypeError, ValueError):
                token_count = int(chunk.token_count or 0)
            if token_count < self.chunk_quality_min_tokens:
                short_chunk_count += 1
            if meta.get("chunk_heading_only"):
                heading_only_count += 1
            try:
                score = float(meta.get("chunk_quality_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score < self.chunk_quality_low_score:
                low_quality_count += 1
            fingerprint = self._chunk_fingerprint(chunk.content or "")
            if fingerprint:
                if fingerprint in seen_fingerprints:
                    duplicate_count += 1
                else:
                    seen_fingerprints.add(fingerprint)

        report["chunk_quality"] = {
            "short_chunk_count": short_chunk_count,
            "short_chunk_pct": _pct(short_chunk_count, text_chunk_count),
            "heading_only_count": heading_only_count,
            "heading_only_pct": _pct(heading_only_count, text_chunk_count),
            "low_quality_count": low_quality_count,
            "low_quality_pct": _pct(low_quality_count, text_chunk_count),
            "duplicate_count": duplicate_count,
            "duplicate_pct": _pct(duplicate_count, text_chunk_count),
            "min_tokens": self.chunk_quality_min_tokens,
            "min_unique_ratio": self.chunk_quality_min_unique_ratio,
            "low_score_threshold": self.chunk_quality_low_score,
        }

        pages = extraction.pages or []
        report["page_count"] = len(pages)
        total_blocks = 0
        decorative_blocks = 0
        decorative_fragments_filtered = 0
        for page in pages:
            blocks = page.blocks or []
            total_blocks += len(blocks)
            page_meta = page.metadata if isinstance(page.metadata, Mapping) else {}
            decorative_fragments_filtered += int(page_meta.get("decorative_fragments_filtered") or 0)
            for block in blocks:
                block_meta = block.metadata if isinstance(block.metadata, Mapping) else {}
                if block_meta.get("is_decorative") or block_meta.get("region_role") == "decorative":
                    decorative_blocks += 1
        report["block_count"] = total_blocks
        report["decorative_block_count"] = decorative_blocks
        report["decorative_block_pct"] = _pct(decorative_blocks, total_blocks)
        if decorative_fragments_filtered:
            report["decorative_fragments_filtered"] = decorative_fragments_filtered

        table_summary = None
        if structured_summary and isinstance(structured_summary, Mapping):
            table_summary = structured_summary.get("tables")
        if isinstance(table_summary, list):
            table_count = len(table_summary)
            decorative_table_count = sum(1 for entry in table_summary if entry.get("is_decorative"))
        else:
            table_count = len(extraction.tables or [])
            decorative_table_count = 0
        report["table_count"] = table_count
        report["decorative_table_count"] = decorative_table_count
        report["decorative_table_pct"] = _pct(decorative_table_count, table_count)

        issues = extraction.issues or []
        report["issue_count"] = len(issues)
        severity_counts = {
            KnowledgeIssueSeverity.ERROR.value: 0,
            KnowledgeIssueSeverity.WARNING.value: 0,
            KnowledgeIssueSeverity.INFO.value: 0,
        }
        error_codes: set[str] = set()
        for issue in issues:
            severity = str(issue.severity or "")
            if severity in severity_counts:
                severity_counts[severity] += 1
            else:
                severity_counts[KnowledgeIssueSeverity.INFO.value] += 1
            if severity == KnowledgeIssueSeverity.ERROR.value and issue.code:
                error_codes.add(str(issue.code))
        report["issue_severity"] = severity_counts
        if error_codes:
            report["extraction_error_codes"] = sorted(error_codes)[:12]

        report["upload_id"] = str(upload.id)
        report["business_profile_id"] = str(upload.business_profile_id)
        return report
