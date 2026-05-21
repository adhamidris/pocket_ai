from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence


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
