from __future__ import annotations

from collections import deque
import base64
import csv
import gzip
import hashlib
import json
import logging
import math
import mimetypes
import io
import os
import random
import re
import shutil
import statistics
import time
import uuid
import unicodedata
from urllib.parse import urlencode
from datetime import timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Min, Q, Value, When
from django.utils import timezone
from django.utils.text import slugify
from opentelemetry import trace as otel_trace
import requests

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
    KnowledgeUploadShadowChunk,
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
from apps.knowledge.documents import DocumentScrapeError, scrape_document_source
from apps.knowledge.dataset_cards import build_dataset_card_segment_payload
from apps.knowledge.dataset_key_index import (
    BloomFilter,
    bloom_spec_for_items,
    normalize_identifier_value,
    resolve_key_index_storage_path,
    write_bloom_filter,
)
from apps.rag.embeddings import LocalEmbeddingService, build_embedding_service, EmbeddingProviderError
from apps.accounts.feature_flags import FeatureFlagService
from apps.knowledge.privacy import redact_mapping_preview
from apps.rag.quality_monitor import QualityMonitor
from apps.rag.rag_logging import structured_log
from core.tenancy import tenant_context
from apps.knowledge.table_normalization import (
    NormalizedSheet,
    SheetNormalizationDiagnostics,
    normalize_sheet_rows,
    resolve_normalization_policy,
    sheet_is_allowed,
    summarize_normalization,
)

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

OCR_NORMALIZATION_VERSION = "v2"
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u06D6-\u06ED]")


def _log_normalization_summary(upload: KnowledgeUpload | None, source: str, summary: Mapping[str, Any] | None) -> None:
    if not summary or not summary.get("enabled"):
        return
    rows = int((summary.get("rows_dropped") or {}).get("total", 0))
    columns = int((summary.get("columns_trimmed") or {}).get("total", 0))
    tokens = int(summary.get("tokens_replaced") or 0)
    skipped = len(summary.get("empty_sheets_skipped") or []) + len(summary.get("policy_skipped") or [])
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

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - fallback handled via runtime check
    load_workbook = None  # type: ignore

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import xlrd
except ImportError:  # pragma: no cover - fallback handled via runtime check
    xlrd = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import pdfplumber
except ImportError:  # pragma: no cover - optional dependency
    pdfplumber = None  # type: ignore


# THESE ARE STANDALONE FUNCTIONS - NOT INSIDE ANY CLASS
def create_tesseract_ocr_callable() -> Callable[[bytes], str] | None:
    """
    Create Tesseract OCR callable for low-density PDF pages
    Returns None if Tesseract is not available
    """
    try:
        import pytesseract
        from PIL import Image
        import io
        
        def ocr_callable(image_bytes: bytes) -> str:
            """OCR callable that takes image bytes and returns text"""
            try:
                image = Image.open(io.BytesIO(image_bytes))
                text = pytesseract.image_to_string(
                    image,
                    config='--psm 1'
                )
                return text.strip()
            except Exception as exc:
                logger.warning(f"Tesseract OCR failed: {exc}")
                return ""
        
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
    Factory function to create OCRReconciler with optional Tesseract support
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

SUPPORTED_SOURCE_TYPES = {
    KnowledgeSourceType.FILE,
    KnowledgeSourceType.LINK,
    KnowledgeSourceType.INTEGRATION,
}

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

SLUG_PATTERN = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+){1,}\b")
IDENTIFIER_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}", re.IGNORECASE)
ID_LINE_PATTERN = re.compile(
    r"(?:^|\b)(?:id|identifier|sku|code|policy|ref|reference)\s*[:#]\s*([a-z0-9][a-z0-9_\-\/]+)",
    re.IGNORECASE,
)
ALIAS_MIN_LENGTH = 4
ALIAS_SYMBOL_MIN_LENGTH = 3
ALIAS_MAX_LENGTH = 255
CARD_NUMBER_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
DATE_TOKEN_PATTERN = re.compile(
    r"\b(?:\d{4}[/-]\d{1,2}[/-]\d{1,2}|(?:0?[1-9]|1[0-2])[/-](?:\d{2}|\d{4}))\b"
)


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
class PdfSpan:
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
        return 0.5 * (self.x0 + self.x1)

    @property
    def y_center(self) -> float:
        return 0.5 * (self.y0 + self.y1)

def _union_bbox(bboxes: list[dict[str, float]]) -> dict[str, float]:
    if not bboxes:
        return {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}
    x0 = min(b["x0"] for b in bboxes)
    y0 = min(b["y0"] for b in bboxes)
    x1 = max(b["x1"] for b in bboxes)
    y1 = max(b["y1"] for b in bboxes)
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)}

@dataclass(frozen=True)
class ExtractionResult:
    """Enhanced extraction result supporting multiple content representations"""
    text: str  # Plain text with inline tables (TSV format)
    format_hint: str
    metadata: dict[str, Any]
    pages: list[PageLayout] = field(default_factory=list)
    tables: list[TablePayload] = field(default_factory=list)
    issues: list[IssuePayload] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    text_html: str | None = None  # NEW: HTML representation for web content

@dataclass(frozen=True)
class EnhancedContextDocument:
    """
    JSON envelope wrapper for sending structured context to LLM
    Matches Claude's document format for better model understanding
    """
    index: int
    media_type: str
    source: str
    text: str
    pages: list[dict[str, Any]] | None = None
    tables: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
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
        Enhanced PDF rendering with:
        1. Block-based reading order (top-to-bottom, left-to-right)
        2. Inline TSV representation for tables
        3. Better structure preservation
        """
        try:
            document = self._fitz.open(path)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open PDF for layout parsing: {exc}") from exc

        pages: list[PageLayout] = []
        fragments: list[str] = []
        issues: list[IssuePayload] = []
        
        for index, page in enumerate(document, start=1):
            # Get raw blocks for structured extraction
            raw_blocks = page.get_text("blocks") or []
            
            # Sort blocks by reading order: top-to-bottom (y0), then left-to-right (x0)
            sorted_blocks = sorted(
                raw_blocks,
                key=lambda b: (round(b[1] / 5) * 5, b[0])
            )
            
            # NEW: Group blocks into rows based on vertical position
            block_rows = self._group_blocks_into_rows(sorted_blocks)
            
            # Assemble text from rows with inline table formatting
            block_texts = []
            decorative_fragments_filtered = 0
            for row_blocks in block_rows:
                # Check if this row looks like a table row (multiple columns)
                if len(row_blocks) >= 3:  # 3+ blocks in same row = likely table
                    # Format as TSV
                    row_cells = [block[4].strip() for block in row_blocks if len(block) > 4]
                    row_cells = [cell for cell in row_cells if cell]  # Remove empty
                    if row_cells:
                        row_text = "\t".join(row_cells)
                        signals = self._decorative_text_signals(row_text)
                        if self._is_decorative_text(row_text, signals):
                            decorative_fragments_filtered += 1
                            continue
                        block_texts.append(row_text)
                else:
                    # Regular text - just concatenate
                    for block in row_blocks:
                        if len(block) > 4:
                            text_fragment = block[4].strip()
                            if text_fragment:
                                signals = self._decorative_text_signals(text_fragment)
                                if self._is_decorative_text(text_fragment, signals):
                                    decorative_fragments_filtered += 1
                                    continue
                                block_texts.append(text_fragment)
            
            # Join blocks with appropriate spacing
            plain_text = "\n".join(block_texts) if block_texts else ""
            
            # Calculate metrics
            char_count = len(plain_text.strip())
            rect = page.rect
            area = max(rect.width * rect.height, 1.0)
            density = char_count / area
            
            # OCR reconciliation
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
            
            # Build structured blocks (keep your existing logic)
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
                        "decorative_fragments_filtered": decorative_fragments_filtered,
                        "extraction_method": "block_sorted_with_row_detection"
                    },
                )
            )
        
        return PageRendererResult(
            text="\n\n".join(fragments),
            pages=pages,
            issues=issues
        )

            # [ADD] Helper: collect PdfSpan from 'rawdict'/'dict' shapes
    def _collect_spans_from_textdict(self, page_index: int, obj: dict) -> list[PdfSpan]:
        """
        Walk text 'dict'/'rawdict' structure: blocks(type=0)->lines->spans and collect PdfSpan.
        Safely handles missing keys; prefers span bbox, falls back to line bbox, else zeros.
        """
        page_spans: list[PdfSpan] = []
        if not obj:
            return page_spans

        blocks = (obj.get("blocks") or [])
        for block in blocks:
            if (block or {}).get("type") != 0:
                continue
            lines = (block.get("lines") or [])
            for line in lines:
                line_bbox = line.get("bbox") or [0, 0, 0, 0]
                spans = (line.get("spans") or [])
                for span in spans:
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    bbox = span.get("bbox") or line_bbox or [0, 0, 0, 0]
                    size_val = span.get("size")
                    try:
                        size = float(size_val) if size_val is not None else None
                    except Exception:
                        size = None
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
        Returns a list per page; each page is a list of PdfSpan with geometry + font/size.
        Robust: tries 'rawdict', falls back to 'dict', then to 'words'.
        """
        if self._fitz is None:
            return []
        try:
            doc = self._fitz.open(path)
        except Exception:
            return []

        results: list[list[PdfSpan]] = []
        for page_index, page in enumerate(doc, start=1):
            # --- Fast path: 'rawdict'
            page_spans: list[PdfSpan] = []
            try:
                raw = page.get_text("rawdict") or {}
                page_spans = self._collect_spans_from_textdict(page_index, raw)
            except Exception:
                page_spans = []

            # --- Fallback 1: 'dict'
            if not page_spans:
                try:
                    dct = page.get_text("dict") or {}
                    page_spans = self._collect_spans_from_textdict(page_index, dct)
                except Exception:
                    page_spans = []

            # --- Fallback 2: 'words'
            if not page_spans:
                try:
                    words = page.get_text("words") or []
                    word_spans: list[PdfSpan] = []
                    for w in words:
                        # words tuple: x0, y0, x1, y1, "word", block_no, line_no, word_no
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
                                font=None,
                                size=None,  # no size from 'words'; header logic still has non-size cues
                            )
                        )
                    page_spans = word_spans
                except Exception:
                    page_spans = []

            results.append(page_spans)
        return results


    def _group_blocks_into_rows(self, sorted_blocks: list) -> list[list]:
        """
        Group blocks that are on the same horizontal line (same Y position)
        Returns list of rows, where each row is a list of blocks
        """
        if not sorted_blocks:
            return []
        
        rows = []
        current_row = []
        current_y = None
        tolerance = 5  # Vertical position tolerance in points
        
        for block in sorted_blocks:
            if len(block) <= 1:
                continue
            
            block_y = block[1]  # Y position
            
            if current_y is None:
                # First block
                current_y = block_y
                current_row = [block]
            elif abs(block_y - current_y) <= tolerance:
                # Same row
                current_row.append(block)
            else:
                # New row
                if current_row:
                    rows.append(current_row)
                current_row = [block]
                current_y = block_y
        
        # Add last row
        if current_row:
            rows.append(current_row)
        
        return rows

    @staticmethod
    def _block_anchor(page_number: int, order_index: int) -> str:
        return f"p{page_number}-b{order_index}"

    @staticmethod
    def _decorative_text_signals(text: str) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        if not text:
            return signals
        lowered = text.lower()
        if re.search(r"\bvalid\s*thru\b", lowered):
            signals["valid_thru"] = True
        if re.search(r"\b(?:\d{4}[\s-]?){3}\d{4}\b", lowered):
            signals["card_number"] = True
        stripped = text.strip()
        if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]", stripped):
            signals["spaced_characters"] = True
        alnum = [c for c in stripped if c.isalnum()]
        digits = sum(1 for c in stripped if c.isdigit())
        if alnum and (digits / len(alnum)) >= 0.6 and len(stripped.split()) <= 4:
            signals["mostly_digits"] = True
        return signals

    @staticmethod
    def _is_decorative_text(text: str, signals: Mapping[str, Any]) -> bool:
        if not text:
            return False
        if signals.get("card_number") or signals.get("valid_thru"):
            return True
        if signals.get("spaced_characters") and len(text.split()) <= 6:
            return True
        if signals.get("mostly_digits") and len(text.split()) <= 4:
            return True
        return False

    @staticmethod
    def _page_region(bbox: Mapping[str, Any], page_height: float | None) -> str | None:
        if not page_height:
            return None
        y0 = float(bbox.get("y0") or 0.0)
        y1 = float(bbox.get("y1") or 0.0)
        if y1 <= page_height * 0.08:
            return "header"
        if y0 >= page_height * 0.92:
            return "footer"
        return None

    def _looks_like_table_text(self, text: str) -> bool:
        """Check if text block appears to be tabular data"""
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        
        # Check for common table indicators
        sample = lines[0]
        has_pipes = "|" in sample
        has_tabs = "\t" in sample
        has_multi_spaces = bool(re.search(r"\s{3,}", sample))
        
        return has_pipes or has_tabs or has_multi_spaces

    def _format_table_as_tsv(self, text: str) -> str:
        """
        Convert table text to TSV format for better embedding/retrieval
        
        Example output:
        Card Type\tIssuance Fee\tInterest Rate
        Classic\tEGP 250\t3.99%
        Gold\tEGP 300\t3.99%
        """
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return text
        
        # Detect delimiter
        first_line = lines[0]
        if "|" in first_line:
            delimiter = "|"
        elif "\t" in first_line:
            delimiter = "\t"
        else:
            # Multiple spaces - use regex split
            delimiter = None
        
        formatted_rows = []
        for line in lines:
            if delimiter:
                cells = [cell.strip() for cell in line.split(delimiter) if cell.strip()]
            else:
                # Split on 2+ spaces
                cells = [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
            
            # Join with tab for consistent TSV format
            formatted_rows.append("\t".join(cells))
        
        return "\n".join(formatted_rows)


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
        page_height = None
        try:
            page_height = float(page.rect.height)
        except Exception:
            page_height = None
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
            raw_block_type = None
            if len(block) > 6 and block[6] is not None:
                try:
                    raw_block_type = int(block[6])
                except (TypeError, ValueError):
                    raw_block_type = None
            block_type = self._resolve_block_type(block, stripped)
            if raw_block_type == 1:
                block_type = KnowledgeBlockType.IMAGE
            elif raw_block_type in {2, 3}:
                block_type = KnowledgeBlockType.FIGURE
            if self._looks_like_heading(stripped):
                heading_context = [stripped]
                section_heading = stripped
            else:
                section_heading = heading_context[-1] if heading_context else ""
            decorative_signals = self._decorative_text_signals(stripped) if stripped else {}
            is_decorative = self._is_decorative_text(stripped, decorative_signals)
            region_role = "text"
            if block_type == KnowledgeBlockType.TABLE:
                region_role = "table"
            elif block_type in {KnowledgeBlockType.IMAGE, KnowledgeBlockType.FIGURE}:
                region_role = "figure"
            elif is_decorative:
                region_role = "decorative"
            page_region = self._page_region(bbox, page_height) if stripped else None
            block_metadata: dict[str, Any] = {
                "anchor": self._block_anchor(page_number, order_index),
                "region_role": region_role,
            }
            if raw_block_type is not None:
                block_metadata["raw_block_type"] = raw_block_type
            if page_region:
                block_metadata["page_region"] = page_region
            if decorative_signals:
                block_metadata["decorative_signals"] = decorative_signals
            if is_decorative:
                block_metadata["is_decorative"] = True
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
                    metadata=block_metadata,
                )
            )
        if not payloads:
            payloads.append(
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=page.get_text("text") or "",
                    metadata={"fallback": True, "anchor": self._block_anchor(page_number, 0), "region_role": "text"},
                )
            )
        return payloads

    @staticmethod
    def _resolve_block_type(block: Any, text_fragment: str) -> str:
        stripped = (text_fragment or "").strip()
        if not stripped:
            return KnowledgeBlockType.IMAGE

        # Strong table signals: pipes / tabs / multi-spaces in the first line
        first_line = stripped.splitlines()[0] if "\n" in stripped else stripped
        looks_tabular = ("|" in first_line) or ("\t" in first_line) or bool(re.search(r"\s{2,}", first_line))
        if looks_tabular:
            return KnowledgeBlockType.TABLE

        # Headings: short and mostly uppercase or trailing colon
        if stripped.endswith(":"):
            return KnowledgeBlockType.HEADING
        if stripped.isupper() and len(stripped) < 80:
            return KnowledgeBlockType.HEADING

        # Default
        return KnowledgeBlockType.PARAGRAPH

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

        # Quick bullet-list rejection: first 3 non-empty lines start with bullets
        heads = ["".join(line.strip().split()[:1]) for line in lines[:3] if line.strip()]
        bullet_heads = {"*", "**", "***", "****", "*****", "-", "•", "—"}
        if heads and all(h in bullet_heads for h in heads):
            return False

        # Original signals (pipes, tabs, or multi-spaces)
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
        raw = (cell or "").strip()

        # collapse letter-by-letter headers: "W H I T E" -> "WHITE"
        if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]", raw):
            raw = raw.replace(" ", "")

        # collapse spaced underscores: "valid_thru_12_28" is okay; but reduce noise
        s = re.sub(r"\s+", " ", raw)
        s = s.lower()
        s = re.sub(r"[^a-z0-9%$€£]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s or f"column_{index+1}"


    @staticmethod
    def _normalize_cell_value(cell: str) -> dict[str, Any]:
        text = (cell or "")
        # Normalize whitespace incl. thin/nb spaces; preserve decimals/commas
        text = text.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ")
        text = " ".join(text.strip().split())
        if not text:
            return {}

        out: dict[str, Any] = {}

        # Percent first (captures "3.99%" or "2 %")
        m_pct = re.search(r"(\d+(?:[\.,]\d+)?)\s*%", text)
        if m_pct:
            try:
                pct_val = float(m_pct.group(1).replace(",", "."))
                out["percent"] = pct_val / 100.0
            except ValueError:
                pass

        # Currency normalization map
        currency_alias = {
            "EG£": "EGP",
            "LE": "EGP",
            "EGF": "EGP",
            "E6P": "EGP",
        }
        currency_codes = r"(EGP|EG£|EGF|E6P|USD|EUR|AED|SAR|GBP|LE)"
        symbol = r"[$€£]"

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


# PdfPlumber table extraction (optional dependency)
PDFPLUMBER_DEFAULT_TABLE_SETTINGS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "lines",
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
        },
    ),
    (
        "lines_text",
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "min_words_horizontal": 1,
            "text_y_tolerance": 2,
        },
    ),
    (
        "text_lines",
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "min_words_vertical": 1,
            "text_x_tolerance": 2,
        },
    ),
    (
        "text",
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
        },
    ),
)


