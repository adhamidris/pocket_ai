from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Sequence


logger = logging.getLogger(__name__)


class EmbeddingProviderError(RuntimeError):
    """Raised when embeddings cannot be generated."""


def _load_openai_client(api_key: str, base_url: str | None):
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise EmbeddingProviderError("Install the `openai` package to enable embeddings.") from exc
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


@dataclass
class EmbeddingService:
    """
    Thin wrapper around OpenAI embeddings with graceful fallbacks.

    When the provider is unavailable (no API key, import error, request failure),
    callers should catch EmbeddingProviderError and continue without vectors.
    """

    api_key: str | None = None
    model: str = "text-embedding-3-small"
    base_url: str | None = None
    timeout: float = 30.0

    def __post_init__(self) -> None:
        key = self.api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise EmbeddingProviderError("OPENAI_API_KEY is not configured.")
        self.api_key = key
        self.model = self.model or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self.base_url = self.base_url or os.getenv("OPENAI_BASE_URL")
        self._client = _load_openai_client(self.api_key, self.base_url)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(
                model=self.model,
                input=list(texts),
                timeout=self.timeout,
            )
        except Exception as exc:  # pragma: no cover - network
            raise EmbeddingProviderError(f"Embedding request failed: {exc}") from exc
        vectors: list[list[float]] = []
        for record in getattr(response, "data", []):
            embedding = getattr(record, "embedding", None)
            if isinstance(embedding, list):
                vectors.append([float(value) for value in embedding])
        return vectors

    def embed_text(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []


def build_embedding_service() -> EmbeddingService | None:
    """
    Construct an embedding service if credentials are available, otherwise return None.
    """

    try:
        return EmbeddingService()
    except EmbeddingProviderError as exc:
        logger.info("Embedding service disabled: %s", exc)
        return None


__all__ = ["EmbeddingService", "EmbeddingProviderError", "build_embedding_service"]
