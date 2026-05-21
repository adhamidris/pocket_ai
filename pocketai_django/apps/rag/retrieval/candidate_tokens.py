from __future__ import annotations

from typing import Sequence

from django.db.models import Q

from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.contracts import ChunkResult, QueryTraits
from apps.rag.query.normalizer import QueryNormalizer


class CandidateTokenMixin:

    @staticmethod
    def _lexical_threshold(traits: QueryTraits) -> float:
        if traits.token_count <= 3:
            return 0.25
        if traits.token_count <= 6:
            return 0.2
        return 0.15

    @staticmethod
    def _extract_query_tokens(query: str) -> tuple[str, ...]:
        if not query:
            return tuple()
        normalized = QueryNormalizer._normalize_query_text(query).lower()
        tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if len(token) >= 3]
        seen: dict[str, None] = {}
        for token in tokens:
            if token and token not in seen:
                seen[token] = None
        return tuple(seen.keys())

    def _prioritize_token_hits(
        self,
        candidates: Sequence[ChunkResult],
        tokens: tuple[str, ...],
        fallback: int,
    ) -> list[ChunkResult]:
        if not tokens:
            return list(candidates)
        matched: list[ChunkResult] = []
        remainder: list[ChunkResult] = []
        for candidate in candidates:
            cont = self._chunk_contains_tokens(candidate.chunk, tokens)
            if cont:
                matched.append(candidate)
            else:
                remainder.append(candidate)
        if not matched:
            return list(candidates)
        tail = remainder[:fallback] if fallback else []
        return matched + tail

    @staticmethod
    def _chunk_contains_tokens(chunk: KnowledgeUploadChunk, tokens: tuple[str, ...]) -> bool:
        if not tokens:
            return True
        text = (chunk.content or "").lower()
        if not text:
            return False
        if any(token in text for token in tokens):
            return True
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        alias_blob = str(metadata.get("alias_string") or "").lower()
        if alias_blob and any(token in alias_blob for token in tokens):
            return True
        return False

    def _build_fts_token_filter(self, tokens: tuple[str, ...], *, min_length: int) -> Q | None:
        if not tokens:
            return None
        significant = [token for token in tokens if len(token) >= min_length][:3]
        if not significant:
            return None
        clause = Q()
        for token in significant:
            clause |= Q(content__icontains=token) | Q(metadata__alias_string__icontains=token)
        return clause
