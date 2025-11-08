from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import (
    KnowledgeIngestionJob,
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeIssueSeverity,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadFile,
    KnowledgeUploadText,
    KnowledgeUploadIssue,
    KnowledgeUploadPage,
    KnowledgeUploadPageBlock,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
    KnowledgeBlockType,
)
from apps.services.documents import DocumentScrapeError, scrape_document_source
from apps.services.embeddings import build_embedding_service, EmbeddingProviderError

logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import fitz  # type: ignore[attr-defined]  # PyMuPDF
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - fallback handled via runtime check
    PdfReader = None  # type: ignore

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from docx import Document as DocxDocument
except ImportError:  # pragma: no cover - fallback handled via runtime check
    DocxDocument = None  # type: ignore


SUPPORTED_SOURCE_TYPES = {
    KnowledgeSourceType.FILE,
    KnowledgeSourceType.LINK,
}


class KnowledgeIngestionError(RuntimeError):
    """Base error for ingestion failures."""


class UnsupportedFormatError(KnowledgeIngestionError):
    """Raised when we cannot determine how to parse a file."""


@dataclass(frozen=True)
class PageBlockPayload:
    block_type: str
    order_index: int
    text: str
    bbox: dict[str, Any] = field(default_factory=dict)
    section_heading: str = ""
    heading_path: list[str] = field(default_factory=list)
    detected_language: str = ""
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PageLayout:
    page_number: int
    width: float
    height: float
    rotation: int
    text_density: float
    has_ocr_content: bool
    content_type: str
    blocks: list[PageBlockPayload] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableCellPayload:
    row_index: int
    column_index: int
    column_key: str
    raw_text: str
    normalized_value: dict[str, Any] = field(default_factory=dict)
    bbox: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableRowPayload:
    row_index: int
    page_number: int | None
    bbox: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    cells: list[TableCellPayload] = field(default_factory=list)


@dataclass(frozen=True)
class TablePayload:
    order_index: int
    title: str
    section_heading: str
    page_number: int | None
    bbox: dict[str, Any] = field(default_factory=dict)
    column_schema: list[str] = field(default_factory=list)
    data_dictionary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    rows: list[TableRowPayload] = field(default_factory=list)


@dataclass(frozen=True)
class IssuePayload:
    code: str
    severity: str
    description: str
    page_number: int | None = None
    table_order_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PageRendererResult:
    text: str
    pages: list[PageLayout] = field(default_factory=list)
    issues: list[IssuePayload] = field(default_factory=list)


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    format_hint: str
    metadata: dict[str, Any]
    pages: list[PageLayout] = field(default_factory=list)
    tables: list[TablePayload] = field(default_factory=list)
    issues: list[IssuePayload] = field(default_factory=list)


class OCRReconciler:
    """
    Lightweight OCR orchestrator that flags low-density pages and optionally runs OCR.
    """

    def __init__(
        self,
        *,
        density_threshold: float = 0.00015,
        ocr_callable: Callable[[bytes], str] | None = None,
    ):
        self.density_threshold = density_threshold
        self.ocr_callable = ocr_callable

    def reconcile_pdf_page(
        self,
        page: Any,
        *,
        page_number: int,
        extracted_text: str,
        text_density: float,
    ) -> tuple[str, bool, list[IssuePayload]]:
        if text_density >= self.density_threshold:
            return extracted_text, False, []

        issues: list[IssuePayload] = []
        if self.ocr_callable is None:
            issues.append(
                IssuePayload(
                    code="ocr_required",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description="Page text density is low but OCR is not configured.",
                    page_number=page_number,
                    details={"text_density": text_density},
                )
            )
            return extracted_text, False, issues

        try:
            pixmap = page.get_pixmap()  # type: ignore[attr-defined]
            image_bytes = pixmap.tobytes("png")
            ocr_text = self.ocr_callable(image_bytes)
            if not ocr_text:
                issues.append(
                    IssuePayload(
                        code="ocr_empty",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description="OCR returned no text for low-density page.",
                        page_number=page_number,
                    )
                )
                return extracted_text, False, issues
            return ocr_text, True, []
        except Exception as exc:  # pragma: no cover - best effort
            issues.append(
                IssuePayload(
                    code="ocr_failed",
                    severity=KnowledgeIssueSeverity.ERROR.value,
                    description=f"OCR failed for page {page_number}: {exc}",
                    page_number=page_number,
                )
            )
            return extracted_text, False, issues


