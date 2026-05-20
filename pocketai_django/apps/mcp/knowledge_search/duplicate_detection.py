from __future__ import annotations

import hashlib
import math
from typing import Mapping, Sequence


def _normalize_intent_text(values: Sequence[str]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        parts.append(token)
    return " | ".join(parts)

def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float | None:
    if not a or not b:
        return None
    if len(a) != len(b):
        return None
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=False):
        try:
            xf = float(x)
            yf = float(y)
        except (TypeError, ValueError):
            return None
        dot += xf * yf
        norm_a += xf * xf
        norm_b += yf * yf
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

def _response_top_ids(response: Mapping[str, object], *, top_k: int) -> list[str]:
    ids: list[str] = []
    refs = response.get("refs")
    if isinstance(refs, list):
        for entry in refs:
            if not isinstance(entry, Mapping):
                continue
            ref_id = str(entry.get("evidence_group_id") or entry.get("id") or "").strip()
            if ref_id:
                ids.append(ref_id)
            if len(ids) >= top_k:
                break
        return ids[:top_k]

    snippets_local = response.get("snippets")
    if isinstance(snippets_local, list):
        for entry in snippets_local:
            if not isinstance(entry, Mapping):
                continue
            ref_id = str(
                entry.get("evidence_group_id")
                or entry.get("chunk_id")
                or entry.get("id")
                or ""
            ).strip()
            if ref_id:
                ids.append(ref_id)
            if len(ids) >= top_k:
                break
    return ids[:top_k]

def _response_result_fingerprint(response: Mapping[str, object], *, top_k: int) -> tuple[str, list[str]]:
    top_ids = _response_top_ids(response, top_k=top_k)
    if not top_ids:
        return "", []
    digest = hashlib.sha256("|".join(top_ids).encode("utf-8")).hexdigest()[:16]
    return digest, top_ids


def _duplicate_result_diagnostics(
    *,
    history: object,
    result_fingerprint: str,
    result_top_ids: Sequence[str],
    result_fingerprint_top_k: int,
) -> dict[str, object] | None:
    if not result_fingerprint or not isinstance(history, list) or not history:
        return None

    fingerprint_match: Mapping[str, object] | None = None
    for entry in reversed(history[-12:]):
        if not isinstance(entry, Mapping):
            continue
        prior_response = entry.get("response")
        if not isinstance(prior_response, Mapping):
            continue
        prior_fingerprint = str(entry.get("result_fingerprint") or "").strip()
        prior_top_ids_raw = entry.get("result_top_ids")
        if not prior_fingerprint:
            prior_fingerprint, prior_top_ids = _response_result_fingerprint(
                prior_response,
                top_k=result_fingerprint_top_k,
            )
            prior_top_ids_raw = prior_top_ids
        if prior_fingerprint != result_fingerprint:
            continue
        normalized_prior_top_ids = [
            str(value).strip()
            for value in (prior_top_ids_raw if isinstance(prior_top_ids_raw, list) else [])
            if str(value).strip()
        ][:result_fingerprint_top_k]
        if normalized_prior_top_ids and normalized_prior_top_ids != list(result_top_ids[:result_fingerprint_top_k]):
            continue
        fingerprint_match = entry
        break

    if fingerprint_match is None:
        return None
    return {
        "duplicate_result_fingerprint": result_fingerprint,
        "duplicate_result_top_ids": list(result_top_ids[:result_fingerprint_top_k]),
    }


def _duplicate_intent_diagnostics(
    *,
    queries: Sequence[str],
    new_contract_enabled: bool,
    search_history: object,
    embedder: object | None,
) -> tuple[str, list[float] | None, dict[str, object] | None]:
    intent_text = _normalize_intent_text(queries)
    intent_embedding: list[float] | None = None
    duplicate_intent_diagnostics: dict[str, object] | None = None
    if new_contract_enabled:
        if embedder and intent_text:
            try:
                embedded = embedder.embed_text(intent_text)  # type: ignore[attr-defined]
                if isinstance(embedded, list) and embedded:
                    intent_embedding = [float(v) for v in embedded]
            except Exception:
                intent_embedding = None

        history = search_history or []
        best_match: Mapping[str, object] | None = None
        best_similarity: float | None = None
        if intent_text and isinstance(history, list) and history:
            # Only compare against a small recent window to avoid unbounded work.
            for entry in reversed(history[-12:]):
                if not isinstance(entry, Mapping):
                    continue
                prior_response = entry.get("response")
                if not isinstance(prior_response, Mapping):
                    continue
                prior_intent = str(entry.get("intent") or entry.get("query") or "").strip().lower()
                if not prior_intent:
                    continue

                similarity: float | None = None
                if intent_embedding is not None and embedder:
                    prior_embedding = entry.get("embedding")
                    if not isinstance(prior_embedding, list) or not prior_embedding:
                        try:
                            embedded = embedder.embed_text(prior_intent)  # type: ignore[attr-defined]
                            if isinstance(embedded, list) and embedded:
                                prior_embedding = [float(v) for v in embedded]
                                # Cache embedding for future comparisons (not returned to the LLM).
                                try:
                                    entry["embedding"] = prior_embedding
                                except Exception:
                                    pass
                        except Exception:
                            prior_embedding = None
                    if isinstance(prior_embedding, list) and prior_embedding:
                        similarity = _cosine_similarity(intent_embedding, prior_embedding)

                if similarity is None:
                    similarity = 1.0 if prior_intent == intent_text else 0.0

                if best_similarity is None or similarity > best_similarity:
                    best_similarity = similarity
                    best_match = entry

            if best_match is not None and best_similarity is not None and best_similarity >= 0.85:
                duplicate_intent_diagnostics = {
                    "duplicate_intent_similarity": round(float(best_similarity), 4),
                    "duplicate_intent_query": str(best_match.get("intent") or best_match.get("query") or "").strip(),
                }

    return intent_text, intent_embedding, duplicate_intent_diagnostics
