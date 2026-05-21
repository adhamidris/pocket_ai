from __future__ import annotations

import uuid
from typing import Mapping

from django.conf import settings

from apps.conversations.models import Conversation, ConversationFileChunk

from ...types import ToolExecutionContext
from .shared import _coerce_str, _resolve_file_context_conversation


def _portal_file_embedding_service():
    if not bool(getattr(settings, "RAG_PORTAL_FILE_SEARCH_ENABLED", True)):
        return None
    try:
        from apps.rag.embeddings import build_embedding_service
    except Exception:
        return None
    return build_embedding_service()


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
