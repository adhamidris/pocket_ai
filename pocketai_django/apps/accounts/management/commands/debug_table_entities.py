from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.models import (
    KnowledgeEntity,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
)
from apps.knowledge.knowledge_ingestion import KnowledgeIngestionService
from core.tenancy import tenant_bypass


class Command(BaseCommand):
    help = "Inspect how table rows are converted into row entities (column trimming, dropped fields, etc.)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--upload-id",
            required=True,
            help="KnowledgeUpload ID whose tables should be inspected.",
        )
        parser.add_argument(
            "--row-index",
            type=int,
            help="Optional row_index to inspect. When omitted, the first --limit rows are scanned.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Number of rows to inspect when --row-index is not provided (default: 10).",
        )
        parser.add_argument(
            "--table-order",
            type=int,
            help="Table order_index to inspect. Defaults to the first table for the upload.",
        )
        parser.add_argument(
            "--show-values",
            action="store_true",
            help="When set, prints column values for both selected and dropped columns.",
        )

    def handle(self, *args, **options):
        upload_id: str = options["upload_id"]
        row_index: int | None = options.get("row_index")
        limit: int = options["limit"]
        table_order: int | None = options.get("table_order")
        show_values: bool = options["show_values"]

        with tenant_bypass():
            service = KnowledgeIngestionService()

            table_qs = KnowledgeUploadTable.objects.filter(upload_id=upload_id).order_by("order_index")
            if table_order is not None:
                table_qs = table_qs.filter(order_index=table_order)
            table = table_qs.first()
            if not table:
                raise CommandError(
                    f"No KnowledgeUploadTable found for upload_id={upload_id}"
                    + (f" order_index={table_order}" if table_order is not None else "")
                )

            rows_qs = table.rows.order_by("row_index").prefetch_related("cells")
            if row_index is not None:
                rows_qs = rows_qs.filter(row_index=row_index)
            elif limit:
                rows_qs = rows_qs[:limit]

            rows: list[KnowledgeUploadTableRow] = list(rows_qs)
            if not rows:
                raise CommandError("No rows matched the provided filters.")

            self.stdout.write(
                f"Inspecting upload={upload_id} table_order={table.order_index} "
                f"(column count: {len(table.column_schema or [])})"
            )
            self.stdout.write("")

            for row in rows:
                attributes = service._row_model_attributes(row, table.column_schema or [])
                selected_keys = service._select_entity_columns(attributes)
                entity = KnowledgeEntity.objects.filter(
                    upload_id=table.upload_id,
                    metadata__table_metadata__table_order_index=table.order_index,
                    metadata__table_metadata__row_index=row.row_index,
                ).first()
                entity_metadata = entity.metadata if entity and isinstance(entity.metadata, dict) else {}
                entity_attrs = entity_metadata.get("attributes") if isinstance(entity_metadata.get("attributes"), dict) else {}
                entity_cols = len(entity_attrs)
                dropped_keys = [key for key in attributes if key not in selected_keys]
                status = "OK" if entity and entity_cols == len(selected_keys) else "MISMATCH"

                self.stdout.write(
                    f"[{status}] row_index={row.row_index} "
                    f"total_columns={len(attributes)} selected={len(selected_keys)} dropped={len(dropped_keys)} "
                    f"entity_columns={entity_cols}"
                )
                if show_values:
                    self.stdout.write("  Selected columns:")
                    for idx, key in enumerate(selected_keys, start=1):
                        self.stdout.write(f"    {idx:02d}. {key}: {attributes.get(key, '')}")
                    if dropped_keys:
                        self.stdout.write("  Dropped columns:")
                        for key in dropped_keys:
                            self.stdout.write(f"    - {key}: {attributes.get(key, '')}")
                if dropped_keys and not show_values:
                    self.stdout.write(f"  Dropped columns: {', '.join(dropped_keys[:10])}" + (" ..." if len(dropped_keys) > 10 else ""))
                self.stdout.write("")

            self.stdout.write("Inspection complete.")
