from __future__ import annotations

import logging
import mimetypes
import re
import zipfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from xml.etree import ElementTree

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import (
    KnowledgeSourceType,
    KnowledgeUpload,
    KnowledgeUploadFile,
    KnowledgeUploadText,
    KnowledgeUploadUrl,
)
from apps.services.documents import CsvPreviewError, preview_csv_upload

logger = logging.getLogger(__name__)

PREFLIGHT_VERSION = 1

try:  # optional dependencies used by ingestion as well
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None  # type: ignore

try:  # pragma: no cover - fallback depends on installed PDF package
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

try:  # optional
    from openpyxl import load_workbook  # type: ignore
except Exception:  # pragma: no cover
    load_workbook = None  # type: ignore

try:  # optional
    import xlrd  # type: ignore
except Exception:  # pragma: no cover
    xlrd = None  # type: ignore


def ensure_upload_preflight(
    upload: KnowledgeUpload,
    *,
    trigger: str | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """
    Lightweight, bounded scan to summarize what we *can* do with an upload.

    Stores results under `upload.ingestion_metadata["preflight"]` so the dashboard
    and API can show tenants limitations/next steps before (or even if) ingestion
    fails.
    """

    if not upload:
        return None

    ingestion_metadata = dict(upload.ingestion_metadata or {})
    existing = ingestion_metadata.get("preflight")
    if not force and isinstance(existing, Mapping) and existing.get("version") == PREFLIGHT_VERSION:
        return dict(existing)

    performed_at = timezone.now().isoformat()
    base: dict[str, Any] = {
        "version": PREFLIGHT_VERSION,
        "performed_at": performed_at,
        "trigger": trigger or "",
        "source_type": upload.source_type,
        "status": "ok",
        "format": None,
        "suggested_kind": None,
        "warnings": [],
        "recommendations": [],
        "limits": {},
        "metrics": {},
    }

    try:
        if upload.source_type in {KnowledgeSourceType.FILE, KnowledgeSourceType.INTEGRATION}:
            try:
                file_detail = upload.file_detail
            except KnowledgeUploadFile.DoesNotExist:
                file_detail = None
            if not file_detail:
                base["status"] = "error"
                base["warnings"].append("Missing file metadata; ingestion cannot run.")
            else:
                base.update(_preflight_file(upload, file_detail))
        elif upload.source_type == KnowledgeSourceType.LINK:
            try:
                url_detail = upload.url_detail
            except KnowledgeUploadUrl.DoesNotExist:
                url_detail = None
            if not url_detail:
                base["status"] = "error"
                base["warnings"].append("Missing URL metadata; ingestion cannot run.")
            else:
                base.update(_preflight_url(upload, url_detail))
        elif upload.source_type == KnowledgeSourceType.TEXT:
            try:
                text_detail = upload.text_detail
            except KnowledgeUploadText.DoesNotExist:
                text_detail = None
            if not text_detail:
                base["status"] = "error"
                base["warnings"].append("Missing text metadata; ingestion cannot run.")
            else:
                base.update(_preflight_text(upload, text_detail))
        else:
            base["status"] = "error"
            base["warnings"].append(f"Unsupported source type: {upload.source_type}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("knowledge.preflight.failed upload=%s", getattr(upload, "id", None))
        base["status"] = "error"
        base["warnings"].append(f"Preflight failed: {exc}")

    ingestion_metadata["preflight"] = base
    upload.ingestion_metadata = ingestion_metadata
    upload.save(update_fields=["ingestion_metadata", "updated_at"])
    logger.info("knowledge.preflight upload=%s status=%s format=%s", upload.id, base.get("status"), base.get("format"))
    return base


def _preflight_url(upload: KnowledgeUpload, url_detail: KnowledgeUploadUrl) -> dict[str, Any]:
    parsed = urlparse(url_detail.url or "")
    host = parsed.netloc or parsed.path
    warnings: list[str] = []
    recommendations: list[str] = []

    if not url_detail.url:
        warnings.append("URL is empty; ingestion cannot fetch content.")
    if not host:
        warnings.append("URL host could not be determined; ingestion may fail.")
    recommendations.append("If the URL requires login, export/upload the file instead.")

    return {
        "format": "url",
        "suggested_kind": "document",
        "metrics": {
            "url": url_detail.url,
            "host": host,
        },
        "warnings": warnings,
        "recommendations": recommendations,
    }


def _preflight_text(upload: KnowledgeUpload, text_detail: KnowledgeUploadText) -> dict[str, Any]:
    content = text_detail.content or ""
    chars = len(content)
    preview = content.strip().splitlines()[0][:120] if content.strip() else ""
    warnings: list[str] = []
    if chars > 200_000:
        warnings.append("Very large text entry; consider splitting into multiple documents for better retrieval.")
    return {
        "format": "text",
        "suggested_kind": "document",
        "metrics": {
            "characters": chars,
            "preview": preview,
        },
        "warnings": warnings,
        "recommendations": [],
    }


def _preflight_file(upload: KnowledgeUpload, file_detail: KnowledgeUploadFile) -> dict[str, Any]:
    filename = file_detail.filename or ""
    content_type = file_detail.content_type or ""
    fmt = _detect_format(filename, content_type)
    warnings: list[str] = []
    recommendations: list[str] = []
    status = "ok"

    absolute_path = None
    try:
        absolute_path = _resolve_media_path(file_detail.storage_path)
    except Exception as exc:
        status = "error"
        warnings.append(f"Storage path is invalid: {exc}")

    size_bytes = int(file_detail.size_bytes or getattr(upload, "size_bytes", 0) or 0)
    if absolute_path and absolute_path.exists():
        try:
            size_bytes = int(absolute_path.stat().st_size)
        except OSError:
            pass
    elif absolute_path:
        status = "error"
        warnings.append("File is missing from storage; ingestion cannot read it.")

    metrics: dict[str, Any] = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "storage_path": file_detail.storage_path,
    }

    if fmt in {"csv", "tsv"}:
        metrics.update(_preflight_csv_like(absolute_path, fmt, warnings))
        limits = _table_limits_snapshot(upload)
        metrics.update(_table_size_warnings(metrics, limits, warnings, recommendations))
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "dataset",
            "limits": {"table_limits": limits},
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    if fmt in {"xlsx", "xls"}:
        if fmt == "xlsx" and load_workbook is None:
            status = "error"
        if fmt == "xls" and xlrd is None:
            status = "error"
        metrics.update(_preflight_excel_like(absolute_path, fmt, warnings))
        limits = _table_limits_snapshot(upload)
        metrics.update(_table_size_warnings(metrics, limits, warnings, recommendations))
        recommendations.append("For very large sheets, prefer CSV exports or split by time/region/product.")
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "dataset",
            "limits": {"table_limits": limits},
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    if fmt == "pdf":
        page_count = _safe_pdf_page_count(absolute_path)
        if page_count is not None:
            metrics["page_count"] = page_count
            if page_count > 250:
                warnings.append("Large PDF (250+ pages); ingestion can be slow and retrieval may be noisier.")
        if size_bytes > 25 * 1024 * 1024:
            warnings.append("Large file size (25MB+); ingestion can be slow.")
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "document",
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": [],
        }

    if fmt in {"docx", "txt", "md", "rtf"} or (fmt and fmt.startswith("txt")):
        if size_bytes > 10 * 1024 * 1024:
            warnings.append("Large text document (10MB+); consider splitting for better retrieval.")
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "document",
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": [],
        }

    if fmt == "json":
        metrics.update(_preflight_json_like(absolute_path, warnings))
        if metrics.get("json_detected") == "jsonl":
            limits = _table_limits_snapshot(upload)
            metrics.update(_table_size_warnings(metrics, limits, warnings, recommendations))
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "dataset",
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    warnings.append("Unknown file type; ingestion may fail or produce poor retrieval.")
    return {
        "status": status,
        "format": fmt or "",
        "suggested_kind": "document",
        "metrics": metrics,
        "warnings": warnings,
        "recommendations": recommendations,
    }


