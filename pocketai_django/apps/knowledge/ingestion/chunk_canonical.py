from __future__ import annotations

import re
import uuid
from typing import Any, Mapping, Sequence


class IngestionChunkCanonicalMixin:

    @staticmethod
    def _canonical_anchor_token(value: Any, *, max_length: int = 120) -> str:
        token = re.sub(r"[^a-z0-9:_\-]+", "-", str(value or "").strip().lower())
        token = token.strip("-")
        if not token:
            return ""
        return token[:max_length]

    @staticmethod
    def _canonical_chunk_kind(metadata: Mapping[str, Any]) -> str:
        if not isinstance(metadata, Mapping):
            return "narrative_paragraph"
        if metadata.get("is_dataset_card"):
            return "dataset_card"
        content_source = str(metadata.get("content_source") or "").strip().lower()
        index_type = str(metadata.get("index_type") or "").strip().lower()
        if index_type == "entity":
            return "entity_record"
        if content_source == "table_row" or metadata.get("table_chunk_role") == "row":
            return "table_row"
        if content_source == "table_summary" or metadata.get("table_chunk_role") == "summary":
            return "table_summary"
        if content_source == "table_annotation" or metadata.get("table_annotation"):
            return "table_annotation"
        if metadata.get("is_table_chunk") or index_type == "table":
            return "table_chunk"
        return "narrative_paragraph"

    @staticmethod
    def _coverage_reason_for_chunk(metadata: Mapping[str, Any], *, kind: str) -> str:
        content_source = str(metadata.get("content_source") or "").strip().lower()
        if kind == "table_row":
            return "canonical_table_row"
        if kind == "table_summary":
            return "canonical_table_summary"
        if kind == "table_annotation":
            return "anchored_table_annotation"
        if kind == "entity_record":
            return "entity_record_projection"
        if kind == "dataset_card":
            return "dataset_card_summary"
        if content_source == "page_blocks":
            return "layout_paragraph"
        if content_source == "flat_text":
            return "flat_text_fallback"
        if content_source:
            return content_source
        return "narrative_paragraph"

    def _canonical_anchor_id_for_payload(
        self,
        *,
        upload_id: uuid.UUID,
        text: str,
        metadata: Mapping[str, Any],
        kind: str,
    ) -> str:
        fingerprint = self._chunk_fingerprint(text)[:16] or "empty"
        upload_token = self._canonical_anchor_token(upload_id) or "upload"
        table_token = self._canonical_anchor_token(metadata.get("table_id")) or "table"
        if kind == "table_row":
            try:
                row_index = int(metadata.get("table_row_index"))
            except (TypeError, ValueError):
                row_index = None
            row_token = str(row_index) if isinstance(row_index, int) and row_index >= 0 else fingerprint
            return f"table:{table_token}:row:{row_token}"
        if kind == "table_summary":
            return f"table:{table_token}:summary"
        if kind == "table_annotation":
            return f"table:{table_token}:annotation:{fingerprint}"
        if kind == "entity_record":
            entity_name = self._canonical_anchor_token(metadata.get("entity_name"), max_length=48) or "record"
            return f"upload:{upload_token}:entity:{entity_name}:{fingerprint}"
        if kind == "dataset_card":
            return f"upload:{upload_token}:dataset-card"
        page_number = self._segment_page_number(metadata)
        page_token = str(page_number) if isinstance(page_number, int) and page_number > 0 else "na"
        block_anchor = ""
        raw_anchors = metadata.get("block_anchors")
        if isinstance(raw_anchors, Sequence) and not isinstance(raw_anchors, (str, bytes)):
            for entry in raw_anchors:
                normalized = self._canonical_anchor_token(entry, max_length=48)
                if normalized:
                    block_anchor = normalized
                    break
        if block_anchor:
            return f"page:{page_token}:paragraph:{block_anchor}"
        return f"upload:{upload_token}:paragraph:{fingerprint}"

    def _apply_canonical_chunk_metadata(
        self,
        *,
        upload_id: uuid.UUID,
        text: str,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        canonical_meta = dict(metadata or {})
        kind = self._canonical_chunk_kind(canonical_meta)
        anchor_id = self._canonical_anchor_id_for_payload(
            upload_id=upload_id,
            text=text,
            metadata=canonical_meta,
            kind=kind,
        )
        canonical_meta["canonical_schema_version"] = self.canonical_chunk_schema_version
        canonical_meta["canonical_source_layer"] = "canonical"
        canonical_meta["canonical_chunk_kind"] = kind
        canonical_meta["canonical_anchor_id"] = anchor_id
        table_token = self._canonical_anchor_token(canonical_meta.get("table_id")) or ""
        if kind in {"table_row", "table_annotation"} and table_token:
            canonical_meta["canonical_parent_anchor_id"] = f"table:{table_token}:summary"
        elif kind == "table_summary":
            canonical_meta.pop("canonical_parent_anchor_id", None)
        canonical_meta["coverage_reason"] = self._coverage_reason_for_chunk(canonical_meta, kind=kind)
        return canonical_meta
