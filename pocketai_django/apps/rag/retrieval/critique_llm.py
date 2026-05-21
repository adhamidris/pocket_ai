from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

from django.conf import settings

logger = logging.getLogger(__name__)


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
        from apps.llm import llm_provider as llm_provider_module

        PromptGenerationError = getattr(llm_provider_module, "PromptGenerationError", RuntimeError)
        provider = None
        for loader_name in ("load_mcp_provider", "get_provider", "load_provider"):
            loader = getattr(llm_provider_module, loader_name, None)
            if not callable(loader):
                continue
            try:
                candidate = loader()
            except TypeError:
                continue
            if candidate is not None:
                provider = candidate
                break

        if not provider:
            raise PromptGenerationError("Critique provider is not configured.")
        if not hasattr(provider, "chat"):
            raise PromptGenerationError("Critique provider does not support chat().")

        response = provider.chat(
            messages=[
                {"role": "system", "content": "Return only the requested critique fields."},
                {"role": "user", "content": prompt},
            ]
        )

        content = response.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping):
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        parts.append(text)
            return "".join(parts)

        # Compatibility fallback for providers that return OpenAI-like payloads.
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, Mapping):
                message = first.get("message")
                if isinstance(message, Mapping):
                    fallback = message.get("content")
                    if isinstance(fallback, str):
                        return fallback
        return ""

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
