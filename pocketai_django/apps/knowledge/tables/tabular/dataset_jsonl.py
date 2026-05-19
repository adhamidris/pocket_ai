from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import ExtractionResult, IssuePayload, KnowledgeIngestionError
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile


class IngestionJsonlDatasetMixin:

    def _extract_jsonl_dataset(
        self,
        path: Path,
        *,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        if upload is None:
            raise KnowledgeIngestionError("JSONL dataset ingestion requires an upload record.")

        dataset_root, dataset_root_rel = self._reset_dataset_storage(upload)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        display_name = (file_detail.filename or "Dataset").strip() or "Dataset"
        safe_label = slugify(Path(display_name).stem) or "dataset"
        dataset_filename = f"{timestamp}_{safe_label}.jsonl.gz"
        dataset_path = dataset_root / dataset_filename
        dataset_rel_path = (dataset_root_rel / dataset_filename).as_posix()

        rules = self._table_privacy_rules(upload)
        preview_row_cap = int(self.dataset_preview_rows)
        sample_row_cap = int(self.dataset_sample_rows)

        row_count = 0
        sample_lines: list[bytes] = []
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as raw_in, gzip.open(dataset_path, "wb") as out_handle:
            for line in raw_in:
                if not line:
                    continue
                out_handle.write(line)
                if not line.strip():
                    continue
                row_count += 1
                if len(sample_lines) < max(sample_row_cap, 25):
                    sample_lines.append(line)

        try:
            dataset_size = dataset_path.stat().st_size
        except OSError:
            dataset_size = 0

        records: list[dict[str, Any]] = []
        keys: set[str] = set()
        parse_errors = 0
        for line in sample_lines:
            try:
                decoded = line.decode("utf-8", errors="ignore")
                obj = json.loads(decoded)
            except Exception:
                parse_errors += 1
                continue
            if isinstance(obj, dict):
                records.append(obj)
                for key in obj.keys():
                    if key is None:
                        continue
                    keys.add(str(key))

        if not keys:
            preview_text = "\n".join(line.decode("utf-8", errors="ignore").strip() for line in sample_lines[:5]).strip()
            issues = [
                IssuePayload(
                    code="dataset_mode_enabled",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="JSONL dataset stored in dataset mode (file-backed). No structured preview was indexed.",
                    details={
                        "rows_stored": row_count,
                        "sample_parse_errors": parse_errors,
                        "dataset_storage_format": "jsonl_gz",
                    },
                )
            ]
            metadata = {
                "format": "jsonl",
                "filename": file_detail.filename,
                "content_type": file_detail.content_type or "",
                "storage_path": file_detail.storage_path,
                "dataset": {
                    "enabled": True,
                    "storage_format": "jsonl_gz",
                    "storage_path": dataset_rel_path,
                    "size_bytes": dataset_size,
                    "row_count": row_count,
                    "sample_parse_errors": parse_errors,
                },
            }
            return ExtractionResult(
                text=preview_text,
                format_hint="jsonl",
                metadata=metadata,
                pages=[],
                tables=[],
                issues=issues,
                entities=[],
            )

        column_schema = sorted(keys)[:200]
        visible_mask = [
            not self._column_is_sensitive(column, rules)
            for column in column_schema
        ] if rules else [True] * len(column_schema)

        preview_rows: list[tuple[int, list[str]]] = []
        sample_rows: list[list[str]] = []
        sample_visible_rows: list[dict[str, str]] = []

        for idx, record in enumerate(records, start=1):
            values: list[str] = []
            for key in column_schema:
                values.append(self._stringify_dataset_cell(record.get(key)))
            preview_rows.append((idx, values))
            if len(sample_rows) < max(sample_row_cap, 25):
                sample_rows.append(values)
            if len(sample_visible_rows) < sample_row_cap:
                row_preview = {
                    column_schema[i]: values[i]
                    for i in range(len(column_schema))
                    if visible_mask[i]
                }
                sample_visible_rows.append(row_preview)
            if len(preview_rows) >= preview_row_cap:
                break

        suggested_keys = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
        table, page = self._build_dataset_preview_table(
            order_index=1,
            sheet_name=display_name,
            sheet_index=None,
            column_schema=column_schema,
            preview_rows=preview_rows,
            file_detail=file_detail,
            source_label="dataset_jsonl",
            content_type="application/x-ndjson",
            rules=rules,
        )
        tables = [table]
        preview_text = self._table_preview_text(tables, rules=rules)

        issues = [
            IssuePayload(
                code="dataset_mode_enabled",
                severity=KnowledgeIssueSeverity.INFO.value,
                description="JSONL dataset stored in dataset mode (file-backed). Only a small preview is indexed into Postgres.",
                details={
                    "rows_stored": row_count,
                    "preview_rows_indexed": len(table.rows),
                    "sample_parse_errors": parse_errors,
                    "dataset_storage_format": "jsonl_gz",
                },
            )
        ]
        dataset_metadata = {
            "enabled": True,
            "storage_format": "jsonl_gz",
            "storage_path": dataset_rel_path,
            "size_bytes": dataset_size,
            "row_count": row_count,
            "column_schema": column_schema,
            "sample_rows": sample_visible_rows,
            "suggested_key_columns": suggested_keys,
            "sample_parse_errors": parse_errors,
        }
        table_stats = self._table_stats_summary(
            total_rows=row_count,
            indexed_rows=len(table.rows),
            row_cap=len(table.rows),
            source_row_count=self._integration_row_count(upload),
            table_count=1,
            partial_tables=1 if row_count > len(table.rows) else 0,
            row_tier="large" if row_count >= self.dataset_row_threshold else "medium",
        )
        metadata = {
            "format": "jsonl",
            "filename": file_detail.filename,
            "content_type": file_detail.content_type or "",
            "storage_path": file_detail.storage_path,
            "table_count": 1,
            "table_truncation": {
                "truncated_tables": 0,
                "truncated_rows": max(0, row_count - len(table.rows)),
                "truncated_columns": 0,
            },
            "table_stats": table_stats,
            "dataset": dataset_metadata,
        }
        return ExtractionResult(
            text=preview_text,
            format_hint="jsonl",
            metadata=metadata,
            pages=[page],
            tables=tables,
            issues=issues,
            entities=self._table_row_entities(
                tables,
                business_profile=getattr(upload, "business_profile", None),
                upload=upload,
            ),
        )
