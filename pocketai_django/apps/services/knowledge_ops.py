"""
Knowledge operations observability and metrics aggregation.

This module provides dashboard metrics for monitoring knowledge base health,
retrieval performance, and ingestion pipeline status. Metrics are aggregated
from KnowledgeDriftSample records created by QualityMonitor during retrieval
and ingestion operations.

Architecture:
    KnowledgeDriftSample records are created by QualityMonitor.record_retrieval_sample()
    and QualityMonitor.record_ingestion_sample() during normal operations. This module
    aggregates those samples into snapshot metrics for admin dashboards.

Key metrics:
    - Alias coverage: How well identifier aliases are matching queries
    - Search stages: Distribution of search strategies (alias_exact, hybrid, fallback)
    - Ingestion health: Job queue status, truncation events, pending embeddings
    - Latency: P50 and P95 response times for retrieval operations

Related modules:
    - quality_monitor.py: Creates KnowledgeDriftSample records during operations
    - accounts/models.py: Defines KnowledgeDriftSample, KnowledgeIngestionJob models
    - frontend/views.py: Uses KnowledgeOpsDashboard.snapshot() for dashboard display
"""

from __future__ import annotations

import dataclasses
import statistics
from collections import Counter
from typing import Mapping

from django.db.models import Count, Q, Sum
from django.utils import timezone

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
    """
    Statistics on identifier alias coverage and cache performance.

    Tracks how well the knowledge base's alias system is performing:
    - Total aliases and entities in the knowledge base
    - Hit rate: percentage of identifier queries that matched an alias
    - Cache hit rate: percentage of alias lookups served from cache

    Why:
        Identifier aliases (e.g., "SKU-123" -> "Product X") are critical for
        matching user queries to knowledge entities. These stats help identify
        when alias coverage is insufficient or cache performance is degrading.

    Used by:
        - KnowledgeOpsDashboard.snapshot() aggregates these from retrieval samples
        - Admin dashboards display these to monitor knowledge base health
    """

    alias_count: int  # Total number of aliases in the knowledge base
    entity_count: int  # Total number of knowledge entities
    alias_hit_rate: float  # Percentage of identifier queries that matched an alias (0.0-1.0)
    alias_cache_hit_rate: float | None  # Percentage of alias lookups from cache (None if no cache data)


@dataclasses.dataclass(frozen=True)
class SearchStageStats:
    """
    Distribution of search strategies used during retrieval operations.

    Tracks which search stage was used for each query:
    - alias_exact: Direct alias match (fastest, most accurate)
    - hybrid: Combined alias + semantic search (balanced)
    - fallback: Semantic search only (when aliases don't match)
    - not_found: No results found

    Why:
        The knowledge search system uses a multi-stage approach. Understanding
        the distribution helps identify when alias coverage is insufficient
        (high fallback rate) or when queries aren't matching (high not_found).

    Used by:
        - KnowledgeOpsDashboard.snapshot() counts stages from retrieval samples
        - Admin dashboards show this to diagnose search performance issues
    """

    alias_exact: int  # Number of queries matched via direct alias lookup
    hybrid: int  # Number of queries using combined alias + semantic search
    fallback: int  # Number of queries falling back to semantic search only
    not_found: int  # Number of queries that returned no results


@dataclasses.dataclass(frozen=True)
class IngestionHealthStats:
    """
    Health metrics for the knowledge ingestion pipeline.

    Tracks ingestion job status and pipeline health indicators:
    - Job queue status: queued, running, deferred jobs
    - Truncation events: uploads where entities were truncated (data loss indicator)
    - Pending embeddings: chunks waiting to be embedded (pipeline backlog)

    Why:
        Ingestion pipeline health is critical for knowledge base freshness.
        High deferred jobs indicate processing issues. Truncation events suggest
        data quality problems. Pending embeddings show embedding pipeline backlog.

    Used by:
        - KnowledgeOpsDashboard._ingestion_health() aggregates from database queries
        - Admin dashboards alert on high deferred jobs or truncation rates
    """

    queued_jobs: int  # Number of ingestion jobs waiting to start
    running_jobs: int  # Number of ingestion jobs currently processing
    deferred_jobs: int  # Number of ingestion jobs deferred (failed, retrying)
    truncation_events: int  # Number of uploads with truncated entities (data loss)
    pending_embeddings: int  # Total number of chunks waiting for embedding generation


