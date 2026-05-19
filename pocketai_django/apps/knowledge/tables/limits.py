from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Sequence

from django.conf import settings

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import IssuePayload, TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.models import KnowledgeUpload

logger = logging.getLogger(__name__)


class IngestionTableLimitsMixin:

    def _table_privacy_rules(self, upload: KnowledgeUpload | None) -> dict[str, Any]:
        rules = {
            "sensitive_exact": set(),
            "sensitive_patterns": [],
            "row_flag_column": None,
            "row_flag_values": set(),
        }

        def merge(config: Mapping[str, Any] | None) -> None:
            if not isinstance(config, Mapping):
                return
            columns = config.get("sensitive_columns") or []
            patterns = config.get("sensitive_column_patterns") or []
            for entry in columns:
                value = str(entry or "").strip()
                if not value:
                    continue
                if self._looks_like_regex(value):
                    try:
                        rules["sensitive_patterns"].append(re.compile(value, re.IGNORECASE))
                    except re.error:
                        continue
                else:
                    rules["sensitive_exact"].add(value.lower())
            for entry in patterns:
                value = str(entry or "").strip()
                if not value:
                    continue
                try:
                    rules["sensitive_patterns"].append(re.compile(value, re.IGNORECASE))
                except re.error:
                    continue
            column = config.get("row_flag_column")
            if column:
                canonical = self._canonical_column_name(str(column))
                if canonical:
                    rules["row_flag_column"] = canonical
            values = config.get("row_flag_values") or []
            combined = set(rules["row_flag_values"])
            for value in values:
                token = str(value or "").strip().lower()
                if token:
                    combined.add(token)
            rules["row_flag_values"] = combined

        business_meta = getattr(getattr(upload, "business_profile", None), "metadata", {})
        upload_meta = getattr(upload, "metadata", {})
        merge(business_meta.get("table_privacy") or business_meta.get("sensitive_table_config"))
        merge(upload_meta.get("table_privacy") or upload_meta.get("sensitive_table_config"))
        return rules

    def _table_ingest_config(self, upload: KnowledgeUpload | None) -> dict[str, Any]:
        config = {
            "max_rows": self.default_table_max_rows,
            "max_columns": self.default_table_max_columns if self.default_table_max_columns > 0 else None,
            "column_whitelist": set(),
            "max_rows_source": "default",
            "small_row_limit": int(getattr(settings, "RAG_TABLE_SMALL_ROW_LIMIT", 2000)),
            "large_row_limit": int(getattr(settings, "RAG_TABLE_LARGE_ROW_LIMIT", 20000)),
            "hard_row_cap": int(getattr(settings, "RAG_TABLE_MAX_HARD_CAP", 100000)),
        }

        def merge(source: Mapping[str, Any] | None) -> None:
            if not isinstance(source, Mapping):
                return
            rows = source.get("max_rows")
            columns = source.get("max_columns")
            whitelist = source.get("column_whitelist")
            try:
                if rows is not None:
                    value = int(rows)
                    if value > 0:
                        config["max_rows"] = value
                        config["max_rows_source"] = "override"
            except (TypeError, ValueError):
                pass
            try:
                if columns is not None:
                    value = int(columns)
                    config["max_columns"] = value if value > 0 else None
            except (TypeError, ValueError):
                pass
            if isinstance(whitelist, (list, tuple, set)):
                normalized = {
                    self._canonical_column_name(str(item))
                    for item in whitelist
                    if self._canonical_column_name(str(item))
                }
                if normalized:
                    config["column_whitelist"] = normalized

        business_meta = getattr(getattr(upload, "business_profile", None), "metadata", {})
        upload_meta = getattr(upload, "metadata", {})
        merge(business_meta.get("table_limits"))
        merge(upload_meta.get("table_limits"))
        return config

    @staticmethod
    def _determine_table_row_cap(
        row_count: int,
        config: Mapping[str, Any],
    ) -> tuple[int, str, str]:
        """
        Decide how many rows to keep for a given table based on tiering thresholds.

        Runtime contract:
        - Default path keeps all rows (up to hard safety cap) and relies on read-time
          pagination/continuation for bounded responses.
        - Explicit tenant/upload `table_limits.max_rows` remains an intentional cap.
        Returns (row_cap, tier, strategy).
        """
        base_cap = max(1, int(config.get("max_rows", 1)))
        small_limit = max(1, int(config.get("small_row_limit", 2000)))
        large_limit = max(small_limit, int(config.get("large_row_limit", 20000)))
        hard_cap = max(1, int(config.get("hard_row_cap", 100000)))
        override = config.get("max_rows_source") == "override"
        if row_count <= 0:
            return base_cap, "unknown", "default"
        tier = "small" if row_count <= small_limit else ("medium" if row_count <= large_limit else "large")
        if row_count > hard_cap:
            return hard_cap, tier, "hard_capped"
        if override:
            effective_cap = min(base_cap, hard_cap)
            strategy = "override_full" if row_count <= effective_cap else "override_capped"
            return effective_cap, tier, strategy
        return row_count, tier, "full"

    @staticmethod
    def _integration_row_count(upload: KnowledgeUpload | None) -> int | None:
        """
        Best-effort row count provided by integrations (if any). Returns None when unavailable.
        """
        if not upload:
            return None
        metadata = getattr(upload, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        resource = metadata.get("integration_resource")
        if not isinstance(resource, Mapping):
            return None
        raw_value = resource.get("row_count")
        try:
            count = int(raw_value)
        except (TypeError, ValueError):
            return None
        return count if count > 0 else None

    @staticmethod
    def _table_stats_summary(
        *,
        total_rows: int,
        indexed_rows: int,
        row_cap: int | None,
        source_row_count: int | None,
        table_count: int,
        partial_tables: int = 0,
        row_tier: str | None = None,
    ) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "total_rows": max(0, int(total_rows)),
            "indexed_rows": max(0, int(indexed_rows)),
            "table_count": max(0, int(table_count)),
        }
        if row_cap is not None:
            stats["row_cap"] = max(0, int(row_cap))
        if source_row_count is not None:
            stats["source_row_count"] = max(0, int(source_row_count))
        if partial_tables:
            stats["partial_tables"] = max(0, int(partial_tables))
        if row_tier:
            stats["row_tier"] = row_tier
        if stats["indexed_rows"] < stats["total_rows"] or stats.get("partial_tables"):
            stats["partial_index"] = True
        return stats

    def _apply_table_limits(
        self,
        tables: Sequence[TablePayload],
        *,
        upload: KnowledgeUpload | None,
        config: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, int], list[IssuePayload], dict[str, Any]]:
        if not tables:
            empty_metrics = {"truncated_tables": 0, "truncated_rows": 0, "truncated_columns": 0}
            empty_summary = {"total_rows": 0, "indexed_rows": 0, "partial_tables": 0, "row_cap_hint": 0, "row_tier_hint": "unknown"}
            return [], empty_metrics, [], empty_summary
        effective_config = dict(config) if isinstance(config, Mapping) else self._table_ingest_config(upload)
        limited: list[TablePayload] = []
        truncated_tables = 0
        truncated_rows = 0
        truncated_columns = 0
        limit_issues: list[IssuePayload] = []
        summary = {
            "total_rows": 0,
            "indexed_rows": 0,
            "partial_tables": 0,
            "row_cap_hint": 0,
            "row_tier_hint": "unknown",
        }
        tier_rank = {"unknown": 0, "small": 1, "medium": 2, "override": 2, "large": 3}
        for table in tables:
            original_row_count = len(table.rows or [])
            summary["total_rows"] += original_row_count
            table_row_cap, tier, strategy = self._determine_table_row_cap(original_row_count, effective_config)
            summary["row_cap_hint"] = max(summary["row_cap_hint"], table_row_cap)
            if tier_rank.get(tier, 0) > tier_rank.get(summary["row_tier_hint"], 0):
                summary["row_tier_hint"] = tier
            limited_table, removed_columns, removed_rows, dropped_table = self._limit_table_payload(
                table,
                max_rows=table_row_cap,
                max_columns=effective_config["max_columns"],
                column_whitelist=effective_config["column_whitelist"],
            )
            indexed_row_count = len(limited_table.rows or []) if limited_table else 0
            summary["indexed_rows"] += indexed_row_count
            if removed_rows or dropped_table:
                summary["partial_tables"] += 1
            truncated_columns += removed_columns
            truncated_rows += removed_rows
            if removed_rows or dropped_table:
                truncated_amount = removed_rows if not dropped_table else original_row_count
                description = (
                    f"Only the first {indexed_row_count} of {original_row_count} rows were ingested; remaining rows are unavailable."
                )
                if dropped_table:
                    description = (
                        f"Table '{table.title}' exceeded row limits; no rows were indexed."
                    )
                limit_issues.append(
                    IssuePayload(
                        code="table_rows_truncated",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=description,
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={
                            "initial_rows": original_row_count,
                            "indexed_rows": indexed_row_count,
                            "truncated_rows": truncated_amount,
                            "row_cap": table_row_cap,
                            "row_tier": tier,
                            "strategy": strategy,
                        },
                    )
                )
            if removed_columns:
                limit_issues.append(
                    IssuePayload(
                        code="table_columns_truncated",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"{removed_columns} columns were trimmed due to configured limits.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={
                            "removed_columns": removed_columns,
                            "max_columns": effective_config["max_columns"],
                            "row_tier": tier,
                        },
                    )
                )
            if dropped_table:
                truncated_tables += 1
                continue
            limited.append(limited_table)
        metrics = {
            "truncated_tables": truncated_tables,
            "truncated_rows": truncated_rows,
            "truncated_columns": truncated_columns,
        }
        if (truncated_tables or truncated_rows or truncated_columns) and upload:
            logger.info(
                "table.truncation upload=%s rows=%s columns=%s tables=%s limits=%s",
                upload.id,
                truncated_rows,
                truncated_columns,
                truncated_tables,
                effective_config,
            )
        return limited, metrics, limit_issues, summary

    def _limit_table_payload(
        self,
        table: TablePayload,
        *,
        max_rows: int,
        max_columns: int | None,
        column_whitelist: set[str],
    ) -> tuple[TablePayload | None, int, int, bool]:
        schema = list(table.column_schema or [])
        plan: list[tuple[int, str]] = []
        removed_columns = 0
        column_limit = max_columns if isinstance(max_columns, int) and max_columns > 0 else None
        canonical_whitelist = set(column_whitelist or set())
        for idx, column in enumerate(schema):
            label = column or f"column_{idx + 1}"
            canonical = self._canonical_column_name(label, f"column_{idx + 1}")
            if canonical_whitelist and canonical not in canonical_whitelist:
                removed_columns += 1
                continue
            if column_limit is not None and len(plan) >= column_limit:
                removed_columns += 1
                continue
            plan.append((idx, label))
        if not plan:
            return None, len(schema), len(table.rows or []), True
        allowed_indices = [item[0] for item in plan]
        rows = list(table.rows or [])
        new_rows: list[TableRowPayload] = []
        removed_rows = 0
        for row in rows:
            if len(new_rows) >= max_rows:
                removed_rows += 1
                continue
            new_cells: list[TableCellPayload] = []
            cell_lookup = {cell.column_index: cell for cell in row.cells}
            for new_idx, original_idx in enumerate(allowed_indices):
                original = cell_lookup.get(original_idx)
                if original:
                    new_cells.append(
                        TableCellPayload(
                            row_index=row.row_index,
                            column_index=new_idx,
                            column_key=plan[new_idx][1],
                            raw_text=original.raw_text,
                            normalized_value=original.normalized_value,
                            bbox=original.bbox,
                            confidence=original.confidence,
                            metadata=original.metadata,
                        )
                    )
                else:
                    new_cells.append(
                        TableCellPayload(
                            row_index=row.row_index,
                            column_index=new_idx,
                            column_key=plan[new_idx][1],
                            raw_text="",
                            normalized_value={},
                            bbox={},
                            confidence=None,
                            metadata={},
                        )
                    )
            row_text = "\t".join(cell.raw_text for cell in new_cells if cell.raw_text) or row.raw_text
            new_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row_text,
                    metadata=row.metadata,
                    cells=new_cells,
                )
            )
        if not new_rows:
            return None, removed_columns, len(rows), True
        new_table = TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=[label for _, label in plan],
            data_dictionary=table.data_dictionary,
            metadata=table.metadata,
            rows=new_rows,
        )
        return new_table, removed_columns, removed_rows, False

    @staticmethod
    def _looks_like_regex(pattern: str) -> bool:
        return bool(pattern) and (
            pattern.startswith("^")
            or pattern.endswith("$")
            or any(ch in pattern for ch in "[]().*+?|")
        )

    @staticmethod
    def _canonical_column_name(value: str | None, fallback: str | None = None) -> str:
        if isinstance(value, str):
            lowered = re.sub(r"\s+", " ", value.strip().lower())
            if lowered:
                return lowered
        if fallback:
            return fallback.strip().lower()
        return ""
