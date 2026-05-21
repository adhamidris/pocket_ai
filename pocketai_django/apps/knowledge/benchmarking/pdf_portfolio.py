from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from django.utils import timezone
from django.utils.text import slugify

from core.tenancy import tenant_bypass

from apps.knowledge.benchmarking.quality_gate import (
    PDF_PORTFOLIO_EXPECTED_TABLE_MODES,
    _clean_list,
    _metadata_dict,
    _safe_int,
    _safe_ratio,
)
from apps.knowledge.benchmarking.rendering import _as_json
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadIssue,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
)


def normalize_pdf_portfolio_expectations(payload: Any) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        label = str(payload.get("label") or "pdf_portfolio_benchmark").strip() or "pdf_portfolio_benchmark"
        raw_entries = payload.get("entries") or []
    elif isinstance(payload, list):
        label = "pdf_portfolio_benchmark"
        raw_entries = payload
    else:
        raise ValueError("PDF portfolio expectations must be a JSON object or array.")

    if not isinstance(raw_entries, list):
        raise ValueError("PDF portfolio expectations 'entries' must be a list.")

    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"PDF portfolio expectation entry #{index} must be an object.")
        filename = str(raw.get("filename") or "").strip()
        if not filename:
            raise ValueError(f"PDF portfolio expectation entry #{index} is missing 'filename'.")

        mode = str(raw.get("expected_table_mode") or "").strip().lower()
        exact = _safe_int(raw.get("expected_table_count"))
        min_table_count = _safe_int(raw.get("min_table_count"))
        max_table_count = _safe_int(raw.get("max_table_count"))

        if exact is not None:
            mode = "range"
            min_table_count = exact
            max_table_count = exact
        elif not mode:
            if min_table_count is None and max_table_count is None:
                mode = "none"
            elif min_table_count is not None or max_table_count is not None:
                mode = "range"

        if mode not in PDF_PORTFOLIO_EXPECTED_TABLE_MODES:
            raise ValueError(
                f"Unsupported expected_table_mode '{mode}' for '{filename}'. "
                f"Use one of: {', '.join(sorted(PDF_PORTFOLIO_EXPECTED_TABLE_MODES))}."
            )

        if mode == "none":
            min_table_count = 0
            max_table_count = 0
        elif mode == "some":
            min_table_count = max(1, int(min_table_count or 1))
            max_table_count = max_table_count if max_table_count is not None else None
        else:
            if min_table_count is None and max_table_count is None:
                raise ValueError(
                    f"Range expectations for '{filename}' require min_table_count/max_table_count "
                    "or expected_table_count."
                )
            if min_table_count is None:
                min_table_count = 0
            if max_table_count is not None and max_table_count < min_table_count:
                raise ValueError(
                    f"Range expectations for '{filename}' have max_table_count < min_table_count."
                )

        entries.append(
            {
                "filename": filename,
                "document_shape": str(raw.get("document_shape") or "").strip() or None,
                "expected_table_mode": mode,
                "min_table_count": int(min_table_count or 0),
                "max_table_count": int(max_table_count) if max_table_count is not None else None,
                "notes": str(raw.get("notes") or "").strip() or None,
                "confidence": str(raw.get("confidence") or "").strip() or None,
                "tags": _clean_list(raw.get("tags")),
            }
        )

    return {
        "label": label,
        "entries": entries,
    }


def load_pdf_portfolio_expectations_from_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_pdf_portfolio_expectations(payload)


def evaluate_pdf_portfolio_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    entries = list(snapshot.get("entries") or [])
    evaluated_entries: list[dict[str, Any]] = []
    passed_count = 0
    failed_count = 0
    missing_count = 0

    for raw in entries:
        if not isinstance(raw, Mapping):
            continue
        entry = dict(raw)
        mode = str(entry.get("expected_table_mode") or "none").strip().lower()
        min_table_count = max(0, int(_safe_int(entry.get("min_table_count")) or 0))
        max_table_count = _safe_int(entry.get("max_table_count"))
        actual_table_count = _safe_int(entry.get("actual_table_count"))
        upload_found = bool(entry.get("upload_found"))
        status = "passed"
        reasons: list[str] = []

        if not upload_found or actual_table_count is None:
            status = "missing_upload"
            reasons.append("missing_upload")
            missing_count += 1
        else:
            if mode == "none":
                if actual_table_count != 0:
                    status = "failed"
                    reasons.append("unexpected_tables")
            else:
                if actual_table_count < min_table_count:
                    status = "failed"
                    reasons.append("too_few_tables")
                if max_table_count is not None and actual_table_count > max_table_count:
                    status = "failed"
                    reasons.append("too_many_tables")

        if status == "passed":
            passed_count += 1
        else:
            failed_count += 1

        entry["status"] = status
        entry["failed_checks"] = reasons
        evaluated_entries.append(entry)

    total = len(evaluated_entries)
    report = dict(snapshot)
    report["entries"] = evaluated_entries
    report["summary"] = {
        "total": total,
        "passed": passed_count,
        "failed": failed_count,
        "missing_uploads": missing_count,
        "pass_rate": round(_safe_ratio(passed_count, total), 4),
    }
    report["failed_entries"] = [entry for entry in evaluated_entries if entry.get("status") != "passed"]
    report["passed"] = failed_count == 0
    return report