@dataclasses.dataclass(frozen=True)
class LatencyStats:
    """
    Percentile latency statistics for retrieval operations.

    Tracks response time distribution:
    - P50 (median): Half of requests complete within this time
    - P95: 95% of requests complete within this time (tail latency)

    Why:
        Latency percentiles are more useful than averages for understanding
        user experience. P50 shows typical performance, P95 shows worst-case
        performance that affects a small but significant portion of users.

    Used by:
        - KnowledgeOpsDashboard.snapshot() calculates from retrieval sample latencies
        - Admin dashboards use these to monitor retrieval performance degradation
    """

    p50: float | None  # Median latency in milliseconds (50th percentile)
    p95: float | None  # 95th percentile latency in milliseconds (None if no samples)


@dataclasses.dataclass(frozen=True)
class KnowledgeOpsSnapshot:
    """
    Complete snapshot of knowledge operations metrics at a point in time.

    Aggregates all observability metrics into a single immutable snapshot.
    Used for dashboard display and historical trend analysis.

    Why:
        Snapshot-based metrics allow dashboards to show consistent views
        and enable comparison across time periods. Immutable snapshots
        prevent accidental modification of historical data.

    Used by:
        - KnowledgeOpsDashboard.snapshot() creates these from recent samples
        - Admin dashboards display these snapshots for monitoring
        - Can be serialized to JSON for API responses or storage

    Related:
        - QualityMonitor creates the underlying KnowledgeDriftSample records
        - frontend/views.py calls snapshot() to populate dashboard data
    """

    alias_coverage: AliasCoverageStats  # Alias matching and cache performance
    stages: SearchStageStats  # Distribution of search strategies used
    ingestion: IngestionHealthStats  # Ingestion pipeline health
    latency: LatencyStats  # Retrieval response time percentiles
    sample_size: int  # Number of retrieval samples analyzed (for confidence)
    observed_at: str  # ISO timestamp when snapshot was created


