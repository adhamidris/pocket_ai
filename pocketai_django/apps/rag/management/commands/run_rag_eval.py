from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.feature_flags import FeatureFlagService
from apps.rag.evaluation.datasets import GOLDEN_SETS
from apps.rag.evaluation.harness import EvaluationThresholdError, RAGEvaluationHarness


class Command(BaseCommand):
    help = "Run the RAG evaluation harness against one or more golden sets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--set",
            dest="set_slug",
            help="Run a single golden set (e.g. fees-credit-cards).",
        )
        parser.add_argument(
            "--export",
            dest="export_path",
            help="Optional JSON export path for evaluation results.",
        )
        parser.add_argument(
            "--force-reingest",
            action="store_true",
            help="Force fixture re-ingestion before evaluation.",
        )
        parser.add_argument(
            "--no-thresholds",
            action="store_true",
            help="Disable threshold enforcement (reports still captured).",
        )
        parser.add_argument(
            "--top-k",
            type=int,
            default=None,
            help="Override top-k retrieval depth for the evaluation run.",
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
        export_path = options.get("export_path") or "var/logs/rag_eval_latest.json"
        force_reingest = bool(options.get("force_reingest"))
        enforce_thresholds = not bool(options.get("no_thresholds"))
        top_k = options.get("top_k")
        enable_eval_logging = bool(options.get("enable_eval_logging"))
        enable_shadow_ingestion = bool(options.get("enable_shadow_ingestion"))
        enable_shadow_retrieval = bool(options.get("enable_shadow_retrieval"))

        if set_slug and set_slug not in GOLDEN_SETS:
            raise CommandError(f"Unknown golden set '{set_slug}'.")

        harness = RAGEvaluationHarness(top_k=top_k, enforce_thresholds=enforce_thresholds)

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

        try:
            reports = harness.run(
                set_slug=set_slug,
                export_path=export_path,
                force_reingest=force_reingest,
            )
        except EvaluationThresholdError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            reports = []
        if not reports:
            return

        for report in reports:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{report.slug}] status={report.status} queries={report.metrics.get('total_queries')} "
                    f"mrr={report.metrics.get('mrr')} top3={report.metrics.get('top3_recall')} "
                    f"ndcg={report.metrics.get('ndcg_at_k')}"
                )
            )
