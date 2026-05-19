from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any, Mapping, Sequence

from apps.knowledge.models import KnowledgeUpload


class IngestionEntityEvidenceMixin:

    @staticmethod
    def _representation_from_metadata(metadata: Mapping[str, Any]) -> str:
        if not metadata:
            return "text"
        if metadata.get("is_table_chunk"):
            return "table"
        index_type = str(metadata.get("index_type") or "").strip().lower()
        if index_type == "entity":
            return "json"
        if index_type == "table":
            return "table"
        return "text"

    @staticmethod
    def _normalize_evidence_phrase(value: str, *, max_tokens: int = 24) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        normalized = normalized.lower()
        normalized = re.sub(r"[_/\-]+", " ", normalized)
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return ""
        tokens = normalized.split(" ")
        if len(tokens) > max_tokens:
            tokens = tokens[:max_tokens]
        return " ".join(tokens)

    @staticmethod
    def _evidence_group_id_for_key(upload_id: uuid.UUID, key: str) -> str:
        namespace = uuid.UUID(str(upload_id))
        stable_key = key or "empty"
        return str(uuid.uuid5(namespace, stable_key))

    def _payload_primary_evidence_key(
        self,
        *,
        payload_text: str,
        metadata: Mapping[str, Any],
    ) -> str:
        representation = self._representation_from_metadata(metadata)
        table_id = str(metadata.get("table_id") or "").strip()
        table_role = str(metadata.get("table_chunk_role") or "").strip().lower()

        if representation == "table":
            row_index = metadata.get("table_row_index")
            if table_id and row_index is not None:
                return f"table_row:{table_id}:{row_index}"
            row_label = self._normalize_evidence_phrase(str(metadata.get("row_label") or ""))
            if table_id and row_label:
                return f"table_row_label:{table_id}:{row_label}"
            if row_label:
                return f"table_row_label:{row_label}"
            if table_id and table_role:
                return f"table:{table_id}:{table_role}"
            if table_id:
                return f"table:{table_id}"

        if representation == "json":
            entity_name = self._normalize_evidence_phrase(str(metadata.get("entity_name") or ""))
            if entity_name:
                return f"entity:{entity_name}"
            entity_index = metadata.get("entity_index")
            if entity_index is not None:
                return f"entity_index:{entity_index}"

        page_anchor = str(metadata.get("page_anchor") or "").strip()
        anchors = metadata.get("block_anchors")
        if isinstance(anchors, list) and len(anchors) == 1 and anchors[0]:
            anchor_value = str(anchors[0]).strip()
            if anchor_value:
                return f"text_anchor:{anchor_value}"
        if page_anchor:
            normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=40)
            if normalized_text:
                return f"text_page:{page_anchor}:{normalized_text[:180]}"
            return f"text_page:{page_anchor}"

        normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=48)
        if normalized_text:
            return f"text:{normalized_text[:220]}"
        return "text:empty"

    def _assign_evidence_group_metadata(
        self,
        *,
        upload: KnowledgeUpload,
        segment_payloads: Sequence[dict[str, Any]],
    ) -> None:
        if not segment_payloads:
            return

        table_label_groups: dict[str, set[str]] = {}
        prepared: list[tuple[dict[str, Any], dict[str, Any], str, str, str]] = []

        for payload in segment_payloads:
            if not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                payload["metadata"] = metadata
            payload_text = str(payload.get("text") or "")
            representation = self._representation_from_metadata(metadata)
            evidence_type = str(
                metadata.get("content_source") or metadata.get("index_type") or representation
            ).strip().lower() or representation
            metadata["representation"] = representation
            metadata["evidence_type"] = evidence_type
            evidence_key = self._payload_primary_evidence_key(payload_text=payload_text, metadata=metadata)
            prepared.append((payload, metadata, representation, evidence_key, payload_text))

            if representation != "table":
                continue
            evidence_group_id = self._evidence_group_id_for_key(upload.id, evidence_key)
            metadata["evidence_group_id"] = evidence_group_id
            metadata["evidence_key"] = evidence_key
            row_label = self._normalize_evidence_phrase(str(metadata.get("row_label") or ""))
            if row_label:
                table_label_groups.setdefault(row_label, set()).add(evidence_group_id)

        if not prepared:
            return

        unambiguous_table_label_groups = {
            label: next(iter(group_ids))
            for label, group_ids in table_label_groups.items()
            if len(group_ids) == 1
        }
        sorted_table_labels = sorted(unambiguous_table_label_groups.keys(), key=len, reverse=True)

        for _, metadata, representation, evidence_key, payload_text in prepared:
            if representation == "table":
                continue

            linked_group_id = ""
            if representation == "text":
                line_count = len([line for line in payload_text.splitlines() if line.strip()])
                if not line_count:
                    line_count = 1 if payload_text.strip() else 0
                if (
                    payload_text
                    and len(payload_text) <= self.evidence_text_link_max_chars
                    and line_count <= self.evidence_text_link_max_lines
                    and sorted_table_labels
                ):
                    normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=120)
                    if normalized_text:
                        haystack = f" {normalized_text} "
                        for label in sorted_table_labels:
                            if len(label) < 6:
                                continue
                            if label not in unambiguous_table_label_groups:
                                continue
                            if f" {label} " in haystack:
                                linked_group_id = unambiguous_table_label_groups[label]
                                metadata["evidence_linked_label"] = label
                                evidence_key = f"linked_table_label:{label}"
                                break

            metadata["evidence_key"] = evidence_key
            metadata["evidence_group_id"] = linked_group_id or self._evidence_group_id_for_key(upload.id, evidence_key)