class KnowledgeOpsDashboard:
    """
    Aggregate observability metrics for admin dashboards and runbooks.

    Provides snapshot-based metrics aggregation from KnowledgeDriftSample records.
    Designed for dashboard display and operational monitoring of knowledge base
    health, retrieval performance, and ingestion pipeline status.

    Why:
        Centralized metrics aggregation makes it easy to add new dashboards
        without duplicating aggregation logic. Snapshot-based approach ensures
        consistent metrics views and enables historical comparison.

    Usage:
        Call snapshot() to get current metrics for a business profile. The
        method aggregates recent retrieval samples and current ingestion status
        into a KnowledgeOpsSnapshot.

    Related:
        - QualityMonitor.record_retrieval_sample() creates the samples we aggregate
        - frontend/views.py uses snapshot() for dashboard display
        - accounts/models.py defines the underlying data models
    """

    # Default limit for retrieval samples to analyze (balances accuracy vs performance)
    # Higher limits provide more accurate metrics but slower queries
    SAMPLE_LIMIT = 200

    @classmethod
    def snapshot(cls, business_profile: BusinessProfile, sample_limit: int | None = None) -> KnowledgeOpsSnapshot:
        """
        Generate a snapshot of knowledge operations metrics for a business profile.

        Aggregates recent retrieval samples and current ingestion status into
        a comprehensive metrics snapshot. Analyzes the most recent samples to
        provide current performance indicators.

        Args:
            business_profile: Business profile to generate metrics for.
            sample_limit: Optional limit on number of samples to analyze.
                Defaults to SAMPLE_LIMIT (200). Higher values = more accurate
                but slower queries.

        Returns:
            KnowledgeOpsSnapshot with aggregated metrics including:
            - Alias coverage and cache hit rates
            - Search stage distribution
            - Ingestion pipeline health
            - Latency percentiles (P50, P95)

        Design:
            - Fetches most recent retrieval samples (ordered by observed_at DESC)
            - Aggregates metrics across samples to calculate rates and distributions
            - Queries current ingestion job status separately (real-time data)
            - Calculates latency percentiles from sample latencies

        Why:
            Recent samples provide current performance indicators. Limiting sample
            count balances accuracy with query performance. Separate ingestion
            queries ensure we get real-time job status, not historical samples.

        Related:
            - QualityMonitor.record_retrieval_sample() creates the samples we analyze
            - frontend/views.py calls this for dashboard display
        """
        # Use provided limit or default to SAMPLE_LIMIT
        limit = sample_limit or cls.SAMPLE_LIMIT

        # Get current knowledge base size (aliases and entities)
        # These are point-in-time counts, not aggregated from samples
        alias_count = KnowledgeAlias.objects.filter(business_profile=business_profile).count()
        entity_count = KnowledgeEntity.objects.filter(business_profile=business_profile).count()

        # Fetch most recent retrieval samples for this business
        # Only fetch RETRIEVAL samples (not INGESTION samples)
        # Order by most recent first, limit to sample_limit for performance
        # Use values() to only fetch metrics and metadata (not full model instances)
        samples = list(
            KnowledgeDriftSample.objects.filter(
                business_profile=business_profile,
                sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            )
            .order_by("-observed_at")
            .values("metrics", "metadata")[:limit]
        )

        # Initialize counters for aggregating metrics across samples
        stage_counter = Counter()  # Count occurrences of each search stage
        alias_hits = 0  # Identifier queries that matched an alias
        alias_total = 0  # Total identifier queries (for hit rate calculation)
        cache_hits = 0  # Alias lookups served from cache
        cache_total = 0  # Total alias lookups (for cache hit rate)
        not_found = 0  # Queries that returned no results
        latencies: list[float] = []  # All latency values (for percentile calculation)

        # Aggregate metrics from each sample
        for sample in samples:
            # Extract metrics and metadata (defensive: handle None/missing keys)
            metrics = sample.get("metrics") or {}
            metadata = sample.get("metadata") or {}

            # Count search stage usage (alias_exact, hybrid, fallback, etc.)
            stage = metrics.get("stage") or ""
            stage_counter[stage] += 1

            # Count not_found queries (separate from stage counter for clarity)
            if metrics.get("status") == "not_found":
                not_found += 1

            # Calculate alias hit rate: only for identifier queries
            # Other query types (semantic, etc.) don't use aliases
            if metrics.get("query_type") == "identifier":
                alias_total += 1
                if metrics.get("alias_hit"):
                    alias_hits += 1

            # Collect latency values for percentile calculation
            # Only include numeric values (defensive: handle missing/invalid data)
            latency = metrics.get("latency_ms")
            if isinstance(latency, (int, float)):
                latencies.append(float(latency))

            # Extract cache statistics from diagnostics metadata
            # Cache stats are in metadata.diagnostics, not metrics
            diag = metadata.get("diagnostics") if isinstance(metadata, Mapping) else {}
            if isinstance(diag, Mapping):
                hit = diag.get("alias_cache_hit")
                miss = diag.get("alias_cache_miss")
                # Accumulate cache hits and misses (defensive: only count if numeric)
                if isinstance(hit, int):
                    cache_hits += hit
                    cache_total += hit  # Hits are part of total
                if isinstance(miss, int):
                    cache_total += miss  # Misses are also part of total

        # Calculate hit rates (round to 3 decimal places for readability)
        # Handle division by zero: return 0.0 if no identifier queries, None if no cache data
        alias_hit_rate = round(alias_hits / alias_total, 3) if alias_total else 0.0
        alias_cache_hit_rate = round(cache_hits / cache_total, 3) if cache_total else None
        # Build stats objects from aggregated data
        alias_stats = AliasCoverageStats(
            alias_count=alias_count,
            entity_count=entity_count,
            alias_hit_rate=alias_hit_rate,
            alias_cache_hit_rate=alias_cache_hit_rate,
        )
        # Extract stage counts from counter (default to 0 if stage not present)
        # not_found is tracked separately because it's a status, not a stage
        stage_stats = SearchStageStats(
            alias_exact=stage_counter.get("alias_exact", 0),
            hybrid=stage_counter.get("hybrid", 0),
            fallback=stage_counter.get("fallback", 0),
            not_found=not_found,
        )
        # Get current ingestion pipeline health (real-time query, not from samples)
        ingestion = cls._ingestion_health(business_profile)
        # Calculate latency percentiles (P50 = median, P95 = 95th percentile)
        latency_stats = LatencyStats(
            p50=statistics.median(latencies) if latencies else None,
            p95=cls._p95(latencies),
        )
        # Return complete snapshot with all metrics
        return KnowledgeOpsSnapshot(
            alias_coverage=alias_stats,
            stages=stage_stats,
            ingestion=ingestion,
            latency=latency_stats,
            sample_size=len(samples),  # Include sample size for confidence/context
            observed_at=timezone.now().isoformat(),  # ISO timestamp for display/storage
        )

    @staticmethod
    def _ingestion_health(business_profile: BusinessProfile) -> IngestionHealthStats:
        """
        Query current ingestion pipeline health metrics.

        Aggregates real-time ingestion job status and pipeline indicators.
        Unlike retrieval metrics (from samples), this queries current database
        state for up-to-the-moment pipeline health.

        Args:
            business_profile: Business profile to query ingestion health for.

        Returns:
            IngestionHealthStats with current job counts and pipeline indicators.

        Why:
            Ingestion health needs real-time data (current job status), not
            historical samples. This ensures dashboards show current pipeline
            state, not stale metrics.

        Design:
            - Uses Django aggregate() for efficient counting with filters
            - Queries KnowledgeUpload for truncation events (data quality indicator)
            - Sums pending embedding chunks across all uploads (pipeline backlog)

        Related:
            - KnowledgeIngestionJob tracks individual ingestion job status
            - KnowledgeUpload.ingestion_metadata stores pipeline state
        """
        # Count ingestion jobs by status using efficient aggregate query
        # Filter by status in the aggregate to avoid multiple queries
        job_counts = KnowledgeIngestionJob.objects.filter(business_profile=business_profile).aggregate(
            queued=Count("id", filter=Q(status=KnowledgeIngestionJobStatus.QUEUED)),
            running=Count("id", filter=Q(status=KnowledgeIngestionJobStatus.RUNNING)),
            deferred=Count("id", filter=Q(status=KnowledgeIngestionJobStatus.DEFERRED)),
        )

        # Count uploads with truncated entities (indicates data quality issues)
        # Truncation happens when entity data exceeds size limits
        # ingestion_metadata__truncated_entities__gt=0 uses JSON field path lookup
        truncation_events = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            ingestion_metadata__truncated_entities__gt=0,
        ).count()

        # Sum total pending embedding chunks across all uploads
        # This shows embedding pipeline backlog (chunks waiting to be embedded)
        # ingestion_metadata__pending_embedding_chunk_count is a JSON field
        pending_embeddings = KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            ingestion_metadata__pending_embedding_chunk_count__gt=0,
        ).aggregate(total=Sum("ingestion_metadata__pending_embedding_chunk_count"))["total"] or 0

        # Build stats object (defensive: ensure all values are ints, handle None)
        return IngestionHealthStats(
            queued_jobs=int(job_counts.get("queued") or 0),
            running_jobs=int(job_counts.get("running") or 0),
            deferred_jobs=int(job_counts.get("deferred") or 0),
            truncation_events=int(truncation_events),
            pending_embeddings=int(pending_embeddings),
        )

    @staticmethod
    def _p95(values: list[float]) -> float | None:
        """
        Calculate the 95th percentile (P95) from a list of values.

        P95 is the value below which 95% of observations fall. Used for
        understanding tail latency (worst-case performance affecting 5% of requests).

        Args:
            values: List of numeric values to calculate percentile from.

        Returns:
            95th percentile value, or None if list is empty.

        Why:
            P95 is more useful than max for understanding user experience because
            it ignores outliers while still showing worst-case performance that
            affects a meaningful portion of users. P50 (median) shows typical
            performance, P95 shows tail performance.

        Algorithm:
            - Sort values in ascending order
            - Calculate index at 95% of length (0.95 * (n-1))
            - Clamp index to valid range [0, len-1] to handle edge cases
            - Return value at that index

        Edge cases:
            - Empty list: returns None
            - Single value: returns that value (index = 0)
            - Clamping ensures index is always valid even with rounding errors
        """
        if not values:
            return None
        # Sort values for percentile calculation
        ordered = sorted(values)
        # Calculate index at 95th percentile
        # Formula: 0.95 * (n-1) because indices are 0-based
        # Clamp to [0, len-1] to handle rounding edge cases
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
