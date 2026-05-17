from __future__ import annotations

from collections import OrderedDict
import dataclasses
import re
from typing import Mapping, Sequence

from apps.rag.contracts import KnowledgeSnippet


class EvidenceGroupingMixin:
    @staticmethod
    def _snippet_representation(snippet: KnowledgeSnippet) -> str:
        raw = str(snippet.representation or "").strip().lower()
        if raw in {"text", "table", "json"}:
            return raw
        if snippet.is_table_chunk:
            return "table"
        if snippet.entity_type:
            return "json"
        return "text"

    @staticmethod
    def _snippet_evidence_group_key(snippet: KnowledgeSnippet) -> str:
        group_id = str(snippet.evidence_group_id or "").strip()
        if group_id:
            return group_id
        if snippet.chunk_id:
            return f"chunk:{snippet.chunk_id}"
        return f"snippet:{snippet.id}"

    @staticmethod
    def _query_prefers_numeric(query_text: str, tokens: tuple[str, ...]) -> bool:
        if any(char.isdigit() for char in query_text or ""):
            return True
        lowered = (query_text or "").lower()
        if any(mark in lowered for mark in ("%", "fee", "fees", "rate", "rates", "price", "prices", "cost", "costs")):
            return True
        return any(token and any(ch.isdigit() for ch in token) for token in tokens or ())

    @staticmethod
    def _evidence_tokens(snippet: KnowledgeSnippet) -> set[str]:
        text = (snippet.content or snippet.summary or "").strip().lower()
        if not text:
            return set()
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", text)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return set()
        return {
            token
            for token in normalized.split(" ")
            if token and (len(token) >= 3 or token.isdigit())
        }

    @staticmethod
    def _evidence_overlap(tokens_a: set[str], tokens_b: set[str]) -> float:
        if not tokens_a or not tokens_b:
            return 0.0
        union = tokens_a | tokens_b
        if not union:
            return 0.0
        return len(tokens_a & tokens_b) / len(union)

    def _select_primary_evidence_snippet(
        self,
        entries: Sequence[tuple[int, KnowledgeSnippet]],
        *,
        query_text: str,
        tokens: tuple[str, ...],
    ) -> tuple[int, KnowledgeSnippet]:
        query_prefers_numeric = self._query_prefers_numeric(query_text, tokens)
        token_set = {
            str(token or "").strip().lower()
            for token in tokens
            if str(token or "").strip()
        }

        best_entry: tuple[int, KnowledgeSnippet] | None = None
        best_score = float("-inf")
        for original_index, snippet in entries:
            representation = self._snippet_representation(snippet)
            base_score = float(snippet.confidence_score or 0.0)
            bonus = 0.0

            if representation == "text":
                bonus += 0.06 if not query_prefers_numeric else 0.03
            elif representation == "table":
                bonus += 0.08 if query_prefers_numeric else -0.01
                diagnostics = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else {}
                try:
                    quality_score = diagnostics.get("table_quality_score")
                    if quality_score is not None:
                        quality_value = float(quality_score)
                        if quality_value < 0.35:
                            bonus -= 0.25
                        elif quality_value < 0.5:
                            bonus -= 0.12
                except (TypeError, ValueError):
                    pass
                if diagnostics.get("table_is_decorative"):
                    bonus -= 0.25
                if diagnostics.get("header_match") or diagnostics.get("specific_match"):
                    bonus += 0.05
            elif representation == "json":
                bonus -= 0.03

            if snippet.truncated:
                bonus -= 0.04

            if token_set:
                evidence_text = (snippet.content or snippet.summary or "").lower()
                matches = sum(1 for token in token_set if token in evidence_text)
                bonus += min(0.12, (matches / max(1, len(token_set))) * 0.12)

            total_score = base_score + bonus
            if total_score > best_score:
                best_score = total_score
                best_entry = (original_index, snippet)
            elif best_entry is not None and total_score == best_score and original_index < best_entry[0]:
                best_entry = (original_index, snippet)

        return best_entry or entries[0]

    def _collapse_snippets_by_evidence_group(
        self,
        snippets: Sequence[KnowledgeSnippet],
        *,
        query_text: str,
        tokens: tuple[str, ...],
        limit: int | None = None,
    ) -> tuple[tuple[KnowledgeSnippet, ...], dict[str, object]]:
        if not snippets:
            return tuple(), {"evidence_groups": 0, "evidence_collapsed": 0, "evidence_conflicts": 0}
        if not self.evidence_grouping_enabled:
            limited = tuple(snippets[:limit]) if isinstance(limit, int) and limit > 0 else tuple(snippets)
            return limited, {"evidence_groups": len(limited), "evidence_collapsed": 0, "evidence_conflicts": 0}

        grouped: OrderedDict[str, list[tuple[int, KnowledgeSnippet]]] = OrderedDict()
        for idx, snippet in enumerate(snippets):
            group_key = self._snippet_evidence_group_key(snippet)
            grouped.setdefault(group_key, []).append((idx, snippet))

        selected_entries: list[tuple[int, KnowledgeSnippet]] = []
        conflict_groups: list[str] = []
        collapsed_count = 0

        for group_key, entries in grouped.items():
            if len(entries) == 1:
                selected_entries.append(entries[0])
                continue

            collapsed_count += len(entries) - 1
            selected_idx, selected = self._select_primary_evidence_snippet(
                entries,
                query_text=query_text,
                tokens=tokens,
            )

            representation_buckets: dict[str, list[KnowledgeSnippet]] = {}
            for _, entry_snippet in entries:
                representation = self._snippet_representation(entry_snippet)
                representation_buckets.setdefault(representation, []).append(entry_snippet)

            has_conflict = False
            if len(representation_buckets) > 1:
                representations = list(representation_buckets.keys())
                for i, left in enumerate(representations):
                    for right in representations[i + 1 :]:
                        left_tokens = self._evidence_tokens(representation_buckets[left][0])
                        right_tokens = self._evidence_tokens(representation_buckets[right][0])
                        if min(len(left_tokens), len(right_tokens)) < 4:
                            continue
                        overlap = self._evidence_overlap(left_tokens, right_tokens)
                        if overlap < self.evidence_conflict_min_overlap:
                            has_conflict = True
                            break
                    if has_conflict:
                        break

            if has_conflict:
                conflict_groups.append(group_key)
                source_diag = dict(selected.source_diagnostics or {})
                source_diag["evidence_conflict"] = True
                source_diag["evidence_group_size"] = len(entries)
                source_diag["evidence_group_id"] = group_key
                selected = dataclasses.replace(selected, source_diagnostics=source_diag)

            selected_entries.append((selected_idx, selected))

        selected_entries.sort(key=lambda item: item[0])
        collapsed = [snippet for _, snippet in selected_entries]
        if isinstance(limit, int) and limit > 0:
            collapsed = collapsed[:limit]

        diagnostics: dict[str, object] = {
            "evidence_groups": len(grouped),
            "evidence_collapsed": collapsed_count,
            "evidence_conflicts": len(conflict_groups),
        }
        if conflict_groups:
            diagnostics["evidence_conflict_groups"] = conflict_groups[:8]
        return tuple(collapsed), diagnostics
