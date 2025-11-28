"""
Table normalization for spreadsheet ingestion before RAG indexing.

This module cleans and normalizes spreadsheet data (CSV, XLSX) before ingestion
into the knowledge base. It handles null token replacement, empty row/column
trimming, sheet filtering, and type coercion to ensure consistent, analytics-friendly
table structures.

Architecture:
    Normalization happens as a preprocessing step in the ingestion pipeline.
    KnowledgeIngestionService calls normalize_sheet_rows() for each sheet in
    an upload, then uses the NormalizedSheet output to build TablePayload objects
    for embedding and entity extraction.

Key features:
    - Null token handling: Treats common spreadsheet null markers ("N/A", "#REF!", etc.) as empty
    - Empty row/column trimming: Removes empty rows and columns to reduce embedding size
    - Sheet filtering: Whitelist/blacklist support for multi-sheet files
    - Type coercion: Converts all values to strings for consistent downstream processing
    - Policy layering: Global defaults → business metadata → upload metadata (upload wins)

Why normalization:
    Spreadsheets often contain formatting artifacts, error values, and empty cells
    that can skew embeddings and confuse the LLM. Normalization ensures clean,
    consistent data that produces better RAG results.

Related modules:
    - knowledge_ingestion.py: Calls normalize_sheet_rows() during CSV/XLSX extraction
    - Uses NormalizedSheet.column_schema and rows to build TablePayload objects
    - Stores normalization diagnostics in upload.ingestion_metadata for observability
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence

from django.conf import settings

# Default null tokens treated as empty cells (case-insensitive matching)
# These are common spreadsheet null markers that should not appear in embeddings
# or affect aggregations. Includes Excel error values (#REF!, #DIV/0!) and
# common NA representations (n/a, na, null, etc.)
DEFAULT_NULL_TOKENS = {
    "",
    "null",
    "n/a",
    "na",
    "nan",
    "#n/a",
    "#ref!",
    "#div/0!",
    "undefined",
}


def _canonical(value: str) -> str:
    """
    Normalize generic strings for comparison (lowercase, trim).

    Args:
        value: Raw string token.

    Returns:
        Lowercased, trimmed token used for null/whitelist lookups.

    Why:
        Case-insensitive matching ensures "N/A" and "n/a" are treated the same.
        Trimming removes whitespace that could cause false mismatches. This
        canonical form is used for null token matching and sheet name comparisons.
    """
    return value.strip().lower()


def _canonical_sheet(value: str) -> str:
    """
    Normalize sheet names for whitelist/blacklist matching.

    Args:
        value: Raw sheet name from spreadsheet.

    Returns:
        Canonical sheet identifier used for allow/deny checks.

    Why:
        Sheet name matching must be case-insensitive and whitespace-tolerant
        because users may name sheets inconsistently ("Data" vs "data" vs " Data ").
        This ensures whitelist/blacklist policies work regardless of naming style.

    Used by:
        - resolve_normalization_policy() to normalize whitelist/blacklist entries
        - sheet_is_allowed() to match sheet names against policy filters
    """
    return value.strip().lower()


@dataclass(frozen=True)
class TableNormalizationPolicy:
    """
    Configuration controlling how spreadsheet-like uploads are cleaned before RAG indexing.

    This policy is resolved from global settings, business metadata, and upload metadata
    (in that precedence order). It controls all aspects of table normalization to ensure
    consistent, clean data for embedding and entity extraction.

    Attributes:
        enabled: Toggle to bypass normalization entirely (useful for one-off debugging).
            When False, raw spreadsheet structure is preserved for troubleshooting.
        null_tokens: Lowercased tokens treated as empty cells. Includes DEFAULT_NULL_TOKENS
            plus any tenant-specific tokens from business/upload metadata.
        drop_empty_columns: If True, removes columns with no header and no values.
            Preserves sparse numeric columns (header or data must be present).
        sheet_whitelist: Optional allow-list; when set only these sheets are processed.
            Useful for multi-sheet files where only specific tabs should be ingested.
        sheet_blacklist: Optional block-list that always wins over the whitelist.
            Blacklist takes precedence to allow quick exclusion of noisy/problematic sheets.
        policy_version: Tag stored with ingest summaries for replay/forensics.
            Enables comparison of normalization results across policy versions.

    Why:
        Policy-based normalization allows tenant-specific customization without code changes.
        Version tagging enables tracking how policy changes affect ingestion results over time.

    Related:
        - resolve_normalization_policy() builds this from settings and metadata
        - Used by normalize_sheet_rows() to control normalization behavior
        - Stored in ingestion_metadata for audit trail and debugging
    """
    enabled: bool  # Master toggle for normalization (False = preserve raw structure)
    null_tokens: set[str] = field(default_factory=set)  # Tokens treated as empty (canonical form)
    drop_empty_columns: bool = True  # Remove columns with no header and no data
    sheet_whitelist: set[str] = field(default_factory=set)  # Only process these sheets (if set)
    sheet_blacklist: set[str] = field(default_factory=set)  # Never process these sheets (takes precedence)
    policy_version: str = "v1"  # Version tag for tracking policy changes over time


@dataclass
class SheetNormalizationDiagnostics:
    """
    Per-sheet diagnostics emitted during normalization (counts, skip reasons).

    Tracks what normalization operations were performed on each sheet. Stored
    alongside the upload in ingestion_metadata so we can compare ingest runs
    if policy knobs change later or debug ingestion issues.

    Attributes:
        sheet_name: Name of the sheet being normalized (for identification).
        rows_dropped: Number of empty rows removed (reduces embedding size).
        columns_trimmed: Number of empty columns removed (keeps schema compact).
        tokens_replaced: Number of null tokens replaced with empty strings.
        skipped: Whether this sheet was skipped entirely (empty or policy-filtered).
        skip_reason: Reason for skipping ("empty", "empty_columns", "policy", etc.).

    Why:
        Diagnostics enable observability into normalization behavior. They help
        identify when normalization is too aggressive (dropping too many rows/columns)
        or when sheets are being skipped unexpectedly.

    Related:
        - normalize_sheet_rows() accumulates these during processing
        - summarize_normalization() aggregates these into upload-level summary
        - Stored in upload.ingestion_metadata["normalization"] for dashboard display
    """
    sheet_name: str  # Sheet identifier for diagnostics
    rows_dropped: int = 0  # Count of empty rows removed
    columns_trimmed: int = 0  # Count of empty columns removed
    tokens_replaced: int = 0  # Count of null tokens replaced with empty strings
    skipped: bool = False  # Whether sheet was skipped entirely
    skip_reason: str | None = None  # Reason for skipping ("empty", "empty_columns", "policy")


@dataclass
class NormalizedSheet:
    """
    Normalized sheet output with schema, rows, and diagnostics.

    The normalized output of a spreadsheet sheet after cleaning. All values are
    strings, empty rows/columns are removed, and null tokens are replaced with
    empty strings. This structure is ready for table aggregation and embedding.

    Attributes:
        sheet_name: Original sheet name (for identification and diagnostics).
        column_schema: Header row after normalization and empty column removal.
            Empty headers become "column_1", "column_2", etc. This schema matches
            the trimmed structure so downstream code can rely on stable column ordering.
        rows: Data rows (header excluded) after normalization. All values are strings.
            Empty rows are removed. Column count matches column_schema length.
        diagnostics: Per-sheet normalization statistics (rows dropped, tokens replaced, etc.).

    Why:
        The downstream table_aggregate tool expects a single header row and compact
        numeric columns. This structure ensures consistent column ordering and removes
        noise that would confuse embeddings or skew aggregations.

    Related:
        - normalize_sheet_rows() produces these from raw spreadsheet data
        - KnowledgeIngestionService._build_table_from_normalized_sheet() converts
          these into TablePayload objects for embedding and entity extraction
        - column_schema is used to build table headers in the knowledge base
    """
    sheet_name: str  # Original sheet name (preserved for identification)
    column_schema: list[str]  # Header row after normalization (empty headers become "column_N")
    rows: list[list[str]]  # Data rows (header excluded), all values normalized to strings
    diagnostics: SheetNormalizationDiagnostics  # Per-sheet normalization statistics


def resolve_normalization_policy(upload: Any | None) -> TableNormalizationPolicy:
    """
    Build normalization settings by layering global defaults, business overrides, and upload overrides.

    Args:
        upload: Optional upload object that may contain per-business and per-upload `table_policy`.

    Returns:
        TableNormalizationPolicy describing how to treat sheets/cells for this ingest run.

    Precedence:
        Global settings -> business metadata -> upload metadata. The last writer wins so support
        can surgically toggle normalization for a single file without changing tenant defaults.
    """
    base_enabled = bool(getattr(settings, "INGEST_NORMALIZE_TABLES", True))
    # Version tag keeps ingest summaries comparable even if default knobs change later.
    policy_version = getattr(settings, "INGEST_NORMALIZATION_POLICY_VERSION", "v1")

    # Extract metadata from upload object (defensive: handle None upload)
    business_meta: Mapping[str, Any] | None = None
    upload_meta: Mapping[str, Any] | None = None

    if upload is not None:
        # Get upload-level metadata (may contain per-upload table_policy overrides)
        upload_meta = getattr(upload, "metadata", None)
        # Get business-level metadata (may contain tenant-wide table_policy defaults)
        business = getattr(upload, "business_profile", None)
        business_meta = getattr(business, "metadata", None)

    def _extract_policy(source: Mapping[str, Any] | None) -> Mapping[str, Any]:
        """
        Extract table_policy dict from metadata source.

        Helper to safely extract policy from metadata dicts. Returns empty dict
        if source is None or doesn't contain table_policy, ensuring we can always
        safely call .get() on the result.
        """
        if not isinstance(source, Mapping):
            return {}
        payload = source.get("table_policy")
        return payload if isinstance(payload, Mapping) else {}

    # Extract policies from metadata (defensive: handle missing/invalid metadata)
    business_policy = dict(_extract_policy(business_meta))
    upload_policy = dict(_extract_policy(upload_meta))

    enabled = base_enabled
    # Business-level toggle takes precedence over global default to disable normalization tenant-wide.
    if "enable_normalization" in business_policy:
        enabled = bool(business_policy["enable_normalization"])
    # Upload-level toggle wins last to allow one-off experiments/triage.
    if "enable_normalization" in upload_policy:
        enabled = bool(upload_policy["enable_normalization"])

    def _token_set(raw: Iterable[str]) -> set[str]:
        """
        Normalize and deduplicate token list into canonical set.

        Converts raw token list (from settings/metadata) into canonical form
        for null token matching. Filters out non-strings and empty values.
        """
        normalized: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                # Skip non-string items (defensive: handle malformed config)
                continue
            canonical = _canonical(item)
            if canonical:
                # Only add non-empty canonical tokens
                normalized.add(canonical)
        return normalized

    # Build null token set by layering: defaults → settings → business → upload
    # This ensures we always have common null markers, but can add tenant-specific ones
    tokens = set(DEFAULT_NULL_TOKENS)
    # Start from global tokens so we never lose common spreadsheet null markers
    # (Excel error values, common NA representations, etc.)
    extra_tokens = getattr(settings, "INGEST_NORMALIZATION_NULL_TOKENS", None)
    if isinstance(extra_tokens, (list, tuple, set)):
        # Global settings can add site-wide null tokens (e.g., custom error codes)
        tokens.update(_token_set(extra_tokens))
    # Business/upload tokens layer on top to capture tenant-specific jargon
    # (e.g., regional NA strings, custom null markers for specific industries)
    tokens.update(_token_set(business_policy.get("null_tokens") or []))
    tokens.update(_token_set(upload_policy.get("null_tokens") or []))

    def _sheet_set(raw: Iterable[str]) -> set[str]:
        """
        Normalize and deduplicate sheet name list into canonical set.

        Converts raw sheet name list (from metadata) into canonical form
        for whitelist/blacklist matching. Filters out non-strings and empty values.
        """
        normalized: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                # Skip non-string items (defensive: handle malformed config)
                continue
            canonical = _canonical_sheet(item)
            if canonical:
                # Only add non-empty canonical sheet names
                normalized.add(canonical)
        return normalized

    # Build sheet whitelist: business default, upload override wins
    whitelist = _sheet_set(business_policy.get("sheet_whitelist") or [])
    upload_whitelist = _sheet_set(upload_policy.get("sheet_whitelist") or [])
    if upload_whitelist:
        # Upload whitelist wins to allow ingesting only the relevant tabs from a multi-sheet file
        # (e.g., user uploads 10-sheet workbook but only wants "Sales" and "Inventory" sheets)
        whitelist = upload_whitelist
    # Build sheet blacklist: business default, upload override wins
    blacklist = _sheet_set(business_policy.get("sheet_blacklist") or [])
    upload_blacklist = _sheet_set(upload_policy.get("sheet_blacklist") or [])
    if upload_blacklist:
        # Upload blacklist overrides to quickly exclude a noisy tab
        # (e.g., exclude "Archive" or "Scratch" sheets without changing business defaults)
        blacklist = upload_blacklist

    # Resolve drop_empty_columns setting: business default, upload override, disabled override
    drop_empty_columns = True  # Default: trim empty columns to keep schema compact
    if "drop_empty_columns" in business_policy:
        # Business-level setting allows tenant-wide preference
        drop_empty_columns = bool(business_policy["drop_empty_columns"])
    if "drop_empty_columns" in upload_policy:
        # Upload-level setting allows per-file override (e.g., preserve structure for debugging)
        drop_empty_columns = bool(upload_policy["drop_empty_columns"])
    # If normalization is disabled we avoid trimming columns to preserve raw structure for debugging
    # This ensures disabled normalization truly preserves the original spreadsheet structure
    if not enabled:
        drop_empty_columns = False

    return TableNormalizationPolicy(
        enabled=enabled,
        null_tokens=tokens,
        drop_empty_columns=drop_empty_columns,
        sheet_whitelist=whitelist,
        sheet_blacklist=blacklist,
        policy_version=str(policy_version or "v1"),
    )


def sheet_is_allowed(sheet_name: str, policy: TableNormalizationPolicy) -> bool:
    """
    Decide whether a sheet should be normalized based on whitelist/blacklist.

    Args:
        sheet_name: Raw sheet name from the upload.
        policy: Normalization policy to evaluate.

    Returns:
        True when the sheet is eligible for ingest, False otherwise.

    Logic:
        1. Blacklist check first (takes precedence): if sheet is blacklisted, reject
        2. Whitelist check: if whitelist exists and sheet not in it, reject
        3. Default: allow if no filters or sheet passes filters

    Why:
        Blacklist takes precedence to allow quick exclusion of problematic sheets
        even if they're in a whitelist. When a whitelist is present we treat it as
        authoritative to avoid ingesting unintended tabs (PII, scratch pads, etc.).
        This prevents accidental ingestion of sensitive or irrelevant data.

    Used by:
        - KnowledgeIngestionService filters sheets before calling normalize_sheet_rows()
        - Called for each sheet in multi-sheet files (XLSX workbooks)

    Related:
        - resolve_normalization_policy() builds the whitelist/blacklist from metadata
        - Sheet names are canonicalized for case-insensitive matching
    """
    # Canonicalize sheet name for case-insensitive matching
    canonical = _canonical_sheet(sheet_name or "")
    # Blacklist check first (takes precedence over whitelist)
    if policy.sheet_blacklist and canonical in policy.sheet_blacklist:
        return False
    # Whitelist check: if whitelist exists, sheet must be in it
    if policy.sheet_whitelist and canonical not in policy.sheet_whitelist:
        return False
    # Default: allow if no filters or sheet passed all filters
    return True


def normalize_sheet_rows(
    raw_rows: Sequence[Sequence[Any]] | Iterable[Sequence[Any]],
    *,
    sheet_name: str,
    policy: TableNormalizationPolicy,
) -> NormalizedSheet:
    """
    Normalize a sheet into a trimmed, analytics-friendly structure.

    Args:
        raw_rows: Iterable of raw cell rows as read by the parser.
        sheet_name: Name of the sheet for diagnostics.
        policy: Normalization policy controlling null handling and column pruning.

    Returns:
        NormalizedSheet with header-derived schema, data rows (header excluded),
        and per-sheet diagnostics.

    Why:
        The downstream table_aggregate tool expects a single header row and compact
        numeric columns. Dropping empty rows/columns here keeps embeddings small
        and avoids misleading totals.
    """
    # Initialize diagnostics to track normalization operations
    diagnostics = SheetNormalizationDiagnostics(sheet_name=sheet_name)
    normalized_rows: list[list[str]] = []  # Accumulate normalized rows (header + data)

    # Normalize each row: replace null tokens, convert types, track empty rows
    for raw_row in raw_rows:
        normalized_row, replaced, has_values = _normalize_row(raw_row, policy)
        diagnostics.tokens_replaced += replaced  # Track null token replacements
        if not normalized_row:
            # Parser yielded an empty row (e.g., stray delimiter in CSV); ignore silently
            # Empty rows are already handled by has_values check below
            continue
        if not normalized_rows and not has_values:
            # First row empty: treat as header noise rather than data
            # This handles cases where first row is blank/whitespace (common in messy spreadsheets)
            diagnostics.rows_dropped += 1
            continue
        if normalized_rows and not has_values:
            # Drop trailing empty rows to avoid bloating the embedding input
            # Empty rows at the end are common in spreadsheets and add no value
            diagnostics.rows_dropped += 1
            continue
        # Row has content: add to normalized output
        normalized_rows.append(normalized_row)

    # Handle empty sheet case: all rows were empty or dropped
    if not normalized_rows:
        diagnostics.skipped = True
        diagnostics.skip_reason = diagnostics.skip_reason or "empty"
        return NormalizedSheet(sheet_name=sheet_name, column_schema=[], rows=[], diagnostics=diagnostics)

    # Split header from data rows (first row is header, rest is data)
    header = normalized_rows[0]
    data_rows = normalized_rows[1:]

    # Determine column count from widest row (handles ragged rows)
    column_count = max(len(row) for row in normalized_rows)
    columns_to_keep: list[int] = []  # Indices of columns to preserve
    column_schema: list[str] = []  # Header values for kept columns

    # Iterate through columns to identify empty ones to drop
    for idx in range(column_count):
        # Get header value (empty string if column index exceeds header length)
        header_value = header[idx] if idx < len(header) else ""
        # Get all values in this column across data rows (empty string if row is shorter)
        column_values = [row[idx] if idx < len(row) else "" for row in data_rows]
        should_drop = False
        if policy.drop_empty_columns and not header_value and not any(column_values):
            # Only drop when both header and data are empty
            # This preserves sparse numeric columns (e.g., column with header but mostly empty)
            # and columns with data but no header (e.g., unnamed numeric columns)
            should_drop = True
        if should_drop:
            diagnostics.columns_trimmed += 1
            continue
        # Column has content: keep it
        columns_to_keep.append(idx)
        # Use header value if present, otherwise generate "column_N" name
        # This ensures every column has a name for downstream table aggregation
        column_schema.append(header_value or f"column_{len(column_schema) + 1}")

    # Handle case where all columns were empty (shouldn't happen, but defensive)
    if not columns_to_keep:
        diagnostics.skipped = True
        diagnostics.skip_reason = diagnostics.skip_reason or "empty_columns"
        return NormalizedSheet(sheet_name=sheet_name, column_schema=[], rows=[], diagnostics=diagnostics)

    # Trim rows to only include kept columns (preserve column order)
    trimmed_rows: list[list[str]] = []
    for row in data_rows:
        # Extract only the columns we're keeping, using empty string for missing values
        trimmed_rows.append([row[idx] if idx < len(row) else "" for idx in columns_to_keep])

    diagnostics.skipped = False
    diagnostics.skip_reason = None
    return NormalizedSheet(
        sheet_name=sheet_name,
        column_schema=column_schema,
        rows=trimmed_rows,
        diagnostics=diagnostics,
    )


def summarize_normalization(policy: TableNormalizationPolicy, diagnostics: Sequence[SheetNormalizationDiagnostics]) -> dict[str, Any]:
    """
    Summarize per-sheet diagnostics into a policy-level summary for storage/analytics.

    Aggregates normalization statistics across all sheets in an upload into a single
    summary dict. This summary is stored in upload.ingestion_metadata["normalization"]
    for observability and debugging.

    Args:
        policy: Normalization policy used for this ingest run (for version and enabled flag).
        diagnostics: Per-sheet diagnostics collected during normalization (one per sheet).

    Returns:
        Dict with structure:
        - enabled: Whether normalization was enabled
        - policy_version: Policy version tag
        - rows_dropped: Total and per-sheet counts (if any rows dropped)
        - columns_trimmed: Total and per-sheet counts (if any columns trimmed)
        - tokens_replaced: Total null tokens replaced (if any)
        - empty_sheets_skipped: List of empty sheet names (if any)
        - policy_skipped: List of policy-filtered sheet names (if any)
        - null_tokens: Sorted list of null tokens used (for reference)

        Returns minimal dict (enabled, policy_version) when normalization is disabled.

    Why:
        Aggregated summaries make it easy to see normalization impact at a glance.
        Per-sheet breakdowns help identify problematic sheets. Storing this in
        ingestion_metadata enables dashboard display and historical comparison.

    Related:
        - Called by KnowledgeIngestionService after processing all sheets
        - Stored in upload.ingestion_metadata["normalization"] for dashboard display
        - Used for debugging when ingestion results differ from expectations
    """
    # Initialize summary with policy metadata (always included)
    summary: dict[str, Any] = {
        "enabled": bool(policy.enabled),
        "policy_version": policy.policy_version,
    }
    if not policy.enabled:
        # Early return when normalization is off so we avoid misleading counters
        # When disabled, normalization didn't run, so counters would be misleading
        return summary

    # Aggregate totals across all sheets
    total_rows = sum(item.rows_dropped for item in diagnostics)
    total_columns = sum(item.columns_trimmed for item in diagnostics)
    total_tokens = sum(item.tokens_replaced for item in diagnostics)
    if total_rows:
        summary["rows_dropped"] = {
            "total": total_rows,
            "by_sheet": {
                item.sheet_name: item.rows_dropped for item in diagnostics if item.rows_dropped
            },
        }
    if total_columns:
        summary["columns_trimmed"] = {
            "total": total_columns,
            "by_sheet": {
                item.sheet_name: item.columns_trimmed for item in diagnostics if item.columns_trimmed
            },
        }
    if total_tokens:
        summary["tokens_replaced"] = total_tokens

    # Categorize skipped sheets by reason (empty vs policy-driven)
    skipped = [
        item.sheet_name
        for item in diagnostics
        if item.skipped and item.skip_reason in {"empty", "empty_columns"}
    ]
    if skipped:
        # Empty sheets are worth tracking separately from policy-driven skips
        # This helps identify when spreadsheets have many empty tabs (data quality issue)
        summary["empty_sheets_skipped"] = skipped
    policy_skipped = [
        item.sheet_name for item in diagnostics if item.skipped and item.skip_reason == "policy"
    ]
    if policy_skipped:
        # Explicit policy skips show which tabs were intentionally ignored
        # This helps users understand why certain sheets weren't ingested (whitelist/blacklist)
        summary["policy_skipped"] = policy_skipped

    summary["null_tokens"] = sorted(policy.null_tokens)
    return summary


def _normalize_row(row: Sequence[Any], policy: TableNormalizationPolicy) -> tuple[list[str], int, bool]:
    """
    Normalize a single row of values.

    Processes each cell in the row, replacing null tokens, converting types to strings,
    and tracking whether the row has any meaningful content after normalization.

    Args:
        row: Raw row as produced by the parser (can contain Any types).
        policy: Normalization policy controlling null token handling.

    Returns:
        Tuple of:
        - normalized_row: List of normalized string values (null tokens become "")
        - replaced_token_count: Number of null tokens replaced in this row
        - has_values_flag: True if row has at least one non-empty cell after normalization

    Why:
        The has_values flag is critical for empty row detection. A row might have
        cells but all be null tokens or whitespace, making it effectively empty.
        This flag allows callers to drop truly empty rows while preserving rows
        with at least one meaningful value.

    Used by:
        - normalize_sheet_rows() processes each row and uses has_values to drop empty rows
    """
    normalized: list[str] = []  # Accumulate normalized cell values
    replaced_tokens = 0  # Count null tokens replaced in this row
    has_values = False  # Track if row has any meaningful content
    for value in row:
        # Normalize each cell value (handles type conversion, null tokens, etc.)
        normalized_value, replaced, keep_flag = _normalize_cell_value(value, policy)
        if replaced:
            replaced_tokens += 1  # Track null token replacements for diagnostics
        if keep_flag and normalized_value:
            # Track whether the row has meaningful content so callers can drop header-only blanks
            # keep_flag=True means cell has semantic content (not null/empty)
            has_values = True
        normalized.append(normalized_value)
    return normalized, replaced_tokens, has_values


def _normalize_cell_value(value: Any, policy: TableNormalizationPolicy) -> tuple[str, bool, bool]:
    """
    Normalize a single cell value according to ingestion policy.

    Args:
        value: Raw cell value (string/number/bytes/etc.).
        policy: Normalization policy determining null token handling.

    Returns:
        (normalized_value, replaced_token, has_value_flag)
        normalized_value: Final string stored in the normalized table.
        replaced_token: True when the value matched a null token.
        has_value_flag: True when the cell still carries semantic content.

    Why:
        Numbers are stringified so downstream aggregation sees a consistent type.
        NaN and configured null markers are blanked to avoid skewing totals or embeddings.
    """
    # Handle boolean values: convert to human-readable strings for LLM
    # "TRUE"/"FALSE" is more readable than "1"/"0" in embeddings and answers
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), False, True

    # Handle numeric values: convert to strings, special-case NaN
    # Note: bool is a subclass of int in Python, so check bool first above
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            # NaN should not survive into aggregates; treat as an empty cell
            # NaN in aggregations would produce NaN results, confusing the LLM
            return "", True, False
        # Convert numbers to strings for consistent downstream processing
        # Downstream table aggregation expects all values as strings
        return (str(value), False, True)

    # Handle None: treat as empty cell
    if value is None:
        return "", True, False

    # Handle bytes: decode to string (common in CSV exports with encoding issues)
    if isinstance(value, bytes):
        try:
            # Decode bytes defensively to avoid blowing up on badly encoded CSV exports
            # errors="ignore" skips invalid bytes rather than raising exceptions
            value = value.decode("utf-8", errors="ignore")
        except Exception:
            # Fallback: if decode fails entirely, treat as empty
            value = ""

    # Convert to string and trim whitespace
    text = str(value)
    trimmed = text.strip()
    if not trimmed:
        # Preserve replaced=True when original had whitespace so diagnostics capture the cleanup
        # This helps track when normalization is removing whitespace-only cells
        return "", bool(text), False

    # Check if trimmed value matches a null token (case-insensitive)
    canonical = _canonical(trimmed)
    if policy.enabled and canonical in policy.null_tokens:
        # Null tokens are treated as empty to keep totals accurate and embeddings concise
        # Common null markers like "N/A" or "#REF!" should not appear in embeddings
        return "", True, False

    # Value is valid: return trimmed string
    return trimmed, False, True
