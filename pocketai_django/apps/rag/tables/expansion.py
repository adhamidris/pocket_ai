from __future__ import annotations

from apps.rag.tables.chunk_analysis import TableChunkAnalysisMixin
from apps.rag.tables.hit_merge import TableHitMergeMixin
from apps.rag.tables.row_expansion import TableRowExpansionMixin
from apps.rag.tables.routing import TableRoutingMixin


class TableExpansionMixin(
    TableChunkAnalysisMixin,
    TableRoutingMixin,
    TableHitMergeMixin,
    TableRowExpansionMixin,
):
    pass
