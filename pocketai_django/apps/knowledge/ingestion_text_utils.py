from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class IngestionTextUtilsMixin:


    @staticmethod
    def _extract_text_file(path: Path) -> str:
        encodings = ("utf-8", "utf-16", "latin-1")
        for encoding in encodings:
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        # fallback to binary decode ignoring errors
        return path.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _normalize_text(raw: str) -> str:
        text = raw.replace("\x00", " ").replace("\r", "\n")
        # Collapse runs of spaces only; keep tabs intact for TSV
        text = re.sub(r"[ ]{2,}", " ", text)
        # Do NOT touch \t
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _sanitize_text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        if "\x00" in text:
            return text.replace("\x00", " ")
        return text

    @staticmethod
    def _clamp_text(value: Any, max_length: int) -> str:
        text = IngestionTextUtilsMixin._sanitize_text(value)
        if max_length <= 0:
            return text
        if len(text) > max_length:
            return text[:max_length]
        return text

    @staticmethod
    def _clamp_model_field_text(model_cls: Any, field_name: str, value: Any) -> str:
        max_length = getattr(model_cls._meta.get_field(field_name), "max_length", 0) or 0
        return IngestionTextUtilsMixin._clamp_text(value, max_length)

    @staticmethod
    def _build_summary(content: str, limit: int = 500) -> str:
        paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
        if not paragraphs:
            return content[:limit]
        summary = " ".join(paragraphs[:3])
        if len(summary) > limit:
            summary = summary[: limit - 1].rstrip() + "…"
        return summary
