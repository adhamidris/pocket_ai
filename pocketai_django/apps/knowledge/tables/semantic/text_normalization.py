from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from apps.knowledge.ingestion.signals import _ARABIC_CHAR_RE, _ARABIC_DIACRITICS_RE


class IngestionTableTextNormalizationMixin:

    @staticmethod
    def _match_case(replacement: str, original: str) -> str:
        if not original:
            return replacement
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement.lower()

    def _compile_ocr_replacements(self, raw: Any) -> list[tuple[re.Pattern[str], str]]:
        defaults = (
            (r"\bfoos\b", "fees"),
            (r"\bfous\b", "fees"),
            (r"\bfroo\b", "free"),
            (r"\bfrog\b", "free"),
            (r"\bfino\b", "free"),
            (r"\bronowal\b", "renewal"),
            (r"\brenowal\b", "renewal"),
            (r"\bbhield\b", "shield"),
        )
        replacements: list[tuple[str, str]] = list(defaults)
        if isinstance(raw, str) and raw.strip():
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    replacements.extend((str(k), str(v)) for k, v in loaded.items())
                elif isinstance(loaded, list):
                    for item in loaded:
                        if isinstance(item, dict):
                            pattern = item.get("pattern")
                            replacement = item.get("replacement")
                            if pattern and replacement is not None:
                                replacements.append((str(pattern), str(replacement)))
            except json.JSONDecodeError:
                pass
        compiled: list[tuple[re.Pattern[str], str]] = []
        for pattern, replacement in replacements:
            try:
                compiled.append((re.compile(pattern, flags=re.IGNORECASE), replacement))
            except re.error:
                continue
        return compiled

    def _normalize_arabic_text(self, text: str) -> str:
        text = _ARABIC_DIACRITICS_RE.sub("", text)
        text = text.replace("ـ", "")
        text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
        text = text.replace("ى", "ي")
        return text

    def _normalize_currency_tokens(self, text: str) -> str:
        text = re.sub(r"\bE\s*G\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*G\s*F\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*6\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*B\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bB\s*G\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bEGF\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE6P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bEBP\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bBGP\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bL\.?\s*E\.?\b", "EGP", text, flags=re.IGNORECASE)
        return text

    def _normalize_currency_spacing(self, text: str) -> str:
        if not self.ocr_currency_spacing_enabled:
            return text
        return re.sub(r"\bEGP(?=\d)", "EGP ", text)

    def _normalize_percent_spacing(self, text: str) -> str:
        if not self.ocr_percent_space_fix_enabled:
            return text
        def _fix(match: re.Match[str]) -> str:
            whole = match.group(1)
            frac = match.group(2)
            return f"{whole}.{frac}%"
        text = re.sub(r"\b(\d)\s+(\d{1,2})\s*%", _fix, text)
        text = re.sub(r"\b(\d{1,3})\s*%", r"\1%", text)
        text = re.sub(r"%\s*%+", "%", text)
        return text

    def _normalize_percent_sanity(self, text: str) -> str:
        if not self.ocr_percent_fix_enabled:
            return text
        max_val = self.ocr_percent_sanity_max
        if max_val <= 0:
            return text
        def _fix(match: re.Match[str]) -> str:
            raw = match.group(1)
            try:
                value = float(raw)
            except ValueError:
                return match.group(0)
            if value <= max_val or value >= 1000:
                return match.group(0)
            fixed = value / 100.0
            rendered = f"{fixed:.2f}".rstrip("0").rstrip(".")
            return f"{rendered}%"
        return re.sub(r"\b(\d{2,3})\s*%", _fix, text)

    def _normalize_ocr_text(self, text: str) -> str:
        if not self.ocr_normalization_enabled:
            return text
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ")
        text = self._normalize_currency_tokens(text)
        text = self._normalize_currency_spacing(text)
        text = self._normalize_percent_spacing(text)
        for pattern, replacement in self.ocr_word_replacements:
            text = pattern.sub(lambda m: self._match_case(replacement, m.group(0)), text)
        if _ARABIC_CHAR_RE.search(text):
            text = self._normalize_arabic_text(text)
        text = self._normalize_percent_sanity(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _table_cell_text(self, value: Any) -> str:
        text = self._sanitize_text(value)
        text = text.replace("\t", " ").replace("|", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return self._normalize_ocr_text(text)
