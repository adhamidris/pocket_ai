from __future__ import annotations

from pathlib import Path

from django.conf import settings

from apps.conversations.models import Conversation, ConversationFile, ConversationFileChunk
from apps.conversations.portal_files.errors import PortalFileError
from apps.rag.embeddings import build_embedding_service

try:  # pragma: no cover - optional dependency is controlled by requirements
    from pypdf import PdfReader
except Exception:  # pragma: no cover - defensive
    PdfReader = None  # type: ignore


def extract_pdf_text(path: Path, *, max_pages: int | None = None) -> tuple[str, int]:
    if PdfReader is None:
        raise PortalFileError("PDF parsing is not available (pypdf missing).")
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise PortalFileError(f"Invalid PDF: {exc}") from exc
    page_count = len(getattr(reader, "pages", []) or [])
    if max_pages is not None and page_count > int(max_pages):
        raise PortalFileError(f"PDF has {page_count} pages; max allowed is {int(max_pages)}.")
    fragments: list[str] = []
    for page in reader.pages:
        try:
            fragments.append(page.extract_text() or "")
        except Exception:
            fragments.append("")
    return "\n".join(fragments), page_count


def chunk_text(text: str, *, chunk_chars: int = 1200, overlap: int = 160, max_chunks: int = 200) -> list[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    chunk_chars = max(200, int(chunk_chars))
    overlap = max(0, min(int(overlap), chunk_chars // 2))
    max_chunks = max(1, int(max_chunks))

    chunks: list[str] = []
    start = 0
    while start < len(clean) and len(chunks) < max_chunks:
        end = min(len(clean), start + chunk_chars)
        segment = clean[start:end].strip()
        if segment:
            chunks.append(segment)
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return chunks


def index_conversation_file_text(
    *,
    conversation: Conversation,
    file: ConversationFile,
    extracted_text: str,
    page_count: int,
) -> dict[str, int | bool]:
    """
    Store extracted text as chunks and (optionally) embeddings for retrieval.
    """

    max_chunks_setting = getattr(settings, "PORTAL_FILE_MAX_CHUNKS", 200)
    chunk_chars_setting = getattr(settings, "PORTAL_FILE_CHUNK_CHARS", 1200)
    overlap_setting = getattr(settings, "PORTAL_FILE_CHUNK_OVERLAP_CHARS", 160)
    chunks = chunk_text(
        extracted_text,
        chunk_chars=int(chunk_chars_setting),
        overlap=int(overlap_setting),
        max_chunks=int(max_chunks_setting),
    )
    if not chunks:
        return {"chunk_count": 0, "embedded": False}

    chunk_rows = [
        ConversationFileChunk(
            conversation_file=file,
            conversation=conversation,
            business_profile=conversation.business_profile,
            chunk_index=idx,
            content=content,
            token_count=0,
            metadata={"page_count": int(page_count)} if page_count else {},
        )
        for idx, content in enumerate(chunks)
    ]
    ConversationFileChunk.objects.bulk_create(chunk_rows, batch_size=200)

    embedder = build_embedding_service()
    if not embedder:
        return {"chunk_count": len(chunk_rows), "embedded": False}

    # Batch embeddings to keep memory stable.
    batch_size = 64
    embedded = 0
    for start in range(0, len(chunk_rows), batch_size):
        batch = chunk_rows[start : start + batch_size]
        try:
            vectors = embedder.embed_texts([row.content for row in batch])
        except Exception:
            return {"chunk_count": len(chunk_rows), "embedded": False}
        for row, vec in zip(batch, vectors, strict=False):
            row.embedding = vec or None
        ConversationFileChunk.objects.bulk_update(batch, ["embedding", "updated_at"], batch_size=batch_size)
        embedded += len(batch)

    return {"chunk_count": len(chunk_rows), "embedded": bool(embedded)}