def _detect_format(filename: str, content_type: str) -> str | None:
    filename_lower = (filename or "").lower()
    suffix = Path(filename_lower).suffix.lower()
    content_type_lower = (content_type or "").lower()
    guessed = mimetypes.guess_type(filename_lower)[0] if filename_lower else ""

    if suffix == ".pdf" or "pdf" in content_type_lower or "pdf" in (guessed or ""):
        return "pdf"
    if suffix in {".docx", ".dotx"} or "word" in content_type_lower or "officedocument.wordprocessingml" in content_type_lower:
        return "docx"
    if suffix in {".json", ".jsonl", ".ndjson"} or "json" in content_type_lower or "json" in (guessed or ""):
        return "json"
    if suffix in {".csv", ".tsv"} or "csv" in content_type_lower or "csv" in (guessed or ""):
        return "tsv" if suffix == ".tsv" or "tsv" in content_type_lower or "tsv" in (guessed or "") else "csv"
    if (
        suffix in {".xlsx", ".xlsm"}
        or "officedocument.spreadsheetml" in content_type_lower
        or "vnd.google-apps.spreadsheet" in content_type_lower
    ):
        return "xlsx"
    if suffix == ".xls" or "ms-excel" in content_type_lower or "vnd.ms-excel" in (guessed or ""):
        return "xls"
    if suffix in {".txt", ".md", ".rtf"} or "text" in content_type_lower:
        return suffix.strip(".") if suffix else "txt"
    return suffix.strip(".") if suffix else None


