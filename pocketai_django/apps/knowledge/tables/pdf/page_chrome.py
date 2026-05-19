from __future__ import annotations

import re
from collections import Counter
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.ingestion.contracts import PageLayout


class IngestionPdfPageChromeMixin:

    def _normalize_page_chrome_token(self, token: str) -> str:
        cleaned = self._sanitize_text(token or "").strip().lower()
        if not cleaned:
            return ""
        cleaned = re.sub(r"[_\.\-]{2,}", " ", cleaned)
        cleaned = re.sub(r"\b[a-z]*\d+[a-z0-9/\-]*\b", "#", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def _page_chrome_tokens(self, text: str) -> list[str]:
        raw_tokens = re.findall(r"\S+", self._sanitize_text(text or ""))
        normalized: list[str] = []
        for token in raw_tokens:
            cleaned = self._normalize_page_chrome_token(token)
            if cleaned:
                normalized.append(cleaned)
        return normalized

    def _page_chrome_position(
        self,
        bbox: Mapping[str, Any] | None,
        *,
        page_height: float | None,
        page_region: str | None = None,
    ) -> str | None:
        normalized_region = str(page_region or "").strip().lower()
        if normalized_region in {"header", "footer"}:
            return normalized_region
        if not bbox or not page_height:
            return None
        y0 = float(bbox.get("y0") or 0.0)
        y1 = float(bbox.get("y1") or 0.0)
        if y1 <= page_height * self.pdf_page_chrome_top_ratio:
            return "header"
        if y0 >= page_height * (1.0 - self.pdf_page_chrome_bottom_ratio):
            return "footer"
        return None

    def _build_pdf_page_chrome_stats(self, pages: Sequence[PageLayout]) -> dict[str, Counter[str]]:
        stats: dict[str, Counter[str]] = {"header_prefix": Counter(), "footer_suffix": Counter()}
        if not self.pdf_page_chrome_suppression_enabled:
            return stats

        header_prefix_pages: dict[str, set[int]] = {}
        footer_suffix_pages: dict[str, set[int]] = {}
        min_tokens = 2
        max_tokens = max(min_tokens, self.pdf_page_chrome_max_words)

        for page in pages:
            for block in page.blocks or []:
                if block.block_type in {
                    KnowledgeBlockType.TABLE,
                    KnowledgeBlockType.IMAGE,
                    KnowledgeBlockType.FIGURE,
                    KnowledgeBlockType.OTHER,
                }:
                    continue
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block_meta.get("is_decorative"):
                    continue
                position = self._page_chrome_position(
                    block.bbox,
                    page_height=(page.height or None),
                    page_region=block_meta.get("page_region"),
                )
                if not position:
                    continue
                tokens = self._page_chrome_tokens(block.text)
                if len(tokens) < min_tokens:
                    continue
                upper = min(len(tokens), max_tokens)
                if position == "header":
                    for size in range(min_tokens, upper + 1):
                        sig = " ".join(tokens[:size]).strip()
                        if sig:
                            header_prefix_pages.setdefault(sig, set()).add(int(page.page_number))
                elif position == "footer":
                    for size in range(min_tokens, upper + 1):
                        sig = " ".join(tokens[-size:]).strip()
                        if sig:
                            footer_suffix_pages.setdefault(sig, set()).add(int(page.page_number))

        stats["header_prefix"] = Counter(
            {sig: len(page_numbers) for sig, page_numbers in header_prefix_pages.items()}
        )
        stats["footer_suffix"] = Counter(
            {sig: len(page_numbers) for sig, page_numbers in footer_suffix_pages.items()}
        )
        return stats

    @staticmethod
    def _trim_leading_token_count(text: str, token_count: int) -> str:
        if token_count <= 0:
            return text
        matches = list(re.finditer(r"\S+", text or ""))
        if token_count >= len(matches):
            return ""
        start = matches[token_count].start()
        return (text or "")[start:].lstrip()

    @staticmethod
    def _trim_trailing_token_count(text: str, token_count: int) -> str:
        if token_count <= 0:
            return text
        matches = list(re.finditer(r"\S+", text or ""))
        if token_count >= len(matches):
            return ""
        end = matches[-token_count - 1].end()
        return (text or "")[:end].rstrip()

    def _suppress_pdf_page_chrome(
        self,
        text: str,
        *,
        bbox: Mapping[str, Any] | None,
        page_height: float | None,
        page_region: str | None,
        chrome_stats: Mapping[str, Counter[str]] | None,
    ) -> tuple[str, dict[str, Any]]:
        diagnostics: dict[str, Any] = {}
        if not self.pdf_page_chrome_suppression_enabled or not chrome_stats:
            return text, diagnostics
        position = self._page_chrome_position(bbox, page_height=page_height, page_region=page_region)
        if position not in {"header", "footer"}:
            return text, diagnostics
        tokens = self._page_chrome_tokens(text)
        min_tokens = 2
        if len(tokens) < min_tokens:
            return text, diagnostics

        max_tokens = min(len(tokens), max(min_tokens, self.pdf_page_chrome_max_words))
        trimmed = text

        if position == "header":
            counter = chrome_stats.get("header_prefix") or Counter()
            best_size = 0
            best_sig = ""
            for size in range(max_tokens, min_tokens - 1, -1):
                sig = " ".join(tokens[:size]).strip()
                if sig and int(counter.get(sig, 0)) >= self.pdf_page_chrome_min_repeats:
                    best_size = size
                    best_sig = sig
                    break
            if best_size:
                trimmed = self._trim_leading_token_count(trimmed, best_size)
                diagnostics = {
                    "position": "header",
                    "trimmed_tokens": best_size,
                    "signature": best_sig[:120],
                }
        elif position == "footer":
            counter = chrome_stats.get("footer_suffix") or Counter()
            best_size = 0
            best_sig = ""
            for size in range(max_tokens, min_tokens - 1, -1):
                sig = " ".join(tokens[-size:]).strip()
                if sig and int(counter.get(sig, 0)) >= self.pdf_page_chrome_min_repeats:
                    best_size = size
                    best_sig = sig
                    break
            if best_size:
                trimmed = self._trim_trailing_token_count(trimmed, best_size)
                diagnostics = {
                    "position": "footer",
                    "trimmed_tokens": best_size,
                    "signature": best_sig[:120],
                }

        return trimmed.strip(), diagnostics
