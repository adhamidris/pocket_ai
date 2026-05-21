from __future__ import annotations

import time
import uuid
from typing import Mapping, Sequence

from apps.rag.integrations.azure_ai_search import AzureAISearchConfig, AzureAISearchError


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
