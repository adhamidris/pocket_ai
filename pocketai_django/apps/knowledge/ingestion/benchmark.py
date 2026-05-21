from __future__ import annotations

from pathlib import Path
from typing import Iterable

from apps.knowledge.benchmarking.pdf_portfolio import (
    build_pdf_portfolio_stem,
    capture_pdf_portfolio_snapshot,
    evaluate_pdf_portfolio_snapshot,
    load_pdf_portfolio_expectations_from_json,
    normalize_pdf_portfolio_expectations,
    render_pdf_portfolio_markdown,
    write_pdf_portfolio_files,
)
from apps.knowledge.benchmarking.quality_gate import (
    evaluate_quality_gate,
    quality_gate_thresholds,
)
from apps.knowledge.benchmarking.rendering import (
    _as_json,
    build_comparison_stem,
    build_snapshot_stem,
    load_snapshot_from_json,
    render_comparison_markdown,
    render_snapshot_markdown,
    resolve_output_dir,
    write_comparison_files,
    write_snapshot_files,
)
from apps.knowledge.benchmarking.table_snapshots import (
    capture_upload_snapshot,
    compare_snapshots,
)


def parse_terms(values: Iterable[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if token:
            terms.append(token)
    return terms
