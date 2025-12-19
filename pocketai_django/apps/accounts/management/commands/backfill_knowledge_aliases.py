from __future__ import annotations

import time
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import KnowledgeStatus, KnowledgeUpload
from apps.knowledge.knowledge_ingestion import KnowledgeIngestionError, KnowledgeIngestionService


class Command(BaseCommand):
    help = "Replay ingestion to regenerate entity-aware chunks and alias indexes."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--business-id",
            action="append",
            dest="business_ids",
            default=[],
            help="Limit processing to specific business IDs.",
        )
        parser.add_argument(
            "--upload-id",
            action="append",
            dest="upload_ids",
            default=[],
            help="Explicit upload IDs to backfill.",
        )
        parser.add_argument(
            "--resume-after",
            dest="resume_after",
            help="Skip uploads until this upload ID is encountered.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after processing this many uploads.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Sleep after each batch of this many uploads.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to pause between batches (helps throttle load).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report the number of entities/aliases that would be produced.",
        )

    def handle(self, *args, **options):
        business_ids: list[str] = options.get("business_ids") or []
        upload_ids: list[str] = options.get("upload_ids") or []
        resume_after: str | None = options.get("resume_after")
        limit = int(options.get("limit") or 0)
        batch_size = max(1, int(options.get("batch_size") or 10))
        throttle = float(options.get("sleep") or 0.0)
        dry_run = bool(options.get("dry_run"))

        queryset = KnowledgeUpload.objects.filter(status=KnowledgeStatus.ACTIVE).order_by("created_at")
        if business_ids:
            queryset = queryset.filter(business_profile_id__in=business_ids)
        if upload_ids:
            queryset = queryset.filter(id__in=upload_ids)
        total = queryset.count()
        if total == 0:
            raise CommandError("No uploads matched the provided filters.")

        service = KnowledgeIngestionService()
        processed = 0
        skipped = 0
        resumed = not bool(resume_after)
        last_sleep = 0
        batch_counter = 0
        self.stdout.write(f"Starting backfill for {total} upload(s). Dry-run={dry_run}")
        for upload in queryset.iterator():
            if not resumed:
                if str(upload.id) == resume_after:
                    resumed = True
                else:
                    skipped += 1
                    continue
            if limit and processed >= limit:
                break
            batch_counter += 1
            self.stdout.write(f"[{processed + 1}/{total}] Upload {upload.id} ({upload.display_name or upload.source_name})")
            try:
                extraction = service._extract_upload(upload)
            except KnowledgeIngestionError as exc:
                self.stderr.write(f" ! Failed to extract upload {upload.id}: {exc}")
                continue
            except Exception as exc:  # pragma: no cover - defensive logging
                self.stderr.write(f" ! Unexpected failure for upload {upload.id}: {exc}")
                continue

            entity_count = len(extraction.entities or [])
            alias_estimate = sum(len(entity.get("aliases") or []) for entity in (extraction.entities or []))
            truncated = extraction.metadata.get("json_entities_truncated")
            if dry_run:
                self.stdout.write(
                    f"   WOULD INGEST entities={entity_count} aliases~= {alias_estimate} truncated={truncated or 0}"
                )
                processed += 1
            else:
                try:
                    service._persist_extraction(upload, extraction)
                except KnowledgeIngestionError as exc:
                    self.stderr.write(f" ! Persist failed for upload {upload.id}: {exc}")
                    continue
                processed += 1
                upload.refresh_from_db(fields=["ingestion_metadata"])
                metadata = upload.ingestion_metadata or {}
                pending = metadata.get("pending_embedding_chunk_count") if isinstance(metadata, dict) else None
                anomaly_flags: list[str] = []
                if truncated:
                    anomaly_flags.append(f"truncated_entities={truncated}")
                if isinstance(pending, int) and pending > 0:
                    anomaly_flags.append(f"pending_embeddings={pending}")
                if anomaly_flags:
                    self.stderr.write(f"   WARN: {'; '.join(anomaly_flags)}")
            if batch_counter >= batch_size:
                batch_counter = 0
                if throttle > 0:
                    time.sleep(throttle)
                    last_sleep = throttle
        summary = f"Processed {processed} upload(s)"
        if skipped:
            summary += f", skipped {skipped} prior to resume point"
        if limit and processed >= limit:
            summary += " (limit reached)"
        self.stdout.write(self.style.SUCCESS(summary))
        if throttle and not dry_run and processed:
            self.stdout.write(f"Throttling delays applied: last_sleep={last_sleep}s")
        if resume_after and not resumed:
            self.stderr.write(f"Resume marker {resume_after} was not encountered.")
