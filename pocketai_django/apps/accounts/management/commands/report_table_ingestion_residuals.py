from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import KnowledgeSourceType, KnowledgeStatus, KnowledgeUpload
from core.tenancy import tenant_bypass


class Command(BaseCommand):
    help = "Report table-heavy PDF uploads whose baseline metrics show high residual text leakage."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Look back N days from now (default: 30).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum number of rows to print (default: 50).",
        )
        parser.add_argument(
            "--residual-min",
            type=int,
            default=3,
            help="Minimum residual text block count (default: 3).",
        )
        parser.add_argument(
            "--residual-ratio-min",
            type=float,
            default=0.15,
            help="Minimum residual text ratio (default: 0.15).",
        )
        parser.add_argument(
            "--coverage-min",
            type=float,
            default=0.35,
            help="Minimum table bbox coverage ratio (default: 0.35).",
        )
        parser.add_argument(
            "--row-unique-min",
            type=int,
            default=6,
            help="Minimum unique table row evidence count (default: 6).",
        )
        parser.add_argument(
            "--business-id",
            help="Optional business_profile UUID to scope the report.",
        )

    def handle(self, *args, **options):
        lookback_days = max(1, int(options.get("days") or 30))
        limit = max(1, int(options.get("limit") or 50))
        residual_min = max(0, int(options.get("residual_min") or 0))
        residual_ratio_min = max(0.0, min(1.0, float(options.get("residual_ratio_min") or 0.0)))
        coverage_min = max(0.0, min(1.0, float(options.get("coverage_min") or 0.0)))
        row_unique_min = max(0, int(options.get("row_unique_min") or 0))
        business_id = str(options.get("business_id") or "").strip()

        cutoff = timezone.now() - timedelta(days=lookback_days)
        qs = (
            KnowledgeUpload.objects.filter(
                updated_at__gte=cutoff,
                status=KnowledgeStatus.ACTIVE,
                source_type__in=(KnowledgeSourceType.FILE, KnowledgeSourceType.INTEGRATION),
            )
            .order_by("-updated_at")
            .only(
                "id",
                "display_name",
                "business_profile_id",
                "updated_at",
                "ingestion_metadata",
                "source_type",
            )
        )
        if business_id:
            qs = qs.filter(business_profile_id=business_id)

        scanned = 0
        matched: list[dict[str, Any]] = []

        with tenant_bypass():
            for upload in qs.iterator(chunk_size=200):
                scanned += 1
                meta = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, Mapping) else {}
                if str(meta.get("format") or "").lower() != "pdf":
                    continue
                table_extraction = meta.get("table_extraction")
                if not isinstance(table_extraction, Mapping):
                    continue
                baseline = table_extraction.get("baseline_metrics")
                if not isinstance(baseline, Mapping):
                    continue

                residual_blocks = int(baseline.get("residual_text_blocks_count") or 0)
                residual_ratio = float(baseline.get("residual_text_ratio") or 0.0)
                coverage_ratio = float(baseline.get("table_bbox_coverage_ratio") or 0.0)
                row_unique = int(baseline.get("table_row_unique_evidence_count") or 0)
                residual_numeric = int(baseline.get("residual_text_with_numeric_signals_count") or 0)
                if residual_blocks < residual_min:
                    continue
                if residual_ratio < residual_ratio_min:
                    continue
                if coverage_ratio < coverage_min:
                    continue
                if row_unique < row_unique_min:
                    continue

                matched.append(
                    {
                        "upload_id": str(upload.id),
                        "business_id": str(upload.business_profile_id),
                        "name": str(upload.display_name or "")[:48],
                        "updated_at": upload.updated_at.isoformat(),
                        "selected_extractor": str(table_extraction.get("selected_extractor") or ""),
                        "residual_blocks": residual_blocks,
                        "residual_ratio": round(residual_ratio, 4),
                        "residual_numeric": residual_numeric,
                        "coverage_ratio": round(coverage_ratio, 4),
                        "row_unique": row_unique,
                    }
                )

        matched.sort(
            key=lambda row: (
                float(row.get("residual_ratio") or 0.0),
                int(row.get("residual_blocks") or 0),
                int(row.get("residual_numeric") or 0),
            ),
            reverse=True,
        )
        matched = matched[:limit]

        self.stdout.write(
            (
                f"Scanned {scanned} uploads (active file/integration, last {lookback_days}d). "
                f"Matched {len(matched)} high-residual table-heavy PDFs."
            )
        )
        if not matched:
            return

        self.stdout.write(
            "upload_id,business_id,updated_at,selected_extractor,residual_blocks,residual_ratio,"
            "residual_numeric,coverage_ratio,row_unique,name"
        )
        for row in matched:
            self.stdout.write(
                ",".join(
                    [
                        row["upload_id"],
                        row["business_id"],
                        row["updated_at"],
                        row["selected_extractor"],
                        str(row["residual_blocks"]),
                        f"{row['residual_ratio']:.4f}",
                        str(row["residual_numeric"]),
                        f"{row['coverage_ratio']:.4f}",
                        str(row["row_unique"]),
                        row["name"].replace(",", " "),
                    ]
                )
            )
