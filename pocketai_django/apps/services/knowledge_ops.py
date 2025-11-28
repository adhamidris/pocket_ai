from __future__ import annotations

import dataclasses
import statistics
from collections import Counter
from typing import Mapping

from django.db.models import Count, Q, Sum
from django.utils import timezone
from opentelemetry import trace as otel_trace

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeAlias,
    KnowledgeDriftSample,
    KnowledgeEntity,
    KnowledgeIngestionJob,
    KnowledgeIngestionJobStatus,
    KnowledgeUpload,
)


@dataclasses.dataclass(frozen=True)
class AliasCoverageStats:
    alias_count: int
    entity_count: int
    alias_hit_rate: float
    alias_cache_hit_rate: float | None


@dataclasses.dataclass(frozen=True)
class SearchStageStats:
    alias_exact: int
    hybrid: int
    fallback: int
    not_found: int


@dataclasses.dataclass(frozen=True)
class IngestionHealthStats:
    queued_jobs: int
    running_jobs: int
    deferred_jobs: int
    truncation_events: int
    pending_embeddings: int


@dataclasses.dataclass(frozen=True)
class LatencyStats:
    p50: float | None
    p95: float | None


@dataclasses.dataclass(frozen=True)
class KnowledgeOpsSnapshot:
    alias_coverage: AliasCoverageStats
    stages: SearchStageStats
    ingestion: IngestionHealthStats
    latency: LatencyStats
    sample_size: int
    observed_at: str


TRACER = otel_trace.get_tracer(__name__)


class KnowledgeOpsDashboard:
    """Aggregate observability metrics for admin dashboards and runbooks."""

    SAMPLE_LIMIT = 200

    @classmethod
    def snapshot(cls, business_profile: BusinessProfile, sample_limit: int | None = None) -> KnowledgeOpsSnapshot:
        with TRACER.start_as_current_span("knowledge.ops.snapshot") as span:
            limit = sample_limit or cls.SAMPLE_LIMIT
            alias_count = KnowledgeAlias.objects.filter(business_profile=business_profile).count()
            entity_count = KnowledgeEntity.objects.filter(business_profile=business_profile).count()
            samples = list(
                KnowledgeDriftSample.objects.filter(
                    business_profile=business_profile,
                    sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
                )
                .order_by("-observed_at")
                .values("metrics", "metadata")[:limit]
            )
        stage_counter = Counter()
        alias_hits = 0
        alias_total = 0
        cache_hits = 0
        cache_total = 0
        not_found = 0
        latencies: list[float] = []
        for sample in samples:
            metrics = sample.get("metrics") or {}
            metadata = sample.get("metadata") or {}
            stage_counter[metrics.get("stage") or ""] += 1
            if metrics.get("status") == "not_found":
                not_found += 1
            if metrics.get("query_type") == "identifier":
                alias_total += 1
                if metrics.get("alias_hit"):
                    alias_hits += 1
            latency = metrics.get("latency_ms")
            if isinstance(latency, (int, float)):
                latencies.append(float(latency))
            diag = metadata.get("diagnostics") if isinstance(metadata, Mapping) else {}
            if isinstance(diag, Mapping):
                hit = diag.get("alias_cache_hit")
                miss = diag.get("alias_cache_miss")
                if isinstance(hit, int):
                    cache_hits += hit
                    cache_total += hit
                if isinstance(miss, int):
                    cache_total += miss
        alias_hit_rate = round(alias_hits / alias_total, 3) if alias_total else 0.0
        alias_cache_hit_rate = round(cache_hits / cache_total, 3) if cache_total else None
        alias_stats = AliasCoverageStats(
            alias_count=alias_count,
            entity_count=entity_count,
            alias_hit_rate=alias_hit_rate,
            alias_cache_hit_rate=alias_cache_hit_rate,
        )
        stage_stats = SearchStageStats(
            alias_exact=stage_counter.get("alias_exact", 0),
            hybrid=stage_counter.get("hybrid", 0),
            fallback=stage_counter.get("fallback", 0),
            not_found=not_found,
        )
        ingestion = cls._ingestion_health(business_profile)
        latency_stats = LatencyStats(
            p50=statistics.median(latencies) if latencies else None,
            p95=cls._p95(latencies),
        )
        snapshot = KnowledgeOpsSnapshot(
            alias_coverage=alias_stats,
            stages=stage_stats,
            ingestion=ingestion,
            latency=latency_stats,
            sample_size=len(samples),
            observed_at=timezone.now().isoformat(),
        )
        if span.is_recording():
            span.set_attribute("knowledge.ops.samples", len(samples))
            span.set_attribute("knowledge.ops.alias_count", alias_count)
        return snapshot

    @staticmethod
    def _ingestion_health(business_profile: BusinessProfile) -> IngestionHealthStats:
        with TRACER.start_as_current_span("knowledge.ops.ingestion_health") as span:
            job_counts = KnowledgeIngestionJob.objects.filter(business_profile=business_profile).aggregate(
                queued=Count("id", filter=Q(status=KnowledgeIngestionJobStatus.QUEUED)),
                running=Count("id", filter=Q(status=KnowledgeIngestionJobStatus.RUNNING)),
                deferred=Count("id", filter=Q(status=KnowledgeIngestionJobStatus.DEFERRED)),
            )
            truncation_events = KnowledgeUpload.objects.filter(
                business_profile=business_profile,
                ingestion_metadata__truncated_entities__gt=0,
            ).count()
            pending_embeddings = KnowledgeUpload.objects.filter(
                business_profile=business_profile,
                ingestion_metadata__pending_embedding_chunk_count__gt=0,
            ).aggregate(total=Sum("ingestion_metadata__pending_embedding_chunk_count"))["total"] or 0
            stats = IngestionHealthStats(
                queued_jobs=int(job_counts.get("queued") or 0),
                running_jobs=int(job_counts.get("running") or 0),
                deferred_jobs=int(job_counts.get("deferred") or 0),
                truncation_events=int(truncation_events),
                pending_embeddings=int(pending_embeddings),
            )
            if span.is_recording():
                span.set_attribute("knowledge.ops.ingest.queued", stats.queued_jobs)
                span.set_attribute("knowledge.ops.ingest.running", stats.running_jobs)
                span.set_attribute("knowledge.ops.ingest.pending_embeddings", stats.pending_embeddings)
            return stats

    @staticmethod
    def _p95(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
        return float(ordered[index])


__all__ = [
    "AliasCoverageStats",
    "SearchStageStats",
    "IngestionHealthStats",
    "LatencyStats",
    "KnowledgeOpsSnapshot",
    "KnowledgeOpsDashboard",
]
