"""
Retrieval Critique Module for Agentic RAG (Phase 2).

This module implements confidence-gated retrieval critique, where a lightweight
LLM check verifies that search results match query intent before the main LLM
generates an answer.

Key Components:
- RetrievalConfidenceScorer: Computes confidence scores from multiple signals
- RetrievalCritique: Lightweight LLM critique for low-confidence results

This enables self-correcting retrieval without adding cost to every query -
only ~15-20% of queries with low confidence trigger the critique.

References:
- CRAG (Corrective RAG): Query reformulation when retrieval quality is poor
- Self-RAG: LLM generates critique tokens to evaluate retrieved docs
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from django.conf import settings

logger = logging.getLogger(__name__)


# Stopwords to exclude from term overlap calculation
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "how", "i", "in", "is", "it", "its", "me", "my", "of", "on", "or", "our",
    "please", "show", "tell", "that", "the", "their", "them", "they", "this",
    "to", "what", "with", "you", "your", "about", "can", "do", "does", "get",
    "has", "have", "if", "just", "know", "like", "more", "much", "need", "not",
    "only", "other", "out", "some", "than", "then", "there", "these", "those",
    "up", "us", "very", "want", "was", "we", "well", "were", "when", "where",
    "which", "who", "will", "would",
})


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words."""
    return [t for t in re.findall(r"[\w']+", (text or "").lower()) if t]


def _significant_tokens(text: str, min_length: int = 3) -> set[str]:
    """Extract significant tokens (non-stopwords, min length)."""
    return {
        t for t in _tokenize(text)
        if len(t) >= min_length and t not in _STOPWORDS
    }


@dataclass(frozen=True)
class ConfidenceSignals:
    """
    Signals used to compute retrieval confidence.

    Each signal contributes to the overall confidence score.
    """
    # Vector distance of top result (0.0 = perfect match, 1.0 = no match)
    top_vector_distance: float | None = None

    # Rerank score of top result (0.0 - 1.0, higher = better)
    top_rerank_score: float | None = None

    # Term overlap between query and top results (0.0 - 1.0)
    term_overlap_ratio: float = 0.0

    # Number of unique source documents in results
    source_diversity: int = 0

    # Whether table_direct results are present (exact match signal)
    has_table_direct: bool = False

    # Whether parallel_rrf path was used
    used_parallel_rrf: bool = False

    # Number of results returned
    result_count: int = 0

    # Whether query was rewritten
    query_was_rewritten: bool = False


@dataclass
class ConfidenceResult:
    """
    Result of confidence calculation.
    """
    score: float  # 0.0 - 1.0, higher = more confident
    signals: ConfidenceSignals
    should_critique: bool  # Whether LLM critique should be invoked
    reasons: list[str] = field(default_factory=list)  # Why confidence is low/high

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "should_critique": self.should_critique,
            "reasons": self.reasons,
            "signals": {
                "top_vector_distance": self.signals.top_vector_distance,
                "top_rerank_score": self.signals.top_rerank_score,
                "term_overlap_ratio": round(self.signals.term_overlap_ratio, 3),
                "source_diversity": self.signals.source_diversity,
                "has_table_direct": self.signals.has_table_direct,
                "used_parallel_rrf": self.signals.used_parallel_rrf,
                "result_count": self.signals.result_count,
            },
        }


