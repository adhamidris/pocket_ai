from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Sequence

from django.conf import settings
from django.http import FileResponse
from django.utils import timezone

from apps.conversations.models import (
    Conversation,
    ConversationFile,
    ConversationFileChunk,
    ConversationFileKind,
    ConversationFileStatus,
    ConversationSender,
)
from apps.rag.embeddings import build_embedding_service

try:  # pragma: no cover - optional dependency is controlled by requirements
    from pypdf import PdfReader
except Exception:  # pragma: no cover - defensive
    PdfReader = None  # type: ignore


class PortalFileError(RuntimeError):
    pass


class PortalFileTokenError(ValueError):
    pass


_FILENAME_STRIP = re.compile(r"[^A-Za-z0-9._ -]+")


def _media_root() -> Path:
    root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
    if not root:
        raise PortalFileError("MEDIA_ROOT is not configured.")
    return root.resolve()


def normalize_filename(filename: str) -> str:
    raw = (filename or "").strip()
    base = Path(raw).name if raw else ""
    base = base.replace("\x00", "").strip()
    if not base:
        return "upload.bin"
    cleaned = _FILENAME_STRIP.sub("_", base).strip("._ ")
    if not cleaned:
        cleaned = "upload.bin"
    if len(cleaned) > 180:
        suffix = Path(cleaned).suffix
        stem = Path(cleaned).stem[: max(1, 180 - len(suffix))]
        cleaned = f"{stem}{suffix}"
    return cleaned


def guess_content_type(filename: str, provided: str | None = None) -> str:
    provided_clean = (provided or "").strip()
    if provided_clean:
        return provided_clean
    guessed = mimetypes.guess_type(filename)[0]
    return guessed or "application/octet-stream"


def portal_storage_paths(*, business_id: uuid.UUID, conversation_id: uuid.UUID, file_id: uuid.UUID, filename: str) -> tuple[Path, Path]:
    media_root = _media_root()
    safe_filename = normalize_filename(filename)
    rel_dir = Path("portal_files") / str(business_id) / str(conversation_id) / str(file_id)
    rel_path = rel_dir / safe_filename
    abs_path = (media_root / rel_path).resolve()
    try:
        abs_path.relative_to(media_root)
    except ValueError as exc:
        raise PortalFileError("Invalid portal file path.") from exc
    return abs_path, rel_path


