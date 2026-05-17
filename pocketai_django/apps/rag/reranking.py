from __future__ import annotations

import math
import time
from typing import Mapping, Sequence

from apps.accounts.feature_flags import FeatureState
from apps.rag.contracts import ChunkResult, QueryTraits


class RerankingMixin:
    def _rerank_candidates(
        self,
        candidates: Sequence[ChunkResult],
        query_vector: list[float] | None,
        *,
        traits: QueryTraits,
        feature_state: FeatureState | None = None,
        table_context: Mapping[str, object] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        if not candidates:
            return (
                [],
                0,
                {
                    "table_residual_rescue_applied": False,
                    "table_residual_rescue_count": 0,
                    "table_residual_rescue_reason": "no_candidates",
                },
            )
        start = time.perf_counter()
        rerank_diag: dict[str, object] = {
            "table_residual_rescue_applied": False,
            "table_residual_rescue_count": 0,
            "table_residual_rescue_reason": None,
        }
        continuity_allowed_for_rerank = False
        continuity_reason_for_rerank = "document_continuity_removed"
        rerank_diag.update(
            {
                "document_continuity_allowed": continuity_allowed_for_rerank,
                "document_continuity_reason": continuity_reason_for_rerank or None,
                "document_continuity_primary_present": False,
                "document_continuity_boosted_candidates": 0,
                "document_continuity_max_bonus": 0.0,
            }
        )
        top_pool = min(len(candidates), self.rerank_pool)
        scored: list[tuple[float, int, ChunkResult]] = []
        tail: list[ChunkResult] = []
        text_penalty_enabled = bool(feature_state and feature_state.rag_text_chunk_penalty)
        table_context = table_context or {}
        table_intent = bool(table_context.get("has_intent"))
        modality_bias = str(table_context.get("modality_bias") or "").strip().lower()
        if modality_bias not in {"table", "text", "mixed"}:
            modality_bias = "table" if table_intent else "text"
        prefer_section_context = bool(table_context.get("prefer_section_context"))
        section_focus_terms = tuple(
            str(item).strip().lower()
            for item in (table_context.get("section_focus_terms") or ())
            if str(item).strip()
        )
        query_tokens = set(table_context.get("query_tokens") or ())
        specific_tokens = set(table_context.get("specific_tokens") or ())
        document_name_tokens = self._document_name_relevance_tokens(traits)
        rerank_diag["document_name_boost_token_count"] = len(document_name_tokens)
        for idx, cand in enumerate(candidates):
            if idx >= top_pool:
                tail.append(cand)
                continue
            vector_score = 0.0
            if query_vector:
                if cand.vector_distance is not None:
                    try:
                        vector_score = 1.0 - float(cand.vector_distance)
                    except (TypeError, ValueError):
                        vector_score = 0.0
                else:
                    vector_score = self._cosine_similarity(query_vector, self._chunk_embedding(cand))
            lexical_score = cand.lexical_score or self._lexical_overlap_score(cand.chunk, traits.tokens)
            entity_bonus = self._entity_bonus(cand.chunk, traits.tokens, traits.normalized)
            alias_bonus = max(cand.alias_confidence, self._alias_bonus(cand.chunk, traits.tokens, traits.normalized))
            recency_score = cand.recency_score or self._recency_score(cand.chunk.upload)

            quality_penalty = 0.0
            chunk_metadata = cand.chunk.metadata if isinstance(cand.chunk.metadata, dict) else {}
            table_header_bonus = 0.0
            modality_bias_bonus = 0.0
            table_specific_penalty = 0.0
            table_residual_penalty = 0.0
            table_structural_penalty = 0.0
            is_table_residual = bool(
                chunk_metadata.get("table_residual")
                or chunk_metadata.get("content_source") == "table_residual"
                or chunk_metadata.get("region_role") == "table_residual"
            )

            if chunk_metadata.get("is_table_chunk"):
                quality_score_raw = chunk_metadata.get("table_quality_score")
                is_decorative = chunk_metadata.get("table_is_decorative", False)

                quality_score = None
                try:
                    if quality_score_raw is not None:
                        quality_score = float(quality_score_raw)
                        if not math.isfinite(quality_score):
                            quality_score = None
                        elif not (0.0 <= quality_score <= 1.0):
                            quality_score = max(0.0, min(1.0, quality_score))
                except (TypeError, ValueError):
                    quality_score = None

                if quality_score is not None and quality_score < self.table_quality_threshold:
                    quality_gap = self.table_quality_threshold - quality_score
                    quality_penalty = (quality_gap / self.table_quality_threshold) * 0.5
                elif is_decorative:
                    quality_penalty = 0.30
                if table_intent:
                    match_info = self._table_chunk_match_info(
                        cand.chunk,
                        query_tokens=query_tokens,
                        specific_tokens=specific_tokens,
                    )
                    cand.diagnostics.update(match_info)
                    structural_info = self._table_structural_row_info(cand.chunk)
                    cand.diagnostics.update(structural_info)
                    numeric_table_intent = bool(table_context.get("numeric_intent"))
                    if match_info.get("header_match") and not structural_info.get("structural_row"):
                        table_header_bonus = self.table_header_match_bonus
                    if specific_tokens and not match_info.get("specific_match"):
                        table_specific_penalty = self.table_specific_miss_penalty
                    if structural_info.get("structural_row") and (specific_tokens or numeric_table_intent):
                        table_structural_penalty = min(
                            0.3,
                            max(self.table_header_match_bonus + 0.12, self.table_specific_miss_penalty * 0.8),
                        )
                if modality_bias == "table":
                    modality_bias_bonus = 0.04
                elif modality_bias == "mixed" and table_intent:
                    modality_bias_bonus = 0.02

            text_penalty = 0.0
            section_context_boost = 0.0
            if text_penalty_enabled and not chunk_metadata.get("is_table_chunk") and not chunk_metadata.get("is_dataset_card"):
                index_type = chunk_metadata.get("index_type")
                if index_type in (None, "text"):
                    text_penalty = self._text_quality_penalty(chunk_metadata)
            if prefer_section_context and not chunk_metadata.get("is_table_chunk") and not chunk_metadata.get("is_dataset_card"):
                index_type = chunk_metadata.get("index_type")
                if index_type in (None, "text"):
                    section_context_boost = self._section_context_boost(
                        chunk_metadata,
                        query_tokens=traits.tokens,
                        query_text=traits.normalized or traits.original or "",
                        focus_terms=section_focus_terms,
                    )
            if is_table_residual and not chunk_metadata.get("is_table_chunk"):
                if table_intent:
                    table_residual_penalty = self.table_residual_table_intent_penalty
                else:
                    table_residual_penalty = self.table_residual_penalty
            text_phrase_boost = 0.0
            text_proximity_boost = 0.0
            if not chunk_metadata.get("is_table_chunk"):
                if modality_bias == "text":
                    modality_bias_bonus = 0.04
                elif modality_bias == "mixed" and not table_intent:
                    modality_bias_bonus = 0.02
                text_phrase_boost = self._exact_phrase_boost(
                    cand.chunk.content or "",
                    traits.normalized or traits.original or "",
                    max_boost=0.32,
                )
                text_proximity_boost = self._token_proximity_boost(
                    cand.chunk.content or "",
                    traits.tokens,
                    max_boost=0.18,
                )

            document_name_boost = 0.0
            upload = getattr(cand.chunk, "upload", None)
            if upload is not None:
                doc_label = (
                    getattr(upload, "display_name", "") or
                    getattr(upload, "source_name", "") or ""
                )
                if doc_label and document_name_tokens:
                    document_name_boost = self._lexical_score_text(doc_label, document_name_tokens)

            document_continuity_bonus = 0.0

            combined = (
                self.rerank_weights["vector"] * vector_score
                + self.rerank_weights["lexical"] * lexical_score
                + self.rerank_weights["alias"] * alias_bonus
                + self.rerank_weights["entity"] * entity_bonus
                + self.rerank_weights["recency"] * recency_score
                + table_header_bonus
                + self.rerank_weights["document_name"] * document_name_boost
                + document_continuity_bonus
                + section_context_boost
                + modality_bias_bonus
                + text_phrase_boost
                + text_proximity_boost
                - quality_penalty
                - table_specific_penalty
                - table_structural_penalty
                - text_penalty
                - table_residual_penalty
            )
            cand.diagnostics["score_breakdown"] = {
                "vector": round(vector_score, 4),
                "lexical": round(lexical_score, 4),
                "alias": round(alias_bonus, 4),
                "entity": round(entity_bonus, 4),
                "recency": round(recency_score, 4),
                "table_header_bonus": round(table_header_bonus, 4),
                "document_name_boost": round(document_name_boost, 4),
                "document_name_token_count": len(document_name_tokens),
                "document_continuity_bonus": round(document_continuity_bonus, 4),
                "section_context_boost": round(section_context_boost, 4),
                "modality_bias_bonus": round(modality_bias_bonus, 4),
                "quality_penalty": round(quality_penalty, 4),
                "table_specific_penalty": round(table_specific_penalty, 4),
                "table_structural_penalty": round(table_structural_penalty, 4),
                "text_penalty": round(text_penalty, 4),
                "table_residual_penalty": round(table_residual_penalty, 4),
                "text_phrase_boost": round(text_phrase_boost, 4),
                "text_proximity_boost": round(text_proximity_boost, 4),
                "table_residual_rescue_bonus": 0.0,
            }
            cand.diagnostics["table_residual_candidate"] = bool(is_table_residual and not chunk_metadata.get("is_table_chunk"))
            cand.rerank_score = combined
            scored.append((combined, -idx, cand))
        comprehensive_intent = bool(table_context.get("comprehensive_intent"))
        allow_broad_rescue = bool(table_intent and comprehensive_intent and not specific_tokens)
        if table_intent and self.table_residual_rescue_enabled and scored and (specific_tokens or allow_broad_rescue):
            canonical_candidates = []
            for item in scored:
                metadata = item[2].chunk.metadata if isinstance(item[2].chunk.metadata, dict) else {}
                if metadata.get("is_table_chunk"):
                    canonical_candidates.append(item)
            residual_candidates: list[tuple[int, float, int, ChunkResult, float, float]] = []
            if specific_tokens:
                canonical_specific_hits = sum(
                    1
                    for _, _, hit in canonical_candidates
                    if bool(hit.diagnostics.get("specific_match_strong") or hit.diagnostics.get("specific_match"))
                )
            else:
                canonical_specific_hits = 0
            best_canonical_score = max((score for score, _, _ in canonical_candidates), default=None)

            for scored_index, (base_score, order, hit) in enumerate(scored):
                hit_meta = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
                is_residual = bool(
                    (not hit_meta.get("is_table_chunk"))
                    and (
                        hit_meta.get("table_residual")
                        or hit_meta.get("content_source") == "table_residual"
                        or hit_meta.get("region_role") == "table_residual"
                    )
                )
                is_supporting = bool(
                    (not hit_meta.get("is_table_chunk"))
                    and (
                        hit_meta.get("table_annotation")
                        or hit_meta.get("content_source") == "table_annotation"
                        or hit_meta.get("region_role") == "table_annotation"
                    )
                )
                if not (is_residual or is_supporting):
                    continue
                score_breakdown = hit.diagnostics.get("score_breakdown") or {}
                phrase_boost = float(score_breakdown.get("text_phrase_boost") or 0.0)
                lexical_component = float(score_breakdown.get("lexical") or 0.0)
                phrase_min = self.table_residual_rescue_phrase_min
                if allow_broad_rescue:
                    phrase_min = 0.0
                if phrase_boost < phrase_min:
                    continue
                lexical_min = self.table_residual_rescue_lexical_min
                if allow_broad_rescue:
                    lexical_min = min(lexical_min, 0.45)
                if lexical_component < lexical_min:
                    continue
                residual_candidates.append(
                    (scored_index, base_score, order, hit, phrase_boost, lexical_component)
                )

            if canonical_specific_hits > 0:
                rerank_diag["table_residual_rescue_reason"] = "canonical_specific_match_present"
            elif not residual_candidates:
                rerank_diag["table_residual_rescue_reason"] = "no_eligible_residual"
            else:
                residual_candidates.sort(key=lambda item: (item[4], item[5], item[1]), reverse=True)
                promoted = 0
                for scored_index, base_score, order, hit, phrase_boost, lexical_component in residual_candidates:
                    if promoted >= self.table_residual_rescue_max_results:
                        break
                    boosted = base_score + self.table_residual_rescue_bonus
                    hit.rerank_score = boosted
                    score_breakdown = dict(hit.diagnostics.get("score_breakdown") or {})
                    score_breakdown["table_residual_rescue_bonus"] = round(
                        self.table_residual_rescue_bonus,
                        4,
                    )
                    hit.diagnostics["score_breakdown"] = score_breakdown
                    hit.diagnostics["table_residual_rescue"] = {
                        "applied": True,
                        "phrase_boost": round(phrase_boost, 4),
                        "lexical": round(lexical_component, 4),
                        "bonus": round(self.table_residual_rescue_bonus, 4),
                        "canonical_specific_hits": canonical_specific_hits,
                        "best_canonical_score": round(float(best_canonical_score), 4)
                        if isinstance(best_canonical_score, (int, float))
                        else None,
                    }
                    scored[scored_index] = (boosted, order, hit)
                    promoted += 1
                rerank_diag["table_residual_rescue_applied"] = bool(promoted)
                rerank_diag["table_residual_rescue_count"] = promoted
                rerank_diag["table_residual_rescue_reason"] = (
                    "promoted"
                    if promoted
                    else "residual_candidates_below_cap_or_bonus_zero"
                )
        if rerank_diag.get("table_residual_rescue_reason") is None:
            if not table_intent:
                rerank_diag["table_residual_rescue_reason"] = "not_table_intent"
            elif not specific_tokens:
                rerank_diag["table_residual_rescue_reason"] = "no_specific_tokens"
            elif not self.table_residual_rescue_enabled:
                rerank_diag["table_residual_rescue_reason"] = "disabled"
            else:
                rerank_diag["table_residual_rescue_reason"] = "not_applicable"
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        reranked = [item[2] for item in scored]
        if tail:
            reranked.extend(tail)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return reranked, duration_ms, rerank_diag
