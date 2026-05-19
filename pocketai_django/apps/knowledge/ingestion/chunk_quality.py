from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping


class IngestionChunkQualityMixin:

    @staticmethod
    def _tokenize_for_quality(text: str) -> list[str]:
        if not text:
            return []
        return re.findall(r"[a-z0-9]+", text.lower())

    @staticmethod
    def _is_heading_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        letters = [ch for ch in stripped if ch.isalpha()]
        if not letters:
            return False
        upper_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
        if upper_ratio >= 0.7:
            return True
        words = [word for word in re.split(r"\s+", stripped) if word]
        if not words:
            return False
        starts = [word[0] for word in words if word[0].isalpha()]
        if not starts:
            return False
        title_ratio = sum(1 for ch in starts if ch.isupper()) / len(starts)
        return title_ratio >= 0.8

    def _is_heading_only_chunk(self, text: str, token_count: int) -> bool:
        if token_count <= 0:
            return False
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        if len(lines) > self.chunk_quality_heading_max_lines:
            return False
        if token_count > self.chunk_quality_heading_max_tokens:
            return False
        return all(self._is_heading_line(line) for line in lines)

    def _chunk_quality_metrics(self, text: str) -> dict[str, Any]:
        tokens = self._tokenize_for_quality(text)
        token_count = len(tokens)
        unique_ratio = round(len(set(tokens)) / token_count, 3) if token_count else 0.0
        heading_only = self._is_heading_only_chunk(text, token_count)
        flags: list[str] = []
        if token_count < self.chunk_quality_min_tokens:
            flags.append("short_tokens")
        if unique_ratio < self.chunk_quality_min_unique_ratio:
            flags.append("low_unique_ratio")
        if heading_only:
            flags.append("heading_only")
        token_score = min(1.0, token_count / self.chunk_quality_min_tokens) if self.chunk_quality_min_tokens else 1.0
        unique_score = (
            min(1.0, unique_ratio / self.chunk_quality_min_unique_ratio)
            if self.chunk_quality_min_unique_ratio
            else 1.0
        )
        heading_score = 0.0 if heading_only else 1.0
        score = (token_score * 0.45) + (unique_score * 0.45) + (heading_score * 0.10)
        score = round(max(0.0, min(1.0, score)), 3)
        return {
            "chunk_quality_score": score,
            "chunk_quality_tokens": token_count,
            "chunk_quality_unique_ratio": unique_ratio,
            "chunk_heading_only": heading_only,
            "chunk_quality_flags": flags,
        }

    def _is_low_quality_text_chunk(self, metadata: Mapping[str, Any]) -> bool:
        if not metadata:
            return False
        try:
            token_count = int(metadata.get("chunk_quality_tokens") or 0)
        except (TypeError, ValueError):
            token_count = 0
        try:
            unique_ratio = float(metadata.get("chunk_quality_unique_ratio") or 0.0)
        except (TypeError, ValueError):
            unique_ratio = 0.0
        try:
            score = float(metadata.get("chunk_quality_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        heading_only = bool(metadata.get("chunk_heading_only"))
        if heading_only:
            return True
        if token_count < self.chunk_quality_min_tokens:
            return True
        if unique_ratio < self.chunk_quality_min_unique_ratio:
            return True
        return score < self.chunk_quality_low_score

    @staticmethod
    def _segment_is_text(metadata: Mapping[str, Any]) -> bool:
        if not metadata:
            return False
        if metadata.get("is_table_chunk"):
            return False
        index_type = metadata.get("index_type")
        if index_type in {"table", "entity"}:
            return False
        return True

    @staticmethod
    def _chunk_fingerprint(text: str) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _payload_is_table_residual(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("table_residual"):
            return True
        return (
            metadata.get("content_source") == "table_residual"
            or metadata.get("region_role") == "table_residual"
        )

    @staticmethod
    def _payload_is_table_annotation(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("table_annotation"):
            return True
        return (
            metadata.get("content_source") == "table_annotation"
            or metadata.get("region_role") == "table_annotation"
        )

    @staticmethod
    def _payload_is_table_row(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if not metadata.get("is_table_chunk"):
            return False
        return metadata.get("content_source") == "table_row"
