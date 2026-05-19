from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.ingestion.contracts import (
    ExtractionResult,
    KnowledgeIngestionError,
    PageBlockPayload,
    PageLayout,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
from apps.knowledge.ingestion.signals import _log_normalization_summary
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile
from apps.knowledge.tables.normalization import normalize_sheet_rows, resolve_normalization_policy, summarize_normalization


class IngestionDelimitedFilesMixin:

    def _extract_csv(
        self,
        path: Path,
        *,
        format_hint: str,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        estimated_rows = self._estimate_delimited_row_count(path)
        if upload and self._should_use_dataset_mode(estimated_rows=estimated_rows):
            return self._extract_delimited_dataset(
                path,
                format_hint=format_hint,
                file_detail=file_detail,
                upload=upload,
                estimated_rows=estimated_rows,
            )

        raw_text = self._extract_text_file(path)
        normalized = raw_text.lstrip("\ufeff")
        if not normalized.strip():
            raise KnowledgeIngestionError("CSV document did not contain any rows.")
        delimiter = "\t" if format_hint == "tsv" else ","
        sample = normalized[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
            delimiter = dialect.delimiter or delimiter
        except Exception:
            # Keep default delimiter
            pass
        reader = csv.reader(io.StringIO(normalized), delimiter=delimiter)
        parsed_rows = [list(row) for row in reader]
        if not parsed_rows:
            raise KnowledgeIngestionError("CSV document did not contain any usable rows.")

        policy = resolve_normalization_policy(upload)
        sheet_label = (file_detail.filename or "CSV").strip() or "CSV"
        normalized_sheet = normalize_sheet_rows(parsed_rows, sheet_name=sheet_label, policy=policy)
        diagnostics = [normalized_sheet.diagnostics]
        if normalized_sheet.diagnostics.skipped:
            raise KnowledgeIngestionError("CSV document did not contain any usable rows.")

        column_schema = normalized_sheet.column_schema
        table_rows: list[TableRowPayload] = []
        for row_idx, values in enumerate(normalized_sheet.rows, start=1):
            cells: list[TableCellPayload] = []
            formatted_cells: list[str] = []
            for col_idx, column_key in enumerate(column_schema):
                value = values[col_idx] if col_idx < len(values) else ""
                formatted_cells.append(value)
                cells.append(
                    TableCellPayload(
                        row_index=row_idx,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=value,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_idx,
                    page_number=None,
                    raw_text="\t".join(formatted_cells),
                    metadata={"source": format_hint or "csv", "line_number": row_idx + 1},
                    cells=cells,
                )
            )
        table = TablePayload(
            order_index=1,
            title=file_detail.filename or "CSV Table",
            section_heading="",
            page_number=None,
            column_schema=column_schema,
            metadata={
                "source": "csv",
                "delimiter": delimiter,
                "filename": file_detail.filename,
            },
            rows=table_rows,
        )
        rules = self._table_privacy_rules(upload)
        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits([table], upload=upload, config=ingest_config)
        if not tables:
            raise KnowledgeIngestionError("CSV document did not contain rows within configured limits.")
        table_entities = self._table_row_entities(
            tables,
            business_profile=getattr(upload, "business_profile", None),
            upload=upload,
        )
        preview_text = self._table_preview_text(tables, rules=rules)
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
        issues = limit_issues
        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(preview_text.strip()) / float(612 * 792),
            has_ocr_content=False,
            content_type="text/csv" if delimiter == "," else "text/tab-separated-values",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.TABLE,
                    order_index=0,
                    text=preview_text or normalized[:2000],
                    metadata={"source": "csv"},
                )
            ],
            metadata={"table_count": 1, "filename": file_detail.filename},
        )
        metadata = {
            "format": format_hint,
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
        }
        if normalization_summary:
            metadata["normalization"] = normalization_summary
        _log_normalization_summary(upload, "csv", normalization_summary)
        return ExtractionResult(
            text=preview_text or normalized,
            format_hint=format_hint,
            metadata=metadata,
            pages=[page],
            tables=tables,
            issues=issues,
            entities=table_entities,
        )
