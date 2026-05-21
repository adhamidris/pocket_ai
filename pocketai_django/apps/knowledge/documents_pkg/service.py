from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db.models import Prefetch, Q, TextField
from django.db.models.functions import Cast
from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
)
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadFile,
    KnowledgeUploadIssue,
    KnowledgeUploadPage,
    KnowledgeUploadPageBlock,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
    KnowledgeUploadText,
    KnowledgeUploadUrl,
)
from apps.knowledge.documents_pkg.contracts import (
    CsvPreview,
    CsvPreviewError,
    DocumentChunk,
    DocumentDetail,
    DocumentFileMeta,
    DocumentIssue,
    DocumentListItem,
    DocumentListResult,
    DocumentListValidationError,
    DocumentPageBlock,
    DocumentPageDetail,
    DocumentScrapeError,
    DocumentStructuredTable,
    DocumentTableCell,
    DocumentTableRow,
    DocumentTextMeta,
    DocumentUrlMeta,
    ScrapedDocument,
)
from apps.knowledge.documents_pkg.source_tools import (
    preview_csv_upload,
    scrape_document_source,
)

logger = logging.getLogger(__name__)



def _build_document_list_item(upload: KnowledgeUpload) -> DocumentListItem:
    tags = tuple(str(tag) for tag in (upload.tags or []))
    return DocumentListItem(
        id=upload.id,
        name=upload.display_name or "Document",
        status=upload.status,
        status_label=(upload.status or "").replace("_", " ").title(),
        source_type=upload.source_type,
        source_label=upload.get_source_type_display(),
        tags=tags,
        language=upload.language or "",
        category=upload.category or "",
        token_count=upload.token_count or 0,
        size_bytes=upload.size_bytes or 0,
        is_sensitive=bool(upload.is_sensitive),
        last_ingested_at=upload.last_ingested_at,
        last_synced_at=upload.last_synced_at,
        updated_at=upload.updated_at,
        integration_name=getattr(upload.integration, "name", None),
        ingestion_error=upload.ingestion_error or None,
    )


