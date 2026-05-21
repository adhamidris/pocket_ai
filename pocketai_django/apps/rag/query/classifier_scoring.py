from __future__ import annotations

import re


class QueryClassifierScoringMixin:

    def _score_enumerate(self, query_lower: str, tokens: set) -> float:
        """Score likelihood of ENUMERATE intent."""
        score = 0.0

        # Check for enumeration patterns (strong signal)
        for pattern in self._enumerate_re:
            if pattern.search(query_lower):
                score += 0.5
                break

        # Check for enumeration keywords
        enum_tokens = tokens & self.ENUMERATE_KEYWORDS
        if enum_tokens:
            score += 0.3 * len(enum_tokens)

        # Check for list verbs + "all/every"
        if tokens & self.LIST_VERBS and tokens & self.ALL_SCOPE_TOKENS:
            score += 0.4

        # Plural-noun queries are often enumeration-style ("products", "services").
        if any(self._looks_like_plural_noun(word) for word in tokens):
            score += 0.1

        # "what are the" pattern
        if re.search(r'\bwhat\s+(are|is)\s+(the|all)\b', query_lower):
            score += 0.2

        return min(score, 1.0)

    def _score_compare(self, query_lower: str, tokens: set, entity_names: list) -> float:
        """Score likelihood of COMPARE intent."""
        score = 0.0

        # Check for comparison patterns
        for pattern in self._compare_re:
            if pattern.search(query_lower):
                score += 0.6
                break

        # Multiple entity names mentioned
        if len(entity_names) >= 2:
            score += 0.3

        # Comparison words
        if tokens & self.COMPARE_TOKENS:
            score += 0.2

        return min(score, 1.0)

    def _score_aggregate(self, query_lower: str, tokens: set) -> float:
        """Score likelihood of AGGREGATE intent."""
        score = 0.0

        # Check for aggregation patterns
        for pattern in self._aggregate_re:
            if pattern.search(query_lower):
                score += 0.5
                break

        # Aggregation keywords
        if tokens & self.AGGREGATE_TOKENS:
            score += 0.4

        # "how many" pattern
        if re.search(r'\bhow\s+many\b', query_lower):
            score += 0.3

        return min(score, 1.0)

    def _score_specific(self, query_lower: str, tokens: set, entity_names: list) -> float:
        """Score likelihood of SPECIFIC_LOOKUP intent."""
        score = 0.0

        # Specific entity name mentioned (single)
        if len(entity_names) == 1:
            score += 0.5

        # "the X card" pattern
        for pattern in self._specific_re:
            if pattern.search(query_lower):
                score += 0.3
                break

        # No enumeration keywords present
        if not (tokens & self.ENUMERATE_KEYWORDS):
            score += 0.2

        # Definite-article phrasing with an entity mention is typically specific.
        if entity_names and re.search(r"\bthe\s+\w+\b", query_lower):
            score += 0.2

        # Possessive phrasing ("X's details") usually targets a specific item.
        if re.search(r"'s\s+\w+", query_lower):
            score += 0.15

        return min(score, 1.0)
