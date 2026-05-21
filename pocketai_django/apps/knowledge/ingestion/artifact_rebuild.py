from __future__ import annotations

from typing import Any, Mapping

from django.db.models import Prefetch

from apps.accounts.models import KnowledgeSourceType
from apps.knowledge.ingestion.contracts import (
    ExtractionResult,
    IssuePayload,
    KnowledgeIngestionError,
    PageBlockPayload,
    PageLayout,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadFile,
    KnowledgeUploadIssue,
    KnowledgeUploadPage,
    KnowledgeUploadPageBlock,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
    KnowledgeUploadText,
)


class IngestionArtifactRebuildMixin:

    def _build_extraction_from_persisted_artifacts(self, upload: KnowledgeUpload) -> ExtractionResult:
        pages: list[PageLayout] = []
        page_rows = (
            KnowledgeUploadPage.objects.filter(upload=upload)
            .order_by("page_number")
            .prefetch_related(
                Prefetch(
                    "blocks",
                    queryset=KnowledgeUploadPageBlock.objects.order_by("order_index"),
                )
            )
        )
        for page in page_rows:
            block_payloads: list[PageBlockPayload] = []
            for block in page.blocks.all():
                block_payloads.append(
                    PageBlockPayload(
                        block_type=block.block_type,
                        order_index=block.order_index,
                        text=block.text or "",
                        bbox=dict(block.bbox or {}),
                        section_heading=block.section_heading or "",
                        heading_path=list(block.heading_path or []),
                        detected_language=block.detected_language or "",
                        confidence=block.confidence,
                        metadata=dict(block.metadata or {}),
                    )
                )
            pages.append(
                PageLayout(
                    page_number=page.page_number,
                    width=float(page.width or 0.0),
                    height=float(page.height or 0.0),
                    rotation=int(page.rotation or 0),
                    text_density=float(page.text_density or 0.0),
                    has_ocr_content=bool(page.has_ocr_content),
                    content_type=page.content_type or "",
                    blocks=block_payloads,
                    metadata=dict(page.metadata or {}),
                )
            )

        tables: list[TablePayload] = []
        table_rows = (
            KnowledgeUploadTable.objects.filter(upload=upload)
            .order_by("order_index")
            .select_related("page")
            .prefetch_related(
                Prefetch(
                    "rows",
                    queryset=KnowledgeUploadTableRow.objects.order_by("row_index").prefetch_related(
                        Prefetch(
                            "cells",
                            queryset=KnowledgeUploadTableCell.objects.order_by("column_index"),
                        )
                    ),
                )
            )
        )
        for table in table_rows:
            row_payloads: list[TableRowPayload] = []
            for row in table.rows.all():
                cell_payloads: list[TableCellPayload] = []
                for cell in row.cells.all():
                    cell_payloads.append(
                        TableCellPayload(
                            row_index=row.row_index,
                            column_index=cell.column_index,
                            column_key=cell.column_key or "",
                            raw_text=cell.raw_text or "",
                            normalized_value=dict(cell.normalized_value or {}),
                            bbox=dict(cell.bbox or {}),
                            confidence=cell.confidence,
                            metadata=dict(cell.metadata or {}),
                        )
                    )
                row_payloads.append(
                    TableRowPayload(
                        row_index=row.row_index,
                        page_number=row.page_number,
                        bbox=dict(row.bbox or {}),
                        raw_text=row.raw_text or "",
                        metadata=dict(row.metadata or {}),
                        cells=cell_payloads,
                    )
                )
            tables.append(
                TablePayload(
                    order_index=table.order_index,
                    title=table.title or "",
                    section_heading=table.section_heading or "",
                    page_number=table.page.page_number if table.page else None,
                    bbox=dict(table.bbox or {}),
                    column_schema=list(table.column_schema or []),
                    data_dictionary=dict(table.data_dictionary or {}),
                    metadata=dict(table.metadata or {}),
                    rows=row_payloads,
                )
            )

        issues: list[IssuePayload] = []
        issue_rows = (
            KnowledgeUploadIssue.objects.filter(upload=upload)
            .order_by("created_at")
            .select_related("page", "table", "table_row", "table_cell")
        )
        for issue in issue_rows:
            issues.append(
                IssuePayload(
                    code=issue.issue_code,
                    severity=issue.severity,
                    description=issue.description or "",
                    page_number=issue.page.page_number if issue.page else None,
                    table_order_index=issue.table.order_index if issue.table else None,
                    row_index=issue.table_row.row_index if issue.table_row else None,
                    column_index=issue.table_cell.column_index if issue.table_cell else None,
                    details=dict(issue.details or {}),
                )
            )

        text_content = ""
        text_detail = getattr(upload, "text_detail", None)
        if isinstance(text_detail, KnowledgeUploadText):
            text_content = str(text_detail.content or "")
        if not text_content.strip():
            pieces: list[str] = []
            for page in pages:
                for block in page.blocks:
                    cleaned = self._sanitize_text(block.text).strip()
                    if cleaned:
                        pieces.append(cleaned)
            text_content = "\n\n".join(pieces).strip()
        if not text_content.strip():
            raise KnowledgeIngestionError("Persisted artifacts are empty; cannot rebuild extraction.")

        existing_meta = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, Mapping) else {}
        format_hint = str(existing_meta.get("format") or "").strip().lower()
        if not format_hint:
            file_detail = getattr(upload, "file_detail", None)
            if isinstance(file_detail, KnowledgeUploadFile):
                format_hint = self._detect_format(file_detail)
        if not format_hint:
            if upload.source_type == KnowledgeSourceType.TEXT:
                format_hint = "text"
            elif upload.source_type in {KnowledgeSourceType.FILE, KnowledgeSourceType.INTEGRATION}:
                format_hint = "binary"
            elif upload.source_type == KnowledgeSourceType.LINK:
                format_hint = "html"
            else:
                format_hint = "text"

        extraction_metadata: dict[str, Any] = {
            "format": format_hint,
            "content_type": str(existing_meta.get("content_type") or ""),
            "page_count": len(pages),
            "table_count": len(tables),
            "rebuild_source": "persisted_structured_artifacts",
        }
        for key in ("table_extraction", "table_truncation", "table_stats"):
            value = existing_meta.get(key)
            if isinstance(value, Mapping):
                extraction_metadata[key] = dict(value)

        return ExtractionResult(
            text=text_content,
            format_hint=format_hint,
            metadata=extraction_metadata,
            pages=pages,
            tables=tables,
            issues=issues,
            entities=[],
        )

    def reingest_from_persisted_artifacts(self, upload: KnowledgeUpload) -> None:
        extraction = self._build_extraction_from_persisted_artifacts(upload)
        self._persist_extraction(upload, extraction)
