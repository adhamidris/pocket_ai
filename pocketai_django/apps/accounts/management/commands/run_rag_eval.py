from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.services.evaluation.harness import EvaluationThresholdError, RAGEEvaluationHarness


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

    def handle(self, *args, **options):
        harness = RAGEvaluationHarness()
        output_path = options.get("output") or self._default_output_path()
        try:
            reports = harness.run(
                set_slug=options.get("set_slug"),
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
