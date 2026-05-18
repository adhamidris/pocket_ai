from __future__ import annotations

import re
from typing import Mapping, Sequence

from apps.rag.contracts import ChunkResult, KnowledgeSnippet, QueryTraits


SCOPE_CATEGORY_MAX_DEFAULT = 40
SCOPE_TOP_CATEGORY_MAX_DEFAULT = 4


class SearchAutoDecisionMixin:
    @staticmethod
    def _clean_auto_evidence_label(value: object, *, max_chars: int = 72) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" \n\r\t-:|,.;")
        if not text:
            return ""
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
        return text

    def _auto_result_strength(self, hit: ChunkResult) -> float:
        values = [
            self._safe_float(hit.rerank_score),
            self._safe_float(hit.lexical_score),
            self._safe_float(hit.alias_confidence),
            self._safe_float(hit.recency_score),
        ]
        if isinstance(hit.vector_distance, (int, float)):
            values.append(1.0 - float(hit.vector_distance))
        return max(values) if values else 0.0

    def _table_auto_evidence_label(self, hit: ChunkResult, *, query_tokens: Sequence[str]) -> str:
        metadata = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
        diagnostics = hit.diagnostics if isinstance(hit.diagnostics, dict) else {}

        specific_tokens_raw = diagnostics.get("specific_match_tokens")
        header_tokens_raw = diagnostics.get("header_match_tokens")
        token_pool: list[str] = []
        for source in (specific_tokens_raw, header_tokens_raw):
            if isinstance(source, (list, tuple)):
                token_pool.extend(str(item).strip().lower() for item in source if str(item).strip())
        if token_pool:
            preferred: list[str] = []
            token_set = set(token_pool)
            for token in query_tokens:
                lowered = str(token).strip().lower()
                if lowered and lowered in token_set and lowered not in preferred:
                    preferred.append(lowered)
            if not preferred:
                preferred = [token for token in token_pool if token]
            label = " ".join(preferred[:3])
            cleaned = self._clean_auto_evidence_label(label)
            if cleaned:
                return cleaned

        for key in ("row_label", "table_title", "section_heading", "sheet_name"):
            cleaned = self._clean_auto_evidence_label(metadata.get(key))
            if cleaned:
                return cleaned

        content = str(hit.chunk.content or "")
        if content:
            for raw_line in content.splitlines():
                line = self._clean_auto_evidence_label(raw_line)
                if not line:
                    continue
                if line.startswith("["):
                    continue
                if ":" in line:
                    left = self._clean_auto_evidence_label(line.split(":", 1)[0])
                    if left:
                        return left
                return line
        return "table records"

    def _text_auto_evidence_label(self, hit: ChunkResult, *, query_tokens: Sequence[str]) -> str:
        metadata = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
        content = str(hit.chunk.content or "")
        lowered = content.lower()
        normalized_query_tokens = [str(token).strip().lower() for token in query_tokens if str(token).strip()]

        for token in normalized_query_tokens:
            idx = lowered.find(token)
            if idx < 0:
                continue
            start = max(0, idx - 28)
            end = min(len(content), idx + len(token) + 38)
            excerpt = self._clean_auto_evidence_label(content[start:end])
            if excerpt:
                return excerpt

        for key in ("section_heading", "entity_name", "display_name", "title"):
            cleaned = self._clean_auto_evidence_label(metadata.get(key))
            if cleaned:
                return cleaned

        if content:
            for raw_line in content.splitlines():
                line = self._clean_auto_evidence_label(raw_line)
                if line:
                    return line
        return "document text"

    def _build_auto_ambiguity_clarification_question(
        self,
        *,
        hits: Sequence[ChunkResult],
        query_tokens: Sequence[str],
    ) -> tuple[str, str, str]:
        table_hits = [hit for hit in hits if self._chunk_index_type(hit.chunk) == "table"]
        text_hits = [hit for hit in hits if self._chunk_index_type(hit.chunk) != "table"]

        table_label = ""
        text_label = ""
        if table_hits:
            best_table = max(table_hits, key=self._auto_result_strength)
            table_label = self._table_auto_evidence_label(best_table, query_tokens=query_tokens)
        if text_hits:
            best_text = max(text_hits, key=self._auto_result_strength)
            text_label = self._text_auto_evidence_label(best_text, query_tokens=query_tokens)

        if table_label and text_label:
            question = (
                f'I found table evidence around "{table_label}" and text evidence around "{text_label}". '
                "Do you want table-only, text-only, or both?"
            )
            return question, table_label, text_label

        question = (
            "I found relevant evidence in both table data and document text. "
            "Do you want table-only, text-only, or both?"
        )
        return question, table_label, text_label

    def _arbitrate_auto_mode(
        self,
        *,
        scoring_diagnostics: Mapping[str, object] | None = None,
        table_intent_hint: bool = False,
    ) -> dict[str, object]:
        scoring = scoring_diagnostics or {}
        table_score = self._clamp_unit(self._safe_float(scoring.get("auto_table_score")))
        text_score = self._clamp_unit(self._safe_float(scoring.get("auto_text_score")))
        margin = abs(table_score - text_score)

        table_hits = max(0, int(self._safe_float(scoring.get("auto_score_table_hits"))))
        text_hits = max(0, int(self._safe_float(scoring.get("auto_score_text_hits"))))

        both_present = table_hits > 0 and text_hits > 0
        table_strong = table_score >= self.auto_mode_min_score
        text_strong = text_score >= self.auto_mode_min_score
        ambiguous = bool(
            both_present
            and table_strong
            and text_strong
            and margin < self.auto_mode_margin_threshold
        )

        if ambiguous:
            decision = "tie_fallback_to_hint"
            resolved_table_intent = bool(table_intent_hint)
            reason = "score_margin_ambiguous_fallback_to_hint"
            needs_clarification = False
        elif table_score > text_score and margin >= self.auto_mode_margin_threshold:
            decision = "table"
            resolved_table_intent = True
            reason = "score_margin_table"
            needs_clarification = False
        elif text_score > table_score and margin >= self.auto_mode_margin_threshold:
            decision = "text"
            resolved_table_intent = False
            reason = "score_margin_text"
            needs_clarification = False
        else:
            decision = "table_hint" if table_intent_hint else "text_hint"
            resolved_table_intent = bool(table_intent_hint)
            reason = "insufficient_signal_fallback_to_hint"
            needs_clarification = False

        return {
            "auto_arbitration_version": "v1",
            "auto_arbitration_margin_threshold": round(self.auto_mode_margin_threshold, 6),
            "auto_arbitration_min_score": round(self.auto_mode_min_score, 6),
            "auto_arbitration_decision": decision,
            "auto_arbitration_reason": reason,
            "auto_arbitration_needs_clarification": needs_clarification,
            "auto_arbitration_table_intent": resolved_table_intent,
        }

    @classmethod
    def _derive_auto_decision_contract(
        cls,
        *,
        route_diagnostics: Mapping[str, object] | None = None,
        scoring_diagnostics: Mapping[str, object] | None = None,
        requires_clarification: bool = False,
        scope_summary: Mapping[str, object] | None = None,
        conflict_detected: bool | None = None,
        no_result_reason: str | None = None,
    ) -> dict[str, object]:
        route_data = route_diagnostics or {}
        scoring_data = scoring_diagnostics or {}

        try:
            table_hits = max(0, int(route_data.get("index_route_table_hits") or 0))
        except (TypeError, ValueError):
            table_hits = 0
        try:
            text_hits = max(0, int(route_data.get("index_route_text_hits") or 0))
        except (TypeError, ValueError):
            text_hits = 0

        scored_table = scoring_data.get("auto_table_score")
        scored_text = scoring_data.get("auto_text_score")
        table_score = float(scored_table) if isinstance(scored_table, (int, float)) else float(table_hits)
        text_score = float(scored_text) if isinstance(scored_text, (int, float)) else float(text_hits)
        if isinstance(scoring_data.get("auto_score_margin"), (int, float)):
            margin = round(float(scoring_data["auto_score_margin"]), 6)
        else:
            margin = round(abs(table_score - text_score), 6)
        route = str(route_data.get("index_route") or "").strip().lower()
        has_route = bool(route)
        used_scoring = isinstance(scored_table, (int, float)) or isinstance(scored_text, (int, float))

        if requires_clarification:
            decision = "clarification"
        elif used_scoring and margin <= 0.05 and table_score > 0.0 and text_score > 0.0:
            decision = "blended"
        elif used_scoring and table_score > text_score:
            decision = "table"
        elif used_scoring and text_score > table_score:
            decision = "text"
        elif not has_route:
            decision = "undecided"
        elif table_score > text_score:
            decision = "table"
        elif text_score > table_score:
            decision = "text"
        elif route.startswith("mixed"):
            decision = "blended"
        elif route.startswith("table"):
            decision = "table"
        elif route.startswith("text"):
            decision = "text"
        else:
            decision = "undecided"

        normalized_scope_summary = dict(scope_summary) if isinstance(scope_summary, Mapping) else None
        normalized_no_result_reason = (
            str(no_result_reason).strip().lower() if isinstance(no_result_reason, str) and no_result_reason.strip() else None
        )
        normalized_conflict = bool(conflict_detected) if isinstance(conflict_detected, bool) else False
        normalized_categories = list(
            cls._normalize_scope_category_sequence(
                scoring_data.get("categories"),
                max_categories=SCOPE_CATEGORY_MAX_DEFAULT,
            )
        )
        if not normalized_categories:
            ranked_items = cls._rank_scope_category_items(
                scope_summary.get("category_counts")
                if isinstance(scope_summary, Mapping) and isinstance(scope_summary.get("category_counts"), Mapping)
                else None,
                max_categories=SCOPE_CATEGORY_MAX_DEFAULT,
            )
            normalized_categories = [label for label, _count in ranked_items]

        normalized_top_categories = list(
            cls._normalize_scope_category_sequence(
                scoring_data.get("top_categories"),
                max_categories=SCOPE_TOP_CATEGORY_MAX_DEFAULT,
            )
        )
        if normalized_top_categories and normalized_categories:
            category_set = set(normalized_categories)
            normalized_top_categories = [label for label in normalized_top_categories if label in category_set]
        if not normalized_top_categories:
            normalized_top_categories = list(normalized_categories[:SCOPE_TOP_CATEGORY_MAX_DEFAULT])
        raw_ui_mode = (
            scoring_data.get("clarification_ui_mode")
            or route_data.get("clarification_ui_mode")
        )
        normalized_ui_mode = (
            str(raw_ui_mode).strip().lower()
            if isinstance(raw_ui_mode, str) and str(raw_ui_mode).strip()
            else None
        )
        if normalized_ui_mode not in {"text"}:
            normalized_ui_mode = None
        if requires_clarification and not normalized_ui_mode:
            normalized_ui_mode = "text"

        return {
            "table_score": table_score,
            "text_score": text_score,
            "margin": margin,
            "decision": decision,
            "needs_clarification": bool(requires_clarification),
            "scope_summary": normalized_scope_summary,
            "categories": normalized_categories,
            "top_categories": normalized_top_categories,
            "clarification_ui_mode": normalized_ui_mode,
            "conflict_detected": normalized_conflict,
            "no_result_reason": normalized_no_result_reason,
        }

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

    @staticmethod
    def _derive_no_result_reason(
        *,
        diagnostics: Mapping[str, object],
        table_blocked: bool,
    ) -> str:
        if table_blocked or str(diagnostics.get("table_reason") or "").strip().lower() == "specific_tokens_missing":
            return "not_applicable_to_segment"

        def _safe_int(value: object) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        candidate_signals = (
            _safe_int(diagnostics.get("chunk_candidate_count")),
            _safe_int(diagnostics.get("chunk_candidate_count_raw")),
            _safe_int(diagnostics.get("vector_candidates")),
            _safe_int(diagnostics.get("vector_candidates_post_threshold")),
            _safe_int(diagnostics.get("fts_candidates")),
            _safe_int(diagnostics.get("alias_hits")),
        )
        if any(value > 0 for value in candidate_signals):
            return "insufficient_evidence"

        path = str(diagnostics.get("path") or "").strip().lower()
        if path in {"hybrid", "parallel_rrf", "table_direct", "table_blended"}:
            return "insufficient_evidence"
        return "not_found"

    def _apply_phase6_semantics(
        self,
        *,
        status: str,
        snippets: Sequence[KnowledgeSnippet],
        diagnostics: Mapping[str, object],
        business_profile=None,
        traits: QueryTraits,
        table_context: Mapping[str, object] | None,
        table_blocked: bool,
    ) -> tuple[str, tuple[KnowledgeSnippet, ...], dict[str, object]]:
        updated_status = str(status or "not_found").strip().lower() or "not_found"
        updated_snippets = tuple(snippets or ())
        updated_diagnostics: dict[str, object] = dict(diagnostics or {})
        updated_diagnostics.setdefault("conflict_detected", False)
        updated_diagnostics.setdefault("no_result_reason", None)

        conflict_payload: dict[str, object] | None = None
        if (
            updated_status == "ok"
            and updated_snippets
            and not traits.is_identifier_like
        ):
            detected_conflict = self._detect_conflicting_evidence(
                snippets=updated_snippets,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
            )
            if detected_conflict:
                # Conflicts should be non-blocking in agentic RAG. Keep evidence, mark the conflict,
                # and let the assistant explain uncertainty or present both values if needed.
                conflict_payload = dict(detected_conflict)
                updated_diagnostics["conflict_detected"] = True
                updated_diagnostics["conflict_context"] = conflict_payload
                updated_diagnostics["reason"] = "conflicting_evidence"

        no_result_reason = None
        if updated_status == "not_found":
            no_result_reason = self._derive_no_result_reason(
                diagnostics=updated_diagnostics,
                table_blocked=table_blocked,
            )
            updated_diagnostics["no_result_reason"] = no_result_reason

        requires_clarification = bool(updated_status == "needs_clarification")
        scope_summary = (
            updated_diagnostics.get("scope_summary")
            if isinstance(updated_diagnostics.get("scope_summary"), Mapping)
            else None
        )
        categories: tuple[str, ...] = tuple()
        top_categories: tuple[str, ...] = tuple()
        if isinstance(scope_summary, Mapping):
            categories, top_categories = self._scope_categories_for_contract(
                scope_summary=scope_summary,
            )
        if categories and "categories" not in updated_diagnostics:
            updated_diagnostics["categories"] = list(categories)
        if top_categories and "top_categories" not in updated_diagnostics:
            updated_diagnostics["top_categories"] = list(top_categories)
        current_ui_mode = str(updated_diagnostics.get("clarification_ui_mode") or "").strip().lower()
        if requires_clarification and current_ui_mode not in {"text"}:
            updated_diagnostics["clarification_ui_mode"] = "text"
        existing_contract = updated_diagnostics.get("auto_decision_contract")
        if isinstance(existing_contract, Mapping):
            contract = dict(existing_contract)
            if requires_clarification:
                contract["decision"] = "clarification"
            contract["needs_clarification"] = requires_clarification
            contract["scope_summary"] = dict(scope_summary) if isinstance(scope_summary, Mapping) else contract.get("scope_summary")
            contract["categories"] = list(updated_diagnostics.get("categories") or contract.get("categories") or [])
            contract["top_categories"] = list(updated_diagnostics.get("top_categories") or contract.get("top_categories") or [])
            contract_ui_mode = str(updated_diagnostics.get("clarification_ui_mode") or contract.get("clarification_ui_mode") or "").strip().lower()
            contract["clarification_ui_mode"] = contract_ui_mode if contract_ui_mode in {"text"} else None
            contract["conflict_detected"] = bool(updated_diagnostics.get("conflict_detected"))
            contract["no_result_reason"] = (
                str(updated_diagnostics.get("no_result_reason")).strip().lower()
                if str(updated_diagnostics.get("no_result_reason") or "").strip()
                else None
            )
            updated_diagnostics["auto_decision_contract"] = contract
        else:
            updated_diagnostics["auto_decision_contract"] = self._derive_auto_decision_contract(
                route_diagnostics=updated_diagnostics,
                scoring_diagnostics=updated_diagnostics,
                requires_clarification=requires_clarification,
                scope_summary=scope_summary,
                conflict_detected=bool(updated_diagnostics.get("conflict_detected")),
                no_result_reason=str(updated_diagnostics.get("no_result_reason") or "") or None,
            )
        return updated_status, updated_snippets, updated_diagnostics
