from __future__ import annotations

import dataclasses
from typing import Sequence

from apps.accounts.models import BusinessProfile
from apps.knowledge.models import KnowledgeUpload
from apps.knowledge.models import KnowledgeUploadTableRow


@dataclasses.dataclass(frozen=True)
class TableRowQueryRequest:
    """Typed payload for requesting table rows by sheet label and range."""

    business_profile: BusinessProfile
    sheet_label: str
    row_start: int
    row_end: int
    columns: Sequence[str] | None = None
    upload: KnowledgeUpload | None = None


@dataclasses.dataclass(frozen=True)
class TableRowQueryResult:
    """Structured row payload returned by the lookup service."""

    upload_id: str
    sheet_label: str
    row_index: int
    values: Sequence[str]
    column_schema: Sequence[str]
    source_row: KnowledgeUploadTableRow | None = None


class TableRowLookupService:
    """Placeholder interface for future "query rows on demand" workflows.

    This intentionally has no implementation yet; phase five only establishes the
    contract that orchestrator/search layers can depend on once structured row
    retrieval is enabled.
    """

    def fetch_rows(self, request: TableRowQueryRequest) -> Sequence[TableRowQueryResult]:
        raise NotImplementedError(
            "TableRowLookupService.fetch_rows is a placeholder for future structured queries."
        )


__all__ = [
    "TableRowLookupService",
    "TableRowQueryRequest",
    "TableRowQueryResult",
]
