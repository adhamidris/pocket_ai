from __future__ import annotations

from collections import Counter
import re
import uuid
from typing import Iterable, Mapping, Sequence

from django.core.cache import cache

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.knowledge.models import KnowledgeTableColumn, KnowledgeUploadChunk, KnowledgeUploadTableRow
from apps.rag.contracts import ChunkResult
from apps.rag.query_normalizer import QueryNormalizer
from apps.rag.text_utils import is_plural_candidate, singularize


class SearchAutoScopeMixin:
    @classmethod
    def _scope_key_value_pairs(cls, content: str, *, max_pairs: int = 12) -> tuple[tuple[str, str], ...]:
        text = str(content or "")
        if not text:
            return tuple()
        pairs: list[tuple[str, str]] = []
        window = text[:1600]
        for match in re.finditer(r"([^\n:;|]{1,72})\s*:\s*([^;\n|]{1,220})", window):
            key = cls._normalize_topic_value(match.group(1))
            value = cls._normalize_scope_category_value(
                match.group(2),
                max_chars=120,
            )
            if not key or not value:
                continue
            pairs.append((key, value))
            if len(pairs) >= max_pairs:
                break
        return tuple(pairs)

    @staticmethod
    def _scope_upload_identity(chunk: KnowledgeUploadChunk) -> str:
        upload_id = getattr(chunk, "upload_id", None)
        if upload_id:
            return str(upload_id)
        upload = getattr(chunk, "upload", None)
        if upload is not None and getattr(upload, "id", None):
            return str(upload.id)
        return ""

    @classmethod
    def _scope_label_stop_tokens(cls) -> set[str]:
        return {
            "and",
            "or",
            "for",
            "from",
            "to",
            "in",
            "on",
            "with",
            "by",
            "the",
            "a",
            "an",
            "و",
            "او",
            "أو",
            "من",
            "في",
            "على",
            "الى",
            "إلى",
            "عن",
            "ال",
            "en",
            "ar",
            "fr",
            "de",
            "es",
            "it",
            "pt",
            "ru",
            "tr",
            "zh",
            "ja",
        }

    @staticmethod
    def _canonical_scope_token(value: object) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return ""
        if is_plural_candidate(token):
            token = singularize(token)
        return token.strip()

    @classmethod
    def _canonical_scope_token_set(cls, values: Iterable[object]) -> set[str]:
        canonical: set[str] = set()
        for raw in values:
            token = cls._canonical_scope_token(raw)
            if token:
                canonical.add(token)
        return canonical

    @classmethod
    def _normalize_scope_category_value(
        cls,
        value: object,
        *,
        max_chars: int = 96,
    ) -> str:
        """
        Normalize candidate scope labels into a stable, user-facing category key.
        """
        cleaned = cls._clean_auto_evidence_label(value, max_chars=max_chars)
        if not cleaned:
            return ""

        normalized = cls._normalize_topic_value(cleaned)
        if not normalized:
            return ""

        normalized = re.sub(r"\b(?:table|sheet|tab)\s*\d*\b", " ", normalized)
        normalized = re.sub(r"\b([a-z]{3,}s)(?:en|ar|fr|de|es)\b", r"\1", normalized)
        normalized = re.sub(r"[^0-9a-z\u0600-\u06FF]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return ""

        stop_tokens = cls._scope_label_stop_tokens()
        tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if token]
        selected: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            lowered = token.strip().lower()
            if not lowered:
                continue
            if lowered in stop_tokens:
                continue
            if len(lowered) == 1 and not lowered.isdigit():
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            selected.append(lowered)
            if len(selected) >= 10:
                break

        if not selected:
            return normalized
        return " ".join(selected)

    def _scope_is_specific_category(
        self,
        label: str,
        *,
        business_profile,
        query_tokens: set[str],
        filler_tokens: set[str],
    ) -> bool:
        normalized = self._normalize_topic_value(label)
        if not normalized:
            return False
        raw_tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if token]
        if not raw_tokens:
            return False
        canonical_filler_tokens = self._canonical_scope_token_set(filler_tokens)
        canonical_generic_tokens = self._canonical_scope_token_set(
            self._scope_generic_tokens_for_business(business_profile),
        )
        canonical_query_tokens = self._canonical_scope_token_set(query_tokens)

        tokens: list[str] = []
        for token in raw_tokens:
            canonical = self._canonical_scope_token(token)
            if not canonical or canonical in canonical_filler_tokens:
                continue
            tokens.append(canonical)
        if not tokens:
            return False
        non_generic = [token for token in tokens if token not in canonical_generic_tokens]
        if not non_generic:
            return False
        if len(non_generic) <= 2 and all(token in canonical_query_tokens for token in non_generic):
            return False
        return True

    def _scope_categories_for_contract(
        self,
        *,
        scope_summary: Mapping[str, object] | None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        categories_map = (
            scope_summary.get("category_counts")
            if isinstance(scope_summary, Mapping)
            and isinstance(scope_summary.get("category_counts"), Mapping)
            else None
        )
        ranked_items = self._rank_scope_category_items(
            categories_map,
            max_categories=self.scope_category_max,
        )
        categories = tuple(label for label, _count in ranked_items)
        top_categories = categories[: self.scope_top_category_max]
        return categories, top_categories

    @staticmethod
    def _scope_ref_uuid_string(value: object) -> str:
        if isinstance(value, uuid.UUID):
            return str(value)
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return str(uuid.UUID(text))
        except (TypeError, ValueError):
            return ""

    def _scope_category_ref_hints_from_candidates(
        self,
        *,
        hits: Sequence[ChunkResult],
        business_profile,
        top_categories: Sequence[str],
        query_tokens: Sequence[str] | None = None,
        filler_tokens: set[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        normalized_top_categories = self._normalize_scope_category_sequence(
            top_categories,
            max_categories=self.scope_top_category_max,
        )
        if not normalized_top_categories:
            return {}

        normalized_query_tokens = {
            str(token).strip().lower()
            for token in (query_tokens or ())
            if str(token).strip()
        }
        normalized_filler_tokens = {
            str(token).strip().lower()
            for token in (filler_tokens or set())
            if str(token).strip()
        }

        category_lookup: dict[str, str] = {}
        for category in normalized_top_categories:
            normalized = self._normalize_scope_category_value(category, max_chars=96)
            if normalized:
                category_lookup[normalized] = category
        if not category_lookup:
            return {}

        per_category_refs: dict[str, list[str]] = {category: [] for category in normalized_top_categories}
        per_category_seen: dict[str, set[str]] = {category: set() for category in normalized_top_categories}
        max_refs_per_category = max(1, int(self.scope_category_ref_max))

        for hit in hits:
            label = self._scope_category_from_hit(
                hit,
                business_profile=business_profile,
                query_tokens=normalized_query_tokens,
                filler_tokens=normalized_filler_tokens,
            )
            normalized_label = self._normalize_scope_category_value(label, max_chars=96)
            canonical_label = category_lookup.get(normalized_label)
            if not canonical_label:
                continue

            ref_uuid = self._scope_ref_uuid_string(getattr(hit, "chunk_id", None))
            if not ref_uuid:
                ref_uuid = self._scope_ref_uuid_string(getattr(getattr(hit, "chunk", None), "id", None))
            if not ref_uuid:
                continue

            seen_ids = per_category_seen.get(canonical_label)
            ref_list = per_category_refs.get(canonical_label)
            if seen_ids is None or ref_list is None:
                continue
            if ref_uuid in seen_ids or len(ref_list) >= max_refs_per_category:
                continue
            seen_ids.add(ref_uuid)
            ref_list.append(ref_uuid)

            if all(len(refs) >= max_refs_per_category for refs in per_category_refs.values()):
                break

        out: dict[str, dict[str, object]] = {}
        for category in normalized_top_categories:
            refs = per_category_refs.get(category) or []
            if refs:
                out[category] = {
                    "ref_ids": refs,
                    "source": "retrieval_candidates",
                }
            else:
                out[category] = {"fallback": "scoped_search"}
        return out

    @classmethod
    def _rank_scope_category_items(
        cls,
        category_counts: Mapping[str, object] | None,
        *,
        max_categories: int,
    ) -> tuple[tuple[str, int], ...]:
        if not isinstance(category_counts, Mapping):
            return tuple()

        limit = max(1, int(max_categories))

        def _coerce_count(raw: object) -> int:
            try:
                coerced = int(raw)
            except (TypeError, ValueError):
                coerced = 1
            return coerced if coerced > 0 else 0

        merged_counts: dict[str, int] = {}
        for raw_label, raw_count in category_counts.items():
            normalized_label = cls._normalize_scope_category_value(raw_label, max_chars=96)
            if not normalized_label:
                continue
            count_value = _coerce_count(raw_count)
            if count_value <= 0:
                continue
            merged_counts[normalized_label] = merged_counts.get(normalized_label, 0) + count_value

        if not merged_counts:
            return tuple()

        ordered_items = sorted(
            merged_counts.items(),
            key=lambda item: (-int(item[1]), item[0]),
        )
        return tuple((label, int(count)) for label, count in ordered_items[:limit])

    @classmethod
    def _normalize_scope_category_sequence(
        cls,
        values: object,
        *,
        max_categories: int,
    ) -> tuple[str, ...]:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            return tuple()
        limit = max(1, int(max_categories))
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            label = cls._normalize_scope_category_value(raw, max_chars=96)
            if not label or label in seen:
                continue
            seen.add(label)
            normalized.append(label)
            if len(normalized) >= limit:
                break
        return tuple(normalized)

    def _scope_category_from_hit(
        self,
        hit: ChunkResult,
        *,
        business_profile,
        query_tokens: set[str],
        filler_tokens: set[str],
    ) -> str:
        chunk = hit.chunk
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        diagnostics = hit.diagnostics if isinstance(hit.diagnostics, dict) else {}

        metadata_candidates: list[object] = []
        for key in ("row_label", "table_title", "section_heading", "entity_name", "display_name", "title", "sheet_name"):
            metadata_candidates.append(metadata.get(key))
        for key in ("table_title", "column_key", "value"):
            metadata_candidates.append(diagnostics.get(key))

        for candidate in metadata_candidates:
            normalized = self._normalize_scope_category_value(candidate, max_chars=96)
            if self._scope_is_specific_category(
                normalized,
                business_profile=business_profile,
                query_tokens=query_tokens,
                filler_tokens=filler_tokens,
            ):
                return normalized

        content = str(chunk.content or "")
        pairs = self._scope_key_value_pairs(content)
        preferred_value_keys = self._preferred_value_keys_for_business(business_profile)
        scope_column_keys = self._scope_column_keys_for_business(business_profile)
        fallback_values: list[str] = []
        for key, value in pairs:
            if key in scope_column_keys:
                continue
            if not self._scope_is_specific_category(
                value,
                business_profile=business_profile,
                query_tokens=query_tokens,
                filler_tokens=filler_tokens,
            ):
                continue
            if key in preferred_value_keys:
                return value
            fallback_values.append(value)
        if fallback_values:
            return fallback_values[0]

        for raw_line in content.splitlines():
            line = self._normalize_scope_category_value(raw_line, max_chars=96)
            if self._scope_is_specific_category(
                line,
                business_profile=business_profile,
                query_tokens=query_tokens,
                filler_tokens=filler_tokens,
            ):
                return line
        return ""

    def _build_scope_summary_from_candidates(
        self,
        hits: Sequence[ChunkResult],
        *,
        business_profile=None,
        query_tokens: Sequence[str] | None = None,
        filler_tokens: set[str] | None = None,
    ) -> dict[str, object]:
        total_matches = len(hits)
        if not hits:
            return {
                "total_matches": 0,
                "distinct_docs": 0,
                "category_counts": {},
                "is_broad_scope": False,
            }

        normalized_query_tokens = {
            str(token).strip().lower()
            for token in (query_tokens or ())
            if str(token).strip()
        }
        normalized_filler_tokens = {
            str(token).strip().lower()
            for token in (filler_tokens or set())
            if str(token).strip()
        }
        doc_ids: set[str] = set()
        category_counts: Counter[str] = Counter()
        for hit in hits:
            doc_id = self._scope_upload_identity(hit.chunk)
            if doc_id:
                doc_ids.add(doc_id)
            label = self._scope_category_from_hit(
                hit,
                business_profile=business_profile,
                query_tokens=normalized_query_tokens,
                filler_tokens=normalized_filler_tokens,
            )
            if label:
                category_counts[label] += 1

        ranked_categories = self._rank_scope_category_items(
            category_counts,
            max_categories=self.scope_category_max,
        )
        distinct_docs = len(doc_ids)
        distinct_categories = len(category_counts)
        is_broad_scope = bool(
            total_matches >= 6
            and distinct_docs >= 2
            and distinct_categories >= 3
        )
        return {
            "total_matches": int(total_matches),
            "distinct_docs": int(distinct_docs),
            "category_counts": {label: int(count) for label, count in ranked_categories},
            "is_broad_scope": is_broad_scope,
        }

    def _normalize_scope_summary(
        self,
        value: object,
    ) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None

        def _coerce_int(raw: object) -> int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        raw_counts = value.get("category_counts")
        ranked_counts = self._rank_scope_category_items(
            raw_counts if isinstance(raw_counts, Mapping) else None,
            max_categories=self.scope_category_max,
        )
        total_matches = max(0, _coerce_int(value.get("total_matches")))
        distinct_docs = max(0, _coerce_int(value.get("distinct_docs")))
        if distinct_docs == 0 and total_matches > 0:
            distinct_docs = 1
        is_broad_scope = bool(value.get("is_broad_scope"))
        return {
            "total_matches": total_matches,
            "distinct_docs": distinct_docs,
            "category_counts": {label: int(count) for label, count in ranked_counts},
            "is_broad_scope": is_broad_scope,
        }

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