class PageRenderer:
    """
    Produces layout-aware payloads using PyMuPDF when available.
    Fallbacks collapse documents into a single page with coarse metadata so downstream
    persistence can still operate.
    """

    def __init__(self, *, pymupdf_module: Any | None = None):
        self._fitz = pymupdf_module

    def render(self, path: Path, *, format_hint: str, ocr: OCRReconciler | None = None) -> PageRendererResult:
        if format_hint == "pdf" and self._fitz is not None:
            return self._render_pdf(path, ocr=ocr)
        if format_hint == "docx":
            return self._render_docx(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / (612 * 792),
            has_ocr_content=False,
            content_type="text/plain",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=text,
                )
            ],
        )
        return PageRendererResult(text=text, pages=[page])

    def _render_pdf(self, path: Path, *, ocr: OCRReconciler | None = None) -> PageRendererResult:
        try:
            document = self._fitz.open(path)  # type: ignore[call-arg]
        except Exception as exc:  # pragma: no cover - dependency-specific
            raise KnowledgeIngestionError(f"Unable to open PDF for layout parsing: {exc}") from exc

        pages: list[PageLayout] = []
        fragments: list[str] = []
        issues: list[IssuePayload] = []
        for index, page in enumerate(document, start=1):
            plain_text = page.get_text("text") or ""
            char_count = len(plain_text.strip())
            rect = page.rect
            area = max(rect.width * rect.height, 1.0)
            density = char_count / area
            has_ocr = False
            reconciled_text = plain_text
            if ocr:
                reconciled_text, has_ocr, ocr_issues = ocr.reconcile_pdf_page(
                    page,
                    page_number=index,
                    extracted_text=plain_text,
                    text_density=density,
                )
                issues.extend(ocr_issues)
            fragments.append(reconciled_text)
            blocks = self._build_pdf_blocks(page, index)
            pages.append(
                PageLayout(
                    page_number=index,
                    width=float(rect.width),
                    height=float(rect.height),
                    rotation=int(page.rotation or 0),
                    text_density=density,
                    has_ocr_content=has_ocr,
                    content_type="application/pdf",
                    blocks=blocks,
                    metadata={"char_count": char_count},
                )
            )
        return PageRendererResult(text="\n".join(fragments), pages=pages, issues=issues)

    def _render_docx(self, path: Path) -> PageRendererResult:
        if DocxDocument is None:
            raise KnowledgeIngestionError("DOCX ingestion requires the python-docx package.")
        try:
            document = DocxDocument(str(path))
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open DOCX for layout parsing: {exc}") from exc

        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        text = "\n".join(paragraphs)
        blocks: list[PageBlockPayload] = []
        heading_context: list[str] = []
        for idx, paragraph in enumerate(paragraphs):
            stripped = paragraph.strip()
            block_type = KnowledgeBlockType.PARAGRAPH
            if self._looks_like_heading(stripped):
                block_type = KnowledgeBlockType.HEADING
                heading_context = [stripped]
                section_heading = stripped
            else:
                section_heading = heading_context[-1] if heading_context else ""
            blocks.append(
                PageBlockPayload(
                    block_type=block_type,
                    order_index=idx,
                    text=paragraph,
                    section_heading=section_heading,
                    heading_path=list(heading_context),
                )
            )
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / (612 * 792),
            has_ocr_content=False,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            blocks=blocks,
            metadata={"paragraph_count": len(paragraphs)},
        )
        return PageRendererResult(text=text, pages=[page])

    def _build_pdf_blocks(self, page: Any, page_number: int) -> list[PageBlockPayload]:
        try:
            raw_blocks = page.get_text("blocks") or []  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - fallback to full page block
            raw_blocks = []
        payloads: list[PageBlockPayload] = []
        heading_context: list[str] = []
        for order_index, block in enumerate(raw_blocks):
            text_fragment = block[4] if len(block) > 4 else ""
            bbox = {
                "x0": float(block[0]) if len(block) > 0 else 0.0,
                "y0": float(block[1]) if len(block) > 1 else 0.0,
                "x1": float(block[2]) if len(block) > 2 else 0.0,
                "y1": float(block[3]) if len(block) > 3 else 0.0,
            }
            stripped = text_fragment.strip()
            block_type = self._resolve_block_type(block, stripped)
            if self._looks_like_heading(stripped):
                heading_context = [stripped]
                section_heading = stripped
            else:
                section_heading = heading_context[-1] if heading_context else ""
            payloads.append(
                PageBlockPayload(
                    block_type=block_type,
                    order_index=order_index,
                    text=text_fragment,
                    bbox=bbox,
                    section_heading=section_heading,
                    heading_path=list(heading_context),
                    detected_language="",
                    confidence=None,
                )
            )
        if not payloads:
            payloads.append(
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=page.get_text("text") or "",
                    metadata={"fallback": True},
                )
            )
        return payloads

    @staticmethod
    def _resolve_block_type(block: Any, text_fragment: str) -> str:
        if not text_fragment.strip():
            return KnowledgeBlockType.IMAGE
        if "|" in text_fragment or "\t" in text_fragment:
            return KnowledgeBlockType.TABLE
        return KnowledgeBlockType.HEADING if text_fragment.isupper() and len(text_fragment) < 80 else KnowledgeBlockType.PARAGRAPH

    @staticmethod
    def _looks_like_heading(content: str) -> bool:
        if not content:
            return False
        stripped = content.strip()
        if len(stripped) > 80:
            return False
        if stripped.endswith(":"):
            return True
        uppercase_ratio = sum(1 for c in stripped if c.isupper()) / max(len(stripped), 1)
        return uppercase_ratio > 0.6


