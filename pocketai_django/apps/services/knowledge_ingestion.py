from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import (
    KnowledgeIngestionJob,
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadFile,
    KnowledgeUploadText,
)
from apps.services.documents import DocumentScrapeError, scrape_document_source
from apps.services.embeddings import build_embedding_service, EmbeddingProviderError

logger = logging.getLogger(__name__)

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
class ExtractionResult:
    text: str
    format_hint: str
    metadata: dict[str, Any]


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
        if format_hint == "pdf":
            text = self._extract_pdf(absolute)
        elif format_hint == "docx":
            text = self._extract_docx(absolute)
        elif format_hint == "txt":
            text = self._extract_text_file(absolute)
        else:
            raise UnsupportedFormatError(f"Unsupported file type {format_hint or 'unknown'}.")

        metadata = {
            "format": format_hint,
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
        }
        return ExtractionResult(text=text, format_hint=format_hint or "binary", metadata=metadata)

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
            KnowledgeUploadText.objects.update_or_create(upload=upload, defaults=defaults)
            chunk_count = self._build_chunks(upload, normalized)
            upload.summary = summary
            upload.token_count = words
            upload.chunk_count = chunk_count
            upload.status = KnowledgeStatus.ACTIVE
            upload.last_ingested_at = now
            upload.ingestion_error = ""
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
    def _extract_pdf(path: Path) -> str:
        if PdfReader is None:
            raise KnowledgeIngestionError("PDF ingestion requires the pypdf package.")
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
