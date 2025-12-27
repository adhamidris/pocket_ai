from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import KnowledgeUpload
from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
from core.tenancy import tenant_bypass, tenant_context


class Command(BaseCommand):
    help = "Backfill KnowledgeEntity/KnowledgeAlias records from existing structured tables."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--business-id",
            help="Limit backfill to a single business_profile id",
        )
        parser.add_argument(
            "--upload-id",
            help="Limit backfill to a single upload id",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Optional limit on number of uploads to process",
        )

    def handle(self, *args, **options):
        business_id = options.get("business_id")
        upload_id = options.get("upload_id")
        limit = options.get("limit") or 0

        tenant_scope = tenant_context(business_id) if business_id else tenant_bypass()
        with tenant_scope:
            uploads = (
                KnowledgeUpload.objects.filter(tables__isnull=False)
                .distinct()
                .prefetch_related("tables__rows__cells")
                .select_related("business_profile")
                .order_by("created_at")
            )
            if business_id:
                uploads = uploads.filter(business_profile_id=business_id)
            if upload_id:
                uploads = uploads.filter(id=upload_id)
            if limit > 0:
                uploads = uploads[:limit]

            service = KnowledgeIngestionService()

            processed = 0
            total_entities = 0
            total_aliases = 0

            for upload in uploads.iterator(chunk_size=50):
                tables = self._to_payloads(upload)
                if not tables:
                    continue
                entities = service._table_row_entities(
                    tables,
                    business_profile=upload.business_profile,
                    upload=upload,
                )
                if not entities:
                    continue
                with transaction.atomic():
                    stats = service._persist_entities(upload, entities, chunks=[])
                    ingestion_metadata: dict[str, Any] = dict(upload.ingestion_metadata or {})
                    if stats:
                        if stats.get("alias_count") is not None:
                            ingestion_metadata["alias_count"] = stats.get("alias_count")
                        if stats.get("alias_sources"):
                            ingestion_metadata["alias_patterns_used"] = stats.get("alias_sources")
                    upload.ingestion_metadata = ingestion_metadata
                    upload.save(update_fields=["ingestion_metadata", "updated_at"])
                processed += 1
                total_entities += stats.get("entity_count", 0)
                total_aliases += stats.get("alias_count", 0)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Backfilled upload={upload.id} entities={stats.get('entity_count', 0)} aliases={stats.get('alias_count', 0)}"
                    )
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. uploads={processed} entities={total_entities} aliases={total_aliases}"
                )
            )

    def _to_payloads(self, upload: KnowledgeUpload) -> list[TablePayload]:
        payloads: list[TablePayload] = []
        for table in upload.tables.all().order_by("order_index"):
            rows: list[TableRowPayload] = []
            for row in table.rows.all().order_by("row_index"):
                cells: list[TableCellPayload] = []
                for cell in row.cells.all().order_by("column_index"):
                    cells.append(
                        TableCellPayload(
                            row_index=row.row_index,
                            column_index=cell.column_index,
                            column_key=cell.column_key,
                            raw_text=cell.raw_text,
                            normalized_value=cell.normalized_value or {},
                            bbox=cell.bbox or {},
                            confidence=cell.confidence,
                            metadata=cell.metadata or {},
                        )
                    )
                rows.append(
                    TableRowPayload(
                        row_index=row.row_index,
                        page_number=row.page_number,
                        bbox=row.bbox or {},
                        raw_text=row.raw_text,
                        metadata=row.metadata or {},
                        cells=cells,
                    )
                )
            payloads.append(
                TablePayload(
                    order_index=table.order_index,
                    title=table.title,
                    section_heading=table.section_heading,
                    page_number=getattr(table.page, "page_number", None),
                    bbox=table.bbox or {},
                    column_schema=list(table.column_schema or []),
                    data_dictionary=table.data_dictionary or {},
                    metadata=table.metadata or {},
                    rows=rows,
                )
            )
        return payloads
