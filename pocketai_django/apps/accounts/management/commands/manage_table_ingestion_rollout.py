from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import BusinessProfile
from apps.knowledge.ingestion.benchmark import (
    capture_upload_snapshot,
    compare_snapshots,
    load_snapshot_from_json,
    quality_gate_thresholds,
)
from apps.knowledge.models import KnowledgeUpload

PHASE6_FLAG_BUNDLE: tuple[str, ...] = (
    "rag_shadow_ingestion",
    "rag_eval_logging",
)


class Command(BaseCommand):
    help = "Phase 6 rollout controller for table-ingestion observability flags (cohort plan/apply/dashboard/rollback)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--action",
            choices=("plan", "enable", "dashboard", "rollback"),
            default="plan",
            help="Rollout action: inspect, enable canary flags, evaluate dashboard, or rollback.",
        )
        parser.add_argument(
            "--business-id",
            action="append",
            dest="business_ids",
            default=[],
            help="Target a specific business ID (repeatable).",
        )
        parser.add_argument(
            "--cohort",
            default="",
            help="Filter by metadata.cohort value (case-sensitive).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_businesses",
            help="Acknowledge that all businesses should be considered.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview updates without writing to the database.",
        )
        parser.add_argument(
            "--baseline-json",
            default="",
            help="Baseline snapshot JSON path used by --action dashboard.",
        )
        parser.add_argument(
            "--window-hours",
            type=int,
            default=168,
            help="Lookback window for dashboard uploads (default: 168h).",
        )
        parser.add_argument(
            "--max-uploads-per-business",
            type=int,
            default=3,
            help="Max recent uploads to evaluate per business in dashboard mode.",
        )
        parser.add_argument(
            "--focus-row-start",
            type=int,
            default=None,
            help="Optional inclusive lower row bound for snapshot comparison.",
        )
        parser.add_argument(
            "--focus-row-end",
            type=int,
            default=None,
            help="Optional inclusive upper row bound for snapshot comparison.",
        )
        parser.add_argument(
            "--output",
            default="",
            help="Optional JSON output file for dashboard summary.",
        )
        parser.add_argument(
            "--enforce",
            action="store_true",
            help="Fail when dashboard pass rate drops below --min-pass-rate.",
        )
        parser.add_argument(
            "--min-pass-rate",
            type=float,
            default=0.95,
            help="Minimum acceptable pass rate in dashboard mode when --enforce is used.",
        )
        parser.add_argument(
            "--min-row-recall",
            type=float,
            default=None,
            help="Override minimum row recall threshold.",
        )
        parser.add_argument(
            "--min-row-order-stability",
            type=float,
            default=None,
            help="Override minimum row-order stability threshold.",
        )
        parser.add_argument(
            "--min-scope-f1",
            type=float,
            default=None,
            help="Override minimum scope F1 threshold.",
        )
        parser.add_argument(
            "--min-critical-value-coverage",
            type=float,
            default=None,
            help="Override minimum critical-value coverage threshold.",
        )
        parser.add_argument(
            "--min-scope-metadata-coverage",
            type=float,
            default=None,
            help="Override minimum scope metadata coverage threshold.",
        )

    def handle(self, *args, **options):
        action = str(options.get("action") or "plan").strip().lower()
        dry_run = bool(options.get("dry_run"))
        businesses = self._resolve_businesses(
            business_ids=options.get("business_ids") or [],
            cohort=str(options.get("cohort") or "").strip(),
            include_all=bool(options.get("all_businesses")),
        )

        if action == "plan":
            self._handle_plan(businesses)
            return
        if action == "enable":
            self._handle_flag_update(businesses, enable=True, dry_run=dry_run)
            return
        if action == "rollback":
            self._handle_flag_update(businesses, enable=False, dry_run=dry_run)
            return
        if action == "dashboard":
            self._handle_dashboard(businesses, options)
            return
        raise CommandError(f"Unsupported action: {action}")

    @staticmethod
    def _resolve_businesses(
        *,
        business_ids: list[str],
        cohort: str,
        include_all: bool,
    ) -> list[BusinessProfile]:
        if not any([business_ids, cohort, include_all]):
            raise CommandError("Specify at least one scope filter via --business-id, --cohort, or --all.")
        queryset = BusinessProfile.objects.all().order_by("created_at")
        if business_ids:
            queryset = queryset.filter(id__in=business_ids)
        if cohort:
            queryset = queryset.filter(metadata__cohort=cohort)
        if not include_all and not business_ids and not cohort:
            raise CommandError("Refusing to evaluate every business without --all acknowledgement.")
        businesses = list(queryset)
        if not businesses:
            raise CommandError("No businesses matched the provided filters.")
        return businesses

    def _handle_plan(self, businesses: list[BusinessProfile]) -> None:
        shadow_enabled = 0
        eval_enabled = 0
        for business in businesses:
            state = FeatureFlagService.snapshot(business)
            flags = state.as_dict()
            cohort = ""
            if isinstance(business.metadata, Mapping):
                cohort = str(business.metadata.get("cohort") or "")
            row = {
                "rag_shadow_ingestion": bool(flags.get("rag_shadow_ingestion")),
                "rag_eval_logging": bool(flags.get("rag_eval_logging")),
            }
            if row["rag_shadow_ingestion"]:
                shadow_enabled += 1
            if row["rag_eval_logging"]:
                eval_enabled += 1
            self.stdout.write(
                f"{business.id} {business.name} cohort={cohort or '-'} flags={row}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                "phase6.plan total={total} shadow_ingestion_enabled={shadow_on} "
                "shadow_ingestion_disabled={shadow_off} eval_logging_enabled={eval_on} "
                "eval_logging_disabled={eval_off}".format(
                    total=len(businesses),
                    shadow_on=shadow_enabled,
                    shadow_off=len(businesses) - shadow_enabled,
                    eval_on=eval_enabled,
                    eval_off=len(businesses) - eval_enabled,
                )
            )
        )

    def _handle_flag_update(self, businesses: list[BusinessProfile], *, enable: bool, dry_run: bool) -> None:
        if enable:
            results = FeatureFlagService.bulk_apply(
                BusinessProfile.objects.filter(id__in=[business.id for business in businesses]),
                enable=list(PHASE6_FLAG_BUNDLE),
                disable=[],
                dry_run=dry_run,
            )
            status_label = "ENABLED"
        else:
            results = FeatureFlagService.bulk_apply(
                BusinessProfile.objects.filter(id__in=[business.id for business in businesses]),
                enable=[],
                disable=list(PHASE6_FLAG_BUNDLE),
                dry_run=dry_run,
            )
            status_label = "ROLLED_BACK"

        changed = 0
        for result in results:
            before = result.before.as_dict()
            after = result.after.as_dict()
            before_flags = {key: before.get(key) for key in PHASE6_FLAG_BUNDLE}
            after_flags = {key: after.get(key) for key in PHASE6_FLAG_BUNDLE}
            delta = {
                key: after.get(key)
                for key in PHASE6_FLAG_BUNDLE
                if before.get(key) != after.get(key)
            }
            if delta:
                changed += 1
            mode = "DRY RUN" if dry_run else (status_label if delta else "UNCHANGED")
            self.stdout.write(
                f"{mode:<12} {result.business_id} {result.business_name}: before={before_flags} after={after_flags}"
            )
        prefix = "DRY RUN" if dry_run else status_label
        self.stdout.write(self.style.SUCCESS(f"{prefix}: {changed}/{len(results)} business(es) changed."))

    def _handle_dashboard(self, businesses: list[BusinessProfile], options: Mapping[str, Any]) -> None:
        baseline_json = str(options.get("baseline_json") or "").strip()
        if not baseline_json:
            raise CommandError("--baseline-json is required for --action dashboard.")
        baseline_path = Path(baseline_json)
        if not baseline_path.exists():
            raise CommandError(f"Baseline snapshot file not found: {baseline_json}")

        baseline = load_snapshot_from_json(baseline_path)
        thresholds = quality_gate_thresholds(
            min_row_recall=options.get("min_row_recall"),
            min_row_order_stability=options.get("min_row_order_stability"),
            min_scope_f1=options.get("min_scope_f1"),
            min_critical_value_coverage=options.get("min_critical_value_coverage"),
            min_scope_metadata_coverage=options.get("min_scope_metadata_coverage"),
        )
        focus_row_start = options.get("focus_row_start")
        focus_row_end = options.get("focus_row_end")
        window_hours = max(1, int(options.get("window_hours") or 1))
        max_uploads = max(1, int(options.get("max_uploads_per_business") or 1))
        min_pass_rate = max(0.0, min(1.0, float(options.get("min_pass_rate") or 0.95)))
        enforce = bool(options.get("enforce"))
        cutoff = timezone.now() - timedelta(hours=window_hours)

        business_ids = [business.id for business in businesses]
        uploads = (
            KnowledgeUpload.objects.filter(
                business_profile_id__in=business_ids,
                status="active",
                last_ingested_at__isnull=False,
                last_ingested_at__gte=cutoff,
            )
            .order_by("business_profile_id", "-last_ingested_at")
            .only("id", "business_profile_id", "source_name", "display_name", "last_ingested_at")
        )

        uploads_by_business: dict[str, list[KnowledgeUpload]] = {}
        for upload in uploads.iterator():
            business_id = str(upload.business_profile_id)
            bucket = uploads_by_business.setdefault(business_id, [])
            if len(bucket) >= max_uploads:
                continue
            bucket.append(upload)

        failed_checks: Counter[str] = Counter()
        regressions: Counter[str] = Counter()
        businesses_with_uploads = 0
        uploads_evaluated = 0
        passed_uploads = 0
        per_business: list[dict[str, Any]] = []

        for business in businesses:
            key = str(business.id)
            business_uploads = uploads_by_business.get(key, [])
            if business_uploads:
                businesses_with_uploads += 1
            upload_results: list[dict[str, Any]] = []
            for upload in business_uploads:
                snapshot = capture_upload_snapshot(
                    upload_id=str(upload.id),
                    snapshot_label=f"phase6_dashboard_{upload.id}",
                    focus_row_start=focus_row_start,
                    focus_row_end=focus_row_end,
                )
                report = compare_snapshots(
                    baseline,
                    snapshot,
                    focus_row_start=focus_row_start,
                    focus_row_end=focus_row_end,
                    thresholds=thresholds,
                )
                gate = report.get("quality_gate") or {}
                passed = bool(gate.get("passed"))
                uploads_evaluated += 1
                if passed:
                    passed_uploads += 1
                for check in gate.get("failed_checks") or []:
                    failed_checks[str(check)] += 1
                for regression in gate.get("regressions") or []:
                    regressions[str(regression)] += 1
                upload_results.append(
                    {
                        "upload_id": str(upload.id),
                        "source_name": str(upload.source_name or upload.display_name or ""),
                        "last_ingested_at": upload.last_ingested_at.isoformat() if upload.last_ingested_at else None,
                        "passed": passed,
                        "failed_checks": list(gate.get("failed_checks") or []),
                        "regressions": list(gate.get("regressions") or []),
                        "metrics": dict(gate.get("metrics") or {}),
                    }
                )

            business_passed = sum(1 for item in upload_results if item.get("passed"))
            business_rate = (float(business_passed) / float(max(1, len(upload_results)))) if upload_results else 0.0
            cohort = ""
            if isinstance(business.metadata, Mapping):
                cohort = str(business.metadata.get("cohort") or "")
            per_business.append(
                {
                    "business_id": key,
                    "business_name": business.name,
                    "cohort": cohort,
                    "uploads_evaluated": len(upload_results),
                    "passed_uploads": business_passed,
                    "pass_rate": round(business_rate, 4),
                    "uploads": upload_results,
                }
            )

        overall_pass_rate = float(passed_uploads) / float(max(1, uploads_evaluated))
        summary = {
            "generated_at": timezone.now().isoformat(),
            "action": "dashboard",
            "window_hours": window_hours,
            "max_uploads_per_business": max_uploads,
            "business_count": len(businesses),
            "businesses_with_uploads": businesses_with_uploads,
            "uploads_evaluated": uploads_evaluated,
            "passed_uploads": passed_uploads,
            "failed_uploads": max(0, uploads_evaluated - passed_uploads),
            "pass_rate": round(overall_pass_rate, 4),
            "min_pass_rate": min_pass_rate,
            "baseline_snapshot": str(baseline_path),
            "thresholds": thresholds,
            "failed_checks": dict(sorted(failed_checks.items(), key=lambda item: item[0])),
            "regressions": dict(sorted(regressions.items(), key=lambda item: item[0])),
            "businesses": per_business,
        }

        output_raw = str(options.get("output") or "").strip()
        if output_raw:
            output_path = Path(output_raw)
            if not output_path.is_absolute():
                output_path = Path.cwd() / output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self.stdout.write(f"Dashboard JSON: {output_path}")

        self.stdout.write(
            self.style.SUCCESS(
                "phase6.dashboard businesses={businesses} with_uploads={with_uploads} uploads={uploads} pass_rate={pass_rate}".format(
                    businesses=len(businesses),
                    with_uploads=businesses_with_uploads,
                    uploads=uploads_evaluated,
                    pass_rate=round(overall_pass_rate, 4),
                )
            )
        )
        if failed_checks:
            self.stdout.write(self.style.WARNING(f"failed_checks={dict(failed_checks)}"))
        if regressions:
            self.stdout.write(self.style.WARNING(f"regressions={dict(regressions)}"))

        if enforce:
            if uploads_evaluated == 0:
                raise CommandError("Dashboard enforcement failed: no uploads matched the selected window/scope.")
            if overall_pass_rate < min_pass_rate:
                raise CommandError(
                    f"Dashboard enforcement failed: pass_rate={overall_pass_rate:.4f} below min_pass_rate={min_pass_rate:.4f}."
                )
