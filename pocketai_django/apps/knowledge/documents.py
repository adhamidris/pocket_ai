from __future__ import annotations

import csv
import io
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.db.models import Prefetch, Q
from core.tenancy import tenant_context

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
)
from apps.knowledge.models import (
    KnowledgeCollection,
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

logger = logging.getLogger(__name__)


class DocumentListValidationError(ValueError):
    """Raised when invalid query parameters are supplied for listing documents."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class DocumentScrapeError(RuntimeError):
    """Raised when a remote document cannot be fetched or parsed."""


class CsvPreviewError(RuntimeError):
    """Raised when we fail to parse a CSV preview (e.g., wrong encoding or malformed data)."""


@dataclass(frozen=True)
class DocumentListItem:
    id: uuid.UUID
    name: str
    status: str
    status_label: str
    source_type: str
    source_label: str
    tags: tuple[str, ...]
    collections: tuple[str, ...]
    language: str
    category: str
    token_count: int
    size_bytes: int
    is_sensitive: bool
    last_ingested_at: datetime | None
    last_synced_at: datetime | None
    updated_at: datetime
    integration_name: str | None
    ingestion_error: str | None


@dataclass(frozen=True)
class DocumentListResult:
    items: Sequence[DocumentListItem]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class DocumentFileMeta:
    filename: str
    content_type: str
    size_bytes: int
    page_count: int


@dataclass(frozen=True)
class DocumentUrlMeta:
    url: str
    host: str


@dataclass(frozen=True)
class DocumentTextMeta:
    characters: int
    preview: str


@dataclass(frozen=True)
class DocumentChunk:
    index: int
    content: str
    token_count: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DocumentPageBlock:
    block_type: str
    order_index: int
    text: str
    bbox: dict[str, Any]
    section_heading: str
    heading_path: tuple[str, ...]
    detected_language: str | None
    confidence: float | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DocumentPageDetail:
    page_number: int
    width: float
    height: float
    rotation: int
    text_density: float
    has_ocr_content: bool
    content_type: str
    blocks: tuple[DocumentPageBlock, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DocumentTableCell:
    column_index: int
    column_key: str
    raw_text: str
    normalized_value: dict[str, Any]
    bbox: dict[str, Any]
    confidence: float | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DocumentTableRow:
    row_index: int
    page_number: int | None
    bbox: dict[str, Any]
    raw_text: str
    metadata: dict[str, Any]
    cells: tuple[DocumentTableCell, ...]


@dataclass(frozen=True)
class DocumentStructuredTable:
    order_index: int
    title: str
    section_heading: str
    page_number: int | None
    column_schema: tuple[str, ...]
    row_count: int
    bbox: dict[str, Any]
    rows: tuple[DocumentTableRow, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DocumentIssue:
    code: str
    severity: str
    description: str
    page_number: int | None
    table_order_index: int | None
    row_index: int | None
    column_index: int | None
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class DocumentDetail:
    summary: DocumentListItem
    collections: tuple["DocumentCollection", ...]
    description: str
    summary_text: str
    metadata: dict
    ingestion_metadata: dict
    retention_policy: dict
    file_detail: DocumentFileMeta | None
    url_detail: DocumentUrlMeta | None
    text_detail: DocumentTextMeta | None
    created_by_agent: str | None
    pages: tuple[DocumentPageDetail, ...]
    tables: tuple[DocumentStructuredTable, ...]
    issues: tuple[DocumentIssue, ...]
    chunks: tuple[DocumentChunk, ...]


@dataclass(frozen=True)
class DocumentCollection:
    id: uuid.UUID
    name: str
    slug: str
    visibility: str


@dataclass(frozen=True)
class ScrapedDocument:
    url: str
    final_url: str
    status_code: int
    content_type: str
    elapsed_ms: int
    content_length: int
    truncated: bool
    text: str
    preview: str
    word_count: int


@dataclass(frozen=True)
class CsvPreview:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    row_count: int
    truncated: bool
    dialect: dict[str, str | None]


def list_documents(
    *,
    business_profile: BusinessProfile,
    q_name: str | None = None,
    collection_slug: str | None = None,
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
        if collection_slug:
            base_qs = base_qs.filter(collections__slug=collection_slug.strip())
        if status:
            base_qs = base_qs.filter(status__iexact=status.strip())
        if source_type:
            base_qs = base_qs.filter(source_type__iexact=source_type.strip())

        total = base_qs.count()
        collections_prefetch = Prefetch(
            "collections",
            queryset=KnowledgeCollection.objects.only("id", "name").order_by("name"),
        )
        rows = (
            base_qs.select_related("integration")
            .prefetch_related(collections_prefetch)
            .order_by("-updated_at")[offset : offset + limit]
        )

        items = []
        for upload in rows:
            tags = tuple(str(tag) for tag in (upload.tags or []))
            collections_manager = getattr(upload, "collections", None)
            if hasattr(collections_manager, "all"):
                collection_iterable = collections_manager.all()
            elif collections_manager is None:
                collection_iterable = ()
            else:
                collection_iterable = collections_manager
            collection_names = tuple(coll.name for coll in collection_iterable)
            items.append(
                DocumentListItem(
                    id=upload.id,
                    name=upload.display_name or "Document",
                    status=upload.status,
                    status_label=(upload.status or "").replace("_", " ").title(),
                    source_type=upload.source_type,
                    source_label=upload.get_source_type_display(),
                    tags=tags,
                    collections=collection_names,
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
            )

        return DocumentListResult(items=tuple(items), total=total, limit=limit, offset=offset)


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
                Prefetch(
                    "collections",
                    queryset=KnowledgeCollection.objects.only("id", "name").order_by("name"),
                ),
                page_prefetch,
                table_prefetch,
                issue_prefetch,
                chunk_prefetch,
            )
            .first()
        )
        if upload is None:
            raise KnowledgeUpload.DoesNotExist

        summary = DocumentListItem(
            id=upload.id,
            name=upload.display_name or "Document",
            status=upload.status,
            status_label=(upload.status or "").replace("_", " ").title(),
            source_type=upload.source_type,
            source_label=upload.get_source_type_display(),
            tags=tuple(str(tag) for tag in (upload.tags or [])),
            collections=tuple(coll.name for coll in upload.collections.all()),
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

        collection_details = tuple(
            DocumentCollection(
                id=collection.id,
                name=collection.name,
                slug=collection.slug,
                visibility=collection.visibility,
            )
            for collection in upload.collections.all()
        )

        return DocumentDetail(
            summary=summary,
            collections=collection_details,
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
        from apps.rag.ai_orchestrator import KnowledgeSearchService

        KnowledgeSearchService.invalidate_result_cache(business_profile.id)
        
        # P0 #3: Invalidate table profile cache on deletions
        from apps.rag.table_profile_cache import invalidate_table_profile_cache
        invalidate_table_profile_cache(business_profile.id)
    except Exception:
        pass
    try:
        from apps.rag.azure_ai_search import AzureAISearchConfig, delete_upload

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


def scrape_document_source(
    *,
    url: str,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
) -> ScrapedDocument:
    """
    Fetch a remote URL and return a text representation, trimming aggressively for speed.
    """

    normalized = url.strip()
    parsed = urlparse(normalized)
    if not parsed.scheme or parsed.scheme not in {"http", "https"}:
        raise DocumentScrapeError("Enter a valid HTTP or HTTPS URL.")

    headers = {
        "User-Agent": "PocketAI-KnowledgeBot/1.0 (+https://pocket.ai)",
        "Accept": "text/html,text/plain,application/json;q=0.8,*/*;q=0.1",
    }
    request = Request(normalized, headers=headers)
    start = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            collected: list[bytes] = []
            total = 0
            truncated = False
            while True:
                if total >= max_bytes:
                    truncated = True
                    break
                chunk_size = min(64 * 1024, max_bytes - total)
                if chunk_size <= 0:
                    truncated = True
                    break
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                collected.append(chunk)
            raw_bytes = b"".join(collected)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            headers_obj = getattr(response, "headers", {})
            content_type = headers_obj.get("content-type", "") if hasattr(headers_obj, "get") else ""
            text = _bytes_to_text(raw_bytes, content_type)
            preview = text[:1200]
            word_count = len(text.split())
            content_length_header = headers_obj.get("content-length") if hasattr(headers_obj, "get") else None
            try:
                content_length = int(content_length_header) if content_length_header is not None else len(raw_bytes)
            except (TypeError, ValueError):
                content_length = len(raw_bytes)
            status_code = getattr(response, "status", None)
            if status_code is None:
                status_code = response.getcode()
            return ScrapedDocument(
                url=normalized,
                final_url=response.geturl(),
                status_code=status_code,
                content_type=content_type,
                elapsed_ms=elapsed_ms,
                content_length=content_length,
                truncated=truncated,
                text=text,
                preview=preview,
                word_count=word_count,
            )
    except HTTPError as exc:
        message = f"HTTP error {exc.code}: {exc.reason}"
        raise DocumentScrapeError(message) from exc
    except URLError as exc:
        raise DocumentScrapeError(f"Unable to fetch the URL: {exc.reason}") from exc


def preview_csv_upload(
    file_obj: BinaryIO,
    *,
    max_rows: int = 50,
    max_bytes: int = 1_000_000,
    encoding: str = "utf-8",
) -> CsvPreview:
    """
    Stream just enough of a CSV upload to preview headers/content without exhausting memory.
    """

    if max_rows < 1:
        raise CsvPreviewError("max_rows must be >= 1")

    buffer = io.BytesIO()
    total = 0
    truncated = False

    for chunk in _iter_chunks(file_obj):
        if not chunk:
            continue
        next_total = total + len(chunk)
        if next_total > max_bytes:
            buffer.write(chunk[: max_bytes - total])
            truncated = True
            break
        buffer.write(chunk)
        total = next_total
        if total >= max_bytes:
            truncated = True
            break

    if hasattr(file_obj, "seek"):
        try:
            file_obj.seek(0)
        except (OSError, io.UnsupportedOperation):
            pass

    try:
        text = buffer.getvalue().decode(encoding, errors="ignore")
    except UnicodeDecodeError as exc:  # pragma: no cover - defensive
        raise CsvPreviewError("Unable to decode CSV using the provided encoding.") from exc

    sample = io.StringIO(text)
    try:
        dialect = csv.Sniffer().sniff(sample.read(2048)) if text else csv.excel
    except csv.Error:
        dialect = csv.excel
    sample.seek(0)

    reader = csv.reader(sample, dialect)
    rows: list[tuple[str, ...]] = []
    columns: tuple[str, ...] = ()

    try:
        columns = tuple(next(reader))
    except StopIteration:
        columns = ()

    for idx, row in enumerate(reader, start=1):
        if idx > max_rows:
            truncated = True
            break
        rows.append(tuple(row))

    return CsvPreview(
        columns=columns,
        rows=tuple(rows),
        row_count=len(rows),
        truncated=truncated,
        dialect={
            "delimiter": getattr(dialect, "delimiter", ","),
            "quotechar": getattr(dialect, "quotechar", '"'),
            "escapechar": getattr(dialect, "escapechar", None),
        },
    )


def _bytes_to_text(raw: bytes, content_type: str) -> str:
    """
    Convert raw bytes into plain text, stripping HTML when necessary.
    """
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="ignore")

    # If HTML, strip script/style tags, then all tags
    if "html" in (content_type or "").lower():
        # strip <script>...</script> and <style>...</style> (case-insensitive, dot matches newline)
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        # strip any remaining HTML tags
        text = re.sub(r"(?s)<[^>]+>", " ", text)

    # collapse any whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text



def _iter_chunks(file_obj: BinaryIO) -> Iterable[bytes]:
    """
    Iterate over chunks for UploadedFile (with .chunks) or a standard file object.
    """

    if hasattr(file_obj, "chunks"):
        yield from file_obj.chunks()  # type: ignore[attr-defined]
        return
    while True:
        chunk = file_obj.read(64 * 1024)
        if not chunk:
            break
        yield chunk
