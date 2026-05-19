"""
Conversation file search/read and PDF utility tool handlers.
"""

from __future__ import annotations

import uuid
from typing import Mapping

from django.conf import settings

from apps.conversations.models import Conversation, ConversationFile, ConversationFileChunk

from ..types import ToolExecutionContext


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _portal_file_embedding_service():
    if not bool(getattr(settings, "RAG_PORTAL_FILE_SEARCH_ENABLED", True)):
        return None
    try:
        from apps.rag.embeddings import build_embedding_service
    except Exception:
        return None
    return build_embedding_service()


def _resolve_file_context_conversation(conversation: Conversation) -> Conversation:
    """
    Agent runs execute in isolated conversations, but still need access to the
    anchor chat's uploaded files/artifacts.

    If the current conversation is an agent-run execution context and it has an
    `anchor_conversation_id`, route file tools against that anchor conversation.
    """

    cached = getattr(conversation, "_file_context_conversation", None)
    if isinstance(cached, Conversation):
        return cached

    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    if isinstance(meta, Mapping):
        source = str(meta.get("source") or "").strip().lower()
        if source == "agent_run":
            anchor_id_raw = str(meta.get("anchor_conversation_id") or meta.get("anchorConversationId") or "").strip()
            if anchor_id_raw:
                try:
                    anchor_uuid = uuid.UUID(anchor_id_raw)
                except (TypeError, ValueError):
                    anchor_uuid = None
                if anchor_uuid:
                    anchor = Conversation.objects.filter(
                        id=anchor_uuid,
                        business_profile_id=getattr(conversation, "business_profile_id", None),
                    ).first()
                    if anchor is not None:
                        setattr(conversation, "_file_context_conversation", anchor)
                        return anchor

    setattr(conversation, "_file_context_conversation", conversation)
    return conversation


def _search_conversation_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    query_text = _coerce_str(arguments.get("query")).strip()
    if not query_text:
        return {
            "tool": "search_conversation_files",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "query is required.",
        }

    try:
        limit = int(arguments.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(8, limit))

    base_qs = (
        ConversationFileChunk.objects.filter(
            conversation=file_conversation,
            conversation_file__status="ready",
            conversation_file__kind="upload",
        )
        .select_related("conversation_file")
        .order_by("id")
    )
    if not base_qs.exists():
        return {
            "tool": "search_conversation_files",
            "status": "ok",
            "snippets": [],
            "hint": "No uploaded files are available in this chat yet.",
        }

    snippets: list[dict[str, object]] = []
    embedder = _portal_file_embedding_service()
    query_vector: list[float] | None = None
    if embedder:
        try:
            query_vector = embedder.embed_text(query_text)
        except Exception:
            query_vector = None

    if query_vector:
        try:
            from pgvector.django import CosineDistance
        except Exception:  # pragma: no cover - defensive
            query_vector = None
        else:
            ann_limit = max(limit * 10, 40)
            ann_qs = (
                base_qs.exclude(embedding__isnull=True)
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance", "id")[:ann_limit]
            )
            for chunk in ann_qs[:limit]:
                distance = getattr(chunk, "distance", None)
                try:
                    distance_val = float(distance) if distance is not None else None
                except (TypeError, ValueError):
                    distance_val = None
                file = getattr(chunk, "conversation_file", None)
                snippets.append(
                    {
                        "id": str(chunk.id),
                        "file": {
                            "id": str(getattr(file, "id", "")),
                            "filename": getattr(file, "filename", ""),
                            "page_count": getattr(file, "page_count", 0),
                        },
                        "preview": (chunk.content or "")[:800],
                        "vector_distance": distance_val,
                        "read_hint": {"ids": [str(chunk.id)]},
                    }
                )

    if not snippets:
        # Lexical fallback for environments without embeddings.
        try:
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
        except Exception:  # pragma: no cover - defensive
            SearchVector = None  # type: ignore
        if SearchVector is not None:
            config = str(getattr(settings, "RAG_FTS_CONFIG", "english") or "english")
            vector = SearchVector("content", config=config)
            search_query = SearchQuery(query_text, search_type="websearch", config=config)
            fts_qs = (
                base_qs.annotate(rank=SearchRank(vector, search_query, cover_density=True))
                .filter(rank__gt=0)
                .order_by("-rank", "id")[:limit]
            )
            for chunk in fts_qs:
                file = getattr(chunk, "conversation_file", None)
                snippets.append(
                    {
                        "id": str(chunk.id),
                        "file": {
                            "id": str(getattr(file, "id", "")),
                            "filename": getattr(file, "filename", ""),
                            "page_count": getattr(file, "page_count", 0),
                        },
                        "preview": (chunk.content or "")[:800],
                        "rank": float(getattr(chunk, "rank", 0.0) or 0.0),
                        "read_hint": {"ids": [str(chunk.id)]},
                    }
                )

    if not snippets:
        return {
            "tool": "search_conversation_files",
            "status": "ok",
            "snippets": [],
            "hint": "No matches found in uploaded files.",
        }

    return {
        "tool": "search_conversation_files",
        "status": "ok",
        "snippets": snippets[:limit],
    }


