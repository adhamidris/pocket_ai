from __future__ import annotations

import logging
import math
import re
from typing import Any, Mapping, Sequence

from apps.accounts.feature_flags import FeatureFlagService
from apps.knowledge.ingestion.contracts import TablePayload, TableRowPayload
from apps.knowledge.models import KnowledgeUpload


logger = logging.getLogger(__name__)


class IngestionTableSelectionMixin:


    @staticmethod
    def _table_row_render_text(row: TableRowPayload) -> str:
        raw = str(getattr(row, "raw_text", "") or "").strip()
        if raw:
            return re.sub(r"\s+", " ", raw).strip()
        values = [str(cell.raw_text or "").strip() for cell in (row.cells or []) if str(cell.raw_text or "").strip()]
        return re.sub(r"\s+", " ", " ".join(values)).strip()

    def _table_collapsed_factual_row_ratio(self, table: TablePayload) -> float:
        readable_rows = self._pdf_table_readable_rows(table)
        if not readable_rows:
            return 0.0
        factual_rows = 0
        for row in readable_rows:
            text = self._table_row_render_text(row)
            if not text:
                continue
            word_count = len(re.findall(r"[A-Za-z0-9\u0600-\u06FF]+", text))
            if word_count < 3 or word_count > 40:
                continue
            if not self._has_numeric_table_signal(text):
                continue
            if not re.search(r"[A-Za-z\u0600-\u06FF]", text):
                continue
            factual_rows += 1
        return round(factual_rows / max(1, len(readable_rows)), 4)

    def _table_is_collapsed_factual(
        self,
        table: TablePayload,
        assessment: Mapping[str, Any] | None = None,
    ) -> bool:
        assessment = assessment if isinstance(assessment, Mapping) else self._assess_table_quality(table)
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        if not isinstance(signals, Mapping):
            signals = {}
        data_row_count = len(self._pdf_table_readable_rows(table))
        if data_row_count < 3:
            return False
        effective_columns = int(signals.get("effective_column_count") or self._table_effective_column_count(table))
        if effective_columns > 2:
            return False
        if bool(signals.get("no_readable_rows")):
            return False
        if bool(signals.get("bridge_like_table")) or bool(signals.get("compact_banner_table")):
            return False
        if float(signals.get("placeholder_cell_ratio") or 0.0) >= 0.55:
            return False
        if bool(signals.get("nonsense_columns")) and float(signals.get("header_confidence") or 0.0) == 0.0:
            return False
        structured_row_ratio = float(signals.get("structured_row_ratio") or 0.0)
        factual_row_ratio = self._table_collapsed_factual_row_ratio(table)
        return structured_row_ratio >= 0.5 and factual_row_ratio >= 0.6

    def _table_text_readability_score(
        self,
        table: TablePayload,
        assessment: Mapping[str, Any] | None = None,
    ) -> float:
        assessment = assessment if isinstance(assessment, Mapping) else self._assess_table_quality(table)
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        if not isinstance(signals, Mapping):
            signals = {}
        score = 1.0
        header_confidence = float(signals.get("header_confidence") or 0.0)
        if bool(signals.get("nonsense_columns")):
            score -= 0.3
        if bool(signals.get("spaced_characters")):
            score -= 0.2
        if bool(signals.get("row_misalignment")):
            score -= 0.2
        if bool(signals.get("fragmented_logical_rows")):
            score -= 0.15
        if header_confidence < 0.3:
            score -= 0.25
        elif header_confidence < 0.5:
            score -= 0.1
        if bool(signals.get("paragraph_like_table")) and not self._table_is_collapsed_factual(table, assessment):
            score -= 0.15
        score += min(0.15, self._table_collapsed_factual_row_ratio(table) * 0.15)
        return round(max(0.0, min(1.0, score)), 4)

    def _table_is_coherent_pdfplumber_candidate(
        self,
        table: TablePayload,
        assessment: Mapping[str, Any] | None = None,
    ) -> bool:
        assessment = assessment if isinstance(assessment, Mapping) else self._assess_table_quality(table)
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        if not isinstance(signals, Mapping):
            signals = {}
        metadata = table.metadata if isinstance(table.metadata, Mapping) else {}
        detected_via = str(metadata.get("detected_via") or "").lower()
        if "pdfplumber" not in detected_via:
            return False
        if self._table_is_collapsed_factual(table, assessment):
            return False
        data_row_count = len(self._pdf_table_readable_rows(table))
        if data_row_count < 5:
            return False
        effective_columns = int(signals.get("effective_column_count") or self._table_effective_column_count(table))
        if effective_columns < 3:
            return False
        if bool(signals.get("no_readable_rows")):
            return False
        if bool(signals.get("bridge_like_table")) or bool(signals.get("compact_banner_table")):
            return False
        if bool(signals.get("header_paragraph_like")):
            return False
        if float(signals.get("placeholder_cell_ratio") or 0.0) >= 0.45:
            return False
        if float(signals.get("scaffold_row_ratio") or 0.0) >= 0.8:
            return False
        if float(signals.get("multi_cell_row_ratio") or 0.0) < 0.35:
            return False
        if float(signals.get("value_row_ratio") or 0.0) < 0.2:
            return False
        readability = self._table_text_readability_score(table, assessment)
        quality = float(assessment.get("quality_score") or 0.0)
        return readability >= 0.82 and quality >= 0.5

    def _table_distinct_row_prefix_count(
        self,
        table: TablePayload,
        *,
        max_tokens: int = 5,
    ) -> int:
        prefixes: set[str] = set()
        for row in self._pdf_table_readable_rows(table):
            text = self._table_row_render_text(row)
            if not text:
                continue
            tokens = [
                token
                for token in re.findall(r"[A-Za-z0-9\u0600-\u06FF%]+", text.lower())
                if len(token) > 1
            ]
            if not tokens:
                continue
            prefix = " ".join(tokens[:max_tokens]).strip()
            if prefix:
                prefixes.add(prefix)
        return len(prefixes)

    def _table_is_collapsed_uniform_value_matrix(
        self,
        table: TablePayload,
        assessment: Mapping[str, Any] | None = None,
    ) -> bool:
        assessment = assessment if isinstance(assessment, Mapping) else self._assess_table_quality(table)
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        if not isinstance(signals, Mapping):
            signals = {}
        metadata = table.metadata if isinstance(table.metadata, Mapping) else {}
        detected_via = str(metadata.get("detected_via") or "").lower()
        if "pdfplumber" not in detected_via:
            return False
        if self._table_is_collapsed_factual(table, assessment):
            return False
        if self._table_is_coherent_pdfplumber_candidate(table, assessment):
            return False
        data_row_count = len(self._pdf_table_readable_rows(table))
        if data_row_count < 8:
            return False
        effective_columns = int(signals.get("effective_column_count") or self._table_effective_column_count(table))
        if effective_columns != 1:
            return False
        if bool(signals.get("no_readable_rows")):
            return False
        if bool(signals.get("bridge_like_table")) or bool(signals.get("compact_banner_table")):
            return False
        if bool(signals.get("header_paragraph_like")):
            return False
        if float(signals.get("placeholder_cell_ratio") or 0.0) >= 0.2:
            return False
        if float(signals.get("structured_row_ratio") or 0.0) < 0.6:
            return False
        if float(signals.get("value_row_ratio") or 0.0) < 0.5:
            return False
        if float(signals.get("row_consistency") or 0.0) < 0.9:
            return False
        if float(signals.get("cell_fill_ratio") or 0.0) < 0.95:
            return False
        if float(signals.get("long_cell_ratio") or 0.0) > 0.25:
            return False
        if int(signals.get("max_cell_word_count") or 0) > 18:
            return False
        if self._table_distinct_row_prefix_count(table) < 6:
            return False
        quality = float(assessment.get("quality_score") or 0.0)
        return quality >= 0.7

    def _estimate_table_structure_confidence(self, table: TablePayload) -> float:
        """
        Best-effort proxy for table structure confidence (0.0-1.0).

        Azure DI tables may carry their own `structure_confidence`. For other extractors
        we derive a conservative estimate from quality signals (row consistency, fill
        ratio, header confidence) and basic collapse indicators (e.g., single-column
        tables with multiple data rows).
        """
        meta = table.metadata if isinstance(table.metadata, Mapping) else {}
        detected_via = str(meta.get("detected_via") or "").lower()

        base = 0.82
        if "azure" in detected_via:
            base = 0.9
        elif "geometry" in detected_via:
            base = 0.86
        elif "pdfplumber" in detected_via:
            base = 0.76
        elif "heuristic" in detected_via:
            base = 0.72

        assessment = self._assess_table_quality(table)
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        signals = signals if isinstance(signals, Mapping) else {}

        def _num(value: Any) -> float:
            if isinstance(value, (int, float)):
                return float(value)
            try:
                return float(str(value))
            except Exception:
                return 0.0

        row_consistency = max(0.0, min(1.0, _num(signals.get("row_consistency"))))
        fill_ratio = max(0.0, min(1.0, _num(signals.get("cell_fill_ratio"))))
        header_confidence = max(0.0, min(1.0, _num(signals.get("header_confidence"))))

        confidence = float(base)
        if row_consistency:
            confidence *= 0.6 + (0.4 * row_consistency)
        if fill_ratio:
            confidence *= 0.65 + (0.35 * fill_ratio)
        confidence *= 0.8 + (0.2 * header_confidence)

        schema_cols = len(table.column_schema or [])
        row_cols = max((len(row.cells or []) for row in (table.rows or [])), default=0)
        columns = max(schema_cols, row_cols)
        data_rows = len([row for row in (table.rows or []) if (row.metadata or {}).get("row_type") != "header"])

        if columns <= 1 and data_rows >= 2:
            confidence = min(confidence, 0.35)
        if signals.get("row_misalignment"):
            confidence = min(confidence, 0.55)
        if signals.get("spaced_characters") or signals.get("nonsense_columns"):
            confidence = min(confidence, 0.45)

        return round(max(0.0, min(1.0, confidence)), 4)

    def _get_table_structure_confidence(self, table: TablePayload) -> float | None:
        meta = table.metadata if isinstance(table.metadata, dict) else None
        if meta is None:
            return None
        existing = meta.get("structure_confidence")
        if isinstance(existing, (int, float)):
            return max(0.0, min(1.0, float(existing)))
        estimated = self._estimate_table_structure_confidence(table)
        meta["structure_confidence"] = estimated
        return estimated

    def _score_table_set(self, tables: Sequence[TablePayload]) -> float:
        if not tables:
            return 0.0
        selection_context = getattr(self, "_active_table_selection_context", None)
        native_text_pdf = bool(
            isinstance(selection_context, Mapping) and selection_context.get("native_text_pdf")
        )
        total_score = 0.0
        for table in tables:
            assessment = self._assess_table_quality(table)
            quality = float(assessment.get("quality_score") or 0.0)
            structure_conf = self._get_table_structure_confidence(table)
            collapsed_factual = self._table_is_collapsed_factual(table, assessment)
            readability = self._table_text_readability_score(table, assessment)
            table_meta = table.metadata if isinstance(table.metadata, Mapping) else {}
            detected_via = str(table_meta.get("detected_via") or "").lower()
            if isinstance(structure_conf, (int, float)):
                structure_multiplier = max(0.2, min(1.0, float(structure_conf)))
                if native_text_pdf and collapsed_factual:
                    structure_multiplier = max(structure_multiplier, 0.72)
                if native_text_pdf and "azure" in detected_via and readability < 0.55:
                    structure_multiplier = min(structure_multiplier, 0.62)
                quality *= structure_multiplier
            signals = assessment.get("signals") or {}
            if isinstance(signals, Mapping) and (
                signals.get("card_mockup")
                or signals.get("card_number_pattern")
                or signals.get("valid_thru")
            ):
                quality = max(0.0, quality - 0.4)
            if native_text_pdf:
                quality *= 0.55 + (0.45 * readability)
                if "pdfplumber" in detected_via and collapsed_factual:
                    quality *= self.native_pdf_pdfplumber_score_boost
                elif "azure" in detected_via and readability < 0.55:
                    quality *= self.native_pdf_low_readability_penalty
            # Align with read_knowledge: "visible body rows" exclude header + section_header.
            data_rows = len(
                [
                    row
                    for row in (table.rows or [])
                    if str((row.metadata or {}).get("row_type") or "").strip().lower()
                    not in {"header", "section_header"}
                ]
            )
            weight = 1.0 + (min(5, data_rows) / 5.0)
            total_score += quality * weight
        return round(total_score, 4)

    def _candidate_selection_metrics(self, tables: Sequence[TablePayload]) -> dict[str, float | int]:
        if not tables:
            return {
                "table_count": 0,
                "total_data_rows": 0,
                "avg_data_rows": 0.0,
                "micro_table_count": 0,
                "micro_table_ratio": 0.0,
                "distinct_title_count": 0,
                "distinct_title_ratio": 0.0,
                "total_bbox_area": 0.0,
                "valid_bbox_count": 0,
            }

        data_rows_per_table: list[int] = []
        micro_table_count = 0
        titles: list[str] = []
        total_bbox_area = 0.0
        valid_bbox_count = 0
        for table in tables:
            data_rows = len(
                [
                    row
                    for row in (table.rows or [])
                    if str((row.metadata or {}).get("row_type") or "").strip().lower()
                    not in {"header", "section_header"}
                ]
            )
            data_rows_per_table.append(data_rows)
            if data_rows <= 2:
                micro_table_count += 1
            normalized_title = self._normalize_evidence_phrase(str(table.title or ""))
            if normalized_title:
                titles.append(normalized_title)
            normalized_bbox = self._normalize_bbox(table.bbox)
            bbox_area = self._bbox_area(normalized_bbox)
            if bbox_area > 0.0:
                total_bbox_area += bbox_area
                valid_bbox_count += 1

        table_count = len(tables)
        total_data_rows = sum(data_rows_per_table)
        distinct_title_count = len(set(titles))
        distinct_title_ratio = (
            (distinct_title_count / table_count) if table_count else 0.0
        )
        micro_table_ratio = (micro_table_count / table_count) if table_count else 0.0
        avg_data_rows = (total_data_rows / table_count) if table_count else 0.0
        return {
            "table_count": table_count,
            "total_data_rows": total_data_rows,
            "avg_data_rows": round(avg_data_rows, 4),
            "micro_table_count": micro_table_count,
            "micro_table_ratio": round(micro_table_ratio, 4),
            "distinct_title_count": distinct_title_count,
            "distinct_title_ratio": round(distinct_title_ratio, 4),
            "total_bbox_area": round(total_bbox_area, 4),
            "valid_bbox_count": valid_bbox_count,
        }

    def _candidate_quality_diagnostics(
        self,
        tables: Sequence[TablePayload],
    ) -> dict[str, float | int]:
        if not tables:
            return {
                "table_count": 0,
                "avg_quality_score": 0.0,
                "avg_readability_score": 0.0,
                "collapsed_factual_count": 0,
                "avg_structure_confidence": 0.0,
            }

        quality_scores: list[float] = []
        readability_scores: list[float] = []
        structure_scores: list[float] = []
        collapsed_count = 0
        for table in tables:
            assessment = self._assess_table_quality(table)
            quality_scores.append(float(assessment.get("quality_score") or 0.0))
            readability_scores.append(self._table_text_readability_score(table, assessment))
            structure_conf = self._get_table_structure_confidence(table)
            if isinstance(structure_conf, (int, float)):
                structure_scores.append(float(structure_conf))
            if self._table_is_collapsed_factual(table, assessment):
                collapsed_count += 1

        def _avg(values: Sequence[float]) -> float:
            if not values:
                return 0.0
            return round(sum(values) / len(values), 4)

        return {
            "table_count": len(tables),
            "avg_quality_score": _avg(quality_scores),
            "avg_readability_score": _avg(readability_scores),
            "collapsed_factual_count": collapsed_count,
            "avg_structure_confidence": _avg(structure_scores),
        }

    def _native_pdf_candidate_rank_scores(
        self,
        *,
        scores: Mapping[str, float],
        metrics: Mapping[str, Mapping[str, Any]],
        quality_diagnostics: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, float]:
        if not scores:
            return {}
        available = [name for name in scores.keys()]
        table_counts = [
            max(1, int((metrics.get(name) or {}).get("table_count") or 0))
            for name in available
        ]
        baseline_table_count = min(table_counts) if table_counts else 1
        best_readability = max(
            (float((quality_diagnostics.get(name) or {}).get("avg_readability_score") or 0.0) for name in available),
            default=0.0,
        )
        ranked_scores: dict[str, float] = {}
        for name in available:
            raw_score = float(scores.get(name) or 0.0)
            table_count = max(1, int((metrics.get(name) or {}).get("table_count") or 0))
            avg_readability = float((quality_diagnostics.get(name) or {}).get("avg_readability_score") or 0.0)
            avg_quality = float((quality_diagnostics.get(name) or {}).get("avg_quality_score") or 0.0)
            collapsed_factual_count = int((quality_diagnostics.get(name) or {}).get("collapsed_factual_count") or 0)
            detected_name = str(name or "").lower()

            adjusted = raw_score / math.sqrt(float(table_count))
            adjusted *= 0.75 + (0.25 * avg_readability)
            adjusted *= 0.8 + (0.2 * avg_quality)

            inflated = table_count >= max(3, int(math.ceil(baseline_table_count * 1.5)))
            readability_gap = best_readability - avg_readability
            if inflated and readability_gap <= 0.04:
                adjusted *= max(0.5, baseline_table_count / float(table_count))

            if collapsed_factual_count > 0:
                adjusted *= 1.0 + min(0.2, collapsed_factual_count * 0.04)

            if "pdfplumber:lines" == detected_name:
                adjusted *= 1.08
            elif "azure" in detected_name and inflated and collapsed_factual_count == 0:
                adjusted *= 0.92

            ranked_scores[name] = round(adjusted, 4)
        return ranked_scores

    def _table_runtime_flags(self, upload: KnowledgeUpload | None) -> dict[str, Any]:
        business = getattr(upload, "business_profile", None) if upload else None
        feature_state = FeatureFlagService.snapshot(business)
        business_metadata: Mapping[str, Any] = {}
        if business is not None and isinstance(getattr(business, "metadata", None), Mapping):
            business_metadata = getattr(business, "metadata") or {}
        cohort = str(business_metadata.get("cohort") or "").strip() or None
        return {
            "shadow_ingestion_enabled": bool(getattr(feature_state, "rag_shadow_ingestion", False)),
            "eval_logging_enabled": bool(getattr(feature_state, "rag_eval_logging", False)),
            "cohort": cohort,
        }

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
