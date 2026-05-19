from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.aliases import (
    ALIAS_KEYWORDS,
    ALIAS_MAX_LENGTH,
    ALIAS_MIN_LENGTH,
    ALIAS_SYMBOL_MIN_LENGTH,
    DATE_TOKEN_PATTERN,
    IDENTIFIER_TOKEN_PATTERN,
    ID_LINE_PATTERN,
)


class IngestionEntityAliasesMixin:

    @staticmethod
    def _looks_like_identifier(candidate: str) -> bool:
        if not candidate:
            return False
        token = candidate.strip()
        if not token:
            return False
        lowered = token.lower()
        if len(lowered) >= ALIAS_MIN_LENGTH:
            return True
        if len(lowered) >= ALIAS_SYMBOL_MIN_LENGTH and any(ch in "-_0123456789" for ch in lowered):
            return True
        return bool(IDENTIFIER_TOKEN_PATTERN.fullmatch(lowered))

    @staticmethod
    def _is_noisy_identifier(candidate: str) -> bool:
        if not candidate:
            return False
        token = candidate.strip()
        if not token:
            return False
        if DATE_TOKEN_PATTERN.search(token):
            return True
        if re.fullmatch(r"[\d\s\-]+", token):
            digits = re.sub(r"\D", "", token)
            if 13 <= len(digits) <= 19:
                return True
        return False

    @staticmethod
    def _normalize_alias_value(value: str) -> str:
        if not value:
            return ""
        normalized = re.sub(r"\s+", "-", value.strip().lower())
        normalized = re.sub(r"-{2,}", "-", normalized)
        normalized = normalized.strip("-")
        if len(normalized) > ALIAS_MAX_LENGTH:
            normalized = normalized[:ALIAS_MAX_LENGTH]
        return normalized

    @staticmethod
    def _collect_aliases_from_record(
        *,
        record: Mapping[str, Any],
        flattened: Mapping[str, str],
        attributes: Mapping[str, str],
        entity_name: str,
        alias_hygiene: bool = False,
    ) -> tuple[list[str], set[str]]:
        alias_candidates: list[str] = []
        alias_sources: set[str] = set()
        seen: set[str] = set()

        def maybe_add(value: Any, source: str) -> None:
            if not isinstance(value, str):
                return
            candidate = value.strip()
            if not candidate:
                return
            if len(candidate) > ALIAS_MAX_LENGTH:
                candidate = candidate[:ALIAS_MAX_LENGTH]
            if alias_hygiene and IngestionEntityAliasesMixin._is_noisy_identifier(candidate):
                return
            if not IngestionEntityAliasesMixin._looks_like_identifier(candidate):
                return
            lowered = candidate.lower()
            if lowered in seen:
                return
            seen.add(lowered)
            alias_candidates.append(candidate)
            alias_sources.add(source)

        maybe_add(entity_name, "entity_name")
        for key in ALIAS_KEYWORDS:
            maybe_add(record.get(key), f"record_{key}")
        for key, value in flattened.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"flattened_{key}")
        for key, value in attributes.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"attribute_{key}")
        # Include values that look like identifiers even if the key didn't match
        for value in flattened.values():
            if isinstance(value, str) and IngestionEntityAliasesMixin._looks_like_identifier(value):
                maybe_add(value, "inline_pattern")
        return alias_candidates[:8], alias_sources

    @staticmethod
    def _alias_metadata(aliases: Sequence[str]) -> dict[str, Any]:
        normalized: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            if not alias:
                continue
            trimmed = alias.strip()
            if not trimmed:
                continue
            if len(trimmed) > ALIAS_MAX_LENGTH:
                trimmed = trimmed[:ALIAS_MAX_LENGTH]
            lower = trimmed.lower()
            if lower in seen:
                continue
            seen.add(lower)
            normalized.append(trimmed)
        if not normalized:
            return {}
        alias_string = " ".join(sorted(seen))
        return {"aliases": normalized, "alias_string": alias_string}

    @staticmethod
    def _append_identifier_line(text: str, aliases: Sequence[str]) -> str:
        alias_list = [alias for alias in aliases if alias]
        if not alias_list:
            return text
        if "Identifiers:" in text:
            return text
        suffix = "Identifiers: " + ", ".join(alias_list[:6])
        return f"{text.rstrip()}\n{suffix}"

    def _extract_inline_identifiers(self, text: str, *, alias_hygiene: bool = False) -> list[str]:
        if not text:
            return []
        aliases: list[str] = []
        seen: set[str] = set()
        for match in IDENTIFIER_TOKEN_PATTERN.finditer(text.lower()):
            alias = match.group().strip()
            if not alias:
                continue
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            if alias_hygiene and self._is_noisy_identifier(alias):
                continue
            if not self._looks_like_identifier(alias):
                continue
            if alias not in seen:
                seen.add(alias)
                aliases.append(alias)
        for match in ID_LINE_PATTERN.finditer(text):
            alias = match.group(1).strip()
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            alias_lower = alias.lower()
            if alias_hygiene and self._is_noisy_identifier(alias):
                continue
            if alias_lower and alias_lower not in seen and self._looks_like_identifier(alias):
                seen.add(alias_lower)
                aliases.append(alias)
        return aliases[:6]

    def _inject_identifiers_into_text(
        self,
        text: str,
        *,
        alias_hygiene: bool = False,
    ) -> tuple[str, list[str]]:
        aliases = self._extract_inline_identifiers(text, alias_hygiene=alias_hygiene)
        if aliases:
            text = self._append_identifier_line(text, aliases)
        return text, aliases

    @staticmethod
    def _finalize_alias_metadata(metadata: dict[str, Any]) -> None:
        aliases = metadata.get("aliases")
        if not aliases:
            metadata.pop("alias_string", None)
            return
        normalized: list[str] = []
        seen: set[str] = set()
        for alias in aliases if isinstance(aliases, (list, tuple)) else [aliases]:
            if not alias:
                continue
            trimmed = str(alias).strip()
            if not trimmed:
                continue
            if len(trimmed) > ALIAS_MAX_LENGTH:
                trimmed = trimmed[:ALIAS_MAX_LENGTH]
            lower = trimmed.lower()
            if lower in seen:
                continue
            seen.add(lower)
            normalized.append(trimmed)
        metadata["aliases"] = normalized
        metadata["alias_string"] = " ".join(sorted(seen))
