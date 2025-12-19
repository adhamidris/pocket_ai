from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Sequence


try:
    from fastembed import TextEmbedding
except ImportError:
    TextEmbedding = None


logger = logging.getLogger(__name__)


class LocalEmbeddingError(RuntimeError):
    pass

@dataclass
class LocalEmbeddingService:
    """Local CPU embeddings via FastEmbed (multilingual by default)."""
    model: str | None = None

    def __post_init__(self):
        if TextEmbedding is None:
            raise EmbeddingProviderError("Install `fastembed` to enable local embeddings.")
        self.model = self.model or os.getenv(
            "EMBED_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        # FastEmbed downloads on first use; keep instance around.
        # Fall back to the legacy English model if the configured model cannot be loaded
        # (e.g., missing cache or unsupported fastembed build) so retrieval stays online.
        try:
            self._embedder = TextEmbedding(model_name=self.model)
        except Exception as exc:
            fallback_model = "BAAI/bge-small-en-v1.5"
            logger.warning("FastEmbed init failed model=%s; error=%s", self.model, exc)
            if self.model and self.model != fallback_model:
                try:
                    self._embedder = TextEmbedding(model_name=fallback_model)
                except Exception as exc2:
                    raise EmbeddingProviderError(f"FastEmbed init failed: {exc2}") from exc2
                else:
                    logger.warning("FastEmbed falling back to model=%s", fallback_model)
                    self.model = fallback_model
            else:
                raise EmbeddingProviderError(f"FastEmbed init failed: {exc}") from exc

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # FastEmbed returns an iterator of vectors
        return [list(map(float, vec)) for vec in self._embedder.embed(texts)]

    def embed_text(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []


def build_embedding_service(preferred_provider: str | None = None):
    provider = (preferred_provider or os.getenv("EMBED_PROVIDER") or "local").lower()
    if provider == "openai":
        try:
            return EmbeddingService()
        except EmbeddingProviderError as exc:
            logger.info("OpenAI embedding unavailable (%s); falling back to local", exc)
            if preferred_provider == "openai":
                raise
            try:
                return LocalEmbeddingService()
            except Exception as exc2:
                logger.info("Local embeddings unavailable: %s", exc2)
                return None
    elif provider == "local":
        try:
            return LocalEmbeddingService()
        except Exception as exc:
            logger.info("Local embeddings disabled: %s", exc)
            return None
    else:
        logger.warning("Unknown embedding provider '%s'; defaulting to local", provider)
        return build_embedding_service("local")


__all__ = [
    "EmbeddingService",
    "EmbeddingProviderError",
    "LocalEmbeddingService",
    "build_embedding_service",
    "warm_rag_embeddings",
]


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


def warm_rag_embeddings(*, log: logging.Logger | None = None) -> None:
    """Eagerly construct the knowledge service and warm its embedder."""

    logger_obj = log or logger
    try:
        from apps.mcp import tools as mcp_tools  # Imported lazily to avoid cycles
    except Exception as exc:  # pragma: no cover - defensive
        logger_obj.warning("RAG warmup skipped; MCP tools unavailable: %s", exc)
        return

    try:
        knowledge_service = mcp_tools._knowledge_service()
    except Exception as exc:  # pragma: no cover - defensive
        logger_obj.warning("RAG warmup skipped; knowledge service init failed: %s", exc)
        return

    embedder = getattr(knowledge_service, "embedding_service", None)
    if not embedder:
        logger_obj.info("RAG warmup skipped; no embedding service configured.")
        return

    try:
        embedder.embed_text("pocketai-warmup")
    except EmbeddingProviderError as exc:
        logger_obj.warning("Embedding warmup failed: %s", exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger_obj.warning("Unexpected error during embedding warmup: %s", exc)
