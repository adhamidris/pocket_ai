from __future__ import annotations

import dataclasses
import hashlib
import uuid
from typing import Any, Mapping, MutableMapping, Sequence

from django.conf import settings
from django.core.cache import cache

from apps.accounts.feature_flags import FeatureState
from apps.knowledge.access.visibility import CUSTOMER_VISIBILITY_POLICY_KEY
from apps.rag.contracts import (
    AliasSearchResult,
    KnowledgeSearchResult,
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_SUMMARY,
    QueryTraits,
)
from apps.rag.query_classifier import QueryClassification


class KnowledgeSearchCacheMixin:
    @staticmethod
    def _alias_cache_version_key(business_id: uuid.UUID) -> str:
        return f"rag:alias:ver:{business_id}"

    @staticmethod
    def _alias_cache_key(business_id: uuid.UUID, alias_value: str, version: int) -> str:
        return f"rag:alias:{business_id}:{version}:{alias_value}"

    @staticmethod
    def _query_cache_version_key(business_id: uuid.UUID) -> str:
        return f"rag:qvec:ver:{business_id}"

    @classmethod
    def invalidate_alias_cache(cls, business_id: uuid.UUID) -> None:
        version_key = cls._alias_cache_version_key(business_id)
        try:
            cache.incr(version_key)
        except ValueError:
            cache.set(version_key, 1, None)

    @classmethod
    def invalidate_query_cache(cls, business_id: uuid.UUID) -> None:
        version_key = cls._query_cache_version_key(business_id)
        try:
            cache.incr(version_key)
        except ValueError:
            cache.set(version_key, 1, None)

    def _get_alias_cache_version(self, business_id: uuid.UUID) -> int:
        version_key = self._alias_cache_version_key(business_id)
        version = cache.get(version_key)
        if version is None:
            cache.set(version_key, 0, None)
            return 0
        return int(version)

    def _get_query_cache_version(self, business_id: uuid.UUID) -> int:
        version_key = self._query_cache_version_key(business_id)
        version = cache.get(version_key)
        if version is None:
            cache.set(version_key, 0, None)
            return 0
        return int(version)

    @staticmethod
    def _result_cache_version_key(business_id: uuid.UUID) -> str:
        return f"rag:result:ver:{business_id}"

    @classmethod
    def invalidate_result_cache(cls, business_id: uuid.UUID) -> None:
        version_key = cls._result_cache_version_key(business_id)
        try:
            cache.incr(version_key)
        except ValueError:
            cache.set(version_key, 1, None)

    def _get_result_cache_version(self, business_id: uuid.UUID) -> int:
        version_key = self._result_cache_version_key(business_id)
        version = cache.get(version_key)
        if version is None:
            cache.set(version_key, 0, None)
            return 0
        return int(version)

    @staticmethod
    def _serialize_snippet_for_cache(snippet: KnowledgeSnippet) -> dict[str, object]:
        payload = dataclasses.asdict(snippet)
        for key, value in list(payload.items()):
            if isinstance(value, uuid.UUID):
                payload[key] = str(value)
            elif isinstance(value, tuple):
                payload[key] = list(value)
        return payload

    @staticmethod
    def _deserialize_snippet_from_cache(payload: Mapping[str, Any]) -> KnowledgeSnippet | None:
        try:
            return KnowledgeSnippet(
                id=uuid.UUID(str(payload.get("id"))),
                title=str(payload.get("title") or ""),
                summary=str(payload.get("summary") or ""),
                source=str(payload.get("source") or ""),
                content=payload.get("content"),
                content_mode=payload.get("content_mode"),
                public_label=payload.get("public_label"),
                structured_tables=tuple(payload.get("structured_tables") or ()),
                issues=tuple(payload.get("issues") or ()),
                page_summaries=tuple(payload.get("page_summaries") or ()),
                read_state=str(payload.get("read_state") or KNOWLEDGE_READ_STATE_SUMMARY),
                topic_hints=tuple(payload.get("topic_hints") or ()),
                is_pinned=bool(payload.get("is_pinned") or False),
                supplemental_sections=tuple(payload.get("supplemental_sections") or ()),
                upload_id=uuid.UUID(str(payload["upload_id"])) if payload.get("upload_id") else None,
                chunk_id=uuid.UUID(str(payload["chunk_id"])) if payload.get("chunk_id") else None,
                chunk_index=int(payload.get("chunk_index")) if payload.get("chunk_index") is not None else None,
                entity_type=payload.get("entity_type"),
                entity_name=payload.get("entity_name"),
                entity_business=payload.get("entity_business"),
                is_table_chunk=bool(payload.get("is_table_chunk") or False),
                table_id=str(payload.get("table_id") or "").strip() or None,
                evidence_group_id=str(payload.get("evidence_group_id") or "").strip() or None,
                evidence_type=str(payload.get("evidence_type") or "").strip() or None,
                representation=str(payload.get("representation") or "").strip().lower() or None,
                aliases=tuple(payload.get("aliases") or ()),
                search_stage=payload.get("search_stage"),
                confidence_score=float(payload["confidence_score"]) if payload.get("confidence_score") is not None else None,
                truncated=bool(payload.get("truncated") or False),
                source_diagnostics=payload.get("source_diagnostics") or {},
                partial_index=bool(payload.get("partial_index") or False),
                structured_table_count=int(payload.get("structured_table_count") or 0),
                issue_count=int(payload.get("issue_count") or 0),
                structured_table_hint=payload.get("structured_table_hint"),
                page_number=int(payload.get("page_number")) if payload.get("page_number") is not None else None,
                page_mode=payload.get("page_mode"),
            )
        except Exception:
            return None

    @staticmethod
    def _upload_scope_token(
        allowed_upload_ids: Sequence[uuid.UUID] | None,
        *,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> str:
        """Stable scope token used to key per-scope caches."""
        if allowed_upload_ids is None and not allowed_explicit_upload_ids:
            return "all"
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return "none"
            unique = sorted({str(value) for value in allowed_upload_ids if value})
            digest = hashlib.sha256("|".join(unique).encode("utf-8")).hexdigest()[:16]
            return f"u{len(unique)}:{digest}"

        explicit_ids = sorted({str(value) for value in (allowed_explicit_upload_ids or ()) if value})
        if not explicit_ids:
            return "all"
        fingerprint = "explicit:" + ",".join(explicit_ids)
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
        return f"d{len(explicit_ids)}:{digest}"

    def _result_cache_key(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        limit: int,
        alias_result: AliasSearchResult | None,
        table_context: Mapping[str, object],
        feature_state: FeatureState,
        identifier_filter: Mapping[str, str] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> str:
        version = self._get_result_cache_version(business_profile.id)
        qvec_version = self._get_query_cache_version(business_profile.id)
        model_name = getattr(self.embedding_service, "model", "local")
        backend = str(getattr(settings, "RAG_SEARCH_BACKEND", "postgres") or "postgres").strip().lower()
        azure_index = str(getattr(settings, "AZURE_SEARCH_INDEX_NAME", "") or "")
        azure_semantic = bool(getattr(settings, "AZURE_SEARCH_SEMANTIC_ENABLED", False))
        azure_semantic_config = str(getattr(settings, "AZURE_SEARCH_SEMANTIC_CONFIG", "") or "")
        alias_stage = ""
        if alias_result and alias_result.diagnostics:
            alias_stage = str(alias_result.diagnostics.get("stage") or "")
        classification = table_context.get("query_classification")
        intent_name = "none"
        intent_source = "none"
        intent_clarification = "0"
        if isinstance(classification, QueryClassification):
            intent_name = classification.intent.value
            intent_source = str(classification.source or "heuristic")
            intent_clarification = "1" if classification.requires_clarification else "0"
        normalized_query = (traits.normalized or traits.original or "").strip().lower()
        scope_token = self._upload_scope_token(
            allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        fingerprint = "|".join(
            [
                str(business_profile.id),
                scope_token,
                str(version),
                str(qvec_version),
                CUSTOMER_VISIBILITY_POLICY_KEY,
                model_name,
                f"backend:{backend}",
                f"azure_index:{azure_index}",
                f"azure_semantic:{int(azure_semantic)}",
                f"azure_semantic_config:{azure_semantic_config}",
                "evidence_grouping:v3",
                "section" if table_context.get("prefer_section_context") else "chunk",
                ",".join(str(term) for term in (table_context.get("section_focus_terms") or ())[:4]),
                normalized_query,
                str(limit),
                "table" if table_context.get("has_intent") else "chunk",
                "comprehensive" if table_context.get("comprehensive_intent") else "specific",
                f"intent:{intent_name}",
                f"intent_source:{intent_source}",
                f"intent_clarify:{intent_clarification}",
                "hybrid" if feature_state.hybrid_search else "lexical_only",
                "alias_on" if feature_state.alias_lookup else "alias_off",
                "alias_short" if alias_result and alias_result.short_circuit else "alias_none",
                alias_stage,
                str(identifier_filter or {}),
            ]
        )
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
        return f"rag:result:{digest}"

    def _result_cache_get(self, cache_key: str) -> KnowledgeSearchResult | None:
        if not self.result_cache_enabled:
            return None
        cached = cache.get(cache_key)
        if not isinstance(cached, Mapping):
            return None
        snippets_raw = cached.get("snippets") or []
        snippets: list[KnowledgeSnippet] = []
        for item in snippets_raw:
            if not isinstance(item, Mapping):
                continue
            resolved = self._deserialize_snippet_from_cache(item)
            if resolved:
                snippets.append(resolved)
        status = str(cached.get("status") or "ok")
        diagnostics = cached.get("diagnostics") or {}
        limit_hint = cached.get("limit")
        if isinstance(limit_hint, int):
            snippets = snippets[: max(1, limit_hint)]
        return KnowledgeSearchResult(snippets=tuple(snippets), status=status, diagnostics=diagnostics)

    def _result_cache_set(self, cache_key: str, result: KnowledgeSearchResult, *, limit: int) -> None:
        if not self.result_cache_enabled:
            return
        diag = dict(result.diagnostics or {})
        diag.pop("request_id", None)
        diag.pop("total_duration_ms", None)
        payload = {
            "status": result.status,
            "diagnostics": diag,
            "snippets": [self._serialize_snippet_for_cache(s) for s in result.snippets],
            "limit": limit,
        }
        cache.set(cache_key, payload, timeout=self.result_cache_ttl)

    def _session_cache_get(
        self,
        cache_dict: MutableMapping[str, object],
        cache_key: str,
    ) -> KnowledgeSearchResult | None:
        if not cache_dict:
            return None
        cached = cache_dict.get(cache_key)
        if not isinstance(cached, Mapping):
            return None
        snippets_raw = cached.get("snippets") or []
        snippets: list[KnowledgeSnippet] = []
        for item in snippets_raw:
            if not isinstance(item, Mapping):
                continue
            resolved = self._deserialize_snippet_from_cache(item)
            if resolved:
                snippets.append(resolved)
        if not snippets and cached.get("status") == "ok":
            return None
        status = str(cached.get("status") or "ok")
        diagnostics = cached.get("diagnostics") or {}
        limit_hint = cached.get("limit")
        if isinstance(limit_hint, int):
            snippets = snippets[: max(1, limit_hint)]
        return KnowledgeSearchResult(snippets=tuple(snippets), status=status, diagnostics=diagnostics)

    def _session_cache_set(
        self,
        cache_dict: MutableMapping[str, object] | None,
        cache_key: str,
        result: KnowledgeSearchResult,
        *,
        limit: int,
    ) -> None:
        if cache_dict is None:
            return
        diag = dict(result.diagnostics or {})
        diag.pop("request_id", None)
        diag.pop("total_duration_ms", None)
        cache_dict[cache_key] = {
            "status": result.status,
            "diagnostics": diag,
            "snippets": [self._serialize_snippet_for_cache(s) for s in result.snippets],
            "limit": limit,
        }
        while len(cache_dict) > self.session_cache_limit:
            oldest_key = next(iter(cache_dict))
            cache_dict.pop(oldest_key, None)

    def _cache_alias_payload(self, business_id: uuid.UUID, alias_value: str, payload: Sequence[Mapping[str, str]]) -> None:
        version = self._get_alias_cache_version(business_id)
        key = self._alias_cache_key(business_id, alias_value, version)
        cache.set(key, list(payload), timeout=self.alias_cache_ttl)
