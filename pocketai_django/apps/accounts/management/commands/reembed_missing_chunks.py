from __future__ import annotations

import logging
import uuid
from typing import Iterable, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import BusinessProfile, KnowledgeUpload, KnowledgeUploadChunk
from apps.services.embeddings import EmbeddingProviderError, build_embedding_service


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Re-embed knowledge chunks (missing-only by default)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of chunks to backfill in this run.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=32,
            help="How many chunks to embed per provider call.",
        )
        parser.add_argument(
            "--provider",
            choices=["auto", "local", "openai"],
            default="auto",
            help="Override embedding provider for retries (default respects EMBED_PROVIDER).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many chunks would be re-embedded without making changes.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Re-embed all chunks (not only missing embeddings).",
        )
        parser.add_argument(
            "--business",
            dest="business",
            help="Optional business UUID or slug to scope the re-embed run.",
        )
        parser.add_argument(
            "--upload",
            dest="upload",
            help="Optional upload UUID to scope the re-embed run.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Confirm running with --all without a scope filter.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        batch_size = max(1, options["batch_size"])
        provider = options["provider"]
        dry_run = bool(options["dry_run"])
        reembed_all = bool(options.get("all"))
        business_filter = options.get("business")
        upload_filter = options.get("upload")
        force = bool(options.get("force"))
        business_id = self._resolve_business_id(business_filter) if business_filter else None
        upload_id = self._resolve_uuid(upload_filter) if upload_filter else None

        if reembed_all and not force and not (business_id or upload_id):
            raise CommandError("--all without --business/--upload requires --force to confirm.")

        try:
            service = build_embedding_service(None if provider == "auto" else provider)
        except EmbeddingProviderError as exc:
            raise CommandError(f"Failed to initialize embedding provider: {exc}") from exc

        if service is None:
            raise CommandError("Embedding provider is not configured. Check EMBED_PROVIDER or install fastembed.")

        qs = KnowledgeUploadChunk.objects.order_by("updated_at", "id")
        if business_id:
            qs = qs.filter(business_profile_id=business_id)
        if upload_id:
            qs = qs.filter(upload_id=upload_id)
        if not reembed_all:
            qs = qs.filter(embedding__isnull=True)
        if limit:
            qs = qs[:limit]

        if not qs.exists():
            if reembed_all:
                message = "No knowledge chunks matched the requested scope."
            else:
                message = "No knowledge chunks are missing embeddings."
            self.stdout.write(self.style.SUCCESS(message))
            return

        total_attempted = 0
        total_updated = 0
        touched_uploads: set[uuid.UUID] = set()
        batch: list[KnowledgeUploadChunk] = []

        for chunk in qs.iterator(chunk_size=batch_size):
            batch.append(chunk)
            touched_uploads.add(chunk.upload_id)
            if len(batch) >= batch_size:
                total_updated += self._process_batch(batch, service, dry_run=dry_run)
                total_attempted += len(batch)
                batch = []

        if batch:
            total_updated += self._process_batch(batch, service, dry_run=dry_run)
            total_attempted += len(batch)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would attempt {total_attempted} chunk(s). No changes applied.")
            )
            return

        self._refresh_ingestion_metadata(touched_uploads)
        self.stdout.write(
            self.style.SUCCESS(f"Re-embedded {total_updated} of {total_attempted} chunk(s).")
        )

    def _process_batch(
        self,
        batch: Sequence[KnowledgeUploadChunk],
        service,
        *,
        dry_run: bool = False,
    ) -> int:
        if not batch:
            return 0
        if dry_run:
            logger.info("[dry-run] Skipping embedding for %s chunks", len(batch))
            return 0
        texts = [chunk.content or "" for chunk in batch]
        try:
            vectors = service.embed_texts(texts)
        except EmbeddingProviderError as exc:
            raise CommandError(f"Embedding request failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - provider specific
            raise CommandError(f"Unexpected embedding failure: {exc}") from exc

        now = timezone.now()
        updated_chunks: list[KnowledgeUploadChunk] = []
        for chunk, vector in zip(batch, vectors):
            normalized = self._normalize_embedding(vector)
            if not normalized:
                continue
            chunk.embedding = normalized
            chunk.updated_at = now
            updated_chunks.append(chunk)

        if updated_chunks:
            KnowledgeUploadChunk.objects.bulk_update(updated_chunks, ["embedding", "updated_at"])
        return len(updated_chunks)

    def _normalize_embedding(self, vector: Sequence[float] | None) -> list[float] | None:
        if not vector:
            return None
        try:
            values = [float(v) for v in vector]
        except (TypeError, ValueError):
            return None
        expected = getattr(settings, "EMBED_DIM", None)
        if expected:
            if len(values) > expected:
                values = values[:expected]
            elif len(values) < expected:
                values = values + [0.0] * (expected - len(values))
        return values

    def _refresh_ingestion_metadata(self, upload_ids: Iterable[uuid.UUID]) -> None:
        unique_ids = {uid for uid in upload_ids if uid}
        if not unique_ids:
            return
        for upload in KnowledgeUpload.objects.filter(id__in=unique_ids):
            metadata = dict(upload.ingestion_metadata or {})
            if not metadata:
                continue
            if upload.chunks.filter(embedding__isnull=True).exists():
                continue
            changed = False
            if metadata.pop("pending_embedding_chunks", None) is not None:
                changed = True
            if metadata.pop("pending_embedding_chunk_count", None) is not None:
                changed = True
            if changed:
                upload.ingestion_metadata = metadata
                upload.save(update_fields=["ingestion_metadata", "updated_at"])

    @staticmethod
    def _resolve_uuid(value: str | None) -> uuid.UUID | None:
        if not value:
            return None
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _resolve_business_id(self, value: str | None) -> uuid.UUID | None:
        if not value:
            return None
        parsed = self._resolve_uuid(value)
        if parsed:
            return parsed
        business = BusinessProfile.objects.filter(slug=str(value).strip()).only("id").first()
        if not business:
            raise CommandError(f"Unknown business '{value}'. Provide a UUID or slug.")
        return business.id
