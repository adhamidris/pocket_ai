from __future__ import annotations

import logging
import uuid
from typing import Mapping, Sequence

from apps.rag.contracts import QueryTraits
from apps.rag.query.classifier import QueryClassifier, QueryIntent
from apps.rag.observability.logging import rag_log
from apps.rag.tables.context_support import TableContextSupportMixin
from apps.rag.tables.profile import TableProfileMixin
from apps.rag.tables.query_tokens import TableQueryTokenMixin


logger = logging.getLogger(__name__)


def _rag_log(
    stage: str,
    detail: object | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, object] | None = None,
) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class TableContextMixin(TableQueryTokenMixin, TableProfileMixin, TableContextSupportMixin):
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

        from apps.rag.tables.profile_cache import get_table_profile_cache, set_table_profile_cache

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
