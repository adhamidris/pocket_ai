from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any, Iterable, Mapping, Sequence

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class AzureAISearchError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class AzureAISearchConfig:
    endpoint: str
    index_name: str
    admin_key: str
    query_key: str | None
    semantic_enabled: bool
    semantic_config: str
    request_timeout_s: float
    index_batch_size: int
    upload_filter_threshold: int

    @classmethod
    def from_settings(cls) -> AzureAISearchConfig | None:
        endpoint = str(getattr(settings, "AZURE_SEARCH_ENDPOINT", "") or "").strip()
        index_name = str(getattr(settings, "AZURE_SEARCH_INDEX_NAME", "") or "").strip()
        admin_key = str(getattr(settings, "AZURE_SEARCH_ADMIN_KEY", "") or "").strip()
        query_key = str(getattr(settings, "AZURE_SEARCH_QUERY_KEY", "") or "").strip() or None
        semantic_enabled = bool(getattr(settings, "AZURE_SEARCH_SEMANTIC_ENABLED", False))
        semantic_config = str(getattr(settings, "AZURE_SEARCH_SEMANTIC_CONFIG", "default") or "default").strip() or "default"
        try:
            request_timeout_s = float(getattr(settings, "AZURE_SEARCH_REQUEST_TIMEOUT_S", 6.0) or 6.0)
        except (TypeError, ValueError):
            request_timeout_s = 6.0
        try:
            index_batch_size = int(getattr(settings, "AZURE_SEARCH_INDEX_BATCH_SIZE", 500) or 500)
        except (TypeError, ValueError):
            index_batch_size = 500
        try:
            upload_filter_threshold = int(getattr(settings, "AZURE_SEARCH_UPLOAD_FILTER_THRESHOLD", 150) or 150)
        except (TypeError, ValueError):
            upload_filter_threshold = 150

        if not endpoint or not index_name or not admin_key:
            missing: list[str] = []
            if not endpoint:
                missing.append("AZURE_SEARCH_ENDPOINT")
            if not index_name:
                missing.append("AZURE_SEARCH_INDEX_NAME")
            if not admin_key:
                missing.append("AZURE_SEARCH_ADMIN_KEY")
            logger.warning("azure_search.disabled missing=%s", ",".join(missing))
            return None

        index_batch_size = max(1, min(1000, index_batch_size))
        upload_filter_threshold = max(1, min(500, upload_filter_threshold))
        request_timeout_s = max(1.0, min(30.0, request_timeout_s))
        return cls(
            endpoint=endpoint,
            index_name=index_name,
            admin_key=admin_key,
            query_key=query_key,
            semantic_enabled=semantic_enabled,
            semantic_config=semantic_config,
            request_timeout_s=request_timeout_s,
            index_batch_size=index_batch_size,
            upload_filter_threshold=upload_filter_threshold,
        )


def _safe_uuid_str(value: object) -> str:
    try:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return ""


def _chunk_document_key(upload_id: uuid.UUID, chunk_index: int) -> str:
    return f"{upload_id}:{int(chunk_index)}"


def _search_in(field_name: str, values: Sequence[str]) -> str:
    # Azure filter function: search.in(field, 'a,b,c', ',')
    joined = ",".join(v for v in values if v)
    return f"search.in({field_name}, '{joined}', ',')"


def build_scope_filter(
    *,
    business_id: uuid.UUID,
    allowed_upload_ids: Sequence[uuid.UUID] | None,
    agent_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    upload_filter_threshold: int = 150,
) -> tuple[str, dict[str, object]]:
    """
    Build an Azure AI Search filter expression.

    Returns: (filter_string, diagnostics)
    """

    diagnostics: dict[str, object] = {}
    base = f"business_id eq '{business_id}'"
    agent_explicit_upload_str = [str(v) for v in (agent_explicit_upload_ids or ()) if v]
    if allowed_upload_ids is None and not agent_explicit_upload_str:
        diagnostics["scope_mode"] = "all"
        return base, diagnostics

    allowed_upload_str = [str(v) for v in (allowed_upload_ids or ()) if v]

    diagnostics["scope_mode"] = "restricted"
    diagnostics["allowed_uploads"] = len(allowed_upload_str)
    diagnostics["agent_explicit_uploads"] = len(agent_explicit_upload_str)

    if allowed_upload_ids is not None and not allowed_upload_str:
        diagnostics["scope_fallback"] = "none"
        return f"{base} and id eq ''", diagnostics

    if allowed_upload_ids is not None and len(allowed_upload_str) <= upload_filter_threshold:
        return f"{base} and {_search_in('upload_id', allowed_upload_str)}", diagnostics

    if allowed_upload_ids is None and agent_explicit_upload_str:
        if len(agent_explicit_upload_str) <= upload_filter_threshold:
            diagnostics["scope_fallback"] = "explicit"
            return f"{base} and {_search_in('upload_id', agent_explicit_upload_str)}", diagnostics
        diagnostics["explicit_uploads_filter_skipped"] = True

    diagnostics["scope_fallback"] = "unfiltered"
    diagnostics["scope_filter_skipped"] = True
    return base, diagnostics