def list_documents(
    *,
    business_profile: BusinessProfile,
    q_name: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> DocumentListResult:
    """
    Efficiently list knowledge documents scoped to a business with lightweight metadata.
    """

    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise DocumentListValidationError("limit must be an integer", field="limit") from exc
    if limit < 1 or limit > 100:
        raise DocumentListValidationError("limit must be between 1 and 100", field="limit")

    try:
        offset = int(offset)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise DocumentListValidationError("offset must be an integer", field="offset") from exc
    if offset < 0 or offset > 10_000:
        raise DocumentListValidationError("offset must be between 0 and 10000", field="offset")

    with tenant_context(business_profile.id):
        base_qs = KnowledgeUpload.objects.filter(business_profile=business_profile).only(
            "id",
            "display_name",
            "status",
            "source_type",
            "tags",
            "language",
            "category",
            "token_count",
            "size_bytes",
            "is_sensitive",
            "last_ingested_at",
            "last_synced_at",
            "updated_at",
            "ingestion_error",
            "integration__name",
        )
        if q_name:
            search = q_name.strip()
            base_qs = base_qs.annotate(tags_text=Cast("tags", TextField())).filter(
                Q(display_name__icontains=search) |
                Q(description__icontains=search) |
                Q(summary__icontains=search) |
                Q(tags_text__icontains=search)
            )
        if status:
            base_qs = base_qs.filter(status__iexact=status.strip())
        if source_type:
            base_qs = base_qs.filter(source_type__iexact=source_type.strip())

        total = base_qs.count()
        rows = (
            base_qs.select_related("integration")
            .order_by("-updated_at")[offset : offset + limit]
        )

        items = [_build_document_list_item(upload) for upload in rows]

        return DocumentListResult(items=tuple(items), total=total, limit=limit, offset=offset)


def get_document_summary(*, business_profile: BusinessProfile, document_id: uuid.UUID) -> DocumentListItem:
    """
    Load the lightweight summary used by list/status surfaces without the heavy detail payload.
    """

    with tenant_context(business_profile.id):
        upload = (
            KnowledgeUpload.objects.filter(business_profile=business_profile, id=document_id)
            .select_related("integration")
            .only(
                "id",
                "display_name",
                "status",
                "source_type",
                "tags",
                "language",
                "category",
                "token_count",
                "size_bytes",
                "is_sensitive",
                "last_ingested_at",
                "last_synced_at",
                "updated_at",
                "ingestion_error",
                "integration__name",
            )
            .first()
        )
        if upload is None:
            raise KnowledgeUpload.DoesNotExist
        return _build_document_list_item(upload)


def get_document_detail(*, business_profile: BusinessProfile, document_id: uuid.UUID) -> DocumentDetail:
    """
    Load a single knowledge document with rich metadata, optimized for dashboard detail views.
    """

    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    with tenant_context(business_profile.id):
        page_prefetch = Prefetch(
            "pages",
            queryset=KnowledgeUploadPage.objects.order_by("page_number").prefetch_related(
                Prefetch(
                    "blocks",
                    queryset=KnowledgeUploadPageBlock.objects.order_by("order_index"),
                )
            ),
        )
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
            queryset=KnowledgeUploadIssue.objects.order_by("-created_at").select_related("page", "table"),
        )
        chunk_prefetch = Prefetch(
            "chunks",
            queryset=KnowledgeUploadChunk.objects.order_by("chunk_index"),
        )

        upload = (
            KnowledgeUpload.objects.filter(business_profile=business_profile, id=document_id)
            .select_related(
                "integration",
                "file_detail",
                "url_detail",
                "text_detail",
                "created_by_agent",
            )
            .prefetch_related(
                page_prefetch,
                table_prefetch,
                issue_prefetch,
                chunk_prefetch,
            )
            .first()
        )
        if upload is None:
            raise KnowledgeUpload.DoesNotExist

        summary = _build_document_list_item(upload)

        file_meta = None
        file_detail = getattr(upload, "file_detail", None)
        if file_detail:
            file_meta = DocumentFileMeta(
                filename=file_detail.filename,
                content_type=file_detail.content_type or "",
                size_bytes=file_detail.size_bytes or upload.size_bytes or 0,
                page_count=file_detail.page_count or 0,
            )

        url_meta = None
        url_detail = getattr(upload, "url_detail", None)
        if url_detail:
            url_meta = DocumentUrlMeta(
                url=url_detail.url,
                host=url_detail.normalized_host or urlparse(url_detail.url).netloc,
            )

        text_meta = None
        text_detail = getattr(upload, "text_detail", None)
        if text_detail:
            content = text_detail.content or ""
            preview = content.strip()[:400]
            text_meta = DocumentTextMeta(
                characters=len(content),
                preview=preview,
            )

        created_by_agent = None
        if isinstance(upload.created_by_agent, AgentProfile):
            created_by_agent = upload.created_by_agent.name

        page_details: list[DocumentPageDetail] = []
        pages_manager = getattr(upload, "pages", None)
        if hasattr(pages_manager, "all"):
            for page in pages_manager.all():
                blocks = []
                blocks_manager = getattr(page, "blocks", None)
                block_iterable = blocks_manager.all() if hasattr(blocks_manager, "all") else ()
                for block in block_iterable:
                    blocks.append(
                        DocumentPageBlock(
                            block_type=block.block_type,
                            order_index=block.order_index,
                            text=block.text or "",
                            bbox=_as_dict(block.bbox),
                            section_heading=block.section_heading or "",
                            heading_path=tuple(str(item) for item in _as_list(block.heading_path)),
                            detected_language=block.detected_language or None,
                            confidence=block.confidence,
                            metadata=_as_dict(block.metadata),
                        )
                    )
                page_details.append(
                    DocumentPageDetail(
                        page_number=page.page_number,
                        width=page.width,
                        height=page.height,
                        rotation=page.rotation,
                        text_density=page.text_density,
                        has_ocr_content=page.has_ocr_content,
                        content_type=page.content_type or "",
                        blocks=tuple(blocks),
                        metadata=_as_dict(page.metadata),
                    )
                )

        table_details: list[DocumentStructuredTable] = []
        tables_manager = getattr(upload, "tables", None)
        if hasattr(tables_manager, "all"):
            for table in tables_manager.all():
                rows_payload: list[DocumentTableRow] = []
                rows_iterable = table.rows.all() if hasattr(table, "rows") else ()
                rows_list = list(rows_iterable)
                for row in rows_list:
                    cells_iterable = row.cells.all() if hasattr(row, "cells") else ()
                    cells_payload = [
                        DocumentTableCell(
                            column_index=cell.column_index,
                            column_key=cell.column_key or "",
                            raw_text=cell.raw_text or "",
                            normalized_value=_as_dict(cell.normalized_value),
                            bbox=_as_dict(cell.bbox),
                            confidence=cell.confidence,
                            metadata=_as_dict(cell.metadata),
                        )
                        for cell in cells_iterable
                    ]
                    rows_payload.append(
                        DocumentTableRow(
                            row_index=row.row_index,
                            page_number=row.page_number,
                            bbox=_as_dict(row.bbox),
                            raw_text=row.raw_text or "",
                            metadata=_as_dict(row.metadata),
                            cells=tuple(cells_payload),
                        )
                    )
                table_details.append(
                    DocumentStructuredTable(
                        order_index=table.order_index,
                        title=table.title or f"Table {table.order_index}",
                        section_heading=table.section_heading or "",
                        page_number=table.page.page_number if table.page else None,
                        column_schema=tuple(table.column_schema or []),
                        row_count=len(rows_payload),
                        bbox=_as_dict(table.bbox),
                        rows=tuple(rows_payload),
                        metadata=_as_dict(table.metadata),
                    )
                )

        issue_details: list[DocumentIssue] = []
        issues_manager = getattr(upload, "issues", None)
        if hasattr(issues_manager, "all"):
            for issue in issues_manager.all():
                table_order_index = getattr(issue.table, "order_index", None)
                issue_details.append(
                    DocumentIssue(
                        code=issue.issue_code,
                        severity=issue.severity,
                        description=issue.description or "",
                        page_number=issue.page.page_number if issue.page else None,
                        table_order_index=table_order_index,
                        row_index=issue.table_row.row_index if issue.table_row else None,
                        column_index=issue.table_cell.column_index if issue.table_cell else None,
                        details=_as_dict(issue.details),
                        created_at=issue.created_at,
                    )
                )

        chunk_details: list[DocumentChunk] = []
        chunks_manager = getattr(upload, "chunks", None)
        if hasattr(chunks_manager, "all"):
            for chunk in chunks_manager.all():
                chunk_details.append(
                    DocumentChunk(
                        index=chunk.chunk_index,
                        content=chunk.content or "",
                        token_count=chunk.token_count or 0,
                        metadata=_as_dict(chunk.metadata),
                    )
                )

        return DocumentDetail(
            summary=summary,
            description=upload.description or "",
            summary_text=upload.summary or "",
            metadata=upload.metadata or {},
            ingestion_metadata=upload.ingestion_metadata or {},
            retention_policy=upload.retention_policy or {},
            file_detail=file_meta,
            url_detail=url_meta,
            text_detail=text_meta,
            created_by_agent=created_by_agent,
            pages=tuple(page_details),
            tables=tuple(table_details),
            issues=tuple(issue_details),
            chunks=tuple(chunk_details),
        )


