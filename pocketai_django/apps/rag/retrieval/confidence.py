from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

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