class RetrievalConfidenceScorer:
    """
    Computes confidence scores for retrieval results.

    Uses multiple signals to determine if results likely match query intent:
    - Vector distance (semantic similarity)
    - Term overlap (lexical match)
    - Source diversity (single doc vs multiple)
    - Table direct hits (exact phrase match)
    - Rerank scores

    Example:
        scorer = RetrievalConfidenceScorer()
        result = scorer.compute(
            query="Withdraw Bills for Collection fees",
            snippets=search_results,
            diagnostics=result.diagnostics,
        )
        if result.should_critique:
            # Invoke LLM critique
    """

    # Weight for each signal in final score
    WEIGHTS = {
        "vector_distance": 0.25,
        "rerank_score": 0.20,
        "term_overlap": 0.25,
        "table_direct": 0.15,
        "source_diversity": 0.10,
        "result_count": 0.05,
    }

    def __init__(
        self,
        min_confidence: float = 0.5,
        vector_distance_good: float = 0.35,
        vector_distance_bad: float = 0.55,
        min_term_overlap: float = 0.3,
    ):
        """
        Initialize the confidence scorer.

        Args:
            min_confidence: Threshold below which critique is triggered
            vector_distance_good: Vector distance considered "good" (high confidence)
            vector_distance_bad: Vector distance considered "bad" (low confidence)
            min_term_overlap: Minimum term overlap for high confidence
        """
        self.min_confidence = min_confidence
        self.vector_distance_good = vector_distance_good
        self.vector_distance_bad = vector_distance_bad
        self.min_term_overlap = min_term_overlap

    def compute(
        self,
        query: str,
        snippets: Sequence[Mapping[str, object]],
        diagnostics: Mapping[str, object] | None = None,
    ) -> ConfidenceResult:
        """
        Compute confidence score for retrieval results.

        Args:
            query: The search query
            snippets: List of snippet payloads from search
            diagnostics: Search diagnostics dict

        Returns:
            ConfidenceResult with score and signals
        """
        diagnostics = diagnostics or {}

        # Extract signals
        signals = self._extract_signals(query, snippets, diagnostics)

        # Compute weighted confidence score
        score, reasons = self._compute_score(signals)

        # Determine if critique should be invoked
        should_critique = score < self.min_confidence

        if should_critique:
            reasons.append(f"confidence {score:.2f} < threshold {self.min_confidence}")

        return ConfidenceResult(
            score=score,
            signals=signals,
            should_critique=should_critique,
            reasons=reasons,
        )

    def _extract_signals(
        self,
        query: str,
        snippets: Sequence[Mapping[str, object]],
        diagnostics: Mapping[str, object],
    ) -> ConfidenceSignals:
        """Extract confidence signals from search results."""

        # Top result vector distance
        top_vector_distance = None
        if snippets:
            # Try to get from snippet metadata or diagnostics
            first_snippet = snippets[0] if snippets else {}
            source_diag = first_snippet.get("source_diagnostics") or {}
            if isinstance(source_diag, dict):
                top_vector_distance = source_diag.get("vector_distance")
            if top_vector_distance is None:
                top_vector_distance = diagnostics.get("top_vector_distance")

        # Top rerank score
        top_rerank_score = None
        if snippets:
            first_snippet = snippets[0] if snippets else {}
            top_rerank_score = first_snippet.get("confidence_score")
            if top_rerank_score is None:
                source_diag = first_snippet.get("source_diagnostics") or {}
                if isinstance(source_diag, dict):
                    top_rerank_score = source_diag.get("rerank_score")

        # Term overlap calculation
        query_tokens = _significant_tokens(query)
        result_tokens: set[str] = set()
        for snippet in snippets[:5]:  # Sample top 5
            title = snippet.get("title") or ""
            preview = snippet.get("preview") or snippet.get("summary") or ""
            content = snippet.get("content") or ""
            result_tokens.update(_significant_tokens(f"{title} {preview} {content}"))

        overlap = query_tokens & result_tokens if query_tokens else set()
        term_overlap_ratio = len(overlap) / len(query_tokens) if query_tokens else 0.0

        # Source diversity
        unique_sources = {
            snippet.get("upload_id") or snippet.get("document_id")
            for snippet in snippets
            if snippet.get("upload_id") or snippet.get("document_id")
        }
        source_diversity = len(unique_sources)

        # Table direct presence
        has_table_direct = any(
            snippet.get("search_stage") == "table_direct"
            or snippet.get("source") == "table_direct"
            for snippet in snippets
        )

        # Check if parallel_rrf was used
        used_parallel_rrf = diagnostics.get("path") == "parallel_rrf"

        return ConfidenceSignals(
            top_vector_distance=top_vector_distance,
            top_rerank_score=top_rerank_score,
            term_overlap_ratio=term_overlap_ratio,
            source_diversity=source_diversity,
            has_table_direct=has_table_direct,
            used_parallel_rrf=used_parallel_rrf,
            result_count=len(snippets),
        )

    def _compute_score(self, signals: ConfidenceSignals) -> tuple[float, list[str]]:
        """Compute weighted confidence score from signals."""

        score = 0.0
        reasons: list[str] = []

        # Vector distance score (inverted: lower distance = higher score)
        if signals.top_vector_distance is not None:
            if signals.top_vector_distance <= self.vector_distance_good:
                vector_score = 1.0
            elif signals.top_vector_distance >= self.vector_distance_bad:
                vector_score = 0.0
                reasons.append(f"high vector distance ({signals.top_vector_distance:.2f})")
            else:
                # Linear interpolation
                range_size = self.vector_distance_bad - self.vector_distance_good
                vector_score = 1.0 - (signals.top_vector_distance - self.vector_distance_good) / range_size
            score += self.WEIGHTS["vector_distance"] * vector_score
        else:
            # No vector distance info, neutral
            score += self.WEIGHTS["vector_distance"] * 0.5

        # Rerank score (direct use)
        if signals.top_rerank_score is not None:
            rerank_score = min(1.0, max(0.0, signals.top_rerank_score))
            if rerank_score < 0.4:
                reasons.append(f"low rerank score ({signals.top_rerank_score:.2f})")
            score += self.WEIGHTS["rerank_score"] * rerank_score
        else:
            score += self.WEIGHTS["rerank_score"] * 0.5

        # Term overlap score
        if signals.term_overlap_ratio >= self.min_term_overlap:
            overlap_score = min(1.0, signals.term_overlap_ratio / 0.6)  # Cap at 60% overlap
        else:
            overlap_score = signals.term_overlap_ratio / self.min_term_overlap
            if signals.term_overlap_ratio < 0.15:
                reasons.append(f"low term overlap ({signals.term_overlap_ratio:.0%})")
        score += self.WEIGHTS["term_overlap"] * overlap_score

        # Table direct bonus (high confidence signal)
        if signals.has_table_direct:
            score += self.WEIGHTS["table_direct"] * 1.0
        else:
            score += self.WEIGHTS["table_direct"] * 0.3  # Penalty for no exact match

        # Source diversity score
        # Single source can be suspicious (all results from wrong doc)
        # But can also be correct (focused query)
        if signals.source_diversity == 1 and signals.result_count > 3:
            diversity_score = 0.5  # Slightly suspicious
            reasons.append("all results from single document")
        elif signals.source_diversity >= 2:
            diversity_score = 0.8
        else:
            diversity_score = 0.6
        score += self.WEIGHTS["source_diversity"] * diversity_score

        # Result count score
        if signals.result_count == 0:
            count_score = 0.0
            reasons.append("no results found")
        elif signals.result_count < 3:
            count_score = 0.6
        else:
            count_score = 1.0
        score += self.WEIGHTS["result_count"] * count_score

        # Bonus for parallel_rrf path (indicates both vector and table search ran)
        if signals.used_parallel_rrf:
            score = min(1.0, score + 0.05)  # Small bonus

        return min(1.0, max(0.0, score)), reasons


