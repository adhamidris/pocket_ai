from __future__ import annotations

from typing import Mapping


SCOPE_CATEGORY_MAX_DEFAULT = 40
SCOPE_TOP_CATEGORY_MAX_DEFAULT = 4


class AutoDecisionContractMixin:

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
