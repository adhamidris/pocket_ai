from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path
from typing import Any

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
from apps.knowledge.ingestion.contracts import ExtractionResult, IssuePayload, KnowledgeIngestionError
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadFile


class IngestionDelimitedDatasetMixin:

    def _extract_delimited_dataset(
        self,
        path: Path,
        *,
        format_hint: str,
        file_detail: KnowledgeUploadFile,
        upload: KnowledgeUpload,
        estimated_rows: int | None,
    ) -> ExtractionResult:
        if not upload:
            raise KnowledgeIngestionError("Dataset mode requires an upload record.")

        dataset_root, dataset_root_rel = self._reset_dataset_storage(upload)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        display_name = (file_detail.filename or "Dataset").strip() or "Dataset"
        safe_label = slugify(Path(display_name).stem) or "dataset"
        dataset_filename = f"{timestamp}_{safe_label}.csv.gz"
        dataset_path = dataset_root / dataset_filename
        dataset_rel_path = (dataset_root_rel / dataset_filename).as_posix()

        rules = self._table_privacy_rules(upload)
        preview_row_cap = int(self.dataset_preview_rows)
        sample_row_cap = int(self.dataset_sample_rows)

        try:
            with path.open("rb") as handle:
                sample_bytes = handle.read(64 * 1024)
        except OSError as exc:
            raise KnowledgeIngestionError(f"Unable to read dataset sample: {exc}") from exc

        sample_text = sample_bytes.decode("utf-8", errors="ignore")
        delimiter = "\t" if format_hint == "tsv" else ","
        try:
            dialect = csv.Sniffer().sniff(sample_text[:4096], delimiters=[",", ";", "\t", "|"])
            delimiter = getattr(dialect, "delimiter", delimiter) or delimiter
        except Exception:
            dialect = csv.excel

        preview_rows: list[tuple[int, list[str]]] = []
        sample_rows: list[list[str]] = []
        sample_visible_rows: list[dict[str, str]] = []
        row_count = 0
        internal_skipped = 0
        sample_target = max(sample_row_cap, 25)

        key_index_enabled = str(getattr(settings, "DATASET_KEY_INDEX_ENABLED", "true")).lower() in {"1", "true", "yes"}
        key_index_max_bytes = int(getattr(settings, "DATASET_KEY_INDEX_MAX_BYTES", 2_000_000) or 2_000_000)
        key_index_max_bytes = max(4096, min(25_000_000, key_index_max_bytes))
        key_indexes: list[dict[str, Any]] = []

        with path.open("rb") as raw_in:
            reader_stream = io.TextIOWrapper(raw_in, encoding="utf-8", errors="ignore", newline="")
            reader = csv.reader(reader_stream, delimiter=delimiter)
            try:
                raw_header = next(reader)
            except StopIteration:
                raise KnowledgeIngestionError("Dataset did not contain a header row.")

            if raw_header and isinstance(raw_header[0], str):
                raw_header[0] = raw_header[0].lstrip("\ufeff")

            column_schema: list[str] = []
            for idx, value in enumerate(raw_header):
                label = str(value or "").strip()
                column_schema.append(label or f"column_{idx + 1}")
            if not column_schema:
                raise KnowledgeIngestionError("Dataset did not contain any columns.")

            visible_mask = [
                not self._column_is_sensitive(column, rules)
                for column in column_schema
            ] if rules else [True] * len(column_schema)

            dataset_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(dataset_path, "wt", encoding="utf-8", newline="") as out_handle:
                writer = csv.writer(out_handle)
                writer.writerow(column_schema)

                for row in reader:
                    values = [str(item or "") for item in row]
                    if len(values) < len(column_schema):
                        values.extend([""] * (len(column_schema) - len(values)))
                    elif len(values) > len(column_schema):
                        values = values[: len(column_schema)]

                    if not any(value.strip() for value in values):
                        continue

                    attributes = {column_schema[i]: values[i] for i in range(len(column_schema))}
                    if rules and self._row_is_internal(attributes, rules):
                        internal_skipped += 1
                        continue

                    row_count += 1
                    writer.writerow(values)

                    if len(preview_rows) < preview_row_cap:
                        preview_rows.append((row_count, values))
                    if len(sample_rows) < sample_target:
                        sample_rows.append(values)
                    if len(sample_visible_rows) < sample_row_cap:
                        row_preview = {
                            column_schema[i]: values[i]
                            for i in range(len(column_schema))
                            if visible_mask[i]
                        }
                        sample_visible_rows.append(row_preview)

                    if key_index_enabled and not key_indexes and len(sample_rows) >= sample_target:
                        suggested_preview = self._suggest_dataset_key_columns(column_schema=column_schema, sample_rows=sample_rows)
                        chosen_columns = self._dataset_key_index_columns(
                            upload=upload,
                            sheet_name=None,
                            column_schema=column_schema,
                            suggested_keys=suggested_preview,
                            rules=rules,
                        )
                        expected_items = int(estimated_rows or row_count or 1)
                        spec = bloom_spec_for_items(expected_items)
                        for entry in chosen_columns:
                            bits = spec.bits
                            byte_len = (bits + 7) // 8
                            if byte_len > key_index_max_bytes:
                                bits = key_index_max_bytes * 8
                            bloom = BloomFilter(bits=bits, hashes=spec.hashes)
                            key_indexes.append(
                                {
                                    "column": entry.get("column"),
                                    "column_index": int(entry.get("column_index")),
                                    "source": entry.get("source"),
                                    "bloom": bloom,
                                    "value_count": 0,
                                }
                            )
                        if key_indexes:
                            for sample in sample_rows:
                                for info in key_indexes:
                                    idx = int(info["column_index"])
                                    if idx >= len(sample):
                                        continue
                                    normalized = normalize_identifier_value(sample[idx])
                                    if not normalized:
                                        continue
                                    info["bloom"].add(normalized)
                                    info["value_count"] += 1
                    elif key_indexes:
                        for info in key_indexes:
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
        if key_indexes:
            for info in key_indexes:
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

        table, page = self._build_dataset_preview_table(
            order_index=1,
            sheet_name=display_name,
            sheet_index=None,
            column_schema=column_schema,
            preview_rows=preview_rows,
            file_detail=file_detail,
            source_label="dataset_csv",
            content_type="text/csv",
            rules=rules,
        )
        tables = [table]
        preview_text = self._table_preview_text(tables, rules=rules)

        issues: list[IssuePayload] = []
        issues.append(
            IssuePayload(
                code="dataset_mode_enabled",
                severity=KnowledgeIssueSeverity.INFO.value,
                description="Large dataset stored in dataset mode (file-backed). Only a small preview is indexed into Postgres.",
                details={
                    "estimated_rows": estimated_rows,
                    "rows_stored": row_count,
                    "preview_rows_indexed": len(table.rows),
                    "internal_rows_skipped": internal_skipped,
                    "dataset_storage_format": self.dataset_storage_format,
                },
            )
        )

        dataset_metadata = {
            "enabled": True,
            "storage_format": self.dataset_storage_format,
            "storage_path": dataset_rel_path,
            "size_bytes": dataset_size,
            "row_count": row_count,
            "estimated_row_count": estimated_rows,
            "column_schema": column_schema,
            "sample_rows": sample_visible_rows,
            "suggested_key_columns": suggested_keys,
            "delimiter": delimiter,
            "key_indexes": key_index_payloads,
        }

        table_stats = self._table_stats_summary(
            total_rows=row_count,
            indexed_rows=len(table.rows),
            row_cap=len(table.rows),
            source_row_count=self._integration_row_count(upload),
            table_count=1,
            partial_tables=1 if row_count > len(table.rows) else 0,
            row_tier="large" if estimated_rows and estimated_rows >= self.dataset_row_threshold else "medium",
        )
        metadata = {
            "format": format_hint,
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
            format_hint=format_hint,
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
