from __future__ import annotations

import uuid
from datetime import datetime
from typing import Mapping, Sequence

from django.utils import timezone

from apps.rag.integrations.azure_ai_search import (
    AzureAISearchConfig,
    AzureAISearchError,
    _chunk_document_key,
    _coerce_bool,
    _safe_uuid_str,
    _serialize_datetime,
)


def _index_documents(
    *,
    config: AzureAISearchConfig,
    documents: Sequence[Mapping[str, object]],
) -> None:
    if not documents:
        return
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
    except Exception as exc:  # pragma: no cover - optional dependency
        raise AzureAISearchError(f"azure-search-documents is required: {exc}") from exc

    client = SearchClient(config.endpoint, config.index_name, AzureKeyCredential(config.admin_key))
    # Indexing uses the admin key and "mergeOrUpload" for idempotency.
    batch_size = max(1, min(1000, int(config.index_batch_size)))
    for i in range(0, len(documents), batch_size):
        batch = documents[i : i + batch_size]
        client.merge_or_upload_documents(documents=list(batch))


def _merge_documents(
    *,
    config: AzureAISearchConfig,
    documents: Sequence[Mapping[str, object]],
) -> None:
    if not documents:
        return
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
    except Exception as exc:  # pragma: no cover - optional dependency
        raise AzureAISearchError(f"azure-search-documents is required: {exc}") from exc

    client = SearchClient(config.endpoint, config.index_name, AzureKeyCredential(config.admin_key))
    batch_size = max(1, min(1000, int(config.index_batch_size)))
    for i in range(0, len(documents), batch_size):
        batch = list(documents[i : i + batch_size])
        if batch:
            client.merge_documents(documents=batch)


def _delete_documents(
    *,
    config: AzureAISearchConfig,
    keys: Sequence[str],
) -> None:
    if not keys:
        return
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
    except Exception as exc:  # pragma: no cover - optional dependency
        raise AzureAISearchError(f"azure-search-documents is required: {exc}") from exc

    client = SearchClient(config.endpoint, config.index_name, AzureKeyCredential(config.admin_key))
    batch_size = max(1, min(1000, int(config.index_batch_size)))
    for i in range(0, len(keys), batch_size):
        batch = [{"id": key} for key in keys[i : i + batch_size] if key]
        if batch:
            client.delete_documents(documents=batch)


def delete_upload(
    *,
    config: AzureAISearchConfig,
    upload_id: uuid.UUID,
    chunk_count: int,
) -> None:
    keys = [_chunk_document_key(upload_id, idx) for idx in range(max(0, int(chunk_count)))]
    _delete_documents(config=config, keys=keys)


def upsert_upload_chunks(
    *,
    config: AzureAISearchConfig,
    business_id: uuid.UUID,
    upload_id: uuid.UUID,
    title: str,
    format_hint: str | None,
    updated_at: datetime | None,
    chunks: Sequence[Mapping[str, object]],
) -> None:
    """
    Upsert chunk documents for a single upload.

    `chunks` payload items must contain: chunk_id (uuid/str), chunk_index (int), content (str), embedding (list[float]|None), metadata (dict|None)
    """

    updated_iso = _serialize_datetime(updated_at)
    docs: list[dict[str, object]] = []
    for item in chunks:
        chunk_id = _safe_uuid_str(item.get("chunk_id"))
        try:
            chunk_index = int(item.get("chunk_index"))
        except (TypeError, ValueError):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        embedding = item.get("embedding")
        vector: list[float] | None = None
        if isinstance(embedding, (list, tuple)) and embedding:
            try:
                vector = [float(v) for v in embedding]
            except (TypeError, ValueError):
                vector = None
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        index_type = str(metadata.get("index_type") or "").strip() or "text"
        doc: dict[str, object] = {
            "id": _chunk_document_key(upload_id, chunk_index),
            "business_id": str(business_id),
            "upload_id": str(upload_id),
            "chunk_id": chunk_id,
            "chunk_index": chunk_index,
            "title": title[:256],
            "content": content,
            "format": str(format_hint or "").strip().lower()[:24],
            "index_type": index_type[:32],
            "is_table_chunk": _coerce_bool(metadata.get("is_table_chunk")),
            "updated_at": updated_iso,
        }
        if vector is not None:
            doc["content_vector"] = vector
        docs.append(doc)

    _index_documents(config=config, documents=docs)


def update_chunk_embeddings(
    *,
    config: AzureAISearchConfig,
    upload_id: uuid.UUID,
    chunks: Sequence[Mapping[str, object]],
) -> None:
    """
    Update vector embeddings for existing chunk documents.

    `chunks` items must contain: chunk_index (int), embedding (list[float]|None), chunk_id (uuid/str optional).
    """
    updated_iso = _serialize_datetime(timezone.now())
    docs: list[dict[str, object]] = []
    for item in chunks:
        try:
            chunk_index = int(item.get("chunk_index"))
        except (TypeError, ValueError):
            continue
        embedding = item.get("embedding")
        if not isinstance(embedding, (list, tuple)) or not embedding:
            continue
        try:
            vector = [float(v) for v in embedding]
        except (TypeError, ValueError):
            continue
        payload: dict[str, object] = {
            "id": _chunk_document_key(upload_id, chunk_index),
            "content_vector": vector,
            "updated_at": updated_iso,
        }
        chunk_id = _safe_uuid_str(item.get("chunk_id"))
        if chunk_id:
            payload["chunk_id"] = chunk_id
        docs.append(payload)
    _merge_documents(config=config, documents=docs)

