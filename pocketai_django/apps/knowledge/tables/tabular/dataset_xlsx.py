from __future__ import annotations

import csv
import gzip
from pathlib import Path
from typing import Any, Sequence

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.datasets.key_index import (
    BloomFilter,
    bloom_spec_for_items,
    normalize_identifier_value,
    resolve_key_index_storage_path,
    write_bloom_filter,
)
from apps.knowledge.ingestion.contracts import (
    ExtractionResult,
    IssuePayload,
    PageLayout,
    TablePayload,
)
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile


class IngestionXlsxDatasetMixin:

    def _extract_xlsx_dataset_mode(
        self,
        *,
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
        sample_target = max(sample_row_cap, 25)

        key_index_enabled = str(getattr(settings, "DATASET_KEY_INDEX_ENABLED", "true")).lower() in {"1", "true", "yes"}
        key_index_max_bytes = int(getattr(settings, "DATASET_KEY_INDEX_MAX_BYTES", 2_000_000) or 2_000_000)
        key_index_max_bytes = max(4096, min(25_000_000, key_index_max_bytes))

        tables: list[TablePayload] = []
        pages: list[PageLayout] = []
        issues: list[IssuePayload] = []
        dataset_sheets: list[dict[str, Any]] = []

        total_rows = 0
        total_preview_rows = 0
        internal_skipped_total = 0

        for order_index, (sheet_idx, sheet_name, sheet) in enumerate(sheets, start=1):
            try:
                max_col = int(getattr(sheet, "max_column", 0) or 0)
            except (TypeError, ValueError):
                max_col = 0

            rows_iter = sheet.iter_rows(values_only=True)
            header_values: list[str] | None = None
            for raw_row in rows_iter:
                candidate = [self._stringify_dataset_cell(val) for val in raw_row]
                if any(str(value).strip() for value in candidate):
                    header_values = candidate
                    break
            if header_values is None:
                continue

            if max_col <= 0:
                max_col = len(header_values)
            if len(header_values) < max_col:
                header_values.extend([""] * (max_col - len(header_values)))

            column_schema: list[str] = []
            for idx in range(max_col):
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
            expected_items = 0
            try:
                expected_items = int(getattr(sheet, "max_row", 0) or 0)
            except (TypeError, ValueError):
                expected_items = 0
            sheet_key_indexes: list[dict[str, Any]] = []

            dataset_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(dataset_path, "wt", encoding="utf-8", newline="") as out_handle:
                writer = csv.writer(out_handle)
                writer.writerow(column_schema)
                for raw_row in rows_iter:
                    values = [self._stringify_dataset_cell(val) for val in raw_row]
                    if len(values) < len(column_schema):
                        values.extend([""] * (len(column_schema) - len(values)))
                    elif len(values) > len(column_schema):
                        values = values[: len(column_schema)]

                    if not any(str(value).strip() for value in values):
                        continue

                    attributes = {column_schema[i]: str(values[i] or "") for i in range(len(column_schema))}
                    if rules and self._row_is_internal(attributes, rules):
                        internal_skipped += 1
                        continue

                    row_count += 1
                    writer.writerow([str(value or "") for value in values])

                    if len(preview_rows) < preview_row_cap:
                        preview_rows.append((row_count, [str(value or "") for value in values]))
                    if len(sample_rows) < sample_target:
                        sample_rows.append([str(value or "") for value in values])
                    if len(sample_visible_rows) < sample_row_cap:
                        row_preview = {
                            column_schema[i]: str(values[i] or "")
                            for i in range(len(column_schema))
                            if visible_mask[i]
                        }
                        sample_visible_rows.append(row_preview)

                    if key_index_enabled and not sheet_key_indexes and len(sample_rows) >= sample_target:
                        suggested_preview = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
                        chosen_columns = self._dataset_key_index_columns(
                            upload=upload,
                            sheet_name=sheet_name,
                            column_schema=column_schema,
                            suggested_keys=suggested_preview,
                            rules=rules,
                        )
                        spec = bloom_spec_for_items(int(expected_items or estimated_rows or row_count or 1))
                        for entry in chosen_columns:
                            bits = spec.bits
                            byte_len = (bits + 7) // 8
                            if byte_len > key_index_max_bytes:
                                bits = key_index_max_bytes * 8
                            bloom = BloomFilter(bits=bits, hashes=spec.hashes)
                            sheet_key_indexes.append(
                                {
                                    "column": entry.get("column"),
                                    "column_index": int(entry.get("column_index")),
                                    "source": entry.get("source"),
                                    "bloom": bloom,
                                    "value_count": 0,
                                }
                            )
                        if sheet_key_indexes:
                            for sample in sample_rows:
                                for info in sheet_key_indexes:
                                    idx = int(info["column_index"])
                                    if idx >= len(sample):
                                        continue
                                    normalized = normalize_identifier_value(sample[idx])
                                    if not normalized:
                                        continue
                                    info["bloom"].add(normalized)
                                    info["value_count"] += 1
                    elif sheet_key_indexes:
                        for info in sheet_key_indexes:
                            idx = int(info["column_index"])
                            if idx >= len(values):
                                continue
                            normalized = normalize_identifier_value(values[idx])
                            if not normalized:
                                continue
                            info["bloom"].add(normalized)
                            info["value_count"] += 1

            try:
                dataset_size = dataset_path.stat().st_size
            except OSError:
                dataset_size = 0

            suggested_keys = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
            key_index_payloads: list[dict[str, Any]] = []
            if sheet_key_indexes:
                for info in sheet_key_indexes:
                    column_name = str(info.get("column") or "").strip()
                    if not column_name:
                        continue
                    index_rel_path = resolve_key_index_storage_path(dataset_rel_path=dataset_rel_path, column_name=column_name)
                    abs_index_path = (self.media_root / Path(index_rel_path)).resolve()
                    try:
                        abs_index_path.relative_to(self.media_root)
                    except ValueError:
                        continue
                    bloom = info.get("bloom")
                    if not isinstance(bloom, BloomFilter):
                        continue
                    try:
                        written_bytes = write_bloom_filter(abs_index_path, bloom)
                    except OSError:
                        continue
                    key_index_payloads.append(
                        {
                            "column": column_name,
                            "storage_path": index_rel_path,
                            "bits": int(bloom.bits),
                            "hashes": int(bloom.hashes),
                            "bytes": int(written_bytes),
                            "values_indexed": int(info.get("value_count") or 0),
                            "source": info.get("source"),
                        }
                    )
            table, page_layout = self._build_dataset_preview_table(
                order_index=order_index,
                sheet_name=sheet_name,
                sheet_index=sheet_idx,
                column_schema=column_schema,
                preview_rows=preview_rows,
                file_detail=file_detail,
                source_label="dataset_xlsx",
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
                    "estimated_row_count": int(getattr(sheet, "max_row", 0) or 0),
                    "column_schema": column_schema,
                    "sample_rows": sample_visible_rows,
                    "suggested_key_columns": suggested_keys,
                    "key_indexes": key_index_payloads,
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
            raise KnowledgeIngestionError("XLSX workbook did not contain any populated sheets.")

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
            "format": "xlsx",
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
            format_hint="xlsx",
            metadata=metadata,
            pages=pages,
            tables=tables,
            issues=issues,
            entities=table_entities,
        )
