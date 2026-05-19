from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Mapping, Sequence


_WS_RE = re.compile(r"\s+")
_HAS_DIGIT_RE = re.compile(r"\d")
_NUMERIC_SIGNAL_RE = re.compile(
    r"(?ix)"
    r"("
    r"\b(?:egp|usd|eur|gbp|sar|aed)\b"  # common currency codes
    r"|[%$€£]"
    r"|\b(?:min|max|minimum|maximum)\b"
    r"|\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\b"  # 1,560 / 1.560 / 1560 / 0.2
    r")"
)
_VALUE_PLACEHOLDER_RE = re.compile(r"(?ix)^(?:no\s+fees?|free|waived?)$")


def _clean_text(value: str) -> str:
    return _WS_RE.sub(" ", (value or "").strip())


def _norm_for_contains(value: str) -> str:
    return _clean_text(value).lower()


def _numeric_signal(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    if not _HAS_DIGIT_RE.search(text):
        return False
    return bool(_NUMERIC_SIGNAL_RE.search(text))


def _looks_like_value_placeholder(value: str) -> bool:
    """
    Generic filter: some tables include a low-information middle column (e.g. "No fees", "Free")
    between the true row label and the numeric value. When reconstructing 2-column pseudo tables
    we should avoid picking that placeholder as the row label.
    """
    text = _clean_text(value)
    if not text:
        return False
    return bool(_VALUE_PLACEHOLDER_RE.match(text))


def _bbox_tuple(bbox: Mapping[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, Mapping):
        return None
    try:
        x0 = float(bbox.get("x0") or 0.0)
        y0 = float(bbox.get("y0") or 0.0)
        x1 = float(bbox.get("x1") or 0.0)
        y1 = float(bbox.get("y1") or 0.0)
    except Exception:
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _bbox_union(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _bbox_overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    inter_w = min(a[2], b[2]) - max(a[0], b[0])
    inter_h = min(a[3], b[3]) - max(a[1], b[1])
    if inter_w <= 0.0 or inter_h <= 0.0:
        return 0.0
    inter = inter_w * inter_h
    area_a = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
    denom = max(1.0, min(area_a, area_b))
    return max(0.0, min(1.0, inter / denom))


def _bbox_width(bbox: tuple[float, float, float, float] | None) -> float:
    if not bbox:
        return 0.0
    return max(0.0, bbox[2] - bbox[0])


def _bbox_edge_distance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    # 0 if intersect; otherwise Euclidean distance between closest edges.
    dx = 0.0
    if a[2] < b[0]:
        dx = b[0] - a[2]
    elif b[2] < a[0]:
        dx = a[0] - b[2]
    dy = 0.0
    if a[3] < b[1]:
        dy = b[1] - a[3]
    elif b[3] < a[1]:
        dy = a[1] - b[3]
    return math.sqrt((dx * dx) + (dy * dy))


def _anchor_for_block(*, page_number: int, order_index: int, meta: Mapping[str, Any]) -> str:
    anchor = str(meta.get("anchor") or "").strip()
    if anchor:
        return anchor
    return f"p{page_number}-b{order_index}"


@dataclass(frozen=True)
class CanonicalReconstructionMeta:
    reconstructed_tables: int = 0
    reconstructed_rows: int = 0
    attached_blocks: int = 0
    attached_cells: int = 0
    consumed_blocks: int = 0
    modified_tables: int = 0
    details: dict[str, Any] = field(default_factory=dict)


from apps.knowledge.tables.canonical.attachments import CanonicalTableAttachmentMixin
from apps.knowledge.tables.canonical.pseudo_tables import CanonicalPseudoTableMixin


class CanonicalTableReconstructor(CanonicalPseudoTableMixin, CanonicalTableAttachmentMixin):
    """
    Generic ingestion-time reconstruction for "table-ish" PDF layouts:
    - Pass A: Build pseudo-tables from aligned blocks (gridless tables).
    - Pass B: Attach orphan/residual blocks into the most likely cell (cell completion).
    """

    def __init__(
        self,
        *,
        PageLayout: type,
        PageBlockPayload: type,
        TablePayload: type,
        TableRowPayload: type,
        TableCellPayload: type,
    ) -> None:
        self.PageLayout = PageLayout
        self.PageBlockPayload = PageBlockPayload
        self.TablePayload = TablePayload
        self.TableRowPayload = TableRowPayload
        self.TableCellPayload = TableCellPayload

    def run(
        self,
        *,
        pages: Sequence[Any],
        tables: Sequence[Any],
    ) -> tuple[list[Any], list[Any], CanonicalReconstructionMeta, list[dict[str, Any]]]:
        # issues are returned as IssuePayload-like dicts so knowledge_ingestion can wrap them if desired.
        issues: list[dict[str, Any]] = []
        meta = CanonicalReconstructionMeta()

        if not pages:
            return list(pages), list(tables), meta, issues

        tables_in: list[Any] = list(tables or [])
        pages_in: list[Any] = list(pages)

        max_order_index = max((int(getattr(t, "order_index", 0) or 0) for t in tables_in), default=0)

        # Pass A: reconstruct missing pseudo-tables from page blocks.
        pages_in, new_tables, a_meta = self._reconstruct_pseudo_tables(pages_in, tables_in, start_order_index=max_order_index + 1)
        if new_tables:
            tables_in.extend(new_tables)
            max_order_index = max((int(getattr(t, "order_index", 0) or 0) for t in tables_in), default=max_order_index)
        meta = self._merge_meta(meta, a_meta)

        # Pass B: attach orphan/residual blocks into extracted table cells.
        pages_in, tables_in, b_meta = self._attach_orphan_blocks(pages_in, tables_in)
        meta = self._merge_meta(meta, b_meta)

        return pages_in, tables_in, meta, issues

    @staticmethod
    def _merge_meta(base: CanonicalReconstructionMeta, delta: CanonicalReconstructionMeta) -> CanonicalReconstructionMeta:
        details = dict(base.details)
        details.update(delta.details)
        return CanonicalReconstructionMeta(
            reconstructed_tables=base.reconstructed_tables + delta.reconstructed_tables,
            reconstructed_rows=base.reconstructed_rows + delta.reconstructed_rows,
            attached_blocks=base.attached_blocks + delta.attached_blocks,
            attached_cells=base.attached_cells + delta.attached_cells,
            consumed_blocks=base.consumed_blocks + delta.consumed_blocks,
            modified_tables=base.modified_tables + delta.modified_tables,
            details=details,
        )
