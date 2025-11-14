from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

from django.test import SimpleTestCase


@dataclass(frozen=True)
class SyntheticChunk:
    chunk_id: str
    upload_id: str
    entity_name: str
    content: str
    is_ready: bool = True
    aliases: tuple[str, ...] = tuple()

    @property
    def normalized_text(self) -> str:
        return self.content.lower()

    @property
    def tokens(self) -> set[str]:
        return {token for token in self.normalized_text.replace("\n", " ").split() if token}

    def contains_any(self, phrases: Sequence[str]) -> bool:
        lowered = self.normalized_text
        return any(phrase.lower() in lowered for phrase in phrases)

    def alias_blob(self) -> str:
        return " ".join(alias.lower() for alias in self.aliases if alias).strip()

    def matches_alias(self, query: str, tokens: Sequence[str]) -> bool:
        blob = self.alias_blob()
        if not blob:
            return False
        query_terms = {query.lower().strip()}
        query_terms.update(token for token in tokens if token)
        query_terms.update(term.replace(" ", "-") for term in list(query_terms))
        return any(term and term in blob for term in query_terms)


@dataclass(frozen=True)
class RegressionTurn:
    query: str
    expected_primary: str
    expected_top_k: Sequence[str]
    expected_price_tokens: Sequence[str]
    expect_reads: int = 0


@dataclass(frozen=True)
class ScenarioResult:
    hit_at_1: float
    hit_at_3: float
    avg_reads: float
    median_time_to_ready_ms: float
    rerank_acceptance: float
    failures: Sequence[str]


class SyntheticKnowledgeIndex:
    def __init__(self, chunks: Sequence[SyntheticChunk]) -> None:
        self.chunks = list(chunks)

    def search(self, query: str, top_k: int = 3):
        query_tokens = self._normalize_query(query)
        scored: list[tuple[float, bool, SyntheticChunk]] = []
        for chunk in self.chunks:
            lexical = sum(1 for token in query_tokens if token in chunk.tokens)
            entity_bonus = 1.0 if chunk.entity_name.lower() in query.lower() else 0.0
            price_bonus = 0.3 if "price" in query_tokens and "price" in chunk.tokens else 0.0
            alias_hit = chunk.matches_alias(query, query_tokens)
            alias_bonus = 2.0 if alias_hit else 0.0
            score = lexical + entity_bonus + price_bonus + alias_bonus
            scored.append((score, alias_hit, chunk))
        scored.sort(key=lambda item: (item[0], item[1], item[2].chunk_id), reverse=True)
        candidates = [chunk for _, _, chunk in scored[: max(top_k, 5)]]

        reranker_choice = self._rerank(candidates, query_tokens, query)
        results = candidates[:top_k]
        return {
            "results": results,
            "reranker_choice": reranker_choice,
            "reranker_applied": len(candidates) > top_k,
        }

    @staticmethod
    def _normalize_query(query: str) -> tuple[str, ...]:
        tokens = query.lower().replace("-", " ").split()
        return tuple(token.strip() for token in tokens if token.strip())

    @staticmethod
    def _rerank(candidates: Sequence[SyntheticChunk], tokens: Sequence[str], query: str) -> str | None:
        if not candidates or not tokens:
            return None
        entity_tokens = {token for token in tokens if len(token) > 3}
        best_chunk = None
        best_score = -1.0
        for chunk in candidates:
            score = 0.0
            if chunk.entity_name.lower() in entity_tokens:
                score += 1.5
            overlap = len(chunk.tokens & set(tokens))
            score += overlap * 0.1
            if chunk.matches_alias(query, tokens):
                score += 2.0
            if score > best_score:
                best_score = score
                best_chunk = chunk
        return best_chunk.chunk_id if best_chunk else None


