from __future__ import annotations

import re
from typing import Optional

from apps.rag.lexicon.tenant import tokenize_lexicon_text


def _intent_is(intent: object, *values: str) -> bool:
    return str(getattr(intent, "value", intent)) in values


class QueryClassifierHintMixin:

    def _determine_scope(self, intent: QueryIntent, tokens: set, entity_names: list) -> str:
        """Determine the scope of the query."""
        if _intent_is(intent, "enumerate"):
            return "all"
        elif _intent_is(intent, "aggregate"):
            return "all"
        elif _intent_is(intent, "compare"):
            return "subset"
        elif _intent_is(intent, "specific_lookup"):
            return "specific"
        elif tokens & {'some', 'few', 'several'}:
            return "subset"
        else:
            return "unknown"

    def _generate_reasoning(self, intent: QueryIntent, query_lower: str, tokens: set) -> str:
        """Generate human-readable reasoning for the classification."""
        if _intent_is(intent, "enumerate"):
            if tokens & self.ENUMERATE_KEYWORDS:
                keywords = tokens & self.ENUMERATE_KEYWORDS
                return f"Enumeration keywords detected: {keywords}"
            return "Query pattern suggests listing/enumeration intent"

        elif _intent_is(intent, "compare"):
            return "Comparison pattern or multiple entities detected"

        elif _intent_is(intent, "aggregate"):
            return "Aggregation/calculation intent detected"

        elif _intent_is(intent, "specific_lookup"):
            return "Specific entity lookup pattern detected"

        return "Classification based on overall query analysis"

    def _generate_retrieval_hints(
        self,
        intent: QueryIntent,
        entity_type: Optional[str],
        entity_names: list[str],
        *,
        attributes: list[str],
        token_list: list[str],
        query_lower: str,
    ) -> dict:
        """Generate hints for the retrieval layer."""
        prefer_section_context = self._should_prefer_section_context(
            intent,
            token_list=token_list,
            query_lower=query_lower,
            entity_type=entity_type,
            entity_names=entity_names,
            attributes=attributes,
        )
        section_focus_terms = self._section_focus_terms(
            entity_type=entity_type,
            attributes=attributes,
            query_lower=query_lower,
        )
        hints = {
            'diversify_tables': _intent_is(intent, "enumerate", "exploratory"),
            'increase_snippet_limit': _intent_is(intent, "enumerate", "aggregate"),
            'filter_by_entity_names': bool(entity_names) and _intent_is(intent, "specific_lookup"),
            'entity_names_filter': entity_names if _intent_is(intent, "specific_lookup") else [],
            'prefer_table_headers': _intent_is(intent, "enumerate"),
            'include_all_tables': _intent_is(intent, "enumerate"),
            'prefer_section_context': prefer_section_context,
            'section_focus_terms': section_focus_terms if prefer_section_context else [],
            'modality_bias': 'mixed',
        }

        if _intent_is(intent, "enumerate"):
            hints['min_tables_coverage'] = 'all'
            hints['snippet_limit_multiplier'] = 3.0
        elif _intent_is(intent, "aggregate"):
            hints['min_tables_coverage'] = 'all'
            hints['snippet_limit_multiplier'] = 2.5
        elif _intent_is(intent, "compare"):
            hints['snippet_limit_multiplier'] = 1.5
        else:
            hints['snippet_limit_multiplier'] = 1.0

        return hints

    def _section_focus_terms(
        self,
        *,
        entity_type: Optional[str],
        attributes: list[str],
        query_lower: str,
    ) -> list[str]:
        terms: list[str] = []
        if entity_type:
            terms.append(entity_type)
        for attr in attributes:
            cleaned = str(attr or "").strip().lower()
            if not cleaned:
                continue
            terms.append(cleaned)
            attr_tokens = [token for token in cleaned.split() if token]
            if len(attr_tokens) == 1:
                singular = self._singularize(attr_tokens[0])
                if singular and singular != cleaned:
                    terms.append(singular)
        for pattern in self.SECTION_SEEKING_PATTERNS:
            match = re.search(pattern, query_lower)
            if match:
                terms.append(match.group(0).strip().lower())
        return self._dedupe_preserve(terms)[:8]

    def _should_prefer_section_context(
        self,
        intent: QueryIntent,
        *,
        token_list: list[str],
        query_lower: str,
        entity_type: Optional[str],
        entity_names: list[str],
        attributes: list[str],
    ) -> bool:
        token_set = set(token_list)
        coverage_terms = token_set & self.SECTION_COVERAGE_HEADWORDS
        plural_attribute_signal = False
        for attr in attributes:
            attr_tokens = list(tokenize_lexicon_text(attr, max_tokens=12))
            if any(self._looks_like_plural_noun(token) for token in attr_tokens):
                plural_attribute_signal = True
                break

        phrase_signal = any(re.search(pattern, query_lower) for pattern in self.SECTION_SEEKING_PATTERNS)
        temporal_location_signal = bool(
            {"where", "when"} & token_set
            and {"work", "employment", "experience", "history", "job", "jobs"} & token_set
        )
        coverage_question_signal = bool(
            coverage_terms
            and (
                plural_attribute_signal
                or bool(token_set & self.ALL_SCOPE_TOKENS)
                or bool(token_set & self.LIST_VERBS)
                or "else" in token_set
                or phrase_signal
                or temporal_location_signal
            )
        )

        if _intent_is(intent, "enumerate", "aggregate"):
            return bool(attributes or entity_type or coverage_terms or phrase_signal)
        if _intent_is(intent, "compare"):
            return bool(coverage_terms and len(entity_names) <= 1 and (plural_attribute_signal or phrase_signal))
        return bool(coverage_question_signal or phrase_signal or temporal_location_signal)
