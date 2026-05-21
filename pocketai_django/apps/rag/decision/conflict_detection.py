from __future__ import annotations

import re
from typing import Mapping, Sequence

from apps.rag.contracts import KnowledgeSnippet, QueryTraits


class ConflictDetectionMixin:

    @staticmethod
    def _normalize_segment_key(value: object) -> str:
        text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
        return text.strip("_")

    @staticmethod
    def _normalize_conflict_value(value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = text.replace(",", "")
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^a-z0-9\u0600-\u06FF%./:+ -]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text in {"n/a", "na", "-", "none", "null", "not applicable"}:
            return ""
        return text

    def _segment_targets_from_context(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        table_context: Mapping[str, object] | None,
    ) -> tuple[str, ...]:
        known_segments = self._known_segment_keys_for_business(business_profile)
        allow_fallback_tokens = not known_segments
        stop_tokens = self._scope_label_stop_tokens()
        candidates: list[str] = []
        seen: set[str] = set()
        for source in (
            (table_context or {}).get("matched_columns_specific"),
            (table_context or {}).get("matched_columns_tokens"),
            (table_context or {}).get("specific_tokens"),
            traits.tokens,
        ):
            for raw in source or ():
                normalized = self._normalize_segment_key(raw)
                if not normalized:
                    continue
                if known_segments and normalized in known_segments and normalized not in seen:
                    seen.add(normalized)
                    candidates.append(normalized)
                    continue
                if (
                    allow_fallback_tokens
                    and normalized not in seen
                    and normalized not in stop_tokens
                    and len(normalized) > 1
                ):
                    seen.add(normalized)
                    candidates.append(normalized)
        return tuple(candidates)

    def _detect_conflicting_evidence(
        self,
        *,
        snippets: Sequence[KnowledgeSnippet],
        business_profile,
        traits: QueryTraits,
        table_context: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        segment_targets = self._segment_targets_from_context(
            business_profile=business_profile,
            traits=traits,
            table_context=table_context,
        )
        if not segment_targets:
            return None

        segment_keys = self._known_segment_keys_for_business(business_profile)
        conflict_groups: dict[tuple[str, str], dict[str, object]] = {}
        for snippet in snippets[:8]:
            if not snippet.is_table_chunk:
                continue
            diagnostics = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else {}
            text = str(snippet.content or snippet.summary or "").strip()
            if not text:
                continue
            pairs = self._scope_key_value_pairs(text, max_pairs=16)
            pair_map: dict[str, str] = {}
            category = ""
            for key, value in pairs:
                pair_map[key] = value
                if key in {
                    "service",
                    "services",
                    "types_of_services_fee",
                    "types of services fee",
                    "tariff",
                    "tarrif",
                    "subsection",
                    "category",
                    "product",
                    "plan",
                    "account",
                } and value and not category:
                    category = value
            if not category:
                category = str(diagnostics.get("table_title") or snippet.title or "").strip()
            category = self._normalize_topic_value(self._clean_auto_evidence_label(category, max_chars=96))
            if not category:
                continue

            selected_segment = ""
            selected_value = ""
            for segment in segment_targets:
                if segment in pair_map and pair_map[segment]:
                    selected_segment = segment
                    selected_value = pair_map[segment]
                    break
            if not selected_value:
                fee_value = str(diagnostics.get("table_row_fee_value") or "").strip()
                if fee_value and segment_targets:
                    selected_segment = segment_targets[0]
                    selected_value = fee_value
            if not selected_value:
                for key, value in pairs:
                    if self._normalize_segment_key(key) in segment_keys and value:
                        normalized_key = self._normalize_segment_key(key)
                        if normalized_key in segment_targets:
                            selected_segment = normalized_key
                            selected_value = value
                            break
            if not selected_segment or not selected_value:
                continue

            normalized_value = self._normalize_conflict_value(selected_value)
            if not normalized_value:
                continue
            group_key = (selected_segment, category)
            group = conflict_groups.setdefault(
                group_key,
                {"values": {}, "sources": set()},
            )
            values_map = group["values"]
            if isinstance(values_map, dict):
                entry = values_map.setdefault(
                    normalized_value,
                    {
                        "display_value": str(selected_value).strip(),
                        "snippet_ids": [],
                    },
                )
                snippet_ids = entry.get("snippet_ids")
                if isinstance(snippet_ids, list):
                    snippet_ids.append(str(snippet.id))
            sources = group.get("sources")
            if isinstance(sources, set):
                sources.add(str(snippet.id))

        if not conflict_groups:
            return None

        best_conflict: dict[str, object] | None = None
        for (segment, category), payload in conflict_groups.items():
            values_map = payload.get("values")
            sources = payload.get("sources")
            if not isinstance(values_map, dict):
                continue
            if len(values_map) < 2:
                continue
            display_values = [
                str((entry or {}).get("display_value") or normalized).strip()
                for normalized, entry in values_map.items()
            ]
            if len(display_values) < 2:
                continue
            candidate = {
                "segment": segment,
                "category": category,
                "values": display_values[:3],
                "source_count": len(sources) if isinstance(sources, set) else 0,
                "value_count": len(values_map),
            }
            if best_conflict is None:
                best_conflict = candidate
                continue
            if int(candidate["value_count"]) > int(best_conflict.get("value_count") or 0):
                best_conflict = candidate
                continue
            if (
                int(candidate["value_count"]) == int(best_conflict.get("value_count") or 0)
                and int(candidate["source_count"]) > int(best_conflict.get("source_count") or 0)
            ):
                best_conflict = candidate

        return best_conflict

    @staticmethod
    def _build_conflict_clarification_question(conflict: Mapping[str, object] | None) -> str:
        if not isinstance(conflict, Mapping):
            return (
                "I found conflicting values in the retrieved sources. "
                "Do you want me to list all conflicting values with sources?"
            )
        segment = str(conflict.get("segment") or "").replace("_", " ").strip()
        category = str(conflict.get("category") or "").strip()
        values = conflict.get("values")
        rendered_values: list[str] = []
        if isinstance(values, list):
            for raw in values:
                value = str(raw or "").strip()
                if value:
                    rendered_values.append(value)
                if len(rendered_values) >= 2:
                    break
        value_text = " vs ".join(rendered_values) if rendered_values else "different values"
        category_text = category or "this fee item"
        if segment:
            return (
                f"I found conflicting values for {category_text} in the {segment} segment "
                f"({value_text}). Do you want both values with sources or should I narrow by document/date?"
            )
        return (
            f"I found conflicting values for {category_text} ({value_text}). "
            "Do you want both values with sources or should I narrow by document/date?"
        )
