from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from apps.knowledge.ingestion_contracts import KnowledgeIngestionError, UnsupportedFormatError
from apps.knowledge.models import KnowledgeUploadFile

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


class IngestionFileFormatsMixin:


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


    def _fallback_text_extraction(self, path: Path, format_hint: str) -> str:
        if format_hint == "pdf":
            return self._extract_pdf(path)
        if format_hint == "docx":
            return self._extract_docx(path)
        if format_hint in {"txt", "text", "csv", "tsv"}:
            return self._extract_text_file(path)
        raise UnsupportedFormatError(f"Unsupported file type {format_hint}.")
