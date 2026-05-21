from __future__ import annotations

from collections import Counter
import uuid
from typing import Mapping, Sequence

from apps.rag.contracts import ChunkResult


class ScopeSummaryMixin:

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
