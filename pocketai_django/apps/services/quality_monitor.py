from __future__ import annotations

import logging
import statistics
from typing import Mapping, Sequence

from django.conf import settings

from apps.accounts.models import BusinessProfile, KnowledgeDriftSample, RAGEvaluationRun

logger = logging.getLogger(__name__)


class QualityMonitor:
    """Centralized helpers for capturing ingestion/retrieval quality metrics."""

    @staticmethod
    def record_ingestion_sample(
        *,
        business_profile: BusinessProfile,
        alias_values: Sequence[str],
        truncated_entities: int,
        indexed_entities: int,
    ) -> None:
        alias_lengths = [len(value or "") for value in alias_values if value]
        symbol_hits = sum(1 for value in alias_values if value and any(ch in "-_" for ch in value))
        digit_hits = sum(1 for value in alias_values if value and any(ch.isdigit() for ch in value))
        total_entities = max(1, indexed_entities + truncated_entities)
        trunc_rate = truncated_entities / total_entities

        metrics = {
            "indexed_entities": indexed_entities,
            "truncated_entities": truncated_entities,
            "alias_count": len(alias_values),
            "alias_length_p50": float(statistics.median(alias_lengths)) if alias_lengths else 0.0,
            "alias_length_p95": QualityMonitor._p95(alias_lengths),
            "alias_symbol_rate": round(symbol_hits / max(1, len(alias_values)), 3),
            "alias_digit_rate": round(digit_hits / max(1, len(alias_values)), 3),
            "truncation_rate": round(trunc_rate, 4),
        }
        KnowledgeDriftSample.objects.create(
            business_profile=business_profile,
            sample_kind=KnowledgeDriftSample.SampleKind.INGESTION,
            metrics=metrics,
        )
        trunc_threshold = float(getattr(settings, "RAG_DRIFT_TRUNCATION_THRESHOLD", 0.15))
        if trunc_rate > trunc_threshold:
            logger.warning(
                "rag.drift.truncation business=%s rate=%.3f threshold=%.3f",
                business_profile.id,
                trunc_rate,
                trunc_threshold,
            )

    @staticmethod
    def record_retrieval_sample(
        *,
        business_profile: BusinessProfile,
        query_type: str,
        alias_hit: bool,
        fallback_used: bool,
        latency_ms: int | None,
        stage: str | None = None,
        feature_flags: Mapping[str, bool] | None = None,
        diagnostics: Mapping[str, object] | None = None,
        result_status: str | None = None,
    ) -> None:
        metrics = {
            "query_type": query_type,
            "alias_hit": bool(alias_hit),
            "fallback_used": bool(fallback_used),
            "latency_ms": latency_ms,
            "stage": stage or "",
            "status": result_status or "",
        }
        metadata_payload: dict[str, object] = {}
        if feature_flags:
            metadata_payload["features"] = dict(feature_flags)
        if diagnostics:
            interesting_keys = (
                "alias_cache_hit",
                "alias_cache_miss",
                "vector_candidates",
                "fts_candidates",
                "vector_distance_mean",
                "vector_distance_min",
                "vector_distance_max",
                "alias_candidates",
                "hybrid_enabled",
                "reason",
            )
            metadata_payload["diagnostics"] = {
                key: diagnostics.get(key)
                for key in interesting_keys
                if diagnostics.get(key) is not None
            }
        KnowledgeDriftSample.objects.create(
            business_profile=business_profile,
            sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            metrics=metrics,
            metadata=metadata_payload,
        )
        if query_type == "identifier":
            QualityMonitor._check_alias_hit_rate(business_profile)
        QualityMonitor._check_not_found_spike(business_profile)

    @staticmethod
    def record_evaluation_run(
        *,
        business_profile: BusinessProfile,
        slug: str,
        metrics: Mapping[str, object],
        latencies: Mapping[str, Mapping[str, float]],
        thresholds: Mapping[str, object],
        status: str,
        violations: Mapping[str, object],
    ) -> None:
        RAGEvaluationRun.objects.create(
            business_profile=business_profile,
            slug=slug,
            status=status,
            metrics=metrics,
            latencies=latencies,
            thresholds=thresholds,
            violations=violations,
        )

    @staticmethod
    def compute_threshold_violations(
        metrics: Mapping[str, float],
        latencies: Mapping[str, Mapping[str, float]],
        thresholds: Mapping[str, object],
    ) -> dict[str, Mapping[str, float]]:
        violations: dict[str, Mapping[str, float]] = {}
        minimums = (thresholds or {}).get("minimums") or {}
        maximums = (thresholds or {}).get("maximums") or {}
        for key, minimum in minimums.items():
            observed = metrics.get(key)
            if observed is None or observed < float(minimum):
                violations[key] = {"observed": observed, "minimum": float(minimum)}
        for key, maximum in maximums.items():
            observed = QualityMonitor._latency_value(latencies, key)
            if observed is None:
                continue
            if observed > float(maximum):
                violations[key] = {"observed": observed, "maximum": float(maximum)}
        return violations

    @staticmethod
    def _latency_value(latencies: Mapping[str, Mapping[str, float]], spec: str) -> float | None:
        if not spec:
            return None
        normalized = spec.replace("latency.", "")
        parts = normalized.split(".")
        if len(parts) != 2:
            return None
        stage, stat = parts
        stage_stats = latencies.get(stage)
        if not isinstance(stage_stats, Mapping):
            return None
        value = stage_stats.get(stat)
        return float(value) if value is not None else None

    @staticmethod
    def _p95(samples: Sequence[int]) -> float:
        ordered = sorted(samples)
        if not ordered:
            return 0.0
        index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
        return float(ordered[index])

    @staticmethod
    def _check_alias_hit_rate(business_profile: BusinessProfile) -> None:
        threshold = float(getattr(settings, "RAG_DRIFT_ALIAS_HIT_THRESHOLD", 0.85))
        recent = list(
            KnowledgeDriftSample.objects.filter(
                business_profile=business_profile,
                sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            )
            .order_by("-observed_at")
            .values_list("metrics", flat=True)[:50]
        )
        identifier_samples = [
            metrics for metrics in recent if isinstance(metrics, dict) and metrics.get("query_type") == "identifier"
        ]
        if len(identifier_samples) < 5:
            return
        hits = sum(1 for metrics in identifier_samples if metrics.get("alias_hit"))
        rate = hits / max(1, len(identifier_samples))
        if rate < threshold:
            logger.warning(
                "rag.drift.alias_hit business=%s rate=%.3f threshold=%.3f samples=%s",
                business_profile.id,
                rate,
                threshold,
                len(identifier_samples),
            )

    @staticmethod
    def _check_not_found_spike(business_profile: BusinessProfile) -> None:
        threshold = float(getattr(settings, "RAG_DRIFT_NOT_FOUND_THRESHOLD", 0.3))
        recent = list(
            KnowledgeDriftSample.objects.filter(
                business_profile=business_profile,
                sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            )
            .order_by("-observed_at")
            .values_list("metrics", flat=True)[:50]
        )
        if len(recent) < 10:
            return
        not_found = sum(
            1 for metrics in recent if isinstance(metrics, dict) and (metrics.get("status") or "").lower() == "not_found"
        )
        rate = not_found / max(1, len(recent))
        if rate > threshold:
            logger.warning(
                "rag.drift.not_found business=%s rate=%.3f threshold=%.3f samples=%s",
                business_profile.id,
                rate,
                threshold,
                len(recent),
            )
