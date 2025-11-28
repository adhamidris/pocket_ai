"""
Table row lookup service for on-demand structured data retrieval.

This module provides the contract for querying individual table rows from normalized
spreadsheet uploads. It's designed to support "query rows on demand" workflows where
the LLM or search layer needs to fetch specific rows by sheet name and row range.

Related modules:
    - table_normalization.py: Handles normalization of spreadsheet data during ingestion
    - knowledge_ingestion.py: Persists structured table data into KnowledgeUploadTableRow models
    - mcp/tools.py: Contains table_aggregate_handler that may use this service in the future

Architecture note:
    This is a placeholder interface (phase five) that establishes the contract before
    implementation. The service will query KnowledgeUploadTableRow models created during
    ingestion, allowing the orchestrator to retrieve specific rows without re-parsing
    the entire spreadsheet.
"""

from __future__ import annotations

import dataclasses
from typing import Sequence

from apps.accounts.models import BusinessProfile
from apps.accounts.models import KnowledgeUpload
from apps.accounts.models import KnowledgeUploadTableRow


@dataclasses.dataclass(frozen=True)
class TableRowQueryRequest:
    """
    Typed payload for requesting table rows by sheet label and row range.

    This dataclass encapsulates all parameters needed to query rows from a normalized
    spreadsheet. It's frozen to prevent accidental mutation and ensure thread-safety
    when passed between service layers.

    Attributes:
        business_profile: The business context for the query. Required for multi-tenant
            isolation and to access business-specific normalization policies.
        sheet_label: The name/label of the sheet to query (e.g., "Sales Q1", "Sheet1").
            Must match the sheet name used during ingestion. Case-insensitive matching
            may be applied by the implementation.
        row_start: Zero-based inclusive start index of the row range to fetch.
            Must be >= 0 and <= row_end.
        row_end: Zero-based inclusive end index of the row range to fetch.
            Must be >= row_start. The implementation should handle large ranges efficiently.
        columns: Optional sequence of column names to filter the result. When None,
            all columns are returned. When specified, only these columns appear in the
            result's values array (maintaining the requested order).
        upload: Optional KnowledgeUpload instance to scope the query to a specific
            upload. When None, the service may search across all uploads for the
            business_profile. Providing this narrows the search and improves performance.

    Edge cases:
        - Empty ranges (row_start > row_end) should return an empty sequence
        - Invalid sheet_label should raise a clear error rather than returning empty
        - Mismatched column names should be handled gracefully (skip or error per policy)

    Example:
        Request rows 5-10 from "Sales Data" sheet, columns ["Product", "Revenue"]:
        >>> request = TableRowQueryRequest(
        ...     business_profile=bp,
        ...     sheet_label="Sales Data",
        ...     row_start=5,
        ...     row_end=10,
        ...     columns=["Product", "Revenue"]
        ... )
    """

    business_profile: BusinessProfile
    sheet_label: str
    row_start: int
    row_end: int
    columns: Sequence[str] | None = None
    upload: KnowledgeUpload | None = None