def delete_document(*, business_profile: BusinessProfile, document_id: uuid.UUID) -> None:
    """
    Delete a knowledge upload and any stored artifacts on disk.
    """

    chunk_count = 0
    with tenant_context(business_profile.id):
        upload = (
            KnowledgeUpload.objects.filter(business_profile=business_profile, id=document_id)
            .select_related("file_detail")
            .first()
        )
        if upload is None:
            raise KnowledgeUpload.DoesNotExist

        chunk_count = int(getattr(upload, "chunk_count", 0) or 0)
        storage_path = ""
        file_detail = getattr(upload, "file_detail", None)
        if file_detail and file_detail.storage_path:
            storage_path = file_detail.storage_path

        upload.delete()
        logger.info("knowledge_document_delete business=%s document=%s", business_profile.id, document_id)

    try:
        from apps.rag.knowledge_search import KnowledgeSearchService

        KnowledgeSearchService.invalidate_result_cache(business_profile.id)
        
        # P0 #3: Invalidate table profile cache on deletions
        from apps.rag.tables.profile_cache import invalidate_table_profile_cache
        invalidate_table_profile_cache(business_profile.id)
    except Exception:
        pass
    try:
        from apps.rag.integrations.azure_ai_search import AzureAISearchConfig, delete_upload

        config = AzureAISearchConfig.from_settings()
        if config and chunk_count:
            delete_upload(config=config, upload_id=document_id, chunk_count=chunk_count)
    except Exception as exc:
        logger.warning(
            "azure_search.delete_failed business=%s document=%s error=%s",
            business_profile.id,
            document_id,
            str(exc)[:250],
        )

    media_root = getattr(settings, "MEDIA_ROOT", "")
    if not media_root:
        return

    root = None
    try:
        root = Path(media_root).resolve()
    except OSError:
        return

    dataset_dir = (root / "datasets" / str(business_profile.id) / str(document_id)).resolve()
    try:
        dataset_dir.relative_to(root)
    except (OSError, ValueError):
        dataset_dir = None
    if dataset_dir and dataset_dir.exists():
        try:
            shutil.rmtree(dataset_dir)
        except OSError as exc:
            logger.warning(
                "knowledge_document_dataset_delete_failed business=%s document=%s error=%s",
                business_profile.id,
                document_id,
                exc,
            )

    if not storage_path:
        return

    try:
        candidate = (root / Path(storage_path)).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        logger.warning(
            "knowledge_document_delete_invalid_path business=%s document=%s storage_path=%s",
            business_profile.id,
            document_id,
            storage_path,
        )
        return

    try:
        if candidate.exists():
            candidate.unlink()
    except OSError as exc:
        logger.warning(
            "knowledge_document_file_delete_failed business=%s document=%s error=%s",
            business_profile.id,
            document_id,
            exc,
        )
