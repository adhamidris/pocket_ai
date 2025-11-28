"""
Knowledge Ingestion Service

This module handles the complete pipeline for ingesting knowledge documents into the system.
It processes various file formats (PDF, DOCX, CSV, XLSX, JSON, etc.) and web links, extracting
structured content, tables, and entities for use in RAG (Retrieval-Augmented Generation) systems.

Key Components:
- KnowledgeIngestionService: Main orchestration service for the ingestion pipeline
- PageRenderer: Extracts structured layout from PDFs and DOCX files
- TableDetector: Heuristic-based table detection from text blocks
- GeometryTableReconstructor: Geometry-based table extraction using PDF coordinate data
- OCRReconciler: Handles OCR for low-density PDF pages (scanned documents)

Data Flow:
1. Upload → Queue ingestion job
2. Extract text and structure from file/link
3. Detect and normalize tables
4. Extract entities and aliases (for lookup)
5. Chunk content for embedding
6. Generate embeddings (inline or async)
7. Persist to database (chunks, pages, tables, entities)

The service supports multiple extraction strategies:
- Layout-aware extraction (PyMuPDF for PDFs)
- Geometry-based table reconstruction (for complex PDF layouts)
- Heuristic table detection (fallback for simpler formats)
- OCR reconciliation (for scanned documents)
"""

from __future__ import annotations

from collections import deque
import csv
import json
import logging
import math
import mimetypes
import io
import re
import statistics
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from django.conf import settings
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from apps.accounts.models import (
    KnowledgeIngestionJob,
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeIssueSeverity,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeVisibility,
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
    KnowledgeEntity,
    KnowledgeAlias,
)
from apps.services.documents import DocumentScrapeError, scrape_document_source
from apps.services.embeddings import LocalEmbeddingService, build_embedding_service, EmbeddingProviderError
from apps.services.feature_flags import FeatureFlagService
from apps.services.quality_monitor import QualityMonitor
from apps.services.table_normalization import (
    NormalizedSheet,
    SheetNormalizationDiagnostics,
    normalize_sheet_rows,
    resolve_normalization_policy,
    sheet_is_allowed,
    summarize_normalization,
)

logger = logging.getLogger(__name__)


def _log_normalization_summary(upload: KnowledgeUpload | None, source: str, summary: Mapping[str, Any] | None) -> None:
    """
    Log normalization statistics for table processing.
    
    This helper logs when table normalization has modified data (dropped rows,
    trimmed columns, replaced tokens, or skipped sheets). Only logs if there
    were actual changes to avoid noise in logs.
    
    Args:
        upload: The knowledge upload being processed (for logging context)
        source: Source format identifier (e.g., "csv", "xlsx")
        summary: Normalization summary dict with enabled flag and statistics
    """
    if not summary or not summary.get("enabled"):
        return
    # Extract normalization metrics - these indicate data was modified during processing
    rows = int((summary.get("rows_dropped") or {}).get("total", 0))
    columns = int((summary.get("columns_trimmed") or {}).get("total", 0))
    tokens = int(summary.get("tokens_replaced") or 0)
    skipped = len(summary.get("empty_sheets_skipped") or []) + len(summary.get("policy_skipped") or [])
    # Only log if there were actual changes (avoids log spam for clean data)
    if not any([rows, columns, tokens, skipped]):
        return
    logger.info(
        "ingest.normalization upload=%s source=%s rows_dropped=%s columns_trimmed=%s tokens_replaced=%s sheets_skipped=%s",
        getattr(upload, "id", None),
        source,
        rows,
        columns,
        tokens,
        skipped,
    )

# Optional dependencies with graceful fallbacks
# These are imported conditionally because not all environments may have them installed.
# The code checks for None before using these modules.
try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import fitz  # type: ignore[attr-defined]  # PyMuPDF - preferred for PDF layout extraction
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from pypdf import PdfReader  # Fallback PDF reader when PyMuPDF unavailable
except ImportError:  # pragma: no cover - fallback handled via runtime check
    PdfReader = None  # type: ignore

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from docx import Document as DocxDocument  # Microsoft Word document processing
except ImportError:  # pragma: no cover - fallback handled via runtime check
    DocxDocument = None  # type: ignore

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from openpyxl import load_workbook  # Excel .xlsx file processing
except ImportError:  # pragma: no cover - fallback handled via runtime check
    load_workbook = None  # type: ignore

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import xlrd  # Legacy Excel .xls file processing
except ImportError:  # pragma: no cover - fallback handled via runtime check
    xlrd = None  # type: ignore


# ============================================================================
# OCR Support Functions
# ============================================================================
# These standalone functions handle OCR (Optical Character Recognition) for
# scanned PDFs. OCR is expensive, so we only use it when text density is low,
# indicating the page might be a scanned image rather than native text.

def create_tesseract_ocr_callable() -> Callable[[bytes], str] | None:
    """
    Create a Tesseract OCR callable for processing low-density PDF pages.
    
    This factory function checks if Tesseract OCR is available and returns
    a callable that can convert image bytes to text. Used by OCRReconciler
    to handle scanned documents where text extraction yields little content.
    
    Returns:
        A callable that takes image bytes and returns extracted text, or None
        if Tesseract is not installed or unavailable.
        
    Note:
        Requires both pytesseract (Python wrapper) and Tesseract (system binary)
        to be installed. The PSM (Page Segmentation Mode) 1 is used for
        automatic page segmentation with OSD (Orientation and Script Detection).
    """
    try:
        import pytesseract
        from PIL import Image
        import io
        
        def ocr_callable(image_bytes: bytes) -> str:
            """
            OCR callable that processes image bytes and returns extracted text.
            
            Args:
                image_bytes: Raw PNG/JPEG image bytes from PDF page render
            
            Returns:
                Extracted text string, or empty string on failure
            """
            try:
                image = Image.open(io.BytesIO(image_bytes))
                # PSM 1: Automatic page segmentation with OSD
                # This works well for most document layouts
                text = pytesseract.image_to_string(
                    image,
                    config='--psm 1'
                )
                return text.strip()
            except Exception as exc:
                logger.warning(f"Tesseract OCR failed: {exc}")
                return ""
        
        # Verify Tesseract is actually installed (not just the Python wrapper)
        try:
            pytesseract.get_tesseract_version()
            logger.info("Tesseract OCR enabled successfully")
            return ocr_callable
        except Exception:
            logger.warning("Tesseract not found - OCR will be disabled")
            return None
            
    except ImportError:
        logger.warning("pytesseract not installed - pip install pytesseract")
        return None


def create_ocr_reconciler(
    *,
    density_threshold: float = 0.00015,
    enable_ocr: bool = True,
) -> OCRReconciler:
    """
    Factory function to create an OCRReconciler with optional Tesseract support.
    
    The OCR reconciler is used to detect and process scanned PDF pages that have
    low text density (indicating they're images rather than native text). When
    density falls below the threshold, OCR is attempted to extract text.
    
    Args:
        density_threshold: Text density threshold (chars per point²). Pages below
            this threshold are considered candidates for OCR. Default 0.00015 means
            a page needs ~0.015% text density to trigger OCR.
        enable_ocr: Whether to attempt OCR setup. If False, returns reconciler
            that only flags low-density pages without processing them.
    
    Returns:
        Configured OCRReconciler instance ready for use in PDF processing
    """
    ocr_callable = None
    
    if enable_ocr:
        ocr_callable = create_tesseract_ocr_callable()
        if ocr_callable:
            logger.info("OCR enabled with density threshold: %.6f", density_threshold)
        else:
            logger.warning("OCR requested but not available")
    
    return OCRReconciler(
        density_threshold=density_threshold,
        ocr_callable=ocr_callable,
    )

# ============================================================================
# Constants and Configuration
# ============================================================================

# Source types that require parsing/ingestion (as opposed to raw storage)
SUPPORTED_SOURCE_TYPES = {
    KnowledgeSourceType.FILE,
    KnowledgeSourceType.LINK,
    KnowledgeSourceType.INTEGRATION,
}

# Keywords that typically indicate identifier/alias fields in structured data
# Used by entity extraction to find lookup keys (e.g., product codes, SKUs)
# These help build searchable aliases for entity lookup in RAG queries
ALIAS_KEYWORDS = (
    "slug",
    "id",
    "identifier",
    "code",
    "sku",
    "policy",
    "policy_id",
    "record_id",
    "product_code",
    "trip_code",
    "trip_id",
    "reference",
    "reference_id",
)

# Regex patterns for detecting identifiers in text
# These are used to extract lookup keys from unstructured content
SLUG_PATTERN = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+){1,}\b")  # kebab-case slugs
IDENTIFIER_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}", re.IGNORECASE)  # General identifiers
ID_LINE_PATTERN = re.compile(
    r"(?:^|\b)(?:id|identifier|sku|code|policy|ref|reference)\s*[:#]\s*([a-z0-9][a-z0-9_\-\/]+)",
    re.IGNORECASE,
)  # Pattern: "ID: ABC123" or "Code: xyz-456"

# Alias validation constraints
# These prevent noise from very short strings or overly long identifiers
ALIAS_MIN_LENGTH = 4  # Minimum length for normal aliases
ALIAS_SYMBOL_MIN_LENGTH = 3  # Minimum for symbol-like identifiers (e.g., "ABC")
ALIAS_MAX_LENGTH = 255  # Database column limit


# ============================================================================
# Exception Classes
# ============================================================================

class KnowledgeIngestionError(RuntimeError):
    """
    Base exception for all knowledge ingestion failures.
    
    Raised when ingestion cannot proceed due to errors in extraction,
    processing, or validation. Subclasses provide more specific error types.
    """


class UnsupportedFormatError(KnowledgeIngestionError):
    """
    Raised when the file format cannot be determined or is not supported.
    
    This typically happens when:
    - File extension/content-type don't match known formats
    - File is corrupted or unreadable
    - Format requires dependencies that aren't installed
    """


# ============================================================================
# Data Transfer Objects (DTOs)
# ============================================================================
# These dataclasses represent structured content extracted from documents.
# They're used to pass data between extraction, processing, and persistence layers.

@dataclass(frozen=True)
class PageBlockPayload:
    """
    Represents a single block of content on a document page.
    
    Blocks are the atomic units of layout-aware extraction. They can be
    paragraphs, headings, tables, or images. The bbox (bounding box) preserves
    spatial information for downstream processing like table reconstruction.
    
    Attributes:
        block_type: Type of block (PARAGRAPH, HEADING, TABLE, IMAGE)
        order_index: Reading order position on the page (0-based)
        text: Extracted text content
        bbox: Bounding box coordinates {x0, y0, x1, y1} in points
        section_heading: Nearest heading that precedes this block
        heading_path: Hierarchical path of headings (e.g., ["Chapter 1", "Section 1.1"])
        detected_language: Language code if detected (empty if unknown)
        confidence: Extraction confidence score (0.0-1.0) if available
        metadata: Additional extraction metadata (font info, style, etc.)
    """
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
    """
    Represents the complete layout structure of a single document page.
    
    This captures both the geometric properties of the page and its structured
    content blocks. Text density is used to determine if OCR is needed for
    scanned documents.
    
    Attributes:
        page_number: 1-based page number in the document
        width: Page width in points (72 points = 1 inch)
        height: Page height in points
        rotation: Page rotation in degrees (0, 90, 180, 270)
        text_density: Characters per point² - used to detect scanned pages
        has_ocr_content: True if this page was processed with OCR
        content_type: MIME type of the source document
        blocks: Ordered list of content blocks on this page
        metadata: Page-level metadata (extraction method, char count, etc.)
    """
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
    """
    Represents a single cell in a table with both raw and normalized values.
    
    Normalized values extract structured data like currency amounts, percentages,
    and ranges for better semantic search. For example, "EGP 100-200" becomes
    {"range": {"min": 100, "max": 200}, "currency": "EGP"}.
    
    Attributes:
        row_index: 0-based row position (0 = header row)
        column_index: 0-based column position
        column_key: Normalized column name (e.g., "interest_rate" not "Interest Rate")
        raw_text: Original cell text as extracted
        normalized_value: Structured data extracted from raw_text (amounts, ranges, etc.)
        bbox: Cell bounding box coordinates
        confidence: Extraction confidence if available
        metadata: Cell-level metadata (span count, formatting, etc.)
    """
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
    """
    Represents a single row in a table with its cells.
    
    Rows can be header rows (row_index=0) or data rows. The raw_text is a
    TSV-formatted representation for embedding/retrieval purposes.
    
    Attributes:
        row_index: 0-based row position (0 = header)
        page_number: Page where this row appears (None for CSV/JSON sources)
        bbox: Row bounding box (union of all cell bboxes)
        raw_text: TSV-formatted row content for text search
        metadata: Row-level metadata (row_type: "header" or "data")
        cells: Ordered list of cells in this row
    """
    row_index: int
    page_number: int | None
    bbox: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    cells: list[TableCellPayload] = field(default_factory=list)


@dataclass(frozen=True)
class TablePayload:
    """
    Represents a complete table extracted from a document.
    
    Tables are one of the most valuable structures for RAG because they contain
    structured, queryable data. This payload includes both the raw structure and
    normalized values for semantic search.
    
    Attributes:
        order_index: Table order in the document (1-based)
        title: Table title or name
        section_heading: Section heading that contains this table
        page_number: Page where table appears (None for CSV/JSON)
        bbox: Table bounding box (union of all row bboxes)
        column_schema: Normalized column names (e.g., ["card_type", "issuance_fee"])
        data_dictionary: Schema-level metadata (column types, constraints, etc.)
        metadata: Table-level metadata (detection method, source, etc.)
        rows: Ordered list of rows (first row is typically header)
    """
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
    """
    Represents a warning or error encountered during document processing.
    
    Issues are non-fatal problems that don't stop ingestion but should be
    reported to users. Examples: missing table headers, column mismatches,
    OCR failures, truncated content.
    
    Attributes:
        code: Issue code identifier (e.g., "table_missing_header", "ocr_failed")
        severity: Severity level (INFO, WARNING, ERROR)
        description: Human-readable description
        page_number: Page where issue occurred (None if page-agnostic)
        table_order_index: Table where issue occurred (None if not table-related)
        row_index: Row where issue occurred (None if not row-specific)
        column_index: Column where issue occurred (None if not column-specific)
        details: Additional diagnostic information
    """
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
    """
    Result of page-level extraction from a document.
    
    Contains the extracted text (for embedding), structured page layouts,
    and any issues encountered during extraction.
    
    Attributes:
        text: Plain text representation of the document (for chunking/embedding)
        pages: Structured page layouts with blocks and metadata
        issues: Non-fatal issues encountered during extraction
    """
    text: str
    pages: list[PageLayout] = field(default_factory=list)
    issues: list[IssuePayload] = field(default_factory=list)

@dataclass(frozen=True)
class PdfSpan:
    """
    Represents a single text span with geometric coordinates from PDF extraction.
    
    Spans are the atomic units for geometry-based table reconstruction. They
    preserve exact position and font information needed to cluster text into
    rows and columns. Used by GeometryTableReconstructor to build tables
    from coordinate data rather than text patterns.
    
    Attributes:
        page_number: 1-based page number
        text: Text content of this span
        x0, y0: Bottom-left corner coordinates (in points)
        x1, y1: Top-right corner coordinates (in points)
        font: Font name if available
        size: Font size in points if available
    
    Properties:
        x_center: Horizontal center point (for column clustering)
        y_center: Vertical center point (for row clustering)
    """
    page_number: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font: str | None
    size: float | None

    @property
    def x_center(self) -> float:
        """Horizontal center point for column clustering."""
        return 0.5 * (self.x0 + self.x1)

    @property
    def y_center(self) -> float:
        """Vertical center point for row clustering."""
        return 0.5 * (self.y0 + self.y1)

def _union_bbox(bboxes: list[dict[str, float]]) -> dict[str, float]:
    """
    Compute the union bounding box from multiple bboxes.
    
    This creates a single bounding box that encompasses all input bboxes.
    Used when combining multiple spans/cells into a single row or table region.
    The union bbox represents the total area covered by all components.
    
    Args:
        bboxes: List of bounding box dictionaries with x0, y0, x1, y1 keys
    
    Returns:
        Union bounding box (or zero bbox if input is empty)
    """
    if not bboxes:
        return {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}
    # Union = min x0/y0 (top-left) and max x1/y1 (bottom-right)
    x0 = min(b["x0"] for b in bboxes)
    y0 = min(b["y0"] for b in bboxes)
    x1 = max(b["x1"] for b in bboxes)
    y1 = max(b["y1"] for b in bboxes)
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)}

@dataclass(frozen=True)
class ExtractionResult:
    """
    Complete extraction result from a document source.
    
    This is the primary output of the extraction pipeline, containing all
    structured content extracted from a file or link. The text field is
    optimized for chunking and embedding, while pages and tables preserve
    structure for semantic search and entity extraction.
    
    Attributes:
        text: Plain text representation with inline tables in TSV format.
            This is the primary content for chunking and embedding.
        format_hint: Detected format (pdf, docx, csv, json, etc.)
        metadata: Format-specific metadata (page count, table count, etc.)
        pages: Structured page layouts (for PDF/DOCX sources)
        tables: Extracted tables with normalized values
        issues: Non-fatal issues encountered during extraction
        entities: Extracted entities (from JSON or table rows) for lookup
        text_html: HTML representation for web content (optional)
    """
    text: str  # Plain text with inline tables (TSV format)
    format_hint: str
    metadata: dict[str, Any]
    pages: list[PageLayout] = field(default_factory=list)
    tables: list[TablePayload] = field(default_factory=list)
    issues: list[IssuePayload] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    text_html: str | None = None  # HTML representation for web content

