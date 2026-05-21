from __future__ import annotations

import time
import uuid
from typing import Mapping, MutableMapping, Sequence

from django.conf import settings
from django.db import connection, transaction
from django.db.utils import DatabaseError

from apps.rag.contracts import AliasSearchResult, KnowledgeSearchResult, QueryTraits
from apps.rag.query.normalizer import QueryNormalizer
from core.otel import otel_trace
from core.tenancy import tenant_context


TRACER = otel_trace.get_tracer(__name__)


class SearchEntrypointMixin:

    def analyze_query(self, query: str, business_profile=None) -> QueryTraits:
        filler_tokens = self._filler_tokens_for_business(business_profile) if business_profile else None
        return QueryNormalizer.normalize(query, filler_tokens=filler_tokens)

    def search(
        self,
        *,
        business_profile,
        query: str,
        limit: int | None = None,
        traits: QueryTraits | None = None,
        alias_result: AliasSearchResult | None = None,
        session_cache: MutableMapping[str, object] | None = None,
        identifier_filter: Mapping[str, str] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> KnowledgeSearchResult:
        """
        Entry point for retrieval planning and execution.

        Architectural ownership:
        - Query analysis / rewriting signals live here.
        - Retrieval strategy selection lives here.
        - Hybrid candidate generation lives here.
        - Table routing / blending / fallback lives here.
        - Fusion and reranking live here.

        MCP should treat this method as the retrieval source of truth and remain
        a thin tool-contract layer around it. If we ever need explicit batched
        query search, add that entry point here rather than rebuilding retrieval
        planning in `apps.mcp.tools`.
        """

        traits = traits or self.analyze_query(query, business_profile=business_profile)
        business_id = getattr(business_profile, "id", None) if business_profile else None
        with tenant_context(business_id):
            with TRACER.start_as_current_span("knowledge.search") as span:
                if span.is_recording():
                    span.set_attribute("knowledge.query", traits.original or query)
                    span.set_attribute("knowledge.query_tokens", traits.token_count)
                    if business_profile and getattr(business_profile, "id", None):
                        span.set_attribute("knowledge.business_id", str(business_profile.id))
                start = time.perf_counter()
                statement_timeout_ms = int(getattr(settings, "RAG_DB_STATEMENT_TIMEOUT_MS", 0) or 0)
                lock_timeout_ms = int(getattr(settings, "RAG_DB_LOCK_TIMEOUT_MS", 0) or 0)

                def _run_search() -> KnowledgeSearchResult:
                    return self._search_inner(
                        business_profile=business_profile,
                        query=query,
                        limit=limit,
                        traits=traits,
                        alias_result=alias_result,
                        session_cache=session_cache,
                        identifier_filter=identifier_filter,
                        allowed_upload_ids=allowed_upload_ids,
                        allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                        session_context=session_context,
                    )

                def _apply_db_timeouts() -> None:
                    if statement_timeout_ms <= 0 and lock_timeout_ms <= 0:
                        return
                    with connection.cursor() as cursor:
                        if lock_timeout_ms > 0:
                            cursor.execute("SET LOCAL lock_timeout = %s", [lock_timeout_ms])
                        if statement_timeout_ms > 0:
                            cursor.execute("SET LOCAL statement_timeout = %s", [statement_timeout_ms])

                try:
                    if statement_timeout_ms > 0 or lock_timeout_ms > 0:
                        # Use SET LOCAL (transaction-scoped) so we don't leak timeouts across pooled connections.
                        with transaction.atomic():
                            _apply_db_timeouts()
                            result = _run_search()
                    else:
                        result = _run_search()
                except DatabaseError as exc:
                    duration_ms = int((time.perf_counter() - start) * 1000.0)
                    message = str(exc)
                    lowered = message.lower()
                    timeout_reason = None
                    if "statement timeout" in lowered or "canceling statement" in lowered:
                        timeout_reason = "statement_timeout"
                    elif "lock timeout" in lowered:
                        timeout_reason = "lock_timeout"

                    diagnostics = {
                        "original_query": traits.original,
                        "normalized_query": traits.normalized,
                        "token_count": traits.token_count,
                        "total_duration_ms": duration_ms,
                        "error_code": "db_timeout" if timeout_reason else "db_error",
                        "db_timeout_reason": timeout_reason,
                        "db_statement_timeout_ms": statement_timeout_ms if statement_timeout_ms > 0 else None,
                        "db_lock_timeout_ms": lock_timeout_ms if lock_timeout_ms > 0 else None,
                        "error": message[:500],
                    }
                    result = KnowledgeSearchResult(snippets=tuple(), status="not_found", diagnostics=diagnostics)
                if span.is_recording():
                    span.set_attribute("knowledge.status", result.status)
                    span.set_attribute("knowledge.snippet_count", len(result.snippets))
                    diagnostics = result.diagnostics or {}
                    snippet_limit = diagnostics.get("snippet_limit")
                    if isinstance(snippet_limit, int):
                        span.set_attribute("knowledge.limit", snippet_limit)
                    duration_ms = diagnostics.get("total_duration_ms")
                    if isinstance(duration_ms, (int, float)):
                        span.set_attribute("knowledge.duration_ms", duration_ms)
                return result
