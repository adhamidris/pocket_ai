from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from apps.knowledge.models import KnowledgeUpload


class IngestionChunkResidualsMixin:

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

    @staticmethod
    def _token_signature(text: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        if not tokens:
            return set()
        stop = {
            "section",
            "table",
            "row",
            "rows",
            "columns",
            "column",
            "labels",
            "label",
            "identifiers",
            "identifier",
        }
        signature: set[str] = set()
        for token in tokens:
            if len(token) < 3:
                continue
            if token in stop:
                continue
            signature.add(token)
        return signature

    @staticmethod
    def _segment_page_number(metadata: Mapping[str, Any]) -> int | None:
        if not isinstance(metadata, Mapping):
            return None
        raw_page_numbers = metadata.get("page_numbers")
        if isinstance(raw_page_numbers, Sequence) and not isinstance(raw_page_numbers, (str, bytes)):
            for raw_value in raw_page_numbers:
                try:
                    page_number = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if page_number > 0:
                    return page_number
        raw_page = metadata.get("table_page_number")
        try:
            page_number = int(raw_page)
        except (TypeError, ValueError):
            return None
        return page_number if page_number > 0 else None

    def _residual_region_key(self, metadata: Mapping[str, Any]) -> str:
        if not isinstance(metadata, Mapping):
            return "residual:unscoped"
        region_key = str(metadata.get("table_residual_region_key") or "").strip()
        if region_key:
            return region_key
        region_keys = metadata.get("table_residual_region_keys")
        if isinstance(region_keys, Sequence) and not isinstance(region_keys, (str, bytes)):
            for value in region_keys:
                normalized = str(value or "").strip()
                if normalized:
                    return normalized
        page_number = self._segment_page_number(metadata)
        if page_number:
            return f"p{page_number}-residual"
        page_anchor = str(metadata.get("page_anchor") or "").strip()
        if page_anchor:
            return f"{page_anchor}-residual"
        return "residual:unscoped"

    def _segment_semantically_equivalent_to_table_row(
        self,
        *,
        residual_text: str,
        row_signatures_for_page: Sequence[set[str]],
        all_row_signatures: Sequence[set[str]],
        min_residual_coverage: float | None = None,
        min_row_coverage: float | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        residual_tokens = self._token_signature(residual_text)
        if not residual_tokens:
            return False, None
        candidates = list(row_signatures_for_page) if row_signatures_for_page else list(all_row_signatures)
        if not candidates:
            return False, None
        if min_residual_coverage is None:
            min_residual_coverage = self.table_residual_equivalence_min_overlap
        if min_row_coverage is None:
            min_row_coverage = self.table_residual_equivalence_min_overlap
        min_residual = max(0.0, min(1.0, float(min_residual_coverage)))
        min_row = max(0.0, min(1.0, float(min_row_coverage)))
        best: dict[str, Any] | None = None
        for row_tokens in candidates:
            if not row_tokens:
                continue
            shared = residual_tokens & row_tokens
            if len(shared) < self.table_residual_equivalence_min_shared_tokens:
                continue
            residual_coverage = len(shared) / max(1, len(residual_tokens))
            row_coverage = len(shared) / max(1, len(row_tokens))
            union_count = max(1, len(residual_tokens | row_tokens))
            jaccard = len(shared) / union_count
            candidate = {
                "shared_tokens": int(len(shared)),
                "residual_tokens": int(len(residual_tokens)),
                "row_tokens": int(len(row_tokens)),
                "residual_coverage": round(float(residual_coverage), 4),
                "row_coverage": round(float(row_coverage), 4),
                "jaccard": round(float(jaccard), 4),
            }
            if best is None:
                best = candidate
            else:
                best_key = (
                    min(float(best["residual_coverage"]), float(best["row_coverage"])),
                    float(best["jaccard"]),
                    int(best["shared_tokens"]),
                )
                candidate_key = (
                    min(float(candidate["residual_coverage"]), float(candidate["row_coverage"])),
                    float(candidate["jaccard"]),
                    int(candidate["shared_tokens"]),
                )
                if candidate_key > best_key:
                    best = candidate
            if residual_coverage >= min_residual and row_coverage >= min_row:
                candidate["matched"] = True
                return True, candidate
        if best is not None:
            best["matched"] = False
        return False, best

    def _classify_table_residual_segment_kind(self, text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return "unknown"
        base_text = re.sub(r"\n?\s*Identifiers:\s.*$", "", normalized, flags=re.IGNORECASE).strip()
        if not base_text:
            base_text = normalized
        tokens = self._token_signature(base_text)
        token_count = len(tokens)
        line_count = len([line for line in base_text.splitlines() if line.strip()])
        key_value_pairs = len(re.findall(r"\b[^:\n]{1,40}:\s+\S+", base_text))
        has_row_marker = "[Row]" in base_text or "\t" in base_text
        has_sentence_punctuation = bool(re.search(r"[.!?;:]", base_text))
        numeric_signal = self._has_numeric_table_signal(base_text)
        if token_count <= 4 and line_count <= 2:
            return "cell_like"
        if has_row_marker or key_value_pairs >= 2:
            return "row_like"
        if token_count <= 12 and not has_sentence_punctuation:
            if numeric_signal:
                return "row_like"
            return "heading_like"
        if numeric_signal and token_count <= 24 and line_count <= 4 and not has_sentence_punctuation:
            return "row_like"
        return "note_like"

    def _compact_residual_text(self, text: str) -> tuple[str, bool]:
        normalized = str(text or "").strip()
        if not normalized:
            return "", False
        if len(normalized) <= self.table_residual_compact_max_chars:
            return normalized, False
        compact_segments = self._chunk_text(
            normalized,
            chunk_chars=self.table_residual_compact_max_chars,
            overlap=0,
        )
        if compact_segments:
            return compact_segments[0], True
        return normalized[: self.table_residual_compact_max_chars], True

    def _residual_projection_target(
        self,
        *,
        text: str,
        metadata: Mapping[str, Any],
    ) -> str:
        segment_kind = str(
            metadata.get("table_residual_segment_kind")
            or self._classify_table_residual_segment_kind(text)
            or "unknown"
        ).strip()
        base_text = re.sub(r"\n?\s*Identifiers:\s.*$", "", str(text or ""), flags=re.IGNORECASE).strip()
        if not base_text:
            base_text = str(text or "")
        numeric_signal = self._has_numeric_table_signal(base_text)
        starts_with_note_marker = base_text.lstrip().startswith("*")
        token_count = len(self._token_signature(base_text))
        has_sentence_punctuation = bool(re.search(r"[.!?;:]", base_text))
        overlap_raw = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
        try:
            overlap_ratio = float(overlap_raw)
        except (TypeError, ValueError):
            overlap_ratio = 0.0

        # Headline-like table-adjacent text should remain narrative, not anchored notes.
        if segment_kind == "heading_like" and not numeric_signal and not starts_with_note_marker:
            return "narrative"

        # Short, non-numeric near-table snippets are usually surrounding prose.
        if (
            segment_kind == "note_like"
            and not numeric_signal
            and not starts_with_note_marker
            and token_count <= 18
            and not has_sentence_punctuation
        ):
            return "narrative"

        # Weak-overlap text that survived residual reconciliation should stay narrative.
        if (
            segment_kind == "note_like"
            and not numeric_signal
            and not starts_with_note_marker
            and overlap_ratio < self.pdf_table_residual_overlap_min_ratio
        ):
            return "narrative"

        return "annotation"

    def _reconcile_table_residual_segments(
        self,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            payloads.append({"text": text, "metadata": dict(metadata)})
        if not payloads:
            return [], {
                "input_residual_segments": 0,
                "kept_residual_segments": 0,
                "dropped_equivalent_segments": 0,
                "dropped_equivalent_cell_like_segments": 0,
                "dropped_equivalent_heading_like_segments": 0,
                "dropped_equivalent_row_like_segments": 0,
                "dropped_equivalent_note_like_segments": 0,
                "dropped_cap_segments": 0,
                "regions_with_residuals": 0,
                "equivalence_uncertain_kept_segments": 0,
            }

        row_signatures_all: list[set[str]] = []
        row_signatures_by_page: dict[int, list[set[str]]] = {}
        row_fingerprints_by_page: dict[int, set[str]] = {}
        row_fingerprints_all: set[str] = set()
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_row(metadata):
                continue
            text = payload["text"]
            page_number = self._segment_page_number(metadata)
            row_signature = self._token_signature(text)
            if row_signature:
                row_signatures_all.append(row_signature)
                if page_number:
                    row_signatures_by_page.setdefault(page_number, []).append(row_signature)
            fingerprint = self._chunk_fingerprint(text)
            if not fingerprint:
                continue
            row_fingerprints_all.add(fingerprint)
            if page_number:
                row_fingerprints_by_page.setdefault(page_number, set()).add(fingerprint)

        residual_count = 0
        dropped_equivalent = 0
        dropped_equivalent_by_kind: dict[str, int] = {
            "cell_like": 0,
            "heading_like": 0,
            "row_like": 0,
            "note_like": 0,
            "unknown": 0,
        }
        equivalence_uncertain_kept = 0
        dropped_equivalence_audit: list[dict[str, Any]] = []

        def _record_equivalence_drop(
            *,
            reason: str,
            segment_kind: str,
            metadata: Mapping[str, Any],
            page_number: int | None,
            region_key: str,
            fingerprint: str,
            match: Mapping[str, Any] | None = None,
        ) -> None:
            dropped_equivalent_by_kind.setdefault(segment_kind, 0)
            dropped_equivalent_by_kind[segment_kind] += 1
            if len(dropped_equivalence_audit) >= 16:
                return
            anchor = ""
            anchors = metadata.get("block_anchors")
            if isinstance(anchors, Sequence) and not isinstance(anchors, (str, bytes)):
                for value in anchors:
                    normalized = str(value or "").strip()
                    if normalized:
                        anchor = normalized
                        break
            event: dict[str, Any] = {
                "reason": reason,
                "segment_kind": segment_kind,
                "page_number": page_number,
                "region_key": region_key,
                "anchor": anchor or None,
                "fingerprint": (fingerprint[:16] if fingerprint else None),
            }
            if isinstance(match, Mapping):
                event["shared_tokens"] = int(match.get("shared_tokens") or 0)
                event["residual_coverage"] = float(match.get("residual_coverage") or 0.0)
                event["row_coverage"] = float(match.get("row_coverage") or 0.0)
                event["jaccard"] = float(match.get("jaccard") or 0.0)
            dropped_equivalence_audit.append(event)

        residual_groups: dict[str, list[dict[str, Any]]] = {}
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_residual(metadata):
                continue
            residual_count += 1
            page_number = self._segment_page_number(metadata)
            residual_text = payload["text"]
            segment_kind = self._classify_table_residual_segment_kind(residual_text)
            metadata["table_residual_segment_kind"] = segment_kind
            region_key = self._residual_region_key(metadata)
            if segment_kind == "cell_like":
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="low_information_cell",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=self._chunk_fingerprint(residual_text),
                )
                continue
            residual_fingerprint = self._chunk_fingerprint(residual_text)
            if residual_fingerprint:
                page_fingerprints = row_fingerprints_by_page.get(page_number or -1, set())
                if residual_fingerprint in page_fingerprints or (
                    not page_fingerprints and residual_fingerprint in row_fingerprints_all
                ):
                    dropped_equivalent += 1
                    _record_equivalence_drop(
                        reason="fingerprint_match",
                        segment_kind=segment_kind,
                        metadata=metadata,
                        page_number=page_number,
                        region_key=region_key,
                        fingerprint=residual_fingerprint,
                    )
                    continue
            page_signatures = row_signatures_by_page.get(page_number or -1, [])
            semantic_min_residual: float | None = None
            semantic_min_row: float | None = None
            if segment_kind in {"row_like", "heading_like"}:
                semantic_min_residual = self.table_residual_equivalence_min_overlap
                semantic_min_row = min(0.4, self.table_residual_equivalence_min_overlap)
            elif segment_kind == "note_like":
                residual_token_count = len(self._token_signature(residual_text))
                if residual_token_count <= 16:
                    semantic_min_residual = self.table_residual_equivalence_min_overlap
                    semantic_min_row = self.table_residual_equivalence_min_overlap
                else:
                    strict = min(0.98, max(0.9, self.table_residual_equivalence_min_overlap + 0.25))
                    semantic_min_residual = strict
                    semantic_min_row = strict
            equivalent, match = self._segment_semantically_equivalent_to_table_row(
                residual_text=residual_text,
                row_signatures_for_page=page_signatures,
                all_row_signatures=row_signatures_all,
                min_residual_coverage=semantic_min_residual,
                min_row_coverage=semantic_min_row,
            )
            metadata["table_residual_equivalence_checked"] = True
            if isinstance(match, Mapping):
                metadata["table_residual_equivalence_best"] = dict(match)
            one_sided_row_equivalent = False
            if not equivalent and isinstance(match, Mapping) and segment_kind in {"row_like", "heading_like"}:
                residual_cov = float(match.get("residual_coverage") or 0.0)
                shared_tokens = int(match.get("shared_tokens") or 0)
                if (
                    residual_cov >= self.table_residual_equivalence_min_overlap
                    and shared_tokens >= self.table_residual_equivalence_min_shared_tokens
                ):
                    one_sided_row_equivalent = True
            if equivalent:
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="semantic_equivalent",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=residual_fingerprint,
                    match=match,
                )
                continue
            if one_sided_row_equivalent:
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="semantic_row_subset",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=residual_fingerprint,
                    match=match,
                )
                continue
            if isinstance(match, Mapping):
                residual_cov = float(match.get("residual_coverage") or 0.0)
                row_cov = float(match.get("row_coverage") or 0.0)
                if (
                    residual_cov >= self.table_residual_equivalence_min_overlap
                    or row_cov >= self.table_residual_equivalence_min_overlap
                ):
                    metadata["table_residual_equivalence_uncertain"] = True
                    equivalence_uncertain_kept += 1

            compact_text, compacted = self._compact_residual_text(residual_text)
            payload["text"] = compact_text
            if compacted:
                metadata["table_residual_compacted"] = True
                metadata["table_residual_compact_max_chars"] = self.table_residual_compact_max_chars

            metadata["table_residual_region_key"] = region_key
            residual_groups.setdefault(region_key, []).append(payload)

        kept_residual: list[dict[str, Any]] = []
        dropped_cap = 0
        for region_key, region_payloads in residual_groups.items():
            def _rank(payload: Mapping[str, Any]) -> tuple[float, int, int]:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
                overlap = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
                try:
                    overlap_score = float(overlap)
                except (TypeError, ValueError):
                    overlap_score = 0.0
                text = str(payload.get("text") or "")
                token_score = len(self._token_signature(text))
                length_score = len(text)
                return overlap_score, token_score, length_score

            ranked = sorted(region_payloads, key=_rank, reverse=True)
            # Coverage-first policy: once a residual segment is proven non-equivalent
            # to indexed table rows, keep it. Overlap is a dedupe hint, not a hard
            # suppression decision.
            kept_residual.extend(ranked)

        kept_residual_ids = {id(payload) for payload in kept_residual}
        reconciled: list[dict[str, Any]] = []
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_residual(metadata):
                reconciled.append(payload)
                continue
            if id(payload) in kept_residual_ids:
                reconciled.append(payload)
        stats = {
            "input_residual_segments": residual_count,
            "kept_residual_segments": len(kept_residual),
            "dropped_equivalent_segments": dropped_equivalent,
            "dropped_equivalent_cell_like_segments": int(dropped_equivalent_by_kind.get("cell_like", 0)),
            "dropped_equivalent_heading_like_segments": int(dropped_equivalent_by_kind.get("heading_like", 0)),
            "dropped_equivalent_row_like_segments": int(dropped_equivalent_by_kind.get("row_like", 0)),
            "dropped_equivalent_note_like_segments": int(dropped_equivalent_by_kind.get("note_like", 0)),
            "dropped_cap_segments": dropped_cap,
            "regions_with_residuals": len(residual_groups),
            "max_per_region": self.table_residual_max_per_region,
            "equiv_min_overlap": round(self.table_residual_equivalence_min_overlap, 4),
            "equiv_min_shared_tokens": self.table_residual_equivalence_min_shared_tokens,
            "equivalence_uncertain_kept_segments": equivalence_uncertain_kept,
        }
        if dropped_equivalence_audit:
            stats["dropped_equivalence_audit_sample"] = dropped_equivalence_audit
        return reconciled, stats
