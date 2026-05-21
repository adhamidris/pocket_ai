from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence

from django.core.cache import cache

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.knowledge.models import KnowledgeTableColumn, KnowledgeUploadTableRow
from apps.rag.query.normalizer import QueryNormalizer


class ScopeMetadataMixin:

    def _normalized_scope_values(self, value: object) -> set[str]:
        if isinstance(value, Mapping):
            iterable = tuple(value.keys())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            iterable = value
        else:
            iterable = (value,)
        normalized: set[str] = set()
        for raw in iterable:
            cleaned = self._normalize_topic_value(str(raw or ""))
            if cleaned:
                normalized.add(cleaned)
        return normalized

    def _scope_metadata_keysets_for_business(self, business_profile) -> dict[str, set[str]]:
        empty = {
            "scope_generic_tokens": set(),
            "scope_column_keys": set(),
            "known_segment_keys": set(),
            "preferred_value_keys": set(),
        }
        business_id = getattr(business_profile, "id", None)
        if not business_id:
            return empty

        raw_version = cache.get(f"table_profile:ver:{business_id}")
        try:
            version = max(0, int(raw_version or 0))
        except (TypeError, ValueError):
            version = 0
        cache_key = f"rag_scope_keys:{business_id}:v{version}"
        cached = cache.get(cache_key)
        if isinstance(cached, Mapping):
            return {
                "scope_generic_tokens": {
                    str(token).strip().lower()
                    for token in (cached.get("scope_generic_tokens") or ())
                    if str(token).strip()
                },
                "scope_column_keys": {
                    str(token).strip().lower()
                    for token in (cached.get("scope_column_keys") or ())
                    if str(token).strip()
                },
                "known_segment_keys": {
                    str(token).strip().lower()
                    for token in (cached.get("known_segment_keys") or ())
                    if str(token).strip()
                },
                "preferred_value_keys": {
                    str(token).strip().lower()
                    for token in (cached.get("preferred_value_keys") or ())
                    if str(token).strip()
                },
            }

        row_scope_labels: set[str] = set()
        rows = KnowledgeUploadTableRow.objects.filter(
            table__upload__business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL).order_by("-updated_at").values_list(
            "metadata",
            flat=True,
        )[: self.table_row_label_sample_limit]
        for metadata in rows:
            if not isinstance(metadata, Mapping):
                continue
            for key in (
                "scope_dimension_columns",
                "inferred_scope_columns",
                "table_row_scope_dimension_columns",
                "table_row_inferred_scope_columns",
            ):
                row_scope_labels.update(self._normalized_scope_values(metadata.get(key)))

        column_labels = {
            self._normalize_topic_value(column)
            for column in self._table_columns_for_business(business_profile)
            if self._normalize_topic_value(column)
        }
        column_rows = KnowledgeTableColumn.objects.filter(
            business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL).order_by("-updated_at").values_list(
            "column_name",
            "column_normalized",
        )[: self.table_column_sample_limit]
        for column_name, column_normalized in column_rows:
            for raw in (column_normalized, column_name):
                normalized = self._normalize_topic_value(raw)
                if normalized:
                    column_labels.add(normalized)

        scope_column_keys = set(row_scope_labels)
        known_segment_keys = {
            self._normalize_segment_key(label)
            for label in row_scope_labels
            if self._normalize_segment_key(label)
        }

        preferred_value_keys = {
            label for label in column_labels if label and label not in scope_column_keys
        }
        if len(preferred_value_keys) > 64:
            preferred_value_keys = set(sorted(preferred_value_keys)[:64])

        phrase_pool = set(column_labels) | set(scope_column_keys)
        token_counts: Counter[str] = Counter()
        for phrase in phrase_pool:
            tokens = {
                token
                for token in QueryNormalizer._TOKEN_SPLIT.split(phrase)
                if token and len(token) > 1 and not token.isdigit()
            }
            for token in tokens:
                token_counts[token] += 1

        min_frequency = (
            max(2, int(round(len(phrase_pool) * self.table_generic_df_threshold)))
            if phrase_pool
            else 2
        )
        generic_tokens = {token for token, count in token_counts.items() if count >= min_frequency}
        for label in scope_column_keys:
            for token in QueryNormalizer._TOKEN_SPLIT.split(label):
                cleaned = token.strip().lower()
                if cleaned and len(cleaned) > 1 and not cleaned.isdigit():
                    generic_tokens.add(cleaned)

        payload = {
            "scope_generic_tokens": sorted(generic_tokens),
            "scope_column_keys": sorted(scope_column_keys),
            "known_segment_keys": sorted(known_segment_keys),
            "preferred_value_keys": sorted(preferred_value_keys),
        }
        cache.set(cache_key, payload, timeout=900)
        return {
            "scope_generic_tokens": set(payload["scope_generic_tokens"]),
            "scope_column_keys": set(payload["scope_column_keys"]),
            "known_segment_keys": set(payload["known_segment_keys"]),
            "preferred_value_keys": set(payload["preferred_value_keys"]),
        }

    def _scope_generic_tokens_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("scope_generic_tokens") or set())

    def _scope_column_keys_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("scope_column_keys") or set())

    def _known_segment_keys_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("known_segment_keys") or set())

    def _preferred_value_keys_for_business(self, business_profile) -> set[str]:
        return set(self._scope_metadata_keysets_for_business(business_profile).get("preferred_value_keys") or set())
