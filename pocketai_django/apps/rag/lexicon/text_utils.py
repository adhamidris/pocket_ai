"""Shared text-normalization utilities for the RAG pipeline.

Pure Python — no Django dependencies.
"""

from __future__ import annotations

PLURAL_BLACKLIST: frozenset[str] = frozenset({
    "this", "that", "thus", "is", "was", "does", "plus", "versus",
    "bus", "bonus", "campus", "corpus", "focus", "genus", "minus",
    "radius", "status", "virus",
})


def singularize(token: str) -> str:
    """Return the singular form of *token*, or *token* unchanged if not plural.

    Handles three patterns the naive ``token[:-1]`` rule misses:
    - ``"ies"`` → ``"y"``  (batteries → battery)
    - ``"sses"`` → ``"ss"`` (dresses → dress)
    - regular ``"s"`` removal with blacklist protection (plus stays plus)
    """
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("sses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        if token in PLURAL_BLACKLIST:
            return token
        return token[:-1]
    return token


def is_plural_candidate(token: str) -> bool:
    """Return ``True`` when *token* looks like a plural noun worth singularising."""
    return (
        len(token) > 3
        and token.endswith("s")
        and token not in PLURAL_BLACKLIST
        and not token.endswith("ss")
    )