class TableDetector:
    """
    Heuristic table extraction that promotes structured storage even when advanced
    detectors (Camelot, pdfplumber, etc.) are not available. Designed to be easily
    swapped with more capable detectors later.
    """

    def detect_tables(
        self,
        pages: list[PageLayout],
        *,
        format_hint: str | None = None,
    ) -> tuple[list[TablePayload], list[IssuePayload]]:
        tables: list[TablePayload] = []
        issues: list[IssuePayload] = []
        order_index = 0
        for page in pages:
            for block in page.blocks:
                if self._looks_like_table_block(block):
                    order_index += 1
                    table_payload, block_issues = self._build_table_from_block(
                        block,
                        page_number=page.page_number,
                        order_index=order_index,
                        section_heading=block.section_heading,
                    )
                    tables.append(table_payload)
                    issues.extend(block_issues)
        return tables, issues

    def _looks_like_table_block(self, block: PageBlockPayload) -> bool:
        text = block.text or ""
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        if block.block_type == KnowledgeBlockType.TABLE:
            return True
        sample = lines[0]
        return ("|" in sample) or ("\t" in sample) or bool(re.search(r"\s{2,}", sample))

    def _build_table_from_block(
        self,
        block: PageBlockPayload,
        *,
        page_number: int,
        order_index: int,
        section_heading: str,
    ) -> tuple[TablePayload, list[IssuePayload]]:
        lines = [line for line in (block.text or "").splitlines() if line.strip()]
        delimiter = self._detect_delimiter(lines[0])
        rows = [self._split_row(line, delimiter) for line in lines]
        header = rows[0] if rows else []
        issues: list[IssuePayload] = []
        if not header:
            issues.append(
                IssuePayload(
                    code="table_missing_header",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Table block did not contain a header row.",
                    page_number=page_number,
                    table_order_index=order_index,
                )
            )
        column_schema = [self._normalize_header_cell(cell, idx) for idx, cell in enumerate(header)]
        expected_columns = len(column_schema) or len(rows[1]) if len(rows) > 1 else 0
        table_rows: list[TableRowPayload] = []
        for idx, row_cells in enumerate(rows):
            row_index = idx
            normalized_cells: list[TableCellPayload] = []
            if expected_columns and len(row_cells) != expected_columns:
                issues.append(
                    IssuePayload(
                        code="table_column_mismatch",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description="Row column count mismatch.",
                        page_number=page_number,
                        table_order_index=order_index,
                        row_index=row_index,
                        details={
                            "expected": expected_columns,
                            "observed": len(row_cells),
                        },
                    )
                )
            for col_idx, cell_text in enumerate(row_cells):
                column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                normalized_value = self._normalize_cell_value(cell_text)
                normalized_cells.append(
                    TableCellPayload(
                        row_index=row_index,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=cell_text,
                        normalized_value=normalized_value,
                        bbox=block.bbox,
                        confidence=None,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_index,
                    page_number=page_number,
                    bbox=block.bbox,
                    raw_text=" | ".join(row_cells),
                    metadata={"row_type": "header" if idx == 0 else "data"},
                    cells=normalized_cells,
                )
            )
        table_payload = TablePayload(
            order_index=order_index,
            title=section_heading or f"Table {order_index}",
            section_heading=section_heading,
            page_number=page_number,
            bbox=block.bbox,
            column_schema=column_schema,
            data_dictionary={},
            metadata={"detected_via": "heuristic"},
            rows=table_rows,
        )
        return table_payload, issues

    @staticmethod
    def _detect_delimiter(sample: str) -> str:
        if "|" in sample:
            return "|"
        if "\t" in sample:
            return "\t"
        return "  "

    @staticmethod
    def _split_row(line: str, delimiter: str) -> list[str]:
        if delimiter == "  ":
            return [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
        return [cell.strip() for cell in line.split(delimiter)]

    @staticmethod
    def _normalize_header_cell(cell: str, index: int) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", cell.strip().lower()).strip("_")
        return normalized or f"column_{index+1}"

    @staticmethod
    def _normalize_cell_value(cell: str) -> dict[str, Any]:
        text = cell.strip()
        if not text:
            return {}
        numeric = re.sub(r"[,$%]", "", text)
        try:
            value = float(numeric)
            result = {"number": value}
            if "$" in text:
                result["currency"] = "USD"
            if text.endswith("%"):
                result["unit"] = "percent"
            return result
        except ValueError:
            return {}
@dataclass(frozen=True)
class IngestionJobResult:
    job_id: uuid.UUID
    upload_id: uuid.UUID
    status: KnowledgeIngestionJobStatus
    characters: int
    error: str | None = None


def queue_ingestion_job(upload: KnowledgeUpload, *, trigger: str = "upload", force: bool = False) -> KnowledgeIngestionJob | None:
    """
    Ensure an ingestion job exists for the upload if the source type requires parsing.
    """

    if upload.source_type not in SUPPORTED_SOURCE_TYPES:
        return None

    existing = KnowledgeIngestionJob.objects.filter(
        upload=upload,
        status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.RUNNING),
        job_type=KnowledgeIngestionJobType.INGEST,
    ).first()
    if existing:
        if not force:
            logger.info("Ingestion job already queued upload=%s job=%s", upload.id, existing.id)
            return existing
        KnowledgeIngestionJob.objects.filter(id=existing.id).update(status=KnowledgeIngestionJobStatus.CANCELLED)
        logger.info("Cancelled stale ingestion job upload=%s job=%s", upload.id, existing.id)

    if upload.status != KnowledgeStatus.PROCESSING:
        upload.status = KnowledgeStatus.PROCESSING
        upload.save(update_fields=["status", "updated_at"])

    job = KnowledgeIngestionJob.objects.create(
        business_profile=upload.business_profile,
        upload=upload,
        job_type=KnowledgeIngestionJobType.INGEST,
        status=KnowledgeIngestionJobStatus.QUEUED,
        payload={"trigger": trigger},
    )

    logger.info("Queued ingestion job upload=%s job=%s trigger=%s", upload.id, job.id, trigger)
    return job


