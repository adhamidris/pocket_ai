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


from apps.rag.integrations.azure_search_documents import (  # noqa: E402
    delete_upload,
    update_chunk_embeddings,
    upsert_upload_chunks,
)
from apps.rag.integrations.azure_search_index import ensure_index  # noqa: E402
from apps.rag.integrations.azure_search_query import search  # noqa: E402

__all__ = [
    "AzureAISearchConfig",
    "AzureAISearchError",
    "build_scope_filter",
    "delete_upload",
    "ensure_index",
    "search",
    "update_chunk_embeddings",
    "upsert_upload_chunks",
]

