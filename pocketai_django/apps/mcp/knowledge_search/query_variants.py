from __future__ import annotations

from typing import Sequence


def _prune_queries(values: Sequence[str], *, query_variant_limit: int) -> list[str]:
    if len(values) <= query_variant_limit:
        return list(values)
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in values:
        normalized = entry.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(entry)
        if len(deduped) >= query_variant_limit:
            break
    if not deduped and values:
        deduped.append(values[0])
    return deduped

def _collect_queries(*, raw_query: object, raw_queries: object) -> list[str]:
    queries: list[str] = []
    seen_queries: set[str] = set()

    def _append_query(candidate: str) -> None:
        normalized = candidate.strip()
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen_queries:
            return
        seen_queries.add(lowered)
        queries.append(normalized)

    raw_query_text = _coerce_str(raw_query).strip()
    if raw_query_text:
        _append_query(raw_query_text)
    if isinstance(raw_queries, (list, tuple)):
        for candidate in raw_queries:
            candidate_str = _coerce_str(candidate).strip()
            if candidate_str:
                _append_query(candidate_str)

    return queries


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)
