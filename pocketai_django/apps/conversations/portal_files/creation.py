from __future__ import annotations

import uuid

from apps.conversations.models import (
    Conversation,
    ConversationFile,
    ConversationFileKind,
    ConversationFileStatus,
    ConversationSender,
)
from apps.conversations.portal_files.errors import PortalFileError
from apps.conversations.portal_files.ingestion import extract_pdf_text, index_conversation_file_text
from apps.conversations.portal_files.storage import (
    guess_content_type,
    normalize_filename,
    portal_storage_paths,
    write_bytes,
    write_uploaded_bytes,
)


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
