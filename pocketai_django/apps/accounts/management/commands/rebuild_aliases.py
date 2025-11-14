from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import KnowledgeUpload
from apps.services.knowledge_ingestion import KnowledgeIngestionError, KnowledgeIngestionService


class Command(BaseCommand):
    help = "Rebuild knowledge entities and aliases for one or more uploads."

    def add_arguments(self, parser):
        parser.add_argument("--upload-id", action="append", dest="upload_ids", default=[], help="Specific upload ID(s) to rebuild.")
        parser.add_argument("--business-id", action="append", dest="business_ids", default=[], help="Limit rebuild to one or more businesses.")

    def handle(self, *args, **options):
        upload_ids: list[str] = options.get("upload_ids") or []
        business_ids: list[str] = options.get("business_ids") or []

        uploads = KnowledgeUpload.objects.all().select_related("file_detail", "url_detail", "business_profile")
        if business_ids:
            uploads = uploads.filter(business_profile_id__in=business_ids)
        if upload_ids:
            uploads = uploads.filter(id__in=upload_ids)
        uploads = list(uploads.order_by("created_at"))
        if not uploads:
            raise CommandError("No uploads matched the provided filters.")

        service = KnowledgeIngestionService()
        rebuilt = 0
        for upload in uploads:
            self.stdout.write(f"Rebuilding upload {upload.id} ({upload.display_name or upload.source_name})...")
            try:
                extraction = service._extract_upload(upload)
                service._persist_extraction(upload, extraction)
            except KnowledgeIngestionError as exc:
                self.stderr.write(f"Failed to rebuild upload {upload.id}: {exc}")
                continue
            rebuilt += 1
        self.stdout.write(self.style.SUCCESS(f"Rebuilt {rebuilt} upload(s)."))
