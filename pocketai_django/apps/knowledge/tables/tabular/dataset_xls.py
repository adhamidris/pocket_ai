from __future__ import annotations

import csv
import gzip
from pathlib import Path
from typing import Any, Sequence

from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import (
    ExtractionResult,
    IssuePayload,
    PageLayout,
    TablePayload,
)
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile


class IngestionXlsDatasetMixin:

    def _extract_xls_dataset_mode(
        self,
        *,
        workbook: Any,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload,
        sheets: Sequence[tuple[int, str, Any]],
        policy_skipped: Sequence[str],
        estimated_rows: int,
    ) -> ExtractionResult:
        dataset_root, dataset_root_rel = self._reset_dataset_storage(upload)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        rules = self._table_privacy_rules(upload)
        preview_row_cap = int(self.dataset_preview_rows)
        sample_row_cap = int(self.dataset_sample_rows)

        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        issues: list[IssuePayload] = []
        dataset_sheets: list[dict[str, Any]] = []

        total_rows = 0
        total_preview_rows = 0
        internal_skipped_total = 0

        for order_index, (sheet_idx, sheet_name, sheet) in enumerate(sheets, start=1):
            nrows = int(getattr(sheet, "nrows", 0) or 0)
            ncols = int(getattr(sheet, "ncols", 0) or 0)
            if nrows <= 0 or ncols <= 0:
                continue

            header_row_idx: int | None = None
            header_values: list[str] | None = None
            scan_limit = min(nrows, 50)
            for idx in range(scan_limit):
                raw_values = list(getattr(sheet, "row_values")(idx))
                candidate = [self._stringify_dataset_cell(val) for val in raw_values[:ncols]]
                if any(str(value).strip() for value in candidate):
                    header_row_idx = idx
                    header_values = candidate
                    break
            if header_row_idx is None or header_values is None:
                continue
            if len(header_values) < ncols:
                header_values.extend([""] * (ncols - len(header_values)))

            column_schema: list[str] = []
            for idx in range(ncols):
                label = header_values[idx] if idx < len(header_values) else ""
                column_schema.append(str(label or "").strip() or f"column_{idx + 1}")

            visible_mask = [
                not self._column_is_sensitive(column, rules)
                for column in column_schema
            ] if rules else [True] * len(column_schema)

            sheet_slug = slugify(sheet_name) or f"sheet_{sheet_idx}"
            dataset_filename = f"{timestamp}_sheet{sheet_idx}_{sheet_slug}.csv.gz"
            dataset_path = dataset_root / dataset_filename
            dataset_rel_path = (dataset_root_rel / dataset_filename).as_posix()

            preview_rows: list[tuple[int, list[str]]] = []
            sample_rows: list[list[str]] = []
            sample_visible_rows: list[dict[str, str]] = []
            row_count = 0
            internal_skipped = 0

            dataset_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(dataset_path, "wt", encoding="utf-8", newline="") as out_handle:
                writer = csv.writer(out_handle)
                writer.writerow(column_schema)

                for row_idx in range(header_row_idx + 1, nrows):
                    raw_values = list(getattr(sheet, "row_values")(row_idx))
                    raw_types = list(getattr(sheet, "row_types")(row_idx))
                    values: list[str] = []
                    for col_idx in range(ncols):
                        cell_value = raw_values[col_idx] if col_idx < len(raw_values) else ""
                        cell_type = raw_types[col_idx] if col_idx < len(raw_types) else None
                        if cell_type == xlrd.XL_CELL_DATE:
                            try:
                                cell_value = xlrd.xldate_as_datetime(cell_value, workbook.datemode)
                            except Exception:
                                cell_value = ""
                        elif cell_type == xlrd.XL_CELL_BOOLEAN:
                            cell_value = bool(cell_value)
                        elif cell_type == xlrd.XL_CELL_ERROR:
                            cell_value = ""
                        values.append(self._stringify_dataset_cell(cell_value))

                    if not any(str(value).strip() for value in values):
                        continue

                    attributes = {column_schema[i]: str(values[i] or "") for i in range(len(column_schema))}
                    if rules and self._row_is_internal(attributes, rules):
                        internal_skipped += 1
                        continue

                    row_count += 1
                    writer.writerow(values)

                    if len(preview_rows) < preview_row_cap:
                        preview_rows.append((row_count, list(values)))
                    if len(sample_rows) < max(sample_row_cap, 25):
                        sample_rows.append(list(values))
                    if len(sample_visible_rows) < sample_row_cap:
                        row_preview = {
                            column_schema[i]: str(values[i] or "")
                            for i in range(len(column_schema))
                            if visible_mask[i]
                        }
                        sample_visible_rows.append(row_preview)

            try:
                dataset_size = dataset_path.stat().st_size
            except OSError:
                dataset_size = 0

            suggested_keys = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
            table, page_layout = self._build_dataset_preview_table(
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                column_schema=column_schema,
                preview_rows=preview_rows,
                file_detail=file_detail,
                source_label="dataset_xls",
                content_type="text/csv",
                rules=rules,
            )
            tables.append(table)
            pages.append(page_layout)
            total_preview_rows += len(table.rows)
            total_rows += row_count
            internal_skipped_total += internal_skipped

            dataset_sheets.append(
                {
                    "sheet_index": sheet_idx,
                    "sheet_name": sheet_name,
                    "storage_path": dataset_rel_path,
                    "size_bytes": dataset_size,
                    "row_count": row_count,
                    "estimated_row_count": nrows,
                    "column_schema": column_schema,
                    "sample_rows": sample_visible_rows,
                    "suggested_key_columns": suggested_keys,
                    "preview_rows_indexed": len(table.rows),
                    "internal_rows_skipped": internal_skipped,
                }
            )
            if row_count > len(table.rows):
                issues.append(
                    IssuePayload(
                        code="dataset_preview_truncated",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="Only a small preview of this dataset sheet was indexed into Postgres.",
                        page_number=sheet_idx,
                        table_order_index=order_index,
                        details={
                            "rows_stored": row_count,
                            "preview_rows_indexed": len(table.rows),
                            "storage_path": dataset_rel_path,
                        },
                    )
                )

        if not tables:
            raise KnowledgeIngestionError("XLS workbook did not contain any populated sheets.")

        partial_tables = sum(1 for sheet in dataset_sheets if sheet.get("row_count", 0) > sheet.get("preview_rows_indexed", 0))
        table_stats = self._table_stats_summary(
            total_rows=total_rows,
            indexed_rows=total_preview_rows,
            row_cap=preview_row_cap,
            source_row_count=self._integration_row_count(upload),
            table_count=len(tables),
            partial_tables=partial_tables,
            row_tier="large",
        )
        metadata = {
            "format": "xls",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": {
                "truncated_tables": 0,
                "truncated_rows": max(0, total_rows - total_preview_rows),
                "truncated_columns": 0,
            },
            "table_stats": table_stats,
            "dataset": {
                "enabled": True,
                "storage_format": self.dataset_storage_format,
                "row_count": total_rows,
                "estimated_row_count": estimated_rows,
                "preview_rows_indexed": total_preview_rows,
                "internal_rows_skipped": internal_skipped_total,
                "policy_skipped_sheets": list(policy_skipped),
                "sheets": dataset_sheets,
            },
        }

        issues.insert(
            0,
            IssuePayload(
                code="dataset_mode_enabled",
                severity=KnowledgeIssueSeverity.INFO.value,
                description="Large spreadsheet stored in dataset mode (file-backed). Only a small preview is indexed into Postgres.",
                details={
                    "estimated_rows": estimated_rows,
                    "rows_stored": total_rows,
                    "preview_rows_indexed": total_preview_rows,
                    "internal_rows_skipped": internal_skipped_total,
                    "sheet_count": len(dataset_sheets),
                },
            ),
        )

        preview_text = self._table_preview_text(tables, rules=rules)
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        return ExtractionResult(
            text=preview_text,
            format_hint="xls",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=issues,
            entities=table_entities,
        )
