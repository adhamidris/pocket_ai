from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import (
    IssuePayload,
    PageLayout,
    PdfSpan,
    TablePayload,
)
from apps.knowledge.tables.pdf.sparse_matrix import IngestionPdfSparseMatrixMixin
from apps.knowledge.tables.pdf.span_matrix import IngestionPdfSpanMatrixMixin


class IngestionPdfTableReconstructionMixin(
    IngestionPdfSparseMatrixMixin,
    IngestionPdfSpanMatrixMixin,
):


    @staticmethod
    def _collapsed_matrix_text_signal_count(value: str) -> int:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text:
            return 0
        count = len(re.findall(r"\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\b", text))
        count += len(re.findall(r"\b(?:egp|usd|eur|gbp|sar|aed)\b", text, flags=re.IGNORECASE))
        count += len(re.findall(r"%", text))
        return count

    def _diagnose_pdf_table_structure(
        self,
        table: TablePayload,
        *,
        selection_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        readable_rows = self._pdf_table_readable_rows(table)
        row_source = readable_rows or list(table.rows or [])
        column_count = self._table_column_count(table)
        header_row = next(
            (row for row in (table.rows or []) if (row.metadata or {}).get("row_type") == "header"),
            row_source[0] if row_source else None,
        )
        header_cells = list(header_row.cells or []) if header_row else []
        header_texts = [self._sanitize_text(cell.raw_text).strip() for cell in header_cells if str(cell.raw_text or "").strip()]
        header_joined = " ".join(header_texts).strip()
        merged_header = False
        if column_count <= 1:
            if len(header_texts) == 1 and len(header_joined.split()) >= 4:
                merged_header = True
            elif not header_texts and header_row and len(self._sanitize_text(header_row.raw_text).split()) >= 4:
                merged_header = True

        packed_value_rows = 0
        data_row_count = 0
        for row in row_source:
            cells = [cell for cell in (row.cells or []) if str(cell.raw_text or "").strip()]
            if not cells:
                continue
            if (row.metadata or {}).get("row_type") == "header":
                continue
            data_row_count += 1
            if len(cells) == 1 and self._collapsed_matrix_text_signal_count(cells[0].raw_text) >= 2:
                packed_value_rows += 1
        packed_value_ratio = float(packed_value_rows) / float(data_row_count or 1)

        lane = str((selection_context or {}).get("pdf_lane") or "")
        kind = "other"
        recoverable = False
        if lane == "native_text" and column_count <= 1 and data_row_count >= 3 and merged_header and packed_value_ratio >= 0.35:
            kind = "collapsed_matrix"
            recoverable = True

        return {
            "kind": kind,
            "recoverable": recoverable,
            "column_count": column_count,
            "data_row_count": data_row_count,
            "packed_value_ratio": round(packed_value_ratio, 4),
            "merged_header": merged_header,
            "header_text": header_joined or self._sanitize_text(getattr(header_row, "raw_text", "")).strip(),
        }

    def _reconstruct_collapsed_native_text_tables(
        self,
        *,
        tables: Sequence[TablePayload],
        candidates: Mapping[str, list[TablePayload]],
        page_spans: Sequence[Sequence[PdfSpan]] | None = None,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not self.pdf_collapsed_matrix_reconstruction_enabled:
            return list(tables), {"enabled": False}, []
        if str((selection_context or {}).get("pdf_lane") or "") != "native_text":
            return list(tables), {"enabled": True, "skipped_reason": "non_native_text_lane"}, []

        collapsed_targets = [
            table
            for table in tables
            if (self._diagnose_pdf_table_structure(table, selection_context=selection_context).get("kind") == "collapsed_matrix")
        ]
        if not collapsed_targets:
            return list(tables), {"enabled": True, "collapsed_targets": 0}, []

        span_reconstructed = self._reconstruct_collapsed_native_text_tables_from_spans(
            collapsed_targets=collapsed_targets,
            page_spans=page_spans or [],
        )
        if not span_reconstructed:
            return list(tables), {
                "enabled": True,
                "collapsed_targets": len(collapsed_targets),
                "reconstructed_tables": 0,
                "skipped_reason": "no_span_reconstruction_candidates",
            }, []

        issues: list[IssuePayload] = []
        replacements: dict[tuple[int, int | None], TablePayload] = {}
        for target_key, rebuilt in span_reconstructed.items():
            replacements[target_key] = rebuilt
            issues.append(
                IssuePayload(
                    code="pdf_table_structure_reconstructed",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Reconstructed collapsed native-text table from pymupdf:spans before canonical persistence.",
                    page_number=target_key[1],
                    table_order_index=target_key[0],
                    details={
                        "source_name": "pymupdf:spans",
                        "column_count": len(rebuilt.column_schema),
                        "row_count": len(rebuilt.rows),
                    },
                )
            )

        updated_tables: list[TablePayload] = []
        reconstructed_count = 0
        for table in tables:
            key = (table.order_index, table.page_number)
            replacement = replacements.get(key)
            if replacement is not None:
                updated_tables.append(replacement)
                reconstructed_count += 1
            else:
                updated_tables.append(table)
        return updated_tables, {
            "enabled": True,
            "collapsed_targets": len(collapsed_targets),
            "span_reconstruction_candidates": len(span_reconstructed),
            "reconstructed_tables": reconstructed_count,
        }, issues


    def _enforce_pdf_canonical_acceptance(
        self,
        tables: Sequence[TablePayload],
        *,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not self.pdf_structural_acceptance_enabled:
            return list(tables), {"enabled": False}, []
        kept: list[TablePayload] = []
        issues: list[IssuePayload] = []
        dropped_tables: list[dict[str, Any]] = []
        for table in tables:
            diagnosis = self._diagnose_pdf_table_structure(table, selection_context=selection_context)
            metadata = dict(table.metadata or {})
            metadata["structure_diagnosis"] = diagnosis
            unsafe = diagnosis.get("kind") == "collapsed_matrix" and not metadata.get("structure_reconstructed")
            metadata["canonical_acceptance"] = "fallback_text_only" if unsafe else "accepted"
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
            if unsafe:
                dropped_tables.append(
                    {
                        "order_index": table.order_index,
                        "page_number": table.page_number,
                        "diagnosis": diagnosis,
                    }
                )
                issues.append(
                    IssuePayload(
                        code="pdf_table_not_canonical",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="Dropped structurally unsafe PDF table from canonical persistence; text fallback remains available.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={"diagnosis": diagnosis},
                    )
                )
                continue
            kept.append(updated)
        return kept, {
            "enabled": True,
            "input_tables": len(tables),
            "kept_tables": len(kept),
            "dropped_tables": len(dropped_tables),
            "dropped_examples": dropped_tables[:8],
        }, issues

    def _repair_pdf_table_structure(
        self,
        tables: Sequence[TablePayload],
        *,
        candidates: Mapping[str, list[TablePayload]],
        page_spans: Sequence[Sequence[PdfSpan]] | None = None,
        selected_extractor: str,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not tables:
            return [], {"enabled": True, "selected_extractor": selected_extractor}, []
        reconstructed_tables, reconstruction_meta, reconstruction_issues = (
            self._reconstruct_collapsed_native_text_tables(
                tables=tables,
                candidates=candidates,
                page_spans=page_spans,
                selection_context=selection_context,
            )
        )
        accepted_tables, acceptance_meta, acceptance_issues = self._enforce_pdf_canonical_acceptance(
            reconstructed_tables,
            selection_context=selection_context,
        )
        meta = {
            "enabled": True,
            "selected_extractor": selected_extractor,
            "reconstruction": reconstruction_meta,
            "acceptance": acceptance_meta,
        }
        return accepted_tables, meta, reconstruction_issues + acceptance_issues

    def _pdf_pages_look_native_text(self, pages: Sequence[PageLayout]) -> bool:
        if not pages:
            return False
        non_ocr_pages = 0
        for page in pages:
            if bool(getattr(page, "has_ocr_content", False)):
                continue
            density = 0.0
            try:
                density = float(
                    (getattr(page, "metadata", {}) or {}).get("raw_text_density")
                    or getattr(page, "text_density", 0.0)
                    or 0.0
                )
            except (TypeError, ValueError):
                density = 0.0
            has_text_block = any(
                self._sanitize_text(str(getattr(block, "text", "") or "")).strip()
                for block in (getattr(page, "blocks", None) or [])
            )
            if has_text_block or density >= self.native_pdf_text_density_floor:
                non_ocr_pages += 1
        return non_ocr_pages >= max(1, math.ceil(len(pages) * 0.6))
