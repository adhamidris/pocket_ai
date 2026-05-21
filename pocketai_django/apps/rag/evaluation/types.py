from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class QueryObservation:
    query_id: str
    text: str
    query_type: str
    expected_behavior: str
    has_targets: bool
    status: str
    path: str | None
    behavior_correct: bool
    wrong_source: bool
    top_entities: tuple[str, ...]
    top_chunk_ids: tuple[str, ...]
    top_upload_ids: tuple[str, ...]
    top_fixtures: tuple[str, ...]
    top_score_breakdown: Mapping[str, Any]
    top_vector_distance: float | None
    match_rank: int | None
    reciprocal_rank: float
    precision_at_k: float
    top1: bool
    top3: bool
    classification_correct: bool
    alias_short_circuit: bool
    alias_hits: tuple[str, ...]
    alias_latency_ms: int | None
    vector_latency_ms: int | None
    lexical_latency_ms: int | None
    rerank_latency_ms: int | None
    total_latency_ms: int
    top_snippet_previews: tuple[Mapping[str, Any], ...]
    top_snippet_token_counts: tuple[int, ...]
    top_snippet_char_counts: tuple[int, ...]
    diagnostics: Mapping[str, Any]


@dataclass
class EvaluationReport:
    slug: str
    business_id: uuid.UUID
    metrics: Mapping[str, Any]
    latencies: Mapping[str, Mapping[str, float]]
    observations: Sequence[QueryObservation]
    status: str
    violations: Mapping[str, Any]
    fixtures: Sequence[str]
    feedback_cases: int


class EvaluationThresholdError(RuntimeError):
    def __init__(self, violations: Mapping[str, Mapping[str, Any]]):
        self.violations = violations
        details = ", ".join(f"{slug}:{len(items)}" for slug, items in violations.items())
        super().__init__(f"Evaluation thresholds failed ({details})")