def _serialize_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone=dt_timezone.utc)
    return value.isoformat()


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def ensure_index(*, config: AzureAISearchConfig, embedding_dim: int) -> None:
    """
    Create or update the knowledge chunk index in Azure AI Search.

    Safe to call repeatedly. Requires admin key.
    """

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents.indexes import SearchIndexClient
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            HnswParameters,
            SearchField,
            SearchFieldDataType,
            SearchIndex,
            SearchableField,
            SemanticConfiguration,
            SemanticField,
            SemanticPrioritizedFields,
            SemanticSearch,
            SimpleField,
            VectorSearch,
            VectorSearchAlgorithmMetric,
            VectorSearchProfile,
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        raise AzureAISearchError(f"azure-search-documents is required: {exc}") from exc

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True, sortable=False, facetable=False),
        SimpleField(name="business_id", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="upload_id", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True, sortable=True, facetable=False),
        SearchableField(name="title", type=SearchFieldDataType.String, searchable=True, filterable=False, sortable=False, facetable=False, retrievable=True),
        SearchableField(name="content", type=SearchFieldDataType.String, searchable=True, filterable=False, sortable=False, facetable=False, retrievable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            retrievable=False,
            vector_search_dimensions=int(embedding_dim),
            vector_search_profile_name="default",
        ),
        SimpleField(name="format", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="index_type", type=SearchFieldDataType.String, filterable=True, sortable=False, facetable=False),
        SimpleField(name="is_table_chunk", type=SearchFieldDataType.Boolean, filterable=True, sortable=False, facetable=False),
        SimpleField(name="updated_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True, facetable=False),
    ]

    vector_search = VectorSearch(
        profiles=[VectorSearchProfile(name="default", algorithm_configuration_name="hnsw")],
        algorithms=[
            HnswAlgorithmConfiguration(
                name="hnsw",
                parameters=HnswParameters(
                    metric=VectorSearchAlgorithmMetric.COSINE,
                    m=6,
                    ef_construction=400,
                    ef_search=200,
                ),
            )
        ],
    )

    semantic_search = SemanticSearch(
        default_configuration_name="default",
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ],
    )

    index = SearchIndex(
        name=config.index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    client = SearchIndexClient(config.endpoint, AzureKeyCredential(config.admin_key))
    client.create_or_update_index(index)


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


def search(
    *,
    config: AzureAISearchConfig,
    business_id: uuid.UUID,
    query_text: str,
    query_vector: Sequence[float] | None,
    top: int,
    filter: str,
    semantic_enabled: bool,
    semantic_config: str,
    request_timeout_s: float,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """
    Execute a hybrid search (text + vector if provided).
    """

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.models import VectorizedQuery
    except Exception as exc:  # pragma: no cover - optional dependency
        raise AzureAISearchError(f"azure-search-documents is required: {exc}") from exc

    started = time.perf_counter()
    credential_value = config.query_key or config.admin_key
    client = SearchClient(config.endpoint, config.index_name, AzureKeyCredential(credential_value))

    vector_queries = None
    if query_vector is not None:
        vector_queries = [
            VectorizedQuery(
                vector=list(query_vector),
                k_nearest_neighbors=max(1, int(top)),
                fields="content_vector",
            )
        ]

    kwargs: dict[str, object] = {
        "filter": filter,
        "top": max(1, int(top)),
        "select": ["chunk_id", "upload_id", "chunk_index", "title", "format", "index_type", "is_table_chunk"],
        "vector_queries": vector_queries,
    }
    if semantic_enabled:
        kwargs.update(
            {
                "query_type": "semantic",
                "semantic_configuration_name": semantic_config,
                "semantic_query": query_text,
            }
        )
    response = client.search(query_text, **kwargs, timeout=request_timeout_s)
    results: list[dict[str, object]] = []
    for hit in response:
        if not isinstance(hit, Mapping):
            continue
        payload = dict(hit)
        score = payload.get("@search.score")
        if score is not None:
            payload["score"] = score
        results.append(payload)
    diagnostics = {
        "duration_ms": int((time.perf_counter() - started) * 1000.0),
        "result_count": len(results),
        "semantic": bool(semantic_enabled),
        "filter": filter[:500],
        "has_vector": query_vector is not None,
    }
    return results, diagnostics