def _read_conversation_file_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    raw_ids = arguments.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return {
            "tool": "read_conversation_file",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "ids[] is required (from search_conversation_files).",
        }

    ids: list[str] = []
    uuid_ids: list[uuid.UUID] = []
    for item in raw_ids:
        token = str(item or "").strip()
        if not token:
            continue
        try:
            uuid_ids.append(uuid.UUID(token))
            ids.append(token)
        except (TypeError, ValueError):
            continue
    if not uuid_ids:
        return {
            "tool": "read_conversation_file",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "ids[] must contain valid UUIDs from search_conversation_files.",
        }

    try:
        max_chars = int(arguments.get("max_chars") or 8000)
    except (TypeError, ValueError):
        max_chars = 8000
    max_chars = max(500, min(20000, max_chars))

    rows = list(
        ConversationFileChunk.objects.filter(
            conversation=file_conversation,
            id__in=uuid_ids,
            conversation_file__status="ready",
        )
        .select_related("conversation_file")
        .order_by("id")
    )
    by_id = {str(row.id): row for row in rows}

    out_chunks: list[dict[str, object]] = []
    remaining = max_chars
    for chunk_id in ids:
        row = by_id.get(chunk_id)
        if row is None:
            continue
        content = (row.content or "").strip()
        if not content:
            continue
        clipped = content[:remaining]
        remaining -= len(clipped)
        file = getattr(row, "conversation_file", None)
        out_chunks.append(
            {
                "id": str(row.id),
                "file": {
                    "id": str(getattr(file, "id", "")),
                    "filename": getattr(file, "filename", ""),
                    "page_count": getattr(file, "page_count", 0),
                },
                "content": clipped,
            }
        )
        if remaining <= 0:
            break

    if not out_chunks:
        return {
            "tool": "read_conversation_file",
            "status": "ok",
            "chunks": [],
            "hint": "No content could be read for the requested ids.",
        }

    return {
        "tool": "read_conversation_file",
        "status": "ok",
        "chunks": out_chunks,
    }


def _portal_file_download_url(conversation: Conversation, file_id: uuid.UUID) -> str:
    from datetime import timedelta

    from apps.conversations.portal_files import sign_portal_file_token

    ttl_seconds = int(getattr(settings, "PORTAL_FILE_DOWNLOAD_TTL_SECONDS", 3600) or 0)
    if ttl_seconds <= 0:
        ttl_seconds = 3600
    token = sign_portal_file_token(
        file_id=file_id,
        business_id=conversation.business_profile_id,
        ttl=timedelta(seconds=ttl_seconds),
    )
    return f"/api/chat/portal/files/{file_id}/download/?token={token}"


def _pdf_generate_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    content = _coerce_str(arguments.get("content")).strip()
    if not content:
        return {
            "tool": "pdf_generate",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "content is required.",
        }

    title = _coerce_str(arguments.get("title")).strip()
    filename = _coerce_str(arguments.get("filename")).strip() or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    fmt = _coerce_str(arguments.get("format")).strip().lower() or "markdown"
    page_size = _coerce_str(arguments.get("page_size")).strip().lower() or "letter"

    try:
        import io

        from reportlab.lib.pagesizes import A4, LETTER
        from reportlab.lib.units import inch
        from reportlab.lib.utils import simpleSplit
        from reportlab.pdfgen import canvas
    except Exception:
        return {
            "tool": "pdf_generate",
            "status": "error",
            "error": "pdf_generation_unavailable",
            "error_code": "pdf_generation_unavailable",
            "hint": "PDF generation backend is not installed.",
        }

    if page_size == "a4":
        pagesize = A4
    else:
        pagesize = LETTER
    width, height = pagesize

    # Render markdown as plain text for now (no HTML/CSS rendering).
    text = content
    if fmt not in {"markdown", "text"}:
        fmt = "text"

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=pagesize)

    margin_x = 0.75 * inch
    margin_y = 0.75 * inch
    line_height = 14
    body_font = ("Helvetica", 11)
    title_font = ("Helvetica-Bold", 16)

    y = height - margin_y
    if title:
        c.setFont(*title_font)
        for line in simpleSplit(title, title_font[0], title_font[1], width - (2 * margin_x)):
            if y <= margin_y:
                c.showPage()
                y = height - margin_y
                c.setFont(*title_font)
            c.drawString(margin_x, y, line)
            y -= line_height + 2
        y -= 6

    c.setFont(*body_font)
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            y -= line_height
            if y <= margin_y:
                c.showPage()
                y = height - margin_y
                c.setFont(*body_font)
            continue
        wrapped = simpleSplit(line, body_font[0], body_font[1], width - (2 * margin_x))
        for segment in wrapped:
            if y <= margin_y:
                c.showPage()
                y = height - margin_y
                c.setFont(*body_font)
            c.drawString(margin_x, y, segment)
            y -= line_height

    c.save()
    pdf_bytes = buf.getvalue()

    from apps.conversations.portal_files import create_conversation_artifact_from_bytes

    artifact = create_conversation_artifact_from_bytes(
        conversation=file_conversation,
        filename=filename,
        content_type="application/pdf",
        payload=pdf_bytes,
        sender="ai",
        max_pdf_pages=int(getattr(settings, "PORTAL_PDF_MAX_PAGES", 250) or 0) or None,
    )
    download_url = _portal_file_download_url(file_conversation, artifact.id)
    return {
        "tool": "pdf_generate",
        "status": "ok",
        "artifact": {
            "file_id": str(artifact.id),
            "filename": artifact.filename,
            "download_url": download_url,
        },
        "download_url": download_url,
    }


