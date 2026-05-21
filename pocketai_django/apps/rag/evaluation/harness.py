from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from django.conf import settings

from apps.rag.knowledge_search import KnowledgeSearchService
from apps.rag.evaluation.datasets import GOLDEN_SETS, GoldenSet
from apps.rag.evaluation.execution import EvaluationExecutionMixin
from apps.rag.evaluation.fixtures import EvaluationFixtureMixin
from apps.rag.evaluation.metrics import EvaluationMetricsMixin
from apps.rag.evaluation.reporting import EvaluationReportingMixin
from apps.rag.evaluation.types import EvaluationReport, EvaluationThresholdError, QueryObservation
from apps.knowledge.ingestion.service import KnowledgeIngestionService


class RAGEvaluationHarness(
    EvaluationFixtureMixin,
    EvaluationExecutionMixin,
    EvaluationMetricsMixin,
    EvaluationReportingMixin,
):
    """Coordinates ingestion of fixtures and executes multi-stage RAG evaluations."""

    def __init__(
        self,
        *,
        ingestion_service: KnowledgeIngestionService | None = None,
        search_service: KnowledgeSearchService | None = None,
        top_k: int | None = None,
        enforce_thresholds: bool = True,
    ) -> None:
        self.ingestion_service = ingestion_service or KnowledgeIngestionService()
        self.search_service = search_service or KnowledgeSearchService()
        self.top_k = max(1, top_k or getattr(settings, "RAG_EVAL_TOP_K", 3))
        self.thresholds = getattr(settings, "RAG_EVAL_THRESHOLDS", {})
        self.enforce_thresholds = enforce_thresholds
        self.media_root = Path(getattr(settings, "MEDIA_ROOT"))
        self.media_root.mkdir(parents=True, exist_ok=True)
        self._upload_fixture_cache: dict[uuid.UUID, str] = {}

    # ------------------------------------------------------------------
    # Public API

    def run(
        self,
        *,
        set_slug: str | None = None,
        export_path: str | Path | None = None,
        force_reingest: bool = False,
    ) -> list[EvaluationReport]:
        reports: list[EvaluationReport] = []
        available_sets = GOLDEN_SETS
        target_sets: Iterable[GoldenSet]
        if set_slug:
            target = available_sets.get(set_slug)
            if not target:
                raise ValueError(f"Unknown golden set '{set_slug}'.")
            target_sets = (target,)
        else:
            target_sets = available_sets.values()

        failures: dict[str, Mapping[str, Any]] = {}
        for golden_set in target_sets:
            business = self._prepare_business(golden_set)
            self._ingest_fixtures(golden_set, business=business, force=force_reingest)
            report = self._evaluate_set(golden_set, business=business)
            reports.append(report)
            if report.status == "fail":
                failures[report.slug] = report.violations

        if export_path:
            self._export_reports(reports, Path(export_path))
        if self.enforce_thresholds and failures:
            raise EvaluationThresholdError(failures)
        return reports

    # ------------------------------------------------------------------
    # Evaluation + aggregation


__all__ = [
    "RAGEvaluationHarness",
    "EvaluationThresholdError",
    "EvaluationReport",
    "QueryObservation",
]
