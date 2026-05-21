from __future__ import annotations

import re
from typing import Optional

from apps.rag.lexicon.tenant import normalize_lexicon_text, tokenize_lexicon_text
from apps.rag.lexicon.text_utils import is_plural_candidate, singularize


class QueryClassifierEntityMixin:

    @staticmethod
    def _singularize(token: str) -> str:
        return singularize(token)

    @classmethod
    def _looks_like_plural_noun(cls, token: str) -> bool:
        return is_plural_candidate(token)

    @staticmethod
    def _dedupe_preserve(values: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    @staticmethod
    def _tokenize_entity_name(name: str) -> list[str]:
        return list(tokenize_lexicon_text(name, max_tokens=32))

    def _collect_entity_phrase(self, tokens: list[str], start_idx: int) -> list[str]:
        phrase: list[str] = []
        for token in tokens[start_idx:]:
            if token in self.ENTITY_BOUNDARY_TOKENS:
                break
            if len(token) < 2:
                continue
            phrase.append(token)
            if len(phrase) >= 3:
                break
        while phrase and phrase[0] in self.ENTITY_NOISE_PREFIX_TOKENS:
            phrase.pop(0)
        if phrase:
            phrase[-1] = self._singularize(phrase[-1])
        return phrase

    def _extract_entity_type(
        self,
        token_list: list[str],
        entity_names: list[str],
        *,
        tenant_entity_terms: tuple[str, ...] = tuple(),
        query_lower: str = "",
    ) -> Optional[str]:
        """Extract a generic entity type phrase without domain-specific dictionaries."""
        if not token_list:
            return None

        entity_name_tokens: set[str] = set()
        for name in entity_names:
            entity_name_tokens.update(self._tokenize_entity_name(name))

        candidates: list[list[str]] = []

        for idx, token in enumerate(token_list):
            if token in self.ENTITY_CAPTURE_ANCHORS:
                phrase = self._collect_entity_phrase(token_list, idx + 1)
                if phrase:
                    candidates.append(phrase)

        # Known-entity anchored candidate: first descriptor token after the entity name.
        if entity_names:
            for name in entity_names:
                name_tokens = self._tokenize_entity_name(name)
                if not name_tokens:
                    continue
                span = len(name_tokens)
                for idx in range(0, len(token_list) - span + 1):
                    if token_list[idx:idx + span] == name_tokens:
                        phrase = self._collect_entity_phrase(token_list, idx + span)
                        if phrase:
                            candidates.append(phrase[:1])

        normalized: list[str] = []
        for parts in candidates:
            filtered = [part for part in parts if part not in entity_name_tokens and part not in self.CONTROL_TOKENS]
            if not filtered:
                continue
            normalized.append(" ".join(filtered))

        if not normalized:
            if tenant_entity_terms and query_lower:
                for term in tenant_entity_terms:
                    if self._phrase_in_query(term, query_lower):
                        return term
            return None

        ordered = self._dedupe_preserve(normalized)
        # Prefer shortest candidate to avoid leaking attribute terms into entity type.
        ordered.sort(key=lambda value: (len(value.split()), len(value)))
        return ordered[0]

    def _extract_attributes(
        self,
        token_list: list[str],
        entity_type: Optional[str],
        entity_names: list[str],
        *,
        tenant_attribute_terms: tuple[str, ...] = tuple(),
        query_lower: str = "",
    ) -> list[str]:
        """Extract attribute-like tokens using query structure (domain agnostic)."""
        entity_tokens: set[str] = set()
        if entity_type:
            entity_tokens.update(re.findall(r"\b\w+\b", entity_type.lower()))
        for name in entity_names:
            entity_tokens.update(self._tokenize_entity_name(name))

        attributes: list[str] = []
        for token in token_list:
            if len(token) < 3:
                continue
            if token in self.CONTROL_TOKENS:
                continue
            if token in entity_tokens:
                continue
            attributes.append(token)

        if tenant_attribute_terms and query_lower:
            for phrase in tenant_attribute_terms:
                if phrase in entity_tokens:
                    continue
                if self._phrase_in_query(phrase, query_lower):
                    attributes.insert(0, phrase)

        return self._dedupe_preserve(attributes)

    def _extract_entity_names(self, query: str, *, tenant_entity_terms: tuple[str, ...] = tuple()) -> list[str]:
        """Extract specific entity names from the query."""
        names = []
        query_lower = normalize_lexicon_text(query)

        # Check against known entity names
        for name in self.known_entity_names:
            if self._phrase_in_query(name, query_lower):
                names.append(name)

        if tenant_entity_terms:
            for term in tenant_entity_terms:
                if self._phrase_in_query(term, query_lower):
                    names.append(term)

        # Check for capitalized words that might be entity names
        # (excluding common words and query terms)
        common_words = {'the', 'a', 'an', 'all', 'every', 'what', 'which', 'how',
                       'is', 'are', 'do', 'does', 'can', 'could', 'would', 'should',
                       'list', 'show', 'give', 'tell', 'me', 'please', 'and', 'or',
                       'for', 'with', 'from', 'to', 'in', 'on', 'at', 'by'}

        # Find capitalized words in original query
        capitalized = re.findall(r'\b[A-Z][a-z]+\b', query)
        for word in capitalized:
            if word.lower() not in common_words and word not in names:
                names.append(word)

        return names
