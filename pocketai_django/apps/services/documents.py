from __future__ import annotations

import csv
import io
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from django.db.models import Prefetch, Q

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeCollection,
    KnowledgeUpload,
    KnowledgeUploadFile,
    KnowledgeUploadText,
    KnowledgeUploadUrl,
)


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
        base_qs = base_qs.filter(
            Q(display_name__icontains=search)
            | Q(description__icontains=search)
            | Q(summary__icontains=search)
            | Q(tags__icontains=search)
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
            )
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
    if "html" in (content_type or "").lower():
        text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\\s+", " ", text).strip()
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
