from __future__ import annotations

from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.chunk_residual_reconciliation import IngestionChunkResidualReconciliationMixin
from apps.knowledge.models import KnowledgeUpload


class IngestionChunkResidualsMixin(IngestionChunkResidualReconciliationMixin):

    def _project_table_residual_annotations(
        self,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            metadata = payload.get("metadata")
            payloads.append(
                {
                    "text": text,
                    "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
                }
            )
        stats: dict[str, Any] = {
            "enabled": bool(self.table_annotation_enabled),
            "input_payloads": len(payloads),
            "input_residual_segments": 0,
            "residual_segments_projected": 0,
            "unanchored_residual_promoted": 0,
            "narrative_promoted_segments": 0,
            "table_annotation_chunks_created": 0,
            "tables_with_annotations": 0,
            "soft_limit_exceeded_tables": 0,
        }
        if not payloads or not self.table_annotation_enabled:
            stats["output_payloads"] = len(payloads)
            return payloads, stats

        table_context_by_id: dict[str, dict[str, Any]] = {}
        table_candidates_by_page: dict[int, list[tuple[int, int, str]]] = {}
        for payload in payloads:
            metadata = payload.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            if self._payload_is_table_residual(metadata):
                continue
            if self._payload_is_table_annotation(metadata):
                continue
            table_id = str(metadata.get("table_id") or "").strip()
            if not table_id:
                continue
            content_source = str(metadata.get("content_source") or "").strip().lower()
            table_context_by_id.setdefault(
                table_id,
                {
                    "table_id": table_id,
                    "table_title": str(metadata.get("table_title") or "").strip(),
                    "table_order_index": metadata.get("table_order_index"),
                    "table_page_number": self._segment_page_number(metadata),
                },
            )
            page_number = self._segment_page_number(metadata)
            if not page_number:
                continue
            try:
                order_index = int(metadata.get("table_order_index"))
            except (TypeError, ValueError):
                order_index = 10_000
            priority = 0 if content_source == "table_summary" else 1
            table_candidates_by_page.setdefault(page_number, []).append((priority, order_index, table_id))

        preferred_table_by_page: dict[int, str] = {}
        for page_number, candidates in table_candidates_by_page.items():
            if not candidates:
                continue
            best = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
            preferred_table_by_page[page_number] = best[2]

        passthrough: list[dict[str, Any]] = []
        residual_groups: dict[str, list[dict[str, Any]]] = {}
        for payload in payloads:
            metadata = payload.get("metadata")
            if not isinstance(metadata, Mapping) or not self._payload_is_table_residual(metadata):
                passthrough.append(payload)
                continue
            stats["input_residual_segments"] = int(stats["input_residual_segments"]) + 1
            table_id = str(metadata.get("table_id") or "").strip()
            if not table_id:
                page_number = self._segment_page_number(metadata)
                if page_number:
                    table_id = preferred_table_by_page.get(page_number, "")
            if not table_id:
                promoted_meta = dict(metadata)
                promoted_meta.pop("table_residual", None)
                promoted_meta.pop("table_residual_candidate", None)
                promoted_meta["content_source"] = "page_blocks"
                promoted_meta["region_role"] = "text"
                promoted_meta["table_adjacent"] = True
                promoted_meta.pop("search_tier", None)
                promoted_meta["table_annotation_unanchored"] = True
                passthrough.append({"text": payload["text"], "metadata": promoted_meta})
                stats["unanchored_residual_promoted"] = int(stats["unanchored_residual_promoted"]) + 1
                continue
            projection_target = self._residual_projection_target(text=payload["text"], metadata=metadata)
            if projection_target == "narrative":
                promoted_meta = dict(metadata)
                promoted_meta.pop("table_residual", None)
                promoted_meta.pop("table_residual_candidate", None)
                promoted_meta["content_source"] = "page_blocks"
                promoted_meta["region_role"] = "text"
                promoted_meta["table_adjacent"] = True
                promoted_meta.pop("search_tier", None)
                promoted_meta["table_residual_projected"] = "narrative"
                promoted_meta["table_reference_id"] = table_id
                passthrough.append({"text": payload["text"], "metadata": promoted_meta})
                stats["narrative_promoted_segments"] = int(stats["narrative_promoted_segments"]) + 1
                continue
            residual_groups.setdefault(table_id, []).append(payload)
            stats["residual_segments_projected"] = int(stats["residual_segments_projected"]) + 1

        annotation_payloads: list[dict[str, Any]] = []
        for table_id, grouped in residual_groups.items():
            if not grouped:
                continue
            table_context = table_context_by_id.get(table_id, {"table_id": table_id})

            def _rank(payload: Mapping[str, Any]) -> tuple[float, int, int]:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
                overlap = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
                try:
                    overlap_score = float(overlap)
                except (TypeError, ValueError):
                    overlap_score = 0.0
                text = str(payload.get("text") or "")
                token_score = len(self._token_signature(text))
                return overlap_score, token_score, len(text)

            ranked = sorted(grouped, key=_rank, reverse=True)
            seen_fingerprints: set[str] = set()
            selected_texts: list[str] = []
            region_keys: list[str] = []
            reasons: list[str] = []
            source_anchors: list[str] = []
            page_numbers: list[int] = []
            for candidate in ranked:
                candidate_text = str(candidate.get("text") or "").strip()
                if not candidate_text:
                    continue
                fingerprint = self._chunk_fingerprint(candidate_text)
                if fingerprint and fingerprint in seen_fingerprints:
                    continue
                if fingerprint:
                    seen_fingerprints.add(fingerprint)
                selected_texts.append(candidate_text)
                metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
                region_key = str(metadata.get("table_residual_region_key") or "").strip()
                if region_key and region_key not in region_keys:
                    region_keys.append(region_key)
                reason = str(metadata.get("table_residual_reason") or "").strip()
                if reason and reason not in reasons:
                    reasons.append(reason)
                anchors = metadata.get("block_anchors")
                if isinstance(anchors, Sequence) and not isinstance(anchors, (str, bytes)):
                    for raw_anchor in anchors:
                        anchor = str(raw_anchor or "").strip()
                        if anchor and anchor not in source_anchors:
                            source_anchors.append(anchor)
                page_number = self._segment_page_number(metadata)
                if page_number and page_number not in page_numbers:
                    page_numbers.append(page_number)
            if not selected_texts:
                continue
            lines: list[str] = []
            table_title = str(table_context.get("table_title") or "").strip()
            if table_title:
                lines.append(f"[Table] {table_title}")
            lines.append("[Notes]")
            lines.extend(selected_texts)
            annotation_text = "\n".join(lines).strip()
            split_annotations = self._chunk_text(
                annotation_text,
                chunk_chars=self.table_annotation_max_chars,
                overlap=0,
            )
            if not split_annotations:
                split_annotations = [annotation_text]
            soft_limit = self.table_annotation_max_per_table
            soft_limit_exceeded = bool(soft_limit and len(split_annotations) > soft_limit)
            if soft_limit_exceeded:
                stats["soft_limit_exceeded_tables"] = int(stats["soft_limit_exceeded_tables"]) + 1
            for idx, rendered in enumerate(split_annotations, start=1):
                annotation_meta: dict[str, Any] = {
                    "strategy": "table_residual_projection",
                    "index_type": "text",
                    "content_source": "table_annotation",
                    "region_role": "table_annotation",
                    "table_annotation": True,
                    "table_annotation_source": "table_residual",
                    "table_annotation_rank": idx,
                    "table_annotation_total": len(split_annotations),
                    "table_annotation_fragment_count": len(selected_texts),
                    "table_annotation_max_chars": self.table_annotation_max_chars,
                    "table_annotation_soft_limit": soft_limit,
                    "table_annotation_soft_limit_exceeded": soft_limit_exceeded,
                    "table_id": table_id,
                    "search_tier": "supporting",
                }
                table_order_index = table_context.get("table_order_index")
                if table_order_index is not None:
                    annotation_meta["table_order_index"] = table_order_index
                if table_title:
                    annotation_meta["table_title"] = table_title
                table_page_number = table_context.get("table_page_number")
                if isinstance(table_page_number, int) and table_page_number > 0:
                    annotation_meta["table_page_number"] = table_page_number
                elif page_numbers:
                    annotation_meta["table_page_number"] = page_numbers[0]
                if page_numbers:
                    annotation_meta["page_numbers"] = page_numbers[:4]
                if region_keys:
                    annotation_meta["table_annotation_region_keys"] = region_keys[:8]
                if reasons:
                    annotation_meta["table_annotation_reasons"] = reasons[:6]
                if source_anchors:
                    annotation_meta["block_anchors"] = source_anchors[:12]
                annotation_payloads.append({"text": rendered, "metadata": annotation_meta})

        if residual_groups:
            stats["tables_with_annotations"] = len(residual_groups)
        stats["table_annotation_chunks_created"] = len(annotation_payloads)
        output_payloads = passthrough + annotation_payloads
        stats["output_payloads"] = len(output_payloads)
        return output_payloads, stats

    def _canonicalize_segment_payloads(
        self,
        *,
        upload: KnowledgeUpload,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        projected_payloads, projection_stats = self._project_table_residual_annotations(segment_payloads)
        canonicalized: list[dict[str, Any]] = []
        kind_counts: dict[str, int] = {}
        for payload in projected_payloads:
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
            canonical_meta = self._apply_canonical_chunk_metadata(
                upload_id=upload.id,
                text=text,
                metadata=metadata,
            )
            kind = str(canonical_meta.get("canonical_chunk_kind") or "unknown")
            kind_counts[kind] = int(kind_counts.get(kind, 0)) + 1
            canonicalized.append({"text": text, "metadata": canonical_meta})
        stats: dict[str, Any] = {
            "schema_version": self.canonical_chunk_schema_version,
            "input_payloads": len(segment_payloads),
            "output_payloads": len(canonicalized),
            "kind_counts": kind_counts,
            "table_residual_projection": projection_stats,
        }
        return canonicalized, stats
