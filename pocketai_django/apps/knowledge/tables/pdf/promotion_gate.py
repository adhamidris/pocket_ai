from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import IssuePayload, TablePayload


class IngestionPdfPromotionGateMixin:

    def _classify_pdf_table_candidate(
        self,
        table: TablePayload,
        assessment: Mapping[str, Any],
        recurrence_stats: Mapping[str, Counter[str]] | None = None,
    ) -> tuple[str, str, list[str]]:
        signals = assessment.get("signals") if isinstance(assessment, Mapping) else {}
        if not isinstance(signals, Mapping):
            signals = {}
        quality = float(assessment.get("quality_score") or 0.0) if isinstance(assessment, Mapping) else 0.0
        effective_columns = int(signals.get("effective_column_count") or self._table_effective_column_count(table))
        reasons: list[str] = []
        first_sig = self._table_row_signature(table, 0)
        second_sig = self._table_row_signature(table, 1)
        first_prefix = self._table_row_prefix_signature(table, 0)
        second_prefix = self._table_row_prefix_signature(table, 1)
        recurring_first = 0
        recurring_second = 0
        recurring_first_prefix = 0
        recurring_second_prefix = 0
        if recurrence_stats:
            recurring_first = int((recurrence_stats.get("first_rows") or {}).get(first_sig, 0)) if first_sig else 0
            recurring_second = int((recurrence_stats.get("second_rows") or {}).get(second_sig, 0)) if second_sig else 0
            recurring_first_prefix = (
                int((recurrence_stats.get("first_row_prefixes") or {}).get(first_prefix, 0))
                if first_prefix
                else 0
            )
            recurring_second_prefix = (
                int((recurrence_stats.get("second_row_prefixes") or {}).get(second_prefix, 0))
                if second_prefix
                else 0
            )
        recurring_signal = max(
            recurring_first,
            recurring_second,
            recurring_first_prefix,
            recurring_second_prefix,
        )
        data_row_count = len(self._pdf_table_readable_rows(table))

        recurring_scaffold = (
            effective_columns <= 2
            and recurring_signal >= self.pdf_table_recurring_scaffold_min_repeats
            and (
                bool(signals.get("nonsense_columns"))
                or float(signals.get("header_confidence") or 0.0) == 0.0
                or quality < 0.9
                or data_row_count <= 12
            )
        )
        if recurring_scaffold:
            reasons.append("recurring_scaffold")
            return "recurring_scaffold", "suppress", reasons

        paragraph_like = bool(signals.get("paragraph_like_table"))
        fragment_like = bool(signals.get("fragmented_logical_rows"))
        micro_fragment = bool(signals.get("micro_fragment_table"))
        bridge_like = bool(signals.get("bridge_like_table"))
        low_structure = bool(signals.get("low_structure_table"))
        compact_banner = bool(signals.get("compact_banner_table"))
        collapsed_factual_keep = self._table_is_collapsed_factual(table, assessment)
        coherent_pdfplumber_keep = self._table_is_coherent_pdfplumber_candidate(table, assessment)
        collapsed_uniform_matrix_keep = self._table_is_collapsed_uniform_value_matrix(table, assessment)
        header_paragraph_like = bool(signals.get("header_paragraph_like"))
        leading_blank_rows = int(signals.get("leading_blank_rows") or 0)
        scaffold_row_ratio = float(signals.get("scaffold_row_ratio") or 0.0)
        placeholder_cell_ratio = float(signals.get("placeholder_cell_ratio") or 0.0)
        multi_cell_row_ratio = float(signals.get("multi_cell_row_ratio") or 0.0)
        value_row_ratio = float(signals.get("value_row_ratio") or 0.0)
        header_confidence = float(signals.get("header_confidence") or 0.0)

        if micro_fragment:
            reasons.append("micro_fragment_table")
            return "layout_fragment", "suppress", reasons

        if data_row_count <= 0 or bool(signals.get("no_readable_rows")):
            reasons.append("no_readable_rows")
            return "layout_fragment", "suppress", reasons

        if header_paragraph_like and data_row_count <= 3 and not collapsed_factual_keep:
            reasons.append("header_paragraph_like")
            return "layout_fragment", "suppress", reasons

        if paragraph_like and not collapsed_factual_keep and (quality <= 0.8 or bool(signals.get("nonsense_columns"))):
            reasons.append("paragraph_like_table")
            if fragment_like:
                reasons.append("fragmented_logical_rows")
            return "layout_fragment", "suppress", reasons

        if bridge_like:
            reasons.append("bridge_like_table")
            if fragment_like:
                reasons.append("fragmented_logical_rows")
            if bool(signals.get("nonsense_columns")):
                reasons.append("nonsense_columns")
            return "layout_fragment", "suppress", reasons

        if low_structure and not collapsed_factual_keep:
            reasons.append("low_structure_table")
            if paragraph_like:
                reasons.append("paragraph_like_table")
            return "layout_fragment", "suppress", reasons

        if compact_banner:
            reasons.append("compact_banner_table")
            if bool(signals.get("nonsense_columns")):
                reasons.append("nonsense_columns")
            return "layout_fragment", "suppress", reasons

        if (
            effective_columns >= 4
            and scaffold_row_ratio >= 0.85
            and placeholder_cell_ratio >= 0.45
        ):
            reasons.append("form_scaffold_table")
            return "layout_fragment", "suppress", reasons

        matrix_like_keep = (
            data_row_count >= 2
            and effective_columns >= 3
            and multi_cell_row_ratio >= 0.5
            and value_row_ratio >= 0.25
            and scaffold_row_ratio < 0.85
        )
        narrow_factual_keep = (
            data_row_count >= 3
            and effective_columns == 2
            and multi_cell_row_ratio >= 0.75
            and value_row_ratio >= 0.4
            and scaffold_row_ratio < 0.75
            and placeholder_cell_ratio < 0.4
            and not header_paragraph_like
        )
        sparse_grid_keep = (
            data_row_count >= 2
            and effective_columns >= 3
            and header_confidence >= 0.5
            and not header_paragraph_like
            and float(signals.get("structured_row_ratio") or 0.0) >= 0.5
            and scaffold_row_ratio <= 0.5
        )
        keep_evidence = (
            matrix_like_keep
            or narrow_factual_keep
            or sparse_grid_keep
            or collapsed_factual_keep
            or coherent_pdfplumber_keep
            or collapsed_uniform_matrix_keep
        )

        if bool(signals.get("insufficient_rows")) and not keep_evidence:
            reasons.append("insufficient_rows")
            return "weak_table", "suppress", reasons

        single_row_bridge = (
            data_row_count <= 2
            and effective_columns >= 3
            and (
                float(signals.get("long_cell_ratio") or 0.0) >= 0.3
                or int(signals.get("max_cell_word_count") or 0) >= 18
            )
            and float(signals.get("short_cell_ratio") or 0.0) >= 0.2
            and quality < 0.85
        )
        if single_row_bridge:
            reasons.append("single_row_bridge")
            return "layout_fragment", "suppress", reasons

        if leading_blank_rows >= self.pdf_table_leading_blank_row_limit and bool(signals.get("column_misalignment")):
            reasons.extend(["leading_blank_rows", "column_misalignment"])
            return "layout_fragment", "suppress", reasons

        if quality < 0.6 and not collapsed_factual_keep and (fragment_like or bool(signals.get("nonsense_columns"))):
            if fragment_like:
                reasons.append("fragmented_logical_rows")
            if signals.get("nonsense_columns"):
                reasons.append("nonsense_columns")
            return "weak_table", "suppress", reasons

        if not keep_evidence:
            reasons.append("insufficient_keep_evidence")
            return "weak_table", "suppress", reasons

        if collapsed_factual_keep and quality >= 0.4:
            reasons.append("collapsed_factual_keep")
            return "collapsed_factual_table", "keep", reasons

        if coherent_pdfplumber_keep and quality >= 0.5:
            reasons.append("coherent_pdfplumber_keep")
            return "coherent_native_text_table", "keep", reasons

        if collapsed_uniform_matrix_keep and quality >= 0.7:
            reasons.append("collapsed_uniform_matrix_keep")
            return "collapsed_uniform_value_matrix", "keep", reasons

        if quality < 0.75:
            reasons.append("low_quality_table")
            return "weak_table", "suppress", reasons

        return "strong_table", "keep", reasons

    def _apply_pdf_table_promotion_gate(
        self,
        tables: Sequence[TablePayload],
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not tables:
            return [], {"enabled": bool(self.pdf_table_promotion_gate_enabled), "input_tables": 0}, []
        if not self.pdf_table_promotion_gate_enabled:
            return list(tables), {"enabled": False, "input_tables": len(tables), "kept_tables": len(tables)}, []

        recurrence_stats = self._build_pdf_table_recurrence_stats(tables)
        kept: list[TablePayload] = []
        issues: list[IssuePayload] = []
        class_counts: Counter[str] = Counter()
        suppressed_examples: list[dict[str, Any]] = []
        evaluated_tables: list[dict[str, Any]] = []

        for table in tables:
            assessment = self._assess_table_quality(table)
            quality_score = float(assessment.get("quality_score") or 0.0)
            candidate_class, decision, reasons = self._classify_pdf_table_candidate(
                table,
                assessment,
                recurrence_stats=recurrence_stats,
            )
            class_counts[candidate_class] += 1
            metadata = dict(table.metadata or {})
            metadata["promotion_class"] = candidate_class
            metadata["promotion_decision"] = decision
            metadata["promotion_quality_score"] = round(quality_score, 4)
            if reasons:
                metadata["promotion_reasons"] = reasons
            updated = TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=metadata,
                rows=table.rows,
            )
            evaluated_tables.append(
                {
                    "table": updated,
                    "candidate_class": candidate_class,
                    "decision": decision,
                    "reasons": list(reasons),
                    "assessment": assessment,
                    "quality_score": quality_score,
                    "data_row_count": len(self._pdf_table_readable_rows(updated)),
                    "column_count": self._table_column_count(updated),
                }
            )
            if decision == "keep":
                kept.append(updated)
                continue

            if len(suppressed_examples) < 8:
                suppressed_examples.append(
                    {
                        "order_index": table.order_index,
                        "page_number": table.page_number,
                        "class": candidate_class,
                        "reasons": reasons,
                        "title": table.title,
                    }
                )
            issues.append(
                IssuePayload(
                    code="pdf_table_suppressed",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description=(
                        f"Suppressed low-value PDF table candidate ({candidate_class}) "
                        f"for page {table.page_number or '?'}."
                    ),
                    page_number=table.page_number,
                    table_order_index=table.order_index,
                    details={"class": candidate_class, "reasons": reasons},
                )
            )

        fallback_kept: dict[str, Any] | None = None
        if not kept and evaluated_tables:
            hard_reject_reasons = {
                "recurring_scaffold",
                "micro_fragment_table",
                "compact_banner_table",
                "form_scaffold_table",
                "bridge_like_table",
                "single_row_bridge",
                "no_readable_rows",
            }

            fallback_candidates = [
                entry
                for entry in evaluated_tables
                if entry["data_row_count"] >= 2
                and entry["column_count"] >= 2
                and float(entry["quality_score"] or 0.0) >= 0.55
                and not (hard_reject_reasons & set(entry["reasons"]))
            ]
            if fallback_candidates:
                fallback_candidates.sort(
                    key=lambda entry: (
                        -float(entry["quality_score"] or 0.0),
                        -int(entry["data_row_count"] or 0),
                        -int(entry["column_count"] or 0),
                        entry["table"].order_index,
                    )
                )
                selected_fallback = fallback_candidates[0]
                fallback_table = selected_fallback["table"]
                fallback_meta = dict(fallback_table.metadata or {})
                fallback_meta["promotion_decision"] = "keep_fallback"
                fallback_meta["promotion_fallback"] = True
                fallback_meta["promotion_fallback_reason"] = "bounded_structured_recovery"
                if selected_fallback["reasons"]:
                    fallback_meta["promotion_fallback_from_reasons"] = list(selected_fallback["reasons"])
                fallback_table = TablePayload(
                    order_index=fallback_table.order_index,
                    title=fallback_table.title,
                    section_heading=fallback_table.section_heading,
                    page_number=fallback_table.page_number,
                    bbox=fallback_table.bbox,
                    column_schema=fallback_table.column_schema,
                    data_dictionary=fallback_table.data_dictionary,
                    metadata=fallback_meta,
                    rows=fallback_table.rows,
                )
                kept.append(fallback_table)
                fallback_kept = {
                    "order_index": fallback_table.order_index,
                    "page_number": fallback_table.page_number,
                    "quality_score": round(float(selected_fallback["quality_score"] or 0.0), 4),
                    "class": selected_fallback["candidate_class"],
                }
                class_counts["fallback_recovered"] += 1
                issues = [
                    issue
                    for issue in issues
                    if not (
                        issue.table_order_index == fallback_table.order_index
                        and issue.page_number == fallback_table.page_number
                    )
                ]
                suppressed_examples = [
                    example
                    for example in suppressed_examples
                    if not (
                        example.get("order_index") == fallback_table.order_index
                        and example.get("page_number") == fallback_table.page_number
                    )
                ]

        metadata = {
            "enabled": True,
            "input_tables": len(tables),
            "kept_tables": len(kept),
            "suppressed_tables": max(0, len(tables) - len(kept)),
            "class_counts": dict(class_counts),
        }
        if fallback_kept:
            metadata["fallback_kept_table"] = fallback_kept
        if suppressed_examples:
            metadata["suppressed_examples"] = suppressed_examples
        return kept, metadata, issues