class RetrievalRegressionEvaluator:
    def __init__(self, index: SyntheticKnowledgeIndex, *, top_k: int = 3) -> None:
        self.index = index
        self.top_k = top_k

    def evaluate(self, *, turns: Sequence[RegressionTurn]) -> ScenarioResult:
        total = len(turns)
        hits_at_1 = 0
        hits_at_3 = 0
        reads: list[int] = []
        readiness_ms: list[float] = []
        reranker_accept = 0
        reranker_total = 0
        failures: list[str] = []

        for turn in turns:
            observation = self.index.search(turn.query, top_k=self.top_k)
            chunks = observation["results"]
            top_ids = [chunk.chunk_id for chunk in chunks]
            primary_hit = bool(top_ids and top_ids[0] == turn.expected_primary)
            if primary_hit:
                hits_at_1 += 1
            if turn.expected_primary in top_ids:
                hits_at_3 += 1
            if not set(turn.expected_top_k).issubset(set(top_ids)):
                failures.append(f"{turn.query}: missing expected top-k coverage {turn.expected_top_k}")

            chunk_lookup = {chunk.chunk_id: chunk for chunk in chunks}
            target_chunk = chunk_lookup.get(turn.expected_primary)
            if not target_chunk:
                failures.append(f"{turn.query}: expected chunk {turn.expected_primary} absent from ledger")
                continue
            if not target_chunk.contains_any(turn.expected_price_tokens):
                failures.append(f"{turn.query}: primary chunk missing expected price tokens {turn.expected_price_tokens}")

            read_needed = 0 if target_chunk.is_ready else 1
            reads.append(read_needed)
            readiness_ms.append(self._time_to_ready(read_needed))

            reranker_choice = observation["reranker_choice"]
            if observation["reranker_applied"]:
                reranker_total += 1
                if reranker_choice == top_ids[0]:
                    reranker_accept += 1

        return ScenarioResult(
            hit_at_1=hits_at_1 / total if total else 0.0,
            hit_at_3=hits_at_3 / total if total else 0.0,
            avg_reads=(sum(reads) / len(reads)) if reads else 0.0,
            median_time_to_ready_ms=statistics.median(readiness_ms) if readiness_ms else 0.0,
            rerank_acceptance=(reranker_accept / reranker_total) if reranker_total else 1.0,
            failures=tuple(failures),
        )

    @staticmethod
    def _time_to_ready(reads: int) -> float:
        base = 220.0
        return base + reads * 180.0


class RetrievalRegressionTest(SimpleTestCase):
    def test_siwa_luxor_regression(self) -> None:
        chunks = [
            SyntheticChunk(
                chunk_id="uploadA:chunk_siwa_price",
                upload_id="uploadA",
                entity_name="Siwa",
                content="Siwa Oasis escape • price USD 1450 per guest • includes 3 nights and desert transfers.",
                is_ready=True,
            ),
            SyntheticChunk(
                chunk_id="uploadA:chunk_siwa_addons",
                upload_id="uploadA",
                entity_name="Siwa",
                content="Siwa addons • optional spa package USD 300 • camel trek USD 120 per person.",
                is_ready=True,
            ),
            SyntheticChunk(
                chunk_id="uploadA:chunk_luxor_price",
                upload_id="uploadA",
                entity_name="Luxor",
                content="Luxor highlights • deluxe package USD 1600 • balloon ride USD 250 per guest.",
                is_ready=True,
            ),
            SyntheticChunk(
                chunk_id="uploadB:chunk_sokhna_slug",
                upload_id="uploadB",
                entity_name="Ain El Sokhna",
                content="Ain El Sokhna 3-day city tour and beach escape • includes marina visits • total price USD 540.",
                aliases=("cairo-to-el-ain-sokhna-3-day-city-tour-beach-escape",),
                is_ready=True,
            ),
        ]
        turns = [
            RegressionTurn(
                query="What is the Siwa price?",
                expected_primary="uploadA:chunk_siwa_price",
                expected_top_k=("uploadA:chunk_siwa_price", "uploadA:chunk_siwa_addons"),
                expected_price_tokens=("1450", "USD"),
            ),
            RegressionTurn(
                query="Tell me more about Siwa addons",
                expected_primary="uploadA:chunk_siwa_addons",
                expected_top_k=("uploadA:chunk_siwa_price", "uploadA:chunk_siwa_addons"),
                expected_price_tokens=("300", "Siwa"),
            ),
            RegressionTurn(
                query="cairo-to-el-ain-sokhna-3-day-city-tour-beach-escape",
                expected_primary="uploadB:chunk_sokhna_slug",
                expected_top_k=("uploadB:chunk_sokhna_slug", "uploadA:chunk_siwa_price"),
                expected_price_tokens=("540", "sokhna"),
            ),
        ]

        index = SyntheticKnowledgeIndex(chunks)
        evaluator = RetrievalRegressionEvaluator(index)
        result = evaluator.evaluate(turns=turns)

        self.assertGreaterEqual(result.hit_at_1, 1.0)
        self.assertGreaterEqual(result.hit_at_3, 1.0)
        self.assertLessEqual(result.avg_reads, 0.1)
        self.assertLessEqual(result.median_time_to_ready_ms, 250.0)
        self.assertGreaterEqual(result.rerank_acceptance, 0.8)
        self.assertFalse(result.failures, f"Unexpected retrieval regressions: {result.failures}")