class PdfPlumberTableExtractor:
    def __init__(self, *, table_settings: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        self.table_settings = list(table_settings)

    @staticmethod
    def _cell_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _row_non_numeric_ratio(row: Sequence[str]) -> float:
        total = 0
        non_numeric = 0
        for cell in row:
            text = cell.strip()
            if not text:
                continue
            total += 1
            if not re.search(r"\d", text):
                non_numeric += 1
        if total == 0:
            return 0.0
        return non_numeric / total

    def _infer_header_index(self, rows: Sequence[list[str]]) -> int | None:
        if not rows:
            return None
        first = rows[0]
        second = rows[1] if len(rows) > 1 else []
        first_ratio = self._row_non_numeric_ratio(first)
        second_ratio = self._row_non_numeric_ratio(second)
        if first_ratio >= 0.6 and (second_ratio <= 0.5 or first_ratio >= second_ratio):
            return 0
        return None

    def _build_table_payload(
        self,
        *,
        rows: list[list[str]],
        page_number: int,
        order_index: int,
        extractor_label: str,
    ) -> TablePayload | None:
        cleaned_rows = [row for row in rows if any(cell.strip() for cell in row)]
        if len(cleaned_rows) < 2:
            return None
        max_cols = max(len(row) for row in cleaned_rows)
        header_idx = self._infer_header_index(cleaned_rows)
        if header_idx is not None:
            header_row = cleaned_rows[header_idx]
            schema = [
                TableDetector._normalize_header_cell(cell, idx)
                for idx, cell in enumerate(header_row)
            ]
            if len(schema) < max_cols:
                schema.extend([f"column_{idx+1}" for idx in range(len(schema), max_cols)])
        else:
            schema = [f"column_{idx+1}" for idx in range(max_cols)]

        table_rows: list[TableRowPayload] = []
        next_row_idx = 0
        if header_idx is not None:
            header_cells_payload: list[TableCellPayload] = []
            for col_idx in range(max_cols):
                raw = header_row[col_idx] if col_idx < len(header_row) else ""
                header_cells_payload.append(
                    TableCellPayload(
                        row_index=0,
                        column_index=col_idx,
                        column_key=schema[col_idx],
                        raw_text=raw,
                        normalized_value=TableDetector._normalize_cell_value(raw),
                        bbox={},
                        confidence=None,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=0,
                    page_number=page_number,
                    bbox={},
                    raw_text=" | ".join(header_row),
                    metadata={"row_type": "header"},
                    cells=header_cells_payload,
                )
            )
            next_row_idx = 1

        for row in cleaned_rows[1 if header_idx is not None else 0 :]:
            cells_payload: list[TableCellPayload] = []
            for col_idx in range(max_cols):
                raw = row[col_idx] if col_idx < len(row) else ""
                cells_payload.append(
                    TableCellPayload(
                        row_index=next_row_idx,
                        column_index=col_idx,
                        column_key=schema[col_idx],
                        raw_text=raw,
                        normalized_value=TableDetector._normalize_cell_value(raw),
                        bbox={},
                        confidence=None,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=next_row_idx,
                    page_number=page_number,
                    bbox={},
                    raw_text=" | ".join(row),
                    metadata={"row_type": "data"},
                    cells=cells_payload,
                )
            )
            next_row_idx += 1

        return TablePayload(
            order_index=order_index,
            title=f"Table {order_index}",
            section_heading="",
            page_number=page_number,
            bbox={},
            column_schema=schema,
            data_dictionary={},
            metadata={"detected_via": "pdfplumber", "extractor": extractor_label},
            rows=table_rows,
        )

    def extract_candidates(
        self,
        path: Path,
    ) -> tuple[dict[str, list[TablePayload]], list[IssuePayload], dict[str, Any]]:
        if pdfplumber is None:
            return (
                {},
                [
                    IssuePayload(
                        code="pdfplumber_missing",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="pdfplumber is not installed; skipping PDF table extraction.",
                    )
                ],
                {},
            )

        candidates: dict[str, list[TablePayload]] = {}
        issues: list[IssuePayload] = []
        page_counts: dict[int, dict[str, int]] = {}

        try:
            with pdfplumber.open(path) as pdf:
                for label, settings in self.table_settings:
                    order_index = 0
                    tables_for_label: list[TablePayload] = []
                    for page_number, page in enumerate(pdf.pages, start=1):
                        try:
                            extracted = page.extract_tables(table_settings=dict(settings)) or []
                        except Exception as exc:
                            issues.append(
                                IssuePayload(
                                    code="pdfplumber_page_failed",
                                    severity=KnowledgeIssueSeverity.WARNING.value,
                                    description=f"pdfplumber failed on page {page_number}: {exc}",
                                    page_number=page_number,
                                )
                            )
                            continue
                        if not extracted:
                            continue
                        for raw_table in extracted:
                            normalized_rows = [
                                [self._cell_text(cell) for cell in row]
                                for row in (raw_table or [])
                                if row
                            ]
                            if not normalized_rows:
                                continue
                            order_index += 1
                            payload = self._build_table_payload(
                                rows=normalized_rows,
                                page_number=page_number,
                                order_index=order_index,
                                extractor_label=label,
                            )
                            if payload:
                                tables_for_label.append(payload)
                        page_counts.setdefault(page_number, {})[label] = page_counts.get(page_number, {}).get(label, 0) + len(extracted)
                    if tables_for_label:
                        candidates[f"pdfplumber:{label}"] = tables_for_label
        except Exception as exc:
            issues.append(
                IssuePayload(
                    code="pdfplumber_failed",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description=f"pdfplumber extraction failed: {exc}",
                )
            )

        metadata = {"page_counts": page_counts}
        return candidates, issues, metadata


# Azure Document Intelligence table extraction (optional, REST-based)
class AzureDocumentIntelligenceExtractor:
    def __init__(
        self,
        *,
        endpoint: str | None,
        key: str | None,
        model: str = "prebuilt-layout",
        api_version: str = "2023-07-31",
        base_path: str = "formrecognizer",
        locale: str | None = None,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 1.5,
        max_polls: int = 40,
    ) -> None:
        self.endpoint = (endpoint or "").rstrip("/")
        self.key = (key or "").strip()
        self.model = (model or "prebuilt-layout").strip()
        self.api_version = (api_version or "2023-07-31").strip()
        self.base_path = (base_path or "formrecognizer").strip().strip("/")
        self.locale = (locale or "").strip()
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        self.poll_interval_seconds = max(0.5, float(poll_interval_seconds))
        self.max_polls = max(5, int(max_polls))

    @staticmethod
    def _polygon_to_bbox(polygon: Sequence[Any]) -> dict[str, float]:
        xs: list[float] = []
        ys: list[float] = []
        for point in polygon or []:
            if isinstance(point, dict):
                x_val = point.get("x")
                y_val = point.get("y")
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                x_val, y_val = point[0], point[1]
            else:
                continue
            try:
                xs.append(float(x_val))
                ys.append(float(y_val))
            except (TypeError, ValueError):
                continue
        if not xs or not ys:
            return {}
        return {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}

    @staticmethod
    def _bbox_from_regions(regions: Sequence[Mapping[str, Any]] | None) -> tuple[int | None, dict[str, float]]:
        if not regions:
            return None, {}
        first = regions[0] if regions else {}
        page_number = first.get("pageNumber")
        polygon = first.get("boundingPolygon") or []
        bbox = AzureDocumentIntelligenceExtractor._polygon_to_bbox(polygon)
        try:
            page_number = int(page_number) if page_number is not None else None
        except (TypeError, ValueError):
            page_number = None
        return page_number, bbox

    def _build_analyze_url(self, *, locale: str | None = None) -> str:
        base_path = self.base_path or "formrecognizer"
        params = {"api-version": self.api_version}
        if locale:
            params["locale"] = locale
        query = urlencode(params)
        return f"{self.endpoint}/{base_path}/documentModels/{self.model}:analyze?{query}"

    def _analyze_document(self, path: Path) -> tuple[dict[str, Any] | None, list[IssuePayload], dict[str, Any]]:
        issues: list[IssuePayload] = []
        meta: dict[str, Any] = {}
        if not self.endpoint or not self.key:
            issues.append(
                IssuePayload(
                    code="azure_di_missing",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Azure Document Intelligence credentials are missing; skipping.",
                )
            )
            return None, issues, meta

        url = self._build_analyze_url(locale=self.locale)
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/pdf",
        }
        start = time.time()
        try:
            with path.open("rb") as handle:
                response = requests.post(url, headers=headers, data=handle, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            issues.append(
                IssuePayload(
                    code="azure_di_request_failed",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description=f"Azure DI request failed: {exc}",
                )
            )
            return None, issues, meta

        if response.status_code not in {200, 201, 202}:
            issues.append(
                IssuePayload(
                    code="azure_di_request_error",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description=f"Azure DI request error {response.status_code}: {response.text[:200]}",
                )
            )
            return None, issues, meta

        operation_url = response.headers.get("operation-location") or response.headers.get("Operation-Location")
        if not operation_url:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if payload.get("status") == "succeeded" and payload.get("analyzeResult"):
                meta["duration_ms"] = int((time.time() - start) * 1000)
                meta["status"] = "succeeded"
                return payload.get("analyzeResult"), issues, meta
            issues.append(
                IssuePayload(
                    code="azure_di_missing_operation",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description="Azure DI response missing operation-location header.",
                )
            )
            return None, issues, meta

        poll_headers = {"Ocp-Apim-Subscription-Key": self.key}
        status_payload: dict[str, Any] | None = None
        for _ in range(self.max_polls):
            try:
                poll_response = requests.get(operation_url, headers=poll_headers, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                issues.append(
                    IssuePayload(
                        code="azure_di_poll_failed",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI poll failed: {exc}",
                    )
                )
                break
            if poll_response.status_code not in {200, 201}:
                issues.append(
                    IssuePayload(
                        code="azure_di_poll_error",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI poll error {poll_response.status_code}: {poll_response.text[:200]}",
                    )
                )
                break
            try:
                status_payload = poll_response.json()
            except ValueError:
                status_payload = None
            if not status_payload:
                time.sleep(self.poll_interval_seconds)
                continue
            status = (status_payload.get("status") or "").lower()
            if status == "succeeded":
                meta["duration_ms"] = int((time.time() - start) * 1000)
                meta["status"] = "succeeded"
                return status_payload.get("analyzeResult"), issues, meta
            if status in {"failed", "error"}:
                issues.append(
                    IssuePayload(
                        code="azure_di_failed",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI failed: {status_payload.get('error', {})}",
                    )
                )
                meta["status"] = "failed"
                break
            time.sleep(self.poll_interval_seconds)

        meta["status"] = meta.get("status") or "timeout"
        if meta["status"] == "timeout":
            issues.append(
                IssuePayload(
                    code="azure_di_timeout",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description="Azure DI polling timed out.",
                )
            )
        return None, issues, meta

    def extract_tables(self, path: Path) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        analyze_result, issues, meta = self._analyze_document(path)
        if not analyze_result:
            return [], issues, meta

        tables_data = analyze_result.get("tables") or []
        table_payloads: list[TablePayload] = []
        table_meta: dict[str, Any] = {
            "model": self.model,
            "api_version": self.api_version,
            "table_count": len(tables_data),
        }
        if meta:
            table_meta.update(meta)

        for order_index, table in enumerate(tables_data, start=1):
            row_count = int(table.get("rowCount") or 0)
            col_count = int(table.get("columnCount") or 0)
            cells = table.get("cells") or []
            page_number, table_bbox = self._bbox_from_regions(table.get("boundingRegions"))
            header_rows: set[int] = set()
            cell_confidences: list[float] = []

            grid: list[list[str]] = [["" for _ in range(col_count)] for _ in range(row_count)]
            cell_lookup: dict[tuple[int, int], dict[str, Any]] = {}

            for cell in cells:
                try:
                    r_idx = int(cell.get("rowIndex") or 0)
                    c_idx = int(cell.get("columnIndex") or 0)
                except (TypeError, ValueError):
                    continue
                row_span = int(cell.get("rowSpan") or 1)
                col_span = int(cell.get("columnSpan") or 1)
                content = str(cell.get("content") or "").strip()
                kind = str(cell.get("kind") or "").lower()
                confidence = cell.get("confidence")
                if isinstance(confidence, (int, float)):
                    cell_confidences.append(float(confidence))
                if kind in {"columnheader", "rowheader"}:
                    header_rows.add(r_idx)
                for rr in range(r_idx, min(r_idx + row_span, row_count)):
                    for cc in range(c_idx, min(c_idx + col_span, col_count)):
                        grid[rr][cc] = content
                        cell_lookup[(rr, cc)] = {
                            "row_span": row_span,
                            "column_span": col_span,
                            "kind": kind,
                            "confidence": confidence,
                            "regions": cell.get("boundingRegions") or [],
                        }

            column_schema: list[str] = []
            header_row_indices = sorted(header_rows)
            for col_idx in range(col_count):
                header_parts: list[str] = []
                for row_idx in header_row_indices:
                    if 0 <= row_idx < row_count:
                        value = grid[row_idx][col_idx]
                        if value:
                            header_parts.append(value)
                header_text = " ".join(header_parts).strip()
                column_schema.append(TableDetector._normalize_header_cell(header_text, col_idx))

            table_rows: list[TableRowPayload] = []
            for row_idx in range(row_count):
                row_cells: list[TableCellPayload] = []
                for col_idx in range(col_count):
                    raw_text = grid[row_idx][col_idx]
                    cell_meta = cell_lookup.get((row_idx, col_idx), {})
                    cell_page, cell_bbox = self._bbox_from_regions(cell_meta.get("regions"))
                    normalized_value = TableDetector._normalize_cell_value(raw_text)
                    column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                    row_cells.append(
                        TableCellPayload(
                            row_index=row_idx,
                            column_index=col_idx,
                            column_key=column_key,
                            raw_text=raw_text,
                            normalized_value=normalized_value,
                            bbox=cell_bbox or table_bbox,
                            confidence=cell_meta.get("confidence"),
                            metadata={
                                "row_span": cell_meta.get("row_span", 1),
                                "column_span": cell_meta.get("column_span", 1),
                                "kind": cell_meta.get("kind"),
                                "page_number": cell_page or page_number,
                            },
                        )
                    )
                row_type = "header" if row_idx in header_rows else "data"
                table_rows.append(
                    TableRowPayload(
                        row_index=row_idx,
                        page_number=page_number,
                        bbox=table_bbox,
                        raw_text=" | ".join(grid[row_idx]) if row_idx < len(grid) else "",
                        metadata={"row_type": row_type},
                        cells=row_cells,
                    )
                )

            avg_conf = round(sum(cell_confidences) / max(1, len(cell_confidences)), 4) if cell_confidences else None
            table_payloads.append(
                TablePayload(
                    order_index=order_index,
                    title=table.get("caption") or f"Table {order_index}",
                    section_heading="",
                    page_number=page_number,
                    bbox=table_bbox,
                    column_schema=column_schema,
                    data_dictionary={},
                    metadata={
                        "detected_via": "azure_di",
                        "model": self.model,
                        "structure_confidence": avg_conf,
                        "cell_confidence_avg": avg_conf,
                        "table_index": order_index,
                    },
                    rows=table_rows,
                )
            )

        return table_payloads, issues, table_meta


# === [ADD] GeometryTableReconstructor: x/y clustering → grid → TablePayloads ===
class GeometryTableReconstructor:
    def __init__(
        self,
        y_tol: float = 6.0,
        x_tol_min: float = 6.0,
        header_keywords: Optional[Iterable[str]] = None,  # optional, default generic
    ):
        self.y_tol = y_tol
        self.x_tol_min = x_tol_min
        self.header_keywords = {k.strip().lower() for k in header_keywords} if header_keywords else set()

    @staticmethod
    def _mostly_numeric_or_amount(s: str) -> bool:
        """
        True if cell looks numeric/currency/percent-heavy (typical for data rows).
        """
        if not s:
            return False
        t = s.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ").strip()
        # obvious numeric/amount/percent signals
        if re.search(r"\d", t) and (re.search(r"[%\d]", t) or re.search(r"[$€£]|EGP|USD|EUR|AED|SAR|GBP|LE", t, re.I)):
            return True
        # general numeric density heuristic
        letters = sum(c.isalpha() for c in t)
        digits = sum(c.isdigit() for c in t)
        return digits > 0 and digits >= letters

    # ---- Clustering helpers ----
    @staticmethod
    def _greedy_cluster(values: list[tuple[float, int]], tol: float) -> list[list[int]]:
        """
        values: list of (key, index) sorted by key
        Returns list of clusters of indices based on tolerance.
        """
        clusters: list[list[int]] = []
        if not values:
            return clusters
        current = [values[0][1]]
        anchor = values[0][0]
        for val, idx in values[1:]:
            if abs(val - anchor) <= tol:
                current.append(idx)
            else:
                clusters.append(current)
                current = [idx]
                anchor = val
        clusters.append(current)
        return clusters

    def _cluster_rows(self, spans: list[PdfSpan]) -> list[list[int]]:
        sorted_by_y = sorted(((s.y_center, i) for i, s in enumerate(spans)), key=lambda t: t[0])
        return self._greedy_cluster(sorted_by_y, self.y_tol)

    def _cluster_columns(self, spans: list[PdfSpan], page_width: float) -> list[list[int]]:
        x_tol = max(self.x_tol_min, page_width * 0.01)  # ~1% of page width
        sorted_by_x = sorted(((s.x_center, i) for i, s in enumerate(spans)), key=lambda t: t[0])
        return self._greedy_cluster(sorted_by_x, x_tol)

    # ---- Header detection ----
    def _is_header_row(self, cell_texts: list[str], avg_font: float, page_font_median: float) -> bool:
        """
        Generic header row scoring with layout-first cues:
          - font size prominence vs page median
          - uppercase ratio
          - trailing colon
          - cells are mostly NOT numeric/amount values
          - optional domain keywords (only if provided)
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
        if not prev_bins or not next_bins or len(prev_bins) != len(next_bins):
            return False
        for (a0, a1), (b0, b1) in zip(prev_bins, next_bins):
            if max(abs(a0 - b0), abs(a1 - b1)) > tol:
                return False
        return True

    def reconstruct(self, page_spans: list[list[PdfSpan]], pages: list[PageLayout]) -> tuple[list[TablePayload], list[IssuePayload]]:
        all_tables: list[TablePayload] = []
        all_issues: list[IssuePayload] = []
        prev_bins: list[tuple[float, float]] | None = None
        prev_order_index = 0
        prev_schema: list[str] | None = None

        for p_idx, (spans, layout) in enumerate(zip(page_spans, pages), start=1):
            width = float(getattr(layout, "width", 612.0) or 612.0)
            page_tables, page_issues, meta = self._build_page_tables(
                page_number=p_idx,
                page_width=width,
                page_spans=spans,
                page_layout=layout,
                order_offset=len(all_tables),
            )
            # Header propagation: if no explicit header and bins align with previous
            if not page_tables and prev_bins and spans:
                # None produced — try to create a propagated header notice if bins align
                # (No-op; we only signal if a table exists.)
                pass
            elif page_tables:
                # Compare first table bins to previous
                bins = meta.get("bins")
                schema = page_tables[0].column_schema
                if self._bins_compatible(prev_bins, bins) and schema == prev_schema:
                    # Mark propagated
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
    Ensure an ingestion job exists for the upload if the source type requires parsing.
    """

    if upload.source_type not in SUPPORTED_SOURCE_TYPES:
        return None

    try:
        from apps.knowledge.knowledge_preflight import ensure_upload_preflight

        ensure_upload_preflight(upload, trigger=trigger)
    except Exception:  # pragma: no cover - preflight must never block ingestion
        logger.exception("knowledge.preflight.enqueue_failed upload=%s", getattr(upload, "id", None))

    pending_jobs = KnowledgeIngestionJob.objects.filter(
        upload=upload,
        status__in=(
            KnowledgeIngestionJobStatus.QUEUED,
            KnowledgeIngestionJobStatus.RUNNING,
            KnowledgeIngestionJobStatus.DEFERRED,
        ),
        job_type=KnowledgeIngestionJobType.INGEST,
    )
    existing = (
        pending_jobs.filter(status=KnowledgeIngestionJobStatus.RUNNING).order_by("created_at").first()
        or pending_jobs.filter(status=KnowledgeIngestionJobStatus.QUEUED).order_by("created_at").first()
        or pending_jobs.filter(status=KnowledgeIngestionJobStatus.DEFERRED).order_by("created_at").first()
    )
    if existing:
        duplicate_count = pending_jobs.exclude(id=existing.id).exclude(status=KnowledgeIngestionJobStatus.RUNNING).update(
            status=KnowledgeIngestionJobStatus.CANCELLED,
        )
        if duplicate_count:
            logger.warning(
                "Cancelled duplicate ingestion jobs upload=%s kept_job=%s cancelled=%s",
                upload.id,
                existing.id,
                duplicate_count,
            )
        if existing.status == KnowledgeIngestionJobStatus.RUNNING:
            logger.info("Ingestion job already running upload=%s job=%s", upload.id, existing.id)
            return existing
        if not force:
            logger.info("Ingestion job already scheduled upload=%s job=%s status=%s", upload.id, existing.id, existing.status)
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
        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        active_jobs = KnowledgeIngestionJob.objects.filter(
            business_profile=upload.business_profile,
            job_type=KnowledgeIngestionJobType.INGEST,
        ).filter(
            Q(status=KnowledgeIngestionJobStatus.RUNNING)
            | (Q(status=KnowledgeIngestionJobStatus.QUEUED) & eligible)
        ).count()
        if active_jobs >= active_limit:
            job_status = KnowledgeIngestionJobStatus.DEFERRED
            payload["rate_limited"] = True

    job = KnowledgeIngestionJob.objects.create(
        business_profile=upload.business_profile,
        upload=upload,
        job_type=KnowledgeIngestionJobType.INGEST,
        status=job_status,
        max_attempts=max(1, int(getattr(settings, "INGEST_JOB_MAX_ATTEMPTS", 3))),
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


def get_ingestion_queue_health(
    *,
    business_profile_id: uuid.UUID | None = None,
) -> dict[str, object]:
    """
    Lightweight queue health snapshot for ops dashboards/alerts.

    Intended to be called from long-running workers (e.g., process_knowledge_ingestion --watch).
    """

    qs = KnowledgeIngestionJob.objects.all()
    if business_profile_id:
        qs = qs.filter(business_profile_id=business_profile_id)

    status_counts: dict[str, int] = {}
    for row in qs.values("status").annotate(count=Count("id")):
        status = str(row.get("status") or "")
        if not status:
            continue
        status_counts[status] = int(row.get("count") or 0)

    by_type: dict[str, dict[str, int]] = {}
    for row in qs.values("job_type", "status").annotate(count=Count("id")):
        job_type = str(row.get("job_type") or "")
        status = str(row.get("status") or "")
        if not job_type or not status:
            continue
        by_type.setdefault(job_type, {})[status] = int(row.get("count") or 0)

    now = timezone.now()
    pending_qs = qs.filter(status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.DEFERRED))
    oldest_pending = pending_qs.aggregate(oldest=Min("created_at")).get("oldest")
    oldest_pending_age_s: int | None = None
    if oldest_pending:
        try:
            oldest_pending_age_s = max(0, int((now - oldest_pending).total_seconds()))
        except Exception:
            oldest_pending_age_s = None

    failed_last_hour = qs.filter(
        status=KnowledgeIngestionJobStatus.FAILED,
        finished_at__gte=(now - timedelta(hours=1)),
    ).count()
    running_lease_expired = qs.filter(
        status=KnowledgeIngestionJobStatus.RUNNING,
        lease_expires_at__isnull=False,
        lease_expires_at__lt=now,
    ).count()

    pending_count = int(status_counts.get(KnowledgeIngestionJobStatus.QUEUED, 0)) + int(
        status_counts.get(KnowledgeIngestionJobStatus.DEFERRED, 0)
    )
    return {
        "pending": pending_count,
        "queued": int(status_counts.get(KnowledgeIngestionJobStatus.QUEUED, 0)),
        "deferred": int(status_counts.get(KnowledgeIngestionJobStatus.DEFERRED, 0)),
        "running": int(status_counts.get(KnowledgeIngestionJobStatus.RUNNING, 0)),
        "failed": int(status_counts.get(KnowledgeIngestionJobStatus.FAILED, 0)),
        "failed_last_hour": int(failed_last_hour),
        "running_lease_expired": int(running_lease_expired),
        "oldest_pending_age_s": oldest_pending_age_s,
        "status_counts": status_counts,
        "by_type": by_type,
        "observed_at": now.isoformat(),
    }


class KnowledgeIngestionService:
    """
    Pulled-text ingestion pipeline for PDF/DOCX/TXT uploads and external links.

    Designed to run inside a management command or async worker. Fetches queued jobs,
    extracts text, and persists normalized content so the orchestrator and dashboard
    can serve full document context.
    """

    def __init__(self, *, media_root: Path | None = None, enable_ocr: bool = True):
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
        self.pdfplumber_enabled = bool(getattr(settings, "RAG_PDFPLUMBER_ENABLED", True))
        self.pdf_table_extractor = str(getattr(settings, "RAG_PDF_TABLE_EXTRACTOR", "auto") or "auto").lower()
        self.pdfplumber_table_settings = self._normalize_pdfplumber_settings(
            getattr(settings, "RAG_PDFPLUMBER_TABLE_SETTINGS", None)
        )
        self.azure_di_enabled = bool(getattr(settings, "RAG_AZURE_DI_ENABLED", True))
        self.azure_di_endpoint = getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", None)
        self.azure_di_key = getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_KEY", None)
        self.azure_di_model = getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout")
        self.azure_di_api_version = getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "2023-07-31")
        self.azure_di_base_path = getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH", "formrecognizer")
        self.azure_di_locale = str(getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_LOCALE", "") or "").strip()
        self.azure_di_timeout_seconds = float(getattr(settings, "RAG_AZURE_DI_TIMEOUT_SECONDS", 60.0))
        self.azure_di_poll_interval_seconds = float(getattr(settings, "RAG_AZURE_DI_POLL_INTERVAL_SECONDS", 1.5))
        self.azure_di_max_polls = int(getattr(settings, "RAG_AZURE_DI_MAX_POLLS", 40))
        self.table_vlm_enabled = bool(getattr(settings, "RAG_TABLE_VLM_ENABLED", True))
        self.table_vlm_model = str(getattr(settings, "RAG_TABLE_VLM_MODEL", "gpt-4o") or "gpt-4o").strip()
        self.table_vlm_confidence_threshold = float(
            getattr(settings, "RAG_TABLE_VLM_CONFIDENCE_THRESHOLD", 0.6)
        )
        self.table_vlm_max_repairs = int(getattr(settings, "RAG_TABLE_VLM_MAX_REPAIRS_PER_UPLOAD", 3))
        self.table_schema_chunking = bool(getattr(settings, "RAG_TABLE_SCHEMA_CHUNKING", True))
        self.table_parent_max_rows = max(1, int(getattr(settings, "RAG_TABLE_PARENT_MAX_ROWS", 200)))
        self.table_parent_max_chars = max(2000, int(getattr(settings, "RAG_TABLE_PARENT_MAX_CHARS", 16000)))
        self.table_child_max_rows = max(0, int(getattr(settings, "RAG_TABLE_CHILD_MAX_ROWS", 500)))
        self.table_header_propagation_enabled = bool(
            getattr(settings, "RAG_TABLE_HEADER_PROPAGATION_ENABLED", True)
        )
        self.table_header_propagation_min_overlap = float(
            getattr(settings, "RAG_TABLE_HEADER_PROPAGATION_MIN_OVERLAP", 0.45)
        )
        self.table_dedupe_enabled = bool(getattr(settings, "RAG_TABLE_DEDUPE_ENABLED", True))
        self.table_dedupe_min_overlap = float(getattr(settings, "RAG_TABLE_DEDUPE_MIN_OVERLAP", 0.6))
        self.table_postprocess_row_limit = max(
            5, int(getattr(settings, "RAG_TABLE_POSTPROCESS_ROW_LIMIT", 40))
        )
        self.ocr_normalization_enabled = bool(getattr(settings, "RAG_OCR_NORMALIZATION_ENABLED", True))
        self.ocr_word_replacements = self._compile_ocr_replacements(
            getattr(settings, "RAG_OCR_NORMALIZATION_REPLACEMENTS", None)
        )
        self.ocr_percent_fix_enabled = bool(getattr(settings, "RAG_OCR_PERCENT_FIX_ENABLED", True))
        self.ocr_percent_space_fix_enabled = bool(getattr(settings, "RAG_OCR_PERCENT_SPACE_FIX_ENABLED", True))
        self.ocr_percent_sanity_max = float(getattr(settings, "RAG_OCR_PERCENT_SANITY_MAX", 100.0))
        self.ocr_currency_spacing_enabled = bool(getattr(settings, "RAG_OCR_CURRENCY_SPACING_ENABLED", True))
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
        self.chunk_quality_min_tokens = max(1, int(getattr(settings, "RAG_CHUNK_MIN_TOKENS", 20)))
        self.chunk_quality_min_unique_ratio = float(getattr(settings, "RAG_CHUNK_MIN_UNIQUE_RATIO", 0.35))
        if not (0.0 <= self.chunk_quality_min_unique_ratio <= 1.0):
            self.chunk_quality_min_unique_ratio = 0.35
        self.chunk_quality_low_score = float(getattr(settings, "RAG_CHUNK_LOW_QUALITY_SCORE", 0.45))
        if not (0.0 <= self.chunk_quality_low_score <= 1.0):
            self.chunk_quality_low_score = 0.45
        self.chunk_quality_heading_max_lines = max(
            1,
            int(getattr(settings, "RAG_CHUNK_HEADING_MAX_LINES", 3)),
        )
        self.chunk_quality_heading_max_tokens = max(
            1,
            int(getattr(settings, "RAG_CHUNK_HEADING_MAX_TOKENS", 12)),
        )
        self.dataset_mode_enabled = bool(getattr(settings, "DATASET_MODE_ENABLED", True))
        self.dataset_row_threshold = max(
            1,
            int(
                getattr(
                    settings,
                    "DATASET_MODE_ROW_THRESHOLD",
                    getattr(settings, "RAG_TABLE_LARGE_ROW_LIMIT", 20000),
                )
            ),
        )
        self.dataset_preview_rows = max(5, int(getattr(settings, "DATASET_MODE_PREVIEW_ROWS", 200)))
        self.dataset_sample_rows = max(1, int(getattr(settings, "DATASET_MODE_SAMPLE_ROWS", 20)))
        self.dataset_storage_format = str(getattr(settings, "DATASET_STORAGE_FORMAT", "csv_gz") or "csv_gz").strip()
        if self.dataset_storage_format not in {"csv_gz"}:
            self.dataset_storage_format = "csv_gz"
        self.job_lease_seconds = max(60, int(getattr(settings, "INGEST_JOB_LEASE_SECONDS", 1800)))
        self.job_retry_base_seconds = max(1.0, float(getattr(settings, "INGEST_JOB_RETRY_BASE_SECONDS", 5.0)))
        self.job_retry_max_seconds = max(
            self.job_retry_base_seconds,
            float(getattr(settings, "INGEST_JOB_RETRY_MAX_SECONDS", 300.0)),
        )
        self.job_retry_jitter_seconds = max(0.0, float(getattr(settings, "INGEST_JOB_RETRY_JITTER_SECONDS", 2.0)))
        self.ingest_job_max_attempts = max(1, int(getattr(settings, "INGEST_JOB_MAX_ATTEMPTS", 3)))
        self.embed_job_max_attempts = max(1, int(getattr(settings, "INGEST_EMBED_JOB_MAX_ATTEMPTS", 5)))
        self.requeue_stale_jobs = bool(getattr(settings, "INGEST_JOB_REQUEUE_STALE_ENABLED", True))

    # ------------------------------------------------------------------
    # Job coordination

    @staticmethod
    def _normalize_pdfplumber_settings(raw: Any) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(raw, Mapping):
            raw = [raw]
        settings_list: list[tuple[str, dict[str, Any]]] = []
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for idx, entry in enumerate(raw):
                if not isinstance(entry, Mapping):
                    continue
                label = str(entry.get("label") or entry.get("name") or f"custom_{idx + 1}").strip() or f"custom_{idx + 1}"
                inner = entry.get("settings")
                if isinstance(inner, Mapping):
                    settings = dict(inner)
                else:
                    settings = {k: v for k, v in entry.items() if k not in {"label", "name", "settings"}}
                if settings:
                    settings_list.append((label, settings))
        return settings_list or list(PDFPLUMBER_DEFAULT_TABLE_SETTINGS)

    def _job_max_attempts(self, job: KnowledgeIngestionJob) -> int:
        configured = int(getattr(job, "max_attempts", 0) or 0)
        if configured > 0:
            return configured
        if job.job_type == KnowledgeIngestionJobType.EMBED:
            return self.embed_job_max_attempts
        return self.ingest_job_max_attempts

    def _job_retry_delay_seconds(self, attempt_count: int, job: KnowledgeIngestionJob) -> float:
        normalized_attempt = max(1, int(attempt_count))
        if job.job_type == KnowledgeIngestionJobType.EMBED:
            base = max(1.0, float(getattr(settings, "INGEST_EMBED_JOB_RETRY_BASE_SECONDS", self.job_retry_base_seconds)))
        else:
            base = self.job_retry_base_seconds
        delay = min(self.job_retry_max_seconds, base * (2 ** (normalized_attempt - 1)))
        jitter = 0.0
        if self.job_retry_jitter_seconds:
            jitter = random.uniform(0.0, self.job_retry_jitter_seconds)
        return delay + jitter

    def _heartbeat_job(self, job: KnowledgeIngestionJob, *, now: timezone.datetime | None = None) -> None:
        if not job or getattr(job, "status", None) != KnowledgeIngestionJobStatus.RUNNING:
            return
        current = now or timezone.now()
        lease = current + timedelta(seconds=self.job_lease_seconds)
        KnowledgeIngestionJob.objects.filter(id=job.id, status=KnowledgeIngestionJobStatus.RUNNING).update(
            lease_expires_at=lease
        )

    def _requeue_job_with_backoff(self, job: KnowledgeIngestionJob, message: str, *, reason: str) -> bool:
        """Return True when a retry was scheduled, False when the job is now terminal."""
        now = timezone.now()
        max_attempts = self._job_max_attempts(job)
        next_attempt = max(0, int(getattr(job, "attempt_count", 0) or 0)) + 1
        payload = dict(job.payload or {})
        attempt_events = payload.get("attempts")
        if not isinstance(attempt_events, list):
            attempt_events = []
        attempt_events.append(
            {
                "attempt": next_attempt,
                "at": now.isoformat(),
                "reason": reason,
                "error": (message or "")[:400],
            }
        )
        payload["attempts"] = attempt_events[-10:]
        payload["last_error_at"] = now.isoformat()
        payload["last_error_reason"] = reason

        if next_attempt < max_attempts:
            delay = self._job_retry_delay_seconds(next_attempt, job)
            run_after = now + timedelta(seconds=float(delay))
            KnowledgeIngestionJob.objects.filter(id=job.id).update(
                status=KnowledgeIngestionJobStatus.QUEUED,
                attempt_count=next_attempt,
                run_after=run_after,
                started_at=None,
                lease_expires_at=None,
                error_detail=message,
                payload=payload,
            )
            logger.warning(
                "ingest.job_retry_scheduled job=%s upload=%s type=%s attempt=%s/%s run_after=%s reason=%s error=%s",
                job.id,
                job.upload_id,
                job.job_type,
                next_attempt,
                max_attempts,
                run_after.isoformat(),
                reason,
                (message or "")[:200],
            )
            return True

        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.FAILED,
            attempt_count=next_attempt,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail=message,
            payload=payload,
        )
        logger.error(
            "ingest.job_retry_exhausted job=%s upload=%s type=%s attempts=%s error=%s",
            job.id,
            job.upload_id,
            job.job_type,
            next_attempt,
            (message or "")[:200],
        )
        return False

    def _mark_job_failed_terminal(self, job: KnowledgeIngestionJob, message: str, *, reason: str) -> None:
        now = timezone.now()
        next_attempt = max(0, int(getattr(job, "attempt_count", 0) or 0)) + 1
        payload = dict(job.payload or {})
        attempt_events = payload.get("attempts")
        if not isinstance(attempt_events, list):
            attempt_events = []
        attempt_events.append(
            {
                "attempt": next_attempt,
                "at": now.isoformat(),
                "reason": reason,
                "error": (message or "")[:400],
            }
        )
        payload["attempts"] = attempt_events[-10:]
        payload["last_error_at"] = now.isoformat()
        payload["last_error_reason"] = reason
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.FAILED,
            attempt_count=next_attempt,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail=message,
            payload=payload,
        )

    def _requeue_stale_running_jobs(self, *, limit: int = 25) -> int:
        if not self.requeue_stale_jobs:
            return 0
        now = timezone.now()
        cutoff = now - timedelta(seconds=self.job_lease_seconds)
        stale_jobs = list(
            KnowledgeIngestionJob.objects.filter(status=KnowledgeIngestionJobStatus.RUNNING)
            .filter(
                Q(lease_expires_at__lt=now)
                | Q(lease_expires_at__isnull=True, started_at__lt=cutoff)
            )
            .select_related("upload")
            .order_by("started_at")[: max(1, int(limit))]
        )
        if not stale_jobs:
            return 0
        for job in stale_jobs:
            scheduled = self._requeue_job_with_backoff(job, "auto-requeue: ingestion job lease expired", reason="lease_expired")
            if not scheduled:
                upload = job.upload
                if job.job_type == KnowledgeIngestionJobType.EMBED:
                    metadata = dict(upload.ingestion_metadata or {})
                    embedding_meta = metadata.get("embedding")
                    if not isinstance(embedding_meta, dict):
                        embedding_meta = {}
                    embedding_meta.update(
                        {
                            "status": "failed",
                            "job_id": str(job.id),
                            "failed_at": now.isoformat(),
                            "error": "auto-requeue: ingestion job lease expired",
                        }
                    )
                    metadata["embedding"] = embedding_meta
                    upload.ingestion_metadata = metadata
                    upload.save(update_fields=["ingestion_metadata", "updated_at"])
                else:
                    upload.ingestion_error = "auto-requeue: ingestion job lease expired"
                    upload.status = KnowledgeStatus.FAILED
                    upload.save(update_fields=["ingestion_error", "status", "updated_at"])
        logger.warning("ingest.job_requeued_stale count=%s", len(stale_jobs))
        return len(stale_jobs)

    def _json_entity_limit(self, business_profile) -> int:
        if not business_profile:
            return self.default_json_entity_limit
        metadata = business_profile.metadata if isinstance(getattr(business_profile, "metadata", None), dict) else {}
        override = metadata.get("ingest_max_json_entities")
        try:
            value = int(override)
            return max(1, value)
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
        job = self._claim_next_job()

        if job is None:
            return None
        tenant_id = getattr(job, "business_profile_id", None)
        try:
            with tenant_context(tenant_id):
                with TRACER.start_as_current_span("ingest.process_job") as span:
                    if span.is_recording():
                        span.set_attribute("ingest.job_id", str(job.id))
                        span.set_attribute("ingest.job_type", str(job.job_type))
                    if job.job_type == KnowledgeIngestionJobType.EMBED:
                        result = self._process_embedding_job(job)
                    else:
                        upload = job.upload
                        job_started_at = time.perf_counter()
                        logger.info("ingest.start upload=%s job=%s source_type=%s", upload.id, job.id, upload.source_type)
                        structured_log(
                            "rag",
                            "ingest.job_start",
                            {
                                "job_id": str(job.id),
                                "upload_id": str(upload.id),
                                "source_type": upload.source_type,
                                "size_bytes": getattr(upload, "size_bytes", None),
                            },
                            context={"business": upload.business_profile_id, "upload": upload.id, "job": job.id},
                            logger_obj=logger,
                        )
                        try:
                            from apps.knowledge.knowledge_preflight import ensure_upload_preflight

                            preflight = ensure_upload_preflight(upload, trigger="ingest_job_start")
                            if isinstance(preflight, dict):
                                payload = dict(job.payload or {})
                                payload["preflight"] = {
                                    "status": preflight.get("status"),
                                    "format": preflight.get("format"),
                                    "suggested_kind": preflight.get("suggested_kind"),
                                    "warnings": list(preflight.get("warnings") or [])[:8],
                                }
                                KnowledgeIngestionJob.objects.filter(id=job.id).update(payload=payload)
                                job.payload = payload
                                if str(preflight.get("status") or "").lower() == "error":
                                    warnings = preflight.get("warnings") or []
                                    description = "; ".join([str(w) for w in warnings if w])[:400] if warnings else "Preflight blocked ingestion."
                                    raise KnowledgeIngestionError(f"preflight: {description}")
                        except KnowledgeIngestionError:
                            raise
                        except Exception as exc:  # pragma: no cover - preflight must not block ingestion
                            logger.warning("knowledge.preflight.job_start_failed upload=%s error=%s", upload.id, exc)
                        try:
                            with TRACER.start_as_current_span("ingest.extract") as extract_span:
                                extraction = self._extract_upload(upload)
                                characters = len(extraction.text)
                                if extract_span.is_recording():
                                    extract_span.set_attribute("ingest.characters", characters)
                                    extract_span.set_attribute("ingest.format", extraction.format_hint or "unknown")
                            with TRACER.start_as_current_span("ingest.persist") as persist_span:
                                self._persist_extraction(upload, extraction)
                                self._mark_job_completed(job, extra={"characters": characters, "format": extraction.format_hint})
                                if persist_span.is_recording():
                                    persist_span.set_attribute("ingest.characters", characters)
                            logger.info(
                                "ingest.done upload=%s job=%s chars=%s format=%s",
                                upload.id,
                                job.id,
                                characters,
                                extraction.format_hint,
                            )
                            duration_ms = int((time.perf_counter() - job_started_at) * 1000.0)
                            warn_ms = int(getattr(settings, "INGEST_SLO_WARN_MS", 60000) or 0)
                            slow = bool(warn_ms and duration_ms >= warn_ms)
                            ingestion_meta = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
                            dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, dict) else None
                            dataset_enabled = bool(isinstance(dataset_meta, dict) and dataset_meta.get("enabled"))
                            sheet_count = None
                            row_count = None
                            storage_format = None
                            if isinstance(dataset_meta, dict):
                                storage_format = dataset_meta.get("storage_format")
                                row_count = dataset_meta.get("row_count")
                                sheets = dataset_meta.get("sheets")
                                if isinstance(sheets, list):
                                    sheet_count = len([s for s in sheets if isinstance(s, dict)])

                            structured_log(
                                "rag",
                                "ingest.job_done",
                                {
                                    "job_id": str(job.id),
                                    "upload_id": str(upload.id),
                                    "status": "completed",
                                    "duration_ms": duration_ms,
                                    "format": extraction.format_hint,
                                    "chars": characters,
                                    "chunk_count": getattr(upload, "chunk_count", None),
                                    "token_count": getattr(upload, "token_count", None),
                                    "dataset_enabled": dataset_enabled,
                                    "dataset_storage_format": storage_format,
                                    "dataset_row_count": row_count,
                                    "dataset_sheet_count": sheet_count,
                                    "slo": "slow" if slow else None,
                                    "slo_warn_ms": warn_ms if slow else None,
                                },
                                context={"business": upload.business_profile_id, "upload": upload.id, "job": job.id},
                                logger_obj=logger,
                                level=logging.WARNING if slow else logging.INFO,
                            )

                            result = IngestionJobResult(
                                job_id=job.id,
                                upload_id=upload.id,
                                job_type=job.job_type,
                                status=KnowledgeIngestionJobStatus.COMPLETED,
                                characters=characters,
                            )
                        except KnowledgeIngestionError as exc:
                            self._handle_failure(job, str(exc), exc=exc)
                            logger.warning("Ingestion failed upload=%s job=%s error=%s", upload.id, job.id, exc)
                            duration_ms = int((time.perf_counter() - job_started_at) * 1000.0)
                            job.refresh_from_db(fields=["status"])
                            structured_log(
                                "rag",
                                "ingest.job_done",
                                {
                                    "job_id": str(job.id),
                                    "upload_id": str(upload.id),
                                    "status": "failed",
                                    "duration_ms": duration_ms,
                                    "error": str(exc)[:200],
                                },
                                context={"business": upload.business_profile_id, "upload": upload.id, "job": job.id},
                                logger_obj=logger,
                                level=logging.WARNING,
                            )
                            result = IngestionJobResult(
                                job_id=job.id,
                                upload_id=upload.id,
                                job_type=job.job_type,
                                status=job.status,
                                characters=0,
                                error=str(exc),
                            )
                if span.is_recording():
                    span.set_attribute("ingest.result_status", result.status.value)
                return result
        except Exception as exc:  # pragma: no cover - defensive guardrail
            logger.exception(
                "ingest.unexpected_error upload=%s job=%s", getattr(job, "upload_id", None), getattr(job, "id", None)
            )
            self._handle_failure(job, f"unexpected ingestion error: {exc}", exc=exc)
            job.refresh_from_db(fields=["status"])
            structured_log(
                "rag",
                "ingest.job_done",
                {
                    "job_id": str(getattr(job, "id", "")),
                    "upload_id": str(getattr(job, "upload_id", "")),
                    "status": "failed",
                    "error": str(exc)[:200],
                },
                context={
                    "business": getattr(job, "business_profile_id", None),
                    "upload": getattr(job, "upload_id", None),
                    "job": getattr(job, "id", None),
                },
                logger_obj=logger,
                level=logging.ERROR,
            )
            return IngestionJobResult(
                job_id=job.id,
                upload_id=job.upload_id,
                job_type=job.job_type,
                status=job.status,
                characters=0,
                error=str(exc),
            )

    def _process_embedding_job(self, job: KnowledgeIngestionJob) -> IngestionJobResult:
        with TRACER.start_as_current_span("ingest.embed_job") as span:
            payload = job.payload or {}
            chunk_ids = payload.get("chunk_ids") if isinstance(payload, dict) else []
            normalized_ids: list[uuid.UUID] = []
            for value in chunk_ids or []:
                try:
                    normalized_ids.append(uuid.UUID(str(value)))
                except (TypeError, ValueError):
                    continue
            if span.is_recording():
                span.set_attribute("ingest.embed.chunk_ids", len(normalized_ids))
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
                self._handle_failure(job, error, retryable=True)
                job.refresh_from_db(fields=["status"])
                return IngestionJobResult(
                    job_id=job.id,
                    upload_id=job.upload_id,
                    job_type=job.job_type,
                    status=job.status,
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
                    with TRACER.start_as_current_span("ingest.embed_batch") as batch_span:
                        vectors = provider.embed_texts(texts)
                        if batch_span.is_recording():
                            batch_span.set_attribute("ingest.embed.batch_size", len(batch))
                except EmbeddingProviderError as exc:
                    self._handle_failure(job, f"Embedding batch failed: {exc}", exc=exc)
                    job.refresh_from_db(fields=["status"])
                    return IngestionJobResult(
                        job_id=job.id,
                        upload_id=job.upload_id,
                        job_type=job.job_type,
                        status=job.status,
                        characters=processed,
                        error=str(exc),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    self._handle_failure(job, f"Embedding batch exception: {exc}", exc=exc)
                    job.refresh_from_db(fields=["status"])
                    return IngestionJobResult(
                        job_id=job.id,
                        upload_id=job.upload_id,
                        job_type=job.job_type,
                        status=job.status,
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
                self._try_update_azure_search_embeddings(upload=job.upload, chunks=updated)
            if processed_ids:
                self._update_upload_embedding_metadata(job.upload, processed_ids=processed_ids)
            self._mark_job_completed(job, extra={"embedded_chunks": processed})
            if span.is_recording():
                span.set_attribute("ingest.embed.chunks", processed)
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
        if upload.source_type in {KnowledgeSourceType.FILE, KnowledgeSourceType.INTEGRATION}:
            file_detail = getattr(upload, "file_detail", None)
            if not isinstance(file_detail, KnowledgeUploadFile):
                upload = KnowledgeUpload.objects.select_related("file_detail").get(id=upload.id)
                file_detail = upload.file_detail
            if file_detail is None:
                raise KnowledgeIngestionError("File metadata missing for upload.")
            limit = self._json_entity_limit(upload.business_profile)
            return self._extract_from_file(file_detail, upload=upload, entity_limit=limit)

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
        logger.info("extract.file.start path=%s format=%s", absolute, format_hint)
        if format_hint == "jsonl":
            return self._extract_jsonl_dataset(absolute, file_detail=file_detail, upload=upload)
        if format_hint == "json":
            limit = entity_limit or self.default_json_entity_limit
            return self._extract_json(absolute, entity_limit=limit, upload=upload)
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

        # Geometry-based reconstruction (PDF only, when PyMuPDF available)
        geometry_tables: list[TablePayload] = []
        geom_issues: list[IssuePayload] = []
        if format_hint == "pdf" and fitz is not None:
            try:
                page_spans = self.page_renderer.extract_pdf_spans(absolute)
                for idx, spans in enumerate(page_spans, start=1):
                    logger.info("geometry.spans page=%s count=%s", idx, len(spans))
                recon = GeometryTableReconstructor()
                geometry_tables, geom_issues = recon.reconstruct(page_spans, layout_result.pages)
                logger.info("geometry.tables path=%s count=%s", absolute, len(geometry_tables))
            except Exception as exc:  # best-effort guard
                logger.warning("geometry.reconstruct_failed path=%s err=%s", absolute, exc)
                geometry_tables, geom_issues = [], [
                    IssuePayload(
                        code="geometry_failed",
                        severity=KnowledgeIssueSeverity.ERROR.value,
                        description=str(exc),
                    )
                ]

        pdfplumber_candidates: dict[str, list[TablePayload]] = {}
        pdfplumber_issues: list[IssuePayload] = []
        pdfplumber_meta: dict[str, Any] = {}
        if format_hint == "pdf" and self.pdfplumber_enabled:
            extractor = PdfPlumberTableExtractor(table_settings=self.pdfplumber_table_settings)
            pdfplumber_candidates, pdfplumber_issues, pdfplumber_meta = extractor.extract_candidates(absolute)

        azure_tables: list[TablePayload] = []
        azure_issues: list[IssuePayload] = []
        azure_meta: dict[str, Any] = {}
        if format_hint == "pdf" and self.azure_di_enabled:
            azure_extractor = AzureDocumentIntelligenceExtractor(
                endpoint=self.azure_di_endpoint,
                key=self.azure_di_key,
                model=self.azure_di_model,
                api_version=self.azure_di_api_version,
                base_path=self.azure_di_base_path,
                locale=self.azure_di_locale,
                timeout_seconds=self.azure_di_timeout_seconds,
                poll_interval_seconds=self.azure_di_poll_interval_seconds,
                max_polls=self.azure_di_max_polls,
            )
            azure_tables, azure_issues, azure_meta = azure_extractor.extract_tables(absolute)

        heuristic_tables, table_issues = self.table_detector.detect_tables(layout_result.pages)
        filtered_heuristics, suppress_issues = self._suppress_list_like_heuristics(heuristic_tables)
        if len(filtered_heuristics) != len(heuristic_tables):
            logger.info(
                "heuristic.suppressed_list_like_tables before=%s after=%s",
                len(heuristic_tables),
                len(filtered_heuristics),
            )

        candidates: dict[str, list[TablePayload]] = {}
        candidates.update(pdfplumber_candidates)
        if azure_tables:
            candidates["azure:layout"] = azure_tables
        if geometry_tables:
            candidates["geometry"] = geometry_tables
        if filtered_heuristics:
            candidates["heuristic"] = filtered_heuristics

        selected_extractor, tables, selection_meta = self._select_table_candidates(candidates)
        issues = layout_result.issues + table_issues + geom_issues + suppress_issues + pdfplumber_issues + azure_issues

        repair_meta: dict[str, Any] = {}
        if tables:
            tables, repair_issues, repair_meta = self._repair_tables_with_vlm(
                absolute,
                tables,
            )
            issues.extend(repair_issues)

        postprocess_meta: dict[str, Any] = {}
        if tables:
            tables, postprocess_issues, postprocess_meta = self._postprocess_tables(tables)
            issues.extend(postprocess_issues)



        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(tables, upload=upload, config=ingest_config)
        issues = issues + limit_issues

        # PDFs are documents (not datasets). Indexing per-row "table entities" from a PDF tends to
        # flood retrieval with low-context chunks (e.g. `Table_1: ...`) and mislead downstream
        # prompting into "dataset-like" behavior. We keep structured table artifacts, but skip
        # row-entity generation for PDFs.
        if (format_hint or "").lower() == "pdf":
            table_entities: list[dict[str, Any]] = []
        else:
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
        if format_hint == "pdf":
            extraction_meta = {
                "selected_extractor": selected_extractor,
                "candidate_counts": {key: len(val) for key, val in candidates.items()},
                "candidate_scores": selection_meta.get("scores", {}),
            }
            if pdfplumber_meta:
                extraction_meta["pdfplumber"] = pdfplumber_meta
            if azure_meta:
                extraction_meta["azure_di"] = azure_meta
            if repair_meta:
                extraction_meta["table_repairs"] = repair_meta
            if postprocess_meta:
                extraction_meta["table_postprocess"] = postprocess_meta
            metadata["table_extraction"] = extraction_meta
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
        if format_hint == "pdf":
            return self._extract_pdf(path)
        if format_hint == "docx":
            return self._extract_docx(path)
        if format_hint in {"txt", "text", "csv", "tsv"}:
            return self._extract_text_file(path)
        raise UnsupportedFormatError(f"Unsupported file type {format_hint}.")

    def _estimate_table_structure_confidence(self, table: TablePayload) -> float:
        """
        Best-effort proxy for table structure confidence (0.0-1.0).

        Azure DI tables may carry their own `structure_confidence`. For other extractors
        we derive a conservative estimate from quality signals (row consistency, fill
        ratio, header confidence) and basic collapse indicators (e.g., single-column
        tables with multiple data rows).
        """
        meta = table.metadata if isinstance(table.metadata, Mapping) else {}
        detected_via = str(meta.get("detected_via") or "").lower()

        base = 0.82
        if "azure" in detected_via:
            base = 0.9
        elif "geometry" in detected_via:
            base = 0.86
        elif "pdfplumber" in detected_via:
            base = 0.76
        elif "heuristic" in detected_via:
            base = 0.72

        assessment = self._assess_table_quality(table)
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        signals = signals if isinstance(signals, Mapping) else {}

        def _num(value: Any) -> float:
            if isinstance(value, (int, float)):
                return float(value)
            try:
                return float(str(value))
            except Exception:
                return 0.0

        row_consistency = max(0.0, min(1.0, _num(signals.get("row_consistency"))))
        fill_ratio = max(0.0, min(1.0, _num(signals.get("cell_fill_ratio"))))
        header_confidence = max(0.0, min(1.0, _num(signals.get("header_confidence"))))

        confidence = float(base)
        if row_consistency:
            confidence *= 0.6 + (0.4 * row_consistency)
        if fill_ratio:
            confidence *= 0.65 + (0.35 * fill_ratio)
        confidence *= 0.8 + (0.2 * header_confidence)

        schema_cols = len(table.column_schema or [])
        row_cols = max((len(row.cells or []) for row in (table.rows or [])), default=0)
        columns = max(schema_cols, row_cols)
        data_rows = len([row for row in (table.rows or []) if (row.metadata or {}).get("row_type") != "header"])

        if columns <= 1 and data_rows >= 2:
            confidence = min(confidence, 0.35)
        if signals.get("row_misalignment"):
            confidence = min(confidence, 0.55)
        if signals.get("spaced_characters") or signals.get("nonsense_columns"):
            confidence = min(confidence, 0.45)

        return round(max(0.0, min(1.0, confidence)), 4)

    def _get_table_structure_confidence(self, table: TablePayload) -> float | None:
        meta = table.metadata if isinstance(table.metadata, dict) else None
        if meta is None:
            return None
        existing = meta.get("structure_confidence")
        if isinstance(existing, (int, float)):
            return max(0.0, min(1.0, float(existing)))
        estimated = self._estimate_table_structure_confidence(table)
        meta["structure_confidence"] = estimated
        return estimated

    def _score_table_set(self, tables: Sequence[TablePayload]) -> float:
        if not tables:
            return 0.0
        total_score = 0.0
        for table in tables:
            assessment = self._assess_table_quality(table)
            quality = float(assessment.get("quality_score") or 0.0)
            structure_conf = self._get_table_structure_confidence(table)
            if isinstance(structure_conf, (int, float)):
                quality *= max(0.2, min(1.0, float(structure_conf)))
            signals = assessment.get("signals") or {}
            if isinstance(signals, Mapping) and (
                signals.get("card_mockup")
                or signals.get("card_number_pattern")
                or signals.get("valid_thru")
            ):
                quality = max(0.0, quality - 0.4)
            data_rows = len(
                [row for row in table.rows if (row.metadata or {}).get("row_type") != "header"]
            )
            weight = 1.0 + (min(5, data_rows) / 5.0)
            total_score += quality * weight
        return round(total_score, 4)

    def _select_table_candidates(
        self,
        candidates: Mapping[str, list[TablePayload]],
    ) -> tuple[str, list[TablePayload], dict[str, Any]]:
        if not candidates:
            return "none", [], {"scores": {}}
        scores = {name: self._score_table_set(tables) for name, tables in candidates.items()}

        preferred = (self.pdf_table_extractor or "auto").strip().lower()
        selected = ""
        if preferred and preferred != "auto":
            if preferred in candidates:
                selected = preferred
            elif preferred == "pdfplumber":
                pdf_options = [name for name in candidates if name.startswith("pdfplumber:")]
                if pdf_options:
                    selected = max(pdf_options, key=lambda name: scores.get(name, 0.0))
            elif preferred == "azure":
                azure_options = [name for name in candidates if name.startswith("azure")]
                if azure_options:
                    selected = max(azure_options, key=lambda name: scores.get(name, 0.0))
            elif preferred.startswith("pdfplumber"):
                suffix = preferred.replace("pdfplumber", "").lstrip(":-_")
                if suffix:
                    key = f"pdfplumber:{suffix}"
                    if key in candidates:
                        selected = key
            elif preferred.startswith("azure"):
                suffix = preferred.replace("azure", "").lstrip(":-_")
                key = f"azure:{suffix}" if suffix else "azure"
                if key in candidates:
                    selected = key

        if not selected:
            ordered = sorted(
                candidates.keys(),
                key=lambda name: (scores.get(name, 0.0), len(candidates.get(name) or []), name),
                reverse=True,
            )
            selected = ordered[0]

        return selected, candidates.get(selected, []), {"scores": scores}

    def _repair_tables_with_vlm(
        self,
        path: Path,
        tables: list[TablePayload],
    ) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        if not self.table_vlm_enabled or not tables:
            return tables, [], {}
        if fitz is None:
            return tables, [
                IssuePayload(
                    code="table_vlm_no_renderer",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="VLM repair skipped because PyMuPDF is unavailable.",
                )
            ], {}

        candidates: list[tuple[int, float]] = []
        for idx, table in enumerate(tables):
            structure_conf = self._get_table_structure_confidence(table)
            if isinstance(structure_conf, (int, float)) and structure_conf >= self.table_vlm_confidence_threshold:
                continue
            if not table.page_number or not table.bbox:
                continue
            candidates.append((idx, float(structure_conf) if isinstance(structure_conf, (int, float)) else 0.0))

        if not candidates:
            return tables, [], {"attempted": 0, "repaired": 0, "skipped": len(tables), "model": self.table_vlm_model}

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return tables, [
                IssuePayload(
                    code="table_vlm_missing_key",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="OPENAI_API_KEY not configured; skipping VLM repair.",
                )
            ], {}

        try:
            from openai import OpenAI
        except Exception as exc:
            return tables, [
                IssuePayload(
                    code="table_vlm_missing_client",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description=f"OpenAI client unavailable: {exc}",
                )
            ], {}

        client = OpenAI(api_key=api_key)
        repaired: list[TablePayload] = list(tables)
        issues: list[IssuePayload] = []
        meta: dict[str, Any] = {"attempted": 0, "repaired": 0, "model": self.table_vlm_model}
        remaining_budget = max(0, self.table_vlm_max_repairs)

        def _repair_sort_key(item: tuple[int, float]) -> tuple[float, int, int]:
            index, conf = item
            table = tables[index]
            data_rows = len(
                [row for row in (table.rows or []) if (row.metadata or {}).get("row_type") != "header"]
            )
            columns = max(len(table.column_schema or []), max((len(r.cells or []) for r in (table.rows or [])), default=0))
            return (conf, -data_rows, -columns)

        for idx, conf in sorted(candidates, key=_repair_sort_key):
            if remaining_budget <= 0:
                break
            table = tables[idx]

            crop_bytes = self._render_table_crop(path, int(table.page_number), table.bbox)
            if not crop_bytes:
                continue

            meta["attempted"] += 1
            remaining_budget -= 1
            payload = self._run_vlm_table_repair(client, crop_bytes)
            if not payload:
                issues.append(
                    IssuePayload(
                        code="table_vlm_failed",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"VLM repair failed for table {table.order_index}.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={"structure_confidence": conf},
                    )
                )
                continue

            vlm_table = self._table_payload_from_vlm(
                payload=payload,
                order_index=table.order_index,
                page_number=int(table.page_number),
                bbox=table.bbox,
                title=table.title,
                section_heading=table.section_heading,
                source_metadata=table.metadata,
            )
            if vlm_table:
                meta["repaired"] += 1
                repaired[idx] = vlm_table

        return repaired, issues, meta

    @staticmethod
    def _render_table_crop(path: Path, page_number: int, bbox: dict[str, float]) -> bytes | None:
        if fitz is None:
            return None
        doc = None
        try:
            x0 = float(bbox.get("x0", 0.0))
            y0 = float(bbox.get("y0", 0.0))
            x1 = float(bbox.get("x1", 0.0))
            y1 = float(bbox.get("y1", 0.0))
            if x1 <= x0 or y1 <= y0:
                return None

            doc = fitz.open(path)
            page = doc[page_number - 1]
            rect = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(clip=rect, dpi=200)
            return pix.tobytes("png")
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def _run_vlm_table_repair(self, client: Any, image_bytes: bytes) -> dict[str, Any] | None:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        prompt = (
            "Extract the table from this image. "
            "Return strict JSON with keys: columns (array of strings) and rows "
            "(array of arrays). Rows should contain only data rows (no header row)."
        )
        try:
            response = client.chat.completions.create(
                model=self.table_vlm_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded}"},
                            },
                        ],
                    }
                ],
                max_tokens=1200,
            )
        except Exception as exc:
            logger.warning("table.vlm.repair_failed error=%s", exc)
            return None

        content = ""
        try:
            content = response.choices[0].message.content or ""
        except Exception:
            content = ""
        if not content:
            return None
        return self._parse_vlm_table_json(content)

    @staticmethod
    def _parse_vlm_table_json(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if "columns" not in payload or "rows" not in payload:
            return None
        return payload

    def _table_payload_from_vlm(
        self,
        *,
        payload: Mapping[str, Any],
        order_index: int,
        page_number: int,
        bbox: dict[str, float],
        title: str,
        section_heading: str,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> TablePayload | None:
        columns = payload.get("columns") or []
        rows = payload.get("rows") or []
        if not isinstance(columns, list) or not isinstance(rows, list):
            return None

        column_schema: list[str] = []
        for idx, col in enumerate(columns):
            column_schema.append(TableDetector._normalize_header_cell(str(col), idx))
        if not column_schema:
            max_cols = max((len(r) for r in rows if isinstance(r, list)), default=0)
            column_schema = [f"column_{i+1}" for i in range(max_cols)]

        table_rows: list[TableRowPayload] = []
        header_cells: list[TableCellPayload] = []
        for col_idx, label in enumerate(columns):
            header_cells.append(
                TableCellPayload(
                    row_index=0,
                    column_index=col_idx,
                    column_key=column_schema[col_idx],
                    raw_text=str(label),
                    normalized_value=TableDetector._normalize_cell_value(str(label)),
                    bbox=bbox,
                    confidence=None,
                )
            )
        if header_cells:
            table_rows.append(
                TableRowPayload(
                    row_index=0,
                    page_number=page_number,
                    bbox=bbox,
                    raw_text=" | ".join(str(c.raw_text) for c in header_cells),
                    metadata={"row_type": "header"},
                    cells=header_cells,
                )
            )

        for row_idx, row in enumerate(rows, start=1):
            if not isinstance(row, list):
                continue
            cells: list[TableCellPayload] = []
            for col_idx, value in enumerate(row):
                raw_text = str(value) if value is not None else ""
                column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                cells.append(
                    TableCellPayload(
                        row_index=row_idx,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=raw_text,
                        normalized_value=TableDetector._normalize_cell_value(raw_text),
                        bbox=bbox,
                        confidence=None,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_idx,
                    page_number=page_number,
                    bbox=bbox,
                    raw_text=" | ".join(str(c.raw_text) for c in cells),
                    metadata={"row_type": "data"},
                    cells=cells,
                )
            )

        merged_meta: dict[str, Any] = dict(source_metadata or {})
        base_detected_via = str(merged_meta.get("detected_via") or "").strip() or "vlm"
        if "vlm" not in base_detected_via.lower():
            merged_meta["detected_via"] = f"{base_detected_via}+vlm"
        merged_meta["vlm_model"] = self.table_vlm_model
        try:
            existing_conf = float(merged_meta.get("structure_confidence") or 0.0)
        except (TypeError, ValueError):
            existing_conf = 0.0
        merged_meta["structure_confidence"] = max(0.0, min(1.0, max(existing_conf, 0.9)))

        return TablePayload(
            order_index=order_index,
            title=title or f"Table {order_index}",
            section_heading=section_heading or "",
            page_number=page_number,
            bbox=bbox,
            column_schema=column_schema,
            data_dictionary={},
            metadata=merged_meta,
            rows=table_rows,
        )

    def _extract_from_link(self, url: str) -> ExtractionResult:
        """
        Fetch link via scrape_document_source and return plain text extraction.
        (Keeps signature consistent with _extract_upload() caller.)
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
        Sanitize HTML keeping only allowed semantic tags
        Removes all attributes except href for links
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
        """Detect content encoding from bytes"""
        # Try UTF-8 first (most common)
        try:
            content_bytes.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass
        
        # Try common encodings
        for encoding in ["latin-1", "iso-8859-1", "windows-1252"]:
            try:
                content_bytes.decode(encoding)
                return encoding
            except UnicodeDecodeError:
                continue
        
        # Fallback
        return "utf-8"

    # ------------------------------------------------------------------
    # Persistence

    def _persist_extraction(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> None:
        normalized = self._normalize_text(extraction.text)
        if not normalized:
            raise KnowledgeIngestionError("Extracted document is empty.")

        previous_chunk_count = int(getattr(upload, "chunk_count", 0) or 0)
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
        format_hint = (extraction.format_hint or "").lower()
        if format_hint in {"pdf", "application/pdf"} and entity_payloads:
            logger.info(
                "pdf.entities.disabled upload=%s business=%s entities=%s",
                upload.id,
                upload.business_profile_id,
                len(entity_payloads),
            )
            entity_payloads = []
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
            table_profile = self._build_table_profile(extraction.tables or ())
            if table_profile:
                ingestion_metadata["table_profile"] = table_profile
            else:
                ingestion_metadata.pop("table_profile", None)
            KnowledgeUploadText.objects.update_or_create(upload=upload, defaults=defaults)
            chunk_count, missing_chunk_ids, chunk_objects = self._build_chunks(
                upload,
                normalized,
                entities=entity_payloads,
                ingestion_metadata=ingestion_metadata,
                pages=extraction.pages,
                format_hint=extraction.format_hint,
                shadow_ingestion=feature_state.rag_shadow_ingestion,
            )
            quality_report = self._build_quality_report(
                upload=upload,
                extraction=extraction,
                chunk_count=chunk_count,
                chunk_objects=chunk_objects,
                structured_summary=structured_summary,
            )
            if quality_report:
                ingestion_metadata["quality_report"] = quality_report
            else:
                ingestion_metadata.pop("quality_report", None)
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
                # Ensure old entity/alias records are cleared when we intentionally skip entities
                # (e.g., PDFs) or when extraction no longer yields any entities.
                existing_entities = KnowledgeEntity.objects.filter(upload=upload)
                removed = existing_entities.count()
                if removed:
                    existing_entities.delete()
                    self._invalidate_alias_cache(upload.business_profile_id)
                    logger.info(
                        "json.entities.cleared upload=%s business=%s entities=%s",
                        upload.id,
                        upload.business_profile_id,
                        removed,
                    )
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
            self._schedule_azure_search_index_update(
                upload=upload,
                format_hint=extraction.format_hint,
                now=now,
                previous_chunk_count=previous_chunk_count,
                chunk_objects=chunk_objects,
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

    def _schedule_azure_search_index_update(
        self,
        *,
        upload: KnowledgeUpload,
        format_hint: str | None,
        now: timezone.datetime,
        previous_chunk_count: int,
        chunk_objects: Sequence[KnowledgeUploadChunk],
    ) -> None:
        """
        Index freshly ingested chunks into Azure AI Search (P2) after DB commit.

        This is best-effort: ingestion should succeed even if the external index
        is temporarily unavailable. When Azure search is the active retrieval
        backend, failures are recorded in upload.ingestion_metadata for visibility.
        """

        def _on_commit() -> None:
            try:
                from apps.rag.azure_ai_search import (
                    AzureAISearchConfig,
                    delete_upload,
                    upsert_upload_chunks,
                )
            except Exception:
                return

            config = AzureAISearchConfig.from_settings()
            if not config:
                return

            business_id = getattr(upload, "business_profile_id", None)
            if not business_id:
                return

            started = time.perf_counter()
            status = "ok"
            error = ""
            try:
                with tenant_context(business_id):
                    collection_ids = list(upload.collections.values_list("id", flat=True))
                    title = (upload.display_name or upload.source_name or upload.external_reference or str(upload.id)).strip()
                    chunk_payloads = [
                        {
                            "chunk_id": chunk.id,
                            "chunk_index": chunk.chunk_index,
                            "content": chunk.content,
                            "embedding": chunk.embedding,
                            "metadata": chunk.metadata,
                        }
                        for chunk in chunk_objects
                    ]
                if previous_chunk_count:
                    delete_upload(config=config, upload_id=upload.id, chunk_count=previous_chunk_count)
                upsert_upload_chunks(
                    config=config,
                    business_id=uuid.UUID(str(business_id)),
                    upload_id=upload.id,
                    title=title,
                    format_hint=format_hint,
                    updated_at=now,
                    collection_ids=collection_ids,
                    chunks=chunk_payloads,
                )
            except Exception as exc:  # pragma: no cover - external dependency
                status = "failed"
                error = str(exc)[:300]
                logger.warning(
                    "azure_search.index_failed business=%s upload=%s error=%s",
                    business_id,
                    upload.id,
                    error,
                )
            finally:
                duration_ms = int((time.perf_counter() - started) * 1000.0)
                try:
                    from apps.rag.ai_orchestrator import KnowledgeSearchService

                    KnowledgeSearchService.invalidate_result_cache(uuid.UUID(str(business_id)))
                except Exception:
                    pass
                try:
                    with tenant_context(business_id):
                        refreshed = KnowledgeUpload.objects.filter(id=upload.id).values("ingestion_metadata").first()
                        meta = dict((refreshed or {}).get("ingestion_metadata") or {})
                        meta["azure_search"] = {
                            "status": status,
                            "index_name": config.index_name,
                            "chunk_count": int(getattr(upload, "chunk_count", 0) or 0),
                            "duration_ms": duration_ms,
                            "indexed_at": now.isoformat(),
                            "previous_chunk_count": int(previous_chunk_count),
                            "error": error,
                        }
                        KnowledgeUpload.objects.filter(id=upload.id).update(ingestion_metadata=meta)
                except Exception:
                    pass

        try:
            transaction.on_commit(_on_commit)
        except Exception:  # pragma: no cover - defensive
            return

    def _try_update_azure_search_embeddings(
        self,
        *,
        upload: KnowledgeUpload,
        chunks: Sequence[KnowledgeUploadChunk],
    ) -> None:
        """
        Best-effort: when embeddings are generated asynchronously, update the Azure
        index vectors so hybrid search quality remains stable.
        """

        try:
            from apps.rag.azure_ai_search import AzureAISearchConfig, update_chunk_embeddings
        except Exception:
            return

        config = AzureAISearchConfig.from_settings()
        if not config:
            return

        business_id = getattr(upload, "business_profile_id", None)
        if not business_id:
            return

        payloads = [
            {
                "chunk_id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "embedding": chunk.embedding,
            }
            for chunk in chunks
            if chunk.embedding is not None
        ]
        if not payloads:
            return
        try:
            update_chunk_embeddings(config=config, upload_id=upload.id, chunks=payloads)
        except Exception as exc:  # pragma: no cover - external dependency
            logger.warning(
                "azure_search.embedding_update_failed business=%s upload=%s error=%s",
                business_id,
                upload.id,
                str(exc)[:250],
            )

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

    def _build_chunks(
        self,
        upload: KnowledgeUpload,
        content: str,
        *,
        entities: Sequence[Mapping[str, Any]] | None = None,
        ingestion_metadata: Mapping[str, Any] | None = None,
        pages: Sequence[PageLayout] | None = None,
        format_hint: str | None = None,
        shadow_ingestion: bool = False,
    ) -> tuple[int, list[str], list[KnowledgeUploadChunk]]:
        """
        Build semantic chunks from either structured entities or sliding windows of text/tables.
        """
        from apps.accounts.models import KnowledgeUploadTable  # local import to avoid cycles

        entity_payloads = list(entities or [])
        feature_flags: Mapping[str, Any] = {}
        if isinstance(ingestion_metadata, Mapping):
            raw_flags = ingestion_metadata.get("feature_flags")
            if isinstance(raw_flags, Mapping):
                feature_flags = raw_flags
        alias_hygiene = bool(feature_flags.get("rag_alias_hygiene"))
        quality_filter_enabled = bool(feature_flags.get("rag_chunk_quality_filter"))
        dedupe_enabled = bool(feature_flags.get("rag_chunk_dedupe"))
        used_page_blocks = False
        if entity_payloads:
            segment_payloads = self._build_entity_segment_payloads(entity_payloads, alias_hygiene=alias_hygiene)
        else:
            segment_payloads = []
            if pages:
                page_segments = self._build_text_segments_from_blocks(pages, alias_hygiene=alias_hygiene)
                if page_segments:
                    segment_payloads.extend(page_segments)
                    used_page_blocks = True
            if not segment_payloads:
                text_segments = self._chunk_text(content)
                for segment in text_segments:
                    if not segment:
                        continue
                    augmented, aliases = self._inject_identifiers_into_text(segment, alias_hygiene=alias_hygiene)
                    metadata = {
                        "strategy": "sliding_window",
                        "index_type": "text",
                        "content_source": "flat_text",
                        "region_role": "text",
                    }
                    if aliases:
                        metadata.update(self._alias_metadata(aliases))
                    segment_payloads.append({"text": augmented, "metadata": metadata})

            table_segment_payloads: list[dict[str, Any]] = []
            privacy_rules = self._table_privacy_rules(upload)
            schema_chunking = self.table_schema_chunking
            try:
                tables = (
                    KnowledgeUploadTable.objects.filter(upload=upload)
                    .order_by("order_index")
                    .prefetch_related("rows__cells")
                )
                for t in tables:
                    table_metadata = t.metadata if isinstance(t.metadata, dict) else {}
                    quality_score = table_metadata.get("quality_score")
                    is_decorative = table_metadata.get("is_decorative")
                    quality_signals = table_metadata.get("quality_signals")
                    strong_noise_signal = False
                    if isinstance(quality_signals, dict):
                        strong_noise_signal = bool(
                            quality_signals.get("card_mockup") or quality_signals.get("spaced_characters")
                        )
                    if is_decorative is True and strong_noise_signal:
                        logger.info(
                            "table.preview.skip_decorative upload=%s table=%s score=%s signals=%s",
                            upload.id,
                            getattr(t, "id", None),
                            quality_score,
                            list(quality_signals.keys()) if isinstance(quality_signals, dict) else None,
                        )
                        continue
                    if isinstance(quality_score, (int, float)) and quality_score <= 0.2:
                        logger.info(
                            "table.preview.skip_low_quality upload=%s table=%s score=%s",
                            upload.id,
                            getattr(t, "id", None),
                            quality_score,
                        )
                        continue
                    raw_schema = list(map(str, (t.column_schema or [])))
                    header_labels = self._table_header_labels_for_model(t, raw_schema)
                    column_map, hidden_columns = self._table_column_map_for_model(
                        header_labels, raw_schema, privacy_rules
                    )
                    if not column_map:
                        continue
                    title = t.title or f"Table {t.order_index}"
                    base_metadata: dict[str, Any] = {
                        "strategy": "table_schema",
                        "is_table_chunk": True,
                        "table_title": title,
                        "table_id": str(t.id),
                        "table_order_index": t.order_index,
                        "table_page_number": t.page.page_number if t.page else None,  # FIXED: t.page_number doesn't exist
                        "index_type": "table",
                        "region_role": "table",
                        "visibility": getattr(upload, "visibility", KnowledgeVisibility.PRIVATE),
                    }
                    if self.ocr_normalization_enabled:
                        base_metadata["ocr_normalized"] = True
                        base_metadata["ocr_normalization_version"] = OCR_NORMALIZATION_VERSION
                    if hidden_columns:
                        base_metadata["restricted_columns"] = hidden_columns[:8]
                    table_metadata = t.metadata if isinstance(t.metadata, dict) else {}
                    for key in ("entity_type", "entity_name", "entity_business"):
                        if table_metadata.get(key):
                            base_metadata[key] = table_metadata[key]
                    if "quality_score" in table_metadata:
                        base_metadata["table_quality_score"] = table_metadata["quality_score"]
                    if "is_decorative" in table_metadata:
                        base_metadata["table_is_decorative"] = table_metadata["is_decorative"]
                    if "quality_signals" in table_metadata:
                        base_metadata["table_quality_signals"] = table_metadata["quality_signals"]
                    if table_metadata.get("page_anchor"):
                        base_metadata["page_anchor"] = table_metadata["page_anchor"]

                    if schema_chunking:
                        parent_text, truncated = self._table_parent_markdown_from_model(
                            table=t,
                            column_map=column_map,
                            raw_schema=raw_schema,
                            privacy_rules=privacy_rules,
                            max_rows=self.table_parent_max_rows,
                            max_chars=self.table_parent_max_chars,
                        )
                        if parent_text:
                            parent_meta = dict(base_metadata)
                            parent_meta.update(
                                {
                                    "content_source": "table_parent",
                                    "table_chunk_role": "parent",
                                    "is_table_preview": True,
                                    "table_parent_truncated": truncated,
                                }
                            )
                            table_segment_payloads.append({"text": parent_text, "metadata": parent_meta})
                        row_payloads = self._table_row_chunk_payloads(
                            table=t,
                            column_map=column_map,
                            raw_schema=raw_schema,
                            privacy_rules=privacy_rules,
                            base_metadata=base_metadata,
                            max_rows=self.table_child_max_rows,
                        )
                        table_segment_payloads.extend(row_payloads)
                    else:
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

                            legacy_meta = dict(base_metadata)
                            legacy_meta.update(
                                {
                                    "content_source": "table_preview",
                                    "table_chunk_role": "preview",
                                    "is_table_preview": True,
                                }
                            )
                            alias_list = legacy_meta.get("aliases") or []
                            for block in blocks:
                                if not block:
                                    continue
                                block_text = self._append_identifier_line(block, alias_list) if alias_list else block
                                table_segment_payloads.append({"text": block_text, "metadata": dict(legacy_meta)})
            except Exception:
                table_segment_payloads = []

            segment_payloads.extend(table_segment_payloads)

        dataset_card = build_dataset_card_segment_payload(upload=upload, ingestion_metadata=ingestion_metadata)
        if dataset_card:
            segment_payloads.append(dataset_card)

        quality_stats = {
            "evaluated": 0,
            "short_tokens": 0,
            "heading_only": 0,
            "low_unique_ratio": 0,
            "low_quality": 0,
            "filtered": 0,
            "deduped": 0,
        }
        scored_payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metrics = self._chunk_quality_metrics(text)
            metadata.update(metrics)
            payload["metadata"] = metadata
            if self._segment_is_text(metadata):
                quality_stats["evaluated"] += 1
                if metrics["chunk_quality_tokens"] < self.chunk_quality_min_tokens:
                    quality_stats["short_tokens"] += 1
                if metrics["chunk_heading_only"]:
                    quality_stats["heading_only"] += 1
                if metrics["chunk_quality_unique_ratio"] < self.chunk_quality_min_unique_ratio:
                    quality_stats["low_unique_ratio"] += 1
                if metrics["chunk_quality_score"] < self.chunk_quality_low_score:
                    quality_stats["low_quality"] += 1
            scored_payloads.append(payload)

        segment_payloads = scored_payloads

        if quality_filter_enabled and segment_payloads:
            filtered_payloads: list[dict[str, Any]] = []
            for payload in segment_payloads:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if self._segment_is_text(metadata) and not metadata.get("is_dataset_card"):
                    if self._is_low_quality_text_chunk(metadata):
                        quality_stats["filtered"] += 1
                        continue
                filtered_payloads.append(payload)
            if not filtered_payloads:
                filtered_payloads = segment_payloads[:1]
            segment_payloads = filtered_payloads

        if dedupe_enabled and segment_payloads:
            deduped_payloads: list[dict[str, Any]] = []
            seen_fingerprints: set[str] = set()
            for payload in segment_payloads:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if self._segment_is_text(metadata) and not metadata.get("is_dataset_card"):
                    fingerprint = self._chunk_fingerprint(str(payload.get("text") or ""))
                    if fingerprint and fingerprint in seen_fingerprints:
                        quality_stats["deduped"] += 1
                        continue
                    if fingerprint:
                        seen_fingerprints.add(fingerprint)
                deduped_payloads.append(payload)
            if not deduped_payloads:
                deduped_payloads = segment_payloads[:1]
            segment_payloads = deduped_payloads

        if isinstance(ingestion_metadata, dict):
            ingestion_metadata["chunk_quality_stats"] = {
                **quality_stats,
                "min_tokens": self.chunk_quality_min_tokens,
                "min_unique_ratio": self.chunk_quality_min_unique_ratio,
                "low_score_threshold": self.chunk_quality_low_score,
            }

        KnowledgeUploadChunk.objects.filter(upload=upload).delete()
        if shadow_ingestion:
            KnowledgeUploadShadowChunk.objects.filter(upload=upload).delete()
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
                "strategy": "json_entity"
                if entity_payloads
                else ("page_blocks_plus_tables" if used_page_blocks else "sliding_window_plus_tables"),
            }
            extra_meta = payload.get("metadata") or {}
            if isinstance(extra_meta, dict):
                chunk_metadata.update(extra_meta)
            chunk_metadata.setdefault("is_table_chunk", False)
            if entity_payloads:
                chunk_metadata.setdefault("index_type", "entity")
            else:
                chunk_metadata.setdefault("index_type", "text")
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

        shadow_objects: list[KnowledgeUploadShadowChunk] = []
        if shadow_ingestion:
            for chunk in chunk_objects:
                shadow_meta = dict(chunk.metadata or {})
                shadow_meta["shadow_index"] = True
                shadow_meta.setdefault("shadow_source", "baseline")
                shadow_objects.append(
                    KnowledgeUploadShadowChunk(
                        upload=chunk.upload,
                        business_profile=chunk.business_profile,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        embedding=chunk.embedding,
                        metadata=shadow_meta,
                    )
                )

        KnowledgeUploadChunk.objects.bulk_create(chunk_objects, batch_size=100)
        logger.info(
            "chunks.persisted upload=%s count=%s missing_embeddings=%s",
            upload.id,
            len(chunk_objects),
            len(missing_chunk_ids),
        )
        if shadow_objects:
            shadow_missing = sum(1 for chunk in shadow_objects if chunk.embedding is None)
            KnowledgeUploadShadowChunk.objects.bulk_create(shadow_objects, batch_size=100)
            logger.info(
                "shadow.chunks.persisted upload=%s count=%s missing_embeddings=%s",
                upload.id,
                len(shadow_objects),
                shadow_missing,
            )
        if missing_chunk_ids:
            self._schedule_embedding_jobs(upload, missing_chunk_ids)
        return len(chunk_objects), missing_chunk_ids, chunk_objects

    @staticmethod
    def _tokenize_for_quality(text: str) -> list[str]:
        if not text:
            return []
        return re.findall(r"[a-z0-9]+", text.lower())

    @staticmethod
    def _is_heading_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        letters = [ch for ch in stripped if ch.isalpha()]
        if not letters:
            return False
        upper_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
        if upper_ratio >= 0.7:
            return True
        words = [word for word in re.split(r"\s+", stripped) if word]
        if not words:
            return False
        starts = [word[0] for word in words if word[0].isalpha()]
        if not starts:
            return False
        title_ratio = sum(1 for ch in starts if ch.isupper()) / len(starts)
        return title_ratio >= 0.8

    def _is_heading_only_chunk(self, text: str, token_count: int) -> bool:
        if token_count <= 0:
            return False
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        if len(lines) > self.chunk_quality_heading_max_lines:
            return False
        if token_count > self.chunk_quality_heading_max_tokens:
            return False
        return all(self._is_heading_line(line) for line in lines)

    def _chunk_quality_metrics(self, text: str) -> dict[str, Any]:
        tokens = self._tokenize_for_quality(text)
        token_count = len(tokens)
        unique_ratio = round(len(set(tokens)) / token_count, 3) if token_count else 0.0
        heading_only = self._is_heading_only_chunk(text, token_count)
        flags: list[str] = []
        if token_count < self.chunk_quality_min_tokens:
            flags.append("short_tokens")
        if unique_ratio < self.chunk_quality_min_unique_ratio:
            flags.append("low_unique_ratio")
        if heading_only:
            flags.append("heading_only")
        token_score = min(1.0, token_count / self.chunk_quality_min_tokens) if self.chunk_quality_min_tokens else 1.0
        unique_score = (
            min(1.0, unique_ratio / self.chunk_quality_min_unique_ratio)
            if self.chunk_quality_min_unique_ratio
            else 1.0
        )
        heading_score = 0.0 if heading_only else 1.0
        score = (token_score * 0.45) + (unique_score * 0.45) + (heading_score * 0.10)
        score = round(max(0.0, min(1.0, score)), 3)
        return {
            "chunk_quality_score": score,
            "chunk_quality_tokens": token_count,
            "chunk_quality_unique_ratio": unique_ratio,
            "chunk_heading_only": heading_only,
            "chunk_quality_flags": flags,
        }

    def _is_low_quality_text_chunk(self, metadata: Mapping[str, Any]) -> bool:
        if not metadata:
            return False
        try:
            token_count = int(metadata.get("chunk_quality_tokens") or 0)
        except (TypeError, ValueError):
            token_count = 0
        try:
            unique_ratio = float(metadata.get("chunk_quality_unique_ratio") or 0.0)
        except (TypeError, ValueError):
            unique_ratio = 0.0
        try:
            score = float(metadata.get("chunk_quality_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        heading_only = bool(metadata.get("chunk_heading_only"))
        if heading_only:
            return True
        if token_count < self.chunk_quality_min_tokens:
            return True
        if unique_ratio < self.chunk_quality_min_unique_ratio:
            return True
        return score < self.chunk_quality_low_score

    @staticmethod
    def _segment_is_text(metadata: Mapping[str, Any]) -> bool:
        if not metadata:
            return False
        if metadata.get("is_table_chunk"):
            return False
        index_type = metadata.get("index_type")
        if index_type in {"table", "entity"}:
            return False
        return True

    @staticmethod
    def _chunk_fingerprint(text: str) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _build_entity_segment_payloads(
        self,
        entities: Sequence[Mapping[str, Any]],
        *,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for index, entity in enumerate(entities):
            attributes = entity.get("attributes") or {}
            columns = entity.get("columns") or []
            alias_list = list(entity.get("aliases") or [])
            entity_type = entity.get("entity_type") or "record"
            entity_name = entity.get("entity_name") or f"{entity_type.title()} {index + 1}"
            lines = [f"{entity_type.title()}: {entity_name}"]
            for column in columns[:16]:
                value = attributes.get(column)
                if value:
                    lines.append(f"- {column}: {value}")
            text = "\n".join(lines).strip()
            text, inline_aliases = self._inject_identifiers_into_text(text, alias_hygiene=alias_hygiene)
            combined_aliases = alias_list[:]
            for alias in inline_aliases:
                if alias and alias not in combined_aliases:
                    combined_aliases.append(alias)
            metadata: dict[str, Any] = {
                "strategy": entity.get("chunk_strategy") or "json_entity",
                "index_type": "entity",
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
        KnowledgeEntity.objects.filter(upload=upload).delete()
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
                chunk_id=chunk.id if chunk else None,
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
        if not chunk_ids:
            return
        batch_size = self.embedding_job_payload_size
        business = upload.business_profile
        for idx in range(0, len(chunk_ids), batch_size):
            batch = chunk_ids[idx : idx + batch_size]
            job = KnowledgeIngestionJob.objects.create(
                business_profile=business,
                upload=upload,
                job_type=KnowledgeIngestionJobType.EMBED,
                status=KnowledgeIngestionJobStatus.QUEUED,
                max_attempts=self.embed_job_max_attempts,
                payload={"chunk_ids": batch},
            )
            logger.info("Queued embedding job upload=%s job=%s chunks=%s", upload.id, job.id, len(batch))
        backlog = self._embedding_backlog_count(business.id)
        if self.embedding_backlog_threshold and backlog >= self.embedding_backlog_threshold:
            logger.warning(
                "embedding.backlog threshold exceeded business=%s backlog=%s threshold=%s",
                business.id,
                backlog,
                self.embedding_backlog_threshold,
            )

    def _update_upload_embedding_metadata(self, upload: KnowledgeUpload, *, processed_ids: Sequence[str]) -> None:
        metadata = dict(upload.ingestion_metadata or {})
        pending = metadata.get("pending_embedding_chunks")
        if isinstance(pending, list):
            pending_set = {str(value) for value in pending}
            for chunk_id in processed_ids:
                pending_set.discard(str(chunk_id))
            if pending_set:
                metadata["pending_embedding_chunks"] = list(pending_set)[:50]
            else:
                metadata.pop("pending_embedding_chunks", None)
        remaining = KnowledgeUploadChunk.objects.filter(upload=upload, embedding__isnull=True).count()
        if remaining:
            metadata["pending_embedding_chunk_count"] = remaining
        else:
            metadata.pop("pending_embedding_chunk_count", None)
        upload.ingestion_metadata = metadata
        upload.save(update_fields=["ingestion_metadata", "updated_at"])

    def _invalidate_alias_cache(self, business_id: uuid.UUID) -> None:
        try:
            from apps.rag.ai_orchestrator import KnowledgeSearchService
        except ImportError:  # pragma: no cover - defensive import
            return
        KnowledgeSearchService.invalidate_alias_cache(business_id)
        KnowledgeSearchService.invalidate_query_cache(business_id)
        KnowledgeSearchService.invalidate_result_cache(business_id)

    def _embedding_backlog_count(self, business_id: uuid.UUID) -> int:
        return KnowledgeIngestionJob.objects.filter(
            business_profile_id=business_id,
            job_type=KnowledgeIngestionJobType.EMBED,
            status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.RUNNING),
        ).count()

    def _release_deferred_jobs(self, business_id: uuid.UUID) -> None:
        if not self.ingest_concurrency_limit:
            return
        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        active = KnowledgeIngestionJob.objects.filter(
            business_profile_id=business_id,
            job_type=KnowledgeIngestionJobType.INGEST,
        ).filter(
            Q(status=KnowledgeIngestionJobStatus.RUNNING)
            | (Q(status=KnowledgeIngestionJobStatus.QUEUED) & eligible)
        ).count()
        available = self.ingest_concurrency_limit - active
        if available <= 0:
            return
        deferred = list(
            KnowledgeIngestionJob.objects.filter(
                business_profile_id=business_id,
                status=KnowledgeIngestionJobStatus.DEFERRED,
                job_type=KnowledgeIngestionJobType.INGEST,
            )
            .order_by("created_at")[:available]
        )
        if not deferred:
            return
        ids = [job.id for job in deferred]
        KnowledgeIngestionJob.objects.filter(id__in=ids).update(status=KnowledgeIngestionJobStatus.QUEUED)
        logger.info("Promoted %s deferred ingestion jobs for business=%s", len(ids), business_id)

    def _normalize_embedding(self, vector: Sequence[float] | None) -> list[float] | None:
        if not vector:
            return None
        try:
            values = [float(v) for v in vector]
        except (TypeError, ValueError):
            return None
        expected = getattr(settings, "EMBED_DIM", None)
        if expected:
            if len(values) > expected:
                logger.warning("Embedding dimension mismatch: got %s, expected %s. Trimming.", len(values), expected)
                values = values[:expected]
            elif len(values) < expected:
                logger.warning("Embedding dimension mismatch: got %s, expected %s. Padding.", len(values), expected)
                values = values + [0.0] * (expected - len(values))
        return values

    def _get_fallback_embedding_service(self) -> LocalEmbeddingService | None:
        if isinstance(self.embedding_service, LocalEmbeddingService):
            return self.embedding_service
        if self._fallback_embedding_service:
            return self._fallback_embedding_service
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

    def _assess_table_quality(self, table_payload) -> dict[str, Any]:
        """
        Assess table quality to detect decorative/garbage tables.
        
        Returns dict with:
        - quality_score: float 0.0-1.0 (0=garbage, 1=high quality)
        - is_decorative: bool (True if likely decorative)
        - signals: dict of detected quality signals
        """
        signals: dict[str, Any] = {}
        penalties = 0
        max_penalties = 14
        
        # Get table data
        column_schema = table_payload.column_schema or []
        rows = table_payload.rows or []
        page_number = table_payload.page_number or 0
        order_index = table_payload.order_index or 0
        
        # Heuristic 1: Nonsense column names
        nonsense_patterns = [
            r'^column_\d+$',  # Generic column_1, column_2
            r'^col\d+$',      # col1, col2
            r'^\d+$',         # Just numbers
            r'^[a-z]$',       # Single letters
        ]
        nonsense_count = 0
        for col in column_schema:
            col_str = str(col).strip().lower()
            for pattern in nonsense_patterns:
                if re.match(pattern, col_str):
                    nonsense_count += 1
                    break
        
        if nonsense_count >= len(column_schema) * 0.75 and len(column_schema) > 0:
            signals['nonsense_columns'] = True
            penalties += 3
        
        # Heuristic 2: Spaced characters detection (e.g., "W H I T E")
        spaced_char_count = 0
        total_cells = 0
        
        for row in rows[:10]:  # Check first 10 rows
            for cell in (row.cells or []):
                raw_text = str(cell.raw_text or "").strip()
                total_cells += 1
                
                # Check for spaced single characters: "A B C D"
                if re.match(r'^([A-Z]\s){2,}[A-Z]$', raw_text) or re.match(r'^(\w\s){2,}\w$', raw_text):
                    spaced_char_count += 1
                    signals.setdefault('spaced_char_examples', []).append(raw_text[:50])
        
        if total_cells > 0 and spaced_char_count / total_cells >= 0.3:
            signals['spaced_characters'] = True
            penalties += 4
        
        # Heuristic 3: Row/column consistency
        row_lengths: list[int] = []
        non_empty_cells = 0
        expected_columns = len(column_schema)
        for row in rows:
            cell_list = list(row.cells or [])
            row_lengths.append(len(cell_list))
            for cell in cell_list:
                if str(cell.raw_text or "").strip():
                    non_empty_cells += 1
        if not expected_columns and row_lengths:
            expected_columns = max(row_lengths)
        if rows and expected_columns:
            matching = sum(1 for length in row_lengths if length == expected_columns)
            row_consistency = matching / max(1, len(row_lengths))
            fill_ratio = non_empty_cells / max(1, expected_columns * len(rows))
            signals["row_consistency"] = round(row_consistency, 2)
            signals["cell_fill_ratio"] = round(fill_ratio, 2)
            if row_consistency < 0.6:
                signals["row_misalignment"] = True
                penalties += 2
            if fill_ratio < 0.4:
                signals["sparse_table"] = True
                penalties += 1

        # Heuristic 4: Header confidence
        header_cells = None
        for row in rows:
            if (row.metadata or {}).get("row_type") == "header":
                header_cells = [str(cell.raw_text or "") for cell in (row.cells or [])]
                break
        if header_cells:
            joined = " ".join(header_cells).strip()
            alnum = [c for c in joined if c.isalnum()]
            digits = sum(1 for c in joined if c.isdigit())
            letters = sum(1 for c in joined if c.isalpha())
            non_numeric = sum(1 for cell in header_cells if not re.search(r"\d", cell or ""))
            non_numeric_ratio = non_numeric / max(1, len(header_cells))
            digit_ratio = digits / max(1, len(alnum))
            alpha_ratio = letters / max(1, len(alnum))
            length_ratio = sum(1 for cell in header_cells if len(cell.strip()) >= 3) / max(1, len(header_cells))
            header_confidence = 0.0
            if non_numeric_ratio >= 0.6:
                header_confidence += 0.4
            if alpha_ratio >= 0.4:
                header_confidence += 0.3
            if digit_ratio < 0.3:
                header_confidence += 0.2
            if length_ratio >= 0.5:
                header_confidence += 0.1
            signals["header_confidence"] = round(min(header_confidence, 1.0), 2)
            if header_confidence < 0.3:
                penalties += 1
        else:
            signals["header_confidence"] = 0.0
            penalties += 1

        # Heuristic 5: Repeating patterns
        cell_values: list[str] = []
        for row in rows[:5]:
            for cell in (row.cells or []):
                raw_text = str(cell.raw_text or "").strip().lower()
                if raw_text:
                    cell_values.append(raw_text)
        
        if len(cell_values) >= 3:
            unique_values = len(set(cell_values))
            if unique_values / len(cell_values) < 0.3:  # Less than 30% unique
                signals['high_repetition'] = True
                signals['unique_ratio'] = round(unique_values / len(cell_values), 2)
                penalties += 2
        
        # Heuristic 6: Too few data rows
        data_row_count = len([r for r in rows if not (r.metadata or {}).get('row_type') == 'header'])
        if data_row_count < 2:
            signals['insufficient_rows'] = True
            penalties += 2
        
        # Heuristic 7: Header/footer position (first/last page)
        if page_number == 1 and order_index == 0:
            # First table on first page = might be header decoration
            signals['first_page_first_table'] = True
            penalties += 1
        
        # Heuristic 8: Card-like patterns (e.g., credit card mockups)
        card_keywords = ['valid', 'thru', 'expires', 'cvv', 'card number', 'cardholder']
        keyword_matches = 0
        card_number_hits = 0
        
        for row in rows[:5]:
            for cell in (row.cells or []):
                raw_text = str(cell.raw_text or "").strip().lower()
                if "valid" in raw_text and "thru" in raw_text:
                    signals["valid_thru"] = True
                if re.search(r"\b(?:\d{4}[\s-]?){3}\d{4}\b", raw_text):
                    card_number_hits += 1
                for keyword in card_keywords:
                    if keyword in raw_text:
                        keyword_matches += 1
                        signals.setdefault('card_keywords', []).append(keyword)
        
        if card_number_hits:
            signals["card_number_pattern"] = True
            penalties += 2
        if keyword_matches >= 3 and data_row_count <= 2:
            signals['card_mockup'] = True
            penalties += 3
        
        # Calculate quality score (0.0 = garbage, 1.0 = high quality)
        quality_score = max(0.0, 1.0 - (penalties / max_penalties))
        
        # Determine if decorative (threshold: quality < 0.5)
        is_decorative = quality_score < 0.5
        
        return {
            'quality_score': round(quality_score, 2),
            'is_decorative': is_decorative,
            'signals': signals,
            'penalties': penalties,
        }

    def _table_row_label_set(self, table: TablePayload) -> set[str]:
        labels: list[str] = []
        for row in table.rows:
            if (row.metadata or {}).get("row_type") == "header":
                continue
            first_cell = None
            for cell in row.cells:
                if cell.column_index == 0:
                    first_cell = cell
                    break
            if not first_cell and row.cells:
                first_cell = row.cells[0]
            raw = str(getattr(first_cell, "raw_text", "") or "").strip() if first_cell else str(row.raw_text or "")
            if not raw:
                continue
            normalized = self._normalize_ocr_text(raw).lower()
            normalized = re.sub(r"\s+", " ", normalized).strip()
            if len(normalized) < 3 or not re.search(r"[a-z]", normalized):
                continue
            labels.append(normalized)
            if len(labels) >= self.table_postprocess_row_limit:
                break
        return set(labels)

    def _build_table_profile(self, tables: Sequence[TablePayload]) -> dict[str, Any] | None:
        if not tables:
            return None
        column_limit = max(16, int(getattr(settings, "RAG_TABLE_PROFILE_COLUMN_LIMIT", 256)))
        token_limit = max(32, int(getattr(settings, "RAG_TABLE_PROFILE_ROW_LABEL_TOKEN_LIMIT", 800)))
        label_limit = max(8, int(getattr(settings, "RAG_TABLE_PROFILE_ROW_LABEL_SAMPLE_LIMIT", 60)))

        columns: set[str] = set()
        row_label_tokens: set[str] = set()
        row_label_samples: list[str] = []
        token_split = re.compile(r"[^\w]+", flags=re.UNICODE)

        for table in tables:
            schema = table.column_schema or []
            for col in schema:
                lowered = str(col or "").strip().lower()
                if lowered:
                    columns.add(lowered)
                    if len(columns) >= column_limit:
                        break
            if len(columns) >= column_limit:
                columns = set(sorted(columns)[:column_limit])

            labels = self._table_row_label_set(table)
            for label in labels:
                if label and len(row_label_samples) < label_limit:
                    row_label_samples.append(label)
                for token in token_split.split(label):
                    cleaned = token.strip().lower()
                    if not cleaned or cleaned.isdigit():
                        continue
                    row_label_tokens.add(cleaned)
                    if len(row_label_tokens) >= token_limit:
                        break
                if len(row_label_tokens) >= token_limit:
                    break

            if len(columns) >= column_limit and len(row_label_tokens) >= token_limit:
                break

        profile: dict[str, Any] = {
            "version": 1,
            "generated_at": timezone.now().isoformat(),
            "table_count": len(tables),
            "columns": sorted(columns)[:column_limit],
            "row_label_tokens": sorted(row_label_tokens)[:token_limit],
            "row_label_samples": row_label_samples[:label_limit],
        }
        return profile

    def _table_schema_is_generic(self, table: TablePayload) -> bool:
        if not table.column_schema:
            return True
        assessment = self._assess_table_quality(table)
        signals = assessment.get("signals") or {}
        if signals.get("nonsense_columns"):
            return True
        header_confidence = float(signals.get("header_confidence") or 0.0)
        if header_confidence < 0.3:
            return True
        return False

    def _apply_schema_override(
        self,
        table: TablePayload,
        schema: Sequence[str],
        *,
        inferred_from: int | None = None,
    ) -> TablePayload:
        normalized_schema = [str(col or "").strip() or f"column_{idx+1}" for idx, col in enumerate(schema)]
        new_rows: list[TableRowPayload] = []
        for row in table.rows:
            row_meta = dict(row.metadata or {})
            if row_meta.get("row_type") == "header":
                row_meta["row_type"] = "data"
                row_meta["header_inferred"] = True
            new_cells: list[TableCellPayload] = []
            for cell in row.cells:
                col_key = normalized_schema[cell.column_index] if cell.column_index < len(normalized_schema) else f"column_{cell.column_index+1}"
                new_cells.append(
                    TableCellPayload(
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                        column_key=col_key,
                        raw_text=cell.raw_text,
                        normalized_value=cell.normalized_value,
                        bbox=cell.bbox,
                        confidence=cell.confidence,
                        metadata=cell.metadata,
                    )
                )
            new_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row_meta,
                    cells=new_cells,
                )
            )
        table_meta = dict(table.metadata or {})
        table_meta["header_inferred"] = True
        if inferred_from is not None:
            table_meta["header_inferred_from"] = inferred_from
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=normalized_schema,
            data_dictionary=table.data_dictionary,
            metadata=table_meta,
            rows=new_rows,
        )

    def _postprocess_tables(
        self,
        tables: Sequence[TablePayload],
    ) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        if not tables:
            return [], [], {}
        grouped: dict[int | None, list[TablePayload]] = {}
        for table in tables:
            grouped.setdefault(table.page_number, []).append(table)
        issues: list[IssuePayload] = []
        meta = {"deduped_tables": 0, "header_inferred": 0}
        processed: list[TablePayload] = []

        def _jaccard(a: set[str], b: set[str]) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / max(1, len(a | b))

        for page_number, page_tables in grouped.items():
            page_tables = sorted(page_tables, key=lambda t: t.order_index)
            if self.table_dedupe_enabled:
                deduped: list[TablePayload] = []
                dedupe_labels: list[set[str]] = []
                dedupe_quality: list[float] = []
                for table in page_tables:
                    labels = self._table_row_label_set(table)
                    quality = float(self._assess_table_quality(table).get("quality_score") or 0.0)
                    merged = False
                    if labels:
                        for idx, existing in enumerate(deduped):
                            if len(existing.column_schema) != len(table.column_schema):
                                continue
                            overlap = _jaccard(labels, dedupe_labels[idx])
                            if overlap >= self.table_dedupe_min_overlap:
                                meta["deduped_tables"] += 1
                                if quality > dedupe_quality[idx]:
                                    deduped[idx] = table
                                    dedupe_labels[idx] = labels
                                    dedupe_quality[idx] = quality
                                issues.append(
                                    IssuePayload(
                                        code="table_duplicate_suppressed",
                                        severity=KnowledgeIssueSeverity.INFO.value,
                                        description="Duplicate table suppressed based on row-label overlap.",
                                        page_number=page_number,
                                        table_order_index=table.order_index,
                                        details={"overlap": round(overlap, 3)},
                                    )
                                )
                                merged = True
                                break
                    if not merged:
                        deduped.append(table)
                        dedupe_labels.append(labels)
                        dedupe_quality.append(quality)
                page_tables = deduped

            prev_schema: list[str] | None = None
            prev_labels: set[str] | None = None
            prev_order_index: int | None = None
            for table in page_tables:
                labels = self._table_row_label_set(table)
                is_generic = self._table_schema_is_generic(table)
                if (
                    self.table_header_propagation_enabled
                    and is_generic
                    and prev_schema
                    and labels
                    and prev_labels
                    and len(prev_schema) == len(table.column_schema)
                ):
                    overlap = _jaccard(labels, prev_labels)
                    if overlap >= self.table_header_propagation_min_overlap:
                        table = self._apply_schema_override(table, prev_schema, inferred_from=prev_order_index)
                        meta["header_inferred"] += 1
                        issues.append(
                            IssuePayload(
                                code="table_header_inferred",
                                severity=KnowledgeIssueSeverity.INFO.value,
                                description="Table headers inferred from adjacent table on same page.",
                                page_number=page_number,
                                table_order_index=table.order_index,
                                details={"overlap": round(overlap, 3), "source_table": prev_order_index},
                            )
                        )
                if not is_generic and labels:
                    prev_schema = list(table.column_schema)
                    prev_labels = labels
                    prev_order_index = table.order_index
                processed.append(table)

        return processed, issues, meta

    def _persist_structured_artifacts(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> dict[str, Any]:
        KnowledgeUploadPage.objects.filter(upload=upload).delete()
        KnowledgeUploadTable.objects.filter(upload=upload).delete()
        KnowledgeUploadIssue.objects.filter(upload=upload).delete()

        page_content_type_max = getattr(KnowledgeUploadPage._meta.get_field("content_type"), "max_length", 100) or 100
        block_section_heading_max = getattr(
            KnowledgeUploadPageBlock._meta.get_field("section_heading"),
            "max_length",
            255,
        ) or 255
        block_language_max = getattr(
            KnowledgeUploadPageBlock._meta.get_field("detected_language"),
            "max_length",
            32,
        ) or 32
        table_title_max = getattr(KnowledgeUploadTable._meta.get_field("title"), "max_length", 255) or 255
        table_section_heading_max = getattr(
            KnowledgeUploadTable._meta.get_field("section_heading"),
            "max_length",
            255,
        ) or 255
        issue_code_max = getattr(KnowledgeUploadIssue._meta.get_field("issue_code"), "max_length", 120) or 120
        cell_column_key_max = getattr(
            KnowledgeUploadTableCell._meta.get_field("column_key"),
            "max_length",
            160,
        ) or 160

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
                content_type=self._clamp_text(page_payload.content_type, page_content_type_max),
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
                        section_heading=self._clamp_text(block_payload.section_heading, block_section_heading_max),
                        heading_path=[
                            self._sanitize_text(item)
                            for item in (block_payload.heading_path or [])
                        ],
                        detected_language=self._clamp_text(block_payload.detected_language, block_language_max),
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
            # Assess table quality
            quality_assessment = self._assess_table_quality(table_payload)
            
            # Merge quality data into table metadata
            table_metadata = dict(table_payload.metadata or {})
            table_metadata['quality_score'] = quality_assessment['quality_score']
            table_metadata['is_decorative'] = quality_assessment['is_decorative']
            table_metadata['quality_signals'] = quality_assessment['signals']
            if table_payload.page_number:
                table_metadata["page_anchor"] = f"p{table_payload.page_number}-t{table_payload.order_index}"
            else:
                table_metadata["page_anchor"] = f"t{table_payload.order_index}"
            
            page_obj = page_lookup.get(table_payload.page_number or -1)
            table_obj = KnowledgeUploadTable.objects.create(
                upload=upload,
                page=page_obj,
                source_block=None,
                title=self._clamp_text(table_payload.title, table_title_max),
                section_heading=self._clamp_text(table_payload.section_heading, table_section_heading_max),
                order_index=table_payload.order_index,
                bbox=table_payload.bbox,
                column_schema=table_payload.column_schema,
                data_dictionary=table_payload.data_dictionary,
                metadata=table_metadata,  # Include quality metadata
            )
            table_lookup[(table_payload.order_index, table_payload.page_number)] = table_obj
            table_summaries.append(
                {
                    "order_index": table_payload.order_index,
                    "title": table_payload.title,
                    "page_number": table_payload.page_number,
                    "row_count": len(table_payload.rows),
                    "column_schema": table_payload.column_schema,
                    "quality_score": quality_assessment['quality_score'],  # NEW
                    "is_decorative": quality_assessment['is_decorative'],  # NEW
                }
            )
            for row_payload in table_payload.rows:
                row_text = self._table_cell_text(row_payload.raw_text)
                row_obj = KnowledgeUploadTableRow.objects.create(
                    table=table_obj,
                    row_index=row_payload.row_index,
                    page_number=row_payload.page_number,
                    bbox=row_payload.bbox,
                    raw_text=row_text,
                    metadata=row_payload.metadata,
                )
                row_lookup[(table_obj.id, row_payload.row_index)] = row_obj
                for cell_payload in row_payload.cells:
                    cell_text = self._table_cell_text(cell_payload.raw_text)
                    cell_obj = KnowledgeUploadTableCell.objects.create(
                        table=table_obj,
                        row=row_obj,
                        column_index=cell_payload.column_index,
                        column_key=self._clamp_text(cell_payload.column_key, cell_column_key_max),
                        raw_text=cell_text,
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
                    issue_code=self._clamp_text(issue.code, issue_code_max),
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

    def _build_text_segments_from_blocks(
        self,
        pages: Sequence[PageLayout],
        *,
        chunk_chars: int = 1200,
        overlap: int = 200,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        if not pages:
            return []
        chunk_chars = max(200, int(chunk_chars))
        overlap = max(0, min(int(overlap), chunk_chars // 2))
        skip_types = {
            KnowledgeBlockType.TABLE,
            KnowledgeBlockType.IMAGE,
            KnowledgeBlockType.FIGURE,
            KnowledgeBlockType.HEADER,
            KnowledgeBlockType.FOOTER,
            KnowledgeBlockType.OTHER,
        }
        anchor_limit = 12
        heading_limit = 6
        segments: list[dict[str, Any]] = []

        def _dedupe(values: Sequence[str], limit: int) -> list[str]:
            seen: set[str] = set()
            output: list[str] = []
            for value in values:
                if not value or value in seen:
                    continue
                seen.add(value)
                output.append(value)
                if limit and len(output) >= limit:
                    break
            return output

        for page in pages:
            block_units: list[dict[str, Any]] = []
            for block in page.blocks:
                if block.block_type in skip_types:
                    continue
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block_meta.get("is_decorative") or block_meta.get("region_role") == "decorative":
                    continue
                text = self._sanitize_text(block.text).strip()
                if not text:
                    continue
                anchor = block_meta.get("anchor") or f"p{page.page_number}-b{block.order_index}"
                heading = self._sanitize_text(block.section_heading).strip() if block.section_heading else ""
                block_units.append(
                    {
                        "text": text,
                        "page_number": page.page_number,
                        "anchor": anchor,
                        "section_heading": heading,
                    }
                )
            if not block_units:
                continue

            current_blocks: list[dict[str, Any]] = []
            current_len = 0

            def emit(blocks: Sequence[dict[str, Any]]) -> None:
                if not blocks:
                    return
                text = "\n\n".join(entry["text"] for entry in blocks).strip()
                if not text:
                    return
                text, aliases = self._inject_identifiers_into_text(text, alias_hygiene=alias_hygiene)
                anchors = _dedupe([entry.get("anchor") for entry in blocks if entry.get("anchor")], anchor_limit)
                headings = _dedupe(
                    [entry.get("section_heading") for entry in blocks if entry.get("section_heading")],
                    heading_limit,
                )
                metadata: dict[str, Any] = {
                    "strategy": "page_blocks",
                    "index_type": "text",
                    "content_source": "page_blocks",
                    "region_role": "text",
                    "page_numbers": [page.page_number],
                    "page_anchor": f"p{page.page_number}",
                }
                if aliases:
                    metadata.update(self._alias_metadata(aliases))
                if anchors:
                    metadata["block_anchors"] = anchors
                if headings:
                    metadata["section_headings"] = headings
                segments.append({"text": text, "metadata": metadata})

            for unit in block_units:
                block_text = unit["text"]
                if len(block_text) >= chunk_chars:
                    if current_blocks:
                        emit(current_blocks)
                        current_blocks = []
                        current_len = 0
                    for piece in self._chunk_text(block_text, chunk_chars=chunk_chars, overlap=overlap):
                        if not piece:
                            continue
                        piece, aliases = self._inject_identifiers_into_text(piece, alias_hygiene=alias_hygiene)
                        metadata = {
                            "strategy": "page_blocks",
                            "index_type": "text",
                            "content_source": "page_blocks",
                            "region_role": "text",
                            "page_numbers": [page.page_number],
                            "page_anchor": f"p{page.page_number}",
                            "block_anchors": [unit["anchor"]],
                        }
                        if aliases:
                            metadata.update(self._alias_metadata(aliases))
                        if unit.get("section_heading"):
                            metadata["section_headings"] = [unit["section_heading"]]
                        segments.append({"text": piece, "metadata": metadata})
                    continue

                additional = len(block_text) + (2 if current_blocks else 0)
                if current_blocks and current_len + additional > chunk_chars:
                    emit(current_blocks)
                    if overlap > 0:
                        carried: list[dict[str, Any]] = []
                        carried_len = 0
                        for prev in reversed(current_blocks):
                            prev_len = len(prev["text"]) + (2 if carried else 0)
                            carried.insert(0, prev)
                            carried_len += prev_len
                            if carried_len >= overlap:
                                break
                        current_blocks = carried
                        current_len = carried_len
                    else:
                        current_blocks = []
                        current_len = 0

                current_blocks.append(unit)
                current_len += additional

            if current_blocks:
                emit(current_blocks)

        return segments

    @staticmethod
    def _chunk_text(content: str, *, chunk_chars: int = 1200, overlap: int = 200) -> list[str]:
        """
        Boundary-aware chunker:
        - Prefers to end chunks on paragraph/line boundaries to avoid splitting table rows
        - If a chunk starts on a tab-delimited line, pull in up to 2 preceding lines to capture headers
        - Aligns the overlap start to token/line boundaries to avoid mid-word fragments (e.g., "ee", "pend")
        """
        text = (content or "").strip()
        if not text:
            return []

        segments: list[str] = []
        length = len(text)
        start = 0
        overlap = max(0, min(overlap, chunk_chars // 2))

        def _align_next_start(raw_start: int, *, min_progress: int) -> int:
            """
            Ensure the next chunk starts on a sane boundary so we don't create mid-word fragments
            when applying overlap (common in PDF-extracted tabular text).
            """
            if raw_start <= 0:
                return 0
            candidate = min(raw_start, length)
            if candidate <= min_progress:
                candidate = min_progress

            if candidate < length:
                # Prefer starting on a line boundary near the overlap start (helps tabular PDFs).
                lookback = min(200, candidate - min_progress)
                if lookback > 0:
                    nl = text.rfind("\n", candidate - lookback, candidate)
                    if nl != -1 and (nl + 1) >= min_progress:
                        candidate = nl + 1

                # If we're still inside a token, move back to the start of the token.
                if (
                    candidate > min_progress
                    and text[candidate].isalnum()
                    and text[candidate - 1].isalnum()
                ):
                    while candidate > min_progress and not text[candidate - 1].isspace():
                        candidate -= 1

            # Skip leading whitespace so chunk content starts cleanly.
            while candidate < length and text[candidate].isspace():
                candidate += 1

            # Guarantee forward progress even in edge cases.
            if candidate <= min_progress and raw_start > min_progress:
                candidate = raw_start
                while candidate < length and text[candidate].isspace():
                    candidate += 1

            return min(candidate, length)

        while start < length:
            current_start = start
            end_candidate = min(length, start + chunk_chars)
            window = text[start:end_candidate]

            # Try to cut on paragraph boundary; otherwise on line boundary.
            cut = window.rfind("\n\n")
            if cut == -1:
                cut = window.rfind("\n")
            if cut != -1 and cut >= int(chunk_chars * 0.6):
                end = start + cut
            else:
                end = end_candidate

            # Heuristic: if the chunk begins inside a table block (first non-empty line has tabs),
            # expand start backwards to include up to 2 previous lines (likely headers).
            # Identify the first non-empty line of the current chunk.
            first_line_start = start
            nl_pos = text.find("\n", start, end)
            if nl_pos == -1:
                first_line = text[start:end].lstrip()
            else:
                first_line = text[start:nl_pos].lstrip()

            if "\t" in first_line and start > 0:
                back_search_from = max(0, start - 300)
                back_slice = text[back_search_from:start]
                back_lines = back_slice.splitlines()
                take_lines = "\n".join(back_lines[-2:])  # pull up to 2 lines
                if take_lines:
                    start = max(0, start - (len(take_lines) + 1))  # +1 for newline
                    # Recompute cut with the expanded start
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
            raw_next_start = max(0, end - overlap)
            start = _align_next_start(raw_next_start, min_progress=current_start + 1)

        return segments

    @staticmethod
    def _page_synopsis_from_blocks(blocks: Sequence[PageBlockPayload], *, max_chars: int = 480) -> str:
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
        if len(synopsis) > max_chars:
            synopsis = synopsis[:max_chars].rsplit(" ", 1)[0].rstrip()
            synopsis = f"{synopsis}…"
        return synopsis


    def _mark_job_completed(self, job: KnowledgeIngestionJob, *, extra: dict[str, Any] | None = None) -> None:
        finished = timezone.now()
        payload = dict(job.payload or {})
        if extra:
            payload.update(extra)
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.COMPLETED,
            finished_at=finished,
            run_after=None,
            lease_expires_at=None,
            payload=payload,
        )
        self._invalidate_alias_cache(job.business_profile_id)
        self._release_deferred_jobs(job.business_profile_id)

    def _handle_failure(
        self,
        job: KnowledgeIngestionJob,
        message: str,
        *,
        exc: Exception | None = None,
        retryable: bool | None = None,
    ) -> None:
        error_message = (message or "").strip() or "ingestion failed"

        is_retryable = retryable
        if is_retryable is None:
            is_retryable = True
            if isinstance(exc, UnsupportedFormatError):
                is_retryable = False
            if job.job_type == KnowledgeIngestionJobType.INGEST:
                lowered = error_message.lower()
                if "extracted document is empty" in lowered or "unsupported file type" in lowered:
                    is_retryable = False
                if "is not installed" in lowered and "ingestion is not available" in lowered:
                    is_retryable = False

        if is_retryable:
            scheduled = self._requeue_job_with_backoff(job, error_message, reason="error")
            if scheduled:
                if job.job_type == KnowledgeIngestionJobType.INGEST:
                    upload = job.upload
                    upload.ingestion_error = error_message[:400]
                    upload.status = KnowledgeStatus.PROCESSING
                    upload.save(update_fields=["ingestion_error", "status", "updated_at"])
                self._release_deferred_jobs(job.business_profile_id)
                return
        else:
            self._mark_job_failed_terminal(job, error_message, reason="fatal")
        now = timezone.now()
        upload = job.upload
        if job.job_type == KnowledgeIngestionJobType.EMBED:
            metadata = dict(upload.ingestion_metadata or {})
            embedding_meta = metadata.get("embedding")
            if not isinstance(embedding_meta, dict):
                embedding_meta = {}
            embedding_meta.update(
                {
                    "status": "failed",
                    "job_id": str(job.id),
                    "failed_at": now.isoformat(),
                    "error": error_message[:400],
                }
            )
            metadata["embedding"] = embedding_meta
            upload.ingestion_metadata = metadata
            upload.save(update_fields=["ingestion_metadata", "updated_at"])
        else:
            upload.ingestion_error = error_message
            upload.status = KnowledgeStatus.FAILED
            upload.save(update_fields=["ingestion_error", "status", "updated_at"])
        self._release_deferred_jobs(job.business_profile_id)

    # ------------------------------------------------------------------
    # Helpers

    def _claim_next_job(self) -> KnowledgeIngestionJob | None:
        self._requeue_stale_running_jobs()

        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        qs = (
            KnowledgeIngestionJob.objects.filter(status=KnowledgeIngestionJobStatus.QUEUED)
            .filter(eligible)
            .annotate(
                priority=Case(
                    When(job_type=KnowledgeIngestionJobType.INGEST, then=Value(0)),
                    When(job_type=KnowledgeIngestionJobType.EMBED, then=Value(1)),
                    default=Value(5),
                    output_field=IntegerField(),
                )
            )
            .select_related("upload__file_detail", "upload__url_detail", "upload__business_profile")
            .order_by("priority", "created_at")
        )

        supports_skip_locked = bool(
            getattr(connection.features, "has_select_for_update", False)
            and getattr(connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(connection.features, "has_select_for_update_of", False))

        with transaction.atomic():
            # Lock only the ingestion job row to avoid FOR UPDATE errors on nullable outer joins.
            for_update_kwargs: dict[str, Any] = {}
            if supports_skip_locked:
                for_update_kwargs["skip_locked"] = True
            if supports_for_update_of:
                for_update_kwargs["of"] = ("self",)
            locked = qs.select_for_update(**for_update_kwargs)
            job = locked.first()
            if not job:
                return None
            lease = now + timedelta(seconds=self.job_lease_seconds)
            defaults = self._job_max_attempts(job)
            if job.job_type == KnowledgeIngestionJobType.INGEST:
                cancelled = (
                    KnowledgeIngestionJob.objects.filter(
                        upload_id=job.upload_id,
                        job_type=KnowledgeIngestionJobType.INGEST,
                        status__in=(
                            KnowledgeIngestionJobStatus.QUEUED,
                            KnowledgeIngestionJobStatus.DEFERRED,
                        ),
                    )
                    .exclude(id=job.id)
                    .update(
                        status=KnowledgeIngestionJobStatus.CANCELLED,
                        finished_at=now,
                        error_detail="auto-cancel: duplicate ingestion job",
                    )
                )
                if cancelled:
                    logger.warning(
                        "ingest.job_dedupe_cancelled upload=%s kept_job=%s cancelled=%s",
                        job.upload_id,
                        job.id,
                        cancelled,
                    )
            job.status = KnowledgeIngestionJobStatus.RUNNING
            job.started_at = now
            job.run_after = None
            job.lease_expires_at = lease
            if not job.max_attempts:
                job.max_attempts = defaults
            job.save(update_fields=["status", "started_at", "run_after", "lease_expires_at", "max_attempts", "updated_at"])
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
        if suffix in {".jsonl", ".ndjson"} or "jsonl" in content_type or "ndjson" in content_type:
            return "jsonl"
        if suffix == ".json" or "json" in content_type or "json" in (guessed or ""):
            return "json"
        if suffix in {".csv", ".tsv"} or "csv" in content_type or "csv" in (guessed or ""):
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
        return suffix.strip(".") if suffix else None

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

    def _should_use_dataset_mode(self, *, estimated_rows: int | None) -> bool:
        if not self.dataset_mode_enabled:
            return False
        if estimated_rows is None:
            return False
        return int(estimated_rows) >= int(self.dataset_row_threshold)

    def _dataset_storage_directory(self, upload: KnowledgeUpload) -> tuple[Path, Path]:
        rel_dir = Path("datasets") / str(upload.business_profile_id) / str(upload.id)
        abs_dir = (self.media_root / rel_dir).resolve()
        abs_dir.relative_to(self.media_root)
        return abs_dir, rel_dir

    def _reset_dataset_storage(self, upload: KnowledgeUpload) -> tuple[Path, Path]:
        abs_dir, rel_dir = self._dataset_storage_directory(upload)
        if abs_dir.exists():
            try:
                shutil.rmtree(abs_dir)
            except OSError as exc:
                logger.warning("dataset.cleanup_failed upload=%s error=%s", upload.id, exc)
        abs_dir.mkdir(parents=True, exist_ok=True)
        return abs_dir, rel_dir

    @staticmethod
    def _stringify_dataset_cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()  # datetime/date-like
            except Exception:
                pass
        text = str(value)
        if "\x00" in text:
            text = text.replace("\x00", " ")
        return text

    @staticmethod
    def _estimate_delimited_row_count(path: Path, *, sample_bytes: int = 250_000) -> int | None:
        try:
            file_size = path.stat().st_size
        except OSError:
            return None
        if file_size <= 0:
            return 0
        try:
            with path.open("rb") as handle:
                sample = handle.read(sample_bytes)
        except OSError:
            return None
        lines = sample.count(b"\n")
        if lines <= 1:
            return 1 if file_size else 0
        avg = len(sample) / float(lines)
        if avg <= 0:
            return None
        return max(0, int(file_size / avg))

    def _suggest_dataset_key_columns(
        self,
        *,
        column_schema: Sequence[str],
        sample_rows: Sequence[Sequence[str]],
        max_candidates: int = 8,
    ) -> list[dict[str, Any]]:
        if not column_schema or not sample_rows:
            return []
        candidates: list[dict[str, Any]] = []
        sample_count = len(sample_rows)
        min_samples = min(10, sample_count)

        for idx, column in enumerate(column_schema):
            name = str(column or "").strip()
            canonical = self._canonical_column_name(name)
            if not canonical:
                continue
            values = [
                str(row[idx]).strip()
                for row in sample_rows
                if idx < len(row) and str(row[idx]).strip()
            ]
            non_empty = len(values)
            if non_empty < max(3, min_samples):
                continue
            unique = len(set(values))
            unique_ratio = unique / float(non_empty) if non_empty else 0.0
            keyword_match = any(key in canonical for key in ALIAS_KEYWORDS) or canonical.endswith("_id")
            score = (1.0 if keyword_match else 0.0) + unique_ratio
            candidates.append(
                {
                    "column": name,
                    "normalized": canonical,
                    "non_empty": non_empty,
                    "unique": unique,
                    "unique_ratio": round(unique_ratio, 4),
                    "keyword_match": bool(keyword_match),
                    "score": round(score, 4),
                }
            )

        candidates.sort(key=lambda item: (item.get("score", 0), item.get("non_empty", 0)), reverse=True)
        return candidates[: max_candidates]

    @staticmethod
    def _identifier_mapping_is_required(mapping: Any) -> bool:
        override = getattr(mapping, "is_required", None)
        if override is not None:
            return bool(override)
        identifier = getattr(mapping, "identifier", None)
        return bool(getattr(identifier, "is_required", False))

    def _dataset_key_index_columns(
        self,
        *,
        upload: KnowledgeUpload,
        sheet_name: str | None,
        column_schema: Sequence[str],
        suggested_keys: Sequence[Mapping[str, Any]] | None,
        rules: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """
        Decide which columns should receive a dataset key index (Bloom filter).

        Preference order:
        1) Active IdentifierColumnMapping columns (tenant-defined).
        2) Suggested key columns from sampling.
        3) Fallback heuristics on column names.
        """

        max_columns = int(getattr(settings, "DATASET_KEY_INDEX_MAX_COLUMNS", 4) or 4)
        max_columns = max(0, min(20, max_columns))
        if max_columns <= 0:
            return []

        allow_sensitive = str(getattr(settings, "DATASET_KEY_INDEX_ALLOW_SENSITIVE", "false")).lower() in {
            "1",
            "true",
            "yes",
        }
        min_suggested_score = float(getattr(settings, "DATASET_KEY_INDEX_SUGGESTED_MIN_SCORE", 0.9) or 0.9)
        min_suggested_score = max(0.0, min(10.0, min_suggested_score))

        schema_canon: list[str] = [self._canonical_column_name(col) for col in column_schema]
        index_map = {canon: idx for idx, canon in enumerate(schema_canon) if canon}
        name_map = {canon: col for canon, col in zip(schema_canon, column_schema) if canon and col}

        def _eligible(column_name: str) -> bool:
            if not column_name:
                return False
            if allow_sensitive or not rules:
                return True
            return not self._column_is_sensitive(column_name, rules)

        chosen: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(*, column: str, source: str, **extra: Any) -> None:
            canon = self._canonical_column_name(column)
            if not canon or canon in seen:
                return
            idx = index_map.get(canon)
            if idx is None:
                return
            actual = name_map.get(canon) or column
            if not _eligible(actual):
                return
            seen.add(canon)
            chosen.append(
                {
                    "column": actual,
                    "column_index": idx,
                    "source": source,
                    **extra,
                }
            )

        try:
            from apps.accounts.models import IdentifierColumnMapping, IdentifierColumnStatus, IdentifierSchemaStatus

            mapping_qs = IdentifierColumnMapping.objects.select_related("identifier").filter(
                business_profile=upload.business_profile,
                upload=upload,
                status=IdentifierColumnStatus.ACTIVE,
                identifier__status=IdentifierSchemaStatus.ACTIVE,
            )
            if sheet_name:
                mapping_qs = mapping_qs.filter(sheet_name__iexact=str(sheet_name).strip())
            for mapping in mapping_qs:
                identifier = getattr(mapping, "identifier", None)
                key = getattr(identifier, "key", None)
                _add(
                    column=str(getattr(mapping, "column_name", "") or "").strip(),
                    source="identifier_mapping",
                    identifier_key=str(key or "").strip() or None,
                    identifier_required=self._identifier_mapping_is_required(mapping),
                )
                if len(chosen) >= max_columns:
                    break
        except Exception:  # pragma: no cover - optional for early deployments
            pass

        if suggested_keys:
            for entry in suggested_keys:
                col = str(entry.get("column") or "").strip()
                if not col:
                    continue
                try:
                    score = float(entry.get("score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                if score < min_suggested_score:
                    continue
                _add(column=col, source="suggested", suggested_score=score, suggested_keyword_match=bool(entry.get("keyword_match")))
                if len(chosen) >= max_columns:
                    break

        if len(chosen) >= max_columns:
            return chosen[:max_columns]

        heuristic_terms = ("invoice", "order", "ticket", "serial", "reference", "ref", "email", "phone", "mobile", "code", "sku")
        for col in column_schema:
            canon = self._canonical_column_name(col)
            if not canon or canon in seen:
                continue
            if not any(term in canon for term in heuristic_terms):
                continue
            _add(column=str(col or "").strip(), source="heuristic")
            if len(chosen) >= max_columns:
                break

        return chosen[:max_columns]

    def _build_dataset_card_segment_payload(
        self,
        *,
        upload: KnowledgeUpload,
        ingestion_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return build_dataset_card_segment_payload(upload=upload, ingestion_metadata=ingestion_metadata)

    def _build_dataset_preview_table(
        self,
        *,
        order_index: int,
        sheet_name: str,
        sheet_index: int | None,
        column_schema: list[str],
        preview_rows: Sequence[tuple[int, Sequence[str]]],
        file_detail: KnowledgeUploadFile,
        source_label: str,
        content_type: str,
        rules: Mapping[str, Any] | None,
    ) -> tuple[TablePayload, PageLayout]:
        visible_mask = [
            not self._column_is_sensitive(column, rules)
            for column in column_schema
        ] if rules else [True] * len(column_schema)
        rows: list[TableRowPayload] = []
        for row_index, values in preview_rows:
            attributes = {column_schema[i]: (values[i] if i < len(values) else "") for i in range(len(column_schema))}
            if rules and self._row_is_internal(attributes, rules):
                continue
            cells: list[TableCellPayload] = []
            row_values: list[str] = []
            for col_idx, column_key in enumerate(column_schema):
                if col_idx >= len(values):
                    cell_value = ""
                else:
                    cell_value = str(values[col_idx] or "")
                if visible_mask[col_idx]:
                    cells.append(
                        TableCellPayload(
                            row_index=row_index,
                            column_index=col_idx,
                            column_key=column_key,
                            raw_text=cell_value,
                        )
                    )
                    row_values.append(cell_value)
            rows.append(
                TableRowPayload(
                    row_index=row_index,
                    page_number=sheet_index,
                    raw_text="\t".join(row_values),
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
            rows=rows,
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
            metadata={"sheet_name": sheet_name, "dataset_mode": True},
        )
        return table, page_layout

    def _extract_csv(
        self,
        path: Path,
        *,
        format_hint: str,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        estimated_rows = self._estimate_delimited_row_count(path)
        if upload and self._should_use_dataset_mode(estimated_rows=estimated_rows):
            return self._extract_delimited_dataset(
                path,
                format_hint=format_hint,
                file_detail=file_detail,
                upload=upload,
                estimated_rows=estimated_rows,
            )

        raw_text = self._extract_text_file(path)
        normalized = raw_text.lstrip("\ufeff")
        if not normalized.strip():
            raise KnowledgeIngestionError("CSV document did not contain any rows.")
        delimiter = "\t" if format_hint == "tsv" else ","
        sample = normalized[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
            delimiter = dialect.delimiter or delimiter
        except Exception:
            # Keep default delimiter
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

    def _extract_delimited_dataset(
        self,
        path: Path,
        *,
        format_hint: str,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload,
        estimated_rows: int | None,
    ) -> ExtractionResult:
        if not upload:
            raise KnowledgeIngestionError("Dataset mode requires an upload record.")

        dataset_root, dataset_root_rel = self._reset_dataset_storage(upload)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        display_name = (file_detail.filename or "Dataset").strip() or "Dataset"
        safe_label = slugify(Path(display_name).stem) or "dataset"
        dataset_filename = f"{timestamp}_{safe_label}.csv.gz"
        dataset_path = dataset_root / dataset_filename
        dataset_rel_path = (dataset_root_rel / dataset_filename).as_posix()

        rules = self._table_privacy_rules(upload)
        preview_row_cap = int(self.dataset_preview_rows)
        sample_row_cap = int(self.dataset_sample_rows)

        try:
            with path.open("rb") as handle:
                sample_bytes = handle.read(64 * 1024)
        except OSError as exc:
            raise KnowledgeIngestionError(f"Unable to read dataset sample: {exc}") from exc

        sample_text = sample_bytes.decode("utf-8", errors="ignore")
        delimiter = "\t" if format_hint == "tsv" else ","
        try:
            dialect = csv.Sniffer().sniff(sample_text[:4096], delimiters=[",", ";", "\t", "|"])
            delimiter = getattr(dialect, "delimiter", delimiter) or delimiter
        except Exception:
            dialect = csv.excel

        preview_rows: list[tuple[int, list[str]]] = []
        sample_rows: list[list[str]] = []
        sample_visible_rows: list[dict[str, str]] = []
        row_count = 0
        internal_skipped = 0
        sample_target = max(sample_row_cap, 25)

        key_index_enabled = str(getattr(settings, "DATASET_KEY_INDEX_ENABLED", "true")).lower() in {"1", "true", "yes"}
        key_index_max_bytes = int(getattr(settings, "DATASET_KEY_INDEX_MAX_BYTES", 2_000_000) or 2_000_000)
        key_index_max_bytes = max(4096, min(25_000_000, key_index_max_bytes))
        key_indexes: list[dict[str, Any]] = []

        with path.open("rb") as raw_in:
            reader_stream = io.TextIOWrapper(raw_in, encoding="utf-8", errors="ignore", newline="")
            reader = csv.reader(reader_stream, delimiter=delimiter)
            try:
                raw_header = next(reader)
            except StopIteration:
                raise KnowledgeIngestionError("Dataset did not contain a header row.")

            if raw_header and isinstance(raw_header[0], str):
                raw_header[0] = raw_header[0].lstrip("\ufeff")

            column_schema: list[str] = []
            for idx, value in enumerate(raw_header):
                label = str(value or "").strip()
                column_schema.append(label or f"column_{idx + 1}")
            if not column_schema:
                raise KnowledgeIngestionError("Dataset did not contain any columns.")

            visible_mask = [
                not self._column_is_sensitive(column, rules)
                for column in column_schema
            ] if rules else [True] * len(column_schema)

            dataset_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(dataset_path, "wt", encoding="utf-8", newline="") as out_handle:
                writer = csv.writer(out_handle)
                writer.writerow(column_schema)

                for row in reader:
                    values = [str(item or "") for item in row]
                    if len(values) < len(column_schema):
                        values.extend([""] * (len(column_schema) - len(values)))
                    elif len(values) > len(column_schema):
                        values = values[: len(column_schema)]

                    if not any(value.strip() for value in values):
                        continue

                    attributes = {column_schema[i]: values[i] for i in range(len(column_schema))}
                    if rules and self._row_is_internal(attributes, rules):
                        internal_skipped += 1
                        continue

                    row_count += 1
                    writer.writerow(values)

                    if len(preview_rows) < preview_row_cap:
                        preview_rows.append((row_count, values))
                    if len(sample_rows) < sample_target:
                        sample_rows.append(values)
                    if len(sample_visible_rows) < sample_row_cap:
                        row_preview = {
                            column_schema[i]: values[i]
                            for i in range(len(column_schema))
                            if visible_mask[i]
                        }
                        sample_visible_rows.append(redact_mapping_preview(row_preview))

                    if key_index_enabled and not key_indexes and len(sample_rows) >= sample_target:
                        suggested_preview = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
                        chosen_columns = self._dataset_key_index_columns(
                            upload=upload,
                            sheet_name=None,
                            column_schema=column_schema,
                            suggested_keys=suggested_preview,
                            rules=rules,
                        )
                        expected_items = int(estimated_rows or row_count or 1)
                        spec = bloom_spec_for_items(expected_items)
                        for entry in chosen_columns:
                            bits = spec.bits
                            byte_len = (bits + 7) // 8
                            if byte_len > key_index_max_bytes:
                                bits = key_index_max_bytes * 8
                            bloom = BloomFilter(bits=bits, hashes=spec.hashes)
                            key_indexes.append(
                                {
                                    "column": entry.get("column"),
                                    "column_index": int(entry.get("column_index")),
                                    "source": entry.get("source"),
                                    "identifier_key": entry.get("identifier_key"),
                                    "identifier_required": entry.get("identifier_required"),
                                    "bloom": bloom,
                                    "value_count": 0,
                                }
                            )
                        if key_indexes:
                            for sample in sample_rows:
                                for info in key_indexes:
                                    idx = int(info["column_index"])
                                    if idx >= len(sample):
                                        continue
                                    normalized = normalize_identifier_value(sample[idx])
                                    if not normalized:
                                        continue
                                    info["bloom"].add(normalized)
                                    info["value_count"] += 1
                    elif key_indexes:
                        for info in key_indexes:
                            idx = int(info["column_index"])
                            if idx >= len(values):
                                continue
                            normalized = normalize_identifier_value(values[idx])
                            if not normalized:
                                continue
                            info["bloom"].add(normalized)
                            info["value_count"] += 1

        try:
            dataset_size = dataset_path.stat().st_size
        except OSError:
            dataset_size = 0

        suggested_keys = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
        key_index_payloads: list[dict[str, Any]] = []
        if key_indexes:
            for info in key_indexes:
                column_name = str(info.get("column") or "").strip()
                if not column_name:
                    continue
                index_rel_path = resolve_key_index_storage_path(dataset_rel_path=dataset_rel_path, column_name=column_name)
                abs_index_path = (self.media_root / Path(index_rel_path)).resolve()
                try:
                    abs_index_path.relative_to(self.media_root)
                except ValueError:
                    continue
                bloom = info.get("bloom")
                if not isinstance(bloom, BloomFilter):
                    continue
                try:
                    written_bytes = write_bloom_filter(abs_index_path, bloom)
                except OSError:
                    continue
                key_index_payloads.append(
                    {
                        "column": column_name,
                        "storage_path": index_rel_path,
                        "bits": int(bloom.bits),
                        "hashes": int(bloom.hashes),
                        "bytes": int(written_bytes),
                        "values_indexed": int(info.get("value_count") or 0),
                        "source": info.get("source"),
                        "identifier_key": info.get("identifier_key"),
                        "identifier_required": info.get("identifier_required"),
                    }
                )

        table, page = self._build_dataset_preview_table(
            order_index=1,
            sheet_name=display_name,
            sheet_index=None,
            column_schema=column_schema,
            preview_rows=preview_rows,
            file_detail=file_detail,
            source_label="dataset_csv",
            content_type="text/csv",
            rules=rules,
        )
        tables = [table]
        preview_text = self._table_preview_text(tables, rules=rules)

        issues: list[IssuePayload] = []
        issues.append(
            IssuePayload(
                code="dataset_mode_enabled",
                severity=KnowledgeIssueSeverity.INFO.value,
                description="Large dataset stored in dataset mode (file-backed). Only a small preview is indexed into Postgres.",
                details={
                    "estimated_rows": estimated_rows,
                    "rows_stored": row_count,
                    "preview_rows_indexed": len(table.rows),
                    "internal_rows_skipped": internal_skipped,
                    "dataset_storage_format": self.dataset_storage_format,
                },
            )
        )

        dataset_metadata = {
            "enabled": True,
            "storage_format": self.dataset_storage_format,
            "storage_path": dataset_rel_path,
            "size_bytes": dataset_size,
            "row_count": row_count,
            "estimated_row_count": estimated_rows,
            "column_schema": column_schema,
            "sample_rows": sample_visible_rows,
            "suggested_key_columns": suggested_keys,
            "delimiter": delimiter,
            "key_indexes": key_index_payloads,
        }

        table_stats = self._table_stats_summary(
            total_rows=row_count,
            indexed_rows=len(table.rows),
            row_cap=len(table.rows),
            source_row_count=self._integration_row_count(upload),
            table_count=1,
            partial_tables=1 if row_count > len(table.rows) else 0,
            row_tier="large" if estimated_rows and estimated_rows >= self.dataset_row_threshold else "medium",
        )
        metadata = {
            "format": format_hint,
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": 1,
            "table_truncation": {
                "truncated_tables": 0,
                "truncated_rows": max(0, row_count - len(table.rows)),
                "truncated_columns": 0,
            },
            "table_stats": table_stats,
            "dataset": dataset_metadata,
        }
        return ExtractionResult(
            text=preview_text,
            format_hint=format_hint,
            metadata=metadata,
            pages=[page],
            tables=tables,
            issues=issues,
            entities=self._table_row_entities(
                tables,
                business_profile=getattr(upload, "business_profile", None),
                upload=upload,
            ),
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
        rules = self._table_privacy_rules(upload)

        allowed_sheets: list[tuple[int, str, Any]] = []
        estimated_rows = 0
        policy_skipped: list[str] = []
        for sheet_idx, sheet in enumerate(workbook.worksheets, start=1):
            sheet_name = sheet.title or f"Sheet {sheet_idx}"
            if not sheet_is_allowed(sheet_name, policy):
                policy_skipped.append(sheet_name)
                continue
            allowed_sheets.append((sheet_idx, sheet_name, sheet))
            try:
                estimated_rows += int(getattr(sheet, "max_row", 0) or 0)
            except (TypeError, ValueError):
                continue

        if upload and self._should_use_dataset_mode(estimated_rows=estimated_rows):
            return self._extract_xlsx_dataset_mode(
                file_detail=file_detail,
                upload=upload,
                sheets=allowed_sheets,
                policy_skipped=policy_skipped,
                estimated_rows=estimated_rows,
            )

        diagnostics: list[SheetNormalizationDiagnostics] = [
            SheetNormalizationDiagnostics(sheet_name=name, skipped=True, skip_reason="policy")
            for name in policy_skipped
        ]
        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        order_index = 1
        for sheet_idx, sheet_name, sheet in allowed_sheets:
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

    def _extract_xlsx_dataset_mode(
        self,
        *,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload,
        sheets: Sequence[tuple[int, str, Any]],
        policy_skipped: Sequence[str],
        estimated_rows: int,
    ) -> ExtractionResult:
        dataset_root, dataset_root_rel = self._reset_dataset_storage(upload)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        rules = self._table_privacy_rules(upload)
        preview_row_cap = int(self.dataset_preview_rows)
        sample_row_cap = int(self.dataset_sample_rows)
        sample_target = max(sample_row_cap, 25)

        key_index_enabled = str(getattr(settings, "DATASET_KEY_INDEX_ENABLED", "true")).lower() in {"1", "true", "yes"}
        key_index_max_bytes = int(getattr(settings, "DATASET_KEY_INDEX_MAX_BYTES", 2_000_000) or 2_000_000)
        key_index_max_bytes = max(4096, min(25_000_000, key_index_max_bytes))

        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        issues: list[IssuePayload] = []
        dataset_sheets: list[dict[str, Any]] = []

        total_rows = 0
        total_preview_rows = 0
        internal_skipped_total = 0

        for order_index, (sheet_idx, sheet_name, sheet) in enumerate(sheets, start=1):
            try:
                max_col = int(getattr(sheet, "max_column", 0) or 0)
            except (TypeError, ValueError):
                max_col = 0

            rows_iter = sheet.iter_rows(values_only=True)
            header_values: list[str] | None = None
            for raw_row in rows_iter:
                candidate = [self._stringify_dataset_cell(val) for val in raw_row]
                if any(str(value).strip() for value in candidate):
                    header_values = candidate
                    break
            if header_values is None:
                continue

            if max_col <= 0:
                max_col = len(header_values)
            if len(header_values) < max_col:
                header_values.extend([""] * (max_col - len(header_values)))

            column_schema: list[str] = []
            for idx in range(max_col):
                label = header_values[idx] if idx < len(header_values) else ""
                column_schema.append(str(label or "").strip() or f"column_{idx + 1}")

            visible_mask = [
                not self._column_is_sensitive(column, rules)
                for column in column_schema
            ] if rules else [True] * len(column_schema)

            sheet_slug = slugify(sheet_name) or f"sheet_{sheet_idx}"
            dataset_filename = f"{timestamp}_sheet{sheet_idx}_{sheet_slug}.csv.gz"
            dataset_path = dataset_root / dataset_filename
            dataset_rel_path = (dataset_root_rel / dataset_filename).as_posix()

            preview_rows: list[tuple[int, list[str]]] = []
            sample_rows: list[list[str]] = []
            sample_visible_rows: list[dict[str, str]] = []
            row_count = 0
            internal_skipped = 0
            expected_items = 0
            try:
                expected_items = int(getattr(sheet, "max_row", 0) or 0)
            except (TypeError, ValueError):
                expected_items = 0
            sheet_key_indexes: list[dict[str, Any]] = []

            dataset_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(dataset_path, "wt", encoding="utf-8", newline="") as out_handle:
                writer = csv.writer(out_handle)
                writer.writerow(column_schema)
                for raw_row in rows_iter:
                    values = [self._stringify_dataset_cell(val) for val in raw_row]
                    if len(values) < len(column_schema):
                        values.extend([""] * (len(column_schema) - len(values)))
                    elif len(values) > len(column_schema):
                        values = values[: len(column_schema)]

                    if not any(str(value).strip() for value in values):
                        continue

                    attributes = {column_schema[i]: str(values[i] or "") for i in range(len(column_schema))}
                    if rules and self._row_is_internal(attributes, rules):
                        internal_skipped += 1
                        continue

                    row_count += 1
                    writer.writerow([str(value or "") for value in values])

                    if len(preview_rows) < preview_row_cap:
                        preview_rows.append((row_count, [str(value or "") for value in values]))
                    if len(sample_rows) < sample_target:
                        sample_rows.append([str(value or "") for value in values])
                    if len(sample_visible_rows) < sample_row_cap:
                        row_preview = {
                            column_schema[i]: str(values[i] or "")
                            for i in range(len(column_schema))
                            if visible_mask[i]
                        }
                        sample_visible_rows.append(redact_mapping_preview(row_preview))

                    if key_index_enabled and not sheet_key_indexes and len(sample_rows) >= sample_target:
                        suggested_preview = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
                        chosen_columns = self._dataset_key_index_columns(
                            upload=upload,
                            sheet_name=sheet_name,
                            column_schema=column_schema,
                            suggested_keys=suggested_preview,
                            rules=rules,
                        )
                        spec = bloom_spec_for_items(int(expected_items or estimated_rows or row_count or 1))
                        for entry in chosen_columns:
                            bits = spec.bits
                            byte_len = (bits + 7) // 8
                            if byte_len > key_index_max_bytes:
                                bits = key_index_max_bytes * 8
                            bloom = BloomFilter(bits=bits, hashes=spec.hashes)
                            sheet_key_indexes.append(
                                {
                                    "column": entry.get("column"),
                                    "column_index": int(entry.get("column_index")),
                                    "source": entry.get("source"),
                                    "identifier_key": entry.get("identifier_key"),
                                    "identifier_required": entry.get("identifier_required"),
                                    "bloom": bloom,
                                    "value_count": 0,
                                }
                            )
                        if sheet_key_indexes:
                            for sample in sample_rows:
                                for info in sheet_key_indexes:
                                    idx = int(info["column_index"])
                                    if idx >= len(sample):
                                        continue
                                    normalized = normalize_identifier_value(sample[idx])
                                    if not normalized:
                                        continue
                                    info["bloom"].add(normalized)
                                    info["value_count"] += 1
                    elif sheet_key_indexes:
                        for info in sheet_key_indexes:
                            idx = int(info["column_index"])
                            if idx >= len(values):
                                continue
                            normalized = normalize_identifier_value(values[idx])
                            if not normalized:
                                continue
                            info["bloom"].add(normalized)
                            info["value_count"] += 1

            try:
                dataset_size = dataset_path.stat().st_size
            except OSError:
                dataset_size = 0

            suggested_keys = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
            key_index_payloads: list[dict[str, Any]] = []
            if sheet_key_indexes:
                for info in sheet_key_indexes:
                    column_name = str(info.get("column") or "").strip()
                    if not column_name:
                        continue
                    index_rel_path = resolve_key_index_storage_path(dataset_rel_path=dataset_rel_path, column_name=column_name)
                    abs_index_path = (self.media_root / Path(index_rel_path)).resolve()
                    try:
                        abs_index_path.relative_to(self.media_root)
                    except ValueError:
                        continue
                    bloom = info.get("bloom")
                    if not isinstance(bloom, BloomFilter):
                        continue
                    try:
                        written_bytes = write_bloom_filter(abs_index_path, bloom)
                    except OSError:
                        continue
                    key_index_payloads.append(
                        {
                            "column": column_name,
                            "storage_path": index_rel_path,
                            "bits": int(bloom.bits),
                            "hashes": int(bloom.hashes),
                            "bytes": int(written_bytes),
                            "values_indexed": int(info.get("value_count") or 0),
                            "source": info.get("source"),
                            "identifier_key": info.get("identifier_key"),
                            "identifier_required": info.get("identifier_required"),
                        }
                    )
            table, page_layout = self._build_dataset_preview_table(
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                column_schema=column_schema,
                preview_rows=preview_rows,
                file_detail=file_detail,
                source_label="dataset_xlsx",
                content_type="text/csv",
                rules=rules,
            )
            tables.append(table)
            pages.append(page_layout)
            total_preview_rows += len(table.rows)
            total_rows += row_count
            internal_skipped_total += internal_skipped

            dataset_sheets.append(
                {
                    "sheet_index": sheet_idx,
                    "sheet_name": sheet_name,
                    "storage_path": dataset_rel_path,
                    "size_bytes": dataset_size,
                    "row_count": row_count,
                    "estimated_row_count": int(getattr(sheet, "max_row", 0) or 0),
                    "column_schema": column_schema,
                    "sample_rows": sample_visible_rows,
                    "suggested_key_columns": suggested_keys,
                    "key_indexes": key_index_payloads,
                    "preview_rows_indexed": len(table.rows),
                    "internal_rows_skipped": internal_skipped,
                }
            )
            if row_count > len(table.rows):
                issues.append(
                    IssuePayload(
                        code="dataset_preview_truncated",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="Only a small preview of this dataset sheet was indexed into Postgres.",
                        page_number=sheet_idx,
                        table_order_index=order_index,
                        details={
                            "rows_stored": row_count,
                            "preview_rows_indexed": len(table.rows),
                            "storage_path": dataset_rel_path,
                        },
                    )
                )

        if not tables:
            raise KnowledgeIngestionError("XLSX workbook did not contain any populated sheets.")

        partial_tables = sum(1 for sheet in dataset_sheets if sheet.get("row_count", 0) > sheet.get("preview_rows_indexed", 0))
        table_stats = self._table_stats_summary(
            total_rows=total_rows,
            indexed_rows=total_preview_rows,
            row_cap=preview_row_cap,
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=partial_tables,
            row_tier="large",
        )
        metadata = {
            "format": "xlsx",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": {
                "truncated_tables": 0,
                "truncated_rows": max(0, total_rows - total_preview_rows),
                "truncated_columns": 0,
            },
            "table_stats": table_stats,
            "dataset": {
                "enabled": True,
                "storage_format": self.dataset_storage_format,
                "row_count": total_rows,
                "estimated_row_count": estimated_rows,
                "preview_rows_indexed": total_preview_rows,
                "internal_rows_skipped": internal_skipped_total,
                "policy_skipped_sheets": list(policy_skipped),
                "sheets": dataset_sheets,
            },
        }

        issues.insert(
            0,
            IssuePayload(
                code="dataset_mode_enabled",
                severity=KnowledgeIssueSeverity.INFO.value,
                description="Large spreadsheet stored in dataset mode (file-backed). Only a small preview is indexed into Postgres.",
                details={
                    "estimated_rows": estimated_rows,
                    "rows_stored": total_rows,
                    "preview_rows_indexed": total_preview_rows,
                    "internal_rows_skipped": internal_skipped_total,
                    "sheet_count": len(dataset_sheets),
                },
            ),
        )

        preview_text = self._table_preview_text(tables, rules=rules)
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        return ExtractionResult(
            text=preview_text,
            format_hint="xlsx",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=issues,
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
            workbook = xlrd.open_workbook(filename=str(path), on_demand=True)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open XLS file: {exc}") from exc

        policy = resolve_normalization_policy(upload)
        rules = self._table_privacy_rules(upload)

        allowed_sheets: list[tuple[int, str, Any]] = []
        estimated_rows = 0
        policy_skipped: list[str] = []
        for sheet_idx in range(1, (getattr(workbook, "nsheets", 0) or 0) + 1):
            sheet = workbook.sheet_by_index(sheet_idx - 1)
            sheet_name = getattr(sheet, "name", None) or f"Sheet {sheet_idx}"
            if not sheet_is_allowed(sheet_name, policy):
                policy_skipped.append(sheet_name)
                continue
            allowed_sheets.append((sheet_idx, sheet_name, sheet))
            try:
                estimated_rows += int(getattr(sheet, "nrows", 0) or 0)
            except (TypeError, ValueError):
                continue

        if upload and self._should_use_dataset_mode(estimated_rows=estimated_rows):
            return self._extract_xls_dataset_mode(
                workbook=workbook,
                file_detail=file_detail,
                upload=upload,
                sheets=allowed_sheets,
                policy_skipped=policy_skipped,
                estimated_rows=estimated_rows,
            )

        diagnostics: list[SheetNormalizationDiagnostics] = [
            SheetNormalizationDiagnostics(sheet_name=name, skipped=True, skip_reason="policy")
            for name in policy_skipped
        ]
        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        order_index = 1

        for sheet_idx, sheet_name, sheet in allowed_sheets:
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

    def _extract_xls_dataset_mode(
        self,
        *,
        workbook: Any,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload,
        sheets: Sequence[tuple[int, str, Any]],
        policy_skipped: Sequence[str],
        estimated_rows: int,
    ) -> ExtractionResult:
        dataset_root, dataset_root_rel = self._reset_dataset_storage(upload)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        rules = self._table_privacy_rules(upload)
        preview_row_cap = int(self.dataset_preview_rows)
        sample_row_cap = int(self.dataset_sample_rows)

        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        issues: list[IssuePayload] = []
        dataset_sheets: list[dict[str, Any]] = []

        total_rows = 0
        total_preview_rows = 0
        internal_skipped_total = 0

        for order_index, (sheet_idx, sheet_name, sheet) in enumerate(sheets, start=1):
            nrows = int(getattr(sheet, "nrows", 0) or 0)
            ncols = int(getattr(sheet, "ncols", 0) or 0)
            if nrows <= 0 or ncols <= 0:
                continue

            header_row_idx: int | None = None
            header_values: list[str] | None = None
            scan_limit = min(nrows, 50)
            for idx in range(scan_limit):
                raw_values = list(getattr(sheet, "row_values")(idx))
                candidate = [self._stringify_dataset_cell(val) for val in raw_values[:ncols]]
                if any(str(value).strip() for value in candidate):
                    header_row_idx = idx
                    header_values = candidate
                    break
            if header_row_idx is None or header_values is None:
                continue
            if len(header_values) < ncols:
                header_values.extend([""] * (ncols - len(header_values)))

            column_schema: list[str] = []
            for idx in range(ncols):
                label = header_values[idx] if idx < len(header_values) else ""
                column_schema.append(str(label or "").strip() or f"column_{idx + 1}")

            visible_mask = [
                not self._column_is_sensitive(column, rules)
                for column in column_schema
            ] if rules else [True] * len(column_schema)

            sheet_slug = slugify(sheet_name) or f"sheet_{sheet_idx}"
            dataset_filename = f"{timestamp}_sheet{sheet_idx}_{sheet_slug}.csv.gz"
            dataset_path = dataset_root / dataset_filename
            dataset_rel_path = (dataset_root_rel / dataset_filename).as_posix()

            preview_rows: list[tuple[int, list[str]]] = []
            sample_rows: list[list[str]] = []
            sample_visible_rows: list[dict[str, str]] = []
            row_count = 0
            internal_skipped = 0

            dataset_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(dataset_path, "wt", encoding="utf-8", newline="") as out_handle:
                writer = csv.writer(out_handle)
                writer.writerow(column_schema)

                for row_idx in range(header_row_idx + 1, nrows):
                    raw_values = list(getattr(sheet, "row_values")(row_idx))
                    raw_types = list(getattr(sheet, "row_types")(row_idx))
                    values: list[str] = []
                    for col_idx in range(ncols):
                        cell_value = raw_values[col_idx] if col_idx < len(raw_values) else ""
                        cell_type = raw_types[col_idx] if col_idx < len(raw_types) else None
                        if cell_type == xlrd.XL_CELL_DATE:
                            try:
                                cell_value = xlrd.xldate_as_datetime(cell_value, workbook.datemode)
                            except Exception:
                                cell_value = ""
                        elif cell_type == xlrd.XL_CELL_BOOLEAN:
                            cell_value = bool(cell_value)
                        elif cell_type == xlrd.XL_CELL_ERROR:
                            cell_value = ""
                        values.append(self._stringify_dataset_cell(cell_value))

                    if not any(str(value).strip() for value in values):
                        continue

                    attributes = {column_schema[i]: str(values[i] or "") for i in range(len(column_schema))}
                    if rules and self._row_is_internal(attributes, rules):
                        internal_skipped += 1
                        continue

                    row_count += 1
                    writer.writerow(values)

                    if len(preview_rows) < preview_row_cap:
                        preview_rows.append((row_count, list(values)))
                    if len(sample_rows) < max(sample_row_cap, 25):
                        sample_rows.append(list(values))
                    if len(sample_visible_rows) < sample_row_cap:
                        row_preview = {
                            column_schema[i]: str(values[i] or "")
                            for i in range(len(column_schema))
                            if visible_mask[i]
                        }
                        sample_visible_rows.append(redact_mapping_preview(row_preview))

            try:
                dataset_size = dataset_path.stat().st_size
            except OSError:
                dataset_size = 0

            suggested_keys = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
            table, page_layout = self._build_dataset_preview_table(
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                column_schema=column_schema,
                preview_rows=preview_rows,
                file_detail=file_detail,
                source_label="dataset_xls",
                content_type="text/csv",
                rules=rules,
            )
            tables.append(table)
            pages.append(page_layout)
            total_preview_rows += len(table.rows)
            total_rows += row_count
            internal_skipped_total += internal_skipped

            dataset_sheets.append(
                {
                    "sheet_index": sheet_idx,
                    "sheet_name": sheet_name,
                    "storage_path": dataset_rel_path,
                    "size_bytes": dataset_size,
                    "row_count": row_count,
                    "estimated_row_count": nrows,
                    "column_schema": column_schema,
                    "sample_rows": sample_visible_rows,
                    "suggested_key_columns": suggested_keys,
                    "preview_rows_indexed": len(table.rows),
                    "internal_rows_skipped": internal_skipped,
                }
            )
            if row_count > len(table.rows):
                issues.append(
                    IssuePayload(
                        code="dataset_preview_truncated",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="Only a small preview of this dataset sheet was indexed into Postgres.",
                        page_number=sheet_idx,
                        table_order_index=order_index,
                        details={
                            "rows_stored": row_count,
                            "preview_rows_indexed": len(table.rows),
                            "storage_path": dataset_rel_path,
                        },
                    )
                )

        if not tables:
            raise KnowledgeIngestionError("XLS workbook did not contain any populated sheets.")

        partial_tables = sum(1 for sheet in dataset_sheets if sheet.get("row_count", 0) > sheet.get("preview_rows_indexed", 0))
        table_stats = self._table_stats_summary(
            total_rows=total_rows,
            indexed_rows=total_preview_rows,
            row_cap=preview_row_cap,
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=partial_tables,
            row_tier="large",
        )
        metadata = {
            "format": "xls",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": {
                "truncated_tables": 0,
                "truncated_rows": max(0, total_rows - total_preview_rows),
                "truncated_columns": 0,
            },
            "table_stats": table_stats,
            "dataset": {
                "enabled": True,
                "storage_format": self.dataset_storage_format,
                "row_count": total_rows,
                "estimated_row_count": estimated_rows,
                "preview_rows_indexed": total_preview_rows,
                "internal_rows_skipped": internal_skipped_total,
                "policy_skipped_sheets": list(policy_skipped),
                "sheets": dataset_sheets,
            },
        }

        issues.insert(
            0,
            IssuePayload(
                code="dataset_mode_enabled",
                severity=KnowledgeIssueSeverity.INFO.value,
                description="Large spreadsheet stored in dataset mode (file-backed). Only a small preview is indexed into Postgres.",
                details={
                    "estimated_rows": estimated_rows,
                    "rows_stored": total_rows,
                    "preview_rows_indexed": total_preview_rows,
                    "internal_rows_skipped": internal_skipped_total,
                    "sheet_count": len(dataset_sheets),
                },
            ),
        )

        preview_text = self._table_preview_text(tables, rules=rules)
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        return ExtractionResult(
            text=preview_text,
            format_hint="xls",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=issues,
            entities=table_entities,
        )

    def _extract_jsonl_dataset(
        self,
        path: Path,
        *,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        if upload is None:
            raise KnowledgeIngestionError("JSONL dataset ingestion requires an upload record.")

        dataset_root, dataset_root_rel = self._reset_dataset_storage(upload)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        display_name = (file_detail.filename or "Dataset").strip() or "Dataset"
        safe_label = slugify(Path(display_name).stem) or "dataset"
        dataset_filename = f"{timestamp}_{safe_label}.jsonl.gz"
        dataset_path = dataset_root / dataset_filename
        dataset_rel_path = (dataset_root_rel / dataset_filename).as_posix()

        rules = self._table_privacy_rules(upload)
        preview_row_cap = int(self.dataset_preview_rows)
        sample_row_cap = int(self.dataset_sample_rows)

        row_count = 0
        sample_lines: list[bytes] = []
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as raw_in, gzip.open(dataset_path, "wb") as out_handle:
            for line in raw_in:
                if not line:
                    continue
                out_handle.write(line)
                if not line.strip():
                    continue
                row_count += 1
                if len(sample_lines) < max(sample_row_cap, 25):
                    sample_lines.append(line)

        try:
            dataset_size = dataset_path.stat().st_size
        except OSError:
            dataset_size = 0

        records: list[dict[str, Any]] = []
        keys: set[str] = set()
        parse_errors = 0
        for line in sample_lines:
            try:
                decoded = line.decode("utf-8", errors="ignore")
                obj = json.loads(decoded)
            except Exception:
                parse_errors += 1
                continue
            if isinstance(obj, dict):
                records.append(obj)
                for key in obj.keys():
                    if key is None:
                        continue
                    keys.add(str(key))

        if not keys:
            preview_text = "\n".join(line.decode("utf-8", errors="ignore").strip() for line in sample_lines[:5]).strip()
            issues = [
                IssuePayload(
                    code="dataset_mode_enabled",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="JSONL dataset stored in dataset mode (file-backed). No structured preview was indexed.",
                    details={
                        "rows_stored": row_count,
                        "sample_parse_errors": parse_errors,
                        "dataset_storage_format": "jsonl_gz",
                    },
                )
            ]
            metadata = {
                "format": "jsonl",
                "filename": file_detail.filename,
                "content_type": file_detail.content_type or "",
                "storage_path": file_detail.storage_path,
                "dataset": {
                    "enabled": True,
                    "storage_format": "jsonl_gz",
                    "storage_path": dataset_rel_path,
                    "size_bytes": dataset_size,
                    "row_count": row_count,
                    "sample_parse_errors": parse_errors,
                },
            }
            return ExtractionResult(
                text=preview_text,
                format_hint="jsonl",
                metadata=metadata,
                pages=[],
                tables=[],
                issues=issues,
                entities=[],
            )

        column_schema = sorted(keys)[:200]
        visible_mask = [
            not self._column_is_sensitive(column, rules)
            for column in column_schema
        ] if rules else [True] * len(column_schema)

        preview_rows: list[tuple[int, list[str]]] = []
        sample_rows: list[list[str]] = []
        sample_visible_rows: list[dict[str, str]] = []

        for idx, record in enumerate(records, start=1):
            values: list[str] = []
            for key in column_schema:
                values.append(self._stringify_dataset_cell(record.get(key)))
            preview_rows.append((idx, values))
            if len(sample_rows) < max(sample_row_cap, 25):
                sample_rows.append(values)
            if len(sample_visible_rows) < sample_row_cap:
                row_preview = {
                    column_schema[i]: values[i]
                    for i in range(len(column_schema))
                    if visible_mask[i]
                }
                sample_visible_rows.append(redact_mapping_preview(row_preview))
            if len(preview_rows) >= preview_row_cap:
                break

        suggested_keys = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
        table, page = self._build_dataset_preview_table(
            order_index=1,
            sheet_name=display_name,
            sheet_index=None,
            column_schema=column_schema,
            preview_rows=preview_rows,
            file_detail=file_detail,
            source_label="dataset_jsonl",
            content_type="application/x-ndjson",
            rules=rules,
        )
        tables = [table]
        preview_text = self._table_preview_text(tables, rules=rules)

        issues = [
            IssuePayload(
                code="dataset_mode_enabled",
                severity=KnowledgeIssueSeverity.INFO.value,
                description="JSONL dataset stored in dataset mode (file-backed). Only a small preview is indexed into Postgres.",
                details={
                    "rows_stored": row_count,
                    "preview_rows_indexed": len(table.rows),
                    "sample_parse_errors": parse_errors,
                    "dataset_storage_format": "jsonl_gz",
                },
            )
        ]
        dataset_metadata = {
            "enabled": True,
            "storage_format": "jsonl_gz",
            "storage_path": dataset_rel_path,
            "size_bytes": dataset_size,
            "row_count": row_count,
            "column_schema": column_schema,
            "sample_rows": sample_visible_rows,
            "suggested_key_columns": suggested_keys,
            "sample_parse_errors": parse_errors,
        }
        table_stats = self._table_stats_summary(
            total_rows=row_count,
            indexed_rows=len(table.rows),
            row_cap=len(table.rows),
            source_row_count=self._integration_row_count(upload),
            table_count=1,
            partial_tables=1 if row_count > len(table.rows) else 0,
            row_tier="large" if row_count >= self.dataset_row_threshold else "medium",
        )
        metadata = {
            "format": "jsonl",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": 1,
            "table_truncation": {
                "truncated_tables": 0,
                "truncated_rows": max(0, row_count - len(table.rows)),
                "truncated_columns": 0,
            },
            "table_stats": table_stats,
            "dataset": dataset_metadata,
        }
        return ExtractionResult(
            text=preview_text,
            format_hint="jsonl",
            metadata=metadata,
            pages=[page],
            tables=tables,
            issues=issues,
            entities=self._table_row_entities(
                tables,
                business_profile=getattr(upload, "business_profile", None),
                upload=upload,
            ),
        )

    def _extract_json(
        self,
        path: Path,
        *,
        entity_limit: int | None = None,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        raw_text = self._extract_text_file(path)
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise KnowledgeIngestionError(f"Invalid JSON document: {exc}") from exc

        alias_hygiene = False
        if upload and getattr(upload, "business_profile", None):
            alias_hygiene = FeatureFlagService.snapshot(upload.business_profile).rag_alias_hygiene
        entities, alias_sources = self._json_entities_from_data(data, alias_hygiene=alias_hygiene)
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

    def _json_entities_from_data(
        self,
        data: Any,
        *,
        alias_hygiene: bool = False,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        entities: list[dict[str, Any]] = []
        alias_sources_union: set[str] = set()
        for record_label, record in self._iter_json_entity_records(data):
            flattened = self._flatten_json_record(record)
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
                alias_hygiene=alias_hygiene,
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
        queue: deque[tuple[str, Any]] = deque()
        if isinstance(data, dict):
            queue.append(("record", data))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    queue.append(("record", item))

        seen_ids: set[int] = set()
        while queue and len(seen_ids) < self.max_json_entity_candidates:
            label, record = queue.popleft()
            if not isinstance(record, dict):
                continue
            marker = id(record)
            if marker in seen_ids:
                continue
            seen_ids.add(marker)
            yield label, record
            if len(seen_ids) >= self.max_json_entity_candidates:
                break
            for key, value in record.items():
                next_label = key.rstrip("s") or key or label
                if isinstance(value, dict):
                    queue.append((next_label, value))
                elif isinstance(value, list):
                    for entry in value:
                        if isinstance(entry, dict):
                            queue.append((next_label, entry))

    @staticmethod
    def _flatten_json_record(record: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}

        def visit(prefix: str, value: Any) -> None:
            key_prefix = prefix.strip(".")
            if isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    if sub_key is None:
                        continue
                    next_prefix = f"{prefix}.{sub_key}" if prefix else str(sub_key)
                    visit(next_prefix, sub_val)
            elif isinstance(value, list):
                if not value:
                    result[key_prefix] = ""
                    return
                scalar_items = [item for item in value if isinstance(item, (str, int, float, bool))]
                if scalar_items and len(scalar_items) == len(value):
                    joined = ", ".join(KnowledgeIngestionService._stringify_json_scalar(item) for item in scalar_items[:5])
                    if len(value) > 5:
                        joined = f"{joined} …"
                    result[key_prefix] = joined
                    return
                dict_items = [item for item in value if isinstance(item, dict)]
                if dict_items:
                    result[f"{key_prefix}_count"] = str(len(dict_items))
                    first = dict_items[0]
                    for sub_key, sub_val in list(first.items())[:3]:
                        nested_key = f"{key_prefix}_0_{sub_key}".strip("_")
                        result[nested_key] = KnowledgeIngestionService._stringify_json_scalar(sub_val)
                    return
                result[key_prefix] = KnowledgeIngestionService._stringify_json_scalar(value)
            else:
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
        if not flattened:
            return False
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
            direct = record.get(key) if isinstance(record, Mapping) else None
            indirect = flattened.get(key)
            value = direct or indirect
            if isinstance(value, str) and value.strip():
                return True
            if isinstance(value, (int, float)):
                return True
        populated = sum(1 for value in flattened.values() if value)
        return populated >= 2

    @staticmethod
    def _stringify_json_scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value.strip()
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
        if not tables:
            empty_metrics = {"truncated_tables": 0, "truncated_rows": 0, "truncated_columns": 0}
            empty_summary = {"total_rows": 0, "indexed_rows": 0, "partial_tables": 0, "row_cap_hint": 0, "row_tier_hint": "unknown"}
            return [], empty_metrics, [], empty_summary
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
        tier_rank = {"unknown": 0, "small": 1, "medium": 2, "override": 2, "large": 3}
        for table in tables:
            original_row_count = len(table.rows or [])
            summary["total_rows"] += original_row_count
            table_row_cap, tier, strategy = self._determine_table_row_cap(original_row_count, effective_config)
            summary["row_cap_hint"] = max(summary["row_cap_hint"], table_row_cap)
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
        schema = list(table.column_schema or [])
        plan: list[tuple[int, str]] = []
        removed_columns = 0
        column_limit = max_columns if isinstance(max_columns, int) and max_columns > 0 else None
        canonical_whitelist = set(column_whitelist or set())
        for idx, column in enumerate(schema):
            label = column or f"column_{idx + 1}"
            canonical = self._canonical_column_name(label, f"column_{idx + 1}")
            if canonical_whitelist and canonical not in canonical_whitelist:
                removed_columns += 1
                continue
            if column_limit is not None and len(plan) >= column_limit:
                removed_columns += 1
                continue
            plan.append((idx, label))
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
        return bool(pattern) and (
            pattern.startswith("^")
            or pattern.endswith("$")
            or any(ch in pattern for ch in "[]().*+?|")
        )

    @staticmethod
    def _canonical_column_name(value: str | None, fallback: str | None = None) -> str:
        if isinstance(value, str):
            lowered = re.sub(r"\s+", " ", value.strip().lower())
            if lowered:
                return lowered
        if fallback:
            return fallback.strip().lower()
        return ""

    def _column_is_sensitive(self, column: str, rules: Mapping[str, Any]) -> bool:
        canonical = self._canonical_column_name(column)
        if canonical and canonical in rules.get("sensitive_exact", set()):
            return True
        patterns = rules.get("sensitive_patterns") or []
        combined = column or ""
        for pattern in patterns:
            try:
                if pattern.search(combined) or (canonical and pattern.search(canonical)):
                    return True
            except re.error:
                continue
        return False

    def _row_is_internal(self, attributes: Mapping[str, str], rules: Mapping[str, Any]) -> bool:
        column = rules.get("row_flag_column")
        values = rules.get("row_flag_values") or set()
        if not column or not values:
            return False
        for key, value in attributes.items():
            canonical = self._canonical_column_name(key)
            if canonical == column and str(value or "").strip().lower() in values:
                return True
        return False

    @staticmethod
    def _match_case(replacement: str, original: str) -> str:
        if not original:
            return replacement
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement.lower()

    def _compile_ocr_replacements(self, raw: Any) -> list[tuple[re.Pattern[str], str]]:
        defaults = (
            (r"\bfoos\b", "fees"),
            (r"\bfous\b", "fees"),
            (r"\bfroo\b", "free"),
            (r"\bfrog\b", "free"),
            (r"\bfino\b", "free"),
            (r"\bronowal\b", "renewal"),
            (r"\brenowal\b", "renewal"),
            (r"\bbhield\b", "shield"),
        )
        replacements: list[tuple[str, str]] = list(defaults)
        if isinstance(raw, str) and raw.strip():
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    replacements.extend((str(k), str(v)) for k, v in loaded.items())
                elif isinstance(loaded, list):
                    for item in loaded:
                        if isinstance(item, dict):
                            pattern = item.get("pattern")
                            replacement = item.get("replacement")
                            if pattern and replacement is not None:
                                replacements.append((str(pattern), str(replacement)))
            except json.JSONDecodeError:
                pass
        compiled: list[tuple[re.Pattern[str], str]] = []
        for pattern, replacement in replacements:
            try:
                compiled.append((re.compile(pattern, flags=re.IGNORECASE), replacement))
            except re.error:
                continue
        return compiled

    def _normalize_arabic_text(self, text: str) -> str:
        text = _ARABIC_DIACRITICS_RE.sub("", text)
        text = text.replace("ـ", "")
        text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
        text = text.replace("ى", "ي")
        return text

    def _normalize_currency_tokens(self, text: str) -> str:
        text = re.sub(r"\bE\s*G\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*G\s*F\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*6\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*B\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bB\s*G\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bEGF\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE6P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bEBP\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bBGP\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bL\.?\s*E\.?\b", "EGP", text, flags=re.IGNORECASE)
        return text

    def _normalize_currency_spacing(self, text: str) -> str:
        if not self.ocr_currency_spacing_enabled:
            return text
        return re.sub(r"\bEGP(?=\d)", "EGP ", text)

    def _normalize_percent_spacing(self, text: str) -> str:
        if not self.ocr_percent_space_fix_enabled:
            return text
        def _fix(match: re.Match[str]) -> str:
            whole = match.group(1)
            frac = match.group(2)
            return f"{whole}.{frac}%"
        text = re.sub(r"\b(\d)\s+(\d{1,2})\s*%", _fix, text)
        text = re.sub(r"\b(\d{1,3})\s*%", r"\1%", text)
        text = re.sub(r"%\s*%+", "%", text)
        return text

    def _normalize_percent_sanity(self, text: str) -> str:
        if not self.ocr_percent_fix_enabled:
            return text
        max_val = self.ocr_percent_sanity_max
        if max_val <= 0:
            return text
        def _fix(match: re.Match[str]) -> str:
            raw = match.group(1)
            try:
                value = float(raw)
            except ValueError:
                return match.group(0)
            if value <= max_val or value >= 1000:
                return match.group(0)
            fixed = value / 100.0
            rendered = f"{fixed:.2f}".rstrip("0").rstrip(".")
            return f"{rendered}%"
        return re.sub(r"\b(\d{2,3})\s*%", _fix, text)

    def _normalize_ocr_text(self, text: str) -> str:
        if not self.ocr_normalization_enabled:
            return text
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ")
        text = self._normalize_currency_tokens(text)
        text = self._normalize_currency_spacing(text)
        text = self._normalize_percent_spacing(text)
        for pattern, replacement in self.ocr_word_replacements:
            text = pattern.sub(lambda m: self._match_case(replacement, m.group(0)), text)
        if _ARABIC_CHAR_RE.search(text):
            text = self._normalize_arabic_text(text)
        text = self._normalize_percent_sanity(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _table_cell_text(self, value: Any) -> str:
        text = KnowledgeIngestionService._sanitize_text(value)
        text = text.replace("\t", " ").replace("|", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return self._normalize_ocr_text(text)

    def _table_header_labels_for_model(
        self,
        table: KnowledgeUploadTable,
        raw_schema: Sequence[str],
    ) -> list[str]:
        header_row = None
        for row in table.rows.all():
            if (row.metadata or {}).get("row_type") == "header":
                header_row = row
                break
        labels: list[str] = []
        if header_row:
            header_cells = sorted(list(header_row.cells.all()), key=lambda c: c.column_index)
            max_len = max(len(raw_schema), len(header_cells))
            for idx in range(max_len):
                if idx < len(header_cells):
                    label = header_cells[idx].raw_text
                elif idx < len(raw_schema):
                    label = raw_schema[idx]
                else:
                    label = f"column_{idx + 1}"
                cleaned = self._table_cell_text(label)
                labels.append(cleaned or (raw_schema[idx] if idx < len(raw_schema) else f"column_{idx + 1}"))
        else:
            for idx, col in enumerate(raw_schema):
                label = self._table_cell_text(col or f"column_{idx + 1}")
                labels.append(label or f"column_{idx + 1}")
        return labels

    def _table_column_map_for_model(
        self,
        header_labels: Sequence[str],
        raw_schema: Sequence[str],
        rules: Mapping[str, Any],
    ) -> tuple[list[tuple[str, str, int]], list[str]]:
        column_map: list[tuple[str, str, int]] = []
        hidden_columns: list[str] = []
        max_len = max(len(header_labels), len(raw_schema))
        for idx in range(max_len):
            label = header_labels[idx] if idx < len(header_labels) else ""
            if not label and idx < len(raw_schema):
                label = raw_schema[idx]
            label = self._table_cell_text(label) or f"column_{idx + 1}"
            if self._column_is_sensitive(label, rules):
                hidden_columns.append(label)
                continue
            canonical = self._canonical_column_name(label, f"column_{idx + 1}")
            column_map.append((label, canonical, idx))
        return column_map, hidden_columns

    def _table_parent_markdown_from_model(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        raw_schema: Sequence[str],
        privacy_rules: Mapping[str, Any],
        max_rows: int,
        max_chars: int,
    ) -> tuple[str, bool]:
        preface: list[str] = []
        if table.section_heading:
            preface.append(f"[Section] {table.section_heading}")
        title = table.title or f"Table {table.order_index}"
        preface.append(f"[Table] {title}")

        headers = [entry[0] for entry in column_map]
        if not headers:
            return "", False

        lines = list(preface)
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        char_count = sum(len(line) + 1 for line in lines)

        data_rows = 0
        truncated = False
        for row in table.rows.all():
            if (row.metadata or {}).get("row_type") == "header":
                continue
            row_attributes = self._row_model_attributes(row, raw_schema)
            if self._row_is_internal(row_attributes, privacy_rules):
                continue
            cell_lookup = {cell.column_index: cell.raw_text for cell in row.cells.all()}
            values = [self._table_cell_text(cell_lookup.get(idx, "")) for _, _, idx in column_map]
            line = "| " + " | ".join(values) + " |"
            if max_rows and data_rows >= max_rows:
                truncated = True
                break
            if max_chars and (char_count + len(line) + 1) > max_chars:
                truncated = True
                break
            lines.append(line)
            char_count += len(line) + 1
            data_rows += 1

        if truncated:
            lines.append("[Table truncated]")
        return "\n".join(lines).strip(), truncated

    def _table_row_chunk_payloads(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        raw_schema: Sequence[str],
        privacy_rules: Mapping[str, Any],
        base_metadata: Mapping[str, Any],
        max_rows: int,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        title = table.title or f"Table {table.order_index}"
        data_rows = 0
        for row in table.rows.all():
            if (row.metadata or {}).get("row_type") == "header":
                continue
            if max_rows and data_rows >= max_rows:
                break
            row_attributes = self._row_model_attributes(row, raw_schema)
            if self._row_is_internal(row_attributes, privacy_rules):
                continue
            cell_lookup = {cell.column_index: cell.raw_text for cell in row.cells.all()}
            pairs: list[str] = []
            for label, _, idx in column_map:
                value = self._table_cell_text(cell_lookup.get(idx, ""))
                if value:
                    pairs.append(f"{label}: {value}")
            if not pairs:
                continue
            preface = []
            if table.section_heading:
                preface.append(f"[Section] {table.section_heading}")
            preface.append(f"[Table] {title}")
            preface.append(f"[Row] {row.row_index}")
            text = "\n".join(preface + pairs)
            row_meta = dict(base_metadata)
            row_meta.update(
                {
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "is_table_preview": False,
                    "table_row_index": row.row_index,
                }
            )
            payloads.append({"text": text, "metadata": row_meta})
            data_rows += 1
        return payloads

    def _table_preview_text(
        self,
        tables: Sequence[TablePayload],
        *,
        row_limit: int = 5,
        rules: Mapping[str, Any] | None = None,
    ) -> str:
        if not tables:
            return ""
        lines: list[str] = []
        for table in tables:
            visible_columns = table.column_schema
            if rules:
                visible_columns = [col for col in table.column_schema if not self._column_is_sensitive(col, rules)]
            if not visible_columns:
                continue
            if table.title:
                lines.append(f"[Table] {table.title}")
            header_line = "\t".join(visible_columns)
            if header_line.strip():
                lines.append(header_line)
            for row in table.rows[:row_limit]:
                attributes = self._row_attributes_from_table(row, table.column_schema)
                if rules and self._row_is_internal(attributes, rules):
                    continue
                values = [attributes.get(column, "") for column in visible_columns]
                if any(values):
                    lines.append("\t".join(values))
            lines.append("")
        return "\n".join(lines).strip()

    def _table_row_entities(
        self,
        tables: Sequence[TablePayload],
        *,
        business_profile=None,
        upload: KnowledgeUpload | None = None,
    ) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        business_name = getattr(business_profile, "name", None)
        alias_hygiene = False
        if upload and getattr(upload, "business_profile", None):
            alias_hygiene = FeatureFlagService.snapshot(upload.business_profile).rag_alias_hygiene
        rules = self._table_privacy_rules(upload)
        for table_idx, table in enumerate(tables):
            entity_type = self._derive_table_entity_type(table, table_idx)
            column_schema = table.column_schema or []
            for row in table.rows:
                entity_index = len(entities)
                attributes = self._row_attributes_from_table(row, column_schema)
                if self._row_is_internal(attributes, rules):
                    continue
                visible_columns = [
                    column for column in column_schema if not self._column_is_sensitive(column, rules)
                ]
                if not visible_columns:
                    continue
                limited_attributes = {column: attributes.get(column, "") for column in visible_columns}
                entity_name = self._infer_table_row_entity_name(
                    entity_type,
                    table,
                    limited_attributes,
                    row.row_index,
                )
                flattened = dict(limited_attributes)
                flattened["table_title"] = table.title or ""
                flattened["sheet_name"] = (table.metadata or {}).get("sheet_name", "")
                flattened["table_order_index"] = str(table.order_index)
                columns = self._select_entity_columns(limited_attributes)
                if not columns:
                    columns = list(limited_attributes.keys())
                limited_attributes = {column: limited_attributes.get(column, "") for column in columns}
                aliases, alias_sources = self._collect_aliases_from_record(
                    record=flattened,
                    flattened=flattened,
                    attributes=limited_attributes,
                    entity_name=entity_name,
                    alias_hygiene=alias_hygiene,
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
        candidate = (
            (table.metadata or {}).get("entity_type")
            or (table.metadata or {}).get("sheet_name")
            or table.section_heading
            or table.title
            or f"table_{index + 1}"
        )
        normalized = re.sub(r"[^a-z0-9]+", "_", (candidate or "").lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized or "table_row"

    def _row_attributes_from_table(
        self,
        row: TableRowPayload,
        column_schema: Sequence[str],
    ) -> dict[str, str]:
        attributes: dict[str, str] = {}
        for cell in row.cells:
            key = cell.column_key or (column_schema[cell.column_index] if cell.column_index < len(column_schema) else "")
            key = key.strip() if isinstance(key, str) else ""
            if not key:
                key = f"column_{cell.column_index + 1}"
            value = (cell.raw_text or "").strip()
            if value:
                attributes[key] = value
        # include columns with no explicit cell entry to preserve schema ordering
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
        attributes: dict[str, str] = {}
        schema = list(column_schema)
        for cell in row.cells.all():
            column = cell.column_key or (schema[cell.column_index] if cell.column_index < len(schema) else "")
            key = column or f"column_{cell.column_index + 1}"
            attributes[key] = (cell.raw_text or "").strip()
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
        for column in table.column_schema:
            if not column:
                continue
            value = attributes.get(column)
            if value:
                return value
        for value in attributes.values():
            if value:
                return value
        label = entity_type.replace("_", " ").title() or "Row"
        return f"{label} {row_index}"

    @staticmethod
    def _infer_entity_name(record_label: str, record: Mapping[str, Any], flattened: Mapping[str, str], index: int) -> str:
        candidate_keys = ("name", "title", "destination", "city", "label", "slug")
        for key in candidate_keys:
            value = record.get(key) if isinstance(record, Mapping) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
            if key in flattened and flattened[key]:
                return flattened[key]
        fallback = record.get("id") if isinstance(record, Mapping) else None
        if fallback:
            return str(fallback)
        label = record_label.rstrip("s") or record_label or "record"
        return f"{label.title()} {index + 1}"

    @staticmethod
    def _select_entity_columns(flattened: Mapping[str, str]) -> list[str]:
        if not flattened:
            return []
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
        for key in preferred:
            if key in flattened and key not in seen and flattened[key]:
                columns.append(key)
                seen.add(key)
        for key, value in flattened.items():
            if key in seen:
                continue
            if value:
                columns.append(key)
                seen.add(key)
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
        lines = [f"{entity_type.title()}: {entity_title}"]
        for column in column_schema[:8]:
            value = attributes.get(column)
            if value:
                lines.append(f"- {column}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _looks_like_identifier(candidate: str) -> bool:
        if not candidate:
            return False
        token = candidate.strip()
        if not token:
            return False
        lowered = token.lower()
        if len(lowered) >= ALIAS_MIN_LENGTH:
            return True
        if len(lowered) >= ALIAS_SYMBOL_MIN_LENGTH and any(ch in "-_0123456789" for ch in lowered):
            return True
        return bool(IDENTIFIER_TOKEN_PATTERN.fullmatch(lowered))

    @staticmethod
    def _is_noisy_identifier(candidate: str) -> bool:
        if not candidate:
            return False
        token = candidate.strip()
        if not token:
            return False
        if DATE_TOKEN_PATTERN.search(token):
            return True
        if re.fullmatch(r"[\d\s\-]+", token):
            digits = re.sub(r"\D", "", token)
            if 13 <= len(digits) <= 19:
                return True
        return False

    @staticmethod
    def _normalize_alias_value(value: str) -> str:
        if not value:
            return ""
        normalized = re.sub(r"\s+", "-", value.strip().lower())
        normalized = re.sub(r"-{2,}", "-", normalized)
        normalized = normalized.strip("-")
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
        alias_hygiene: bool = False,
    ) -> tuple[list[str], set[str]]:
        alias_candidates: list[str] = []
        alias_sources: set[str] = set()
        seen: set[str] = set()

        def maybe_add(value: Any, source: str) -> None:
            if not isinstance(value, str):
                return
            candidate = value.strip()
            if not candidate:
                return
            if len(candidate) > ALIAS_MAX_LENGTH:
                candidate = candidate[:ALIAS_MAX_LENGTH]
            if alias_hygiene and KnowledgeIngestionService._is_noisy_identifier(candidate):
                return
            if not KnowledgeIngestionService._looks_like_identifier(candidate):
                return
            lowered = candidate.lower()
            if lowered in seen:
                return
            seen.add(lowered)
            alias_candidates.append(candidate)
            alias_sources.add(source)

        maybe_add(entity_name, "entity_name")
        for key in ALIAS_KEYWORDS:
            maybe_add(record.get(key), f"record_{key}")
        for key, value in flattened.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"flattened_{key}")
        for key, value in attributes.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"attribute_{key}")
        # Include values that look like identifiers even if the key didn't match
        for value in flattened.values():
            if isinstance(value, str) and KnowledgeIngestionService._looks_like_identifier(value):
                maybe_add(value, "inline_pattern")
        return alias_candidates[:8], alias_sources

    @staticmethod
    def _alias_metadata(aliases: Sequence[str]) -> dict[str, Any]:
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
                continue
            seen.add(lower)
            normalized.append(trimmed)
        if not normalized:
            return {}
        alias_string = " ".join(sorted(seen))
        return {"aliases": normalized, "alias_string": alias_string}

    @staticmethod
    def _append_identifier_line(text: str, aliases: Sequence[str]) -> str:
        alias_list = [alias for alias in aliases if alias]
        if not alias_list:
            return text
        if "Identifiers:" in text:
            return text
        suffix = "Identifiers: " + ", ".join(alias_list[:6])
        return f"{text.rstrip()}\n{suffix}"

    def _extract_inline_identifiers(self, text: str, *, alias_hygiene: bool = False) -> list[str]:
        if not text:
            return []
        aliases: list[str] = []
        seen: set[str] = set()
        for match in IDENTIFIER_TOKEN_PATTERN.finditer(text.lower()):
            alias = match.group().strip()
            if not alias:
                continue
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            if alias_hygiene and self._is_noisy_identifier(alias):
                continue
            if not self._looks_like_identifier(alias):
                continue
            if alias not in seen:
                seen.add(alias)
                aliases.append(alias)
        for match in ID_LINE_PATTERN.finditer(text):
            alias = match.group(1).strip()
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            alias_lower = alias.lower()
            if alias_hygiene and self._is_noisy_identifier(alias):
                continue
            if alias_lower and alias_lower not in seen and self._looks_like_identifier(alias):
                seen.add(alias_lower)
                aliases.append(alias)
        return aliases[:6]

    def _inject_identifiers_into_text(
        self,
        text: str,
        *,
        alias_hygiene: bool = False,
    ) -> tuple[str, list[str]]:
        aliases = self._extract_inline_identifiers(text, alias_hygiene=alias_hygiene)
        if aliases:
            text = self._append_identifier_line(text, aliases)
        return text, aliases

    @staticmethod
    def _finalize_alias_metadata(metadata: dict[str, Any]) -> None:
        aliases = metadata.get("aliases")
        if not aliases:
            metadata.pop("alias_string", None)
            return
        normalized: list[str] = []
        seen: set[str] = set()
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
                continue
            seen.add(lower)
            normalized.append(trimmed)
        metadata["aliases"] = normalized
        metadata["alias_string"] = " ".join(sorted(seen))

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
        # Collapse runs of spaces only; keep tabs intact for TSV
        text = re.sub(r"[ ]{2,}", " ", text)
        # Do NOT touch \t
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _sanitize_text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        if "\x00" in text:
            return text.replace("\x00", " ")
        return text

    @staticmethod
    def _clamp_text(value: Any, max_length: int) -> str:
        text = KnowledgeIngestionService._sanitize_text(value)
        if max_length <= 0:
            return text
        if len(text) > max_length:
            return text[:max_length]
        return text

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
