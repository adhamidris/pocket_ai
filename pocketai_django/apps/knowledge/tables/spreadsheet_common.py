from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

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
from apps.knowledge.ingestion.signals import (
    _log_normalization_summary,
    _SPREADSHEET_INSTRUCTION_SHEET_RE,
    _SPREADSHEET_INSTRUCTION_TOKEN_RE,
    _SPREADSHEET_RECORD_ID_RE,
    _SPREADSHEET_REFERENCE_SHEET_RE,
    _SPREADSHEET_SUMMARY_ROW_RE,
    _SPREADSHEET_ZERO_LIKE_RE,
    _TABLE_NUMBER_LIKE_RE,
)
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile
from apps.knowledge.tables.normalization import (
    NormalizedSheet,
    SheetNormalizationDiagnostics,
    SpreadsheetRowInput,
    normalize_sheet_rows,
    resolve_normalization_policy,
    sheet_is_allowed,
    summarize_normalization,
)

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - fallback handled via runtime check
    load_workbook = None  # type: ignore


class IngestionSpreadsheetCommonMixin:

    def _build_table_from_normalized_sheet(
        self,
        normalized: NormalizedSheet,
        *,
        order_index: int,
        sheet_name: str,
        sheet_index: int | None,
        sheet_hidden: bool,
        sheet_role: str,
        file_detail: KnowledgeUploadFile,
        source_label: str,
        content_type: str,
        rules: Mapping[str, Any] | None,
    ) -> tuple[TablePayload, PageLayout]:
        column_schema = normalized.column_schema
        table_rows: list[TableRowPayload] = []
        for row_idx, values in enumerate(normalized.rows, start=1):
            row_meta = (
                normalized.row_metadata[row_idx - 1]
                if row_idx - 1 < len(normalized.row_metadata)
                else None
            )
            cells: list[TableCellPayload] = []
            formatted: list[str] = []
            for col_idx, column_key in enumerate(column_schema):
                value = values[col_idx] if col_idx < len(values) else ""
                formatted.append(value)
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
                    page_number=sheet_index,
                    raw_text="\t".join(formatted),
                    metadata={
                        "source": source_label,
                        "sheet": sheet_name,
                        "source_row_index": getattr(row_meta, "source_row_index", None),
                        "hidden": bool(getattr(row_meta, "hidden", False)),
                        "row_kind": getattr(row_meta, "row_kind", "data"),
                    },
                    cells=cells,
                )
            )
        table = TablePayload(
            order_index=order_index,
            title=sheet_name,
            section_heading=sheet_name,
            page_number=sheet_index,
            column_schema=column_schema,
            metadata={
                "source": source_label,
                "sheet_name": sheet_name,
                "sheet_hidden": bool(sheet_hidden),
                "sheet_role": sheet_role,
                "is_decorative": sheet_role == "reference_hidden",
                "filename": file_detail.filename,
            },
            rows=table_rows,
        )
        preview = self._table_preview_text([table], rules=rules)
        page_layout = PageLayout(
            page_number=sheet_index or order_index,
            width=612,
            height=792,
            rotation=0,
            text_density=len(preview.strip()) / float(612 * 792),
            has_ocr_content=False,
            content_type=content_type,
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.TABLE,
                    order_index=order_index,
                    text=preview,
                    metadata={
                        "source": source_label,
                        "sheet": sheet_name,
                        "sheet_role": sheet_role,
                        "is_decorative": sheet_role == "reference_hidden",
                    },
                )
            ],
            metadata={"sheet_name": sheet_name, "sheet_role": sheet_role},
        )
        return table, page_layout

    def _extract_xlsx(
        self,
        path: Path,
        *,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        if load_workbook is None:
            raise KnowledgeIngestionError("XLSX ingestion requires the openpyxl package.")
        try:
            # Spreadsheet-template normalization needs row visibility metadata
            # (e.g. hidden filler rows), which openpyxl does not expose on
            # ReadOnlyWorksheet. Load the workbook normally on the non-dataset
            # XLSX path so normalization can make deterministic keep/drop
            # decisions before indexing.
            workbook = load_workbook(filename=path, read_only=False, data_only=True)
        except Exception as exc:
            raise KnowledgeIngestionError(f"Unable to open XLSX file: {exc}") from exc

        policy = resolve_normalization_policy(upload)
        rules = self._table_privacy_rules(upload)

        allowed_sheets: list[tuple[int, str, Any]] = []
        estimated_rows = 0
        policy_skipped: list[str] = []
        for sheet_idx, sheet in enumerate(workbook.worksheets, start=1):
            sheet_name = sheet.title or f"Sheet {sheet_idx}"
            if not sheet_is_allowed(sheet_name, policy):
                policy_skipped.append(sheet_name)
                continue
            allowed_sheets.append((sheet_idx, sheet_name, sheet))
            try:
                estimated_rows += int(getattr(sheet, "max_row", 0) or 0)
            except (TypeError, ValueError):
                continue

        if upload and self._should_use_dataset_mode(estimated_rows=estimated_rows):
            return self._extract_xlsx_dataset_mode(
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
        sheet_role_counts: dict[str, int] = {}
        order_index = 1
        for sheet_idx, sheet_name, sheet in allowed_sheets:
            normalized = normalize_sheet_rows(
                self._iter_xlsx_rows_with_metadata(sheet),
                sheet_name=sheet_name,
                policy=policy,
            )
            diagnostics.append(normalized.diagnostics)
            if normalized.diagnostics.skipped:
                continue
            sheet_hidden = getattr(sheet, "sheet_state", "visible") != "visible"
            sheet_role = self._classify_spreadsheet_sheet_role(
                sheet_name=sheet_name,
                sheet_hidden=sheet_hidden,
                normalized=normalized,
            )
            sheet_role_counts[sheet_role] = sheet_role_counts.get(sheet_role, 0) + 1
            table, page_layout = self._build_table_from_normalized_sheet(
                normalized,
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                sheet_hidden=sheet_hidden,
                sheet_role=sheet_role,
                file_detail=file_detail,
                source_label="xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                rules=rules,
            )
            tables.append(table)
            pages.append(page_layout)
            order_index += 1

        original_tables = list(tables)
        if not original_tables:
            raise KnowledgeIngestionError("XLSX workbook did not contain any populated sheets.")
        ingest_config = self._table_ingest_config(upload)
        tables, table_metrics, limit_issues, table_summary = self._apply_table_limits(original_tables, upload=upload, config=ingest_config)
        if not tables:
            raise KnowledgeIngestionError("XLSX workbook exceeded configured limits and no rows were indexed.")
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
            "format": "xlsx",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": len(tables),
            "table_truncation": table_metrics,
            "table_stats": table_stats,
            "spreadsheet_sheet_roles": sheet_role_counts,
        }
        if normalization_summary:
            metadata["normalization"] = normalization_summary
        _log_normalization_summary(upload, "xlsx", normalization_summary)
        return ExtractionResult(
            text=preview_text,
            format_hint="xlsx",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=limit_issues,
            entities=table_entities,
        )

    @staticmethod
    def _iter_xlsx_rows_with_metadata(sheet: Any) -> Iterable[SpreadsheetRowInput]:
        for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            row_dimension = None
            try:
                row_dimension = sheet.row_dimensions.get(row_idx)
            except Exception:
                row_dimension = None
            hidden = bool(getattr(row_dimension, "hidden", False))
            yield SpreadsheetRowInput(values=row, row_index=row_idx, hidden=hidden)

    def _classify_spreadsheet_sheet_role(
        self,
        *,
        sheet_name: str,
        sheet_hidden: bool,
        normalized: NormalizedSheet,
    ) -> str:
        name = str(sheet_name or "").strip()
        if sheet_hidden and _SPREADSHEET_REFERENCE_SHEET_RE.search(name):
            return "reference_hidden"
        if _SPREADSHEET_INSTRUCTION_SHEET_RE.search(name):
            return "instructional"
        if _SPREADSHEET_SUMMARY_ROW_RE.search(name):
            return "summary"
        if sheet_hidden:
            return "reference_hidden"

        rows = normalized.rows or []
        if not rows:
            return "unknown"

        record_id_hits = 0
        transactional_record_rows = 0
        narrative_rows = 0
        summary_hits = 0
        form_like_rows = 0
        for row in rows[:100]:
            values = [str(value or "").strip() for value in row if str(value or "").strip()]
            if not values:
                continue
            descriptor = values[0]
            if _SPREADSHEET_RECORD_ID_RE.fullmatch(descriptor):
                record_id_hits += 1
            row_text = " ".join(values)
            if _SPREADSHEET_SUMMARY_ROW_RE.search(row_text):
                summary_hits += 1
            if len(values) <= 2 and sum(len(value.split()) for value in values) >= 6:
                narrative_rows += 1
            if self._spreadsheet_row_looks_transactional_record(values):
                transactional_record_rows += 1
            if self._spreadsheet_row_looks_form_like(values):
                form_like_rows += 1

        if record_id_hits > 0:
            return "transactional"
        if transactional_record_rows >= max(3, min(12, math.ceil(len(rows) * 0.3))) and transactional_record_rows > form_like_rows:
            return "transactional"
        if summary_hits > 0 and summary_hits >= max(narrative_rows, form_like_rows):
            return "summary"
        if narrative_rows >= max(3, len(rows) // 3):
            return "instructional"
        if form_like_rows >= max(2, min(8, math.ceil(len(rows) * 0.25))):
            return "form_like"
        return "unknown"

    def _spreadsheet_row_looks_guidance(self, values: Sequence[str]) -> bool:
        cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
        if not cleaned:
            return False

        joined = " ".join(cleaned)
        word_count = len(joined.split())
        numeric_like_count = sum(
            1
            for value in cleaned
            if _SPREADSHEET_RECORD_ID_RE.fullmatch(value) or _TABLE_NUMBER_LIKE_RE.search(value)
        )
        long_text_cells = sum(1 for value in cleaned if len(value.split()) >= 8)
        has_instruction_token = any(_SPREADSHEET_INSTRUCTION_TOKEN_RE.search(value) for value in cleaned)
        if has_instruction_token and numeric_like_count == 0:
            return True
        return word_count >= 18 and numeric_like_count <= 1 and long_text_cells >= 1 and len(cleaned) <= 3

    def _spreadsheet_row_looks_form_like(self, values: Sequence[str]) -> bool:
        cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
        if len(cleaned) < 2 or len(cleaned) > 6:
            return False
        if any(_SPREADSHEET_RECORD_ID_RE.fullmatch(value) for value in cleaned):
            return False
        if self._spreadsheet_row_looks_guidance(cleaned):
            return False

        text_like_count = 0
        numeric_like_count = 0
        control_like_count = 0
        total_words = 0
        for value in cleaned:
            total_words += len(value.split())
            if self._spreadsheet_value_is_control(value):
                control_like_count += 1
                continue
            if _TABLE_NUMBER_LIKE_RE.search(value):
                numeric_like_count += 1
                continue
            text_like_count += 1

        if control_like_count >= len(cleaned) - 1:
            return False
        if text_like_count >= 1 and numeric_like_count >= 1:
            return True
        if text_like_count >= 2 and total_words <= 24:
            return True
        return False

    def _spreadsheet_row_looks_transactional_record(self, values: Sequence[str]) -> bool:
        cleaned = [str(value or "").strip() for value in values if str(value or "").strip()]
        if len(cleaned) < 5:
            return False
        if any(_SPREADSHEET_RECORD_ID_RE.fullmatch(value) for value in cleaned):
            return True
        if self._spreadsheet_row_looks_guidance(cleaned):
            return False

        zero_like_count = sum(1 for value in cleaned if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value))
        control_like_count = sum(1 for value in cleaned if self._spreadsheet_value_is_control(value))
        if control_like_count >= 2:
            return False
        if zero_like_count >= max(2, len(cleaned) // 2):
            return False

        substantive_text_count = 0
        numeric_like_count = 0
        long_text_count = 0
        for value in cleaned:
            if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value) or self._spreadsheet_value_is_control(value):
                continue
            if _TABLE_NUMBER_LIKE_RE.search(value):
                numeric_like_count += 1
                continue
            substantive_text_count += 1
            if len(value.split()) >= 2 or len(value) >= 16:
                long_text_count += 1

        if substantive_text_count < 3:
            return False
        if numeric_like_count >= 1:
            return True
        return long_text_count >= 2 and substantive_text_count >= 4
