from __future__ import annotations

import time
import uuid
from typing import Mapping

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import BusinessProfile
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
)
from core.tenancy import tenant_context


class Command(BaseCommand):
    help = "Backfill Azure AI Search index from Postgres knowledge chunks."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--business-id", required=True, help="BusinessProfile UUID to backfill.")
        parser.add_argument("--upload-id", default=None, help="Optional KnowledgeUpload UUID to backfill.")
        parser.add_argument(
            "--wipe-existing",
            action="store_true",
            help="Delete existing Azure documents for each upload (best-effort; uses current chunk_count).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
            help="Override Azure index batch size (defaults to AZURE_SEARCH_INDEX_BATCH_SIZE).",
        )
        parser.add_argument(
            "--skip-ensure-index",
            action="store_true",
            help="Skip index creation/update before backfill.",
        )

    def handle(self, *args, **options) -> None:
        try:
            business_id = uuid.UUID(str(options["business_id"]))
        except (TypeError, ValueError) as exc:
            raise CommandError("--business-id must be a valid UUID") from exc

        upload_id = None
        if options.get("upload_id"):
            try:
                upload_id = uuid.UUID(str(options["upload_id"]))
            except (TypeError, ValueError) as exc:
                raise CommandError("--upload-id must be a valid UUID") from exc

        try:
            from apps.rag.integrations.azure_ai_search import (
                AzureAISearchConfig,
                delete_upload,
                ensure_index,
                upsert_upload_chunks,
            )
        except Exception as exc:
            raise CommandError(f"Azure AI Search dependency missing: {exc}") from exc

        config = AzureAISearchConfig.from_settings()
        if not config:
            raise CommandError(
                "Azure AI Search is not configured. Set AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_ADMIN_KEY, and AZURE_SEARCH_INDEX_NAME."
            )

        embedding_dim = int(getattr(settings, "EMBED_DIM", 384) or 384)
        if not options.get("skip_ensure_index"):
            ensure_index(config=config, embedding_dim=embedding_dim)

        batch_size = options.get("batch_size")
        if batch_size is None:
            batch_size = int(getattr(settings, "AZURE_SEARCH_INDEX_BATCH_SIZE", 500) or 500)
        batch_size = max(1, min(1000, int(batch_size)))

        business = BusinessProfile.objects.filter(id=business_id).first()
        if not business:
            raise CommandError(f"BusinessProfile not found: {business_id}")

        with tenant_context(business.id):
            uploads_qs = KnowledgeUpload.objects.filter(business_profile=business, status="active").order_by("updated_at")
            if upload_id:
                uploads_qs = uploads_qs.filter(id=upload_id)
            uploads = list(uploads_qs)
        if not uploads:
            raise CommandError("No uploads found to backfill.")

        total_chunks = 0
        start_all = time.perf_counter()
        for upload in uploads:
            business_uuid = uuid.UUID(str(upload.business_profile_id))
            with tenant_context(business_uuid):
                title = (upload.display_name or upload.source_name or upload.external_reference or str(upload.id)).strip()
                format_hint = None
                meta = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, Mapping) else {}
                if isinstance(meta, Mapping):
                    format_hint = str(meta.get("format") or "").strip() or None
                if not format_hint:
                    format_hint = str(getattr(upload, "source_type", "") or "").strip() or None

                chunk_count = int(getattr(upload, "chunk_count", 0) or 0)
                self.stdout.write(f"Backfilling upload={upload.id} chunks={chunk_count}")
                start_one = time.perf_counter()
                if options.get("wipe_existing") and chunk_count:
                    delete_upload(config=config, upload_id=upload.id, chunk_count=chunk_count)

                qs = (
                    KnowledgeUploadChunk.objects.filter(upload=upload, business_profile=business)
                    .order_by("chunk_index")
                    .values("id", "chunk_index", "content", "embedding", "metadata")
                )
                batch: list[dict[str, object]] = []
                indexed = 0
                for row in qs.iterator(chunk_size=2000):
                    if not isinstance(row, Mapping):
                        continue
                    content = str(row.get("content") or "").strip()
                    if not content:
                        continue
                    batch.append(
                        {
                            "chunk_id": row.get("id"),
                            "chunk_index": row.get("chunk_index"),
                            "content": content,
                            "embedding": row.get("embedding"),
                            "metadata": row.get("metadata"),
                        }
                    )
                    if len(batch) >= batch_size:
                        upsert_upload_chunks(
                            config=config,
                            business_id=business_uuid,
                            upload_id=upload.id,
                            title=title,
                            format_hint=format_hint,
                            updated_at=upload.updated_at,
                            chunks=batch,
                        )
                        indexed += len(batch)
                        batch = []
                if batch:
                    upsert_upload_chunks(
                        config=config,
                        business_id=business_uuid,
                        upload_id=upload.id,
                        title=title,
                        format_hint=format_hint,
                        updated_at=upload.updated_at,
                        chunks=batch,
                    )
                    indexed += len(batch)

            total_chunks += indexed
            duration_ms = int((time.perf_counter() - start_one) * 1000.0)
            self.stdout.write(self.style.SUCCESS(f"Indexed upload={upload.id} chunks={indexed} in {duration_ms}ms"))

        total_ms = int((time.perf_counter() - start_all) * 1000.0)
        self.stdout.write(self.style.SUCCESS(f"Azure backfill complete uploads={len(uploads)} chunks={total_chunks} in {total_ms}ms"))
