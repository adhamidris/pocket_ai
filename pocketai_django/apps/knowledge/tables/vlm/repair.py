from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import IssuePayload, TablePayload
from apps.knowledge.tables.vlm.guardrails import IngestionTableVlmGuardrailsMixin
from apps.knowledge.tables.vlm.payload import IngestionTableVlmPayloadMixin, fitz


logger = logging.getLogger(__name__)


class IngestionTableVlmRepairMixin(
    IngestionTableVlmGuardrailsMixin,
    IngestionTableVlmPayloadMixin,
):



    def _repair_tables_with_vlm(
        self,
        path: Path,
        tables: list[TablePayload],
    ) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        if not self.table_vlm_enabled or not tables:
            return tables, [], {}
        if fitz is None:
            return tables, [
                IssuePayload(
                    code="table_vlm_no_renderer",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="VLM repair skipped because PyMuPDF is unavailable.",
                )
            ], {}

        candidates: list[tuple[int, float, bool]] = []
        for idx, table in enumerate(tables):
            structure_conf = self._get_table_structure_confidence(table)
            
            # Check for column misalignment (empty header cells with non-empty data)
            has_column_misalignment = False
            header_cells: list[str] = []
            for row in (table.rows or []):
                if (row.metadata or {}).get("row_type") == "header":
                    header_cells = [str(cell.raw_text or "") for cell in (row.cells or [])]
                    break
            
            if header_cells:
                data_rows = [r for r in (table.rows or []) if (r.metadata or {}).get("row_type") != "header"][:5]
                for col_idx, header_val in enumerate(header_cells):
                    if not str(header_val or "").strip():  # Empty header
                        for row in data_rows:
                            for cell in (row.cells or []):
                                if cell.column_index == col_idx:
                                    cell_text = str(cell.raw_text or "").strip()
                                    if cell_text and len(cell_text) > 2:
                                        has_column_misalignment = True
                                        break
                            if has_column_misalignment:
                                break
                    if has_column_misalignment:
                        break
            
            # Trigger VLM repair if confidence is low OR column misalignment detected
            if isinstance(structure_conf, (int, float)) and structure_conf >= self.table_vlm_confidence_threshold:
                if not has_column_misalignment:
                    continue
                # Log that we're triggering repair due to column misalignment
                logger.info(
                    "table.vlm.triggered_by_misalignment table=%s conf=%s",
                    table.order_index,
                    structure_conf,
                )
            
            # Only require page_number - we'll fallback to full page if bbox is missing
            if not table.page_number:
                continue
            candidates.append(
                (
                    idx,
                    float(structure_conf) if isinstance(structure_conf, (int, float)) else 0.0,
                    has_column_misalignment,
                )
            )

        if not candidates:
            return tables, [], {
                "attempted": 0,
                "repaired": 0,
                "rejected": 0,
                "skipped": len(tables),
                "model": self.table_vlm_model,
                "guardrails_enabled": self.table_vlm_guardrails_enabled,
            }

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return tables, [
                IssuePayload(
                    code="table_vlm_missing_key",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="OPENAI_API_KEY not configured; skipping VLM repair.",
                )
            ], {}

        try:
            from openai import OpenAI
        except Exception as exc:
            return tables, [
                IssuePayload(
                    code="table_vlm_missing_client",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description=f"OpenAI client unavailable: {exc}",
                )
            ], {}

        client = OpenAI(api_key=api_key)
        repaired: list[TablePayload] = list(tables)
        issues: list[IssuePayload] = []
        meta: dict[str, Any] = {
            "attempted": 0,
            "repaired": 0,
            "rejected": 0,
            "model": self.table_vlm_model,
            "guardrails_enabled": self.table_vlm_guardrails_enabled,
            "guardrail_version": "v2",
        }
        remaining_budget = max(0, self.table_vlm_max_repairs)

        def _repair_sort_key(item: tuple[int, float, bool]) -> tuple[int, float, int, int]:
            index, conf, misaligned = item
            table = tables[index]
            data_rows = len(
                [row for row in (table.rows or []) if (row.metadata or {}).get("row_type") != "header"]
            )
            columns = max(len(table.column_schema or []), max((len(r.cells or []) for r in (table.rows or [])), default=0))
            # Prioritize misaligned tables first (they cause wrong value attribution),
            # then prioritize by low confidence, then larger tables.
            return (0 if misaligned else 1, conf, -data_rows, -columns)

        def _table_hint(table: TablePayload) -> str:
            parts: list[str] = []
            if table.title:
                parts.append(f"Title: {table.title}")
            if table.section_heading:
                parts.append(f"Section: {table.section_heading}")
            # Use non-empty column headers as the primary anchor for full-page extraction.
            schema = [str(col or '').strip() for col in (table.column_schema or []) if str(col or '').strip()]
            if schema:
                parts.append("Columns: " + " | ".join(schema[:10]))
            # Add a few row-label anchors (first column of early rows).
            labels: list[str] = []
            for row in (table.rows or []):
                if (row.metadata or {}).get("row_type") == "header":
                    continue
                first_cell = None
                for cell in (row.cells or []):
                    if cell.column_index == 0:
                        first_cell = cell
                        break
                raw = str(getattr(first_cell, "raw_text", "") or "").strip() if first_cell else str(row.raw_text or "").strip()
                if raw:
                    labels.append(raw)
                if len(labels) >= 5:
                    break
            if labels:
                parts.append("Row labels (examples): " + " | ".join(labels))
            parts.append(f"Order index: {table.order_index} (page {table.page_number})")
            return "\n".join(parts).strip()

        for idx, conf, _misaligned in sorted(candidates, key=_repair_sort_key):
            if remaining_budget <= 0:
                break
            table = tables[idx]
            repair_reason = "misalignment" if _misaligned else "low_confidence"

            # Try to crop table region, fallback to full page if bbox is missing
            crop_bytes = self._render_table_crop(path, int(table.page_number), table.bbox)
            render_mode = "crop"
            if not crop_bytes:
                # Fallback: render full page when bbox is missing/invalid
                crop_bytes = self._render_full_page(path, int(table.page_number))
                render_mode = "full_page"
                if crop_bytes:
                    logger.info(
                        "table.vlm.fallback_full_page table=%s page=%s reason=bbox_missing",
                        table.order_index,
                        table.page_number,
                    )
            if not crop_bytes:
                continue

            meta["attempted"] += 1
            remaining_budget -= 1
            # Use a strong hint for full-page extraction (title + columns + row labels).
            table_hint = _table_hint(table)
            attempted_modes: list[str] = [render_mode]
            payload = self._run_vlm_table_repair(
                client, crop_bytes, render_mode=render_mode, table_hint=table_hint
            )
            if not payload and render_mode == "crop":
                retry_bytes = self._render_full_page(path, int(table.page_number))
                if retry_bytes:
                    attempted_modes.append("full_page")
                    logger.info(
                        "table.vlm.retry_full_page_after_crop_failure table=%s page=%s",
                        table.order_index,
                        table.page_number,
                    )
                    payload = self._run_vlm_table_repair(
                        client,
                        retry_bytes,
                        render_mode="full_page",
                        table_hint=table_hint,
                    )
                    if payload:
                        render_mode = "full_page_retry"
            if not payload:
                issues.append(
                    IssuePayload(
                        code="table_vlm_failed",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"VLM repair failed for table {table.order_index}.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={
                            "structure_confidence": conf,
                            "render_mode": render_mode,
                            "attempted_modes": attempted_modes,
                        },
                    )
                )
                continue

            vlm_table = self._table_payload_from_vlm(
                payload=payload,
                order_index=table.order_index,
                page_number=int(table.page_number),
                bbox=table.bbox,
                title=table.title,
                section_heading=table.section_heading,
                source_metadata=table.metadata,
            )
            if vlm_table:
                accepted, diagnostics, guarded_table = self._evaluate_vlm_guardrails(
                    baseline=table,
                    candidate=vlm_table,
                )
                diagnostics_record = {
                    "order_index": table.order_index,
                    "page_number": table.page_number,
                    "reason": repair_reason,
                    "render_mode": render_mode,
                    **diagnostics,
                }
                meta.setdefault("guardrail_diagnostics", []).append(diagnostics_record)

                # Crop extracts can be partial on dense PDFs. If the first candidate is rejected,
                # run a single full-page retry before final rejection.
                #
                # However, not every guardrail rejection is likely to be fixed by switching to
                # a full-page render. Only retry when the rejection indicates missing coverage
                # (rows/schema/values/scope), or when this repair was triggered by misalignment.
                if not accepted and render_mode == "crop":
                    retry_reasons = set(diagnostics.get("rejection_reasons") or [])
                    coverage_retry_reasons = {
                        "row_coverage_regression",
                        "schema_coverage_regression",
                        "value_coverage_regression",
                        "scope_row_regression",
                        "scope_quality_regression",
                    }
                    should_retry_full_page = bool(retry_reasons & coverage_retry_reasons) or (
                        repair_reason == "misalignment"
                    )
                    if not should_retry_full_page:
                        logger.info(
                            "table.vlm.skip_full_page_retry_after_guardrail_rejection table=%s page=%s reasons=%s",
                            table.order_index,
                            table.page_number,
                            ",".join(sorted(retry_reasons)),
                        )
                        # Fall through to rejection handling below.
                    else:
                        retry_bytes = self._render_full_page(path, int(table.page_number))
                        if retry_bytes:
                            attempted_modes.append("full_page")
                            logger.info(
                                "table.vlm.retry_full_page_after_guardrail_rejection table=%s page=%s reasons=%s",
                                table.order_index,
                                table.page_number,
                                ",".join(diagnostics.get("rejection_reasons") or []),
                            )
                            retry_payload = self._run_vlm_table_repair(
                                client,
                                retry_bytes,
                                render_mode="full_page",
                                table_hint=table_hint,
                            )
                            if retry_payload:
                                retry_table = self._table_payload_from_vlm(
                                    payload=retry_payload,
                                    order_index=table.order_index,
                                    page_number=int(table.page_number),
                                    bbox=table.bbox,
                                    title=table.title,
                                    section_heading=table.section_heading,
                                    source_metadata=table.metadata,
                                )
                                if retry_table:
                                    retry_accepted, retry_diagnostics, retry_guarded_table = (
                                        self._evaluate_vlm_guardrails(
                                            baseline=table,
                                            candidate=retry_table,
                                        )
                                    )
                                    retry_record = {
                                        "order_index": table.order_index,
                                        "page_number": table.page_number,
                                        "reason": repair_reason,
                                        "render_mode": "full_page_retry",
                                        **retry_diagnostics,
                                    }
                                    meta.setdefault("guardrail_diagnostics", []).append(retry_record)
                                    if retry_accepted:
                                        accepted = True
                                        diagnostics = retry_diagnostics
                                        guarded_table = retry_guarded_table
                                        render_mode = "full_page_retry"
                                    else:
                                        diagnostics = retry_diagnostics
                                        render_mode = "full_page_retry"

                if not accepted:
                    meta["rejected"] += 1
                    meta.setdefault("rejected_tables", []).append(
                        {
                            "order_index": table.order_index,
                            "page_number": table.page_number,
                            "reason": repair_reason,
                            "render_mode": render_mode,
                            "rejection_reasons": diagnostics.get("rejection_reasons") or [],
                        }
                    )
                    logger.info(
                        "table.vlm.rejected table=%s page=%s reasons=%s",
                        table.order_index,
                        table.page_number,
                        ",".join(diagnostics.get("rejection_reasons") or []),
                    )
                    issues.append(
                        IssuePayload(
                            code="table_vlm_rejected_regression",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description=f"VLM candidate rejected by guardrails for table {table.order_index}.",
                            page_number=table.page_number,
                            table_order_index=table.order_index,
                            details={
                                "structure_confidence": conf,
                                "repair_reason": repair_reason,
                                "render_mode": render_mode,
                                "attempted_modes": attempted_modes,
                                "rejection_reasons": diagnostics.get("rejection_reasons") or [],
                                "metrics": diagnostics.get("metrics") or {},
                            },
                        )
                    )
                    continue

                meta["repaired"] += 1
                meta.setdefault("repaired_tables", []).append(
                    {
                        "order_index": table.order_index,
                        "page_number": table.page_number,
                        "reason": repair_reason,
                        "render_mode": render_mode,
                    }
                )
                logger.info(
                    "table.vlm.repaired table=%s page=%s reason=%s render=%s conf=%s model=%s",
                    table.order_index,
                    table.page_number,
                    repair_reason,
                    render_mode,
                    conf,
                    self.table_vlm_model,
                )
                repaired[idx] = guarded_table

        return repaired, issues, meta
