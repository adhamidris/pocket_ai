from __future__ import annotations

from typing import Mapping

from django.db.models import Prefetch

from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
)


class ContentTableSerializationMixin:

    def _serialize_structured_tables_with_rows(
        self,
        upload: KnowledgeUpload,
        *,
        max_tables: int = 3,
        max_rows: int = 5,
        max_columns: int = 8,
    ) -> list[dict[str, object]]:
        tables_manager = getattr(upload, "tables", None)
        if not hasattr(tables_manager, "order_by"):
            return []
        enriched: list[dict[str, object]] = []
        table_qs = tables_manager.order_by("order_index")
        for table in table_qs[:max_tables]:
            rows_manager = getattr(table, "rows", None)
            rows_sample: list[list[str]] = []
            rows_qs = rows_manager.order_by("row_index") if hasattr(rows_manager, "order_by") else None
            if rows_qs is not None:
                limited_rows = rows_qs[:max_rows] if max_rows else rows_qs
                for row in limited_rows:
                    cells_manager = getattr(row, "cells", None)
                    cells_qs = cells_manager.order_by("column_index") if hasattr(cells_manager, "order_by") else None
                    if cells_qs is None:
                        rows_sample.append([])
                        continue
                    limited_cells = cells_qs[:max_columns] if max_columns else cells_qs
                    rows_sample.append([cell.raw_text for cell in limited_cells])
            enriched.append(
                {
                    "order_index": table.order_index,
                    "title": table.title or f"Table {table.order_index}",
                    "section_heading": table.section_heading or "",
                    "page_number": table.page.page_number if table.page else None,
                    "column_schema": list(table.column_schema or []),
                    "row_count": rows_manager.count() if hasattr(rows_manager, "count") else 0,
                    "rowsSample": rows_sample,
                    "bbox": dict(table.bbox or {}),
                    "metadata": dict(table.metadata or {}),
                }
            )
        return enriched

    def _table_row_sample(
        self,
        chunk: KnowledgeUploadChunk,
        *,
        max_columns: int = 6,
        max_rows: int = 1,
        query: str | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        chunk_metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        if not chunk_metadata.get("is_table_chunk"):
            return tuple()

        table_id = chunk_metadata.get("table_id")
        table = None
        if table_id:
            try:
                table = KnowledgeUploadTable.objects.filter(id=table_id).first()
            except Exception:
                table = None
        if table is None:
            upload = chunk.upload
            tables_manager = getattr(upload, "tables", None)
            if not hasattr(tables_manager, "order_by"):
                return tuple()
            try:
                table = tables_manager.order_by("order_index").first()
            except Exception:
                return tuple()
        if table is None:
            return tuple()

        target_row_index = chunk_metadata.get("table_row_index")
        if isinstance(target_row_index, str) and target_row_index.isdigit():
            target_row_index = int(target_row_index)
        if not isinstance(target_row_index, int):
            target_row_index = None

        max_rows = max(1, int(max_rows))
        max_columns = max(2, int(max_columns))
        row_limit = max(1, min(50, max_rows * 25))

        try:
            row_qs = (
                table.rows.filter(row_index__isnull=False)
                .exclude(metadata__row_type="header")
                .order_by("row_index")
                .prefetch_related(
                    Prefetch(
                        "cells",
                        queryset=KnowledgeUploadTableCell.objects.order_by("column_index"),
                    )
                )
            )
            if target_row_index is not None:
                row_qs = row_qs.filter(row_index=target_row_index)
                selected_rows = list(row_qs[:max_rows])
            else:
                rows = list(row_qs[:row_limit])
                if not rows:
                    return tuple()
                if not query or len(rows) <= 1:
                    selected_rows = rows[:max_rows]
                else:
                    query_tokens = {tok for tok in (query or "").lower().split() if tok and len(tok) >= 3}
                    scored: list[tuple[int, int]] = []
                    for idx, row in enumerate(rows):
                        score = 0
                        for cell in row.cells.all():
                            cell_text = str(cell.raw_text or "").lower()
                            for tok in query_tokens:
                                if tok in cell_text:
                                    score += 1
                        scored.append((score, idx))
                    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                    picked = [rows[idx] for score, idx in scored[:max_rows] if score > 0]
                    selected_rows = picked if picked else rows[:max_rows]

            if not selected_rows:
                return tuple()

            all_cells = list(selected_rows[0].cells.all()) if selected_rows else []
            ordered_columns = [
                (cell.column_index, (cell.column_key or f"column_{(cell.column_index or 0) + 1}"))
                for cell in all_cells
            ]
            ordered_columns.sort(key=lambda item: item[0])
            column_labels = [label for _, label in ordered_columns]
            if len(column_labels) > max_columns:
                column_labels = column_labels[:max_columns]

            rows_payload: list[list[str]] = []
            for row in selected_rows:
                cell_lookup = {cell.column_index: (cell.raw_text or "") for cell in row.cells.all()}
                values: list[str] = []
                for col_idx, _label in ordered_columns[: len(column_labels)]:
                    values.append(str(cell_lookup.get(col_idx, "")))
                rows_payload.append(values)

            structured_table = {
                "title": table.title or table.section_heading or f"Table {table.order_index}",
                "columns": column_labels,
                "rows": rows_payload,
                "metadata": {
                    "table_id": str(table.id),
                    "table_order_index": table.order_index,
                    "page_number": table.page.page_number if table.page else None,
                    "row_indexes": [row.row_index for row in selected_rows],
                    "chunk_role": chunk_metadata.get("table_chunk_role"),
                },
            }
            table_meta = table.metadata if isinstance(getattr(table, "metadata", None), Mapping) else {}
            if table_meta:
                quality_score = table_meta.get("quality_score")
                if isinstance(quality_score, (int, float)):
                    structured_table["metadata"]["quality_score"] = float(quality_score)
                is_decorative = table_meta.get("is_decorative")
                if isinstance(is_decorative, bool):
                    structured_table["metadata"]["is_decorative"] = is_decorative
                signals = table_meta.get("quality_signals")
                if isinstance(signals, Mapping) and signals.get("column_misalignment") is True:
                    structured_table["metadata"]["column_misalignment"] = True
            return (structured_table,)
        except Exception:
            return tuple()

    @staticmethod
    def _render_structured_tables_text(upload: KnowledgeUpload, *, max_preview_rows: int = 5) -> str:
        tables_manager = getattr(upload, "tables", None)
        if not hasattr(tables_manager, "all"):
            return ""
        tables = list(tables_manager.all())
        if not tables:
            return ""
        lines = ["[Structured Tables]"]
        for table in tables:
            page_number = table.page.page_number if table.page else None
            title = table.title or f"Table {table.order_index}"
            header = ", ".join((table.column_schema or [])[:10])
            lines.append(f"- {title} (page {page_number or 'n/a'}) columns: {header or 'unspecified'}")
            rows = list(table.rows.all()) if hasattr(table, "rows") else []
            preview_rows = rows[:max_preview_rows]
            for row in preview_rows:
                cells = list(row.cells.all()) if hasattr(row, "cells") else []
                cell_values = [cell.raw_text for cell in sorted(cells, key=lambda cell: cell.column_index)]
                if cell_values:
                    lines.append(f"    • {' | '.join(cell_values)}")
            if len(rows) > max_preview_rows:
                lines.append(f"    • … ({len(rows) - max_preview_rows} more rows)")
        return "\n".join(lines)
