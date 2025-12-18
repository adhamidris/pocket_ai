from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.services.evaluation.harness import EvaluationThresholdError, RAGEEvaluationHarness


class Command(BaseCommand):
    help = "Run production gates: RAG eval (quality) + MCP load test (latency/error)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--set", dest="set_slug", help="Optional golden set slug (default: run all).")
        parser.add_argument(
            "--rag-output",
            dest="rag_output",
            default="",
            help="Path to export RAG eval JSON (default: var/logs/rag_eval_latest.json).",
        )
        parser.add_argument(
            "--force-reingest",
            action="store_true",
            help="Force re-ingestion of fixture uploads even if already processed.",
        )

        parser.add_argument(
            "--business-id",
            dest="business_id",
            default="",
            help="BusinessProfile UUID to load-test (default: use first RAG eval business).",
        )
        parser.add_argument(
            "--load-output",
            dest="load_output",
            default="",
            help="Path to export load test JSON (default: var/logs/mcp_load_test_latest.json).",
        )
        parser.add_argument(
            "--mode",
            type=str,
            default="search_read",
            choices=("search", "search_read", "read_knowledge"),
            help="Which tool path to load-test.",
        )
        parser.add_argument("--queries", nargs="*", default=["pricing"], help="Queries to rotate through.")
        parser.add_argument("--iterations", type=int, default=200, help="Total calls to execute.")
        parser.add_argument("--concurrency", type=int, default=8, help="Number of threads.")
        parser.add_argument("--document-id", type=str, default="", help="Required for read_knowledge mode.")
        parser.add_argument("--intent", type=str, default="auto", help="read_knowledge intent (auto|text|table).")
        parser.add_argument(
            "--p95-max-ms",
            type=int,
            default=0,
            help="Maximum allowed p95 latency in ms (0 = use settings default).",
        )
        parser.add_argument(
            "--max-error-rate",
            type=float,
            default=-1.0,
            help="Maximum allowed error rate, as fraction 0..1 (-1 = use settings default).",
        )
        parser.add_argument(
            "--max-throttled-rate",
            type=float,
            default=-1.0,
            help="Maximum allowed throttled rate, as fraction 0..1 (-1 = use settings default).",
        )

    def handle(self, *args, **options):
        rag_output_raw = str(options.get("rag_output") or "").strip()
        rag_output = Path(rag_output_raw) if rag_output_raw else self._default_rag_output()

        harness = RAGEEvaluationHarness()
        try:
            reports = harness.run(
                set_slug=options.get("set_slug"),
                export_path=rag_output,
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
            raise CommandError("RAG evaluation thresholds failed") from exc

        if not reports:
            raise CommandError("No evaluation reports generated.")

        business_id_raw = str(options.get("business_id") or "").strip()
        if business_id_raw:
            try:
                business_id = uuid.UUID(business_id_raw)
            except (TypeError, ValueError):
                raise CommandError("Invalid --business-id UUID") from None
        else:
            business_id = reports[0].business_id

        load_output_raw = str(options.get("load_output") or "").strip()
        load_output = Path(load_output_raw) if load_output_raw else self._default_load_output()

        call_command(
            "run_mcp_load_test",
            business_id=str(business_id),
            mode=options.get("mode"),
            queries=options.get("queries") or [],
            iterations=int(options.get("iterations") or 0),
            concurrency=int(options.get("concurrency") or 0),
            document_id=str(options.get("document_id") or ""),
            intent=str(options.get("intent") or "auto"),
            output=str(load_output),
            enforce=True,
            p95_max_ms=int(options.get("p95_max_ms") or 0),
            max_error_rate=float(options.get("max_error_rate") if options.get("max_error_rate") is not None else -1.0),
            max_throttled_rate=float(
                options.get("max_throttled_rate") if options.get("max_throttled_rate") is not None else -1.0
            ),
        )

        self.stdout.write(self.style.SUCCESS("Production gates passed"))

    @staticmethod
    def _default_rag_output() -> Path:
        base = getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "var" / "logs")
        return Path(base) / "rag_eval_latest.json"

    @staticmethod
    def _default_load_output() -> Path:
        base = getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "var" / "logs")
        return Path(base) / "mcp_load_test_latest.json"

