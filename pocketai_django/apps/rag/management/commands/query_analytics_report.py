from __future__ import annotations

import json
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.knowledge.models import KnowledgeDriftSample
from apps.rag.query_analytics import build_query_analytics_report, format_query_analytics_report


class Command(BaseCommand):
    help = "Summarize query analytics from retrieval drift samples."

    def add_arguments(self, parser):
        parser.add_argument(
            "--business-id",
            dest="business_id",
            help="Optional business UUID to scope analytics.",
        )
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Trailing window in hours (default: 24).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5000,
            help="Maximum retrieval samples to scan (default: 5000).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Print machine-readable JSON output.",
        )

    def handle(self, *args, **options):
        business_id_raw = options.get("business_id")
        hours = int(options.get("hours") or 24)
        limit = int(options.get("limit") or 5000)
        as_json = bool(options.get("as_json"))

        if hours <= 0:
            raise CommandError("--hours must be > 0")
        if limit <= 0:
            raise CommandError("--limit must be > 0")

        business_id: uuid.UUID | None = None
        if business_id_raw:
            try:
                business_id = uuid.UUID(str(business_id_raw).strip())
            except (TypeError, ValueError) as exc:
                raise CommandError("--business-id must be a valid UUID") from exc

        window_start = timezone.now() - timedelta(hours=hours)

        qs = KnowledgeDriftSample.objects.filter(
            sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            observed_at__gte=window_start,
        )
        if business_id is not None:
            qs = qs.filter(business_profile_id=business_id)

        rows = list(
            qs.order_by("-observed_at")
            .values("business_profile_id", "observed_at", "metrics", "metadata")[:limit]
        )

        report = build_query_analytics_report(rows)
        report["window"] = {
            "hours": hours,
            "from": window_start.isoformat(),
            "to": timezone.now().isoformat(),
            "sample_limit": limit,
            "scoped_business_id": str(business_id) if business_id is not None else None,
            "scanned_samples": len(rows),
        }

        if as_json:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
            return

        self.stdout.write(format_query_analytics_report(report))
