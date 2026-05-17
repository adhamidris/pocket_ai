from __future__ import annotations

import logging
import uuid
from collections import Counter
from typing import Mapping, Sequence

from django.db.models import Q

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
)
from apps.rag.contracts import QueryTraits
from apps.rag.query_classifier import QueryClassifier, QueryIntent
from apps.rag.query_normalizer import QueryNormalizer
from apps.rag.rag_logging import rag_log
from apps.rag.table_semantics import normalize_column_name
from apps.rag.text_utils import is_plural_candidate, singularize


logger = logging.getLogger(__name__)


def _rag_log(
    stage: str,
    detail: object | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, object] | None = None,
) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class TableContextMixin:
    @staticmethod
    def _table_tokenize(value: str) -> set[str]:
        if not value:
            return set()
        normalized = QueryNormalizer._normalize_query_text(str(value))
        normalized = normalized.replace("_", " ").replace("-", " ")
        lowered = normalized.lower()
        return {token for token in QueryNormalizer._TOKEN_SPLIT.split(lowered) if token}

    def _table_query_tokens(
        self,
        business_profile,
        traits: QueryTraits,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> tuple[set[str], set[str]]:
        tokens = {token.lower() for token in traits.tokens if token}
        filler = self._filler_tokens_for_business(business_profile)
        tokens = {token for token in tokens if token not in filler and not token.isdigit()}
        normalized_tokens: set[str] = set()
        for token in tokens:
            if is_plural_candidate(token):
                normalized_tokens.add(singularize(token))
                continue
            normalized_tokens.add(token)
        tokens = normalized_tokens
        generic = set(self.table_query_keywords)
        generic.update(
            {
                "what",
                "which",
                "how",
                "when",
                "where",
                "who",
                "whats",
                "what's",
                "is",
                "are",
                "was",
                "were",
                "do",
                "does",
                "did",
                "can",
                "could",
                "should",
                "would",
                "egp",
                "usd",
                "eur",
                "gbp",
                "aed",
                "sar",
                "qar",
                "kwd",
                "bhd",
                "omr",
                "jod",
                # Product-category words are too generic to act as "specific table" anchors.
                # Keeping them out of `specific_tokens` prevents wrong-table drift like
                # "Heya credit card" -> matching any table that has a `card_type` column.
                "card",
                "cards",
                "credit",
                "debit",
            }
        )
        generic.update(
            self._table_generic_tokens_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
        )
        specific = {
            token
            for token in tokens
            if token not in generic and len(token) >= self.table_specific_min_length
        }
        return tokens, specific

    def _column_matches_tokens(self, column: str | None, tokens: set[str]) -> bool:
        if not column or not tokens:
            return False
        normalized = normalize_column_name(column)
        source = normalized or str(column)
        column_tokens = self._table_tokenize(source)
        if column_tokens & tokens:
            return True
        condensed = (normalized or "").replace("_", "")
        if condensed and any(token in condensed for token in tokens if token):
            return True
        return False

    def _is_usable_table_query_column_match(self, column: str | None) -> bool:
        if not column:
            return False
        normalized = normalize_column_name(column)
        source = (normalized or str(column or "")).strip().lower()
        if not source:
            return False
        if len(source) <= 1:
            return False
        tokens = {token for token in self._table_tokenize(source) if token}
        if tokens and max((len(token) for token in tokens), default=0) <= 1:
            return False
        return True

    def _has_strong_row_label_signal(
        self,
        *,
        matched_row_labels: set[str],
        specific_tokens: set[str],
    ) -> bool:
        if not matched_row_labels:
            return False
        if len(matched_row_labels) >= 2:
            return True
        if not specific_tokens:
            return False
        overlap_ratio = len(matched_row_labels) / max(len(specific_tokens), 1)
        return overlap_ratio > 0.5

    def _table_header_tokens(self, table_id: str | uuid.UUID | None) -> set[str]:
        if not table_id:
            return set()
        cache_key = str(table_id).strip()
        if not cache_key:
            return set()
        cached = self._table_header_token_cache.get(cache_key)
        if cached is not None:
            self._table_header_token_cache.move_to_end(cache_key)
            return cached
        parsed_table_id: uuid.UUID | None = None
        if isinstance(table_id, uuid.UUID):
            parsed_table_id = table_id
        else:
            try:
                parsed_table_id = uuid.UUID(cache_key)
            except (TypeError, ValueError, AttributeError):
                self._table_header_token_cache[cache_key] = set()
                return set()
        payload = (
            KnowledgeUploadTable.objects.filter(id=parsed_table_id)
            .values("column_schema", "title", "section_heading")
            .first()
        )
        tokens: set[str] = set()
        if payload:
            for value in payload.get("column_schema") or []:
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for value in (payload.get("title"), payload.get("section_heading")):
                if value:
                    normalized = normalize_column_name(str(value))
                    tokens.update(self._table_tokenize(normalized or str(value)))
        self._table_header_token_cache[cache_key] = tokens
        if len(self._table_header_token_cache) > self.table_header_token_cache_limit:
            self._table_header_token_cache.popitem(last=False)
        return tokens

    def _table_generic_tokens_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_generic_token_cache.get(scope_key)
        if cached is not None:
            self._table_generic_token_cache.move_to_end(scope_key)
            return cached
        qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
            upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_generic_token_cache[scope_key] = set()
                return set()
            qs = qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                qs = qs.filter(clause)
        rows = list(
            qs.order_by("-updated_at").values_list(
                "column_schema",
                "title",
                "section_heading",
                "upload__display_name",
                "upload__source_name",
                "upload__external_reference",
            )[: self.table_column_sample_limit]
        )
        total_tables = len(rows)
        if not total_tables:
            self._table_generic_token_cache[scope_key] = set()
            return set()
        required_tables = min(self.table_generic_min_tables, total_tables)
        df: Counter[str] = Counter()
        for column_schema, title, section_heading, display_name, source_name, external_reference in rows:
            tokens: set[str] = set()
            for value in column_schema or []:
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for value in (title, section_heading, display_name, source_name, external_reference):
                if not value:
                    continue
                normalized = normalize_column_name(str(value))
                tokens.update(self._table_tokenize(normalized or str(value)))
            for token in tokens:
                if not token or token.isdigit():
                    continue
                if len(token) < self.table_specific_min_length:
                    continue
                df[token] += 1
        generic: set[str] = set()
        if total_tables:
            for token, count in df.items():
                if count < required_tables:
                    continue
                if (count / total_tables) >= self.table_generic_df_threshold:
                    generic.add(token)
        if self.table_generic_topk > 0 and df:
            # Only treat frequently-occurring tokens as "generic".
            # The previous behavior could swallow rare but critical entity tokens
            # when `table_generic_topk` is large relative to the number of tables.
            for token, count in df.most_common(self.table_generic_topk):
                if count < required_tables:
                    continue
                generic.add(token)
        self._table_generic_token_cache[scope_key] = generic
        if len(self._table_generic_token_cache) > self.table_header_token_cache_limit:
            self._table_generic_token_cache.popitem(last=False)
        return generic

    def _table_query_context(
        self,
        business_profile,
        traits: QueryTraits,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> Mapping[str, object]:
        """
        Build table query context with column/profile metadata.

        Checks Redis cache first to avoid the DB scan penalty. Falls back to DB
        computation if cache misses, then caches the result.
        """

        def _lexicon_values(snapshot: Mapping[str, object], key: str, *, limit: int = 300) -> tuple[str, ...]:
            values = snapshot.get(key) if isinstance(snapshot, Mapping) else None
            if not isinstance(values, (list, tuple, set)):
                return tuple()
            normalized: list[str] = []
            seen: set[str] = set()
            for value in values:
                token = str(value or "").strip().lower()
                if not token or token in seen:
                    continue
                seen.add(token)
                normalized.append(token)
                if len(normalized) >= limit:
                    break
            return tuple(normalized)

        from apps.rag.table_profile_cache import get_table_profile_cache, set_table_profile_cache

        query_text = (traits.normalized or traits.original or "").lower()
        query_tokens, specific_tokens = self._table_query_tokens(
            business_profile,
            traits,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        tokens = set(query_tokens)
        matched_keywords = tokens & self.table_query_keywords

        business_id = getattr(business_profile, "id", None)
        cached_profile = None
        if business_id:
            try:
                cached_profile = get_table_profile_cache(business_id)
            except Exception as exc:
                logger.warning(
                    "table_profile_cache.get_failed business=%s error=%s",
                    business_id,
                    str(exc)[:200],
                )

        if cached_profile:
            _rag_log(
                "table.context.cache_hit",
                {"business": business_id, "cache_keys": list(cached_profile.keys())[:10]},
                indent=2,
                context={"business": business_id},
            )
            columns = set(cached_profile.get("available_columns") or [])
            row_label_tokens = set(cached_profile.get("row_label_tokens") or [])
            table_profile = {
                "table_uploads": cached_profile.get("table_uploads", 0),
                "total_uploads": cached_profile.get("total_uploads", 0),
                "table_upload_ratio": cached_profile.get("table_upload_ratio", 0.0),
                "table_count": cached_profile.get("table_count", 0),
                "dominant": cached_profile.get("dominant", False),
            }
        else:
            _rag_log(
                "table.context.cache_miss",
                {"business": business_id, "will_compute_and_cache": True},
                indent=2,
                context={"business": business_id},
            )

            columns = self._table_columns_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            table_profile = self._table_profile_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            row_label_tokens = self._table_row_label_tokens_for_business(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )

            if business_id:
                try:
                    profile_to_cache = {
                        "available_columns": columns,
                        "row_label_tokens": row_label_tokens,
                        **table_profile,
                    }
                    set_table_profile_cache(business_id, profile_to_cache)
                except Exception as exc:
                    logger.warning(
                        "table_profile_cache.set_failed business=%s error=%s",
                        business_id,
                        str(exc)[:200],
                    )

        hints = self._table_column_hints(business_profile)
        semantic_columns = {column for column in columns if any(hint in column for hint in hints)}
        matched_columns_query_raw = {column for column in columns if column and column in query_text}
        matched_columns_query = {
            column for column in matched_columns_query_raw if self._is_usable_table_query_column_match(column)
        }
        matched_columns_tokens = {column for column in columns if self._column_matches_tokens(column, query_tokens)}
        matched_columns_specific = {column for column in columns if self._column_matches_tokens(column, specific_tokens)}
        matched_row_labels_raw = {token for token in row_label_tokens if token in tokens}
        matched_row_labels_specific = {token for token in matched_row_labels_raw if token in specific_tokens}
        matched_row_labels = (
            matched_row_labels_specific
            if self._has_strong_row_label_signal(
                matched_row_labels=matched_row_labels_specific,
                specific_tokens=specific_tokens,
            )
            else set()
        )
        matched_columns = matched_columns_query or matched_columns_tokens or semantic_columns
        has_currency_token = bool(tokens & {"egp", "usd", "eur", "gbp", "aed", "sar", "qar", "kwd", "bhd", "omr", "jod"})
        has_percent = "%" in query_text
        numeric_table_intent = bool(traits.has_digits and (has_currency_token or has_percent))
        has_intent_pre_classification = bool(
            matched_columns_query
            or matched_columns_specific
            or matched_row_labels
            or numeric_table_intent
        )

        tenant_lexicon_snapshot: Mapping[str, object] = {}
        if self._tenant_lexicon_tables_ready():
            try:
                tenant_lexicon_snapshot = self.tenant_lexicon_service.get_snapshot(
                    business_profile=business_profile,
                    use_cache=True,
                )
            except Exception as exc:  # pragma: no cover - cache/db failures should not block search
                logger.warning(
                    "tenant_lexicon.snapshot_failed business=%s error=%s",
                    business_profile.id if business_profile else None,
                    str(exc)[:240],
                )
                tenant_lexicon_snapshot = {}
        tenant_entity_terms = _lexicon_values(tenant_lexicon_snapshot, "entity_terms")
        tenant_attribute_terms = _lexicon_values(tenant_lexicon_snapshot, "attribute_terms", limit=500)

        query_classifier = QueryClassifier(known_entity_names=list(row_label_tokens)[:100])
        classification = query_classifier.classify(
            traits.original or traits.normalized or query_text,
            context={
                "tenant_id": str(getattr(business_profile, "id", "") or ""),
                "document_entities": list(row_label_tokens)[:100],
                "table_schemas": list(columns)[:50],
                "tenant_entity_terms": tenant_entity_terms,
                "tenant_attribute_terms": tenant_attribute_terms,
            },
        )

        fallback_attempted = False
        fallback_applied = False
        classification.fallback_used = bool(classification.fallback_used)

        should_require_clarification = bool(
            has_intent_pre_classification
            and classification.intent == QueryIntent.EXPLORATORY
            and classification.confidence < self.intent_clarification_threshold
        )
        if should_require_clarification:
            classification.requires_clarification = True
            if classification.intent == QueryIntent.COMPARE and len(classification.entity_names) < 2:
                question = "Which two items should I compare? Please share both names or IDs."
            elif classification.intent == QueryIntent.AGGREGATE and not classification.attributes:
                question = "Which metric should I calculate (for example total count, total amount, or average value)?"
            elif classification.intent == QueryIntent.SPECIFIC_LOOKUP and not classification.entity_names:
                question = "Do you want one specific record or all matching records? Share a name or ID if specific."
            else:
                question = (
                    "Please clarify what to retrieve: a specific record (with name/ID), "
                    "a comparison, or a full list."
                )
            classification.clarification_question = question
        else:
            classification.requires_clarification = False
            classification.clarification_question = ""

        comprehensive_intent = classification.requires_full_coverage()
        generic_intents = {QueryIntent.ENUMERATE, QueryIntent.AGGREGATE, QueryIntent.COMPARE}
        generic_column_signal = bool(
            matched_columns_tokens
            and table_profile.get("dominant")
            and classification.intent in generic_intents
            and classification.confidence >= 0.45
            and not classification.requires_clarification
        )
        row_label_intent = bool(
            matched_row_labels
            and (
                classification.intent != QueryIntent.EXPLORATORY
                or len(matched_row_labels) >= self.table_specific_min_match_count
            )
        )
        has_intent = bool(
            matched_columns_query
            or matched_columns_specific
            or row_label_intent
            or numeric_table_intent
            or generic_column_signal
        )

        legacy_comprehensive_keywords = {"all", "every", "everything", "list", "compare", "comparison", "full", "complete", "entire", "whole", "show"}
        legacy_has_comprehensive_keyword = bool(tokens & legacy_comprehensive_keywords)
        legacy_comprehensive_tokens = tokens & legacy_comprehensive_keywords

        _rag_log(
            "table.comprehensive_detection",
            {
                "query_tokens": list(tokens)[:20],
                "classifier_intent": classification.intent.value,
                "classifier_confidence": round(classification.confidence, 2),
                "classifier_reasoning": classification.reasoning,
                "classifier_source": classification.source,
                "classifier_fallback_attempted": fallback_attempted,
                "classifier_fallback_applied": fallback_applied,
                "classifier_requires_clarification": classification.requires_clarification,
                "comprehensive_intent_result": comprehensive_intent,
                "legacy_comprehensive_tokens": list(legacy_comprehensive_tokens),
                "legacy_has_keyword": legacy_has_comprehensive_keyword,
                "matched_row_labels": list(matched_row_labels)[:10] if matched_row_labels else [],
                "legacy_would_be_comprehensive": legacy_has_comprehensive_keyword and not matched_row_labels,
            },
            indent=2,
            context={"business": business_profile.id if business_profile else None},
        )

        allow_generic = bool(
            table_profile.get("dominant")
            and classification.intent in generic_intents
            and classification.confidence >= 0.45
            and not classification.requires_clarification
        )
        if row_label_intent and not classification.requires_clarification:
            allow_generic = True
        return {
            "has_intent": has_intent,
            "comprehensive_intent": comprehensive_intent,
            "query_classification": classification,
            "intent_source": classification.source,
            "intent_fallback_attempted": fallback_attempted,
            "intent_fallback_applied": fallback_applied,
            "requires_clarification": classification.requires_clarification,
            "clarification_question": classification.clarification_question,
            "tenant_lexicon_entity_terms_count": len(tenant_entity_terms),
            "tenant_lexicon_attribute_terms_count": len(tenant_attribute_terms),
            "matched_columns": matched_columns,
            "matched_columns_query": matched_columns_query,
            "matched_columns_query_raw": matched_columns_query_raw,
            "matched_columns_tokens": matched_columns_tokens,
            "matched_columns_specific": matched_columns_specific,
            "matched_row_labels": matched_row_labels,
            "matched_row_labels_raw": matched_row_labels_raw,
            "row_label_intent": row_label_intent,
            "matched_keywords": matched_keywords,
            "numeric_intent": numeric_table_intent,
            "available_columns": columns,
            "semantic_columns": semantic_columns,
            "matched_column_count": len(matched_columns),
            "query_tokens": query_tokens,
            "specific_tokens": specific_tokens,
            "table_dominant": table_profile.get("dominant"),
            "table_upload_ratio": table_profile.get("table_upload_ratio"),
            "table_count": table_profile.get("table_count"),
            "table_uploads": table_profile.get("table_uploads"),
            "allow_generic": allow_generic,
            "generic_column_signal": generic_column_signal,
        }

    def _table_columns_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_column_cache.get(scope_key)
        if cached is not None:
            self._table_column_cache.move_to_end(scope_key)
            return cached
        columns: set[str] = set()
        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_column_cache[scope_key] = set()
                return set()
            uploads_qs = uploads_qs.filter(id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                uploads_qs = uploads_qs.filter(clause)
        uploads_qs = self._filter_queryable_table_uploads(
            uploads_qs,
            format_lookup="ingestion_metadata__format",
        )
        profiles = list(
            uploads_qs.order_by("-updated_at").values_list("ingestion_metadata__table_profile", flat=True)[
                : self.table_column_sample_limit
            ]
        )
        for profile in profiles:
            if not isinstance(profile, Mapping):
                continue
            for column in profile.get("columns") or []:
                lowered = str(column).strip().lower()
                if lowered:
                    columns.add(lowered)
        if not columns:
            qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
                upload__visibility=KnowledgeVisibility.INTERNAL,
            )
            if allowed_upload_ids is not None:
                qs = qs.filter(upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    qs = qs.filter(clause)
            qs = self._filter_queryable_table_uploads(qs, format_lookup="upload__ingestion_metadata__format")
            qs = qs.order_by("-updated_at").values_list("column_schema", flat=True)[: self.table_column_sample_limit]
            for schema in qs:
                if not isinstance(schema, (list, tuple)):
                    continue
                for column in schema:
                    if not column:
                        continue
                    lowered = str(column).strip().lower()
                    if lowered:
                        columns.add(lowered)
        self._table_column_cache[scope_key] = columns
        if len(self._table_column_cache) > self.table_column_cache_limit:
            self._table_column_cache.popitem(last=False)
        return columns

    def _table_profile_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> dict[str, object]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return {
                "table_uploads": 0,
                "total_uploads": 0,
                "table_upload_ratio": 0.0,
                "table_count": 0,
                "dominant": False,
            }
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_context_cache.get(scope_key)
        if cached is not None:
            self._table_context_cache.move_to_end(scope_key)
            return cached

        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                profile = {
                    "table_uploads": 0,
                    "total_uploads": 0,
                    "table_upload_ratio": 0.0,
                    "table_count": 0,
                    "dominant": False,
                }
                self._table_context_cache[scope_key] = profile
                if len(self._table_context_cache) > self.table_context_cache_limit:
                    self._table_context_cache.popitem(last=False)
                return profile
            uploads_qs = uploads_qs.filter(id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                uploads_qs = uploads_qs.filter(clause)
        uploads_qs = self._filter_queryable_table_uploads(
            uploads_qs,
            format_lookup="ingestion_metadata__format",
        )
        total_uploads = uploads_qs.count()

        table_qs = KnowledgeUploadTable.objects.filter(
            upload__business_profile=business_profile,
        ).exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            table_qs = table_qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                table_qs = table_qs.filter(clause)
        table_qs = self._filter_queryable_table_uploads(
            table_qs,
            format_lookup="upload__ingestion_metadata__format",
        )
        table_uploads = table_qs.values("upload_id").distinct().count()
        table_count = table_qs.count()
        upload_ratio = (table_uploads / total_uploads) if total_uploads else 0.0
        dominant = bool(
            table_count >= self.table_dominant_min_tables
            and upload_ratio >= self.table_dominant_upload_ratio
        )
        profile = {
            "table_uploads": table_uploads,
            "total_uploads": total_uploads,
            "table_upload_ratio": round(upload_ratio, 4),
            "table_count": table_count,
            "dominant": dominant,
        }
        self._table_context_cache[scope_key] = profile
        if len(self._table_context_cache) > self.table_context_cache_limit:
            self._table_context_cache.popitem(last=False)
        return profile

    def _table_row_label_tokens_for_business(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> set[str]:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return set()
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_row_label_cache.get(scope_key)
        if cached is not None:
            self._table_row_label_cache.move_to_end(scope_key)
            return cached
        uploads_qs = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
        ).exclude(visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_row_label_cache[scope_key] = set()
                return set()
            uploads_qs = uploads_qs.filter(id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                uploads_qs = uploads_qs.filter(clause)
        uploads_qs = self._filter_queryable_table_uploads(
            uploads_qs,
            format_lookup="ingestion_metadata__format",
        )
        profiles = list(
            uploads_qs.order_by("-updated_at").values_list("ingestion_metadata__table_profile", flat=True)[
                : self.table_row_label_sample_limit
            ]
        )
        cell_qs = KnowledgeUploadTableCell.objects.filter(
            table__upload__business_profile=business_profile,
            column_index=0,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
        tokens: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, Mapping):
                continue
            for token in profile.get("row_label_tokens") or []:
                cleaned = str(token).strip().lower()
                if cleaned:
                    tokens.add(cleaned)
        if not tokens:
            if allowed_upload_ids is not None:
                cell_qs = cell_qs.filter(table__upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    cell_qs = cell_qs.filter(clause)
            cell_qs = self._filter_queryable_table_uploads(
                cell_qs,
                format_lookup="table__upload__ingestion_metadata__format",
            ).exclude(row__metadata__row_type="header")
            labels = list(
                cell_qs.order_by("-row__updated_at")
                .values_list("raw_text", flat=True)[: self.table_row_label_sample_limit]
            )
            for label in labels:
                if not label:
                    continue
                tokens.update(self._table_tokenize(str(label)))
        self._table_row_label_cache[scope_key] = tokens
        if len(self._table_row_label_cache) > self.table_context_cache_limit:
            self._table_row_label_cache.popitem(last=False)
        return tokens

    def _table_row_result_cap_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve how many table rows we can consider before final ranking.

        Requested `limit` remains the primary driver. Business overrides and
        default caps act as floors for candidate collection, not hard ceilings.
        """
        requested_cap = 0
        if requested is not None:
            try:
                requested_cap = max(1, int(requested))
            except (TypeError, ValueError):
                requested_cap = 0
        base = max(1, int(self.table_result_cap))
        if not business_profile:
            return max(base, requested_cap)
        override = self._business_override(business_profile, "table_results_limit", base)
        try:
            override_cap = max(1, int(override))
        except (TypeError, ValueError):
            override_cap = base
        return max(base, override_cap, requested_cap)

    def _table_ingestion_diagnostics(self, upload: KnowledgeUpload | None) -> dict[str, object]:
        diagnostics: dict[str, object] = {
            "table_truncated": False,
            "total_rows": None,
            "indexed_rows": None,
            "row_cap": None,
            "partial_tables": None,
            "partial_index": False,
            "truncated_rows": None,
            "truncated_columns": None,
            "truncated_tables": None,
        }
        if not upload:
            return diagnostics
        metadata = getattr(upload, "ingestion_metadata", None)
        if isinstance(metadata, Mapping):
            table_stats = metadata.get("table_stats")
            if isinstance(table_stats, Mapping):
                diagnostics["total_rows"] = table_stats.get("total_rows")
                diagnostics["indexed_rows"] = table_stats.get("indexed_rows")
                diagnostics["row_cap"] = table_stats.get("row_cap")
                diagnostics["partial_tables"] = table_stats.get("partial_tables")
                diagnostics["partial_index"] = bool(table_stats.get("partial_index"))
            table_truncation = metadata.get("table_truncation")
            if isinstance(table_truncation, Mapping):
                diagnostics["truncated_rows"] = table_truncation.get("truncated_rows")
                diagnostics["truncated_columns"] = table_truncation.get("truncated_columns")
                diagnostics["truncated_tables"] = table_truncation.get("truncated_tables")
        indexed_rows = self._coerce_int(diagnostics.get("indexed_rows"))
        total_rows = self._coerce_int(diagnostics.get("total_rows"))
        truncated_rows = self._coerce_int(diagnostics.get("truncated_rows"))
        truncated_tables = self._coerce_int(diagnostics.get("truncated_tables"))
        partial_tables = self._coerce_int(diagnostics.get("partial_tables"))
        partial_index = bool(diagnostics.get("partial_index"))
        table_truncated = (
            truncated_rows > 0
            or truncated_tables > 0
            or partial_tables > 0
            or partial_index
            or (indexed_rows and total_rows and indexed_rows < total_rows)
        )
        diagnostics["table_truncated"] = table_truncated
        return diagnostics

    def _table_column_hints(self, business_profile) -> set[str]:
        hints = set(self.table_column_hint_base)
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if isinstance(overrides, dict):
            extra = overrides.get("table_column_hints")
            if isinstance(extra, (list, tuple, set)):
                hints.update(str(item).strip().lower() for item in extra if str(item).strip())
        return hints

    def _filter_queryable_table_uploads(self, qs, *, format_lookup: str):
        """
        Keep rows where the upload format is missing/NULL or not in non-queryable formats.

        We use a positive filter (IS NULL OR NOT IN) instead of exclude(IN) because
        SQL NULL semantics can otherwise drop rows where the JSON key is absent.
        """
        if not self.non_queryable_table_formats:
            return qs
        formats = sorted(self.non_queryable_table_formats)
        return qs.filter(Q(**{f"{format_lookup}__isnull": True}) | ~Q(**{f"{format_lookup}__in": formats}))

    def _business_has_tables(
        self,
        business_profile,
        cached_columns: set[str] | None = None,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> bool:
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return False
        if cached_columns is not None and cached_columns:
            return True
        scope_key = (
            business_id,
            self._upload_scope_token(
                allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            ),
        )
        cached = self._table_presence_cache.get(scope_key)
        if cached is not None:
            self._table_presence_cache.move_to_end(scope_key)
            return cached
        qs = KnowledgeUploadTable.objects.filter(upload__business_profile=business_profile).exclude(
            upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                self._table_presence_cache[scope_key] = False
                self._table_presence_cache.move_to_end(scope_key)
                if len(self._table_presence_cache) > self.table_column_cache_limit:
                    self._table_presence_cache.popitem(last=False)
                return False
            qs = qs.filter(upload_id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                qs = qs.filter(clause)
        qs = self._filter_queryable_table_uploads(qs, format_lookup="upload__ingestion_metadata__format")
        exists = qs.exists()
        self._table_presence_cache[scope_key] = exists
        self._table_presence_cache.move_to_end(scope_key)
        if len(self._table_presence_cache) > self.table_column_cache_limit:
            self._table_presence_cache.popitem(last=False)
        return exists