@dataclass
class CritiqueResult:
    """
    Result of LLM critique.
    """
    verdict: str  # "match", "mismatch", "uncertain"
    confidence: float  # 0.0 - 1.0
    explanation: str
    suggested_refinement: str | None = None  # Refined query if mismatch

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": round(self.confidence, 2),
            "explanation": self.explanation,
            "suggested_refinement": self.suggested_refinement,
        }


class RetrievalCritique:
    """
    Lightweight LLM critique for low-confidence retrieval results.

    When retrieval confidence is low, asks the LLM to verify if results
    match query intent. If mismatch detected, suggests query refinement.

    This is a cheap operation (~100-200 tokens) that only runs on ~15-20%
    of queries where confidence is low.

    Example:
        critique = RetrievalCritique()
        result = critique.evaluate(
            query="Withdraw Bills for Collection fees",
            top_results=snippets[:3],
        )
        if result.verdict == "mismatch":
            # Re-search with result.suggested_refinement
    """

    CRITIQUE_PROMPT = '''You are evaluating if search results match a user's query intent.

Query: {query}

Top search results:
{results_summary}

Task: Determine if these results answer the query.

Respond in this exact format:
VERDICT: [match/mismatch/uncertain]
CONFIDENCE: [0.0-1.0]
EXPLANATION: [one sentence why]
REFINED_QUERY: [if mismatch, suggest better query; otherwise "none"]'''

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 150,
    ):
        """
        Initialize the critique module.

        Args:
            model: LLM model to use (defaults to settings or gpt-4o-mini)
            max_tokens: Max tokens for critique response
        """
        self.model = model or str(
            getattr(settings, "RAG_RETRIEVAL_CRITIQUE_MODEL", "gpt-4o-mini")
        )
        self.max_tokens = max_tokens

    def evaluate(
        self,
        query: str,
        top_results: Sequence[Mapping[str, object]],
    ) -> CritiqueResult:
        """
        Evaluate if search results match query intent.

        Args:
            query: The user's search query
            top_results: Top 3-5 search result snippets

        Returns:
            CritiqueResult with verdict and optional refinement
        """
        # Build results summary for prompt
        results_summary = self._build_results_summary(top_results)

        # Build prompt
        prompt = self.CRITIQUE_PROMPT.format(
            query=query,
            results_summary=results_summary,
        )

        try:
            # Call LLM
            response = self._call_llm(prompt)

            # Parse response
            return self._parse_response(response)

        except Exception as e:
            logger.warning(
                "retrieval_critique.failed query=%s error=%s",
                query[:50],
                str(e)[:100],
            )
            # On failure, assume match (don't block retrieval)
            return CritiqueResult(
                verdict="uncertain",
                confidence=0.5,
                explanation=f"Critique failed: {str(e)[:50]}",
            )

    def _build_results_summary(
        self,
        results: Sequence[Mapping[str, object]],
    ) -> str:
        """Build a concise summary of top results for the prompt."""
        summaries = []
        for i, result in enumerate(results[:5], start=1):
            title = result.get("title") or "Untitled"
            preview = result.get("preview") or result.get("summary") or ""
            # Truncate preview
            if len(preview) > 150:
                preview = preview[:150] + "..."
            summaries.append(f"{i}. {title}\n   {preview}")

        return "\n\n".join(summaries) if summaries else "(no results)"

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM for critique."""
        from apps.llm.llm_provider import get_provider

        provider = get_provider()
        response = provider.complete(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.0,  # Deterministic for critique
        )

        return response.get("content") or ""

    def _parse_response(self, response: str) -> CritiqueResult:
        """Parse the LLM response into a CritiqueResult."""
        lines = response.strip().split("\n")

        verdict = "uncertain"
        confidence = 0.5
        explanation = ""
        refined_query = None

        for line in lines:
            line = line.strip()
            if line.upper().startswith("VERDICT:"):
                verdict_text = line.split(":", 1)[1].strip().lower()
                if "match" in verdict_text and "mismatch" not in verdict_text:
                    verdict = "match"
                elif "mismatch" in verdict_text:
                    verdict = "mismatch"
                else:
                    verdict = "uncertain"
            elif line.upper().startswith("CONFIDENCE:"):
                try:
                    confidence = float(line.split(":", 1)[1].strip())
                    confidence = min(1.0, max(0.0, confidence))
                except ValueError:
                    confidence = 0.5
            elif line.upper().startswith("EXPLANATION:"):
                explanation = line.split(":", 1)[1].strip()
            elif line.upper().startswith("REFINED_QUERY:"):
                refined = line.split(":", 1)[1].strip()
                if refined.lower() != "none" and refined:
                    refined_query = refined

        return CritiqueResult(
            verdict=verdict,
            confidence=confidence,
            explanation=explanation,
            suggested_refinement=refined_query,
        )


def get_confidence_scorer() -> RetrievalConfidenceScorer:
    """
    Get a configured RetrievalConfidenceScorer instance.

    Reads configuration from Django settings:
    - RAG_RETRIEVAL_CRITIQUE_MIN_CONFIDENCE: Threshold for critique (default: 0.5)
    """
    try:
        min_confidence = float(
            getattr(settings, "RAG_RETRIEVAL_CRITIQUE_MIN_CONFIDENCE", 0.5)
        )
    except (TypeError, ValueError):
        min_confidence = 0.5

    return RetrievalConfidenceScorer(min_confidence=min_confidence)


def get_retrieval_critique() -> RetrievalCritique:
    """
    Get a configured RetrievalCritique instance.

    Reads configuration from Django settings:
    - RAG_RETRIEVAL_CRITIQUE_MODEL: Model to use (default: gpt-4o-mini)
    """
    model = str(getattr(settings, "RAG_RETRIEVAL_CRITIQUE_MODEL", "gpt-4o-mini"))

    return RetrievalCritique(model=model)