def _pdf_merge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    raw_ids = arguments.get("file_ids")
    if not isinstance(raw_ids, list) or len(raw_ids) < 2:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_ids[] must contain at least two PDF file IDs.",
        }

    ordered_ids: list[str] = []
    uuid_ids: list[uuid.UUID] = []
    for item in raw_ids:
        token = str(item or "").strip()
        if not token:
            continue
        try:
            uuid_ids.append(uuid.UUID(token))
            ordered_ids.append(token)
        except (TypeError, ValueError):
            continue
    if len(uuid_ids) < 2:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_ids[] must contain valid UUIDs.",
        }

    filename = _coerce_str(arguments.get("filename")).strip() or "merged.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    files = list(
        ConversationFile.objects.filter(
            conversation=file_conversation,
            id__in=uuid_ids,
            status="ready",
        ).order_by("id")
    )
    by_id = {str(f.id): f for f in files}
    resolved = [by_id.get(file_id) for file_id in ordered_ids]
    resolved = [f for f in resolved if f is not None]
    if len(resolved) < 2:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "not_found",
            "error_code": "not_found",
            "hint": "One or more PDFs were not found in this chat session.",
        }

    try:
        import io

        from pypdf import PdfReader, PdfWriter
    except Exception:
        return {
            "tool": "pdf_merge",
            "status": "error",
            "error": "pdf_backend_unavailable",
            "error_code": "pdf_backend_unavailable",
            "hint": "PDF backend is not available.",
        }

    from apps.conversations.portal_files import resolve_portal_file_path

    writer = PdfWriter()
    for f in resolved:
        if not (f.content_type == "application/pdf" or str(f.filename or "").lower().endswith(".pdf")):
            return {
                "tool": "pdf_merge",
                "status": "error",
                "error": "validation_error",
                "error_code": "validation_error",
                "hint": f"{f.filename} is not a PDF.",
            }
        path = resolve_portal_file_path(f)
        if not path.exists():
            return {
                "tool": "pdf_merge",
                "status": "error",
                "error": "not_found",
                "error_code": "not_found",
                "hint": f"Missing file on disk: {f.filename}",
            }
        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            return {
                "tool": "pdf_merge",
                "status": "error",
                "error": "invalid_pdf",
                "error_code": "invalid_pdf",
                "hint": f"Invalid PDF: {f.filename}.",
            }
        for page in reader.pages:
            writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    pdf_bytes = out.getvalue()

    from apps.conversations.portal_files import create_conversation_artifact_from_bytes

    artifact = create_conversation_artifact_from_bytes(
        conversation=file_conversation,
        filename=filename,
        content_type="application/pdf",
        payload=pdf_bytes,
        sender="ai",
        max_pdf_pages=int(getattr(settings, "PORTAL_PDF_MAX_PAGES", 250) or 0) or None,
    )
    download_url = _portal_file_download_url(file_conversation, artifact.id)
    return {
        "tool": "pdf_merge",
        "status": "ok",
        "artifact": {
            "file_id": str(artifact.id),
            "filename": artifact.filename,
            "download_url": download_url,
        },
        "download_url": download_url,
    }