def _resolve_media_path(storage_path: str) -> Path:
    root = Path(getattr(settings, "MEDIA_ROOT", settings.BASE_DIR / "var" / "media")).resolve()
    candidate = (root / (storage_path or "")).resolve()
    if candidate == root or root in candidate.parents:
        return candidate
    raise ValueError("storage path escapes MEDIA_ROOT")


def _safe_pdf_page_count(path: Path | None) -> int | None:
    if not path or not path.exists():
        return None
    if fitz is not None:
        try:
            doc = fitz.open(path)
            return int(getattr(doc, "page_count", None) or len(doc))
        except Exception:
            return None
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            return len(getattr(reader, "pages", []) or [])
        except Exception:
            return None
    return None


def _preflight_csv_like(path: Path | None, format_hint: str, warnings: list[str]) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            preview = preview_csv_upload(handle, max_rows=25, max_bytes=500_000)
        return {
            "columns": list(preview.columns),
            "preview_rows": [list(row) for row in preview.rows[:10]],
            "preview_truncated": bool(preview.truncated),
            "row_count_sampled": int(preview.row_count),
            "estimated_row_count": _estimate_delimited_row_count(path),
            "dialect": preview.dialect,
        }
    except CsvPreviewError as exc:
        warnings.append(f"Unable to parse CSV preview: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"Unable to read CSV preview: {exc}")
    return {}


def _estimate_delimited_row_count(path: Path, *, sample_bytes: int = 250_000) -> int | None:
    try:
        file_size = path.stat().st_size
    except OSError:
        return None
    if file_size <= 0:
        return 0
    try:
        with path.open("rb") as handle:
            sample = handle.read(sample_bytes)
    except OSError:
        return None
    lines = sample.count(b"\n")
    if lines <= 1:
        return 1 if file_size else 0
    avg = len(sample) / float(lines)
    if avg <= 0:
        return None
    return max(0, int(file_size / avg))


def _preflight_excel_like(path: Path | None, format_hint: str, warnings: list[str]) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    if format_hint == "xlsx":
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0

        if file_size >= 15 * 1024 * 1024:
            # Avoid openpyxl overhead for large XLSX (shared strings + worksheet parsing).
            fast = _preflight_xlsx_zip_dimensions(path, warnings)
            if fast:
                return fast

        if load_workbook is None:
            warnings.append("openpyxl is not installed; XLSX ingestion is not available.")
            return {}
        try:
            workbook = load_workbook(filename=path, read_only=True, data_only=True)
        except Exception as exc:
            warnings.append(f"Unable to open XLSX: {exc}")
            return {}
        sheet_summaries: list[dict[str, Any]] = []
        total_rows = 0
        max_columns = 0
        for sheet in workbook.worksheets[:10]:
            max_row = int(getattr(sheet, "max_row", 0) or 0)
            max_col = int(getattr(sheet, "max_column", 0) or 0)
            total_rows += max_row
            max_columns = max(max_columns, max_col)
            sheet_summaries.append(
                {
                    "sheet_name": sheet.title,
                    "max_rows": max_row,
                    "max_columns": max_col,
                }
            )
        if len(workbook.worksheets) > 10:
            warnings.append(f"Workbook has {len(workbook.worksheets)} sheets; only the first 10 were inspected.")
        return {
            "sheets": sheet_summaries,
            "estimated_total_rows": total_rows,
            "estimated_max_columns": max_columns,
        }

    if format_hint == "xls":
        if xlrd is None:
            warnings.append("xlrd is not installed; XLS ingestion is not available.")
            return {}
        try:
            workbook = xlrd.open_workbook(filename=str(path), on_demand=True)
        except Exception as exc:
            warnings.append(f"Unable to open XLS: {exc}")
            return {}
        sheet_summaries: list[dict[str, Any]] = []
        total_rows = 0
        max_columns = 0
        for idx in range(min(getattr(workbook, "nsheets", 0) or 0, 10)):
            sheet = workbook.sheet_by_index(idx)
            nrows = int(getattr(sheet, "nrows", 0) or 0)
            ncols = int(getattr(sheet, "ncols", 0) or 0)
            total_rows += nrows
            max_columns = max(max_columns, ncols)
            sheet_summaries.append(
                {
                    "sheet_name": getattr(sheet, "name", None) or f"Sheet {idx + 1}",
                    "max_rows": nrows,
                    "max_columns": ncols,
                }
            )
        if getattr(workbook, "nsheets", 0) > 10:
            warnings.append(f"Workbook has {workbook.nsheets} sheets; only the first 10 were inspected.")
        return {
            "sheets": sheet_summaries,
            "estimated_total_rows": total_rows,
            "estimated_max_columns": max_columns,
        }

    return {}


_XLSX_NS_MAIN = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_XLSX_NS_REL = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
_XLSX_REL_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_XLSX_DIM_RE = re.compile(r"(?:(?P<a>[A-Z]+)(?P<ar>[0-9]+))(?::(?P<b>[A-Z]+)(?P<br>[0-9]+))?$")


def _preflight_xlsx_zip_dimensions(path: Path, warnings: list[str]) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                workbook_xml = archive.read("xl/workbook.xml")
                rels_xml = archive.read("xl/_rels/workbook.xml.rels")
            except KeyError:
                warnings.append("XLSX workbook structure is missing expected metadata files.")
                return {}

            workbook_root = ElementTree.fromstring(workbook_xml)
            rels_root = ElementTree.fromstring(rels_xml)

            rel_map: dict[str, str] = {}
            for rel in rels_root.findall("rel:Relationship", _XLSX_NS_REL):
                rid = rel.get("Id")
                target = rel.get("Target")
                if not rid or not target:
                    continue
                target_norm = target.lstrip("/")
                if not target_norm.startswith("xl/"):
                    target_norm = f"xl/{target_norm}"
                rel_map[rid] = target_norm

            sheets_parent = workbook_root.find("main:sheets", _XLSX_NS_MAIN)
            if sheets_parent is None:
                return {}

            sheet_summaries: list[dict[str, Any]] = []
            total_rows = 0
            max_columns = 0
            for sheet in sheets_parent.findall("main:sheet", _XLSX_NS_MAIN)[:10]:
                name = sheet.get("name") or ""
                rid = sheet.get(_XLSX_REL_ATTR) or ""
                target = rel_map.get(rid)
                max_row = 0
                max_col = 0
                if target:
                    try:
                        ws_root = ElementTree.fromstring(archive.read(target))
                        dim = ws_root.find("main:dimension", _XLSX_NS_MAIN)
                        ref = dim.get("ref") if dim is not None else ""
                        max_row, max_col = _parse_xlsx_dimension(ref)
                    except Exception:
                        max_row = 0
                        max_col = 0
                sheet_summaries.append({"sheet_name": name, "max_rows": max_row, "max_columns": max_col})
                total_rows += max_row
                max_columns = max(max_columns, max_col)

            sheet_count = len(sheets_parent.findall("main:sheet", _XLSX_NS_MAIN))
            if sheet_count > 10:
                warnings.append(f"Workbook has {sheet_count} sheets; only the first 10 were inspected.")

            return {
                "sheets": sheet_summaries,
                "estimated_total_rows": total_rows,
                "estimated_max_columns": max_columns,
                "dimension_source": "xlsx_zip",
            }
    except zipfile.BadZipFile:
        warnings.append("XLSX file is not a valid ZIP container.")
    except Exception as exc:
        warnings.append(f"Unable to inspect XLSX workbook: {exc}")
    return {}


def _parse_xlsx_dimension(ref: str) -> tuple[int, int]:
    raw = (ref or "").strip().upper()
    if not raw:
        return 0, 0
    match = _XLSX_DIM_RE.match(raw)
    if not match:
        return 0, 0
    col = match.group("b") or match.group("a") or ""
    row = match.group("br") or match.group("ar") or ""
    return _excel_col_to_int(col), int(row or 0)


def _excel_col_to_int(col: str) -> int:
    value = 0
    for char in (col or "").strip().upper():
        if not ("A" <= char <= "Z"):
            continue
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _preflight_json_like(path: Path | None, warnings: list[str]) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            sample = handle.read(200_000)
    except OSError as exc:
        warnings.append(f"Unable to read JSON sample: {exc}")
        return {}

    stripped = sample.lstrip()
    detected: str | None = None
    if stripped.startswith(b"{") and b"\n" in stripped[:5000]:
        # Likely JSONL/NDJSON (heuristic).
        detected = "jsonl"
    elif stripped.startswith(b"["):
        detected = "array"
    elif stripped.startswith(b"{"):
        detected = "object"
    else:
        detected = "unknown"

    return {
        "json_detected": detected,
        "estimated_row_count": _estimate_delimited_row_count(path) if detected == "jsonl" else None,
    }


def _table_limits_snapshot(upload: KnowledgeUpload | None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "max_rows": max(1, int(getattr(settings, "TABLE_MAX_ROWS_DEFAULT", 5000))),
        "max_columns": int(getattr(settings, "TABLE_MAX_COLUMNS_DEFAULT", 0) or 0) or None,
        "column_whitelist": [],
        "max_rows_source": "default",
        "small_row_limit": int(getattr(settings, "RAG_TABLE_SMALL_ROW_LIMIT", 2000)),
        "large_row_limit": int(getattr(settings, "RAG_TABLE_LARGE_ROW_LIMIT", 20000)),
        "hard_row_cap": int(getattr(settings, "RAG_TABLE_MAX_HARD_CAP", 100000)),
    }
    whitelist: set[str] = set()

    def merge(source: Mapping[str, Any] | None) -> None:
        if not isinstance(source, Mapping):
            return
        rows = source.get("max_rows")
        columns = source.get("max_columns")
        raw_whitelist = source.get("column_whitelist")
        try:
            if rows is not None:
                value = int(rows)
                if value > 0:
                    config["max_rows"] = value
                    config["max_rows_source"] = "override"
        except (TypeError, ValueError):
            pass
        try:
            if columns is not None:
                value = int(columns)
                config["max_columns"] = value if value > 0 else None
        except (TypeError, ValueError):
            pass
        if isinstance(raw_whitelist, (list, tuple, set)):
            for item in raw_whitelist:
                token = str(item or "").strip()
                if token:
                    whitelist.add(token)

    business_meta = getattr(getattr(upload, "business_profile", None), "metadata", {}) if upload else {}
    upload_meta = getattr(upload, "metadata", {}) if upload else {}
    merge(business_meta.get("table_limits"))
    merge(upload_meta.get("table_limits"))

    config["column_whitelist"] = sorted(whitelist)
    return config


def _table_size_warnings(
    metrics: Mapping[str, Any],
    limits: Mapping[str, Any],
    warnings: list[str],
    recommendations: list[str],
) -> dict[str, Any]:
    row_count = (
        metrics.get("estimated_total_rows")
        or metrics.get("estimated_row_count")
        or metrics.get("row_count")
        or 0
    )
    try:
        row_count_int = int(row_count or 0)
    except (TypeError, ValueError):
        row_count_int = 0

    cap, tier, strategy = _determine_row_cap(row_count_int, limits)
    dataset_enabled = bool(getattr(settings, "DATASET_MODE_ENABLED", True))
    dataset_threshold = int(getattr(settings, "DATASET_MODE_ROW_THRESHOLD", limits.get("large_row_limit", 20000)))
    dataset_preview_rows = int(getattr(settings, "DATASET_MODE_PREVIEW_ROWS", 200))
    table_note = {
        "row_cap": cap,
        "row_tier": tier,
        "strategy": strategy,
        "dataset_mode": bool(dataset_enabled and row_count_int and row_count_int >= dataset_threshold),
    }

    if row_count_int and row_count_int > int(limits.get("large_row_limit", 20000)):
        if dataset_enabled and row_count_int >= dataset_threshold:
            warnings.append(
                f"Large table detected (~{row_count_int} rows). It will be stored in dataset mode (file-backed); only a preview is indexed for search."
            )
            recommendations.append(
                f"Expect better results when you query the dataset using an identifier column (e.g., order_id, ticket_id). Preview rows are limited to ~{dataset_preview_rows}."
            )
        else:
            warnings.append(
                f"Large table detected (~{row_count_int} rows). By default we only index the first {cap} rows."
            )
            recommendations.append(
                "If you need exact lookups across all rows, consider splitting the dataset or using a database-backed integration."
            )
    if row_count_int and row_count_int > int(limits.get("hard_row_cap", 100000)):
        warnings.append("Table exceeds hard row cap; ingestion will drop data beyond configured limits.")
    return {"table_budget": table_note}


def _determine_row_cap(row_count: int, limits: Mapping[str, Any]) -> tuple[int, str, str]:
    base_cap = max(1, int(limits.get("max_rows", 1)))
    small_limit = max(1, int(limits.get("small_row_limit", 2000)))
    large_limit = max(small_limit, int(limits.get("large_row_limit", 20000)))
    hard_cap = max(1, int(limits.get("hard_row_cap", 100000)))
    override = limits.get("max_rows_source") == "override"
    if row_count <= 0:
        return base_cap, "unknown", "default"
    if not override and row_count <= large_limit:
        tier = "small" if row_count <= small_limit else "medium"
        return row_count, tier, "full"
    tier = "large" if row_count > large_limit else "override"
    effective_cap = min(base_cap, hard_cap)
    strategy = "override" if override and tier != "large" else "capped"
    return effective_cap, tier, strategy
