from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.knowledge.ingestion.contracts import ExtractionResult, KnowledgeIngestionError
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile
from apps.knowledge.tables.normalization import (
    SheetNormalizationDiagnostics,
    normalize_sheet_rows,
    resolve_normalization_policy,
    sheet_is_allowed,
    summarize_normalization,
)

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import xlrd
except ImportError:  # pragma: no cover - fallback handled via runtime check
    xlrd = None  # type: ignore


class IngestionXlsExtractionMixin:

    def _extract_xls(
        self,
        path: Path,
        *,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        if xlrd is None:
            raise KnowledgeIngestionError("XLS ingestion requires the xlrd package.")
        try:
            workbook = xlrd.open_workbook(filename=str(path), on_demand=True)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open XLS file: {exc}") from exc

        policy = resolve_normalization_policy(upload)
        rules = self._table_privacy_rules(upload)

        allowed_sheets: list[tuple[int, str, Any]] = []
        estimated_rows = 0
        policy_skipped: list[str] = []
        for sheet_idx in range(1, (getattr(workbook, "nsheets", 0) or 0) + 1):
            sheet = workbook.sheet_by_index(sheet_idx - 1)
            sheet_name = getattr(sheet, "name", None) or f"Sheet {sheet_idx}"
            if not sheet_is_allowed(sheet_name, policy):
                policy_skipped.append(sheet_name)
                continue
            allowed_sheets.append((sheet_idx, sheet_name, sheet))
            try:
                estimated_rows += int(getattr(sheet, "nrows", 0) or 0)
            except (TypeError, ValueError):
                continue

        if upload and self._should_use_dataset_mode(estimated_rows=estimated_rows):
            return self._extract_xls_dataset_mode(
                workbook=workbook,
                file_detail=file_detail,
                upload=upload,
                sheets=allowed_sheets,
                policy_skipped=policy_skipped,
                estimated_rows=estimated_rows,
            )

        diagnostics: list[SheetNormalizationDiagnostics] = [
            SheetNormalizationDiagnostics(sheet_name=name, skipped=True, skip_reason="policy")
            for name in policy_skipped
        ]
        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        order_index = 1

        for sheet_idx, sheet_name, sheet in allowed_sheets:
            raw_rows: list[list[Any]] = []
            nrows = getattr(sheet, "nrows", 0) or 0
            ncols = getattr(sheet, "ncols", 0) or 0
            for row_idx in range(nrows):
                row_values: list[Any] = []
                for col_idx in range(ncols):
                    cell_value = sheet.cell_value(row_idx, col_idx)
                    cell_type = sheet.cell_type(row_idx, col_idx)
                    if cell_type == xlrd.XL_CELL_DATE:
                        try:
                            cell_value = xlrd.xldate_as_datetime(cell_value, workbook.datemode)
                        except Exception:
                            cell_value = ""
                    elif cell_type == xlrd.XL_CELL_BOOLEAN:
                        cell_value = bool(cell_value)
                    elif cell_type == xlrd.XL_CELL_ERROR:
                        cell_value = ""
                    row_values.append(cell_value)
                raw_rows.append(row_values)
            normalized = normalize_sheet_rows(
                raw_rows,
                sheet_name=sheet_name,
                policy=policy,
            )
            diagnostics.append(normalized.diagnostics)
            if normalized.diagnostics.skipped:
                continue
            table, page_layout = self._build_table_from_normalized_sheet(
                normalized,
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                file_detail=file_detail,
                source_label="xls",
                content_type="application/vnd.ms-excel",
                rules=rules,
            )
            tables.append(table)
            pages.append(page_layout)
            order_index += 1

        original_tables = list(tables)
        if not original_tables:
            raise KnowledgeIngestionError("XLS workbook did not contain any populated sheets.")
        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(
            original_tables,
            upload=upload,
            config=ingest_config,
        )
        if not tables:
            raise KnowledgeIngestionError("XLS workbook exceeded configured limits and no rows were indexed.")
        preview_text = self._table_preview_text(tables, rules=rules)
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        table_stats = self._table_stats_summary(
            total_rows=table_summary["total_rows"],
            indexed_rows=table_summary["indexed_rows"],
            row_cap=table_summary.get("row_cap_hint"),
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=table_summary.get("partial_tables", 0),
            row_tier=table_summary.get("row_tier_hint"),
        )
        normalization_summary = summarize_normalization(policy, diagnostics)
        metadata = {
            "format": "xls",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
        }
        if normalization_summary:
            metadata["normalization"] = normalization_summary
        _log_normalization_summary(upload, "xls", normalization_summary)
        return ExtractionResult(
            text=preview_text,
            format_hint="xls",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=limit_issues,
            entities=table_entities,
        )
