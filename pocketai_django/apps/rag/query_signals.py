from __future__ import annotations

from apps.rag.contracts import QueryTraits
from apps.rag.query_normalizer import QueryNormalizer


class QuerySignalMixin:
    def _identifier_like_tokens(self, traits: QueryTraits) -> list[str]:
        tokens: list[str] = []
        pattern = QueryNormalizer._IDENTIFIER_PATTERN
        for token in traits.tokens:
            if not token:
                continue
            if pattern.fullmatch(token):
                tokens.append(token)
                continue
            if any(ch.isdigit() for ch in token) and any(sym in token for sym in ("-", "_")):
                tokens.append(token)
        if traits.alias_candidates:
            for alias in traits.alias_candidates:
                if alias and pattern.fullmatch(alias):
                    tokens.append(alias)
        return tokens

    def _query_has_entity_tokens(self, business_profile, traits: QueryTraits) -> bool:
        tokens = list(traits.tokens or ())
        if not tokens:
            return False
        filler = self._filler_tokens_for_business(business_profile)
        meaningful = [token for token in tokens if token and token not in filler]
        if len([token for token in meaningful if len(token) > 3]) >= 2:
            return True
        if self._identifier_like_tokens(traits):
            return True
        return False