def _pdf_extract_pages_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    file_id_raw = _coerce_str(arguments.get("file_id")).strip()
    if not file_id_raw:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id is required.",
        }
    try:
        file_id = uuid.UUID(file_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id must be a valid UUID.",
        }

    pages_raw = arguments.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "pages[] is required.",
        }
    pages: list[int] = []
    for item in pages_raw:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page >= 1:
            pages.append(page)
    pages = sorted(set(pages))
    if not pages:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "pages[] must contain 1-based page numbers.",
        }

    filename = _coerce_str(arguments.get("filename")).strip() or "pages.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    file = ConversationFile.objects.filter(conversation=file_conversation, id=file_id, status="ready").first()
    if file is None:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "not_found",
            "error_code": "not_found",
            "hint": "PDF not found in this chat session.",
        }
    if not (file.content_type == "application/pdf" or str(file.filename or "").lower().endswith(".pdf")):
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "Selected file is not a PDF.",
        }

    try:
        import io

        from pypdf import PdfReader, PdfWriter
    except Exception:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "pdf_backend_unavailable",
            "error_code": "pdf_backend_unavailable",
            "hint": "PDF backend is not available.",
        }

    from apps.conversations.portal_files import resolve_portal_file_path

    path = resolve_portal_file_path(file)
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    invalid = [p for p in pages if p > total_pages]
    if invalid:
        return {
            "tool": "pdf_extract_pages",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": f"Invalid pages {invalid}; PDF has {total_pages} pages.",
        }
    writer = PdfWriter()
    for page_no in pages:
        writer.add_page(reader.pages[page_no - 1])
    out = io.BytesIO()
    writer.write(out)
    pdf_bytes = out.getvalue()

    from apps.conversations.portal_files import create_conversation_artifact_from_bytes

    artifact = create_conversation_artifact_from_bytes(
        conversation=file_conversation,
        filename=filename,
        content_type="application/pdf",
        payload=pdf_bytes,
        sender="ai",
        max_pdf_pages=int(getattr(settings, "PORTAL_PDF_MAX_PAGES", 250) or 0) or None,
    )
    download_url = _portal_file_download_url(file_conversation, artifact.id)
    return {
        "tool": "pdf_extract_pages",
        "status": "ok",
        "artifact": {
            "file_id": str(artifact.id),
            "filename": artifact.filename,
            "download_url": download_url,
        },
        "download_url": download_url,
    }


def _pdf_extract_text_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_conversation = _resolve_file_context_conversation(conversation)
    file_id_raw = _coerce_str(arguments.get("file_id")).strip()
    if not file_id_raw:
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id is required.",
        }
    try:
        file_id = uuid.UUID(file_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "file_id must be a valid UUID.",
        }

    try:
        max_chars = int(arguments.get("max_chars") or 12000)
    except (TypeError, ValueError):
        max_chars = 12000
    max_chars = max(500, min(50000, max_chars))

    pages_raw = arguments.get("pages")
    pages: list[int] | None = None
    if isinstance(pages_raw, list) and pages_raw:
        extracted_pages: list[int] = []
        for item in pages_raw:
            try:
                page = int(item)
            except (TypeError, ValueError):
                continue
            if page >= 1:
                extracted_pages.append(page)
        extracted_pages = sorted(set(extracted_pages))
        if extracted_pages:
            pages = extracted_pages

    file = ConversationFile.objects.filter(conversation=file_conversation, id=file_id, status="ready").first()
    if file is None:
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "not_found",
            "error_code": "not_found",
            "hint": "PDF not found in this chat session.",
        }
    if not (file.content_type == "application/pdf" or str(file.filename or "").lower().endswith(".pdf")):
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "validation_error",
            "error_code": "validation_error",
            "hint": "Selected file is not a PDF.",
        }

    try:
        from pypdf import PdfReader
    except Exception:
        return {
            "tool": "pdf_extract_text",
            "status": "error",
            "error": "pdf_backend_unavailable",
            "error_code": "pdf_backend_unavailable",
            "hint": "PDF backend is not available.",
        }

    from apps.conversations.portal_files import resolve_portal_file_path

    path = resolve_portal_file_path(file)
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    if pages:
        invalid = [p for p in pages if p > total_pages]
        if invalid:
            return {
                "tool": "pdf_extract_text",
                "status": "error",
                "error": "validation_error",
                "error_code": "validation_error",
                "hint": f"Invalid pages {invalid}; PDF has {total_pages} pages.",
            }
        page_numbers = pages
    else:
        page_numbers = list(range(1, total_pages + 1))

    fragments: list[str] = []
    for p in page_numbers:
        try:
            fragments.append(reader.pages[p - 1].extract_text() or "")
        except Exception:
            fragments.append("")
        if sum(len(x) for x in fragments) >= max_chars:
            break
    text = "\n".join(fragments).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return {
        "tool": "pdf_extract_text",
        "status": "ok",
        "file": {"id": str(file.id), "filename": file.filename, "page_count": total_pages},
        "text": text,
    }
