from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
