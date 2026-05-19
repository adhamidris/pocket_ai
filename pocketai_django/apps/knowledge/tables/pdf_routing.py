from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.contracts import PageLayout, TablePayload
from apps.knowledge.ingestion.pdfplumber import PDFPLUMBER_DEFAULT_TABLE_SETTINGS


logger = logging.getLogger(__name__)


class IngestionPdfTableRoutingMixin:

    @staticmethod
    def _normalize_pdfplumber_settings(raw: Any) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(raw, Mapping):
            raw = [raw]
        settings_list: list[tuple[str, dict[str, Any]]] = []
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for idx, entry in enumerate(raw):
                if not isinstance(entry, Mapping):
                    continue
                label = str(entry.get("label") or entry.get("name") or f"custom_{idx + 1}").strip() or f"custom_{idx + 1}"
                inner = entry.get("settings")
                if isinstance(inner, Mapping):
                    settings = dict(inner)
                else:
                    settings = {k: v for k, v in entry.items() if k not in {"label", "name", "settings"}}
                if settings:
                    settings_list.append((label, settings))
        return settings_list or list(PDFPLUMBER_DEFAULT_TABLE_SETTINGS)



    def _build_table_selection_context(
        self,
        *,
        format_hint: str,
        pages: Sequence[PageLayout] | None = None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {"format_hint": str(format_hint or "").strip().lower()}
        if context["format_hint"] == "pdf":
            native_text_pdf = self._pdf_pages_look_native_text(pages or [])
            context["native_text_pdf"] = native_text_pdf
            context["pdf_lane"] = "native_text" if native_text_pdf else "ocr_or_mixed"
        return context

    @staticmethod
    def _subset_candidate_map(
        candidates: Mapping[str, list[TablePayload]],
        names: Sequence[str],
    ) -> dict[str, list[TablePayload]]:
        subset: dict[str, list[TablePayload]] = {}
        for name in names:
            if name in candidates:
                subset[name] = candidates[name]
        return subset

    def _pdf_route_policy(self, selection_context: Mapping[str, Any] | None) -> dict[str, Any]:
        native_text_pdf = bool(
            isinstance(selection_context, Mapping) and selection_context.get("native_text_pdf")
        )
        if native_text_pdf:
            return {
                "pdf_lane": "native_text",
                "primary_chain": [
                    "pdfplumber:lines",
                    "pdfplumber:lines_text",
                    "pdfplumber:text_lines",
                    "pdfplumber:text",
                ],
                "fallback_chain": [
                    "azure:layout",
                    "geometry",
                    "heuristic",
                ],
            }
        return {
            "pdf_lane": "ocr_or_mixed",
            "primary_chain": [
                "azure:layout",
                "pdfplumber:lines",
                "pdfplumber:lines_text",
                "geometry",
                "heuristic",
            ],
            "fallback_chain": [
                "pdfplumber:text_lines",
                "pdfplumber:text",
            ],
        }

    def _pdf_route_primary_selection_acceptable(
        self,
        *,
        selected_name: str,
        selection_meta: Mapping[str, Any],
        selection_context: Mapping[str, Any] | None,
    ) -> tuple[bool, str]:
        quality_diagnostics = selection_meta.get("quality_diagnostics")
        diagnostics = quality_diagnostics.get(selected_name) if isinstance(quality_diagnostics, Mapping) else {}
        diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
        table_count = int(diagnostics.get("table_count") or 0)
        avg_quality = float(diagnostics.get("avg_quality_score") or 0.0)
        avg_readability = float(diagnostics.get("avg_readability_score") or 0.0)
        collapsed_factual_count = int(diagnostics.get("collapsed_factual_count") or 0)
        lane = str((selection_context or {}).get("pdf_lane") or "")

        if table_count <= 0:
            return False, "no_tables"

        if lane == "native_text":
            if avg_readability < 0.65:
                return False, "readability_below_threshold"
            if avg_quality < 0.5 and collapsed_factual_count <= 0:
                return False, "quality_below_threshold"
            return True, "native_text_primary_acceptable"

        if avg_readability < 0.55 and avg_quality < 0.45:
            return False, "ocr_primary_below_threshold"
        return True, "ocr_primary_acceptable"

    def _route_pdf_table_candidates(
        self,
        candidates: Mapping[str, list[TablePayload]],
        *,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[str, list[TablePayload], dict[str, Any]]:
        if not candidates:
            return "none", [], {"route_applied": False}

        route_policy = self._pdf_route_policy(selection_context)
        primary_candidates = self._subset_candidate_map(candidates, route_policy["primary_chain"])
        fallback_candidates = self._subset_candidate_map(candidates, route_policy["fallback_chain"])

        route_meta: dict[str, Any] = {
            "route_applied": True,
            "pdf_lane": route_policy.get("pdf_lane"),
            "primary_chain": list(route_policy.get("primary_chain") or []),
            "fallback_chain": list(route_policy.get("fallback_chain") or []),
            "primary_candidate_names": list(primary_candidates.keys()),
            "fallback_candidate_names": list(fallback_candidates.keys()),
        }

        lane = str(route_policy.get("pdf_lane") or "")
        primary_attempts: list[dict[str, Any]] = []

        if primary_candidates:
            if lane == "native_text":
                for candidate_name in route_policy.get("primary_chain") or []:
                    if candidate_name not in primary_candidates:
                        continue
                    primary_context = dict(selection_context or {})
                    primary_context["route_stage"] = "primary"
                    primary_context["route_candidate"] = candidate_name
                    selected, tables, selection_meta = self._select_table_candidates(
                        {candidate_name: primary_candidates[candidate_name]},
                        selection_context=primary_context,
                    )
                    acceptable, reason = self._pdf_route_primary_selection_acceptable(
                        selected_name=selected,
                        selection_meta=selection_meta,
                        selection_context=selection_context,
                    )
                    primary_attempts.append(
                        {
                            "candidate": candidate_name,
                            "selected": selected,
                            "acceptable": acceptable,
                            "reason": reason,
                        }
                    )
                    if acceptable:
                        route_meta["primary_selected"] = selected
                        route_meta["primary_acceptance_reason"] = reason
                        route_meta["primary_attempts"] = primary_attempts
                        selection_meta["route"] = route_meta
                        logger.info(
                            "table.route.decision lane=%s selected=%s fallback_triggered=%s reason=%s",
                            route_meta.get("pdf_lane"),
                            selected,
                            False,
                            reason,
                        )
                        return selected, tables, selection_meta
                selected = "none"
                tables = []
                selection_meta = {"scores": {}}
                route_meta["primary_selected"] = None
                route_meta["primary_acceptance_reason"] = (
                    primary_attempts[-1]["reason"] if primary_attempts else "no_primary_candidates"
                )
            else:
                primary_context = dict(selection_context or {})
                primary_context["route_stage"] = "primary"
                selected, tables, selection_meta = self._select_table_candidates(
                    primary_candidates,
                    selection_context=primary_context,
                )
                acceptable, reason = self._pdf_route_primary_selection_acceptable(
                    selected_name=selected,
                    selection_meta=selection_meta,
                    selection_context=selection_context,
                )
                route_meta["primary_selected"] = selected
                route_meta["primary_acceptance_reason"] = reason
                if acceptable:
                    route_meta["primary_attempts"] = [
                        {
                            "candidate": selected,
                            "selected": selected,
                            "acceptable": True,
                            "reason": reason,
                        }
                    ]
                    selection_meta["route"] = route_meta
                    logger.info(
                        "table.route.decision lane=%s selected=%s fallback_triggered=%s reason=%s",
                        route_meta.get("pdf_lane"),
                        selected,
                        False,
                        reason,
                    )
                    return selected, tables, selection_meta
        else:
            selected = "none"
            tables = []
            selection_meta = {"scores": {}}
            route_meta["primary_selected"] = None
            route_meta["primary_acceptance_reason"] = "no_primary_candidates"

        if primary_attempts:
            route_meta["primary_attempts"] = primary_attempts

        if fallback_candidates:
            fallback_context = dict(selection_context or {})
            fallback_context["route_stage"] = "fallback"
            fallback_context["fallback_triggered"] = True
            selected, tables, fallback_meta = self._select_table_candidates(
                fallback_candidates,
                selection_context=fallback_context,
            )
            route_meta["fallback_triggered"] = True
            route_meta["fallback_selected"] = selected
            fallback_meta["route"] = route_meta
            logger.info(
                "table.route.decision lane=%s selected=%s fallback_triggered=%s reason=%s",
                route_meta.get("pdf_lane"),
                selected,
                True,
                route_meta.get("primary_acceptance_reason"),
            )
            return selected, tables, fallback_meta

        route_meta["fallback_triggered"] = False
        selection_meta["route"] = route_meta
        logger.info(
            "table.route.decision lane=%s selected=%s fallback_triggered=%s reason=%s",
            route_meta.get("pdf_lane"),
            selected,
            False,
            route_meta.get("primary_acceptance_reason"),
        )
        return selected, tables, selection_meta