class KnowledgeIngestionService:
    """
    Pulled-text ingestion pipeline for PDF/DOCX/TXT uploads and external links.

    Designed to run inside a management command or async worker. Fetches queued jobs,
    extracts text, and persists normalized content so the orchestrator and dashboard
    can serve full document context.
    """

    def __init__(self, *, media_root: Path | None = None):
        root = media_root or getattr(settings, "MEDIA_ROOT", None)
        if not root:
            raise RuntimeError("MEDIA_ROOT must be configured for ingestion.")
        self.media_root = Path(root).resolve()
        self.embedding_service = build_embedding_service()
        self.ocr_reconciler = OCRReconciler()
        self.page_renderer = PageRenderer(pymupdf_module=fitz)
        self.table_detector = TableDetector()

    # ------------------------------------------------------------------
    # Job coordination

    def process_next_job(self) -> IngestionJobResult | None:
        job = self._claim_next_job()
        if job is None:
            return None

        upload = job.upload
        try:
            extraction = self._extract_upload(upload)
            characters = len(extraction.text)
            self._persist_extraction(upload, extraction)
            self._mark_job_completed(job, extra={"characters": characters, "format": extraction.format_hint})
            logger.info("Ingested knowledge upload=%s job=%s chars=%s", upload.id, job.id, characters)
            return IngestionJobResult(
                job_id=job.id,
                upload_id=upload.id,
                status=KnowledgeIngestionJobStatus.COMPLETED,
                characters=characters,
            )
        except KnowledgeIngestionError as exc:
            self._handle_failure(job, str(exc))
            logger.warning("Ingestion failed upload=%s job=%s error=%s", upload.id, job.id, exc)
            return IngestionJobResult(
                job_id=job.id,
                upload_id=upload.id,
                status=KnowledgeIngestionJobStatus.FAILED,
                characters=0,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Extraction path

    def _extract_upload(self, upload: KnowledgeUpload) -> ExtractionResult:
        if upload.source_type == KnowledgeSourceType.FILE:
            file_detail = getattr(upload, "file_detail", None)
            if not isinstance(file_detail, KnowledgeUploadFile):
                upload = KnowledgeUpload.objects.select_related("file_detail").get(id=upload.id)
                file_detail = upload.file_detail
            if file_detail is None:
                raise KnowledgeIngestionError("File metadata missing for upload.")
            return self._extract_from_file(file_detail)

        if upload.source_type == KnowledgeSourceType.LINK:
            url_detail = getattr(upload, "url_detail", None)
            url = getattr(url_detail, "url", None) or upload.legacy_url
            if not url:
                raise KnowledgeIngestionError("Link upload missing URL.")
            return self._extract_from_link(url)

        raise KnowledgeIngestionError(f"Ingestion not implemented for {upload.source_type}.")

    def _extract_from_file(self, file_detail: KnowledgeUploadFile) -> ExtractionResult:
        storage_path = Path(file_detail.storage_path)
        absolute = (self.media_root / storage_path).resolve()
        try:
            absolute.relative_to(self.media_root)
        except ValueError as exc:  # pragma: no cover - defensive
            raise KnowledgeIngestionError("File path escapes MEDIA_ROOT.") from exc
        if not absolute.exists():
            raise KnowledgeIngestionError("File not found on disk.")

        format_hint = self._detect_format(file_detail)
        if not format_hint:
            raise UnsupportedFormatError(f"Unsupported file type {format_hint or 'unknown'}.")

        layout_result: PageRendererResult | None = None
        should_attempt_layout = not (format_hint == "pdf" and fitz is None)
        if should_attempt_layout:
            try:
                layout_result = self.page_renderer.render(
                    absolute,
                    format_hint=format_hint,
                    ocr=self.ocr_reconciler,
                )
            except KnowledgeIngestionError as exc:
                logger.warning("Layout extraction failed for %s: %s", absolute, exc)

        if layout_result is None:
            text = self._fallback_text_extraction(absolute, format_hint)
            layout_result = PageRendererResult(
                text=text,
                pages=[
                    PageLayout(
                        page_number=1,
                        width=612,
                        height=792,
                        rotation=0,
                        text_density=len(text.strip()) / (612 * 792),
                        has_ocr_content=False,
                        content_type=f"application/{format_hint}",
                        blocks=[
                            PageBlockPayload(
                                block_type=KnowledgeBlockType.PARAGRAPH,
                                order_index=0,
                                text=text,
                            )
                        ],
                        metadata={"fallback": True},
                    )
                ],
            )
        text = layout_result.text

        tables, table_issues = self.table_detector.detect_tables(
            layout_result.pages,
            format_hint=format_hint,
        )
        issues = layout_result.issues + table_issues

        metadata = {
            "format": format_hint,
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "page_count": len(layout_result.pages),
            "table_count": len(tables),
        }
        return ExtractionResult(
            text=text,
            format_hint=format_hint or "binary",
            metadata=metadata,
            pages=layout_result.pages,
            tables=tables,
            issues=issues,
        )

    def _fallback_text_extraction(self, path: Path, format_hint: str) -> str:
        if format_hint == "pdf":
            return self._extract_pdf(path)
        if format_hint == "docx":
            return self._extract_docx(path)
        if format_hint in {"txt", "text"}:
            return self._extract_text_file(path)
        raise UnsupportedFormatError(f"Unsupported file type {format_hint}.")

    def _extract_from_link(self, url: str) -> ExtractionResult:
        try:
            scraped = scrape_document_source(url=url, timeout=10.0, max_bytes=2_000_000)
        except DocumentScrapeError as exc:
            raise KnowledgeIngestionError(str(exc)) from exc

        metadata = {
            "format": scraped.content_type or "text/html",
            "status_code": scraped.status_code,
            "content_length": scraped.content_length,
            "elapsed_ms": scraped.elapsed_ms,
            "word_count": scraped.word_count,
            "source_url": scraped.final_url or scraped.url,
        }
        return ExtractionResult(text=scraped.text, format_hint="text/html", metadata=metadata)

    # ------------------------------------------------------------------
    # Persistence

    def _persist_extraction(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> None:
        normalized = self._normalize_text(extraction.text)
        if not normalized:
            raise KnowledgeIngestionError("Extracted document is empty.")

        now = timezone.now()
        summary = self._build_summary(normalized)
        words = len(normalized.split())
        char_count = len(normalized)

        ingestion_metadata = dict(upload.ingestion_metadata or {})
        ingestion_metadata.update(
            {
                "format": extraction.format_hint,
                "word_count": words,
                "character_count": char_count,
                "ingested_at": now.isoformat(),
            }
        )
        ingestion_metadata.update(extraction.metadata or {})

        defaults = {
            "content": normalized,
            "metadata": {
                "ingested_at": now.isoformat(),
                "format": extraction.format_hint,
            },
        }

        with transaction.atomic():
            structured_summary = self._persist_structured_artifacts(upload, extraction)
            KnowledgeUploadText.objects.update_or_create(upload=upload, defaults=defaults)
            chunk_count = self._build_chunks(upload, normalized)
            upload.summary = summary
            upload.token_count = words
            upload.chunk_count = chunk_count
            upload.status = KnowledgeStatus.ACTIVE
            upload.last_ingested_at = now
            upload.ingestion_error = ""
            if structured_summary:
                ingestion_metadata["structured_exports"] = structured_summary
            upload.ingestion_metadata = ingestion_metadata
            upload.save(
                update_fields=[
                    "summary",
                    "token_count",
                    "chunk_count",
                    "status",
                    "last_ingested_at",
                    "ingestion_error",
                    "ingestion_metadata",
                    "updated_at",
                ]
            )

    def _build_chunks(self, upload: KnowledgeUpload, content: str) -> int:
        segments = self._chunk_text(content)
        KnowledgeUploadChunk.objects.filter(upload=upload).delete()
        if not segments:
            return 0

        embeddings: list[list[float]] | None = None
        if self.embedding_service:
            try:
                embeddings = self.embedding_service.embed_texts(segments)
            except EmbeddingProviderError as exc:
                logger.warning("Embedding generation failed upload=%s error=%s", upload.id, exc)
                embeddings = None
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Unexpected embedding failure upload=%s", upload.id)
                embeddings = None

        chunk_objects: list[KnowledgeUploadChunk] = []
        for index, segment in enumerate(segments):
            vector = None
            if embeddings and index < len(embeddings):
                vector = embeddings[index]
            chunk_objects.append(
                KnowledgeUploadChunk(
                    upload=upload,
                    chunk_index=index,
                    content=segment,
                    token_count=len(segment.split()),
                    embedding=vector,
                    metadata={
                        "strategy": "sliding_window",
                        "overlap": index > 0,
                    },
                )
            )
        KnowledgeUploadChunk.objects.bulk_create(chunk_objects, batch_size=100)
        logger.info("Chunked upload=%s into %s segments", upload.id, len(chunk_objects))
        return len(chunk_objects)

    def _persist_structured_artifacts(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> dict[str, Any]:
        KnowledgeUploadPage.objects.filter(upload=upload).delete()
        KnowledgeUploadTable.objects.filter(upload=upload).delete()
        KnowledgeUploadIssue.objects.filter(upload=upload).delete()

        page_lookup: dict[int, KnowledgeUploadPage] = {}
        block_objects: list[KnowledgeUploadPageBlock] = []
        page_summaries: list[dict[str, Any]] = []

        for page_payload in extraction.pages:
            page_obj = KnowledgeUploadPage.objects.create(
                upload=upload,
                page_number=page_payload.page_number,
                width=page_payload.width,
                height=page_payload.height,
                rotation=page_payload.rotation,
                text_density=page_payload.text_density,
                has_ocr_content=page_payload.has_ocr_content,
                content_type=page_payload.content_type,
                metadata=page_payload.metadata,
            )
            page_lookup[page_payload.page_number] = page_obj
            page_summaries.append(
                {
                    "page_number": page_payload.page_number,
                    "text_density": page_payload.text_density,
                    "has_ocr_content": page_payload.has_ocr_content,
                    "width": page_payload.width,
                    "height": page_payload.height,
                }
            )
            for block_payload in page_payload.blocks:
                block_objects.append(
                    KnowledgeUploadPageBlock(
                        upload=upload,
                        page=page_obj,
                        block_type=block_payload.block_type,
                        order_index=block_payload.order_index,
                        text=block_payload.text,
                        bbox=block_payload.bbox,
                        section_heading=block_payload.section_heading,
                        heading_path=block_payload.heading_path,
                        detected_language=block_payload.detected_language,
                        confidence=block_payload.confidence,
                        metadata=block_payload.metadata,
                    )
                )

        if block_objects:
            KnowledgeUploadPageBlock.objects.bulk_create(block_objects, batch_size=200)

        table_lookup: dict[tuple[int, int | None], KnowledgeUploadTable] = {}
        row_lookup: dict[tuple[uuid.UUID, int], KnowledgeUploadTableRow] = {}
        cell_lookup: dict[tuple[uuid.UUID, int], KnowledgeUploadTableCell] = {}
        table_summaries: list[dict[str, Any]] = []

        for table_payload in extraction.tables:
            page_obj = page_lookup.get(table_payload.page_number or -1)
            table_obj = KnowledgeUploadTable.objects.create(
                upload=upload,
                page=page_obj,
                source_block=None,
                title=table_payload.title,
                section_heading=table_payload.section_heading,
                order_index=table_payload.order_index,
                bbox=table_payload.bbox,
                column_schema=table_payload.column_schema,
                data_dictionary=table_payload.data_dictionary,
                metadata=table_payload.metadata,
            )
            table_lookup[(table_payload.order_index, table_payload.page_number)] = table_obj
            table_summaries.append(
                {
                    "order_index": table_payload.order_index,
                    "title": table_payload.title,
                    "page_number": table_payload.page_number,
                    "row_count": len(table_payload.rows),
                    "column_schema": table_payload.column_schema,
                }
            )
            for row_payload in table_payload.rows:
                row_obj = KnowledgeUploadTableRow.objects.create(
                    table=table_obj,
                    row_index=row_payload.row_index,
                    page_number=row_payload.page_number,
                    bbox=row_payload.bbox,
                    raw_text=row_payload.raw_text,
                    metadata=row_payload.metadata,
                )
                row_lookup[(table_obj.id, row_payload.row_index)] = row_obj
                for cell_payload in row_payload.cells:
                    cell_obj = KnowledgeUploadTableCell.objects.create(
                        table=table_obj,
                        row=row_obj,
                        column_index=cell_payload.column_index,
                        column_key=cell_payload.column_key,
                        raw_text=cell_payload.raw_text,
                        normalized_value=cell_payload.normalized_value,
                        bbox=cell_payload.bbox,
                        confidence=cell_payload.confidence,
                        metadata=cell_payload.metadata,
                    )
                    cell_lookup[(row_obj.id, cell_payload.column_index)] = cell_obj

        issue_objects: list[KnowledgeUploadIssue] = []
        issue_summaries: list[dict[str, Any]] = []
        valid_severities = set(KnowledgeIssueSeverity.values)

        for issue in extraction.issues:
            page_obj = page_lookup.get(issue.page_number) if issue.page_number is not None else None
            table_obj = None
            if issue.table_order_index is not None:
                table_obj = table_lookup.get((issue.table_order_index, issue.page_number))
                if table_obj is None:
                    for (order_idx, _), candidate in table_lookup.items():
                        if order_idx == issue.table_order_index:
                            table_obj = candidate
                            break
            row_obj = None
            if table_obj and issue.row_index is not None:
                row_obj = row_lookup.get((table_obj.id, issue.row_index))
            cell_obj = None
            if row_obj and issue.column_index is not None:
                cell_obj = cell_lookup.get((row_obj.id, issue.column_index))
            severity_value = issue.severity
            if severity_value not in valid_severities:
                severity_value = KnowledgeIssueSeverity.INFO.value
            issue_objects.append(
                KnowledgeUploadIssue(
                    upload=upload,
                    page=page_obj,
                    table=table_obj,
                    table_row=row_obj,
                    table_cell=cell_obj,
                    issue_code=issue.code,
                    severity=severity_value,
                    description=issue.description,
                    details=issue.details,
                )
            )
            issue_summaries.append(self._issue_to_dict(issue))

        if issue_objects:
            KnowledgeUploadIssue.objects.bulk_create(issue_objects, batch_size=100)

        if not (page_summaries or table_summaries or issue_summaries):
            return {}
        return {
            "pages": page_summaries,
            "tables": table_summaries,
            "issues": issue_summaries,
        }

    @staticmethod
    def _issue_to_dict(issue: IssuePayload) -> dict[str, Any]:
        return {
            "code": issue.code,
            "severity": issue.severity,
            "description": issue.description,
            "page_number": issue.page_number,
            "table_order_index": issue.table_order_index,
            "row_index": issue.row_index,
            "column_index": issue.column_index,
            "details": issue.details,
        }

    @staticmethod
    def _chunk_text(content: str, *, chunk_chars: int = 1200, overlap: int = 200) -> list[str]:
        text = (content or "").strip()
        if not text:
            return []
        length = len(text)
        start = 0
        segments: list[str] = []
        while start < length:
            end = min(length, start + chunk_chars)
            if end < length:
                newline = text.rfind("\n", start + 200, end)
                if newline > start:
                    end = newline
                else:
                    space = text.rfind(" ", start + 200, end)
                    if space > start:
                        end = space
            chunk = text[start:end].strip()
            if chunk:
                segments.append(chunk)
            if end >= length:
                break
            next_start = end - overlap if overlap else end
            if next_start <= start:
                next_start = end
            start = next_start
        return segments

    def _mark_job_completed(self, job: KnowledgeIngestionJob, *, extra: dict[str, Any] | None = None) -> None:
        finished = timezone.now()
        payload = dict(job.payload or {})
        if extra:
            payload.update(extra)
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.COMPLETED,
            finished_at=finished,
            payload=payload,
        )

    def _handle_failure(self, job: KnowledgeIngestionJob, message: str) -> None:
        finished = timezone.now()
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.FAILED,
            finished_at=finished,
            error_detail=message,
        )
        upload = job.upload
        upload.ingestion_error = message
        upload.status = KnowledgeStatus.FAILED
        upload.save(update_fields=["ingestion_error", "status", "updated_at"])

    # ------------------------------------------------------------------
    # Helpers

    def _claim_next_job(self) -> KnowledgeIngestionJob | None:
        job = (
            KnowledgeIngestionJob.objects.filter(
                status=KnowledgeIngestionJobStatus.QUEUED,
                job_type=KnowledgeIngestionJobType.INGEST,
            )
            .select_related("upload__file_detail", "upload__url_detail", "upload__business_profile")
            .order_by("created_at")
            .first()
        )
        if not job:
            return None

        claimed = KnowledgeIngestionJob.objects.filter(
            id=job.id,
            status=KnowledgeIngestionJobStatus.QUEUED,
        ).update(
            status=KnowledgeIngestionJobStatus.RUNNING,
            started_at=timezone.now(),
        )
        if not claimed:
            return None

        job.refresh_from_db()
        return job

    @staticmethod
    def _detect_format(file_detail: KnowledgeUploadFile) -> str | None:
        filename = (file_detail.filename or "").lower()
        suffix = Path(filename).suffix.lower()
        content_type = (file_detail.content_type or "").lower()
        guessed = mimetypes.guess_type(filename)[0] if filename else ""

        if suffix == ".pdf" or "pdf" in content_type or "pdf" in (guessed or ""):
            return "pdf"
        if suffix in {".docx", ".dotx"} or "word" in content_type or "officedocument.wordprocessingml" in content_type:
            return "docx"
        if suffix in {".txt", ".md", ".rtf"} or "text" in content_type:
            return "txt"
        return suffix.strip(".") if suffix else None

    @staticmethod
    @staticmethod
    def _extract_pdf(path: Path) -> str:
        pymupdf_error: Exception | None = None
        if fitz is not None:
            try:
                document = fitz.open(path)
                fragments = []
                for page in document:
                    fragments.append(page.get_text("text") or "")
                return "\n".join(fragments)
            except Exception as exc:  # pragma: no cover - fall back to PyPDF
                pymupdf_error = exc
                logger.warning("PyMuPDF extraction failed for %s: %s", path, exc)

        if PdfReader is None:
            raise KnowledgeIngestionError(
                f"PDF ingestion requires PyMuPDF or pypdf (PyMuPDF error: {pymupdf_error})"
            )

        try:
            reader = PdfReader(str(path))
            fragments = []
            for page in reader.pages:
                try:
                    fragments.append(page.extract_text() or "")
                except Exception:  # pragma: no cover - individual page failures should not abort entire job
                    fragments.append("")
            return "\n".join(fragments)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to extract PDF text: {exc}") from exc

    @staticmethod
    def _extract_docx(path: Path) -> str:
        if DocxDocument is None:
            raise KnowledgeIngestionError("DOCX ingestion requires the python-docx package.")
        try:
            document = DocxDocument(str(path))
            fragments = [paragraph.text for paragraph in document.paragraphs]
            return "\n".join(fragments)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to extract DOCX text: {exc}") from exc

    @staticmethod
    def _extract_text_file(path: Path) -> str:
        encodings = ("utf-8", "utf-16", "latin-1")
        for encoding in encodings:
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        # fallback to binary decode ignoring errors
        return path.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _normalize_text(raw: str) -> str:
        text = raw.replace("\x00", " ").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _build_summary(content: str, limit: int = 500) -> str:
        paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
        if not paragraphs:
            return content[:limit]
        summary = " ".join(paragraphs[:3])
        if len(summary) > limit:
            summary = summary[: limit - 1].rstrip() + "…"
        return summary


__all__ = [
    "queue_ingestion_job",
    "KnowledgeIngestionService",
    "KnowledgeIngestionError",
    "IngestionJobResult",
]