@dataclass(frozen=True)
class EnhancedContextDocument:
    """
    JSON envelope wrapper for sending structured context to LLM.
    
    This format matches Claude's document structure for better model
    understanding. Used when sending document context to LLMs for
    RAG queries, where structured data (tables, pages) improves
    comprehension over plain text alone.
    
    Attributes:
        index: Document index in a batch
        media_type: MIME type of the source
        source: Source identifier (filename, URL, etc.)
        text: Primary text content
        pages: Structured page data (optional, for PDF sources)
        tables: Structured table data (optional, for tabular sources)
        metadata: Additional context metadata
    
    Methods:
        to_dict: Serialize to dictionary for JSON transmission
    """
    index: int
    media_type: str
    source: str
    text: str
    pages: list[dict[str, Any]] | None = None
    tables: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary for JSON serialization.
        
        Returns:
            Dictionary representation suitable for JSON encoding
        """
        result = {
            "index": self.index,
            "media_type": self.media_type,
            "source": self.source,
            "text": self.text,
        }
        if self.pages:
            result["pages"] = self.pages
        if self.tables:
            result["tables"] = self.tables
        if self.metadata:
            result["metadata"] = self.metadata
        return result

class OCRReconciler:
    """
    Lightweight OCR orchestrator that flags low-density pages and optionally runs OCR.
    
    This class implements a smart OCR strategy: only process pages that appear to be
    scanned images (low text density) rather than native text PDFs. This avoids the
    expensive OCR operation for most documents while still handling scanned content.
    
    The density threshold (default 0.00015) means a page needs approximately
    0.015% text density to be considered "native text". Pages below this threshold
    are candidates for OCR processing.
    
    Attributes:
        density_threshold: Minimum text density (chars/point²) to skip OCR
        ocr_callable: Optional OCR function (from create_tesseract_ocr_callable)
    """

    def __init__(
        self,
        *,
        density_threshold: float = 0.00015,
        ocr_callable: Callable[[bytes], str] | None = None,
    ):
        """
        Initialize OCR reconciler.
        
        Args:
            density_threshold: Text density threshold below which OCR is attempted.
                Default 0.00015 means ~0.015% density triggers OCR.
            ocr_callable: Optional OCR function. If None, only flags low-density
                pages without processing them.
        """
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
        """
        Reconcile extracted text with OCR if page appears to be scanned.
        
        This method decides whether a page needs OCR based on text density.
        If density is high enough, returns the extracted text as-is. If low,
        attempts OCR (if available) or flags the issue.
        
        Args:
            page: PyMuPDF page object (for rendering to image if OCR needed)
            page_number: Page number for error reporting
            extracted_text: Text extracted via normal PDF text extraction
            text_density: Calculated text density (chars per point²)
        
        Returns:
            Tuple of:
            - Final text (extracted or OCR'd)
            - Whether OCR was used (True) or not (False)
            - List of issues encountered (empty if successful)
        """
        # Fast path: page has sufficient text density, skip OCR
        if text_density >= self.density_threshold:
            return extracted_text, False, []

        issues: list[IssuePayload] = []
        
        # OCR not available - flag the issue but continue with extracted text
        # This allows ingestion to proceed even when OCR isn't configured
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

        # Attempt OCR: render page to image, then extract text
        try:
            # Render page as PNG image for OCR processing
            pixmap = page.get_pixmap()  # type: ignore[attr-defined]
            image_bytes = pixmap.tobytes("png")
            ocr_text = self.ocr_callable(image_bytes)
            
            # OCR returned empty - might be blank page or OCR failure
            if not ocr_text:
                issues.append(
                    IssuePayload(
                        code="ocr_empty",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description="OCR returned no text for low-density page.",
                        page_number=page_number,
                    )
                )
                # Return original extracted text as fallback
                return extracted_text, False, issues
            
            # Success: return OCR'd text
            return ocr_text, True, []
        except Exception as exc:  # pragma: no cover - best effort
            # OCR failed but don't abort ingestion - return original text with error
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
    Produces layout-aware payloads from documents using PyMuPDF when available.
    
    This class extracts structured content from PDFs and DOCX files, preserving
    layout information (bounding boxes, reading order, headings) that's critical
    for downstream processing like table detection and entity extraction.
    
    The renderer uses different strategies:
    - PDF: PyMuPDF for layout-aware extraction with block-level coordinates
    - DOCX: python-docx for paragraph-level structure
    - Fallback: Plain text extraction when specialized libraries unavailable
    
    Fallbacks collapse documents into a single page with coarse metadata so
    downstream persistence can still operate, but without layout benefits.
    
    Attributes:
        _fitz: PyMuPDF module (fitz) if available, None otherwise
    """

    def __init__(self, *, pymupdf_module: Any | None = None):
        """
        Initialize page renderer.
        
        Args:
            pymupdf_module: PyMuPDF module (fitz) if available. If None, PDF
                processing will use fallback methods.
        """
        self._fitz = pymupdf_module

    def render(self, path: Path, *, format_hint: str, ocr: OCRReconciler | None = None) -> PageRendererResult:
        """
        Render document into structured page layouts.
        
        This is the main entry point for document extraction. Routes to
        format-specific renderers (PDF, DOCX) or falls back to plain text.
        
        Args:
            path: Path to document file
            format_hint: Detected format (pdf, docx, txt, etc.)
            ocr: Optional OCR reconciler for scanned PDF pages
        
        Returns:
            PageRendererResult with extracted text, page layouts, and issues
        
        Raises:
            KnowledgeIngestionError: If document cannot be opened or processed
        """
        if format_hint == "pdf" and self._fitz is not None:
            return self._render_pdf(path, ocr=ocr)
        if format_hint == "docx":
            return self._render_docx(path)
        text = path.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
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
        """
        Enhanced PDF rendering with layout-aware extraction.
        
        This method implements a sophisticated PDF extraction strategy:
        1. Block-based reading order (top-to-bottom, left-to-right)
        2. Row detection for table-like structures
        3. Inline TSV representation for tables (better embedding)
        4. OCR reconciliation for scanned pages
        
        The extraction preserves spatial information (bounding boxes) which is
        critical for downstream table reconstruction and entity extraction.
        
        Args:
            path: Path to PDF file
            ocr: Optional OCR reconciler for low-density pages
        
        Returns:
            PageRendererResult with extracted text, page layouts, and issues
        
        Raises:
            KnowledgeIngestionError: If PDF cannot be opened or processed
        """
        try:
            document = self._fitz.open(path)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open PDF for layout parsing: {exc}") from exc

        pages: list[PageLayout] = []
        fragments: list[str] = []
        issues: list[IssuePayload] = []
        
        for index, page in enumerate(document, start=1):
            # Get raw blocks from PyMuPDF - each block is a text region with coordinates
            # Block format: (x0, y0, x1, y1, text, block_no, block_type)
            raw_blocks = page.get_text("blocks") or []
            
            # Sort blocks by reading order: top-to-bottom (y0), then left-to-right (x0)
            # Rounding y0 to nearest 5 points groups blocks on the same horizontal line
            # This helps detect table rows where multiple blocks share the same Y position
            sorted_blocks = sorted(
                raw_blocks,
                key=lambda b: (round(b[1] / 5) * 5, b[0])
            )
            
            # Group blocks into rows based on vertical position
            # This enables table detection: rows with 3+ blocks are likely table rows
            block_rows = self._group_blocks_into_rows(sorted_blocks)
            
            # Assemble text from rows with inline table formatting
            # This creates TSV-formatted text for table rows, which improves
            # embedding quality by preserving column structure
            block_texts = []
            for row_blocks in block_rows:
                # Heuristic: 3+ blocks in same row = likely table row
                # This detects tabular data without requiring explicit table detection
                if len(row_blocks) >= 3:  # 3+ blocks in same row = likely table
                    # Format as TSV (tab-separated values) for better embedding
                    # TSV format helps LLMs understand column relationships
                    row_cells = [block[4].strip() for block in row_blocks if len(block) > 4]
                    row_cells = [cell for cell in row_cells if cell]  # Remove empty cells
                    if row_cells:
                        block_texts.append("\t".join(row_cells))
                else:
                    # Regular text - just concatenate blocks in order
                    for block in row_blocks:
                        if len(block) > 4:
                            text_fragment = block[4].strip()
                            if text_fragment:
                                block_texts.append(text_fragment)
            
            # Join blocks with newlines to preserve paragraph structure
            plain_text = "\n".join(block_texts) if block_texts else ""
            
            # Calculate text density for OCR decision
            # Density = characters per point² (used to detect scanned pages)
            char_count = len(plain_text.strip())
            rect = page.rect
            area = max(rect.width * rect.height, 1.0)  # Avoid division by zero
            density = char_count / area
            
            # OCR reconciliation: check if page needs OCR processing
            # Low-density pages are likely scanned images that need OCR
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
            
            # Build structured blocks with metadata for downstream processing
            # These blocks preserve layout information for table detection
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
                    metadata={
                        "char_count": char_count,
                        "block_count": len(raw_blocks),
                        "extraction_method": "block_sorted_with_row_detection"
                    },
                )
            )
        
        return PageRendererResult(
            text="\n\n".join(fragments),
            pages=pages,
            issues=issues
        )

    def _collect_spans_from_textdict(self, page_index: int, obj: dict) -> list[PdfSpan]:
        """
        Extract PdfSpan objects from PyMuPDF's text dict/rawdict structure.
        
        PyMuPDF can return text in structured format: blocks -> lines -> spans.
        This method walks that hierarchy to extract individual text spans with
        their coordinates and font information. This data is critical for
        geometry-based table reconstruction.
        
        The method safely handles missing keys (common in malformed PDFs) and
        uses a fallback strategy for bounding boxes:
        1. Prefer span-level bbox (most accurate)
        2. Fall back to line-level bbox (less accurate but better than nothing)
        3. Use zeros as last resort (prevents crashes)
        
        Args:
            page_index: 1-based page number for span attribution
            obj: PyMuPDF text dict/rawdict structure with blocks/lines/spans
        
        Returns:
            List of PdfSpan objects with coordinates and font info
        """
        page_spans: list[PdfSpan] = []
        if not obj:
            return page_spans

        # Walk the hierarchy: blocks -> lines -> spans
        # Type 0 = text block (other types are images, etc.)
        blocks = (obj.get("blocks") or [])
        for block in blocks:
            if (block or {}).get("type") != 0:
                continue  # Skip non-text blocks (images, etc.)
            
            lines = (block.get("lines") or [])
            for line in lines:
                # Get line bbox as fallback (in case span doesn't have one)
                line_bbox = line.get("bbox") or [0, 0, 0, 0]
                spans = (line.get("spans") or [])
                
                for span in spans:
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue  # Skip empty spans
                    
                    # Bbox fallback strategy: span > line > zeros
                    # This handles PDFs where span coordinates are missing
                    bbox = span.get("bbox") or line_bbox or [0, 0, 0, 0]
                    
                    # Extract font size (may be missing in some PDFs)
                    size_val = span.get("size")
                    try:
                        size = float(size_val) if size_val is not None else None
                    except Exception:
                        size = None  # Invalid size value - ignore it
                    
                    page_spans.append(
                        PdfSpan(
                            page_number=page_index,
                            text=text,
                            x0=float(bbox[0]),
                            y0=float(bbox[1]),
                            x1=float(bbox[2]),
                            y1=float(bbox[3]),
                            font=span.get("font"),
                            size=size,
                        )
                    )
        return page_spans


    def extract_pdf_spans(self, path: Path) -> list[list[PdfSpan]]:
        """
        Extract text spans with coordinates from all pages in a PDF.
        
        This method implements a robust fallback strategy to extract coordinate
        data from PDFs. Different PDFs expose coordinate data in different formats,
        so we try multiple extraction methods in order of preference:
        
        1. 'rawdict': Most detailed format with full font/size info (preferred)
        2. 'dict': Similar to rawdict but may have less detail
        3. 'words': Word-level coordinates (no font info, but still usable)
        
        This fallback ensures we can extract geometry data even from PDFs with
        non-standard structure. The coordinate data is essential for geometry-based
        table reconstruction.
        
        Args:
            path: Path to PDF file
        
        Returns:
            List of lists: one list per page, each containing PdfSpan objects
            with coordinates and font information. Empty list if extraction fails.
        """
        if self._fitz is None:
            return []
        try:
            doc = self._fitz.open(path)
        except Exception:
            return []

        results: list[list[PdfSpan]] = []
        for page_index, page in enumerate(doc, start=1):
            # --- Fast path: 'rawdict' (most detailed format)
            # This format has the richest metadata (font, size, exact bboxes)
            page_spans: list[PdfSpan] = []
            try:
                raw = page.get_text("rawdict") or {}
                page_spans = self._collect_spans_from_textdict(page_index, raw)
            except Exception:
                page_spans = []

            # --- Fallback 1: 'dict' (similar structure, may have less detail)
            # Some PDFs don't support rawdict but support dict
            if not page_spans:
                try:
                    dct = page.get_text("dict") or {}
                    page_spans = self._collect_spans_from_textdict(page_index, dct)
                except Exception:
                    page_spans = []

            # --- Fallback 2: 'words' (word-level coordinates, no font info)
            # This is the most basic format but still provides coordinates
            # We lose font size info, but header detection can use other cues
            if not page_spans:
                try:
                    words = page.get_text("words") or []
                    word_spans: list[PdfSpan] = []
                    for w in words:
                        # words tuple format: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
                        if not w or len(w) < 5:
                            continue
                        x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
                        wtext = (w[4] or "").strip()
                        if not wtext:
                            continue
                        word_spans.append(
                            PdfSpan(
                                page_number=page_index,
                                text=wtext,
                                x0=x0,
                                y0=y0,
                                x1=x1,
                                y1=y1,
                                font=None,  # Not available in 'words' format
                                size=None,  # Not available, but header detection has other cues
                            )
                        )
                    page_spans = word_spans
                except Exception:
                    page_spans = []

            results.append(page_spans)
        return results


    def _group_blocks_into_rows(self, sorted_blocks: list) -> list[list]:
        """
        Group blocks that are on the same horizontal line (same Y position).
        
        This method clusters text blocks by vertical position to detect table rows.
        Blocks within a tolerance (5 points) are considered on the same row.
        This is critical for table detection: rows with multiple blocks indicate
        columnar data.
        
        Args:
            sorted_blocks: Blocks sorted by reading order (top-to-bottom, left-to-right)
        
        Returns:
            List of rows, where each row is a list of blocks on the same Y position
        """
        if not sorted_blocks:
            return []
        
        rows = []
        current_row = []
        current_y = None
        # 5-point tolerance accounts for slight vertical misalignment in PDFs
        # This is necessary because PDF coordinates aren't always perfectly aligned
        tolerance = 5  # Vertical position tolerance in points
        
        for block in sorted_blocks:
            if len(block) <= 1:
                continue
            
            block_y = block[1]  # Y position (top coordinate)
            
            if current_y is None:
                # First block - start new row
                current_y = block_y
                current_row = [block]
            elif abs(block_y - current_y) <= tolerance:
                # Same row - add to current row
                current_row.append(block)
            else:
                # New row detected - finalize previous row and start new one
                if current_row:
                    rows.append(current_row)
                current_row = [block]
                current_y = block_y
        
        # Don't forget the last row
        if current_row:
            rows.append(current_row)
        
        return rows

    def _looks_like_table_text(self, text: str) -> bool:
        """
        Check if a text block appears to contain tabular data.
        
        This is a quick heuristic to identify table-like content before
        running more expensive table detection. Looks for common table
        delimiters in the first line.
        
        Args:
            text: Text block to check
        
        Returns:
            True if text appears to be tabular (has pipes, tabs, or multiple spaces)
        """
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False  # Need at least header + one data row
        
        # Check first line for common table delimiters
        # These are strong indicators of tabular structure
        sample = lines[0]
        has_pipes = "|" in sample  # Markdown-style tables
        has_tabs = "\t" in sample   # TSV format
        has_multi_spaces = bool(re.search(r"\s{3,}", sample))  # Space-aligned columns
        
        return has_pipes or has_tabs or has_multi_spaces

    def _format_table_as_tsv(self, text: str) -> str:
        """
        Convert table text to TSV (tab-separated values) format.
        
        TSV format is preferred for embedding because:
        1. Preserves column structure (better semantic understanding)
        2. Consistent delimiter (tabs) regardless of source format
        3. LLMs understand TSV better than space-aligned or pipe-delimited
        
        The method auto-detects the delimiter (pipes, tabs, or spaces) and
        normalizes everything to tabs for consistency.
        
        Args:
            text: Table text in any format (pipe, tab, or space-delimited)
        
        Returns:
            TSV-formatted table text with tabs as delimiters
        
        Example:
            Input: "Card Type | Issuance Fee | Interest Rate"
            Output: "Card Type\tIssuance Fee\tInterest Rate"
        """
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return text
        
        # Auto-detect delimiter from first line
        # Priority: pipes > tabs > multiple spaces
        first_line = lines[0]
        if "|" in first_line:
            delimiter = "|"  # Markdown-style
        elif "\t" in first_line:
            delimiter = "\t"  # Already TSV
        else:
            # Space-aligned columns - use regex to split on 2+ spaces
            delimiter = None
        
        formatted_rows = []
        for line in lines:
            if delimiter:
                # Split on detected delimiter
                cells = [cell.strip() for cell in line.split(delimiter) if cell.strip()]
            else:
                # Split on 2+ spaces (handles variable-width columns)
                cells = [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
            
            # Normalize to TSV format (tabs between cells)
            formatted_rows.append("\t".join(cells))
        
        return "\n".join(formatted_rows)


    def _render_docx(self, path: Path) -> PageRendererResult:
        """
        Extract structured content from a DOCX (Word) document.
        
        DOCX extraction is simpler than PDF because Word documents have explicit
        paragraph structure. We extract paragraphs and detect headings heuristically
        (by uppercase ratio and trailing colons). The heading context is tracked
        to provide section headings for downstream blocks.
        
        Args:
            path: Path to DOCX file
        
        Returns:
            PageRendererResult with extracted text, single page layout, and blocks
        
        Raises:
            KnowledgeIngestionError: If python-docx is not installed or file cannot be opened
        """
        if DocxDocument is None:
            raise KnowledgeIngestionError("DOCX ingestion requires the python-docx package.")
        try:
            document = DocxDocument(str(path))
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open DOCX for layout parsing: {exc}") from exc

        # Extract paragraphs (Word's atomic text units)
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        text = "\n".join(paragraphs)
        blocks: list[PageBlockPayload] = []
        heading_context: list[str] = []  # Track heading hierarchy for section context
        
        # Process each paragraph, detecting headings and building context
        for idx, paragraph in enumerate(paragraphs):
            stripped = paragraph.strip()
            block_type = KnowledgeBlockType.PARAGRAPH
            
            # Detect headings heuristically (Word doesn't always expose style info)
            if self._looks_like_heading(stripped):
                block_type = KnowledgeBlockType.HEADING
                heading_context = [stripped]  # Reset context to this heading
                section_heading = stripped
            else:
                # Use most recent heading as section context
                section_heading = heading_context[-1] if heading_context else ""
            
            blocks.append(
                PageBlockPayload(
                    block_type=block_type,
                    order_index=idx,
                    text=paragraph,
                    section_heading=section_heading,
                    heading_path=list(heading_context),  # Full heading hierarchy
                )
            )
        
        # DOCX is treated as a single page (no explicit page breaks in structure)
        # Use standard page dimensions for density calculation
        page = PageLayout(
            page_number=1,
            width=612,  # Standard US Letter width in points
            height=792,  # Standard US Letter height in points
            rotation=0,
            text_density=len(text.strip()) / (612 * 792),
            has_ocr_content=False,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            blocks=blocks,
            metadata={"paragraph_count": len(paragraphs)},
        )
        return PageRendererResult(text=text, pages=[page])

    def _build_pdf_blocks(self, page: Any, page_number: int) -> list[PageBlockPayload]:
        """
        Convert PyMuPDF blocks into structured PageBlockPayload objects.
        
        This method processes raw PDF blocks and enriches them with:
        - Block type classification (paragraph, heading, table, image)
        - Bounding box coordinates
        - Heading context (for section attribution)
        - Reading order (order_index)
        
        The method tracks heading context as it processes blocks, so each
        block knows which section it belongs to. This is critical for
        downstream table detection and entity extraction.
        
        Args:
            page: PyMuPDF page object
            page_number: 1-based page number
        
        Returns:
            List of PageBlockPayload objects with structure and metadata
        """
        try:
            raw_blocks = page.get_text("blocks") or []  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - fallback to full page block
            raw_blocks = []  # Some PDFs don't support block extraction
        
        payloads: list[PageBlockPayload] = []
        heading_context: list[str] = []  # Track heading hierarchy for section context
        
        for order_index, block in enumerate(raw_blocks):
            # Block format: (x0, y0, x1, y1, text, block_no, block_type)
            text_fragment = block[4] if len(block) > 4 else ""
            
            # Extract bounding box coordinates (with safe defaults)
            bbox = {
                "x0": float(block[0]) if len(block) > 0 else 0.0,
                "y0": float(block[1]) if len(block) > 1 else 0.0,
                "x1": float(block[2]) if len(block) > 2 else 0.0,
                "y1": float(block[3]) if len(block) > 3 else 0.0,
            }
            
            stripped = text_fragment.strip()
            
            # Classify block type (table, heading, paragraph, image)
            block_type = self._resolve_block_type(block, stripped)
            
            # Track heading context for section attribution
            if self._looks_like_heading(stripped):
                heading_context = [stripped]  # Reset to this heading
                section_heading = stripped
            else:
                # Use most recent heading as section context
                section_heading = heading_context[-1] if heading_context else ""
            
            payloads.append(
                PageBlockPayload(
                    block_type=block_type,
                    order_index=order_index,
                    text=text_fragment,
                    bbox=bbox,
                    section_heading=section_heading,
                    heading_path=list(heading_context),  # Full heading hierarchy
                    detected_language="",  # Language detection not implemented
                    confidence=None,
                )
            )
        
        # Fallback: if no blocks extracted, create single block from full page text
        # This ensures we always have some content, even from malformed PDFs
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
        """
        Classify a PDF block's type based on content patterns.
        
        This heuristic classification helps downstream processing:
        - Tables: Detected early for specialized handling
        - Headings: Used for section context and hierarchy
        - Images: Empty blocks are likely images
        - Paragraphs: Default for regular text
        
        Args:
            block: Raw PDF block tuple
            text_fragment: Extracted text from the block
        
        Returns:
            Block type constant (TABLE, HEADING, PARAGRAPH, IMAGE)
        """
        stripped = (text_fragment or "").strip()
        if not stripped:
            return KnowledgeBlockType.IMAGE  # Empty block = likely image

        # Strong table signals: pipes / tabs / multi-spaces in the first line
        # Check first line only (tables often have header row with delimiters)
        first_line = stripped.splitlines()[0] if "\n" in stripped else stripped
        looks_tabular = ("|" in first_line) or ("\t" in first_line) or bool(re.search(r"\s{2,}", first_line))
        if looks_tabular:
            return KnowledgeBlockType.TABLE

        # Headings: short and mostly uppercase or trailing colon
        # These patterns are common in document structure
        if stripped.endswith(":"):
            return KnowledgeBlockType.HEADING
        if stripped.isupper() and len(stripped) < 80:
            return KnowledgeBlockType.HEADING

        # Default: regular paragraph text
        return KnowledgeBlockType.PARAGRAPH

    @staticmethod
    def _looks_like_heading(content: str) -> bool:
        """
        Heuristically detect if content looks like a heading.
        
        Headings are typically:
        - Short (≤80 chars)
        - End with colon (section markers)
        - Mostly uppercase (60%+ uppercase ratio)
        
        This is used for section context tracking when explicit heading
        styles aren't available (e.g., in plain text or malformed PDFs).
        
        Args:
            content: Text to check
        
        Returns:
            True if content appears to be a heading
        """
        if not content:
            return False
        stripped = content.strip()
        if len(stripped) > 80:
            return False  # Too long to be a heading
        if stripped.endswith(":"):
            return True  # Colon is strong heading signal
        # Check uppercase ratio (headings are often ALL CAPS or Title Case)
        uppercase_ratio = sum(1 for c in stripped if c.isupper()) / max(len(stripped), 1)
        return uppercase_ratio > 0.6  # 60%+ uppercase = likely heading


class TableDetector:
    """
    Heuristic table extraction using pattern-based detection.
    
    This class detects tables in documents by analyzing text patterns rather than
    geometric layout. It looks for common table indicators:
    - Pipe-separated values (|)
    - Tab-separated values (\t)
    - Multiple spaces (column alignment)
    - Block type markers (from PageRenderer)
    
    This is a lightweight alternative to advanced table detectors (Camelot,
    pdfplumber) that don't require additional dependencies. Designed to be
    easily swapped with more capable detectors later.
    
    The detector works on PageBlockPayload objects, which already have some
    structure from layout extraction. It's particularly effective for simple
    tables in PDFs and DOCX files.
    """

    def detect_tables(
        self,
        pages: list[PageLayout],
        *,
        format_hint: str | None = None,
    ) -> tuple[list[TablePayload], list[IssuePayload]]:
        """
        Detect tables across all pages in a document.
        
        Scans page blocks for table-like patterns and converts them to
        TablePayload objects with normalized column schemas and cell values.
        
        Args:
            pages: List of page layouts to scan
            format_hint: Optional format hint (currently unused, reserved for
                format-specific detection strategies)
        
        Returns:
            Tuple of (detected tables, issues encountered)
        """
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
        """
        Determine if a page block contains table-like content.
        
        This method uses multiple heuristics:
        1. Block type marker (if PageRenderer already classified it as TABLE)
        2. Pattern detection (pipes, tabs, multiple spaces)
        3. False positive filtering (rejects bullet lists that look like 2-column tables)
        
        The bullet list rejection is important because bullet lists often have
        2 columns (bullet + text) which can be mistaken for tables.
        
        Args:
            block: Page block to check
        
        Returns:
            True if block appears to contain tabular data
        """
        text = block.text or ""
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False  # Need at least header + one data row
        
        # Fast path: block was already classified as table by PageRenderer
        if block.block_type == KnowledgeBlockType.TABLE:
            return True

        # False positive filter: reject bullet lists
        # Bullet lists often look like 2-column tables (bullet | text)
        # Check if first 3 lines all start with bullet characters
        heads = ["".join(line.strip().split()[:1]) for line in lines[:3] if line.strip()]
        bullet_heads = {"*", "**", "***", "****", "*****", "-", "•", "—"}
        if heads and all(h in bullet_heads for h in heads):
            return False  # This is a bullet list, not a table

        # Pattern detection: look for table delimiters in first line
        # These are strong indicators of tabular structure
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
        """
        Convert a table-like page block into a structured TablePayload.
        
        This method parses the block text, detects the delimiter, extracts rows,
        normalizes column headers, and builds cell payloads with normalized values.
        It also validates table structure (column counts, header presence) and
        generates issues for problems found.
        
        Args:
            block: Page block containing table text
            page_number: Page where table appears
            order_index: Table order in document (1-based)
            section_heading: Section heading that contains this table
        
        Returns:
            Tuple of (TablePayload, list of issues)
        """
        lines = [line for line in (block.text or "").splitlines() if line.strip()]
        
        # Auto-detect delimiter from first line
        delimiter = self._detect_delimiter(lines[0])
        
        # Split each line into cells
        rows = [self._split_row(line, delimiter) for line in lines]
        
        # First row is typically the header
        header = rows[0] if rows else []
        issues: list[IssuePayload] = []
        
        # Validate header presence (tables work without headers, but it's less ideal)
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
        
        # Normalize column headers to canonical names (e.g., "Interest Rate" -> "interest_rate")
        column_schema = [self._normalize_header_cell(cell, idx) for idx, cell in enumerate(header)]
        
        # Determine expected column count (from header or first data row)
        expected_columns = len(column_schema) or len(rows[1]) if len(rows) > 1 else 0
        
        table_rows: list[TableRowPayload] = []
        for idx, row_cells in enumerate(rows):
            row_index = idx
            normalized_cells: list[TableCellPayload] = []
            
            # Validate column count (mismatches indicate malformed table)
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
            
            # Build cell payloads with normalized values
            for col_idx, cell_text in enumerate(row_cells):
                # Use normalized column key if available, otherwise generate one
                column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                
                # Extract structured values (amounts, percentages, ranges)
                normalized_value = self._normalize_cell_value(cell_text)
                
                normalized_cells.append(
                    TableCellPayload(
                        row_index=row_index,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=cell_text,
                        normalized_value=normalized_value,
                        bbox=block.bbox,  # Use block bbox (cell-level bboxes not available)
                        confidence=None,
                    )
                )
            
            table_rows.append(
                TableRowPayload(
                    row_index=row_index,
                    page_number=page_number,
                    bbox=block.bbox,
                    raw_text=" | ".join(row_cells),  # Pipe-separated for display
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
        """
        Detect the column delimiter used in table text.
        
        Priority order: pipes > tabs > multiple spaces
        This matches common table formats (Markdown, TSV, space-aligned).
        
        Args:
            sample: First line of table text (typically header row)
        
        Returns:
            Detected delimiter string
        """
        if "|" in sample:
            return "|"  # Markdown-style tables
        if "\t" in sample:
            return "\t"  # TSV format
        return "  "  # Space-aligned columns (2+ spaces)

    @staticmethod
    def _split_row(line: str, delimiter: str) -> list[str]:
        """
        Split a table row line into individual cells.
        
        Handles space-aligned columns specially (uses regex to split on 2+ spaces)
        because space alignment can have variable-width columns.
        
        Args:
            line: Table row text
            delimiter: Column delimiter to use
        
        Returns:
            List of cell text values (stripped of whitespace)
        """
        if delimiter == "  ":
            # Space-aligned: split on 2+ spaces (handles variable-width columns)
            return [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
        # Fixed delimiter: simple split
        return [cell.strip() for cell in line.split(delimiter)]

    @staticmethod
    def _normalize_header_cell(cell: str, index: int) -> str:
        """
        Normalize a table header cell to a canonical column name.
        
        This converts human-readable headers like "Interest Rate" or "Card Type"
        into normalized keys like "interest_rate" or "card_type". The normalization:
        - Lowercases everything
        - Replaces spaces/special chars with underscores
        - Handles letter-by-letter spacing ("W H I T E" -> "white")
        - Preserves currency symbols and percentages
        
        Normalized names are used as column keys throughout the system for
        consistent lookup and querying.
        
        Args:
            cell: Raw header cell text
            index: Column index (for fallback naming)
        
        Returns:
            Normalized column name (e.g., "interest_rate") or "column_N" if empty
        """
        raw = (cell or "").strip()

        # Handle letter-by-letter spacing (common in PDFs with wide character spacing)
        # Example: "W H I T E" -> "WHITE"
        if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]", raw):
            raw = raw.replace(" ", "")

        # Normalize to snake_case: lowercase, replace special chars with underscores
        # Preserve currency symbols and percentages for semantic value
        s = re.sub(r"\s+", " ", raw)  # Collapse multiple spaces
        s = s.lower()
        s = re.sub(r"[^a-z0-9%$€£]+", "_", s)  # Replace non-alphanumeric with underscore
        s = re.sub(r"_+", "_", s).strip("_")  # Collapse multiple underscores
        return s or f"column_{index+1}"  # Fallback if empty


    @staticmethod
    def _normalize_cell_value(cell: str) -> dict[str, Any]:
        """
        Extract structured data from a table cell's raw text.
        
        This method performs sophisticated value extraction to convert text
        like "EGP 100-200" or "3.99%" into structured data that can be
        queried semantically. This is critical for RAG: instead of just
        searching for text, we can search for "amounts > 150" or "rates < 5%".
        
        Extracts:
        - Percentages: "3.99%" → {"percent": 0.0399}
        - Currency amounts: "EGP 250" → {"amount": 250.0, "currency": "EGP"}
        - Ranges: "EGP 100-200" → {"range": {"min": 100, "max": 200}, "currency": "EGP"}
        - Minimum amounts: "min. EGP 100" → {"min": {"amount": 100, "currency": "EGP"}}
        - Plain numbers: "42" → {"number": 42.0}
        
        Args:
            cell: Raw cell text from table extraction
        
        Returns:
            Dictionary with normalized values (empty if no structured data found)
        """
        text = (cell or "")
        # Normalize whitespace: replace Unicode thin/non-breaking spaces with regular spaces
        # This handles international formatting while preserving decimal separators
        text = text.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ")
        text = " ".join(text.strip().split())
        if not text:
            return {}

        out: dict[str, Any] = {}

        # Extract percentages first (they often appear with currency, so check before currency)
        # Pattern matches "3.99%" or "2 %" (with optional space)
        m_pct = re.search(r"(\d+(?:[\.,]\d+)?)\s*%", text)
        if m_pct:
            try:
                # Handle both comma and dot as decimal separator (international formats)
                pct_val = float(m_pct.group(1).replace(",", "."))
                out["percent"] = pct_val / 100.0  # Convert to decimal (0.0399 for 3.99%)
            except ValueError:
                pass

        # Currency normalization: map variants to standard codes
        # This handles regional variations (e.g., "LE" = Egyptian Pound = "EGP")
        currency_alias = {
            "EG£": "EGP",  # Egyptian Pound variant
            "LE": "EGP",   # Egyptian Pound abbreviation
        }
        currency_codes = r"(EGP|EG£|USD|EUR|AED|SAR|GBP|LE)"
        symbol = r"[$€£]"  # Currency symbols

        # Ranges: "EGP 100–200" / "EGP 100-200" / "100–200 EGP"
        m_range = re.search(
            rf"(?:(?:{currency_codes}|{symbol})\s*)?([+-]?\d[\d,]*(?:\.\d+)?)\s*[-–]\s*([+-]?\d[\d,]*(?:\.\d+)?)(?:\s*(?:{currency_codes}|{symbol}))?",
            text,
        )
        if m_range:
            try:
                a = float(m_range.group(2 if m_range.group(2) else 1).replace(",", ""))
            except Exception:
                a = None
            try:
                b = float(m_range.group(3 if m_range.group(3) else 2).replace(",", ""))
            except Exception:
                b = None
            cur_match = re.search(rf"{currency_codes}|{symbol}", text)
            cur = (cur_match.group(0) if cur_match else "").upper()
            cur = currency_alias.get(cur, cur or "")
            if a is not None and b is not None:
                out["range"] = {"min": min(a, b), "max": max(a, b)}
                if cur:
                    out["currency"] = "EGP" if cur in {"EG£", "LE"} else cur

        # Currency amount (single)
        m_amt = re.search(
            rf"(?:{currency_codes}|{symbol})\s*([+-]?\d[\d,]*(?:\.\d+)?)", text, flags=re.IGNORECASE
        )
        if m_amt:
            try:
                amount = float(m_amt.group(2).replace(",", "")) if m_amt.lastindex and m_amt.lastindex >= 2 else float(m_amt.group(1).replace(",", ""))
                cur_match = re.search(rf"{currency_codes}|{symbol}", text, flags=re.IGNORECASE)
                cur = (cur_match.group(0).upper() if cur_match else "") or ""
                cur = currency_alias.get(cur, cur)
                if cur in {"EG£", "LE"}:
                    cur = "EGP"
                if cur:
                    out["currency"] = cur
                out["amount"] = amount
            except ValueError:
                pass

        # Minimum amount (e.g., "min. EGP 100")
        m_min = re.search(
            rf"\bmin(?:imum)?\.?\s+(?:{currency_codes}|{symbol})\s*([+-]?\d[\d,]*(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if m_min:
            try:
                amount = float(m_min.group(2).replace(",", "")) if m_min.lastindex and m_min.lastindex >= 2 else float(m_min.group(1).replace(",", ""))
                cur_match = re.search(rf"{currency_codes}|{symbol}", text, flags=re.IGNORECASE)
                cur = (cur_match.group(0).upper() if cur_match else "") or ""
                cur = currency_alias.get(cur, cur)
                if cur in {"EG£", "LE"}:
                    cur = "EGP"
                out["min"] = {"amount": amount, "currency": cur or out.get("currency")}
            except ValueError:
                pass

        # Fallback number (no currency)
        if "amount" not in out and "range" not in out:
            m_num = re.search(r"([+-]?\d[\d,]*(?:\.\d+)?)", text)
            if m_num:
                try:
                    out["number"] = float(m_num.group(1).replace(",", ""))
                except ValueError:
                    pass

        return out


# ============================================================================
# Geometry-Based Table Reconstruction
# ============================================================================
# This class reconstructs tables from PDF coordinate data by clustering
# text spans into rows and columns. It's more accurate than heuristic
# detection for complex layouts but requires PyMuPDF and coordinate data.

class GeometryTableReconstructor:
    """
    Reconstructs tables from PDF coordinate data using spatial clustering.
    
    This class implements a geometry-first approach to table extraction:
    1. Cluster text spans by Y position (rows) and X position (columns)
    2. Detect header rows using font size, uppercase ratio, and keywords
    3. Build table structure from clustered spans
    4. Handle page breaks by propagating headers across pages
    
    This approach is more accurate than heuristic detection because it uses
    actual PDF coordinates rather than text patterns. It can handle:
    - Complex multi-column layouts
    - Rotated or skewed text
    - Tables spanning multiple pages
    - Irregular column widths
    
    The reconstruction uses tolerance values to group spans that are "close enough"
    to be considered in the same row/column. This handles slight misalignment
    common in PDFs.
    
    Attributes:
        y_tol: Vertical tolerance for row clustering (default 6.0 points)
        x_tol_min: Minimum horizontal tolerance for column clustering (default 6.0)
        header_keywords: Optional keywords to help identify header rows
    """
    def __init__(
        self,
        y_tol: float = 6.0,
        x_tol_min: float = 6.0,
        header_keywords: Optional[Iterable[str]] = None,  # optional, default generic
    ):
        """
        Initialize geometry table reconstructor.
        
        Args:
            y_tol: Vertical tolerance in points for grouping spans into rows.
                Spans within this distance are considered on the same row.
            x_tol_min: Minimum horizontal tolerance for column clustering.
                Actual tolerance is max(x_tol_min, 1% of page width).
            header_keywords: Optional keywords to help identify header rows.
                If provided, rows containing these keywords are more likely
                to be identified as headers.
        """
        self.y_tol = y_tol
        self.x_tol_min = x_tol_min
        self.header_keywords = {k.strip().lower() for k in header_keywords} if header_keywords else set()

    @staticmethod
    def _mostly_numeric_or_amount(s: str) -> bool:
        """
        Determine if a cell value appears to be numeric/currency/percent data.
        
        This heuristic helps distinguish data rows from header rows. Data rows
        typically contain numbers, amounts, or percentages, while headers are
        mostly text. Used in header detection to avoid misclassifying data rows.
        
        Args:
            s: Cell text to check
        
        Returns:
            True if cell appears numeric-heavy (typical for data rows)
        """
        if not s:
            return False
        # Normalize Unicode spaces
        t = s.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ").strip()
        
        # Strong signals: contains digits AND (percentages OR currency symbols)
        if re.search(r"\d", t) and (re.search(r"[%\d]", t) or re.search(r"[$€£]|EGP|USD|EUR|AED|SAR|GBP|LE", t, re.I)):
            return True
        
        # General heuristic: more digits than letters = likely numeric
        letters = sum(c.isalpha() for c in t)
        digits = sum(c.isdigit() for c in t)
        return digits > 0 and digits >= letters

    # ---- Clustering helpers ----
    @staticmethod
    def _greedy_cluster(values: list[tuple[float, int]], tol: float) -> list[list[int]]:
        """
        Cluster values that are within tolerance of each other.
        
        This is a greedy clustering algorithm: values within tolerance of
        the current cluster anchor are added to that cluster. When a value
        exceeds tolerance, a new cluster is started.
        
        Used for grouping spans into rows (Y clustering) and columns (X clustering).
        The tolerance accounts for slight misalignment in PDF coordinates.
        
        Args:
            values: List of (coordinate_value, index) tuples, sorted by coordinate
            tol: Tolerance for clustering (values within this distance are grouped)
        
        Returns:
            List of clusters, where each cluster is a list of indices
        """
        clusters: list[list[int]] = []
        if not values:
            return clusters
        current = [values[0][1]]  # Start first cluster with first value
        anchor = values[0][0]  # Anchor is the coordinate value
        
        for val, idx in values[1:]:
            if abs(val - anchor) <= tol:
                # Within tolerance - add to current cluster
                current.append(idx)
            else:
                # Exceeds tolerance - start new cluster
                clusters.append(current)
                current = [idx]
                anchor = val
        clusters.append(current)  # Don't forget the last cluster
        return clusters

    def _cluster_rows(self, spans: list[PdfSpan]) -> list[list[int]]:
        """
        Cluster spans into rows based on vertical position (Y coordinates).
        
        Spans with similar Y centers are grouped into the same row. This is
        the first step in geometry-based table reconstruction.
        
        Args:
            spans: List of PDF spans with coordinates
        
        Returns:
            List of row clusters, where each cluster is a list of span indices
        """
        sorted_by_y = sorted(((s.y_center, i) for i, s in enumerate(spans)), key=lambda t: t[0])
        return self._greedy_cluster(sorted_by_y, self.y_tol)

    def _cluster_columns(self, spans: list[PdfSpan], page_width: float) -> list[list[int]]:
        """
        Cluster spans into columns based on horizontal position (X coordinates).
        
        Column tolerance is adaptive: uses max of minimum tolerance or 1% of
        page width. This handles both narrow and wide tables correctly.
        
        Args:
            spans: List of PDF spans with coordinates
            page_width: Page width in points (for adaptive tolerance)
        
        Returns:
            List of column clusters, where each cluster is a list of span indices
        """
        # Adaptive tolerance: at least x_tol_min, or 1% of page width (whichever is larger)
        # This ensures columns are detected correctly on both narrow and wide pages
        x_tol = max(self.x_tol_min, page_width * 0.01)  # ~1% of page width
        sorted_by_x = sorted(((s.x_center, i) for i, s in enumerate(spans)), key=lambda t: t[0])
        return self._greedy_cluster(sorted_by_x, x_tol)

    # ---- Header detection ----
    def _is_header_row(self, cell_texts: list[str], avg_font: float, page_font_median: float) -> bool:
        """
        Determine if a table row is a header row using multiple heuristics.
        
        Header detection is critical for table understanding: headers provide
        column semantics and enable structured queries. This method combines
        multiple signals because no single signal is reliable:
        
        1. Font size: Headers are often larger (≥12% larger than page median)
        2. Uppercase ratio: Headers often use ALL CAPS or Title Case (>60% caps)
        3. Trailing colon: Headers sometimes end with colons (e.g., "Name:")
        4. Non-numeric content: Headers are typically text, not numbers
        5. Domain keywords: Optional business-specific header keywords
        
        The method is conservative: requires strong signals to avoid false positives.
        This prevents data rows from being misclassified as headers.
        
        Args:
            cell_texts: Text content of each cell in the row
            avg_font: Average font size for this row
            page_font_median: Median font size for the entire page
        
        Returns:
            True if row appears to be a header based on combined signals
        """
        text = " ".join(cell_texts).strip()
        if not text:
            return False

        stripped = text.replace("\t", " ").strip()
        letters = sum(1 for c in stripped if c.isalpha())
        uppers  = sum(1 for c in stripped if c.isupper())
        digits  = sum(1 for c in stripped if c.isdigit())

        caps_ratio   = (uppers / letters) if letters else 0.0
        digit_ratio  = (digits / max(1, len([c for c in stripped if c.isalnum()])))
        colon        = stripped.endswith(":")

        size_boost = (avg_font > 0 and page_font_median > 0 and (avg_font >= page_font_median * 1.12))

        non_numeric_cells = sum(1 for t in cell_texts if t and not self._mostly_numeric_or_amount(t))
        non_numeric_ratio = non_numeric_cells / max(1, len(cell_texts))

        keyword_hit = False
        if self.header_keywords:
            low = stripped.lower()
            keyword_hit = any(k in low for k in self.header_keywords)

        # Combine signals (tuned to be conservative):
        # - any strong layout signal, or majority non-numeric cells with low digit density
        if size_boost:
            return True
        if colon:
            return True
        if caps_ratio > 0.6:
            return True
        if non_numeric_ratio >= 0.6 and digit_ratio < 0.35:
            return True
        if keyword_hit:
            return True
        return False

    # ---- Build tables for a single page ----
        # ---- Build tables for a single page ----
        # ---- Build tables for a single page ----
    def _build_page_tables(
            self,
            page_number: int,
            page_width: float,
            page_spans: list[PdfSpan],
            page_layout: PageLayout,
            order_offset: int,
        ) -> tuple[list[TablePayload], list[IssuePayload], dict]:
            """
            Build geometry-first tables by:
            - clustering spans into row bins (y)
            - deriving column bins from the FIRST QUALIFYING ROW (row-local)
            - allowing 2 columns if header/numeric cues present
            - using union-of-spans bboxes for cells & rows
            - accumulating subsequent rows that map to those bins
            - stopping when rows become too sparse (prevents giant noisy tables)
            """
            issues: list[IssuePayload] = []
            tables: list[TablePayload] = []

            if not page_spans:
                return tables, issues, {"bins": None, "header": None, "schema": None}

            # Page-wide row bins (y)
            row_clusters = self._cluster_rows(page_spans)

            # For header cue only
            font_sizes = [s.size for s in page_spans if s.size]
            page_font_median = statistics.median(font_sizes) if font_sizes else 0.0

            # Helper: compute row-local column bins from spans in the row
            def make_local_bins(row_span_idxs: list[int]) -> list[tuple[float, float]]:
                spans_in_row = [page_spans[i] for i in row_span_idxs]
                if not spans_in_row:
                    return []
                # cluster x-centers within the row
                x_tol = max(self.x_tol_min, page_width * 0.01)
                sorted_by_x = sorted(((s.x_center, j) for j, s in enumerate(spans_in_row)), key=lambda t: t[0])
                clusters = self._greedy_cluster(sorted_by_x, x_tol)
                # convert to (min_x0, max_x1) per bin
                bins: list[tuple[float, float]] = []
                for cl in clusters:
                    members = [spans_in_row[j] for j in cl]
                    if not members:
                        bins.append((0.0, 0.0))
                    else:
                        x0 = min(m.x0 for m in members)
                        x1 = max(m.x1 for m in members)
                        bins.append((float(x0), float(x1)))
                # left→right
                bins.sort(key=lambda b: (b[0], b[1]))
                return bins

            def assign_to_bins(row_span_idxs: list[int], col_bins: list[tuple[float, float]]):
                spans_in_row = [page_spans[i] for i in row_span_idxs]
                row_fonts = [s.size for s in spans_in_row if s.size]
                avg_font = (sum(row_fonts) / len(row_fonts)) if row_fonts else 0.0
                cell_texts: list[str] = []
                cell_bboxes: list[dict[str, float]] = []
                cell_counts: list[int] = []
                for (x_min, x_max) in col_bins:
                    members = [s for s in spans_in_row if (x_min <= s.x_center <= x_max)]
                    members.sort(key=lambda s: (round(s.y_center / 2) * 2, s.x_center))
                    text = " ".join([m.text for m in members if m.text]).strip()
                    if members:
                        bbox = {
                            "x0": float(min(m.x0 for m in members)),
                            "y0": float(min(m.y0 for m in members)),
                            "x1": float(max(m.x1 for m in members)),
                            "y1": float(max(m.y1 for m in members)),
                        }
                    else:
                        bbox = {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}
                    cell_texts.append(text)
                    cell_bboxes.append(bbox)
                    cell_counts.append(len(members))
                return cell_texts, cell_bboxes, cell_counts, avg_font

            # Find anchor row: row that qualifies as table start
            start_row_idx = None
            anchor_bins: list[tuple[float, float]] = []
            header_is_present = False
            header_cells: list[str] = []
            for r_idx, row_span_idxs in enumerate(row_clusters):
                bins = make_local_bins(row_span_idxs)
                if len(bins) < 2:
                    continue
                cell_texts, cell_bboxes, cell_counts, avg_font = assign_to_bins(row_span_idxs, bins)
                populated_cols = sum(1 for t in cell_texts if t)
                has_numeric = any(self._mostly_numeric_or_amount(t) for t in cell_texts if t)
                is_header = self._is_header_row(cell_texts, avg_font, page_font_median)

                # reject obvious bullet-list anchors: col0 is just bullets and col1 is a long paragraph
                first = (cell_texts[0] or "").strip()
                second = (cell_texts[1] or "").strip() if len(cell_texts) > 1 else ""
                if re.fullmatch(r"(\*{1,5}|[-•—])+\.?", first) and len(second) >= 60:
                    continue

                # Guard against "repeating labels" grids (e.g., VALID THRU/dates repeated across columns)
                short_repeats = sum(1 for t in cell_texts if 0 < len(t.strip()) <= 12)
                distinct = len({t.strip().lower() for t in cell_texts if t.strip()})
                repetitive = (distinct <= max(2, len(cell_texts)//4)) and (short_repeats >= len(cell_texts)//2)
                
                qualifies = ((populated_cols >= 3) or (populated_cols >= 2 and (is_header or has_numeric))) and not repetitive
                if not qualifies:
                    continue

                start_row_idx = r_idx
                anchor_bins = bins
                header_is_present = is_header
                header_cells = cell_texts[:] if is_header else []
                break

            if start_row_idx is None:
                return tables, issues, {"bins": None, "header": None, "schema": None}

            # Build schema
            if header_is_present and any(c.strip() for c in header_cells):
                schema = [
                    TableDetector._normalize_header_cell(raw, idx) or f"column_{idx+1}"
                    for idx, raw in enumerate(header_cells)
                ]
            else:
                schema = [f"column_{i+1}" for i in range(len(anchor_bins))]

            # Collect rows
            order_index = order_offset + 1
            table_rows: list[TableRowPayload] = []

            # Header row payload (if present)
            if header_is_present:
                h_texts, h_bboxes, h_counts, h_font = assign_to_bins(row_clusters[start_row_idx], anchor_bins)
                header_cells_payload: list[TableCellPayload] = []
                for c_idx, (raw, bbox) in enumerate(zip(h_texts, h_bboxes)):
                    header_cells_payload.append(
                        TableCellPayload(
                            row_index=0,
                            column_index=c_idx,
                            column_key=schema[c_idx] if c_idx < len(schema) else f"column_{c_idx+1}",
                            raw_text=raw,
                            normalized_value=self._normalize_cell_value(raw),
                            bbox=bbox,
                            confidence=None,
                            metadata={"span_count": h_counts[c_idx]},
                        )
                    )
                table_rows.append(
                    TableRowPayload(
                        row_index=0,
                        page_number=page_number,
                        bbox=_union_bbox(h_bboxes),
                        raw_text=" | ".join(h_texts),
                        metadata={"row_type": "header"},
                        cells=header_cells_payload,
                    )
                )

            # Data rows (including anchor row if it wasn't header)
            # Sparsity control: stop growing table when rows become too empty
            sparse_streak = 0
            SPARSE_ROW_MAX_EMPTY_RATIO = 0.7  # tweakable: 70% or more cells empty = sparse
            SPARSE_STREAK_LIMIT = 5           # tweakable: stop after 5 consecutive sparse rows
            
            next_row_idx = 1 if header_is_present else 0
            data_start = start_row_idx + (1 if header_is_present else 0)
            
            for r_idx in range(data_start, len(row_clusters)):
                texts, bboxes, counts, _ = assign_to_bins(row_clusters[r_idx], anchor_bins)
                
                # skip totally empty
                if not any(t.strip() for t in texts):
                    continue
                
                # Calculate sparsity: what fraction of cells are empty?
                empty_ratio = 1.0 - (sum(1 for t in texts if t.strip()) / max(1, len(texts)))
                
                if empty_ratio >= SPARSE_ROW_MAX_EMPTY_RATIO:
                    sparse_streak += 1
                    if sparse_streak >= SPARSE_STREAK_LIMIT:
                        # Stop table: we've entered a different layout/section
                        break
                else:
                    # Reset streak when we hit a non-sparse row
                    sparse_streak = 0
                
                # Build cell payloads for this row
                cells_payload: list[TableCellPayload] = []
                for c_idx, (raw, bbox) in enumerate(zip(texts, bboxes)):
                    cells_payload.append(
                        TableCellPayload(
                            row_index=next_row_idx,
                            column_index=c_idx,
                            column_key=schema[c_idx] if c_idx < len(schema) else f"column_{c_idx+1}",
                            raw_text=raw,
                            normalized_value=self._normalize_cell_value(raw),
                            bbox=bbox,
                            confidence=None,
                            metadata={"span_count": counts[c_idx]},
                        )
                    )
                
                table_rows.append(
                    TableRowPayload(
                        row_index=next_row_idx,
                        page_number=page_number,
                        bbox=_union_bbox(bboxes),
                        raw_text=" | ".join(texts),
                        metadata={"row_type": "data"},
                        cells=cells_payload,
                    )
                )
                next_row_idx += 1

            table_payload = TablePayload(
                order_index=order_index,
                title=page_layout.section_heading if getattr(page_layout, "section_heading", "") else f"Table {order_index}",
                section_heading=getattr(page_layout, "section_heading", "") or "",
                page_number=page_number,
                bbox=_union_bbox([row.bbox for row in table_rows]) if table_rows else {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0},
                column_schema=schema,
                data_dictionary={},
                metadata={"detected_via": "geometry", "col_bins": anchor_bins, "start_row_idx": start_row_idx},
                rows=table_rows,
            )
            tables.append(table_payload)

            meta = {"bins": anchor_bins, "header": schema if header_is_present else None, "schema": schema}
            return tables, issues, meta


    # ---- Header propagation across pages ----
    @staticmethod
    def _bins_compatible(prev_bins: list[tuple[float, float]] | None, next_bins: list[tuple[float, float]] | None, tol: float = 8.0) -> bool:
        """
        Check if column bins from two pages are compatible (same table structure).
        
        When a table spans multiple pages, the column positions should be similar.
        This method checks if the column bins (x-coordinate ranges) align within
        tolerance. If they do, it indicates the table continues across the page break,
        and we can propagate the header from the previous page.
        
        The 8-point tolerance accounts for slight PDF rendering variations between
        pages while still catching genuine table continuations.
        
        Args:
            prev_bins: Column bins from previous page [(x0_min, x1_max), ...]
            next_bins: Column bins from next page
            tol: Position tolerance in points (default 8.0)
        
        Returns:
            True if bins are compatible (same count and positions within tolerance)
        """
        if not prev_bins or not next_bins or len(prev_bins) != len(next_bins):
            return False  # Different column counts = different tables
        # Check each column position is within tolerance
        for (a0, a1), (b0, b1) in zip(prev_bins, next_bins):
            if max(abs(a0 - b0), abs(a1 - b1)) > tol:
                return False  # Column position mismatch
        return True

    def reconstruct(self, page_spans: list[list[PdfSpan]], pages: list[PageLayout]) -> tuple[list[TablePayload], list[IssuePayload]]:
        """
        Reconstruct tables from PDF spans across multiple pages.
        
        This is the main entry point for geometry-based table reconstruction.
        It processes each page independently, then connects tables that span
        page breaks by detecting compatible column structures.
        
        Header propagation: When a table continues across pages, the header
        from the first page is propagated to subsequent pages. This is detected
        by comparing column bin positions and schemas. This ensures multi-page
        tables have consistent headers for proper semantic understanding.
        
        Args:
            page_spans: List of span lists (one per page) with coordinate data
            pages: Page layout information (width, height, etc.)
        
        Returns:
            Tuple of (reconstructed tables, issues encountered)
        """
        all_tables: list[TablePayload] = []
        all_issues: list[IssuePayload] = []
        # Track previous page state for header propagation
        prev_bins: list[tuple[float, float]] | None = None
        prev_order_index = 0
        prev_schema: list[str] | None = None

        for p_idx, (spans, layout) in enumerate(zip(page_spans, pages), start=1):
            width = float(getattr(layout, "width", 612.0) or 612.0)
            # Build tables for this page independently
            page_tables, page_issues, meta = self._build_page_tables(
                page_number=p_idx,
                page_width=width,
                page_spans=spans,
                page_layout=layout,
                order_offset=len(all_tables),  # Offset ensures unique order_index across pages
            )
            
            # Header propagation: detect table continuation across page breaks
            # This happens when column positions and schemas match between pages
            if page_tables:
                bins = meta.get("bins")
                schema = page_tables[0].column_schema
                
                # Check if this table continues from previous page
                # Bins must align (same column positions) and schema must match
                if self._bins_compatible(prev_bins, bins) and schema == prev_schema:
                    # Mark as continuation and propagate header metadata
                    page_tables[0].metadata["header_propagated"] = True
                    page_tables[0].metadata["continuation_of"] = prev_order_index
                    all_issues.append(
                        IssuePayload(
                            code="header_propagated",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Header propagated across page break.",
                            page_number=p_idx,
                            table_order_index=page_tables[0].order_index,
                            details={}
                        )
                    )
                
                # Update state for next page comparison
                prev_bins = bins
                prev_order_index = page_tables[0].order_index
                prev_schema = schema

            all_tables.extend(page_tables)
            all_issues.extend(page_issues)
        return all_tables, all_issues

    # Reuse existing normalization for consistency
    _normalize_cell_value = staticmethod(TableDetector._normalize_cell_value)


@dataclass(frozen=True)
class IngestionJobResult:
    job_id: uuid.UUID
    upload_id: uuid.UUID
    job_type: KnowledgeIngestionJobType
    status: KnowledgeIngestionJobStatus
    characters: int
    error: str | None = None


def queue_ingestion_job(upload: KnowledgeUpload, *, trigger: str = "upload", force: bool = False) -> KnowledgeIngestionJob | None:
    """
    Queue an ingestion job for a knowledge upload.
    
    This is the main entry point for initiating document ingestion. It creates
    a KnowledgeIngestionJob that will be processed by a worker. The job
    includes rate limiting logic: if too many jobs are active for a business,
    new jobs are marked as DEFERRED until capacity is available.
    
    The function is idempotent: if a job already exists and is queued/running,
    it returns the existing job unless force=True.
    
    Args:
        upload: Knowledge upload to process
        trigger: Trigger source identifier (e.g., "upload", "api", "sync")
        force: If True, cancel existing job and create new one
    
    Returns:
        Created or existing KnowledgeIngestionJob, or None if source type
        doesn't require ingestion (e.g., raw binary files)
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

    active_limit = max(0, int(getattr(settings, "INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS", 0)))
    job_status = KnowledgeIngestionJobStatus.QUEUED
    payload: dict[str, object] = {"trigger": trigger}
    if active_limit:
        active_jobs = KnowledgeIngestionJob.objects.filter(
            business_profile=upload.business_profile,
            status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.RUNNING),
            job_type=KnowledgeIngestionJobType.INGEST,
        ).count()
        if active_jobs >= active_limit:
            job_status = KnowledgeIngestionJobStatus.DEFERRED
            payload["rate_limited"] = True

    job = KnowledgeIngestionJob.objects.create(
        business_profile=upload.business_profile,
        upload=upload,
        job_type=KnowledgeIngestionJobType.INGEST,
        status=job_status,
        payload=payload,
    )

    logger.info(
        "Queued ingestion job upload=%s job=%s trigger=%s status=%s",
        upload.id,
        job.id,
        trigger,
        job_status,
    )
    return job


class KnowledgeIngestionService:
    """
    Main orchestration service for knowledge document ingestion.
    
    This service implements the complete ingestion pipeline:
    1. Extract text and structure from files/links (PDF, DOCX, CSV, JSON, etc.)
    2. Detect and normalize tables with value extraction
    3. Extract entities and aliases for lookup
    4. Chunk content for embedding
    5. Generate embeddings (inline or async)
    6. Persist to database (chunks, pages, tables, entities, issues)
    
    The service is designed to run inside a management command or async worker.
    It processes queued KnowledgeIngestionJob objects, extracting and persisting
    content so the RAG orchestrator and dashboard can serve full document context.
    
    Key Features:
    - Multi-format support: PDF, DOCX, CSV, XLSX, JSON, TXT, web links
    - Layout-aware extraction: Preserves structure for better semantic search
    - Table normalization: Extracts structured values (amounts, ranges, percentages)
    - Entity extraction: Builds searchable aliases from structured data
    - Embedding generation: Inline for small docs, async for large ones
    - OCR support: Handles scanned documents with Tesseract
    
    The service uses a tiered extraction strategy:
    - Geometry-based table reconstruction (for complex PDF layouts)
    - Heuristic table detection (fallback for simpler formats)
    - Layout-aware text extraction (preserves reading order and structure)
    
    Attributes:
        media_root: Root directory for uploaded files
        embedding_service: Primary embedding provider
        ocr_reconciler: OCR handler for scanned documents
        page_renderer: Document layout extractor
        table_detector: Heuristic table detector
    """

    def __init__(self, *, media_root: Path | None = None, enable_ocr: bool = True):
        """
        Initialize knowledge ingestion service.
        
        Sets up all extraction components (renderers, detectors, OCR) and
        configures embedding service. Reads settings from Django settings
        for limits, batch sizes, and feature flags.
        
        Args:
            media_root: Root directory for uploaded files. If None, uses
                settings.MEDIA_ROOT (required).
            enable_ocr: Whether to enable OCR support for scanned documents.
                If False, low-density pages will be flagged but not processed.
        
        Raises:
            RuntimeError: If MEDIA_ROOT is not configured
        """
        root = media_root or getattr(settings, "MEDIA_ROOT", None)
        if not root:
            raise RuntimeError("MEDIA_ROOT must be configured for ingestion.")
        self.media_root = Path(root).resolve()
        self.embedding_service = build_embedding_service()
        self._fallback_embedding_service: LocalEmbeddingService | None = None
        self.ingest_inline_chunk_limit = max(0, int(getattr(settings, "INGEST_SYNC_EMBED_CHUNK_LIMIT", 200)))
        self.embedding_batch_size = max(16, int(getattr(settings, "INGEST_EMBED_BATCH_SIZE", 64)))
        self.embedding_job_payload_size = max(self.embedding_batch_size * 4, 256)
        self.ingest_concurrency_limit = max(0, int(getattr(settings, "INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS", 0)))
        self.embedding_backlog_threshold = max(0, int(getattr(settings, "INGEST_EMBEDDING_BACKLOG_THRESHOLD", 500)))
        self.embedding_prewarm_limit = max(0, int(getattr(settings, "INGEST_EMBED_PREWARM_CHUNK_LIMIT", 32)))
        self._fallback_embedding_attempted = False
        
        # NEW: Create OCR reconciler with Tesseract support
        self.ocr_reconciler = create_ocr_reconciler(enable_ocr=enable_ocr)
        
        self.page_renderer = PageRenderer(pymupdf_module=fitz)
        self.table_detector = TableDetector()
        self.default_json_entity_limit = max(
            1,
            int(getattr(settings, "INGEST_MAX_JSON_ENTITIES_DEFAULT", 200)),
        )
        candidate_cap = int(getattr(settings, "INGEST_MAX_JSON_ENTITY_CANDIDATES", 0)) or (
            self.default_json_entity_limit * 4
        )
        self.max_json_entity_candidates = max(self.default_json_entity_limit, candidate_cap)
        self.default_table_max_rows = max(1, int(getattr(settings, "TABLE_MAX_ROWS_DEFAULT", 5000)))
        self.default_table_max_columns = max(0, int(getattr(settings, "TABLE_MAX_COLUMNS_DEFAULT", 0) or 0))
        self.alias_warning_threshold = int(getattr(settings, "INGEST_ALIAS_WARNING_THRESHOLD", 2000))

    # ------------------------------------------------------------------
    # Job coordination

    def _json_entity_limit(self, business_profile) -> int:
        """
        Get the maximum number of JSON entities to extract for a business.
        
        Businesses can override the default limit via metadata. This allows
        high-volume businesses to process more entities while keeping defaults
        reasonable for most use cases.
        
        Args:
            business_profile: Business profile with optional metadata override
        
        Returns:
            Maximum entities to extract (at least 1)
        """
        if not business_profile:
            return self.default_json_entity_limit
        metadata = business_profile.metadata if isinstance(getattr(business_profile, "metadata", None), dict) else {}
        override = metadata.get("ingest_max_json_entities")
        try:
            value = int(override)
            return max(1, value)  # Ensure at least 1
        except (TypeError, ValueError):
            return self.default_json_entity_limit

    def _suppress_list_like_heuristics(
        self, tables: list[TablePayload]
    ) -> tuple[list[TablePayload], list[IssuePayload]]:
        """
        Drop heuristic tables that are actually bullet lists:
        - mainly 2 columns,
        - first col looks like bullets (*, **, -, •, —),
        - second col is long paragraph-like text for the majority of sampled rows.
        """
        out: list[TablePayload] = []
        issues: list[IssuePayload] = []
        bullet_re = re.compile(r"^(\*{1,5}|[-•—])+$")
        for t in tables:
            rows = t.rows or []
            if len(rows) < 2:
                out.append(t)
                continue
            sample = rows[: min(10, len(rows))]
            bullety = 0
            long_second = 0
            examined = 0
            for r in sample:
                cells = r.cells or []
                if not cells:
                    continue
                c0 = (cells[0].raw_text or "").strip() if len(cells) >= 1 else ""
                c1 = (cells[1].raw_text or "").strip() if len(cells) >= 2 else ""
                examined += 1
                if bullet_re.fullmatch(c0):
                    bullety += 1
                if len(c1) >= 60:
                    long_second += 1
            if examined >= 3 and (len(t.column_schema or []) <= 2) and (bullety / examined >= 0.5) and (long_second / examined >= 0.5):
                issues.append(
                    IssuePayload(
                        code="list_promoted_suppressed",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="Heuristic table looked like a bullet/list; suppressed in favor of plain text.",
                        page_number=t.page_number,
                        table_order_index=t.order_index,
                        details={"rows_checked": examined},
                    )
                )
                continue
            out.append(t)
        return out, issues

    def _suppress_sparse_geometry_tables(
        self, tables: list[TablePayload]
    ) -> tuple[list[TablePayload], list[IssuePayload]]:
        """
        Drop geometry tables that are too sparse:
        - >60% of cells are empty across sampled rows,
        - >50% of columns are mostly empty (>60% empty in that column).
        
        This prevents giant noisy tables from low-quality geometry detection.
        """
        out: list[TablePayload] = []
        issues: list[IssuePayload] = []
        
        CELL_EMPTY_THRESHOLD = 0.6      # 60% empty cells
        COLUMN_EMPTY_THRESHOLD = 0.6    # 60% empty in a column = "mostly empty"
        COLUMN_SPARSE_RATIO = 0.5       # 50% of columns mostly empty
        
        for t in tables:
            rows = t.rows or []
            if len(rows) < 2:
                out.append(t)
                continue
            
            # Sample first N rows (excluding header if present)
            sample_size = min(10, len(rows))
            sample = rows[:sample_size]
            
            # Skip if no column schema
            num_cols = len(t.column_schema or [])
            if num_cols == 0:
                out.append(t)
                continue
            
            # Count empty cells overall
            total_cells = 0
            empty_cells = 0
            
            # Track emptiness per column
            column_empty_counts = [0] * num_cols
            column_total_counts = [0] * num_cols
            
            for r in sample:
                cells = r.cells or []
                for c_idx in range(num_cols):
                    if c_idx < len(cells):
                        cell_text = (cells[c_idx].raw_text or "").strip()
                        total_cells += 1
                        column_total_counts[c_idx] += 1
                        
                        if not cell_text:
                            empty_cells += 1
                            column_empty_counts[c_idx] += 1
                    else:
                        # Missing cell counts as empty
                        total_cells += 1
                        empty_cells += 1
                        column_total_counts[c_idx] += 1
                        column_empty_counts[c_idx] += 1
            
            if total_cells == 0:
                out.append(t)
                continue
            
            # Calculate overall empty ratio
            overall_empty_ratio = empty_cells / total_cells
            
            # Calculate per-column empty ratios
            mostly_empty_columns = 0
            for c_idx in range(num_cols):
                if column_total_counts[c_idx] > 0:
                    col_empty_ratio = column_empty_counts[c_idx] / column_total_counts[c_idx]
                    if col_empty_ratio > COLUMN_EMPTY_THRESHOLD:
                        mostly_empty_columns += 1
            
            column_sparse_ratio = mostly_empty_columns / num_cols if num_cols > 0 else 0
            
            # Suppress if both conditions met
            if overall_empty_ratio > CELL_EMPTY_THRESHOLD and column_sparse_ratio > COLUMN_SPARSE_RATIO:
                issues.append(
                    IssuePayload(
                        code="geometry_suppressed_sparse",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"Geometry table was too sparse ({overall_empty_ratio:.1%} empty cells, {column_sparse_ratio:.1%} sparse columns); suppressed.",
                        page_number=t.page_number,
                        table_order_index=t.order_index,
                        details={
                            "rows_checked": sample_size,
                            "overall_empty_ratio": round(overall_empty_ratio, 3),
                            "mostly_empty_columns": mostly_empty_columns,
                            "total_columns": num_cols,
                            "column_sparse_ratio": round(column_sparse_ratio, 3),
                        },
                    )
                )
                continue
            
            out.append(t)
        
        return out, issues

    def process_next_job(self) -> IngestionJobResult | None:
        """
        Process the next queued ingestion job.
        
        This is the main entry point for the ingestion worker. It:
        1. Claims the next queued job (atomic operation)
        2. Routes to appropriate handler (INGEST or EMBED job type)
        3. Extracts and persists content
        4. Marks job as completed or failed
        
        Job types:
        - INGEST: Full document extraction and persistence
        - EMBED: Generate embeddings for previously created chunks
        
        Returns:
            IngestionJobResult with processing status, or None if no jobs available
        
        Note:
            This method is designed to be called in a loop by a worker process.
            Jobs are claimed atomically to prevent duplicate processing.
        """
        job = self._claim_next_job()

        if job is None:
            return None

        # Route to embedding job handler (separate from ingestion)
        if job.job_type == KnowledgeIngestionJobType.EMBED:
            return self._process_embedding_job(job)

        # Main ingestion path: extract and persist document
        upload = job.upload
        logger.info("ingest.start upload=%s job=%s source_type=%s", upload.id, job.id, upload.source_type)
        try:
            # Extract content from file or link
            extraction = self._extract_upload(upload)
            characters = len(extraction.text)
            
            # Persist to database (chunks, pages, tables, entities)
            self._persist_extraction(upload, extraction)
            
            # Mark job complete with statistics
            self._mark_job_completed(job, extra={"characters": characters, "format": extraction.format_hint})
            logger.info("ingest.done upload=%s job=%s chars=%s format=%s", upload.id, job.id, characters, extraction.format_hint)

            return IngestionJobResult(
                job_id=job.id,
                upload_id=upload.id,
                job_type=job.job_type,
                status=KnowledgeIngestionJobStatus.COMPLETED,
                characters=characters,
            )
        except KnowledgeIngestionError as exc:
            self._handle_failure(job, str(exc))
            logger.warning("Ingestion failed upload=%s job=%s error=%s", upload.id, job.id, exc)
            return IngestionJobResult(
                job_id=job.id,
                upload_id=upload.id,
                job_type=job.job_type,
                status=KnowledgeIngestionJobStatus.FAILED,
                characters=0,
                error=str(exc),
            )

    def _process_embedding_job(self, job: KnowledgeIngestionJob) -> IngestionJobResult:
        """
        Process an embedding job to generate vectors for chunks.
        
        This method handles async embedding jobs that were queued when chunks
        couldn't be embedded inline (e.g., due to size limits). It:
        1. Extracts chunk IDs from job payload
        2. Loads chunks from database
        3. Generates embeddings in batches
        4. Updates chunks with embedding vectors
        5. Schedules new jobs if backlog is too high
        
        Embeddings are generated in batches to optimize API calls and handle
        rate limits. The batch size is configurable via settings.
        
        Args:
            job: Embedding job with chunk IDs in payload
        
        Returns:
            IngestionJobResult with processing status and chunk count
        """
        payload = job.payload or {}
        chunk_ids = payload.get("chunk_ids") if isinstance(payload, dict) else []
        normalized_ids: list[uuid.UUID] = []
        # Normalize chunk IDs (handle string UUIDs from JSON)
        for value in chunk_ids or []:
            try:
                normalized_ids.append(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue  # Skip invalid UUIDs
        if not normalized_ids:
            self._mark_job_completed(job, extra={"embedded_chunks": 0})
            return IngestionJobResult(
                job_id=job.id,
                upload_id=job.upload_id,
                job_type=job.job_type,
                status=KnowledgeIngestionJobStatus.COMPLETED,
                characters=0,
            )
        chunks = list(
            KnowledgeUploadChunk.objects.filter(
                id__in=normalized_ids,
                upload=job.upload,
            ).order_by("chunk_index")
        )
        if not chunks:
            self._mark_job_completed(job, extra={"embedded_chunks": 0})
            return IngestionJobResult(
                job_id=job.id,
                upload_id=job.upload_id,
                job_type=job.job_type,
                status=KnowledgeIngestionJobStatus.COMPLETED,
                characters=0,
            )
        provider = self.embedding_service or self._get_fallback_embedding_service()
        if not provider:
            error = "Embedding backend unavailable"
            self._handle_failure(job, error)
            return IngestionJobResult(
                job_id=job.id,
                upload_id=job.upload_id,
                job_type=job.job_type,
                status=KnowledgeIngestionJobStatus.FAILED,
                characters=0,
                error=error,
            )
        updated: list[KnowledgeUploadChunk] = []
        processed = 0
        batched: list[list[KnowledgeUploadChunk]] = [
            chunks[i : i + self.embedding_batch_size] for i in range(0, len(chunks), self.embedding_batch_size)
        ]
        for batch in batched:
            texts = [chunk.content or "" for chunk in batch]
            if not any(texts):
                continue
            try:
                vectors = provider.embed_texts(texts)
            except EmbeddingProviderError as exc:
                self._handle_failure(job, f"Embedding batch failed: {exc}")
                return IngestionJobResult(
                    job_id=job.id,
                    upload_id=job.upload_id,
                    job_type=job.job_type,
                    status=KnowledgeIngestionJobStatus.FAILED,
                    characters=processed,
                    error=str(exc),
                )
            except Exception as exc:  # pragma: no cover - defensive
                self._handle_failure(job, f"Embedding batch exception: {exc}")
                return IngestionJobResult(
                    job_id=job.id,
                    upload_id=job.upload_id,
                    job_type=job.job_type,
                    status=KnowledgeIngestionJobStatus.FAILED,
                    characters=processed,
                    error=str(exc),
                )
            for chunk, vector in zip(batch, vectors):
                normalized = self._normalize_embedding(vector)
                if normalized:
                    chunk.embedding = normalized
                    chunk.updated_at = timezone.now()
                    updated.append(chunk)
                    processed += 1
        processed_ids = [str(chunk.id) for chunk in updated]
        if updated:
            KnowledgeUploadChunk.objects.bulk_update(updated, ["embedding", "updated_at"])
        if processed_ids:
            self._update_upload_embedding_metadata(job.upload, processed_ids=processed_ids)
        self._mark_job_completed(job, extra={"embedded_chunks": processed})
        return IngestionJobResult(
            job_id=job.id,
            upload_id=job.upload_id,
            job_type=job.job_type,
            status=KnowledgeIngestionJobStatus.COMPLETED,
            characters=processed,
        )


    # ------------------------------------------------------------------
    # Extraction path

    def _extract_upload(self, upload: KnowledgeUpload) -> ExtractionResult:
        """
        Extract content from a knowledge upload (file or link).
        
        Routes to appropriate extractor based on source type. This is the
        main extraction entry point that handles all supported formats.
        
        Args:
            upload: Knowledge upload to extract content from
        
        Returns:
            ExtractionResult with text, pages, tables, entities, and issues
        
        Raises:
            KnowledgeIngestionError: If source type is unsupported or metadata is missing
        """
        # File-based sources (uploaded files or integration imports)
        if upload.source_type in {KnowledgeSourceType.FILE, KnowledgeSourceType.INTEGRATION}:
            file_detail = getattr(upload, "file_detail", None)
            # Ensure file_detail is loaded (may not be in queryset)
            if not isinstance(file_detail, KnowledgeUploadFile):
                upload = KnowledgeUpload.objects.select_related("file_detail").get(id=upload.id)
                file_detail = upload.file_detail
            if file_detail is None:
                raise KnowledgeIngestionError("File metadata missing for upload.")
            
            # Get entity limit from business profile (for JSON sources)
            limit = self._json_entity_limit(upload.business_profile)
            return self._extract_from_file(file_detail, upload=upload, entity_limit=limit)

        # Web link sources
        if upload.source_type == KnowledgeSourceType.LINK:
            url_detail = getattr(upload, "url_detail", None)
            url = getattr(url_detail, "url", None) or upload.legacy_url
            if not url:
                raise KnowledgeIngestionError("Link upload missing URL.")
            return self._extract_from_link(url)

        raise KnowledgeIngestionError(f"Ingestion not implemented for {upload.source_type}.")

    def _extract_from_file(
        self,
        file_detail: KnowledgeUploadFile,
        *,
        upload: KnowledgeUpload | None = None,
        entity_limit: int | None = None,
    ) -> ExtractionResult:
        """
        Extract content from an uploaded file.
        
        This method routes to format-specific extractors based on file type.
        It handles security (path validation) and format detection before
        delegating to specialized extractors.
        
        Supported formats:
        - PDF: Layout-aware extraction with table detection
        - DOCX: Paragraph and structure extraction
        - CSV/TSV: Table extraction with normalization
        - XLSX/XLS: Multi-sheet table extraction
        - JSON: Entity extraction with alias detection
        - TXT: Plain text extraction
        
        Args:
            file_detail: File metadata with storage path
            upload: Optional upload object (for table limits, privacy rules)
            entity_limit: Maximum entities to extract (for JSON sources)
        
        Returns:
            ExtractionResult with extracted content
        
        Raises:
            KnowledgeIngestionError: If file not found or path invalid
            UnsupportedFormatError: If format cannot be determined
        """
        # Resolve file path and validate it's within MEDIA_ROOT (security)
        storage_path = Path(file_detail.storage_path)
        absolute = (self.media_root / storage_path).resolve()
        try:
            absolute.relative_to(self.media_root)
        except ValueError as exc:  # pragma: no cover - defensive
            raise KnowledgeIngestionError("File path escapes MEDIA_ROOT.") from exc
        if not absolute.exists():
            raise KnowledgeIngestionError("File not found on disk.")

        # Detect format from extension and content-type
        format_hint = self._detect_format(file_detail)
        if not format_hint:
            raise UnsupportedFormatError(f"Unsupported file type {format_hint or 'unknown'}.")
        logger.info("extract.file.start path=%s format=%s", absolute, format_hint)
        
        # Route to format-specific extractors
        if format_hint == "json":
            limit = entity_limit or self.default_json_entity_limit
            return self._extract_json(absolute, entity_limit=limit)
        if format_hint in {"csv", "tsv"}:
            return self._extract_csv(
                absolute,
                format_hint=format_hint,
                file_detail=file_detail,
                upload=upload,
            )
        if format_hint == "xlsx":
            return self._extract_xlsx(
                absolute,
                file_detail=file_detail,
                upload=upload,
            )
        if format_hint == "xls":
            return self._extract_xls(
                absolute,
                file_detail=file_detail,
                upload=upload,
            )
        layout_result: PageRendererResult | None = None
        should_attempt_layout = not (format_hint == "pdf" and fitz is None)
        if should_attempt_layout:
            try:
                layout_result = self.page_renderer.render(
                    absolute,
                    format_hint=format_hint,
                    ocr=self.ocr_reconciler,
                )
                logger.info("extract.file.result path=%s format=%s pages=%s", absolute, format_hint, len(layout_result.pages))
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

        # ========================================================================
        # Table Detection Strategy: Geometry-first with heuristic fallback
        # ========================================================================
        # We use a two-stage approach:
        # 1. Geometry-based reconstruction: Uses PDF coordinate data to build
        #    tables from spatial clustering. This is more accurate for complex
        #    layouts but requires PyMuPDF and coordinate data.
        # 2. Heuristic detection: Pattern-based detection (pipes, tabs, spaces).
        #    This is a fallback that works on any format but is less accurate.
        #
        # If geometry tables are found, we use them exclusively (they're more
        # accurate). Otherwise, we use heuristic tables but filter out false
        # positives (like bullet lists that look like 2-column tables).
        
        geometry_tables: list[TablePayload] = []
        geom_issues: list[IssuePayload] = []
        if format_hint == "pdf" and fitz is not None:
            try:
                # Extract PDF spans with coordinates (for geometry-based reconstruction)
                # Spans preserve exact position and font info needed for clustering
                page_spans = self.page_renderer.extract_pdf_spans(absolute)
                for idx, spans in enumerate(page_spans, start=1):
                    logger.info("geometry.spans page=%s count=%s", idx, len(spans))
                
                # Reconstruct tables using coordinate clustering
                # This groups spans by Y position (rows) and X position (columns)
                recon = GeometryTableReconstructor()
                geometry_tables, geom_issues = recon.reconstruct(page_spans, layout_result.pages)
                logger.info("geometry.tables path=%s count=%s", absolute, len(geometry_tables))
            except Exception as exc:  # best-effort guard - don't abort on geometry failure
                logger.warning("geometry.reconstruct_failed path=%s err=%s", absolute, exc)
                geometry_tables, geom_issues = [], [
                    IssuePayload(
                        code="geometry_failed",
                        severity=KnowledgeIssueSeverity.ERROR.value,
                        description=str(exc),
                    )
                ]


        # Fallback: Use heuristic table detector (pattern-based)
        # This works on any format but is less accurate than geometry-based
        tables, table_issues = self.table_detector.detect_tables(layout_result.pages)

        # Choose table source: prefer geometry if available, otherwise use heuristic
        if geometry_tables:
            # Geometry tables found - use them exclusively (more accurate)
            tables = geometry_tables
            issues = layout_result.issues + table_issues + geom_issues
        else:
            # No geometry tables - use heuristic but filter false positives
            # Heuristic detector can mistake bullet lists for 2-column tables
            filtered, suppress_issues = self._suppress_list_like_heuristics(tables)
            if len(filtered) != len(tables):
                logger.info(
                    "heuristic.suppressed_list_like_tables before=%s after=%s",
                    len(tables), len(filtered)
                )
            tables = filtered
            issues = layout_result.issues + table_issues + geom_issues + suppress_issues



        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(tables, upload=upload, config=ingest_config)
        issues = issues + limit_issues
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        table_stats = self._table_stats_summary(
            total_rows=table_summary["total_rows"],
            indexed_rows=table_summary["indexed_rows"],
            row_cap=table_summary.get("row_cap_hint"),
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=table_summary.get("partial_tables", 0),
            row_tier=table_summary.get("row_tier_hint"),
        )
        metadata = {
            "format": format_hint,
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "page_count": len(layout_result.pages),
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
        }
        return ExtractionResult(
            text=text,
            format_hint=format_hint or "binary",
            metadata=metadata,
            pages=layout_result.pages,
            tables=tables,
            issues=issues,
            entities=table_entities,
        )

    def _fallback_text_extraction(self, path: Path, format_hint: str) -> str:
        """
        Fallback text extraction when layout-aware extraction fails.
        
        This method provides basic text extraction without structure preservation.
        Used when PageRenderer fails or when format doesn't support layout extraction.
        The extracted text is still usable for chunking and embedding, just without
        the benefits of structured layout.
        
        Args:
            path: Path to file
            format_hint: Detected format
        
        Returns:
            Plain text content
        
        Raises:
            UnsupportedFormatError: If format is not supported
        """
        if format_hint == "pdf":
            return self._extract_pdf(path)
        if format_hint == "docx":
            return self._extract_docx(path)
        if format_hint in {"txt", "text", "csv", "tsv"}:
            return self._extract_text_file(path)
        raise UnsupportedFormatError(f"Unsupported file type {format_hint}.")

    def _extract_from_link(self, url: str) -> ExtractionResult:
        """
        Extract content from a web URL.
        
        Fetches the URL using the document scraper service, which handles:
        - HTTP requests with timeout
        - Content size limits (2MB max)
        - Content type detection
        - Text extraction from HTML
        
        The result is treated as a single-page document with plain text content.
        No table detection or structure extraction is performed on web content.
        
        Args:
            url: Web URL to fetch and extract
        
        Returns:
            ExtractionResult with scraped text and metadata
        
        Raises:
            KnowledgeIngestionError: If URL cannot be fetched or scraped
        """
        try:
            scraped = scrape_document_source(url=url, timeout=15.0, max_bytes=2_000_000)
        except DocumentScrapeError as exc:
            raise KnowledgeIngestionError(str(exc)) from exc

        text = scraped.text or ""
        content_type = (scraped.content_type or "").lower()
        fmt = "text/html" if "html" in content_type else "text/plain"

        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / float(612 * 792),
            has_ocr_content=False,
            content_type=fmt,
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=text,
                )
            ],
            metadata={"url": scraped.final_url},
        )

        return ExtractionResult(
            text=text,
            format_hint=fmt,
            metadata={
                "url": scraped.final_url,
                "status_code": scraped.status_code,
                "content_type": scraped.content_type,
                "word_count": scraped.word_count,
            },
            pages=[page],
            tables=[],
            issues=[],
        )

    def _sanitize_html(self, soup: BeautifulSoup, allowed_tags: list[str]) -> str:
        """
        Sanitize HTML keeping only allowed semantic tags.
        
        Removes all attributes except href for links. This method is currently
        not used in the ingestion pipeline but is kept for potential future
        web content processing.
        
        Note: BeautifulSoup import is missing - this method requires
        'beautifulsoup4' package to be installed.
        
        Args:
            soup: BeautifulSoup parsed HTML document
            allowed_tags: List of HTML tag names to preserve
        
        Returns:
            Sanitized HTML string with only allowed tags and href attributes
        """
        # Remove disallowed tags but keep their content
        for tag in soup.find_all():
            if tag.name not in allowed_tags:
                tag.unwrap()
        
        # Clean attributes (keep only href for links)
        for tag in soup.find_all():
            if tag.name == "a" and tag.has_attr("href"):
                href = tag["href"]
                tag.attrs = {"href": href}
            else:
                tag.attrs = {}
        
        # Convert to string and clean up
        html = str(soup)
        
        # Remove excessive whitespace
        html = re.sub(r"\n\s*\n", "\n\n", html)
        html = re.sub(r" +", " ", html)
        
        return html.strip()

    def _detect_encoding(self, content_bytes: bytes) -> str:
        """
        Detect text encoding from raw bytes.
        
        Tries encodings in order of likelihood:
        1. UTF-8 (most common modern encoding)
        2. Latin-1 / ISO-8859-1 (Western European)
        3. Windows-1252 (Windows Western European)
        
        Falls back to UTF-8 if none work (may produce mojibake but won't crash).
        
        Args:
            content_bytes: Raw file bytes
        
        Returns:
            Detected encoding name (e.g., "utf-8")
        """
        # Try UTF-8 first (most common modern encoding)
        try:
            content_bytes.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass
        
        # Try common legacy encodings (for older documents)
        for encoding in ["latin-1", "iso-8859-1", "windows-1252"]:
            try:
                content_bytes.decode(encoding)
                return encoding
            except UnicodeDecodeError:
                continue
        
        # Fallback to UTF-8 (may produce mojibake but won't crash)
        return "utf-8"

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
        feature_state = FeatureFlagService.snapshot(upload.business_profile)
        ingestion_metadata["feature_flags"] = feature_state.as_dict()

        defaults = {
            "content": normalized,
            "metadata": {
                "ingested_at": now.isoformat(),
                "format": extraction.format_hint,
            },
        }

        entity_payloads = list(extraction.entities or [])
        if entity_payloads and not feature_state.entity_chunking:
            table_entities = [e for e in entity_payloads if e.get("alias_source_type") == "table"]
            if table_entities:
                logger.info(
                    "table.entities.persist upload=%s business=%s entities=%s",
                    upload.id,
                    upload.business_profile_id,
                    len(table_entities),
                )
            else:
                logger.info(
                    "json.entities.disabled upload=%s business=%s entities=%s",
                    upload.id,
                    upload.business_profile_id,
                    len(entity_payloads),
                )
            entity_payloads = table_entities
        entity_stats: dict[str, Any] = {}
        with transaction.atomic():
            structured_summary = self._persist_structured_artifacts(upload, extraction)
            KnowledgeUploadText.objects.update_or_create(upload=upload, defaults=defaults)
            chunk_count, missing_chunk_ids, chunk_objects = self._build_chunks(
                upload,
                normalized,
                entities=entity_payloads,
            )
            if entity_payloads:
                entity_stats = self._persist_entities(upload, entity_payloads, chunk_objects)
                alias_count = entity_stats.get("alias_count", 0)
                alias_sources = entity_stats.get("alias_sources") or extraction.metadata.get("json_alias_sources") or []
                ingestion_metadata["alias_count"] = alias_count
                ingestion_metadata["alias_patterns_used"] = sorted(set(alias_sources))
                if alias_count > self.alias_warning_threshold:
                    logger.warning(
                        "json.aliases.threshold upload=%s business=%s aliases=%s threshold=%s",
                        upload.id,
                        upload.business_profile_id,
                        alias_count,
                        self.alias_warning_threshold,
                    )
            else:
                ingestion_metadata.pop("alias_count", None)
                ingestion_metadata.pop("alias_patterns_used", None)
            upload.summary = summary
            upload.token_count = words
            upload.chunk_count = chunk_count
            upload.status = KnowledgeStatus.ACTIVE
            upload.last_ingested_at = now
            upload.ingestion_error = ""
            if structured_summary:
                ingestion_metadata["structured_exports"] = structured_summary
            if missing_chunk_ids:
                ingestion_metadata["pending_embedding_chunks"] = missing_chunk_ids[:50]
                ingestion_metadata["pending_embedding_chunk_count"] = len(missing_chunk_ids)
            else:
                ingestion_metadata.pop("pending_embedding_chunks", None)
                ingestion_metadata.pop("pending_embedding_chunk_count", None)
            if "json_entities_truncated" in extraction.metadata:
                ingestion_metadata["truncated_entities"] = extraction.metadata.get("json_entities_truncated", 0)
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
        if entity_payloads:
            truncated = extraction.metadata.get("json_entities_truncated", 0)
            logger.info(
                "json.entities.summary upload=%s business=%s entities=%s aliases=%s truncated=%s",
                upload.id,
                upload.business_profile_id,
                entity_stats.get("entity_count", len(entity_payloads)),
                entity_stats.get("alias_count", 0),
                truncated,
            )
            try:
                QualityMonitor.record_ingestion_sample(
                    business_profile=upload.business_profile,
                    alias_values=entity_stats.get("alias_values") or [],
                    truncated_entities=int(truncated),
                    indexed_entities=entity_stats.get("entity_count", len(entity_payloads)),
                )
            except Exception as exc:  # pragma: no cover - monitoring failures must not block ingestion
                logger.warning("quality.ingestion.monitor_failed business=%s error=%s", upload.business_profile_id, exc)

    def _build_chunks(
        self,
        upload: KnowledgeUpload,
        content: str,
        *,
        entities: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[int, list[str], list[KnowledgeUploadChunk]]:
        """
        Build semantic chunks from content for embedding and retrieval.
        
        This method implements a dual chunking strategy:
        1. Entity-based chunking: For JSON/structured data, each entity becomes a chunk
           with its attributes and aliases. This enables precise entity lookup.
        2. Sliding-window chunking: For text documents, uses overlapping windows
           (1200 chars, 200 overlap) with boundary awareness to avoid splitting
           table rows or paragraphs.
        
        The method also handles table preview chunks: extracts first 12 rows of
        each table as TSV-formatted chunks for embedding. This makes table data
        searchable without embedding entire large tables.
        
        Embedding strategy:
        - Inline embedding: First N chunks (configurable, default 200) are embedded
          immediately during ingestion for fast availability
        - Async embedding: Remaining chunks are queued as EMBED jobs for background
          processing. This prevents ingestion from blocking on large documents.
        
        Args:
            upload: Knowledge upload being processed
            content: Full document text (for sliding-window chunking)
            entities: Optional structured entities (for entity-based chunking)
        
        Returns:
            Tuple of:
            - Total chunk count created
            - List of chunk IDs missing embeddings (for async processing)
            - List of chunk objects (for entity linking)
        """
        from apps.accounts.models import KnowledgeUploadTable  # local import to avoid cycles

        entity_payloads = list(entities or [])
        if entity_payloads:
            segment_payloads = self._build_entity_segment_payloads(entity_payloads)
        else:
            text_segments = self._chunk_text(content)
            segment_payloads: list[dict[str, Any]] = []
            for segment in text_segments:
                if not segment:
                    continue
                augmented, aliases = self._inject_identifiers_into_text(segment)
                metadata = {"strategy": "sliding_window"}
                if aliases:
                    metadata.update(self._alias_metadata(aliases))
                segment_payloads.append({"text": augmented, "metadata": metadata})

            table_segment_payloads: list[dict[str, Any]] = []
            privacy_rules = self._table_privacy_rules(upload)
            try:
                tables = (
                    KnowledgeUploadTable.objects.filter(upload=upload)
                    .order_by("order_index")
                    .prefetch_related("rows__cells")
                )
                for t in tables:
                    raw_schema = list(map(str, (t.column_schema or [])))
                    column_map: list[tuple[str, str, int]] = []
                    hidden_columns: list[str] = []
                    for idx, column in enumerate(raw_schema):
                        label = column or f"column_{idx + 1}"
                        if self._column_is_sensitive(label, privacy_rules):
                            hidden_columns.append(label)
                            continue
                        canonical = self._canonical_column_name(label, f"column_{idx + 1}")
                        column_map.append((label, canonical, idx))
                    if not column_map:
                        continue
                    cols = [entry[0] for entry in column_map]
                    tsv_lines: list[str] = []
                    header_line = "\t".join(cols) if cols else ""
                    if header_line:
                        tsv_lines.append(header_line)

                    data_row_count = 0
                    for r in t.rows.all():
                        if (r.metadata or {}).get("row_type") == "header":
                            continue
                        row_attributes = self._row_model_attributes(r, raw_schema)
                        if self._row_is_internal(row_attributes, privacy_rules):
                            continue
                        canonical_lookup = {
                            self._canonical_column_name(key, key): value
                            for key, value in row_attributes.items()
                        }
                        cells = [canonical_lookup.get(entry[1], "") for entry in column_map]
                        if any(cells):
                            tsv_lines.append("\t".join(cells))
                            data_row_count += 1
                        if data_row_count >= 12:
                            break

                    if len(tsv_lines) <= 1:
                        continue

                    if tsv_lines:
                        preface = []
                        if t.section_heading:
                            preface.append(f"[Section] {t.section_heading}")
                        title = t.title or f"Table {t.order_index}"
                        preface.append(f"[Table] {title}")
                        table_block = "\n".join(preface + tsv_lines)
                        if len(table_block) <= 1500:
                            blocks = [table_block]
                        else:
                            blocks = []
                            current: list[str] = []
                            current_len = 0
                            for line in (preface + tsv_lines):
                                if current_len + len(line) + 1 > 1500 and current:
                                    blocks.append("\n".join(current))
                                    current, current_len = [], 0
                                current.append(line)
                                current_len += len(line) + 1
                            if current:
                                blocks.append("\n".join(current))

                        base_metadata: dict[str, Any] = {
                            "strategy": "table_extract",
                            "is_table_chunk": True,
                            "is_table_preview": True,
                            "table_title": title,
                            "visibility": getattr(upload, "visibility", KnowledgeVisibility.PRIVATE),
                        }
                        if hidden_columns:
                            base_metadata["restricted_columns"] = hidden_columns[:8]
                        table_metadata = t.metadata if isinstance(t.metadata, dict) else {}
                        for key in ("entity_type", "entity_name", "entity_business"):
                            if table_metadata.get(key):
                                base_metadata[key] = table_metadata[key]
                        alias_list = base_metadata.get("aliases") or []
                        for block in blocks:
                            if not block:
                                continue
                            block_text = self._append_identifier_line(block, alias_list) if alias_list else block
                            table_segment_payloads.append({"text": block_text, "metadata": dict(base_metadata)})
            except Exception:
                table_segment_payloads = []

            segment_payloads.extend(table_segment_payloads)

        KnowledgeUploadChunk.objects.filter(upload=upload).delete()
        if not segment_payloads:
            return 0, [], []

        logger.info(
            "embed.start upload=%s segments=%s provider=%s model=%s",
            upload.id,
            len(segment_payloads),
            type(self.embedding_service).__name__ if self.embedding_service else None,
            getattr(self.embedding_service, "model", "local"),
        )

        total_segments = len(segment_payloads)
        inline_limit = total_segments
        if self.ingest_inline_chunk_limit:
            inline_limit = min(total_segments, self.ingest_inline_chunk_limit)
        embeddings: list[list[float] | None] = [None] * total_segments
        if self.embedding_service and inline_limit:
            try:
                inline_vectors = self.embedding_service.embed_texts(
                    [payload["text"] for payload in segment_payloads[:inline_limit]]
                )
                for idx, vector in enumerate(inline_vectors):
                    embeddings[idx] = self._normalize_embedding(vector)
            except EmbeddingProviderError as exc:
                logger.warning("Embedding generation failed upload=%s error=%s", upload.id, exc)
            except Exception:
                logger.exception("Unexpected embedding failure upload=%s", upload.id)

        got_vectors = len([v for v in embeddings if v])
        staged_vectors = total_segments - inline_limit if self.ingest_inline_chunk_limit else 0
        logger.info(
            "embed.inline upload=%s segments=%s inline=%s staged=%s got_vectors=%s",
            upload.id,
            total_segments,
            inline_limit,
            staged_vectors,
            got_vectors,
        )

        chunk_objects: list[KnowledgeUploadChunk] = []
        fallback_targets: list[KnowledgeUploadChunk] = []
        for index, payload in enumerate(segment_payloads):
            segment_text = payload.get("text") or ""
            vector = None
            if embeddings and index < len(embeddings):
                vector = embeddings[index]

            chunk_metadata = {
                "strategy": "json_entity" if entity_payloads else "sliding_window_plus_tables",
            }
            extra_meta = payload.get("metadata") or {}
            if isinstance(extra_meta, dict):
                chunk_metadata.update(extra_meta)
            chunk_metadata.setdefault("is_table_chunk", False)
            self._finalize_alias_metadata(chunk_metadata)

            chunk = KnowledgeUploadChunk(
                upload=upload,
                business_profile=upload.business_profile,
                chunk_index=index,
                content=segment_text,
                token_count=len(segment_text.split()),
                embedding=vector,
                metadata=chunk_metadata,
            )
            if chunk.embedding is None:
                fallback_targets.append(chunk)
            chunk_objects.append(chunk)

        prewarmed = 0
        if (
            self.embedding_prewarm_limit
            and fallback_targets
            and len(fallback_targets) <= self.embedding_prewarm_limit
            and self.embedding_service
        ):
            try:
                vectors = self.embedding_service.embed_texts([chunk.content or "" for chunk in fallback_targets])
                for chunk, vector in zip(fallback_targets, vectors):
                    normalized = self._normalize_embedding(vector)
                    if normalized:
                        chunk.embedding = normalized
                        prewarmed += 1
            except EmbeddingProviderError as exc:
                logger.warning("Embedding prewarm failed upload=%s error=%s", upload.id, exc)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Embedding prewarm unexpected failure upload=%s", upload.id)
            if prewarmed:
                logger.info("embed.prewarm upload=%s chunks=%s", upload.id, prewarmed)
        if prewarmed:
            fallback_targets = [chunk for chunk in fallback_targets if chunk.embedding is None]

        backfilled = 0
        if fallback_targets:
            backfilled = self._apply_fallback_embeddings(upload, fallback_targets)
            if backfilled:
                logger.info(
                    "embed.fallback upload=%s requested=%s backfilled=%s",
                    upload.id,
                    len(fallback_targets),
                    backfilled,
                )
            else:
                logger.warning(
                    "embed.fallback_failed upload=%s requested=%s",
                    upload.id,
                    len(fallback_targets),
                )

        missing_chunk_ids = [str(chunk.id) for chunk in chunk_objects if chunk.embedding is None]

        KnowledgeUploadChunk.objects.bulk_create(chunk_objects, batch_size=100)
        logger.info(
            "chunks.persisted upload=%s count=%s missing_embeddings=%s",
            upload.id,
            len(chunk_objects),
            len(missing_chunk_ids),
        )
        if missing_chunk_ids:
            self._schedule_embedding_jobs(upload, missing_chunk_ids)
        return len(chunk_objects), missing_chunk_ids, chunk_objects

    def _build_entity_segment_payloads(self, entities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """
        Convert structured entities into chunk payloads for embedding.
        
        Each entity becomes a separate chunk with its attributes formatted as
        key-value pairs. This enables precise entity lookup in RAG queries.
        The chunk includes aliases for searchability and entity metadata for
        context.
        
        Args:
            entities: List of entity dictionaries with attributes, columns, aliases
        
        Returns:
            List of chunk payloads with text and metadata
        """
        payloads: list[dict[str, Any]] = []
        for index, entity in enumerate(entities):
            attributes = entity.get("attributes") or {}
            columns = entity.get("columns") or []
            alias_list = list(entity.get("aliases") or [])
            entity_type = entity.get("entity_type") or "record"
            entity_name = entity.get("entity_name") or f"{entity_type.title()} {index + 1}"
            
            # Format entity as structured text (entity type: name, then attributes)
            lines = [f"{entity_type.title()}: {entity_name}"]
            # Limit to 16 columns to keep chunks focused (prevents oversized chunks)
            for column in columns[:16]:
                value = attributes.get(column)
                if value:
                    lines.append(f"- {column}: {value}")
            text = "\n".join(lines).strip()
            text, inline_aliases = self._inject_identifiers_into_text(text)
            combined_aliases = alias_list[:]
            for alias in inline_aliases:
                if alias and alias not in combined_aliases:
                    combined_aliases.append(alias)
            metadata: dict[str, Any] = {
                "strategy": entity.get("chunk_strategy") or "json_entity",
                "entity_type": entity_type,
                "entity_name": entity_name,
                "entity_business": entity.get("entity_business"),
                "entity_index": entity.get("entity_index", index),
                "visibility": entity.get("visibility") or entity.get("entity_visibility"),
            }
            table_meta = entity.get("table_metadata")
            if isinstance(table_meta, dict):
                metadata["table_metadata"] = table_meta
            metadata.update(self._alias_metadata(combined_aliases))
            payloads.append({"text": text, "metadata": metadata})
        return payloads

    def _persist_entities(
        self,
        upload: KnowledgeUpload,
        entities: Sequence[Mapping[str, Any]],
        chunks: Sequence[KnowledgeUploadChunk],
    ) -> dict[str, Any]:
        """
        Persist extracted entities and their aliases to the database.
        
        This method creates KnowledgeEntity and KnowledgeAlias records for
        entity lookup in RAG queries. Entities are linked to their chunks
        via entity_index for bidirectional lookup.
        
        The method:
        1. Deletes existing entities for this upload (idempotent re-ingestion)
        2. Creates entity records with attributes and metadata
        3. Creates alias records for searchable identifiers
        4. Invalidates alias cache (for RAG lookup)
        
        Args:
            upload: Knowledge upload being processed
            entities: Entity payloads from extraction
            chunks: Chunk objects (for linking entities to chunks)
        
        Returns:
            Dictionary with entity/alias counts and statistics
        """
        # Delete existing entities (allows re-ingestion)
        KnowledgeEntity.objects.filter(upload=upload).delete()
        
        # Build chunk lookup by entity_index (for linking entities to chunks)
        chunk_by_index: dict[int, KnowledgeUploadChunk] = {}
        for chunk in chunks:
            metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            idx = metadata.get("entity_index")
            if isinstance(idx, int):
                chunk_by_index[idx] = chunk
        
        business = upload.business_profile
        entity_models: list[KnowledgeEntity] = []
        for entity in entities:
            idx = entity.get("entity_index")
            chunk = chunk_by_index.get(idx) if isinstance(idx, int) else None
            entity_model = KnowledgeEntity(
                business_profile=business,
                upload=upload,
                chunk=chunk,
                entity_type=entity.get("entity_type") or "",
                entity_name=entity.get("entity_name") or "",
                primary_label=entity.get("entity_name") or entity.get("entity_type") or "",
                metadata={
                    "attributes": entity.get("attributes"),
                    "columns": entity.get("columns"),
                    "table_metadata": entity.get("table_metadata"),
                },
            )
            entity_models.append(entity_model)
        KnowledgeEntity.objects.bulk_create(entity_models, batch_size=200)

        alias_models: list[KnowledgeAlias] = []
        alias_sources: set[str] = set()
        alias_values: list[str] = []
        for model, payload in zip(entity_models, entities):
            payload_sources = payload.get("alias_sources") or []
            alias_sources.update(payload_sources)
            seen_aliases: set[str] = set()
            for alias in payload.get("aliases") or []:
                if not alias:
                    continue
                cleaned = str(alias).strip()
                if not cleaned:
                    continue
                if len(cleaned) > ALIAS_MAX_LENGTH:
                    cleaned = cleaned[:ALIAS_MAX_LENGTH]
                normalized = self._normalize_alias_value(cleaned)
                if not normalized or normalized in seen_aliases:
                    continue
                seen_aliases.add(normalized)
                alias_models.append(
                    KnowledgeAlias(
                        business_profile=business,
                        entity=model,
                        alias_raw=cleaned,
                        alias_normalized=normalized,
                        alias_search_vector=normalized.replace("-", " "),
                        source=payload.get("alias_source_type") or "json",
                    )
                )
                if len(alias_values) < 2048:
                    alias_values.append(normalized)
        if alias_models:
            KnowledgeAlias.objects.bulk_create(alias_models, batch_size=500)
            self._invalidate_alias_cache(business.id)
        return {
            "entity_count": len(entity_models),
            "alias_count": len(alias_models),
            "alias_sources": sorted(alias_sources),
            "alias_values": alias_values,
        }

    def _schedule_embedding_jobs(self, upload: KnowledgeUpload, chunk_ids: Sequence[str]) -> None:
        """
        Schedule async embedding jobs for chunks that couldn't be embedded inline.
        
        Chunks are batched into jobs to optimize processing. Each job contains
        up to embedding_job_payload_size chunk IDs. This prevents individual
        jobs from being too large while ensuring efficient batch processing.
        
        The method also checks embedding backlog and logs warnings if it exceeds
        the threshold, indicating potential processing delays.
        
        Args:
            upload: Knowledge upload being processed
            chunk_ids: List of chunk UUIDs that need embeddings
        """
        if not chunk_ids:
            return
        batch_size = self.embedding_job_payload_size
        business = upload.business_profile
        
        # Create embedding jobs in batches
        for idx in range(0, len(chunk_ids), batch_size):
            batch = chunk_ids[idx : idx + batch_size]
            job = KnowledgeIngestionJob.objects.create(
                business_profile=business,
                upload=upload,
                job_type=KnowledgeIngestionJobType.EMBED,
                status=KnowledgeIngestionJobStatus.QUEUED,
                payload={"chunk_ids": batch},
            )
            logger.info("Queued embedding job upload=%s job=%s chunks=%s", upload.id, job.id, len(batch))
        
        # Check backlog and warn if too high (indicates processing delays)
        backlog = self._embedding_backlog_count(business.id)
        if self.embedding_backlog_threshold and backlog >= self.embedding_backlog_threshold:
            logger.warning(
                "embedding.backlog threshold exceeded business=%s backlog=%s threshold=%s",
                business.id,
                backlog,
                self.embedding_backlog_threshold,
            )

    def _update_upload_embedding_metadata(self, upload: KnowledgeUpload, *, processed_ids: Sequence[str]) -> None:
        """
        Update upload metadata to reflect embedding progress.
        
        Removes processed chunk IDs from pending list and updates the count
        of remaining chunks. This provides visibility into embedding progress
        for monitoring and debugging.
        
        Args:
            upload: Knowledge upload to update
            processed_ids: Chunk IDs that were just embedded
        """
        metadata = dict(upload.ingestion_metadata or {})
        pending = metadata.get("pending_embedding_chunks")
        if isinstance(pending, list):
            # Remove processed chunks from pending list
            pending_set = {str(value) for value in pending}
            for chunk_id in processed_ids:
                pending_set.discard(str(chunk_id))
            if pending_set:
                # Keep only first 50 for metadata size (full list can be queried)
                metadata["pending_embedding_chunks"] = list(pending_set)[:50]
            else:
                metadata.pop("pending_embedding_chunks", None)
        
        # Update count of remaining chunks (more efficient than storing all IDs)
        remaining = KnowledgeUploadChunk.objects.filter(upload=upload, embedding__isnull=True).count()
        if remaining:
            metadata["pending_embedding_chunk_count"] = remaining
        else:
            metadata.pop("pending_embedding_chunk_count", None)
        upload.ingestion_metadata = metadata
        upload.save(update_fields=["ingestion_metadata", "updated_at"])

    def _invalidate_alias_cache(self, business_id: uuid.UUID) -> None:
        """
        Invalidate RAG caches after entity/alias updates.
        
        When entities or aliases are created/updated, we need to invalidate
        the RAG search caches to ensure queries see the latest data. This
        includes alias cache, query cache, and result cache.
        
        Args:
            business_id: Business profile ID whose caches to invalidate
        """
        try:
            from apps.services.ai_orchestrator import KnowledgeSearchService
        except ImportError:  # pragma: no cover - defensive import
            return
        KnowledgeSearchService.invalidate_alias_cache(business_id)
        KnowledgeSearchService.invalidate_query_cache(business_id)
        KnowledgeSearchService.invalidate_result_cache(business_id)

    def _embedding_backlog_count(self, business_id: uuid.UUID) -> int:
        """
        Count pending embedding jobs for a business.
        
        Used to monitor embedding backlog and trigger warnings when it
        exceeds thresholds (indicates processing delays).
        
        Args:
            business_id: Business profile ID
        
        Returns:
            Number of queued/running embedding jobs
        """
        return KnowledgeIngestionJob.objects.filter(
            business_profile_id=business_id,
            job_type=KnowledgeIngestionJobType.EMBED,
            status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.RUNNING),
        ).count()

    def _release_deferred_jobs(self, business_id: uuid.UUID) -> None:
        """
        Promote deferred ingestion jobs to queued when capacity is available.
        
        When ingestion jobs exceed the concurrency limit, they're marked as
        DEFERRED. This method checks if capacity is available and promotes
        the oldest deferred jobs to QUEUED status.
        
        This implements a fair queuing system: jobs are processed in order
        of creation, with capacity limits preventing resource exhaustion.
        
        Args:
            business_id: Business profile ID
        """
        if not self.ingest_concurrency_limit:
            return  # No limit configured, nothing to release
        
        # Count active jobs (queued or running)
        active = KnowledgeIngestionJob.objects.filter(
            business_profile_id=business_id,
            status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.RUNNING),
            job_type=KnowledgeIngestionJobType.INGEST,
        ).count()
        
        # Calculate available capacity
        available = self.ingest_concurrency_limit - active
        if available <= 0:
            return  # No capacity available
        
        # Promote oldest deferred jobs up to available capacity
        deferred = list(
            KnowledgeIngestionJob.objects.filter(
                business_profile_id=business_id,
                status=KnowledgeIngestionJobStatus.DEFERRED,
                job_type=KnowledgeIngestionJobType.INGEST,
            )
            .order_by("created_at")[:available]  # Oldest first
        )
        if not deferred:
            return
        
        ids = [job.id for job in deferred]
        KnowledgeIngestionJob.objects.filter(id__in=ids).update(status=KnowledgeIngestionJobStatus.QUEUED)
        logger.info("Promoted %s deferred ingestion jobs for business=%s", len(ids), business_id)

    def _normalize_embedding(self, vector: Sequence[float] | None) -> list[float] | None:
        """
        Normalize embedding vector to expected dimensions.
        
        Handles dimension mismatches by trimming or padding vectors to match
        EMBED_DIM setting. This ensures all embeddings have consistent dimensions
        for vector search operations.
        
        Args:
            vector: Embedding vector (may be None or wrong dimension)
        
        Returns:
            Normalized vector as list of floats, or None if invalid
        """
        if not vector:
            return None
        try:
            values = [float(v) for v in vector]
        except (TypeError, ValueError):
            return None  # Invalid vector format
        
        expected = getattr(settings, "EMBED_DIM", None)
        if expected:
            if len(values) > expected:
                # Trim excess dimensions (may lose information but prevents errors)
                logger.warning("Embedding dimension mismatch: got %s, expected %s. Trimming.", len(values), expected)
                values = values[:expected]
            elif len(values) < expected:
                # Pad with zeros (less ideal but allows processing)
                logger.warning("Embedding dimension mismatch: got %s, expected %s. Padding.", len(values), expected)
                values = values + [0.0] * (expected - len(values))
        return values

    def _get_fallback_embedding_service(self) -> LocalEmbeddingService | None:
        """
        Get local embedding service as fallback when primary service fails.
        
        This provides a safety net: if the primary embedding service (e.g., OpenAI)
        is unavailable, we can fall back to local embeddings (e.g., sentence-transformers).
        The fallback is lazy-loaded and cached to avoid repeated initialization attempts.
        
        Returns:
            Local embedding service if available, None otherwise
        """
        # If primary service is already local, use it
        if isinstance(self.embedding_service, LocalEmbeddingService):
            return self.embedding_service
        
        # Return cached fallback if already loaded
        if self._fallback_embedding_service:
            return self._fallback_embedding_service
        
        # Only attempt once (prevents repeated failures)
        if self._fallback_embedding_attempted:
            return None
        
        self._fallback_embedding_attempted = True
        try:
            self._fallback_embedding_service = build_embedding_service("local")
        except EmbeddingProviderError as exc:
            logger.warning("Local embedding fallback unavailable: %s", exc)
            self._fallback_embedding_service = None
        return self._fallback_embedding_service

    def _apply_fallback_embeddings(
        self,
        upload: KnowledgeUpload,
        chunks: Sequence[KnowledgeUploadChunk],
    ) -> int:
        """
        Generate embeddings using fallback service for chunks missing vectors.
        
        This is a last-resort attempt to embed chunks when the primary service
        failed. Uses local embedding service if available. This ensures chunks
        get embedded even when external services are down.
        
        Args:
            upload: Knowledge upload being processed
            chunks: Chunks missing embeddings
        
        Returns:
            Number of chunks successfully embedded
        """
        service = self._get_fallback_embedding_service()
        if not service:
            return 0
        texts = [chunk.content or "" for chunk in chunks]
        if not any(texts):
            return 0
        try:
            vectors = service.embed_texts(texts)
        except EmbeddingProviderError as exc:
            logger.warning("Fallback embedding generation failed upload=%s error=%s", upload.id, exc)
            return 0
        except Exception:
            logger.exception("Unexpected fallback embedding failure upload=%s", upload.id)
            return 0
        filled = 0
        for chunk, vector in zip(chunks, vectors):
            normalized = self._normalize_embedding(vector)
            if normalized:
                chunk.embedding = normalized
                filled += 1
        return filled

    def _persist_structured_artifacts(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> dict[str, Any]:
        """
        Persist structured extraction artifacts (pages, tables, issues) to database.
        
        This method creates the full structured representation of a document:
        - Pages with blocks (for layout-aware retrieval)
        - Tables with rows and cells (for structured queries)
        - Issues (for quality monitoring and user feedback)
        
        The method uses bulk operations for efficiency and builds lookup dictionaries
        to link related objects (e.g., issues to their pages/tables).
        
        Args:
            upload: Knowledge upload being processed
            extraction: Extraction result with pages, tables, and issues
        
        Returns:
            Dictionary with summaries of persisted artifacts (for metadata)
        """
        # Delete existing artifacts (allows idempotent re-ingestion)
        KnowledgeUploadPage.objects.filter(upload=upload).delete()
        KnowledgeUploadTable.objects.filter(upload=upload).delete()
        KnowledgeUploadIssue.objects.filter(upload=upload).delete()

        # Build pages and blocks
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
            synopsis = self._sanitize_text(self._page_synopsis_from_blocks(page_payload.blocks))
            headings: list[str] = []
            for block in page_payload.blocks:
                if not isinstance(block.section_heading, str):
                    continue
                normalized_heading = self._sanitize_text(block.section_heading).strip()
                if normalized_heading:
                    headings.append(normalized_heading)
            page_summaries.append(
                {
                    "page_number": page_payload.page_number,
                    "text_density": page_payload.text_density,
                    "has_ocr_content": page_payload.has_ocr_content,
                    "width": page_payload.width,
                    "height": page_payload.height,
                    "synopsis": synopsis,
                    "headings": headings[:3],
                }
            )
            for block_payload in page_payload.blocks:
                block_objects.append(
                    KnowledgeUploadPageBlock(
                        upload=upload,
                        page=page_obj,
                        block_type=block_payload.block_type,
                        order_index=block_payload.order_index,
                        text=self._sanitize_text(block_payload.text),
                        bbox=block_payload.bbox,
                        section_heading=self._sanitize_text(block_payload.section_heading),
                        heading_path=[
                            self._sanitize_text(item)
                            for item in (block_payload.heading_path or [])
                        ],
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
        """
        Convert IssuePayload to dictionary for metadata storage.
        
        Used to store issue summaries in ingestion metadata for quick
        access without querying the database.
        
        Args:
            issue: Issue payload to convert
        
        Returns:
            Dictionary representation of the issue
        """
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
        """
        Boundary-aware text chunker with smart table handling.
        
        This method splits text into overlapping chunks optimized for embedding.
        The chunking strategy:
        1. Prefers paragraph boundaries (double newlines) to avoid splitting context
        2. Falls back to line boundaries if no paragraph break available
        3. Uses 200-char overlap to preserve context across chunk boundaries
        4. Special handling for tables: if a chunk starts mid-table, it pulls in
           up to 2 preceding lines (likely headers) to preserve table structure
        
        The table header detection is critical: without headers, table chunks
        lose semantic meaning. By pulling headers into chunks that start mid-table,
        we ensure each table chunk is self-contained.
        
        Args:
            content: Full text to chunk
            chunk_chars: Target chunk size in characters (default 1200)
            overlap: Overlap between chunks in characters (default 200, max chunk_chars/2)
        
        Returns:
            List of text chunks ready for embedding
        """
        text = (content or "").strip()
        if not text:
            return []

        segments: list[str] = []
        length = len(text)
        start = 0
        # Cap overlap at half chunk size (prevents excessive overlap)
        overlap = max(0, min(overlap, chunk_chars // 2))

        while start < length:
            end_candidate = min(length, start + chunk_chars)
            window = text[start:end_candidate]

            # Try to cut on paragraph boundary (preserves context better)
            # If no paragraph break, try line boundary
            # Only use boundary if it's at least 60% through the chunk (avoids tiny chunks)
            cut = window.rfind("\n\n")
            if cut == -1:
                cut = window.rfind("\n")
            if cut != -1 and cut >= int(chunk_chars * 0.6):
                end = start + cut
            else:
                end = end_candidate  # No good boundary, use full chunk

            # Smart table handling: if chunk starts mid-table row, pull in headers
            # This ensures table chunks are self-contained and meaningful
            first_line_start = start
            nl_pos = text.find("\n", start, end)
            if nl_pos == -1:
                first_line = text[start:end].lstrip()
            else:
                first_line = text[start:nl_pos].lstrip()

            # Detect table row (tabs indicate TSV-formatted table)
            if "\t" in first_line and start > 0:
                # Search backwards up to 300 chars for header rows
                back_search_from = max(0, start - 300)
                back_slice = text[back_search_from:start]
                back_lines = back_slice.splitlines()
                take_lines = "\n".join(back_lines[-2:])  # Pull up to 2 lines (header + separator)
                if take_lines:
                    # Expand chunk start to include headers
                    start = max(0, start - (len(take_lines) + 1))  # +1 for newline
                    # Recompute chunk end with expanded start
                    end_candidate = min(length, start + chunk_chars)
                    window = text[start:end_candidate]
                    cut2 = window.rfind("\n\n")
                    if cut2 == -1:
                        cut2 = window.rfind("\n")
                    end = start + (cut2 if cut2 != -1 else len(window))

            chunk = text[start:end].strip()
            if chunk:
                segments.append(chunk)
            if end >= length:
                break
            # Move start forward with overlap (preserves context across chunks)
            start = max(0, end - overlap)

        return segments

    @staticmethod
    def _page_synopsis_from_blocks(blocks: Sequence[PageBlockPayload], *, max_chars: int = 480) -> str:
        """
        Generate a short synopsis of a page from its blocks.
        
        Used for page summaries in metadata. Takes first blocks until max_chars
        is reached, then truncates at word boundary to avoid cutting words.
        
        Args:
            blocks: Page blocks to summarize
            max_chars: Maximum synopsis length (default 480)
        
        Returns:
            Synopsis text with ellipsis if truncated
        """
        if not blocks:
            return ""
        snippets: list[str] = []
        for block in blocks:
            text = (block.text or "").strip()
            if not text:
                continue
            snippets.append(text)
            combined = " ".join(snippets)
            if len(combined) >= max_chars:
                break
        synopsis = " ".join(snippets).strip()
        # Truncate at word boundary to avoid cutting words
        if len(synopsis) > max_chars:
            synopsis = synopsis[:max_chars].rsplit(" ", 1)[0].rstrip()
            synopsis = f"{synopsis}…"
        return synopsis


    def _mark_job_completed(self, job: KnowledgeIngestionJob, *, extra: dict[str, Any] | None = None) -> None:
        """
        Mark an ingestion job as completed and perform cleanup.
        
        Updates job status, stores completion metadata, invalidates caches,
        and releases deferred jobs if capacity is available.
        
        Args:
            job: Job to mark as completed
            extra: Additional metadata to store in job payload
        """
        finished = timezone.now()
        payload = dict(job.payload or {})
        if extra:
            payload.update(extra)
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.COMPLETED,
            finished_at=finished,
            payload=payload,
        )
        # Invalidate caches so RAG queries see new content
        self._invalidate_alias_cache(job.business_profile_id)
        # Release deferred jobs if capacity available
        self._release_deferred_jobs(job.business_profile_id)

    def _handle_failure(self, job: KnowledgeIngestionJob, message: str) -> None:
        """
        Handle job failure and update upload status.
        
        Marks job as failed, stores error message, and updates upload status
        to FAILED so users can see what went wrong. Also releases deferred
        jobs to allow other work to proceed.
        
        Args:
            job: Failed job
            message: Error message to store
        """
        finished = timezone.now()
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.FAILED,
            finished_at=finished,
            error_detail=message,
        )
        # Update upload status so users see the failure
        upload = job.upload
        upload.ingestion_error = message
        upload.status = KnowledgeStatus.FAILED
        upload.save(update_fields=["ingestion_error", "status", "updated_at"])
        # Release deferred jobs (failure doesn't block other work)
        self._release_deferred_jobs(job.business_profile_id)

    # ------------------------------------------------------------------
    # Helpers

    def _claim_next_job(self) -> KnowledgeIngestionJob | None:
        """
        Atomically claim the next queued job for processing.
        
        This method implements optimistic locking: it selects a job and
        immediately tries to update its status to RUNNING. If the update
        succeeds (rows_affected > 0), the job was successfully claimed.
        If it fails (another worker claimed it), returns None.
        
        Job priority:
        - INGEST jobs (priority 0) are processed before EMBED jobs
        - Within same priority, oldest jobs first (FIFO)
        
        This ensures ingestion completes before embeddings, and jobs are
        processed in order to prevent starvation.
        
        Returns:
            Claimed job ready for processing, or None if no jobs available
        """
        # Select next job by priority and age
        job = (
            KnowledgeIngestionJob.objects.filter(
                status=KnowledgeIngestionJobStatus.QUEUED,
            )
            .annotate(
                # Priority: INGEST (0) before EMBED (1) before others (5)
                priority=Case(
                    When(job_type=KnowledgeIngestionJobType.INGEST, then=Value(0)),
                    When(job_type=KnowledgeIngestionJobType.EMBED, then=Value(1)),
                    default=Value(5),
                    output_field=IntegerField(),
                )
            )
            .select_related("upload__file_detail", "upload__url_detail", "upload__business_profile")
            .order_by("priority", "created_at")  # Priority first, then FIFO
            .first()
        )
        if not job:
            return None

        # Atomic claim: try to update status to RUNNING
        # This fails if another worker already claimed it (prevents duplicate processing)
        claimed = KnowledgeIngestionJob.objects.filter(
            id=job.id,
            status=KnowledgeIngestionJobStatus.QUEUED,  # Only claim if still queued
        ).update(
            status=KnowledgeIngestionJobStatus.RUNNING,
            started_at=timezone.now(),
        )
        if not claimed:
            return None  # Another worker claimed it first

        job.refresh_from_db()
        return job

    @staticmethod
    def _detect_format(file_detail: KnowledgeUploadFile) -> str | None:
        """
        Detect file format from filename and content-type.
        
        Uses multiple signals for robust detection:
        1. File extension (suffix)
        2. Content-Type header
        3. MIME type guessing
        
        This multi-signal approach handles cases where one signal is missing
        or incorrect (e.g., files uploaded without proper content-type).
        
        Args:
            file_detail: File metadata with filename and content_type
        
        Returns:
            Format identifier (pdf, docx, json, csv, xlsx, xls, txt) or None
        """
        filename = (file_detail.filename or "").lower()
        suffix = Path(filename).suffix.lower()
        content_type = (file_detail.content_type or "").lower()
        guessed = mimetypes.guess_type(filename)[0] if filename else ""

        # Check multiple signals for each format (robust detection)
        if suffix == ".pdf" or "pdf" in content_type or "pdf" in (guessed or ""):
            return "pdf"
        if suffix in {".docx", ".dotx"} or "word" in content_type or "officedocument.wordprocessingml" in content_type:
            return "docx"
        if suffix in {".json", ".jsonl", ".ndjson"} or "json" in content_type or "json" in (guessed or ""):
            return "json"
        if suffix in {".csv", ".tsv"} or "csv" in content_type or "csv" in (guessed or ""):
            # Distinguish TSV from CSV
            return "tsv" if suffix == ".tsv" or "tsv" in content_type or "tsv" in (guessed or "") else "csv"
        if (
            suffix in {".xlsx", ".xlsm"}
            or "officedocument.spreadsheetml" in content_type
            or "vnd.google-apps.spreadsheet" in content_type
        ):
            return "xlsx"
        if (
            suffix == ".xls"
            or "ms-excel" in content_type
            or "vnd.ms-excel" in (guessed or "")
        ):
            return "xls"
        if suffix in {".txt", ".md", ".rtf"} or "text" in content_type:
            return "txt"
        # Fallback: use suffix without dot
        return suffix.strip(".") if suffix else None

    @staticmethod
    def _extract_pdf(path: Path) -> str:
        """
        Extract plain text from PDF using available libraries.
        
        Tries PyMuPDF first (better performance), falls back to pypdf if
        PyMuPDF fails or is unavailable. Individual page failures don't
        abort the entire extraction (empty string for that page).
        
        Args:
            path: Path to PDF file
        
        Returns:
            Extracted text with pages joined by newlines
        
        Raises:
            KnowledgeIngestionError: If no PDF library available or extraction fails
        """
        pymupdf_error: Exception | None = None
        # Try PyMuPDF first (faster and more reliable)
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

        # Fallback to pypdf
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
                    fragments.append("")  # Continue with other pages
            return "\n".join(fragments)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to extract PDF text: {exc}") from exc

    @staticmethod
    def _extract_docx(path: Path) -> str:
        """
        Extract plain text from DOCX file.
        
        Extracts paragraphs in order and joins them with newlines.
        This is a simple extraction without structure preservation
        (use PageRenderer for structured extraction).
        
        Args:
            path: Path to DOCX file
        
        Returns:
            Extracted text with paragraphs joined by newlines
        
        Raises:
            KnowledgeIngestionError: If python-docx unavailable or extraction fails
        """
        if DocxDocument is None:
            raise KnowledgeIngestionError("DOCX ingestion requires the python-docx package.")
        try:
            document = DocxDocument(str(path))
            fragments = [paragraph.text for paragraph in document.paragraphs]
            return "\n".join(fragments)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to extract DOCX text: {exc}") from exc

    def _extract_csv(
        self,
        path: Path,
        *,
        format_hint: str,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        """
        Extract content from CSV/TSV file and convert to structured table.
        
        This method:
        1. Detects delimiter (auto-detects from content, falls back to format hint)
        2. Parses rows using CSV reader
        3. Normalizes column headers and cell values (via table_normalization)
        4. Applies privacy rules and table limits
        5. Extracts entities from table rows
        
        The normalization step is critical: it standardizes column names and
        cell values, making tables queryable and consistent across sources.
        
        Args:
            path: Path to CSV/TSV file
            format_hint: Format hint ("csv" or "tsv")
            file_detail: File metadata
            upload: Optional upload (for privacy rules and limits)
        
        Returns:
            ExtractionResult with table, entities, and metadata
        
        Raises:
            KnowledgeIngestionError: If file is empty or contains no usable rows
        """
        raw_text = self._extract_text_file(path)
        # Remove BOM (Byte Order Mark) if present (common in Excel exports)
        normalized = raw_text.lstrip("\ufeff")
        if not normalized.strip():
            raise KnowledgeIngestionError("CSV document did not contain any rows.")
        
        # Auto-detect delimiter from content (handles various CSV formats)
        # Default to tab for TSV, comma for CSV
        delimiter = "\t" if format_hint == "tsv" else ","
        sample = normalized[:2048]  # Sample first 2KB for detection
        try:
            # Try to auto-detect delimiter (handles semicolon, pipe, etc.)
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
            delimiter = dialect.delimiter or delimiter
        except Exception:
            # Keep default delimiter if detection fails
            pass
        reader = csv.reader(io.StringIO(normalized), delimiter=delimiter)
        parsed_rows = [list(row) for row in reader]
        if not parsed_rows:
            raise KnowledgeIngestionError("CSV document did not contain any usable rows.")

        policy = resolve_normalization_policy(upload)
        sheet_label = (file_detail.filename or "CSV").strip() or "CSV"
        normalized_sheet = normalize_sheet_rows(parsed_rows, sheet_name=sheet_label, policy=policy)
        diagnostics = [normalized_sheet.diagnostics]
        if normalized_sheet.diagnostics.skipped:
            raise KnowledgeIngestionError("CSV document did not contain any usable rows.")

        column_schema = normalized_sheet.column_schema
        table_rows: list[TableRowPayload] = []
        for row_idx, values in enumerate(normalized_sheet.rows, start=1):
            cells: list[TableCellPayload] = []
            formatted_cells: list[str] = []
            for col_idx, column_key in enumerate(column_schema):
                value = values[col_idx] if col_idx < len(values) else ""
                formatted_cells.append(value)
                cells.append(
                    TableCellPayload(
                        row_index=row_idx,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=value,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_idx,
                    page_number=None,
                    raw_text="\t".join(formatted_cells),
                    metadata={"source": format_hint or "csv", "line_number": row_idx + 1},
                    cells=cells,
                )
            )
        table = TablePayload(
            order_index=1,
            title=file_detail.filename or "CSV Table",
            section_heading="",
            page_number=None,
            column_schema=column_schema,
            metadata={
                "source": "csv",
                "delimiter": delimiter,
                "filename": file_detail.filename,
            },
            rows=table_rows,
        )
        rules = self._table_privacy_rules(upload)
        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits([table], upload=upload, config=ingest_config)
        if not tables:
            raise KnowledgeIngestionError("CSV document did not contain rows within configured limits.")
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        preview_text = self._table_preview_text(tables, rules=rules)
        table_stats = self._table_stats_summary(
            total_rows=table_summary["total_rows"],
            indexed_rows=table_summary["indexed_rows"],
            row_cap=table_summary.get("row_cap_hint"),
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=table_summary.get("partial_tables", 0),
            row_tier=table_summary.get("row_tier_hint"),
        )
        normalization_summary = summarize_normalization(policy, diagnostics)
        issues = limit_issues
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(preview_text.strip()) / float(612 * 792),
            has_ocr_content=False,
            content_type="text/csv" if delimiter == "," else "text/tab-separated-values",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.TABLE,
                    order_index=0,
                    text=preview_text or normalized[:2000],
                    metadata={"source": "csv"},
                )
            ],
            metadata={"table_count": 1, "filename": file_detail.filename},
        )
        metadata = {
            "format": format_hint,
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
        }
        if normalization_summary:
            metadata["normalization"] = normalization_summary
        _log_normalization_summary(upload, "csv", normalization_summary)
        return ExtractionResult(
            text=preview_text or normalized,
            format_hint=format_hint,
            metadata=metadata,
            pages=[page],
            tables=tables,
            issues=issues,
            entities=table_entities,
        )

    def _build_table_from_normalized_sheet(
        self,
        normalized: NormalizedSheet,
        *,
        order_index: int,
        sheet_name: str,
        sheet_index: int | None,
        file_detail: KnowledgeUploadFile,
        source_label: str,
        content_type: str,
        rules: Mapping[str, Any] | None,
    ) -> tuple[TablePayload, PageLayout]:
        column_schema = normalized.column_schema
        table_rows: list[TableRowPayload] = []
        for row_idx, values in enumerate(normalized.rows, start=1):
            cells: list[TableCellPayload] = []
            formatted: list[str] = []
            for col_idx, column_key in enumerate(column_schema):
                value = values[col_idx] if col_idx < len(values) else ""
                formatted.append(value)
                cells.append(
                    TableCellPayload(
                        row_index=row_idx,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=value,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_idx,
                    page_number=sheet_index,
                    raw_text="\t".join(formatted),
                    metadata={"source": source_label, "sheet": sheet_name},
                    cells=cells,
                )
            )
        table = TablePayload(
            order_index=order_index,
            title=sheet_name,
            section_heading=sheet_name,
            page_number=sheet_index,
            column_schema=column_schema,
            metadata={
                "source": source_label,
                "sheet_name": sheet_name,
                "filename": file_detail.filename,
            },
            rows=table_rows,
        )
        preview = self._table_preview_text([table], rules=rules)
        page_layout = PageLayout(
            page_number=sheet_index or order_index,
            width=612,
            height=792,
            rotation=0,
            text_density=len(preview.strip()) / float(612 * 792),
            has_ocr_content=False,
            content_type=content_type,
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.TABLE,
                    order_index=order_index,
                    text=preview,
                    metadata={"source": source_label, "sheet": sheet_name},
                )
            ],
            metadata={"sheet_name": sheet_name},
        )
        return table, page_layout

    def _extract_xlsx(
        self,
        path: Path,
        *,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        if load_workbook is None:
            raise KnowledgeIngestionError("XLSX ingestion requires the openpyxl package.")
        try:
            workbook = load_workbook(filename=path, read_only=True, data_only=True)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open XLSX file: {exc}") from exc

        policy = resolve_normalization_policy(upload)
        diagnostics: list[SheetNormalizationDiagnostics] = []
        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        order_index = 1
        rules = self._table_privacy_rules(upload)
        for sheet_idx, sheet in enumerate(workbook.worksheets, start=1):
            sheet_name = sheet.title or f"Sheet {sheet_idx}"
            if not sheet_is_allowed(sheet_name, policy):
                diagnostics.append(
                    SheetNormalizationDiagnostics(sheet_name=sheet_name, skipped=True, skip_reason="policy")
                )
                continue
            normalized = normalize_sheet_rows(
                sheet.iter_rows(values_only=True),
                sheet_name=sheet_name,
                policy=policy,
            )
            diagnostics.append(normalized.diagnostics)
            if normalized.diagnostics.skipped:
                continue
            table, page_layout = self._build_table_from_normalized_sheet(
                normalized,
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                file_detail=file_detail,
                source_label="xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                rules=rules,
            )
            tables.append(table)
            pages.append(page_layout)
            order_index += 1

        original_tables = list(tables)
        if not original_tables:
            raise KnowledgeIngestionError("XLSX workbook did not contain any populated sheets.")
        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(original_tables, upload=upload, config=ingest_config)
        if not tables:
            raise KnowledgeIngestionError("XLSX workbook exceeded configured limits and no rows were indexed.")
        preview_text = self._table_preview_text(tables, rules=rules)
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        table_stats = self._table_stats_summary(
            total_rows=table_summary["total_rows"],
            indexed_rows=table_summary["indexed_rows"],
            row_cap=table_summary.get("row_cap_hint"),
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=table_summary.get("partial_tables", 0),
            row_tier=table_summary.get("row_tier_hint"),
        )
        normalization_summary = summarize_normalization(policy, diagnostics)
        metadata = {
            "format": "xlsx",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
        }
        if normalization_summary:
            metadata["normalization"] = normalization_summary
        _log_normalization_summary(upload, "xlsx", normalization_summary)
        return ExtractionResult(
            text=preview_text,
            format_hint="xlsx",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=limit_issues,
            entities=table_entities,
        )

    def _extract_xls(
        self,
        path: Path,
        *,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        if xlrd is None:
            raise KnowledgeIngestionError("XLS ingestion requires the xlrd package.")
        try:
            workbook = xlrd.open_workbook(filename=str(path))
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open XLS file: {exc}") from exc

        policy = resolve_normalization_policy(upload)
        diagnostics: list[SheetNormalizationDiagnostics] = []
        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        order_index = 1
        rules = self._table_privacy_rules(upload)
        for sheet_idx in range(1, workbook.nsheets + 1):
            sheet = workbook.sheet_by_index(sheet_idx - 1)
            sheet_name = getattr(sheet, "name", None) or f"Sheet {sheet_idx}"
            if not sheet_is_allowed(sheet_name, policy):
                diagnostics.append(
                    SheetNormalizationDiagnostics(sheet_name=sheet_name, skipped=True, skip_reason="policy")
                )
                continue
            raw_rows: list[list[Any]] = []
            nrows = getattr(sheet, "nrows", 0) or 0
            ncols = getattr(sheet, "ncols", 0) or 0
            for row_idx in range(nrows):
                row_values: list[Any] = []
                for col_idx in range(ncols):
                    cell_value = sheet.cell_value(row_idx, col_idx)
                    cell_type = sheet.cell_type(row_idx, col_idx)
                    if cell_type == xlrd.XL_CELL_DATE:
                        try:
                            cell_value = xlrd.xldate_as_datetime(cell_value, workbook.datemode)
                        except Exception:
                            cell_value = ""
                    elif cell_type == xlrd.XL_CELL_BOOLEAN:
                        cell_value = bool(cell_value)
                    elif cell_type == xlrd.XL_CELL_ERROR:
                        cell_value = ""
                    row_values.append(cell_value)
                raw_rows.append(row_values)
            normalized = normalize_sheet_rows(
                raw_rows,
                sheet_name=sheet_name,
                policy=policy,
            )
            diagnostics.append(normalized.diagnostics)
            if normalized.diagnostics.skipped:
                continue
            table, page_layout = self._build_table_from_normalized_sheet(
                normalized,
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                file_detail=file_detail,
                source_label="xls",
                content_type="application/vnd.ms-excel",
                rules=rules,
            )
            tables.append(table)
            pages.append(page_layout)
            order_index += 1

        original_tables = list(tables)
        if not original_tables:
            raise KnowledgeIngestionError("XLS workbook did not contain any populated sheets.")
        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(
            original_tables,
            upload=upload,
            config=ingest_config,
        )
        if not tables:
            raise KnowledgeIngestionError("XLS workbook exceeded configured limits and no rows were indexed.")
        preview_text = self._table_preview_text(tables, rules=rules)
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        table_stats = self._table_stats_summary(
            total_rows=table_summary["total_rows"],
            indexed_rows=table_summary["indexed_rows"],
            row_cap=table_summary.get("row_cap_hint"),
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=table_summary.get("partial_tables", 0),
            row_tier=table_summary.get("row_tier_hint"),
        )
        normalization_summary = summarize_normalization(policy, diagnostics)
        metadata = {
            "format": "xls",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
        }
        if normalization_summary:
            metadata["normalization"] = normalization_summary
        _log_normalization_summary(upload, "xls", normalization_summary)
        return ExtractionResult(
            text=preview_text,
            format_hint="xls",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=limit_issues,
            entities=table_entities,
        )

    def _extract_json(self, path: Path, *, entity_limit: int | None = None) -> ExtractionResult:
        raw_text = self._extract_text_file(path)
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise KnowledgeIngestionError(f"Invalid JSON document: {exc}") from exc

        entities, alias_sources = self._json_entities_from_data(data)
        if not entities:
            return ExtractionResult(
                text=raw_text,
                format_hint="json",
                metadata={"json_entity_count": 0},
                pages=[],
                tables=[],
                issues=[
                    IssuePayload(
                        code="json_entities_not_detected",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description="JSON document did not contain a recognizable list of entities.",
                    )
                ],
            )

        limit = max(1, int(entity_limit or self.default_json_entity_limit))
        limited_entities = entities[: limit]
        issues: list[IssuePayload] = []
        truncated_count = max(0, len(entities) - len(limited_entities))
        if truncated_count:
            issues.append(
                IssuePayload(
                    code="json_entities_truncated",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description=f"Captured first {limit} entities out of {len(entities)}.",
                )
            )

        tables: list[TablePayload] = []
        summaries: list[str] = []
        for order_index, entity in enumerate(limited_entities, start=1):
            column_schema = entity["columns"]
            attributes = entity["attributes"]
            entity_name = entity["entity_name"]
            entity_type = entity["entity_type"]
            aliases = entity.get("aliases") or []

            cells = [
                TableCellPayload(
                    row_index=0,
                    column_index=col_idx,
                    column_key=column,
                    raw_text=attributes.get(column, ""),
                    metadata={},
                )
                for col_idx, column in enumerate(column_schema)
            ]
            row = TableRowPayload(
                row_index=0,
                page_number=None,
                raw_text=" | ".join(
                    f"{column}: {attributes.get(column, '')}" for column in column_schema if attributes.get(column)
                ),
                metadata={"entity_name": entity_name, **self._alias_metadata(aliases)},
                cells=cells,
            )
            tables.append(
                TablePayload(
                    order_index=order_index,
                    title=entity_name or f"{entity_type.title()} {order_index}",
                    section_heading=entity_type.title(),
                    page_number=None,
                    column_schema=column_schema,
                    metadata={
                        "entity_type": entity_type,
                        "entity_name": entity_name,
                        "entity_business": entity["entity_business"],
                        "json_entity": True,
                        **self._alias_metadata(aliases),
                    },
                    rows=[row],
                )
            )
            summary_text = self._render_json_entity_summary(
                entity_title=entity_name or f"{entity_type.title()} {order_index}",
                entity_type=entity_type,
                column_schema=column_schema,
                attributes=attributes,
            )
            if aliases:
                summary_text = self._append_identifier_line(summary_text, aliases)
            summaries.append(summary_text)

        text = "\n\n".join(summaries) if summaries else raw_text
        metadata = {
            "json_entity_count": len(entities),
            "json_entities_indexed": len(limited_entities),
            "json_entity_type": limited_entities[0]["entity_type"] if limited_entities else "record",
            "json_entity_limit": limit,
            "json_entities_truncated": truncated_count,
            "json_alias_sources": sorted(alias_sources),
        }
        return ExtractionResult(
            text=text,
            format_hint="json",
            metadata=metadata,
            pages=[],
            tables=tables,
            issues=issues,
            entities=limited_entities,
        )

    def _json_entities_from_data(self, data: Any) -> tuple[list[dict[str, Any]], set[str]]:
        """
        Extract entities from JSON data structure.
        
        This method walks the JSON structure to find entity-like records (dicts
        with identifier fields). It:
        1. Iterates through nested structures to find records
        2. Flattens nested records to key-value pairs
        3. Filters to structured records (have identifier fields)
        4. Extracts aliases and builds entity payloads
        
        The method respects max_json_entity_candidates to prevent processing
        extremely large JSON files. This limit is higher than the final entity
        limit to allow for filtering.
        
        Args:
            data: JSON data (dict, list, or nested structure)
        
        Returns:
            Tuple of (entity list, union of alias sources)
        """
        entities: list[dict[str, Any]] = []
        alias_sources_union: set[str] = set()
        for record_label, record in self._iter_json_entity_records(data):
            flattened = self._flatten_json_record(record)
            # Filter to structured records (have identifier fields like name, id, etc.)
            if not flattened or not self._is_structured_record(record, flattened):
                continue
            entity_index = len(entities)
            entity_name = self._infer_entity_name(record_label, record, flattened, entity_index)
            entity_business = (
                record.get("business")
                or record.get("company")
                or record.get("brand")
                or flattened.get("business")
                or flattened.get("company")
            )
            columns = self._select_entity_columns(flattened)
            if not columns:
                columns = list(flattened.keys())
            attributes = {column: flattened.get(column, "") for column in columns}
            aliases, alias_sources = self._collect_aliases_from_record(
                record=record,
                flattened=flattened,
                attributes=attributes,
                entity_name=entity_name,
            )
            alias_sources_union.update(alias_sources)
            entities.append(
                {
                    "entity_type": (record_label.rstrip("s") or record_label or "record").lower(),
                    "entity_name": entity_name,
                    "entity_business": entity_business,
                    "columns": columns,
                    "attributes": attributes,
                    "aliases": aliases,
                    "alias_sources": sorted(alias_sources),
                    "entity_index": entity_index,
                }
            )
            if len(entities) >= self.max_json_entity_candidates:
                break
        return entities, alias_sources_union

    def _iter_json_entity_records(self, data: Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
        """
        Iterate through JSON structure to find entity-like records.
        
        This method performs a breadth-first traversal of nested JSON structures
        to find dict objects that might be entities. It handles:
        - Top-level dicts or lists
        - Nested dicts in object properties
        - Lists of dicts (common entity format)
        
        The method uses object identity (id()) to avoid processing the same
        record twice (prevents infinite loops on circular references).
        
        Label inference: Uses the key name (e.g., "products" -> "product") to
        infer entity type. This helps with entity naming and categorization.
        
        Args:
            data: JSON data structure (any type)
        
        Yields:
            Tuples of (entity_label, record_dict) for each potential entity
        """
        queue: deque[tuple[str, Any]] = deque()
        # Initialize queue with top-level structure
        if isinstance(data, dict):
            queue.append(("record", data))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    queue.append(("record", item))

        # Track seen objects by identity (prevents duplicate processing)
        seen_ids: set[int] = set()
        while queue and len(seen_ids) < self.max_json_entity_candidates:
            label, record = queue.popleft()
            if not isinstance(record, dict):
                continue
            marker = id(record)
            if marker in seen_ids:
                continue  # Already processed (prevents cycles)
            seen_ids.add(marker)
            yield label, record
            if len(seen_ids) >= self.max_json_entity_candidates:
                break
            # Enqueue nested structures for traversal
            for key, value in record.items():
                # Infer label from key (e.g., "products" -> "product")
                next_label = key.rstrip("s") or key or label
                if isinstance(value, dict):
                    queue.append((next_label, value))
                elif isinstance(value, list):
                    for entry in value:
                        if isinstance(entry, dict):
                            queue.append((next_label, entry))

    @staticmethod
    def _flatten_json_record(record: Mapping[str, Any]) -> dict[str, str]:
        """
        Flatten a nested JSON record into a flat key-value dictionary.
        
        This method recursively walks nested structures and creates dot-separated
        keys (e.g., "pricing.adult_price_per_person"). This enables entity
        attributes to be accessed uniformly regardless of nesting depth.
        
        Special handling:
        - Lists of scalars: Joined with commas (limited to 5 items)
        - Lists of dicts: Extracts count and first item's fields (limited to 3)
        - Empty values: Filtered out in final result
        
        The flattening is essential for entity extraction: it makes nested
        attributes queryable and enables alias detection across all levels.
        
        Args:
            record: Nested JSON record (dict)
        
        Returns:
            Flat dictionary with dot-separated keys and string values
        """
        result: dict[str, str] = {}

        def visit(prefix: str, value: Any) -> None:
            """Recursive visitor to flatten nested structures."""
            key_prefix = prefix.strip(".")
            if isinstance(value, dict):
                # Recurse into nested dicts, building dot-separated keys
                for sub_key, sub_val in value.items():
                    if sub_key is None:
                        continue
                    next_prefix = f"{prefix}.{sub_key}" if prefix else str(sub_key)
                    visit(next_prefix, sub_val)
            elif isinstance(value, list):
                if not value:
                    result[key_prefix] = ""
                    return
                # Lists of scalars: join with commas (limit to 5 for readability)
                scalar_items = [item for item in value if isinstance(item, (str, int, float, bool))]
                if scalar_items and len(scalar_items) == len(value):
                    joined = ", ".join(KnowledgeIngestionService._stringify_json_scalar(item) for item in scalar_items[:5])
                    if len(value) > 5:
                        joined = f"{joined} …"  # Indicate truncation
                    result[key_prefix] = joined
                    return
                # Lists of dicts: extract count and sample from first item
                dict_items = [item for item in value if isinstance(item, dict)]
                if dict_items:
                    result[f"{key_prefix}_count"] = str(len(dict_items))
                    first = dict_items[0]
                    # Extract first 3 fields from first dict (gives structure hint)
                    for sub_key, sub_val in list(first.items())[:3]:
                        nested_key = f"{key_prefix}_0_{sub_key}".strip("_")
                        result[nested_key] = KnowledgeIngestionService._stringify_json_scalar(sub_val)
                    return
                # Fallback: stringify the list itself
                result[key_prefix] = KnowledgeIngestionService._stringify_json_scalar(value)
            else:
                # Scalar value: convert to string
                result[key_prefix] = KnowledgeIngestionService._stringify_json_scalar(value)

        visit("", record)

        trips = record.get("trips")
        if isinstance(trips, list):
            result["trip_count"] = str(len(trips))
            trip_titles = [
                item.get("title")
                for item in trips
                if isinstance(item, dict) and isinstance(item.get("title"), str)
            ]
            if trip_titles:
                result["trip_titles"] = "; ".join(trip_titles[:3])
            prices: list[str] = []
            for trip in trips:
                if not isinstance(trip, dict):
                    continue
                pricing = trip.get("pricing")
                if isinstance(pricing, dict):
                    price = (
                        pricing.get("adult_price_per_person")
                        or pricing.get("price")
                        or pricing.get("starts_at")
                    )
                    if price:
                        prices.append(KnowledgeIngestionService._stringify_json_scalar(price))
                if len(prices) >= 3:
                    break
            if prices:
                result["trip_prices"] = ", ".join(prices)

        return {k: v for k, v in result.items() if k and v is not None}

    @staticmethod
    def _is_structured_record(record: Mapping[str, Any], flattened: Mapping[str, str]) -> bool:
        """
        Determine if a JSON record is a structured entity (not just metadata).
        
        This method filters out noise from JSON structures. A record is considered
        structured if it:
        1. Has identifier fields (name, id, code, etc.) - strong signal
        2. Has at least 2 populated fields - weak signal (filters empty objects)
        
        This prevents processing configuration objects, metadata, or empty
        structures as entities. Only records that look like actual business
        entities (products, customers, trips, etc.) are extracted.
        
        Args:
            record: Original nested record
            flattened: Flattened key-value dictionary
        
        Returns:
            True if record appears to be a structured entity
        """
        if not flattened:
            return False
        # Check for identifier fields (strong signal of entity)
        key_hints = (
            "name",
            "title",
            "slug",
            "label",
            "code",
            "id",
            "identifier",
            "question",
            "destination",
            "city",
            "product",
            "sku",
            "reference",
        )
        for key in key_hints:
            # Check both original record and flattened (handles nesting)
            direct = record.get(key) if isinstance(record, Mapping) else None
            indirect = flattened.get(key)
            value = direct or indirect
            if isinstance(value, str) and value.strip():
                return True  # Has identifier field
            if isinstance(value, (int, float)):
                return True  # Has numeric identifier
        # Fallback: require at least 2 populated fields (filters empty/trivial objects)
        populated = sum(1 for value in flattened.values() if value)
        return populated >= 2

    @staticmethod
    def _stringify_json_scalar(value: Any) -> str:
        """
        Convert a JSON scalar value to a string representation.
        
        Used when flattening JSON records: all values must be strings for
        the flat dictionary. Handles None, booleans, numbers, and strings
        with appropriate conversions. Complex values are JSON-encoded and
        truncated to 500 chars to prevent oversized strings.
        
        Args:
            value: Scalar value (any type)
        
        Returns:
            String representation (empty string for None, truncated for complex values)
        """
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value.strip()
        # Complex values: JSON encode and truncate (prevents oversized strings)
        return json.dumps(value, ensure_ascii=False)[:500]

    def _table_privacy_rules(self, upload: KnowledgeUpload | None) -> dict[str, Any]:
        rules = {
            "sensitive_exact": set(),
            "sensitive_patterns": [],
            "row_flag_column": None,
            "row_flag_values": set(),
        }

        def merge(config: Mapping[str, Any] | None) -> None:
            if not isinstance(config, Mapping):
                return
            columns = config.get("sensitive_columns") or []
            patterns = config.get("sensitive_column_patterns") or []
            for entry in columns:
                value = str(entry or "").strip()
                if not value:
                    continue
                if self._looks_like_regex(value):
                    try:
                        rules["sensitive_patterns"].append(re.compile(value, re.IGNORECASE))
                    except re.error:
                        continue
                else:
                    rules["sensitive_exact"].add(value.lower())
            for entry in patterns:
                value = str(entry or "").strip()
                if not value:
                    continue
                try:
                    rules["sensitive_patterns"].append(re.compile(value, re.IGNORECASE))
                except re.error:
                    continue
            column = config.get("row_flag_column")
            if column:
                canonical = self._canonical_column_name(str(column))
                if canonical:
                    rules["row_flag_column"] = canonical
            values = config.get("row_flag_values") or []
            combined = set(rules["row_flag_values"])
            for value in values:
                token = str(value or "").strip().lower()
                if token:
                    combined.add(token)
            rules["row_flag_values"] = combined

        business_meta = getattr(getattr(upload, "business_profile", None), "metadata", {})
        upload_meta = getattr(upload, "metadata", {})
        merge(business_meta.get("table_privacy") or business_meta.get("sensitive_table_config"))
        merge(upload_meta.get("table_privacy") or upload_meta.get("sensitive_table_config"))
        return rules

    def _table_ingest_config(self, upload: KnowledgeUpload | None) -> dict[str, Any]:
        config = {
            "max_rows": self.default_table_max_rows,
            "max_columns": self.default_table_max_columns if self.default_table_max_columns > 0 else None,
            "column_whitelist": set(),
            "max_rows_source": "default",
            "small_row_limit": int(getattr(settings, "RAG_TABLE_SMALL_ROW_LIMIT", 2000)),
            "large_row_limit": int(getattr(settings, "RAG_TABLE_LARGE_ROW_LIMIT", 20000)),
            "hard_row_cap": int(getattr(settings, "RAG_TABLE_MAX_HARD_CAP", 100000)),
        }

        def merge(source: Mapping[str, Any] | None) -> None:
            if not isinstance(source, Mapping):
                return
            rows = source.get("max_rows")
            columns = source.get("max_columns")
            whitelist = source.get("column_whitelist")
            try:
                if rows is not None:
                    value = int(rows)
                    if value > 0:
                        config["max_rows"] = value
                        config["max_rows_source"] = "override"
            except (TypeError, ValueError):
                pass
            try:
                if columns is not None:
                    value = int(columns)
                    config["max_columns"] = value if value > 0 else None
            except (TypeError, ValueError):
                pass
            if isinstance(whitelist, (list, tuple, set)):
                normalized = {
                    self._canonical_column_name(str(item))
                    for item in whitelist
                    if self._canonical_column_name(str(item))
                }
                if normalized:
                    config["column_whitelist"] = normalized

        business_meta = getattr(getattr(upload, "business_profile", None), "metadata", {})
        upload_meta = getattr(upload, "metadata", {})
        merge(business_meta.get("table_limits"))
        merge(upload_meta.get("table_limits"))
        return config

    @staticmethod
    def _determine_table_row_cap(
        row_count: int,
        config: Mapping[str, Any],
    ) -> tuple[int, str, str]:
        """
        Decide how many rows to keep for a given table based on tiering thresholds.
        Returns (row_cap, tier, strategy).
        """
        base_cap = max(1, int(config.get("max_rows", 1)))
        small_limit = max(1, int(config.get("small_row_limit", 2000)))
        large_limit = max(small_limit, int(config.get("large_row_limit", 20000)))
        hard_cap = max(1, int(config.get("hard_row_cap", 100000)))
        override = config.get("max_rows_source") == "override"
        if row_count <= 0:
            return base_cap, "unknown", "default"
        if not override and row_count <= large_limit:
            tier = "small" if row_count <= small_limit else "medium"
            return row_count, tier, "full"
        tier = "large" if row_count > large_limit else "override"
        effective_cap = min(base_cap, hard_cap)
        strategy = "override" if override and tier != "large" else "capped"
        return effective_cap, tier, strategy

    @staticmethod
    def _integration_row_count(upload: KnowledgeUpload | None) -> int | None:
        """
        Best-effort row count provided by integrations (if any). Returns None when unavailable.
        """
        if not upload:
            return None
        metadata = getattr(upload, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        resource = metadata.get("integration_resource")
        if not isinstance(resource, Mapping):
            return None
        raw_value = resource.get("row_count")
        try:
            count = int(raw_value)
        except (TypeError, ValueError):
            return None
        return count if count > 0 else None

    @staticmethod
    def _table_stats_summary(
        *,
        total_rows: int,
        indexed_rows: int,
        row_cap: int | None,
        source_row_count: int | None,
        table_count: int,
        partial_tables: int = 0,
        row_tier: str | None = None,
    ) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "total_rows": max(0, int(total_rows)),
            "indexed_rows": max(0, int(indexed_rows)),
            "table_count": max(0, int(table_count)),
        }
        if row_cap is not None:
            stats["row_cap"] = max(0, int(row_cap))
        if source_row_count is not None:
            stats["source_row_count"] = max(0, int(source_row_count))
        if partial_tables:
            stats["partial_tables"] = max(0, int(partial_tables))
        if row_tier:
            stats["row_tier"] = row_tier
        if stats["indexed_rows"] < stats["total_rows"] or stats.get("partial_tables"):
            stats["partial_index"] = True
        return stats

    def _apply_table_limits(
        self,
        tables: Sequence[TablePayload],
        *,
        upload: KnowledgeUpload | None,
        config: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, int], list[IssuePayload], dict[str, Any]]:
        """
        Apply row and column limits to tables based on configuration.
        
        This method implements a tiered row limit strategy:
        - Small tables (≤2000 rows): Index all rows
        - Medium tables (2000-20000 rows): Index all rows
        - Large tables (>20000 rows): Apply hard cap (default 5000 rows)
        - Override: Business-specific limits override defaults
        
        The tiering prevents embedding costs from exploding on large tables
        while still indexing full content for smaller tables. Column limits
        are applied uniformly to all tables.
        
        Args:
            tables: Tables to apply limits to
            upload: Optional upload (for business-specific config)
            config: Optional limit configuration (defaults to upload/business config)
        
        Returns:
            Tuple of:
            - Limited tables (with rows/columns trimmed)
            - Truncation metrics (tables/rows/columns truncated)
            - Issues for truncated content
            - Summary statistics (total rows, indexed rows, tier, etc.)
        """
        if not tables:
            empty_metrics = {"truncated_tables": 0, "truncated_rows": 0, "truncated_columns": 0}
            empty_summary = {"total_rows": 0, "indexed_rows": 0, "partial_tables": 0, "row_cap_hint": 0, "row_tier_hint": "unknown"}
            return [], empty_metrics, [], empty_summary
        
        # Get effective config: merge upload config, business config, and defaults
        effective_config = dict(config) if isinstance(config, Mapping) else self._table_ingest_config(upload)
        limited: list[TablePayload] = []
        truncated_tables = 0
        truncated_rows = 0
        truncated_columns = 0
        limit_issues: list[IssuePayload] = []
        summary = {
            "total_rows": 0,
            "indexed_rows": 0,
            "partial_tables": 0,
            "row_cap_hint": 0,
            "row_tier_hint": "unknown",
        }
        
        # Tier ranking for summary: tracks the "worst" tier across all tables
        # This helps identify if any tables hit limits
        tier_rank = {"unknown": 0, "small": 1, "medium": 2, "override": 2, "large": 3}
        
        for table in tables:
            original_row_count = len(table.rows or [])
            summary["total_rows"] += original_row_count
            
            # Determine row cap based on tiering strategy
            # Small/medium: no cap, large: apply cap, override: use custom limit
            table_row_cap, tier, strategy = self._determine_table_row_cap(original_row_count, effective_config)
            summary["row_cap_hint"] = max(summary["row_cap_hint"], table_row_cap)
            
            # Track highest tier (for summary reporting)
            if tier_rank.get(tier, 0) > tier_rank.get(summary["row_tier_hint"], 0):
                summary["row_tier_hint"] = tier
            limited_table, removed_columns, removed_rows, dropped_table = self._limit_table_payload(
                table,
                max_rows=table_row_cap,
                max_columns=effective_config["max_columns"],
                column_whitelist=effective_config["column_whitelist"],
            )
            indexed_row_count = len(limited_table.rows or []) if limited_table else 0
            summary["indexed_rows"] += indexed_row_count
            if removed_rows or dropped_table:
                summary["partial_tables"] += 1
            truncated_columns += removed_columns
            truncated_rows += removed_rows
            if removed_rows or dropped_table:
                truncated_amount = removed_rows if not dropped_table else original_row_count
                description = (
                    f"Only the first {indexed_row_count} of {original_row_count} rows were ingested; remaining rows are unavailable."
                )
                if dropped_table:
                    description = (
                        f"Table '{table.title}' exceeded row limits; no rows were indexed."
                    )
                limit_issues.append(
                    IssuePayload(
                        code="table_rows_truncated",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=description,
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={
                            "initial_rows": original_row_count,
                            "indexed_rows": indexed_row_count,
                            "truncated_rows": truncated_amount,
                            "row_cap": table_row_cap,
                            "row_tier": tier,
                            "strategy": strategy,
                        },
                    )
                )
            if removed_columns:
                limit_issues.append(
                    IssuePayload(
                        code="table_columns_truncated",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"{removed_columns} columns were trimmed due to configured limits.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={
                            "removed_columns": removed_columns,
                            "max_columns": effective_config["max_columns"],
                            "row_tier": tier,
                        },
                    )
                )
            if dropped_table:
                truncated_tables += 1
                continue
            limited.append(limited_table)
        metrics = {
            "truncated_tables": truncated_tables,
            "truncated_rows": truncated_rows,
            "truncated_columns": truncated_columns,
        }
        if (truncated_tables or truncated_rows or truncated_columns) and upload:
            logger.info(
                "table.truncation upload=%s rows=%s columns=%s tables=%s limits=%s",
                upload.id,
                truncated_rows,
                truncated_columns,
                truncated_tables,
                effective_config,
            )
        return limited, metrics, limit_issues, summary

    def _limit_table_payload(
        self,
        table: TablePayload,
        *,
        max_rows: int,
        max_columns: int | None,
        column_whitelist: set[str],
    ) -> tuple[TablePayload | None, int, int, bool]:
        """
        Apply row and column limits to a table payload.
        
        This method creates a new table with trimmed rows and columns based on
        configuration. It handles:
        - Row limits: keeps first N rows, drops the rest
        - Column limits: keeps first N columns or whitelisted columns
        - Column whitelist: only includes specified columns (if provided)
        
        Returns None if all columns are filtered out (table becomes empty).
        
        Args:
            table: Table to limit
            max_rows: Maximum number of rows to keep
            max_columns: Maximum number of columns (None = no limit)
            column_whitelist: Set of canonical column names to keep (empty = all)
        
        Returns:
            Tuple of:
            - Limited table (or None if empty)
            - Number of columns removed
            - Number of rows removed
            - Whether table was dropped entirely
        """
        schema = list(table.column_schema or [])
        plan: list[tuple[int, str]] = []  # (original_index, column_label) for kept columns
        removed_columns = 0
        column_limit = max_columns if isinstance(max_columns, int) and max_columns > 0 else None
        canonical_whitelist = set(column_whitelist or set())
        
        # Build column plan: determine which columns to keep
        for idx, column in enumerate(schema):
            label = column or f"column_{idx + 1}"
            canonical = self._canonical_column_name(label, f"column_{idx + 1}")
            
            # Filter by whitelist if provided
            if canonical_whitelist and canonical not in canonical_whitelist:
                removed_columns += 1
                continue
            
            # Filter by column limit
            if column_limit is not None and len(plan) >= column_limit:
                removed_columns += 1
                continue
            
            plan.append((idx, label))
        
        # If no columns remain, drop the table entirely
        if not plan:
            return None, len(schema), len(table.rows or []), True
        allowed_indices = [item[0] for item in plan]
        rows = list(table.rows or [])
        new_rows: list[TableRowPayload] = []
        removed_rows = 0
        for row in rows:
            if len(new_rows) >= max_rows:
                removed_rows += 1
                continue
            new_cells: list[TableCellPayload] = []
            cell_lookup = {cell.column_index: cell for cell in row.cells}
            for new_idx, original_idx in enumerate(allowed_indices):
                original = cell_lookup.get(original_idx)
                if original:
                    new_cells.append(
                        TableCellPayload(
                            row_index=row.row_index,
                            column_index=new_idx,
                            column_key=plan[new_idx][1],
                            raw_text=original.raw_text,
                            normalized_value=original.normalized_value,
                            bbox=original.bbox,
                            confidence=original.confidence,
                            metadata=original.metadata,
                        )
                    )
                else:
                    new_cells.append(
                        TableCellPayload(
                            row_index=row.row_index,
                            column_index=new_idx,
                            column_key=plan[new_idx][1],
                            raw_text="",
                            normalized_value={},
                            bbox={},
                            confidence=None,
                            metadata={},
                        )
                    )
            row_text = "\t".join(cell.raw_text for cell in new_cells if cell.raw_text) or row.raw_text
            new_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row_text,
                    metadata=row.metadata,
                    cells=new_cells,
                )
            )
        if not new_rows:
            return None, removed_columns, len(rows), True
        new_table = TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=[label for _, label in plan],
            data_dictionary=table.data_dictionary,
            metadata=table.metadata,
            rows=new_rows,
        )
        return new_table, removed_columns, removed_rows, False

    @staticmethod
    def _looks_like_regex(pattern: str) -> bool:
        """
        Heuristically detect if a string looks like a regex pattern.
        
        Checks for common regex metacharacters that indicate the pattern
        should be compiled as a regex rather than used as a literal string.
        
        Args:
            pattern: String to check
        
        Returns:
            True if string appears to be a regex pattern
        """
        return bool(pattern) and (
            pattern.startswith("^")  # Start anchor
            or pattern.endswith("$")  # End anchor
            or any(ch in pattern for ch in "[]().*+?|")  # Regex metacharacters
        )

    @staticmethod
    def _canonical_column_name(value: str | None, fallback: str | None = None) -> str:
        """
        Normalize a column name to canonical form.
        
        Converts to lowercase and normalizes whitespace. This enables
        case-insensitive and whitespace-tolerant column matching for
        privacy rules and whitelists.
        
        Args:
            value: Column name to normalize
            fallback: Fallback value if value is empty
        
        Returns:
            Normalized column name (lowercase, single spaces)
        """
        if isinstance(value, str):
            lowered = re.sub(r"\s+", " ", value.strip().lower())
            if lowered:
                return lowered
        if fallback:
            return fallback.strip().lower()
        return ""

    def _column_is_sensitive(self, column: str, rules: Mapping[str, Any]) -> bool:
        """
        Check if a column should be hidden based on privacy rules.
        
        Matches against exact column names and regex patterns configured
        in privacy rules. Used to filter sensitive columns from chunks and
        table previews.
        
        Args:
            column: Column name to check
            rules: Privacy rules with sensitive_exact and sensitive_patterns
        
        Returns:
            True if column should be hidden
        """
        canonical = self._canonical_column_name(column)
        # Check exact matches first (faster)
        if canonical and canonical in rules.get("sensitive_exact", set()):
            return True
        
        # Check regex patterns (slower but more flexible)
        patterns = rules.get("sensitive_patterns") or []
        combined = column or ""
        for pattern in patterns:
            try:
                # Check both original and canonical (handles case variations)
                if pattern.search(combined) or (canonical and pattern.search(canonical)):
                    return True
            except re.error:
                continue  # Invalid pattern - skip it
        return False

    def _row_is_internal(self, attributes: Mapping[str, str], rules: Mapping[str, Any]) -> bool:
        """
        Check if a table row should be excluded as "internal" data.
        
        Some businesses flag certain rows (e.g., "internal", "test", "draft")
        that shouldn't be indexed. This method checks if a row matches
        the configured flag column and values.
        
        Args:
            attributes: Row attributes (column -> value mapping)
            rules: Privacy rules with row_flag_column and row_flag_values
        
        Returns:
            True if row should be excluded
        """
        column = rules.get("row_flag_column")
        values = rules.get("row_flag_values") or set()
        if not column or not values:
            return False  # No flagging configured
        
        # Check if any attribute matches the flag column and value
        for key, value in attributes.items():
            canonical = self._canonical_column_name(key)
            if canonical == column and str(value or "").strip().lower() in values:
                return True
        return False

    def _table_preview_text(
        self,
        tables: Sequence[TablePayload],
        *,
        row_limit: int = 5,
        rules: Mapping[str, Any] | None = None,
    ) -> str:
        """
        Generate TSV-formatted preview text from tables.
        
        Creates a compact text representation of tables for embedding and
        display. Applies privacy rules to hide sensitive columns and exclude
        internal rows. Limited to first N rows per table to keep previews
        manageable.
        
        The preview format is TSV (tab-separated) which preserves column
        structure for better semantic understanding by LLMs.
        
        Args:
            tables: Tables to generate preview for
            row_limit: Maximum rows per table (default 5)
            rules: Optional privacy rules for filtering
        
        Returns:
            TSV-formatted table preview text
        """
        if not tables:
            return ""
        lines: list[str] = []
        for table in tables:
            # Filter out sensitive columns if privacy rules provided
            visible_columns = table.column_schema
            if rules:
                visible_columns = [col for col in table.column_schema if not self._column_is_sensitive(col, rules)]
            if not visible_columns:
                continue  # All columns hidden - skip table
            
            # Add table title as context
            if table.title:
                lines.append(f"[Table] {table.title}")
            
            # Add header row
            header_line = "\t".join(visible_columns)
            if header_line.strip():
                lines.append(header_line)
            
            # Add data rows (limited and filtered)
            for row in table.rows[:row_limit]:
                attributes = self._row_attributes_from_table(row, table.column_schema)
                # Skip internal rows if privacy rules configured
                if rules and self._row_is_internal(attributes, rules):
                    continue
                values = [attributes.get(column, "") for column in visible_columns]
                if any(values):  # Only add non-empty rows
                    lines.append("\t".join(values))
            lines.append("")  # Blank line between tables
        return "\n".join(lines).strip()

    def _table_row_entities(
        self,
        tables: Sequence[TablePayload],
        *,
        business_profile=None,
        upload: KnowledgeUpload | None = None,
    ) -> list[dict[str, Any]]:
        """
        Extract entities from table rows for lookup and search.
        
        Each table row becomes an entity with:
        - Entity type derived from table metadata/title
        - Entity name inferred from row attributes
        - Aliases extracted from identifier fields
        - Attributes filtered by privacy rules
        
        Entities enable precise lookup in RAG queries (e.g., "find product ABC123").
        Privacy rules are applied to exclude sensitive columns and internal rows.
        
        Args:
            tables: Tables to extract entities from
            business_profile: Business profile (for entity attribution)
            upload: Knowledge upload (for privacy rules and visibility)
        
        Returns:
            List of entity dictionaries ready for persistence
        """
        entities: list[dict[str, Any]] = []
        business_name = getattr(business_profile, "name", None)
        rules = self._table_privacy_rules(upload)
        
        for table_idx, table in enumerate(tables):
            # Derive entity type from table metadata (e.g., "product", "customer")
            entity_type = self._derive_table_entity_type(table, table_idx)
            column_schema = table.column_schema or []
            
            for row in table.rows:
                entity_index = len(entities)
                attributes = self._row_attributes_from_table(row, column_schema)
                
                # Skip internal rows (flagged for exclusion)
                if self._row_is_internal(attributes, rules):
                    continue
                
                # Filter sensitive columns
                visible_columns = [
                    column for column in column_schema if not self._column_is_sensitive(column, rules)
                ]
                if not visible_columns:
                    continue  # All columns hidden - skip row
                
                limited_attributes = {column: attributes.get(column, "") for column in visible_columns}
                
                # Infer entity name from row attributes (e.g., "name", "title", "sku")
                entity_name = self._infer_table_row_entity_name(
                    entity_type,
                    table,
                    limited_attributes,
                    row.row_index,
                )
                
                # Build flattened record for alias extraction
                flattened = dict(limited_attributes)
                flattened["table_title"] = table.title or ""
                flattened["sheet_name"] = (table.metadata or {}).get("sheet_name", "")
                flattened["table_order_index"] = str(table.order_index)
                
                # Select important columns for entity representation
                columns = self._select_entity_columns(limited_attributes)
                if not columns:
                    columns = list(limited_attributes.keys())
                limited_attributes = {column: limited_attributes.get(column, "") for column in columns}
                
                # Extract aliases (identifiers for lookup)
                aliases, alias_sources = self._collect_aliases_from_record(
                    record=flattened,
                    flattened=flattened,
                    attributes=limited_attributes,
                    entity_name=entity_name,
                )
                
                table_meta = {
                    "table_order_index": table.order_index,
                    "table_title": table.title,
                    "section_heading": table.section_heading,
                    "sheet_name": (table.metadata or {}).get("sheet_name"),
                    "row_index": row.row_index,
                }
                entities.append(
                    {
                        "entity_type": entity_type,
                        "entity_name": entity_name,
                        "entity_business": business_name,
                        "columns": columns,
                        "attributes": limited_attributes,
                        "aliases": aliases,
                        "alias_sources": sorted(alias_sources),
                        "alias_source_type": "table",
                        "table_metadata": table_meta,
                        "chunk_strategy": "table_entity",
                        "table_metadata": table_meta,
                        "visibility": getattr(upload, "visibility", KnowledgeVisibility.PRIVATE) if upload else KnowledgeVisibility.PRIVATE,
                        "entity_index": entity_index,
                    }
                )
        return entities

    @staticmethod
    def _derive_table_entity_type(table: TablePayload, index: int) -> str:
        """
        Derive entity type name from table metadata.
        
        Tries multiple sources in order:
        1. Explicit entity_type in metadata
        2. Sheet name (for Excel sources)
        3. Section heading
        4. Table title
        5. Fallback: "table_N"
        
        Normalizes to snake_case for consistency.
        
        Args:
            table: Table payload
            index: Table index (for fallback naming)
        
        Returns:
            Normalized entity type (e.g., "product", "customer", "table_row")
        """
        candidate = (
            (table.metadata or {}).get("entity_type")
            or (table.metadata or {}).get("sheet_name")
            or table.section_heading
            or table.title
            or f"table_{index + 1}"
        )
        # Normalize to snake_case
        normalized = re.sub(r"[^a-z0-9]+", "_", (candidate or "").lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized or "table_row"

    def _row_attributes_from_table(
        self,
        row: TableRowPayload,
        column_schema: Sequence[str],
    ) -> dict[str, str]:
        """
        Extract row attributes from table row payload.
        
        Builds a dictionary mapping column names to cell values. Includes
        all columns from schema (even if empty) to preserve structure.
        
        Args:
            row: Table row payload with cells
            column_schema: Column schema for key resolution
        
        Returns:
            Dictionary of column_name -> cell_value
        """
        attributes: dict[str, str] = {}
        for cell in row.cells:
            # Use column_key if available, otherwise look up in schema
            key = cell.column_key or (column_schema[cell.column_index] if cell.column_index < len(column_schema) else "")
            key = key.strip() if isinstance(key, str) else ""
            if not key:
                key = f"column_{cell.column_index + 1}"  # Fallback naming
            value = (cell.raw_text or "").strip()
            if value:
                attributes[key] = value
        
        # Include all schema columns (even empty) to preserve structure
        # This ensures entity attributes match schema even if some cells are empty
        for idx, column in enumerate(column_schema):
            normalized = column.strip() if isinstance(column, str) else ""
            if not normalized:
                normalized = f"column_{idx + 1}"
            attributes.setdefault(normalized, "")
        return attributes

    def _row_model_attributes(
        self,
        row: KnowledgeUploadTableRow,
        column_schema: Sequence[str],
    ) -> dict[str, str]:
        """
        Extract row attributes from database model (for chunking).
        
        Similar to _row_attributes_from_table but works with database
        models instead of payloads. Used when building chunks from
        persisted tables.
        
        Args:
            row: Database table row model
            column_schema: Column schema for key resolution
        
        Returns:
            Dictionary of column_name -> cell_value
        """
        attributes: dict[str, str] = {}
        schema = list(column_schema)
        for cell in row.cells.all():
            column = cell.column_key or (schema[cell.column_index] if cell.column_index < len(schema) else "")
            key = column or f"column_{cell.column_index + 1}"
            attributes[key] = (cell.raw_text or "").strip()
        # Ensure all schema columns present (even if empty)
        if not attributes:
            for idx, column in enumerate(schema):
                key = column or f"column_{idx + 1}"
                attributes.setdefault(key, "")
        return attributes

    @staticmethod
    def _infer_table_row_entity_name(
        entity_type: str,
        table: TablePayload,
        attributes: Mapping[str, str],
        row_index: int,
    ) -> str:
        """
        Infer a human-readable name for a table row entity.
        
        Tries multiple strategies in order:
        1. Priority keys (name, title, sku, etc.) - common identifier fields
        2. Any column in schema with a value
        3. First non-empty attribute value
        4. Fallback: "{EntityType} {row_index}"
        
        This ensures entities have meaningful names for display and lookup,
        even when explicit name fields aren't present.
        
        Args:
            entity_type: Entity type (e.g., "product", "customer")
            table: Table containing the row
            attributes: Row attributes (column -> value)
            row_index: Row index (for fallback naming)
        
        Returns:
            Inferred entity name
        """
        # Priority keys: common fields that typically contain entity names
        priority_keys = (
            "name",
            "title",
            "plan",
            "product",
            "sku",
            "code",
            "id",
            "identifier",
            "slug",
            "trip",
            "customer",
        )
        for key in priority_keys:
            value = attributes.get(key)
            if value:
                return value
        
        # Try any column in schema
        for column in table.column_schema:
            if not column:
                continue
            value = attributes.get(column)
            if value:
                return value
        
        # Use first non-empty value
        for value in attributes.values():
            if value:
                return value
        
        # Fallback: generate name from type and index
        label = entity_type.replace("_", " ").title() or "Row"
        return f"{label} {row_index}"

    @staticmethod
    def _infer_entity_name(record_label: str, record: Mapping[str, Any], flattened: Mapping[str, str], index: int) -> str:
        """
        Infer entity name from JSON record.
        
        Similar to _infer_table_row_entity_name but for JSON entities.
        Checks both the original record structure and flattened attributes.
        
        Args:
            record_label: Label for the record type (e.g., "product", "trip")
            record: Original JSON record
            flattened: Flattened attributes dictionary
            index: Entity index (for fallback naming)
        
        Returns:
            Inferred entity name
        """
        candidate_keys = ("name", "title", "destination", "city", "label", "slug")
        for key in candidate_keys:
            # Check original record first
            value = record.get(key) if isinstance(record, Mapping) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
            # Check flattened attributes
            if key in flattened and flattened[key]:
                return flattened[key]
        
        # Fallback to ID if available
        fallback = record.get("id") if isinstance(record, Mapping) else None
        if fallback:
            return str(fallback)
        
        # Generate name from label and index
        label = record_label.rstrip("s") or record_label or "record"
        return f"{label.title()} {index + 1}"

    @staticmethod
    def _select_entity_columns(flattened: Mapping[str, str]) -> list[str]:
        """
        Select important columns for entity representation.
        
        Prioritizes semantically important fields (name, title, pricing, etc.)
        over less important metadata. This keeps entity chunks focused on
        the most queryable information.
        
        Args:
            flattened: Flattened attribute dictionary
        
        Returns:
            Ordered list of column names (preferred first, then others)
        """
        if not flattened:
            return []
        
        # Preferred columns: semantically important fields
        preferred = [
            "name",
            "title",
            "destination",
            "slug",
            "city",
            "region",
            "trip_count",
            "trip_titles",
            "trip_prices",
            "adult_price_per_person",
            "child_price_per_person",
            "currency",
            "business",
            "duration_days",
        ]
        columns: list[str] = []
        seen: set[str] = set()
        
        # Add preferred columns first (if present and non-empty)
        for key in preferred:
            if key in flattened and key not in seen and flattened[key]:
                columns.append(key)
                seen.add(key)
        
        # Add remaining columns with values
        for key, value in flattened.items():
            if key in seen:
                continue
            if value:
                columns.append(key)
                seen.add(key)
        
        # Fallback: include all columns if none selected
        if not columns:
            columns = list(flattened.keys())
        return columns

    @staticmethod
    def _render_json_entity_summary(
        *,
        entity_title: str,
        entity_type: str,
        column_schema: Sequence[str],
        attributes: Mapping[str, str],
    ) -> str:
        """
        Generate a text summary of a JSON entity for chunking.
        
        Formats entity as structured text with type, title, and key attributes.
        Limited to first 8 columns to keep summaries focused.
        
        Args:
            entity_title: Entity name/title
            entity_type: Entity type (e.g., "product", "trip")
            column_schema: Important columns to include
            attributes: Entity attributes
        
        Returns:
            Formatted text summary
        """
        lines = [f"{entity_type.title()}: {entity_title}"]
        # Limit to 8 columns to keep summaries concise
        for column in column_schema[:8]:
            value = attributes.get(column)
            if value:
                lines.append(f"- {column}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _looks_like_identifier(candidate: str) -> bool:
        """
        Determine if a string looks like an identifier/alias.
        
        Identifiers are used for entity lookup, so we need to filter out
        noise (common words, short strings, etc.). Valid identifiers:
        - At least ALIAS_MIN_LENGTH (4) chars, OR
        - At least ALIAS_SYMBOL_MIN_LENGTH (3) chars with symbols/numbers, OR
        - Matches IDENTIFIER_TOKEN_PATTERN
        
        Args:
            candidate: String to check
        
        Returns:
            True if string appears to be an identifier
        """
        if not candidate:
            return False
        token = candidate.strip()
        if not token:
            return False
        lowered = token.lower()
        
        # Long enough to be meaningful
        if len(lowered) >= ALIAS_MIN_LENGTH:
            return True
        
        # Short but has symbols/numbers (e.g., "ABC", "123", "SKU-1")
        if len(lowered) >= ALIAS_SYMBOL_MIN_LENGTH and any(ch in "-_0123456789" for ch in lowered):
            return True
        
        # Matches identifier pattern (alphanumeric with separators)
        return bool(IDENTIFIER_TOKEN_PATTERN.fullmatch(lowered))

    @staticmethod
    def _normalize_alias_value(value: str) -> str:
        """
        Normalize an alias value for storage and lookup.
        
        Converts to lowercase, replaces spaces with hyphens, collapses
        multiple hyphens, and truncates to ALIAS_MAX_LENGTH. This ensures
        consistent alias matching regardless of formatting variations.
        
        Args:
            value: Raw alias value
        
        Returns:
            Normalized alias (empty if invalid)
        """
        if not value:
            return ""
        # Normalize: lowercase, spaces -> hyphens, collapse hyphens
        normalized = re.sub(r"\s+", "-", value.strip().lower())
        normalized = re.sub(r"-{2,}", "-", normalized)  # Collapse multiple hyphens
        normalized = normalized.strip("-")  # Remove leading/trailing hyphens
        # Truncate to database limit
        if len(normalized) > ALIAS_MAX_LENGTH:
            normalized = normalized[:ALIAS_MAX_LENGTH]
        return normalized

    @staticmethod
    def _collect_aliases_from_record(
        *,
        record: Mapping[str, Any],
        flattened: Mapping[str, str],
        attributes: Mapping[str, str],
        entity_name: str,
    ) -> tuple[list[str], set[str]]:
        """
        Extract aliases (identifiers) from an entity record.
        
        Aliases enable lookup queries like "find product ABC123". This method
        searches multiple sources:
        1. Entity name itself
        2. Fields with alias keywords (id, sku, code, etc.)
        3. Values that match identifier patterns (even without keyword fields)
        
        The method deduplicates aliases and tracks their sources for debugging.
        
        Args:
            record: Original record structure
            flattened: Flattened attributes
            attributes: Selected entity attributes
            entity_name: Inferred entity name
        
        Returns:
            Tuple of (alias list, source set)
        """
        alias_candidates: list[str] = []
        alias_sources: set[str] = set()
        seen: set[str] = set()

        def maybe_add(value: Any, source: str) -> None:
            """Helper to add alias if valid and not duplicate."""
            if not isinstance(value, str):
                return
            candidate = value.strip()
            if not candidate:
                return
            if len(candidate) > ALIAS_MAX_LENGTH:
                candidate = candidate[:ALIAS_MAX_LENGTH]
            if not KnowledgeIngestionService._looks_like_identifier(candidate):
                return  # Not a valid identifier
            lowered = candidate.lower()
            if lowered in seen:
                return  # Duplicate
            seen.add(lowered)
            alias_candidates.append(candidate)
            alias_sources.add(source)

        # Add entity name as alias
        maybe_add(entity_name, "entity_name")
        
        # Check record for alias keywords
        for key in ALIAS_KEYWORDS:
            maybe_add(record.get(key), f"record_{key}")
        
        # Check flattened attributes for alias keywords
        for key, value in flattened.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"flattened_{key}")
        
        # Check selected attributes for alias keywords
        for key, value in attributes.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"attribute_{key}")
        
        # Include values that look like identifiers even without keyword fields
        # This catches identifiers in unexpected places
        for value in flattened.values():
            if isinstance(value, str) and KnowledgeIngestionService._looks_like_identifier(value):
                maybe_add(value, "inline_pattern")
        
        # Limit to 8 aliases per entity (prevents noise)
        return alias_candidates[:8], alias_sources

    @staticmethod
    def _alias_metadata(aliases: Sequence[str]) -> dict[str, Any]:
        """
        Build metadata dictionary from alias list.
        
        Normalizes and deduplicates aliases, then creates metadata with:
        - aliases: List of normalized aliases
        - alias_string: Space-separated sorted aliases (for search)
        
        Args:
            aliases: List of alias strings
        
        Returns:
            Metadata dictionary (empty if no valid aliases)
        """
        normalized: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            if not alias:
                continue
            trimmed = alias.strip()
            if not trimmed:
                continue
            if len(trimmed) > ALIAS_MAX_LENGTH:
                trimmed = trimmed[:ALIAS_MAX_LENGTH]
            lower = trimmed.lower()
            if lower in seen:
                continue  # Deduplicate
            seen.add(lower)
            normalized.append(trimmed)
        if not normalized:
            return {}
        # Create searchable string (sorted for consistency)
        alias_string = " ".join(sorted(seen))
        return {"aliases": normalized, "alias_string": alias_string}

    @staticmethod
    def _append_identifier_line(text: str, aliases: Sequence[str]) -> str:
        """
        Append identifier line to text for better embedding.
        
        Adds "Identifiers: alias1, alias2, ..." to the end of text. This
        makes identifiers explicit in the chunk, improving lookup accuracy.
        Only appends if identifiers aren't already present.
        
        Args:
            text: Text to augment
            aliases: Identifiers to append
        
        Returns:
            Text with identifier line appended (or original if no aliases)
        """
        alias_list = [alias for alias in aliases if alias]
        if not alias_list:
            return text
        # Don't duplicate if already present
        if "Identifiers:" in text:
            return text
        # Limit to 6 aliases to keep line concise
        suffix = "Identifiers: " + ", ".join(alias_list[:6])
        return f"{text.rstrip()}\n{suffix}"

    def _extract_inline_identifiers(self, text: str) -> list[str]:
        """
        Extract identifier-like strings from unstructured text.
        
        Uses pattern matching to find identifiers embedded in text:
        1. IDENTIFIER_TOKEN_PATTERN: General identifier pattern
        2. ID_LINE_PATTERN: Explicit ID lines (e.g., "ID: ABC123")
        
        This enables alias extraction from plain text documents where
        identifiers aren't in structured fields.
        
        Args:
            text: Text to search for identifiers
        
        Returns:
            List of extracted identifiers (max 6)
        """
        if not text:
            return []
        aliases: list[str] = []
        seen: set[str] = set()
        
        # Pattern 1: General identifier tokens (alphanumeric with separators)
        for match in IDENTIFIER_TOKEN_PATTERN.finditer(text.lower()):
            alias = match.group().strip()
            if not alias:
                continue
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            if not self._looks_like_identifier(alias):
                continue
            if alias not in seen:
                seen.add(alias)
                aliases.append(alias)
        
        # Pattern 2: Explicit ID lines (e.g., "ID: ABC123", "Code: xyz-456")
        for match in ID_LINE_PATTERN.finditer(text):
            alias = match.group(1).strip()
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            alias_lower = alias.lower()
            if alias_lower and alias_lower not in seen and self._looks_like_identifier(alias):
                seen.add(alias_lower)
                aliases.append(alias)
        
        return aliases[:6]  # Limit to prevent noise

    def _inject_identifiers_into_text(self, text: str) -> tuple[str, list[str]]:
        """
        Extract identifiers from text and append them explicitly.
        
        This two-step process:
        1. Extracts identifiers embedded in text
        2. Appends them as an explicit "Identifiers:" line
        
        This makes identifiers more discoverable in embeddings and improves
        lookup accuracy for queries like "find product ABC123".
        
        Args:
            text: Text to process
        
        Returns:
            Tuple of (augmented text, extracted aliases)
        """
        aliases = self._extract_inline_identifiers(text)
        if aliases:
            text = self._append_identifier_line(text, aliases)
        return text, aliases

    @staticmethod
    def _finalize_alias_metadata(metadata: dict[str, Any]) -> None:
        """
        Finalize alias metadata in chunk metadata dictionary.
        
        Normalizes and deduplicates aliases, then updates metadata in-place.
        Removes alias_string if no aliases remain. This ensures chunk metadata
        has consistent alias format.
        
        Args:
            metadata: Chunk metadata dictionary (modified in-place)
        """
        aliases = metadata.get("aliases")
        if not aliases:
            metadata.pop("alias_string", None)
            return
        normalized: list[str] = []
        seen: set[str] = set()
        # Handle both list and single value
        for alias in aliases if isinstance(aliases, (list, tuple)) else [aliases]:
            if not alias:
                continue
            trimmed = str(alias).strip()
            if not trimmed:
                continue
            if len(trimmed) > ALIAS_MAX_LENGTH:
                trimmed = trimmed[:ALIAS_MAX_LENGTH]
            lower = trimmed.lower()
            if lower in seen:
                continue  # Deduplicate
            seen.add(lower)
            normalized.append(trimmed)
        metadata["aliases"] = normalized
        metadata["alias_string"] = " ".join(sorted(seen))

    @staticmethod
    def _extract_text_file(path: Path) -> str:
        """
        Extract text from a plain text file with encoding detection.
        
        Tries multiple encodings in order (UTF-8, UTF-16, Latin-1) to handle
        files with different encodings. Falls back to UTF-8 with error
        ignoring if all fail (prevents crashes on corrupted files).
        
        Args:
            path: Path to text file
        
        Returns:
            File contents as string
        """
        encodings = ("utf-8", "utf-16", "latin-1")
        for encoding in encodings:
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        # Fallback: decode with error ignoring (handles corrupted files)
        return path.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _normalize_text(raw: str) -> str:
        """
        Normalize text for processing and storage.
        
        Performs cleanup:
        - Removes null bytes (can break databases)
        - Normalizes line endings (CRLF -> LF)
        - Collapses multiple spaces (but preserves tabs for TSV)
        - Collapses multiple blank lines (max 2)
        
        Preserves tabs because they're used for TSV table formatting,
        which is important for semantic understanding.
        
        Args:
            raw: Raw text to normalize
        
        Returns:
            Normalized text
        """
        text = raw.replace("\x00", " ").replace("\r", "\n")
        # Collapse runs of spaces only; keep tabs intact for TSV
        text = re.sub(r"[ ]{2,}", " ", text)
        # Do NOT touch \t (tabs are used for TSV table formatting)
        text = re.sub(r"\n{3,}", "\n\n", text)  # Max 2 blank lines
        return text.strip()

    @staticmethod
    def _sanitize_text(value: Any) -> str:
        """
        Sanitize a value to safe string format.
        
        Handles None values and removes null bytes (which can break
        database storage). Used for sanitizing user input and database
        values before storage.
        
        Args:
            value: Value to sanitize (any type)
        
        Returns:
            Safe string (empty if None)
        """
        if value is None:
            return ""
        text = str(value)
        if "\x00" in text:
            return text.replace("\x00", " ")  # Remove null bytes
        return text

    @staticmethod
    def _build_summary(content: str, limit: int = 500) -> str:
        """
        Build a short summary from content.
        
        Takes first 3 paragraphs and truncates to limit. Used for generating
        preview text in metadata. Falls back to simple truncation if no
        paragraph structure.
        
        Args:
            content: Full content to summarize
            limit: Maximum summary length (default 500)
        
        Returns:
            Summary text with ellipsis if truncated
        """
        paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
        if not paragraphs:
            # No structure - simple truncation
            return content[:limit]
        # Use first 3 paragraphs
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
