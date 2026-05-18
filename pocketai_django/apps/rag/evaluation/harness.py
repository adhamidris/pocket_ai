from __future__ import annotations

import json
import math
import logging
import shutil
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.knowledge.models import (
    KnowledgeFeedbackCase,
    KnowledgeUpload,
    KnowledgeUploadFile,
)
from apps.rag.knowledge_search import KnowledgeSearchService, KnowledgeSnippet
from apps.rag.evaluation.datasets import GOLDEN_SETS, GoldenQuery, GoldenSet, GoldenFixture
from apps.knowledge.ingestion_jobs import queue_ingestion_job
from apps.knowledge.ingestion_service import KnowledgeIngestionService
from apps.rag.quality_monitor import QualityMonitor
from apps.rag.rag_logging import rag_log

logger = logging.getLogger(__name__)


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


class RAGEvaluationHarness:
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
    # Ingestion helpers

    def _prepare_business(self, golden_set: GoldenSet) -> BusinessProfile:
        user = self._ensure_eval_user(slug=golden_set.slug)
        session = self._ensure_registration_session(user)
        business, created = BusinessProfile.objects.get_or_create(
            slug=golden_set.business_slug,
            defaults={
                "user": user,
                "registration_session": session,
                "name": f"{golden_set.industry.title()} Eval",
                "industry": golden_set.industry,
                "status": "active",
                "metadata": {"rag_eval": True},
            },
        )
        if created:
            AgentProfile.objects.get_or_create(
                business_profile=business,
                defaults={
                    "user": user,
                    "name": f"{golden_set.slug.title()} Eval Agent",
                    "slug": f"{golden_set.slug}-eval",
                    "role": "Evaluation Assistant",
                    "traits": ["deterministic", "observability"],
                },
            )
        return business

    @staticmethod
    def _ensure_eval_user(slug: str) -> User:
        user_model = get_user_model()
        email = f"rag-eval+{slug}@pocketai.local"
        user, _ = user_model.objects.get_or_create(
            email=email,
            defaults={
                "first_name": "RAG",
                "last_name": "Eval",
                "is_staff": False,
                "is_active": True,
            },
        )
        return user

    @staticmethod
    def _ensure_registration_session(user: User) -> RegistrationSession:
        session = (
            RegistrationSession.objects.filter(user=user)
            .order_by("-created_at")
            .first()
        )
        if session:
            return session
        return RegistrationSession.objects.create(user=user, total_steps=4, steps_completed=4, is_complete=True)

    def _ingest_fixtures(
        self,
        golden_set: GoldenSet,
        *,
        business: BusinessProfile,
        force: bool,
    ) -> None:
        user = business.user
        queued = 0
        for fixture in golden_set.fixtures:
            upload = self._stage_fixture_upload(
                fixture,
                business=business,
                user=user,
            )
            job = queue_ingestion_job(upload, trigger=f"rag_eval:{golden_set.slug}", force=force)
            if job:
                queued += 1
        if queued:
            rag_log(
                "eval.ingest",
                {"queued": queued},
                context={"business": business.id},
                indent=1,
            )
        processed = 0
        while True:
            result = self.ingestion_service.process_next_job()
            if not result:
                break
            processed += 1
        if processed:
            rag_log(
                "eval.ingest_completed",
                {"completed": processed},
                context={"business": business.id},
                indent=1,
            )

    def _stage_fixture_upload(
        self,
        fixture: GoldenFixture,
        *,
        business: BusinessProfile,
        user: User,
    ) -> KnowledgeUpload:
        content_type = self._fixture_content_type(fixture)
        source_uid = f"rag-eval::{business.slug}::{fixture.name}"
        relative_path = Path("rag_eval") / business.slug / fixture.filename
        absolute = self.media_root / relative_path
        absolute.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture.absolute_path, absolute)

        upload, _ = KnowledgeUpload.objects.get_or_create(
            business_profile=business,
            source_uid=source_uid,
            defaults={
                "user": user,
                "display_name": fixture.description or fixture.name.replace("_", " ").title(),
                "source_name": fixture.filename,
                "source_type": KnowledgeSourceType.FILE,
                "status": KnowledgeStatus.PENDING,
                "metadata": {"rag_eval_fixture": fixture.name},
            },
        )
        upload.display_name = fixture.description or upload.display_name
        upload.source_name = fixture.filename
        upload.status = KnowledgeStatus.PENDING
        meta = dict(upload.metadata or {})
        meta["rag_eval_fixture"] = fixture.name
        upload.metadata = meta
        upload.save(update_fields=["display_name", "source_name", "status", "metadata", "updated_at"])

        file_detail, _ = KnowledgeUploadFile.objects.get_or_create(
            upload=upload,
            defaults={
                "filename": fixture.filename,
                "content_type": content_type,
                "storage_path": str(relative_path),
                "size_bytes": absolute.stat().st_size,
            },
        )
        file_detail.filename = fixture.filename
        file_detail.content_type = content_type
        file_detail.storage_path = str(relative_path)
        file_detail.size_bytes = absolute.stat().st_size
        file_detail.save(update_fields=["filename", "content_type", "storage_path", "size_bytes", "updated_at"])
        return upload

    # ------------------------------------------------------------------
    # Evaluation + aggregation

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
        start = time.perf_counter()
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

    def _fixture_name_for_upload_id(self, upload_id: uuid.UUID) -> str:
        cached = self._upload_fixture_cache.get(upload_id)
        if cached is not None:
            return cached
        try:
            upload = KnowledgeUpload.objects.only("id", "metadata").get(id=upload_id)
        except KnowledgeUpload.DoesNotExist:
            self._upload_fixture_cache[upload_id] = ""
            return ""
        metadata = upload.metadata if isinstance(upload.metadata, dict) else {}
        fixture_name = str(metadata.get("rag_eval_fixture") or "")
        self._upload_fixture_cache[upload_id] = fixture_name
        return fixture_name

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

    # ------------------------------------------------------------------
    # Serialization

    def _export_reports(self, reports: Sequence[EvaluationReport], destination: Path) -> None:
        payload = {
            "generated_at": timezone.now().isoformat(),
            "reports": [
                {
                    "slug": report.slug,
                    "business_id": str(report.business_id),
                    "metrics": report.metrics,
                    "latencies": report.latencies,
                    "status": report.status,
                    "violations": report.violations,
                    "fixtures": list(report.fixtures),
                    "feedback_cases": report.feedback_cases,
                    "observations": [asdict(obs) for obs in report.observations],
                }
                for report in reports
            ],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2))
        rag_log(
            "eval.export",
            {"path": destination},
            indent=1,
        )

    @staticmethod
    def _fixture_content_type(fixture: GoldenFixture) -> str:
        if fixture.source_type == "csv" or fixture.filename.lower().endswith(".csv"):
            return "text/csv"
        if fixture.source_type == "tsv" or fixture.filename.lower().endswith(".tsv"):
            return "text/tab-separated-values"
        if fixture.source_type == "pdf" or fixture.filename.lower().endswith(".pdf"):
            return "application/pdf"
        return "application/json"

    @staticmethod
    def _snippet_token_count(snippet: KnowledgeSnippet) -> int:
        content = (snippet.content or "").strip()
        if not content:
            content = (snippet.summary or "").strip()
        return len(content.split()) if content else 0

    @staticmethod
    def _snippet_previews(snippets: Sequence[KnowledgeSnippet]) -> tuple[Mapping[str, Any], ...]:
        previews: list[Mapping[str, Any]] = []
        for snippet in snippets:
            content = (snippet.content or "").strip()
            summary = (snippet.summary or "").strip()
            preview_source = content or summary
            if len(preview_source) > 400:
                preview_source = f"{preview_source[:400]}..."
            previews.append(
                {
                    "snippet_id": str(snippet.id),
                    "upload_id": str(snippet.upload_id) if snippet.upload_id else None,
                    "title": snippet.title,
                    "summary": summary[:200] + "..." if len(summary) > 200 else summary,
                    "preview": preview_source,
                    "search_stage": snippet.search_stage,
                    "is_table_chunk": bool(snippet.is_table_chunk),
                }
            )
        return tuple(previews)


__all__ = [
    "RAGEvaluationHarness",
    "EvaluationReport",
    "QueryObservation",
]