def capture_pdf_portfolio_snapshot(
    *,
    expectations: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    business_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_pdf_portfolio_expectations(expectations)
    resolved_label = str(label or normalized.get("label") or "pdf_portfolio_benchmark").strip() or "pdf_portfolio_benchmark"
    entries: list[dict[str, Any]] = []
    business_token = str(business_id or "").strip()

    with tenant_bypass():
        for expected in normalized.get("entries") or []:
            filename = str(expected.get("filename") or "").strip()
            query = KnowledgeUpload.objects.filter(file_detail__filename__iexact=filename).select_related("file_detail")
            if business_token:
                query = query.filter(business_profile_id=business_token)
            upload = query.order_by("-created_at").first()

            if upload is None:
                entries.append(
                    {
                        **dict(expected),
                        "upload_found": False,
                        "upload_id": None,
                        "business_profile_id": business_token or None,
                        "actual_table_count": None,
                        "chunk_count": None,
                        "issue_count": None,
                        "updated_at": None,
                        "table_titles": [],
                        "table_previews": [],
                    }
                )
                continue

            tables = (
                KnowledgeUploadTable.objects.filter(upload=upload)
                .select_related("page")
                .order_by("page__page_number", "order_index")
            )
            table_previews: list[dict[str, Any]] = []
            for table in tables[:5]:
                first_row = (
                    KnowledgeUploadTableRow.objects.filter(table=table)
                    .order_by("row_index")
                    .only("raw_text")
                    .first()
                )
                table_previews.append(
                    {
                        "title": str(table.title or ""),
                        "page_number": int(table.page.page_number) if table.page_id and table.page else None,
                        "row_count": int(KnowledgeUploadTableRow.objects.filter(table=table).count()),
                        "column_count": len(table.column_schema) if isinstance(table.column_schema, list) else 0,
                        "preview": str((first_row.raw_text if first_row else "") or "").strip()[:240],
                    }
                )

            entries.append(
                {
                    **dict(expected),
                    "upload_found": True,
                    "upload_id": str(upload.id),
                    "business_profile_id": str(upload.business_profile_id),
                    "actual_table_count": int(tables.count()),
                    "chunk_count": int(upload.chunk_count),
                    "issue_count": int(KnowledgeUploadIssue.objects.filter(upload=upload).count()),
                    "updated_at": upload.updated_at.isoformat() if upload.updated_at else None,
                    "table_titles": [item["title"] for item in table_previews if item.get("title")],
                    "table_previews": table_previews,
                }
            )

    snapshot = {
        "label": resolved_label,
        "captured_at": timezone.now().isoformat(),
        "business_profile_id": business_token or None,
        "entries": entries,
    }
    return evaluate_pdf_portfolio_snapshot(snapshot)


def render_pdf_portfolio_markdown(report: Mapping[str, Any]) -> str:
    summary = _metadata_dict(report.get("summary"))
    lines: list[str] = []
    lines.append(f"# PDF Portfolio Benchmark: {report.get('label')}")
    lines.append("")
    lines.append(f"- captured_at: `{report.get('captured_at')}`")
    lines.append(f"- business_profile_id: `{report.get('business_profile_id')}`")
    lines.append(f"- passed: `{report.get('passed')}`")
    lines.append(f"- total: `{summary.get('total')}`")
    lines.append(f"- passed_count: `{summary.get('passed')}`")
    lines.append(f"- failed_count: `{summary.get('failed')}`")
    lines.append(f"- missing_uploads: `{summary.get('missing_uploads')}`")
    lines.append(f"- pass_rate: `{summary.get('pass_rate')}`")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    for entry in list(report.get("entries") or []):
        filename = str(entry.get("filename") or "")
        status = str(entry.get("status") or "")
        mode = str(entry.get("expected_table_mode") or "")
        min_tables = entry.get("min_table_count")
        max_tables = entry.get("max_table_count")
        actual = entry.get("actual_table_count")
        lines.append(
            "- {filename}: status=`{status}` expected_mode=`{mode}` expected_range=`{min_tables}-{max_tables}` actual_tables=`{actual}` upload_id=`{upload_id}`".format(
                filename=filename,
                status=status,
                mode=mode,
                min_tables=min_tables,
                max_tables=max_tables if max_tables is not None else "∞",
                actual=actual,
                upload_id=entry.get("upload_id"),
            )
        )
        failed_checks = _clean_list(entry.get("failed_checks"))
        if failed_checks:
            lines.append(f"  failed_checks: `{', '.join(failed_checks)}`")
        notes = str(entry.get("notes") or "").strip()
        if notes:
            lines.append(f"  notes: {notes}")
        previews = list(entry.get("table_previews") or [])
        for preview in previews[:3]:
            lines.append(
                "  kept_table: page=`{page}` rows=`{rows}` cols=`{cols}` title=`{title}` preview=`{preview}`".format(
                    page=preview.get("page_number"),
                    rows=preview.get("row_count"),
                    cols=preview.get("column_count"),
                    title=preview.get("title"),
                    preview=preview.get("preview"),
                )
            )
    lines.append("")
    return "\n".join(lines)


def write_pdf_portfolio_files(report: Mapping[str, Any], *, output_dir: Path, stem: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(_as_json(report) + "\n", encoding="utf-8")
    md_path.write_text(render_pdf_portfolio_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_pdf_portfolio_stem(label: str) -> str:
    date_token = timezone.now().date().isoformat()
    safe_label = slugify(label) or "pdf-portfolio-benchmark"
    return f"{safe_label}_{date_token}"
