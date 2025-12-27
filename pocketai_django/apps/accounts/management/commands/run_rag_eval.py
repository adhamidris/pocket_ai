from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.feature_flags import FeatureFlagService
from apps.rag.evaluation.datasets import GOLDEN_SETS
from apps.rag.evaluation.harness import EvaluationThresholdError, RAGEvaluationHarness


class Command(BaseCommand):
    help = "Execute the RAG evaluation harness against curated golden sets."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--set",
            dest="set_slug",
            help="Optional golden set slug (default: run all).",
        )
        parser.add_argument(
            "--output",
            dest="output",
            help="Path to export JSON results.",
        )
        parser.add_argument(
            "--force-reingest",
            action="store_true",
            help="Force reingestion of fixture uploads even if already processed.",
        )
        parser.add_argument(
            "--skip-threshold-check",
            action="store_true",
            help="Skip evaluation threshold enforcement (baseline mode).",
        )
        parser.add_argument(
            "--baseline",
            action="store_true",
            help="Alias for --skip-threshold-check.",
        )
        parser.add_argument(
            "--enable-eval-logging",
            action="store_true",
            help="Enable per-snippet logging for the evaluation business.",
        )
        parser.add_argument(
            "--enable-shadow-ingestion",
            action="store_true",
            help="Enable shadow ingestion for the evaluation business.",
        )
        parser.add_argument(
            "--enable-shadow-retrieval",
            action="store_true",
            help="Enable shadow retrieval logging for the evaluation business.",
        )

    def handle(self, *args, **options):
        set_slug = options.get("set_slug")
        if set_slug and set_slug not in GOLDEN_SETS:
            raise CommandError(f"Unknown golden set '{set_slug}'.")
        enforce_thresholds = not bool(options.get("skip_threshold_check") or options.get("baseline"))
        harness = RAGEvaluationHarness(enforce_thresholds=enforce_thresholds)
        enable_eval_logging = bool(options.get("enable_eval_logging"))
        enable_shadow_ingestion = bool(options.get("enable_shadow_ingestion"))
        enable_shadow_retrieval = bool(options.get("enable_shadow_retrieval"))
        if enable_eval_logging or enable_shadow_ingestion or enable_shadow_retrieval:
            target_sets = [GOLDEN_SETS[set_slug]] if set_slug else list(GOLDEN_SETS.values())
            for golden_set in target_sets:
                business = harness._prepare_business(golden_set)
                updates = {}
                if enable_eval_logging:
                    updates["rag_eval_logging"] = True
                if enable_shadow_ingestion:
                    updates["rag_shadow_ingestion"] = True
                if enable_shadow_retrieval:
                    updates["rag_shadow_retrieval"] = True
                if updates:
                    FeatureFlagService.set_flags(business, updates=updates)
        output_path = options.get("output") or self._default_output_path()
        try:
            reports = harness.run(
                set_slug=set_slug,
                export_path=output_path,
                force_reingest=bool(options.get("force_reingest")),
            )
        except EvaluationThresholdError as exc:
            for slug, violations in exc.violations.items():
                for key, info in violations.items():
                    self.stderr.write(
                        self.style.ERROR(
                            f"[{slug}] {key} violated (obs={info.get('observed')} target={info.get('minimum') or info.get('maximum')})"
                        )
                    )
            raise CommandError("Evaluation thresholds failed") from exc
        if not reports:
            raise CommandError("No reports generated.")
        for report in reports:
            metrics = report.metrics
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{report.slug}] status={report.status} top1={metrics['top1_recall']:.2f} top3={metrics['top3_recall']:.2f} "
                    f"mrr={metrics['mrr']:.2f} src_acc={metrics.get('source_accuracy', 0):.2f} "
                    f"beh_acc={metrics.get('behavior_accuracy', 0):.2f} not_found_acc={metrics['not_found_accuracy']:.2f}"
                )
            )
        self.stdout.write(self.style.SUCCESS(f"Exported evaluation artifacts to {output_path}"))

    @staticmethod
    def _default_output_path() -> Path:
        base = getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "var" / "logs")
        return Path(base) / "rag_eval_latest.json"
