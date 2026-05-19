from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.contracts import TablePayload
from apps.knowledge.ingestion.signals import TABLE_SCOPE_CONTRACT_VERSION
from apps.knowledge.models import KnowledgeUploadTable
from apps.knowledge.tables.semantic.column_roles import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_NOTE,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    role_lookup_by_index,
)
from apps.knowledge.tables.semantic.scope_engine import SCOPE_REASON_ABSTAIN, canonical_scope_reason


logger = logging.getLogger(__name__)


class IngestionTableChunkPayloadsMixin:

    def _table_parent_markdown_from_model(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        raw_schema: Sequence[str],
        privacy_rules: Mapping[str, Any],
        max_rows: int,
        max_chars: int,
    ) -> tuple[str, bool]:
        preface: list[str] = []
        if table.section_heading:
            preface.append(f"[Section] {table.section_heading}")
        title = table.title or f"Table {table.order_index}"
        preface.append(f"[Table] {title}")

        headers = [entry[0] for entry in column_map]
        if not headers:
            return "", False

        lines = list(preface)
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        char_count = sum(len(line) + 1 for line in lines)

        data_rows = 0
        truncated = False
        for row in table.rows.all():
            if (row.metadata or {}).get("row_type") == "header":
                continue
            row_attributes = self._row_model_attributes(row, raw_schema)
            if self._row_is_internal(row_attributes, privacy_rules):
                continue
            cell_lookup = {cell.column_index: cell.raw_text for cell in row.cells.all()}
            values = [self._table_cell_text(cell_lookup.get(idx, "")) for _, _, idx in column_map]
            line = "| " + " | ".join(values) + " |"
            if max_rows and data_rows >= max_rows:
                truncated = True
                break
            if max_chars and (char_count + len(line) + 1) > max_chars:
                truncated = True
                break
            lines.append(line)
            char_count += len(line) + 1
            data_rows += 1

        if truncated:
            lines.append("[Table truncated]")
        return "\n".join(lines).strip(), truncated

    def _table_row_chunk_payloads(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        raw_schema: Sequence[str],
        privacy_rules: Mapping[str, Any],
        base_metadata: Mapping[str, Any],
        max_rows: int,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        title = table.title or f"Table {table.order_index}"
        data_rows = 0
        suppressed_low_signal_rows = 0
        shard_size = max(1, int(max_rows or 1))
        table_rows = list(table.rows.all())
        table_data_dictionary = (
            getattr(table, "data_dictionary", {})
            if isinstance(getattr(table, "data_dictionary", {}), Mapping)
            else {}
        )
        cached_column_roles = table_data_dictionary.get("column_roles") if isinstance(table_data_dictionary, Mapping) else None
        inferred_column_roles = self._infer_table_column_roles(
            table_rows=table_rows,
            column_map=column_map,
            cached_roles=cached_column_roles if isinstance(cached_column_roles, (list, tuple)) else None,
        )
        role_lookup = role_lookup_by_index(inferred_column_roles)
        descriptor_labels: list[str] = []
        qualifier_labels: list[str] = []
        inferred_segment_labels: list[str] = []
        for label, _canonical, raw_idx in column_map:
            role = str((role_lookup.get(raw_idx) or {}).get("role") or "").strip()
            if role == COLUMN_ROLE_SCOPE_DIMENSION:
                inferred_segment_labels.append(label)
                continue
            if role == COLUMN_ROLE_QUALIFIER:
                qualifier_labels.append(label)
                continue
            if role == COLUMN_ROLE_DESCRIPTOR:
                descriptor_labels.append(label)
                continue
            if role == COLUMN_ROLE_NOTE:
                continue

        contextual_labels = set(dict.fromkeys(descriptor_labels + qualifier_labels))
        if not inferred_segment_labels:
            contextual_index_set = {
                idx
                for idx, (label, _canonical, _raw_idx) in enumerate(column_map)
                if label in contextual_labels
            }
            inferred_segment_labels = [
                label
                for idx, (label, _canonical, _raw_idx) in enumerate(column_map)
                if idx not in contextual_index_set
            ]
        active_subsection: str = ""
        for row in table_rows:
            if (row.metadata or {}).get("row_type") == "header":
                continue

            # ── Section header rows: track label, skip as data ──
            row_model_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            if row_model_meta.get("row_type") == "section_header":
                row_cells = list(row.cells.all())
                cell_lookup = {cell.column_index: cell.raw_text for cell in row_cells}
                label = ""
                for _, _, idx in column_map:
                    label = self._table_cell_text(cell_lookup.get(idx, ""))
                    if label:
                        break
                active_subsection = self._table_subsection_label(label)
                continue

            row_attributes = self._row_model_attributes(row, raw_schema)
            if self._row_is_internal(row_attributes, privacy_rules):
                continue
            row_cells = list(row.cells.all())
            cell_lookup = {cell.column_index: cell.raw_text for cell in row_cells}
            pairs: list[str] = []
            value_by_label: dict[str, str] = {}
            # Extract row_label from first column (typically the row identifier/name)
            row_label = ""
            for label, _, idx in column_map:
                value = self._table_cell_text(cell_lookup.get(idx, ""))
                if value:
                    pairs.append(f"{label}: {value}")
                    value_by_label[label] = value
                    # First column value becomes the row_label for search indexing
                    if not row_label:
                        row_label = value
            if not pairs:
                continue

            # Fallback: detect uniform-value rows not caught by annotation
            unique_values = set(value_by_label.values())
            if len(unique_values) == 1 and len(value_by_label) >= 4:
                active_subsection = self._table_subsection_label(next(iter(unique_values), ""))
                continue
            scope_contract = self._resolve_row_scope_contract(
                row_model_meta=row_model_meta,
                value_by_label=value_by_label,
                contextual_labels=contextual_labels,
                inferred_segment_labels=inferred_segment_labels,
            )
            inferred_scope_columns = list(scope_contract.get("inferred_scope_columns") or [])
            observed_value_columns = list(scope_contract.get("observed_value_columns") or [])
            qualifier_columns = list(scope_contract.get("qualifier_columns") or [])
            scope_dimension_columns = list(scope_contract.get("scope_dimension_columns") or [])
            scope_reason = canonical_scope_reason(scope_contract.get("scope_reason"))
            scope_confidence = self._coerce_scope_confidence(scope_contract.get("scope_confidence"))

            fee_value = self._table_cell_text(str(row_model_meta.get("scope_value") or ""))
            if not fee_value:
                scoped_values = [
                    value_by_label.get(label, "")
                    for label in inferred_scope_columns
                    if value_by_label.get(label, "")
                ]
                unique_values = list(dict.fromkeys(scoped_values))
                if len(unique_values) == 1:
                    fee_value = unique_values[0]

            row_signal_score, row_signal_diag = self._table_row_signal_score(
                value_by_label=value_by_label,
                inferred_scope_columns=inferred_scope_columns,
                observed_value_columns=observed_value_columns,
                fee_value=fee_value,
            )
            structural_row_diag = self._table_row_structural_context_profile(
                value_by_label=value_by_label,
                scope_dimension_columns=(
                    scope_dimension_columns
                    or row_model_meta.get("scope_dimension_columns")
                    or inferred_scope_columns
                ),
                fee_value=fee_value,
                row_signal_diag=row_signal_diag,
            )
            row_signal_override = self._collapsed_native_text_row_override_applies(
                table=table,
                value_by_label=value_by_label,
                row_signal_diag=row_signal_diag,
                structural_row_diag=structural_row_diag,
            )
            if (
                row_signal_diag["pair_count"] < self.table_row_signal_min_pairs
                and row_signal_score < self.table_row_signal_min_score
            ):
                if not row_signal_override:
                    suppressed_low_signal_rows += 1
                    continue

            evidence_cell_ids = [
                str(cell.id)
                for cell in row_cells
                if str(cell.raw_text or "").strip()
            ]
            preface = []
            # Keep derived headings out of row-level chunks to avoid broad-term noise.
            section_heading = str(getattr(table, "section_heading", "") or "").strip()
            if section_heading:
                table_meta = getattr(table, "metadata", None) if isinstance(getattr(table, "metadata", None), Mapping) else {}
                derived_heading = str((table_meta or {}).get("derived_section_heading") or "").strip()
                if not derived_heading or derived_heading != section_heading:
                    preface.append(f"[Section] {section_heading}")
            if active_subsection:
                preface.append(f"[SubSection] {active_subsection}")
            preface.append(f"[Table] {title}")
            preface.append(f"[Row] {row.row_index}")
            if inferred_scope_columns:
                preface.append(f"[Scope] {', '.join(inferred_scope_columns)}")
            text = "\n".join(preface + pairs)
            shard_index = data_rows // shard_size
            shard_offset = data_rows % shard_size
            row_meta = dict(base_metadata)
            row_meta.update(
                {
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "is_table_preview": False,
                    "table_row_index": row.row_index,
                    "row_label": row_label,  # Enable row-label search matching
                    "search_tier": "drill_down",
                    "table_row_contract_version": scope_contract.get("contract_version") or TABLE_SCOPE_CONTRACT_VERSION,
                    "table_row_observed_value_columns": observed_value_columns,
                    "table_row_qualifier_columns": qualifier_columns,
                    "table_row_scope_dimension_columns": scope_dimension_columns,
                    "table_row_inferred_scope_columns": inferred_scope_columns,
                    "table_row_scope_reason": scope_reason or SCOPE_REASON_ABSTAIN,
                    "table_row_scope_confidence": scope_confidence,
                    "table_row_fee_value": fee_value,
                    "table_row_evidence_cell_ids": evidence_cell_ids,
                    "table_row_shard_index": int(shard_index),
                    "table_row_shard_size": int(shard_size),
                    "table_row_shard_offset": int(shard_offset),
                    "table_row_signal_filter_enabled": True,
                    "table_row_signal_min_pairs": int(self.table_row_signal_min_pairs),
                    "table_row_signal_min_score": float(self.table_row_signal_min_score),
                    "table_row_signal_pair_count": int(row_signal_diag["pair_count"]),
                    "table_row_signal_numeric_value_count": int(row_signal_diag["numeric_value_count"]),
                    "table_row_signal_value_keyword_count": int(row_signal_diag["keyword_value_count"]),
                    "table_row_signal_scope_column_count": int(row_signal_diag["scope_column_count"]),
                    "table_row_signal_observed_value_column_count": int(
                        row_signal_diag["observed_value_column_count"]
                    ),
                    "table_row_signal_has_fee_value": bool(row_signal_diag["has_fee_value"]),
                    "table_row_signal_score": float(row_signal_diag["score"]),
                    "table_row_signal_override": bool(row_signal_override),
                    "table_row_is_structural_context": bool(
                        structural_row_diag["is_structural_context"]
                    ),
                    "table_row_structural_scope_label_count": int(
                        structural_row_diag["scope_label_count"]
                    ),
                    "table_row_structural_scope_echo_count": int(
                        structural_row_diag["scope_echo_count"]
                    ),
                    "table_row_structural_scope_numeric_count": int(
                        structural_row_diag["scope_numeric_count"]
                    ),
                    "table_row_structural_scope_echo_ratio": float(
                        structural_row_diag["scope_echo_ratio"]
                    ),
                }
            )
            payloads.append({"text": text, "metadata": row_meta})
            data_rows += 1
        if suppressed_low_signal_rows > 0:
            table_id = getattr(table, "id", None)
            page_number = None
            page = getattr(table, "page", None)
            if page is not None:
                page_number = getattr(page, "page_number", None)
            logger.info(
                "table.row_signal_filter table_id=%s page=%s kept=%s suppressed=%s min_pairs=%s min_score=%.2f",
                table_id,
                page_number,
                len(payloads),
                suppressed_low_signal_rows,
                self.table_row_signal_min_pairs,
                self.table_row_signal_min_score,
            )
        return payloads

    def _table_summary_chunk_payloads(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        base_metadata: Mapping[str, Any],
        total_data_rows: int,
        row_label_entries: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build one primary-search summary chunk per row shard."""
        column_names = [entry[0] for entry in column_map]
        if not column_names:
            return []

        title = table.title or f"Table {table.order_index}"
        grouped_labels: dict[int, list[str]] = {}
        grouped_row_indices: dict[int, list[int]] = {}
        for entry in row_label_entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                shard_index = int(entry.get("shard_index") or 0)
            except (TypeError, ValueError):
                shard_index = 0
            label = self._sanitize_text(entry.get("label") or "").strip()
            if label:
                grouped_labels.setdefault(shard_index, []).append(label)
            raw_row_index = entry.get("row_index")
            try:
                row_index = int(raw_row_index)
            except (TypeError, ValueError):
                row_index = None  # type: ignore[assignment]
            if row_index is not None:
                grouped_row_indices.setdefault(shard_index, []).append(row_index)

        if not grouped_labels and not grouped_row_indices:
            grouped_labels[0] = []

        shard_ids = sorted(set(grouped_labels.keys()) | set(grouped_row_indices.keys()))
        total_shards = max(1, len(shard_ids))
        payloads: list[dict[str, Any]] = []
        for position, shard_index in enumerate(shard_ids, start=1):
            lines: list[str] = []
            if table.section_heading:
                lines.append(f"[Section] {table.section_heading}")
            lines.append(f"[Table] {title}")
            lines.append(f"[Columns] {' | '.join(column_names)}")
            lines.append(f"[Rows] {total_data_rows} data rows")
            lines.append(f"[Shard] {position}/{total_shards}")

            row_indices = grouped_row_indices.get(shard_index) or []
            if row_indices:
                lines.append(f"[Row Range] {min(row_indices)} - {max(row_indices)}")
                lines.append(f"[Rows In Shard] {len(row_indices)}")

            shard_labels = grouped_labels.get(shard_index) or []
            cap = self.table_summary_max_row_labels
            if shard_labels:
                sample = shard_labels[:cap]
                label_text = ", ".join(sample)
                if len(shard_labels) > cap:
                    label_text += f", ... and {len(shard_labels) - cap} more"
                lines.append(f"[Row Labels] {label_text}")

            summary_meta = dict(base_metadata)
            summary_meta.update(
                {
                    "content_source": "table_summary",
                    "table_chunk_role": "summary",
                    "is_table_preview": True,
                    "search_tier": "primary",
                    "table_total_rows": total_data_rows,
                    "table_row_shard_index": int(shard_index),
                    "table_shard_position": int(position),
                    "table_shard_count": int(total_shards),
                }
            )
            if row_indices:
                summary_meta["table_row_shard_start_row"] = int(min(row_indices))
                summary_meta["table_row_shard_end_row"] = int(max(row_indices))
                summary_meta["table_row_shard_row_count"] = int(len(row_indices))

            payloads.append({"text": "\n".join(lines), "metadata": summary_meta})
        return payloads

    def _table_summary_chunk_payload(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        base_metadata: Mapping[str, Any],
        total_data_rows: int,
        row_labels: Sequence[str],
    ) -> dict[str, Any] | None:
        """Backward-compatible wrapper for a single summary chunk.

        New ingestion paths should use `_table_summary_chunk_payloads` for
        row-sharded summaries.
        """
        entries = [{"label": label, "row_index": idx, "shard_index": 0} for idx, label in enumerate(row_labels)]
        payloads = self._table_summary_chunk_payloads(
            table=table,
            column_map=column_map,
            base_metadata=base_metadata,
            total_data_rows=total_data_rows,
            row_label_entries=entries,
        )
        return payloads[0] if payloads else None

    def _table_preview_text(
        self,
        tables: Sequence[TablePayload],
        *,
        row_limit: int = 5,
        rules: Mapping[str, Any] | None = None,
    ) -> str:
        if not tables:
            return ""
        lines: list[str] = []
        for table in tables:
            sheet_role = str((table.metadata or {}).get("sheet_role") or "")
            if sheet_role == "reference_hidden":
                continue
            visible_columns = table.column_schema
            if rules:
                visible_columns = [col for col in table.column_schema if not self._column_is_sensitive(col, rules)]
            if not visible_columns:
                continue
            if table.title:
                lines.append(f"[Table] {table.title}")
            header_line = "\t".join(visible_columns)
            if header_line.strip():
                lines.append(header_line)
            effective_row_limit = row_limit
            if sheet_role == "instructional":
                effective_row_limit = min(row_limit, 3)
            elif sheet_role == "summary":
                effective_row_limit = min(row_limit, 4)
            elif sheet_role == "form_like":
                effective_row_limit = min(row_limit, 4)
            for row in table.rows[:effective_row_limit]:
                attributes = self._row_attributes_from_table(row, table.column_schema)
                if rules and self._row_is_internal(attributes, rules):
                    continue
                values = [attributes.get(column, "") for column in visible_columns]
                if any(values):
                    lines.append("\t".join(values))
            lines.append("")
        return "\n".join(lines).strip()
