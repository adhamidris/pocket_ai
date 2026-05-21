from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


class IngestionChunkResidualReconciliationMixin:

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
