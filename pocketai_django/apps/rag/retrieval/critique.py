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

from django.conf import settings

from apps.rag.retrieval.confidence import (
    ConfidenceResult,
    ConfidenceSignals,
    RetrievalConfidenceScorer,
)
from apps.rag.retrieval.critique_llm import CritiqueResult, RetrievalCritique


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
