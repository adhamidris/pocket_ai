from __future__ import annotations

import logging
from typing import Any, Mapping

from apps.knowledge.ingestion.contracts import TablePayload
from apps.knowledge.tables.selection_scoring import IngestionTableSelectionScoringMixin


logger = logging.getLogger(__name__)


class IngestionTableSelectionMixin(IngestionTableSelectionScoringMixin):

    def _select_table_candidates(
        self,
        candidates: Mapping[str, list[TablePayload]],
        *,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[str, list[TablePayload], dict[str, Any]]:
        if not candidates:
            return "none", [], {"scores": {}}
        previous_context = getattr(self, "_active_table_selection_context", None)
        self._active_table_selection_context = dict(selection_context or {})
        try:
            scores = {name: self._score_table_set(tables) for name, tables in candidates.items()}
            metrics = {name: self._candidate_selection_metrics(tables) for name, tables in candidates.items()}
            quality_diagnostics = {
                name: self._candidate_quality_diagnostics(tables) for name, tables in candidates.items()
            }
        finally:
            self._active_table_selection_context = previous_context
        preferred = (self.pdf_table_extractor or "auto").strip().lower()
        selection_mode = self.table_selection_mode or "scored_promotion_v2"

        alias_priority = {
            "auto": [
                "pdfplumber:lines",
                "pdfplumber:lines_text",
                "pdfplumber:text_lines",
                "pdfplumber:text",
                "azure:layout",
                "heuristic",
                "geometry",
            ],
            "pdfplumber": [
                "pdfplumber:lines",
                "pdfplumber:lines_text",
                "pdfplumber:text_lines",
                "pdfplumber:text",
            ],
            "azure": ["azure:layout"],
            "geometry": ["geometry"],
            "heuristic": ["heuristic"],
        }

        explicit_variants: list[str] = []
        if preferred in candidates:
            explicit_variants = [preferred]
        elif preferred.startswith("pdfplumber"):
            suffix = preferred.replace("pdfplumber", "").lstrip(":-_")
            explicit_variants = [f"pdfplumber:{suffix}"] if suffix else alias_priority["pdfplumber"]
        elif preferred.startswith("azure"):
            suffix = preferred.replace("azure", "").lstrip(":-_")
            explicit_variants = [f"azure:{suffix}"] if suffix else alias_priority["azure"]
        else:
            explicit_variants = list(alias_priority.get(preferred, []))

        default_chain = list(alias_priority["auto"])
        preference_chain = list(explicit_variants)
        for candidate_name in default_chain:
            if candidate_name not in preference_chain:
                preference_chain.append(candidate_name)

        available_names = sorted(candidates.keys())

        def _preference_rank(name: str) -> int:
            if name in preference_chain:
                return preference_chain.index(name)
            return len(preference_chain) + available_names.index(name)

        if selection_mode == "deterministic_priority_v1":
            selected = next((name for name in explicit_variants if name in candidates), "")
            if not selected:
                selected = next((name for name in default_chain if name in candidates), "")
            if not selected:
                selected = available_names[0]
            ranked_candidates = sorted(available_names, key=lambda name: (_preference_rank(name), name))
            rank_scores = dict(scores)
            selector_disabled = True
        else:
            native_text_pdf = bool(
                isinstance(selection_context, Mapping) and selection_context.get("native_text_pdf")
            )
            if native_text_pdf:
                rank_scores = self._native_pdf_candidate_rank_scores(
                    scores=scores,
                    metrics=metrics,
                    quality_diagnostics=quality_diagnostics,
                )
            else:
                rank_scores = dict(scores)
            ranked_candidates = sorted(
                available_names,
                key=lambda name: (
                    -float(rank_scores.get(name) or 0.0),
                    -int(metrics.get(name, {}).get("total_data_rows") or 0),
                    -float(metrics.get(name, {}).get("avg_data_rows") or 0.0),
                    float(metrics.get(name, {}).get("micro_table_ratio") or 0.0),
                    -float(metrics.get(name, {}).get("distinct_title_ratio") or 0.0),
                    -float(metrics.get(name, {}).get("total_bbox_area") or 0.0),
                    _preference_rank(name),
                    name,
                ),
            )
            selected = ranked_candidates[0]
            selector_disabled = False

        selected_score = round(float(scores.get(selected) or 0.0), 4)
        runner_up = ranked_candidates[1] if len(ranked_candidates) > 1 else None

        selection_meta: dict[str, Any] = {
            "scores": scores,
            "rank_scores": rank_scores,
            "metrics": metrics,
            "quality_diagnostics": quality_diagnostics,
            "selection_mode": selection_mode,
            "preferred_extractor": preferred,
            "fallback_chain": default_chain,
            "selector_disabled": selector_disabled,
            "ranked_candidates": ranked_candidates,
            "selected_score": selected_score,
        }
        if selection_context:
            selection_meta["selection_context"] = dict(selection_context)
        if runner_up:
            selection_meta["runner_up"] = runner_up
            selection_meta["score_margin_to_runner_up"] = round(
                selected_score - float(scores.get(runner_up) or 0.0),
                4,
            )
        logger.info(
            "table.selector.decision selection_mode=%s selected=%s score=%.4f rank_score=%.4f context=%s ranked=%s diagnostics=%s",
            selection_mode,
            selected,
            selected_score,
            float(rank_scores.get(selected) or 0.0),
            dict(selection_context or {}),
            ranked_candidates,
            quality_diagnostics,
        )
        return selected, candidates.get(selected, []), selection_meta
