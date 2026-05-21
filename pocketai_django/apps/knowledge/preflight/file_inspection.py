from __future__ import annotations

import mimetypes
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from django.conf import settings

from apps.knowledge.documents import CsvPreviewError, preview_csv_upload

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
