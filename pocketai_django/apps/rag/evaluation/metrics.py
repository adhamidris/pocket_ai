from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from apps.rag.evaluation.datasets import GoldenQuery
from apps.rag.knowledge_search import KnowledgeSnippet

if TYPE_CHECKING:
    from apps.rag.evaluation.types import QueryObservation


class EvaluationMetricsMixin:
    @staticmethod
    def _behavior_correct(
        query: GoldenQuery,
        *,
        status: str,
        path: str | None,
        alias_short_circuit: bool,
        alias_hits: Sequence[str],
        match_rank: int | None,
    ) -> bool:
        expected = (query.expected_behavior or "").strip().lower()
        path_value = (path or "").strip().lower()
        if expected == "not_found":
            if status == "not_found":
                return True
            return path_value == "fallback"
        if expected == "alias_exact":
            return alias_short_circuit or path_value == "alias_exact"
        if expected == "alias_fallback":
            if alias_short_circuit or not alias_hits:
                return False
            if status != "ok":
                return False
            if query.target_entities and match_rank is None:
                return False
            return True
        if expected == "hybrid":
            if path_value in {"hybrid", "table_direct", "table_blended"}:
                return True
            if match_rank is not None:
                return True
            return status == "ok"
        return True

    @staticmethod
    def _match_rank(
        snippets: Sequence[KnowledgeSnippet],
        targets: Sequence[str],
        *,
        target_fixtures: Sequence[str] | None = None,
        fixture_names: Sequence[str] | None = None,
    ) -> int | None:
        normalized_targets = {target.strip().lower() for target in targets if target}
        normalized_fixtures = {target.strip().lower() for target in (target_fixtures or ()) if target}
        if not normalized_targets and not normalized_fixtures:
            return None
        for index, snippet in enumerate(snippets, start=1):
            if normalized_targets:
                entity = (snippet.entity_name or "").strip().lower()
                if entity and entity in normalized_targets:
                    return index
            if normalized_fixtures and fixture_names:
                fixture = fixture_names[index - 1] if index - 1 < len(fixture_names) else ""
                if fixture and fixture.strip().lower() in normalized_fixtures:
                    return index
        return None

    @staticmethod
    def _precision(
        snippets: Sequence[KnowledgeSnippet],
        targets: Sequence[str],
        top_k: int,
        *,
        target_fixtures: Sequence[str] | None = None,
        fixture_names: Sequence[str] | None = None,
    ) -> float:
        if not snippets:
            return 0.0
        normalized_targets = {target.strip().lower() for target in targets if target}
        normalized_fixtures = {target.strip().lower() for target in (target_fixtures or ()) if target}
        if not normalized_targets and not normalized_fixtures:
            return 0.0
        considered = snippets[:top_k]
        relevant: list[KnowledgeSnippet] = []
        for index, snippet in enumerate(considered, start=1):
            if normalized_targets:
                entity = (snippet.entity_name or "").strip().lower()
                if entity and entity in normalized_targets:
                    relevant.append(snippet)
                    continue
            if normalized_fixtures and fixture_names:
                fixture = fixture_names[index - 1] if index - 1 < len(fixture_names) else ""
                if fixture and fixture.strip().lower() in normalized_fixtures:
                    relevant.append(snippet)
        if not considered:
            return 0.0
        return len(relevant) / len(considered)

    @staticmethod
    def _classification_correct(query: GoldenQuery, result) -> bool:
        if query.query_type != "not_found":
            return True
        if result.status == "not_found":
            return True
        path = result.diagnostics.get("path")
        reason = result.diagnostics.get("reason")
        return path == "fallback" or reason == "fallback_used"

    @staticmethod
    def _aggregate_metrics(observations: Sequence[QueryObservation]) -> dict[str, Any]:
        evaluated_obs = [obs for obs in observations if obs.query_type != "not_found"]
        denominator = max(1, len(evaluated_obs))
        identifier_obs = [obs for obs in observations if obs.query_type == "identifier"]
        identifier_hits = [obs for obs in identifier_obs if obs.top1]
        identifier_count = max(1, len(identifier_obs))
        identifier_mrr = sum(obs.reciprocal_rank for obs in identifier_obs) / identifier_count if identifier_obs else 0.0
        natural_obs = [obs for obs in observations if obs.query_type == "natural"]
        top1 = sum(obs.top1 for obs in evaluated_obs) / denominator
        top3 = sum(obs.top3 for obs in evaluated_obs) / denominator
        mrr = sum(obs.reciprocal_rank for obs in evaluated_obs) / denominator
        precision = sum(obs.precision_at_k for obs in evaluated_obs) / denominator if evaluated_obs else 0.0
        not_found_obs = [obs for obs in observations if obs.query_type == "not_found"]
        expected_hit_obs = [obs for obs in observations if obs.has_targets and obs.query_type != "not_found"]
        wrong_source_count = sum(1 for obs in expected_hit_obs if obs.wrong_source)
        wrong_source_rate = wrong_source_count / max(1, len(expected_hit_obs)) if expected_hit_obs else 0.0
        source_accuracy = 1.0 - wrong_source_rate
        behavior_accuracy = sum(1 for obs in observations if obs.behavior_correct) / max(1, len(observations))
        fallback_accuracy = (
            sum(1 for obs in not_found_obs if obs.classification_correct) / max(1, len(not_found_obs))
            if not_found_obs
            else 1.0
        )
        alias_short_circuit = (
            sum(1 for obs in identifier_obs if obs.alias_short_circuit) / identifier_count if identifier_obs else 0.0
        )
        ndcg_values = [
            (1.0 / math.log2(obs.match_rank + 1)) if obs.match_rank else 0.0 for obs in evaluated_obs
        ]
        avg_top_tokens = sum(
            (
                sum(obs.top_snippet_token_counts) / len(obs.top_snippet_token_counts)
                if obs.top_snippet_token_counts
                else 0.0
            )
            for obs in observations
        ) / max(1, len(observations))
        avg_top_chars = sum(
            (
                sum(obs.top_snippet_char_counts) / len(obs.top_snippet_char_counts)
                if obs.top_snippet_char_counts
                else 0.0
            )
            for obs in observations
        ) / max(1, len(observations))
        table_applicability_observations = 0
        table_multi_scope_hits = 0
        table_inferred_scope_hits = 0
        table_ambiguous_scope_hits = 0
        for obs in observations:
            diagnostics = obs.diagnostics if isinstance(obs.diagnostics, Mapping) else {}
            top_diag = diagnostics.get("top_snippet_diagnostics")
            if not isinstance(top_diag, Mapping):
                continue
            raw_scope = top_diag.get("table_row_inferred_scope_columns")
            if not isinstance(raw_scope, (list, tuple)):
                raw_scope = top_diag.get("table_row_applies_to_columns")
            if not isinstance(raw_scope, (list, tuple)):
                continue
            scope = [str(item or "").strip() for item in raw_scope if str(item or "").strip()]
            if not scope:
                continue
            table_applicability_observations += 1
            if len(scope) > 1:
                table_multi_scope_hits += 1
            mode = str(
                top_diag.get("table_row_scope_reason")
                or top_diag.get("table_row_applicability_mode")
                or ""
            ).strip().lower()
            if mode.startswith("inferred_") or mode in {
                "scope_repeated_value_span",
                "scope_sparse_expansion",
                "scope_edge_completion",
            }:
                table_inferred_scope_hits += 1
            if mode in {"ambiguous", "scope_abstain"}:
                table_ambiguous_scope_hits += 1
        return {
            "top1_recall": round(top1, 4),
            "top3_recall": round(top3, 4),
            "mrr": round(mrr, 4),
            "ndcg_at_k": round(sum(ndcg_values) / denominator, 4),
            "precision_at_k": round(precision, 4),
            "identifier_top1": round(len(identifier_hits) / identifier_count, 4),
            "identifier_mrr": round(identifier_mrr, 4),
            "source_accuracy": round(source_accuracy, 4),
            "wrong_source_rate": round(wrong_source_rate, 4),
            "behavior_accuracy": round(behavior_accuracy, 4),
            "avg_top_snippet_tokens": round(avg_top_tokens, 2),
            "avg_top_snippet_chars": round(avg_top_chars, 2),
            "natural_query_count": len(natural_obs),
            "not_found_accuracy": round(fallback_accuracy, 4),
            "alias_short_circuit_rate": round(alias_short_circuit, 4),
            "table_applicability_coverage": round(
                table_applicability_observations / max(1, len(observations)),
                4,
            ),
            "table_multi_scope_rate": round(
                table_multi_scope_hits / max(1, table_applicability_observations),
                4,
            ),
            "table_inferred_scope_rate": round(
                table_inferred_scope_hits / max(1, table_applicability_observations),
                4,
            ),
            "table_ambiguous_scope_rate": round(
                table_ambiguous_scope_hits / max(1, table_applicability_observations),
                4,
            ),
            "total_queries": len(observations),
        }

    @staticmethod
    def _percentiles(samples: Sequence[int]) -> dict[str, float]:
        ordered = sorted(samples)
        if not ordered:
            return {"p50": 0.0, "p95": 0.0}
        p50 = statistics.median(ordered)
        if len(ordered) == 1:
            return {"p50": float(p50), "p95": float(p50)}
        index_95 = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
        p95 = ordered[index_95]
        return {"p50": float(p50), "p95": float(p95)}