def write_uploaded_bytes(uploaded_file, destination: Path) -> tuple[int, str]:
    """
    Stream an uploaded file to disk while computing sha256.

    Returns: (size_bytes, checksum_sha256)
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    with destination.open("wb") as f:
        for chunk in uploaded_file.chunks():
            if not chunk:
                continue
            size += len(chunk)
            hasher.update(chunk)
            f.write(chunk)
    return size, hasher.hexdigest()


def write_bytes(payload: bytes, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    with destination.open("wb") as f:
        if payload:
            size = len(payload)
            hasher.update(payload)
            f.write(payload)
    return size, hasher.hexdigest()


def open_portal_file_response(*, file: ConversationFile, download: bool = True) -> FileResponse:
    media_root = _media_root()
    storage_path = Path(file.storage_path)
    absolute = (media_root / storage_path).resolve()
    try:
        absolute.relative_to(media_root)
    except ValueError as exc:
        raise PortalFileError("Invalid storage path.") from exc
    if not absolute.exists():
        raise FileNotFoundError(file.storage_path)
    filename = file.filename or absolute.name
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = FileResponse(absolute.open("rb"), as_attachment=download, filename=filename)
    response["Content-Type"] = content_type
    return response


def resolve_portal_file_path(file: ConversationFile) -> Path:
    media_root = _media_root()
    storage_path = Path(file.storage_path)
    absolute = (media_root / storage_path).resolve()
    try:
        absolute.relative_to(media_root)
    except ValueError as exc:
        raise PortalFileError("Invalid storage path.") from exc
    return absolute


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


def create_conversation_upload_from_request(
    *,
    conversation: Conversation,
    uploaded_file,
    kind: str = ConversationFileKind.UPLOAD,
    sender: str = ConversationSender.CUSTOMER,
    max_bytes: int | None = None,
    max_pdf_pages: int | None = None,
) -> tuple[ConversationFile, dict[str, int | bool]]:
    """
    Save an uploaded file and (for PDFs) ingest extracted text for retrieval.

    Returns (ConversationFile, ingest_meta).
    """

    filename = normalize_filename(getattr(uploaded_file, "name", "") or "upload.pdf")
    content_type = guess_content_type(filename, getattr(uploaded_file, "content_type", None))

    size_hint = getattr(uploaded_file, "size", None)
    if max_bytes is not None and isinstance(size_hint, int) and size_hint > int(max_bytes):
        raise PortalFileError(f"File too large ({size_hint} bytes); max allowed is {int(max_bytes)} bytes.")

    file_id = uuid.uuid4()
    abs_path, rel_path = portal_storage_paths(
        business_id=conversation.business_profile_id,
        conversation_id=conversation.id,
        file_id=file_id,
        filename=filename,
    )
    size_bytes, checksum = write_uploaded_bytes(uploaded_file, abs_path)
    if max_bytes is not None and size_bytes > int(max_bytes):
        # Clean up the stored file if the streamed size exceeds the limit.
        try:
            abs_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise PortalFileError(f"File too large ({size_bytes} bytes); max allowed is {int(max_bytes)} bytes.")

    convo_file = ConversationFile.objects.create(
        id=file_id,
        conversation=conversation,
        business_profile=conversation.business_profile,
        kind=kind,
        status=ConversationFileStatus.PROCESSING,
        sender=sender,
        filename=filename,
        content_type=content_type,
        storage_path=str(rel_path),
        size_bytes=size_bytes,
        checksum_sha256=checksum,
        page_count=0,
        metadata={},
    )

    ingest_meta: dict[str, int | bool] = {"chunk_count": 0, "embedded": False}
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        try:
            extracted, page_count = extract_pdf_text(abs_path, max_pages=max_pdf_pages)
        except Exception as exc:
            # Cleanup invalid uploads (do not persist bad artifacts).
            try:
                convo_file.delete()
            finally:
                try:
                    abs_path.unlink(missing_ok=True)
                except Exception:
                    pass
            raise PortalFileError(str(exc)) from exc

        convo_file.page_count = int(page_count or 0)
        if extracted.strip():
            ingest_meta = index_conversation_file_text(
                conversation=conversation,
                file=convo_file,
                extracted_text=extracted,
                page_count=page_count,
            )
            convo_file.status = ConversationFileStatus.READY
            convo_file.metadata = {
                "ingest": {
                    "chunk_count": int(ingest_meta.get("chunk_count") or 0),
                    "embedded": bool(ingest_meta.get("embedded")),
                }
            }
        else:
            convo_file.status = ConversationFileStatus.FAILED
            convo_file.metadata = {"ingest": {"error": "no_text_extracted"}}
        convo_file.save(update_fields=["page_count", "status", "metadata", "updated_at"])
    else:
        convo_file.status = ConversationFileStatus.READY
        convo_file.save(update_fields=["status", "updated_at"])

    return convo_file, ingest_meta


def create_conversation_artifact_from_bytes(
    *,
    conversation: Conversation,
    filename: str,
    content_type: str,
    payload: bytes,
    sender: str = ConversationSender.AI,
    max_pdf_pages: int | None = None,
) -> ConversationFile:
    safe_filename = normalize_filename(filename)
    file_id = uuid.uuid4()
    abs_path, rel_path = portal_storage_paths(
        business_id=conversation.business_profile_id,
        conversation_id=conversation.id,
        file_id=file_id,
        filename=safe_filename,
    )
    size_bytes, checksum = write_bytes(payload, abs_path)
    convo_file = ConversationFile.objects.create(
        id=file_id,
        conversation=conversation,
        business_profile=conversation.business_profile,
        kind=ConversationFileKind.ARTIFACT,
        status=ConversationFileStatus.PROCESSING,
        sender=sender,
        filename=safe_filename,
        content_type=content_type,
        storage_path=str(rel_path),
        size_bytes=size_bytes,
        checksum_sha256=checksum,
        page_count=0,
        metadata={},
    )

    if content_type == "application/pdf" or safe_filename.lower().endswith(".pdf"):
        try:
            extracted, page_count = extract_pdf_text(abs_path, max_pages=max_pdf_pages)
        except Exception:
            convo_file.status = ConversationFileStatus.READY
            convo_file.save(update_fields=["status", "updated_at"])
            return convo_file

        convo_file.page_count = int(page_count or 0)
        if extracted.strip():
            ingest_meta = index_conversation_file_text(
                conversation=conversation,
                file=convo_file,
                extracted_text=extracted,
                page_count=page_count,
            )
            convo_file.status = ConversationFileStatus.READY
            convo_file.metadata = {
                "ingest": {
                    "chunk_count": int(ingest_meta.get("chunk_count") or 0),
                    "embedded": bool(ingest_meta.get("embedded")),
                }
            }
        else:
            convo_file.status = ConversationFileStatus.READY
            convo_file.metadata = {"ingest": {"error": "no_text_extracted"}}
        convo_file.save(update_fields=["page_count", "status", "metadata", "updated_at"])
        return convo_file

    convo_file.status = ConversationFileStatus.READY
    convo_file.save(update_fields=["status", "updated_at"])
    return convo_file


@dataclass(frozen=True, slots=True)
class PortalFileTokenPayload:
    file_id: uuid.UUID
    business_id: uuid.UUID
    exp: int


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def sign_portal_file_token(*, file_id: uuid.UUID, business_id: uuid.UUID, ttl: timedelta) -> str:
    if ttl.total_seconds() <= 0:
        raise PortalFileTokenError("ttl must be positive")
    exp = int((timezone.now() + ttl).timestamp())
    payload = {"file_id": str(file_id), "business_id": str(business_id), "exp": exp}
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def verify_portal_file_token(token: str) -> PortalFileTokenPayload:
    raw = (token or "").strip()
    if not raw or "." not in raw:
        raise PortalFileTokenError("invalid token")
    body_b64, sig_b64 = raw.split(".", 1)
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    expected = hmac.new(secret, body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(sig_b64)
    except Exception as exc:
        raise PortalFileTokenError("invalid token") from exc
    if not hmac.compare_digest(expected, provided):
        raise PortalFileTokenError("invalid token")
    try:
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except Exception as exc:
        raise PortalFileTokenError("invalid token") from exc
    file_id_raw = payload.get("file_id")
    business_id_raw = payload.get("business_id")
    exp_raw = payload.get("exp")
    try:
        file_id = uuid.UUID(str(file_id_raw))
        business_id = uuid.UUID(str(business_id_raw))
        exp = int(exp_raw)
    except Exception as exc:
        raise PortalFileTokenError("invalid token") from exc
    now_ts = int(timezone.now().timestamp())
    if exp <= now_ts:
        raise PortalFileTokenError("expired token")
    return PortalFileTokenPayload(file_id=file_id, business_id=business_id, exp=exp)


def portal_file_block(file: ConversationFile, *, label: str | None = None) -> dict[str, object]:
    """
    Build a stable, renderable content block representing a conversation-scoped file.

    Note: This block intentionally does NOT include a signed download URL so we
    don't persist short-lived tokens in message transcripts.
    """

    from apps.conversations.rich_blocks import new_block_id

    payload: dict[str, object] = {
        "file_id": str(file.id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": int(file.size_bytes or 0),
        "page_count": int(file.page_count or 0),
        "kind": str(file.kind or ""),
        "status": str(file.status or ""),
        "created_at": file.created_at.isoformat() if getattr(file, "created_at", None) else None,
    }
    if label:
        payload["label"] = str(label)
    return {
        "block_id": new_block_id(),
        "type": "file",
        "created_at": timezone.now().isoformat(),
        "payload": payload,
    }


def portal_file_text_block(
    *,
    file_id: uuid.UUID,
    filename: str,
    page_count: int | None,
    text: str,
    title: str | None = None,
    collapsed: bool = True,
) -> dict[str, object]:
    """
    Build a renderable block for extracted text (collapsed/expand UX).
    """

    from apps.conversations.rich_blocks import new_block_id

    payload: dict[str, object] = {
        "file_id": str(file_id),
        "filename": filename,
        "page_count": int(page_count or 0),
        "title": title or "",
        "text": text or "",
        "collapsed": bool(collapsed),
    }
    return {
        "block_id": new_block_id(),
        "type": "file_text",
        "created_at": timezone.now().isoformat(),
        "payload": payload,
    }
