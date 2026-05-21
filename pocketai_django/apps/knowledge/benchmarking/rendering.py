from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from apps.knowledge.benchmarking.quality_gate import (
    _clean_list,
    _metadata_dict,
    _row_scope_columns,
    _row_scope_reason,
)


def _as_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def render_snapshot_markdown(snapshot: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Ingestion Snapshot: {snapshot.get('snapshot_label')}")
    lines.append("")
    lines.append(f"- upload_id: `{snapshot.get('upload_id')}`")
    lines.append(f"- business_profile_id: `{snapshot.get('business_profile_id')}`")
    lines.append(f"- captured_at: `{snapshot.get('captured_at')}`")
    lines.append(f"- table_count: `{snapshot.get('table_count')}`")
    lines.append(f"- table_data_row_count: `{snapshot.get('table_data_row_count')}`")
    lines.append(f"- row_chunk_count: `{(snapshot.get('scope_metrics') or {}).get('row_chunk_count')}`")
    lines.append(f"- multi_scope_rate: `{(snapshot.get('scope_metrics') or {}).get('multi_scope_rate')}`")
    lines.append(f"- ambiguous_scope_rate: `{(snapshot.get('scope_metrics') or {}).get('ambiguous_scope_rate')}`")
    lines.append(f"- inferred_scope_rate: `{(snapshot.get('scope_metrics') or {}).get('inferred_scope_rate')}`")

    quality = snapshot.get("quality_metrics") or {}
    lines.append(f"- table_bbox_coverage_ratio: `{quality.get('table_bbox_coverage_ratio')}`")
    lines.append(f"- residual_text_ratio: `{quality.get('residual_text_ratio')}`")
    lines.append(f"- table_row_unique_evidence_count: `{quality.get('table_row_unique_evidence_count')}`")
    lines.append("")
    lines.append("## Settings")
    lines.append("")
    for key, value in (snapshot.get("settings") or {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Chunk Counts")
    lines.append("")
    for key, value in sorted((snapshot.get("chunk_counts") or {}).items(), key=lambda item: item[0]):
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Focus Scope Metrics")
    lines.append("")
    for key, value in (snapshot.get("focus_scope_metrics") or {}).items():
        lines.append(f"- {key}: `{value}`")

    focus_rows = list(snapshot.get("focused_row_chunks") or [])
    if focus_rows:
        lines.append("")
        lines.append("## Focus Rows")
        lines.append("")
        for row in focus_rows:
            lines.append(
                "- row {row}: scope={scope} reason={mode} fee={fee}".format(
                    row=row.get("table_row_index"),
                    scope=_row_scope_columns(row),
                    mode=_row_scope_reason(row),
                    fee=row.get("table_row_fee_value"),
                )
            )

    term_hits = snapshot.get("term_hits") or {}
    if term_hits:
        lines.append("")
        lines.append("## Term Hits")
        lines.append("")
        for term, hits in term_hits.items():
            lines.append(f"- {term}: `{len(hits)}` hits")
    lines.append("")
    return "\n".join(lines)


def render_comparison_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    baseline = report.get("baseline") or {}
    candidate = report.get("candidate") or {}
    deltas = report.get("deltas") or {}

    lines.append("# Table Ingestion Comparison")
    lines.append("")
    lines.append(f"- baseline_snapshot: `{baseline.get('snapshot_label')}` (`{baseline.get('upload_id')}`)")
    lines.append(f"- candidate_snapshot: `{candidate.get('snapshot_label')}` (`{candidate.get('upload_id')}`)")
    lines.append(f"- generated_at: `{report.get('generated_at')}`")
    lines.append("")
    lines.append("## Delta Summary")
    lines.append("")
    for key, value in deltas.items():
        lines.append(f"- {key}: `{value}`")

    quality_gate = _metadata_dict(report.get("quality_gate"))
    quality_metrics = _metadata_dict(report.get("quality_gate_metrics"))
    lines.append("")
    lines.append("## Quality Gate")
    lines.append("")
    lines.append(f"- passed: `{quality_gate.get('passed')}`")
    failed_checks = _clean_list(quality_gate.get("failed_checks"))
    if failed_checks:
        lines.append(f"- failed_checks: `{', '.join(failed_checks)}`")
    else:
        lines.append("- failed_checks: `none`")
    for key, value in sorted(_metadata_dict(quality_gate.get("thresholds")).items(), key=lambda item: item[0]):
        lines.append(f"- threshold.{key}: `{value}`")

    lines.append("")
    lines.append("## Quality Metrics")
    lines.append("")
    for key, value in sorted(quality_metrics.items(), key=lambda item: item[0]):
        lines.append(f"- {key}: `{value}`")

    regressions = list(report.get("regressions") or [])
    lines.append("")
    lines.append("## Regressions")
    lines.append("")
    if regressions:
        for item in regressions:
            lines.append(f"- {item}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Changed Rows")
    lines.append("")
    changed_rows = list(report.get("changed_rows") or [])
    if not changed_rows:
        lines.append("- none")
    else:
        for row in changed_rows:
            lines.append(
                "- {key}: baseline={left} | candidate={right} | reason={reason}".format(
                    key=row.get("row_key"),
                    left=(row.get("baseline") or {}).get("inferred_scope_columns"),
                    right=(row.get("candidate") or {}).get("inferred_scope_columns"),
                    reason=row.get("reason"),
                )
            )
    lines.append("")
    return "\n".join(lines)


def write_snapshot_files(snapshot: Mapping[str, Any], *, output_dir: Path, stem: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(_as_json(snapshot) + "\n", encoding="utf-8")
    md_path.write_text(render_snapshot_markdown(snapshot), encoding="utf-8")
    return json_path, md_path


def write_comparison_files(report: Mapping[str, Any], *, output_dir: Path, stem: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(_as_json(report) + "\n", encoding="utf-8")
    md_path.write_text(render_comparison_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_snapshot_stem(label: str, upload_id: str) -> str:
    date_token = timezone.now().date().isoformat()
    safe_label = slugify(label) or "snapshot"
    short_upload = str(upload_id).replace("-", "")[:8]
    return f"{safe_label}_{short_upload}_{date_token}"


def build_comparison_stem(label: str) -> str:
    date_token = timezone.now().date().isoformat()
    safe_label = slugify(label) or "comparison"
    return f"{safe_label}_{date_token}"


def resolve_output_dir(output_dir: str | Path | None) -> Path:
    raw = str(output_dir or "").strip()
    if not raw:
        return Path(settings.BASE_DIR) / "docs" / "ingestion_snapshots"
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return path


def load_snapshot_from_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Snapshot must be an object: {path}")
    return dict(payload)