@dataclasses.dataclass(frozen=True)
class TableRowQueryResult:
    """
    Structured row payload returned by the lookup service.

    Each result represents a single row from a normalized table, with metadata
    linking it back to the source upload and providing the column schema for
    interpreting the values array.

    Attributes:
        upload_id: UUID of the KnowledgeUpload that contains this row. Allows
            callers to track provenance and link back to the original document.
        sheet_label: The sheet name this row came from. Matches the sheet_label
            in the query request, normalized to the canonical form used during ingestion.
        row_index: Zero-based index of this row within the sheet. This is the
            original row position from the normalized sheet (header row excluded).
            Use this to maintain ordering when fetching multiple rows.
        values: Sequence of cell values for this row, as strings. The length
            matches column_schema. Values are normalized (null tokens replaced with
            empty strings) per the normalization policy used during ingestion.
        column_schema: Ordered list of column names/headers that correspond to
            the values array. This matches the schema stored in KnowledgeUploadTable
            and ensures callers can map values to column names correctly.
        source_row: Optional reference to the KnowledgeUploadTableRow model instance.
            Provided when the implementation wants to expose the full ORM object for
            advanced use cases (e.g., accessing raw_text, bbox, metadata). When None,
            callers should use the other fields for standard row data.

    Design decisions:
        - Values are strings to match the normalized format from table_normalization.py
        - column_schema is included per-row to handle schema evolution (different
          sheets may have different schemas even within the same upload)
        - source_row is optional to allow lightweight implementations that don't
          load full ORM objects unless needed

    Example:
        Result for row 5 from "Sales Data" with columns ["Product", "Revenue"]:
        >>> result = TableRowQueryResult(
        ...     upload_id="abc-123",
        ...     sheet_label="Sales Data",
        ...     row_index=5,
        ...     values=["Widget A", "1500.00"],
        ...     column_schema=["Product", "Revenue"],
        ...     source_row=row_obj
        ... )
    """

    upload_id: str
    sheet_label: str
    row_index: int
    values: Sequence[str]
    column_schema: Sequence[str]
    source_row: KnowledgeUploadTableRow | None = None


class TableRowLookupService:
    """
    Placeholder interface for future "query rows on demand" workflows.

    This service will enable the orchestrator and search layers to fetch specific
    table rows from normalized spreadsheet uploads without re-parsing entire files.
    It queries the KnowledgeUploadTableRow models created during ingestion.

    Current status:
        This is intentionally unimplemented (phase five placeholder). The interface
        is established now so that orchestrator/search code can depend on it without
        blocking on the implementation.

    Future implementation notes:
        - Should query KnowledgeUploadTableRow via KnowledgeUploadTable relationships
        - Must respect business_profile isolation (multi-tenant security)
        - Should handle sheet name normalization (case-insensitive, whitespace trimming)
        - Must apply column filtering when columns parameter is provided
        - Should optimize for range queries (row_start to row_end) using database indexes
        - Consider caching frequently accessed sheets to reduce DB load
        - May need to join with KnowledgeUploadTableCell to reconstruct full row values

    Integration points:
        - Called by orchestrator when LLM requests specific rows (e.g., "show me rows 10-20")
        - Used by search layer to fetch context rows after identifying relevant tables
        - May be invoked by MCP tools (table_aggregate_handler) for detailed row inspection

    Related systems:
        - table_normalization.py: Normalizes data during ingestion, creating the
          structured data this service queries
        - knowledge_ingestion.py: Persists KnowledgeUploadTableRow models that this
          service will read from
        - mcp/tools.py: Contains table aggregation tools that may use this service
    """

    def fetch_rows(self, request: TableRowQueryRequest) -> Sequence[TableRowQueryResult]:
        """
        Fetch table rows matching the query request.

        Args:
            request: Query parameters specifying business, sheet, row range, and
                optional column filters.

        Returns:
            Sequence of TableRowQueryResult objects, one per matching row. Results
            are ordered by row_index ascending. Empty sequence if no rows match.

        Raises:
            NotImplementedError: Always raised until implementation is complete.
                This is intentional - the service is a placeholder for phase five.

        Future implementation should:
            - Validate request parameters (row_start <= row_end, valid business_profile)
            - Query KnowledgeUploadTableRow via appropriate relationships
            - Apply sheet name matching (case-insensitive, normalized)
            - Filter by row_index range (row_start <= row_index <= row_end)
            - Apply column filtering if columns parameter is provided
            - Handle empty results gracefully (return empty sequence, don't raise)
            - Preserve row ordering (sort by row_index)
            - Optimize queries using database indexes on (table_id, row_index)

        Performance considerations:
            - For large ranges, consider pagination or streaming
            - Cache sheet metadata (column_schema) to avoid repeated lookups
            - Use select_related/prefetch_related to minimize DB queries
        """
        raise NotImplementedError(
            "TableRowLookupService.fetch_rows is a placeholder for future structured queries."
        )


__all__ = [
    "TableRowLookupService",
    "TableRowQueryRequest",
    "TableRowQueryResult",
]
