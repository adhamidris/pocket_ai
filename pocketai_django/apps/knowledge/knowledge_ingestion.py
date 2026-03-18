from __future__ import annotations

from collections import Counter, deque
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
from datetime import datetime, timedelta, timezone as datetime_timezone
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Min, Prefetch, Q, Value, When
from django.utils import timezone
from django.utils.text import slugify
from core.otel import otel_trace
import requests

from apps.accounts.models import (
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeIssueSeverity,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeVisibility,
    KnowledgeBlockType,
)
from apps.knowledge.models import (
    KnowledgeIngestionJob,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadShadowChunk,
    KnowledgeUploadFile,
    KnowledgeUploadText,
    KnowledgeUploadIssue,
    KnowledgeUploadPage,
    KnowledgeUploadPageBlock,
    KnowledgeUploadTable,
    KnowledgeTableColumn,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
    KnowledgeEntity,
    KnowledgeAlias,
)
from apps.knowledge.lexicon_learning import TenantLexiconAutoLearningService
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
from apps.rag.quality_monitor import QualityMonitor
from apps.rag.rag_logging import structured_log
from apps.rag.table_semantics import normalize_column_name
from core.tenancy import tenant_context
from apps.core.logging_utils import log_start, log_success, log_progress, log_warning, log_error, LogEmoji
from apps.knowledge.table_normalization import (
    NormalizedSheet,
    SpreadsheetRowInput,
    SheetNormalizationDiagnostics,
    normalize_sheet_rows,
    resolve_normalization_policy,
    sheet_is_allowed,
    summarize_normalization,
)
from apps.knowledge.column_role_inference import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_NOTE,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    column_role_groups,
    column_role_payloads,
    infer_column_roles,
    role_lookup_by_index,
)
from apps.knowledge.table_scope_engine import (
    SCOPE_ENGINE_VERSION,
    SCOPE_REASON_ABSTAIN,
    build_scope_table_profile,
    canonical_scope_reason,
    infer_scope_for_row,
)

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

OCR_NORMALIZATION_VERSION = "v2"
TABLE_SCOPE_CONTRACT_VERSION = "v2"
COLUMN_ROLE_INFERENCE_VERSION = "v1"
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u06D6-\u06ED]")
_TABLE_NUMERIC_SIGNAL_TOKEN_RE = re.compile(
    r"(?:%|[$€£¥₹]|(?:^|[\s(])(?:USD|EUR|GBP|JPY|CHF|AUD|CAD|CNY|INR|SAR|AED|EGP|QAR|KWD|OMR|BHD|TRY|ZAR)(?:$|[\s):,.;]))",
    flags=re.IGNORECASE,
)
_TABLE_NUMBER_LIKE_RE = re.compile(r"[+-]?\d[\d,]*(?:[.:]\d+)?")
_TABLE_DATE_TIME_LIKE_RE = re.compile(r"\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
_TABLE_NUMBER_WITH_UNIT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:[a-zA-Z]{1,5}|%)\b")
_TABLE_ROW_VALUE_KEYWORD_RE = re.compile(
    r"\b(?:free|discount|waived?|commission|fee|fees|charge|charges|min(?:imum)?|max(?:imum)?|equivalent)\b",
    flags=re.IGNORECASE,
)
_SPREADSHEET_INSTRUCTION_SHEET_RE = re.compile(r"\b(?:instructions?|guidance|notes?|help)\b", flags=re.IGNORECASE)
_SPREADSHEET_PLACEHOLDER_CELL_RE = re.compile(
    r"^\s*(?:select\b|insert\b)\s*",
    flags=re.IGNORECASE,
)
_SPREADSHEET_CONTROL_CELL_RE = re.compile(r"^(?:yes|no|true|false|n/?a|none)$", flags=re.IGNORECASE)
_SPREADSHEET_MASKED_PLACEHOLDER_RE = re.compile(r"^#{4,}$")
_SPREADSHEET_SUMMARY_ROW_RE = re.compile(
    r"\b(?:total|totals|summary|grand total)\b",
    flags=re.IGNORECASE,
)
_SPREADSHEET_ZERO_LIKE_RE = re.compile(r"^(?:0|0\.0+|0%)$")
_SPREADSHEET_PURE_NUMBER_RE = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?%?$")
_SPREADSHEET_RECORD_ID_RE = re.compile(r"^[A-Za-z]{1,8}-\d+[A-Za-z0-9-]*$")
_SPREADSHEET_REFERENCE_SHEET_RE = re.compile(
    r"\b(?:lists?|lookup|lookups|options?|choices|reference|references|validation)\b",
    flags=re.IGNORECASE,
)
_SPREADSHEET_INSTRUCTION_TOKEN_RE = re.compile(r"\b(?:instructions?|guidance|notes?|comment|comments?)\b", flags=re.IGNORECASE)


def _column_numeric_signal(text: str) -> bool:
    sample = str(text or "").strip()
    if not sample:
        return False
    return bool(
        _TABLE_NUMERIC_SIGNAL_TOKEN_RE.search(sample)
        or _TABLE_NUMBER_LIKE_RE.search(sample)
        or _TABLE_NUMBER_WITH_UNIT_RE.search(sample)
    )


def _normalize_cell_for_stats(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _infer_contextual_column_indices(
    *,
    row_values: Sequence[Sequence[str]],
    header_row_indices: set[int] | None = None,
    min_segment_columns: int = 3,
) -> set[int]:
    """
    Infer descriptor/context columns using structural signals only.

    This avoids header-name dependence so the behavior generalizes across tenants
    and domains with different column labels.
    """
    rows = list(row_values or [])
    if not rows:
        return set()

    max_cols = max((len(row) for row in rows), default=0)
    if max_cols <= 0:
        return set()

    header_set = set(header_row_indices or set())
    data_rows = [row for idx, row in enumerate(rows) if idx not in header_set]
    if not data_rows:
        data_rows = rows
    data_count = max(1, len(data_rows))

    candidates: set[int] = set()
    profiles: list[dict[str, float]] = []
    for col_idx in range(max_cols):
        values: list[str] = []
        for row in data_rows:
            value = _normalize_cell_for_stats(row[col_idx] if col_idx < len(row) else "")
            if value:
                values.append(value)
        non_empty = len(values)
        if non_empty <= 0:
            profiles.append(
                {
                    "non_empty": 0.0,
                    "non_empty_ratio": 0.0,
                    "numeric_ratio": 0.0,
                    "unique_ratio": 0.0,
                    "long_ratio": 0.0,
                    "avg_chars": 0.0,
                }
            )
            continue

        normalized = [value.lower() for value in values]
        unique_ratio = float(len(set(normalized))) / float(non_empty)
        numeric_ratio = float(sum(1 for value in values if _column_numeric_signal(value))) / float(non_empty)
        long_ratio = float(sum(1 for value in values if len(value) >= 18 or len(value.split()) >= 4)) / float(non_empty)
        avg_chars = float(sum(len(value) for value in values)) / float(non_empty)
        non_empty_ratio = float(non_empty) / float(data_count)

        descriptor_score = 0.0
        if unique_ratio >= 0.68:
            descriptor_score += 1.0
        if long_ratio >= 0.35 or avg_chars >= 16.0:
            descriptor_score += 1.0
        if numeric_ratio <= 0.35:
            descriptor_score += 1.0
        if non_empty_ratio >= 0.5:
            descriptor_score += 0.5

        value_score = 0.0
        if numeric_ratio >= 0.45:
            value_score += 1.0
        if avg_chars <= 14.0:
            value_score += 0.5
        if unique_ratio <= 0.6:
            value_score += 0.5

        if non_empty >= max(2, int(round(0.2 * data_count))) and descriptor_score >= 2.0 and descriptor_score > value_score:
            candidates.add(col_idx)

        profiles.append(
            {
                "non_empty": float(non_empty),
                "non_empty_ratio": non_empty_ratio,
                "numeric_ratio": numeric_ratio,
                "unique_ratio": unique_ratio,
                "long_ratio": long_ratio,
                "avg_chars": avg_chars,
            }
        )

    contextual: set[int] = set()
    for idx in range(max_cols):
        if idx in candidates:
            contextual.add(idx)
        else:
            break

    # Fallback: at least recognize a dominant descriptor first column.
    if not contextual and profiles:
        first = profiles[0]
        if (
            first.get("non_empty", 0.0) >= 2.0
            and first.get("avg_chars", 0.0) >= 18.0
            and first.get("unique_ratio", 0.0) >= 0.7
            and first.get("numeric_ratio", 0.0) <= 0.25
        ):
            contextual.add(0)
            if len(profiles) > 1:
                second = profiles[1]
                if (
                    second.get("non_empty", 0.0) >= 2.0
                    and second.get("avg_chars", 0.0) >= 14.0
                    and second.get("unique_ratio", 0.0) >= 0.6
                    and second.get("numeric_ratio", 0.0) <= 0.35
                ):
                    contextual.add(1)

    if len(contextual) >= max_cols:
        contextual = set()

    contextual_sorted = sorted(contextual)
    while max_cols - len(contextual_sorted) < max(1, int(min_segment_columns)) and contextual_sorted:
        contextual_sorted.pop()
    return set(contextual_sorted)


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
    render_dpi: int = 200,
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
        render_dpi=render_dpi,
        ocr_callable=ocr_callable,
    )

SUPPORTED_SOURCE_TYPES = {
    KnowledgeSourceType.FILE,
    KnowledgeSourceType.LINK,
    KnowledgeSourceType.TEXT,
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
        render_dpi: int = 200,
        ocr_callable: Callable[[bytes], str] | None = None,
    ):
        self.density_threshold = density_threshold
        try:
            dpi = int(render_dpi)
        except (TypeError, ValueError):
            dpi = 200
        self.render_dpi = max(72, min(600, dpi))
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
            try:
                pixmap = page.get_pixmap(dpi=self.render_dpi, alpha=False)  # type: ignore[attr-defined]
            except TypeError:
                pixmap = page.get_pixmap(alpha=False)  # type: ignore[attr-defined]
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
            raw_char_count = len(plain_text.strip())
            rect = page.rect
            area = max(rect.width * rect.height, 1.0)
            density = raw_char_count / area
            
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
            
            final_text = reconciled_text or ""
            final_char_count = len(final_text.strip())
            final_density = final_char_count / area if final_char_count else 0.0
            
            fragments.append(final_text)
            
            # Build structured blocks (keep your existing logic)
            blocks = self._build_pdf_blocks(page, index)
            if has_ocr and final_text.strip() and not any((block.text or "").strip() for block in blocks):
                blocks = [
                    PageBlockPayload(
                        block_type=KnowledgeBlockType.PARAGRAPH,
                        order_index=0,
                        text=final_text,
                        metadata={"source": "ocr"},
                    )
                ]
            
            pages.append(
                PageLayout(
                    page_number=index,
                    width=float(rect.width),
                    height=float(rect.height),
                    rotation=int(page.rotation or 0),
                    text_density=final_density,
                    has_ocr_content=has_ocr,
                    content_type="application/pdf",
                    blocks=blocks,
                    metadata={
                        "char_count": final_char_count,
                        "raw_char_count": raw_char_count,
                        "raw_text_density": density,
                        "ocr_render_dpi": getattr(ocr, "render_dpi", None) if has_ocr else None,
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
    _RETRYABLE_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
    _THROTTLE_HTTP_STATUS = {429, 503}

    def __init__(
        self,
        *,
        endpoint: str | None,
        key: str | None,
        model: str = "prebuilt-layout",
        api_version: str = "2024-11-30",
        base_path: str = "formrecognizer",
        locale: str | None = None,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 1.5,
        max_polls: int = 40,
        request_max_attempts: int = 3,
        poll_request_max_attempts: int = 3,
        retry_backoff_base_seconds: float = 1.0,
        retry_backoff_max_seconds: float = 8.0,
        max_retry_after_seconds: float = 30.0,
    ) -> None:
        self.endpoint = (endpoint or "").rstrip("/")
        self.key = (key or "").strip()
        self.model = (model or "prebuilt-layout").strip()
        self.api_version = (api_version or "2024-11-30").strip()
        self.base_path = (base_path or "formrecognizer").strip().strip("/")
        self.locale = (locale or "").strip()
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        # Azure DI guidance recommends spacing status polls (avoid rapid polling loops).
        self.poll_interval_seconds = max(2.0, float(poll_interval_seconds))
        self.max_polls = max(5, int(max_polls))
        self.request_max_attempts = max(1, int(request_max_attempts))
        self.poll_request_max_attempts = max(1, int(poll_request_max_attempts))
        self.retry_backoff_base_seconds = max(0.1, float(retry_backoff_base_seconds))
        self.retry_backoff_max_seconds = max(
            self.retry_backoff_base_seconds,
            float(retry_backoff_max_seconds),
        )
        self.max_retry_after_seconds = max(
            self.retry_backoff_base_seconds,
            float(max_retry_after_seconds),
        )

    @staticmethod
    def _polygon_to_bbox(polygon: Sequence[Any]) -> dict[str, float]:
        xs: list[float] = []
        ys: list[float] = []
        
        # Handle flat array format: [x1, y1, x2, y2, x3, y3, x4, y4]
        if polygon and isinstance(polygon, (list, tuple)) and all(isinstance(p, (int, float)) for p in polygon):
            # Flat array of coordinates - pair them up
            for i in range(0, len(polygon), 2):
                if i + 1 < len(polygon):
                    try:
                        xs.append(float(polygon[i]))
                        ys.append(float(polygon[i + 1]))
                    except (TypeError, ValueError):
                        continue
        else:
            # Dict format [{x, y}] or nested array [[x, y]]
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
    def _bbox_from_regions(
        regions: Sequence[Mapping[str, Any]] | None,
        *,
        page_unit_scale: Mapping[int, float] | None = None,
    ) -> tuple[int | None, dict[str, float]]:
        if not regions:
            return None, {}
        first = regions[0] if regions else {}
        page_number = first.get("pageNumber")
        polygon = first.get("polygon") or first.get("boundingPolygon") or []
        bbox = AzureDocumentIntelligenceExtractor._polygon_to_bbox(polygon)
        try:
            page_number = int(page_number) if page_number is not None else None
        except (TypeError, ValueError):
            page_number = None
        if bbox and page_unit_scale and page_number is not None:
            try:
                scale = float(page_unit_scale.get(page_number, 1.0) or 1.0)
            except (TypeError, ValueError):
                scale = 1.0
            if scale != 1.0:
                bbox = {
                    "x0": float(bbox.get("x0", 0.0)) * scale,
                    "y0": float(bbox.get("y0", 0.0)) * scale,
                    "x1": float(bbox.get("x1", 0.0)) * scale,
                    "y1": float(bbox.get("y1", 0.0)) * scale,
                }
        return page_number, bbox

    @staticmethod
    def _normalized_cell_text(value: str | None) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).lower()

    @staticmethod
    def _caption_text(caption: Any) -> str:
        if isinstance(caption, str):
            return caption.strip()
        if isinstance(caption, Mapping):
            content = caption.get("content")
            if isinstance(content, str):
                return content.strip()
            text = caption.get("text")
            if isinstance(text, str):
                return text.strip()
            return ""
        return str(caption or "").strip()

    @staticmethod
    def _infer_segment_indices_from_structure(
        *,
        table_rows: Sequence[TableRowPayload],
        column_schema: Sequence[str],
        header_rows: set[int],
    ) -> list[int]:
        width = len(column_schema)
        if width <= 0:
            return []
        if not table_rows:
            return list(range(width))

        row_values: list[list[str]] = []
        header_row_positions: set[int] = set()
        for pos, row in enumerate(table_rows):
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            row_type = str(row_meta.get("row_type") or "").strip().lower()
            if row.row_index in header_rows or row_type == "header":
                header_row_positions.add(pos)
            values: list[str] = []
            for idx in range(width):
                if idx < len(row.cells):
                    values.append(str(row.cells[idx].raw_text or ""))
                else:
                    values.append("")
            row_values.append(values)

        role_profiles = infer_column_roles(
            row_values=row_values,
            column_schema=[str(col or "") for col in column_schema],
            header_row_indices=header_row_positions,
            min_scope_columns=3,
        )
        role_groups = column_role_groups(role_profiles)
        scope_indices = set(role_groups.get(COLUMN_ROLE_SCOPE_DIMENSION, []))

        # Keep near-scope qualifiers when their scope score is close enough.
        # This prevents sparse-table edge cases from dropping a true segment
        # column that received a qualifier label due low support.
        for profile in role_profiles:
            role_scores = profile.role_scores if isinstance(profile.role_scores, Mapping) else {}
            scope_score = float(role_scores.get(COLUMN_ROLE_SCOPE_DIMENSION) or 0.0)
            qualifier_score = float(role_scores.get(COLUMN_ROLE_QUALIFIER) or 0.0)
            if (
                profile.role == COLUMN_ROLE_QUALIFIER
                and scope_score >= 0.46
                and (qualifier_score - scope_score) <= 0.2
            ):
                scope_indices.add(int(profile.column_index))

        profile_by_index = {
            int(profile.column_index): profile
            for profile in role_profiles
        }

        def _extend_sparse_scope_tail(base_scope: set[int]) -> set[int]:
            if not base_scope:
                return base_scope
            max_scope = max(base_scope)
            extended = set(base_scope)
            # Preserve sparse right-edge scope dimensions (e.g., "private") that
            # have real value participation but can be misclassified as note due to
            # low density and broad note rows elsewhere in the table.
            for idx in range(max_scope + 1, width):
                profile = profile_by_index.get(idx)
                if profile is None:
                    break
                if profile.role == COLUMN_ROLE_DESCRIPTOR:
                    break
                role_scores = profile.role_scores if isinstance(profile.role_scores, Mapping) else {}
                signals = profile.signals if isinstance(profile.signals, Mapping) else {}
                label = str(column_schema[idx] or f"column_{idx + 1}").strip().lower()
                tokens = [t for t in re.split(r"[^a-z0-9]+", label) if t]
                if not tokens or len(tokens) > 4:
                    break
                if any(tok in {"note", "notes", "remark", "remarks", "comment", "comments", "details"} for tok in tokens):
                    break

                non_empty_count = int(signals.get("non_empty_count") or 0)
                non_empty_ratio = float(signals.get("non_empty_ratio") or 0.0)
                avg_chars = float(signals.get("avg_chars") or 0.0)
                unique_count = int(signals.get("unique_count") or 0)
                scope_score = float(role_scores.get(COLUMN_ROLE_SCOPE_DIMENSION) or 0.0)

                if non_empty_count < 2 or non_empty_ratio < 0.05:
                    break
                if avg_chars > 40.0 and unique_count <= 2 and scope_score < 0.25:
                    break

                extended.add(idx)
            return extended

        resolved_scope_indices = sorted(scope_indices)
        if len(resolved_scope_indices) >= 3:
            min_scope = min(resolved_scope_indices)
            max_scope = max(resolved_scope_indices)
            bridged_scope = set(resolved_scope_indices)
            for profile in role_profiles:
                idx = int(profile.column_index)
                if idx <= min_scope or idx >= max_scope:
                    continue
                if profile.role in {COLUMN_ROLE_DESCRIPTOR, COLUMN_ROLE_NOTE}:
                    continue
                # If a non-descriptor column is between two scope-dimension
                # columns, treat it as a bridge to avoid dropping middle
                # segments due classifier noise.
                bridged_scope.add(idx)
            bridged_scope = _extend_sparse_scope_tail(bridged_scope)
            return sorted(bridged_scope)

        contextual_indices = sorted(
            set(role_groups.get(COLUMN_ROLE_DESCRIPTOR, []))
            | {
                int(profile.column_index)
                for profile in role_profiles
                if profile.role == COLUMN_ROLE_QUALIFIER
                and (
                    float((profile.role_scores or {}).get(COLUMN_ROLE_QUALIFIER) or 0.0)
                    - float((profile.role_scores or {}).get(COLUMN_ROLE_SCOPE_DIMENSION) or 0.0)
                ) >= 0.25
            }
        )
        fallback = [idx for idx in range(width) if idx not in contextual_indices]
        fallback = sorted(_extend_sparse_scope_tail(set(fallback)))
        if len(fallback) >= 3:
            return fallback
        return resolved_scope_indices or fallback

    def _reconcile_spans_from_geometry(
        self,
        *,
        grid: list[list[str]],
        cell_lookup: dict[tuple[int, int], dict[str, Any]],
        header_rows: set[int],
        row_count: int,
        col_count: int,
        page_unit_scale: Mapping[int, float] | None = None,
    ) -> None:
        """
        Compare data-cell bounding boxes against header-cell bounding boxes
        to detect true column spans that Azure DI did not report via columnSpan.

        Mutates *grid* and *cell_lookup* in place: when a data cell's
        horizontal extent overlaps N header columns but column_span == 1,
        the value is duplicated across those columns and column_span is
        updated.  This runs before frozen TableCellPayload objects are built.
        """
        if col_count < 3 or not header_rows:
            return

        # Build column x-boundaries from header cell bounding boxes.
        header_row_idx = min(header_rows)
        col_boundaries: list[tuple[float, float]] = []  # (x0, x1) per column
        for c in range(col_count):
            meta = cell_lookup.get((header_row_idx, c))
            if not meta:
                col_boundaries.append((0.0, 0.0))
                continue
            _page, bbox = self._bbox_from_regions(
                meta.get("regions"),
                page_unit_scale=page_unit_scale,
            )
            if bbox and bbox.get("x0", 0.0) < bbox.get("x1", 0.0):
                col_boundaries.append((float(bbox["x0"]), float(bbox["x1"])))
            else:
                col_boundaries.append((0.0, 0.0))

        # Need at least 3 valid column boundaries to make geometric decisions.
        valid_boundaries = [(x0, x1) for x0, x1 in col_boundaries if x1 > x0]
        if len(valid_boundaries) < 3:
            return

        # Use a small tolerance to avoid floating-point near-misses.
        # 15% of median column width is a safe margin.
        widths = [x1 - x0 for x0, x1 in valid_boundaries if (x1 - x0) > 0]
        if not widths:
            return
        sorted_widths = sorted(widths)
        median_width = sorted_widths[len(sorted_widths) // 2]
        tolerance = median_width * 0.15

        for r in range(row_count):
            if r in header_rows:
                continue
            for c in range(col_count):
                value = grid[r][c]
                if not value:
                    continue
                meta = cell_lookup.get((r, c))
                if not meta:
                    continue
                existing_span = int(meta.get("column_span") or 1)
                if existing_span > 1:
                    # Azure DI already reported a span — trust it.
                    continue
                _page, bbox = self._bbox_from_regions(
                    meta.get("regions"),
                    page_unit_scale=page_unit_scale,
                )
                if not bbox or bbox.get("x1", 0.0) <= bbox.get("x0", 0.0):
                    continue
                cell_x0 = float(bbox["x0"])
                cell_x1 = float(bbox["x1"])
                # Find all header columns whose x-range overlaps with this cell.
                overlapping: list[int] = []
                for hc, (hx0, hx1) in enumerate(col_boundaries):
                    if hx1 <= hx0:
                        continue
                    # Two ranges overlap if one starts before the other ends.
                    if cell_x0 < (hx1 - tolerance) and cell_x1 > (hx0 + tolerance):
                        overlapping.append(hc)
                if len(overlapping) <= 1:
                    continue
                # The cell physically spans multiple header columns.
                # Duplicate the value across all overlapped columns and
                # update column_span in cell_lookup.
                span = len(overlapping)
                for oc in overlapping:
                    # Only fill empty targets (or identical values). Never overwrite
                    # an already-populated cell because it likely contains a
                    # per-column value (e.g. "EGP 200") that should trump a
                    # broad-span label.
                    existing_value = grid[r][oc]
                    if existing_value and str(existing_value).strip() and str(existing_value).strip() != str(value).strip():
                        continue
                    grid[r][oc] = value
                    existing_meta = cell_lookup.get((r, oc)) or {}
                    cell_lookup[(r, oc)] = {
                        **existing_meta,
                        "column_span": span,
                        "geometric_span_reconciled": True,
                    }
                    # Preserve the original cell's regions on newly filled cells
                    # so downstream bbox extraction works correctly.
                    if oc != c and "regions" not in existing_meta:
                        cell_lookup[(r, oc)]["regions"] = meta.get("regions") or []

    def _annotate_row_applicability(
        self,
        *,
        table_rows: Sequence[TableRowPayload],
        column_schema: Sequence[str],
        header_rows: set[int],
    ) -> list[TableRowPayload]:
        """
        Infer row-level applicability across peer columns for centered/merged values.

        Azure sometimes anchors a centered value to one interior segment column
        even when visually it applies to a wider segment group.  We use multiple
        signals — explicit column spans, table-level sparse-row patterns, and
        per-row emptiness — to recover the intended multi-column scope.

        Key improvement over v1: instead of requiring a single *dominant* column
        to accumulate most single-value placements (which fails when Azure DI
        scatters values across different columns row-by-row), we count the
        *fraction of data rows that are sparse* (exactly one non-empty segment
        cell).  A high sparse fraction indicates the table uses centered/merged
        values regardless of which column each value landed in.
        """

        rows = list(table_rows or [])
        if not rows or not column_schema:
            return rows

        column_count = len(column_schema)

        def _is_contextual_broad_span_row(row: TableRowPayload) -> bool:
            if row.row_index in header_rows:
                return False
            non_empty_cells: list[TableCellPayload] = []
            normalized_values: set[str] = set()
            has_broad_context_span = False
            for cell in row.cells:
                value = str(cell.raw_text or "").strip()
                if not value:
                    continue
                non_empty_cells.append(cell)
                normalized_values.add(self._normalized_cell_text(value))
                try:
                    col_idx = int(cell.column_index)
                    span_width = int((cell.metadata or {}).get("column_span") or 1)
                except (TypeError, ValueError):
                    continue
                if (
                    span_width >= max(4, column_count - 1)
                    and col_idx <= 1
                ):
                    has_broad_context_span = True
            if not has_broad_context_span:
                # Secondary heuristic: all cells same text (no column_span needed).
                # Covers pdfplumber/heuristic extractors that duplicate the section
                # label into every column instead of reporting a column_span.
                if len(non_empty_cells) >= 4 and len(normalized_values) == 1:
                    return True
                return False
            # A near full-width span carrying one repeated phrase is usually a
            # note/footer row and should not shape scope-axis inference.
            return len(normalized_values) <= 1 or len(non_empty_cells) <= 2

        rows_for_structure = [row for row in rows if not _is_contextual_broad_span_row(row)]
        if not rows_for_structure:
            rows_for_structure = rows

        section_header_indices: set[int] = {
            row.row_index
            for row in rows
            if row.row_index not in header_rows and _is_contextual_broad_span_row(row)
        }

        segment_indices = self._infer_segment_indices_from_structure(
            table_rows=rows_for_structure,
            column_schema=column_schema,
            header_rows=header_rows,
        )
        base_segment_indices = sorted(set(segment_indices))
        base_segment_set = set(base_segment_indices)
        span_evidence_indices: set[int] = set()
        for row in rows:
            if row.row_index in header_rows:
                continue
            for cell in row.cells:
                try:
                    col_idx = int(cell.column_index)
                    span_width = int((cell.metadata or {}).get("column_span") or 1)
                except (TypeError, ValueError):
                    continue
                if span_width <= 1:
                    continue
                span_targets = set(range(col_idx, min(len(column_schema), col_idx + span_width)))
                if len(span_targets) <= 1:
                    continue

                if len(base_segment_set) >= 3:
                    # Keep span evidence constrained to the structurally inferred
                    # scope axis so wide note/footer rows do not pollute scope
                    # dimensions with descriptor or qualifier columns.
                    overlap = sorted(span_targets & base_segment_set)
                    if len(overlap) <= 1:
                        continue
                    span_evidence_indices.update(overlap)
                    continue

                # Bootstrap mode for weak structural inference: reject near
                # full-width spans beginning in contextual columns because these
                # are usually note rows, not scope axes.
                if (
                    len(span_targets) >= max(4, len(column_schema) - 1)
                    and min(span_targets) <= 1
                ):
                    continue
                span_evidence_indices.update(span_targets)
        if span_evidence_indices:
            segment_indices = sorted(set(segment_indices) | span_evidence_indices)
        if len(segment_indices) < 3:
            return rows

        scope_indices = sorted(set(segment_indices))
        scope_set = set(scope_indices)
        data_rows = [row for row in rows if row.row_index not in header_rows]
        if not data_rows:
            return rows

        table_scope_profile = build_scope_table_profile(
            rows=rows,
            scope_indices=scope_indices,
            header_rows=header_rows,
        )
        scope_dimension_labels = [
            str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
            for idx in scope_indices
        ]

        # ── Per-row annotation via deterministic precedence engine ──
        updated_rows: list[TableRowPayload] = []
        for row in rows:
            if row.row_index in header_rows:
                updated_rows.append(row)
                continue

            if row.row_index in section_header_indices:
                row_meta = dict(row.metadata or {})
                row_meta["row_type"] = "section_header"
                updated_rows.append(
                    TableRowPayload(
                        row_index=row.row_index,
                        page_number=row.page_number,
                        bbox=row.bbox,
                        raw_text=row.raw_text,
                        metadata=row_meta,
                        cells=row.cells,
                    )
                )
                continue

            scope_decision = infer_scope_for_row(
                row=row,
                table_profile=table_scope_profile,
            )
            if scope_decision is None:
                updated_rows.append(row)
                continue

            applies_to_indices = [
                idx for idx in scope_decision.applies_to_indices if idx in scope_set
            ]
            detected_indices = [
                idx for idx in scope_decision.detected_indices if idx in scope_set
            ]
            scope_reason = canonical_scope_reason(scope_decision.reason)
            scope_confidence = round(float(scope_decision.confidence), 3)

            applies_to_labels = [
                str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
                for idx in applies_to_indices
            ]
            detected_labels = [
                str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
                for idx in detected_indices
            ]
            observed_value_columns: list[str] = []
            qualifier_columns: list[str] = []
            representative_value = ""
            for cell in row.cells:
                value = str(cell.raw_text or "").strip()
                if not value:
                    continue
                try:
                    idx = int(cell.column_index)
                except (TypeError, ValueError):
                    continue
                if idx < len(column_schema):
                    label = str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
                else:
                    label = f"column_{idx + 1}"
                observed_value_columns.append(label)
                if idx in scope_set and not representative_value:
                    representative_value = re.sub(r"\s+", " ", value).strip()
                if idx not in scope_set:
                    qualifier_columns.append(label)
            observed_value_columns = list(dict.fromkeys(observed_value_columns))
            qualifier_columns = list(dict.fromkeys(qualifier_columns))

            row_meta = dict(row.metadata or {})
            row_meta.update(
                {
                    "table_scope_contract_version": TABLE_SCOPE_CONTRACT_VERSION,
                    "scope_engine_version": SCOPE_ENGINE_VERSION,
                    "observed_value_columns": observed_value_columns,
                    "qualifier_columns": qualifier_columns,
                    "scope_dimension_columns": scope_dimension_labels,
                    "inferred_scope_columns": applies_to_labels,
                    "scope_confidence": scope_confidence,
                    "scope_reason": scope_reason,
                    "applicability_source": "scope_engine_v3",
                    "applicability_detected_columns": detected_labels,
                    "applicability_segment_columns": scope_dimension_labels,
                    "scope_value": representative_value or "",
                }
            )

            updated_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row_meta,
                    cells=row.cells,
                )
            )
        return updated_rows

    def _build_analyze_url(self, *, locale: str | None = None) -> str:
        base_path = self.base_path or "formrecognizer"
        params = {"api-version": self.api_version}
        if locale:
            params["locale"] = locale
        query = urlencode(params)
        return f"{self.endpoint}/{base_path}/documentModels/{self.model}:analyze?{query}"

    @staticmethod
    def _parse_retry_after_seconds(raw_value: Any) -> float | None:
        if raw_value is None:
            return None
        raw = str(raw_value).strip()
        if not raw:
            return None
        try:
            seconds = float(raw)
            if seconds >= 0.0:
                return seconds
        except (TypeError, ValueError):
            pass
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = None
        if not parsed:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime_timezone.utc)
        delta = (parsed - datetime.now(datetime_timezone.utc)).total_seconds()
        return max(0.0, delta)

    def _retry_delay_seconds(
        self,
        attempt: int,
        *,
        response: Any = None,
    ) -> float:
        backoff = min(
            self.retry_backoff_max_seconds,
            self.retry_backoff_base_seconds * (2 ** max(0, int(attempt) - 1)),
        )
        jitter = random.uniform(0.0, min(0.25, backoff * 0.25))
        delay = backoff + jitter
        if response is not None:
            headers = getattr(response, "headers", None)
            retry_after_value = headers.get("retry-after") if isinstance(headers, Mapping) else None
            retry_after = self._parse_retry_after_seconds(retry_after_value)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self.max_retry_after_seconds))
        return round(max(0.0, delay), 3)

    @classmethod
    def _classify_failure_class(
        cls,
        *,
        status_code: int | None = None,
        exc: Exception | None = None,
    ) -> str:
        if isinstance(exc, requests.Timeout):
            return "timeout"
        if status_code in cls._THROTTLE_HTTP_STATUS:
            return "throttle_retryable"
        if status_code in cls._RETRYABLE_HTTP_STATUS:
            return "throttle_retryable"
        if isinstance(exc, requests.ConnectionError):
            return "throttle_retryable"
        return "hard_failure"

    @staticmethod
    def _append_retry_event(
        meta: dict[str, Any],
        *,
        phase: str,
        attempt: int,
        delay_s: float,
        reason: str,
        status_code: int | None = None,
    ) -> None:
        events = meta.setdefault("retry_events", [])
        if not isinstance(events, list):
            events = []
            meta["retry_events"] = events
        events.append(
            {
                "phase": phase,
                "attempt": int(attempt),
                "delay_s": round(float(delay_s), 3),
                "reason": reason,
                "status_code": status_code,
            }
        )
        if len(events) > 24:
            del events[:-24]

    @staticmethod
    def _finalize_failure_meta(
        meta: dict[str, Any],
        *,
        status: str,
        failure_class: str,
        failure_stage: str,
        failure_reason: str,
        start_time: float,
        failure_status_code: int | None = None,
        last_error: str | None = None,
    ) -> None:
        meta["status"] = status
        meta["failure_class"] = failure_class
        meta["failure_stage"] = failure_stage
        meta["failure_reason"] = failure_reason
        if failure_status_code is not None:
            meta["failure_status_code"] = int(failure_status_code)
        if last_error:
            meta["last_error"] = str(last_error)[:300]
        meta["duration_ms"] = int((time.time() - start_time) * 1000)

    def _analyze_document(self, path: Path) -> tuple[dict[str, Any] | None, list[IssuePayload], dict[str, Any]]:
        issues: list[IssuePayload] = []
        meta: dict[str, Any] = {
            "request_attempts": 0,
            "poll_attempts": 0,
            "poll_http_attempts": 0,
            "retry_events": [],
        }
        if not self.endpoint or not self.key:
            issues.append(
                IssuePayload(
                    code="azure_di_missing",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Azure Document Intelligence credentials are missing; skipping.",
                )
            )
            meta["status"] = "skipped"
            return None, issues, meta

        url = self._build_analyze_url(locale=self.locale)
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/pdf",
        }
        start = time.time()
        response: Any = None
        for attempt in range(1, self.request_max_attempts + 1):
            meta["request_attempts"] = attempt
            try:
                with path.open("rb") as handle:
                    response = requests.post(
                        url,
                        headers=headers,
                        data=handle,
                        timeout=self.timeout_seconds,
                    )
            except requests.RequestException as exc:
                failure_class = self._classify_failure_class(exc=exc)
                retryable = failure_class in {"timeout", "throttle_retryable"}
                if retryable and attempt < self.request_max_attempts:
                    delay = self._retry_delay_seconds(attempt)
                    self._append_retry_event(
                        meta,
                        phase="submit",
                        attempt=attempt,
                        delay_s=delay,
                        reason=f"submit_exception:{exc.__class__.__name__}",
                    )
                    time.sleep(delay)
                    continue
                issues.append(
                    IssuePayload(
                        code="azure_di_request_failed",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI request failed: {exc}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status=("timeout" if failure_class == "timeout" else "failed"),
                    failure_class=failure_class,
                    failure_stage="submit",
                    failure_reason="request_exception",
                    start_time=start,
                    last_error=str(exc),
                )
                return None, issues, meta

            if response.status_code in {200, 201, 202}:
                break

            failure_class = self._classify_failure_class(status_code=int(response.status_code))
            retryable_status = int(response.status_code) in self._RETRYABLE_HTTP_STATUS
            if retryable_status and attempt < self.request_max_attempts:
                delay = self._retry_delay_seconds(attempt, response=response)
                self._append_retry_event(
                    meta,
                    phase="submit",
                    attempt=attempt,
                    delay_s=delay,
                    reason="submit_http_retry",
                    status_code=int(response.status_code),
                )
                time.sleep(delay)
                continue

            issues.append(
                IssuePayload(
                    code="azure_di_request_error",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description=f"Azure DI request error {response.status_code}: {response.text[:200]}",
                )
            )
            self._finalize_failure_meta(
                meta,
                status=("timeout" if failure_class == "timeout" else "failed"),
                failure_class=failure_class,
                failure_stage="submit",
                failure_reason="request_http_error",
                start_time=start,
                failure_status_code=int(response.status_code),
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
            self._finalize_failure_meta(
                meta,
                status="failed",
                failure_class="hard_failure",
                failure_stage="submit",
                failure_reason="missing_operation_location",
                start_time=start,
            )
            return None, issues, meta

        poll_headers = {"Ocp-Apim-Subscription-Key": self.key}
        status_payload: dict[str, Any] | None = None
        for poll_attempt in range(1, self.max_polls + 1):
            meta["poll_attempts"] = poll_attempt
            poll_response: Any = None
            for http_attempt in range(1, self.poll_request_max_attempts + 1):
                meta["poll_http_attempts"] = int(meta.get("poll_http_attempts") or 0) + 1
                try:
                    poll_response = requests.get(
                        operation_url,
                        headers=poll_headers,
                        timeout=self.timeout_seconds,
                    )
                except requests.RequestException as exc:
                    failure_class = self._classify_failure_class(exc=exc)
                    retryable = failure_class in {"timeout", "throttle_retryable"}
                    if retryable and http_attempt < self.poll_request_max_attempts:
                        delay = self._retry_delay_seconds(http_attempt)
                        self._append_retry_event(
                            meta,
                            phase="poll",
                            attempt=http_attempt,
                            delay_s=delay,
                            reason=f"poll_exception:{exc.__class__.__name__}",
                        )
                        time.sleep(delay)
                        continue
                    issues.append(
                        IssuePayload(
                            code="azure_di_poll_failed",
                            severity=KnowledgeIssueSeverity.WARNING.value,
                            description=f"Azure DI poll failed: {exc}",
                        )
                    )
                    self._finalize_failure_meta(
                        meta,
                        status=("timeout" if failure_class == "timeout" else "failed"),
                        failure_class=failure_class,
                        failure_stage="poll",
                        failure_reason="poll_exception",
                        start_time=start,
                        last_error=str(exc),
                    )
                    return None, issues, meta

                if poll_response.status_code in {200, 201}:
                    break

                failure_class = self._classify_failure_class(status_code=int(poll_response.status_code))
                retryable_status = int(poll_response.status_code) in self._RETRYABLE_HTTP_STATUS
                if retryable_status and http_attempt < self.poll_request_max_attempts:
                    delay = self._retry_delay_seconds(http_attempt, response=poll_response)
                    self._append_retry_event(
                        meta,
                        phase="poll",
                        attempt=http_attempt,
                        delay_s=delay,
                        reason="poll_http_retry",
                        status_code=int(poll_response.status_code),
                    )
                    time.sleep(delay)
                    continue
                issues.append(
                    IssuePayload(
                        code="azure_di_poll_error",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI poll error {poll_response.status_code}: {poll_response.text[:200]}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status=("timeout" if failure_class == "timeout" else "failed"),
                    failure_class=failure_class,
                    failure_stage="poll",
                    failure_reason="poll_http_error",
                    start_time=start,
                    failure_status_code=int(poll_response.status_code),
                )
                return None, issues, meta

            if poll_response is None:
                continue
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
                self._finalize_failure_meta(
                    meta,
                    status="failed",
                    failure_class="hard_failure",
                    failure_stage="poll",
                    failure_reason="poll_status_failed",
                    start_time=start,
                )
                return None, issues, meta
            time.sleep(self.poll_interval_seconds)

        issues.append(
            IssuePayload(
                code="azure_di_timeout",
                severity=KnowledgeIssueSeverity.WARNING.value,
                description="Azure DI polling timed out.",
            )
        )
        self._finalize_failure_meta(
            meta,
            status="timeout",
            failure_class="timeout",
            failure_stage="poll",
            failure_reason="poll_max_attempts_exceeded",
            start_time=start,
        )
        return None, issues, meta

    def extract_tables(self, path: Path) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        analyze_result, issues, meta = self._analyze_document(path)
        if not analyze_result:
            return [], issues, meta

        pages_data = analyze_result.get("pages") or []
        page_unit_scale: dict[int, float] = {}
        for page in pages_data:
            if not isinstance(page, Mapping):
                continue
            page_number = page.get("pageNumber")
            try:
                page_number_int = int(page_number) if page_number is not None else None
            except (TypeError, ValueError):
                page_number_int = None
            if not page_number_int:
                continue
            unit = str(page.get("unit") or "").strip().lower()
            # Azure DI uses page units (commonly "inch") for polygon coordinates. PyMuPDF uses PDF points (1/72 inch).
            if unit in {"inch", "in"}:
                page_unit_scale[page_number_int] = 72.0
            elif unit in {"point", "pt"}:
                page_unit_scale[page_number_int] = 1.0
            else:
                # Unknown units (e.g., "pixel" for images). Leave unscaled by default.
                page_unit_scale[page_number_int] = 1.0

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
            
            # Debug: Check what boundingRegions Azure DI returns
            bounding_regions = table.get("boundingRegions")
            if order_index <= 2:  # Log first 2 tables only
                logger.info(
                    "azure_di.table_bbox_debug table=%s has_regions=%s region_count=%s first_region=%s",
                    order_index,
                    bool(bounding_regions),
                    len(bounding_regions) if bounding_regions else 0,
                    bounding_regions[0] if bounding_regions else None,
                )
            
            page_number, table_bbox = self._bbox_from_regions(
                bounding_regions,
                page_unit_scale=page_unit_scale,
            )
            header_rows: set[int] = set()
            cell_confidences: list[float] = []

            grid: list[list[str]] = [["" for _ in range(col_count)] for _ in range(row_count)]
            cell_lookup: dict[tuple[int, int], dict[str, Any]] = {}

            def _cell_value_signal(text: str) -> int:
                """
                Prefer value-like cells over label-like cells when spans overlap.

                Azure DI can emit broad-span "labels" (e.g. "Annual Fees") whose
                geometry overlaps value columns. If we write labels after values,
                the grid becomes unreadable (no numeric/value evidence). This
                signal is intentionally conservative and generic.
                """
                sample = str(text or "").strip()
                if not sample:
                    return 0
                if _column_numeric_signal(sample):
                    return 3
                lowered = sample.strip().lower()
                if lowered in {"free", "no fees", "no fee", "n/a", "na", "--", "-"}:
                    return 2
                return 0

            def _cell_priority(text: str, meta: Mapping[str, Any]) -> tuple[int, int, int]:
                value_score = _cell_value_signal(text)
                try:
                    span_area = int(meta.get("row_span") or 1) * int(meta.get("column_span") or 1)
                except (TypeError, ValueError):
                    span_area = 1
                span_score = -max(1, span_area)  # smaller span wins on ties
                kind = str(meta.get("kind") or "").strip().lower()
                kind_score = -1 if kind in {"columnheader", "rowheader"} else 0
                return (value_score, span_score, kind_score)

            def _should_write_cell(
                *,
                existing_text: str,
                existing_meta: Mapping[str, Any] | None,
                new_text: str,
                new_meta: Mapping[str, Any],
            ) -> bool:
                new_text = str(new_text or "").strip()
                if not new_text:
                    return False
                existing_text = str(existing_text or "").strip()
                if not existing_text:
                    return True
                if existing_text == new_text:
                    return False
                existing_meta = existing_meta or {}
                return _cell_priority(new_text, new_meta) > _cell_priority(existing_text, existing_meta)

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
                regions = cell.get("boundingRegions") or []
                for rr in range(r_idx, min(r_idx + row_span, row_count)):
                    for cc in range(c_idx, min(c_idx + col_span, col_count)):
                        new_meta = {
                            "row_span": row_span,
                            "column_span": col_span,
                            "kind": kind,
                            "confidence": confidence,
                            "regions": regions,
                        }
                        existing_meta = cell_lookup.get((rr, cc)) or {}
                        if not _should_write_cell(
                            existing_text=grid[rr][cc],
                            existing_meta=existing_meta,
                            new_text=content,
                            new_meta=new_meta,
                        ):
                            continue
                        grid[rr][cc] = content
                        cell_lookup[(rr, cc)] = new_meta

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

            # Geometric span reconciliation: compare data cell bounding
            # boxes against header cell bounding boxes to detect true column
            # spans that Azure DI failed to report via columnSpan.
            self._reconcile_spans_from_geometry(
                grid=grid,
                cell_lookup=cell_lookup,
                header_rows=header_rows,
                row_count=row_count,
                col_count=col_count,
                page_unit_scale=page_unit_scale,
            )

            table_rows: list[TableRowPayload] = []
            for row_idx in range(row_count):
                row_cells: list[TableCellPayload] = []
                for col_idx in range(col_count):
                    raw_text = grid[row_idx][col_idx]
                    cell_meta = cell_lookup.get((row_idx, col_idx), {})
                    cell_page, cell_bbox = self._bbox_from_regions(
                        cell_meta.get("regions"),
                        page_unit_scale=page_unit_scale,
                    )
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

            table_rows = self._annotate_row_applicability(
                table_rows=table_rows,
                column_schema=column_schema,
                header_rows=header_rows,
            )

            avg_conf = round(sum(cell_confidences) / max(1, len(cell_confidences)), 4) if cell_confidences else None
            caption_text = self._caption_text(table.get("caption"))
            table_payloads.append(
                TablePayload(
                    order_index=order_index,
                    title=caption_text or f"Table {order_index}",
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

    @staticmethod
    def _merge_fragment_text(left: str, right: str) -> str:
        left_text = str(left or "").strip()
        right_text = str(right or "").strip()
        if not left_text:
            return right_text
        if not right_text:
            return left_text
        if left_text == right_text:
            return left_text
        if right_text in left_text:
            return left_text
        if left_text in right_text:
            return right_text
        if left_text.endswith(("+", "/", "-", "(")):
            return f"{left_text} {right_text}".strip()
        return f"{left_text} {right_text}".strip()

    @staticmethod
    def _row_texts(row: TableRowPayload, column_count: int) -> list[str]:
        texts = [""] * max(0, column_count)
        for cell in (row.cells or []):
            if 0 <= cell.column_index < len(texts):
                texts[cell.column_index] = str(cell.raw_text or "").strip()
        return texts

    def _row_non_empty_columns(self, texts: Sequence[str]) -> list[int]:
        return [idx for idx, text in enumerate(texts) if str(text or "").strip()]

    def _leading_descriptor_text(self, texts: Sequence[str]) -> str:
        return " ".join(str(texts[idx] or "").strip() for idx in range(min(2, len(texts))) if str(texts[idx] or "").strip()).strip()

    @staticmethod
    def _looks_like_range_descriptor(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        return bool(
            re.search(r"\b(?:from|to|up to|above|below|over|under)\b", candidate)
            or re.search(r"\d+\s*\+\b", candidate)
        )

    @staticmethod
    def _looks_like_value_continuation(text: str) -> bool:
        candidate = str(text or "").strip()
        if not candidate:
            return False
        lowered = candidate.lower()
        if candidate.endswith(("+", "/", "-", "(")):
            return True
        if lowered.startswith(("+", "correspondent", "courier", "swift", "telex", "fees", "max.", "min.")):
            return True
        if re.search(r"\b(?:correspondent|courier|swift|telex)\b", lowered):
            return True
        if re.search(r"\bmin\.?\b", lowered) and "max" not in lowered:
            return True
        return False

    def _is_descriptor_only_fragment_row(self, texts: Sequence[str]) -> bool:
        non_empty = self._row_non_empty_columns(texts)
        if not non_empty:
            return False
        if any(idx >= 2 for idx in non_empty):
            return False
        joined = self._leading_descriptor_text(texts)
        if not joined:
            return False
        if self._mostly_numeric_or_amount(joined):
            return False
        return len(re.findall(r"\w+", joined)) <= 6

    def _has_complementary_value_fragments(self, current_texts: Sequence[str], next_texts: Sequence[str]) -> bool:
        value_columns = range(2, min(len(current_texts), len(next_texts)))
        for idx in value_columns:
            current = str(current_texts[idx] or "").strip()
            nxt = str(next_texts[idx] or "").strip()
            if current and nxt and (
                self._looks_like_value_continuation(current) or self._looks_like_value_continuation(nxt)
            ):
                return True
            if current and not nxt and self._looks_like_value_continuation(current):
                return True
            if nxt and not current and self._looks_like_value_continuation(nxt):
                return True
        return False

    def _rows_should_merge_logically(self, current: TableRowPayload, nxt: TableRowPayload, column_count: int) -> bool:
        current_texts = self._row_texts(current, column_count)
        next_texts = self._row_texts(nxt, column_count)
        current_non_empty = self._row_non_empty_columns(current_texts)
        next_non_empty = self._row_non_empty_columns(next_texts)
        if not current_non_empty or not next_non_empty:
            return False

        current_descriptor = self._leading_descriptor_text(current_texts)
        next_descriptor = self._leading_descriptor_text(next_texts)

        if self._looks_like_range_descriptor(current_descriptor) and self._looks_like_range_descriptor(next_descriptor):
            return False

        # Descriptor-only continuation lines should attach to the nearest logical row.
        if self._is_descriptor_only_fragment_row(current_texts) and self._is_descriptor_only_fragment_row(next_texts):
            return True
        if current_descriptor and self._is_descriptor_only_fragment_row(next_texts):
            return True

        # Value continuation rows are common in dense tariff tables where the fee formula wraps
        # across the next physical line while the descriptor stays on the first line.
        next_descriptor_only = self._is_descriptor_only_fragment_row(next_texts)
        if current_descriptor and (not next_descriptor or next_descriptor_only):
            if self._has_complementary_value_fragments(current_texts, next_texts):
                return True

        return False

    def _merge_geometry_rows(
        self,
        current: TableRowPayload,
        nxt: TableRowPayload,
        column_count: int,
    ) -> TableRowPayload:
        current_cells = {cell.column_index: cell for cell in (current.cells or [])}
        next_cells = {cell.column_index: cell for cell in (nxt.cells or [])}
        merged_cells: list[TableCellPayload] = []
        merged_bboxes: list[dict[str, Any]] = []

        for col_idx in range(column_count):
            current_cell = current_cells.get(col_idx)
            next_cell = next_cells.get(col_idx)
            current_text = str(current_cell.raw_text if current_cell else "").strip()
            next_text = str(next_cell.raw_text if next_cell else "").strip()
            merged_text = self._merge_fragment_text(current_text, next_text)
            current_bbox = current_cell.bbox if current_cell else {}
            next_bbox = next_cell.bbox if next_cell else {}
            merged_bbox = _union_bbox([bbox for bbox in [current_bbox, next_bbox] if bbox and any(bbox.values())])
            if any(merged_bbox.values()):
                merged_bboxes.append(merged_bbox)
            column_key = (
                current_cell.column_key
                if current_cell is not None
                else next_cell.column_key
                if next_cell is not None
                else f"column_{col_idx+1}"
            )
            span_count = int((current_cell.metadata or {}).get("span_count") or 0) + int((next_cell.metadata or {}).get("span_count") or 0)
            metadata = {"span_count": span_count}
            if current_text and next_text and merged_text != current_text:
                metadata["logical_row_merged"] = True
            merged_cells.append(
                TableCellPayload(
                    row_index=current.row_index,
                    column_index=col_idx,
                    column_key=column_key,
                    raw_text=merged_text,
                    normalized_value=TableDetector._normalize_cell_value(merged_text),
                    bbox=merged_bbox,
                    confidence=None,
                    metadata=metadata,
                )
            )

        merged_meta = dict(current.metadata or {})
        merged_meta["geometry_logical_row_merged"] = True
        merged_meta["geometry_merged_row_count"] = int(merged_meta.get("geometry_merged_row_count") or 1) + int((nxt.metadata or {}).get("geometry_merged_row_count") or 1)
        merged_bbox = _union_bbox([bbox for bbox in [current.bbox, nxt.bbox] if bbox and any(bbox.values())] + merged_bboxes)
        merged_raw = " | ".join(str(cell.raw_text or "").strip() for cell in merged_cells)
        return TableRowPayload(
            row_index=current.row_index,
            page_number=current.page_number,
            bbox=merged_bbox,
            raw_text=merged_raw,
            metadata=merged_meta,
            cells=merged_cells,
        )

    def _normalize_geometry_logical_rows(self, rows: list[TableRowPayload], column_count: int) -> tuple[list[TableRowPayload], int]:
        if not rows:
            return rows, 0
        merged_rows: list[TableRowPayload] = [rows[0]]
        merged_pairs = 0
        for nxt in rows[1:]:
            current = merged_rows[-1]
            if self._rows_should_merge_logically(current, nxt, column_count):
                merged_rows[-1] = self._merge_geometry_rows(current, nxt, column_count)
                merged_pairs += 1
            else:
                merged_rows.append(nxt)
        normalized_rows: list[TableRowPayload] = []
        for idx, row in enumerate(merged_rows, start=1):
            normalized_cells = [
                TableCellPayload(
                    row_index=idx,
                    column_index=cell.column_index,
                    column_key=cell.column_key,
                    raw_text=cell.raw_text,
                    normalized_value=cell.normalized_value,
                    bbox=cell.bbox,
                    confidence=cell.confidence,
                    metadata=cell.metadata,
                )
                for cell in (row.cells or [])
            ]
            normalized_rows.append(
                TableRowPayload(
                    row_index=idx,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row.metadata,
                    cells=normalized_cells,
                )
            )
        return normalized_rows, merged_pairs

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

            logical_merge_pairs = 0
            if table_rows:
                header_rows = [row for row in table_rows if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"]
                data_rows = [row for row in table_rows if str((row.metadata or {}).get("row_type") or "").strip().lower() != "header"]
                normalized_data_rows, logical_merge_pairs = self._normalize_geometry_logical_rows(
                    data_rows,
                    len(schema),
                )
                if header_rows:
                    header_row = header_rows[0]
                    normalized_header_cells = [
                        TableCellPayload(
                            row_index=0,
                            column_index=cell.column_index,
                            column_key=cell.column_key,
                            raw_text=cell.raw_text,
                            normalized_value=cell.normalized_value,
                            bbox=cell.bbox,
                            confidence=cell.confidence,
                            metadata=cell.metadata,
                        )
                        for cell in (header_row.cells or [])
                    ]
                    normalized_header = TableRowPayload(
                        row_index=0,
                        page_number=header_row.page_number,
                        bbox=header_row.bbox,
                        raw_text=header_row.raw_text,
                        metadata=header_row.metadata,
                        cells=normalized_header_cells,
                    )
                    table_rows = [normalized_header, *normalized_data_rows]
                else:
                    table_rows = normalized_data_rows

            table_payload = TablePayload(
                order_index=order_index,
                title=page_layout.section_heading if getattr(page_layout, "section_heading", "") else f"Table {order_index}",
                section_heading=getattr(page_layout, "section_heading", "") or "",
                page_number=page_number,
                bbox=_union_bbox([row.bbox for row in table_rows]) if table_rows else {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0},
                column_schema=schema,
                data_dictionary={},
                metadata={
                    "detected_via": "geometry",
                    "col_bins": anchor_bins,
                    "start_row_idx": start_row_idx,
                    "geometry_logical_row_merged_pairs": logical_merge_pairs,
                },
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
        
        ocr_render_dpi = int(getattr(settings, "RAG_OCR_RENDER_DPI", 200) or 200)

        # NEW: Create OCR reconciler with Tesseract support
        self.ocr_reconciler = create_ocr_reconciler(enable_ocr=enable_ocr, render_dpi=ocr_render_dpi)
        
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
        self.azure_di_request_max_attempts = int(getattr(settings, "RAG_AZURE_DI_REQUEST_MAX_ATTEMPTS", 3))
        self.azure_di_poll_request_max_attempts = int(
            getattr(settings, "RAG_AZURE_DI_POLL_REQUEST_MAX_ATTEMPTS", 3)
        )
        self.azure_di_retry_backoff_base_seconds = float(
            getattr(settings, "RAG_AZURE_DI_RETRY_BACKOFF_BASE_SECONDS", 1.0)
        )
        self.azure_di_retry_backoff_max_seconds = float(
            getattr(settings, "RAG_AZURE_DI_RETRY_BACKOFF_MAX_SECONDS", 8.0)
        )
        self.azure_di_max_retry_after_seconds = float(
            getattr(settings, "RAG_AZURE_DI_MAX_RETRY_AFTER_SECONDS", 30.0)
        )
        self.table_vlm_enabled = bool(getattr(settings, "RAG_TABLE_VLM_ENABLED", True))
        self.table_vlm_model = str(getattr(settings, "RAG_TABLE_VLM_MODEL", "gpt-4o") or "gpt-4o").strip()
        self.table_vlm_confidence_threshold = float(
            getattr(settings, "RAG_TABLE_VLM_CONFIDENCE_THRESHOLD", 0.6)
        )
        self.table_vlm_max_repairs = int(getattr(settings, "RAG_TABLE_VLM_MAX_REPAIRS_PER_UPLOAD", 3))
        self.table_vlm_guardrails_enabled = bool(getattr(settings, "RAG_TABLE_VLM_GUARDRAILS_ENABLED", True))
        self.table_vlm_guardrail_min_row_recall = float(
            getattr(settings, "RAG_TABLE_VLM_GUARDRAIL_MIN_ROW_RECALL", 0.99)
        )
        self.table_vlm_guardrail_hard_row_recall_floor = float(
            getattr(settings, "RAG_TABLE_VLM_GUARDRAIL_HARD_ROW_RECALL_FLOOR", 0.75)
        )
        self.table_vlm_guardrail_min_order_ratio = float(
            getattr(settings, "RAG_TABLE_VLM_GUARDRAIL_MIN_ORDER_RATIO", 0.7)
        )
        self.table_vlm_guardrail_min_schema_recall = float(
            getattr(settings, "RAG_TABLE_VLM_GUARDRAIL_MIN_SCHEMA_RECALL", 0.9)
        )
        self.table_vlm_guardrail_min_cell_recall = float(
            getattr(settings, "RAG_TABLE_VLM_GUARDRAIL_MIN_CELL_RECALL", 0.9)
        )
        self.table_schema_chunking = bool(getattr(settings, "RAG_TABLE_SCHEMA_CHUNKING", True))
        self.table_parent_max_rows = max(1, int(getattr(settings, "RAG_TABLE_PARENT_MAX_ROWS", 200)))
        self.table_parent_max_chars = max(2000, int(getattr(settings, "RAG_TABLE_PARENT_MAX_CHARS", 16000)))
        self.table_child_max_rows = max(0, int(getattr(settings, "RAG_TABLE_CHILD_MAX_ROWS", 500)))
        self.table_summary_enabled = bool(getattr(settings, "RAG_TABLE_SUMMARY_ENABLED", True))
        self.table_summary_max_row_labels = max(0, int(getattr(settings, "RAG_TABLE_SUMMARY_MAX_ROW_LABELS", 50)))
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
        self.table_row_signal_min_pairs = max(
            1,
            int(getattr(settings, "RAG_TABLE_ROW_SIGNAL_MIN_PAIRS", 2)),
        )
        self.table_row_signal_min_score = float(
            getattr(settings, "RAG_TABLE_ROW_SIGNAL_MIN_SCORE", 1.6)
        )
        if self.table_row_signal_min_score < 0.0:
            self.table_row_signal_min_score = 0.0
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
        self.evidence_grouping_enabled = bool(getattr(settings, "RAG_EVIDENCE_GROUPING_ENABLED", True))
        self.evidence_text_link_max_chars = max(
            200,
            int(getattr(settings, "RAG_EVIDENCE_TEXT_LINK_MAX_CHARS", 650)),
        )
        self.evidence_text_link_max_lines = max(
            1,
            int(getattr(settings, "RAG_EVIDENCE_TEXT_LINK_MAX_LINES", 4)),
        )
        self.pdf_table_text_overlap_filter_enabled = bool(
            getattr(settings, "RAG_PDF_TABLE_TEXT_OVERLAP_FILTER_ENABLED", True)
        )
        self.pdf_table_promotion_gate_enabled = bool(
            getattr(settings, "RAG_PDF_TABLE_PROMOTION_GATE_ENABLED", True)
        )
        self.pdf_table_recurring_scaffold_min_repeats = max(
            2,
            int(getattr(settings, "RAG_PDF_TABLE_RECURRING_SCAFFOLD_MIN_REPEATS", 5)),
        )
        self.pdf_table_paragraph_long_cell_words = max(
            6,
            int(getattr(settings, "RAG_PDF_TABLE_PARAGRAPH_LONG_CELL_WORDS", 12)),
        )
        self.pdf_table_paragraph_long_cell_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_PARAGRAPH_LONG_CELL_RATIO", 0.35)
        )
        if not (0.0 <= self.pdf_table_paragraph_long_cell_ratio <= 1.0):
            self.pdf_table_paragraph_long_cell_ratio = 0.35
        self.pdf_table_paragraph_min_rows = max(
            2,
            int(getattr(settings, "RAG_PDF_TABLE_PARAGRAPH_MIN_ROWS", 3)),
        )
        self.pdf_table_leading_blank_row_limit = max(
            1,
            int(getattr(settings, "RAG_PDF_TABLE_LEADING_BLANK_ROW_LIMIT", 2)),
        )
        self.pdf_page_chrome_suppression_enabled = bool(
            getattr(settings, "RAG_PDF_PAGE_CHROME_SUPPRESSION_ENABLED", True)
        )
        self.pdf_page_chrome_min_repeats = max(
            2,
            int(getattr(settings, "RAG_PDF_PAGE_CHROME_MIN_REPEATS", 3)),
        )
        self.pdf_page_chrome_top_ratio = float(
            getattr(settings, "RAG_PDF_PAGE_CHROME_TOP_RATIO", 0.16)
        )
        if not (0.0 <= self.pdf_page_chrome_top_ratio <= 1.0):
            self.pdf_page_chrome_top_ratio = 0.16
        self.pdf_page_chrome_bottom_ratio = float(
            getattr(settings, "RAG_PDF_PAGE_CHROME_BOTTOM_RATIO", 0.12)
        )
        if not (0.0 <= self.pdf_page_chrome_bottom_ratio <= 1.0):
            self.pdf_page_chrome_bottom_ratio = 0.12
        self.pdf_page_chrome_max_words = max(
            4,
            int(getattr(settings, "RAG_PDF_PAGE_CHROME_MAX_WORDS", 24)),
        )
        self.pdf_table_micro_fragment_min_columns = max(
            4,
            int(getattr(settings, "RAG_PDF_TABLE_MICRO_FRAGMENT_MIN_COLUMNS", 8)),
        )
        self.pdf_table_micro_fragment_short_cell_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_MICRO_FRAGMENT_SHORT_CELL_RATIO", 0.45)
        )
        if not (0.0 <= self.pdf_table_micro_fragment_short_cell_ratio <= 1.0):
            self.pdf_table_micro_fragment_short_cell_ratio = 0.45
        self.pdf_table_bridge_max_rows = max(
            1,
            int(getattr(settings, "RAG_PDF_TABLE_BRIDGE_MAX_ROWS", 4)),
        )
        self.pdf_table_bridge_long_cell_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_BRIDGE_LONG_CELL_RATIO", 0.5)
        )
        if not (0.0 <= self.pdf_table_bridge_long_cell_ratio <= 1.0):
            self.pdf_table_bridge_long_cell_ratio = 0.5
        self.pdf_table_text_overlap_min_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_TEXT_OVERLAP_MIN_RATIO", 0.55)
        )
        if not (0.0 <= self.pdf_table_text_overlap_min_ratio <= 1.0):
            self.pdf_table_text_overlap_min_ratio = 0.55
        self.pdf_table_region_merge_x_margin_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_REGION_MERGE_X_MARGIN_RATIO", 0.012)
        )
        if self.pdf_table_region_merge_x_margin_ratio < 0.0:
            self.pdf_table_region_merge_x_margin_ratio = 0.0
        self.pdf_table_region_merge_y_margin_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_REGION_MERGE_Y_MARGIN_RATIO", 0.008)
        )
        if self.pdf_table_region_merge_y_margin_ratio < 0.0:
            self.pdf_table_region_merge_y_margin_ratio = 0.0
        self.pdf_table_residual_overlap_min_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_RESIDUAL_OVERLAP_MIN_RATIO", 0.08)
        )
        if not (0.0 <= self.pdf_table_residual_overlap_min_ratio <= 1.0):
            self.pdf_table_residual_overlap_min_ratio = 0.08
        self.pdf_table_residual_near_region_ratio = float(
            getattr(settings, "RAG_PDF_TABLE_RESIDUAL_NEAR_REGION_RATIO", 0.012)
        )
        if self.pdf_table_residual_near_region_ratio < 0.0:
            self.pdf_table_residual_near_region_ratio = 0.0
        self.table_residual_max_per_region = max(
            1,
            int(getattr(settings, "RAG_TABLE_RESIDUAL_MAX_PER_REGION", 1)),
        )
        self.table_residual_equivalence_min_overlap = float(
            getattr(settings, "RAG_TABLE_RESIDUAL_EQUIV_MIN_OVERLAP", 0.65)
        )
        if not (0.0 <= self.table_residual_equivalence_min_overlap <= 1.0):
            self.table_residual_equivalence_min_overlap = 0.65
        self.table_residual_equivalence_min_shared_tokens = max(
            1,
            int(getattr(settings, "RAG_TABLE_RESIDUAL_EQUIV_MIN_SHARED_TOKENS", 5)),
        )
        self.table_residual_compact_max_chars = max(
            200,
            int(getattr(settings, "RAG_TABLE_RESIDUAL_COMPACT_MAX_CHARS", 700)),
        )
        self.table_annotation_enabled = bool(getattr(settings, "RAG_TABLE_ANNOTATION_ENABLED", True))
        self.table_annotation_max_chars = max(
            200,
            int(getattr(settings, "RAG_TABLE_ANNOTATION_MAX_CHARS", 1200)),
        )
        self.table_annotation_max_per_table = max(
            1,
            int(getattr(settings, "RAG_TABLE_ANNOTATION_MAX_PER_TABLE", 1)),
        )
        self.canonical_chunk_schema_version = max(
            1,
            int(getattr(settings, "RAG_CANONICAL_CHUNK_SCHEMA_VERSION", 1)),
        )
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
        self.tenant_lexicon_auto_learning_enabled = bool(
            getattr(settings, "RAG_TENANT_LEXICON_AUTO_LEARN_ENABLED", True)
        )
        self._tenant_lexicon_auto_learning_service: TenantLexiconAutoLearningService | None = None

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
                        # New emoji-enhanced logging
                        log_start(
                            logger,
                            "INGEST",
                            f"Document: {upload.source_name or upload.display_name}",
                            {
                                "job_id": job.id,
                                "upload_id": upload.id,
                                "source_type": upload.source_type,
                                "size_bytes": getattr(upload, "size_bytes", None),
                            },
                            emoji=LogEmoji.UPLOAD,
                        )
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

        if upload.source_type == KnowledgeSourceType.TEXT:
            text_detail = getattr(upload, "text_detail", None)
            if not isinstance(text_detail, KnowledgeUploadText):
                upload = KnowledgeUpload.objects.select_related("text_detail").get(id=upload.id)
                text_detail = upload.text_detail
            if text_detail is None:
                raise KnowledgeIngestionError("Text metadata missing for upload.")
            return self._extract_from_text(text_detail)

        raise KnowledgeIngestionError(f"Ingestion not implemented for {upload.source_type}.")

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

    def _extract_from_text(self, text_detail: KnowledgeUploadText) -> ExtractionResult:
        text = text_detail.content or ""
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / float(612 * 792) if text.strip() else 0.0,
            has_ocr_content=False,
            content_type="text/plain",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=text,
                )
            ],
            metadata={},
        )
        return ExtractionResult(
            text=text,
            format_hint="text",
            metadata={"content_type": "text/plain"},
            pages=[page],
            tables=[],
            issues=[],
        )

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
        # New emoji-enhanced logging
        log_start(
            logger,
            "EXTRACT",
            f"File: {Path(absolute).name}",
            {"format": format_hint, "path": str(absolute)},
            emoji=LogEmoji.DOCUMENT,
        )
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
                # New emoji-enhanced logging
                log_success(
                    logger,
                    "EXTRACTED",
                    f"{len(layout_result.pages)} pages from {format_hint.upper()}",
                    {"path": Path(absolute).name},
                    emoji=LogEmoji.DOCUMENT,
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
        page_count = len(layout_result.pages)
        if page_count and int(getattr(file_detail, "page_count", 0) or 0) != page_count:
            KnowledgeUploadFile.objects.filter(id=file_detail.id).update(page_count=page_count, updated_at=timezone.now())
            file_detail.page_count = page_count

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
                # New emoji-enhanced logging
                if geometry_tables:
                    log_success(
                        logger,
                        "TABLES DETECTED",
                        f"{len(geometry_tables)} tables found",
                        {"source": "geometry"},
                        emoji=LogEmoji.TABLE,
                    )
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
                request_max_attempts=self.azure_di_request_max_attempts,
                poll_request_max_attempts=self.azure_di_poll_request_max_attempts,
                retry_backoff_base_seconds=self.azure_di_retry_backoff_base_seconds,
                retry_backoff_max_seconds=self.azure_di_retry_backoff_max_seconds,
                max_retry_after_seconds=self.azure_di_max_retry_after_seconds,
            )
            azure_tables, azure_issues, azure_meta = azure_extractor.extract_tables(absolute)

        docx_tables: list[TablePayload] = []
        docx_issues: list[IssuePayload] = []
        docx_meta: dict[str, Any] = {}
        if format_hint == "docx":
            docx_tables, docx_issues, docx_meta = self._extract_docx_table_candidates(
                absolute,
                filename=file_detail.filename,
            )

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
        if docx_tables:
            candidates["docx:table_xml"] = docx_tables
        if geometry_tables:
            candidates["geometry"] = geometry_tables
        if filtered_heuristics:
            candidates["heuristic"] = filtered_heuristics

        table_runtime_flags = self._table_runtime_flags(upload)
        selected_extractor, tables, selection_meta = self._select_table_candidates(candidates)
        issues = (
            layout_result.issues
            + table_issues
            + geom_issues
            + suppress_issues
            + pdfplumber_issues
            + azure_issues
            + docx_issues
        )

        repair_meta: dict[str, Any] = {}
        if tables:
            tables, repair_issues, repair_meta = self._repair_tables_with_vlm(
                absolute,
                tables,
            )
            issues.extend(repair_issues)
            # VLM repair replaces entire TablePayload objects whose rows lack
            # applicability metadata.  Re-run annotation so VLM-repaired tables
            # get the same sparse-row / span enrichment as Azure DI tables.
            if repair_meta.get("repaired"):
                # _annotate_row_applicability lives on the extractor class;
                # create a lightweight instance (no API calls are made).
                _applicability_annotator = AzureDocumentIntelligenceExtractor(
                    endpoint=None, key=None,
                )
                for idx, table in enumerate(tables):
                    detected_via = str((table.metadata or {}).get("detected_via") or "")
                    if "vlm" not in detected_via:
                        continue
                    header_rows: set[int] = set()
                    for row in (table.rows or []):
                        if (row.metadata or {}).get("row_type") == "header":
                            header_rows.add(row.row_index)
                    annotated_rows = _applicability_annotator._annotate_row_applicability(
                        table_rows=table.rows or [],
                        column_schema=table.column_schema or [],
                        header_rows=header_rows,
                    )
                    tables[idx] = TablePayload(
                        order_index=table.order_index,
                        title=table.title,
                        section_heading=table.section_heading,
                        page_number=table.page_number,
                        bbox=table.bbox,
                        column_schema=table.column_schema,
                        data_dictionary=table.data_dictionary,
                        metadata=table.metadata,
                        rows=annotated_rows,
                    )

        chunking_pages = list(layout_result.pages)

        # Canonical table reconstruction (ingestion-time):
        # - reconstruct gridless pseudo-tables from aligned page blocks
        # - attach orphan/residual blocks into the most likely cell
        canonical_reconstruction_meta: dict[str, Any] = {}
        if format_hint == "pdf" and chunking_pages:
            try:
                from apps.knowledge.canonical_table_reconstruction import CanonicalTableReconstructor

                reconstructor = CanonicalTableReconstructor(
                    PageLayout=PageLayout,
                    PageBlockPayload=PageBlockPayload,
                    TablePayload=TablePayload,
                    TableRowPayload=TableRowPayload,
                    TableCellPayload=TableCellPayload,
                )
                chunking_pages, tables, recon_meta, _recon_issues = reconstructor.run(
                    pages=chunking_pages,
                    tables=tables,
                )
                if recon_meta.reconstructed_tables or recon_meta.attached_blocks:
                    canonical_reconstruction_meta = {
                        "reconstructed_tables": recon_meta.reconstructed_tables,
                        "reconstructed_rows": recon_meta.reconstructed_rows,
                        "attached_blocks": recon_meta.attached_blocks,
                        "attached_cells": recon_meta.attached_cells,
                        "consumed_blocks": recon_meta.consumed_blocks,
                        "modified_tables": recon_meta.modified_tables,
                        "details": recon_meta.details,
                    }
            except Exception as exc:
                logger.warning("canonical_table_reconstruction.failed upload=%s err=%s", upload.id, exc)

        postprocess_meta: dict[str, Any] = {}
        if tables:
            tables, postprocess_issues, postprocess_meta = self._postprocess_tables(tables)
            issues.extend(postprocess_issues)

        table_promotion_meta: dict[str, Any] = {}
        if format_hint == "pdf" and tables:
            tables, table_promotion_meta, promotion_issues = self._apply_pdf_table_promotion_gate(tables)
            issues.extend(promotion_issues)

        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(
            tables,
            upload=upload,
            config=ingest_config,
        )
        issues = issues + limit_issues

        table_text_overlap_filter_meta: dict[str, Any] = {}
        if (
            format_hint == "pdf"
            and self.pdf_table_text_overlap_filter_enabled
            and tables
            and chunking_pages
        ):
            chunking_pages, table_text_overlap_filter_meta = self._annotate_pdf_blocks_with_table_overlap(
                chunking_pages,
                tables,
            )

        pdf_baseline_metrics: dict[str, Any] = {}
        if format_hint == "pdf":
            pdf_baseline_metrics = self._build_pdf_table_baseline_metrics(
                chunking_pages,
                tables,
                overlap_diagnostics=table_text_overlap_filter_meta,
            )

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
            "page_count": len(chunking_pages),
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
            extraction_meta["selection_mode"] = str(selection_meta.get("selection_mode") or "candidate_scorer_v2")
            extraction_meta["runtime_flags"] = table_runtime_flags
            candidate_metrics = selection_meta.get("metrics")
            if isinstance(candidate_metrics, Mapping):
                extraction_meta["candidate_metrics"] = candidate_metrics
            selector_debug = {
                "heuristic_override_applied": selection_meta.get("heuristic_override_applied"),
                "heuristic_override_reason": selection_meta.get("heuristic_override_reason"),
                "heuristic_override_flags": selection_meta.get("heuristic_override_flags"),
                "heuristic_override_from": selection_meta.get("heuristic_override_from"),
                "heuristic_override_to": selection_meta.get("heuristic_override_to"),
                "heuristic_override_candidate": selection_meta.get("heuristic_override_candidate"),
                "heuristic_override_comparable_non_heuristic": selection_meta.get(
                    "heuristic_override_comparable_non_heuristic"
                ),
                "heuristic_override_fallback_fragmentation": selection_meta.get(
                    "heuristic_override_fallback_fragmentation"
                ),
                "region_blend_applied": selection_meta.get("region_blend_applied"),
                "region_blend_document_selected": selection_meta.get("region_blend_document_selected"),
                "region_blend_extractors_used": selection_meta.get("region_blend_extractors_used"),
                "region_blend_region_count": selection_meta.get("region_blend_region_count"),
                "region_blend_regions": selection_meta.get("region_blend_regions"),
            }
            selector_debug = {key: value for key, value in selector_debug.items() if value is not None}
            if selector_debug:
                extraction_meta["selector_debug"] = selector_debug
            if pdf_baseline_metrics:
                extraction_meta["baseline_metrics"] = pdf_baseline_metrics
            if pdfplumber_meta:
                extraction_meta["pdfplumber"] = pdfplumber_meta
            if azure_meta:
                extraction_meta["azure_di"] = azure_meta
            if repair_meta:
                extraction_meta["table_repairs"] = repair_meta
            if postprocess_meta:
                extraction_meta["table_postprocess"] = postprocess_meta
            if table_promotion_meta:
                extraction_meta["table_promotion"] = table_promotion_meta
            if table_text_overlap_filter_meta:
                extraction_meta["table_text_overlap_filter"] = table_text_overlap_filter_meta
            if canonical_reconstruction_meta:
                extraction_meta["canonical_table_reconstruction"] = canonical_reconstruction_meta
            metadata["table_extraction"] = extraction_meta
        elif format_hint == "docx":
            extraction_meta = {
                "selected_extractor": selected_extractor,
                "candidate_counts": {key: len(val) for key, val in candidates.items()},
                "candidate_scores": selection_meta.get("scores", {}),
                "selection_mode": str(selection_meta.get("selection_mode") or "candidate_scorer_v2"),
            }
            if docx_meta:
                extraction_meta["docx"] = docx_meta
            if postprocess_meta:
                extraction_meta["table_postprocess"] = postprocess_meta
            metadata["table_extraction"] = extraction_meta
        return ExtractionResult(
            text=text,
            format_hint=format_hint or "binary",
            metadata=metadata,
            pages=chunking_pages,
            tables=tables,
            issues=issues,
            entities=table_entities,
        )

    def _extract_docx_table_candidates(
        self,
        path: Path,
        *,
        filename: str = "",
    ) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        """
        Parse DOCX tables directly from `document.tables` and emit structured TablePayloads.

        This is the primary DOCX table path. Paragraph extraction remains supplemental.
        """
        if DocxDocument is None:
            return [], [
                IssuePayload(
                    code="docx_tables_missing_dependency",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description="python-docx is unavailable; DOCX table extraction skipped.",
                    page_number=1,
                )
            ], {"enabled": False, "reason": "python_docx_missing"}

        try:
            document = DocxDocument(str(path))
        except Exception as exc:
            return [], [
                IssuePayload(
                    code="docx_tables_parse_failed",
                    severity=KnowledgeIssueSeverity.ERROR.value,
                    description=f"DOCX table extraction failed: {exc}",
                    page_number=1,
                )
            ], {"enabled": False, "reason": "parse_failed"}

        heading_map = self._docx_table_heading_map(document)
        tables: list[TablePayload] = []
        issues: list[IssuePayload] = []
        merged_regions_total = 0
        header_rows_total = 0
        compacted_blank_rows_total = 0
        section_rows_total = 0
        series_columns_total = 0

        for order_index, table in enumerate(document.tables, start=1):
            key_grid, text_by_key, span_by_key = self._docx_table_grid(table)
            key_grid, span_by_key, grid_meta = self._docx_compact_table_grid(
                key_grid,
                text_by_key,
                span_by_key,
            )
            provisional_header_rows = self._docx_detect_header_rows(key_grid, text_by_key)
            key_grid, text_by_key, span_by_key, collapse_meta = self._docx_collapse_helper_columns(
                key_grid,
                text_by_key,
                span_by_key,
                provisional_header_rows,
            )
            key_grid, text_by_key, span_by_key, series_meta = self._docx_normalize_sparse_series_columns(
                key_grid,
                text_by_key,
                span_by_key,
            )
            row_count = len(key_grid)
            col_count = max((len(row) for row in key_grid), default=0)
            if row_count <= 0 or col_count <= 0:
                issues.append(
                    IssuePayload(
                        code="docx_table_empty",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"DOCX table {order_index} had no readable cells.",
                        page_number=1,
                        table_order_index=order_index,
                    )
                )
                continue

            header_rows = self._docx_detect_header_rows(key_grid, text_by_key)
            header_row_set = set(header_rows)
            section_rows = self._docx_detect_section_rows(key_grid, text_by_key, header_row_set)
            section_row_set = set(section_rows)
            if header_rows:
                header_rows_total += len(header_rows)
            compacted_blank_rows_total += int(grid_meta.get("removed_blank_rows") or 0)
            if section_rows:
                section_rows_total += len(section_rows)
            series_columns_total += int(series_meta.get("merged_columns") or 0)

            column_schema: list[str] = []
            for col_idx in range(col_count):
                labels: list[str] = []
                seen_labels: set[str] = set()
                for row_idx in header_rows:
                    key = key_grid[row_idx][col_idx] if col_idx < len(key_grid[row_idx]) else None
                    if key is None:
                        continue
                    label = self._docx_canonicalize_header_label(
                        self._sanitize_text(text_by_key.get(key, "")).strip()
                    )
                    if not label:
                        continue
                    dedupe_key = re.sub(r"\s+", " ", label).strip().lower()
                    if dedupe_key in seen_labels:
                        continue
                    seen_labels.add(dedupe_key)
                    labels.append(label)
                merged_label = " | ".join(labels).strip()
                column_schema.append(self._docx_normalize_column_key(merged_label, col_idx))

            if not any(column_schema):
                column_schema = [f"column_{idx + 1}" for idx in range(col_count)]
            else:
                column_schema = self._docx_refine_grouped_column_schema(
                    column_schema,
                    key_grid,
                    text_by_key,
                    header_rows,
                )

            table_rows: list[TableRowPayload] = []
            merged_regions = 0
            for row_idx, row_keys in enumerate(key_grid):
                row_type = "data"
                if row_idx in header_row_set:
                    row_type = "header"
                elif row_idx in section_row_set:
                    row_type = "section_header"

                initial_row_values = [
                    self._sanitize_text(
                        text_by_key.get(row_keys[col_idx], "")
                        if col_idx < len(row_keys) and row_keys[col_idx] is not None
                        else ""
                    ).strip()
                    for col_idx in range(col_count)
                ]
                repeated_section_label = ""
                repeated_section_col_idx: int | None = None
                if row_type == "section_header":
                    repeated_section_label, repeated_section_col_idx = self._docx_repeated_row_label_info(
                        initial_row_values
                    )

                row_cells: list[TableCellPayload] = []
                row_values: list[str] = []
                for col_idx in range(col_count):
                    key = row_keys[col_idx] if col_idx < len(row_keys) else None
                    raw_text = self._sanitize_text(text_by_key.get(key, "") if key is not None else "").strip()
                    if repeated_section_label and raw_text:
                        canonical_cell = self._docx_canonicalize_header_label(raw_text)
                        if canonical_cell.strip().lower() == repeated_section_label.strip().lower():
                            raw_text = repeated_section_label if col_idx == repeated_section_col_idx else ""
                    row_values.append(raw_text)
                    cell_metadata: dict[str, Any] = {}
                    if key is not None:
                        span = span_by_key.get(key) or {}
                        row_span = int(span.get("row_span") or 1)
                        col_span = int(span.get("column_span") or 1)
                        if row_span > 1 or col_span > 1:
                            is_anchor = (
                                row_idx == int(span.get("row_start") or 0)
                                and col_idx == int(span.get("col_start") or 0)
                            )
                            cell_metadata["row_span"] = row_span
                            cell_metadata["column_span"] = col_span
                            cell_metadata["merged_anchor"] = is_anchor
                            if is_anchor:
                                merged_regions += 1
                            else:
                                cell_metadata["merged_from"] = {
                                    "row_index": int(span.get("row_start") or 0),
                                    "column_index": int(span.get("col_start") or 0),
                                }

                    row_cells.append(
                        TableCellPayload(
                            row_index=row_idx,
                            column_index=col_idx,
                            column_key=column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx + 1}",
                            raw_text=raw_text,
                            normalized_value=self.table_detector._normalize_cell_value(raw_text),
                            metadata=cell_metadata,
                        )
                    )

                row_metadata: dict[str, Any] = {"row_type": row_type}
                if row_idx in header_row_set:
                    row_metadata["header_source"] = "docx_detected"
                    row_metadata["header_level"] = header_rows.index(row_idx) + 1
                elif row_idx in section_row_set:
                    row_metadata["section_source"] = "docx_detected"
                table_rows.append(
                    TableRowPayload(
                        row_index=row_idx,
                        page_number=1,
                        raw_text="\t".join(row_values),
                        metadata=row_metadata,
                        cells=row_cells,
                    )
                )

            merged_regions_total += merged_regions
            section_heading = self._sanitize_text(heading_map.get(order_index, "")).strip()
            title = section_heading or f"Table {order_index}"
            table_metadata: dict[str, Any] = {
                "detected_via": "docx:table_xml",
                "extractor": "docx_table_parser",
                "table_index": order_index,
                "row_count": row_count,
                "column_count": col_count,
                "header_rows": list(header_rows),
                "section_rows": list(section_rows),
                "merged_regions": merged_regions,
                "structure_confidence": 0.95,
            }
            if grid_meta.get("removed_blank_rows"):
                table_metadata["blank_rows_compacted"] = int(grid_meta.get("removed_blank_rows") or 0)
            if collapse_meta.get("removed_columns"):
                table_metadata["helper_columns_removed"] = int(collapse_meta.get("removed_columns") or 0)
            if collapse_meta.get("merged_columns"):
                table_metadata["helper_columns_merged"] = int(collapse_meta.get("merged_columns") or 0)
            if series_meta.get("merged_columns"):
                table_metadata["series_columns_merged"] = int(series_meta.get("merged_columns") or 0)
            if filename:
                table_metadata["filename"] = filename
            if section_heading:
                table_metadata["section_heading"] = section_heading
            style_name = self._sanitize_text(getattr(getattr(table, "style", None), "name", "")).strip()
            if style_name:
                table_metadata["style_name"] = style_name

            tables.append(
                TablePayload(
                    order_index=order_index,
                    title=title,
                    section_heading=section_heading,
                    page_number=1,
                    column_schema=column_schema,
                    data_dictionary={},
                    metadata=table_metadata,
                    rows=table_rows,
                )
            )

        meta: dict[str, Any] = {
            "enabled": True,
            "table_count": len(tables),
            "header_rows_detected": header_rows_total,
            "section_rows_detected": section_rows_total,
            "blank_rows_compacted": compacted_blank_rows_total,
            "merged_regions": merged_regions_total,
            "series_columns_merged": series_columns_total,
        }
        return tables, issues, meta

    def _docx_table_heading_map(self, document: Any) -> dict[int, str]:
        body = getattr(getattr(document, "element", None), "body", None)
        if body is None:
            return {}
        try:
            from docx.text.paragraph import Paragraph as DocxParagraphClass  # type: ignore
        except Exception:
            return {}

        current_heading = ""
        heading_map: dict[int, str] = {}
        table_index = 0
        for child in body.iterchildren():
            child_tag = str(getattr(child, "tag", "") or "")
            if child_tag.endswith("}p"):
                paragraph = DocxParagraphClass(child, document)
                text = self._sanitize_text(getattr(paragraph, "text", "")).strip()
                if text and PageRenderer._looks_like_heading(text):
                    current_heading = text
                continue
            if child_tag.endswith("}tbl"):
                table_index += 1
                if current_heading:
                    heading_map[table_index] = current_heading
        return heading_map

    @staticmethod
    def _docx_table_grid(
        table: Any,
    ) -> tuple[list[list[int | None]], dict[int, str], dict[int, dict[str, int]]]:
        rows = [list(getattr(row, "cells", []) or []) for row in getattr(table, "rows", [])]
        if not rows:
            return [], {}, {}
        column_count = max((len(cells) for cells in rows), default=0)
        if column_count <= 0:
            return [], {}, {}

        key_grid: list[list[int | None]] = []
        text_by_key: dict[int, str] = {}
        positions_by_key: dict[int, list[tuple[int, int]]] = {}

        for row_idx, row_cells in enumerate(rows):
            row_keys: list[int | None] = []
            for col_idx in range(column_count):
                if col_idx >= len(row_cells):
                    row_keys.append(None)
                    continue
                cell = row_cells[col_idx]
                tc = getattr(cell, "_tc", None)
                key = id(tc) if tc is not None else id(cell)
                row_keys.append(key)
                positions_by_key.setdefault(key, []).append((row_idx, col_idx))
                if key not in text_by_key:
                    text_by_key[key] = KnowledgeIngestionService._sanitize_text(getattr(cell, "text", "")).strip()
            key_grid.append(row_keys)

        span_by_key: dict[int, dict[str, int]] = {}
        for key, positions in positions_by_key.items():
            row_start = min(pos[0] for pos in positions)
            row_end = max(pos[0] for pos in positions)
            col_start = min(pos[1] for pos in positions)
            col_end = max(pos[1] for pos in positions)
            span_by_key[key] = {
                "row_start": row_start,
                "row_end": row_end,
                "col_start": col_start,
                "col_end": col_end,
                "row_span": (row_end - row_start) + 1,
                "column_span": (col_end - col_start) + 1,
            }
        return key_grid, text_by_key, span_by_key

    @staticmethod
    def _docx_compact_table_grid(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        span_by_key: Mapping[int, Mapping[str, int]],
    ) -> tuple[list[list[int | None]], dict[int, dict[str, int]], dict[str, Any]]:
        if not key_grid:
            return [], {}, {"removed_blank_rows": 0}

        compacted_grid: list[list[int | None]] = []
        positions_by_key: dict[int, list[tuple[int, int]]] = {}
        removed_blank_rows = 0

        for row in key_grid:
            has_readable_value = any(
                key is not None and str(text_by_key.get(key, "")).strip()
                for key in row
            )
            if not has_readable_value:
                removed_blank_rows += 1
                continue
            new_row = list(row)
            new_row_index = len(compacted_grid)
            compacted_grid.append(new_row)
            for col_idx, key in enumerate(new_row):
                if key is None:
                    continue
                positions_by_key.setdefault(key, []).append((new_row_index, col_idx))

        if not compacted_grid:
            return [], {}, {"removed_blank_rows": removed_blank_rows}

        compacted_spans: dict[int, dict[str, int]] = {}
        for key, positions in positions_by_key.items():
            prior_span = span_by_key.get(key) or {}
            row_start = min(pos[0] for pos in positions)
            row_end = max(pos[0] for pos in positions)
            col_start = min(pos[1] for pos in positions)
            col_end = max(pos[1] for pos in positions)
            compacted_spans[key] = {
                "row_start": row_start,
                "row_end": row_end,
                "col_start": col_start,
                "col_end": col_end,
                "row_span": (row_end - row_start) + 1,
                "column_span": int(prior_span.get("column_span") or ((col_end - col_start) + 1)),
            }

        return compacted_grid, compacted_spans, {"removed_blank_rows": removed_blank_rows}

    @staticmethod
    def _docx_cell_is_helper_token(value: str) -> bool:
        sample = str(value or "").strip()
        if not sample:
            return False
        if re.fullmatch(r"[$€£¥₹]", sample):
            return True
        if sample in {"(", ")", "[", "]"}:
            return True
        return False

    @staticmethod
    def _docx_combine_cell_texts(parts: Sequence[str]) -> str:
        cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
        if not cleaned:
            return ""
        combined = cleaned[0]
        currency_tokens = {"$", "€", "£", "¥", "₹"}
        for part in cleaned[1:]:
            if not combined:
                combined = part
                continue
            if part in {")", "]", "%"}:
                combined = combined.rstrip() + part
                continue
            if part in {"(", "["}:
                combined = combined.rstrip() + part
                continue
            if combined.endswith(tuple(currency_tokens)) or combined.endswith(("(", "[")):
                combined = combined.rstrip() + part
                continue
            combined = combined.rstrip() + " " + part
        return combined.strip()

    @staticmethod
    def _docx_collapse_repeated_sequence(parts: Sequence[str]) -> list[str]:
        items = [str(part or "").strip() for part in parts if str(part or "").strip()]
        size = len(items)
        if size <= 1:
            return items
        for chunk_size in range(1, (size // 2) + 1):
            if size % chunk_size != 0:
                continue
            chunk = items[:chunk_size]
            if chunk * (size // chunk_size) == items:
                return chunk
        return items

    @staticmethod
    def _docx_canonicalize_header_label(label: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(label or "").strip())
        if not cleaned:
            return ""
        tokens = cleaned.split(" ")
        collapsed = KnowledgeIngestionService._docx_collapse_repeated_sequence(tokens)
        return " ".join(collapsed).strip()

    @staticmethod
    def _docx_repeated_row_label_info(values: Sequence[str]) -> tuple[str, int | None]:
        normalized: list[tuple[int, str, str]] = []
        for idx, value in enumerate(values):
            raw = str(value or "").strip()
            if not raw:
                continue
            canonical = KnowledgeIngestionService._docx_canonicalize_header_label(raw)
            if not canonical:
                continue
            normalized.append((idx, canonical, canonical.strip().lower()))
        if len(normalized) < 2:
            return "", None
        lowered = {entry[2] for entry in normalized if entry[2]}
        if len(lowered) != 1:
            return "", None
        first_idx, first_label, _ = normalized[0]
        return first_label, first_idx

    @staticmethod
    def _docx_normalize_column_key(label: str, index: int) -> str:
        normalized = TableDetector._normalize_header_cell(label, index)
        parts = [part for part in str(normalized or "").split("_") if part]
        collapsed = KnowledgeIngestionService._docx_collapse_repeated_sequence(parts)
        if collapsed:
            normalized = "_".join(collapsed)
        return normalized or f"column_{index + 1}"

    @staticmethod
    def _docx_column_key_looks_period_like(key: str) -> bool:
        sample = str(key or "").strip().lower()
        if not sample or sample.startswith("column_"):
            return False
        if re.fullmatch(r"(?:19|20)\d{2}", sample):
            return True
        if re.fullmatch(r"\d{1,2}_\d{2,4}", sample):
            return True
        if re.fullmatch(r"(?:q[1-4]|[1-4]q)(?:_(?:fy)?(?:19|20)?\d{2,4})?", sample):
            return True
        if "thereafter" in sample:
            return True
        return bool(
            re.search(r"(?:19|20)\d{2}", sample)
            and re.search(
                r"\b(?:year|years|quarter|quarters|month|months|ended|ending|june|march|september|december)\b",
                sample.replace("_", " "),
            )
        )

    @staticmethod
    def _docx_column_key_is_generic(key: str) -> bool:
        return bool(re.fullmatch(r"column_\d+", str(key or "").strip().lower()))

    @classmethod
    def _docx_refine_grouped_column_schema(
        cls,
        column_schema: Sequence[str],
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        header_rows: Sequence[int],
    ) -> list[str]:
        schema = list(column_schema or [])
        if not schema or not key_grid:
            return schema

        row_count = len(key_grid)
        header_row_set = set(header_rows)

        def _value_at(row_idx: int, col_idx: int) -> str:
            if row_idx >= row_count:
                return ""
            row = key_grid[row_idx]
            if col_idx >= len(row):
                return ""
            key = row[col_idx]
            if key is None:
                return ""
            return str(text_by_key.get(key, "")).strip()

        profiles: list[dict[str, Any]] = []
        for col_idx, key in enumerate(schema):
            data_values = [
                _value_at(row_idx, col_idx)
                for row_idx in range(row_count)
                if row_idx not in header_row_set and _value_at(row_idx, col_idx)
            ]
            helper_only = bool(data_values) and all(cls._docx_cell_is_helper_token(value) for value in data_values)
            substantive = any(
                value
                and not cls._docx_cell_is_helper_token(value)
                and (_column_numeric_signal(value) or re.search(r"[A-Za-z\u0600-\u06FF]", value))
                for value in data_values
            )
            profiles.append(
                {
                    "key": str(key or "").strip(),
                    "data_count": len(data_values),
                    "helper_only": helper_only,
                    "substantive": substantive,
                    "period_like": cls._docx_column_key_looks_period_like(str(key or "").strip()),
                    "generic": cls._docx_column_key_is_generic(str(key or "").strip()),
                }
            )

        refined = list(schema)
        for col_idx, profile in enumerate(profiles):
            key = profile["key"]
            if not key or not profile["period_like"] or profile["data_count"] <= 0:
                continue
            if col_idx + 1 >= len(profiles):
                continue
            right = profiles[col_idx + 1]
            right_key = str(right["key"] or "").strip()
            if (
                right_key
                and not right["period_like"]
                and not right["generic"]
                and not right["helper_only"]
                and right["data_count"] == 0
            ):
                refined[col_idx] = cls._docx_normalize_column_key(f"{right_key} {key}", col_idx)

        for col_idx, profile in enumerate(profiles[:-1]):
            key = str(refined[col_idx] or "").strip()
            next_key = str(refined[col_idx + 1] or "").strip()
            if not key or key != next_key or not cls._docx_column_key_looks_period_like(key):
                continue
            next_profile = profiles[col_idx + 1]
            if profile["helper_only"] and next_profile["substantive"]:
                refined[col_idx] = cls._docx_normalize_column_key(f"helper {key}", col_idx)
            elif next_profile["helper_only"] and profile["substantive"]:
                refined[col_idx + 1] = cls._docx_normalize_column_key(f"helper {key}", col_idx + 1)

        return refined

    @staticmethod
    def _docx_value_looks_like_period_label(value: str) -> bool:
        sample = KnowledgeIngestionService._docx_canonicalize_header_label(str(value or "").strip())
        if not sample:
            return False
        lowered = sample.lower()
        if re.fullmatch(r"(?:19|20)\d{2}", lowered):
            return True
        if re.fullmatch(r"\d{1,2}/\d{2,4}", lowered):
            return True
        if re.fullmatch(r"(?:q[1-4]|[1-4]q)(?:\s*(?:fy)?\s*(?:19|20)?\d{2,4})?", lowered):
            return True
        if re.fullmatch(
            r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s\-]+(?:19|20)\d{2}",
            lowered,
        ):
            return True
        if (
            re.search(r"\b(?:year|years|quarter|quarters|month|months|ended|ending)\b", lowered)
            and re.search(r"(?:19|20)\d{2}", lowered)
        ):
            return True
        return False

    @staticmethod
    def _docx_normalize_sparse_series_columns(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        span_by_key: Mapping[int, Mapping[str, int]],
    ) -> tuple[list[list[int | None]], dict[int, str], dict[int, dict[str, int]], dict[str, int]]:
        if not key_grid:
            return [], dict(text_by_key), dict(span_by_key), {"merged_columns": 0}

        row_count = len(key_grid)
        column_count = max((len(row) for row in key_grid), default=0)
        if row_count < 2 or column_count < 4:
            return list(map(list, key_grid)), dict(text_by_key), {int(key): dict(value) for key, value in span_by_key.items()}, {"merged_columns": 0}

        def _value_at(row_idx: int, col_idx: int) -> str:
            if row_idx >= row_count:
                return ""
            row = key_grid[row_idx]
            if col_idx >= len(row):
                return ""
            key = row[col_idx]
            if key is None:
                return ""
            return str(text_by_key.get(key, "")).strip()

        series_row_idx: int | None = None
        for candidate_row_idx in range(min(2, row_count)):
            row_values = [_value_at(candidate_row_idx, col_idx) for col_idx in range(column_count)]
            non_empty_values = [value for value in row_values if value]
            if len(non_empty_values) < 3:
                continue
            period_like_count = sum(
                1 for value in non_empty_values if KnowledgeIngestionService._docx_value_looks_like_period_label(value)
            )
            if period_like_count < max(2, int(math.ceil(len(non_empty_values) * 0.6))):
                continue
            duplicate_pairs = 0
            for col_idx in range(1, column_count - 1):
                current_value = KnowledgeIngestionService._docx_canonicalize_header_label(row_values[col_idx])
                next_value = KnowledgeIngestionService._docx_canonicalize_header_label(row_values[col_idx + 1])
                if (
                    current_value
                    and next_value
                    and current_value == next_value
                    and KnowledgeIngestionService._docx_value_looks_like_period_label(current_value)
                ):
                    duplicate_pairs += 1
            if duplicate_pairs >= 2:
                series_row_idx = candidate_row_idx
                break

        if series_row_idx is None:
            return list(map(list, key_grid)), dict(text_by_key), {int(key): dict(value) for key, value in span_by_key.items()}, {"merged_columns": 0}

        actions = ["keep"] * column_count
        merge_targets: dict[int, int] = {}
        data_row_count = max(1, row_count - (series_row_idx + 1))

        for col_idx in range(1, column_count - 1):
            current_label = KnowledgeIngestionService._docx_canonicalize_header_label(_value_at(series_row_idx, col_idx))
            next_label = KnowledgeIngestionService._docx_canonicalize_header_label(_value_at(series_row_idx, col_idx + 1))
            if (
                not current_label
                or not next_label
                or current_label != next_label
                or not KnowledgeIngestionService._docx_value_looks_like_period_label(current_label)
            ):
                continue

            left_values = [_value_at(row_idx, col_idx) for row_idx in range(series_row_idx + 1, row_count)]
            right_values = [_value_at(row_idx, col_idx + 1) for row_idx in range(series_row_idx + 1, row_count)]
            left_non_empty = sum(1 for value in left_values if value)
            right_non_empty = sum(1 for value in right_values if value)
            if left_non_empty == right_non_empty:
                continue

            if left_non_empty < right_non_empty:
                sparse_idx, data_idx = col_idx, col_idx + 1
                sparse_non_empty, data_non_empty = left_non_empty, right_non_empty
            else:
                sparse_idx, data_idx = col_idx + 1, col_idx
                sparse_non_empty, data_non_empty = right_non_empty, left_non_empty

            if sparse_non_empty > 1:
                continue
            if data_non_empty < max(2, int(math.ceil(data_row_count * 0.5))):
                continue
            actions[sparse_idx] = "merge"
            merge_targets[sparse_idx] = data_idx

        if not merge_targets:
            return list(map(list, key_grid)), dict(text_by_key), {int(key): dict(value) for key, value in span_by_key.items()}, {"merged_columns": 0}

        groups: dict[int, list[int]] = {}
        for col_idx, action in enumerate(actions):
            if action == "merge":
                target_idx = merge_targets.get(col_idx)
                if target_idx is None:
                    continue
                groups.setdefault(target_idx, []).append(col_idx)
                groups[target_idx].append(target_idx)
                continue
            groups.setdefault(col_idx, []).append(col_idx)

        ordered_targets = [col_idx for col_idx, action in enumerate(actions) if action == "keep"]
        new_key_grid: list[list[int | None]] = []
        new_text_by_key: dict[int, str] = {}
        positions_by_key: dict[int, list[tuple[int, int]]] = {}
        next_synthetic_key = -1

        for row_idx in range(row_count):
            new_row: list[int | None] = []
            for out_col_idx, target_idx in enumerate(ordered_targets):
                source_indices = sorted(set(groups.get(target_idx, [target_idx])))
                parts: list[str] = []
                source_keys: list[int] = []
                for source_idx in source_indices:
                    if source_idx >= len(key_grid[row_idx]):
                        continue
                    key = key_grid[row_idx][source_idx]
                    if key is None:
                        continue
                    source_keys.append(int(key))
                    value = str(text_by_key.get(key, "")).strip()
                    if value:
                        parts.append(value)

                combined = KnowledgeIngestionService._docx_combine_cell_texts(parts)
                if row_idx == series_row_idx:
                    combined = KnowledgeIngestionService._docx_canonicalize_header_label(combined)
                if not combined:
                    new_row.append(None)
                    continue

                if len(source_keys) == 1 and combined == str(text_by_key.get(source_keys[0], "")).strip():
                    key_to_use = source_keys[0]
                else:
                    key_to_use = next_synthetic_key
                    next_synthetic_key -= 1
                new_text_by_key[key_to_use] = combined
                positions_by_key.setdefault(key_to_use, []).append((row_idx, out_col_idx))
                new_row.append(key_to_use)
            new_key_grid.append(new_row)

        new_span_by_key: dict[int, dict[str, int]] = {}
        for key, positions in positions_by_key.items():
            row_start = min(pos[0] for pos in positions)
            row_end = max(pos[0] for pos in positions)
            col_start = min(pos[1] for pos in positions)
            col_end = max(pos[1] for pos in positions)
            prior_span = span_by_key.get(key) or {}
            new_span_by_key[key] = {
                "row_start": row_start,
                "row_end": row_end,
                "col_start": col_start,
                "col_end": col_end,
                "row_span": int(prior_span.get("row_span") or ((row_end - row_start) + 1)),
                "column_span": int(prior_span.get("column_span") or ((col_end - col_start) + 1)),
            }

        return new_key_grid, new_text_by_key, new_span_by_key, {
            "merged_columns": len(merge_targets),
        }

    @staticmethod
    def _docx_collapse_helper_columns(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        span_by_key: Mapping[int, Mapping[str, int]],
        header_rows: Sequence[int],
    ) -> tuple[list[list[int | None]], dict[int, str], dict[int, dict[str, int]], dict[str, int]]:
        if not key_grid:
            return [], dict(text_by_key), dict(span_by_key), {"removed_columns": 0, "merged_columns": 0}

        row_count = len(key_grid)
        column_count = max((len(row) for row in key_grid), default=0)
        if column_count <= 0:
            return list(map(list, key_grid)), dict(text_by_key), dict(span_by_key), {"removed_columns": 0, "merged_columns": 0}

        header_row_set = set(header_rows)

        def _value_at(row_idx: int, col_idx: int) -> str:
            if row_idx >= row_count:
                return ""
            row = key_grid[row_idx]
            if col_idx >= len(row):
                return ""
            key = row[col_idx]
            if key is None:
                return ""
            return str(text_by_key.get(key, "")).strip()

        column_profiles: list[dict[str, Any]] = []
        for col_idx in range(column_count):
            all_values = [_value_at(row_idx, col_idx) for row_idx in range(row_count)]
            non_empty_values = [value for value in all_values if value]
            header_values = [_value_at(row_idx, col_idx) for row_idx in range(row_count) if row_idx in header_row_set]
            header_values = [value for value in header_values if value]
            data_values = [_value_at(row_idx, col_idx) for row_idx in range(row_count) if row_idx not in header_row_set]
            data_values = [value for value in data_values if value]
            header_fingerprint = tuple(
                re.sub(r"\s+", " ", value).strip().lower()
                for value in header_values
                if value.strip()
            )
            column_profiles.append(
                {
                    "all_values": non_empty_values,
                    "header_values": header_values,
                    "data_values": data_values,
                    "header_fingerprint": header_fingerprint,
                    "helper_only_data": bool(data_values)
                    and all(KnowledgeIngestionService._docx_cell_is_helper_token(value) for value in data_values),
                    "substantive_data": any(
                        value
                        and not KnowledgeIngestionService._docx_cell_is_helper_token(value)
                        and (
                            _column_numeric_signal(value)
                            or re.search(r"[A-Za-z\u0600-\u06FF]", value)
                        )
                        for value in data_values
                    ),
                }
            )

        actions = ["keep"] * column_count
        merge_targets: dict[int, int] = {}

        for col_idx, profile in enumerate(column_profiles):
            all_values = profile["all_values"]
            data_values = profile["data_values"]
            header_fingerprint = profile["header_fingerprint"]

            if not all_values:
                actions[col_idx] = "drop"
                continue

            if not data_values and not header_fingerprint:
                actions[col_idx] = "drop"
                continue

            if not data_values and header_fingerprint:
                prev_fingerprint = column_profiles[col_idx - 1]["header_fingerprint"] if col_idx > 0 else ()
                next_fingerprint = column_profiles[col_idx + 1]["header_fingerprint"] if col_idx + 1 < column_count else ()
                if header_fingerprint == prev_fingerprint or header_fingerprint == next_fingerprint:
                    actions[col_idx] = "drop"
                    continue

            if not profile["helper_only_data"]:
                continue

            helper_values = {value for value in data_values if value}
            prefer_right = helper_values <= {"$", "€", "£", "¥", "₹", "(", "["}
            prefer_left = helper_values <= {")", "]"}

            target_idx: int | None = None
            candidate_indices: list[int] = []
            if prefer_left and col_idx > 0:
                candidate_indices.append(col_idx - 1)
            if prefer_right and col_idx + 1 < column_count:
                candidate_indices.append(col_idx + 1)
            if not candidate_indices:
                if col_idx + 1 < column_count:
                    candidate_indices.append(col_idx + 1)
                if col_idx > 0:
                    candidate_indices.append(col_idx - 1)

            for candidate_idx in candidate_indices:
                if actions[candidate_idx] == "drop":
                    continue
                if column_profiles[candidate_idx]["substantive_data"]:
                    target_idx = candidate_idx
                    break
            if target_idx is None:
                continue
            actions[col_idx] = "merge"
            merge_targets[col_idx] = target_idx

        groups: dict[int, list[int]] = {}
        for col_idx, action in enumerate(actions):
            if action == "drop":
                continue
            if action == "merge":
                target_idx = merge_targets.get(col_idx)
                if target_idx is None or actions[target_idx] == "drop":
                    actions[col_idx] = "drop"
                    continue
                groups.setdefault(target_idx, []).append(col_idx)
                continue
            groups.setdefault(col_idx, []).append(col_idx)

        for target_idx, source_indices in list(groups.items()):
            unique_sources = sorted(set(source_indices + [target_idx]))
            groups[target_idx] = unique_sources

        ordered_targets = [col_idx for col_idx, action in enumerate(actions) if action == "keep"]
        if len(ordered_targets) == column_count and not any(action != "keep" for action in actions):
            return (
                [list(row) for row in key_grid],
                dict(text_by_key),
                {int(key): dict(value) for key, value in span_by_key.items()},
                {"removed_columns": 0, "merged_columns": 0},
            )

        new_key_grid: list[list[int | None]] = []
        new_text_by_key: dict[int, str] = {}
        positions_by_key: dict[int, list[tuple[int, int]]] = {}
        next_synthetic_key = -1

        for row_idx in range(row_count):
            new_row: list[int | None] = []
            for out_col_idx, target_idx in enumerate(ordered_targets):
                source_indices = groups.get(target_idx, [target_idx])
                parts: list[str] = []
                source_keys: list[int] = []
                for source_idx in source_indices:
                    if source_idx >= len(key_grid[row_idx]):
                        continue
                    key = key_grid[row_idx][source_idx]
                    if key is None:
                        continue
                    value = str(text_by_key.get(key, "")).strip()
                    if value:
                        parts.append(value)
                    source_keys.append(int(key))

                combined = KnowledgeIngestionService._docx_combine_cell_texts(parts)
                if row_idx in header_row_set:
                    combined = KnowledgeIngestionService._docx_canonicalize_header_label(combined)
                if not combined:
                    new_row.append(None)
                    continue

                if len(source_keys) == 1 and combined == str(text_by_key.get(source_keys[0], "")).strip():
                    key_to_use = source_keys[0]
                else:
                    key_to_use = next_synthetic_key
                    next_synthetic_key -= 1
                new_text_by_key[key_to_use] = combined
                positions_by_key.setdefault(key_to_use, []).append((row_idx, out_col_idx))
                new_row.append(key_to_use)
            new_key_grid.append(new_row)

        new_span_by_key: dict[int, dict[str, int]] = {}
        for key, positions in positions_by_key.items():
            if key in span_by_key and key >= 0:
                prior_span = span_by_key.get(key) or {}
                row_start = min(pos[0] for pos in positions)
                row_end = max(pos[0] for pos in positions)
                col_start = min(pos[1] for pos in positions)
                col_end = max(pos[1] for pos in positions)
                new_span_by_key[key] = {
                    "row_start": row_start,
                    "row_end": row_end,
                    "col_start": col_start,
                    "col_end": col_end,
                    "row_span": int(prior_span.get("row_span") or ((row_end - row_start) + 1)),
                    "column_span": int(prior_span.get("column_span") or ((col_end - col_start) + 1)),
                }
                continue
            row_start = min(pos[0] for pos in positions)
            row_end = max(pos[0] for pos in positions)
            col_start = min(pos[1] for pos in positions)
            col_end = max(pos[1] for pos in positions)
            new_span_by_key[key] = {
                "row_start": row_start,
                "row_end": row_end,
                "col_start": col_start,
                "col_end": col_end,
                "row_span": (row_end - row_start) + 1,
                "column_span": (col_end - col_start) + 1,
            }

        removed_columns = sum(1 for action in actions if action == "drop")
        merged_columns = sum(1 for action in actions if action == "merge")
        return new_key_grid, new_text_by_key, new_span_by_key, {
            "removed_columns": removed_columns,
            "merged_columns": merged_columns,
        }

    @staticmethod
    def _docx_row_has_financial_data_signal(values: Sequence[str]) -> bool:
        for value in values:
            sample = str(value or "").strip()
            if not sample:
                continue
            if "$" in sample or "%" in sample:
                return True
            if re.search(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b", sample):
                return True
            if re.search(r"\(\s*\d", sample):
                return True
        return False

    @staticmethod
    def _docx_row_looks_like_header_band(values: Sequence[str]) -> bool:
        non_empty = [
            KnowledgeIngestionService._docx_canonicalize_header_label(str(value or "").strip())
            for value in values
            if str(value or "").strip()
        ]
        if not non_empty:
            return False
        if KnowledgeIngestionService._docx_row_has_financial_data_signal(non_empty):
            return False

        short_cell_ratio = sum(
            1
            for value in non_empty
            if len(re.findall(r"\w+", value)) <= 4 and len(value) <= 40
        ) / float(max(1, len(non_empty)))
        if short_cell_ratio < 0.6:
            return False

        lowered = [value.lower() for value in non_empty]
        period_or_header_terms = sum(
            1
            for value in lowered
            if re.search(
                r"\b(year|years|ended|ending|quarter|quarters|fiscal|period|periods|date|dates|record|payment|declaration|month|months|june|march|september|december|thereafter)\b",
                value,
            )
        )
        explicit_year_cells = sum(
            1
            for value in non_empty
            if re.fullmatch(r"(?:19|20)\d{2}", value)
        )
        period_label_cells = sum(
            1
            for value in non_empty
            if KnowledgeIngestionService._docx_value_looks_like_period_label(value)
        )
        if explicit_year_cells >= max(1, len(non_empty) // 2):
            return True
        if period_label_cells >= max(2, int(math.ceil(len(non_empty) * 0.6))):
            return True
        if len(non_empty) == 1 and period_or_header_terms > 0:
            return True
        return period_or_header_terms > 0

    @staticmethod
    def _docx_detect_header_rows(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
    ) -> list[int]:
        row_count = len(key_grid)
        if row_count <= 1:
            return []

        def _row_features(row_keys: Sequence[int | None]) -> dict[str, float]:
            values = [
                str(text_by_key.get(key, "")).strip()
                for key in row_keys
                if key is not None and str(text_by_key.get(key, "")).strip()
            ]
            if not values:
                return {"non_empty": 0.0, "alpha_ratio": 0.0, "numeric_ratio": 0.0}
            alpha_count = sum(1 for value in values if re.search(r"[A-Za-z\u0600-\u06FF]", value))
            numeric_count = sum(1 for value in values if _column_numeric_signal(value))
            total = max(1, len(values))
            return {
                "non_empty": float(len(values)),
                "alpha_ratio": float(alpha_count) / total,
                "numeric_ratio": float(numeric_count) / total,
            }

        probe_rows = min(4, row_count)
        header_rows: list[int] = []
        for row_idx in range(probe_rows):
            values = [
                str(text_by_key.get(key, "")).strip()
                for key in key_grid[row_idx]
                if key is not None and str(text_by_key.get(key, "")).strip()
            ]
            stats = _row_features(key_grid[row_idx])
            header_band = KnowledgeIngestionService._docx_row_looks_like_header_band(values)
            if stats["non_empty"] <= 0:
                if row_idx == 0:
                    continue
                break
            looks_header = stats["alpha_ratio"] >= 0.5 and stats["numeric_ratio"] <= 0.5
            if row_idx == 0:
                if looks_header or header_band or stats["numeric_ratio"] < 0.8:
                    header_rows.append(row_idx)
                continue
            multi_value_header = looks_header and len(values) >= 2
            if (multi_value_header or header_band) and header_rows:
                header_rows.append(row_idx)
                continue
            break

        if len(header_rows) >= row_count:
            header_rows = header_rows[: max(1, row_count - 1)]
        if header_rows:
            next_idx = header_rows[-1] + 1
            if next_idx < row_count - 1 and next_idx not in header_rows:
                next_values = [
                    str(text_by_key.get(key, "")).strip()
                    for key in key_grid[next_idx]
                    if key is not None and str(text_by_key.get(key, "")).strip()
                ]
                if (
                    len(next_values) == 1
                    and KnowledgeIngestionService._docx_value_looks_like_period_label(next_values[0])
                    and not KnowledgeIngestionService._docx_row_has_financial_data_signal(next_values)
                ):
                    following_stats = _row_features(key_grid[next_idx + 1])
                    if following_stats["non_empty"] >= 1:
                        header_rows.append(next_idx)
        return header_rows

    @staticmethod
    def _docx_detect_section_rows(
        key_grid: Sequence[Sequence[int | None]],
        text_by_key: Mapping[int, str],
        header_row_set: set[int],
    ) -> list[int]:
        row_count = len(key_grid)
        if row_count <= 2:
            return []

        def _non_empty_values(row_idx: int) -> list[str]:
            return [
                str(text_by_key.get(key, "")).strip()
                for key in key_grid[row_idx]
                if key is not None and str(text_by_key.get(key, "")).strip()
            ]

        section_rows: list[int] = []
        for row_idx in range(row_count):
            if row_idx in header_row_set:
                continue
            values = _non_empty_values(row_idx)
            repeated_label, _ = KnowledgeIngestionService._docx_repeated_row_label_info(values)
            if len(values) != 1 and not repeated_label:
                continue
            label = repeated_label or values[0]
            normalized = label.strip().lower()
            if not normalized:
                continue
            if re.search(r"\d", normalized) and not KnowledgeIngestionService._docx_value_looks_like_period_label(label):
                continue
            if normalized in {"total", "subtotal", "totals"}:
                continue
            if len(re.findall(r"\w+", label)) > 10 or len(label) > 80:
                continue
            prev_counts = [
                len(_non_empty_values(candidate))
                for candidate in range(max(0, row_idx - 2), row_idx)
                if candidate not in header_row_set
            ]
            next_counts = [
                len(_non_empty_values(candidate))
                for candidate in range(row_idx + 1, min(row_count, row_idx + 3))
                if candidate not in header_row_set
            ]
            if not next_counts:
                continue
            prev_supports_section = max(prev_counts, default=0) >= 2 or not prev_counts
            if prev_supports_section and max(next_counts, default=0) >= 2:
                section_rows.append(row_idx)
        return section_rows

    @staticmethod
    def _normalize_bbox(raw_bbox: Mapping[str, Any] | None) -> dict[str, float] | None:
        if not isinstance(raw_bbox, Mapping):
            return None
        x0 = y0 = x1 = y1 = None
        if all(key in raw_bbox for key in ("x0", "y0", "x1", "y1")):
            x0, y0, x1, y1 = (
                raw_bbox.get("x0"),
                raw_bbox.get("y0"),
                raw_bbox.get("x1"),
                raw_bbox.get("y1"),
            )
        elif all(key in raw_bbox for key in ("left", "top", "right", "bottom")):
            x0, y0, x1, y1 = (
                raw_bbox.get("left"),
                raw_bbox.get("top"),
                raw_bbox.get("right"),
                raw_bbox.get("bottom"),
            )
        elif all(key in raw_bbox for key in ("x", "y", "width", "height")):
            x0 = raw_bbox.get("x")
            y0 = raw_bbox.get("y")
            width = raw_bbox.get("width")
            height = raw_bbox.get("height")
            try:
                x1 = float(x0) + float(width)
                y1 = float(y0) + float(height)
            except (TypeError, ValueError):
                return None
        try:
            parsed = {
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
            }
        except (TypeError, ValueError):
            return None
        if parsed["x1"] <= parsed["x0"] or parsed["y1"] <= parsed["y0"]:
            return None
        return parsed

    @staticmethod
    def _bbox_area(bbox: Mapping[str, float] | None) -> float:
        if not bbox:
            return 0.0
        width = float(bbox.get("x1", 0.0) - bbox.get("x0", 0.0))
        height = float(bbox.get("y1", 0.0) - bbox.get("y0", 0.0))
        if width <= 0.0 or height <= 0.0:
            return 0.0
        return width * height

    @staticmethod
    def _bbox_union(first: Mapping[str, float], second: Mapping[str, float]) -> dict[str, float]:
        return {
            "x0": min(float(first["x0"]), float(second["x0"])),
            "y0": min(float(first["y0"]), float(second["y0"])),
            "x1": max(float(first["x1"]), float(second["x1"])),
            "y1": max(float(first["y1"]), float(second["y1"])),
        }

    @staticmethod
    def _expand_bbox(
        bbox: Mapping[str, float],
        *,
        margin_x: float = 0.0,
        margin_y: float = 0.0,
    ) -> dict[str, float]:
        margin_x = max(0.0, float(margin_x))
        margin_y = max(0.0, float(margin_y))
        return {
            "x0": float(bbox["x0"]) - margin_x,
            "y0": float(bbox["y0"]) - margin_y,
            "x1": float(bbox["x1"]) + margin_x,
            "y1": float(bbox["y1"]) + margin_y,
        }

    @staticmethod
    def _bbox_intersects(first: Mapping[str, float], second: Mapping[str, float]) -> bool:
        return not (
            float(first["x1"]) <= float(second["x0"])
            or float(second["x1"]) <= float(first["x0"])
            or float(first["y1"]) <= float(second["y0"])
            or float(second["y1"]) <= float(first["y0"])
        )

    @classmethod
    def _bbox_edge_distance(cls, first: Mapping[str, float], second: Mapping[str, float]) -> float:
        if cls._bbox_intersects(first, second):
            return 0.0
        dx = max(
            float(second["x0"]) - float(first["x1"]),
            float(first["x0"]) - float(second["x1"]),
            0.0,
        )
        dy = max(
            float(second["y0"]) - float(first["y1"]),
            float(first["y0"]) - float(second["y1"]),
            0.0,
        )
        return math.sqrt((dx * dx) + (dy * dy))

    @classmethod
    def _bbox_overlap_ratio(cls, block_bbox: Mapping[str, float], region_bbox: Mapping[str, float]) -> float:
        block_area = cls._bbox_area(block_bbox)
        if block_area <= 0.0:
            return 0.0
        x0 = max(float(block_bbox["x0"]), float(region_bbox["x0"]))
        y0 = max(float(block_bbox["y0"]), float(region_bbox["y0"]))
        x1 = min(float(block_bbox["x1"]), float(region_bbox["x1"]))
        y1 = min(float(block_bbox["y1"]), float(region_bbox["y1"]))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        overlap_area = (x1 - x0) * (y1 - y0)
        return max(0.0, min(1.0, overlap_area / block_area))

    def _merge_table_regions(
        self,
        regions: Sequence[Mapping[str, float]],
        *,
        page_width: float | None = None,
        page_height: float | None = None,
    ) -> list[dict[str, float]]:
        if not regions:
            return []
        if len(regions) == 1:
            region = regions[0]
            return [
                {
                    "x0": float(region["x0"]),
                    "y0": float(region["y0"]),
                    "x1": float(region["x1"]),
                    "y1": float(region["y1"]),
                }
            ]
        width = max(0.0, float(page_width or 0.0))
        height = max(0.0, float(page_height or 0.0))
        margin_x = max(2.0, width * self.pdf_table_region_merge_x_margin_ratio)
        margin_y = max(2.0, height * self.pdf_table_region_merge_y_margin_ratio)
        pending: list[dict[str, float]] = [
            {
                "x0": float(region["x0"]),
                "y0": float(region["y0"]),
                "x1": float(region["x1"]),
                "y1": float(region["y1"]),
            }
            for region in regions
        ]
        while True:
            merged_any = False
            next_regions: list[dict[str, float]] = []
            while pending:
                current = pending.pop(0)
                current_expanded = self._expand_bbox(current, margin_x=margin_x, margin_y=margin_y)
                compare_index = 0
                while compare_index < len(pending):
                    candidate = pending[compare_index]
                    candidate_expanded = self._expand_bbox(candidate, margin_x=margin_x, margin_y=margin_y)
                    if not self._bbox_intersects(current_expanded, candidate_expanded):
                        compare_index += 1
                        continue
                    current = self._bbox_union(current, candidate)
                    current_expanded = self._expand_bbox(current, margin_x=margin_x, margin_y=margin_y)
                    pending.pop(compare_index)
                    merged_any = True
                next_regions.append(current)
            pending = next_regions
            if not merged_any:
                break
        return sorted(pending, key=lambda bbox: (float(bbox["y0"]), float(bbox["x0"])))

    def _table_regions_by_page(
        self,
        tables: Sequence[TablePayload],
        *,
        pages: Sequence[PageLayout] | None = None,
    ) -> dict[int, list[dict[str, float]]]:
        raw_regions_by_page: dict[int, list[dict[str, float]]] = {}
        for table in tables:
            if not table.page_number:
                continue
            normalized_bbox = self._normalize_bbox(table.bbox)
            if not normalized_bbox:
                continue
            raw_regions_by_page.setdefault(int(table.page_number), []).append(normalized_bbox)
        if not raw_regions_by_page:
            return {}
        page_dimensions: dict[int, tuple[float, float]] = {}
        for page in pages or []:
            page_dimensions[int(page.page_number)] = (float(page.width or 0.0), float(page.height or 0.0))
        merged_regions_by_page: dict[int, list[dict[str, float]]] = {}
        for page_number, page_regions in raw_regions_by_page.items():
            width, height = page_dimensions.get(page_number, (0.0, 0.0))
            merged_regions_by_page[page_number] = self._merge_table_regions(
                page_regions,
                page_width=width,
                page_height=height,
            )
        return merged_regions_by_page

    @staticmethod
    def _has_numeric_table_signal(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if not re.search(r"\d", normalized):
            return False

        # Generic, domain-agnostic "structured numeric" cues:
        # - repeated numeric values (typical for row-wise factual cells),
        # - percentages / currency symbols or common currency codes,
        # - date/time-like tokens,
        # - number + short unit patterns (e.g. 12 kg, 24 hrs).
        number_like_tokens = _TABLE_NUMBER_LIKE_RE.findall(normalized)
        if len(number_like_tokens) >= 2:
            return True
        if _TABLE_NUMERIC_SIGNAL_TOKEN_RE.search(normalized):
            return True
        if _TABLE_DATE_TIME_LIKE_RE.search(normalized):
            return True
        if _TABLE_NUMBER_WITH_UNIT_RE.search(normalized):
            return True
        return False

    @staticmethod
    def _pdf_cell_looks_placeholder(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if re.search(r"[_\.]{3,}|[□☐☑]", normalized):
            return True
        lowered = normalized.lower()
        if re.search(r"\bpage\s+\d+\s+of\s+\d+\b", lowered):
            return True
        if "rev." in lowered or lowered.startswith("rev "):
            return True
        return False

    @staticmethod
    def _pdf_cell_looks_label_like(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if normalized.endswith(":"):
            return True
        words = [word for word in re.findall(r"[A-Za-z]+", normalized) if word]
        if not words:
            return False
        if len(words) > 8:
            return False
        uppercase_ratio = sum(1 for word in words if word.isupper()) / len(words)
        return uppercase_ratio >= 0.75

    def _build_pdf_table_baseline_metrics(
        self,
        pages: Sequence[PageLayout],
        tables: Sequence[TablePayload],
        *,
        overlap_diagnostics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        table_regions_by_page = self._table_regions_by_page(tables, pages=pages)
        text_block_types = {KnowledgeBlockType.PARAGRAPH, KnowledgeBlockType.HEADING}

        total_page_area = 0.0
        total_table_area = 0.0
        checked_text_blocks = 0
        suppressed_text_blocks = 0
        residual_text_blocks = 0
        residual_numeric_blocks = 0
        pages_with_regions = 0

        for page in pages:
            page_regions = table_regions_by_page.get(int(page.page_number), [])
            if not page_regions:
                continue
            pages_with_regions += 1
            page_area = max(0.0, float(page.width or 0.0) * float(page.height or 0.0))
            if page_area > 0.0:
                total_page_area += page_area
                page_table_area = sum(self._bbox_area(region_bbox) for region_bbox in page_regions)
                total_table_area += min(page_area, page_table_area)

            for block in page.blocks:
                if block.block_type not in text_block_types:
                    continue
                block_text = self._sanitize_text(block.text).strip()
                if not block_text:
                    continue
                checked_text_blocks += 1
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block_meta.get("table_overlap_candidate") or block_meta.get("suppress_text_chunk"):
                    suppressed_text_blocks += 1
                residual_text_blocks += 1
                if self._has_numeric_table_signal(block_text):
                    residual_numeric_blocks += 1

        row_labels: set[str] = set()
        for table in tables:
            row_labels.update(self._table_row_label_set(table))

        coverage_ratio = (total_table_area / total_page_area) if total_page_area > 0.0 else 0.0
        residual_ratio = (residual_text_blocks / checked_text_blocks) if checked_text_blocks > 0 else 0.0

        metrics: dict[str, Any] = {
            "table_bbox_coverage_ratio": round(max(0.0, min(1.0, coverage_ratio)), 4),
            "table_pages_with_regions": pages_with_regions,
            "checked_text_blocks_count": checked_text_blocks,
            "suppressed_text_blocks_count": suppressed_text_blocks,
            "residual_text_blocks_count": residual_text_blocks,
            "residual_text_ratio": round(max(0.0, min(1.0, residual_ratio)), 4),
            "residual_text_with_numeric_signals_count": residual_numeric_blocks,
            "table_row_unique_evidence_count": len(row_labels),
        }
        if isinstance(overlap_diagnostics, Mapping):
            metrics["overlap_threshold"] = overlap_diagnostics.get("threshold")
            metrics["overlap_checked_text_blocks"] = int(overlap_diagnostics.get("checked_text_blocks") or 0)
            metrics["overlap_suppressed_text_blocks"] = int(overlap_diagnostics.get("suppressed_text_blocks") or 0)
        return metrics

    def _annotate_pdf_blocks_with_table_overlap(
        self,
        pages: Sequence[PageLayout],
        tables: Sequence[TablePayload],
    ) -> tuple[list[PageLayout], dict[str, Any]]:
        table_regions_by_page = self._table_regions_by_page(tables, pages=pages)

        diagnostics: dict[str, Any] = {
            "enabled": True,
            "threshold": round(self.pdf_table_text_overlap_min_ratio, 4),
            "residual_overlap_threshold": round(self.pdf_table_residual_overlap_min_ratio, 4),
            "residual_near_region_ratio": round(self.pdf_table_residual_near_region_ratio, 4),
            "table_regions": sum(len(v) for v in table_regions_by_page.values()),
            "checked_text_blocks": 0,
            "overlapping_text_blocks": 0,
            "suppressed_text_blocks": 0,
            "table_residual_blocks": 0,
            "table_residual_numeric_blocks": 0,
        }
        if not pages or not table_regions_by_page:
            diagnostics["reason"] = "no_pages_or_table_regions"
            return list(pages), diagnostics

        text_block_types = {KnowledgeBlockType.PARAGRAPH, KnowledgeBlockType.HEADING}
        annotated_pages: list[PageLayout] = []
        for page in pages:
            page_regions = table_regions_by_page.get(int(page.page_number), [])
            if not page_regions:
                annotated_pages.append(page)
                continue

            page_checked = 0
            page_suppressed = 0
            page_residual = 0
            page_diagonal = math.sqrt((float(page.width or 0.0) ** 2) + (float(page.height or 0.0) ** 2))
            near_distance_threshold = max(2.0, page_diagonal * self.pdf_table_residual_near_region_ratio)
            updated_blocks: list[PageBlockPayload] = []
            for block in page.blocks:
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block.block_type not in text_block_types:
                    updated_blocks.append(block)
                    continue

                block_text = self._sanitize_text(block.text).strip()
                numeric_signal = self._has_numeric_table_signal(block_text) if block_text else False
                page_checked += 1
                diagnostics["checked_text_blocks"] = int(diagnostics["checked_text_blocks"]) + 1
                normalized_block_bbox = self._normalize_bbox(block.bbox)
                overlap_ratio = 0.0
                nearest_distance: float | None = None
                best_region_index: int | None = None
                if normalized_block_bbox:
                    best_overlap = -1.0
                    best_distance = float("inf")
                    for region_index, region_bbox in enumerate(page_regions):
                        overlap = self._bbox_overlap_ratio(normalized_block_bbox, region_bbox)
                        distance = self._bbox_edge_distance(normalized_block_bbox, region_bbox)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best_distance = distance
                            best_region_index = region_index
                            continue
                        if math.isclose(overlap, best_overlap, rel_tol=1e-6, abs_tol=1e-6) and distance < best_distance:
                            best_distance = distance
                            best_region_index = region_index
                    if best_overlap > 0.0:
                        overlap_ratio = best_overlap
                    nearest_distance = best_distance if best_region_index is not None else None
                if overlap_ratio > 0.0:
                    diagnostics["overlapping_text_blocks"] = int(diagnostics["overlapping_text_blocks"]) + 1
                near_table_region = bool(
                    nearest_distance is not None and nearest_distance <= near_distance_threshold
                )

                updated_meta = dict(block_meta)
                if overlap_ratio > 0.0:
                    updated_meta["table_overlap_ratio"] = round(overlap_ratio, 4)
                    updated_meta["overlaps_table_region"] = True
                if nearest_distance is not None:
                    updated_meta["table_region_distance"] = round(nearest_distance, 4)
                if best_region_index is not None:
                    updated_meta["table_region_index"] = int(best_region_index)
                    updated_meta["table_region_key"] = f"p{page.page_number}-r{best_region_index}"
                if overlap_ratio >= self.pdf_table_text_overlap_min_ratio:
                    updated_meta["table_overlap_candidate"] = True
                    updated_meta["table_overlap_candidate_reason"] = "table_overlap"
                    page_suppressed += 1
                    diagnostics["suppressed_text_blocks"] = int(diagnostics["suppressed_text_blocks"]) + 1
                elif numeric_signal and (
                    overlap_ratio >= self.pdf_table_residual_overlap_min_ratio or near_table_region
                ):
                    updated_meta["table_residual_candidate"] = True
                    updated_meta["table_residual"] = True
                    updated_meta["region_role"] = "table_residual"
                    updated_meta["content_source"] = "table_residual"
                    updated_meta["search_tier"] = "fallback"
                    if overlap_ratio >= self.pdf_table_residual_overlap_min_ratio:
                        updated_meta["table_residual_reason"] = "numeric_overlap"
                    else:
                        updated_meta["table_residual_reason"] = "numeric_near_table_region"
                    page_residual += 1
                    diagnostics["table_residual_blocks"] = int(diagnostics["table_residual_blocks"]) + 1
                    diagnostics["table_residual_numeric_blocks"] = int(diagnostics["table_residual_numeric_blocks"]) + 1

                updated_blocks.append(
                    PageBlockPayload(
                        block_type=block.block_type,
                        order_index=block.order_index,
                        text=block.text,
                        bbox=block.bbox,
                        section_heading=block.section_heading,
                        heading_path=list(block.heading_path or []),
                        detected_language=block.detected_language,
                        confidence=block.confidence,
                        metadata=updated_meta,
                    )
                )

            updated_page_meta = dict(page.metadata or {})
            updated_page_meta["table_overlap_checked_blocks"] = page_checked
            if page_suppressed:
                updated_page_meta["table_overlap_suppressed_blocks"] = page_suppressed
                updated_page_meta["table_overlap_threshold"] = round(
                    self.pdf_table_text_overlap_min_ratio,
                    4,
                )
            if page_residual:
                updated_page_meta["table_residual_blocks"] = page_residual
                updated_page_meta["table_residual_overlap_threshold"] = round(
                    self.pdf_table_residual_overlap_min_ratio,
                    4,
                )
            annotated_pages.append(
                PageLayout(
                    page_number=page.page_number,
                    width=page.width,
                    height=page.height,
                    rotation=page.rotation,
                    text_density=page.text_density,
                    has_ocr_content=page.has_ocr_content,
                    content_type=page.content_type,
                    blocks=updated_blocks,
                    metadata=updated_page_meta,
                )
            )
        return annotated_pages, diagnostics

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
            # Align with read_knowledge: "visible body rows" exclude header + section_header.
            data_rows = len(
                [
                    row
                    for row in (table.rows or [])
                    if str((row.metadata or {}).get("row_type") or "").strip().lower()
                    not in {"header", "section_header"}
                ]
            )
            weight = 1.0 + (min(5, data_rows) / 5.0)
            total_score += quality * weight
        return round(total_score, 4)

    def _candidate_selection_metrics(self, tables: Sequence[TablePayload]) -> dict[str, float | int]:
        if not tables:
            return {
                "table_count": 0,
                "total_data_rows": 0,
                "avg_data_rows": 0.0,
                "micro_table_count": 0,
                "micro_table_ratio": 0.0,
                "distinct_title_count": 0,
                "distinct_title_ratio": 0.0,
                "total_bbox_area": 0.0,
                "valid_bbox_count": 0,
            }

        data_rows_per_table: list[int] = []
        micro_table_count = 0
        titles: list[str] = []
        total_bbox_area = 0.0
        valid_bbox_count = 0
        for table in tables:
            data_rows = len(
                [
                    row
                    for row in (table.rows or [])
                    if str((row.metadata or {}).get("row_type") or "").strip().lower()
                    not in {"header", "section_header"}
                ]
            )
            data_rows_per_table.append(data_rows)
            if data_rows <= 2:
                micro_table_count += 1
            normalized_title = self._normalize_evidence_phrase(str(table.title or ""))
            if normalized_title:
                titles.append(normalized_title)
            normalized_bbox = self._normalize_bbox(table.bbox)
            bbox_area = self._bbox_area(normalized_bbox)
            if bbox_area > 0.0:
                total_bbox_area += bbox_area
                valid_bbox_count += 1

        table_count = len(tables)
        total_data_rows = sum(data_rows_per_table)
        distinct_title_count = len(set(titles))
        distinct_title_ratio = (
            (distinct_title_count / table_count) if table_count else 0.0
        )
        micro_table_ratio = (micro_table_count / table_count) if table_count else 0.0
        avg_data_rows = (total_data_rows / table_count) if table_count else 0.0
        return {
            "table_count": table_count,
            "total_data_rows": total_data_rows,
            "avg_data_rows": round(avg_data_rows, 4),
            "micro_table_count": micro_table_count,
            "micro_table_ratio": round(micro_table_ratio, 4),
            "distinct_title_count": distinct_title_count,
            "distinct_title_ratio": round(distinct_title_ratio, 4),
            "total_bbox_area": round(total_bbox_area, 4),
            "valid_bbox_count": valid_bbox_count,
        }

    def _maybe_override_fragmented_heuristic_selection(
        self,
        selected: str,
        *,
        candidates: Mapping[str, list[TablePayload]],
        scores: Mapping[str, float],
        metrics: Mapping[str, Mapping[str, float | int]],
    ) -> tuple[str, dict[str, Any]]:
        diag: dict[str, Any] = {
            "heuristic_override_applied": False,
            "heuristic_override_reason": "",
        }
        if not selected.startswith("heuristic"):
            diag["heuristic_override_reason"] = "selected_not_heuristic"
            return selected, diag

        non_heuristic_names = [name for name in candidates.keys() if not name.startswith("heuristic")]
        if not non_heuristic_names:
            diag["heuristic_override_reason"] = "no_non_heuristic_candidate"
            return selected, diag

        best_non_heuristic = max(
            non_heuristic_names,
            key=lambda name: (scores.get(name, 0.0), len(candidates.get(name) or []), name),
        )
        selected_metrics = metrics.get(selected) or {}
        non_heuristic_metrics = metrics.get(best_non_heuristic) or {}

        selected_table_count = int(selected_metrics.get("table_count") or 0)
        selected_micro_ratio = float(selected_metrics.get("micro_table_ratio") or 0.0)
        selected_title_ratio = float(selected_metrics.get("distinct_title_ratio") or 0.0)
        selected_bbox_area = float(selected_metrics.get("total_bbox_area") or 0.0)
        selected_total_rows = int(selected_metrics.get("total_data_rows") or 0)
        non_heuristic_bbox_area = float(non_heuristic_metrics.get("total_bbox_area") or 0.0)
        non_heuristic_total_rows = int(non_heuristic_metrics.get("total_data_rows") or 0)
        non_heuristic_micro_ratio = float(non_heuristic_metrics.get("micro_table_ratio") or 0.0)
        selected_score = float(scores.get(selected) or 0.0)
        non_heuristic_score = float(scores.get(best_non_heuristic) or 0.0)

        suspicious_flags: list[str] = []
        if selected_table_count >= 3 and selected_micro_ratio >= 0.75:
            suspicious_flags.append("fragmented_micro_tables")
        if selected_table_count >= 3 and selected_title_ratio <= 0.4:
            suspicious_flags.append("repeated_titles")
        if (
            selected_bbox_area > 0.0
            and non_heuristic_bbox_area > 0.0
            and selected_bbox_area <= (non_heuristic_bbox_area * 0.45)
        ):
            suspicious_flags.append("low_bbox_coverage")

        comparable_non_heuristic = non_heuristic_score >= (selected_score * 0.35)
        large_coverage_gain = (
            selected_bbox_area > 0.0
            and non_heuristic_bbox_area >= (selected_bbox_area * 2.5)
        )
        strong_row_gain = non_heuristic_total_rows >= max(8, selected_total_rows * 2)
        viable_non_heuristic_shape = non_heuristic_micro_ratio <= 0.6
        fallback_fragmentation_override = (
            len(suspicious_flags) >= 2
            and large_coverage_gain
            and strong_row_gain
            and viable_non_heuristic_shape
        )

        if len(suspicious_flags) >= 2 and (
            comparable_non_heuristic or fallback_fragmentation_override
        ):
            override_reason = (
                "suspicious_fragmentation"
                if comparable_non_heuristic
                else "suspicious_fragmentation_low_coverage_rows"
            )
            diag.update(
                {
                    "heuristic_override_applied": True,
                    "heuristic_override_reason": override_reason,
                    "heuristic_override_flags": suspicious_flags,
                    "heuristic_override_from": selected,
                    "heuristic_override_to": best_non_heuristic,
                }
            )
            return best_non_heuristic, diag

        diag.update(
            {
                "heuristic_override_reason": "not_triggered",
                "heuristic_override_flags": suspicious_flags,
                "heuristic_override_candidate": best_non_heuristic,
                "heuristic_override_comparable_non_heuristic": comparable_non_heuristic,
                "heuristic_override_fallback_fragmentation": fallback_fragmentation_override,
            }
        )
        return selected, diag

    def _table_region_candidate_score(self, table: TablePayload) -> float:
        assessment = self._assess_table_quality(table)
        quality = float(assessment.get("quality_score") or 0.0)
        structure_conf = self._get_table_structure_confidence(table)
        confidence = float(structure_conf if isinstance(structure_conf, (int, float)) else 0.0)
        return round(quality * confidence, 4)

    def _table_region_candidate_rank(
        self,
        table: TablePayload,
    ) -> tuple[int, int, float, int, str, str, list[str]]:
        assessment = self._assess_table_quality(table)
        candidate_class, decision, reasons = self._classify_pdf_table_candidate(
            table,
            assessment,
            recurrence_stats=None,
        )
        region_score = self._table_region_candidate_score(table)
        data_rows = len(self._pdf_table_readable_rows(table))
        class_rank = {
            "strong_table": 3,
            "weak_table": 2,
            "layout_fragment": 1,
            "recurring_scaffold": 0,
        }.get(candidate_class, 0)
        keep_rank = 1 if decision == "keep" else 0
        return keep_rank, class_rank, region_score, data_rows, candidate_class, decision, reasons

    def _extractor_is_primary_table_candidate(self, extractor_name: str) -> bool:
        normalized = str(extractor_name or "").strip().lower()
        return bool(normalized) and not normalized.startswith("heuristic")

    def _table_region_membership_score(self, left: TablePayload, right: TablePayload) -> float:
        overlap_ratio = self._table_region_overlap_ratio(left, right)
        left_bbox = self._normalize_bbox(left.bbox)
        right_bbox = self._normalize_bbox(right.bbox)
        if not left_bbox or not right_bbox:
            return overlap_ratio

        ix0 = max(left_bbox["x0"], right_bbox["x0"])
        iy0 = max(left_bbox["y0"], right_bbox["y0"])
        ix1 = min(left_bbox["x1"], right_bbox["x1"])
        iy1 = min(left_bbox["y1"], right_bbox["y1"])
        if ix1 <= ix0 or iy1 <= iy0:
            return overlap_ratio

        intersection = (ix1 - ix0) * (iy1 - iy0)
        left_area = self._bbox_area(left_bbox)
        right_area = self._bbox_area(right_bbox)
        containment = max(
            intersection / max(left_area, 1.0),
            intersection / max(right_area, 1.0),
        )
        return max(overlap_ratio, containment)

    def _select_table_candidates_by_region(
        self,
        candidates: Mapping[str, list[TablePayload]],
        *,
        document_selected: str,
    ) -> tuple[str, list[TablePayload], dict[str, Any]] | None:
        region_items: list[dict[str, Any]] = []
        for extractor_name, tables in candidates.items():
            for table in tables:
                bbox = self._normalize_bbox(table.bbox)
                if not bbox:
                    continue
                keep_rank, class_rank, region_score, data_rows, candidate_class, decision, reasons = (
                    self._table_region_candidate_rank(table)
                )
                region_items.append(
                    {
                        "extractor": extractor_name,
                        "table": table,
                        "page": int(table.page_number or 0),
                        "bbox": bbox,
                        "region_score": region_score,
                        "data_rows": data_rows,
                        "keep_rank": keep_rank,
                        "class_rank": class_rank,
                        "candidate_class": candidate_class,
                        "candidate_decision": decision,
                        "candidate_reasons": reasons,
                    }
                )
        if len(region_items) < 2:
            return None

        regions: list[list[dict[str, Any]]] = []
        for item in sorted(
            region_items,
            key=lambda entry: (
                entry["page"],
                float(entry["bbox"]["y0"]),
                float(entry["bbox"]["x0"]),
                entry["extractor"],
            ),
        ):
            matched_region: list[dict[str, Any]] | None = None
            for region in regions:
                if region[0]["page"] != item["page"]:
                    continue
                if any(
                    self._table_region_membership_score(member["table"], item["table"]) >= 0.35
                    for member in region
                ):
                    matched_region = region
                    break
            if matched_region is None:
                regions.append([item])
            else:
                matched_region.append(item)

        blended_tables: list[TablePayload] = []
        region_choices: list[dict[str, Any]] = []
        chosen_extractors: list[str] = []
        chosen_region_entries: list[dict[str, Any]] = []
        document_selected_region_entries: list[dict[str, Any] | None] = []

        for region_index, region in enumerate(regions, start=1):
            primary_region = [
                entry for entry in region if self._extractor_is_primary_table_candidate(entry["extractor"])
            ]
            candidate_pool = primary_region or region
            best = max(
                candidate_pool,
                key=lambda entry: (
                    entry["keep_rank"],
                    entry["class_rank"],
                    entry["region_score"],
                    entry["data_rows"],
                    entry["extractor"],
                ),
            )
            document_selected_pool = [
                entry for entry in region if str(entry["extractor"] or "").strip().lower() == document_selected
            ]
            document_best = (
                max(
                    document_selected_pool,
                    key=lambda entry: (
                        entry["keep_rank"],
                        entry["class_rank"],
                        entry["region_score"],
                        entry["data_rows"],
                        entry["extractor"],
                    ),
                )
                if document_selected_pool
                else None
            )
            blended_tables.append(best["table"])
            chosen_extractors.append(best["extractor"])
            chosen_region_entries.append(best)
            document_selected_region_entries.append(document_best)
            region_choices.append(
                {
                    "region_index": region_index,
                    "page_number": best["page"],
                    "selected_extractor": best["extractor"],
                    "selected_order_index": best["table"].order_index,
                    "selected_score": best["region_score"],
                    "selected_class": best["candidate_class"],
                    "selected_decision": best["candidate_decision"],
                    "selected_reasons": best["candidate_reasons"],
                    "candidate_extractors": [entry["extractor"] for entry in region],
                    "primary_candidate_extractors": [entry["extractor"] for entry in primary_region],
                    "heuristic_only_region": not primary_region,
                }
            )

        unique_extractors = sorted(set(chosen_extractors))
        document_selected_gap_improved = any(
            chosen.get("candidate_decision") == "keep"
            and (document_best is None or document_best.get("candidate_decision") != "keep")
            for chosen, document_best in zip(chosen_region_entries, document_selected_region_entries)
        )
        if len(unique_extractors) <= 1 and unique_extractors and unique_extractors[0] == document_selected:
            return None
        if not document_selected_gap_improved:
            return None

        blended_tables.sort(
            key=lambda table: (
                int(table.page_number or 0),
                float((self._normalize_bbox(table.bbox) or {}).get("y0", 0.0)),
                table.order_index,
            )
        )
        return "auto:region_blend", blended_tables, {
            "selection_mode": "candidate_region_blend_v1",
            "region_blend_applied": True,
            "region_blend_document_selected": document_selected,
            "region_blend_extractors_used": unique_extractors,
            "region_blend_region_count": len(regions),
            "region_blend_regions": region_choices,
        }

    def _table_runtime_flags(self, upload: KnowledgeUpload | None) -> dict[str, Any]:
        business = getattr(upload, "business_profile", None) if upload else None
        feature_state = FeatureFlagService.snapshot(business)
        business_metadata: Mapping[str, Any] = {}
        if business is not None and isinstance(getattr(business, "metadata", None), Mapping):
            business_metadata = getattr(business, "metadata") or {}
        cohort = str(business_metadata.get("cohort") or "").strip() or None
        return {
            "shadow_ingestion_enabled": bool(getattr(feature_state, "rag_shadow_ingestion", False)),
            "eval_logging_enabled": bool(getattr(feature_state, "rag_eval_logging", False)),
            "cohort": cohort,
        }

    def _select_table_candidates(
        self,
        candidates: Mapping[str, list[TablePayload]],
    ) -> tuple[str, list[TablePayload], dict[str, Any]]:
        if not candidates:
            return "none", [], {"scores": {}}
        scores = {name: self._score_table_set(tables) for name, tables in candidates.items()}
        metrics = {name: self._candidate_selection_metrics(tables) for name, tables in candidates.items()}

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
            selected, override_diag = self._maybe_override_fragmented_heuristic_selection(
                selected,
                candidates=candidates,
                scores=scores,
                metrics=metrics,
            )
        else:
            override_diag = {
                "heuristic_override_applied": False,
                "heuristic_override_reason": "preferred_extractor",
            }
        selection_meta: dict[str, Any] = {
            "scores": scores,
            "metrics": metrics,
            "selection_mode": "candidate_scorer_v2",
            **override_diag,
        }

        if preferred in {"", "auto"}:
            region_blend = self._select_table_candidates_by_region(
                candidates,
                document_selected=selected,
            )
            if region_blend is not None:
                blended_name, blended_tables, region_meta = region_blend
                selection_meta.update(region_meta)
                return blended_name, blended_tables, selection_meta

        return selected, candidates.get(selected, []), selection_meta

    @staticmethod
    def _table_data_rows(table: TablePayload) -> list[TableRowPayload]:
        return [
            row
            for row in (table.rows or [])
            if str((row.metadata or {}).get("row_type") or "").strip().lower()
            not in {"header", "section_header"}
        ]

    def _table_column_count(self, table: TablePayload) -> int:
        schema_count = len(table.column_schema or [])
        row_count = max((len(row.cells or []) for row in (table.rows or [])), default=0)
        return max(schema_count, row_count)

    @staticmethod
    def _table_non_empty_cell_count(table: TablePayload) -> int:
        count = 0
        for row in table.rows or []:
            if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header":
                continue
            for cell in row.cells or []:
                if str(cell.raw_text or "").strip():
                    count += 1
        return count

    def _table_row_labels(self, table: TablePayload, *, limit: int = 500) -> list[str]:
        labels: list[str] = []
        for row in self._table_data_rows(table):
            value = ""
            for cell in row.cells or []:
                if int(getattr(cell, "column_index", -1)) == 0:
                    value = self._normalize_evidence_phrase(str(cell.raw_text or ""))
                    break
            if not value:
                value = self._normalize_evidence_phrase(str(row.raw_text or ""))
            if value:
                labels.append(value)
            if limit and len(labels) >= limit:
                break
        return labels

    @staticmethod
    def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
        if not left or not right:
            return 0
        previous = [0] * (len(right) + 1)
        current = [0] * (len(right) + 1)
        for token_left in left:
            for idx, token_right in enumerate(right, start=1):
                if token_left == token_right:
                    current[idx] = previous[idx - 1] + 1
                else:
                    current[idx] = max(previous[idx], current[idx - 1])
            previous, current = current, [0] * (len(right) + 1)
        return previous[-1]

    @classmethod
    def _lcs_ratio(cls, left: Sequence[str], right: Sequence[str]) -> float:
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        lcs = cls._lcs_length(left, right)
        return float(lcs) / float(max(len(left), len(right), 1))

    def _table_with_scope_annotations_for_guardrails(self, table: TablePayload) -> TablePayload:
        if not table.rows or len(table.column_schema or []) < 3:
            return table
        data_rows = self._table_data_rows(table)
        if not data_rows:
            return table

        has_scope_meta = True
        for row in data_rows:
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            inferred_scope = row_meta.get("inferred_scope_columns")
            scope_reason = row_meta.get("scope_reason")
            if inferred_scope is None or scope_reason is None:
                has_scope_meta = False
                break
        if has_scope_meta:
            return table

        header_rows: set[int] = {
            int(row.row_index)
            for row in (table.rows or [])
            if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"
        }
        annotator = AzureDocumentIntelligenceExtractor(endpoint=None, key=None)
        annotated_rows = annotator._annotate_row_applicability(
            table_rows=table.rows or [],
            column_schema=table.column_schema or [],
            header_rows=header_rows,
        )
        meta = dict(table.metadata or {})
        meta["scope_guardrail_annotated"] = True
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=table.column_schema,
            data_dictionary=dict(table.data_dictionary or {}),
            metadata=meta,
            rows=annotated_rows,
        )

    def _table_scope_snapshot(self, table: TablePayload) -> dict[str, int]:
        rows_with_scope = 0
        rows_with_non_abstain = 0
        rows_with_confidence = 0
        scope_axis_violations = 0
        for row in self._table_data_rows(table):
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            inferred_scope = self._clean_scope_labels(row_meta.get("inferred_scope_columns"))
            scope_dimensions = self._clean_scope_labels(row_meta.get("scope_dimension_columns"))
            if inferred_scope:
                rows_with_scope += 1
            reason = canonical_scope_reason(row_meta.get("scope_reason"))
            if inferred_scope and reason != SCOPE_REASON_ABSTAIN:
                rows_with_non_abstain += 1
            confidence = self._coerce_scope_confidence(row_meta.get("scope_confidence"))
            if inferred_scope and confidence is not None:
                rows_with_confidence += 1
            if inferred_scope and scope_dimensions:
                scope_set = set(scope_dimensions)
                if any(label not in scope_set for label in inferred_scope):
                    scope_axis_violations += 1
        return {
            "rows_with_scope": rows_with_scope,
            "rows_with_non_abstain": rows_with_non_abstain,
            "rows_with_confidence": rows_with_confidence,
            "scope_axis_violations": scope_axis_violations,
        }

    def _table_guardrail_snapshot(self, table: TablePayload) -> dict[str, Any]:
        data_rows = self._table_data_rows(table)
        labels = self._table_row_labels(table)
        scope = self._table_scope_snapshot(table)
        return {
            "data_row_count": len(data_rows),
            "column_count": self._table_column_count(table),
            "non_empty_cell_count": self._table_non_empty_cell_count(table),
            "row_labels": labels,
            **scope,
        }

    def _evaluate_vlm_guardrails(
        self,
        *,
        baseline: TablePayload,
        candidate: TablePayload,
    ) -> tuple[bool, dict[str, Any], TablePayload]:
        baseline_scoped = self._table_with_scope_annotations_for_guardrails(baseline)
        candidate_scoped = self._table_with_scope_annotations_for_guardrails(candidate)

        baseline_snapshot = self._table_guardrail_snapshot(baseline_scoped)
        candidate_snapshot = self._table_guardrail_snapshot(candidate_scoped)

        baseline_rows = int(baseline_snapshot.get("data_row_count") or 0)
        candidate_rows = int(candidate_snapshot.get("data_row_count") or 0)
        row_recall = float(candidate_rows) / float(max(1, baseline_rows))

        baseline_labels = list(baseline_snapshot.get("row_labels") or [])
        candidate_labels = list(candidate_snapshot.get("row_labels") or [])
        row_order_ratio = self._lcs_ratio(baseline_labels, candidate_labels)

        baseline_columns = int(baseline_snapshot.get("column_count") or 0)
        candidate_columns = int(candidate_snapshot.get("column_count") or 0)
        schema_recall = float(candidate_columns) / float(max(1, baseline_columns))

        baseline_cells = int(baseline_snapshot.get("non_empty_cell_count") or 0)
        candidate_cells = int(candidate_snapshot.get("non_empty_cell_count") or 0)
        cell_recall = float(candidate_cells) / float(max(1, baseline_cells))

        baseline_scope_rows = int(baseline_snapshot.get("rows_with_scope") or 0)
        candidate_scope_rows = int(candidate_snapshot.get("rows_with_scope") or 0)
        scope_row_recall = (
            float(candidate_scope_rows) / float(max(1, baseline_scope_rows))
            if baseline_scope_rows > 0
            else 1.0
        )

        baseline_scope_non_abstain = int(baseline_snapshot.get("rows_with_non_abstain") or 0)
        candidate_scope_non_abstain = int(candidate_snapshot.get("rows_with_non_abstain") or 0)
        scope_non_abstain_recall = (
            float(candidate_scope_non_abstain) / float(max(1, baseline_scope_non_abstain))
            if baseline_scope_non_abstain > 0
            else 1.0
        )

        baseline_axis_violations = int(baseline_snapshot.get("scope_axis_violations") or 0)
        candidate_axis_violations = int(candidate_snapshot.get("scope_axis_violations") or 0)

        reasons: list[str] = []
        soft_signals: list[str] = []
        if self.table_vlm_guardrails_enabled:
            hard_row_floor = max(
                0.0,
                min(1.0, float(self.table_vlm_guardrail_hard_row_recall_floor)),
            )
            row_merge_normalization = bool(
                row_recall < self.table_vlm_guardrail_min_row_recall
                and cell_recall >= max(self.table_vlm_guardrail_min_cell_recall, 1.08)
                and schema_recall >= self.table_vlm_guardrail_min_schema_recall
            )
            if row_recall < self.table_vlm_guardrail_min_row_recall:
                if row_recall < hard_row_floor:
                    reasons.append("row_coverage_regression")
                elif row_merge_normalization:
                    soft_signals.append("row_count_normalization")
                else:
                    reasons.append("row_coverage_regression")
            if baseline_rows >= 3 and baseline_labels and candidate_labels:
                if row_order_ratio < self.table_vlm_guardrail_min_order_ratio:
                    if row_recall >= self.table_vlm_guardrail_min_row_recall:
                        reasons.append("row_order_regression")
                    elif row_merge_normalization:
                        soft_signals.append("row_order_shift_with_row_merge")
                    else:
                        reasons.append("row_order_regression")
            if schema_recall < self.table_vlm_guardrail_min_schema_recall:
                reasons.append("schema_coverage_regression")
            if cell_recall < self.table_vlm_guardrail_min_cell_recall:
                reasons.append("value_coverage_regression")
            if baseline_scope_rows > 0 and scope_row_recall < 1.0:
                if row_merge_normalization:
                    soft_signals.append("scope_row_delta_with_row_merge")
                else:
                    reasons.append("scope_row_regression")
            if baseline_scope_non_abstain > 0 and scope_non_abstain_recall < 1.0:
                if row_merge_normalization:
                    soft_signals.append("scope_quality_delta_with_row_merge")
                else:
                    reasons.append("scope_quality_regression")
            if candidate_axis_violations > baseline_axis_violations:
                reasons.append("scope_axis_violation_increase")
        else:
            row_merge_normalization = False

        diagnostics = {
            "accepted": not reasons,
            "rejection_reasons": reasons,
            "soft_signals": soft_signals,
            "thresholds": {
                "row_recall": self.table_vlm_guardrail_min_row_recall,
                "hard_row_recall_floor": self.table_vlm_guardrail_hard_row_recall_floor,
                "row_order_ratio": self.table_vlm_guardrail_min_order_ratio,
                "schema_recall": self.table_vlm_guardrail_min_schema_recall,
                "cell_recall": self.table_vlm_guardrail_min_cell_recall,
            },
            "metrics": {
                "row_recall": round(row_recall, 4),
                "row_order_ratio": round(row_order_ratio, 4),
                "schema_recall": round(schema_recall, 4),
                "cell_recall": round(cell_recall, 4),
                "scope_row_recall": round(scope_row_recall, 4),
                "scope_non_abstain_recall": round(scope_non_abstain_recall, 4),
                "row_merge_normalization": row_merge_normalization,
                "baseline_scope_axis_violations": baseline_axis_violations,
                "candidate_scope_axis_violations": candidate_axis_violations,
            },
            "baseline_snapshot": baseline_snapshot,
            "candidate_snapshot": candidate_snapshot,
        }
        return (not reasons), diagnostics, candidate_scoped

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

        candidates: list[tuple[int, float, bool]] = []
        for idx, table in enumerate(tables):
            structure_conf = self._get_table_structure_confidence(table)
            
            # Check for column misalignment (empty header cells with non-empty data)
            has_column_misalignment = False
            header_cells: list[str] = []
            for row in (table.rows or []):
                if (row.metadata or {}).get("row_type") == "header":
                    header_cells = [str(cell.raw_text or "") for cell in (row.cells or [])]
                    break
            
            if header_cells:
                data_rows = [r for r in (table.rows or []) if (r.metadata or {}).get("row_type") != "header"][:5]
                for col_idx, header_val in enumerate(header_cells):
                    if not str(header_val or "").strip():  # Empty header
                        for row in data_rows:
                            for cell in (row.cells or []):
                                if cell.column_index == col_idx:
                                    cell_text = str(cell.raw_text or "").strip()
                                    if cell_text and len(cell_text) > 2:
                                        has_column_misalignment = True
                                        break
                            if has_column_misalignment:
                                break
                    if has_column_misalignment:
                        break
            
            # Trigger VLM repair if confidence is low OR column misalignment detected
            if isinstance(structure_conf, (int, float)) and structure_conf >= self.table_vlm_confidence_threshold:
                if not has_column_misalignment:
                    continue
                # Log that we're triggering repair due to column misalignment
                logger.info(
                    "table.vlm.triggered_by_misalignment table=%s conf=%s",
                    table.order_index,
                    structure_conf,
                )
            
            # Only require page_number - we'll fallback to full page if bbox is missing
            if not table.page_number:
                continue
            candidates.append(
                (
                    idx,
                    float(structure_conf) if isinstance(structure_conf, (int, float)) else 0.0,
                    has_column_misalignment,
                )
            )

        if not candidates:
            return tables, [], {
                "attempted": 0,
                "repaired": 0,
                "rejected": 0,
                "skipped": len(tables),
                "model": self.table_vlm_model,
                "guardrails_enabled": self.table_vlm_guardrails_enabled,
            }

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
        meta: dict[str, Any] = {
            "attempted": 0,
            "repaired": 0,
            "rejected": 0,
            "model": self.table_vlm_model,
            "guardrails_enabled": self.table_vlm_guardrails_enabled,
            "guardrail_version": "v2",
        }
        remaining_budget = max(0, self.table_vlm_max_repairs)

        def _repair_sort_key(item: tuple[int, float, bool]) -> tuple[int, float, int, int]:
            index, conf, misaligned = item
            table = tables[index]
            data_rows = len(
                [row for row in (table.rows or []) if (row.metadata or {}).get("row_type") != "header"]
            )
            columns = max(len(table.column_schema or []), max((len(r.cells or []) for r in (table.rows or [])), default=0))
            # Prioritize misaligned tables first (they cause wrong value attribution),
            # then prioritize by low confidence, then larger tables.
            return (0 if misaligned else 1, conf, -data_rows, -columns)

        def _table_hint(table: TablePayload) -> str:
            parts: list[str] = []
            if table.title:
                parts.append(f"Title: {table.title}")
            if table.section_heading:
                parts.append(f"Section: {table.section_heading}")
            # Use non-empty column headers as the primary anchor for full-page extraction.
            schema = [str(col or '').strip() for col in (table.column_schema or []) if str(col or '').strip()]
            if schema:
                parts.append("Columns: " + " | ".join(schema[:10]))
            # Add a few row-label anchors (first column of early rows).
            labels: list[str] = []
            for row in (table.rows or []):
                if (row.metadata or {}).get("row_type") == "header":
                    continue
                first_cell = None
                for cell in (row.cells or []):
                    if cell.column_index == 0:
                        first_cell = cell
                        break
                raw = str(getattr(first_cell, "raw_text", "") or "").strip() if first_cell else str(row.raw_text or "").strip()
                if raw:
                    labels.append(raw)
                if len(labels) >= 5:
                    break
            if labels:
                parts.append("Row labels (examples): " + " | ".join(labels))
            parts.append(f"Order index: {table.order_index} (page {table.page_number})")
            return "\n".join(parts).strip()

        for idx, conf, _misaligned in sorted(candidates, key=_repair_sort_key):
            if remaining_budget <= 0:
                break
            table = tables[idx]
            repair_reason = "misalignment" if _misaligned else "low_confidence"

            # Try to crop table region, fallback to full page if bbox is missing
            crop_bytes = self._render_table_crop(path, int(table.page_number), table.bbox)
            render_mode = "crop"
            if not crop_bytes:
                # Fallback: render full page when bbox is missing/invalid
                crop_bytes = self._render_full_page(path, int(table.page_number))
                render_mode = "full_page"
                if crop_bytes:
                    logger.info(
                        "table.vlm.fallback_full_page table=%s page=%s reason=bbox_missing",
                        table.order_index,
                        table.page_number,
                    )
            if not crop_bytes:
                continue

            meta["attempted"] += 1
            remaining_budget -= 1
            # Use a strong hint for full-page extraction (title + columns + row labels).
            table_hint = _table_hint(table)
            attempted_modes: list[str] = [render_mode]
            payload = self._run_vlm_table_repair(
                client, crop_bytes, render_mode=render_mode, table_hint=table_hint
            )
            if not payload and render_mode == "crop":
                retry_bytes = self._render_full_page(path, int(table.page_number))
                if retry_bytes:
                    attempted_modes.append("full_page")
                    logger.info(
                        "table.vlm.retry_full_page_after_crop_failure table=%s page=%s",
                        table.order_index,
                        table.page_number,
                    )
                    payload = self._run_vlm_table_repair(
                        client,
                        retry_bytes,
                        render_mode="full_page",
                        table_hint=table_hint,
                    )
                    if payload:
                        render_mode = "full_page_retry"
            if not payload:
                issues.append(
                    IssuePayload(
                        code="table_vlm_failed",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"VLM repair failed for table {table.order_index}.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={
                            "structure_confidence": conf,
                            "render_mode": render_mode,
                            "attempted_modes": attempted_modes,
                        },
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
                accepted, diagnostics, guarded_table = self._evaluate_vlm_guardrails(
                    baseline=table,
                    candidate=vlm_table,
                )
                diagnostics_record = {
                    "order_index": table.order_index,
                    "page_number": table.page_number,
                    "reason": repair_reason,
                    "render_mode": render_mode,
                    **diagnostics,
                }
                meta.setdefault("guardrail_diagnostics", []).append(diagnostics_record)

                # Crop extracts can be partial on dense PDFs. If the first candidate is rejected,
                # run a single full-page retry before final rejection.
                #
                # However, not every guardrail rejection is likely to be fixed by switching to
                # a full-page render. Only retry when the rejection indicates missing coverage
                # (rows/schema/values/scope), or when this repair was triggered by misalignment.
                if not accepted and render_mode == "crop":
                    retry_reasons = set(diagnostics.get("rejection_reasons") or [])
                    coverage_retry_reasons = {
                        "row_coverage_regression",
                        "schema_coverage_regression",
                        "value_coverage_regression",
                        "scope_row_regression",
                        "scope_quality_regression",
                    }
                    should_retry_full_page = bool(retry_reasons & coverage_retry_reasons) or (
                        repair_reason == "misalignment"
                    )
                    if not should_retry_full_page:
                        logger.info(
                            "table.vlm.skip_full_page_retry_after_guardrail_rejection table=%s page=%s reasons=%s",
                            table.order_index,
                            table.page_number,
                            ",".join(sorted(retry_reasons)),
                        )
                        # Fall through to rejection handling below.
                    else:
                        retry_bytes = self._render_full_page(path, int(table.page_number))
                        if retry_bytes:
                            attempted_modes.append("full_page")
                            logger.info(
                                "table.vlm.retry_full_page_after_guardrail_rejection table=%s page=%s reasons=%s",
                                table.order_index,
                                table.page_number,
                                ",".join(diagnostics.get("rejection_reasons") or []),
                            )
                            retry_payload = self._run_vlm_table_repair(
                                client,
                                retry_bytes,
                                render_mode="full_page",
                                table_hint=table_hint,
                            )
                            if retry_payload:
                                retry_table = self._table_payload_from_vlm(
                                    payload=retry_payload,
                                    order_index=table.order_index,
                                    page_number=int(table.page_number),
                                    bbox=table.bbox,
                                    title=table.title,
                                    section_heading=table.section_heading,
                                    source_metadata=table.metadata,
                                )
                                if retry_table:
                                    retry_accepted, retry_diagnostics, retry_guarded_table = (
                                        self._evaluate_vlm_guardrails(
                                            baseline=table,
                                            candidate=retry_table,
                                        )
                                    )
                                    retry_record = {
                                        "order_index": table.order_index,
                                        "page_number": table.page_number,
                                        "reason": repair_reason,
                                        "render_mode": "full_page_retry",
                                        **retry_diagnostics,
                                    }
                                    meta.setdefault("guardrail_diagnostics", []).append(retry_record)
                                    if retry_accepted:
                                        accepted = True
                                        diagnostics = retry_diagnostics
                                        guarded_table = retry_guarded_table
                                        render_mode = "full_page_retry"
                                    else:
                                        diagnostics = retry_diagnostics
                                        render_mode = "full_page_retry"

                if not accepted:
                    meta["rejected"] += 1
                    meta.setdefault("rejected_tables", []).append(
                        {
                            "order_index": table.order_index,
                            "page_number": table.page_number,
                            "reason": repair_reason,
                            "render_mode": render_mode,
                            "rejection_reasons": diagnostics.get("rejection_reasons") or [],
                        }
                    )
                    logger.info(
                        "table.vlm.rejected table=%s page=%s reasons=%s",
                        table.order_index,
                        table.page_number,
                        ",".join(diagnostics.get("rejection_reasons") or []),
                    )
                    issues.append(
                        IssuePayload(
                            code="table_vlm_rejected_regression",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description=f"VLM candidate rejected by guardrails for table {table.order_index}.",
                            page_number=table.page_number,
                            table_order_index=table.order_index,
                            details={
                                "structure_confidence": conf,
                                "repair_reason": repair_reason,
                                "render_mode": render_mode,
                                "attempted_modes": attempted_modes,
                                "rejection_reasons": diagnostics.get("rejection_reasons") or [],
                                "metrics": diagnostics.get("metrics") or {},
                            },
                        )
                    )
                    continue

                meta["repaired"] += 1
                meta.setdefault("repaired_tables", []).append(
                    {
                        "order_index": table.order_index,
                        "page_number": table.page_number,
                        "reason": repair_reason,
                        "render_mode": render_mode,
                    }
                )
                logger.info(
                    "table.vlm.repaired table=%s page=%s reason=%s render=%s conf=%s model=%s",
                    table.order_index,
                    table.page_number,
                    repair_reason,
                    render_mode,
                    conf,
                    self.table_vlm_model,
                )
                repaired[idx] = guarded_table

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

    @staticmethod
    def _render_full_page(path: Path, page_number: int) -> bytes | None:
        """Render entire PDF page as PNG for VLM repair when bbox is unavailable."""
        if fitz is None:
            return None
        doc = None
        try:
            doc = fitz.open(path)
            if page_number < 1 or page_number > len(doc):
                return None
            page = doc[page_number - 1]
            # Use lower DPI for full page to keep token cost reasonable
            pix = page.get_pixmap(dpi=150)
            return pix.tobytes("png")
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def _run_vlm_table_repair(
        self,
        client: Any,
        image_bytes: bytes,
        render_mode: str = "crop",
        table_hint: str | None = None,
    ) -> dict[str, Any] | None:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        
        _merged_cell_instruction = (
            "IMPORTANT: If a cell value is visually centered across multiple columns "
            "(i.e. it spans or merges several column positions), you MUST repeat that "
            "same value in EVERY column it applies to.  Do NOT leave the other columns "
            "empty — duplicate the value so each spanned column contains it."
        )

        if render_mode == "full_page" and table_hint:
            prompt = (
                "Extract the table from this page image.\n"
                "Select the table that best matches the hint below (it includes expected columns/row labels).\n"
                "HINT:\n"
                f"{table_hint}\n\n"
                "Return strict JSON with keys: columns (array of column header strings) and rows "
                "(array of arrays with cell values). Rows should contain only data rows (no header row). "
                "Make sure to capture ALL columns and ALL values correctly.\n\n"
                f"{_merged_cell_instruction}"
            )
            max_tokens = 4000  # Full page may have more data
        else:
            prompt = (
                "Extract the table from this image. "
                "Return strict JSON with keys: columns (array of strings) and rows "
                "(array of arrays). Rows should contain only data rows (no header row).\n\n"
                f"{_merged_cell_instruction}"
            )
            max_tokens = 1200
        
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
                max_tokens=max_tokens,
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
            # Detect runs of identical non-empty adjacent values — these
            # indicate the VLM correctly duplicated a spanning/merged value.
            # We tag each cell in such a run with the true column_span so
            # downstream applicability annotation can treat them as explicit
            # spans rather than independent values.
            str_values = [str(v) if v is not None else "" for v in row]
            span_for_col: dict[int, int] = {}
            col_cursor = 0
            while col_cursor < len(str_values):
                val = str_values[col_cursor].strip()
                if val:
                    run_end = col_cursor + 1
                    while run_end < len(str_values) and str_values[run_end].strip() == val:
                        run_end += 1
                    run_length = run_end - col_cursor
                    if run_length > 1:
                        for ci in range(col_cursor, run_end):
                            span_for_col[ci] = run_length
                    col_cursor = run_end
                else:
                    col_cursor += 1
            cells: list[TableCellPayload] = []
            for col_idx, value in enumerate(row):
                raw_text = str(value) if value is not None else ""
                column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                cell_meta: dict[str, Any] = {}
                if col_idx in span_for_col:
                    cell_meta["column_span"] = span_for_col[col_idx]
                cells.append(
                    TableCellPayload(
                        row_index=row_idx,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=raw_text,
                        normalized_value=TableDetector._normalize_cell_value(raw_text),
                        bbox=bbox,
                        confidence=None,
                        metadata=cell_meta,
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
        extraction = self._enrich_extraction_with_column_roles(extraction)
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
            lexicon_auto_learning = self._auto_learn_tenant_lexicon(
                upload=upload,
                extraction=extraction,
                structured_summary=structured_summary,
                ingestion_metadata=ingestion_metadata,
                entity_payloads=entity_payloads,
            )
            if lexicon_auto_learning:
                ingestion_metadata["lexicon_auto_learning"] = lexicon_auto_learning
            else:
                ingestion_metadata.pop("lexicon_auto_learning", None)
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

    def _auto_learn_tenant_lexicon(
        self,
        *,
        upload: KnowledgeUpload,
        extraction: ExtractionResult,
        structured_summary: Mapping[str, Any] | None,
        ingestion_metadata: Mapping[str, Any] | None,
        entity_payloads: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.tenant_lexicon_auto_learning_enabled:
            return {"enabled": False, "term_count": 0, "synonym_count": 0, "language_code": "und"}
        if self._tenant_lexicon_auto_learning_service is None:
            self._tenant_lexicon_auto_learning_service = TenantLexiconAutoLearningService()
        try:
            stats = self._tenant_lexicon_auto_learning_service.learn_from_ingestion(
                upload=upload,
                extraction=extraction,
                structured_summary=structured_summary,
                ingestion_metadata=ingestion_metadata,
                entity_payloads=entity_payloads,
            )
            logger.info(
                "lexicon.autolearn.summary upload=%s business=%s terms=%s synonyms=%s enabled=%s",
                upload.id,
                upload.business_profile_id,
                stats.get("term_count", 0),
                stats.get("synonym_count", 0),
                stats.get("enabled", True),
            )
            return stats
        except Exception as exc:  # pragma: no cover - ingestion must stay resilient
            logger.warning(
                "lexicon.autolearn.failed upload=%s business=%s error=%s",
                upload.id,
                upload.business_profile_id,
                exc,
            )
            return {
                "enabled": True,
                "term_count": 0,
                "synonym_count": 0,
                "error": str(exc)[:240],
            }

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

    def _pdf_table_readable_rows(self, table: TablePayload) -> list[TableRowPayload]:
        readable: list[TableRowPayload] = []
        for row in table.rows or []:
            meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            row_type = str(meta.get("row_type") or "").strip().lower()
            if row_type in {"header", "section_header"}:
                continue
            readable.append(row)
        return readable

    def _normalize_table_signature_text(self, text: str) -> str:
        cleaned = self._sanitize_text(text or "").strip().lower()
        if not cleaned:
            return ""
        cleaned = re.sub(r"[_\.\-]{2,}", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:240]

    def _table_row_signature(self, table: TablePayload, row_offset: int = 0) -> str:
        readable_rows = self._pdf_table_readable_rows(table)
        if row_offset < 0 or row_offset >= len(readable_rows):
            return ""
        row = readable_rows[row_offset]
        values: list[str] = []
        for cell in row.cells or []:
            raw = str(cell.raw_text or "").strip()
            if raw:
                values.append(raw)
            if len(values) >= 3:
                break
        return self._normalize_table_signature_text(" | ".join(values))

    def _table_row_prefix_signature(
        self,
        table: TablePayload,
        row_offset: int = 0,
        *,
        max_tokens: int = 8,
    ) -> str:
        readable_rows = self._pdf_table_readable_rows(table)
        if row_offset < 0 or row_offset >= len(readable_rows):
            return ""
        row = readable_rows[row_offset]
        values: list[str] = []
        for cell in row.cells or []:
            raw = str(cell.raw_text or "").strip()
            if raw:
                values.append(raw)
            if len(values) >= 2:
                break
        if not values:
            return ""
        text = self._normalize_table_signature_text(" ".join(values))
        if not text:
            return ""
        tokens = [token for token in text.split() if len(token) > 1 and token not in {"|"}]
        return " ".join(tokens[:max_tokens]).strip()

    def _table_effective_column_count(self, table: TablePayload) -> int:
        occupied: set[int] = set()
        for row in self._pdf_table_readable_rows(table):
            for cell in row.cells or []:
                if str(cell.raw_text or "").strip():
                    occupied.add(int(cell.column_index))
        if occupied:
            return len(occupied)
        return max(0, len(table.column_schema or []))

    def _build_pdf_table_recurrence_stats(self, tables: Sequence[TablePayload]) -> dict[str, Counter[str]]:
        first_rows = Counter()
        second_rows = Counter()
        first_row_prefixes = Counter()
        second_row_prefixes = Counter()
        for table in tables:
            first_sig = self._table_row_signature(table, 0)
            second_sig = self._table_row_signature(table, 1)
            first_prefix = self._table_row_prefix_signature(table, 0)
            second_prefix = self._table_row_prefix_signature(table, 1)
            if first_sig:
                first_rows[first_sig] += 1
            if second_sig:
                second_rows[second_sig] += 1
            if first_prefix:
                first_row_prefixes[first_prefix] += 1
            if second_prefix:
                second_row_prefixes[second_prefix] += 1
        return {
            "first_rows": first_rows,
            "second_rows": second_rows,
            "first_row_prefixes": first_row_prefixes,
            "second_row_prefixes": second_row_prefixes,
        }

    def _normalize_page_chrome_token(self, token: str) -> str:
        cleaned = self._sanitize_text(token or "").strip().lower()
        if not cleaned:
            return ""
        cleaned = re.sub(r"[_\.\-]{2,}", " ", cleaned)
        cleaned = re.sub(r"\b[a-z]*\d+[a-z0-9/\-]*\b", "#", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def _page_chrome_tokens(self, text: str) -> list[str]:
        raw_tokens = re.findall(r"\S+", self._sanitize_text(text or ""))
        normalized: list[str] = []
        for token in raw_tokens:
            cleaned = self._normalize_page_chrome_token(token)
            if cleaned:
                normalized.append(cleaned)
        return normalized

    def _page_chrome_position(
        self,
        bbox: Mapping[str, Any] | None,
        *,
        page_height: float | None,
        page_region: str | None = None,
    ) -> str | None:
        normalized_region = str(page_region or "").strip().lower()
        if normalized_region in {"header", "footer"}:
            return normalized_region
        if not bbox or not page_height:
            return None
        y0 = float(bbox.get("y0") or 0.0)
        y1 = float(bbox.get("y1") or 0.0)
        if y1 <= page_height * self.pdf_page_chrome_top_ratio:
            return "header"
        if y0 >= page_height * (1.0 - self.pdf_page_chrome_bottom_ratio):
            return "footer"
        return None

    def _build_pdf_page_chrome_stats(self, pages: Sequence[PageLayout]) -> dict[str, Counter[str]]:
        stats: dict[str, Counter[str]] = {"header_prefix": Counter(), "footer_suffix": Counter()}
        if not self.pdf_page_chrome_suppression_enabled:
            return stats

        header_prefix_pages: dict[str, set[int]] = {}
        footer_suffix_pages: dict[str, set[int]] = {}
        min_tokens = 2
        max_tokens = max(min_tokens, self.pdf_page_chrome_max_words)

        for page in pages:
            for block in page.blocks or []:
                if block.block_type in {
                    KnowledgeBlockType.TABLE,
                    KnowledgeBlockType.IMAGE,
                    KnowledgeBlockType.FIGURE,
                    KnowledgeBlockType.OTHER,
                }:
                    continue
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block_meta.get("is_decorative"):
                    continue
                position = self._page_chrome_position(
                    block.bbox,
                    page_height=(page.height or None),
                    page_region=block_meta.get("page_region"),
                )
                if not position:
                    continue
                tokens = self._page_chrome_tokens(block.text)
                if len(tokens) < min_tokens:
                    continue
                upper = min(len(tokens), max_tokens)
                if position == "header":
                    for size in range(min_tokens, upper + 1):
                        sig = " ".join(tokens[:size]).strip()
                        if sig:
                            header_prefix_pages.setdefault(sig, set()).add(int(page.page_number))
                elif position == "footer":
                    for size in range(min_tokens, upper + 1):
                        sig = " ".join(tokens[-size:]).strip()
                        if sig:
                            footer_suffix_pages.setdefault(sig, set()).add(int(page.page_number))

        stats["header_prefix"] = Counter(
            {sig: len(page_numbers) for sig, page_numbers in header_prefix_pages.items()}
        )
        stats["footer_suffix"] = Counter(
            {sig: len(page_numbers) for sig, page_numbers in footer_suffix_pages.items()}
        )
        return stats

    @staticmethod
    def _trim_leading_token_count(text: str, token_count: int) -> str:
        if token_count <= 0:
            return text
        matches = list(re.finditer(r"\S+", text or ""))
        if token_count >= len(matches):
            return ""
        start = matches[token_count].start()
        return (text or "")[start:].lstrip()

    @staticmethod
    def _trim_trailing_token_count(text: str, token_count: int) -> str:
        if token_count <= 0:
            return text
        matches = list(re.finditer(r"\S+", text or ""))
        if token_count >= len(matches):
            return ""
        end = matches[-token_count - 1].end()
        return (text or "")[:end].rstrip()

    def _suppress_pdf_page_chrome(
        self,
        text: str,
        *,
        bbox: Mapping[str, Any] | None,
        page_height: float | None,
        page_region: str | None,
        chrome_stats: Mapping[str, Counter[str]] | None,
    ) -> tuple[str, dict[str, Any]]:
        diagnostics: dict[str, Any] = {}
        if not self.pdf_page_chrome_suppression_enabled or not chrome_stats:
            return text, diagnostics
        position = self._page_chrome_position(bbox, page_height=page_height, page_region=page_region)
        if position not in {"header", "footer"}:
            return text, diagnostics
        tokens = self._page_chrome_tokens(text)
        min_tokens = 2
        if len(tokens) < min_tokens:
            return text, diagnostics

        max_tokens = min(len(tokens), max(min_tokens, self.pdf_page_chrome_max_words))
        trimmed = text

        if position == "header":
            counter = chrome_stats.get("header_prefix") or Counter()
            best_size = 0
            best_sig = ""
            for size in range(max_tokens, min_tokens - 1, -1):
                sig = " ".join(tokens[:size]).strip()
                if sig and int(counter.get(sig, 0)) >= self.pdf_page_chrome_min_repeats:
                    best_size = size
                    best_sig = sig
                    break
            if best_size:
                trimmed = self._trim_leading_token_count(trimmed, best_size)
                diagnostics = {
                    "position": "header",
                    "trimmed_tokens": best_size,
                    "signature": best_sig[:120],
                }
        elif position == "footer":
            counter = chrome_stats.get("footer_suffix") or Counter()
            best_size = 0
            best_sig = ""
            for size in range(max_tokens, min_tokens - 1, -1):
                sig = " ".join(tokens[-size:]).strip()
                if sig and int(counter.get(sig, 0)) >= self.pdf_page_chrome_min_repeats:
                    best_size = size
                    best_sig = sig
                    break
            if best_size:
                trimmed = self._trim_trailing_token_count(trimmed, best_size)
                diagnostics = {
                    "position": "footer",
                    "trimmed_tokens": best_size,
                    "signature": best_sig[:120],
                }

        return trimmed.strip(), diagnostics

    def _classify_pdf_table_candidate(
        self,
        table: TablePayload,
        assessment: Mapping[str, Any],
        recurrence_stats: Mapping[str, Counter[str]] | None = None,
    ) -> tuple[str, str, list[str]]:
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        if not isinstance(signals, Mapping):
            signals = {}
        quality = float(assessment.get("quality_score") or 0.0) if isinstance(assessment, Mapping) else 0.0
        effective_columns = int(signals.get("effective_column_count") or self._table_effective_column_count(table))
        reasons: list[str] = []
        first_sig = self._table_row_signature(table, 0)
        second_sig = self._table_row_signature(table, 1)
        first_prefix = self._table_row_prefix_signature(table, 0)
        second_prefix = self._table_row_prefix_signature(table, 1)
        recurring_first = 0
        recurring_second = 0
        recurring_first_prefix = 0
        recurring_second_prefix = 0
        if recurrence_stats:
            recurring_first = int((recurrence_stats.get("first_rows") or {}).get(first_sig, 0)) if first_sig else 0
            recurring_second = int((recurrence_stats.get("second_rows") or {}).get(second_sig, 0)) if second_sig else 0
            recurring_first_prefix = (
                int((recurrence_stats.get("first_row_prefixes") or {}).get(first_prefix, 0))
                if first_prefix
                else 0
            )
            recurring_second_prefix = (
                int((recurrence_stats.get("second_row_prefixes") or {}).get(second_prefix, 0))
                if second_prefix
                else 0
            )
        recurring_signal = max(
            recurring_first,
            recurring_second,
            recurring_first_prefix,
            recurring_second_prefix,
        )
        data_row_count = len(self._pdf_table_readable_rows(table))

        recurring_scaffold = (
            effective_columns <= 2
            and recurring_signal >= self.pdf_table_recurring_scaffold_min_repeats
            and (
                bool(signals.get("nonsense_columns"))
                or float(signals.get("header_confidence") or 0.0) == 0.0
                or quality < 0.9
                or data_row_count <= 12
            )
        )
        if recurring_scaffold:
            reasons.append("recurring_scaffold")
            return "recurring_scaffold", "suppress", reasons

        paragraph_like = bool(signals.get("paragraph_like_table"))
        fragment_like = bool(signals.get("fragmented_logical_rows"))
        micro_fragment = bool(signals.get("micro_fragment_table"))
        bridge_like = bool(signals.get("bridge_like_table"))
        low_structure = bool(signals.get("low_structure_table"))
        compact_banner = bool(signals.get("compact_banner_table"))
        header_paragraph_like = bool(signals.get("header_paragraph_like"))
        leading_blank_rows = int(signals.get("leading_blank_rows") or 0)
        scaffold_row_ratio = float(signals.get("scaffold_row_ratio") or 0.0)
        placeholder_cell_ratio = float(signals.get("placeholder_cell_ratio") or 0.0)
        multi_cell_row_ratio = float(signals.get("multi_cell_row_ratio") or 0.0)
        value_row_ratio = float(signals.get("value_row_ratio") or 0.0)
        header_confidence = float(signals.get("header_confidence") or 0.0)

        if micro_fragment:
            reasons.append("micro_fragment_table")
            return "layout_fragment", "suppress", reasons

        if data_row_count <= 0 or bool(signals.get("no_readable_rows")):
            reasons.append("no_readable_rows")
            return "layout_fragment", "suppress", reasons

        if header_paragraph_like and data_row_count <= 3:
            reasons.append("header_paragraph_like")
            return "layout_fragment", "suppress", reasons

        if paragraph_like and (quality <= 0.8 or bool(signals.get("nonsense_columns"))):
            reasons.append("paragraph_like_table")
            if fragment_like:
                reasons.append("fragmented_logical_rows")
            return "layout_fragment", "suppress", reasons

        if bridge_like:
            reasons.append("bridge_like_table")
            if fragment_like:
                reasons.append("fragmented_logical_rows")
            if bool(signals.get("nonsense_columns")):
                reasons.append("nonsense_columns")
            return "layout_fragment", "suppress", reasons

        if low_structure:
            reasons.append("low_structure_table")
            if paragraph_like:
                reasons.append("paragraph_like_table")
            return "layout_fragment", "suppress", reasons

        if compact_banner:
            reasons.append("compact_banner_table")
            if bool(signals.get("nonsense_columns")):
                reasons.append("nonsense_columns")
            return "layout_fragment", "suppress", reasons

        if (
            effective_columns >= 4
            and scaffold_row_ratio >= 0.85
            and placeholder_cell_ratio >= 0.45
        ):
            reasons.append("form_scaffold_table")
            return "layout_fragment", "suppress", reasons

        matrix_like_keep = (
            data_row_count >= 2
            and effective_columns >= 3
            and multi_cell_row_ratio >= 0.5
            and value_row_ratio >= 0.25
            and scaffold_row_ratio < 0.85
        )
        narrow_factual_keep = (
            data_row_count >= 3
            and effective_columns == 2
            and multi_cell_row_ratio >= 0.75
            and value_row_ratio >= 0.4
            and scaffold_row_ratio < 0.75
            and placeholder_cell_ratio < 0.4
            and not header_paragraph_like
        )
        sparse_grid_keep = (
            data_row_count >= 2
            and effective_columns >= 3
            and header_confidence >= 0.5
            and not header_paragraph_like
            and float(signals.get("structured_row_ratio") or 0.0) >= 0.5
            and scaffold_row_ratio <= 0.5
        )
        keep_evidence = matrix_like_keep or narrow_factual_keep or sparse_grid_keep

        if bool(signals.get("insufficient_rows")) and not keep_evidence:
            reasons.append("insufficient_rows")
            return "weak_table", "suppress", reasons

        single_row_bridge = (
            data_row_count <= 2
            and effective_columns >= 3
            and (
                float(signals.get("long_cell_ratio") or 0.0) >= 0.3
                or int(signals.get("max_cell_word_count") or 0) >= 18
            )
            and float(signals.get("short_cell_ratio") or 0.0) >= 0.2
            and quality < 0.85
        )
        if single_row_bridge:
            reasons.append("single_row_bridge")
            return "layout_fragment", "suppress", reasons

        if leading_blank_rows >= self.pdf_table_leading_blank_row_limit and bool(signals.get("column_misalignment")):
            reasons.extend(["leading_blank_rows", "column_misalignment"])
            return "layout_fragment", "suppress", reasons

        if quality < 0.6 and (fragment_like or bool(signals.get("nonsense_columns"))):
            if fragment_like:
                reasons.append("fragmented_logical_rows")
            if signals.get("nonsense_columns"):
                reasons.append("nonsense_columns")
            return "weak_table", "suppress", reasons

        if not keep_evidence:
            reasons.append("insufficient_keep_evidence")
            return "weak_table", "suppress", reasons

        if quality < 0.75:
            reasons.append("low_quality_table")
            return "weak_table", "suppress", reasons

        return "strong_table", "keep", reasons

    def _apply_pdf_table_promotion_gate(
        self,
        tables: Sequence[TablePayload],
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not tables:
            return [], {"enabled": bool(self.pdf_table_promotion_gate_enabled), "input_tables": 0}, []
        if not self.pdf_table_promotion_gate_enabled:
            return list(tables), {"enabled": False, "input_tables": len(tables), "kept_tables": len(tables)}, []

        recurrence_stats = self._build_pdf_table_recurrence_stats(tables)
        kept: list[TablePayload] = []
        issues: list[IssuePayload] = []
        class_counts: Counter[str] = Counter()
        suppressed_examples: list[dict[str, Any]] = []

        for table in tables:
            assessment = self._assess_table_quality(table)
            candidate_class, decision, reasons = self._classify_pdf_table_candidate(
                table,
                assessment,
                recurrence_stats=recurrence_stats,
            )
            class_counts[candidate_class] += 1
            metadata = dict(table.metadata or {})
            metadata["promotion_class"] = candidate_class
            metadata["promotion_decision"] = decision
            if reasons:
                metadata["promotion_reasons"] = reasons
            updated = TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=metadata,
                rows=table.rows,
            )
            if decision == "keep":
                kept.append(updated)
                continue

            if len(suppressed_examples) < 8:
                suppressed_examples.append(
                    {
                        "order_index": table.order_index,
                        "page_number": table.page_number,
                        "class": candidate_class,
                        "reasons": reasons,
                        "title": table.title,
                    }
                )
            issues.append(
                IssuePayload(
                    code="pdf_table_suppressed",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description=(
                        f"Suppressed low-value PDF table candidate ({candidate_class}) "
                        f"for page {table.page_number or '?'}."
                    ),
                    page_number=table.page_number,
                    table_order_index=table.order_index,
                    details={"class": candidate_class, "reasons": reasons},
                )
            )

        metadata = {
            "enabled": True,
            "input_tables": len(tables),
            "kept_tables": len(kept),
            "suppressed_tables": max(0, len(tables) - len(kept)),
            "class_counts": dict(class_counts),
        }
        if suppressed_examples:
            metadata["suppressed_examples"] = suppressed_examples
        return kept, metadata, issues

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
        from apps.knowledge.models import KnowledgeUploadTable

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
                        # DISABLED: Parent chunks create column-position ambiguity when LLM
                        # processes multiple tables with different column orders.
                        # Row chunks (key: value format) are semantically unambiguous.
                        # See: llm_confusion_diagnosis.md
                        # if parent_text:
                        #     parent_meta = dict(base_metadata)
                        #     parent_meta.update(
                        #         {
                        #             "content_source": "table_parent",
                        #             "table_chunk_role": "parent",
                        #             "is_table_preview": True,
                        #             "table_parent_truncated": truncated,
                        #         }
                        #     )
                        #     table_segment_payloads.append({"text": parent_text, "metadata": parent_meta})
                        row_payloads = self._table_row_chunk_payloads(
                            table=t,
                            column_map=column_map,
                            raw_schema=raw_schema,
                            privacy_rules=privacy_rules,
                            base_metadata=base_metadata,
                            max_rows=self.table_child_max_rows,
                        )
                        table_segment_payloads.extend(row_payloads)

                        # Two-tier table retrieval: emit a single summary chunk
                        # per row shard for primary search. Row chunks (above) are
                        # tagged search_tier="drill_down" and excluded from the
                        # primary search index, then pulled via expansion.
                        if self.table_summary_enabled and row_payloads:
                            row_label_entries: list[dict[str, Any]] = []
                            for payload in row_payloads:
                                payload_meta = payload.get("metadata")
                                if not isinstance(payload_meta, Mapping):
                                    continue
                                label = str(payload_meta.get("row_label") or "").strip()
                                if not label:
                                    continue
                                row_label_entries.append(
                                    {
                                        "label": label,
                                        "row_index": payload_meta.get("table_row_index"),
                                        "shard_index": payload_meta.get("table_row_shard_index"),
                                    }
                                )
                            summary_payloads = self._table_summary_chunk_payloads(
                                table=t,
                                column_map=column_map,
                                base_metadata=base_metadata,
                                total_data_rows=len(row_payloads),
                                row_label_entries=row_label_entries,
                            )
                            table_segment_payloads.extend(summary_payloads)
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
            if segment_payloads:
                segment_payloads, residual_reconciliation = self._reconcile_table_residual_segments(segment_payloads)
                if isinstance(ingestion_metadata, dict):
                    ingestion_metadata["table_residual_reconciliation"] = residual_reconciliation

        dataset_card = build_dataset_card_segment_payload(upload=upload, ingestion_metadata=ingestion_metadata)
        if dataset_card:
            segment_payloads.append(dataset_card)

        canonical_projection_stats: dict[str, Any] = {}
        if segment_payloads:
            segment_payloads, canonical_projection_stats = self._canonicalize_segment_payloads(
                upload=upload,
                segment_payloads=segment_payloads,
            )
            if isinstance(ingestion_metadata, dict):
                ingestion_metadata["canonical_chunk_projection"] = canonical_projection_stats

        if self.evidence_grouping_enabled and segment_payloads:
            self._assign_evidence_group_metadata(upload=upload, segment_payloads=segment_payloads)

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
                    if self._payload_is_table_residual(metadata) or self._payload_is_table_annotation(metadata):
                        filtered_payloads.append(payload)
                        continue
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

        # Explicitly set tenant context for RLS - ensures app.current_tenant is set
        # so PostgreSQL row-level security allows the inserts. Without this, RLS
        # silently discards the rows when bulk_create runs.
        business_id = upload.business_profile_id
        with tenant_context(business_id):
            KnowledgeUploadChunk.objects.bulk_create(chunk_objects, batch_size=100)
            # Verify chunks were actually persisted (RLS can silently discard)
            actual_count = KnowledgeUploadChunk.objects.filter(upload=upload).count()

        if actual_count != len(chunk_objects):
            logger.error(
                "chunks.persistence_mismatch upload=%s expected=%s actual=%s business=%s "
                "hint=RLS may have discarded inserts due to missing tenant context",
                upload.id,
                len(chunk_objects),
                actual_count,
                business_id,
            )
        logger.info(
            "chunks.persisted upload=%s count=%s actual=%s missing_embeddings=%s",
            upload.id,
            len(chunk_objects),
            actual_count,
            len(missing_chunk_ids),
        )
        # New emoji-enhanced logging
        log_success(
            logger,
            "CHUNKS COMMITTED",
            f"{actual_count} chunks persisted",
            {
                "upload_id": upload.id,
                "expected": len(chunk_objects),
                "missing_embeddings": len(missing_chunk_ids),
            },
            emoji=LogEmoji.SUCCESS,
        )
        if shadow_objects:
            shadow_missing = sum(1 for chunk in shadow_objects if chunk.embedding is None)
            with tenant_context(business_id):
                KnowledgeUploadShadowChunk.objects.bulk_create(shadow_objects, batch_size=100)
                shadow_actual = KnowledgeUploadShadowChunk.objects.filter(upload=upload).count()
            if shadow_actual != len(shadow_objects):
                logger.error(
                    "shadow.chunks.persistence_mismatch upload=%s expected=%s actual=%s business=%s",
                    upload.id,
                    len(shadow_objects),
                    shadow_actual,
                    business_id,
                )
            logger.info(
                "shadow.chunks.persisted upload=%s count=%s actual=%s missing_embeddings=%s",
                upload.id,
                len(shadow_objects),
                shadow_actual,
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

    @staticmethod
    def _payload_is_table_residual(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("table_residual"):
            return True
        return (
            metadata.get("content_source") == "table_residual"
            or metadata.get("region_role") == "table_residual"
        )

    @staticmethod
    def _payload_is_table_annotation(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("table_annotation"):
            return True
        return (
            metadata.get("content_source") == "table_annotation"
            or metadata.get("region_role") == "table_annotation"
        )

    @staticmethod
    def _payload_is_table_row(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if not metadata.get("is_table_chunk"):
            return False
        return metadata.get("content_source") == "table_row"

    @staticmethod
    def _canonical_anchor_token(value: Any, *, max_length: int = 120) -> str:
        token = re.sub(r"[^a-z0-9:_\-]+", "-", str(value or "").strip().lower())
        token = token.strip("-")
        if not token:
            return ""
        return token[:max_length]

    @staticmethod
    def _canonical_chunk_kind(metadata: Mapping[str, Any]) -> str:
        if not isinstance(metadata, Mapping):
            return "narrative_paragraph"
        if metadata.get("is_dataset_card"):
            return "dataset_card"
        content_source = str(metadata.get("content_source") or "").strip().lower()
        index_type = str(metadata.get("index_type") or "").strip().lower()
        if index_type == "entity":
            return "entity_record"
        if content_source == "table_row" or metadata.get("table_chunk_role") == "row":
            return "table_row"
        if content_source == "table_summary" or metadata.get("table_chunk_role") == "summary":
            return "table_summary"
        if content_source == "table_annotation" or metadata.get("table_annotation"):
            return "table_annotation"
        if metadata.get("is_table_chunk") or index_type == "table":
            return "table_chunk"
        return "narrative_paragraph"

    @staticmethod
    def _coverage_reason_for_chunk(metadata: Mapping[str, Any], *, kind: str) -> str:
        content_source = str(metadata.get("content_source") or "").strip().lower()
        if kind == "table_row":
            return "canonical_table_row"
        if kind == "table_summary":
            return "canonical_table_summary"
        if kind == "table_annotation":
            return "anchored_table_annotation"
        if kind == "entity_record":
            return "entity_record_projection"
        if kind == "dataset_card":
            return "dataset_card_summary"
        if content_source == "page_blocks":
            return "layout_paragraph"
        if content_source == "flat_text":
            return "flat_text_fallback"
        if content_source:
            return content_source
        return "narrative_paragraph"

    def _canonical_anchor_id_for_payload(
        self,
        *,
        upload_id: uuid.UUID,
        text: str,
        metadata: Mapping[str, Any],
        kind: str,
    ) -> str:
        fingerprint = self._chunk_fingerprint(text)[:16] or "empty"
        upload_token = self._canonical_anchor_token(upload_id) or "upload"
        table_token = self._canonical_anchor_token(metadata.get("table_id")) or "table"
        if kind == "table_row":
            try:
                row_index = int(metadata.get("table_row_index"))
            except (TypeError, ValueError):
                row_index = None
            row_token = str(row_index) if isinstance(row_index, int) and row_index >= 0 else fingerprint
            return f"table:{table_token}:row:{row_token}"
        if kind == "table_summary":
            return f"table:{table_token}:summary"
        if kind == "table_annotation":
            return f"table:{table_token}:annotation:{fingerprint}"
        if kind == "entity_record":
            entity_name = self._canonical_anchor_token(metadata.get("entity_name"), max_length=48) or "record"
            return f"upload:{upload_token}:entity:{entity_name}:{fingerprint}"
        if kind == "dataset_card":
            return f"upload:{upload_token}:dataset-card"
        page_number = self._segment_page_number(metadata)
        page_token = str(page_number) if isinstance(page_number, int) and page_number > 0 else "na"
        block_anchor = ""
        raw_anchors = metadata.get("block_anchors")
        if isinstance(raw_anchors, Sequence) and not isinstance(raw_anchors, (str, bytes)):
            for entry in raw_anchors:
                normalized = self._canonical_anchor_token(entry, max_length=48)
                if normalized:
                    block_anchor = normalized
                    break
        if block_anchor:
            return f"page:{page_token}:paragraph:{block_anchor}"
        return f"upload:{upload_token}:paragraph:{fingerprint}"

    def _apply_canonical_chunk_metadata(
        self,
        *,
        upload_id: uuid.UUID,
        text: str,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        canonical_meta = dict(metadata or {})
        kind = self._canonical_chunk_kind(canonical_meta)
        anchor_id = self._canonical_anchor_id_for_payload(
            upload_id=upload_id,
            text=text,
            metadata=canonical_meta,
            kind=kind,
        )
        canonical_meta["canonical_schema_version"] = self.canonical_chunk_schema_version
        canonical_meta["canonical_source_layer"] = "canonical"
        canonical_meta["canonical_chunk_kind"] = kind
        canonical_meta["canonical_anchor_id"] = anchor_id
        table_token = self._canonical_anchor_token(canonical_meta.get("table_id")) or ""
        if kind in {"table_row", "table_annotation"} and table_token:
            canonical_meta["canonical_parent_anchor_id"] = f"table:{table_token}:summary"
        elif kind == "table_summary":
            canonical_meta.pop("canonical_parent_anchor_id", None)
        canonical_meta["coverage_reason"] = self._coverage_reason_for_chunk(canonical_meta, kind=kind)
        return canonical_meta

    def _project_table_residual_annotations(
        self,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            metadata = payload.get("metadata")
            payloads.append(
                {
                    "text": text,
                    "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
                }
            )
        stats: dict[str, Any] = {
            "enabled": bool(self.table_annotation_enabled),
            "input_payloads": len(payloads),
            "input_residual_segments": 0,
            "residual_segments_projected": 0,
            "unanchored_residual_promoted": 0,
            "narrative_promoted_segments": 0,
            "table_annotation_chunks_created": 0,
            "tables_with_annotations": 0,
            "soft_limit_exceeded_tables": 0,
        }
        if not payloads or not self.table_annotation_enabled:
            stats["output_payloads"] = len(payloads)
            return payloads, stats

        table_context_by_id: dict[str, dict[str, Any]] = {}
        table_candidates_by_page: dict[int, list[tuple[int, int, str]]] = {}
        for payload in payloads:
            metadata = payload.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            if self._payload_is_table_residual(metadata):
                continue
            if self._payload_is_table_annotation(metadata):
                continue
            table_id = str(metadata.get("table_id") or "").strip()
            if not table_id:
                continue
            content_source = str(metadata.get("content_source") or "").strip().lower()
            table_context_by_id.setdefault(
                table_id,
                {
                    "table_id": table_id,
                    "table_title": str(metadata.get("table_title") or "").strip(),
                    "table_order_index": metadata.get("table_order_index"),
                    "table_page_number": self._segment_page_number(metadata),
                },
            )
            page_number = self._segment_page_number(metadata)
            if not page_number:
                continue
            try:
                order_index = int(metadata.get("table_order_index"))
            except (TypeError, ValueError):
                order_index = 10_000
            priority = 0 if content_source == "table_summary" else 1
            table_candidates_by_page.setdefault(page_number, []).append((priority, order_index, table_id))

        preferred_table_by_page: dict[int, str] = {}
        for page_number, candidates in table_candidates_by_page.items():
            if not candidates:
                continue
            best = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
            preferred_table_by_page[page_number] = best[2]

        passthrough: list[dict[str, Any]] = []
        residual_groups: dict[str, list[dict[str, Any]]] = {}
        for payload in payloads:
            metadata = payload.get("metadata")
            if not isinstance(metadata, Mapping) or not self._payload_is_table_residual(metadata):
                passthrough.append(payload)
                continue
            stats["input_residual_segments"] = int(stats["input_residual_segments"]) + 1
            table_id = str(metadata.get("table_id") or "").strip()
            if not table_id:
                page_number = self._segment_page_number(metadata)
                if page_number:
                    table_id = preferred_table_by_page.get(page_number, "")
            if not table_id:
                promoted_meta = dict(metadata)
                promoted_meta.pop("table_residual", None)
                promoted_meta.pop("table_residual_candidate", None)
                promoted_meta["content_source"] = "page_blocks"
                promoted_meta["region_role"] = "text"
                promoted_meta.pop("search_tier", None)
                promoted_meta["table_annotation_unanchored"] = True
                passthrough.append({"text": payload["text"], "metadata": promoted_meta})
                stats["unanchored_residual_promoted"] = int(stats["unanchored_residual_promoted"]) + 1
                continue
            projection_target = self._residual_projection_target(text=payload["text"], metadata=metadata)
            if projection_target == "narrative":
                promoted_meta = dict(metadata)
                promoted_meta.pop("table_residual", None)
                promoted_meta.pop("table_residual_candidate", None)
                promoted_meta["content_source"] = "page_blocks"
                promoted_meta["region_role"] = "text"
                promoted_meta.pop("search_tier", None)
                promoted_meta["table_residual_projected"] = "narrative"
                promoted_meta["table_reference_id"] = table_id
                passthrough.append({"text": payload["text"], "metadata": promoted_meta})
                stats["narrative_promoted_segments"] = int(stats["narrative_promoted_segments"]) + 1
                continue
            residual_groups.setdefault(table_id, []).append(payload)
            stats["residual_segments_projected"] = int(stats["residual_segments_projected"]) + 1

        annotation_payloads: list[dict[str, Any]] = []
        for table_id, grouped in residual_groups.items():
            if not grouped:
                continue
            table_context = table_context_by_id.get(table_id, {"table_id": table_id})

            def _rank(payload: Mapping[str, Any]) -> tuple[float, int, int]:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
                overlap = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
                try:
                    overlap_score = float(overlap)
                except (TypeError, ValueError):
                    overlap_score = 0.0
                text = str(payload.get("text") or "")
                token_score = len(self._token_signature(text))
                return overlap_score, token_score, len(text)

            ranked = sorted(grouped, key=_rank, reverse=True)
            seen_fingerprints: set[str] = set()
            selected_texts: list[str] = []
            region_keys: list[str] = []
            reasons: list[str] = []
            source_anchors: list[str] = []
            page_numbers: list[int] = []
            for candidate in ranked:
                candidate_text = str(candidate.get("text") or "").strip()
                if not candidate_text:
                    continue
                fingerprint = self._chunk_fingerprint(candidate_text)
                if fingerprint and fingerprint in seen_fingerprints:
                    continue
                if fingerprint:
                    seen_fingerprints.add(fingerprint)
                selected_texts.append(candidate_text)
                metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
                region_key = str(metadata.get("table_residual_region_key") or "").strip()
                if region_key and region_key not in region_keys:
                    region_keys.append(region_key)
                reason = str(metadata.get("table_residual_reason") or "").strip()
                if reason and reason not in reasons:
                    reasons.append(reason)
                anchors = metadata.get("block_anchors")
                if isinstance(anchors, Sequence) and not isinstance(anchors, (str, bytes)):
                    for raw_anchor in anchors:
                        anchor = str(raw_anchor or "").strip()
                        if anchor and anchor not in source_anchors:
                            source_anchors.append(anchor)
                page_number = self._segment_page_number(metadata)
                if page_number and page_number not in page_numbers:
                    page_numbers.append(page_number)
            if not selected_texts:
                continue
            lines: list[str] = []
            table_title = str(table_context.get("table_title") or "").strip()
            if table_title:
                lines.append(f"[Table] {table_title}")
            lines.append("[Notes]")
            lines.extend(selected_texts)
            annotation_text = "\n".join(lines).strip()
            split_annotations = self._chunk_text(
                annotation_text,
                chunk_chars=self.table_annotation_max_chars,
                overlap=0,
            )
            if not split_annotations:
                split_annotations = [annotation_text]
            soft_limit = self.table_annotation_max_per_table
            soft_limit_exceeded = bool(soft_limit and len(split_annotations) > soft_limit)
            if soft_limit_exceeded:
                stats["soft_limit_exceeded_tables"] = int(stats["soft_limit_exceeded_tables"]) + 1
            for idx, rendered in enumerate(split_annotations, start=1):
                annotation_meta: dict[str, Any] = {
                    "strategy": "table_residual_projection",
                    "index_type": "text",
                    "content_source": "table_annotation",
                    "region_role": "table_annotation",
                    "table_annotation": True,
                    "table_annotation_source": "table_residual",
                    "table_annotation_rank": idx,
                    "table_annotation_total": len(split_annotations),
                    "table_annotation_fragment_count": len(selected_texts),
                    "table_annotation_max_chars": self.table_annotation_max_chars,
                    "table_annotation_soft_limit": soft_limit,
                    "table_annotation_soft_limit_exceeded": soft_limit_exceeded,
                    "table_id": table_id,
                    "search_tier": "supporting",
                }
                table_order_index = table_context.get("table_order_index")
                if table_order_index is not None:
                    annotation_meta["table_order_index"] = table_order_index
                if table_title:
                    annotation_meta["table_title"] = table_title
                table_page_number = table_context.get("table_page_number")
                if isinstance(table_page_number, int) and table_page_number > 0:
                    annotation_meta["table_page_number"] = table_page_number
                elif page_numbers:
                    annotation_meta["table_page_number"] = page_numbers[0]
                if page_numbers:
                    annotation_meta["page_numbers"] = page_numbers[:4]
                if region_keys:
                    annotation_meta["table_annotation_region_keys"] = region_keys[:8]
                if reasons:
                    annotation_meta["table_annotation_reasons"] = reasons[:6]
                if source_anchors:
                    annotation_meta["block_anchors"] = source_anchors[:12]
                annotation_payloads.append({"text": rendered, "metadata": annotation_meta})

        if residual_groups:
            stats["tables_with_annotations"] = len(residual_groups)
        stats["table_annotation_chunks_created"] = len(annotation_payloads)
        output_payloads = passthrough + annotation_payloads
        stats["output_payloads"] = len(output_payloads)
        return output_payloads, stats

    def _canonicalize_segment_payloads(
        self,
        *,
        upload: KnowledgeUpload,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        projected_payloads, projection_stats = self._project_table_residual_annotations(segment_payloads)
        canonicalized: list[dict[str, Any]] = []
        kind_counts: dict[str, int] = {}
        for payload in projected_payloads:
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
            canonical_meta = self._apply_canonical_chunk_metadata(
                upload_id=upload.id,
                text=text,
                metadata=metadata,
            )
            kind = str(canonical_meta.get("canonical_chunk_kind") or "unknown")
            kind_counts[kind] = int(kind_counts.get(kind, 0)) + 1
            canonicalized.append({"text": text, "metadata": canonical_meta})
        stats: dict[str, Any] = {
            "schema_version": self.canonical_chunk_schema_version,
            "input_payloads": len(segment_payloads),
            "output_payloads": len(canonicalized),
            "kind_counts": kind_counts,
            "table_residual_projection": projection_stats,
        }
        return canonicalized, stats

    @staticmethod
    def _token_signature(text: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        if not tokens:
            return set()
        stop = {
            "section",
            "table",
            "row",
            "rows",
            "columns",
            "column",
            "labels",
            "label",
            "identifiers",
            "identifier",
        }
        signature: set[str] = set()
        for token in tokens:
            if len(token) < 3:
                continue
            if token in stop:
                continue
            signature.add(token)
        return signature

    @staticmethod
    def _segment_page_number(metadata: Mapping[str, Any]) -> int | None:
        if not isinstance(metadata, Mapping):
            return None
        raw_page_numbers = metadata.get("page_numbers")
        if isinstance(raw_page_numbers, Sequence) and not isinstance(raw_page_numbers, (str, bytes)):
            for raw_value in raw_page_numbers:
                try:
                    page_number = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if page_number > 0:
                    return page_number
        raw_page = metadata.get("table_page_number")
        try:
            page_number = int(raw_page)
        except (TypeError, ValueError):
            return None
        return page_number if page_number > 0 else None

    def _residual_region_key(self, metadata: Mapping[str, Any]) -> str:
        if not isinstance(metadata, Mapping):
            return "residual:unscoped"
        region_key = str(metadata.get("table_residual_region_key") or "").strip()
        if region_key:
            return region_key
        region_keys = metadata.get("table_residual_region_keys")
        if isinstance(region_keys, Sequence) and not isinstance(region_keys, (str, bytes)):
            for value in region_keys:
                normalized = str(value or "").strip()
                if normalized:
                    return normalized
        page_number = self._segment_page_number(metadata)
        if page_number:
            return f"p{page_number}-residual"
        page_anchor = str(metadata.get("page_anchor") or "").strip()
        if page_anchor:
            return f"{page_anchor}-residual"
        return "residual:unscoped"

    def _segment_semantically_equivalent_to_table_row(
        self,
        *,
        residual_text: str,
        row_signatures_for_page: Sequence[set[str]],
        all_row_signatures: Sequence[set[str]],
        min_residual_coverage: float | None = None,
        min_row_coverage: float | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        residual_tokens = self._token_signature(residual_text)
        if not residual_tokens:
            return False, None
        candidates = list(row_signatures_for_page) if row_signatures_for_page else list(all_row_signatures)
        if not candidates:
            return False, None
        if min_residual_coverage is None:
            min_residual_coverage = self.table_residual_equivalence_min_overlap
        if min_row_coverage is None:
            min_row_coverage = self.table_residual_equivalence_min_overlap
        min_residual = max(0.0, min(1.0, float(min_residual_coverage)))
        min_row = max(0.0, min(1.0, float(min_row_coverage)))
        best: dict[str, Any] | None = None
        for row_tokens in candidates:
            if not row_tokens:
                continue
            shared = residual_tokens & row_tokens
            if len(shared) < self.table_residual_equivalence_min_shared_tokens:
                continue
            residual_coverage = len(shared) / max(1, len(residual_tokens))
            row_coverage = len(shared) / max(1, len(row_tokens))
            union_count = max(1, len(residual_tokens | row_tokens))
            jaccard = len(shared) / union_count
            candidate = {
                "shared_tokens": int(len(shared)),
                "residual_tokens": int(len(residual_tokens)),
                "row_tokens": int(len(row_tokens)),
                "residual_coverage": round(float(residual_coverage), 4),
                "row_coverage": round(float(row_coverage), 4),
                "jaccard": round(float(jaccard), 4),
            }
            if best is None:
                best = candidate
            else:
                best_key = (
                    min(float(best["residual_coverage"]), float(best["row_coverage"])),
                    float(best["jaccard"]),
                    int(best["shared_tokens"]),
                )
                candidate_key = (
                    min(float(candidate["residual_coverage"]), float(candidate["row_coverage"])),
                    float(candidate["jaccard"]),
                    int(candidate["shared_tokens"]),
                )
                if candidate_key > best_key:
                    best = candidate
            if residual_coverage >= min_residual and row_coverage >= min_row:
                candidate["matched"] = True
                return True, candidate
        if best is not None:
            best["matched"] = False
        return False, best

    def _classify_table_residual_segment_kind(self, text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return "unknown"
        base_text = re.sub(r"\n?\s*Identifiers:\s.*$", "", normalized, flags=re.IGNORECASE).strip()
        if not base_text:
            base_text = normalized
        tokens = self._token_signature(base_text)
        token_count = len(tokens)
        line_count = len([line for line in base_text.splitlines() if line.strip()])
        key_value_pairs = len(re.findall(r"\b[^:\n]{1,40}:\s+\S+", base_text))
        has_row_marker = "[Row]" in base_text or "\t" in base_text
        has_sentence_punctuation = bool(re.search(r"[.!?;:]", base_text))
        numeric_signal = self._has_numeric_table_signal(base_text)
        if token_count <= 4 and line_count <= 2:
            return "cell_like"
        if has_row_marker or key_value_pairs >= 2:
            return "row_like"
        if token_count <= 12 and not has_sentence_punctuation:
            if numeric_signal:
                return "row_like"
            return "heading_like"
        if numeric_signal and token_count <= 24 and line_count <= 4 and not has_sentence_punctuation:
            return "row_like"
        return "note_like"

    def _compact_residual_text(self, text: str) -> tuple[str, bool]:
        normalized = str(text or "").strip()
        if not normalized:
            return "", False
        if len(normalized) <= self.table_residual_compact_max_chars:
            return normalized, False
        compact_segments = self._chunk_text(
            normalized,
            chunk_chars=self.table_residual_compact_max_chars,
            overlap=0,
        )
        if compact_segments:
            return compact_segments[0], True
        return normalized[: self.table_residual_compact_max_chars], True

    def _residual_projection_target(
        self,
        *,
        text: str,
        metadata: Mapping[str, Any],
    ) -> str:
        segment_kind = str(
            metadata.get("table_residual_segment_kind")
            or self._classify_table_residual_segment_kind(text)
            or "unknown"
        ).strip()
        base_text = re.sub(r"\n?\s*Identifiers:\s.*$", "", str(text or ""), flags=re.IGNORECASE).strip()
        if not base_text:
            base_text = str(text or "")
        numeric_signal = self._has_numeric_table_signal(base_text)
        starts_with_note_marker = base_text.lstrip().startswith("*")
        token_count = len(self._token_signature(base_text))
        has_sentence_punctuation = bool(re.search(r"[.!?;:]", base_text))
        overlap_raw = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
        try:
            overlap_ratio = float(overlap_raw)
        except (TypeError, ValueError):
            overlap_ratio = 0.0

        # Headline-like table-adjacent text should remain narrative, not anchored notes.
        if segment_kind == "heading_like" and not numeric_signal and not starts_with_note_marker:
            return "narrative"

        # Short, non-numeric near-table snippets are usually surrounding prose.
        if (
            segment_kind == "note_like"
            and not numeric_signal
            and not starts_with_note_marker
            and token_count <= 18
            and not has_sentence_punctuation
        ):
            return "narrative"

        # Weak-overlap text that survived residual reconciliation should stay narrative.
        if (
            segment_kind == "note_like"
            and not numeric_signal
            and not starts_with_note_marker
            and overlap_ratio < self.pdf_table_residual_overlap_min_ratio
        ):
            return "narrative"

        return "annotation"

    def _reconcile_table_residual_segments(
        self,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            payloads.append({"text": text, "metadata": dict(metadata)})
        if not payloads:
            return [], {
                "input_residual_segments": 0,
                "kept_residual_segments": 0,
                "dropped_equivalent_segments": 0,
                "dropped_equivalent_cell_like_segments": 0,
                "dropped_equivalent_heading_like_segments": 0,
                "dropped_equivalent_row_like_segments": 0,
                "dropped_equivalent_note_like_segments": 0,
                "dropped_cap_segments": 0,
                "regions_with_residuals": 0,
                "equivalence_uncertain_kept_segments": 0,
            }

        row_signatures_all: list[set[str]] = []
        row_signatures_by_page: dict[int, list[set[str]]] = {}
        row_fingerprints_by_page: dict[int, set[str]] = {}
        row_fingerprints_all: set[str] = set()
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_row(metadata):
                continue
            text = payload["text"]
            page_number = self._segment_page_number(metadata)
            row_signature = self._token_signature(text)
            if row_signature:
                row_signatures_all.append(row_signature)
                if page_number:
                    row_signatures_by_page.setdefault(page_number, []).append(row_signature)
            fingerprint = self._chunk_fingerprint(text)
            if not fingerprint:
                continue
            row_fingerprints_all.add(fingerprint)
            if page_number:
                row_fingerprints_by_page.setdefault(page_number, set()).add(fingerprint)

        residual_count = 0
        dropped_equivalent = 0
        dropped_equivalent_by_kind: dict[str, int] = {
            "cell_like": 0,
            "heading_like": 0,
            "row_like": 0,
            "note_like": 0,
            "unknown": 0,
        }
        equivalence_uncertain_kept = 0
        dropped_equivalence_audit: list[dict[str, Any]] = []

        def _record_equivalence_drop(
            *,
            reason: str,
            segment_kind: str,
            metadata: Mapping[str, Any],
            page_number: int | None,
            region_key: str,
            fingerprint: str,
            match: Mapping[str, Any] | None = None,
        ) -> None:
            dropped_equivalent_by_kind.setdefault(segment_kind, 0)
            dropped_equivalent_by_kind[segment_kind] += 1
            if len(dropped_equivalence_audit) >= 16:
                return
            anchor = ""
            anchors = metadata.get("block_anchors")
            if isinstance(anchors, Sequence) and not isinstance(anchors, (str, bytes)):
                for value in anchors:
                    normalized = str(value or "").strip()
                    if normalized:
                        anchor = normalized
                        break
            event: dict[str, Any] = {
                "reason": reason,
                "segment_kind": segment_kind,
                "page_number": page_number,
                "region_key": region_key,
                "anchor": anchor or None,
                "fingerprint": (fingerprint[:16] if fingerprint else None),
            }
            if isinstance(match, Mapping):
                event["shared_tokens"] = int(match.get("shared_tokens") or 0)
                event["residual_coverage"] = float(match.get("residual_coverage") or 0.0)
                event["row_coverage"] = float(match.get("row_coverage") or 0.0)
                event["jaccard"] = float(match.get("jaccard") or 0.0)
            dropped_equivalence_audit.append(event)

        residual_groups: dict[str, list[dict[str, Any]]] = {}
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_residual(metadata):
                continue
            residual_count += 1
            page_number = self._segment_page_number(metadata)
            residual_text = payload["text"]
            segment_kind = self._classify_table_residual_segment_kind(residual_text)
            metadata["table_residual_segment_kind"] = segment_kind
            region_key = self._residual_region_key(metadata)
            if segment_kind == "cell_like":
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="low_information_cell",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=self._chunk_fingerprint(residual_text),
                )
                continue
            residual_fingerprint = self._chunk_fingerprint(residual_text)
            if residual_fingerprint:
                page_fingerprints = row_fingerprints_by_page.get(page_number or -1, set())
                if residual_fingerprint in page_fingerprints or (
                    not page_fingerprints and residual_fingerprint in row_fingerprints_all
                ):
                    dropped_equivalent += 1
                    _record_equivalence_drop(
                        reason="fingerprint_match",
                        segment_kind=segment_kind,
                        metadata=metadata,
                        page_number=page_number,
                        region_key=region_key,
                        fingerprint=residual_fingerprint,
                    )
                    continue
            page_signatures = row_signatures_by_page.get(page_number or -1, [])
            semantic_min_residual: float | None = None
            semantic_min_row: float | None = None
            if segment_kind in {"row_like", "heading_like"}:
                semantic_min_residual = self.table_residual_equivalence_min_overlap
                semantic_min_row = min(0.4, self.table_residual_equivalence_min_overlap)
            elif segment_kind == "note_like":
                residual_token_count = len(self._token_signature(residual_text))
                if residual_token_count <= 16:
                    semantic_min_residual = self.table_residual_equivalence_min_overlap
                    semantic_min_row = self.table_residual_equivalence_min_overlap
                else:
                    strict = min(0.98, max(0.9, self.table_residual_equivalence_min_overlap + 0.25))
                    semantic_min_residual = strict
                    semantic_min_row = strict
            equivalent, match = self._segment_semantically_equivalent_to_table_row(
                residual_text=residual_text,
                row_signatures_for_page=page_signatures,
                all_row_signatures=row_signatures_all,
                min_residual_coverage=semantic_min_residual,
                min_row_coverage=semantic_min_row,
            )
            metadata["table_residual_equivalence_checked"] = True
            if isinstance(match, Mapping):
                metadata["table_residual_equivalence_best"] = dict(match)
            one_sided_row_equivalent = False
            if not equivalent and isinstance(match, Mapping) and segment_kind in {"row_like", "heading_like"}:
                residual_cov = float(match.get("residual_coverage") or 0.0)
                shared_tokens = int(match.get("shared_tokens") or 0)
                if (
                    residual_cov >= self.table_residual_equivalence_min_overlap
                    and shared_tokens >= self.table_residual_equivalence_min_shared_tokens
                ):
                    one_sided_row_equivalent = True
            if equivalent:
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="semantic_equivalent",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=residual_fingerprint,
                    match=match,
                )
                continue
            if one_sided_row_equivalent:
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="semantic_row_subset",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=residual_fingerprint,
                    match=match,
                )
                continue
            if isinstance(match, Mapping):
                residual_cov = float(match.get("residual_coverage") or 0.0)
                row_cov = float(match.get("row_coverage") or 0.0)
                if (
                    residual_cov >= self.table_residual_equivalence_min_overlap
                    or row_cov >= self.table_residual_equivalence_min_overlap
                ):
                    metadata["table_residual_equivalence_uncertain"] = True
                    equivalence_uncertain_kept += 1

            compact_text, compacted = self._compact_residual_text(residual_text)
            payload["text"] = compact_text
            if compacted:
                metadata["table_residual_compacted"] = True
                metadata["table_residual_compact_max_chars"] = self.table_residual_compact_max_chars

            metadata["table_residual_region_key"] = region_key
            residual_groups.setdefault(region_key, []).append(payload)

        kept_residual: list[dict[str, Any]] = []
        dropped_cap = 0
        for region_key, region_payloads in residual_groups.items():
            def _rank(payload: Mapping[str, Any]) -> tuple[float, int, int]:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
                overlap = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
                try:
                    overlap_score = float(overlap)
                except (TypeError, ValueError):
                    overlap_score = 0.0
                text = str(payload.get("text") or "")
                token_score = len(self._token_signature(text))
                length_score = len(text)
                return overlap_score, token_score, length_score

            ranked = sorted(region_payloads, key=_rank, reverse=True)
            # Coverage-first policy: once a residual segment is proven non-equivalent
            # to indexed table rows, keep it. Overlap is a dedupe hint, not a hard
            # suppression decision.
            kept_residual.extend(ranked)

        kept_residual_ids = {id(payload) for payload in kept_residual}
        reconciled: list[dict[str, Any]] = []
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_residual(metadata):
                reconciled.append(payload)
                continue
            if id(payload) in kept_residual_ids:
                reconciled.append(payload)
        stats = {
            "input_residual_segments": residual_count,
            "kept_residual_segments": len(kept_residual),
            "dropped_equivalent_segments": dropped_equivalent,
            "dropped_equivalent_cell_like_segments": int(dropped_equivalent_by_kind.get("cell_like", 0)),
            "dropped_equivalent_heading_like_segments": int(dropped_equivalent_by_kind.get("heading_like", 0)),
            "dropped_equivalent_row_like_segments": int(dropped_equivalent_by_kind.get("row_like", 0)),
            "dropped_equivalent_note_like_segments": int(dropped_equivalent_by_kind.get("note_like", 0)),
            "dropped_cap_segments": dropped_cap,
            "regions_with_residuals": len(residual_groups),
            "max_per_region": self.table_residual_max_per_region,
            "equiv_min_overlap": round(self.table_residual_equivalence_min_overlap, 4),
            "equiv_min_shared_tokens": self.table_residual_equivalence_min_shared_tokens,
            "equivalence_uncertain_kept_segments": equivalence_uncertain_kept,
        }
        if dropped_equivalence_audit:
            stats["dropped_equivalence_audit_sample"] = dropped_equivalence_audit
        return reconciled, stats

    @staticmethod
    def _representation_from_metadata(metadata: Mapping[str, Any]) -> str:
        if not metadata:
            return "text"
        if metadata.get("is_table_chunk"):
            return "table"
        index_type = str(metadata.get("index_type") or "").strip().lower()
        if index_type == "entity":
            return "json"
        if index_type == "table":
            return "table"
        return "text"

    @staticmethod
    def _normalize_evidence_phrase(value: str, *, max_tokens: int = 24) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        normalized = normalized.lower()
        normalized = re.sub(r"[_/\-]+", " ", normalized)
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return ""
        tokens = normalized.split(" ")
        if len(tokens) > max_tokens:
            tokens = tokens[:max_tokens]
        return " ".join(tokens)

    @staticmethod
    def _evidence_group_id_for_key(upload_id: uuid.UUID, key: str) -> str:
        namespace = uuid.UUID(str(upload_id))
        stable_key = key or "empty"
        return str(uuid.uuid5(namespace, stable_key))

    def _payload_primary_evidence_key(
        self,
        *,
        payload_text: str,
        metadata: Mapping[str, Any],
    ) -> str:
        representation = self._representation_from_metadata(metadata)
        table_id = str(metadata.get("table_id") or "").strip()
        table_role = str(metadata.get("table_chunk_role") or "").strip().lower()

        if representation == "table":
            row_label = self._normalize_evidence_phrase(str(metadata.get("row_label") or ""))
            if row_label:
                return f"table_row_label:{row_label}"
            row_index = metadata.get("table_row_index")
            if table_id and row_index is not None:
                return f"table_row:{table_id}:{row_index}"
            if table_id and table_role:
                return f"table:{table_id}:{table_role}"
            if table_id:
                return f"table:{table_id}"

        if representation == "json":
            entity_name = self._normalize_evidence_phrase(str(metadata.get("entity_name") or ""))
            if entity_name:
                return f"entity:{entity_name}"
            entity_index = metadata.get("entity_index")
            if entity_index is not None:
                return f"entity_index:{entity_index}"

        page_anchor = str(metadata.get("page_anchor") or "").strip()
        anchors = metadata.get("block_anchors")
        if isinstance(anchors, list) and len(anchors) == 1 and anchors[0]:
            anchor_value = str(anchors[0]).strip()
            if anchor_value:
                return f"text_anchor:{anchor_value}"
        if page_anchor:
            normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=40)
            if normalized_text:
                return f"text_page:{page_anchor}:{normalized_text[:180]}"
            return f"text_page:{page_anchor}"

        normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=48)
        if normalized_text:
            return f"text:{normalized_text[:220]}"
        return "text:empty"

    def _assign_evidence_group_metadata(
        self,
        *,
        upload: KnowledgeUpload,
        segment_payloads: Sequence[dict[str, Any]],
    ) -> None:
        if not segment_payloads:
            return

        table_label_groups: dict[str, str] = {}
        prepared: list[tuple[dict[str, Any], dict[str, Any], str, str, str]] = []

        for payload in segment_payloads:
            if not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                payload["metadata"] = metadata
            payload_text = str(payload.get("text") or "")
            representation = self._representation_from_metadata(metadata)
            evidence_type = str(
                metadata.get("content_source") or metadata.get("index_type") or representation
            ).strip().lower() or representation
            metadata["representation"] = representation
            metadata["evidence_type"] = evidence_type
            evidence_key = self._payload_primary_evidence_key(payload_text=payload_text, metadata=metadata)
            prepared.append((payload, metadata, representation, evidence_key, payload_text))

            if representation != "table":
                continue
            evidence_group_id = self._evidence_group_id_for_key(upload.id, evidence_key)
            metadata["evidence_group_id"] = evidence_group_id
            metadata["evidence_key"] = evidence_key
            row_label = self._normalize_evidence_phrase(str(metadata.get("row_label") or ""))
            if row_label:
                table_label_groups.setdefault(row_label, evidence_group_id)

        if not prepared:
            return

        sorted_table_labels = sorted(table_label_groups.keys(), key=len, reverse=True)

        for _, metadata, representation, evidence_key, payload_text in prepared:
            if representation == "table":
                continue

            linked_group_id = ""
            if representation == "text":
                line_count = len([line for line in payload_text.splitlines() if line.strip()])
                if not line_count:
                    line_count = 1 if payload_text.strip() else 0
                if (
                    payload_text
                    and len(payload_text) <= self.evidence_text_link_max_chars
                    and line_count <= self.evidence_text_link_max_lines
                    and sorted_table_labels
                ):
                    normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=120)
                    if normalized_text:
                        haystack = f" {normalized_text} "
                        for label in sorted_table_labels:
                            if len(label) < 6:
                                continue
                            if f" {label} " in haystack:
                                linked_group_id = table_label_groups[label]
                                metadata["evidence_linked_label"] = label
                                evidence_key = f"linked_table_label:{label}"
                                break

            metadata["evidence_key"] = evidence_key
            metadata["evidence_group_id"] = linked_group_id or self._evidence_group_id_for_key(upload.id, evidence_key)

    def _build_entity_segment_payloads(
        self,
        entities: Sequence[Mapping[str, Any]],
        *,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        compact_groups: dict[tuple[str, str, str], list[tuple[int, Mapping[str, Any]]]] = {}
        compact_roles = {"summary", "form_like"}
        for index, entity in enumerate(entities):
            table_meta = entity.get("table_metadata")
            sheet_role = ""
            sheet_name = ""
            table_title = ""
            if isinstance(table_meta, dict):
                sheet_role = str(table_meta.get("sheet_role") or "")
                sheet_name = str(table_meta.get("sheet_name") or "")
                table_title = str(table_meta.get("table_title") or "")
            if sheet_role in compact_roles:
                compact_groups.setdefault((sheet_role, sheet_name, table_title), []).append((index, entity))
                continue
            payloads.append(
                self._build_single_entity_segment_payload(
                    entity,
                    entity_index=index,
                    alias_hygiene=alias_hygiene,
                )
            )
        for (sheet_role, sheet_name, table_title), grouped_entities in compact_groups.items():
            payloads.extend(
                self._build_compact_spreadsheet_entity_payloads(
                    grouped_entities,
                    sheet_role=sheet_role,
                    sheet_name=sheet_name,
                    table_title=table_title,
                    alias_hygiene=alias_hygiene,
                )
            )
        return payloads

    def _build_single_entity_segment_payload(
        self,
        entity: Mapping[str, Any],
        *,
        entity_index: int,
        alias_hygiene: bool = False,
    ) -> dict[str, Any]:
        attributes = entity.get("attributes") or {}
        columns = entity.get("columns") or []
        alias_list = list(entity.get("aliases") or [])
        entity_type = entity.get("entity_type") or "record"
        entity_name = entity.get("entity_name") or f"{entity_type.title()} {entity_index + 1}"
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
            "entity_index": entity.get("entity_index", entity_index),
            "visibility": entity.get("visibility") or entity.get("entity_visibility"),
        }
        table_meta = entity.get("table_metadata")
        if isinstance(table_meta, dict):
            metadata["table_metadata"] = table_meta
            metadata["sheet_role"] = table_meta.get("sheet_role")
            metadata["sheet_name"] = table_meta.get("sheet_name")
            metadata["table_title"] = table_meta.get("table_title")
        metadata.update(self._alias_metadata(combined_aliases))
        return {"text": text, "metadata": metadata}

    def _build_compact_spreadsheet_entity_payloads(
        self,
        entities: Sequence[tuple[int, Mapping[str, Any]]],
        *,
        sheet_role: str,
        sheet_name: str,
        table_title: str,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        compact_payloads: list[dict[str, Any]] = []
        if not entities:
            return compact_payloads

        shard_size = 8 if sheet_role == "form_like" else 10
        label = table_title or sheet_name or "Spreadsheet Sheet"
        for shard_index in range(0, len(entities), shard_size):
            shard = entities[shard_index : shard_index + shard_size]
            first_payload = shard[0][1]
            lines = [f"{sheet_role.replace('_', ' ').title()}: {label}"]
            alias_values: list[str] = []
            entity_names: list[str] = []
            for _, entity in shard:
                attributes = entity.get("attributes") or {}
                columns = entity.get("columns") or []
                entity_name = str(entity.get("entity_name") or "").strip() or "Row"
                entity_names.append(entity_name)
                detail_parts = [entity_name]
                for column in columns[:6]:
                    value = str(attributes.get(column) or "").strip()
                    if value:
                        detail_parts.append(f"{column}: {value}")
                lines.append(f"- {' | '.join(detail_parts)}")
                for alias in entity.get("aliases") or []:
                    cleaned = str(alias or "").strip()
                    if cleaned and cleaned not in alias_values:
                        alias_values.append(cleaned)

            text = "\n".join(lines).strip()
            text, inline_aliases = self._inject_identifiers_into_text(text, alias_hygiene=alias_hygiene)
            for alias in inline_aliases:
                if alias and alias not in alias_values:
                    alias_values.append(alias)

            metadata: dict[str, Any] = {
                "strategy": "table_entity_compact",
                "index_type": "entity",
                "entity_type": first_payload.get("entity_type") or "record",
                "entity_name": label,
                "entity_business": first_payload.get("entity_business"),
                "visibility": first_payload.get("visibility") or first_payload.get("entity_visibility"),
                "sheet_role": sheet_role,
                "sheet_name": sheet_name,
                "table_title": table_title,
                "table_entity_compact": True,
                "entity_names": entity_names,
                "entity_count": len(shard),
                "compact_shard_index": (shard_index // shard_size) + 1,
            }
            table_meta = first_payload.get("table_metadata")
            if isinstance(table_meta, dict):
                metadata["table_metadata"] = table_meta
            metadata.update(self._alias_metadata(alias_values))
            compact_payloads.append({"text": text, "metadata": metadata})
        return compact_payloads

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
            raw_entity_type = entity.get("entity_type") or ""
            raw_entity_name = entity.get("entity_name") or ""
            raw_primary_label = raw_entity_name or raw_entity_type or ""
            entity_type = self._clamp_model_field_text(KnowledgeEntity, "entity_type", raw_entity_type)
            entity_name = self._clamp_model_field_text(KnowledgeEntity, "entity_name", raw_entity_name)
            primary_label = self._clamp_model_field_text(
                KnowledgeEntity,
                "primary_label",
                raw_primary_label,
            )
            entity_metadata = {
                "attributes": entity.get("attributes"),
                "columns": entity.get("columns"),
                "table_metadata": entity.get("table_metadata"),
            }
            if raw_entity_type and raw_entity_type != entity_type:
                entity_metadata["entity_type_truncated"] = raw_entity_type
            if raw_entity_name and raw_entity_name != entity_name:
                entity_metadata["entity_name_truncated"] = raw_entity_name
            if raw_primary_label and raw_primary_label != primary_label:
                entity_metadata["primary_label_truncated"] = raw_primary_label
            entity_model = KnowledgeEntity(
                business_profile=business,
                upload=upload,
                chunk_id=chunk.id if chunk else None,
                entity_type=entity_type,
                entity_name=entity_name,
                primary_label=primary_label,
                metadata=entity_metadata,
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
        
        # P0 #3: Invalidate table profile cache on new uploads
        try:
            from apps.rag.table_profile_cache import invalidate_table_profile_cache
            invalidate_table_profile_cache(business_id)
        except Exception as exc:
            logger.warning(
                "table_profile_cache.invalidate_failed business=%s error=%s",
                business_id,
                str(exc)[:200],
            )

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
        max_penalties = 18
        
        # Get table data
        column_schema = table_payload.column_schema or []
        rows = table_payload.rows or []
        page_number = table_payload.page_number or 0
        order_index = table_payload.order_index or 0

        def _row_type(row: Any) -> str:
            meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
            return str(meta.get("row_type") or "").strip().lower()

        # "Readable" rows must match read_knowledge's visible-row filter.
        readable_rows = [row for row in rows if _row_type(row) not in {"header", "section_header"}]
        section_header_rows = [row for row in rows if _row_type(row) == "section_header"]

        effective_columns = self._table_effective_column_count(table_payload)
        signals["effective_column_count"] = effective_columns
        
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
        
        # Heuristic 3: Row/column consistency (use readable rows only).
        row_lengths: list[int] = []
        non_empty_cells = 0
        expected_columns = len(column_schema)
        for row in readable_rows:
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
            fill_ratio = non_empty_cells / max(1, expected_columns * max(1, len(readable_rows)))
            signals["row_consistency"] = round(row_consistency, 2)
            signals["cell_fill_ratio"] = round(fill_ratio, 2)
            if row_consistency < 0.6:
                signals["row_misalignment"] = True
                penalties += 2
            if fill_ratio < 0.4:
                signals["sparse_table"] = True
                penalties += 1

        # Heuristic 3c: long prose cells inside narrow tables.
        non_empty_texts: list[str] = []
        long_cell_count = 0
        short_cell_count = 0
        max_cell_word_count = 0
        paragraph_like_rows = 0
        structured_rows = 0
        multi_cell_rows = 0
        value_rows = 0
        scaffold_rows = 0
        placeholder_cells = 0
        for row in readable_rows:
            row_has_long_cell = False
            row_texts: list[str] = []
            compact_cell_count = 0
            label_like_cells = 0
            row_placeholder_cells = 0
            row_has_numeric = False
            for cell in row.cells or []:
                raw_text = str(cell.raw_text or "").strip()
                if not raw_text:
                    continue
                row_texts.append(raw_text)
                non_empty_texts.append(raw_text)
                word_count = len(re.findall(r"\w+", raw_text))
                max_cell_word_count = max(max_cell_word_count, word_count)
                if self._pdf_cell_looks_placeholder(raw_text):
                    placeholder_cells += 1
                    row_placeholder_cells += 1
                if self._pdf_cell_looks_label_like(raw_text):
                    label_like_cells += 1
                if self._has_numeric_table_signal(raw_text):
                    row_has_numeric = True
                if word_count <= 2 or len(raw_text) <= 10:
                    short_cell_count += 1
                if 0 < word_count <= 6 and len(raw_text) <= 40:
                    compact_cell_count += 1
                if word_count >= self.pdf_table_paragraph_long_cell_words:
                    long_cell_count += 1
                    row_has_long_cell = True
            if row_has_long_cell:
                paragraph_like_rows += 1
            populated_cell_count = len(row_texts)
            if populated_cell_count:
                if populated_cell_count >= 2:
                    multi_cell_rows += 1
                row_has_value_evidence = (
                    (row_has_numeric or compact_cell_count >= 2)
                    and row_placeholder_cells < populated_cell_count
                    and label_like_cells < populated_cell_count
                    and not row_has_long_cell
                )
                if row_has_value_evidence:
                    value_rows += 1
                if (
                    row_has_numeric
                    or populated_cell_count >= 3
                    or (
                        populated_cell_count >= 2
                        and compact_cell_count >= 2
                        and label_like_cells < populated_cell_count
                    )
                ):
                    structured_rows += 1
                if (
                    populated_cell_count == 1
                    or row_has_long_cell
                    or row_placeholder_cells > 0
                    or label_like_cells >= populated_cell_count
                ):
                    scaffold_rows += 1
        long_cell_ratio = long_cell_count / max(1, len(non_empty_texts))
        short_cell_ratio = short_cell_count / max(1, len(non_empty_texts))
        signals["long_cell_ratio"] = round(long_cell_ratio, 2)
        signals["short_cell_ratio"] = round(short_cell_ratio, 2)
        signals["max_cell_word_count"] = max_cell_word_count
        signals["paragraph_like_rows"] = paragraph_like_rows
        data_row_count = len(readable_rows)
        structured_row_ratio = structured_rows / max(1, data_row_count)
        scaffold_row_ratio = scaffold_rows / max(1, data_row_count)
        placeholder_cell_ratio = placeholder_cells / max(1, len(non_empty_texts))
        multi_cell_row_ratio = multi_cell_rows / max(1, data_row_count)
        value_row_ratio = value_rows / max(1, data_row_count)
        signals["structured_row_ratio"] = round(structured_row_ratio, 2)
        signals["scaffold_row_ratio"] = round(scaffold_row_ratio, 2)
        signals["placeholder_cell_ratio"] = round(placeholder_cell_ratio, 2)
        signals["multi_cell_row_ratio"] = round(multi_cell_row_ratio, 2)
        signals["value_row_ratio"] = round(value_row_ratio, 2)
        if (
            effective_columns <= 2
            and len(readable_rows) >= self.pdf_table_paragraph_min_rows
            and long_cell_ratio >= self.pdf_table_paragraph_long_cell_ratio
        ):
            signals["paragraph_like_table"] = True
            penalties += 3
        if (
            effective_columns >= self.pdf_table_micro_fragment_min_columns
            and short_cell_ratio >= self.pdf_table_micro_fragment_short_cell_ratio
            and len(readable_rows) >= self.pdf_table_paragraph_min_rows
        ):
            signals["micro_fragment_table"] = True
            penalties += 4

        # Heuristic 3d: leading blank rows often indicate layout fragments or fake tables.
        leading_blank_rows = 0
        for row in rows:
            row_cells = list(row.cells or [])
            populated = sum(1 for cell in row_cells if str(cell.raw_text or "").strip())
            if populated == 0:
                leading_blank_rows += 1
                continue
            if populated <= 1 and all(len(str(cell.raw_text or "").strip()) <= 2 for cell in row_cells if str(cell.raw_text or "").strip()):
                leading_blank_rows += 1
                continue
            break
        signals["leading_blank_rows"] = leading_blank_rows
        if leading_blank_rows >= self.pdf_table_leading_blank_row_limit:
            penalties += 2

        # Heuristic 3b: logical-row fragmentation.
        # Penalize tables whose rows are structurally "consistent" but semantically shattered
        # into many tiny descriptor/value fragments across adjacent rows.
        def _leading_text(row: Any) -> str:
            values: list[str] = []
            for cell in list(row.cells or [])[:2]:
                value = str(cell.raw_text or "").strip()
                if value:
                    values.append(value)
            return " ".join(values).strip()

        def _non_empty_texts(row: Any) -> list[str]:
            return [str(cell.raw_text or "").strip() for cell in (row.cells or []) if str(cell.raw_text or "").strip()]

        descriptor_fragment_rows = 0
        value_fragment_rows = 0
        fragmented_streak = 0
        fragment_row_sequences = 0
        for row in readable_rows:
            non_empty = _non_empty_texts(row)
            leading = _leading_text(row)
            non_empty_count = len(non_empty)
            has_numeric = any(self._has_numeric_table_signal(text) for text in non_empty)
            descriptor_fragment = (
                0 < non_empty_count <= 2
                and bool(leading)
                and not has_numeric
                and len(re.findall(r"\w+", leading)) <= 5
            )
            value_fragment = any(
                text.endswith(("+", "/", "-"))
                or text.lower().startswith(("correspondent", "courier", "max.", "min.", "+"))
                for text in non_empty
            )
            if descriptor_fragment:
                descriptor_fragment_rows += 1
            if value_fragment:
                value_fragment_rows += 1
            if descriptor_fragment or value_fragment:
                fragmented_streak += 1
                if fragmented_streak == 2:
                    fragment_row_sequences += 1
            else:
                fragmented_streak = 0

        if len(readable_rows) >= 6 and (
            descriptor_fragment_rows >= 3
            or value_fragment_rows >= 3
            or fragment_row_sequences >= 2
        ):
            signals["fragmented_logical_rows"] = True
            signals["descriptor_fragment_rows"] = descriptor_fragment_rows
            signals["value_fragment_rows"] = value_fragment_rows
            signals["fragment_row_sequences"] = fragment_row_sequences
            penalties += 3

        # Heuristic 4: Header confidence
        header_cells = None
        header_rows = [row for row in rows if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"]
        header_column_coverage: list[str] = []
        if header_rows:
            max_header_columns = max(len(list(row.cells or [])) for row in header_rows)
            header_column_coverage = []
            for col_idx in range(max_header_columns):
                labels: list[str] = []
                seen_labels: set[str] = set()
                for row in header_rows:
                    row_cells = list(row.cells or [])
                    if col_idx >= len(row_cells):
                        continue
                    label = str(row_cells[col_idx].raw_text or "").strip()
                    if not label:
                        continue
                    dedupe_key = re.sub(r"\s+", " ", label).strip().lower()
                    if dedupe_key in seen_labels:
                        continue
                    seen_labels.add(dedupe_key)
                    labels.append(label)
                header_column_coverage.append(" | ".join(labels).strip())
            header_cells = [str(cell.raw_text or "") for cell in (header_rows[0].cells or [])]
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
            header_word_counts = [len(re.findall(r"\w+", str(cell or ""))) for cell in header_cells if str(cell or "").strip()]
            header_long_cells = sum(
                1
                for cell in header_cells
                if len(re.findall(r"\w+", str(cell or ""))) >= max(10, self.pdf_table_paragraph_long_cell_words)
                or len(str(cell or "").strip()) >= 80
            )
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
            if header_long_cells > 0 and header_word_counts:
                long_ratio = header_long_cells / max(1, len(header_word_counts))
                avg_words = sum(header_word_counts) / float(len(header_word_counts))
                if long_ratio >= 0.5 or avg_words >= 10.0:
                    signals["header_paragraph_like"] = True
            if header_confidence < 0.3:
                penalties += 1
        else:
            signals["header_confidence"] = 0.0
            penalties += 1
        if (
            effective_columns <= 3
            and data_row_count <= 12
            and structured_row_ratio <= 0.45
            and scaffold_row_ratio >= 0.5
        ):
            signals["low_structure_table"] = True
            penalties += 3
        if (
            effective_columns <= 3
            and 0 < data_row_count <= 4
            and bool(signals.get("nonsense_columns"))
            and float(signals.get("header_confidence") or 0.0) == 0.0
            and multi_cell_row_ratio >= 0.8
            and structured_row_ratio >= 0.8
            and scaffold_row_ratio >= 0.5
            and placeholder_cell_ratio >= 0.2
        ):
            signals["compact_banner_table"] = True
            penalties += 3

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
        
        # Heuristic 6: Too few readable rows (header + section_header excluded).
        if data_row_count < 2:
            signals['insufficient_rows'] = True
            penalties += 2

        if (
            0 < data_row_count <= self.pdf_table_bridge_max_rows
            and effective_columns <= 3
            and (
                long_cell_ratio >= self.pdf_table_bridge_long_cell_ratio
                or max_cell_word_count >= max(10, self.pdf_table_paragraph_long_cell_words - 2)
            )
            and (
                max_cell_word_count >= (self.pdf_table_paragraph_long_cell_words * 2)
                or float(signals.get("header_confidence") or 0.0) == 0.0
                or bool(signals.get("nonsense_columns"))
            )
        ):
            signals["bridge_like_table"] = True
            penalties += 3

        # Heuristic 6b: No readable rows at all.
        # If our postprocess classified everything as section_header/header, the
        # table will be unreadable at runtime (read_knowledge excludes them).
        if not readable_rows and section_header_rows:
            signals["no_readable_rows"] = True
            penalties += 5
        
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
        
        # Heuristic 9: Column misalignment detection
        # Detects when header cells are empty but corresponding data cells have values
        # This is a common Azure DI extraction error for complex tables
        if header_column_coverage and rows:
            data_rows_for_check = readable_rows[:5]
            empty_header_with_data: list[int] = []
            
            for col_idx, header_val in enumerate(header_column_coverage):
                header_empty = not str(header_val or "").strip()
                if header_empty:
                    # Check if any data rows have values in this column
                    for row in data_rows_for_check:
                        cell_list = list(row.cells or [])
                        for cell in cell_list:
                            if cell.column_index == col_idx:
                                cell_text = str(cell.raw_text or "").strip()
                                if cell_text and len(cell_text) > 2:
                                    if not self._quality_misalignment_is_legitimate(
                                        col_idx=col_idx,
                                        header_column_coverage=header_column_coverage,
                                        data_rows=data_rows_for_check,
                                    ):
                                        empty_header_with_data.append(col_idx)
                                    break
                        if col_idx in empty_header_with_data:
                            break
            
            if empty_header_with_data:
                signals['column_misalignment'] = True
                signals['misaligned_columns'] = empty_header_with_data[:5]
                # Significant penalty - this causes wrong data attribution
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

    def _table_descriptor_continuation_tokens(self) -> set[str]:
        # Generic continuation cues for wrapped descriptor labels.
        return {
            "and", "or", "of", "in", "for", "to", "from", "by", "with",
            "without", "on", "at", "via", "through", "the", "a", "an",
            "company", "group", "department", "division", "exchange", "branch",
            "unit", "office", "region", "country", "city",
        }

    @staticmethod
    def _looks_like_tier_descriptor(value: str) -> bool:
        sample = str(value or "").strip().lower()
        if not sample:
            return False
        if not re.search(r"\d", sample):
            return False
        if sample.startswith("from "):
            return True
        if sample.startswith("up to ") or sample.startswith("upto "):
            return True
        if sample.startswith("less than ") or sample.startswith("more than "):
            return True
        if sample.startswith("below ") or sample.startswith("above "):
            return True
        if sample.endswith("+") or " - " in sample or " – " in sample:
            return True
        return False

    @staticmethod
    def _value_fragment_connector_tokens() -> set[str]:
        return {
            "and", "or", "with", "without", "minimum", "maximum", "max", "min",
            "no", "up", "to", "from", "per", "each", "equivalent",
        }

    @staticmethod
    def _is_complete_value_state(value: str) -> bool:
        sample = str(value or "").strip().lower()
        if not sample:
            return False
        normalized = re.sub(r"\s+", " ", sample)
        return normalized in {
            "free",
            "no fee",
            "no fees",
            "waived",
            "n/a",
            "na",
        }

    @staticmethod
    def _fragment_has_numeric_signal(value: str) -> bool:
        sample = str(value or "").strip().lower()
        if not sample:
            return False
        if re.search(r"\d", sample):
            return True
        if "%" in sample:
            return True
        return bool(
            re.search(
                r"\b(?:usd|eur|gbp|jpy|chf|aud|cad|cny|inr|sar|aed|egp|qar|kwd|omr|bhd|try|zar)\b",
                sample,
            )
        )

    @staticmethod
    def _table_row_has_value_keyword(value: str) -> bool:
        sample = str(value or "").strip()
        if not sample:
            return False
        return bool(_TABLE_ROW_VALUE_KEYWORD_RE.search(sample))

    def _table_row_signal_score(
        self,
        *,
        value_by_label: Mapping[str, str],
        inferred_scope_columns: Sequence[str],
        observed_value_columns: Sequence[str],
        fee_value: str,
    ) -> tuple[float, dict[str, Any]]:
        values = [self._table_cell_text(value) for value in value_by_label.values()]
        values = [value for value in values if value]
        pair_count = len(values)
        numeric_value_count = sum(1 for value in values if _column_numeric_signal(value))
        # Numeric-dense single-cell rows (fee formulas / min-max / multiple values) are meaningful
        # even if only one column is populated. Count numeric-like tokens so such rows don't get
        # suppressed by the row-signal filter.
        currency_signal_token_count = sum(len(_TABLE_NUMERIC_SIGNAL_TOKEN_RE.findall(value)) for value in values)
        keyword_value_count = sum(1 for value in values if self._table_row_has_value_keyword(value))
        scope_column_count = len(list(inferred_scope_columns or []))
        observed_value_column_count = len(list(observed_value_columns or []))
        has_fee_value = bool(self._table_cell_text(fee_value))

        score = 0.0
        if numeric_value_count > 0:
            score += 1.25
        if keyword_value_count > 0:
            score += 0.75
        if has_fee_value:
            score += 1.0
        if scope_column_count > 0:
            score += 0.6
        if observed_value_column_count >= 2:
            score += 0.35
        if pair_count >= 3:
            score += 0.4
        if pair_count >= 5:
            score += 0.3

        diagnostics = {
            "pair_count": int(pair_count),
            "numeric_value_count": int(numeric_value_count),
            "currency_signal_token_count": int(currency_signal_token_count),
            "keyword_value_count": int(keyword_value_count),
            "scope_column_count": int(scope_column_count),
            "observed_value_column_count": int(observed_value_column_count),
            "has_fee_value": has_fee_value,
            "score": round(float(score), 3),
        }
        return score, diagnostics

    def _table_row_structural_context_profile(
        self,
        *,
        value_by_label: Mapping[str, str],
        scope_dimension_columns: Sequence[str],
        fee_value: str,
        row_signal_diag: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_scope_labels = [
            self._table_cell_text(label)
            for label in scope_dimension_columns or []
            if self._table_cell_text(label)
        ]
        normalized_scope_labels = list(dict.fromkeys(normalized_scope_labels))
        normalized_lookup = {
            self._table_cell_text(label): self._table_cell_text(value)
            for label, value in (value_by_label or {}).items()
            if self._table_cell_text(label)
        }

        def _norm(value: str) -> str:
            return re.sub(r"\s+", " ", str(value or "").strip()).lower()

        scope_echo_count = 0
        scope_numeric_count = 0
        for label in normalized_scope_labels:
            value = normalized_lookup.get(label, "")
            if value and _norm(value) == _norm(label):
                scope_echo_count += 1
            if value and _column_numeric_signal(value):
                scope_numeric_count += 1

        scope_label_count = len(normalized_scope_labels)
        scope_echo_ratio = (
            float(scope_echo_count) / float(scope_label_count)
            if scope_label_count > 0
            else 0.0
        )
        has_fee_value = bool(self._table_cell_text(fee_value)) or bool(row_signal_diag.get("has_fee_value"))
        is_structural = bool(
            scope_label_count >= 3
            and scope_echo_count >= max(2, int(math.ceil(scope_label_count * 0.6)))
            and scope_numeric_count == 0
            and not has_fee_value
        )
        return {
            "is_structural_context": is_structural,
            "scope_label_count": int(scope_label_count),
            "scope_echo_count": int(scope_echo_count),
            "scope_numeric_count": int(scope_numeric_count),
            "scope_echo_ratio": round(float(scope_echo_ratio), 4),
        }

    def _is_prefix_value_fragment(self, value: str) -> bool:
        sample = self._table_cell_text(value)
        if not sample:
            return False
        if self._is_complete_value_state(sample):
            return False
        if self._fragment_has_numeric_signal(sample):
            return False
        tokens = [tok for tok in re.split(r"[^a-z0-9]+", sample.lower()) if tok]
        if not tokens or len(tokens) > 5:
            return False
        if sample.startswith("("):
            return True
        first = tokens[0]
        return first in self._value_fragment_connector_tokens()

    def _is_suffix_value_fragment(self, value: str) -> bool:
        sample = self._table_cell_text(value)
        if not sample:
            return False
        tokens = [tok for tok in re.split(r"[^a-z0-9]+", sample.lower()) if tok]
        if not tokens or len(tokens) > 8:
            return False
        if self._fragment_has_numeric_signal(sample) and len(tokens) > 4:
            return False
        return tokens[0] in self._value_fragment_connector_tokens()

    def _numeric_fragments_compatible(self, values: Sequence[str]) -> bool:
        normalized = [self._table_cell_text(v).lower() for v in values if self._table_cell_text(v)]
        if not normalized:
            return False
        uniq = list(dict.fromkeys(normalized))
        if len(uniq) <= 1:
            return True
        # Allow near-equivalent numeric fragments (one containing the other).
        for left in uniq:
            for right in uniq:
                if left == right:
                    continue
                if left in right or right in left:
                    continue
                return False
        return True

    def _row_cell_lookup(self, row: TableRowPayload) -> dict[int, TableCellPayload]:
        lookup: dict[int, TableCellPayload] = {}
        for cell in row.cells or []:
            try:
                idx = int(cell.column_index)
            except (TypeError, ValueError):
                continue
            lookup[idx] = cell
        return lookup

    def _rewrite_row_cell_text(
        self,
        *,
        row: TableRowPayload,
        column_index: int,
        new_value: str,
        metadata_patch: Mapping[str, Any] | None = None,
    ) -> TableRowPayload:
        new_cells: list[TableCellPayload] = []
        changed = False
        for cell in row.cells or []:
            if int(getattr(cell, "column_index", -1)) != column_index:
                new_cells.append(cell)
                continue
            changed = True
            cell_meta = dict(cell.metadata or {})
            if metadata_patch:
                cell_meta.update(dict(metadata_patch))
            new_cells.append(
                TableCellPayload(
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    column_key=cell.column_key,
                    raw_text=new_value,
                    normalized_value=cell.normalized_value,
                    bbox=cell.bbox,
                    confidence=cell.confidence,
                    metadata=cell_meta,
                )
            )
        if not changed:
            return row
        ordered_cells = sorted(
            new_cells,
            key=lambda c: int(getattr(c, "column_index", 0)),
        )
        row_text = " | ".join(
            str(cell.raw_text or "").strip()
            for cell in ordered_cells
            if str(cell.raw_text or "").strip()
        ).strip()
        row_meta = dict(row.metadata or {})
        return TableRowPayload(
            row_index=row.row_index,
            page_number=row.page_number,
            bbox=row.bbox,
            raw_text=row_text or row.raw_text,
            metadata=row_meta,
            cells=new_cells,
        )

    def _stitch_table_row_continuations(self, table: TablePayload) -> tuple[TablePayload, int]:
        rows = list(table.rows or [])
        if len(rows) < 3:
            return table, 0

        column_map = self._table_column_map_for_payload(table)
        if not column_map:
            return table, 0
        role_payloads = self._infer_table_column_roles(
            table_rows=rows,
            column_map=column_map,
            cached_roles=(table.data_dictionary or {}).get("column_roles"),
        )
        role_lookup = role_lookup_by_index(role_payloads)
        descriptor_indices = sorted(
            idx
            for idx, payload in role_lookup.items()
            if str(payload.get("role") or "") == COLUMN_ROLE_DESCRIPTOR
        )
        descriptor_idx = descriptor_indices[0] if descriptor_indices else 0

        row_order = sorted(rows, key=lambda row: int(getattr(row, "row_index", 0)))
        data_rows = [
            row
            for row in row_order
            if str((row.metadata or {}).get("row_type") or "").strip().lower() != "header"
        ]
        if len(data_rows) < 2:
            return table, 0

        continuation_tokens = self._table_descriptor_continuation_tokens()
        row_by_index = {int(row.row_index): row for row in rows}
        updated_rows = dict(row_by_index)
        stitched_pairs = 0

        for pos in range(1, len(data_rows)):
            prev_row = updated_rows.get(int(data_rows[pos - 1].row_index)) or data_rows[pos - 1]
            curr_row = updated_rows.get(int(data_rows[pos].row_index)) or data_rows[pos]

            prev_cells = self._row_cell_lookup(prev_row)
            curr_cells = self._row_cell_lookup(curr_row)
            prev_desc_cell = prev_cells.get(descriptor_idx)
            curr_desc_cell = curr_cells.get(descriptor_idx)
            if prev_desc_cell is None or curr_desc_cell is None:
                continue

            prev_desc = self._table_cell_text(prev_desc_cell.raw_text or "")
            curr_desc = self._table_cell_text(curr_desc_cell.raw_text or "")
            if not prev_desc or not curr_desc:
                continue
            if prev_desc.lower() == curr_desc.lower():
                continue

            try:
                prev_span = int((prev_desc_cell.metadata or {}).get("row_span") or 1)
                curr_span = int((curr_desc_cell.metadata or {}).get("row_span") or 1)
            except (TypeError, ValueError):
                prev_span = 1
                curr_span = 1
            if prev_span > 1 or curr_span > 1:
                continue

            prev_words = prev_desc.split()
            curr_words = curr_desc.split()
            if len(prev_words) < 3 or len(curr_words) > 8:
                continue
            if re.search(r"[.!?:;]\s*$", prev_desc):
                continue
            if self._looks_like_tier_descriptor(prev_desc) and self._looks_like_tier_descriptor(curr_desc):
                continue

            first_curr_token = re.sub(r"[^a-z0-9]+", "", curr_words[0].lower())
            continuation_cue = bool(first_curr_token and first_curr_token in continuation_tokens)
            if not continuation_cue and not curr_desc[:1].islower():
                continue

            prev_non_descriptor = {
                idx for idx, cell in prev_cells.items()
                if idx != descriptor_idx and str(cell.raw_text or "").strip()
            }
            curr_non_descriptor = {
                idx for idx, cell in curr_cells.items()
                if idx != descriptor_idx and str(cell.raw_text or "").strip()
            }
            if not prev_non_descriptor or not curr_non_descriptor:
                continue
            if not (prev_non_descriptor & curr_non_descriptor):
                continue

            combined = self._table_cell_text(f"{prev_desc} {curr_desc}")
            if not combined or len(combined) <= max(len(prev_desc), len(curr_desc)):
                continue
            if len(combined) > 220:
                continue

            row_patch = {
                "descriptor_continuation_stitched": True,
                "descriptor_continuation_anchor_row": int(prev_row.row_index),
            }
            updated_prev = self._rewrite_row_cell_text(
                row=prev_row,
                column_index=descriptor_idx,
                new_value=combined,
                metadata_patch=row_patch,
            )
            updated_curr = self._rewrite_row_cell_text(
                row=curr_row,
                column_index=descriptor_idx,
                new_value=combined,
                metadata_patch=row_patch,
            )
            prev_meta = dict(updated_prev.metadata or {})
            curr_meta = dict(updated_curr.metadata or {})
            prev_meta.update(row_patch)
            curr_meta.update(row_patch)
            updated_prev = TableRowPayload(
                row_index=updated_prev.row_index,
                page_number=updated_prev.page_number,
                bbox=updated_prev.bbox,
                raw_text=updated_prev.raw_text,
                metadata=prev_meta,
                cells=updated_prev.cells,
            )
            updated_curr = TableRowPayload(
                row_index=updated_curr.row_index,
                page_number=updated_curr.page_number,
                bbox=updated_curr.bbox,
                raw_text=updated_curr.raw_text,
                metadata=curr_meta,
                cells=updated_curr.cells,
            )

            updated_rows[int(updated_prev.row_index)] = updated_prev
            updated_rows[int(updated_curr.row_index)] = updated_curr
            stitched_pairs += 1

        if stitched_pairs <= 0:
            return table, 0

        rebuilt_rows = [
            updated_rows.get(int(row.row_index), row)
            for row in row_order
        ]
        table_meta = dict(table.metadata or {})
        table_meta["row_continuation_stitched_pairs"] = stitched_pairs
        return (
            TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=table_meta,
                rows=rebuilt_rows,
            ),
            stitched_pairs,
        )

    def _stitch_scope_value_fragments(self, table: TablePayload) -> tuple[TablePayload, int]:
        rows = list(table.rows or [])
        if len(rows) < 2:
            return table, 0

        column_map = self._table_column_map_for_payload(table)
        if not column_map:
            return table, 0
        role_payloads = self._infer_table_column_roles(
            table_rows=rows,
            column_map=column_map,
            cached_roles=(table.data_dictionary or {}).get("column_roles"),
        )
        role_lookup = role_lookup_by_index(role_payloads)

        scope_indices: set[int] = set()
        for idx, payload in role_lookup.items():
            role = str(payload.get("role") or "").strip()
            if role == COLUMN_ROLE_SCOPE_DIMENSION:
                scope_indices.add(int(idx))
                continue
            # In noisy OCR tables, true scope columns can be temporarily
            # classified as qualifier; include non-contextual candidates and
            # let row-level fragment gates decide whether stitching applies.
            if role in {COLUMN_ROLE_QUALIFIER, ""}:
                scope_indices.add(int(idx))
        scope_order = sorted(scope_indices)
        if len(scope_order) < 2:
            return table, 0

        row_order = sorted(rows, key=lambda row: int(getattr(row, "row_index", 0)))
        updated_rows = {int(row.row_index): row for row in row_order}
        stitched_cells = 0

        for row in row_order:
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            if str(row_meta.get("row_type") or "").strip().lower() == "header":
                continue
            effective_row = updated_rows.get(int(row.row_index), row)
            lookup = self._row_cell_lookup(effective_row)

            fragments: list[tuple[int, str]] = []
            numeric_values: list[str] = []
            prefix_indices: set[int] = set()
            suffix_indices: set[int] = set()
            for idx in scope_order:
                cell = lookup.get(idx)
                value = self._table_cell_text(cell.raw_text if cell else "")
                if not value:
                    continue
                fragments.append((idx, value))
                if self._fragment_has_numeric_signal(value):
                    numeric_values.append(value)
                if self._is_prefix_value_fragment(value):
                    prefix_indices.add(idx)
                if self._is_suffix_value_fragment(value):
                    suffix_indices.add(idx)

            if len(fragments) < 2:
                continue
            if not prefix_indices or not numeric_values:
                continue
            if not self._numeric_fragments_compatible(numeric_values):
                continue

            ordered_unique_parts: list[str] = []
            seen_parts: set[str] = set()
            for _idx, value in fragments:
                key = value.lower()
                if key in seen_parts:
                    continue
                ordered_unique_parts.append(value)
                seen_parts.add(key)
            if len(ordered_unique_parts) < 2:
                continue
            merged_value = self._table_cell_text(" ".join(ordered_unique_parts))
            if not merged_value:
                continue
            if len(merged_value) > 260:
                continue

            replace_indices = set(prefix_indices) | set(suffix_indices)
            # When multiple prefix fragments exist, propagate full value across
            # all visible scope fragments in the row for consistent semantics.
            if len(prefix_indices) >= 2:
                replace_indices.update(idx for idx, _value in fragments)
            if not replace_indices:
                continue

            sorted_replace = sorted(replace_indices)
            anchor_idx = sorted_replace[0] if sorted_replace else None
            contiguous = bool(
                sorted_replace
                and all((sorted_replace[i] - sorted_replace[i - 1]) == 1 for i in range(1, len(sorted_replace)))
            )
            anchor_span = len(sorted_replace) if contiguous and len(sorted_replace) > 1 else 1

            updated_row = effective_row
            row_rewrites = 0
            for idx in sorted(replace_indices):
                existing_cell = self._row_cell_lookup(updated_row).get(idx)
                if existing_cell is None:
                    continue
                existing_value = self._table_cell_text(existing_cell.raw_text or "")
                if not existing_value or existing_value.lower() == merged_value.lower():
                    continue
                updated_row = self._rewrite_row_cell_text(
                    row=updated_row,
                    column_index=idx,
                    new_value=merged_value,
                    metadata_patch={
                        "value_fragment_stitched": True,
                        "value_fragment_stitch_source_row": int(row.row_index),
                        # Reset stale extraction spans on rewritten fragments.
                        # If rewritten indices are contiguous, encode one explicit
                        # span anchor so scope refresh can infer the full range.
                        "column_span": anchor_span if idx == anchor_idx else 1,
                    },
                )
                row_rewrites += 1

            if row_rewrites > 0:
                new_meta = dict(updated_row.metadata or {})
                new_meta.update(
                    {
                        "value_fragment_stitched": True,
                        "value_fragment_stitch_cells": row_rewrites,
                    }
                )
                updated_rows[int(updated_row.row_index)] = TableRowPayload(
                    row_index=updated_row.row_index,
                    page_number=updated_row.page_number,
                    bbox=updated_row.bbox,
                    raw_text=updated_row.raw_text,
                    metadata=new_meta,
                    cells=updated_row.cells,
                )
                stitched_cells += row_rewrites

        if stitched_cells <= 0:
            return table, 0

        rebuilt_rows = [updated_rows.get(int(row.row_index), row) for row in row_order]
        table_meta = dict(table.metadata or {})
        table_meta["value_fragment_stitched_cells"] = stitched_cells
        return (
            TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=table_meta,
                rows=rebuilt_rows,
            ),
            stitched_cells,
        )

    def _refresh_table_scope_annotations(self, table: TablePayload) -> TablePayload:
        if not table.rows or len(table.column_schema or []) < 3:
            return table
        header_rows: set[int] = {
            int(row.row_index)
            for row in (table.rows or [])
            if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"
        }
        annotator = AzureDocumentIntelligenceExtractor(endpoint=None, key=None)
        annotated_rows = annotator._annotate_row_applicability(
            table_rows=table.rows or [],
            column_schema=table.column_schema or [],
            header_rows=header_rows,
        )
        table_meta = dict(table.metadata or {})
        table_meta["scope_postprocess_refreshed"] = True
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=table.column_schema,
            data_dictionary=table.data_dictionary,
            metadata=table_meta,
            rows=annotated_rows,
        )

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

    def _table_dedupe_heading_key(self, table: TablePayload) -> str:
        meta = table.metadata if isinstance(table.metadata, Mapping) else {}
        candidates = [
            str(table.section_heading or ""),
            str(meta.get("derived_section_heading") or ""),
            str(table.title or ""),
        ]
        for candidate in candidates:
            normalized = self._normalize_evidence_phrase(candidate)
            if normalized:
                return normalized
        return ""

    def _table_region_overlap_ratio(self, left: TablePayload, right: TablePayload) -> float:
        left_bbox = self._normalize_bbox(left.bbox)
        right_bbox = self._normalize_bbox(right.bbox)
        if not left_bbox or not right_bbox:
            return 0.0

        ix0 = max(left_bbox["x0"], right_bbox["x0"])
        iy0 = max(left_bbox["y0"], right_bbox["y0"])
        ix1 = min(left_bbox["x1"], right_bbox["x1"])
        iy1 = min(left_bbox["y1"], right_bbox["y1"])
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0

        intersection = (ix1 - ix0) * (iy1 - iy0)
        union = self._bbox_area(left_bbox) + self._bbox_area(right_bbox) - intersection
        if union <= 0.0:
            return 0.0
        return intersection / union

    def _tables_are_duplicate_candidates(
        self,
        left: TablePayload,
        right: TablePayload,
        *,
        left_labels: set[str],
        right_labels: set[str],
    ) -> tuple[bool, dict[str, Any]]:
        overlap = 0.0
        if left_labels and right_labels:
            overlap = len(left_labels & right_labels) / max(1, len(left_labels | right_labels))
        if overlap < self.table_dedupe_min_overlap:
            return False, {"overlap": round(overlap, 3), "reason": "label_overlap_below_threshold"}

        left_heading = self._table_dedupe_heading_key(left)
        right_heading = self._table_dedupe_heading_key(right)
        headings_match = bool(left_heading and right_heading and left_heading == right_heading)
        region_overlap = self._table_region_overlap_ratio(left, right)
        same_region = region_overlap >= 0.3

        is_duplicate = headings_match or same_region
        reason = "heading_match" if headings_match else ("region_overlap" if same_region else "distinct_region_or_heading")
        return is_duplicate, {
            "overlap": round(overlap, 3),
            "region_overlap": round(region_overlap, 3),
            "headings_match": headings_match,
            "reason": reason,
        }

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
        meta = {
            "deduped_tables": 0,
            "header_inferred": 0,
            "row_continuation_stitched_pairs": 0,
            "value_fragment_stitched_cells": 0,
            "scope_refreshed_tables": 0,
        }
        processed: list[TablePayload] = []

        def _jaccard(a: set[str], b: set[str]) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / max(1, len(a | b))

        for page_number, page_tables in grouped.items():
            page_tables = sorted(page_tables, key=lambda t: t.order_index)
            stitched_tables: list[TablePayload] = []
            for table in page_tables:
                stitched_table, stitched_pairs = self._stitch_table_row_continuations(table)
                if stitched_pairs > 0:
                    meta["row_continuation_stitched_pairs"] += stitched_pairs
                    issues.append(
                        IssuePayload(
                            code="table_row_continuation_stitched",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Descriptor continuation text stitched across adjacent rows.",
                            page_number=page_number,
                            table_order_index=table.order_index,
                            details={"stitched_pairs": stitched_pairs},
                        )
                    )
                stitched_tables.append(stitched_table)
            page_tables = stitched_tables

            value_stitched_tables: list[TablePayload] = []
            for table in page_tables:
                value_table, stitched_cells = self._stitch_scope_value_fragments(table)
                if stitched_cells > 0:
                    meta["value_fragment_stitched_cells"] += stitched_cells
                value_stitched_tables.append(value_table)
            page_tables = value_stitched_tables

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
                            is_duplicate, dedupe_diag = self._tables_are_duplicate_candidates(
                                existing,
                                table,
                                left_labels=dedupe_labels[idx],
                                right_labels=labels,
                            )
                            if is_duplicate:
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
                                        details=dedupe_diag,
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
                table = self._refresh_table_scope_annotations(table)
                meta["scope_refreshed_tables"] += 1
                processed.append(table)

        return processed, issues, meta

    @staticmethod
    def _derive_table_section_heading_from_blocks(
        *,
        table_bbox: Mapping[str, Any],
        page_blocks: Sequence[PageBlockPayload],
        page_number: int | None,
        page_height: float | None,
    ) -> tuple[str, str | None]:
        """
        Best-effort extraction of a table "section heading" from the page text blocks.

        Motivation: many PDFs render headings (e.g. "Fees & Charges") as a separate
        block above the table. If we only index the table rows, broad queries like
        "plus fees" may never retrieve the table even though it's relevant.

        Heuristic: pick the closest *heading-like* block above the table bbox with
        sufficient horizontal overlap. Returns (heading_text, heading_anchor).
        """

        def _bbox_tuple(bbox: Mapping[str, Any]) -> tuple[float, float, float, float]:
            try:
                x0 = float(bbox.get("x0") or 0.0)
                y0 = float(bbox.get("y0") or 0.0)
                x1 = float(bbox.get("x1") or 0.0)
                y1 = float(bbox.get("y1") or 0.0)
            except Exception:
                return 0.0, 0.0, 0.0, 0.0
            return x0, y0, x1, y1

        def _heading_like(text: str) -> bool:
            if not text:
                return False
            if len(text) > 80:
                return False
            if "@" in text:
                return False
            lowered = text.lower()
            if "http://" in lowered or "https://" in lowered or "www." in lowered:
                return False
            if CARD_NUMBER_PATTERN.search(text):
                return False
            # Loose phone-number-like detector (avoid copying PII-ish headings).
            if re.search(r"\+?\d[\d\s().-]{8,}\d", text):
                return False

            alnum = [c for c in text if c.isalnum()]
            if alnum:
                digits = sum(1 for c in alnum if c.isdigit())
                if (digits / len(alnum)) > 0.3:
                    return False

            words = text.split()
            if not words or len(words) > 12:
                return False

            # Headings tend to be short fragments, not sentences.
            if text.endswith((".", "?", "!")):
                return False
            if text.count(".") >= 2:
                return False

            return True

        table_x0, table_y0, table_x1, table_y1 = _bbox_tuple(table_bbox)
        if table_x1 <= table_x0 or table_y1 <= table_y0:
            return "", None

        table_width = max(1.0, table_x1 - table_x0)
        max_gap = 200.0
        if page_height and page_height > 0:
            max_gap = max(40.0, float(page_height) * 0.25)

        candidates: list[tuple[float, float, int, str, str | None]] = []
        for block in page_blocks:
            raw_text = KnowledgeIngestionService._sanitize_text(getattr(block, "text", "")).strip()
            if not raw_text:
                continue
            if "\t" in raw_text or "|" in raw_text:
                # Likely a table-like block; don't use as a heading.
                continue

            text = raw_text.replace("\n", " ")
            text = re.sub(r"\s+", " ", text).strip()
            if not _heading_like(text):
                continue

            meta = getattr(block, "metadata", None) or {}
            region_role = str(meta.get("region_role") or "").strip().lower()
            if region_role in {"table", "figure", "decorative"}:
                continue

            bx0, by0, bx1, by1 = _bbox_tuple(getattr(block, "bbox", {}) or {})
            if bx1 <= bx0 or by1 <= by0:
                continue

            # Skip page headers/footers (reduce false associations).
            if page_height and page_height > 0:
                if by1 <= float(page_height) * 0.08:
                    continue
                if by0 >= float(page_height) * 0.92:
                    continue

            # Must be above (or barely overlapping) the table.
            if by1 > (table_y0 + 2.0):
                continue

            gap = table_y0 - by1
            if gap < -2.0 or gap > max_gap:
                continue

            # Require some horizontal overlap with the table region.
            overlap = min(bx1, table_x1) - max(bx0, table_x0)
            if overlap <= 0:
                continue
            block_width = max(1.0, bx1 - bx0)
            overlap_ratio = overlap / max(1.0, min(block_width, table_width))
            if overlap_ratio < 0.3:
                continue

            anchor = None
            if page_number is not None:
                try:
                    anchor = f"p{int(page_number)}-b{int(getattr(block, 'order_index', 0))}"
                except Exception:
                    anchor = f"p{page_number}-b0"

            # Prefer: closest block above table, then best overlap.
            candidates.append((float(gap), -float(overlap_ratio), len(text), text, anchor))

        if not candidates:
            return "", None

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        _gap, _overlap, _len, best_text, best_anchor = candidates[0]
        return best_text, best_anchor

    def _persist_structured_artifacts(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> dict[str, Any]:
        KnowledgeUploadPage.objects.filter(upload=upload).delete()
        KnowledgeTableColumn.objects.filter(upload=upload).delete()  # PHASE 2: Delete indexed columns
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
        page_payload_lookup: dict[int, PageLayout] = {}
        block_objects: list[KnowledgeUploadPageBlock] = []
        page_summaries: list[dict[str, Any]] = []

        for page_payload in extraction.pages:
            page_payload_lookup[page_payload.page_number] = page_payload
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
            
            # Log warning for column misalignment (common Azure DI extraction error)
            if quality_assessment.get('signals', {}).get('column_misalignment'):
                misaligned_cols = quality_assessment['signals'].get('misaligned_columns', [])
                logger.warning(
                    "table.quality.column_misalignment upload=%s table=%s columns=%s "
                    "hint=Header cells empty but data cells have values; may cause wrong data attribution",
                    upload.id,
                    table_payload.order_index,
                    misaligned_cols,
                )
            
            # Merge quality data into table metadata
            table_metadata = dict(table_payload.metadata or {})
            table_metadata['quality_score'] = quality_assessment['quality_score']
            table_metadata['is_decorative'] = quality_assessment['is_decorative']
            table_metadata['quality_signals'] = quality_assessment['signals']
            if table_payload.page_number:
                table_metadata["page_anchor"] = f"p{table_payload.page_number}-t{table_payload.order_index}"
            else:
                table_metadata["page_anchor"] = f"t{table_payload.order_index}"
            
            raw_section_heading = (
                self._sanitize_text(table_payload.section_heading).strip()
                if isinstance(table_payload.section_heading, str)
                else ""
            )
            derived_section_heading = ""
            derived_section_heading_anchor: str | None = None
            if not raw_section_heading:
                page_payload = page_payload_lookup.get(table_payload.page_number or -1)
                derived_section_heading, derived_section_heading_anchor = (
                    self._derive_table_section_heading_from_blocks(
                        table_bbox=table_payload.bbox or {},
                        page_blocks=(page_payload.blocks if page_payload else []),
                        page_number=(page_payload.page_number if page_payload else None),
                        page_height=(page_payload.height if page_payload else None),
                    )
                )
                derived_section_heading = self._sanitize_text(derived_section_heading).strip()
                if derived_section_heading:
                    table_metadata["derived_section_heading"] = derived_section_heading
                    table_metadata["derived_section_heading_method"] = "page_block_above_table"
                    if derived_section_heading_anchor:
                        table_metadata["derived_section_heading_anchor"] = derived_section_heading_anchor

            section_heading = raw_section_heading or derived_section_heading

            page_obj = page_lookup.get(table_payload.page_number or -1)
            table_obj = KnowledgeUploadTable.objects.create(
                upload=upload,
                page=page_obj,
                source_block=None,
                title=self._clamp_text(
                    self._derive_table_title(table_payload, upload),
                    table_title_max,
                ),
                section_heading=self._clamp_text(section_heading, table_section_heading_max),
                order_index=table_payload.order_index,
                bbox=table_payload.bbox,
                column_schema=table_payload.column_schema,
                data_dictionary=table_payload.data_dictionary,
                metadata=table_metadata,  # Include quality metadata
            )
            table_lookup[(table_payload.order_index, table_payload.page_number)] = table_obj
            
            # PHASE 2: Index table columns for column-header search
            column_objects = []
            for idx, col_name in enumerate(table_payload.column_schema or []):
                if not col_name:
                    continue
                col_str = str(col_name).strip()
                if not col_str:
                    continue
                column_objects.append(
                    KnowledgeTableColumn(
                        table=table_obj,
                        upload=upload,
                        business_profile=upload.business_profile,
                        column_index=idx,
                        column_name=col_str[:255],
                        column_normalized=normalize_column_name(col_str)[:255],
                    )
                )
            if column_objects:
                KnowledgeTableColumn.objects.bulk_create(column_objects, ignore_conflicts=True)
            
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
        chrome_stats = self._build_pdf_page_chrome_stats(pages)

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
                if block_meta.get("canonical_consumed_by_table"):
                    # Canonical table reconstruction already absorbed this block into a table cell.
                    continue
                if block_meta.get("is_decorative") or block_meta.get("region_role") == "decorative":
                    continue
                text = self._sanitize_text(block.text).strip()
                chrome_trimmed, chrome_meta = self._suppress_pdf_page_chrome(
                    text,
                    bbox=(block.bbox or {}),
                    page_height=(page.height or None),
                    page_region=block_meta.get("page_region"),
                    chrome_stats=chrome_stats,
                )
                if chrome_trimmed != text:
                    text = chrome_trimmed
                if chrome_meta:
                    block_meta = dict(block_meta)
                    block_meta["page_chrome_trimmed"] = True
                    block_meta["page_chrome_position"] = chrome_meta.get("position")
                    block_meta["page_chrome_trimmed_tokens"] = int(chrome_meta.get("trimmed_tokens") or 0)
                if not text:
                    continue
                anchor = block_meta.get("anchor") or f"p{page.page_number}-b{block.order_index}"
                heading = self._sanitize_text(block.section_heading).strip() if block.section_heading else ""
                is_table_residual = bool(
                    block_meta.get("table_residual_candidate")
                    or block_meta.get("table_residual")
                    or block_meta.get("content_source") == "table_residual"
                    or block_meta.get("region_role") == "table_residual"
                    or block_meta.get("table_overlap_candidate")
                    or block_meta.get("suppress_text_chunk")
                    or block_meta.get("suppression_reason") == "table_overlap"
                )
                overlap_ratio = 0.0
                try:
                    overlap_ratio = float(block_meta.get("table_overlap_ratio") or 0.0)
                except (TypeError, ValueError):
                    overlap_ratio = 0.0
                residual_reason = str(
                    block_meta.get("table_residual_reason")
                    or block_meta.get("table_overlap_candidate_reason")
                    or block_meta.get("suppression_reason")
                    or ""
                ).strip()
                residual_region_key = str(block_meta.get("table_region_key") or "").strip()
                block_units.append(
                    {
                        "text": text,
                        "page_number": page.page_number,
                        "anchor": anchor,
                        "section_heading": heading,
                        "segment_role": "table_residual" if is_table_residual else "text",
                        "table_overlap_ratio": max(0.0, min(1.0, overlap_ratio)),
                        "table_residual_reason": residual_reason,
                        "table_residual_region_key": residual_region_key,
                    }
                )
            if not block_units:
                continue

            current_blocks: list[dict[str, Any]] = []
            current_len = 0
            current_role = "text"

            def _metadata_for_blocks(blocks: Sequence[dict[str, Any]], *, role: str) -> dict[str, Any]:
                anchors = _dedupe([entry.get("anchor") for entry in blocks if entry.get("anchor")], anchor_limit)
                headings = _dedupe(
                    [entry.get("section_heading") for entry in blocks if entry.get("section_heading")],
                    heading_limit,
                )
                metadata: dict[str, Any] = {
                    "strategy": "page_blocks",
                    "index_type": "text",
                    "page_numbers": [page.page_number],
                    "page_anchor": f"p{page.page_number}",
                }
                if role == "table_residual":
                    overlap_values = [
                        float(entry.get("table_overlap_ratio") or 0.0)
                        for entry in blocks
                        if isinstance(entry.get("table_overlap_ratio"), (int, float))
                    ]
                    metadata.update(
                        {
                            "content_source": "table_residual",
                            "region_role": "table_residual",
                            "table_residual": True,
                            "search_tier": "fallback",
                        }
                    )
                    if overlap_values:
                        metadata["table_overlap_ratio_max"] = round(max(overlap_values), 4)
                    residual_reasons = _dedupe(
                        [entry.get("table_residual_reason") for entry in blocks if entry.get("table_residual_reason")],
                        4,
                    )
                    if residual_reasons:
                        metadata["table_residual_reasons"] = residual_reasons
                    region_keys = _dedupe(
                        [
                            entry.get("table_residual_region_key")
                            for entry in blocks
                            if entry.get("table_residual_region_key")
                        ],
                        8,
                    )
                    if region_keys:
                        metadata["table_residual_region_keys"] = region_keys
                        metadata["table_residual_region_key"] = region_keys[0]
                else:
                    metadata.update(
                        {
                            "content_source": "page_blocks",
                            "region_role": "text",
                        }
                    )
                if anchors:
                    metadata["block_anchors"] = anchors
                if headings:
                    metadata["section_headings"] = headings
                return metadata

            def emit(blocks: Sequence[dict[str, Any]], *, role: str) -> None:
                if not blocks:
                    return
                text = "\n\n".join(entry["text"] for entry in blocks).strip()
                if not text:
                    return
                text, aliases = self._inject_identifiers_into_text(text, alias_hygiene=alias_hygiene)
                metadata = _metadata_for_blocks(blocks, role=role)
                if aliases:
                    metadata.update(self._alias_metadata(aliases))
                segments.append({"text": text, "metadata": metadata})

            for unit in block_units:
                unit_role = str(unit.get("segment_role") or "text")
                if unit_role == "table_residual":
                    if current_blocks:
                        emit(current_blocks, role=current_role)
                        current_blocks = []
                        current_len = 0
                    current_role = unit_role
                    block_text = str(unit.get("text") or "")
                    residual_pieces = self._chunk_text(block_text, chunk_chars=chunk_chars, overlap=0)
                    if not residual_pieces:
                        residual_pieces = [block_text]
                    for piece in residual_pieces:
                        normalized_piece = str(piece or "").strip()
                        if not normalized_piece:
                            continue
                        piece_unit = dict(unit)
                        piece_unit["text"] = normalized_piece
                        rendered, aliases = self._inject_identifiers_into_text(
                            normalized_piece,
                            alias_hygiene=alias_hygiene,
                        )
                        metadata = _metadata_for_blocks([piece_unit], role=unit_role)
                        metadata["table_residual_granularity"] = "block"
                        if aliases:
                            metadata.update(self._alias_metadata(aliases))
                        segments.append({"text": rendered, "metadata": metadata})
                    continue
                if current_blocks and unit_role != current_role:
                    emit(current_blocks, role=current_role)
                    current_blocks = []
                    current_len = 0
                    current_role = unit_role

                block_text = unit["text"]
                if len(block_text) >= chunk_chars:
                    if current_blocks:
                        emit(current_blocks, role=current_role)
                        current_blocks = []
                        current_len = 0
                    current_role = unit_role
                    for piece in self._chunk_text(block_text, chunk_chars=chunk_chars, overlap=overlap):
                        if not piece:
                            continue
                        piece, aliases = self._inject_identifiers_into_text(piece, alias_hygiene=alias_hygiene)
                        metadata = _metadata_for_blocks([unit], role=unit_role)
                        if aliases:
                            metadata.update(self._alias_metadata(aliases))
                        segments.append({"text": piece, "metadata": metadata})
                    continue

                additional = len(block_text) + (2 if current_blocks else 0)
                if current_blocks and current_len + additional > chunk_chars:
                    emit(current_blocks, role=current_role)
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

                if not current_blocks:
                    current_role = unit_role
                current_blocks.append(unit)
                current_len += additional

            if current_blocks:
                emit(current_blocks, role=current_role)

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
        1) Suggested key columns from sampling.
        2) Fallback heuristics on column names.
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
                        sample_visible_rows.append(row_preview)

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
        sheet_hidden: bool,
        sheet_role: str,
        file_detail: KnowledgeUploadFile,
        source_label: str,
        content_type: str,
        rules: Mapping[str, Any] | None,
    ) -> tuple[TablePayload, PageLayout]:
        column_schema = normalized.column_schema
        table_rows: list[TableRowPayload] = []
        for row_idx, values in enumerate(normalized.rows, start=1):
            row_meta = (
                normalized.row_metadata[row_idx - 1]
                if row_idx - 1 < len(normalized.row_metadata)
                else None
            )
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
                    metadata={
                        "source": source_label,
                        "sheet": sheet_name,
                        "source_row_index": getattr(row_meta, "source_row_index", None),
                        "hidden": bool(getattr(row_meta, "hidden", False)),
                        "row_kind": getattr(row_meta, "row_kind", "data"),
                    },
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
                "sheet_hidden": bool(sheet_hidden),
                "sheet_role": sheet_role,
                "is_decorative": sheet_role == "reference_hidden",
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
                    metadata={
                        "source": source_label,
                        "sheet": sheet_name,
                        "sheet_role": sheet_role,
                        "is_decorative": sheet_role == "reference_hidden",
                    },
                )
            ],
            metadata={"sheet_name": sheet_name, "sheet_role": sheet_role},
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
            # Spreadsheet-template normalization needs row visibility metadata
            # (e.g. hidden filler rows), which openpyxl does not expose on
            # ReadOnlyWorksheet. Load the workbook normally on the non-dataset
            # XLSX path so normalization can make deterministic keep/drop
            # decisions before indexing.
            workbook = load_workbook(filename=path, read_only=False, data_only=True)
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
        sheet_role_counts: dict[str, int] = {}
        order_index = 1
        for sheet_idx, sheet_name, sheet in allowed_sheets:
            normalized = normalize_sheet_rows(
                self._iter_xlsx_rows_with_metadata(sheet),
                sheet_name=sheet_name,
                policy=policy,
            )
            diagnostics.append(normalized.diagnostics)
            if normalized.diagnostics.skipped:
                continue
            sheet_hidden = getattr(sheet, "sheet_state", "visible") != "visible"
            sheet_role = self._classify_spreadsheet_sheet_role(
                sheet_name=sheet_name,
                sheet_hidden=sheet_hidden,
                normalized=normalized,
            )
            sheet_role_counts[sheet_role] = sheet_role_counts.get(sheet_role, 0) + 1
            table, page_layout = self._build_table_from_normalized_sheet(
                normalized,
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                sheet_hidden=sheet_hidden,
                sheet_role=sheet_role,
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
            "spreadsheet_sheet_roles": sheet_role_counts,
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

    @staticmethod
    def _iter_xlsx_rows_with_metadata(sheet: Any) -> Iterable[SpreadsheetRowInput]:
        for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            row_dimension = None
            try:
                row_dimension = sheet.row_dimensions.get(row_idx)
            except Exception:
                row_dimension = None
            hidden = bool(getattr(row_dimension, "hidden", False))
            yield SpreadsheetRowInput(values=row, row_index=row_idx, hidden=hidden)

    def _classify_spreadsheet_sheet_role(
        self,
        *,
        sheet_name: str,
        sheet_hidden: bool,
        normalized: NormalizedSheet,
    ) -> str:
        name = str(sheet_name or "").strip()
        if sheet_hidden and _SPREADSHEET_REFERENCE_SHEET_RE.search(name):
            return "reference_hidden"
        if _SPREADSHEET_INSTRUCTION_SHEET_RE.search(name):
            return "instructional"
        if _SPREADSHEET_SUMMARY_ROW_RE.search(name):
            return "summary"
        if sheet_hidden:
            return "reference_hidden"

        rows = normalized.rows or []
        if not rows:
            return "unknown"

        record_id_hits = 0
        transactional_record_rows = 0
        narrative_rows = 0
        summary_hits = 0
        form_like_rows = 0
        for row in rows[:100]:
            values = [str(value or "").strip() for value in row if str(value or "").strip()]
            if not values:
                continue
            descriptor = values[0]
            if _SPREADSHEET_RECORD_ID_RE.fullmatch(descriptor):
                record_id_hits += 1
            row_text = " ".join(values)
            if _SPREADSHEET_SUMMARY_ROW_RE.search(row_text):
                summary_hits += 1
            if len(values) <= 2 and sum(len(value.split()) for value in values) >= 6:
                narrative_rows += 1
            if self._spreadsheet_row_looks_transactional_record(values):
                transactional_record_rows += 1
            if self._spreadsheet_row_looks_form_like(values):
                form_like_rows += 1

        if record_id_hits > 0:
            return "transactional"
        if transactional_record_rows >= max(3, min(12, math.ceil(len(rows) * 0.3))) and transactional_record_rows > form_like_rows:
            return "transactional"
        if summary_hits > 0 and summary_hits >= max(narrative_rows, form_like_rows):
            return "summary"
        if narrative_rows >= max(3, len(rows) // 3):
            return "instructional"
        if form_like_rows >= max(2, min(8, math.ceil(len(rows) * 0.25))):
            return "form_like"
        return "unknown"

    def _spreadsheet_row_looks_guidance(self, values: Sequence[str]) -> bool:
        cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
        if not cleaned:
            return False

        joined = " ".join(cleaned)
        word_count = len(joined.split())
        numeric_like_count = sum(
            1
            for value in cleaned
            if _SPREADSHEET_RECORD_ID_RE.fullmatch(value) or _TABLE_NUMBER_LIKE_RE.search(value)
        )
        long_text_cells = sum(1 for value in cleaned if len(value.split()) >= 8)
        has_instruction_token = any(_SPREADSHEET_INSTRUCTION_TOKEN_RE.search(value) for value in cleaned)
        if has_instruction_token and numeric_like_count == 0:
            return True
        return word_count >= 18 and numeric_like_count <= 1 and long_text_cells >= 1 and len(cleaned) <= 3

    def _spreadsheet_row_looks_form_like(self, values: Sequence[str]) -> bool:
        cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
        if len(cleaned) < 2 or len(cleaned) > 6:
            return False
        if any(_SPREADSHEET_RECORD_ID_RE.fullmatch(value) for value in cleaned):
            return False
        if self._spreadsheet_row_looks_guidance(cleaned):
            return False

        text_like_count = 0
        numeric_like_count = 0
        control_like_count = 0
        total_words = 0
        for value in cleaned:
            total_words += len(value.split())
            if self._spreadsheet_value_is_control(value):
                control_like_count += 1
                continue
            if _TABLE_NUMBER_LIKE_RE.search(value):
                numeric_like_count += 1
                continue
            text_like_count += 1

        if control_like_count >= len(cleaned) - 1:
            return False
        if text_like_count >= 1 and numeric_like_count >= 1:
            return True
        if text_like_count >= 2 and total_words <= 24:
            return True
        return False

    def _spreadsheet_row_looks_transactional_record(self, values: Sequence[str]) -> bool:
        cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
        if len(cleaned) < 5:
            return False
        if any(_SPREADSHEET_RECORD_ID_RE.fullmatch(value) for value in cleaned):
            return True
        if self._spreadsheet_row_looks_guidance(cleaned):
            return False

        zero_like_count = sum(1 for value in cleaned if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value))
        control_like_count = sum(1 for value in cleaned if self._spreadsheet_value_is_control(value))
        if control_like_count >= 2:
            return False
        if zero_like_count >= max(2, len(cleaned) // 2):
            return False

        substantive_text_count = 0
        numeric_like_count = 0
        long_text_count = 0
        for value in cleaned:
            if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value) or self._spreadsheet_value_is_control(value):
                continue
            if _TABLE_NUMBER_LIKE_RE.search(value):
                numeric_like_count += 1
                continue
            substantive_text_count += 1
            if len(value.split()) >= 2 or len(value) >= 16:
                long_text_count += 1

        if substantive_text_count < 3:
            return False
        if numeric_like_count >= 1:
            return True
        return long_text_count >= 2 and substantive_text_count >= 4

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
                        sample_visible_rows.append(row_preview)

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
                        sample_visible_rows.append(row_preview)

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
                sample_visible_rows.append(row_preview)
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

        Runtime contract:
        - Default path keeps all rows (up to hard safety cap) and relies on read-time
          pagination/continuation for bounded responses.
        - Explicit tenant/upload `table_limits.max_rows` remains an intentional cap.
        Returns (row_cap, tier, strategy).
        """
        base_cap = max(1, int(config.get("max_rows", 1)))
        small_limit = max(1, int(config.get("small_row_limit", 2000)))
        large_limit = max(small_limit, int(config.get("large_row_limit", 20000)))
        hard_cap = max(1, int(config.get("hard_row_cap", 100000)))
        override = config.get("max_rows_source") == "override"
        if row_count <= 0:
            return base_cap, "unknown", "default"
        tier = "small" if row_count <= small_limit else ("medium" if row_count <= large_limit else "large")
        if row_count > hard_cap:
            return hard_cap, tier, "hard_capped"
        if override:
            effective_cap = min(base_cap, hard_cap)
            strategy = "override_full" if row_count <= effective_cap else "override_capped"
            return effective_cap, tier, strategy
        return row_count, tier, "full"

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

    @staticmethod
    def _row_cells_for_role_inference(row: Any) -> list[Any]:
        cells_attr = getattr(row, "cells", None)
        if cells_attr is None:
            return []
        if hasattr(cells_attr, "all"):
            return list(cells_attr.all())
        if isinstance(cells_attr, (list, tuple)):
            return list(cells_attr)
        return []

    def _infer_table_column_roles(
        self,
        *,
        table_rows: Sequence[Any],
        column_map: Sequence[tuple[str, str, int]],
        cached_roles: Sequence[Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not column_map:
            return []
        if isinstance(cached_roles, (list, tuple)):
            lookup = role_lookup_by_index(cached_roles)
            normalized_cached: list[dict[str, Any]] = []
            for _idx, (label, _canonical, raw_idx) in enumerate(column_map):
                payload = lookup.get(raw_idx)
                if not payload:
                    continue
                normalized_cached.append(
                    {
                        "column_index": int(raw_idx),
                        "column_key": str(payload.get("column_key") or label),
                        "role": str(payload.get("role") or COLUMN_ROLE_SCOPE_DIMENSION),
                        "confidence": round(float(payload.get("confidence") or 0.0), 3),
                        "role_scores": dict(payload.get("role_scores") or {}),
                        "signals": dict(payload.get("signals") or {}),
                    }
                )
            if len(normalized_cached) >= max(1, len(column_map) - 1):
                return normalized_cached

        row_values: list[list[str]] = []
        header_row_positions: set[int] = set()
        schema = [str(label or "").strip() or f"column_{idx + 1}" for idx, (label, _canonical, _raw_idx) in enumerate(column_map)]
        for pos, row in enumerate(table_rows):
            row_meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
            if str(row_meta.get("row_type") or "").strip().lower() == "header":
                header_row_positions.add(pos)
            row_cells = self._row_cells_for_role_inference(row)
            cell_lookup = {}
            for cell in row_cells:
                try:
                    idx = int(getattr(cell, "column_index", 0))
                except (TypeError, ValueError):
                    continue
                cell_lookup[idx] = self._table_cell_text(getattr(cell, "raw_text", ""))
            row_values.append(
                [self._table_cell_text(cell_lookup.get(raw_idx, "")) for _label, _canonical, raw_idx in column_map]
            )

        profiles = infer_column_roles(
            row_values=row_values,
            column_schema=schema,
            header_row_indices=header_row_positions,
            min_scope_columns=3,
        )
        payloads: list[dict[str, Any]] = []
        for idx, payload in enumerate(column_role_payloads(profiles)):
            raw_idx = column_map[idx][2] if idx < len(column_map) else idx
            payload["column_index"] = int(raw_idx)
            payload["column_key"] = str(column_map[idx][0] if idx < len(column_map) else payload.get("column_key") or "")
            payloads.append(payload)
        return payloads

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

    def _table_subsection_label(self, value: Any) -> str:
        """
        Keep subsection labels only when they look like short, title-like context.

        This avoids leaking value-heavy or concatenated row content into every
        subsequent row chunk via `[SubSection] ...`.
        """
        text = self._table_cell_text(value)
        if not text:
            return ""
        word_count = len(re.findall(r"[A-Za-z0-9\u0600-\u06FF]+", text))
        numeric_like_count = len(
            [
                token
                for token in re.split(r"\s+", text)
                if token and _column_numeric_signal(token)
            ]
        )
        if len(text) > 80:
            return ""
        if word_count > 10:
            return ""
        if numeric_like_count >= 2:
            return ""
        return text

    def _clean_scope_labels(self, values: Any) -> list[str]:
        if not isinstance(values, (list, tuple)):
            return []
        cleaned: list[str] = []
        for entry in values:
            label = self._table_cell_text(entry)
            if label:
                cleaned.append(label)
        return list(dict.fromkeys(cleaned))

    @staticmethod
    def _coerce_scope_confidence(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return round(float(value), 3)
        if isinstance(value, str):
            try:
                return round(float(value), 3)
            except (TypeError, ValueError):
                return None
        return None

    def _resolve_row_scope_contract(
        self,
        *,
        row_model_meta: Mapping[str, Any],
        value_by_label: Mapping[str, str],
        contextual_labels: set[str],
        inferred_segment_labels: Sequence[str],
    ) -> dict[str, Any]:
        observed_value_columns = self._clean_scope_labels(row_model_meta.get("observed_value_columns"))
        if not observed_value_columns:
            observed_value_columns = list(dict.fromkeys(value_by_label.keys()))

        qualifier_columns = self._clean_scope_labels(row_model_meta.get("qualifier_columns"))
        if not qualifier_columns:
            qualifier_columns = [label for label in observed_value_columns if label in contextual_labels]
        qualifier_columns = list(dict.fromkeys(qualifier_columns))

        scope_dimension_columns = self._clean_scope_labels(row_model_meta.get("scope_dimension_columns"))
        if not scope_dimension_columns:
            scope_dimension_columns = list(dict.fromkeys(inferred_segment_labels))
        scope_dimension_columns = list(dict.fromkeys(scope_dimension_columns))

        inferred_scope_columns = self._clean_scope_labels(row_model_meta.get("inferred_scope_columns"))
        if scope_dimension_columns:
            scope_dimension_set = set(scope_dimension_columns)
            inferred_scope_columns = [
                label for label in inferred_scope_columns if label in scope_dimension_set
            ]
        inferred_scope_columns = list(dict.fromkeys(inferred_scope_columns))

        scope_confidence = self._coerce_scope_confidence(row_model_meta.get("scope_confidence"))

        raw_scope_reason = self._table_cell_text(row_model_meta.get("scope_reason"))
        scope_reason = canonical_scope_reason(raw_scope_reason)

        return {
            "contract_version": str(row_model_meta.get("table_scope_contract_version") or TABLE_SCOPE_CONTRACT_VERSION),
            "observed_value_columns": observed_value_columns,
            "qualifier_columns": qualifier_columns,
            "scope_dimension_columns": scope_dimension_columns,
            "inferred_scope_columns": inferred_scope_columns,
            "scope_confidence": scope_confidence,
            "scope_reason": scope_reason,
        }

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

    def _table_column_map_for_payload(
        self,
        table: TablePayload,
    ) -> list[tuple[str, str, int]]:
        schema = [
            self._table_cell_text(value) or f"column_{idx + 1}"
            for idx, value in enumerate(table.column_schema or [])
        ]
        width = len(schema)
        for row in table.rows or []:
            for cell in self._row_cells_for_role_inference(row):
                try:
                    col_idx = int(getattr(cell, "column_index", -1))
                except (TypeError, ValueError):
                    continue
                width = max(width, col_idx + 1)

        column_map: list[tuple[str, str, int]] = []
        for idx in range(max(0, width)):
            label = schema[idx] if idx < len(schema) else f"column_{idx + 1}"
            canonical = self._canonical_column_name(label, f"column_{idx + 1}")
            column_map.append((label, canonical, idx))
        return column_map

    def _enrich_table_payload_column_roles(self, table: TablePayload) -> TablePayload:
        column_map = self._table_column_map_for_payload(table)
        if not column_map:
            return table

        data_dictionary = dict(table.data_dictionary or {})
        cached_roles = data_dictionary.get("column_roles")
        inferred_roles = self._infer_table_column_roles(
            table_rows=list(table.rows or []),
            column_map=column_map,
            cached_roles=cached_roles if isinstance(cached_roles, (list, tuple)) else None,
        )
        if not inferred_roles:
            return table

        role_lookup = role_lookup_by_index(inferred_roles)
        columns_by_role: dict[str, list[str]] = {
            COLUMN_ROLE_DESCRIPTOR: [],
            COLUMN_ROLE_QUALIFIER: [],
            COLUMN_ROLE_SCOPE_DIMENSION: [],
            COLUMN_ROLE_NOTE: [],
        }
        confidence_values: list[float] = []
        for label, _canonical, raw_idx in column_map:
            payload = role_lookup.get(raw_idx) or {}
            role = str(payload.get("role") or COLUMN_ROLE_SCOPE_DIMENSION).strip() or COLUMN_ROLE_SCOPE_DIMENSION
            if role in columns_by_role:
                columns_by_role[role].append(label)
            confidence = self._coerce_scope_confidence(payload.get("confidence"))
            if confidence is not None:
                confidence_values.append(confidence)
        columns_by_role = {
            role: list(dict.fromkeys(labels))
            for role, labels in columns_by_role.items()
        }

        role_summary = {
            "descriptor_count": len(columns_by_role.get(COLUMN_ROLE_DESCRIPTOR, [])),
            "qualifier_count": len(columns_by_role.get(COLUMN_ROLE_QUALIFIER, [])),
            "scope_dimension_count": len(columns_by_role.get(COLUMN_ROLE_SCOPE_DIMENSION, [])),
            "note_count": len(columns_by_role.get(COLUMN_ROLE_NOTE, [])),
            "columns_profiled": len(inferred_roles),
        }
        if confidence_values:
            role_summary["average_confidence"] = round(
                sum(confidence_values) / float(len(confidence_values)),
                3,
            )

        data_dictionary.update(
            {
                "column_role_inference_version": COLUMN_ROLE_INFERENCE_VERSION,
                "column_roles": inferred_roles,
                "column_roles_by_type": columns_by_role,
                "column_role_summary": role_summary,
            }
        )
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=table.column_schema,
            data_dictionary=data_dictionary,
            metadata=table.metadata,
            rows=table.rows,
        )

    def _enrich_extraction_with_column_roles(self, extraction: ExtractionResult) -> ExtractionResult:
        if not extraction.tables:
            return extraction

        enriched_tables: list[TablePayload] = []
        tables_with_roles = 0
        profiled_columns = 0
        for table in extraction.tables:
            enriched = self._enrich_table_payload_column_roles(table)
            enriched_tables.append(enriched)
            data_dictionary = enriched.data_dictionary if isinstance(enriched.data_dictionary, Mapping) else {}
            roles = data_dictionary.get("column_roles")
            if isinstance(roles, (list, tuple)) and roles:
                tables_with_roles += 1
                profiled_columns += len(roles)

        metadata = dict(extraction.metadata or {})
        metadata["column_role_inference"] = {
            "version": COLUMN_ROLE_INFERENCE_VERSION,
            "table_count": len(enriched_tables),
            "tables_with_roles": tables_with_roles,
            "columns_profiled": profiled_columns,
        }
        return ExtractionResult(
            text=extraction.text,
            format_hint=extraction.format_hint,
            metadata=metadata,
            pages=extraction.pages,
            tables=enriched_tables,
            issues=extraction.issues,
            entities=extraction.entities,
            text_html=extraction.text_html,
        )

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
        suppressed_low_signal_rows = 0
        shard_size = max(1, int(max_rows or 1))
        table_rows = list(table.rows.all())
        table_data_dictionary = (
            getattr(table, "data_dictionary", {})
            if isinstance(getattr(table, "data_dictionary", {}), Mapping)
            else {}
        )
        cached_column_roles = table_data_dictionary.get("column_roles") if isinstance(table_data_dictionary, Mapping) else None
        inferred_column_roles = self._infer_table_column_roles(
            table_rows=table_rows,
            column_map=column_map,
            cached_roles=cached_column_roles if isinstance(cached_column_roles, (list, tuple)) else None,
        )
        role_lookup = role_lookup_by_index(inferred_column_roles)
        descriptor_labels: list[str] = []
        qualifier_labels: list[str] = []
        inferred_segment_labels: list[str] = []
        for label, _canonical, raw_idx in column_map:
            role = str((role_lookup.get(raw_idx) or {}).get("role") or "").strip()
            if role == COLUMN_ROLE_SCOPE_DIMENSION:
                inferred_segment_labels.append(label)
                continue
            if role == COLUMN_ROLE_QUALIFIER:
                qualifier_labels.append(label)
                continue
            if role == COLUMN_ROLE_DESCRIPTOR:
                descriptor_labels.append(label)
                continue
            if role == COLUMN_ROLE_NOTE:
                continue

        contextual_labels = set(dict.fromkeys(descriptor_labels + qualifier_labels))
        if not inferred_segment_labels:
            contextual_index_set = {
                idx
                for idx, (label, _canonical, _raw_idx) in enumerate(column_map)
                if label in contextual_labels
            }
            inferred_segment_labels = [
                label
                for idx, (label, _canonical, _raw_idx) in enumerate(column_map)
                if idx not in contextual_index_set
            ]
        active_subsection: str = ""
        for row in table_rows:
            if (row.metadata or {}).get("row_type") == "header":
                continue

            # ── Section header rows: track label, skip as data ──
            row_model_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            if row_model_meta.get("row_type") == "section_header":
                row_cells = list(row.cells.all())
                cell_lookup = {cell.column_index: cell.raw_text for cell in row_cells}
                label = ""
                for _, _, idx in column_map:
                    label = self._table_cell_text(cell_lookup.get(idx, ""))
                    if label:
                        break
                active_subsection = self._table_subsection_label(label)
                continue

            row_attributes = self._row_model_attributes(row, raw_schema)
            if self._row_is_internal(row_attributes, privacy_rules):
                continue
            row_cells = list(row.cells.all())
            cell_lookup = {cell.column_index: cell.raw_text for cell in row_cells}
            pairs: list[str] = []
            value_by_label: dict[str, str] = {}
            # Extract row_label from first column (typically the row identifier/name)
            row_label = ""
            for label, _, idx in column_map:
                value = self._table_cell_text(cell_lookup.get(idx, ""))
                if value:
                    pairs.append(f"{label}: {value}")
                    value_by_label[label] = value
                    # First column value becomes the row_label for search indexing
                    if not row_label:
                        row_label = value
            if not pairs:
                continue

            # Fallback: detect uniform-value rows not caught by annotation
            unique_values = set(value_by_label.values())
            if len(unique_values) == 1 and len(value_by_label) >= 4:
                active_subsection = self._table_subsection_label(next(iter(unique_values), ""))
                continue
            scope_contract = self._resolve_row_scope_contract(
                row_model_meta=row_model_meta,
                value_by_label=value_by_label,
                contextual_labels=contextual_labels,
                inferred_segment_labels=inferred_segment_labels,
            )
            inferred_scope_columns = list(scope_contract.get("inferred_scope_columns") or [])
            observed_value_columns = list(scope_contract.get("observed_value_columns") or [])
            qualifier_columns = list(scope_contract.get("qualifier_columns") or [])
            scope_dimension_columns = list(scope_contract.get("scope_dimension_columns") or [])
            scope_reason = canonical_scope_reason(scope_contract.get("scope_reason"))
            scope_confidence = self._coerce_scope_confidence(scope_contract.get("scope_confidence"))

            fee_value = self._table_cell_text(str(row_model_meta.get("scope_value") or ""))
            if not fee_value:
                scoped_values = [
                    value_by_label.get(label, "")
                    for label in inferred_scope_columns
                    if value_by_label.get(label, "")
                ]
                unique_values = list(dict.fromkeys(scoped_values))
                if len(unique_values) == 1:
                    fee_value = unique_values[0]

            row_signal_score, row_signal_diag = self._table_row_signal_score(
                value_by_label=value_by_label,
                inferred_scope_columns=inferred_scope_columns,
                observed_value_columns=observed_value_columns,
                fee_value=fee_value,
            )
            structural_row_diag = self._table_row_structural_context_profile(
                value_by_label=value_by_label,
                scope_dimension_columns=(
                    scope_dimension_columns
                    or row_model_meta.get("scope_dimension_columns")
                    or inferred_scope_columns
                ),
                fee_value=fee_value,
                row_signal_diag=row_signal_diag,
            )
            if (
                row_signal_diag["pair_count"] < self.table_row_signal_min_pairs
                and row_signal_score < self.table_row_signal_min_score
            ):
                suppressed_low_signal_rows += 1
                continue

            evidence_cell_ids = [
                str(cell.id)
                for cell in row_cells
                if str(cell.raw_text or "").strip()
            ]
            preface = []
            # Keep derived headings out of row-level chunks to avoid broad-term noise.
            section_heading = str(getattr(table, "section_heading", "") or "").strip()
            if section_heading:
                table_meta = getattr(table, "metadata", None) if isinstance(getattr(table, "metadata", None), Mapping) else {}
                derived_heading = str((table_meta or {}).get("derived_section_heading") or "").strip()
                if not derived_heading or derived_heading != section_heading:
                    preface.append(f"[Section] {section_heading}")
            if active_subsection:
                preface.append(f"[SubSection] {active_subsection}")
            preface.append(f"[Table] {title}")
            preface.append(f"[Row] {row.row_index}")
            if inferred_scope_columns:
                preface.append(f"[Scope] {', '.join(inferred_scope_columns)}")
            text = "\n".join(preface + pairs)
            shard_index = data_rows // shard_size
            shard_offset = data_rows % shard_size
            row_meta = dict(base_metadata)
            row_meta.update(
                {
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "is_table_preview": False,
                    "table_row_index": row.row_index,
                    "row_label": row_label,  # Enable row-label search matching
                    "search_tier": "drill_down",
                    "table_row_contract_version": scope_contract.get("contract_version") or TABLE_SCOPE_CONTRACT_VERSION,
                    "table_row_observed_value_columns": observed_value_columns,
                    "table_row_qualifier_columns": qualifier_columns,
                    "table_row_scope_dimension_columns": scope_dimension_columns,
                    "table_row_inferred_scope_columns": inferred_scope_columns,
                    "table_row_scope_reason": scope_reason or SCOPE_REASON_ABSTAIN,
                    "table_row_scope_confidence": scope_confidence,
                    "table_row_fee_value": fee_value,
                    "table_row_evidence_cell_ids": evidence_cell_ids,
                    "table_row_shard_index": int(shard_index),
                    "table_row_shard_size": int(shard_size),
                    "table_row_shard_offset": int(shard_offset),
                    "table_row_signal_filter_enabled": True,
                    "table_row_signal_min_pairs": int(self.table_row_signal_min_pairs),
                    "table_row_signal_min_score": float(self.table_row_signal_min_score),
                    "table_row_signal_pair_count": int(row_signal_diag["pair_count"]),
                    "table_row_signal_numeric_value_count": int(row_signal_diag["numeric_value_count"]),
                    "table_row_signal_value_keyword_count": int(row_signal_diag["keyword_value_count"]),
                    "table_row_signal_scope_column_count": int(row_signal_diag["scope_column_count"]),
                    "table_row_signal_observed_value_column_count": int(
                        row_signal_diag["observed_value_column_count"]
                    ),
                    "table_row_signal_has_fee_value": bool(row_signal_diag["has_fee_value"]),
                    "table_row_signal_score": float(row_signal_diag["score"]),
                    "table_row_is_structural_context": bool(
                        structural_row_diag["is_structural_context"]
                    ),
                    "table_row_structural_scope_label_count": int(
                        structural_row_diag["scope_label_count"]
                    ),
                    "table_row_structural_scope_echo_count": int(
                        structural_row_diag["scope_echo_count"]
                    ),
                    "table_row_structural_scope_numeric_count": int(
                        structural_row_diag["scope_numeric_count"]
                    ),
                    "table_row_structural_scope_echo_ratio": float(
                        structural_row_diag["scope_echo_ratio"]
                    ),
                }
            )
            payloads.append({"text": text, "metadata": row_meta})
            data_rows += 1
        if suppressed_low_signal_rows > 0:
            table_id = getattr(table, "id", None)
            page_number = None
            page = getattr(table, "page", None)
            if page is not None:
                page_number = getattr(page, "page_number", None)
            logger.info(
                "table.row_signal_filter table_id=%s page=%s kept=%s suppressed=%s min_pairs=%s min_score=%.2f",
                table_id,
                page_number,
                len(payloads),
                suppressed_low_signal_rows,
                self.table_row_signal_min_pairs,
                self.table_row_signal_min_score,
            )
        return payloads

    def _table_summary_chunk_payloads(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        base_metadata: Mapping[str, Any],
        total_data_rows: int,
        row_label_entries: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build one primary-search summary chunk per row shard."""
        column_names = [entry[0] for entry in column_map]
        if not column_names:
            return []

        title = table.title or f"Table {table.order_index}"
        grouped_labels: dict[int, list[str]] = {}
        grouped_row_indices: dict[int, list[int]] = {}
        for entry in row_label_entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                shard_index = int(entry.get("shard_index") or 0)
            except (TypeError, ValueError):
                shard_index = 0
            label = self._sanitize_text(entry.get("label") or "").strip()
            if label:
                grouped_labels.setdefault(shard_index, []).append(label)
            raw_row_index = entry.get("row_index")
            try:
                row_index = int(raw_row_index)
            except (TypeError, ValueError):
                row_index = None  # type: ignore[assignment]
            if row_index is not None:
                grouped_row_indices.setdefault(shard_index, []).append(row_index)

        if not grouped_labels and not grouped_row_indices:
            grouped_labels[0] = []

        shard_ids = sorted(set(grouped_labels.keys()) | set(grouped_row_indices.keys()))
        total_shards = max(1, len(shard_ids))
        payloads: list[dict[str, Any]] = []
        for position, shard_index in enumerate(shard_ids, start=1):
            lines: list[str] = []
            if table.section_heading:
                lines.append(f"[Section] {table.section_heading}")
            lines.append(f"[Table] {title}")
            lines.append(f"[Columns] {' | '.join(column_names)}")
            lines.append(f"[Rows] {total_data_rows} data rows")
            lines.append(f"[Shard] {position}/{total_shards}")

            row_indices = grouped_row_indices.get(shard_index) or []
            if row_indices:
                lines.append(f"[Row Range] {min(row_indices)} - {max(row_indices)}")
                lines.append(f"[Rows In Shard] {len(row_indices)}")

            shard_labels = grouped_labels.get(shard_index) or []
            cap = self.table_summary_max_row_labels
            if shard_labels:
                sample = shard_labels[:cap]
                label_text = ", ".join(sample)
                if len(shard_labels) > cap:
                    label_text += f", ... and {len(shard_labels) - cap} more"
                lines.append(f"[Row Labels] {label_text}")

            summary_meta = dict(base_metadata)
            summary_meta.update(
                {
                    "content_source": "table_summary",
                    "table_chunk_role": "summary",
                    "is_table_preview": True,
                    "search_tier": "primary",
                    "table_total_rows": total_data_rows,
                    "table_row_shard_index": int(shard_index),
                    "table_shard_position": int(position),
                    "table_shard_count": int(total_shards),
                }
            )
            if row_indices:
                summary_meta["table_row_shard_start_row"] = int(min(row_indices))
                summary_meta["table_row_shard_end_row"] = int(max(row_indices))
                summary_meta["table_row_shard_row_count"] = int(len(row_indices))

            payloads.append({"text": "\n".join(lines), "metadata": summary_meta})
        return payloads

    def _table_summary_chunk_payload(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        base_metadata: Mapping[str, Any],
        total_data_rows: int,
        row_labels: Sequence[str],
    ) -> dict[str, Any] | None:
        """Backward-compatible wrapper for a single summary chunk.

        New ingestion paths should use `_table_summary_chunk_payloads` for
        row-sharded summaries.
        """
        entries = [{"label": label, "row_index": idx, "shard_index": 0} for idx, label in enumerate(row_labels)]
        payloads = self._table_summary_chunk_payloads(
            table=table,
            column_map=column_map,
            base_metadata=base_metadata,
            total_data_rows=total_data_rows,
            row_label_entries=entries,
        )
        return payloads[0] if payloads else None

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
            sheet_role = str((table.metadata or {}).get("sheet_role") or "")
            if sheet_role == "reference_hidden":
                continue
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
            effective_row_limit = row_limit
            if sheet_role == "instructional":
                effective_row_limit = min(row_limit, 3)
            elif sheet_role == "summary":
                effective_row_limit = min(row_limit, 4)
            elif sheet_role == "form_like":
                effective_row_limit = min(row_limit, 4)
            for row in table.rows[:effective_row_limit]:
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
        spreadsheet_stats = {
            "kept": 0,
            "skipped": 0,
            "hidden_sheet": 0,
            "instruction_sheet": 0,
            "low_value": 0,
            "header_row": 0,
        }
        if upload and getattr(upload, "business_profile", None):
            alias_hygiene = FeatureFlagService.snapshot(upload.business_profile).rag_alias_hygiene
        rules = self._table_privacy_rules(upload)
        for table_idx, table in enumerate(tables):
            entity_type = self._derive_table_entity_type(table, table_idx)
            column_schema = table.column_schema or []
            spreadsheet_context = self._build_spreadsheet_entity_context(table, column_schema)
            for row in table.rows:
                entity_index = len(entities)
                attributes = self._row_attributes_from_table(row, column_schema)
                if self._row_is_internal(attributes, rules):
                    continue
                should_create, skip_reason = self._should_create_table_row_entity(
                    table=table,
                    row=row,
                    attributes=attributes,
                    spreadsheet_context=spreadsheet_context,
                )
                if not should_create:
                    if skip_reason:
                        spreadsheet_stats["skipped"] += 1
                        spreadsheet_stats[skip_reason] = spreadsheet_stats.get(skip_reason, 0) + 1
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
                    "sheet_role": (table.metadata or {}).get("sheet_role"),
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
                if self._table_uses_spreadsheet_entity_gate(table):
                    spreadsheet_stats["kept"] += 1
        if self._table_entity_stats_active(spreadsheet_stats):
            logger.info(
                "ingest.spreadsheet_entities kept=%s skipped=%s hidden_sheet=%s instruction_sheet=%s header_row=%s low_value=%s",
                spreadsheet_stats["kept"],
                spreadsheet_stats["skipped"],
                spreadsheet_stats["hidden_sheet"],
                spreadsheet_stats["instruction_sheet"],
                spreadsheet_stats["header_row"],
                spreadsheet_stats["low_value"],
            )
        return entities

    @staticmethod
    def _table_entity_stats_active(stats: Mapping[str, int]) -> bool:
        return any(int(stats.get(key, 0) or 0) for key in ("kept", "skipped"))

    @staticmethod
    def _table_uses_spreadsheet_entity_gate(table: TablePayload) -> bool:
        source = str((table.metadata or {}).get("source") or "").lower()
        return source == "xlsx"

    def _should_create_table_row_entity(
        self,
        *,
        table: TablePayload,
        row: TableRowPayload,
        attributes: Mapping[str, str],
        spreadsheet_context: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
        row_kind = str(row_meta.get("row_kind") or "").strip().lower()
        row_type = str(row_meta.get("row_type") or "").strip().lower()
        if row_kind == "header" or row_type in {"header", "section_header"}:
            return False, "header_row"
        if not self._table_uses_spreadsheet_entity_gate(table):
            return True, None
        return self._should_create_spreadsheet_row_entity(
            table=table,
            row=row,
            attributes=attributes,
            spreadsheet_context=spreadsheet_context,
        )

    @staticmethod
    def _normalize_spreadsheet_signal_value(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    @staticmethod
    def _spreadsheet_value_is_control(value: str) -> bool:
        sample = str(value or "").strip()
        return bool(
            _SPREADSHEET_PLACEHOLDER_CELL_RE.match(sample)
            or _SPREADSHEET_CONTROL_CELL_RE.fullmatch(sample)
            or _SPREADSHEET_MASKED_PLACEHOLDER_RE.fullmatch(sample)
        )

    @staticmethod
    def _spreadsheet_value_is_non_default_numeric(value: str) -> bool:
        sample = str(value or "").strip()
        return bool(_SPREADSHEET_PURE_NUMBER_RE.fullmatch(sample) and not _SPREADSHEET_ZERO_LIKE_RE.fullmatch(sample))

    def _build_spreadsheet_entity_context(
        self,
        table: TablePayload,
        column_schema: Sequence[str],
    ) -> Mapping[str, Any] | None:
        if not self._table_uses_spreadsheet_entity_gate(table):
            return None
        repeated_text_counts: Counter[str] = Counter()
        rows = table.rows or []
        for row in rows:
            attributes = self._row_attributes_from_table(row, column_schema)
            seen_text_values: set[str] = set()
            for raw_value in attributes.values():
                value = str(raw_value or "").strip()
                if not value:
                    continue
                if self._spreadsheet_value_is_non_default_numeric(value):
                    continue
                if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value):
                    continue
                if self._spreadsheet_value_is_control(value):
                    continue
                if _SPREADSHEET_RECORD_ID_RE.fullmatch(value):
                    continue
                seen_text_values.add(self._normalize_spreadsheet_signal_value(value))
            repeated_text_counts.update(seen_text_values)
        row_count = max(len(rows), 1)
        boilerplate_threshold = max(3, math.ceil(row_count * 0.2))
        boilerplate_texts = {
            value for value, count in repeated_text_counts.items() if count >= boilerplate_threshold
        }
        return {
            "boilerplate_texts": boilerplate_texts,
            "row_count": row_count,
            "boilerplate_threshold": boilerplate_threshold,
        }

    def _should_create_spreadsheet_row_entity(
        self,
        *,
        table: TablePayload,
        row: TableRowPayload,
        attributes: Mapping[str, str],
        spreadsheet_context: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        table_meta = table.metadata or {}
        row_meta = row.metadata or {}
        sheet_role = str(table_meta.get("sheet_role") or "")
        if sheet_role == "reference_hidden" or bool(table_meta.get("sheet_hidden")):
            return False, "hidden_sheet"
        if sheet_role == "instructional":
            return False, "instruction_sheet"

        values = [str(value or "").strip() for value in attributes.values() if str(value or "").strip()]
        if not values:
            return False, "low_value"

        descriptor = values[0]
        zero_like_count = sum(1 for value in values if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value))
        placeholder_count = sum(1 for value in values if _SPREADSHEET_PLACEHOLDER_CELL_RE.match(value))
        control_count = sum(1 for value in values if self._spreadsheet_value_is_control(value))
        identifier_like = bool(values and _SPREADSHEET_RECORD_ID_RE.fullmatch(values[0]))
        summary_like = any(_SPREADSHEET_SUMMARY_ROW_RE.search(value) for value in values)
        guidance_like = self._spreadsheet_row_looks_guidance(values)
        meaningful_values = [
            value
            for value in values
            if not _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value)
            and not self._spreadsheet_value_is_control(value)
        ]
        boilerplate_texts = set((spreadsheet_context or {}).get("boilerplate_texts") or [])
        substantive_text_values: list[str] = []
        substantive_text_norms: set[str] = set()
        substantive_long_text_count = 0
        substantive_numeric_count = 0
        for value in meaningful_values:
            if self._spreadsheet_value_is_non_default_numeric(value):
                substantive_numeric_count += 1
                continue
            normalized = self._normalize_spreadsheet_signal_value(value)
            if normalized in boilerplate_texts:
                continue
            if normalized in substantive_text_norms:
                continue
            substantive_text_norms.add(normalized)
            substantive_text_values.append(value)
            if len(value) >= 24 or len(value.split()) >= 4:
                substantive_long_text_count += 1
        if guidance_like:
            return False, "low_value"
        if identifier_like or summary_like:
            return True, None
        if sheet_role == "summary":
            if substantive_numeric_count >= 1 or len(substantive_text_values) >= 2:
                return True, None
            return False, "low_value"
        if not identifier_like and placeholder_count >= 1 and zero_like_count >= 2 and len(substantive_text_values) <= 1 and substantive_numeric_count == 0:
            return False, "low_value"
        if _SPREADSHEET_PLACEHOLDER_CELL_RE.match(descriptor) and zero_like_count >= 2 and placeholder_count >= 1:
            return False, "low_value"
        if placeholder_count >= 1 and zero_like_count >= 3 and len(meaningful_values) <= 3:
            return False, "low_value"
        if len(values) >= 5 and zero_like_count >= max(3, len(values) // 2) and len(meaningful_values) <= 2:
            return False, "low_value"
        if row_meta.get("row_kind") in {"default_zero_row", "placeholder_row", "scaffold_row"}:
            return False, "low_value"
        if _SPREADSHEET_PLACEHOLDER_CELL_RE.match(descriptor) and len(meaningful_values) <= 3:
            return False, "low_value"
        if sheet_role == "transactional" and substantive_numeric_count == 0:
            if substantive_long_text_count >= 1:
                return True, None
            if len(substantive_text_values) < 3:
                return False, "low_value"
        if control_count >= 2 and zero_like_count >= 2 and substantive_numeric_count == 0 and len(substantive_text_values) <= 1:
            return False, "low_value"
        return True, None

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

    _GENERIC_TABLE_TITLE_RE = re.compile(r"^Table\s+\d+$", re.IGNORECASE)

    @staticmethod
    def _derive_table_title(table_payload: TablePayload, upload: KnowledgeUpload) -> str:
        raw_title: Any = table_payload.title
        if isinstance(raw_title, Mapping):
            raw_title = raw_title.get("content") or raw_title.get("text") or ""
        current = str(raw_title or "").strip()
        if current and not KnowledgeIngestionService._GENERIC_TABLE_TITLE_RE.match(current):
            return current

        doc_name = ""
        raw = (upload.display_name or upload.source_name or "").strip()
        if raw:
            doc_name = Path(raw).stem.strip()

        section = (table_payload.section_heading or "").strip()

        if doc_name and section:
            return f"{doc_name} – {section}"
        if doc_name:
            return f"{doc_name} – Table {table_payload.order_index}"
        if section:
            return section

        cols = [str(c).strip() for c in (table_payload.column_schema or []) if str(c).strip()]
        if cols:
            preview = ", ".join(cols[:4])
            if len(cols) > 4:
                preview += ", ..."
            return f"Table {table_payload.order_index} ({preview})"

        return f"Table {table_payload.order_index}"

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
                existing = attributes.get(key, "")
                if existing and existing != value:
                    if self._docx_cell_is_helper_token(existing):
                        attributes[key] = self._docx_combine_cell_texts([existing, value])
                    elif self._docx_cell_is_helper_token(value):
                        attributes[key] = self._docx_combine_cell_texts([existing, value])
                    elif existing in value:
                        attributes[key] = value
                    elif value in existing:
                        attributes[key] = existing
                    else:
                        attributes[key] = value
                else:
                    attributes[key] = value
        for idx, column in enumerate(column_schema):
            normalized = column.strip() if isinstance(column, str) else ""
            if normalized.startswith("helper_"):
                base_key = normalized[len("helper_") :].strip()
                helper_value = attributes.get(normalized, "")
                if base_key and helper_value and self._docx_cell_is_helper_token(helper_value):
                    base_value = attributes.get(base_key, "")
                    if base_value:
                        attributes[base_key] = self._docx_combine_cell_texts([helper_value, base_value])
                        attributes[normalized] = ""
        # include columns with no explicit cell entry to preserve schema ordering
        for idx, column in enumerate(column_schema):
            normalized = column.strip() if isinstance(column, str) else ""
            if not normalized:
                normalized = f"column_{idx + 1}"
            attributes.setdefault(normalized, "")
        return attributes

    @staticmethod
    def _column_values_for_quality_rows(rows: Sequence[Any], col_idx: int) -> list[str]:
        values: list[str] = []
        for row in rows:
            for cell in list(getattr(row, "cells", None) or []):
                if int(getattr(cell, "column_index", -1)) != col_idx:
                    continue
                cell_text = str(getattr(cell, "raw_text", "") or "").strip()
                if cell_text:
                    values.append(cell_text)
        return values

    @classmethod
    def _quality_misalignment_is_legitimate(
        cls,
        *,
        col_idx: int,
        header_column_coverage: Sequence[str],
        data_rows: Sequence[Any],
    ) -> bool:
        values = cls._column_values_for_quality_rows(data_rows, col_idx)
        if not values:
            return False
        labeled_other_columns = sum(
            1 for idx, value in enumerate(header_column_coverage) if idx != col_idx and str(value or "").strip()
        )
        if labeled_other_columns <= 0:
            return False

        text_like = sum(
            1
            for value in values
            if re.search(r"[A-Za-z\u0600-\u06FF]", value)
        )
        numeric_like = sum(1 for value in values if _column_numeric_signal(value) or cls._docx_cell_is_helper_token(value))

        if col_idx == 0 and text_like >= max(2, int(math.ceil(len(values) * 0.6))):
            return True
        if len(header_column_coverage) <= 2 and numeric_like >= max(2, int(math.ceil(len(values) * 0.6))):
            return True
        return False

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
    def _clamp_model_field_text(model_cls: Any, field_name: str, value: Any) -> str:
        max_length = getattr(model_cls._meta.get_field(field_name), "max_length", 0) or 0
        return KnowledgeIngestionService._clamp_text(value, max_length)

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
