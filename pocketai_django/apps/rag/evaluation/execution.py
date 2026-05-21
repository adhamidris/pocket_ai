from __future__ import annotations

import logging
import time
from typing import Any, Mapping, Sequence

from apps.accounts.models import BusinessProfile
from apps.knowledge.models import KnowledgeFeedbackCase
from apps.rag.evaluation.datasets import GoldenQuery, GoldenSet
from apps.rag.evaluation.types import EvaluationReport, QueryObservation
from apps.rag.observability.quality_monitor import QualityMonitor

logger = logging.getLogger(__name__)


class EvaluationExecutionMixin:

    def _evaluate_set(self, golden_set: GoldenSet, *, business: BusinessProfile) -> EvaluationReport:
        queries = list(golden_set.queries)
        feedback_queries = list(self._feedback_queries_for_business(business))
        if feedback_queries:
            queries.extend(feedback_queries)
        observations: list[QueryObservation] = []
        stage_samples: dict[str, list[int]] = {
            "alias": [],
            "vector": [],
            "lexical": [],
            "rerank": [],
            "total": [],
        }
        for query in queries:
            observation = self._run_query(business=business, query=query, top_k=golden_set.default_top_k or self.top_k)
            observations.append(observation)
            if observation.alias_latency_ms is not None:
                stage_samples["alias"].append(observation.alias_latency_ms)
            if observation.vector_latency_ms is not None:
                stage_samples["vector"].append(observation.vector_latency_ms)
            if observation.lexical_latency_ms is not None:
                stage_samples["lexical"].append(observation.lexical_latency_ms)
            if observation.rerank_latency_ms is not None:
                stage_samples["rerank"].append(observation.rerank_latency_ms)
            stage_samples["total"].append(observation.total_latency_ms)

        metrics = self._aggregate_metrics(observations)
        latency = {stage: self._percentiles(samples) for stage, samples in stage_samples.items() if samples}
        violations = QualityMonitor.compute_threshold_violations(metrics, latency, self.thresholds)
        status = "fail" if violations else "pass"
        try:
            QualityMonitor.record_evaluation_run(
                business_profile=business,
                slug=golden_set.slug,
                metrics=metrics,
                latencies=latency,
                thresholds=self.thresholds,
                status=status,
                violations=violations,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("quality.eval.record_failed business=%s error=%s", business.id, exc)
        return EvaluationReport(
            slug=golden_set.slug,
            business_id=business.id,
            metrics=metrics,
            latencies=latency,
            observations=tuple(observations),
            status=status,
            violations=violations,
            fixtures=tuple(fixture.filename for fixture in golden_set.fixtures),
            feedback_cases=len(feedback_queries),
        )

    def _feedback_queries_for_business(self, business: BusinessProfile) -> Sequence[GoldenQuery]:
        cases = KnowledgeFeedbackCase.objects.filter(business_profile=business, is_active=True).order_by("created_at")
        feedback_queries: list[GoldenQuery] = []
        for case in cases:
            query_type = "not_found" if case.expected_behavior == "not_found" else "identifier" if case.expected_behavior == "alias_exact" else "natural"
            feedback_queries.append(
                GoldenQuery(
                    query_id=f"feedback_{case.id}",
                    text=case.query_text,
                    query_type=query_type,
                    expected_behavior=case.expected_behavior,
                    target_entities=tuple(case.expected_entities or ()),
                    target_aliases=tuple(case.expected_aliases or ()),
                    notes=f"feedback:{case.source}",
                )
            )
        return tuple(feedback_queries)

    def replay_feedback_case(self, case: KnowledgeFeedbackCase) -> QueryObservation:
        query_type = "not_found" if case.expected_behavior == "not_found" else "identifier" if case.expected_behavior == "alias_exact" else "natural"
        golden_query = GoldenQuery(
            query_id=f"feedback_{case.id}",
            text=case.query_text,
            query_type=query_type,
            expected_behavior=case.expected_behavior,
            target_entities=tuple(case.expected_entities or ()),
            target_aliases=tuple(case.expected_aliases or ()),
            notes=f"admin-replay:{case.source}",
        )
        return self._run_query(business=case.business_profile, query=golden_query, top_k=self.top_k)

    def _run_query(self, *, business: BusinessProfile, query: GoldenQuery, top_k: int) -> QueryObservation:
        top_k = max(1, top_k)
        traits = self.search_service.analyze_query(query.text)
        alias_start = time.perf_counter()
        alias_result = self.search_service.search_by_alias(
            business_profile=business,
            traits=traits,
            limit=max(self.top_k, top_k),
        )
        alias_latency_ms = alias_result.diagnostics.get("duration_ms")
        alias_hits = tuple(str(hit.chunk_id) for hit in alias_result.hits)
        hybrid = self.search_service.search_free_text(
            business_profile=business,
            query=query.text,
            limit=max(top_k * 2, self.top_k),
            traits=traits,
            alias_candidates=alias_result.hits,
        )
        vector_latency_ms = hybrid.diagnostics.get("vector_duration_ms")
        lexical_latency_ms = hybrid.diagnostics.get("fts_duration_ms")
        rerank_latency_ms = hybrid.diagnostics.get("rerank_duration_ms")
        result = self.search_service.search(
            business_profile=business,
            query=query.text,
            limit=top_k,
            traits=traits,
            alias_result=alias_result,
        )
        total_latency_ms = int((time.perf_counter() - alias_start) * 1000)
        snippets = tuple(result.snippets or ())
        has_targets = bool(query.target_entities or query.target_fixtures)
        entity_lookup = tuple((snippet.entity_name or "").strip() for snippet in snippets)
        top_score_breakdown: dict[str, Any] = {}
        top_vector_distance = None
        if snippets:
            top_diag = snippets[0].source_diagnostics
            if isinstance(top_diag, Mapping):
                maybe_breakdown = top_diag.get("score_breakdown")
                if isinstance(maybe_breakdown, Mapping):
                    top_score_breakdown = dict(maybe_breakdown)
                maybe_distance = top_diag.get("vector_distance")
                if isinstance(maybe_distance, (int, float)):
                    top_vector_distance = float(maybe_distance)
        upload_ids = tuple(str(snippet.upload_id) for snippet in snippets if snippet.upload_id)
        fixtures = tuple(
            self._fixture_name_for_upload_id(snippet.upload_id) if snippet.upload_id else "" for snippet in snippets
        )
        rank = self._match_rank(
            snippets,
            query.target_entities,
            target_fixtures=query.target_fixtures,
            fixture_names=fixtures,
        )
        reciprocal_rank = 1.0 / rank if rank else 0.0
        precision = self._precision(
            snippets,
            query.target_entities,
            top_k,
            target_fixtures=query.target_fixtures,
            fixture_names=fixtures,
        )
        top1 = rank == 1
        top3 = bool(rank and rank <= 3)
        classification_correct = self._classification_correct(query, result)
        alias_short_circuit = bool(alias_result.short_circuit and alias_result.hits)
        path = result.diagnostics.get("path")
        behavior_correct = self._behavior_correct(
            query,
            status=result.status,
            path=path,
            alias_short_circuit=alias_short_circuit,
            alias_hits=alias_hits,
            match_rank=rank,
        )
        wrong_source = bool(has_targets and result.status == "ok" and rank is None)
        observation_diagnostics: dict[str, Any] = dict(result.diagnostics or {})
        if snippets:
            top_source_diag = snippets[0].source_diagnostics
            if isinstance(top_source_diag, Mapping) and top_source_diag:
                observation_diagnostics["top_snippet_diagnostics"] = dict(top_source_diag)

        observation = QueryObservation(
            query_id=query.query_id,
            text=query.text,
            query_type=query.query_type,
            expected_behavior=query.expected_behavior,
            has_targets=has_targets,
            status=result.status,
            path=path,
            behavior_correct=behavior_correct,
            wrong_source=wrong_source,
            top_entities=entity_lookup,
            top_chunk_ids=tuple(str(snippet.id) for snippet in snippets),
            top_upload_ids=upload_ids,
            top_fixtures=fixtures,
            top_score_breakdown=top_score_breakdown,
            top_vector_distance=top_vector_distance,
            match_rank=rank,
            reciprocal_rank=reciprocal_rank,
            precision_at_k=precision,
            top1=top1,
            top3=top3,
            classification_correct=classification_correct,
            alias_short_circuit=alias_short_circuit,
            alias_hits=alias_hits,
            alias_latency_ms=int(alias_latency_ms) if alias_latency_ms is not None else None,
            vector_latency_ms=int(vector_latency_ms) if vector_latency_ms is not None else None,
            lexical_latency_ms=int(lexical_latency_ms) if lexical_latency_ms is not None else None,
            rerank_latency_ms=int(rerank_latency_ms) if rerank_latency_ms is not None else None,
            total_latency_ms=total_latency_ms,
            top_snippet_previews=self._snippet_previews(snippets),
            top_snippet_token_counts=tuple(self._snippet_token_count(snippet) for snippet in snippets),
            top_snippet_char_counts=tuple(len((snippet.content or "").strip()) for snippet in snippets),
            diagnostics=observation_diagnostics,
        )
        return observation
